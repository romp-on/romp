#!/usr/bin/env python3
"""The parked-op queue is mutated under ONE lock that no backend call ever holds (2026-09-05).

Since the drain moved onto the pusher cycle (#904) it runs every 0.5 s and on every wake, on the pusher
thread, while WS/HTTP handler threads park and cancel ops in the same dict and the move thread re-inserts
a head — with no lock between them. Two races followed, each confirmed by review: (1) a ✕ (or the move
thread's re-insert) changed a sid's list between the drain's head read and its pop — the drain popped a
list the ✕ had emptied (an IndexError the blanket except turned into a dropped queue), or the ✕'s own
check-then-pop landed on a list the drain had shifted and removed the op BEHIND the one clicked; (2) the
disk mirror was written through ONE fixed temp path from any thread, so two writers could tear it (a
kernel death then restores a wrong queue).

Now every mutation of _pending_ops runs under _pending_ops_lock, and the lock guards the mutations ONLY:
the drain reads a head under it, releases before every backend call (CodexBackend.send can synchronously
spawn `codex app-server`; the SDK's busy()/turn_seq take the backend's own lock), and pops afterwards. A
send run alone is popped before its delivery, as the parent already did; every other kind is popped only
if the head is still the op the backend got, and stays the visible head meanwhile (_inflight_ops — a move
waiting on its turn_seq is not recorded, since nothing is handed over while that is read), so a handler's
gate still sees the queue and parks BEHIND it, and a ✕ on the in-flight head is refused as too late. A
handler runs its expensive gates outside the lock and holds it only for the queue-presence check + park,
one step; the mirror is published through the kernel's per-writer atomic write. The failure contract is
unchanged: a raise during a sid's pass drops that sid's queue once, logged.

SYNTHETIC fixtures only: placeholder uuids, invented texts.
"""
import io
import os
import contextlib
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_parkedlock", os.path.join(BIN, "romp-kernel"))

# The ACCOUNT gate is a separate axis (tests/test_kernel_limit_queue.py); pinned off so the real
# machine's usage.json can never make these tests park for a reason none of them is about.
km._limit_hold = lambda sid: None

SID = "11111111-2222-3333-4444-555555555555"

# Every pusher-cycle job except the drain, quieted so a cycle run here does exactly one thing.
_OTHER_JOBS = ("_push_all", "_lift_spent_awaiting", "_death_sweep_tick", "_end_on_idle_sweep",
               "_deferral_sweep_tick", "_auto_nudge_tick", "_interrupt_block_tick", "_auto_pause_on_limit",
               "_usage_poll_tick", "_auto_pause_on_spend_limit", "_auto_resume_retry",
               "_auto_resume_session_retry", "_auto_retry_tick", "_idle_queue_drive_tick",
               "_clear_done_working_notes")


def _lock_free_for_another_thread():
    """Can a thread other than the caller take the queue lock right now? (RLock ownership is per thread.)"""
    got = []

    def probe():
        ok = km._pending_ops_lock.acquire(blocking=False)
        if ok:
            km._pending_ops_lock.release()
        got.append(ok)
    th = threading.Thread(target=probe, daemon=True)
    th.start()
    th.join(5)
    return bool(got and got[0])


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

    def set_effort(self, sid, value):
        self.calls.append(("effort", value))
        return True

    def set_fast(self, sid, value):
        self.calls.append(("fast", value))
        return True

    def set_auth(self, sid, value):
        self.calls.append(("auth", value))
        return True

    def set_env(self, sid, value):
        self.calls.append(("env", value))
        return True

    def turn_seq(self, sid):
        return 0


def _tmux_row(state):
    return {"state": state, "since": int(time.time()) - 5, "model": "", "effort": "", "context": None,
            "compactPct": None, "color": None, "mode": "", "backend": "tmux"}


