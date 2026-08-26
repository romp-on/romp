#!/usr/bin/env python3
"""The standing usage poll (the user 2026-08-23): the login account's rate-limit meters are polled on
an interval, because all-key traffic ends no login turns — the turn-end refresh never fired and the
meters file froze, blinding the judge quota gate and the headroom line. The poll reuses the /usage
click path (refresh_usage: any live login-auth session answers; key-billed sessions are never
candidates, and an empty candidate set already logs loudly). SYNTHETIC fixtures."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class UsagePoll(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._saved = km._sdk
        poked = self.calls

        class BE:
            def refresh_usage(self):
                poked.append(1)
        km._sdk = lambda: BE()
        km._usage_poll_last[0] = 0.0

    def tearDown(self):
        km._sdk = self._saved
        km._usage_poll_last[0] = 0.0

    def test_polls_on_the_interval_and_not_inside_it(self):
        t0 = 1_787_500_000
        km._usage_poll_tick(t0)
        self.assertEqual(len(self.calls), 1, "the first tick past the interval pokes")
        km._usage_poll_tick(t0 + 60)
        self.assertEqual(len(self.calls), 1, "inside the interval: no extra poke")
        km._usage_poll_tick(t0 + km.USAGE_POLL_SECS + 1)
        self.assertEqual(len(self.calls), 2, "the next interval pokes again")

    def test_no_backend_is_a_quiet_no_op(self):
        km._sdk = lambda: None
        km._usage_poll_tick(1_787_500_000)
        self.assertEqual(self.calls, [])

    def test_the_tick_rides_the_push_loop(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("_usage_poll_tick(now)", src)
        self.assertIn("USAGE_POLL_SECS = 900", src)


if __name__ == "__main__":
    unittest.main()
