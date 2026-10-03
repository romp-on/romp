#!/usr/bin/env python3
"""The bars frame never silently starves (the user 2026-08-18: working sessions, lanes, no bars).

Two mechanisms produced that symptom, both fixed and pinned here:
  - _run_judging re-read and re-parsed the WHOLE judge-usage.jsonl on every bars build (measured
    live: 59.5MB / 222,947 rows ≈ 1s per build during a captioner storm) and shipped every
    horizon-passing row (147,803 → ~35MB) on EVERY {type:"bars"} frame — frames arrived minutes
    late or killed the socket while the cheap lane skeleton kept painting. It now consumes the
    SHARED incremental reader (_judge_usage_rows, built for the 2026-08-13 analytics freeze) and
    bounds the wire at _JUDGING_ROW_CAP newest marks, logging what a trim dropped (never silent).
  - with_bars-only stages ran unguarded inside a try whose handler aborted the WHOLE frame after
    the skeleton had already shipped, and the per-lane parse swallowed exceptions silently — a
    working session rendered bar-less forever with no trace. Every stage now degrades ALONE and
    says so (_bars_complain, one line per distinct cause — the fail-loudly rule).
Synthetic rows only (placeholder ids)."""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_barsres", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_barsres", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000.0


def _rows(n, t0):
    return [{"t": int(t0 + i), "judge": "captioner", "fsid": SID,
             "sent": t0 + i, "recv": t0 + i + 0.5, "ms": 500, "in": 10, "out": 5}
            for i in range(n)]


class RunJudgingFeed(unittest.TestCase):
    def setUp(self):
        self.saved = (km._judge_usage_rows, km.jd.active_runs)
        km.jd.active_runs = lambda: []

    def tearDown(self):
        km._judge_usage_rows, km.jd.active_runs = self.saved

    def test_consumes_the_shared_incremental_reader(self):
        km._judge_usage_rows = lambda: _rows(3, NOW - 100)
        out = km._run_judging(NOW - 3600, {SID}, [])
        self.assertEqual(len(out), 3)
        self.assertTrue(all(m["sid"] == SID and m["judge"] == "captioner" for m in out))
        # and at source: no per-build full read of the log file remains
        src = inspect.getsource(km._run_judging)
        self.assertIn("_judge_usage_rows()", src)
        self.assertNotIn(".read_text(", src, "the per-build full read is gone")

    def test_the_wire_is_bounded_and_the_trim_says_so(self):
        n = km._JUDGING_ROW_CAP + 50
        km._judge_usage_rows = lambda: _rows(n, NOW - n - 10)
        km._JUDGING_TRIMMED.clear()
        out = km._run_judging(NOW - (n + 3600), {SID}, [])
        self.assertEqual(len(out), km._JUDGING_ROW_CAP, "the frame carries at most the cap")
        starts = [m["t"] for m in out]
        self.assertEqual(max(starts), NOW - 11, "the NEWEST marks survive")
        self.assertLess(n - km._JUDGING_ROW_CAP - 1 + (NOW - n - 10), min(starts),
                        "the oldest were the ones trimmed")
        self.assertIn("mag", km._JUDGING_TRIMMED, "the trim logged its transition (never a silent cap)")


class FrameNeverSilentlyDies(unittest.TestCase):
    def test_every_bars_stage_degrades_alone_and_loudly(self):
        # the per-lane seams and marks stages live in _lane_segments (the lane memo's derivation); the parse, the
        # live merge, the store read and the global stages stay in build_timeline
        src = inspect.getsource(km.build_timeline) + inspect.getsource(km._lane_segments)
        # the per-lane parse swallow SAYS why a lane has no bars
        self.assertIn('_bars_complain(sid, "parse", e)', src)
        self.assertIn('_bars_complain(sid, "live-merge", e)', src)
        # seam refinement failing costs the seams, never the lane's bars
        self.assertIn('_bars_complain(sid, "seams", e)', src)
        self.assertIn('_bars_complain(sid, "goals", gfault)', src)   # an unreadable goal store: lane without goal data
        self.assertIn('_bars_complain(sid, "judging-marks", e)', src)
        # the global stages are guarded ALONE — one malformed row costs that band, never the frame
        self.assertIn('_bars_complain("*", "messages", e)', src)
        self.assertIn('_bars_complain("*", "judging", e)', src)

    def test_complain_is_once_per_distinct_cause(self):
        km._BARS_COMPLAINED.clear()
        km._bars_complain(SID, "parse", ValueError("boom"))
        km._bars_complain(SID, "parse", ValueError("boom"))
        self.assertEqual(len(km._BARS_COMPLAINED), 1)
        km._bars_complain(SID, "parse", ValueError("other"))
        self.assertEqual(km._BARS_COMPLAINED[(SID, "parse")], "ValueError: other",
                         "a NEW cause logs again — silence is only for repeats")


