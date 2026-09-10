"""A session HARD-BLOCKED on a live prompt (permission / picker) BEFORE the planner has minted any goal — e.g.
an SDK session that fired an AskUserQuestion on its very first turn — used to be INVISIBLE in the feed: the
hard-block floor can only floor an EXISTING focus card under BLOCKED, and with zero goals there's nothing to
floor, so the block never reached the Blocked column (the user 2026-06-27). build_feed now synthesizes an
ephemeral needs-input placeholder (_blocked_placeholder) carrying a `blocked` badge so feed.ts files it under
BLOCKED. SYNTHETIC fixtures only (placeholder ids, no real data)."""
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

SID = "11111111-2222-3333-4444-555555555555"
COLOR = {"bg": "#123456", "fg": "#ffffff"}


def _card(perm_state):
    # a path that doesn't exist → _parse raises → the placeholder falls back to its generic awaiting text,
    # which is exactly the worst case we must still surface.
    s = {"path": "/nonexistent/TESTHOST/transcript.jsonl"}
    return km._blocked_placeholder(s, "TESTHOST", COLOR, SID, True, 1_700_000_000, perm_state, 1_699_999_900)


class BlockedNoGoal(unittest.TestCase):
    def test_picker_block_makes_a_needs_input_card_with_a_blocked_badge(self):
        c = _card("picker")
        self.assertEqual(c["column"], "needs_input", "filed under the Blocked column")
        self.assertEqual(c["blocked"]["state"], "picker", "carries the live picker block badge")
        self.assertTrue(c["provisional"], "a lightweight placeholder (dim + dashed, no clear/modal)")
        self.assertEqual(c["tree"], [], "no goal node")
        self.assertTrue(c["itemId"].startswith("blocked:"), "stable per-session id, distinct from provisional:")
        self.assertEqual(c["text"], "Awaiting your input", "generic fallback when the prompt can't be parsed")

    def test_permission_block_says_awaiting_your_approval(self):
        c = _card("permission")
        self.assertEqual(c["column"], "needs_input")
        self.assertEqual(c["blocked"]["state"], "permission")
        self.assertEqual(c["text"], "Awaiting your approval")
        self.assertEqual(c["blocked"]["what"], "this session is stopped awaiting your approval")

    def test_build_feed_synthesizes_it_only_when_blocked_with_no_floorable_goal(self):
        src = inspect.getsource(km.build_feed)
        # the synthesis is gated: no working card AND no top goal to floor under BLOCKED (perm_top
        # None; todo_top joined the guard 2026-08-22 — a todo-floored card is had-working-equivalent) ...
        self.assertIn("if not had_working and perm_top is None and todo_top is None and ps:", src)
        # ... and only as the fallback when there's no provisional card and a live perm/picker state
        self.assertIn("elif perm_state in _NEEDS_INPUT_STATES:", src)
        self.assertIn("_blocked_placeholder(s, name, color, fsid, live, now, perm_state", src)

    def test_picker_card_titles_with_the_live_questions_actual_text(self):
        # the most useful title is WHAT input is being asked for — read from the live ask the backend holds
        # (the user 2026-06-29). Patch backend_for→a stub current_ask so no real session is needed.
        class _StubBackend:
            def current_ask(self, sid):
                return {"kind": "single", "header": "Backend", "question": "Use tmux or the SDK backend?",
                        "options": [{"label": "tmux"}, {"label": "SDK"}]}
        orig = km.Sessions.backend_for
        km.Sessions.backend_for = lambda sid: _StubBackend()
        try:
            c = _card("picker")
        finally:
            km.Sessions.backend_for = orig
        self.assertEqual(c["text"], "Use tmux or the SDK backend?", "card shows the question, not the generic line")

    def test_picker_question_is_truncated_with_an_ellipsis(self):
        long_q = "A" * 200
        class _StubBackend:
            def current_ask(self, sid):
                return {"question": long_q, "header": ""}
        orig = km.Sessions.backend_for
        km.Sessions.backend_for = lambda sid: _StubBackend()
        try:
            c = _card("picker")
        finally:
            km.Sessions.backend_for = orig
        self.assertTrue(c["text"].endswith("…"))
        self.assertLessEqual(len(c["text"]), 140)

    def test_placeholder_carries_no_goal_node_so_it_is_replaced_when_the_planner_runs(self):
        # turnId/turnIds empty + provisional → feed.ts dims it and gives it no Clear/Nudge/modal; the real
        # card (minted once the ask is answered and the planner places the work) supersedes it.
        c = _card("picker")
        self.assertIsNone(c["turnId"])
        self.assertNotIn("turnIds", c)   # scaffolding keys retired in the 2026-07-07 contract audit


if __name__ == "__main__":
    unittest.main()
