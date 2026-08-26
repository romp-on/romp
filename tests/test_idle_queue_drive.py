#!/usr/bin/env python3
"""Idle-queue drive — self-scheduled work must wake an idle SDK session (the user 2026-08-18).

The bug, hit twice in one day: under the SDK backend, a session's own scheduled work — a recurring
Monitor, a cron firing, a background task's completion notice — lands as a queue-operation enqueue,
and in the CLI's STUCK regime nothing ever drives that queue into a turn: an overnight 15-minute
Monitor stacked 33 on-time <task-notification> enqueues with no delivery while the session ran zero
turns, until the next human message hours later. The stuck regime is NOT the population's behavior
(the 2026-08-18 review measurement over this machine's live transcripts): in MOST sessions the CLI
delivers this class itself, straight from idle, at median 46ms (p90 126ms) — writing no dequeue
record; the non-meta user record carrying the text is the delivery — so the drive must stay out of
the delivering regime's way or the model hears the same notification twice. Background-AGENT
completion notices (a-prefixed 17-hex task ids) are delivered by the CLI in EVERY regime, the stuck
one included, so they are never wake signals at all.

The fix under test, in three parts:
  * kernel._undelivered_wake_tail: the transcript's still-pending enqueues, resolved PER ENTRY on
    the CLI's own evidence — a content-addressed remove discards its one entry, a dequeue alone
    clears nothing, a non-isMeta user record clears entries whose text it CONTAINS (one entry per
    carried occurrence, oldest first — a varianceless recurring monitor's identical firings are
    distinct signals), and turn records alone clear nothing (a mid-turn arrival in the stuck regime
    is never folded into the open turn, so the turn must not eat it). The parse rides the normal
    push cadence.
  * kernel._idle_queue_drive_tick: the pusher-cycle job that finds idle SDK sessions with a wake
    signal in that tail and hands them to the backend. Kernel-side gates: the age floor, PER ENTRY
    (an entry drives only once IT is _WAKE_DRIVE_FLOOR_S old, and only the aged subset drives — the
    documented time-window exception; the CLI's decision not to deliver emits no record, so only
    its window passing proves the stuck regime; keyed on the NEWEST entry, a sub-floor wake cadence
    reset the clock forever and re-opened the silent strand), the global retry pause, per-session
    retry suppression, an API-error block.
  * SdkBackend.drive_idle_queue: gates (open turn, compaction, ended/cut sessions) re-checked at
    delivery time, a per-watermark latch (one drive per newest-driven-wrapper, held in memory at
    acceptance and persisted to the reg only after the enqueue lands, so a kernel death in between
    re-drives instead of silently discarding), a per-sid in-flight exclusion (overlapping workers
    for one sid double-delivered), and delivery — reconnect-if-dormant via _ensure drawing on the
    SAME spawn-stagger budget as boot reconcile, then enqueue() of the CLI's OWN queued texts
    verbatim (no synthetic prompt), the exact channel boot-reconcile's restored queues ride.

Synthetic transcripts only: placeholder uuids, invented notification text, TESTHOST.
"""
import inspect
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_idledrain", os.path.join(BIN, "romp-kernel")).load_module()
sb = SourceFileLoader("romp_sdk_backend_idledrain", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
SRC = open(os.path.join(BIN, "romp-kernel")).read()

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "11111111-2222-3333-4444-666666666666"
TS = "2026-08-18T06:%02d:%02d.000Z"


def _iso(epoch):
    """An ISO-Z timestamp for an epoch second — the cadence tests span hours, past TS's minute field."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _wrap(n=0, tid="b1111111a"):
    """A synthetic Monitor task-notification, the shape the CLI enqueues (invented text)."""
    return ("<task-notification>\n<task-id>%s</task-id>\n<summary>Monitor event: \"nightly check\"</summary>"
            "\n<event>[tick %d] heartbeat</event>\n</task-notification>" % (tid, n))


def _qop(op, content=None, ts=TS % (14, 0)):
    o = {"type": "queue-operation", "operation": op, "sessionId": SID, "timestamp": ts}
    if content is not None:
        o["content"] = content
    return o


def _urec(text="status?", meta=False, ts=TS % (7, 0)):
    o = {"type": "user", "uuid": "22222222-2222-3333-4444-555555555555", "timestamp": ts,
         "message": {"role": "user", "content": text}}
    if meta:
        o["isMeta"] = True
    return o


def _arec(text="done.", ts=TS % (7, 30), api_error=False):
    o = {"type": "assistant", "uuid": "33333333-2222-3333-4444-555555555555", "timestamp": ts,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if api_error:
        o["isApiErrorMessage"] = True
        o["apiErrorStatus"] = 500
        o["error"] = "server_error"
    return o


def _write_tx(path, recs):
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def _turn(ts_u=TS % (7, 0), ts_a=TS % (7, 30)):
    """One completed turn: a user record and its assistant reply."""
    return [_urec(ts=ts_u), _arec(ts=ts_a)]


def _backlog(count=3, minute0=14):
    """`count` Monitor enqueues, the overnight shape (enqueue records, no dequeue)."""
    return [_qop("enqueue", _wrap(i), ts=TS % (minute0 + i, 9)) for i in range(count)]


class WakeTail(unittest.TestCase):
    """kernel._undelivered_wake_tail — the trailing unconsumed enqueues, from the transcript's own
    queue-operation records (the authoritative queue; the display fold _pending_queued is untouched)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tx = os.path.join(self.dir, SID + ".jsonl")

    def _tail(self, recs):
        _write_tx(self.tx, recs)
        return km._undelivered_wake_tail(self.tx)

    def test_overnight_backlog_is_the_tail(self):
        entries, mark = self._tail(_turn() + _backlog(33))
        self.assertEqual(len(entries), 33, "every undelivered enqueue after the last turn is the tail")
        self.assertTrue(all(e["wrapper"] for e in entries), "task-notifications are wake signals")
        self.assertEqual([e["text"] for e in entries], [_wrap(i) for i in range(33)], "texts verbatim, in order")
        self.assertIsNotNone(mark, "the newest enqueue is the watermark")
        self.assertEqual(mark[0], entries[-1]["pos"], "watermark = the newest enqueue's position")

    def test_a_content_matched_remove_clears_only_its_entry(self):
        # the CLI's removes are content-addressed single-item discards, routinely of a NON-oldest
        # entry while others stay pending (46/76 in the live evidence transcript) — the old
        # whole-tail clear let one supersede-remove erase every other still-pending wake signal
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap(0)),
                                              _qop("enqueue", _wrap(1), ts=TS % (15, 0)),
                                              _qop("remove", _wrap(1))])
        self.assertEqual([e["text"] for e in entries], [_wrap(0)],
                         "the remove discards ITS entry; the older signal is still owed a turn")
        self.assertIsNotNone(mark, "the survivor still marks the session a candidate")
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap()), _qop("remove", _wrap())])
        self.assertEqual((entries, mark), ([], None), "a remove means the CLI discarded it — its call")

    def test_a_contentless_remove_resolves_the_oldest(self):
        entries, _ = self._tail(_turn() + [_qop("enqueue", _wrap(0)),
                                           _qop("enqueue", _wrap(1), ts=TS % (15, 0)), _qop("remove")])
        self.assertEqual([e["text"] for e in entries], [_wrap(1)],
                         "no content → FIFO, exactly as _pending_queued folds the same records")

    def test_a_remove_matching_nothing_drops_nothing(self):
        entries, _ = self._tail(_turn() + [_qop("enqueue", _wrap(0)), _qop("remove", _wrap(7))])
        self.assertEqual([e["text"] for e in entries], [_wrap(0)],
                         "the removed item predates this tail — nothing here resolved")

    def test_a_dequeue_alone_clears_nothing(self):
        # the CLI's idle deliveries write NO dequeue record at all; where a dequeue does appear
        # (agent notices), the delivery evidence is the user record it produces, pinned below
        entries, _ = self._tail(_turn() + [_qop("enqueue", _wrap()), _qop("dequeue")])
        self.assertEqual([e["text"] for e in entries], [_wrap()],
                         "a dequeue is not delivery evidence — the user record it produces is")

    def test_a_user_record_containing_the_text_clears_it(self):
        # the delivering regime's shape: enqueue → non-meta user record carrying the text (median
        # 46ms, no dequeue record) — and romp's own driven turn resolves the same way, since its
        # user record carries the joined wrapper texts verbatim
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap())]
                                   + [_urec(text=_wrap(), ts=TS % (15, 0))])
        self.assertEqual((entries, mark), ([], None), "the CLI delivered it — romp stays out of the way")
        joined = _wrap(0) + "\n\n" + _wrap(1)
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap(0)), _qop("enqueue", _wrap(1))]
                                   + [_urec(text=joined, ts=TS % (15, 0))])
        self.assertEqual((entries, mark), ([], None), "a driven turn's joined texts clear every entry it carried")
        blocks = dict(_urec(ts=TS % (15, 0)))
        blocks["message"] = {"role": "user", "content": [{"type": "text", "text": _wrap()}]}
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap())] + [blocks])
        self.assertEqual((entries, mark), ([], None), "block-list user content is delivery evidence too")

    def test_one_delivery_record_clears_one_identical_entry(self):
        # varianceless recurring monitors enqueue byte-identical firings (live census: 6 of 1373
        # enqueues are exact repeats); a record carrying the text ONCE delivered exactly one of
        # them — an identical firing enqueued mid-flight is a distinct signal still owed a turn,
        # and the whole-tail containment clear silently dropped it
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap()),
                                              _qop("enqueue", _wrap(), ts=TS % (15, 0)),
                                              _urec(text=_wrap(), ts=TS % (15, 1))])
        self.assertEqual(len(entries), 1, "one carried copy clears ONE entry, not every look-alike")
        self.assertEqual(entries[0]["ts"], TS % (15, 0),
                         "oldest first — the delivered copy was the one that had been waiting")
        self.assertIsNotNone(mark, "the surviving firing still marks the session a candidate")

    def test_a_record_carrying_the_text_twice_clears_both_identical_entries(self):
        # the driven turn joins k delivered copies "\n\n"-separated — k non-overlapping
        # occurrences — so its own user record clears exactly what it carried: no re-drive loop
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap()),
                                              _qop("enqueue", _wrap(), ts=TS % (15, 0)),
                                              _urec(text=_wrap() + "\n\n" + _wrap(), ts=TS % (15, 1))])
        self.assertEqual((entries, mark), ([], None),
                         "two carried copies are two deliveries — the driven turn settles its own batch")

    def test_a_midturn_arrival_survives_the_turns_own_records(self):
        # stuck regime, measured live: a wrapper landing during an open turn is NEVER folded into it
        # (zero in-turn appearances) — so the turn's records must not eat the signal; it drives once
        # the turn settles. The old rule ("a turn ran after the signal → it had its shot") silently
        # dropped ~18% of the exact payload class of the overnight incident.
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap())]
                                   + _turn(ts_u=TS % (15, 0), ts_a=TS % (15, 30)))
        self.assertEqual([e["text"] for e in entries], [_wrap()],
                         "a turn that does not CARRY the text is not its delivery")
        self.assertIsNotNone(mark)
        entries, _ = self._tail(_turn() + [_qop("enqueue", _wrap())] + [_arec(ts=TS % (15, 0))])
        self.assertEqual(len(entries), 1, "assistant records clear nothing")

    def test_an_agent_dequeue_does_not_wipe_an_older_watchdog_entry(self):
        # the live 19:14:17 → 19:15:00 shape: watchdog enqueued, then an agent notice's instant
        # enqueue → dequeue → user record; only the agent entry resolves, the watchdog stays owed
        agent = _wrap(5, tid="a0123456789abcdef")
        entries, mark = self._tail(_turn() + [_qop("enqueue", _wrap(0)),
                                              _qop("enqueue", agent, ts=TS % (15, 0)), _qop("dequeue"),
                                              _urec(text=agent, ts=TS % (15, 1))])
        self.assertEqual([e["text"] for e in entries], [_wrap(0)],
                         "the agent's own delivery resolves the agent entry and nothing else")
        self.assertIsNotNone(mark)

    def test_agent_completion_notices_are_not_wake_signals(self):
        # the CLI delivers a-prefixed 17-hex agent notices itself in EVERY regime (verified live,
        # stuck session included) — re-sending one would double-deliver a subagent completion
        entries, _ = self._tail(_turn() + [_qop("enqueue", _wrap(0, tid="a0123456789abcdef"))])
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["wrapper"], "an agent notice rides along like a bare queued message")
        entries, _ = self._tail(_turn() + [_qop("enqueue", _wrap(0))])
        self.assertTrue(entries[0]["wrapper"],
                        "monitor/cron/bash ids (9-char base36) keep the wake-signal class")
        entries, _ = self._tail(_turn() + [_qop("enqueue", "<task-notification>\nno id at all\n"
                                                           "</task-notification>")])
        self.assertTrue(entries[0]["wrapper"],
                        "a missing id fails toward delivering — under-delivering is the original bug")

    def test_meta_records_do_not_eat_wake_signals(self):
        entries, _ = self._tail(_turn() + [_qop("enqueue", _wrap())] + [_urec(meta=True, ts=TS % (15, 0))])
        self.assertEqual(len(entries), 1, "an isMeta record is CLI bookkeeping, not the session being awake")

    def test_non_wrapper_content_is_in_the_tail_but_not_a_wake_signal(self):
        entries, mark = self._tail(_turn() + [_qop("enqueue", "a queued plain message")])
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["wrapper"], "a plain queued message is the CLI's own to deliver")
        self.assertIsNotNone(mark)

    def test_contentless_enqueues_are_not_wake_signals(self):
        entries, _ = self._tail(_turn() + [_qop("enqueue")])
        self.assertTrue(all(not e["wrapper"] for e in entries), "no content, nothing to deliver")


