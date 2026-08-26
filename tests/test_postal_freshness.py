#!/usr/bin/env python3
"""list_agents working-note freshness (the user 2026-06-24): a session's set_working note is an ownership
CLAIM that's only LIVE while the session is actively WORKING. An idle/waiting session's note is a claim
from a finished turn, so format_agents flags it '(idle now — claim may be stale)' — letting a peer discount
a stale claim by READING list_agents instead of waking the session to ask "do you still own this?".

Synthetic only — placeholder UUIDs, no real session data.
"""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = SourceFileLoader("romp_postal", os.path.join(BIN, "romp-postal-service")).load_module()

STALE = "(idle now — claim may be stale)"


class WorkingNoteFreshness(unittest.TestCase):
    def _line(self, agent, me="self"):
        # format_agents renders one line per agent; return the line for this agent.
        out = pm.format_agents([agent], me)
        return out.strip()

    def test_working_session_note_is_not_flagged_stale(self):
        line = self._line({"name": "bugs", "id": "x", "branch": "main",
                           "working": "feed.ts", "state": "working"})
        self.assertIn("feed.ts", line)
        self.assertNotIn(STALE, line, "a session that is actively working holds a LIVE claim — no stale flag")

    def test_idle_session_note_is_flagged_stale(self):
        line = self._line({"name": "ui", "id": "y", "branch": "main",
                           "working": "render.ts", "state": "idle"})
        self.assertIn("render.ts", line)
        self.assertIn(STALE, line, "an idle session's note is a claim from a finished turn → flag it")

    def test_waiting_session_note_is_flagged_stale(self):
        # 'waiting' (at the prompt) is also not actively working → the claim may be stale
        line = self._line({"name": "ui", "id": "y", "branch": "main",
                           "working": "render.ts", "state": "waiting"})
        self.assertIn(STALE, line)

    def test_no_note_means_no_flag(self):
        line = self._line({"name": "logo", "id": "z", "branch": "main", "working": "", "state": "idle"})
        self.assertNotIn(STALE, line, "no published claim → nothing to flag")
        self.assertNotIn("—", line, "no working-note → no note separator at all")

    def test_missing_state_does_not_flag(self):
        # a remote agent (or a session whose @claude-state isn't set) carries no state → don't guess stale
        line = self._line({"name": "remote", "id": "r", "branch": "main", "working": "api.ts", "state": ""})
        self.assertIn("api.ts", line)
        self.assertNotIn(STALE, line, "unknown state must not be reported as stale")

    def test_local_agents_maps_the_kernel_session_fields(self):
        # local_agents now consumes the kernel's unified GET /sessions rows (no tmux shell); each row's
        # state / working / dir carry straight into the agent row used for working-note freshness.
        saved = pm._kernel_sessions
        try:
            pm._kernel_sessions = lambda threads=False: [
                {"id": "sid-1", "name": "bugs", "state": "working", "dir": "/dir", "working": "feed.ts", "backend": "tmux"},
                {"id": "sid-2", "name": "ui", "state": "idle", "dir": "/dir", "working": "render.ts", "backend": "sdk"}]
            agents = {a["name"]: a for a in pm.local_agents()}
        finally:
            pm._kernel_sessions = saved
        self.assertEqual(agents["bugs"]["state"], "working")
        self.assertEqual(agents["ui"]["state"], "idle")
        self.assertEqual(agents["ui"]["working"], "render.ts")


if __name__ == "__main__":
    unittest.main()
