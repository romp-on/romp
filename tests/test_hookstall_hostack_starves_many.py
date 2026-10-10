"""Review 4 (synthetic): twenty streaming sessions, one hook record a second each, the writer at 60 ms an item
(the bench's measured 50-100 ms under CPU load): every session's hostAck is pushed to the back at each re-queue, and at a
drain (2 s bound) each session's final ack runs after its whole backlog."""
import os, shutil, sys, tempfile, threading, time, types, unittest
from pathlib import Path
HERE = os.path.dirname(os.path.realpath(__file__))
TREE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from romp_load import load_source  # noqa: E402
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("hookstall_sb_starve_many", os.path.join(TREE, "bin", "romp_sdk_backend.py"))
N = int(os.environ.get("NSESS", "20"))
SIDS = ["11111111-2222-4333-8444-%012d" % (900 + i) for i in range(N)]


class FakeHost:
    def __init__(self, i):
        self.hello = {"host": {"pid": 5000 + i, "start": "h"}, "cli": {"pid": 6000 + i, "start": "c"}}
        self.exit_info = None
        self.ack_offset = -1


class ManySessions(unittest.TestCase):
    def test_streaming_sessions_final_acks_under_a_lagging_writer(self):
        d = tempfile.mkdtemp(prefix="hookstall-many-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "session-hosts").write_text("off")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        for s in SIDS:
            sb.write_reg(Path(d), s, {"sid": s, "name": "web", "alive": True})
        sess = [types.SimpleNamespace(sid=s, name="web", _host=FakeHost(i), _host_ack_t=0.0) for i, s in enumerate(SIDS)]
        stop = threading.Event()
        mid = {}

        def slow_item(sid, i):                         # the RMW's wall time is spent holding _reg_lock, as a real one is
            be._update_reg_with(sid, lambda: (time.sleep(0.06), {"lastSkill": {"at": i, "name": "x"}})[1])

        def loop(k):
            s = sess[k]
            i = 0
            next_hook = time.time() + k * 0.05
            while not stop.is_set():
                i += 1
                s._host.ack_offset += 1               # ten records a second
                be._write_host_ack(s)
                if time.time() >= next_hook:           # one PostToolUse record a second
                    next_hook += 1.0
                    be._reg_job(None, lambda sid=s.sid, i=i: slow_item(sid, i), sid=s.sid)
                time.sleep(0.1)
            s.detached = True                          # SdkBackend.drain latches this before shutdown()
            be._write_host_ack(s, force=True)          # _leave_host on a live host (the drain's detach)
            s._host = None
            with be._session_ending(s.sid):            # SdkSession._run's finally
                pass
        ths = [threading.Thread(target=loop, args=(k,), name="sdk:web%d" % k, daemon=True) for k in range(N)]
        for t in ths:
            t.start()
        time.sleep(10.0)
        for s in sess:
            r = sb.read_reg(Path(d), s.sid) or {}
            mid[s.sid] = (s._host.ack_offset, (r.get("hostAck") or {}).get("offset", -1))
        stop.set()
        deadline = time.time() + 2.0                   # SdkBackend.drain: join every thread within one 2 s bound
        for t in ths:
            t.join(max(0.05, deadline - time.time()))
        still = sum(t.is_alive() for t in ths)
        exact = lagging = 0
        lag_records = []
        for s in sess:
            r = sb.read_reg(Path(d), s.sid) or {}
            on_disk = (r.get("hostAck") or {}).get("offset", -1)
            c = be._host_ack_carry[s.sid][1]
            if on_disk == c:
                exact += 1
            else:
                lagging += 1
                lag_records.append(c - on_disk)
        mid_lag = sorted(c - o for c, o in mid.values())
        print("at 10 s while streaming: registry hostAck lag per session (records) min %d median %d max %d"
              % (mid_lag[0], mid_lag[len(mid_lag) // 2], mid_lag[-1]))
        print("at the drain bound: %d of %d session threads still ending; %d of %d registries exact, %d lagging (records "
              "a restart replays: %s)" % (still, N, exact, N, lagging, sorted(lag_records)))
        self.assertEqual(lagging, 0)


if __name__ == "__main__":
    unittest.main()
