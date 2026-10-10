"""Reviews 5 to 7 of hook-callback-stall (synthetic data only): the registry writer under overload. Twenty sessions
stream records (the real _write_host_ack: its once-a-second throttle and carry), each queues a hook write every 2.5 s,
and every writer item costs 60 ms (review 4's measured cost under load): the read-position saves alone want more than
the writer has, and the hook writes alone fill the half of it the saves leave (so a copy sent to the back at every
re-queue never reaches the front, the condition the saved-queue check needs). Five of the sessions are busy: their saved queue changes every second, so each re-queues its ('queue', sid)
copy every second. Real sessions re-queue that copy only when the queue changes (review 7), so the other fifteen never
do; all twenty re-queuing it every 2 s asked more than the writer can do and made the old fixed-share check depend on
the machine's load (it failed 4 of 6 runs at a load average of 47 to 70).

What must hold, asserted as progress rather than as a share of a total that depends on machine load:
- other writes keep making progress: after a 2 s warm-up there is no stretch of 3 s in which no other write ran, and
  every other write queued in the run's first half has run by its end (with the saves at the front, 5420787e7 ran none);
- every session's read position keeps being saved: each saved offset trails what it consumed by under 10 s of records
  (fb5c44b65 saved none while the writer lagged; 5420787e7 never saved a few);
- every busy session's saved queue keeps landing: the copy on disk was queued in the run's second half (sent to the
  back at each re-queue, as at c695116fc, it never landed after the start).
Each check holds as long as other writes' waits grow by less than a second a second, so none depends on a fixed latency.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
TREE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from romp_load import load_source  # noqa: E402
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

sb = load_source("hookstall_sb_writer_load", os.path.join(TREE, "bin", "romp_sdk_backend.py"))

NSESS, RATE, HOOK_EVERY, ITEM_S, SECS = 20, 50.0, 2.5, 0.06, 20.0
BUSY, MIRROR_EVERY = 5, 1.0           # sessions whose saved queue changes, and how often it does
WARMUP_S, GAP_S = 2.0, 3.0            # no stretch of GAP_S after the warm-up without another write run
LAG_BOUND = int(RATE * 10.0)          # records: ten seconds of streaming


class FakeHost:
    def __init__(self, i):
        self.hello = {"host": {"pid": 5000 + i, "start": "h%d" % i}, "cli": {"pid": 6000 + i, "start": "c%d" % i}}
        self.exit_info = None
        self.ack_offset = -1


class WriterUnderLoad(unittest.TestCase):
    def test_saves_and_other_writes_both_keep_moving(self):
        d = tempfile.mkdtemp(prefix="hookstall-load-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "session-hosts").write_text("off")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sids = ["11111111-2222-4333-8444-%012d" % (300 + i) for i in range(NSESS)]
        filler = [{"md": "synthetic text " * 20, "qid": "q%d" % j} for j in range(20)]
        for s in sids:
            sb.write_reg(Path(d), s, {"sid": s, "name": "web", "alive": True, "echoes": filler})
        orig_merge = be._reg_merge_locked

        def slow_merge(sid, make_fields):     # every WRITER item costs ITEM_S inside the lock (a slow RMW under load)
            if threading.current_thread().name == "romp-reg-writer":
                time.sleep(ITEM_S)
            return orig_merge(sid, make_fields)
        be._reg_merge_locked = slow_merge

        sessions = [types.SimpleNamespace(sid=s, name="web", _host=FakeHost(i), _host_ack_t=0.0)
                    for i, s in enumerate(sids)]
        stop = threading.Event()
        lk = threading.Lock()
        pending = {}      # token -> time queued
        waits = []        # how long each hook write that ran had waited
        ran_at = []       # when each hook write ran
        queued_at = {}    # token -> time queued, for every hook write issued

        def hook_item(tok, sid):
            t = time.monotonic()
            with lk:
                q = pending.pop(tok, None)
            if q is not None:
                waits.append(t - q)
                ran_at.append(t)
            be._update_reg(sid, lastSkill={"at": tok, "name": "x"})

        def loop(i):
            sess = sessions[i]
            n = 0
            now = time.monotonic()
            next_hook = now + HOOK_EVERY * i / NSESS
            next_mirror = now + MIRROR_EVERY * i / NSESS
            while not stop.is_set():
                sess._host.ack_offset += 1
                be._write_host_ack(sess)                  # the transport's per-record on_ack
                now = time.monotonic()
                if now >= next_hook:
                    next_hook = now + HOOK_EVERY
                    n += 1
                    tok = i * 1_000_000 + n
                    with lk:
                        pending[tok] = now
                        queued_at[tok] = now
                    be._reg_job(None, lambda t=tok, s=sess.sid: hook_item(t, s), sid=sess.sid)
                if i < BUSY and now >= next_mirror:       # a busy session's saved queue changed: its copy re-queued
                    next_mirror = now + MIRROR_EVERY
                    be._reg_job(("queue", sess.sid), lambda s=sess.sid, q=now: be._update_reg(s, queueMirror={"q": q}))
                time.sleep(1.0 / RATE)

        ths = [threading.Thread(target=loop, args=(i,), name="sdk:web%d" % i, daemon=True) for i in range(NSESS)]
        t0 = time.monotonic()
        for t in ths:
            t.start()
        time.sleep(SECS)
        lags = []
        stale = []
        t_snap = time.monotonic()
        for sess in sessions:
            reg = sb.read_reg(Path(d), sess.sid) or {}
            ack = reg.get("hostAck") or {}
            lags.append(sess._host.ack_offset - int(ack.get("offset", -1)))
            if sessions.index(sess) < BUSY:
                q = (reg.get("queueMirror") or {}).get("q")
                stale.append(round(t_snap - q, 1) if q is not None else SECS)
        stop.set()
        t_end = time.monotonic()
        with lk:
            still = dict(pending)
            ran = list(waits)
            ran_times = sorted(ran_at)
        for t in ths:
            t.join(5)
        # the longest stretch after the warm-up with no other write run
        marks = [t0 + WARMUP_S] + [x for x in ran_times if x >= t0 + WARMUP_S] + [t_end]
        gap = max(b - a for a, b in zip(marks, marks[1:]))
        half = t0 + SECS / 2.0
        early_left = sorted(round(t_end - q, 1) for q in still.values() if q < half)
        oldest = max([t_end - q for q in still.values()] + ran + [0.0])
        print("\n[writer load] %d sessions (%d busy), %.0f ms an item, %.0f s: other writes ran %d of %d, longest stretch "
              "with none %.2f s, oldest wait %.2f s, first-half writes still queued %d; per-session save lag (records, "
              "sorted) %s; busy sessions' saved queue age (s, sorted) %s"
              % (NSESS, BUSY, ITEM_S * 1000, SECS, len(ran), len(ran) + len(still), gap, oldest, len(early_left),
                 sorted(lags), sorted(stale)))
        self.assertLess(gap, GAP_S, "no other write ran for %.1f s" % gap)
        self.assertEqual(early_left, [], "%d other writes queued in the first half never ran (waiting %s s)"
                         % (len(early_left), early_left[-5:]))
        trailing = [x for x in lags if x >= LAG_BOUND]
        self.assertEqual(trailing, [], "%d sessions' saved read position trails by %d or more records: %s"
                         % (len(trailing), LAG_BOUND, sorted(lags)))
        self.assertLess(max(stale), SECS / 2.0, "a busy session's saved queue on disk was queued %.1f s before the "
                        "end, before the run's second half: %s" % (max(stale), sorted(stale)))


if __name__ == "__main__":
    unittest.main()
