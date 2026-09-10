#!/usr/bin/env python3
"""Parked ops deliver on the SETTLE event, independent of the judge pass (2026-09-03).

The kernel parks a user's input while its session is busy, compacting, or limit-held, and delivers the
queue FIFO once the session is quiet (_apply_pending_ops). Until this change the ONLY caller of that
function was the tail of the judge producer's pass, after an untimed join on the judge tiers — so every
parked op in every session waited for the judges to finish. A judge pass can run for hours: one session's
closer sweep, alarm-killed turn after turn, held a pass for 6h22m, and a typed /clear parked mid-turn sat
as a queued chip the whole time until the user cancelled it. The drain now rides the pusher cycle, woken
by the backends' turn-end poke (_wake_kernel), by /tick, by every queue mutation, and by its own 0.5 s
backstop; the producer never touches it.

SYNTHETIC fixtures only: a placeholder uuid, invented texts.
"""
import inspect
import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from unittest import mock
from romp_load import load_source

from tests.conftest import thread_census, wait_for_census

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_parkedlive", os.path.join(BIN, "romp-kernel"))

# The ACCOUNT gate is a separate axis (tests/test_kernel_limit_queue.py); pinned off so the real
# machine's usage.json can never make these tests park for a reason none of them is about.
km._limit_hold = lambda sid: None
REAL_PARSE_CACHED = km._parse_cached          # the never-parsing cache reader, before any test patches it

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "11111111-2222-3333-4444-666666666666"
SID3 = "11111111-2222-3333-4444-777777777777"

# Every pusher-cycle job except the drain, quieted so a cycle run here does exactly one thing.
_OTHER_JOBS = ("_push_all", "_lift_spent_awaiting", "_death_sweep_tick", "_end_on_idle_sweep",
               "_deferral_sweep_tick", "_auto_nudge_tick", "_interrupt_block_tick", "_auto_pause_on_limit",
               "_usage_poll_tick", "_auto_pause_on_spend_limit", "_auto_resume_retry",
               "_auto_resume_session_retry", "_auto_retry_tick", "_idle_queue_drive_tick",
               "_clear_done_working_notes")


class _FakeBackend:
    """A tmux-shaped backend: it cannot forward its own sends, so a send parks while the turn is open."""

    def __init__(self):
        self.calls = []
        self.open = True

    def busy(self, sid):
        return self.open

    def forwards_sends(self):
        return False

    def send(self, sid, text):
        self.calls.append(("send", text))
        self.open = True                  # as SdkBackend.send: the turn is pending under the lock before send() returns
        return True

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True

    def turn_seq(self, sid):
        return 0


def _tmux_row(state):
    """One romp tmux session's row, shaped as TmuxBackend.live_sessions builds it for the cycle snapshot
    (the @claude-* vars: state/since/model/effort/context/compactPct/color/mode, tagged backend tmux)."""
    return {"state": state, "since": int(time.time()) - 5, "model": "", "effort": "", "context": None,
            "compactPct": None, "color": None, "mode": "", "backend": "tmux"}