class FakeDriveBackend:
    """Records what the tick hands over; owns() by a fixed sid set."""

    def __init__(self, owned):
        self.owned = set(owned)
        self.calls = []

    def owns(self, sid):
        return sid in self.owned

    def drive_idle_queue(self, cands):
        self.calls.append(cands)


class DriveTick(unittest.TestCase):
    """kernel._idle_queue_drive_tick — kernel-side gates, then hand off to the backend."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tx = os.path.join(self.dir, SID + ".jsonl")
        _write_tx(self.tx, _turn() + _backlog(3))
        self.fb = FakeDriveBackend({SID})
        self.alive = [{"sid": SID, "path": self.tx, "name": "web"}]

    def tearDown(self):
        km._set_retry_paused(False)
        km._clear_session_retry_suppress(SID)

    def _tick(self, now=None):
        with mock.patch.object(km, "_sdk", lambda: self.fb), \
             mock.patch.object(km, "_alive_sessions", lambda now, tmux: self.alive):
            km._idle_queue_drive_tick(int(time.time()) if now is None else now, {SID: {}})

    def test_wake_signals_reach_the_backend(self):
        self._tick()
        self.assertEqual(len(self.fb.calls), 1, "one batch per tick")
        (cand,) = self.fb.calls[0]
        self.assertEqual(cand["sid"], SID)
        self.assertEqual(len(cand["entries"]), 3)
        self.assertIsNotNone(cand["mark"])

    def test_the_global_pause_stands_down(self):
        km._set_retry_paused(True)
        self._tick()
        self.assertEqual(self.fb.calls, [], "the global pause stops all self-driving")

    def test_a_retry_suppressed_session_stands_down(self):
        km._suppress_session_retry(SID)
        self._tick()
        self.assertEqual(self.fb.calls, [], "the user interrupted this thread's storm — hands off")

    def test_an_api_error_blocked_session_stands_down(self):
        _write_tx(self.tx, _turn() + [_arec(ts=TS % (8, 0), api_error=True)] + _backlog(3))
        self._tick()
        self.assertEqual(self.fb.calls, [], "an API-error block is the auto-retry tick's to clear")

    def test_a_tail_without_wake_signals_does_not_drive(self):
        _write_tx(self.tx, _turn() + [_qop("enqueue", "a queued plain message")])
        self._tick()
        self.assertEqual(self.fb.calls, [], "a bare queued message is the CLI's own to deliver")

    def test_an_agent_only_tail_is_not_a_candidate(self):
        _write_tx(self.tx, _turn() + [_qop("enqueue", _wrap(0, tid="a0123456789abcdef"))])
        self._tick()
        self.assertEqual(self.fb.calls, [], "an agent notice is the CLI's own to deliver, in every regime")

    def test_a_young_wake_enqueue_stands_down_without_latching(self):
        """The delivering regime starts the turn itself within milliseconds (p90 0.126s measured), so
        a parse landing in the enqueue→delivery gap must not race it. The floor is the documented
        exception to the no-time-windows rule: the CLI's decision NOT to deliver writes no record, so
        only the window passing proves the stuck regime. Stand-down is latch-free — the same tail
        past the floor drives on a later parse."""
        ts = TS % (14, 9)
        epoch = int(km._wake_ts_epoch(ts))
        _write_tx(self.tx, _turn() + [_qop("enqueue", _wrap(), ts=ts)])
        self._tick(now=epoch + 10)
        self.assertEqual(self.fb.calls, [], "younger than the floor → not a candidate yet")
        self._tick(now=epoch + int(km._WAKE_DRIVE_FLOOR_S) + 60)
        self.assertEqual(len(self.fb.calls), 1, "no latch: the aged tail drives on the next parse")

    def test_the_floor_is_per_entry_the_aged_drive_the_fresh_wait(self):
        # an old backlog with one fresh arrival: the AGED entries drive now — each waited out its
        # own floor — while the fresh one, which may still be racing the CLI's own delivery, waits
        # for its own floor and drives on a later parse. (Keyed on the newest enqueue, the whole
        # batch waited — and a sub-floor cadence made it wait forever; see the cadence test below.)
        old, young = TS % (14, 9), TS % (30, 0)
        _write_tx(self.tx, _turn() + [_qop("enqueue", _wrap(0), ts=old),
                                      _qop("enqueue", _wrap(1), ts=young)])
        self._tick(now=int(km._wake_ts_epoch(young)) + 10)
        self.assertEqual(len(self.fb.calls), 1, "the aged entry is not held hostage by a fresh arrival")
        (cand,) = self.fb.calls[0]
        self.assertEqual([e["text"] for e in cand["entries"]], [_wrap(0)],
                         "only the aged subset drives — the fresh one may still be racing the CLI")
        self.assertEqual(cand["mark"], (cand["entries"][-1]["pos"], cand["entries"][-1]["ts"]),
                         "the watermark is the newest DRIVEN wrapper, never the newest pending entry")
        self._tick(now=int(km._wake_ts_epoch(young)) + int(km._WAKE_DRIVE_FLOOR_S) + 10)
        self.assertEqual(len(self.fb.calls), 2, "the fresh entry drives once IT ages past the floor")
        (cand,) = self.fb.calls[1]
        self.assertIn(_wrap(1), [e["text"] for e in cand["entries"]],
                      "the once-young entry is in the aged subset now")
        self.assertEqual(cand["mark"], (cand["entries"][-1]["pos"], cand["entries"][-1]["ts"]))

    def test_a_sustained_sub_floor_cadence_still_drives(self):
        """The starvation regression: a 30s-cadence monitor in a stuck session, ticked between
        enqueues for two simulated hours. Keyed on the NEWEST enqueue, the floor's clock reset on
        every arrival before the previous one aged past it — 240 pending entries, ZERO drives, and
        nothing logged: the exact silent strand this feature exists to end, back again for any
        wake stream firing faster than the floor. Per entry, the first firing qualifies once IT
        ages past the floor, whatever keeps arriving after it. Synthetic throughout."""
        base = int(km._wake_ts_epoch(TS % (14, 0)))
        cadence = 30
        recs = list(_turn())
        drives = []
        for step in range(240):                     # two simulated hours of a 30s monitor
            enq_t = base + step * cadence
            recs.append(_qop("enqueue", _wrap(step), ts=_iso(enq_t)))
            _write_tx(self.tx, recs)
            now = enq_t + cadence - 1               # just before the next firing: newest is never
            before = len(self.fb.calls)             # older than 29s, so the old floor never expired
            self._tick(now=now)
            if len(self.fb.calls) > before:
                drives.append((now, self.fb.calls[-1]))
        self.assertTrue(drives, "a sub-floor cadence starved candidacy forever — 240 pending, 0 drives")
        self.assertLessEqual(drives[0][0] - base, km._WAKE_DRIVE_FLOOR_S + 2 * cadence,
                             "the first firing drives as soon as it has waited out its own floor")
        for now, cands in drives:
            for cand in cands:
                for e in cand["entries"]:
                    self.assertGreaterEqual(now - km._wake_ts_epoch(e["ts"]), km._WAKE_DRIVE_FLOOR_S,
                                            "only the aged subset ever drives")

    def test_sessions_of_other_backends_are_skipped(self):
        self.fb.owned = set()
        self._tick()
        self.assertEqual(self.fb.calls, [], "tmux CLIs are interactive — they deliver their own queue")

    def test_the_pusher_cycle_runs_the_tick(self):
        # (now, tmux) — the cycle's ONE liveness snapshot, not a per-job fresh read (2026-08-10 CPU fix).
        # Scoped to the CYCLE's body: the whole-file pin also matched the tick's own def line, so
        # deleting the wiring kept every test green (2026-08-18 review, mutation-verified).
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_idle_queue_drive_tick(now, tmux)", src,
                      "the pusher cycle drives queued wake signals server-side — unattended, no client needed")


class FakeLive:
    """A live, connected, idle SdkSession as drive_idle_queue sees one (duck-typed)."""

    def __init__(self, name="web"):
        self.name = name
        self.sid = SID
        self.ended = False
        self.inflight = 0
        self._compacting = False
        self._clearing = False
        self._rewind_to = ""
        self._rewind_armed = False
        self.sent = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._stop.wait, daemon=True)
        self.thread.start()

    def enqueue(self, text):
        self.sent.append(text)

    def close(self):
        self._stop.set()


def _ent(pos, text, wrapper=True, ts=TS % (14, 0)):
    return {"pos": pos, "ts": ts, "text": text, "wrapper": wrapper}


class DriveDelivery(unittest.TestCase):
    """SdkBackend.drive_idle_queue — gates, the watermark latch, delivery, loudness."""

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.logs = []
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None,
                                log=self.logs.append)
        sb.write_reg(self.state, SID, {"sid": SID, "name": "web", "alive": True})
        self.tx = str(self.state / (SID + ".jsonl"))
        self.fakes = []

    def tearDown(self):
        for f in self.fakes:
            f.close()

    def _live(self, **kw):
        s = FakeLive()
        for k, v in kw.items():
            setattr(s, k, v)
        self.fakes.append(s)
        self.be.sessions[SID] = s
        return s

    def _cand(self, entries=None, mark=None, sid=SID):
        entries = entries if entries is not None else [_ent(10, _wrap(0)), _ent(11, _wrap(1))]
        return {"sid": sid, "path": self.tx, "entries": entries,
                "mark": mark or (entries[-1]["pos"], entries[-1]["ts"])}

    def _problems(self):
        return [p["text"] for p in self.be.problems()]

    def test_cron_shape_live_idle_session_gets_the_backlog(self):
        s = self._live()
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(s.sent, [_wrap(0) + "\n\n" + _wrap(1)],
                         "one driven turn carrying the CLI's own queued texts verbatim, in order")
        self.assertTrue(any("idle-queue drive" in str(m) and "web" in str(m) for m in self.logs),
                        "each drive logs one kernel-log line naming the session")
        self.assertFalse(any("idle-queue drive" in p for p in self._problems()),
                         "a normal drive is not a problem")

    def test_an_open_turn_stands_down_without_burning_the_watermark(self):
        s = self._live(inflight=1)
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(s.sent, [], "never mid-turn")
        s.inflight = 0
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(len(s.sent), 1, "the stand-down did not latch — the backlog still delivers")

    def test_compacting_and_clearing_stand_down(self):
        s = self._live(_compacting=True)
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(s.sent, [])
        s._compacting, s._clearing = False, True
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(s.sent, [])

    def test_an_ended_session_never_revives(self):
        sb.write_reg(self.state, SID, {"sid": SID, "name": "web", "alive": False})
        ensured = []
        with mock.patch.object(self.be, "_ensure", lambda sid, **kw: ensured.append(sid)):
            self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(ensured, [], "the user ended this session — housekeeping must not revive it")

    def test_a_cut_turn_stands_down(self):
        sb.append_state(self.state, SID, "working")   # the tail a kernel-death cut leaves
        ensured = []
        with mock.patch.object(self.be, "_ensure", lambda sid, **kw: ensured.append(sid)):
            self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(ensured, [], "a cut turn is the boot/crash resume machinery's recovery")

    def test_a_turn_cut_mid_retry_stands_down(self):
        # 'retrying' is a machine-active open-turn state (the CLI mid-turn, waiting out an API
        # error): a restart there cuts the turn exactly as a working cut does, and boot reconcile's
        # widened cut discriminator claims it — the drive must yield, not deliver into the resume.
        sb.append_state(self.state, SID, "retrying")
        ensured = []
        with mock.patch.object(self.be, "_ensure", lambda sid, **kw: ensured.append(sid)):
            self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(ensured, [], "a mid-retry cut is boot reconcile's recovery, not the drive's")

    def test_a_turn_cut_mid_compact_stands_down(self):
        sb.append_state(self.state, SID, "compacting")   # machine-active, same as retrying
        ensured = []
        with mock.patch.object(self.be, "_ensure", lambda sid, **kw: ensured.append(sid)):
            self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(ensured, [], "a mid-compact cut is boot reconcile's recovery, not the drive's")

    def test_one_drive_per_watermark(self):
        s = self._live()
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(len(s.sent), 1, "the same watermark never drives twice")

    def test_a_newer_enqueue_rearms_and_delivers_only_the_new(self):
        s = self._live()
        self.be.drive_idle_queue([self._cand()], wait=True)
        entries = [_ent(10, _wrap(0)), _ent(11, _wrap(1)), _ent(12, _wrap(2), ts=TS % (15, 0))]
        self.be.drive_idle_queue([self._cand(entries=entries)], wait=True)
        self.assertEqual(len(s.sent), 2, "a newer enqueue is new information — one more drive")
        self.assertEqual(s.sent[1], _wrap(2), "already-driven texts are not re-sent")

    def test_the_watermark_survives_a_kernel_restart(self):
        s = self._live()
        self.be.drive_idle_queue([self._cand()], wait=True)
        be2 = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        s2 = FakeLive()
        self.fakes.append(s2)
        be2.sessions[SID] = s2
        be2.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(s2.sent, [], "the latch is persisted — a restart cannot re-fire a driven backlog")

    def test_a_dormant_session_is_reconnected_and_delivered(self):
        cbs = []

        def fake_ensure(sid, on_boot_settled=None):
            cbs.append(on_boot_settled)
            if on_boot_settled:
                on_boot_settled()
            return self._live()

        with mock.patch.object(self.be, "_ensure", fake_ensure):
            self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(len(self.fakes), 1)
        self.assertEqual(len(self.fakes[0].sent), 1, "reconnect-if-dormant, then deliver")
        self.assertTrue(cbs and cbs[0] is not None, "the spawn holds a stagger slot until the CLI proves up")

    def test_dormant_spawns_are_staggered_like_boot_resume(self):
        sb.write_reg(self.state, SID2, {"sid": SID2, "name": "api", "alive": True})
        self.be._spawn_sem = threading.Semaphore(1)
        old_slot = sb.BOOT_RESUME_SLOT_S
        sb.BOOT_RESUME_SLOT_S = 0.05
        try:
            def fake_ensure(sid, on_boot_settled=None):
                return self._live()          # never fires the callback: the CLI never proves up

            with mock.patch.object(self.be, "_ensure", fake_ensure):
                self.be.drive_idle_queue(
                    [self._cand(), self._cand(entries=[_ent(10, _wrap(9))], sid=SID2)], wait=True)
        finally:
            sb.BOOT_RESUME_SLOT_S = old_slot
        self.assertEqual(len(self.fakes), 2, "the backstop is loud but the sweep continues")
        self.assertTrue(any("backstop" in p for p in self._problems()),
                        "an expired stagger slot says so where the user looks")

    def test_a_refused_reconnect_is_problem_ring_loud(self):
        with mock.patch.object(self.be, "_ensure", lambda sid, **kw: None):
            self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertTrue(any("idle-queue drive" in p for p in self._problems()),
                        "a drive failure is never silent")

    def test_a_failed_send_is_problem_ring_loud(self):
        s = self._live()
        s.enqueue = mock.Mock(side_effect=RuntimeError("stream gone"))
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertTrue(any("idle-queue drive" in p for p in self._problems()))

    def test_boot_parity_the_drive_rides_the_persisted_queue_channel(self):
        """The drive delivers via enqueue() — the same channel boot-reconcile restores through — so a
        kernel death between the drive and the CLI taking the turn loses nothing: the text is in the
        reg's persisted queue and the next boot re-delivers it, exactly as for any queued message."""
        reg = sb.read_reg(self.state, SID)
        real = sb.SdkSession(self.be, reg)            # never started: enqueue works, nothing spawns
        with mock.patch.object(self.be, "_ensure", lambda sid, **kw: real):
            self.be.drive_idle_queue([self._cand()], wait=True)
        q = (sb.read_reg(self.state, SID) or {}).get("queue") or []
        self.assertEqual(q, [_wrap(0) + "\n\n" + _wrap(1)],
                         "the driven text persists like any queued message until the CLI takes it")

    def test_a_bare_queued_message_rides_along_but_is_never_resent(self):
        """The CLI delivers its own queued plain messages at its next turn; the drive re-sending one
        would double-deliver it. Only wake-signal texts go into the driven turn; the bare entry is
        counted, not sent."""
        s = self._live()
        entries = [_ent(10, _wrap(0)), _ent(11, "a queued plain message", wrapper=False),
                   _ent(12, _wrap(1), ts=TS % (15, 0))]
        self.be.drive_idle_queue([self._cand(entries=entries)], wait=True)
        self.assertEqual(s.sent, [_wrap(0) + "\n\n" + _wrap(1)],
                         "a bare queued message is the CLI's own to deliver — never re-sent")
        self.assertTrue(any("+1 other queued item" in str(m) for m in self.logs),
                        "the ride-along is counted in the drive's log line")

    def test_an_agent_notice_rides_along_but_is_never_resent(self):
        # the parser demotes agent completion notices to wrapper=False (the CLI delivers that class
        # itself in every regime); delivery must honor it the same way it honors a bare message
        s = self._live()
        entries = [_ent(10, _wrap(0)),
                   _ent(11, _wrap(5, tid="a0123456789abcdef"), wrapper=False),
                   _ent(12, _wrap(1), ts=TS % (15, 0))]
        self.be.drive_idle_queue([self._cand(entries=entries)], wait=True)
        self.assertEqual(s.sent, [_wrap(0) + "\n\n" + _wrap(1)],
                         "re-sending an agent notice would double-deliver a subagent completion")
        self.assertTrue(any("+1 other queued item" in str(m) for m in self.logs))

    def test_a_kernel_death_between_latch_and_delivery_is_not_a_discard(self):
        """The reg's driveMark is written AFTER the enqueue lands: a kernel death between acceptance
        and delivery (a real window — dormant candidates ahead in the batch each hold delivery back
        for a CLI spawn, and romp is self-hosting, so deploys restart the kernel) must re-drive on
        the next boot, never silently discard the backlog forever."""
        s = self._live()
        with mock.patch.object(self.be, "_drive_deliver", lambda todo: None):   # death before delivery
            self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(s.sent, [], "delivery never ran")
        self.assertIsNone((sb.read_reg(self.state, SID) or {}).get("driveMark"),
                          "acceptance alone persists nothing — the reg latch is delivery's to write")
        be2 = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        s2 = FakeLive()
        self.fakes.append(s2)
        be2.sessions[SID] = s2
        be2.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(len(s2.sent), 1, "the next kernel re-produces the candidate and drives it")

    def test_a_turn_opening_between_acceptance_and_delivery_stands_down(self):
        """Gates are re-checked at the SEND moment: with a dormant candidate ahead in the batch, a
        turn can open on a live candidate while the dormant one spawns — its delivery must stand
        down (without latching), not inject stale pings mid-turn."""
        sb.write_reg(self.state, SID2, {"sid": SID2, "name": "api", "alive": True})
        s = self._live()

        def fake_ensure(sid, on_boot_settled=None):
            if on_boot_settled:
                on_boot_settled()
            s.inflight = 1                       # the live candidate's turn opens during this spawn
            d = FakeLive()
            self.fakes.append(d)
            return d

        with mock.patch.object(self.be, "_ensure", fake_ensure):
            self.be.drive_idle_queue([self._cand(sid=SID2, entries=[_ent(10, _wrap(9))]),
                                      self._cand()], wait=True)
        self.assertEqual(s.sent, [], "never mid-turn — the acceptance-time check alone was a TOCTOU hole")
        s.inflight = 0
        self.be.drive_idle_queue([self._cand()], wait=True)
        self.assertEqual(len(s.sent), 1, "the stand-down did not latch — the backlog still delivers")

    def test_overlapping_workers_for_one_sid_cannot_double_deliver(self):
        """One worker per sid: W1 accepts a backlog and stalls (a dormant candidate ahead in its
        batch can hold it for minutes); a newer enqueue lands and ages, and the next tick would
        dispatch W2 for the same sid; a turn opens; W1 stands down, restoring its pre-acceptance
        mark OVER W2's newer latch — the restored mark re-drives the whole backlog, and W2 then
        delivers its slice a second time. The in-flight exclusion refuses the second acceptance
        while a worker carries the sid; the deferred backlog still delivers, once, on a later
        tick, and every wake text is heard exactly once."""
        s = self._live()
        captured = []
        with mock.patch.object(self.be, "_drive_deliver", captured.append):
            self.be.drive_idle_queue([self._cand()], wait=True)           # W1 accepted, in flight
        self.assertEqual(len(captured), 1)
        entries = [_ent(10, _wrap(0)), _ent(11, _wrap(1)), _ent(12, _wrap(2), ts=TS % (15, 0))]
        with mock.patch.object(self.be, "_drive_deliver", captured.append):
            self.be.drive_idle_queue([self._cand(entries=entries)], wait=True)   # a newer enqueue…
        self.assertEqual(len(captured), 1, "…does not dispatch a second worker while one is in flight")
        s.inflight = 1                             # a turn opens while W1 is stalled…
        self.be._drive_deliver(captured[0])        # …so W1 stands down at the send moment
        self.assertEqual(s.sent, [], "W1 stood down — never mid-turn")
        s.inflight = 0                             # the turn settles
        self.be.drive_idle_queue([self._cand(entries=entries)], wait=True)
        for batch in captured[1:]:                 # any second worker would deliver its slice NOW
            self.be._drive_deliver(batch)
        self.assertEqual(len(s.sent), 1, "the deferred backlog delivers once, on the next tick")
        for i in range(3):
            self.assertEqual(sum(t.count(_wrap(i)) for t in s.sent), 1,
                             "each wake text is heard exactly once across all driven turns")

    def test_a_died_and_respawned_session_gets_the_backlog_not_its_ghost(self):
        """Delivery re-resolves the session object: enqueueing into a dead snapshot would mirror the
        dead queue over reg['queue'] and the texts would evaporate with the object, latched against
        any re-drive."""
        stale = self._live()
        captured = []
        with mock.patch.object(self.be, "_drive_deliver", captured.append):
            self.be.drive_idle_queue([self._cand()], wait=True)
        stale.close()                             # the CLI dies in the latch→delivery window...
        stale.thread.join(timeout=2)
        fresh = FakeLive()                        # ...and something respawns the session
        self.fakes.append(fresh)
        self.be.sessions[SID] = fresh
        self.be._drive_deliver(captured[0])
        self.assertEqual(fresh.sent, [_wrap(0) + "\n\n" + _wrap(1)],
                         "the LIVE object receives the backlog")
        self.assertEqual(stale.sent, [], "the ghost gets nothing — its queue mirror is a clobber")

    def test_a_raising_ensure_returns_its_stagger_slot(self):
        """Boot reconcile frees its slot when _ensure raises; the drive must too — _spawn_sem lives
        for the process, so each leaked slot permanently shrinks every future spawn's budget into
        180s-backstop purgatory."""
        self.be._spawn_sem = threading.Semaphore(1)
        old_slot = sb.BOOT_RESUME_SLOT_S
        sb.BOOT_RESUME_SLOT_S = 0.05
        try:
            with mock.patch.object(self.be, "_ensure",
                                   mock.Mock(side_effect=OSError("reg write failed"))):
                self.be.drive_idle_queue([self._cand()], wait=True)
            got = self.be._spawn_sem.acquire(timeout=1)
        finally:
            sb.BOOT_RESUME_SLOT_S = old_slot
        self.assertTrue(got, "the slot came back — the parked release never got attached")
        self.be._spawn_sem.release()
        self.assertTrue(any("idle-queue drive" in p for p in self._problems()),
                        "the failed spawn is problem-ring loud")

    def test_boot_reconcile_and_the_drive_share_one_spawn_budget(self):
        """Boot reconcile used to mint its own same-sized semaphore, so post-restart boot resumes and
        drive spawns could burst to 2x BOOT_RESUME_CONCURRENCY. Pre-holding the backend's only slot
        (a drive spawn in flight) must make boot reconcile's stagger wait on it — one shared budget."""
        self.be._spawn_sem = threading.Semaphore(1)
        self.assertTrue(self.be._spawn_sem.acquire(timeout=1))    # a drive spawn holds the slot
        old_slot = sb.BOOT_RESUME_SLOT_S
        sb.BOOT_RESUME_SLOT_S = 0.05
        try:
            reg = {"sid": SID, "name": "web", "alive": True, "queue": ["a queued plain message"]}
            sb.write_reg(self.state, SID, reg)
            with mock.patch.object(self.be, "_ensure", lambda sid, on_boot_settled=None: None):
                self.be._boot_reconcile([reg])
        finally:
            sb.BOOT_RESUME_SLOT_S = old_slot
            self.be._spawn_sem.release()
        self.assertTrue(any("resume slot backstop expired" in str(m) for m in self.logs),
                        "boot reconcile drew on the drive-held budget — the same semaphore instance")


