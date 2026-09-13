#!/usr/bin/env python3
"""The kernel's own size over a life (the performance metrics, 2026-09-11): a pusher tick writes one row in
kernel-samples.jsonl at 5, 30 and 60 minutes of uptime and every hour after, with the resident size, processor
seconds, threads and the record cache's held bytes when the event model reports them. Hermetic: a temp state root."""
import json
import os
import tempfile
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_samples", os.path.join(BIN, "romp-kernel"))


class KernelSamples(unittest.TestCase):
    def setUp(self):
        km._KERNEL_SAMPLES_TAKEN.clear()
        self.f = km.KERNEL_SAMPLES_FILE
        if os.path.exists(self.f):
            os.unlink(self.f)

    def _rows(self):
        return [json.loads(l) for l in open(self.f)] if os.path.exists(self.f) else []

    def test_samples_land_at_the_marks_and_hourly_after_and_never_between(self):
        t0 = km._STARTED
        ticks = [(60, 0), (299, 0), (300, 1), (900, 1), (1799, 1), (1800, 2), (3599, 2), (3600, 3), (5000, 3), (7200, 4), (10800, 5)]
        for up, expected in ticks:
            km._kernel_sample_tick(t0 + up)
            self.assertEqual(len(self._rows()), expected, "at uptime %d s" % up)
        rows = self._rows()
        self.assertEqual([r["markS"] for r in rows], [300.0, 1800.0, 3600.0, 7200.0, 10800.0])
        for r in rows:
            for k in ("t", "pid", "uptimeS", "rssKb", "cpuS", "threads"):
                self.assertIn(k, r)
            self.assertGreater(r["rssKb"], 0)

    def test_the_record_cache_bytes_ride_when_the_event_model_reports_them(self):
        t0 = km._STARTED
        with mock.patch.object(km.em, "record_cache_stats", lambda: {"bytes": 12345, "entries": 7}, create=True):
            km._kernel_sample_tick(t0 + 300)
        self.assertEqual((self._rows()[0]["recordCacheBytes"], self._rows()[0]["recordCacheEntries"]), (12345, 7))

    def test_a_late_kernel_takes_the_marks_it_passed_one_per_tick(self):
        # a tick arriving late (the pusher was busy) takes one mark per tick, so the series keeps every mark
        t0 = km._STARTED
        km._kernel_sample_tick(t0 + 4000); km._kernel_sample_tick(t0 + 4001); km._kernel_sample_tick(t0 + 4002); km._kernel_sample_tick(t0 + 4003)
        self.assertEqual([r["markS"] for r in self._rows()], [300.0, 1800.0, 3600.0])

    def test_the_tick_rides_the_pusher(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("        _job_stage('kernelSample', lambda: _kernel_sample_tick(now))\n", src)   # a tick job, its own stage (T398)
        self.assertLess(src.index("_job_stage('bootRowBackstop', lambda: _boot_row_backstop(now))\n"),
                        src.index("        _job_stage('kernelSample', lambda: _kernel_sample_tick(now))\n"), "beside the boot row's backstop in the tick jobs")


class RestartPhasesScript(unittest.TestCase):
    def test_the_reader_pairs_cuts_with_boots_and_lists_the_samples(self):
        import subprocess, sys as _sys
        d = tempfile.mkdtemp()
        rows = [{"t": 1000, "pid": 1, "cutTurns": ["a"], "stopped": 3, "rssKb": 4096000, "ckptS": 0.4, "drainS": 0.1},
                {"t": 1003, "pid": 2, "bootSettled": True, "firstServe": 1002.5, "reconcileDone": 1002.6, "settleS": 0.1, "prevCutT": 1000, "outageS": 2.5, "censusS": 0.9, "attachS": 1.4},
                {"t": 2000, "pid": 2, "cutTurns": [], "stopped": 5, "rssKb": 2048000},
                {"t": 2004, "pid": 3, "bootSettled": True, "firstServe": 2003.0, "reconcileDone": 2003.1, "settleS": 0.1, "prevCutT": 2000, "outageS": 3.0, "attachTimedOut": True}]
        with open(os.path.join(d, "restart-cuts.jsonl"), "w") as f:
            for r in rows: f.write(json.dumps(r) + "\n")
        with open(os.path.join(d, "kernel-samples.jsonl"), "w") as f:
            f.write(json.dumps({"t": 1302, "pid": 2, "uptimeS": 300, "markS": 300.0, "rssKb": 204800, "recordCacheBytes": 52428800}) + "\n")
        script = os.path.join(os.path.dirname(HERE), "scripts", "perf", "restart-phases.py")
        out = subprocess.run([_sys.executable, script, "--state", d, "--json"], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual([c["outageS"] for c in data["cycles"]], [2.5, 3.0])
        self.assertEqual((data["cycles"][0]["ckptS"], data["cycles"][0]["censusS"], data["cycles"][0]["attachS"]), (0.4, 0.9, 1.4))
        self.assertIsNone(data["cycles"][1]["ckptS"], "an older kernel's row has no phases: a dash, not a crash")
        self.assertTrue(data["cycles"][1]["attachTimedOut"])
        self.assertEqual(data["samples"]["2"][0]["recordCacheBytes"], 52428800)
        text = subprocess.run([_sys.executable, script, "--state", d], capture_output=True, text=True, timeout=30).stdout
        self.assertIn("ckptS", text); self.assertIn("life pid 2", text); self.assertIn("cache 50M", text)


if __name__ == "__main__":
    unittest.main()