class DeliveryRidesTheSettle(unittest.TestCase):
    def setUp(self):
        self._census0 = thread_census()
        self.be = _FakeBackend()
        self._patches = [
            mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be)),
            mock.patch.object(km, "_compacting_now", lambda sid, **k: False),
            mock.patch.object(km, "_optimistic_echo", lambda *a, **k: None),
            mock.patch.object(km, "_mark_compacting", lambda sid: None),
            mock.patch.object(km, "_names_snapshot", lambda: {}),
            mock.patch.object(km, "_tmux_sessions", lambda: {SID: {"state": "waiting", "backend": "tmux"}}),
        ] + [mock.patch.object(km, name, lambda *a, **k: None) for name in _OTHER_JOBS]
        for p in self._patches:
            p.start()
        km._pending_ops.clear()
        km._moving.clear()
        km._drain_hold.clear()
        km._refresh_parse_failures.clear()
        km._pusher_wake.clear()
        km._producer_wake.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        km._pending_ops.clear()
        km._moving.clear()
        km._drain_hold.clear()
        km._refresh_parse_failures.clear()
        producer = getattr(self, "producer", None)
        if producer is None or not producer.is_alive():
            km._LOOPS_STOP.clear()                   # a producer still alive keeps the stop set: it exits at its next check
        self.assertEqual(wait_for_census(self._census0), [], "no thread of this test outlives it (T282)")

    def test_parked_op_delivers_on_settle_while_a_judge_pass_is_stuck(self):
        gate = threading.Event()             # the judge tiers block here: a closer sweep that never returns
        ran_on = []                          # the threads that ran the drain
        real_apply = km._apply_pending_ops

        def counting_apply():
            ran_on.append(threading.current_thread().name)
            return real_apply()

        # The pass stays stuck for the whole test; at its end the stop seam is set FIRST and the gate released
        # SECOND, so the tiers return, the pass completes under these same stubs, and the producer ends at its
        # next turn of the wheel instead of outliving the module (T282).
        with mock.patch.object(km, "_run_tier", lambda fn: gate.wait()), \
             mock.patch.object(km, "_retry_paused_on", lambda: False), \
             mock.patch.object(km, "_episode_boundary_tick", lambda now: None), \
             mock.patch.object(km, "_begin_goals_pass", lambda: None), \
             mock.patch.object(km, "_end_goals_pass", lambda: None), \
             mock.patch.object(km, "_compact_goal_stores", lambda: 0), \
             mock.patch.object(km, "_sdk", lambda: None), \
             mock.patch.object(km.jd, "begin_pass_frame", lambda: False), \
             mock.patch.object(km.jd, "end_pass_frame", lambda f: None), \
             mock.patch.object(km.jd, "consume_judge_recovery", lambda: False), \
             mock.patch.object(km, "_apply_pending_ops", counting_apply):
            producer = self.producer = threading.Thread(target=km._producer, name="producer-under-test", daemon=True)
            producer.start()
            deadline = time.time() + 5
            while time.time() < deadline and not any(t.name == "triage" for t in threading.enumerate()):
                time.sleep(0.01)
            self.assertTrue(any(t.name == "triage" for t in threading.enumerate()),
                            "a judge pass is in flight, stuck on its tiers")
            # a typed slash command lands while the turn is open → parked (the standing rule)
            km._send_or_park(self.be, SID, "/frobnicate now")
            self.assertEqual(km._pending_ops[SID], [("command", "/frobnicate now", None)])
            self.assertEqual(self.be.calls, [])
            # the turn SETTLES: the backend pokes the kernel exactly as its ResultMessage path does. (On a
            # tree without _wake_kernel the poke is what the backends did before — both wakes by hand — so
            # this test reaches the delivery assertion there and fails on the bug, not on a missing name.)
            self.be.open = False
            km._pusher_wake.clear()
            poke = getattr(km, "_wake_kernel", None) or (lambda: (km._producer_wake.set(), km._pusher_wake.set()))
            poke()
            self.assertTrue(km._pusher_wake.is_set(), "the settle wakes the thread that delivers")
            self.assertTrue(km._producer_wake.is_set(), "…and the judges, as before")
            km._pusher_cycle()                           # ONE pusher cycle, while the judge pass is still stuck
            self.assertFalse(gate.is_set())
            self.assertTrue(producer.is_alive())
            self.assertEqual(self.be.calls, [("send", "/frobnicate now")], "delivered alone, as a fresh prompt")
            self.assertNotIn(SID, km._pending_ops)
            self.assertEqual(ran_on, [threading.current_thread().name],
                             "the drain ran on this cycle and never on the producer")
            km._LOOPS_STOP.set()                     # the loop ends at its next turn of the wheel...
            gate.set()                               # ...which comes once the stuck tiers return and the pass completes
            km._producer_wake.set()
            producer.join(5)
            self.assertFalse(producer.is_alive(), "the producer ended on the stop seam once its pass finished")

    def test_two_parks_drain_one_per_settle_in_park_order(self):
        self.assertEqual(km._send_or_park(self.be, SID, "one", echo="human"), "parked")   # tmux-shaped: a send parks mid-turn
        self.assertTrue(km._compact_or_park(self.be, SID), "the compact parks behind it (queued)")
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["send", "compact"])
        self.be.open = False                                                    # settle #1
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one")], "the send fired; the compact waits for ITS turn to end")
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["compact"])
        self.assertNotIn(SID, km._drain_hold, "send() closed the busy gate before it returned: nothing to hold for")
        self.assertTrue(self.be.open, "the delivered send's turn is in flight")
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one")], "nothing fires into an open turn")
        self.be.open = False                                                    # settle #2
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one"), ("send", "/compact")])
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn(SID, km._drain_hold, "an authoritative busy() needs no hold between deliveries")

    def test_a_backend_that_cannot_say_busy_still_holds_for_the_fallback_window(self):
        # a backend with NO busy() at all (a test fake, a future backend): nothing can ever observe its gate
        # close, so the prompt hold arms and only its clock fallback (_TMUX_PROMPT_HOLD_S) releases it —
        # the op behind never fires back-to-back into a turn that may be opening
        class _Mute(_FakeBackend):
            def busy(self, sid):
                return None
        self.be = _Mute()
        with mock.patch.object(km, "_working_now", lambda sid: True):             # the turn is open: both park
            km._send_or_park(self.be, SID, "one", echo="human")
            km._compact_or_park(self.be, SID)
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["send", "compact"])
        with mock.patch.object(km, "_TMUX_PROMPT_HOLD_S", 3600.0):
            km._pusher_cycle()                                                    # the cached parse reads idle
            self.assertEqual(self.be.calls, [("send", "one")], "the send fired")
            self.assertIn(SID, km._drain_hold, "…and the sid is held while its prompt lands")
            km._pusher_cycle()                                                    # back-to-back, as a wake would
            self.assertEqual(self.be.calls, [("send", "one")], "the compact does NOT fire into the opening turn")
        far = time.monotonic() + 7200.0                                           # the fallback window passed
        with mock.patch.object(km.time, "monotonic", lambda: far), redirect_stderr(io.StringIO()):
            km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one"), ("send", "/compact")])

    def test_a_dead_sessions_queue_drop_takes_its_hold_with_it(self):
        # the hold check precedes delivery, so a hold present when send() raises can only have been armed
        # DURING the delivery (a concurrent writer — the move thread's re-park); the drop must not leave it behind
        class _Dead(_FakeBackend):
            def send(self, sid, text):
                km._hold_drain(sid, 60.0)
                raise RuntimeError("the session is gone")
        self.be = _Dead()
        self.be.open = False
        km._pending_ops[SID] = [("send", "one", "human")]
        with redirect_stderr(io.StringIO()):
            km._apply_pending_ops()
        self.assertNotIn(SID, km._pending_ops, "a dead session's queue is dropped")
        self.assertNotIn(SID, km._drain_hold, "…and its hold with it")

    def test_wake_kernel_sets_both_events_and_is_the_backends_poke(self):
        km._producer_wake.clear()
        km._pusher_wake.clear()
        km._wake_kernel()
        self.assertTrue(km._producer_wake.is_set() and km._pusher_wake.is_set())
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertEqual(src.count("poke=_wake_kernel"), 2, "both backend constructors (SDK + Codex) poke the kernel")
        self.assertNotIn("poke=_producer_wake.set", src, "no backend pokes only the judges anymore")

    def test_delivery_moved_from_the_producer_to_the_pusher_cycle(self):
        self.assertNotIn("_apply_pending_ops()", inspect.getsource(km._producer), "the judge pass no longer gates delivery")
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_apply_pending_ops()", src, "the pusher cycle delivers the parked queue")
        self.assertLess(src.index("_apply_pending_ops()"), src.index("_push_all("),
                        "delivery precedes the push, so the delivered op's echo and retired chip ride it")

    def test_drain_does_not_wake_the_pusher_when_nothing_changed(self):
        self.be.open = False
        d = tempfile.mkdtemp()
        km._pending_ops[SID] = [("cwd", d, km._MOVE_BUSY_RETRIES, 0)]    # waits on turn_seq: nothing to do yet
        km._pusher_wake.clear()
        km._apply_pending_ops()
        self.assertEqual(km._pending_ops[SID], [("cwd", d, km._MOVE_BUSY_RETRIES, 0)], "still parked")
        self.assertFalse(km._pusher_wake.is_set(),
                         "a cycle that delivered nothing must not re-wake the thread it runs on (a hot loop)")
        km._pending_ops[SID] = [("model", "opus")]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("model", "opus")])
        self.assertTrue(km._pusher_wake.is_set(), "a real delivery wakes the push that retires the chip")

    def test_a_cycle_resolves_a_held_sids_path_once(self):
        # review find on #904: the drain's gates each resolved a held sid's transcript path (a discover
        # fingerprint) every cycle; inside a cycle the resolution is memoized on the cycle's scope, and a
        # caller outside a cycle (a WS handler) still resolves fresh
        calls = []
        with mock.patch.object(km, "_sessions", lambda now: (calls.append(1), [])[1]):
            km._path_of(SID)
            km._path_of(SID)
            self.assertEqual(len(calls), 2, "outside a cycle every ask resolves fresh")
            calls.clear()
            km._live_scope.paths = {}
            try:
                km._path_of(SID)
                km._path_of(SID)
            finally:
                km._live_scope.paths = None
            self.assertEqual(len(calls), 1, "inside a cycle the second ask is the memo")
        km._pusher_cycle()
        self.assertIsNone(getattr(km._live_scope, "paths", None), "the memo ends with the cycle")

    def test_cancel_parked_logs_sid_and_kind_only(self):
        km._pending_ops[SID] = [("send", "a private sentence", "human")]
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertIsNone(km._cancel_parked(SID, 0, ""))
        line = buf.getvalue()
        self.assertIn(SID, line)
        self.assertIn("send", line)
        self.assertNotIn("a private sentence", line, "the body is user text — never logged")
        self.assertNotIn(SID, km._pending_ops)


