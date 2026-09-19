#!/usr/bin/env python3
"""The bus's lifetime follows the MACHINE's romp role, not just local sessions. A quiet hub —
kernel up, zero local sessions — self-stopped on the local-session count alone, and every
cross-host message through it silently parked until a manual `ensure` (verified twice,
2026-08-12): peers dial a machine's bus for presence and INBOUND mail, so it must keep listening
while the kernel runs. Two guards land here:

- bus: the autostop gate (_idle_tick) counts a live local kernel (_kernel_up, /healthz) as a
  reason to stay, alongside local clients. No kernel and no clients → the old self-stop.
- kernel: a REFUSED bus notify kicks _revive_postal_bus (single-flight ensure) — boot-time ensure
  can't help a kernel that is already up when its bus dies, and the supervisor otherwise retried
  into the dead bus forever.

Synthetic everything: hermetic state dir, stub HTTP server for the kernel, dead ports, no ssh.
"""
import http.server
import json
import os
import socket
import tempfile
import threading
import time
import types
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
pm = load_source("romp_postal_buslife", os.path.join(BIN, "romp-postal-service"))
km = load_source("romp_kernel_buslife", os.path.join(BIN, "romp-kernel"))


def _dead_port():
    """A port with nothing listening: bind, read it, close — refused instantly afterwards."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _HealthzStub:
    """A minimal kernel: answers /healthz 200, everything else 404."""

    def __enter__(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                ok = self.path == "/healthz"
                self.send_response(200 if ok else 404)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % self.srv.server_port

    def __exit__(self, *a):
        self.srv.shutdown()


class KernelUp(unittest.TestCase):
    def setUp(self):
        self._base = pm.KERNEL_BASE
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)

    def tearDown(self):
        pm.KERNEL_BASE = self._base
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam

    def test_true_when_healthz_answers(self):
        with _HealthzStub() as base:
            pm.KERNEL_BASE = base
            self.assertTrue(pm._kernel_up())

    def test_false_when_nothing_listens(self):
        pm.KERNEL_BASE = "http://127.0.0.1:%d" % _dead_port()
        self.assertFalse(pm._kernel_up())

    def test_false_under_the_no_kernel_test_seam(self):
        # ROMP_SESSIONS_FILE means "a test with no live kernel" (_kernel_post's seam) — the gate
        # must read the same way, so the bats autostop tests stay hermetic on a dev machine whose
        # REAL kernel is up on the default port.
        with _HealthzStub() as base:
            pm.KERNEL_BASE = base
            os.environ["ROMP_SESSIONS_FILE"] = "/nonexistent"
            self.assertFalse(pm._kernel_up())


ALPHA = "11111111-2222-3333-4444-555555555555"
GAMMA = "99999999-8888-7777-6666-555555555555"


def _forget_presence():
    """A bus that has never seen an answered listing: no in-memory rows, no disk twin."""
    pm._LOCAL_PRESENCE_GOOD[0], pm._LOCAL_PRESENCE_GOOD[1] = [], False
    pm._PRESENCE_GOOD_FILE.unlink(missing_ok=True)


class IdleGate(unittest.TestCase):
    def setUp(self):
        self._ku = pm._kernel_up
        pm.STATE.mkdir(parents=True, exist_ok=True)
        _forget_presence()

    def tearDown(self):
        pm._kernel_up = self._ku
        _forget_presence()

    def test_local_clients_keep_the_bus_no_kernel_probe_needed(self):
        pm._kernel_up = lambda: (_ for _ in ()).throw(AssertionError("must not probe with clients present"))
        self.assertEqual(pm._idle_tick(3, 1), (0, False))

    def test_a_live_kernel_keeps_a_sessionless_bus(self):
        pm._kernel_up = lambda: True
        idle, stop = pm._idle_tick(0, 0)
        self.assertEqual((idle, stop), (0, False), "a quiet hub serves inbound peers; it must not stop")
        # even a count already at the brink resets — the kernel IS the client
        self.assertEqual(pm._idle_tick(0, pm.IDLE_GRACE - 1), (0, False))

    def test_no_kernel_and_no_clients_still_self_stops(self):
        pm._kernel_up = lambda: False
        idle = 0
        for _ in range(pm.IDLE_GRACE - 1):
            idle, stop = pm._idle_tick(0, idle)
            self.assertFalse(stop)
        idle, stop = pm._idle_tick(0, idle)
        self.assertTrue(stop, "a machine with no kernel and no sessions keeps the old autostop")

    def test_never_answered_keeps_the_autostop(self):
        # a kernel-less bus (a CLI `romp mail` spawned it; the kernel never came): an unanswered listing
        # with no evidence that sessions ever existed is an absence, not an outage — it stops as on main,
        # so a later kernel does not adopt a stale process with the old token and state root
        pm._kernel_up = lambda: False
        idle, stop = 0, False
        for _ in range(pm.IDLE_GRACE):
            self.assertFalse(stop)
            idle, stop = pm._idle_tick(0, idle, answered=False)
        self.assertTrue(stop)

    def test_an_outage_after_listed_sessions_holds_the_count(self):
        # the last answered listing held sessions; the kernel then stops answering (mid-restart): the
        # count holds where it was, for as long as the outage lasts — those sessions outlive the kernel
        pm._kernel_up = lambda: False
        pm._remember_presence([{"id": ALPHA, "name": "web"}])
        for start in (0, pm.IDLE_GRACE - 1):
            idle = start
            for _ in range(10):
                idle, stop = pm._idle_tick(0, idle, answered=False)
                self.assertEqual((idle, stop), (start, False))

    def test_a_fresh_bus_primes_the_evidence_from_the_twin(self):
        # a bus restarted during the kernel's blink has empty memory; the disk twin the last process
        # wrote carries the evidence across (the review find of 2026-09-01 made the twin for this)
        pm._kernel_up = lambda: False
        pm._PRESENCE_GOOD_FILE.write_text(json.dumps([{"id": ALPHA, "name": "web"}]))
        self.assertEqual(pm._idle_tick(0, 0, answered=False), (0, False))
        self.assertTrue(pm._LOCAL_PRESENCE_GOOD[1], "primed")

    def test_an_empty_twin_is_no_evidence(self):
        pm._kernel_up = lambda: False
        pm._PRESENCE_GOOD_FILE.write_text("[]")
        idle, stop = 0, False
        for _ in range(pm.IDLE_GRACE):
            idle, stop = pm._idle_tick(0, idle, answered=False)
        self.assertTrue(stop, "the last answered listing was empty: nothing to protect")

    def test_the_hold_releases_only_through_an_answered_listing(self):
        # the hold's one release is the next answered listing, which rewrites the twin: a kernel gone for
        # good after listing sessions leaves the bus up for as long as nothing answers, a code-staleness
        # re-exec re-primes the hold from the twin (a fresh process: empty memory, the twin still on disk),
        # and the first answered listing (here: nobody left) is what lets the autostop resume
        # (review find, 2026-09-08: the re-exec was described as ending the hold; it does not)
        pm._kernel_up = lambda: False
        pm._remember_presence([{"id": ALPHA, "name": "web"}])
        idle = 0
        for _ in range(pm.IDLE_GRACE * 3):
            idle, stop = pm._idle_tick(0, idle, answered=False)
            self.assertEqual((idle, stop), (0, False), "no number of unanswered polls ends the hold")
        pm._LOCAL_PRESENCE_GOOD[0], pm._LOCAL_PRESENCE_GOOD[1] = [], False   # the re-exec: fresh memory, same twin
        for _ in range(pm.IDLE_GRACE * 3):
            idle, stop = pm._idle_tick(0, idle, answered=False)
            self.assertEqual((idle, stop), (0, False), "the re-exec'd bus primes the hold back from the twin")
        pm._remember_presence([])                    # the kernel returns, lists nobody, and is gone again
        for _ in range(pm.IDLE_GRACE - 1):
            idle, stop = pm._idle_tick(0, idle, answered=False)
            self.assertFalse(stop)
        idle, stop = pm._idle_tick(0, idle, answered=False)
        self.assertTrue(stop, "with the twin rewritten empty the autostop resumes")

    def test_an_answered_empty_listing_advances_the_count(self):
        # reachable only through the ROMP_SESSIONS_FILE seam in practice (an answered listing implies
        # a live kernel, which resets the count first); pinned so the seam-driven bats autostop holds
        pm._kernel_up = lambda: False
        idle, stop = 0, False
        for _ in range(pm.IDLE_GRACE):
            idle, stop = pm._idle_tick(0, idle, answered=True)
        self.assertTrue(stop)


class MonitorTick(unittest.TestCase):
    """One _monitor poll, end to end: an answered listing is remembered as evidence, an unanswered one
    holds the count only on that evidence, and the deadness mirror (STATE/remote-sids) is rewritten
    every poll so an expired heartbeat leaves it within one tick (2026-09-06)."""

    def setUp(self):
        self._saved = (pm._kernel_sessions_checked, pm._kernel_up, pm._sweep_orphans, pm._warn_stuck_mail)
        pm._kernel_up = lambda: False
        pm._sweep_orphans = pm._warn_stuck_mail = lambda: None
        pm.HEARTBEATS.clear()
        pm.STATE.mkdir(parents=True, exist_ok=True)
        _forget_presence()

    def tearDown(self):
        pm._kernel_sessions_checked, pm._kernel_up, pm._sweep_orphans, pm._warn_stuck_mail = self._saved
        pm.HEARTBEATS.clear()
        _forget_presence()

    def test_a_never_answered_bus_stops_at_the_grace(self):
        pm._kernel_sessions_checked = lambda threads=False: ([], False)
        idle, stop = 0, False
        for _ in range(pm.IDLE_GRACE):
            self.assertFalse(stop)
            idle, stop = pm._monitor_tick(idle)
        self.assertTrue(stop, "no kernel ever answered: main's autostop")

    def test_an_outage_after_sessions_never_reaches_stop(self):
        listing = [([{"id": ALPHA, "name": "web"}], True)]
        pm._kernel_sessions_checked = lambda threads=False: listing[0]
        self.assertEqual(pm._monitor_tick(0), (0, False))                # sessions listed: the evidence
        self.assertEqual(json.loads(pm._PRESENCE_GOOD_FILE.read_text())[0]["id"], ALPHA,
                         "the evidence reached the disk twin for a bus restarted during the outage")
        listing[0] = ([], False)                                          # the kernel stops answering
        idle = 0
        for _ in range(pm.IDLE_GRACE * 5):
            idle, stop = pm._monitor_tick(idle)
            self.assertEqual((idle, stop), (0, False))

    def test_an_answered_empty_listing_clears_the_evidence(self):
        listing = [([{"id": ALPHA, "name": "web"}], True)]
        pm._kernel_sessions_checked = lambda threads=False: listing[0]
        pm._monitor_tick(0)
        listing[0] = ([], True)                                           # the sessions are gone, says the kernel
        idle, stop = pm._monitor_tick(0)
        self.assertEqual((idle, stop), (1, False))
        listing[0] = ([], False)                                          # and then the kernel goes too
        for _ in range(pm.IDLE_GRACE):
            idle, stop = pm._monitor_tick(idle)
        self.assertTrue(stop, "no sessions were listed when the kernel last answered: stop")

    def test_a_beat_within_ttl_keeps_the_bus_even_unanswered(self):
        pm._kernel_sessions_checked = lambda threads=False: ([], False)
        pm.HEARTBEATS[GAMMA] = ("gamma", pm.time.time())
        self.assertEqual(pm._monitor_tick(pm.IDLE_GRACE - 1), (0, False))

    def test_an_expired_heartbeat_leaves_the_mirror_after_one_tick(self):
        pm._kernel_sessions_checked = lambda threads=False: ([], True)
        old, fresh = GAMMA, "99999999-8888-7777-6666-555555555556"
        now = pm.time.time()
        pm.HEARTBEATS[old] = ("gamma", now - pm.HEARTBEAT_TTL - 1)
        pm.HEARTBEATS[fresh] = ("delta", now)
        (pm.STATE / "remote-sids").write_text(old + "\n" + fresh + "\n")   # what the last beat wrote
        pm._monitor_tick(0)
        self.assertEqual((pm.STATE / "remote-sids").read_text(), fresh + "\n",
                         "the expired sid is pruned by the poll's write, not by the next beat")


class MonitorLoop(unittest.TestCase):
    """_monitor, the production poll loop, runs _monitor_tick, so the tick's outage hold reaches the loop
    that shuts the server down: a kernel blink after listed sessions no longer stops the bus, while a
    bus no kernel ever answered still stops at the grace. The sleep is replaced through the module's
    OWN `time` binding (a proxy over the real module), never process-wide, and it is the test's hand on
    the loop: it counts the polls, flips the listing, and ends a loop that must not end by itself."""

    class _Clock:
        def __init__(self, on_sleep):
            self._on_sleep = on_sleep

        def __getattr__(self, name):
            return getattr(time, name)

        def sleep(self, secs):
            self._on_sleep(secs)

    class _EnoughPolls(Exception):
        pass

    def setUp(self):
        self._saved = (pm.time, pm._kernel_sessions_checked, pm._kernel_up, pm._sweep_orphans,
                       pm._warn_stuck_mail, pm._maybe_restart_for_code)
        pm._kernel_up = lambda: False
        pm._sweep_orphans = pm._warn_stuck_mail = lambda: None
        pm._maybe_restart_for_code = lambda boot_fp: False
        pm.HEARTBEATS.clear()
        pm.STATE.mkdir(parents=True, exist_ok=True)
        _forget_presence()
        self.polls = []
        self.stopped = threading.Event()
        self.httpd = types.SimpleNamespace(shutdown=self.stopped.set)

    def tearDown(self):
        (pm.time, pm._kernel_sessions_checked, pm._kernel_up, pm._sweep_orphans,
         pm._warn_stuck_mail, pm._maybe_restart_for_code) = self._saved
        pm.HEARTBEATS.clear()
        _forget_presence()

    def test_the_loop_holds_through_an_outage_after_listed_sessions(self):
        # the first poll's listing holds a session; from the second poll on the kernel does not answer
        # (mid-restart). The loop keeps polling for as long as the outage lasts, so the test ends it.
        listing = [([{"id": ALPHA, "name": "web"}], True)]
        pm._kernel_sessions_checked = lambda threads=False: listing[0]

        def on_sleep(secs):
            self.polls.append(secs)
            if len(self.polls) > pm.IDLE_GRACE * 5:
                raise self._EnoughPolls()
            if len(self.polls) > 1:
                listing[0] = ([], False)
        pm.time = self._Clock(on_sleep)
        with self.assertRaises(self._EnoughPolls):
            pm._monitor(self.httpd)
        self.assertFalse(self.stopped.is_set(), "the sessions the kernel listed outlive its blink: no shutdown")
        self.assertEqual(self.polls, [pm.POLL] * (pm.IDLE_GRACE * 5 + 1), "one production-cadence sleep per poll")

    def test_a_never_answered_bus_stops_and_shuts_the_server_down(self):
        # the exit path: no kernel ever answered, no clients; the loop returns at the grace and the
        # shutdown reaches the server
        pm._kernel_sessions_checked = lambda threads=False: ([], False)
        pm.time = self._Clock(self.polls.append)
        pm._monitor(self.httpd)
        self.assertTrue(self.stopped.wait(5), "the loop's shutdown thread reached the server")
        self.assertEqual(self.polls, [pm.POLL] * pm.IDLE_GRACE)


class RefusedNotifyRevives(unittest.TestCase):
    def setUp(self):
        # a kernel that OWNS a bus: the run's floor sets ROMP_POSTAL_CLIENT_ONLY=1 (2026-09-18, so no in-process kernel starts a bus
        # by an ensure or a revive) and the revive skips under it; these pins are the contract of a kernel that owns one, so the
        # flag goes for the class and comes back in tearDown after the revive thread is waited out (the ensure is a stub here)
        self._client_only = os.environ.pop("ROMP_POSTAL_CLIENT_ONLY", None)
        self._bp = km.BUS_PORT
        self._ens = km._ensure_postal_bus
        km.BUS_PORT = _dead_port()
        self.kicked = threading.Event()
        km._ensure_postal_bus = self.kicked.set
        km._bus_reviving[0] = False

    def tearDown(self):
        for _ in range(200):                      # the revive thread finishes before the world goes back
            if not km._bus_reviving[0]:
                break
            time.sleep(0.01)
        km.BUS_PORT = self._bp
        km._ensure_postal_bus = self._ens
        km._bus_reviving[0] = False
        if self._client_only is not None:
            os.environ["ROMP_POSTAL_CLIENT_ONLY"] = self._client_only

    def test_refused_peer_notify_kicks_one_ensure(self):
        self.assertFalse(km._notify_bus_peer("TESTHOST", 1, True), "the caller still sees the failure")
        self.assertTrue(self.kicked.wait(5), "the refusal must kick a revive")

    def test_refused_origin_trust_notify_kicks_too(self):
        self.assertFalse(km._notify_bus_origin_trust("TESTHOST", "directed"))
        self.assertTrue(self.kicked.wait(5))

    def test_revive_is_single_flight(self):
        km._bus_reviving[0] = True            # an ensure is (pretend) already in flight
        km._revive_postal_bus()               # a second kick must coalesce, not stack
        self.assertFalse(self.kicked.wait(0.3), "no second ensure while one is in flight")
        km._bus_reviving[0] = False
        km._revive_postal_bus()
        self.assertTrue(self.kicked.wait(5), "released → the next kick runs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
