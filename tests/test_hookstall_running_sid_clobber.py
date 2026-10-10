"""Review 4 of hook-callback-stall (synthetic): a writer that exits clean clears _reg_running_sid in its outer
finally UNCONDITIONALLY, after it has already published "no writer". A successor writer started in that gap is running an
item of session X; the clobber makes X's end (_reg_flush_session) skip the wait and run X's later items, and the end's own
writes, while the successor's item is still in flight, so that item lands after them (review 3's must-fix 1 shape: a stale
queue save landing over the crash heal's queue).
"""
import os
import shutil
import sys
import tempfile
import threading
import time
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

sb = load_source("hookstall_sb_clobber", os.path.join(TREE, "bin", "romp_sdk_backend.py"))
SID = "11111111-2222-4333-8444-0000000000c7"


class GateLock:
    """A Lock whose acquire, called from _reg_writer_run's OUTER finally by the first writer, waits for `go` first: it
    holds the first writer in the gap between its clean return and that finally (a GIL preemption does the same)."""

    def __init__(self, outer_finally_line):
        self._l = threading.Lock()
        self.line = outer_finally_line
        self.parked = threading.Event()
        self.go = threading.Event()
        self.first_writer = None

    def acquire(self, blocking=True, timeout=-1):
        f = sys._getframe(1)
        while f is not None and f.f_code.co_name not in ("_reg_writer_run",):
            f = f.f_back
        me = threading.current_thread()
        if f is not None and f.f_lineno == self.line and self.first_writer is None:
            self.first_writer = me             # the first writer to reach its outer finally is parked there, once
            self.parked.set()
            self.go.wait(10)
        return self._l.acquire(blocking, timeout)

    def release(self):
        self._l.release()

    def __enter__(self):
        self.acquire()
        return True

    def __exit__(self, *a):
        self.release()
        return False


class RunningSidClobber(unittest.TestCase):
    def test_a_clean_exiting_writer_clears_its_successors_running_session(self):
        d = tempfile.mkdtemp(prefix="hookstall-clobber-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "session-hosts").write_text("off")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "queue": []})
        src = open(os.path.join(TREE, "kernel", "sdk_backend.py")).read().splitlines()
        start = next(i for i, l in enumerate(src) if l.startswith("    def _reg_writer_run"))
        outer = next(i for i in range(start, start + 60) if src[i] == "        finally:") + 2   # 1-based line of its `with`
        gl = GateLock(outer)
        be._reg_jobs_lock = gl
        be._reg_jobs_cond = threading.Condition(gl)
        order = []
        x1_gate = threading.Event()

        def x1():                                      # session X's queue save, slow behind a busy registry lock
            x1_gate.wait(10)
            be._update_reg(SID, queue=["held text (stale save)"])
            order.append("x1 landed")

        def on_loop(fn):
            t = threading.Thread(target=fn, name="sdk:web", daemon=True)
            t.start()
            t.join(10)

        # 1. a first writer runs one trivial item and exits clean; park it before its outer finally
        def first():
            be._reg_job(None, lambda: order.append("w1 item"), sid="99999999-2222-4333-8444-000000000000")
            with gl._l:
                pass
        on_loop(first)
        self.assertTrue(gl.parked.wait(5), "the first writer never reached its outer finally")
        # 2. session X's loop queues its save; no writer is published, so a second writer starts and runs it (blocked)
        on_loop(lambda: be._reg_job(("queue", SID), x1))
        deadline = time.time() + 5
        while be._reg_running_sid != SID and time.time() < deadline:
            time.sleep(0.001)
        self.assertEqual(be._reg_running_sid, SID, "the second writer is running X's save")
        # 3. the first writer's outer finally runs now
        gl.go.set()
        time.sleep(0.2)
        clobbered = be._reg_running_sid
        # 4. session X ends: its end flushes, then writes the crash heal's queue
        def end():
            with be._session_ending(SID):
                be._update_reg(SID, queue=["[crash notice]", "held text"])
                order.append("end wrote")
        threading.Timer(0.5, x1_gate.set).start()   # the busy lock frees half a second into X's end
        t0 = time.time()
        on_loop(end)
        waited = time.time() - t0
        x1_gate.set()
        time.sleep(0.3)
        final = (sb.read_reg(Path(d), SID) or {}).get("queue")
        print("running sid after the first writer's finally: %r; the end waited %.2fs; order %r; final queue %r"
              % (clobbered, waited, order, final))
        self.assertEqual(final, ["[crash notice]", "held text"], "the in-flight save landed after the session's end")


if __name__ == "__main__":
    unittest.main()