class QueuedBubbleDisplay(unittest.TestCase):
    """A driven wrapper parked in the SDK pending queue must never render as the user's queued
    message (the 2026-06-30 regression: a raw <task-notification> shown as '1 queued message'). The
    _genuine_queued filter used to exist only on the tmux transcript fold; the drive now routes
    wrappers through the SDK's _pending/reg queue, which build_session reads raw — so the bubble
    build filters too, keeping idx aligned with the backend position for cancelQueued."""

    class _Be:
        """An SDK-shaped backend double: owns the sid, serves a fixed queue, exposes unqueue."""

        def __init__(self, queued):
            self._q = list(queued)

        def owns(self, sid):
            return True

        def pending_queued(self, sid):
            return list(self._q)

        def unqueue(self, sid, idx, expect=None):
            return None

        def live_atoms(self, sid):
            return []

        def busy(self, sid):
            return None

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tx = os.path.join(self.dir, SID + ".jsonl")
        _write_tx(self.tx, _turn())
        self.now = int(time.time())
        self.sess = [{"sid": SID, "name": "web", "path": self.tx, "mtime": self.now}]

    def _events(self, queued):
        be = self._Be(queued)
        with mock.patch.object(km, "_sessions", lambda now: list(self.sess)), \
             mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: be)), \
             mock.patch.object(km, "_captions", lambda sid: {}), \
             mock.patch.object(km, "_limit_hold", lambda sid: None):
            m = km.build_session(SID, self.now, tmux={})
        self.assertIsNotNone(m, "the session must build")
        return m["events"]

    def test_a_driven_wrapper_never_renders_as_the_users_queued_message(self):
        evs = self._events([_wrap(0), "please rerun the failing test"])
        q = next((e for e in evs if e.get("kind") == "queued"), None)
        self.assertIsNotNone(q, "the user's own queued message still shows")
        self.assertEqual([t["md"] for t in q["texts"]], ["please rerun the failing test"],
                         "the wrapper is delivery plumbing, not the user's pending input")
        self.assertEqual(q["texts"][0]["idx"], 1,
                         "a surviving bubble's idx still names its backend _pending position")

    def test_an_all_wrapper_queue_emits_no_queued_event(self):
        evs = self._events([_wrap(0)])
        self.assertIsNone(next((e for e in evs if e.get("kind") == "queued"), None),
                          "nothing of the user's is pending — no empty queued group either")

    def test_the_nudge_guard_reads_only_genuine_queued(self):
        # _backend_queued means "the user has messages waiting" — a parked wrapper must not
        # suppress nudges as if the user had spoken
        with mock.patch.object(km.Sessions, "backend_for",
                               staticmethod(lambda sid: self._Be([_wrap(0)]))):
            self.assertFalse(km._backend_queued(SID), "a parked wrapper is not the user speaking")
        with mock.patch.object(km.Sessions, "backend_for",
                               staticmethod(lambda sid: self._Be([_wrap(0), "hold on"]))):
            self.assertTrue(km._backend_queued(SID))


