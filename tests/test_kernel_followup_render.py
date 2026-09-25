"""Follow-up messages render cleanly (the user 2026-06-27): a romp follow-up prepends the goal context as a
`> …` blockquote and trails <!-- romp-* --> markers. For the chat we strip both and keep just the body,
surfacing the goal title separately so the turn shows a compact "Follow-up · <goal>" header. Applied to BOTH
landed human turns and pending queued messages so the two render the same. SYNTHETIC fixtures only."""
import inspect
import os
import unittest
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

FOLLOWUP = ("> Add a widget-count field to the report header (done)\n"
            "> The question was fully answered: the count renders from the live store on each pass.\n\n"
            "Can you also surface that count in the sidebar so it updates as things change?\n\n"
            "<!-- romp-injected --><!-- romp-goal-id: 11111111-2222-3333-4444-555555555555:g2 -->")


class SplitFollowup(unittest.TestCase):
    def test_strips_the_quote_and_markers_keeps_body_and_goal(self):
        goal, body, fu, ctx = km._split_followup(FOLLOWUP)
        self.assertTrue(fu)
        self.assertEqual(goal, "Add a widget-count field to the report header (done)")
        self.assertEqual(body, "Can you also surface that count in the sidebar so it updates as things change?")
        self.assertNotIn("romp-goal-id", body, "the comment marker is gone")
        self.assertNotIn(">", body, "the goal-context quote is gone")

    def test_context_carries_the_full_stripped_quote_for_the_expandable_header(self):
        # the ↩ Follow-up header is click-expandable (the user 2026-07-01): ctx = ALL quote lines, not just
        # the title line, so the chat can show exactly what rode along with the message. Display-only.
        _goal, _body, _fu, ctx = km._split_followup(FOLLOWUP)
        self.assertEqual(ctx, "Add a widget-count field to the report header (done)\n"
                              "The question was fully answered: the count renders from the live store on each pass.")
        self.assertNotIn("romp-goal-id", ctx, "markers are plumbing, not context")

    def test_plain_message_passes_through(self):
        goal, body, fu, ctx = km._split_followup("just a normal message")
        self.assertFalse(fu)
        self.assertIsNone(goal)
        self.assertIsNone(ctx)
        self.assertEqual(body, "just a normal message")

    def test_no_goal_line_still_strips_markers_and_flags_followup(self):
        goal, body, fu, ctx = km._split_followup("hey can you retry that\n\n<!-- romp-goal-id: S:g1 -->")
        self.assertTrue(fu)
        self.assertEqual(body, "hey can you retry that")
        self.assertIsNone(goal, "no quote → no goal title, but still a follow-up")
        self.assertIsNone(ctx, "no quote → nothing to expand")

    def test_build_session_applies_it_to_queued_and_landed(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"kind": "queued", "texts": qmsgs', src, "queued ships per-message md objects")
        self.assertIn("_split_followup(_strip_fork_opener(t))", src, "each queued message is cleaned, fork opener and all")
        self.assertIn("fu_goal, fu_body, fu, fu_ctx = _split_followup(prompt)", src, "landed human turns are cleaned")
        self.assertIn('ev["followUp"] = True', src)
        self.assertIn('ev["fuCtx"] = fu_ctx', src, "landed follow-ups carry the expandable context")
        self.assertIn('m["fuCtx"] = ctx', src, "queued follow-ups carry it too (pending == landed)")


if __name__ == "__main__":
    unittest.main()
