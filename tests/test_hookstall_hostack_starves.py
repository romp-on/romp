"""Review 4 of hook-callback-stall (synthetic): a streaming session's hostAck job is pushed to the back of
the registry writer's queue at every re-queue (once a second), so while the writer is more than a second behind, the
registry's hostAck is never written; a crash or a drain cut then replays every record consumed since the last landed ack.
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

sb = load_source("hookstall_sb_starve", os.path.join(TREE, "bin", "romp_sdk_backend.py"))
SID = "11111111-2222-4333-8444-0000000000a1"


class FakeHost:
    def __init__(self):
        self.hello = {"host": {"pid": 4242, "start": "h1"}, "cli": {"pid": 4343, "start": "c1"}}
        self.exit_info = None
        self.ack_offset = -1


class HostAckStarves(unittest.TestCase):
    def test_a_streaming_sessions_hostack_never_lands_while_the_writer_is_over_a_second_behind(self):
        d = tempfile.mkdtemp(prefix="hookstall-starve-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "session-hosts").write_text("off")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True})
        sess = types.SimpleNamespace(sid=SID, name="web", _host=FakeHost(), _host_ack_t=0.0)
        stop = threading.Event()
        WRITE_S = 0.06                      # one registry item's wall time under load (measured 50-100 ms in the bench)

        def slow_item(i):
            time.sleep(WRITE_S)
            be._update_reg(SID, lastSkill={"at": i, "name": "x"})

        def loop():                         # the session's loop: records stream, hooks fire (one Bash per 40 ms)
            i = 0
            while not stop.is_set():
                i += 1
                sess._host.ack_offset += 1
                be._write_host_ack(sess)    # the transport's per-record on_ack (rate-limited to once a second)
                be._reg_job(None, lambda i=i: slow_item(i), sid=SID)   # a PostToolUse hook's queued record
                time.sleep(0.04)            # 25 items/s queued against ~16/s the writer can run
        th = threading.Thread(target=loop, name="sdk:web", daemon=True)
        th.start()
        time.sleep(8.0)
        stop.set()
        th.join(5)
        reg = sb.read_reg(Path(d), SID) or {}
        consumed = sess._host.ack_offset
        on_disk = (reg.get("hostAck") or {}).get("offset", -1)
        with be._reg_jobs_lock:
            queued = len(be._reg_jobs)
            pos = [k for k in be._reg_jobs].index(("hostAck", SID)) if ("hostAck", SID) in be._reg_jobs else None
        print("consumed offset %d, registry hostAck offset %d, writer queue %d, hostAck at position %s"
              % (consumed, on_disk, queued, pos))
        # in this kernel the carry still knows; a kernel death (or a drain cut at its 2 s bound) does not
        self.assertEqual(be._host_ack_carry[SID][1], consumed)
        self.assertGreaterEqual(on_disk, consumed - 30,
                                "the registry's hostAck trails %d consumed records; a restart replays them"
                                % (consumed - on_disk))



class DrainCutsTheFinalAck(unittest.TestCase):
    def test_the_detachs_final_ack_runs_after_the_sessions_whole_backlog_and_misses_the_drain_bound(self):
        d = tempfile.mkdtemp(prefix="hookstall-drain-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "session-hosts").write_text("off")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True})
        sess = types.SimpleNamespace(sid=SID, name="web", _host=FakeHost(), _host_ack_t=0.0)
        stop = threading.Event()
        detached = threading.Event()

        def slow_item(i):
            time.sleep(0.06)
            be._update_reg(SID, lastSkill={"at": i, "name": "x"})

        def loop():
            i = 0
            while not stop.is_set():
                i += 1
                sess._host.ack_offset += 1
                be._write_host_ack(sess)
                be._reg_job(None, lambda i=i: slow_item(i), sid=SID)
                time.sleep(0.04)
            # the drain: the session leaves its live host (SdkSession._leave_host, exit_info None), then its thread's
            # finally runs inside _session_ending (SdkSession._run)
            sess.detached = True             # SdkBackend.drain latches this before shutdown()
            be._write_host_ack(sess, force=True)
            sess._host = None
            detached.set()
            with be._session_ending(SID):
                pass
        th = threading.Thread(target=loop, name="sdk:web", daemon=True)
        th.start()
        time.sleep(6.0)
        stop.set()
        detached.wait(5)
        t0 = time.time()
        th.join(2.0)                         # SdkBackend.drain's bound (timeout=2.0), then os._exit
        alive = th.is_alive()
        reg = sb.read_reg(Path(d), SID) or {}
        consumed = be._host_ack_carry[SID][1]
        on_disk = (reg.get("hostAck") or {}).get("offset", -1)
        print("drain: thread still ending after %.1fs: %s; consumed %d, registry hostAck %d" % (time.time() - t0, alive,
                                                                                            consumed, on_disk))
        self.assertEqual(on_disk, consumed, "a kernel exit at the drain bound replays %d consumed records"
                         % (consumed - on_disk))


if __name__ == "__main__":
    unittest.main()
