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
import contextlib
import io
import os
import tempfile
import threading
import types
import unittest
from romp_load import load_source

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = load_source("romp_kernel_pane_clear", os.path.join(BIN, "romp-kernel"))

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

    def test_a_draft_with_a_BLANK_line_still_clears(self):
        # The change detector compares the RAW box region, not the stripped text: a press that pops an
        # emptied or blank LINE moves the region but not the text, and a blank line in the draft produces
        # two such presses in a row — which the stripped compare read as a dead binding, refusing a
        # perfectly clearable box (PR-741 review, 2026-08-27). A multi-paragraph restored prompt is the
        # exact shape the clear exists for, so this is the case that must never refuse.
        pane = _FakePane(["paragraph one", "", "paragraph two"])
        self.assertTrue(self._run_clear(pane))
        self.assertEqual(km._box_text(_pane(pane.box_lines)), "")
        self.assertEqual(set(pane.keys_sent), {("C-u",)}, "nothing but the kill was ever pressed")

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
        """`fail` names the checked delivery steps ("set_buffer" / "paste_buffer" / "enter") that answer False
        WITHOUT acting — what a real tmux whose server died after the clear does (exit 1, and the command
        reached no pane). By default the pane is healthy: each checked step delegates to its forgiving twin
        and answers True, so the recorders below see exactly what a live pane would receive."""

        def __init__(self, box_lines, honors_kill=True, fail=()):
            super().__init__(box_lines, honors_kill)
            self.buffers, self.pastes = [], []
            self.fail = set(fail)

        def pane_in_mode(self, name, t=2):
            return False

        def set_buffer(self, text):
            self.buffers.append(text)

        def paste_buffer(self, name):
            self.pastes.append(name)

        def set_buffer_checked(self, text):
            if "set_buffer" in self.fail:
                return False
            self.set_buffer(text)
            return True

        def paste_buffer_checked(self, name):
            if "paste_buffer" in self.fail:
                return False
            self.paste_buffer(name)
            return True

        def send_keys_checked(self, name, *keys, t=3):
            if "enter" in self.fail:
                return False
            self.send_keys(name, *keys, t=t)
            return True

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


class SendAbortsWhenTmuxAnswersNonzero(unittest.TestCase):
    """The clear-guard covers a death BEFORE the clear; these cover one after it. set-buffer, paste-buffer and
    the submitting Enter used to run through primitives that swallow every subprocess error and never read
    the exit code, so a tmux server or session that died once the clear had passed made all three silent
    no-ops — the send looked complete and the message vanished with no trace. Each step now reads tmux's own
    exit code and a nonzero answer aborts the sequence there, loudly, in the same shape as the clear-guard
    refusal. The box is EMPTY in every case so the clear itself succeeds and the checked step is the only
    thing that can refuse."""

    TEXT = "the composer message"
    _RecordingPane = SendRefusesToConcatenate._RecordingPane

    def setUp(self):
        self._saved_tmux = km._TMUX

    def tearDown(self):
        km._TMUX = self._saved_tmux

    def _send(self, pane, **kw):
        km._TMUX = pane
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._tmux_send(SESSION, self.TEXT, _async=False, **kw)
        return err.getvalue()

    def test_a_dead_server_at_set_buffer_stages_pastes_and_submits_nothing(self):
        pane = self._RecordingPane([""], fail=("set_buffer",))
        err = self._send(pane)
        self.assertEqual(pane.buffers, [], "the failed set-buffer staged nothing")
        self.assertEqual(pane.pastes, [], "and nothing is pasted after it")
        self.assertNotIn(("Enter",), pane.keys_sent, "and nothing is submitted")
        self.assertIn("set-buffer failed", err, "the abort names the step on stderr")

    def test_a_dead_server_at_paste_buffer_never_submits(self):
        pane = self._RecordingPane([""], fail=("paste_buffer",))
        err = self._send(pane)
        self.assertEqual(pane.buffers, [self.TEXT], "the text was staged…")
        self.assertEqual(pane.pastes, [], "…but the paste refused, so nothing reached the input")
        self.assertNotIn(("Enter",), pane.keys_sent, "and Enter is never pressed on an empty box")
        self.assertIn("paste-buffer failed", err)

    def test_a_failed_submitting_Enter_is_reported_not_swallowed(self):
        # the fake records only the keys it actually sent, so a refused Enter leaves no ("Enter",) behind
        pane = self._RecordingPane([""], fail=("enter",))
        err = self._send(pane)
        self.assertEqual(pane.buffers, [self.TEXT])
        self.assertEqual(pane.pastes, [SESSION], "the paste itself landed")
        self.assertNotIn(("Enter",), pane.keys_sent)
        self.assertIn("submitting Enter failed", err)

    def test_a_failed_first_Enter_never_sends_the_model_confirm_Enter(self):
        # /model's second Enter accepts a confirm dialog; with the first Enter refused there is no dialog,
        # and a stray Enter into whatever IS up would be the concatenation class of bug all over again
        pane = self._RecordingPane([""], fail=("enter",))
        err = self._send(pane, model_cmd=True)
        self.assertNotIn(("Enter",), pane.keys_sent, "neither Enter is sent")
        self.assertIn("submitting Enter failed", err)

    def test_a_healthy_pane_sends_exactly_as_before_and_says_nothing(self):
        # the success path is untouched: same three steps, same order, no extra tmux calls, silent stderr
        pane = self._RecordingPane([""])
        err = self._send(pane)
        self.assertEqual(pane.buffers, [self.TEXT])
        self.assertEqual(pane.pastes, [SESSION])
        self.assertEqual(pane.keys_sent, [("Enter",)], "one Enter, and no keystroke the clear did not need")
        self.assertEqual(err, "")


