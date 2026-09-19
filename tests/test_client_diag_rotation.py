#!/usr/bin/env python3
"""client-diag.jsonl is bounded (2026-09-06): the pane bundles post a performance row per pane per active minute
(ui/webview/perf-telemetry.ts), about 1 KB each, into a file that used to hold rare breadcrumbs and that nothing
pruned. The kernel's clientDiag handler now rotates it past CLIENT_DIAG_MAX_BYTES: the file is renamed to
client-diag.jsonl.1 (replacing the previous .1) and a new file starts, so at most two files are ever kept, and
`romp perf client` reads both.

Drives the REAL Handler's WS dispatch (_dispatch_ws with a clientDiag message, the way the pane shim delivers
one) against a hermetic state directory, with the cap lowered for the test. Synthetic fixtures only: a
placeholder dashboard id, invented numbers."""
import contextlib
import io
import json
import os
import pathlib
import tempfile
import threading
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_cdiag", os.path.join(BIN, "romp-kernel"))

WID = "11111111-2222-3333-4444-555555555555"


class ClientDiagRotationTest(unittest.TestCase):
    def setUp(self):
        # A private state root (T282): km.jd is the judge module every test module shares, and its STATE is whatever
        # the last module left, once a directory another module had already removed; the writer under test then
        # had nowhere to write and these tests read a file that was never made.
        self._saved_state = km.jd.STATE
        self._td = tempfile.TemporaryDirectory()
        km.jd._rebind_state(pathlib.Path(self._td.name))
        self.fp = km.jd.STATE / "client-diag.jsonl"
        self.fp1 = km.jd.STATE / "client-diag.jsonl.1"
        for f in (self.fp, self.fp1):
            if f.exists():
                f.unlink()
        self.cap = km.CLIENT_DIAG_MAX_BYTES
        km.CLIENT_DIAG_MAX_BYTES = 600           # a handful of rows; the production value is asserted below
        km._client_diag_rotate_failed = False    # the once-per-kernel stderr latch: each test is its own kernel

    def tearDown(self):
        km.CLIENT_DIAG_MAX_BYTES = self.cap
        for f in (self.fp, self.fp1):
            if f.exists():
                f.unlink()
        km.jd._rebind_state(self._saved_state)
        self._td.cleanup()

    def post(self, i):
        """One breadcrumb through the real dispatch: what the shim sends for a pane's minute row."""
        km.Handler._dispatch_ws(None, {"type": "clientDiag", "surface": "perf", "what": "minute",
                                       "data": {"app": "feed", "i": i, "frames": {"feed": {"n": i}}}},
                                {"wid": WID})

    @staticmethod
    def rows(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_a_row_lands_with_the_kernel_stamp(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.post(1)
        self.assertEqual(err.getvalue(), "", "a fresh state directory has no file yet: that is not a refused rotation")
        rows = self.rows(self.fp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(sorted(rows[0]), ["data", "reconnect", "surface", "t", "what", "wid"])
        self.assertEqual(rows[0]["wid"], WID)
        self.assertEqual(rows[0]["surface"], "perf")
        self.assertEqual(rows[0]["what"], "minute")
        self.assertEqual(rows[0]["data"]["i"], 1)
        self.assertFalse(self.fp1.exists())

    def test_past_the_cap_the_file_rotates_to_dot_one_and_a_second_rotation_replaces_it(self):
        i = 0
        while not self.fp1.exists():
            i += 1
            self.post(i)
            self.assertLess(i, 100, "the cap never tripped")
        first = self.rows(self.fp1)
        self.assertGreaterEqual(sum(len(json.dumps(r)) + 1 for r in first), km.CLIENT_DIAG_MAX_BYTES,
                                "the rename happens only once the file is at the cap")
        self.assertEqual([r["data"]["i"] for r in first], list(range(1, i)), "the older rows moved whole")
        self.assertEqual([r["data"]["i"] for r in self.rows(self.fp)], [i], "the row that tripped it starts the new file")
        # keep going: the second rotation REPLACES .1 rather than stacking a .2
        n1 = i
        while self.rows(self.fp1)[0]["data"]["i"] == 1:
            i += 1
            self.post(i)
            self.assertLess(i, 200, "the second rotation never happened")
        second = self.rows(self.fp1)
        self.assertEqual(second[0]["data"]["i"], n1, "the .1 file is now the second run")
        self.assertEqual(self.rows(self.fp)[0]["data"]["i"], i)
        self.assertFalse((km.jd.STATE / "client-diag.jsonl.2").exists())
        # nothing is lost between the two files at any moment: every row is in exactly one
        seen = [r["data"]["i"] for r in second] + [r["data"]["i"] for r in self.rows(self.fp)]
        self.assertEqual(seen, list(range(n1, i + 1)))

    def test_the_production_cap_is_eight_megabytes(self):
        self.assertEqual(self.cap, 8 * 1024 * 1024)

    def test_a_rename_failure_still_appends_and_is_said_once_on_stderr(self):
        """A refused rotation (an unwritable state directory, a directory sitting at the .1 name) used to share one
        silent `except OSError` with the absent-file case, so the size bound could fail with nothing saying so.
        Now the rows still append, and stderr gets ONE line naming the file and the error, latched for the
        kernel's lifetime: a rename that stays refused is a line per kernel, not a line per row (a minute per
        pane)."""
        self.post(1)
        real = os.replace
        km.CLIENT_DIAG_MAX_BYTES = 1

        def refuse(*a, **k):
            raise OSError("read-only")
        err = io.StringIO()
        os.replace = refuse
        try:
            with contextlib.redirect_stderr(err):
                self.post(2)
                self.post(3)
        finally:
            os.replace = real
        self.assertEqual([r["data"]["i"] for r in self.rows(self.fp)], [1, 2, 3], "both rows past the cap still appended")
        self.assertFalse(self.fp1.exists())
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "two refused renames, one stderr line: %r" % lines)
        self.assertIn(str(self.fp), lines[0], "the line names the file")
        self.assertIn("read-only", lines[0], "the line carries the error")
        self.assertTrue(km._client_diag_rotate_failed)

    def test_concurrent_posts_rename_only_a_file_at_the_cap_and_lose_no_row(self):
        """Every pane's socket is its own handler thread, so rows arrive concurrently. Without one lock across
        the size check, the rename and the append, two threads at the cap at once both renamed: the second
        moved the file the first had just started (a row or two) over the run the first had just rotated, and
        that run was gone. Two properties, checked with os.replace recording what it moved: every rename moves
        a file at the cap, and every row posted ends up in exactly one place (the current file, .1, or a .1 that
        a later rotation replaced)."""
        km.CLIENT_DIAG_MAX_BYTES = 3000
        real_replace = os.replace
        renamed_sizes, discarded, book = [], [], threading.Lock()
        fp, fp1 = self.fp, self.fp1

        def recording_replace(src, dst, *a, **k):
            if src == str(fp):
                size = os.stat(src).st_size
                before = len(self.rows(fp1)) if fp1.exists() else 0
                with book:
                    renamed_sizes.append(size)
                    discarded.append(before)
            return real_replace(src, dst, *a, **k)

        n_threads, per_thread = 6, 1500

        def worker(k):
            for j in range(per_thread):
                self.post(k * per_thread + j)

        os.replace = recording_replace
        try:
            threads = [threading.Thread(target=worker, args=(k,), name="poster-%d" % k) for k in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        finally:
            os.replace = real_replace
        self.assertGreater(len(renamed_sizes), 20, "the cap tripped many times")
        self.assertEqual([s for s in renamed_sizes if s < km.CLIENT_DIAG_MAX_BYTES], [],
                         "a rename of a file below the cap is the race: it moved the just-started file over the rotated run")
        kept = self.rows(fp1) + self.rows(fp)
        self.assertEqual(len(kept) + sum(discarded), n_threads * per_thread, "every row is in exactly one place")
        self.assertGreaterEqual(fp1.stat().st_size, km.CLIENT_DIAG_MAX_BYTES, "the .1 file is a whole rotated run")

    def test_two_threads_at_the_cap_at_once_keep_the_rotated_run(self):
        """The interleaving that lost a run, forced rather than raced: A and B both see the file at the cap, A
        rotates it and starts the new file, and B, whose size check predates A's rename, must not rename again.
        Thread B's stat and thread A's rename are gated on each other with two events, so the order is fixed.
        The waits end early on their event; their bound only keeps the serialized order (B cannot reach its stat
        while A holds the lock, so the event never comes) from hanging. Four rows sit in the file before the cap
        drops to a value one row is under and four rows are over."""
        for i in range(1, 5):
            self.post(i)
        km.CLIENT_DIAG_MAX_BYTES = 300
        b_statted, a_written = threading.Event(), threading.Event()
        real_replace, real_stat = os.replace, pathlib.Path.stat
        fp = self.fp

        def gated_replace(src, dst, *a, **k):
            if src == str(fp) and threading.current_thread().name == "A":
                b_statted.wait(2.0)
            return real_replace(src, dst, *a, **k)

        def gated_stat(self_, *a, **k):
            st = real_stat(self_, *a, **k)
            if self_ == fp and threading.current_thread().name == "B":
                b_statted.set()
                a_written.wait(2.0)
            return st

        def post_a():
            self.post(5)
            a_written.set()

        os.replace, pathlib.Path.stat = gated_replace, gated_stat
        try:
            ta = threading.Thread(target=post_a, name="A")
            tb = threading.Thread(target=self.post, args=(6,), name="B")
            ta.start()
            tb.start()
            ta.join(10)
            tb.join(10)
        finally:
            os.replace, pathlib.Path.stat = real_replace, real_stat
        self.assertEqual([r["data"]["i"] for r in self.rows(self.fp1)], [1, 2, 3, 4], "the rotated run is intact")
        self.assertEqual(sorted(r["data"]["i"] for r in self.rows(self.fp)), [5, 6], "both new rows are in the current file")


if __name__ == "__main__":
    unittest.main()
