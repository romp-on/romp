#!/usr/bin/env python3
"""Restarts never lose typed input (the user 2026-08-23, their strongest point in the restart audit):
a HUMAN send whose CLI died holding it — provably lost (not in the surviving queue) and verified
never-landed by a direct transcript scan — is RE-DELIVERED through the persisted queue in send
order, recreating the pre-restart state, instead of parking as a never-delivered bubble waiting on
a manual restore. romp-authored echoes keep the flag path (re-delivering a nudge double-nudges),
and a landed-but-unpruned echo never re-delivers (the scan is the duplicate guard). SYNTHETIC."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_redeliver", os.path.join(BIN, "romp-event-model")).load_module()
sb = SourceFileLoader("romp_sdk_backend_redeliver2", os.path.join(HERE, "..", "kernel", "sdk_backend.py")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class Redelivery(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.state = self.td
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.td, "claude")
        self.cwd = os.path.join(self.td, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        self.tpath = tp
        open(tp, "w").close()

        class BE:
            state_dir = None
            _live = {}
            _reg_lock = __import__("threading").RLock()
            _persisted = []
            _logs = []

            def _log(self, msg, problem=False):
                self._logs.append(msg)

            def _persist_echoes(self, sid):
                self._persisted.append(sid)

            def _wake_push(self):
                pass
            _text_landed = sb.SdkBackend._text_landed if hasattr(sb, "SdkBackend") else None
        self.be = BE()
        # bind the real methods under test onto the stub
        import pathlib
        self.be.state_dir = pathlib.Path(self.td)
        self.be._mark_dropped_echoes = sb.SdkBackend._mark_dropped_echoes.__get__(self.be)
        self.be._text_landed = sb.SdkBackend._text_landed.__get__(self.be)
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "alive": True, "cwd": self.cwd,
                                              "lastSid": SID, "queue": []})

    def _echo(self, text, author="human", t=100):
        self.be._live.setdefault(SID, {})["echo:" + text[:8]] = {
            "_echo_text": text, "author": author, "t": t}

    def _reg_queue(self):
        return (sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or []

    def tearDown(self):
        self.be._live.clear()
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def test_lost_human_send_re_enters_the_queue_in_send_order(self):
        self._echo("first typed message", t=100)
        self._echo("second typed message", t=200)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), ["first typed message", "second typed message"])
        for a in self.be._live[SID].values():
            self.assertNotIn("dropped", a, "a re-queued send renders as queued, never as lost")

    def test_landed_but_unpruned_echo_never_redelivers(self):
        with open(self.tpath, "w") as f:
            f.write(json.dumps({"type": "user", "uuid": "u1",
                                "message": {"role": "user",
                                            "content": [{"type": "text", "text": "already landed words"}]}}) + "\n")
        self._echo("already landed words")
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), [], "the transcript scan is the duplicate guard")
        self.assertTrue(any(a.get("dropped") for a in self.be._live[SID].values()),
                        "…so it takes the flag path (self-correcting on the next build)")

    def test_romp_authored_echoes_keep_the_flag_path(self):
        self._echo("a nudge body", author="romp")
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), [], "re-delivering a nudge would double-nudge")
        self.assertTrue(any(a.get("dropped") for a in self.be._live[SID].values()))

    def test_surviving_queue_texts_stay_ahead_and_undropped(self):
        self._echo("still queued text")
        self._echo("lost text", t=300)
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "alive": True, "cwd": self.cwd,
                                              "lastSid": SID, "queue": ["still queued text"]})
        self.be._mark_dropped_echoes(SID, ["still queued text"])
        self.assertEqual(self._reg_queue(), ["still queued text", "lost text"],
                         "the surviving queue keeps its place; the re-delivery lands behind it")


if __name__ == "__main__":
    unittest.main()