class OvernightShape(unittest.TestCase):
    """The exact bug, end to end through the kernel tick and a real backend: an idle session, a
    Monitor's overnight enqueue backlog, no external input — a turn is driven, once, with the backlog."""

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lambda m: None)
        sb.write_reg(self.state, SID, {"sid": SID, "name": "web", "alive": True})
        self.tx = str(self.state / (SID + ".jsonl"))
        _write_tx(self.tx, _turn() + _backlog(33))
        self.alive = [{"sid": SID, "path": self.tx, "name": "web"}]
        self.fake = FakeLive()

    def tearDown(self):
        self.fake.close()

    def test_the_overnight_backlog_drives_one_turn(self):
        with mock.patch.object(self.be, "_ensure", lambda sid, **kw: self.fake), \
             mock.patch.object(km, "_sdk", lambda: self.be), \
             mock.patch.object(km, "_alive_sessions", lambda now, tmux: self.alive):
            km._idle_queue_drive_tick(int(time.time()), {SID: {}})
            # the drive runs on a worker thread from the tick — wait for it (bounded)
            for _ in range(100):
                if self.fake.sent:
                    break
                time.sleep(0.02)
            km._idle_queue_drive_tick(int(time.time()), {SID: {}})   # the same parse again: latched
            time.sleep(0.1)
        self.assertEqual(len(self.fake.sent), 1, "one driven turn, not one per notification, not zero")
        for i in range(33):
            self.assertIn(_wrap(i), self.fake.sent[0], "the whole backlog is delivered")


if __name__ == "__main__":
    unittest.main()
