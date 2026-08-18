#!/usr/bin/env python3
"""The judging band reads the SHARED incremental judge-usage cache, never the raw file (the user
2026-08-18): _run_judging used to read_text + json.loads the whole append-only judge-usage.jsonl on
EVERY bars build, on the GIL — at 60 MB / 225k rows the pusher thread sat pinned parsing it and the
{type:"bars"} frame effectively never shipped, so every lane on that kernel showed skeleton
furniture (state chips, comment marks, awaiting stripes) but NO work bars, while smaller-logged
kernels' lanes rendered fine. The 2026-08-13 sweep that introduced _judge_usage_rows converted the
analytics reader and missed this one. Synthetic rows only."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_jbc", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000.0


def row(sent, judge="captioner", ms=800):
    return {"fsid": SID, "judge": judge, "t": sent + 1, "sent": sent, "recv": sent + 1,
            "ms": ms, "in": 100, "out": 20}


class JudgingBandCache(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        # the cache keys on the path, so the tmp STATE resets it — no manual clearing needed

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def log(self, rows, append=False):
        p = jd.STATE / "judge-usage.jsonl"
        with open(p, "a" if append else "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def test_band_rows_come_from_the_log_within_the_window(self):
        self.log([row(NOW - 100), row(NOW - 40, judge="unblocker")])
        out = km._run_judging(NOW - 7200, {SID}, [])
        self.assertEqual([(m["judge"], m["t"]) for m in out],
                         [("captioner", NOW - 100), ("unblocker", NOW - 40)])

    def test_appended_rows_are_seen_without_a_full_reparse(self):
        self.log([row(NOW - 100)])
        self.assertEqual(len(km._run_judging(NOW - 7200, {SID}, [])), 1)
        size_after_first = km._JUDGE_USAGE_CACHE["size"]
        self.log([row(NOW - 30)], append=True)
        out = km._run_judging(NOW - 7200, {SID}, [])
        self.assertEqual(len(out), 2, "the append is visible on the next build")
        self.assertGreater(km._JUDGE_USAGE_CACHE["size"], size_after_first,
                           "…and was consumed incrementally from the saved byte offset")

    def test_rows_before_the_window_or_for_dead_sids_are_filtered(self):
        self.log([row(NOW - 9000), row(NOW - 50), dict(row(NOW - 60), fsid="someone-else")])
        out = km._run_judging(NOW - 7200, {SID}, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["t"], NOW - 50)

    def test_the_band_never_rereads_the_raw_file(self):
        # the regression itself, pinned at the source: _run_judging consumes the shared cache, and no
        # timeline-path code full-reads judge-usage.jsonl (the cache's open/seek is the ONE reader)
        src = open(os.path.join(BIN, "romp-kernel")).read()
        start = src.index("def _run_judging")
        body = src[start:src.index("\ndef ", start + 1)]   # this function only, not its neighbours
        self.assertIn("for o in _judge_usage_rows():", body)
        self.assertNotIn('read_text', body, "_run_judging never touches the file itself")
        self.assertEqual(src.count('judge-usage.jsonl").read_text'), 0,
                         "no full-file reader of judge-usage.jsonl survives anywhere")
