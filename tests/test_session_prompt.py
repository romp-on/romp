#!/usr/bin/env python3
"""The romp harness prompt (claude/romp-session-prompt.md) is appended to EVERY session's system prompt
(tmux via --append-system-prompt, SDK via the designed system_prompt field). It must keep its EXPLICIT
done/not-done reporting instruction, so a session never reports — and the closer never marks — partial
work as complete (the user 2026-06-26: things were getting marked completed that weren't). These pin the
load-bearing intent so a future "make it lighter" edit can't quietly drop it."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
PROMPT = os.path.join(os.path.dirname(HERE), "claude", "romp-session-prompt.md")


class SessionPrompt(unittest.TestCase):
    def setUp(self):
        with open(PROMPT) as f:
            self.text = f.read()
        # collapse line wraps so phrase matches survive prose reflow
        self.flat = re.sub(r"\s+", " ", self.text).lower()

    def test_requires_an_explicit_not_done_account(self):
        # not just "say when done" — the NOT-done side must be called out as explicitly as the done side
        self.assertIn("not done", self.flat,
                      "the prompt must require an explicit account of what is NOT done, not only 'done'")

    def test_asks_for_a_bulleted_done_notdone_list(self):
        # the user's ask: a clear, direct, bulleted Done / Not done list when there's a mix
        self.assertRegex(self.flat, r"done\s*/\s*not done",
                         "the prompt must ask for a bulleted Done / Not done list")

    def test_forbids_implying_completion_while_work_remains(self):
        # the anti-false-completion rule that protects against the closer marking partial work complete
        self.assertIn("while pieces remain", self.flat,
                      "the prompt must forbid stating/implying a task is complete while pieces remain")

    def test_preliminary_step_is_not_the_work(self):
        # reading/mapping/planning a refactor is not doing it (the g405 false-completion pattern)
        self.assertIn("preliminary step", self.flat,
                      "the prompt must say a preliminary step (reading/mapping/planning) is not finishing the work")

    def test_licenses_persisting_through_daunting_work(self):
        # the user 2026-08-11 (after Anthropic's riemann-zeta post, where the operator's whole input was
        # keep-going encouragement): the prompt must license continuing on work that merely LOOKS too big
        # or uncertain — stopping is reserved for decisions that are genuinely the user's to make.
        self.assertIn("talk yourself out of", self.flat,
                      "the prompt must tell the session not to abandon work that merely looks daunting")
        self.assertIn("make progress", self.flat,
                      "the prompt must license taking any visible path to progress without checking in")

    def test_asks_for_cleanup_when_work_is_fully_done(self):
        # the user 2026-08-14 (from a Claude Code team member's advice, via the systems audit): sessions
        # should clean up their own scaffolding — worktrees, merged branches, scratch files — once work
        # is PUBLISHED, so stale worktrees stop accumulating. Guarded on both sides: never anything
        # holding uncommitted/unpushed work (the July-22 lost-prototype lesson), and never another
        # session's things.
        self.assertIn("clean up after yourself", self.flat,
                      "the prompt must carry a cleanup-when-done section")
        self.assertIn("worktree", self.flat,
                      "the cleanup ask must name worktrees, the scaffolding that actually accumulates")
        self.assertIn("never delete anything holding uncommitted or unpushed work", self.flat,
                      "cleanup must be fenced off from the park-your-work safety net")
        self.assertIn("only what you yourself created", self.flat,
                      "cleanup must not license touching peer sessions' worktrees/branches")

    def test_housekeeping_note_preexplains_romp_artifacts(self):
        # The ONE place romp is named to a session (the user 2026-07-25): pre-explain the artifacts
        # every session eventually sees — [romp] notices and <!-- romp-* --> comments — so a kernel
        # restart notice reads as explained information, not an unexplained system voice.
        self.assertIn("romp", self.flat,
                      "the housekeeping note must name romp so its artifacts have an anchor")
        self.assertIn("[romp]", self.text, "the note must show the bracketed-notice form")
        self.assertIn("<!-- romp-", self.text, "the note must show the HTML-comment form")
        self.assertIn("ignore", self.flat,
                      "the note must tell the session to ignore the bookkeeping")


if __name__ == "__main__":
    unittest.main()
