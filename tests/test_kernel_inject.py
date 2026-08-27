#!/usr/bin/env python3
"""Composer↔pane input consistency (the user 2026-06-19): the kernel CLEARS the pane's input box before it
pastes a message, and CLEARS the prompt Claude Code restores on interrupt — so Stop → type-and-send can no
longer concatenate a recalled prompt with the new message. Self-contained: drives the tmux helpers with a
fake subprocess so it doesn't share test_kernel.py's setUp.
"""
import os
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# These exercise tmux BEHAVIOUR (they stub subprocess.run and assert on the argv). Declare a tmux
# host explicitly so they assert the same thing on a machine without tmux installed, where the
# backend is otherwise inert by design (see TmuxBackend.available).
os.environ["ROMP_TMUX_AVAILABLE"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_inj", os.path.join(BIN, "romp-kernel")).load_module()


class _Res:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


RULE = "─" * 40


def _pane(box_lines):
    """A capture shaped like the CLI's — the input box sits between the last two ─── rules."""
    first, rest = (box_lines[0], box_lines[1:]) if box_lines else ("", [])
    return "\n".join(["  an earlier reply", RULE, km.PROMPT_GLYPH + " " + first]
                     + ["  " + line for line in rest] + [RULE])


class Inject(unittest.TestCase):
    """The fake tmux models the ONE pane behavior these paths depend on: Ctrl+U kills the last input line
    (its text first, then the emptied line), which is what a live Claude Code 2.1.223 does. The clear used
    to be Ctrl+A + Backspace on the belief that Ctrl+A selects all — measured 2026-08-26, it does not, and
    the clear silently did nothing to a non-empty box for two months."""

    def setUp(self):
        self.calls = []
        self.box_lines = []            # per-test: the input box the fake pane is holding

        def rec(args, **kw):
            self.calls.append(list(args))
            if args[:2] == ["tmux", "capture-pane"]:
                return _Res(_pane(self.box_lines))
            if args[1:2] == ["send-keys"] and args[-1] == "C-u" and self.box_lines:
                if self.box_lines[-1]:
                    self.box_lines[-1] = ""
                else:
                    self.box_lines.pop()
            return _Res("")            # stdout "" → not in copy-mode, no image paths left in the input
        self.p_run = mock.patch.object(km.subprocess, "run", side_effect=rec)
        self.p_sleep = mock.patch.object(km.time, "sleep", lambda *a, **k: None)
        self.p_run.start()
        self.p_sleep.start()

    def tearDown(self):
        self.p_run.stop()
        self.p_sleep.stop()

    def _cmds(self):
        return [c[1:] for c in self.calls if c and c[0] == "tmux"]   # the tmux sub-command sequence

    KILL = ["send-keys", "-t", "sess", "C-u"]

    def test_clear_kills_until_the_box_is_empty(self):
        self.box_lines = ["a recalled prompt", "and its second line"]
        self.assertTrue(km._clear_pane_input("sess"))
        self.assertEqual(km._box_text(_pane(self.box_lines)), "",
                         "the box READS empty, not merely one character shorter")
        self.assertEqual([c for c in self._cmds() if c == self.KILL], [self.KILL] * 3,
                         "one press per line, plus one for the emptied line left behind")

    def test_clear_on_an_empty_box_presses_nothing(self):
        self.assertTrue(km._clear_pane_input("sess"))
        self.assertNotIn(self.KILL, self._cmds(), "the ordinary send costs one capture and no keystrokes")

    def test_clear_is_a_noop_without_a_name(self):
        self.assertFalse(km._clear_pane_input(""))
        self.assertEqual(self.calls, [])

    def test_interrupt_stops_then_wipes_the_restored_prompt(self):
        self.box_lines = ["the prompt Claude Code restored on Esc"]
        km._interrupt("sess", _async=False)
        cmds = self._cmds()
        esc = ["send-keys", "-t", "sess", "Escape"]
        self.assertIn(esc, cmds, "Esc stops the turn")
        self.assertIn(self.KILL, cmds, "then the restored prompt is cleared")
        self.assertLess(cmds.index(esc), cmds.index(self.KILL), "stop BEFORE clear (let the restore land first)")
        self.assertEqual(km._box_text(_pane(self.box_lines)), "")

    def test_inject_clears_before_pasting_so_it_replaces_not_appends(self):
        self.box_lines = ["leftover typed straight into the terminal"]
        km._tmux_send("sess", "hello", _async=False)
        cmds = self._cmds()
        paste = next(c for c in cmds if c[:1] == ["paste-buffer"])
        self.assertIn(self.KILL, cmds, "the input is cleared as part of the inject")
        self.assertLess(cmds.index(self.KILL), cmds.index(paste),
                        "clear happens BEFORE the paste — a paste REPLACES, never appends to leftover text")
        self.assertEqual(cmds[-1], ["send-keys", "-t", "sess", "Enter"], "Enter submits after the paste")

    def test_inject_refuses_when_the_box_will_not_clear(self):
        # the regression's own shape: presses land, the box does not move. Pasting here would deliver the
        # leftover and the message JOINED as one prompt, so the send is refused instead (loud on stderr).
        self.box_lines = ["text this pane will never let go of"]
        with mock.patch.object(km, "_box_text", lambda cap: "text this pane will never let go of"):
            km._tmux_send("sess", "hello", _async=False)
        cmds = self._cmds()
        self.assertEqual([c for c in cmds if c[:1] == ["paste-buffer"]], [], "nothing is pasted")
        self.assertNotIn(["send-keys", "-t", "sess", "Enter"], cmds, "and nothing is submitted")

    def test_inject_without_a_name_or_text_does_nothing(self):
        km._tmux_send("", "hello", _async=False)
        km._tmux_send("sess", "", _async=False)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