class UsageLogPrune(unittest.TestCase):
    def test_outgrown_log_prunes_to_the_reader_window(self):
        with tempfile.TemporaryDirectory() as td:
            saved_usage, saved_cap = jd.USAGE, jd._USAGE_PRUNE_BYTES
            try:
                jd.USAGE = Path(td) / "judge-usage.jsonl"
                old_t, new_t = 1700000000, 1781100000
                rows = [json.dumps({"t": old_t + i, "judge": "captioner"}) for i in range(50)]
                rows += [json.dumps({"t": new_t + i, "judge": "planner"}) for i in range(5)]
                jd.USAGE.write_text("\n".join(rows) + "\n")
                jd._USAGE_PRUNE_BYTES = 10                     # force the trigger
                jd._prune_usage_log()
                kept = [json.loads(l) for l in jd.USAGE.read_text().splitlines()]
                self.assertEqual(len(kept), 5, "only rows within the newest 31 days survive")
                self.assertTrue(all(r["t"] >= new_t for r in kept))
            finally:
                jd.USAGE, jd._USAGE_PRUNE_BYTES = saved_usage, saved_cap

    def test_a_prune_that_keeps_more_than_the_floor_does_not_rewrite_on_every_call(self):
        # 2026-09-29: the retained month weighed ~134MB against the fixed 128MiB trigger, so every judge call (11-16 a
        # minute) found the file over the line, rewrote all of it inside the kernel (~2.7s of CPU) and landed back
        # over the line. The trigger now follows what the last prune kept: half again past it, and never under the
        # floor. The floor is forced low here, so every row is inside the 31 days and the whole file is "kept".
        # Rewrites are counted at os.replace onto the log (an inode check is fooled by the filesystem reusing them).
        import time as _time
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            saved_usage, saved_cap = jd.USAGE, jd._USAGE_PRUNE_BYTES
            real_replace, rewrites = os.replace, []

            def replace(src, dst, *a, **k):
                if Path(dst) == jd.USAGE:
                    rewrites.append(1)
                return real_replace(src, dst, *a, **k)
            try:
                jd.USAGE = Path(td) / "judge-usage.jsonl"
                now = int(_time.time())
                jd.USAGE.write_text("".join(json.dumps({"t": now - 60 * i, "judge": "captioner", "pad": "x" * 300})
                                            + "\n" for i in range(200)))   # ~70KB: the 22 calls below add ~10%
                jd._USAGE_PRUNE_BYTES = 10                     # force the trigger
                call = lambda: jd._log_judge_usage("captioner", "fast", "test-model", None, {"usage": {}})
                with mock.patch.object(jd.os, "replace", replace):
                    call()                                     # over the floor, nothing written down yet: one prune
                    self.assertEqual(len(rewrites), 1)
                    kept = jd.USAGE.stat().st_size
                    for _ in range(20):
                        call()
                    self.assertEqual(len(rewrites), 1,
                                     "no rewrite on the calls after a prune: before the fix every call rewrote the "
                                     "file, since what the prune kept still stood over the fixed trigger")
                    self.assertLessEqual(int((Path(td) / "judge-usage.jsonl.kept").read_text()), kept,
                                         "the prune writes down what it kept, beside the log")
                    getattr(jd, "_USAGE_KEPT", {}).pop(str(jd.USAGE), None)   # a fresh process (a kernel restart)
                    call()
                    self.assertEqual(len(rewrites), 1, "the kept size outlives the process that pruned")
                    with open(jd.USAGE, "a") as f:             # grows half again past what the prune kept
                        f.write("".join(json.dumps({"t": now, "judge": "planner", "pad": "x" * 64}) + "\n"
                                        for _ in range(kept // 80)))
                    call()
                    self.assertEqual(len(rewrites), 2, "past half again, it prunes once more")
            finally:
                getattr(jd, "_USAGE_KEPT", {}).pop(str(jd.USAGE), None)
                jd.USAGE, jd._USAGE_PRUNE_BYTES = saved_usage, saved_cap

    def test_a_prune_whose_write_fails_backs_off_and_says_so_once(self):
        # review of round two: when the prune's rewrite failed (disk full, quota, a read-only mount) the broad except
        # recorded nothing, so every judge call read and parsed the whole file again only to fail again: the storm the
        # kept size ended came back. Now the failure records a back-off (the next attempt waits for the file to grow a
        # tenth), removes its half-written copy, and says so once. Whole-file reads are counted at Path.read_text.
        import contextlib
        import errno
        import io
        import time as _time
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            saved_usage, saved_cap = jd.USAGE, jd._USAGE_PRUNE_BYTES
            real_replace, real_read, reads = os.replace, Path.read_text, []
            full = [True]                                  # the disk is full until the test frees it

            def replace(src, dst, *a, **k):
                if Path(dst) == jd.USAGE and full[0]:
                    raise OSError(errno.ENOSPC, "No space left on device")
                return real_replace(src, dst, *a, **k)

            def read_text(self, *a, **k):
                if self == jd.USAGE:
                    reads.append(1)
                return real_read(self, *a, **k)
            try:
                jd.USAGE = Path(td) / "judge-usage.jsonl"
                now = int(_time.time())
                jd.USAGE.write_text("".join(json.dumps({"t": now - 60 * i, "judge": "captioner", "pad": "x" * 300})
                                            + "\n" for i in range(400)))   # ~140KB: the 20 calls below add ~4%
                jd._USAGE_PRUNE_BYTES = 10                     # force the trigger
                call = lambda: jd._log_judge_usage("captioner", "fast", "test-model", None, {"usage": {}})
                err = io.StringIO()
                with mock.patch.object(jd.os, "replace", replace), mock.patch.object(Path, "read_text", read_text), \
                        contextlib.redirect_stderr(err):
                    for _ in range(20):
                        call()
                    self.assertEqual(len(reads), 1,
                                     "one failed attempt, then a back-off: before the fix every call re-read the file")
                    said = [ln for ln in err.getvalue().splitlines() if "could not be pruned" in ln]
                    self.assertEqual(len(said), 1, "said once: %r" % (err.getvalue(),))
                    self.assertIn("No space left on device", said[0], "the line names the cause")
                    self.assertFalse((Path(td) / "judge-usage.jsonl.tmp").exists(),
                                     "the half-written copy does not stay behind to fill the disk further")
                    with open(jd.USAGE, "a") as f:             # grows a tenth past the failed attempt
                        f.write("".join(json.dumps({"t": now, "judge": "planner", "pad": "x" * 64}) + "\n"
                                        for _ in range(jd.USAGE.stat().st_size // 700)))
                    call()
                    self.assertEqual(len(reads), 2, "past the back-off it tries once more")
                    self.assertEqual(len([ln for ln in err.getvalue().splitlines() if "could not be pruned" in ln]), 1,
                                     "a second failure in the same spell is not said again")
                    with open(jd.USAGE, "a") as f:
                        f.write("".join(json.dumps({"t": now, "judge": "planner", "pad": "x" * 64}) + "\n"
                                        for _ in range(jd.USAGE.stat().st_size // 700)))
                    full[0] = False                            # the disk frees
                    call()
                    self.assertEqual(len(reads), 3)
                    self.assertTrue((Path(td) / "judge-usage.jsonl.kept").is_file(), "it pruned, and wrote it down")
                    self.assertIn("pruned to the newest 31 days", err.getvalue())
            finally:
                getattr(jd, "_USAGE_KEPT", {}).pop(str(jd.USAGE), None)
                getattr(jd, "_USAGE_RETRY_AT", {}).pop(str(jd.USAGE), None)
                getattr(jd, "_USAGE_FAIL_SAID", set()).discard(str(jd.USAGE))
                jd.USAGE, jd._USAGE_PRUNE_BYTES = saved_usage, saved_cap

    def test_healthy_log_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            saved_usage = jd.USAGE
            try:
                jd.USAGE = Path(td) / "judge-usage.jsonl"
                jd.USAGE.write_text('{"t": 1781100000}\n')
                jd._prune_usage_log()                          # under the real 48MB cap → no-op
                self.assertEqual(jd.USAGE.read_text(), '{"t": 1781100000}\n')
            finally:
                jd.USAGE = saved_usage


if __name__ == "__main__":
    unittest.main()