class _Drain(unittest.TestCase):
    def setUp(self):
        self.be = _FakeBackend()
        self._patches = [
            mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be)),
            mock.patch.object(km, "_compacting_now", lambda sid, **k: False),
            mock.patch.object(km, "_optimistic_echo", lambda *a, **k: None),
            mock.patch.object(km, "_mark_compacting", lambda sid: None),
            mock.patch.object(km, "_names_snapshot", lambda: {}),
            mock.patch.object(km, "_tmux_sessions", lambda: {SID: _tmux_row("waiting")}),
        ] + [mock.patch.object(km, name, lambda *a, **k: None) for name in _OTHER_JOBS]
        for p in self._patches:
            p.start()
        self._reset()
        km._pusher_wake.clear()
        km._producer_wake.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._reset()

    def _reset(self):
        km._pending_ops.clear()
        km._moving.clear()
        km._move_askers.clear()
        km._drain_hold.clear()
        km._refresh_parse_failures.clear()
        km._inflight_ops.clear()
        try:
            os.unlink(km._PENDING_OPS_FILE)
        except OSError:
            pass


class OneLockAroundEveryMutation(_Drain):
    """WS/HTTP handlers park and cancel, the move thread re-inserts a head, and the pusher (the ONE thread
    that ever walks the queue) drains — all on the same dict. One RLock serializes the mutations; no backend
    call runs under it; the head an op is being delivered from stays visible until the backend has it."""

    def test_a_cancel_mid_call_cannot_shift_the_list_under_the_drains_pop(self):
        # race (1): the drain read its head and called the backend; a ✕ arrived before the pop. Unlocked,
        # the ✕ on the head "succeeded" (it popped the op the backend was already delivering), and the
        # drain's pop then took the op behind — or, with nothing behind, an IndexError that dropped the
        # queue. Now the in-flight head is refused as too late, a ✕ on the op behind succeeds, and the
        # drain pops exactly what it delivered.
        self.be.open = False
        entered, release = threading.Event(), threading.Event()

        def set_model(sid, value):
            self.be.calls.append(("model", value))
            entered.set()
            release.wait(5)
            return True
        self.be.set_model = set_model
        head, behind = ("model", "opus"), ("effort", "high")
        km._pending_ops[SID] = [head, behind]
        buf = io.StringIO()
        with redirect_stderr(buf):
            walker = threading.Thread(target=km._apply_pending_ops, name="drain-under-test", daemon=True)
            walker.start()
            self.assertTrue(entered.wait(5))               # the backend has the head; the lock is free
            err_head = km._cancel_parked(SID, 0, km._parked_md(head))
            err_behind = km._cancel_parked(SID, 1, km._parked_md(behind))
            release.set()
            walker.join(5)
        self.assertEqual(err_head, km._cancel_miss_text(km._parked_md(head)), "the in-flight head is too late")
        self.assertIsNone(err_behind, "the op behind it is cancellable")
        self.assertEqual(self.be.calls, [("model", "opus")], "the head was delivered exactly once, the effort pick never")
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn("pending ops apply", buf.getvalue(), "nothing raised, so nothing was dropped")

    def test_a_parked_op_stays_the_visible_head_while_the_backend_has_it(self):
        # the ordering regression the second review caught: popping the head BEFORE the backend call left
        # the queue empty for the whole call, and every handler's park decision keys on queue presence —
        # busy() flips only once the backend has registered the op (SdkBackend.send runs _ensure first,
        # CodexBackend.send may spawn the app server first). A pane send landing in that window saw no
        # queue and busy False and handed over AHEAD of the parked /clear. The head now stays until the
        # backend has it, so the send parks behind.
        self.be.open = False

        def send(sid, text):
            self.be.calls.append(("send", text))
            if text == "/clear":                           # a pane send lands while the backend still has the /clear…
                self.assertTrue(km._send_or_park(self.be, SID, "hello", echo="human"), "…and PARKS")
            self.be.open = True                            # only now does busy() flip
            return True
        self.be.send = send
        km._pending_ops[SID] = [("command", "/clear", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "/clear")], "nothing overtook the in-flight command")
        self.assertEqual(km._pending_ops.get(SID), [("send", "hello", "human")], "the send waits behind it")
        self.be.open = False                               # the /clear's turn ends
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "/clear"), ("send", "hello")], "…and delivers next, in order")

    def test_a_head_replaced_in_place_while_with_the_backend_is_not_popped(self):
        # the identity check: a same-kind pick replaces the in-flight head in place (a new tuple); the drain
        # must not pop the replacement as if it had delivered it, and delivers it on the next iteration
        self.be.open = False

        def set_model(sid, value):
            self.be.calls.append(("model", value))
            if value == "opus":
                km._park_op(SID, ("model", "other"))       # lock free: the user re-picks mid-call
            return True
        self.be.set_model = set_model
        km._pending_ops[SID] = [("model", "opus"), ("effort", "high")]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("model", "opus"), ("model", "other"), ("effort", "high")],
                         "the replacement delivered, not silently popped; the op behind after it")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_second_compact_press_is_cancellable_while_the_first_is_in_flight(self):
        # _compact_or_park parks the literal ("compact",); CPython interns one constant tuple per code object,
        # so every compact parked in a kernel life is the SAME object, and compact is not in the replace set:
        # a second press appends [C, C] with ops[0] is ops[1]. The in-flight refusal must therefore key on
        # the HEAD slot, not on identity alone — a ✕ on the second chip while the first is with the backend
        # is a cancel, and the redundant compaction never runs
        self.be.open = True                                # a turn is open: the press parks
        self.assertTrue(km._compact_or_park(self.be, SID))
        results = []

        def send(sid, text):
            self.be.calls.append(("send", text))
            self.assertTrue(km._compact_or_park(self.be, SID), "a second press parks behind the in-flight one")
            ops = km._pending_ops[SID]
            self.assertEqual([op[0] for op in ops], ["compact", "compact"])
            self.assertIs(ops[0], ops[1], "the same interned tuple, twice")
            results.append(km._cancel_parked(SID, 1, "/compact"))       # ✕ on the SECOND chip
            self.be.open = True
            return True
        self.be.send = send
        self.be.open = False
        with redirect_stderr(io.StringIO()):
            km._apply_pending_ops()
        self.assertEqual(results, [None], "the second chip is cancelled, not refused as too late")
        self.assertEqual(self.be.calls, [("send", "/compact")], "one compaction")
        self.assertNotIn(SID, km._pending_ops, "…and no redundant one stays queued")

    def test_a_stale_index_re_locates_past_the_in_flight_head_to_the_identical_chip_behind_it(self):
        # the ✕ carries the chip's index from the last push; a send ahead delivered since makes it stale, and
        # the body re-locate then walks the list for the first op with that body. With [C(in-flight), C] that
        # is slot 0 — the head no ✕ can take — so the re-locate skips it and lands on the second chip; with
        # only the in-flight op listed it finds nothing, the same miss as today
        self.be.open = True                                # a turn is open: the press parks
        self.assertTrue(km._compact_or_park(self.be, SID))
        results, seen = [], []

        def send(sid, text):
            self.be.calls.append(("send", text))
            results.append(km._cancel_parked(SID, 3, "/compact"))       # stale index, one chip: the in-flight head
            self.assertTrue(km._compact_or_park(self.be, SID), "a second press parks behind the in-flight one")
            results.append(km._cancel_parked(SID, 2, "/compact"))       # stale index, two chips: the second
            seen.append(list(km._pending_ops.get(SID) or []))
            self.be.open = True
            return True
        self.be.send = send
        self.be.open = False
        with redirect_stderr(io.StringIO()):
            km._apply_pending_ops()
        self.assertEqual(results, [km._cancel_miss_text("/compact"), None],
                         "alone in flight → too late; behind an in-flight twin → cancelled")
        self.assertEqual(seen, [[("compact",)]], "the in-flight head alone remains after the ✕, until the backend has it")
        self.assertEqual(self.be.calls, [("send", "/compact")], "one compaction")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_move_waiting_on_its_turn_end_is_cancellable_during_the_turn_seq_read(self):
        # a waiting cwd head's only action in a cycle is the turn_seq read — nothing is handed over — so it is
        # not recorded in flight, and a ✕ landing inside that read is a cancel, not a false "already reached
        # the session" that would recur every cycle while the move waited
        self.be.open = False
        d = tempfile.mkdtemp()
        op = ("cwd", d, km._MOVE_BUSY_RETRIES, 0)
        results, fired = [], []

        def turn_seq(sid):
            results.append(km._cancel_parked(SID, 0, km._parked_md(op)))
            return 0                                       # unchanged: the move would keep waiting
        self.be.turn_seq = turn_seq
        km._pending_ops[SID] = [op]
        buf = io.StringIO()
        with mock.patch.object(km, "_fire_move", lambda be, sid, path, tries, wid: fired.append(path)), \
             redirect_stderr(buf):
            km._apply_pending_ops()
        self.assertEqual(results, [None], "cancelled")
        self.assertNotIn(SID, km._pending_ops)
        self.assertEqual(fired, [], "no move fires")
        self.assertNotIn("pending ops apply", buf.getvalue(), "nothing raised, so the empty queue is the ✕'s doing")

    def test_a_move_replaced_in_place_while_its_turn_seq_is_read_fires_the_replacement_once(self):
        # the cwd twin of the replaced-head test: the user re-picks the folder while the drain reads turn_seq
        # (lock free); the superseded folder must never fire, the replacement fires exactly once
        self.be.open = False
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        fired = []

        def turn_seq(sid):
            km._park_op(SID, ("cwd", d2, 0))               # replace-in-place: a new tuple at the head
            return 1                                       # the turn ended: the move may proceed
        self.be.turn_seq = turn_seq
        km._pending_ops[SID] = [("cwd", d1, km._MOVE_BUSY_RETRIES, 0)]
        with mock.patch.object(km, "_fire_move", lambda be, sid, path, tries, wid: fired.append(path)):
            km._apply_pending_ops()
        self.assertEqual(fired, [d2], "the replacement fires, exactly once; the superseded folder never")
        self.assertNotIn(SID, km._pending_ops, "nothing dropped, nothing left")

    def test_the_locked_re_check_parks_behind_a_queue_the_expensive_gates_missed(self):
        # _park_behind_queue's locked re-check is the gate that decides: with every expensive gate saying
        # quiet, an op that finds a queue there parks BEHIND it and never reaches the backend
        self.be.open = False
        km._pending_ops[SID] = [("compact",)]
        with mock.patch.object(km, "_ops_gate", lambda sid: False):
            km._set_model_or_park(self.be, SID, "opus")
            self.assertTrue(km._send_or_park(self.be, SID, "hello"))   # (its own first gate reads the queue too)
        self.assertEqual(self.be.calls, [], "nothing handed over past an existing queue")
        self.assertEqual(km._pending_ops[SID], [("compact",), ("model", "opus"), ("send", "hello", None)])

    def test_a_cancel_racing_the_drains_pop_never_removes_the_wrong_op(self):
        # _cancel_parked verifies the clicked index by body, THEN pops. Unlocked, the drain could pop its
        # head between the two, and the ✕ removed whatever had shifted into the clicked slot: the user
        # cancelled the queued message, the message was delivered, and the effort pick vanished. Now the
        # check and the pop are one locked step, so a drain landing mid-cancel yields the cycle instead.
        # Event-keyed: the cancel pauses INSIDE its body check (holding the lock), the drain is run then.
        self.be.open = False
        checked, go_cancel = threading.Event(), threading.Event()
        real_md = km._parked_md

        def md_probe(op):
            if threading.current_thread().name == "cancel-under-test" and not checked.is_set():
                checked.set()                              # paused between the check and the pop…
                go_cancel.wait(5)
            return real_md(op)
        msg = ("send", "two", None)
        km._pending_ops[SID] = [("model", "opus"), msg, ("fast", "on"), ("effort", "high")]
        result = []
        with mock.patch.object(km, "_parked_md", md_probe), redirect_stderr(io.StringIO()):
            canceller = threading.Thread(target=lambda: result.append(km._cancel_parked(SID, 1, real_md(msg))),
                                         name="cancel-under-test", daemon=True)
            canceller.start()
            self.assertTrue(checked.wait(5))
            km._apply_pending_ops()                        # …a drain cycle lands exactly there
            go_cancel.set()
            canceller.join(5)
            self.assertIsNone(result[0], "the ✕ reports success")
            km._apply_pending_ops()                        # the next cycle
        self.assertNotIn(("send", "two"), self.be.calls, "…so the cancelled message was never delivered")
        self.assertEqual(self.be.calls, [("model", "opus"), ("fast", "on"), ("effort", "high")],
                         "every other op delivered, in order, none lost")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_cancel_during_an_in_flight_send_hits_only_what_is_still_queued(self):
        # the lock is released while the backend takes a send: a ✕ arriving in that window can still remove
        # an op BEHIND the one in flight, and a ✕ on the in-flight one itself finds it gone — the honest
        # 'too late', not a wrong-op removal
        self.be.open = False
        in_send, release = threading.Event(), threading.Event()

        def send(sid, text):
            self.be.calls.append(("send", text))
            in_send.set()
            release.wait(5)
            self.be.open = True
            return True
        self.be.send = send
        first, behind = ("send", "first", None), ("model", "opus")
        km._pending_ops[SID] = [first, behind]
        walker = threading.Thread(target=km._apply_pending_ops, name="drain-under-test", daemon=True)
        with redirect_stderr(io.StringIO()):
            walker.start()
            self.assertTrue(in_send.wait(5))               # the head is popped and with the backend; the lock is free
            err_behind = km._cancel_parked(SID, 1, km._parked_md(behind))
            err_inflight = km._cancel_parked(SID, 0, km._parked_md(first))
            release.set()
            walker.join(5)
        self.assertIsNone(err_behind, "the op behind the in-flight one is cancellable")
        self.assertEqual(err_inflight, km._cancel_miss_text(km._parked_md(first)), "the in-flight one is too late")
        self.assertEqual(self.be.calls, [("send", "first")])
        self.assertNotIn(SID, km._pending_ops)

    def test_an_edit_during_an_in_flight_send_is_too_late_and_the_original_is_delivered(self):
        # the ✎'s reachable race (review find, 2026-09-08): the drain pops a send run under the lock and
        # delivers it with the lock released, so a ✎ landing in that window finds the send gone from the FIFO.
        # It is refused as too late (the words the session gets are the ORIGINAL ones), and the op behind
        # the in-flight send is untouched: never a rewrite of the wrong op
        self.be.open = False
        in_send, release = threading.Event(), threading.Event()

        def send(sid, text):
            self.be.calls.append(("send", text))
            in_send.set()
            release.wait(5)
            self.be.open = True
            return True
        self.be.send = send
        first, behind = ("send", "first", "human"), ("model", "opus")
        km._pending_ops[SID] = [first, behind]
        walker = threading.Thread(target=km._apply_pending_ops, name="drain-under-test", daemon=True)
        with redirect_stderr(io.StringIO()):
            walker.start()
            self.assertTrue(in_send.wait(5))               # the send is popped and with the backend; the lock is free
            err = km._edit_parked(SID, 0, "first", "first, edited")
            self.assertEqual(km._pending_ops.get(SID), [behind], "the edit touched nothing still queued")
            release.set()
            walker.join(5)
        self.assertEqual(err, km._edit_miss_text("first"), "too late, honestly")
        self.assertEqual(self.be.calls, [("send", "first")], "the ORIGINAL words were delivered, once")

    def test_an_edit_before_the_drain_reaches_the_send_wins_and_the_edited_words_go(self):
        # the other side of the race: while the drain is inside the backend call for the HEAD (a model pick,
        # recorded in flight), a ✎ on the send queued behind it lands under the lock, and the drain's next
        # step delivers the EDITED words. The in-flight head itself is refused as too late, as the ✕ does
        self.be.open = False
        entered, release = threading.Event(), threading.Event()

        def set_model(sid, value):
            self.be.calls.append(("model", value))
            entered.set()
            release.wait(5)
            return True
        self.be.set_model = set_model
        head, msg = ("model", "opus"), ("send", "hello", "human")
        km._pending_ops[SID] = [head, msg]
        walker = threading.Thread(target=km._apply_pending_ops, name="drain-under-test", daemon=True)
        with redirect_stderr(io.StringIO()):
            walker.start()
            self.assertTrue(entered.wait(5))
            self.assertIs(km._inflight_ops.get(SID), head)
            err_head = km._edit_parked(SID, 0, "/model opus", "x")
            err_msg = km._edit_parked(SID, 1, "hello", "hello, edited")
            release.set()
            walker.join(5)
        self.assertEqual(err_head, km._edit_miss_text("/model opus"), "the head the backend holds is too late")
        self.assertIsNone(err_msg, "the send behind it is still the user's to change")
        self.assertEqual(self.be.calls, [("model", "opus"), ("send", "hello, edited")])
        self.assertNotIn(SID, km._pending_ops)

    def test_the_drain_yields_to_a_handler_holding_the_lock_and_the_push_still_runs(self):
        # the drain is the first job of every cycle; its top-of-walk acquire is non-blocking, so a cycle
        # that finds a handler holding the lock skips the drain rather than stalling every session's push;
        # the handler's own park wakes the pusher, so the skipped delivery costs nothing
        self.be.open = False
        km._pending_ops[SID] = [("model", "opus")]
        held, release = threading.Event(), threading.Event()

        def handler():
            with km._pending_ops_lock:
                held.set()
                release.wait(5)
        th = threading.Thread(target=handler, name="handler-under-test", daemon=True)
        th.start()
        self.assertTrue(held.wait(5))
        pushes = []
        try:
            with mock.patch.object(km, "_push_all", lambda **k: pushes.append(1)), \
                 mock.patch.object(km, "_clients", [object()]):                      # a browser is connected
                km._pusher_cycle()
            self.assertEqual(self.be.calls, [], "the drain skipped: a handler owns the queue")
            self.assertEqual(km._pending_ops[SID], [("model", "opus")], "nothing lost")
            self.assertEqual(pushes, [1], "…and the push still ran, not stalled behind the handler")
        finally:
            release.set()
            th.join(5)
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("model", "opus")], "the next cycle drains")
        self.assertNotIn(SID, km._pending_ops)

    def test_the_lock_is_never_held_across_a_backend_call(self):
        # CodexBackend.send can synchronously spawn `codex app-server` with no request timeout; a lock held
        # across it would hold every handler's queue check + park behind it. Pinned from inside the backend,
        # for EVERY call the drain makes — the setters, the send, busy() (the working gate and the hold
        # check behind a delivered send) and turn_seq (a waiting move): the drain thread does not own the
        # lock, and another thread can take it
        probes = []

        def probe(name):
            probes.append((name, km._pending_ops_lock._is_owned(), _lock_free_for_another_thread()))

        class _Probed(_FakeBackend):
            def busy(self, sid):
                probe("busy")
                return self.open

            def send(self, sid, text):
                probe("send")
                return _FakeBackend.send(self, sid, text)

            def set_model(self, sid, value):
                probe("set_model")
                return _FakeBackend.set_model(self, sid, value)

            def set_effort(self, sid, value):
                probe("set_effort")
                return _FakeBackend.set_effort(self, sid, value)

            def set_fast(self, sid, value):
                probe("set_fast")
                return _FakeBackend.set_fast(self, sid, value)

            def set_auth(self, sid, value):
                probe("set_auth")
                return _FakeBackend.set_auth(self, sid, value)

            def set_env(self, sid, value):
                probe("set_env")
                return _FakeBackend.set_env(self, sid, value)

            def turn_seq(self, sid):
                probe("turn_seq")
                return 0
        self.be = _Probed()
        self.be.open = False
        d = tempfile.mkdtemp()
        env = {"A": "1"}
        km._pending_ops[SID] = [("model", "opus"), ("effort", "high"), ("fast", "on"), ("auth", "key"), ("env", env),
                                ("send", "hello", None),
                                ("cwd", d, km._MOVE_BUSY_RETRIES, 0)]         # behind the send: waits on turn_seq
        km._apply_pending_ops()                            # busy (gate) → 5 setters → send → busy (the hold check)
        self.assertEqual(self.be.calls, [("model", "opus"), ("effort", "high"), ("fast", "on"), ("auth", "key"),
                                         ("env", env), ("send", "hello")])
        self.be.open = False                               # the send's turn ends
        km._apply_pending_ops()                            # busy (gate) → turn_seq: the move waits on its turn end
        self.assertEqual(km._pending_ops[SID], [("cwd", d, km._MOVE_BUSY_RETRIES, 0)], "still parked, waiting")
        names = [n for n, _, _ in probes]
        self.assertEqual(names, ["busy", "set_model", "set_effort", "set_fast", "set_auth", "set_env", "send",
                                 "busy", "busy", "turn_seq"])
        self.assertEqual([o for _, o, _ in probes], [False] * len(probes), "the drain never owns the lock in a backend call")
        self.assertEqual([f for _, _, f in probes], [True] * len(probes), "…and another thread can take it every time")

    def test_a_handlers_park_completes_while_the_drain_is_inside_a_slow_send(self):
        # the release around the backend call is what lets a user's press land during a slow delivery
        self.be.open = False
        in_send, release = threading.Event(), threading.Event()

        def send(sid, text):
            self.be.calls.append(("send", text))
            self.be.open = True                            # the turn is pending before send() returns
            in_send.set()
            release.wait(5)
            return True
        self.be.send = send
        km._pending_ops[SID] = [("send", "first", "human")]
        walker = threading.Thread(target=km._apply_pending_ops, name="drain-under-test", daemon=True)
        walker.start()
        self.assertTrue(in_send.wait(5))                   # the backend has the send; the lock is released
        parker = threading.Thread(target=lambda: km._send_or_park(self.be, SID, "second", echo="human"),
                                  name="handler-under-test", daemon=True)
        parker.start()
        parker.join(5)
        self.assertFalse(parker.is_alive(), "the handler's park did not wait for the backend")
        self.assertEqual(km._pending_ops.get(SID), [("send", "second", "human")], "…and landed behind the in-flight send")
        release.set()
        walker.join(5)
        self.assertEqual(self.be.calls, [("send", "first")], "the parked second waits for the delivered turn to end")
        self.assertEqual(km._pending_ops.get(SID), [("send", "second", "human")])

    def test_a_handlers_queue_check_and_park_share_the_lock_but_its_gates_and_handover_do_not(self):
        # the expensive gates (they fork tmux / call the backend's busy()) run outside the lock; the
        # queue-presence check and the park are one locked step; the handover runs outside it again
        owned = {"gate": [], "park": [], "handover": []}
        real_wn, real_park_locked = km._working_now, km._park_op_locked

        def gate_probe(sid):
            owned["gate"].append(km._pending_ops_lock._is_owned())
            return real_wn(sid)

        def park_probe(sid, op):
            owned["park"].append(km._pending_ops_lock._is_owned())
            return real_park_locked(sid, op)

        def send(sid, text):
            owned["handover"].append(km._pending_ops_lock._is_owned())
            self.be.calls.append(("send", text))
            return True

        def set_model(sid, value):
            owned["handover"].append(km._pending_ops_lock._is_owned())
            self.be.calls.append(("model", value))
            return True
        self.be.send, self.be.set_model = send, set_model
        self.be.open = False                               # quiet…
        with mock.patch.object(km, "_working_now", gate_probe), \
             mock.patch.object(km, "_park_op_locked", park_probe):
            km._pending_ops[SID] = [("compact",)]          # …but a queue exists: everything parks BEHIND it
            self.assertEqual(km._send_or_park(self.be, SID, "hello"), "parked")
            km._set_model_or_park(self.be, SID, "opus")
            self.assertTrue(km._compact_or_park(self.be, SID))
            self.assertEqual([op[0] for op in km._pending_ops[SID]], ["compact", "send", "model", "compact"],
                             "every op parked BEHIND the queue, in press order (a compact appends, it never replaces)")
            self.assertEqual(owned["park"], [True] * 3, "the queue check + park is one locked step")
            self.assertEqual(self.be.calls, [])
            km._pending_ops.clear()                        # no queue: everything hands over
            self.assertNotEqual(km._send_or_park(self.be, SID, "hello"), "parked")   # handed over: the send's own result
            km._set_model_or_park(self.be, SID, "opus")
            self.assertEqual(owned["handover"], [False, False], "the handover to the backend is outside the lock")
            self.assertEqual(self.be.calls, [("send", "hello"), ("model", "opus")])
        # (_send_or_park short-circuits on the queue read, so not every call reaches _working_now — every one
        # that did ran outside the lock)
        self.assertTrue(owned["gate"], "the expensive gate was consulted")
        self.assertEqual(set(owned["gate"]), {False}, "…and every time outside the lock")

    def test_the_move_threads_head_repark_runs_under_the_lock(self):
        # _move_now re-inserts a `busy` move at the head from its own thread; the insert and its hold are one
        # locked step, so a handler's park or the drain's pops cannot interleave with them
        owned = []
        real_hold = km._hold_drain

        def hold_probe(sid, seconds, until_busy=False):
            owned.append(km._pending_ops_lock._is_owned())
            return real_hold(sid, seconds, until_busy)
        self.be.move = lambda sid, path: "busy"
        d = tempfile.mkdtemp()
        km._moving.add(SID)
        with mock.patch.object(km, "_hold_drain", hold_probe):
            self.assertEqual(km._move_now(self.be, SID, d, 0, ""), "busy")
        self.assertEqual(km._pending_ops[SID], [("cwd", d, 1, 0)], "back at the head, carrying the try")
        self.assertEqual(owned, [True], "the re-insert and its hold are one locked step")
        self.assertNotIn(SID, km._moving)