class PasteVerdictHooks(unittest.TestCase):
    """_tmux_send's verdict seam. TmuxBackend.send returns truthy the moment the paste thread is
    started, so a caller whose bookkeeping keys on that return (the user-todo answer stamp) would
    otherwise never hear that the clear-guard above, or a dead server at a checked step, refused
    the paste. `on_refused` fires iff the message demonstrably never reached the CLI; `on_delivered`
    fires once the submitting Enter landed; exactly one of them fires per send, from the paste
    thread, after the pane lock is released. A hookless send is the call it always was."""

    def setUp(self):
        self._saved_tmux = km._TMUX

    def tearDown(self):
        km._TMUX = self._saved_tmux

    def _send(self, pane, **kw):
        km._TMUX = pane
        fired = []
        km._tmux_send(SESSION, "the composer message", _async=False,
                      on_refused=lambda: fired.append("refused"),
                      on_delivered=lambda: fired.append("delivered"), **kw)
        return fired

    def test_a_clear_guard_refusal_fires_on_refused_only(self):
        pane = SendRefusesToConcatenate._RecordingPane(["a draft typed straight into the terminal"],
                                                       honors_kill=False)
        self.assertEqual(self._send(pane), ["refused"])
        self.assertEqual(pane.pastes, [], "the refusal IS the verdict: nothing was pasted")

    def test_a_clean_paste_fires_on_delivered_only(self):
        pane = SendRefusesToConcatenate._RecordingPane(["an interrupt-restored prompt"])
        self.assertEqual(self._send(pane), ["delivered"])
        self.assertIn(("Enter",), pane.keys_sent)

    def test_every_checked_step_failure_is_a_refusal(self):
        for step in ("set_buffer", "paste_buffer", "enter"):
            with self.subTest(step=step):
                pane = SendRefusesToConcatenate._RecordingPane([""], fail=(step,))
                self.assertEqual(self._send(pane), ["refused"],
                                 "a dead server at %s is not a delivery" % step)

    def test_the_hooks_fire_after_the_pane_lock_is_released(self):
        # the hooks take their own locks (the todo store's, the pending-mark file's); a hook
        # that ran under the pane lock would serialize those behind every send and interrupt
        seen = []

        def probe():
            lock = km._pane_io_lock(SESSION)
            free = lock.acquire(blocking=False)
            if free:
                lock.release()
            seen.append(free)
        km._TMUX = SendRefusesToConcatenate._RecordingPane(["an interrupt-restored prompt"])
        km._tmux_send(SESSION, "the composer message", _async=False, on_delivered=probe)
        km._TMUX = SendRefusesToConcatenate._RecordingPane(["a draft"], honors_kill=False)
        km._tmux_send(SESSION, "the composer message", _async=False, on_refused=probe)
        self.assertEqual(seen, [True, True], "both verdicts fire with the pane lock free")

    def test_a_hookless_send_is_unchanged(self):
        pane = SendRefusesToConcatenate._RecordingPane(["an interrupt-restored prompt"])
        km._TMUX = pane
        km._tmux_send(SESSION, "the composer message", _async=False)
        self.assertEqual(pane.buffers, ["the composer message"])
        self.assertIn(("Enter",), pane.keys_sent)


class TmuxSettersTypeTheClisOwnCommand(unittest.TestCase):
    """TmuxBackend has no control channel: set_model and set_effort type the CLI's own command into the pane
    (SessionBackend documents the per-backend split). The one asymmetry is pinned here. `/model X` opens a
    confirmation in the TUI, so set_model asks _tmux_send for the second Enter (model_cmd=True): the
    dashboard cannot press it in the pane, and the next send's clear-guard would refuse to paste over the
    open dialog. `/effort X` applies in place and gets no such Enter. A recorder stands in
    for _tmux_send (the real one sleeps before the confirm Enter), so this pins the call shape, not the
    keystrokes; `**kw` keeps the recorder valid if the signature grows."""

    def test_set_model_asks_for_the_confirm_enter_and_set_effort_does_not(self):
        rec = []
        saved_send, saved_name = km._tmux_send, km._name_of
        km._tmux_send = lambda name, text, model_cmd=False, **kw: rec.append((name, text, model_cmd))
        km._name_of = lambda sid: SESSION
        try:
            be = km.TmuxBackend()
            self.assertTrue(be.set_model("sid-tmux", "opus"))
            self.assertTrue(be.set_effort("sid-tmux", "high"))
        finally:
            km._tmux_send, km._name_of = saved_send, saved_name
        self.assertEqual(rec, [(SESSION, "/model opus", True), (SESSION, "/effort high", False)])


