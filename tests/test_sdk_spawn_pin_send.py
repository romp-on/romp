#!/usr/bin/env python3
"""A fresh spawn's -m kickoff must never vanish into an effort-pin reconnect (2026-08-16).

The incident: `romp new --model <id> --effort <level> -m <briefing>` acked both /new and /send, but the
briefing never started a conversation — its echo sat dropped for 5.5h until a manual re-send. The race:
the effort pin was applied AFTER connect() had been kicked off, so set_effort's request_reconnect armed a
teardown of the just-connected client; the -m send was then FED into that dying client's stdin (inputs()
had already removed it from the persisted queue) and the teardown cancelled the feeder mid-delivery. With
lastSid still empty, the next iteration started a FRESH conversation — the text existed nowhere.

Two independent halves close it:
1. kernel: /new's per-spawn pins are applied BETWEEN spawn() and connect(), so the FIRST connect's
   options carry them (--effort is connect-time) and no reconnect exists to race.
2. backend: the reconnect loop's reconcile carries fed-but-unresulted turn TEXTS across the teardown —
   re-headed onto the queue when no conversation ever materialized (nothing landed → re-feeding cannot
   duplicate), or surfaced as dropped echoes NOW when the conversation is resumable (re-feeding there
   risks a real duplicate; the loss must be visible, not discovered hours later).

SYNTHETIC fixtures only: placeholder UUIDs, invented briefing text, no SDK dependency (SdkSession imports
claude_agent_sdk lazily inside _amain, so the reconcile is drivable with a fake backend here)."""
import os
import re
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_spawnpin", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

SID = "11111111-2222-3333-4444-aaaaaaaaaaaa"
BRIEF = "Kick off: run the staged comparison suite and report embedding drift."


class _FakeBackend:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.dropped_calls = []
        self.retired = 0
    def retire_live_work(self, sid): self.retired += 1
    def _poke(self): pass
    def _deliver_rename_ping(self, s): return False   # settle hook (2026-08-25); no ping in these worlds
    def _update_reg(self, *a, **k): pass
    def _mark_dropped_echoes(self, sid, surviving): self.dropped_calls.append((sid, list(surviving)))
    # the ResultMessage settle's other hooks (mirrors test_sdk_compacting_signal's fake)
    def _turn_completed(self, sid): pass
    def _record_spend(self, *a, **k): pass
    def _forward(self, s, msg): pass


def _session(reg_extra=None):
    be = _FakeBackend(tempfile.mkdtemp())
    return sb.SdkSession(be, {"sid": SID, "name": "x", **(reg_extra or {})}), be


class ReconcileStranded(unittest.TestCase):
    def test_fresh_conversation_reheads_the_fed_turn(self):
        # the incident's shape: fed, never resulted, and no conversation ever materialized
        s, be = _session()
        self.assertFalse(s.resume_sid, "precondition: a fresh spawn has no resumable conversation")
        s.inflight = 1
        s._inflight_texts.append(BRIEF)
        s._reconcile_stranded()
        self.assertEqual(s._pending, [BRIEF], "the fed turn is back at the queue's head, not vanished")
        self.assertEqual(s.inflight, 0, "counters settle as before")
        self.assertEqual(s._inflight_texts, [])
        self.assertEqual(be.dropped_calls, [], "recovered, so nothing to surface as dropped")

    def test_reheaded_turn_precedes_a_not_yet_started_one(self):
        s, _ = _session()
        s._pending.append("a later message")
        s.inflight = 1
        s._inflight_texts.append(BRIEF)
        s._reconcile_stranded()
        self.assertEqual(s._pending, [BRIEF, "a later message"],
                         "delivery order is preserved: the stranded turn was accepted first")

    def test_resumable_conversation_surfaces_the_loss_instead(self):
        # the atom may genuinely have landed in the resumable transcript — re-feeding risks a duplicate,
        # so the echo flips to dropped NOW instead of at the next thread spawn hours later
        s, be = _session({"lastSid": "22222222-3333-4444-5555-bbbbbbbbbbbb"})
        self.assertTrue(s.resume_sid, "precondition: an established conversation")
        s.inflight = 1
        s._inflight_texts.append(BRIEF)
        s._reconcile_stranded()
        self.assertEqual(s._pending, [], "never re-fed into a resumable conversation")
        self.assertEqual(len(be.dropped_calls), 1, "the loss is surfaced immediately")

    def test_clean_reconnect_is_a_no_op(self):
        s, be = _session()
        s._pending.append("queued but never fed")
        s._reconcile_stranded()
        self.assertEqual(s._pending, ["queued but never fed"], "an unfed turn rides across untouched")
        self.assertEqual(be.retired, 0, "nothing was stranded, nothing settles")

    def test_the_result_settle_clears_the_fed_twin(self):
        # the authoritative turn-end empties the twin exactly as it zeroes inflight — a later reconnect
        # must not re-deliver turns the CLI already processed
        s, _ = _session()
        s.inflight = 1
        s._inflight_texts.append(BRIEF)

        class AssistantMessage: ...
        class ResultMessage: ...
        class SystemMessage: ...
        saved = sb.asyncio.ensure_future
        sb.asyncio.ensure_future = lambda coro: (coro.close() if hasattr(coro, "close") else None)
        try:
            s._on_message(ResultMessage(), AssistantMessage, ResultMessage, SystemMessage)
        finally:
            sb.asyncio.ensure_future = saved
        self.assertEqual(s._inflight_texts, [], "settled turns leave the twin")
        self.assertEqual(s.inflight, 0)


class SpawnPinsRideTheFirstConnect(unittest.TestCase):
    """Source pins on kernel.py: the /new pins are applied BETWEEN spawn and connect, so the first
    connect's options carry them and no reconnect teardown exists for the -m send to race."""
    KERNEL = open(os.path.join(BIN, "romp-kernel")).read()

    def test_prefs_apply_before_the_eager_connect(self):
        body = self.KERNEL[self.KERNEL.index("def _create_sdk_session"):]
        body = body[:body.index("\ndef ")]
        spawn_at = body.index("_sdk().spawn(")
        prefs_at = body.index("_apply_new_session_prefs(sid, prefs or {})")
        connect_at = body.index("_sdk().connect(sid)")
        self.assertTrue(spawn_at < prefs_at < connect_at,
                        "pins land in the reg after spawn and BEFORE connect — connect-time, race-free")

    def test_the_new_route_threads_the_body_through(self):
        self.assertTrue(re.search(r"_create_sdk_session\(nm, cwd, auth=\(a if a in \(\"login\", \"key\"\) else \"\"\),\s*\n\s*prefs=b\)", self.KERNEL),
                        "/new hands its body to the create path instead of applying pins after connect")


if __name__ == "__main__":
    unittest.main()