class TheMirrorIsWrittenPerWriter(_Drain):
    """pending-ops.json used to be published through ONE fixed temp path from any thread: the loser renamed
    a temp the winner had already moved, or wrote into a temp mid-rename — a torn mirror that a kernel
    death then restored as the queue."""

    def test_concurrent_parks_never_tear_the_mirror_or_log_a_failed_save(self):
        errors, buf = [], io.StringIO()

        def hammer(t):
            sid = "11111111-2222-3333-4444-%012d" % t
            for i in range(120):
                try:
                    km._park_op(sid, ("model", "pick-%d-%d" % (t, i)))    # replace-in-place: one op per sid
                except Exception as e:
                    errors.append(repr(e))
        with redirect_stderr(buf):
            threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(30)
        self.assertEqual(errors, [])
        self.assertNotIn("pending-ops save", buf.getvalue(), "no writer lost its temp to another")
        leftovers = [p.name for p in km._PENDING_OPS_FILE.parent.iterdir()
                     if p.name.startswith(km._PENDING_OPS_FILE.stem) and p.name != km._PENDING_OPS_FILE.name]
        self.assertEqual(leftovers, [], "no temp left behind")
        self.assertEqual(km._load_pending_ops(), dict(km._pending_ops), "the mirror is the final state, whole")
        self.assertEqual(len(km._pending_ops), 8)

    def test_the_mirrors_temp_name_is_per_writer_not_a_shared_sibling(self):
        srcs = []
        real_replace = os.replace

        def replace_probe(src, dst, *a, **k):
            if str(dst) == str(km._PENDING_OPS_FILE):
                srcs.append(str(src))
            return real_replace(src, dst, *a, **k)
        km._pending_ops[SID] = [("model", "opus")]
        with mock.patch.object(km.os, "replace", replace_probe):
            km._save_pending_ops()
            km._save_pending_ops()
        self.assertEqual(len(srcs), 2)
        self.assertNotEqual(srcs[0], srcs[1], "two writes, two temps")
        self.assertNotIn(str(km._PENDING_OPS_FILE.with_suffix(".tmp")), srcs, "the shared sibling temp is gone")
        for s in srcs:
            self.assertEqual(os.path.dirname(s), str(km._PENDING_OPS_FILE.parent),
                             "published from the file's own directory (a same-filesystem rename)")