class CheckedPrimitivesReadTheExitCode(unittest.TestCase):
    """The contract of TmuxBackend's checked variants, on the real class: True iff tmux itself answered 0.
    An exec failure or a timeout (`_run` → None), a missing tmux (the same None) and a nonzero exit all read
    as NOT done — and the argv is byte-identical to the forgiving twin's, so the two cannot drift apart."""

    TEXT = "the composer message"

    def _backend(self, result):
        tb = km.TmuxBackend()
        seen = []
        tb._run = lambda args, t=3: seen.append(list(args)) or result
        tb._fire = lambda args, t=3: seen.append(list(args))
        return tb, seen

    def _checked(self, tb):
        return [tb.set_buffer_checked(self.TEXT), tb.paste_buffer_checked(SESSION),
                tb.send_keys_checked(SESSION, "Enter")]

    def test_a_zero_exit_is_done_and_the_argv_matches_the_forgiving_twin(self):
        tb, seen = self._backend(types.SimpleNamespace(returncode=0, stdout="", stderr=""))
        self.assertEqual(self._checked(tb), [True, True, True])
        checked_argv = list(seen)
        del seen[:]
        tb.set_buffer(self.TEXT)
        tb.paste_buffer(SESSION)
        tb.send_keys(SESSION, "Enter")
        self.assertEqual(checked_argv, seen, "the checked variant issues exactly what its twin issues")
        self.assertEqual(checked_argv[0][:1], ["set-buffer"])
        self.assertEqual(checked_argv[1][:1], ["paste-buffer"])
        self.assertEqual(checked_argv[2], ["send-keys", "-t", SESSION, "Enter"])

    def test_a_nonzero_exit_is_not_done(self):
        # tmux 3.4: a dead server, a missing session and a missing buffer all exit 1
        tb, _ = self._backend(types.SimpleNamespace(returncode=1, stdout="", stderr="no server running"))
        self.assertEqual(self._checked(tb), [False, False, False])

    def test_an_exec_failure_or_absent_tmux_is_not_done(self):
        # `_run` answers None when tmux is unavailable, when the exec raises, and when the child times out
        tb, _ = self._backend(None)
        self.assertEqual(self._checked(tb), [False, False, False])


class PaneLockSerializesInterruptAndSend(unittest.TestCase):
    """The interrupt's post-Esc kill loop and a send's clear+paste+Enter ran unserialized on one pane, so
    the loop could read a just-pasted message as leftover and C-u it away in the pre-Enter gap — Enter
    then submitted an empty box (PR-741 review, 2026-08-27). Both sequences now hold the pane's lock;
    these pin that each side actually waits for it."""

    def setUp(self):
        self._saved_tmux = km._TMUX
        km._pane_io_locks.clear()

    def tearDown(self):
        km._TMUX = self._saved_tmux
        km._pane_io_locks.clear()

    def test_a_send_waits_out_the_lock_holder_and_the_paste_is_never_sniped(self):
        pane = SendRefusesToConcatenate._RecordingPane(["an interrupt-restored prompt"])
        km._TMUX = pane
        lock = km._pane_io_lock(SESSION)
        lock.acquire()                                  # stand in for an interrupt mid-drain
        t = threading.Thread(target=km._tmux_send, args=(SESSION, "the composer message"),
                             kwargs={"_async": False}, daemon=True)
        try:
            t.start()
            t.join(0.4)
            self.assertTrue(t.is_alive(), "the send is waiting on the pane lock")
            self.assertEqual(pane.pastes, [], "nothing pastes while another sequence owns the pane")
        finally:
            lock.release()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(pane.buffers, ["the composer message"])
        self.assertIn(("Enter",), pane.keys_sent, "released → the send completes intact")

    def test_an_interrupt_stops_the_turn_at_once_but_clears_under_the_lock(self):
        # Esc rides OUTSIDE the lock on purpose — the user's Stop must not wait behind a send mid-paste —
        # while the wipe of the restored prompt serializes like any other read-decide-keystroke sequence.
        pane = _FakePane(["a restored prompt"])
        km._TMUX = pane
        lock = km._pane_io_lock(SESSION)
        lock.acquire()                                  # stand in for a send mid-sequence
        t = threading.Thread(target=km._interrupt, args=(SESSION,), kwargs={"_async": False}, daemon=True)
        try:
            t.start()
            t.join(0.6)                                 # past the 0.15s restore beat
            self.assertIn(("Escape",), pane.keys_sent, "the Stop lands immediately")
            self.assertNotIn(("C-u",), pane.keys_sent, "the wipe waits for the pane")
        finally:
            lock.release()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertIn(("C-u",), pane.keys_sent, "released → the wipe runs")
        self.assertEqual(km._box_text(_pane(pane.box_lines)), "")


if __name__ == "__main__":
    unittest.main()
