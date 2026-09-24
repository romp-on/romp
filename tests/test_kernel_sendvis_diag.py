#!/usr/bin/env python3
"""/diag/sendvis (the user 2026-07-20): a read-only snapshot of every input the chat build consults to
render an in-flight send — backend routing, the backend queue, parked kernel ops, live echoes. When a
sent message is invisible, this names the layer that dropped it instead of another round of black-box
probing; failures inside the diagnostic report as strings, never silently. SYNTHETIC fixtures only."""
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
km = load_source("romp_kernel_sendvis", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_sendvis", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"


class _FakeSdk:
    def owns(self, sid):
        return sid == SID

    def pending_queued(self, sid):
        return ["queued reply text"]

    def live_atoms(self, sid):
        return [{"uuid": "echo:1", "t": 123, "_echo_text": "an in-flight echo"},
                {"uuid": "echo:2", "t": 123, "_echo_text": "overtaken, never delivered", "dropped": True},
                {"uuid": "echo:4", "t": 123, "_echo_text": "refused at the gate", "dropped": True, "refused": True},
                {"uuid": "echo:3", "t": 123, "_echo_text": "delivered, not yet pruned", "_landed": True},
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
        self.assertEqual([a["echo"] for a in echoes], ["an in-flight echo", "overtaken, never delivered", "refused at the gate",
                                                       "delivered, not yet pruned"])
        self.assertIn("compacting", out)

    def test_an_unowned_sid_reads_as_unowned(self):
        # the routing layer named first: a sid no backend owns is "unowned", never a guess at a backend
        out = km._sendvis_diag("22222222-3333-4444-5555-666666666666")
        self.assertEqual(out["backend"], "unowned")
        self.assertFalse(out["sdkOwns"])
        self.assertEqual(out["pendingQueued"], [])
        self.assertEqual(out["liveAtoms"], [])

    def test_an_echo_row_carries_the_backend_flags(self):
        rows = {a["echo"]: (a["dropped"], a["dropReason"], a["landed"])
                for a in km._sendvis_diag(SID)["liveAtoms"] if a["echo"]}
        self.assertEqual(rows, {"an in-flight echo": (False, None, False),
                                "overtaken, never delivered": (True, None, False),
                                "refused at the gate": (True, "refused", False),
                                "delivered, not yet pruned": (False, None, True)})

    def test_a_codex_row_carries_null_flags(self):
        codex = _FakeSdk()
        saved = km._codex
        km._sdk, km._codex = (lambda: None), (lambda: codex)
        try:
            out = km._sendvis_diag(SID)
        finally:
            km._codex = saved
        self.assertEqual(out["backend"], "codex")
        self.assertEqual({(a["dropped"], a["dropReason"], a["landed"]) for a in out["liveAtoms"]}, {(None, None, None)})

    def test_route_is_wired_and_read_only(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('if p == "/diag/sendvis":', src)
        self.assertIn("_sendvis_diag((q.get(\"sid\") or [\"\"])[0])", src)


class SendVisOverARealBackend(unittest.TestCase):
    """The flags as the SDK backend's boot reseed writes them, not as a fake spells them."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                                # never start a real session host from this root
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tpath = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tpath), exist_ok=True)
        open(tpath, "w").close()
        self._saved = km._sdk

    def tearDown(self):
        km._sdk = self._saved
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def test_a_boot_scan_landed_echo_and_a_refused_one(self):
        echoes = [{"t": 123, "text": "landed before the restart", "author": "human", "uuid": "echo:" + "a" * 32,
                   "landed": True},
                  {"t": 124, "text": "refused at the gate", "author": "human", "uuid": "echo:" + "b" * 32,
                   "dropped": True, "refused": True},
                  {"t": 125, "text": "past the age line", "author": "human", "uuid": "echo:" + "c" * 32}]
        sb.write_reg(self.state, SID, {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd,
                                       "lastSid": SID, "queue": [], "echoes": echoes})
        be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)
        km._sdk = lambda: be
        out = km._sendvis_diag(SID)
        self.assertEqual(out["backend"], "sdk")
        rows = {a["echo"]: (a["dropped"], a["dropReason"], a["landed"]) for a in out["liveAtoms"] if a["echo"]}
        self.assertEqual(rows, {"landed before the restart": (False, None, True),
                                "refused at the gate": (True, "refused", False),
                                "past the age line": (True, "stale", False)})


if __name__ == "__main__":
    unittest.main()
