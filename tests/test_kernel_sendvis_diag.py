#!/usr/bin/env python3
"""/diag/sendvis (the user 2026-07-20): a read-only snapshot of every input the chat build consults to
render an in-flight send — backend routing, the backend queue, parked kernel ops, live echoes. When a
sent message is invisible, this names the layer that dropped it instead of another round of black-box
probing; failures inside the diagnostic report as strings, never silently. SYNTHETIC fixtures only."""
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_sendvis", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class _FakeSdk:
    def owns(self, sid):
        return sid == SID

    def pending_queued(self, sid):
        return ["queued reply text"]

    def live_atoms(self, sid):
        return [{"uuid": "echo:1", "t": 123, "_echo_text": "an in-flight echo"},
                {"uuid": "w1", "t": 124}]


class SendVisDiag(unittest.TestCase):
    def setUp(self):
        self._saved = (km._sdk, dict(km._pending_ops))
        km._sdk = lambda: self.be
        self.be = _FakeSdk()
        km._pending_ops.clear()

    def tearDown(self):
        km._sdk = self._saved[0]
        km._pending_ops.clear()
        km._pending_ops.update(self._saved[1])

    def test_snapshot_names_every_visibility_layer(self):
        km._pending_ops[SID] = [("send", "a parked message body", "human")]
        out = km._sendvis_diag(SID)
        self.assertEqual(out["sid"], SID)
        self.assertEqual(out["backend"], "sdk")
        self.assertTrue(out["sdkOwns"])
        self.assertEqual(out["pendingQueued"], ["queued reply text"])
        self.assertEqual(out["pendingOps"], [["send", "a parked message body"]])
        echoes = [a for a in out["liveAtoms"] if a["echo"]]
        self.assertEqual([a["echo"] for a in echoes], ["an in-flight echo"])
        self.assertIn("compacting", out)
        self.assertIn("tmuxEchoes", out)

    def test_a_tmux_echo_row_says_whether_it_is_settled(self):
        # A settled loss and an in-flight send read identically here until `dropped` was carried (the user
        # 2026-08-26): the diagnostic's job is naming the layer, and "which of these is still going out"
        # was the exact question it could not answer.
        km._tmux_echo.clear()
        km._tmux_echo_add(SID, "sent and still going out")
        km._tmux_echo_add(SID, "overtaken, never delivered")
        try:
            for echo_atom in km._tmux_echo[SID].values():
                if echo_atom["_echo_text"].startswith("overtaken"):
                    echo_atom["dropped"] = True
            rows = {r["echo"]: r["dropped"] for r in km._sendvis_diag(SID)["tmuxEchoes"]}
        finally:
            km._tmux_echo.clear()
        self.assertEqual(rows, {"sent and still going out": False, "overtaken, never delivered": True})

    def test_route_is_wired_and_read_only(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('if p == "/diag/sendvis":', src)
        self.assertIn("_sendvis_diag((q.get(\"sid\") or [\"\"])[0])", src)


if __name__ == "__main__":
    unittest.main()
