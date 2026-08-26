#!/usr/bin/env python3
"""The rename ping (the user 2026-08-24; settle-boundary form 2026-08-25 pm): a renamed session
hears its OWN new name — rename() stamps the reg (renameNote, restart-proof, ONLY when prior turns
exist under the old name), and the ping delivers at a turn's SETTLE as its own machine-dressed
turn. The enqueue-AHEAD form folded: the CLI batches every message that arrives before a turn
starts into ONE user record, so the user's own words rendered inside the ping's machine bubble
(the 2026-08-25 screenshot: one romp-badged bubble holding the ping + the user's reply-quote +
their whole message). Three gates make that fold unreachable: settle-only delivery (the user is
answered first), the empty-queue guard (a queued message would share the pre-turn window), and the
drain's feed-hold until the ping's turn streams its first message — an exact event — after which a
racing send lands mid-turn as its own record by the CLI's own design. Voice pinned in
test_injected_voice.py. Deterministic: reg-level + a stub session, no real claude processes."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_renameping", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

SID = "aaaaaaaa-1111-2222-3333-444444444444"
SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


def _backend(d):
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)


class RenamePing(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name)
        (Path(self.td.name) / "names").mkdir(parents=True, exist_ok=True)
        self.cwd = str(Path(self.td.name) / "proj")
        Path(self.cwd).mkdir()
        sb.write_reg(Path(self.td.name), SID, {"sid": SID, "name": "web", "cwd": self.cwd,
                                               "lastSid": SID})

    def tearDown(self):
        self.td.cleanup()

    def _write_history(self):
        # one prior turn under the old name — the transcript IS the record of prior turns
        tp = Path(sb.transcript_path(self.cwd, SID))
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text('{"type": "user", "uuid": "u1"}\n')

    def test_rename_with_history_stamps_the_pending_note_beside_the_name(self):
        self._write_history()
        self.assertTrue(self.be.rename(SID, "tests"))
        reg = sb.read_reg(Path(self.td.name), SID)
        self.assertEqual(reg.get("name"), "tests")
        self.assertEqual(reg.get("renameNote"), "tests",
                         "reg-persisted, so the ping survives a kernel restart unspoken")

    def test_rename_before_the_first_turn_pings_nothing(self):
        # the 2026-08-25 sighting's second half: a brand-new session has no stale self-knowledge
        # to correct — it learns its name the normal way, and its user's first words stay first
        self.assertTrue(self.be.rename(SID, "tests"))
        reg = sb.read_reg(Path(self.td.name), SID)
        self.assertEqual(reg.get("name"), "tests", "the rename itself still lands")
        self.assertIsNone(reg.get("renameNote"), "…but no ping is owed")

    def test_rename_of_an_unknown_sid_stamps_nothing(self):
        self.assertFalse(self.be.rename("99999999-0000-1111-2222-333333333333", "tests"))

    class _StubSession:
        def __init__(self, sid, pending=()):
            import threading
            self.sid = sid
            self._lock = threading.Lock()
            self._pending = list(pending)
            self.sent = []

        def enqueue(self, text):
            self.sent.append(text)

    def test_settle_delivery_ships_the_dressed_ping_alone_and_once(self):
        self._write_history()
        self.be.rename(SID, "tests")
        s = self._StubSession(SID)
        self.assertTrue(self.be._deliver_rename_ping(s))
        self.assertEqual(len(s.sent), 1, "one turn of its own")
        self.assertTrue(s.sent[0].startswith(sb.RENAME_PING_HEAD), "the machine dress leads the record")
        self.assertIn("'tests'", s.sent[0], "…and it names the new name")
        self.assertIsNone(sb.read_reg(Path(self.td.name), SID).get("renameNote"), "the note is spent")
        self.assertFalse(self.be._deliver_rename_ping(s), "…and a second settle delivers nothing")

    def test_a_queued_turn_holds_the_note_for_a_later_settle(self):
        # the empty-queue guard: a queued message would share the ping's pre-turn window, which is
        # exactly the fold that put the user's words in the machine bubble
        self._write_history()
        self.be.rename(SID, "tests")
        s = self._StubSession(SID, pending=["already queued"])
        self.assertFalse(self.be._deliver_rename_ping(s))
        self.assertEqual(s.sent, [], "nothing fed beside a queued turn")
        self.assertEqual(sb.read_reg(Path(self.td.name), SID).get("renameNote"), "tests",
                         "the note survives for a later, empty-queue settle")

    def test_the_fold_gates_are_pinned_at_source(self):
        # the fold's mechanics live in async plumbing a unit test can't drive — pin the three gates
        self.assertNotIn("RENAME_NUDGE % _reg", SRC, "no send()-time delivery of any form remains")
        self.assertNotIn('text = "%s\\n\\n%s" % (RENAME_NUDGE', SRC, "the string-prepend form stays gone")
        self.assertIn("self.backend._deliver_rename_ping(self)", SRC, "delivery hooks the turn's settle")
        self.assertIn("blocked = blocked or self._ping_feeding", SRC,
                      "the drain holds every feed while the ping's turn has not started")
        self.assertIn("if item.startswith(RENAME_PING_HEAD):", SRC, "…armed exactly when a ping is fed")
        self.assertIn("self._ping_feeding = False   # a reconnect restarts the feed", SRC,
                      "…and a reconnect clears a stale hold instead of wedging the queue")
        self.assertNotIn("romp-injected", sb.RENAME_NUDGE,
                         "the constant stays bare prose; the dress is added only on the separate record")


if __name__ == "__main__":
    unittest.main()
