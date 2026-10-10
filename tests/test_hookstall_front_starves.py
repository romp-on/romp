"""Review 5 of hook-callback-stall, must-fix 1 (synthetic): many streaming sessions' hostAck jobs at the front of the queue; does another session's
ordinary queued item still land while their total cost exceeds the writer's capacity?"""
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
sb = load_source("hookstall_sb_front", os.path.join(TREE, "bin", "romp_sdk_backend.py"))
N, ACK_S, RUN_S = 20, 0.06, 8.0


class FakeHost:
    def __init__(self, i):
        self.hello = {"host": {"pid": 5000 + i, "start": "h%d" % i}, "cli": {"pid": 6000 + i, "start": "c%d" % i}}
        self.exit_info = None
        self.ack_offset = -1


class FrontStarves(unittest.TestCase):
    def test_other_item_lands(self):
        d = tempfile.mkdtemp(prefix="hookstall-front-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "session-hosts").write_text("off")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        orig = be._update_reg_with

        def slow(sid, mk):                      # a hostAck write's wall time under load (bench: 50-100 ms an item)
            time.sleep(ACK_S)
            orig(sid, mk)
        be._update_reg_with = slow
        sids = ["11111111-2222-4333-8444-%012d" % (700 + i) for i in range(N + 1)]
        for sid in sids:
            sb.write_reg(Path(d), sid, {"sid": sid, "name": "web", "alive": True})
        stop = threading.Event()
        landed = {}

        def stream(i):
            s = types.SimpleNamespace(sid=sids[i], name="web%d" % i, _host=FakeHost(i), _host_ack_t=0.0)
            while not stop.is_set():
                s._host.ack_offset += 1
                be._write_host_ack(s)
                time.sleep(0.05)
        ths = [threading.Thread(target=stream, args=(i,), name="sdk:web%d" % i, daemon=True) for i in range(N)]
        for t in ths:
            t.start()
        time.sleep(1.5)
        other = sids[N]
        t0 = time.time()

        def other_item():
            landed["t"] = time.time() - t0
        threading.Thread(target=lambda: be._reg_job(None, other_item, sid=other), name="sdk:other", daemon=True).start()
        time.sleep(RUN_S)
        stop.set()
        print("\nN=%d streaming, hostAck item %.0f ms: the other session's item %s" % (
            N, ACK_S * 1000, ("landed after %.2fs" % landed["t"]) if "t" in landed else "never landed in %.0fs" % RUN_S))
        self.assertIn("t", landed)


if __name__ == "__main__":
    unittest.main()
