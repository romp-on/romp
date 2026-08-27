#!/usr/bin/env python3
"""The clear-before-paste that stopped clearing (the user 2026-08-26).

Every romp send into a tmux pane pastes, and a paste APPENDS — so the input box has to be emptied first or
the CLI receives the leftover and the new message JOINED. `_clear_pane_input` was written for that
(2026-06-19) as Ctrl+A + Backspace, on the understanding that Ctrl+A selects the whole input. Measured
against Claude Code 2.1.223 in a scratch tmux CLI, it does not: Ctrl+A does not select, the Backspace
deletes ONE character, and the clear was a silent no-op on any non-empty box. The joined submission then
carries both texts — the CLI answers a prompt nobody wrote, and the delivered text no longer matches the
echo romp is showing, which makes a corrupted send indistinguishable from a lost one.

Ctrl+U empties the box, one line per press (two per line: the text, then the emptied line), so the clear
presses until the box READS empty and REFUSES when it cannot get there — a refusal keeps the leftover and
the message apart, which is the only safe outcome. The fake pane below models exactly those Ctrl+U
semantics; the live-CLI measurement is what it stands in for. Synthetic pane text throughout.

XDG_STATE_HOME is redirected before the kernel loads so no test state leaks into the live store."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = SourceFileLoader("romp_kernel_pane_clear", os.path.join(BIN, "romp-kernel")).load_module()

RULE = "─" * 40
SESSION = "web"


def _pane(box_lines):
    """A capture shaped like the CLI's: the input box sits between the last two ─── rules."""
    body = ["  an earlier reply", RULE]
    first, rest = (box_lines[0], box_lines[1:]) if box_lines else ("", [])
    body.append(km.PROMPT_GLYPH + " " + first)
    body.extend("  " + line for line in rest)
    body.append(RULE)
    return "\n".join(body)


class _FakePane:
    """The two raw-tmux primitives `_clear_pane_input` uses, over a modelled input box.

    `honors_kill` off is the regression itself: a CLI that ignores the keystroke, where the clear must end
    as a refusal rather than a spin or a false "cleared"."""

    def __init__(self, box_lines, honors_kill=True):
        self.box_lines = list(box_lines)
        self.honors_kill = honors_kill
        self.keys_sent = []
        self.captures = 0

    def capture(self, name, join=False, colour=False, t=2.5):
        self.captures += 1
        return _pane(self.box_lines)

    def send_keys(self, name, *keys, t=3):
        self.keys_sent.append(keys)
        if not self.honors_kill or keys != ("C-u",):
            return
        if not self.box_lines:                      # nothing left to kill
            return
        if self.box_lines[-1]:
            self.box_lines[-1] = ""                 # the CLI kills the line's TEXT first…
        else:
            self.box_lines.pop()                    # …then the emptied line itself


class ClearPaneInput(unittest.TestCase):
    def setUp(self):
        self._saved_tmux = km._TMUX

    def tearDown(self):
        km._TMUX = self._saved_tmux

    def _run_clear(self, pane):
        km._TMUX = pane
        return km._clear_pane_input(SESSION)

    def test_a_multi_line_leftover_is_fully_cleared(self):
        pane = _FakePane(["https://example.com/pull/123 ", "and a second line", ""])
        self.assertTrue(self._run_clear(pane))
        self.assertEqual(km._box_text(_pane(pane.box_lines)), "", "the box is provably empty at the end")

    def test_an_empty_box_costs_one_capture_and_no_keystrokes(self):
        # the ordinary send's path: cheaper than the two keystrokes it used to fire unconditionally
        pane = _FakePane([""])
        self.assertTrue(self._run_clear(pane))
        self.assertEqual(pane.keys_sent, [])
        self.assertEqual(pane.captures, 1)

    def test_a_box_that_will_not_empty_is_a_REFUSAL_not_a_false_clear(self):
        # the regression's shape — the keystroke lands and nothing changes. Answering True here is what
        # let a send paste onto the leftover for two months.
        pane = _FakePane(["text the CLI will not kill"], honors_kill=False)
        self.assertFalse(self._run_clear(pane))
        self.assertEqual(len(pane.keys_sent), km._CLEAR_UNCHANGED_GIVE_UP,
                         "presses that move nothing stop early — the press cap is only the outer guard")
        self.assertEqual(set(pane.keys_sent), {("C-u",)})
        self.assertLess(km._CLEAR_UNCHANGED_GIVE_UP, km._CLEAR_KILL_PRESSES)

    def test_an_unreadable_box_is_a_refusal_and_sends_nothing(self):
        # no locatable prompt box (a loading screen, a picker): the box may hold anything, so a clear that
        # cannot READ it must not claim it, and must not fire keystrokes into whatever IS up
        km._TMUX = _FakePane([""])
        km._TMUX.capture = lambda name, join=False, colour=False, t=2.5: "no rules here at all"
        self.assertFalse(km._clear_pane_input(SESSION))
        self.assertEqual(km._TMUX.keys_sent, [])

    def test_no_session_name_is_a_refusal(self):
        self.assertFalse(km._clear_pane_input(""))


class SendRefusesToConcatenate(unittest.TestCase):
    """The caller's half: a send whose clear failed must not paste. Pasting anyway is not the lesser evil —
    it delivers two texts joined as one prompt."""

    def setUp(self):
        self._saved_tmux = km._TMUX

    def tearDown(self):
        km._TMUX = self._saved_tmux

    class _RecordingPane(_FakePane):
        def __init__(self, box_lines, honors_kill=True):
            super().__init__(box_lines, honors_kill)
            self.buffers, self.pastes = [], []

        def pane_in_mode(self, name, t=2):
            return False

        def set_buffer(self, text):
            self.buffers.append(text)

        def paste_buffer(self, name):
            self.pastes.append(name)

    def test_an_unclearable_box_blocks_the_paste_and_the_Enter(self):
        pane = self._RecordingPane(["a draft typed straight into the terminal"], honors_kill=False)
        km._TMUX = pane
        km._tmux_send(SESSION, "the composer message", _async=False)
        self.assertEqual(pane.buffers, [], "nothing is staged")
        self.assertEqual(pane.pastes, [], "nothing is pasted onto the leftover")
        self.assertNotIn(("Enter",), pane.keys_sent, "and nothing is submitted")

    def test_a_clearable_box_sends_normally(self):
        pane = self._RecordingPane(["an interrupt-restored prompt"])
        km._TMUX = pane
        km._tmux_send(SESSION, "the composer message", _async=False)
        self.assertEqual(pane.buffers, ["the composer message"])
        self.assertEqual(pane.pastes, [SESSION])
        self.assertIn(("Enter",), pane.keys_sent)


if __name__ == "__main__":
    unittest.main()