if __name__ == "__main__":
    unittest.main()


class DrainRaiseClearsTheInFlightRecord(_Drain):
    """The except path in _apply_pending_ops clears _inflight_ops for the sid — without it a drain that
    raised mid-delivery leaves a stale in-flight record, and every later ✕ on that sid's head is refused
    as 'too late' forever (review find on #954, 2026-09-07)."""

    def test_a_raise_mid_delivery_does_not_leave_a_stale_in_flight_record(self):
        # /compact is a turn-opening op: the drain records it in _inflight_ops, then calls the backend.
        # Make that call raise; the sid's in-flight record must be cleared so a re-press is cancellable.
        km._compact_or_park(self.be, SID)
        self.assertEqual(km._pending_ops.get(SID), [("compact",)])
        boom = mock.patch.object(self.be, "send", side_effect=RuntimeError("app-server died"))
        with boom, contextlib.redirect_stderr(io.StringIO()):
            km._apply_pending_ops()
        self.assertNotIn(SID, km._inflight_ops, "the except path cleared the in-flight record")
        # a second /compact press parks, and its ✕ is honoured — not refused as an in-flight head
        km._compact_or_park(self.be, SID)
        self.assertEqual(km._cancel_parked(SID, 0, km._parked_md(("compact",))), None,
                         "the re-pressed compact is cancellable, not stuck behind a phantom in-flight op")

    def test_the_park_wake_runs_after_the_lock_is_released(self):
        # the drain's non-blocking top-of-walk acquire relies on a handler's park waking the pusher AFTER
        # it drops the lock; a wake fired while still holding it could find the lock busy and skip a cycle
        # with nothing to re-fire it (review find on #954, 2026-09-07).
        held = []
        with mock.patch.object(km, "_mark_views_dirty", lambda: held.append(km._pending_ops_lock._is_owned())):
            km._park_op(SID, ("model", "opus"))
            km._park_behind_queue(SID, ("send", "next", "human"))
            km._cancel_parked(SID, 0, km._parked_md(("model", "opus")))
        self.assertTrue(held, "the wake ran")
        self.assertTrue(all(owned is False for owned in held), "every wake fired with the lock released")
