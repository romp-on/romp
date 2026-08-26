#!/usr/bin/env python3
"""SDK-backed (non-tmux) sessions must be VISIBLE + reachable to the Romp Postal Service (the user via ui,
2026-06-26). local_agents() now reads the kernel's UNIFIED GET /sessions (tmux + SDK) — the kernel merges
both backends — so an SDK session the kernel reports is a live postal agent here too: a send to it DELIVERS
instead of resolving DEAD and PARKING. The bus no longer reads the SDK registry directly or shells tmux;
the kernel is the single source. Synthetic only — placeholder UUIDs, hostname-free.
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

SID = "11111111-2222-3333-4444-555555555555"


class SdkAgentsVisibleToPostal(unittest.TestCase):
    def setUp(self):
        # stub the kernel fetch: the kernel reports the OPEN SDK session (alive) in its unified /sessions.
        self._saved = pm._kernel_sessions
        pm._kernel_sessions = lambda threads=False: [
            {"id": SID, "name": "sdksess", "state": "waiting", "dir": "/work/dir",
             "bg": "", "fg": "", "working": "", "backend": "sdk"}]

    def tearDown(self):
        pm._kernel_sessions = self._saved

    def test_alive_sdk_session_is_a_live_local_agent(self):
        a = {x["id"]: x for x in pm.local_agents()}.get(SID)
        self.assertIsNotNone(a, "an SDK session the kernel reports must be a live postal agent (else sends park)")
        self.assertEqual(a["name"], "sdksess")
        self.assertEqual(a["dir"], "/work/dir")
        self.assertEqual(a["state"], "waiting")     # from the kernel's unified state
        self.assertFalse(a["remote"])

    def test_send_resolves_an_sdk_session_ALIVE_not_dead(self):
        # a send to an open SDK session resolves to its live id (delivers); live-only
        # addressing means a non-live name would resolve to nothing instead.
        self.assertEqual(pm._recip_id_for("sdksess"), SID)

    def test_session_absent_from_kernel_is_not_a_live_agent(self):
        pm._kernel_sessions = lambda threads=False: []            # the kernel reports nothing live (dead/ended) → not an agent
        self.assertNotIn(SID, {x["id"] for x in pm.local_agents()})

    def test_unreachable_kernel_yields_no_local_agents(self):
        # _kernel_sessions returns [] on any failure (the real impl swallows the urlopen error); the bus then
        # shows no local agents rather than shelling tmux behind the abstraction.
        pm._kernel_sessions = lambda threads=False: []
        self.assertEqual(pm.local_agents(), [])


if __name__ == "__main__":
    unittest.main()
