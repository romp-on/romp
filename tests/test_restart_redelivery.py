#!/usr/bin/env python3
"""Restarts never lose typed input (the user 2026-08-23, their strongest point in the restart audit):
a HUMAN send whose CLI died holding it — provably lost (not in the surviving queue) and verified
never-landed by a direct transcript scan — is RE-DELIVERED through the persisted queue in send
order, recreating the pre-restart state, instead of parking as a never-delivered bubble waiting on
a manual restore. romp-authored echoes keep the flag path (re-delivering a nudge double-nudges),
and a landed-but-unpruned echo never re-delivers (the scan is the duplicate guard) — nor is it flagged
lost: it landed, the next build's by-text prune retires it (2026-09-06; the scan also reads the
queued_command attachment an absorbed send leaves — tests/test_sdk_echo_durability.py). SYNTHETIC."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_redeliver", os.path.join(BIN, "romp-event-model"))
sb = load_source("romp_sdk_backend_redeliver2", os.path.join(HERE, "..", "kernel", "sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
# Send stamps are RECENT epoch seconds (2026-09-12): re-delivery has an age line (REDELIVER_MAX_AGE_S), so a
# stamp of 100 (1970) would read as a stale send and take the flag path instead of the re-feed under test.
T0 = int(__import__("time").time()) - 600


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
            _live_lock = __import__("threading").RLock()   # the live tail's lock (_mark_dropped_echoes selects under it)
            _persisted = []
            _logs = []

            def _log(self, msg, problem=False):
                self._logs.append(msg)

            def _persist_echoes(self, sid):
                self._persisted.append(sid)

            def _wake_push(self):
                pass

            def _touch_live(self, sid):
                pass                      # the live-tail revision hook (2026-09-03): a stub needs no counter
            _text_landed = sb.SdkBackend._text_landed if hasattr(sb, "SdkBackend") else None
        self.be = BE()
        # bind the real methods under test onto the stub
        import pathlib
        self.be.state_dir = pathlib.Path(self.td)
        self.be._mark_dropped_echoes = sb.SdkBackend._mark_dropped_echoes.__get__(self.be)
        self.be._text_landed = sb.SdkBackend._text_landed.__get__(self.be)
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "alive": True, "cwd": self.cwd,
                                              "lastSid": SID, "queue": []})

    def _echo(self, text, author="human", t=T0):
        self.be._live.setdefault(SID, {})["echo:" + text[:8]] = {
            "_echo_text": text, "author": author, "t": t}

    def _reg_queue(self):
        return (sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or []

    def tearDown(self):
        self.be._live.clear()
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def test_lost_human_send_re_enters_the_queue_in_send_order(self):
        self._echo("first typed message", t=T0)
        self._echo("second typed message", t=T0 + 100)
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
        self.assertFalse(any(a.get("dropped") for a in self.be._live[SID].values()),
                         "…and a found text is not flagged either: it landed, the by-text prune retires it")

    def test_romp_authored_echoes_keep_the_flag_path(self):
        self._echo("a nudge body", author="romp")
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), [], "re-delivering a nudge would double-nudge")
        self.assertTrue(any(a.get("dropped") for a in self.be._live[SID].values()))

    def test_surviving_queue_texts_stay_ahead_and_undropped(self):
        self._echo("still queued text")
        self._echo("lost text", t=T0 + 200)
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "alive": True, "cwd": self.cwd,
                                              "lastSid": SID, "queue": ["still queued text"]})
        self.be._mark_dropped_echoes(SID, ["still queued text"])
        self.assertEqual(self._reg_queue(), ["still queued text", "lost text"],
                         "the surviving queue keeps its place; the re-delivery lands behind it")


class BootDeliversARefedSend(unittest.TestCase):
    """The boot half, end to end. SdkBackend.__init__ lists the registries ONCE, runs the echo
    reseed — whose re-delivery arm above puts a lost human send back into a registry's queue ON
    DISK — and then hands the SAME listing to _boot_reconcile. A row listed before that write
    still shows the old queue, so a session whose only reason to resume was the re-queued text
    read an empty queue in the sweep and stayed dormant: re-queued, but delivered only at the next
    spawn or boot. The sweep must decide from the registry as it is on disk. SYNTHETIC; no SDK."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.state = Path(self.td)
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.td, "claude")
        self.cwd = os.path.join(self.td, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w").close()                          # an empty transcript: the text never landed

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def _boot(self, echoes, queue=(), **reg_extra):
        """Construct the backend the way the kernel does (reconcile=True) over one alive registry
        whose turn FINISHED ('waiting' tail — not a cut), and wait for the sweep to end. Returns the
        sids the sweep resumed. Waits on the sweep's own completion, never on a sleep."""
        sb.write_reg(self.state, SID, {"sid": SID, "alive": True, "cwd": self.cwd, "lastSid": SID,
                                       "queue": list(queue), "echoes": echoes, **reg_extra})
        sb.append_state(self.state, SID, "waiting")
        ensured, done = [], threading.Event()
        real = sb.SdkBackend._boot_reconcile

        def swept(be, regs):
            try:
                real(be, regs)
            finally:
                done.set()

        with mock.patch.object(sb.SdkBackend, "_boot_reconcile", swept), \
             mock.patch.object(sb.SdkBackend, "_ensure",
                               lambda be, sid, on_boot_settled=None:
                               (ensured.append(sid), on_boot_settled and on_boot_settled())[0]), \
             mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            sb.SdkBackend(self.td, "/bin/true", lambda *a, **k: None, reconcile=True)
            self.assertTrue(done.wait(10), "the boot sweep ran to its end")
        return ensured

    def _reg_queue(self):
        return (sb.read_reg(self.state, SID) or {}).get("queue") or []

    def test_a_re_queued_send_earns_the_resume_the_same_boot(self):
        ensured = self._boot([{"t": T0, "text": "typed just before the restart", "author": "human"}])
        self.assertEqual(self._reg_queue(), ["typed just before the restart"],
                         "the reseed re-queued the lost send (the half that already worked)")
        self.assertEqual(ensured, [SID], "…and the same boot's sweep resumes the session to deliver it")

    def test_a_dormant_threads_re_queued_reply_earns_the_resume_too(self):
        # a comment thread is never auto-resumed at boot EXCEPT for a queued reply of the user's own
        # — and a re-queued reply is exactly that
        ensured = self._boot([{"t": T0, "text": "a reply the thread never started", "author": "human"}],
                             threadOf="11111111-2222-3333-4444-000000000000")
        self.assertEqual(self._reg_queue(), ["a reply the thread never started"])
        self.assertEqual(ensured, [SID])

    def test_a_finished_session_with_nothing_re_queued_stays_lazy(self):
        # the resume is keyed on the re-queued text, not on every alive registry
        self.assertEqual(self._boot([]), [])


if __name__ == "__main__":
    unittest.main()