class TmuxBusyFromHookState(unittest.TestCase):
    """TmuxBackend.busy() answers from the hook-maintained @claude-state the cycle snapshot already carries
    (hooks/tmux-status.sh: UserPromptSubmit / PostToolUse → working, Stop / SessionStart → waiting,
    PostCompact → waiting after a manual compaction and working after an auto one, PreCompact →
    compacting, a permission_prompt notification → permission, an idle_prompt one → idle; the revive
    watcher writes picker). Review find on #904: with busy() None, _working_now fell to the
    cached transcript parse, which is None whenever the transcript's (mtime, size) moved since the last
    parse — so a tmux session MID-TURN read as quiet the moment its transcript grew, and the drain fired a
    parked op into the open turn (a parked slash command lands there as text). The drain runs every cycle
    now and before _push_all refreshes the cache, so the stale read was the common case, not a corner.

    The row is not trusted alone (review find on this change's first cut): Claude Code fires NO hook on an
    Esc-interrupt, so an interrupted session's row reads working until romp-idle-dots heals it minutes later
    — every send after a Stop would have parked. busy() corroborates by EVENT ORDER: when the cached parse
    matches the file on disk and the last turn's newest record is NEWER than the row's since, the transcript
    spoke after the hook and its verdict wins; otherwise the hook's does. compacting is no answer at all —
    _compacting_now owns that gate.

    These drive the REAL TmuxBackend (km._TMUX) with its paste stubbed: what it would have pasted, and
    when, is the whole question."""

    def setUp(self):
        self.rows = {SID: _tmux_row("waiting")}
        self.sent = []                                    # (tmux name, text) TmuxBackend.send handed to the paste
        self._tmux_patch = mock.patch.object(km, "_tmux_sessions", lambda: self.rows)
        self._patches = [
            mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: km._TMUX)),
            mock.patch.object(km, "_tmux_send", lambda name, text, **k: self.sent.append((name, text))),
            mock.patch.object(km, "_compacting_now", lambda sid, **k: False),
            mock.patch.object(km, "_optimistic_echo", lambda *a, **k: None),
            mock.patch.object(km, "_mark_compacting", lambda sid: None),
            mock.patch.object(km, "_names_snapshot", lambda: {}),
            # the cached parse is STALE: the transcript moved since the last parse, so it answers None —
            # which the fallback reads as idle. Every correct answer below has to come from the row.
            mock.patch.object(km, "_parse_cached", lambda path: None),
            mock.patch.object(km, "_path_of", lambda sid, now=None: "/nonexistent/" + str(sid) + ".jsonl"),
            self._tmux_patch,
        ] + [mock.patch.object(km, name, lambda *a, **k: None) for name in _OTHER_JOBS]
        for p in self._patches:
            p.start()
        km._pending_ops.clear()
        km._moving.clear()
        km._drain_hold.clear()
        km._refresh_parse_failures.clear()
        km._pusher_wake.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        km._pending_ops.clear()
        km._moving.clear()
        km._drain_hold.clear()
        km._refresh_parse_failures.clear()

    def test_busy_reads_the_hook_state_words(self):
        # the class default: the cache is stale (None), so the transcript cannot be consulted and the row stands
        for word in ("working", "permission", "picker"):
            self.rows[SID] = _tmux_row(word)
            self.assertIs(km._TMUX.busy(SID), True, word)
        for word in ("waiting", "idle"):
            self.rows[SID] = _tmux_row(word)
            self.assertIs(km._TMUX.busy(SID), False, word)
        self.rows[SID] = _tmux_row("compacting")
        self.assertIsNone(km._TMUX.busy(SID), "compacting is no answer: _compacting_now owns that gate, with the "
                                              "open-turn / compact_boundary escape that disbelieves a stuck row")
        self.rows[SID] = _tmux_row("")                    # a fresh pane: no hook has published yet
        self.assertIsNone(km._TMUX.busy(SID), "an unpublished state is no answer — never a hold")
        self.rows[SID] = _tmux_row("frobnicating")
        self.assertIsNone(km._TMUX.busy(SID), "an unknown word is no answer")
        self.rows.clear()
        self.assertIsNone(km._TMUX.busy(SID), "no tmux row → None → the cached parse decides")
        self.rows[SID] = dict(_tmux_row("working"), backend="sdk")
        self.assertIsNone(km._TMUX.busy(SID), "another backend's row is not tmux's to read")

    def test_busy_inside_a_cycle_reads_the_snapshot_not_a_fork(self):
        self._tmux_patch.stop()                           # the real delegator: snapshot inside a cycle, else live
        try:
            km._live_scope.snapshot = {SID: _tmux_row("working")}
            with mock.patch.object(km.Sessions, "live", staticmethod(lambda: self.fail("forked tmux inside a cycle"))):
                self.assertIs(km._TMUX.busy(SID), True)
        finally:
            km._live_scope.snapshot = None
            self._tmux_patch.start()

    def test_a_working_row_holds_a_parked_op_the_stale_cache_would_release(self):
        # THE review find: the hook says mid-turn, the stale cache says idle. The row wins.
        km._pending_ops[SID] = [("command", "/frobnicate now", None)]
        self.rows[SID] = _tmux_row("working")             # UserPromptSubmit flipped it; the transcript grew since
        self.assertTrue(km._working_now(SID))
        km._pusher_cycle()
        self.assertEqual(self.sent, [], "the slash command must not land as text in the open turn")
        self.assertEqual(km._pending_ops[SID], [("command", "/frobnicate now", None)], "still parked")
        self.rows[SID] = _tmux_row("waiting")             # Stop
        km._pusher_cycle()
        self.assertEqual(self.sent, [(SID, "/frobnicate now")], "the settle delivers it, alone")
        self.assertNotIn(SID, km._pending_ops)

    def test_an_idle_row_delivers(self):
        km._pending_ops[SID] = [("command", "/frobnicate now", None)]
        self.rows[SID] = _tmux_row("idle")                # the idle_prompt notification
        km._pusher_cycle()
        self.assertEqual(self.sent, [(SID, "/frobnicate now")])

    def test_no_row_falls_to_the_cached_parse(self):
        # no tmux row for this sid (the hook never published, the pane is gone): busy() is None and the
        # cached event-model parse decides, exactly as before — pinned in both directions
        self.rows.clear()
        km._pending_ops[SID] = [("command", "/frobnicate now", None)]
        now = int(time.time())
        open_turn = {"turns": [{"ended": False, "atoms": [{"type": "user"}], "t": now, "end": now}]}
        with mock.patch.object(km, "_parse_cached", lambda path: open_turn), \
             mock.patch.object(km, "_downtime", []):
            self.assertIsNone(km._TMUX.busy(SID))
            self.assertTrue(km._working_now(SID), "an open turn in the cached parse → working")
            km._pusher_cycle()
            self.assertEqual(self.sent, [], "held on the parse")
        km._pusher_cycle()                                # the class default: a stale cache reads idle
        self.assertEqual(self.sent, [(SID, "/frobnicate now")])

    def test_tmux_double_fire_closes_on_the_hook_flip_not_the_clock(self):
        # SEND-ends-the-drain on tmux, through the EVENT. Two ops park mid-turn (a send, then a compact); the
        # turn settles; the send is pasted. Between that paste and the CLI accepting the prompt the row still
        # reads waiting — a back-to-back cycle (the delivery's own wake) would fire the compact into the
        # opening turn, so the sid holds until the row has been SEEN working once after the delivery (the
        # UserPromptSubmit hook's flip); from there the working gate owns it, and the Stop flip delivers the
        # compact. The clock fallback is pinned out of reach: only the event can carry this test.
        with mock.patch.object(km, "_TMUX_PROMPT_HOLD_S", 3600.0):
            self.rows[SID] = _tmux_row("working")
            self.assertEqual(km._send_or_park(km._TMUX, SID, "one", echo="human"), "parked", "mid-turn per the hook → parked")
            self.assertTrue(km._compact_or_park(km._TMUX, SID), "…and the compact parks behind it")
            self.assertEqual([op[0] for op in km._pending_ops[SID]], ["send", "compact"])
            self.assertEqual(self.sent, [])
            self.rows[SID] = _tmux_row("waiting")         # Stop: the turn settled
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "one")], "the send is pasted")
            self.assertIn(SID, km._drain_hold, "…and the sid holds until the hook has seen the prompt")
            km._pusher_cycle()                            # back-to-back: the paste is in flight, no hook yet
            self.assertEqual(self.sent, [(SID, "one")], "the compact does NOT fire into the opening turn")
            self.rows[SID] = _tmux_row("working")         # UserPromptSubmit: the CLI accepted the prompt
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "one")], "the turn is open: the working gate holds it now")
            self.assertNotIn(SID, km._drain_hold, "the flip is the event that ends the hold")
            self.rows[SID] = _tmux_row("waiting")         # Stop: the delivered turn ended
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "one"), (SID, "/compact")])
            self.assertNotIn(SID, km._pending_ops)

    def test_the_prompt_hold_falls_back_to_the_clock_loudly_when_no_flip_comes(self):
        # a paste tmux refused (the input would not clear, the server died), or a builtin the CLI runs
        # without opening a prompt turn: the row never reads busy, so the event cannot end the hold — the
        # clock does, and says so on stderr (the sid, never the text)
        km._pending_ops[SID] = [("send", "a private sentence", "human"), ("compact",)]
        with mock.patch.object(km, "_TMUX_PROMPT_HOLD_S", 3600.0):
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "a private sentence")])
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "a private sentence")], "held: no flip, the clock not yet passed")
        far = time.monotonic() + 7200.0                    # the fallback window passed, still no flip
        buf = io.StringIO()
        with mock.patch.object(km.time, "monotonic", lambda: far), redirect_stderr(buf):
            km._pusher_cycle()
        self.assertEqual(self.sent, [(SID, "a private sentence"), (SID, "/compact")], "the clock releases the queue")
        self.assertIn(SID, buf.getvalue(), "loud: the release is on the record")
        self.assertNotIn("a private sentence", buf.getvalue(), "the body is user text — never logged")
        self.assertNotIn(SID, km._drain_hold, "the compact was the queue's LAST op: nothing behind it to hold for")

    def test_the_hold_is_armed_and_the_docstrings_tell_the_final_truth(self):
        src = inspect.getsource(km._hold_drain) + inspect.getsource(km._after_turn_opening)
        self.assertNotIn("has no busy()", src, "tmux has an authoritative busy() now — the docstrings say so")
        self.assertIn("until_busy", inspect.getsource(km._after_turn_opening),
                      "a turn-opening delivery holds until busy() has been observed True, not for a clock")

    # ── corroboration by event order ──
    def _cached(self, turns):
        """The cached parse MATCHES the file on disk: the transcript has not moved since it was parsed."""
        return mock.patch.object(km, "_parse_cached", lambda path: {"turns": turns})

    def test_a_turn_just_started_stays_busy_on_the_fresh_hook_row(self):
        # shape 1 (the maintainer's gap): UserPromptSubmit flipped the row a moment ago; the transcript has not
        # recorded the prompt yet, so the cache still matches the file and shows the previous turn ended
        # BEFORE since → the hook wins
        now = int(time.time())
        self.rows[SID] = dict(_tmux_row("working"), since=now)
        ended_before = [{"ended": True, "t": now - 40, "end": now - 30,
                         "atoms": [{"type": "user", "t": now - 40}, {"type": "assistant", "t": now - 30}]}]
        with self._cached(ended_before), mock.patch.object(km, "_downtime", []):
            self.assertIs(km._TMUX.busy(SID), True)
            km._pending_ops[SID] = [("command", "/frobnicate now", None)]
            km._pusher_cycle()
            self.assertEqual(self.sent, [], "the slash command stays parked: the turn is opening")

    def test_an_interrupted_turn_delivers_once_the_transcript_spoke_after_the_hook(self):
        # shape 2: Esc fires no hook, so the row still says working from the last PostToolUse (an old since);
        # the CLI's interrupt record ended the turn AFTER that → the transcript's verdict wins and a send
        # typed after the Stop is handed over now, not parked for minutes
        now = int(time.time())
        self.rows[SID] = dict(_tmux_row("working"), since=now - 60)
        interrupted = [{"ended": True, "t": now - 90, "end": now - 5,
                        "atoms": [{"type": "user", "t": now - 90}, {"type": "assistant", "t": now - 70},
                                  {"type": "user", "t": now - 5,
                                   "message": {"role": "user", "content": "[Request interrupted by user]"}}]}]
        with self._cached(interrupted), mock.patch.object(km, "_downtime", []):
            self.assertIs(km._TMUX.busy(SID), False)
            self.assertNotEqual(km._send_or_park(km._TMUX, SID, "a correction", echo="human"), "parked", "delivered, not parked")
            self.assertEqual(self.sent, [(SID, "a correction")])
        # the kernel's OWN Stop button writes no interrupt record but an idle record (_record_idle), which the
        # parse turns into an idle span at the tail — same verdict, dated by the record's t, not the span's end
        self.sent.clear()
        kernel_stop = [{"ended": False, "t": now - 90, "end": now,
                        "atoms": [{"type": "user", "t": now - 90}, {"type": "assistant", "t": now - 70},
                                  {"type": "idle", "t": now - 6, "end": now}]}]
        with self._cached(kernel_stop), mock.patch.object(km, "_downtime", []):
            self.assertIs(km._TMUX.busy(SID), False)

    def test_a_parse_time_idle_tail_never_outranks_the_hook(self):
        # an idle span at the tail carries end = the PARSE time; only atom `t` (a real record) dates the
        # transcript — else every fresh parse of an idle session would outrank a hook write that just landed
        # and reopen shape 1
        now = int(time.time())
        self.rows[SID] = dict(_tmux_row("working"), since=now - 1)
        idle_tail = [{"ended": True, "t": now - 40, "end": now,
                      "atoms": [{"type": "user", "t": now - 40}, {"type": "assistant", "t": now - 30},
                                {"type": "idle", "t": now - 30, "end": now}]}]
        with self._cached(idle_tail), mock.patch.object(km, "_downtime", []):
            self.assertIs(km._TMUX.busy(SID), True, "the newest RECORD (now-30) predates since (now-1): the hook wins")

    def test_a_stale_waiting_row_yields_to_an_open_turn_the_transcript_shows(self):
        # the other direction: a waiting row OLDER than an open turn's records reads busy (PostCompact once
        # wrote waiting into an auto-compaction's continuing turn); a waiting row NEWER than the last record
        # stands — the ordinary settle, where the Stop hook fires after the final assistant record
        now = int(time.time())
        open_turn = [{"ended": False, "t": now - 90, "end": now - 5,
                      "atoms": [{"type": "user", "t": now - 90}, {"type": "assistant", "t": now - 5}]}]
        with self._cached(open_turn), mock.patch.object(km, "_downtime", []):
            self.rows[SID] = dict(_tmux_row("waiting"), since=now - 60)
            self.assertIs(km._TMUX.busy(SID), True)
            self.rows[SID] = dict(_tmux_row("waiting"), since=now)
            self.assertIs(km._TMUX.busy(SID), False)

    def test_a_row_without_since_stands(self):
        now = int(time.time())
        self.rows[SID] = dict(_tmux_row("working"), since=None)
        interrupted = [{"ended": True, "t": now - 90, "end": now - 5,
                        "atoms": [{"type": "user", "t": now - 90}, {"type": "user", "t": now - 5}]}]
        with self._cached(interrupted), mock.patch.object(km, "_downtime", []):
            self.assertIs(km._TMUX.busy(SID), True, "undated against the row, the transcript cannot overrule it")

    # ── the hold and the queue's tail ──
    def test_no_hold_outlives_the_queue(self):
        # the queue's LAST op has nothing behind it for a hold to protect; a hold armed then outlived the queue
        # (the sid leaves _pending_ops, so no cycle evaluated or removed it) and fired the fallback line when a
        # later op parked for an unrelated reason
        km._pending_ops[SID] = [("send", "one", "human")]
        km._pusher_cycle()
        self.assertEqual(self.sent, [(SID, "one")])
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn(SID, km._drain_hold, "no op behind → no hold")
        self.rows[SID] = _tmux_row("working")             # later: a new op parks because a turn is open…
        self.assertEqual(km._send_or_park(km._TMUX, SID, "two", echo="human"), "parked")
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._pusher_cycle()
            self.rows[SID] = _tmux_row("waiting")         # …and delivers at the settle
            km._pusher_cycle()
        self.assertEqual(self.sent, [(SID, "one"), (SID, "two")])
        self.assertNotIn("drain:", buf.getvalue(), "no stale hold, no spurious fallback line")

    def test_a_delivered_compact_releases_its_hold_on_the_compacting_row_not_the_clock(self):
        # a delivered /compact with an op behind arms an until_busy hold, but the row goes working →
        # compacting within a fraction of a second and compacting is no busy answer — so the hold rode the
        # clock and logged the fallback line although the compaction IS the event it waited for. The
        # compacting gate is the release, and then holds the op behind until the compaction is over.
        km._pending_ops[SID] = [("compact",), ("send", "after the compaction", "human")]
        with mock.patch.object(km, "_TMUX_PROMPT_HOLD_S", 3600.0), \
             mock.patch.object(km, "_compacting_now", lambda sid, **k: self.rows[SID]["state"] == "compacting"):
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "/compact")])
            self.assertIn(SID, km._drain_hold, "the send behind the compact is held")
            self.rows[SID] = _tmux_row("compacting")      # PreCompact
            buf = io.StringIO()
            with redirect_stderr(buf):
                km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "/compact")], "the compacting gate holds the send")
            self.assertNotIn(SID, km._drain_hold, "the compaction is the event: the hold is over")
            self.assertNotIn("drain:", buf.getvalue(), "no fallback line — the clock was never the release")
            self.rows[SID] = _tmux_row("waiting")         # PostCompact (manual)
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "/compact"), (SID, "after the compaction")])

    def test_the_drain_refreshes_a_parked_tmux_sids_moved_transcript_before_its_busy_gate(self):
        # the headless defect (second review): busy() can overrule the hook only through _parse_cached, which
        # NEVER parses — the cache is filled by client builds and the client-gated warmer. With no client, every
        # live tmux session's cache is None from its first transcript write on, so busy() was the hook verbatim
        # and a pane Esc parked every `romp send` for minutes. The drain now re-parses a parked TMUX sid's
        # moved transcript per-sid inside _apply_pending_ops, right before the gate that reads busy() and after
        # the move/hold/account gates (#955 moved it off the retired whole-set _refresh_parked_parses pre-pass)
        # — not a sid without parked ops, not a parked sid whose backend answers busy() authoritatively
        # (SDK/Codex: re-parsing a streaming turn's transcript on every atom's wake was load the parent did not
        # have — third review), and not again while the file is unmoved.
        # REAL transcript files, the real cache reader and the real cache-filling parse; only the paths, the
        # rows and the backend routing are stubbed.
        now = int(time.time())
        td = tempfile.mkdtemp()

        def z(t):
            return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        def transcript(sid):
            recs = [{"type": "user", "timestamp": z(now - 90), "uuid": "u1", "parentUuid": None,
                     "promptSource": "typed", "message": {"role": "user", "content": "please frobnicate"}},
                    {"type": "assistant", "timestamp": z(now - 70), "uuid": "a1", "parentUuid": "u1",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "frobnicating"}],
                                 "stop_reason": None}},
                    {"type": "user", "timestamp": z(now - 5), "uuid": "u2", "parentUuid": "a1",
                     "message": {"role": "user", "content": "[Request interrupted by user]"}}]   # the pane Esc
            path = os.path.join(td, sid + ".jsonl")
            with open(path, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in recs))
            return path
        paths = {SID: transcript(SID), SID2: transcript(SID2), SID3: transcript(SID3)}
        self.rows[SID] = dict(_tmux_row("working"), since=now - 60)     # Esc fired no hook: the row is stale
        self.rows[SID2] = dict(_tmux_row("working"), since=now - 60)    # …a second tmux session, same state, no ops
        km._pending_ops[SID] = [("send", "a correction", "human"), ("compact",)]   # a headless `romp send`, parked
        #                                                                              once, with an op behind it
        sdk = _FakeBackend()                                            # an SDK-shaped backend mid-turn: busy() is
        km._pending_ops[SID3] = [("send", "queued behind the stream", "human")]   # the whole truth, no cache read
        self.assertFalse(getattr(sdk, "corroborates_with_transcript", False), "the ABC default")
        self.assertTrue(km._TMUX.corroborates_with_transcript)
        parsed = []
        real_parse = km._parse
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: sdk if str(sid) == SID3 else km._TMUX)), \
             mock.patch.object(km, "_path_of", lambda sid, now=None: paths.get(str(sid))), \
             mock.patch.object(km, "_parse_cached", REAL_PARSE_CACHED), \
             mock.patch.object(km, "_parse", lambda path, sid, now: (parsed.append(path), real_parse(path, sid, now))[1]), \
             mock.patch.object(km, "_downtime", []):
            self.assertIsNone(REAL_PARSE_CACHED(paths[SID]), "never parsed: the cache is empty")
            self.assertIs(km._TMUX.busy(SID), True, "at this instant the hook is all busy() has")
            km._pusher_cycle()
            self.assertIsNotNone(REAL_PARSE_CACHED(paths[SID]), "the cycle filled the cache")
            self.assertEqual(parsed, [paths[SID]], "the parked tmux sid's moved transcript parsed once; not the idle "
                                                    "tmux sid's, not the parked SDK sid's")
            self.assertEqual(self.sent, [(SID, "a correction")], "the interrupt record overruled the stale row: delivered")
            self.assertEqual([op[0] for op in km._pending_ops[SID]], ["compact"], "the op behind is held (until_busy)")
            self.assertIn(SID, km._drain_hold)
            km._pusher_cycle()                                          # the transcript is UNMOVED: the cache still matches
            self.assertEqual(parsed, [paths[SID]], "no second parse while the file has not moved")
            self.assertEqual(self.sent, [(SID, "a correction")])
        self.assertEqual(sdk.calls, [], "the SDK-shaped sid stayed parked behind its turn — and was never parsed")

    def test_one_unreadable_transcript_does_not_starve_the_other_parked_sids_refresh(self):
        # the refresh runs per sid under its own guard: a parse that raises for one parked tmux sid (whatever raised
        # inside _parse — the reg read, the path discovery, an assembly bug; an unreadable transcript file does NOT
        # raise, the readers swallow it) is logged and the NEXT parked sid's moved transcript is still re-parsed this
        # cycle — before, one raise ended the loop, and every other parked sid rode its stale hook row cycle after
        # cycle, the very hold this job exists to lift. The failing sid is retried every cycle (nothing enters the
        # parse cache), so its line is count-gated: the first with a traceback, then at 10, 100, 1000.
        now = int(time.time())
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))

        def z(t):
            return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        def transcript(sid):
            recs = [{"type": "user", "timestamp": z(now - 90), "uuid": "u1", "parentUuid": None,
                     "promptSource": "typed", "message": {"role": "user", "content": "please frobnicate"}},
                    {"type": "assistant", "timestamp": z(now - 70), "uuid": "a1", "parentUuid": "u1",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "frobnicating"}],
                                 "stop_reason": None}},
                    {"type": "user", "timestamp": z(now - 5), "uuid": "u2", "parentUuid": "a1",
                     "message": {"role": "user", "content": "[Request interrupted by user]"}}]
            path = os.path.join(td, sid + ".jsonl")
            with open(path, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in recs))
            return path
        paths = {SID: transcript(SID), SID2: transcript(SID2)}
        self.rows[SID] = dict(_tmux_row("working"), since=now - 60)      # both rows stale after a pane Esc
        self.rows[SID2] = dict(_tmux_row("working"), since=now - 60)
        km._pending_ops[SID] = [("send", "first", "human")]              # iterated first: its parse will raise
        km._pending_ops[SID2] = [("send", "second", "human")]
        parsed = []
        real_parse = km._parse

        def parse(path, sid, now):
            parsed.append(path)
            if path == paths[SID]:
                raise ValueError("unreadable transcript")
            return real_parse(path, sid, now)
        err = io.StringIO()
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: km._TMUX)), \
             mock.patch.object(km, "_path_of", lambda sid, now=None: paths.get(str(sid))), \
             mock.patch.object(km, "_parse_cached", REAL_PARSE_CACHED), \
             mock.patch.object(km, "_parse", parse), \
             mock.patch.object(km, "_downtime", []), redirect_stderr(err):
            km._pusher_cycle()
        self.assertEqual(parsed, [paths[SID], paths[SID2]], "the raise did not end the loop: the second sid was parsed")
        self.assertEqual(self.sent, [(SID2, "second")], "the second sid's interrupt record overruled its row: delivered")
        self.assertEqual([op[1] for op in km._pending_ops.get(SID, [])], ["first"],
                         "the unreadable sid stays held on its hook row, never dropped")
        self.assertEqual(err.getvalue().count("parked-parse refresh"), 1, "one line names the sid that failed")
        self.assertIn(SID, err.getvalue())
        self.assertIn("Traceback", err.getvalue(), "the first failure carries its traceback")
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: km._TMUX)), \
             mock.patch.object(km, "_path_of", lambda sid, now=None: paths.get(str(sid))), \
             mock.patch.object(km, "_parse_cached", REAL_PARSE_CACHED), \
             mock.patch.object(km, "_parse", parse), \
             mock.patch.object(km, "_downtime", []), redirect_stderr(err):
            for _ in range(11):
                km._pusher_cycle()                                   # the failing sid is retried every cycle…
        self.assertEqual(parsed.count(paths[SID]), 12)
        self.assertEqual(parsed.count(paths[SID2]), 1, "…the healthy sid, delivered and no longer parked, is not")
        self.assertEqual(err.getvalue().count("parked-parse refresh"), 2, "…and the line is count-gated: 1 and 10")
        self.assertIn("(failure 10)", err.getvalue())

    def test_cancelling_the_last_parked_op_drops_the_sids_hold(self):
        km._pending_ops[SID] = [("send", "one", "human"), ("compact",)]
        with mock.patch.object(km, "_TMUX_PROMPT_HOLD_S", 3600.0):
            km._pusher_cycle()
            self.assertEqual(self.sent, [(SID, "one")])
            self.assertIn(SID, km._drain_hold, "the compact behind the send is what the hold protects")
            with redirect_stderr(io.StringIO()):
                self.assertIsNone(km._cancel_parked(SID, 0, "/compact"))
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn(SID, km._drain_hold, "an emptied queue leaves no hold behind")


if __name__ == "__main__":
    unittest.main()
