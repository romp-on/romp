#!/usr/bin/env python3
"""The record reader's transient peak (measured 2026-09-15). _read_jsonl_entry pulled a file, or its grown tail, into
one bytes object and handed it to _scan_jsonl_bytes, which copied it up to the last newline and split the copy into a
list of every line before decoding a single record: two copies of the source were live at once beside the records
the read was building (three when a partial line trailed), and the allocator kept the arenas that peak took. In a lab
process over a 37.7 MB transcript the records weighed 107.6 MB, the read peaked at 183.8 MB and left the process
223 MB larger (a 217,900 KiB VmRSS delta); on the live kernel a 40-minute sample stepped the resident size by 5.8 bytes
per source byte the record cache admitted, the retained heap the lag investigation traced, about one of them this
transient. The reader now decodes line by line off the open file (_scan_jsonl_stream), so what is live beyond the
records is bounded by the largest line (with its strip and decode copies) and the file's read buffer, and the read
ends at the size the reader's stat saw, so a writer appending meanwhile cannot extend it. Pinned here:
  (a) the peak of a from-zero read and of a grown-tail read is its records plus a small fraction of the source;
  (b) the streaming scanner answers exactly what the reference scanner answers on every tricky shape of line
      (records, consumed offset, per-record offsets) on every input, malformed included;
  (c) the cache contract end to end: an append with a partial line and its later newline, a same-size rewrite, a
      shrink, a checkpoint's tail entry streamed from its offset and upgraded whole by a whole reader;
  (d) the byte counters keep their meaning (/perf's whole-read bytes, the per-path bytes pulled).
SYNTHETIC fixtures only (placeholder uuids, invented text, hostname TESTHOST).
"""
import array
import gc
import io
import json
from unittest import mock
import os
import random
import shutil
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — the module resolves its state root at import time, and only pytest runs conftest's
# floor (a bare unittest or script run otherwise writes REAL state). Session hosts off in the root this module mints
# (the standing rule for a test that mints one; nothing here connects a session, so it is a belt, not a need).
_STATE = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_STATE, "romp"), exist_ok=True)
with open(os.path.join(_STATE, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off\n")
em = load_source("romp_event_model_stream_peak", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"
WORDS = ("the", "reader", "streams", "each", "line", "notes", "api", "tests", "web", "session", "record", "offset",
         "append", "cache", "budget", "TESTHOST", "ledger", "guard", "tail", "partial", "verify", "fold", "cursor",
         "checkpoint", "restore", "upgrade", "generation", "one", "two", "three")


def _uuid(i):
    return "11111111-2222-3333-4444-%012d" % i


def _big_rec(i, rng):
    """A record the shape of a transcript row: nested message content with a text field whose length varies the way a
    transcript's does (most rows short, a few long), tool blocks on assistant rows, a tool result on some user rows."""
    n = rng.choices((40, 120, 400, 1500, 4000), weights=(30, 30, 25, 10, 5))[0]
    text = " ".join(rng.choice(WORDS) for _ in range(n))
    role = "assistant" if i % 2 else "user"
    content = [{"type": "text", "text": text}]
    if role == "assistant":
        content.append({"type": "tool_use", "id": "toolu_%08d" % i, "name": "Read",
                        "input": {"file_path": "/work/notes-api/src/module_%d.py" % (i % 97), "limit": n}})
    rec = {"type": role, "uuid": _uuid(i), "parentUuid": _uuid(i - 1) if i else None, "sessionId": SID,
           "cwd": "/work/notes-api", "version": "1.0.0", "isSidechain": False, "userType": "external",
           "timestamp": "2026-09-15T%02d:%02d:%02d.000Z" % ((i // 3600) % 24, (i // 60) % 60, i % 60),
           "message": {"role": role, "content": content, "model": "TESTMODEL" if role == "assistant" else None}}
    if role == "user" and i % 4 == 0:
        rec["toolUseResult"] = {"stdout": text[:300], "stderr": "", "interrupted": False, "isImage": False}
    return rec


def _write_big(path, target_bytes, seed=1, start=0):
    """Append records numbered from `start` until the file grew by `target_bytes`; returns how many were written."""
    rng = random.Random(seed)
    n = size = 0
    with open(path, "ab") as f:
        while size < target_bytes:
            line = (json.dumps(_big_rec(start + n, rng)) + "\n").encode("utf-8")
            f.write(line)
            size += len(line)
            n += 1
    return n


def _small(i, text="hello"):
    return {"type": "user", "uuid": _uuid(i), "timestamp": "2026-09-15T10:%02d:00Z" % (i % 60),
            "message": {"role": "user", "content": "%s %d" % (text, i)}}


def _write(path, recs, partial=None):
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        if partial is not None:
            f.write(partial)                    # no trailing newline — a writer caught mid-append


def _append(path, recs, partial=None):
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        if partial is not None:
            f.write(partial)


def _disk_offsets(path):
    """(offset, length) pairs, flattened, of every complete line of the file that parses as JSON, computed from the
    bytes with str.find alone: independent of both scanners, so it can judge them."""
    data = open(path, "rb").read()
    out, pos = [], 0
    while True:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        line = data[pos:nl + 1]
        try:
            json.loads(line.strip().decode("utf-8", "replace"))
            out += [pos, len(line)]
        except Exception:
            pass
        pos = nl + 1
    return out


def _clear_cache():
    with em._JSONL_CACHE_LOCK:
        em._JSONL_CACHE.clear()
        em._JSONL_CACHE_BYTES[0] = 0


class StreamReadPeak(unittest.TestCase):
    """(a) A read's transient peak is its records plus a small fraction of the source, never copies of the whole of it."""
    ALLOWANCE = 0.35    # of the source bytes a read may hold beyond what it keeps: the line in hand (with its strip and
                        # decode copies) and the file's read buffer on the stream; the whole-blob scan held about twice
                        # the source here (2.02x and 2.03x measured on origin/main)

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="reader-peak-")
        cls.path = os.path.join(cls.dir, "leaf.jsonl")
        cls.n = _write_big(cls.path, 10 * 1024 * 1024)
        cls.source = os.path.getsize(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        _clear_cache()

    def tearDown(self):
        _clear_cache()

    def _traced(self, fn):
        """(result, live, peak): tracemalloc's bytes fn() left live and the peak during it, both as deltas from what was
        held when it started. A tracer already running (PYTHONTRACEMALLOC, -X tracemalloc, an earlier test) is used and
        left running: start() is a no-op then and would neither reset the peak nor be ours to stop (the
        tests/test_sdk_backend.py idiom)."""
        gc.collect()
        tracing = tracemalloc.is_tracing()
        if not tracing:
            tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            held = tracemalloc.get_traced_memory()[0]
            out = fn()
            cur, peak = tracemalloc.get_traced_memory()
        finally:
            if not tracing:
                tracemalloc.stop()
        return out, cur - held, peak - held

    @staticmethod
    def _report(source, live, peak):
        return "source=%d live=%d peak=%d live/source=%.2f peak/live=%.3f (peak-live)/source=%.3f" % (
            source, live, peak, live / source, peak / live, (peak - live) / source)

    def test_a_from_zero_read_peaks_at_its_records_plus_one_line(self):
        ent, live, peak = self._traced(lambda: em._read_jsonl_entry(self.path))
        self.assertEqual(len(ent[4]), self.n)
        report = self._report(self.source, live, peak)
        print("from-zero read:", report)
        self.assertLessEqual(peak, live + self.ALLOWANCE * self.source,
                             "a from-zero read held more than its records plus %d%% of the source: %s"
                             % (self.ALLOWANCE * 100, report))

    def test_a_grown_tail_read_peaks_at_its_new_records_plus_one_line(self):
        first = em._read_jsonl_entry(self.path)
        n0, size0 = len(first[4]), os.path.getsize(self.path)
        try:
            added = _write_big(self.path, 2 * 1024 * 1024, seed=2, start=n0)
            appended = os.path.getsize(self.path) - size0
            ent, live, peak = self._traced(lambda: em._read_jsonl_entry(self.path))
            self.assertEqual(len(ent[4]), n0 + added)
            self.assertEqual(ent[6], first[6], "an append keeps the generation")
            report = self._report(appended, live, peak)
            print("grown-tail read:", report)
            self.assertLessEqual(peak, live + self.ALLOWANCE * appended,
                                 "a grown-tail read held more than its new records plus %d%% of the appended bytes: %s"
                                 % (self.ALLOWANCE * 100, report))
        finally:
            with open(self.path, "r+b") as f:
                f.truncate(size0)                 # the file as the class made it, whichever test runs first


class StreamScannerMatchesTheReference(unittest.TestCase):
    """(b) _scan_jsonl_stream over an open file answers what _scan_jsonl_bytes answers over the same bytes: the same
    records, the same consumed offset, the same per-record offsets, for every shape of line the reader meets."""
    CASES = {
        "blank and whitespace-only lines": b'{"a":1}\n\n   \n\t\n{"b":2}\n',
        "a line that is not json": b'{"a":1}\nnot json at all\n{"b":2}\n',
        "escaped control characters inside a string": b'{"t":"one\\r\\ntwo\\u000c\\u001c end"}\n{"b":2}\n',
        "crlf line endings": b'{"a":1}\r\n{"b":2}\r\n',
        "a byte that is not utf-8": b'{"t":"caf\xe9 \xff"}\n{"b":2}\n',
        "a trailing partial line without its newline": b'{"a":1}\n{"b":2}\n{"c":',
        "a partial that ends in a bare return": b'{"a":1}\n{"b":2}\rjunk',
        "only a partial line": b'{"a":1',
        "an empty file": b"",
    }
    EXPECT = {                                                 # what each case parses to: positive evidence the case exercises what it names
        "blank and whitespace-only lines": [{"a": 1}, {"b": 2}],
        "a line that is not json": [{"a": 1}, {"b": 2}],
        "escaped control characters inside a string": [{"t": "one\r\ntwo\x0c\x1c end"}, {"b": 2}],
        "crlf line endings": [{"a": 1}, {"b": 2}],
        "a byte that is not utf-8": [{"t": "caf� �"}, {"b": 2}],
        "a trailing partial line without its newline": [{"a": 1}, {"b": 2}],
        "a partial that ends in a bare return": [{"a": 1}],            # the return and all after the last newline are the partial
        "only a partial line": [],
        "an empty file": [],
    }

    @staticmethod
    def _both(blob, base):
        ref_offs = array.array("q")
        ref_recs, ref_consumed = em._scan_jsonl_bytes(blob, base, ref_offs)
        fh, st_offs = io.BytesIO(blob), array.array("q")
        st_recs, st_consumed, nread = em._scan_jsonl_stream(fh, base, st_offs)
        return (ref_recs, ref_consumed, ref_offs), (st_recs, st_consumed, st_offs), nread, fh.tell()

    def test_every_case_agrees_with_the_reference(self):
        for name, blob in self.CASES.items():
            for base in (0, 4096):
                with self.subTest(case=name, base=base):
                    ref, st, nread, pos = self._both(blob, base)
                    self.assertEqual(ref[0], self.EXPECT[name], "the case parses to what its name says")
                    self.assertEqual(st[0], ref[0], "records")
                    self.assertEqual(st[1], ref[1], "consumed offset")
                    self.assertEqual(st[2], ref[2], "per-record offsets")
                    self.assertEqual(nread, len(blob), "the stream read every byte to the end, the partial included")
                    self.assertEqual(pos, len(blob), "and left the file at its end, where the reader's guard capture seeks from")

    def test_the_partial_is_left_and_the_offsets_carry_the_base(self):
        blob = self.CASES["a trailing partial line without its newline"]
        ref, st, nread, _ = self._both(blob, 1000)
        self.assertEqual(st[1], 1000 + len(b'{"a":1}\n{"b":2}\n'), "consumed stops before the partial")
        self.assertEqual(list(st[2]), [1000, 8, 1008, 8])
        self.assertEqual(nread, len(blob))

    def test_a_bare_return_inside_a_malformed_line_splits_under_both_scanners(self):
        """bytes.splitlines breaks a line at a bare \\r; the stream splits each newline-terminated line with the same call,
        so from `{"a":1}<CR>junk` both recover the object before the \\r, with the same offsets, and both stop consuming at
        the last newline (review find, 2026-09-15: the first cut split at b"\\n" alone and skipped that line)."""
        blob = b'{"a":1}\rjunk\n{"b":2}\n'
        ref, st, nread, _ = self._both(blob, 0)
        self.assertEqual(ref[0], [{"a": 1}, {"b": 2}]); self.assertEqual(list(ref[2]), [0, 8, 13, 8])
        self.assertEqual(st[0], ref[0]); self.assertEqual(list(st[2]), list(ref[2]))
        self.assertEqual(st[1], ref[1]); self.assertEqual(st[1], len(blob)); self.assertEqual(nread, len(blob))
        oracle = b'{"a":1}\r{"b":2}\n'                                 # a peer's oracle: two records, offsets [0, 8, 8, 8]
        ref, st, nread, _ = self._both(oracle, 0)
        self.assertEqual(ref[0], [{"a": 1}, {"b": 2}]); self.assertEqual(list(ref[2]), [0, 8, 8, 8])
        self.assertEqual(st[0], ref[0]); self.assertEqual(list(st[2]), list(ref[2])); self.assertEqual(st[1], ref[1])
        mixed = b'{"a":1}\r{"c":3}\r\n{"b":2}\n{"d":4}\r'      # bare CR, CRLF, and a partial ending in a bare CR
        ref, st, nread, _ = self._both(mixed, 7)
        self.assertEqual(st[0], ref[0]); self.assertEqual(list(st[2]), list(ref[2])); self.assertEqual(st[1], ref[1])
        self.assertEqual(ref[0], [{"a": 1}, {"c": 3}, {"b": 2}], "the partial after the last newline is left by both")

    def test_a_line_past_the_captured_end_is_left_for_the_next_read(self):
        """`limit` is the end the caller captured at its stat: a fast writer appending while the stream decodes cannot
        extend the read (the one read this replaced ended where it ended). Bytes past the limit are pulled at most one
        line's worth and left unconsumed; a line crossing the limit is a partial."""
        blob = b'{"a":1}\n{"b":2}\n{"c":3}\n'
        offs = array.array("q")
        recs, consumed, nread = em._scan_jsonl_stream(io.BytesIO(blob), 0, offs, limit=12)   # the end fell inside {"b":2}\n
        self.assertEqual(recs, [{"a": 1}]); self.assertEqual(consumed, 8); self.assertEqual(list(offs), [0, 8])
        self.assertEqual(nread, 12, "pulled exactly to the captured end, never past it; the crossing line is not consumed")
        recs, consumed, nread = em._scan_jsonl_stream(io.BytesIO(blob), 0, None, limit=16)   # the end at a line boundary
        self.assertEqual((len(recs), consumed, nread), (2, 16, 16), "two whole lines within the extent are both consumed")
        growing = io.BytesIO(b'{"x":"' + b"y" * 100 + b'"')                                    # a long line with no newline yet
        recs, consumed, nread = em._scan_jsonl_stream(growing, 0, None, limit=10)
        self.assertEqual((recs, consumed, nread), ([], 0, 10), "a line without a newline is read at most to the captured end")
        recs, consumed, nread = em._scan_jsonl_stream(io.BytesIO(blob), 0, None, limit=len(blob))
        self.assertEqual((len(recs), consumed), (3, len(blob)), "a limit at the end changes nothing")
        recs, consumed, nread = em._scan_jsonl_stream(io.BytesIO(blob), 0, None, limit=None)
        self.assertEqual((len(recs), consumed), (3, len(blob)), "no limit: the stream reads to EOF as before")

    def test_a_form_feed_splits_a_line_under_neither_scanner(self):
        """\\x0c (and \\x0b, \\x1c-\\x1e) are str.splitlines' boundaries, not bytes.splitlines': a raw form feed inside a
        malformed line leaves both scanners on the same line, which neither parses; inside a string it is not JSON to
        either. Pinned so the docstring's claim about where the two agree stays true."""
        for blob in (b'{"x":1}\x0cjunk\n{"b":2}\n', b'{"t":"a\x0cb"}\n{"b":2}\n', b'{"x":1}\x1cjunk\n{"b":2}\n'):
            with self.subTest(blob=blob):
                ref, st, nread, _ = self._both(blob, 0)
                self.assertEqual(ref[0], [{"b": 2}]); self.assertEqual(st[0], ref[0])
                self.assertEqual(st[2], ref[2]); self.assertEqual(st[1], ref[1]); self.assertEqual(st[1], len(blob))


class StreamReadThroughTheCache(unittest.TestCase):
    """(c) The reader's contract, end to end through its cache, with the streaming scan underneath."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reader-stream-")
        self.path = os.path.join(self.dir, "leaf.jsonl")
        _clear_cache()

    def tearDown(self):
        _clear_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_an_append_with_a_partial_line_and_then_its_newline(self):
        _write(self.path, [_small(i) for i in range(5)])
        ent = em._read_jsonl_entry(self.path)
        held, gen = ent[4], ent[6]
        self.assertEqual([r["uuid"] for r in held], [_uuid(i) for i in range(5)])
        self.assertEqual(ent[2], os.path.getsize(self.path)); self.assertEqual(list(ent[7]), _disk_offsets(self.path))
        partial = json.dumps(_small(7)); cut = len(partial) // 2
        _append(self.path, [_small(5), _small(6)], partial=partial[:cut])
        ent2 = em._read_jsonl_entry(self.path)
        self.assertEqual([r["uuid"] for r in ent2[4]], [_uuid(i) for i in range(7)], "the old records plus the two complete new ones")
        self.assertEqual(ent2[6], gen, "an append keeps the generation"); self.assertEqual(ent2[5], 0)
        self.assertEqual(ent2[2], os.path.getsize(self.path) - cut, "the partial line is not consumed")
        self.assertEqual(list(ent2[7]), _disk_offsets(self.path), "the offsets continue from the first read's")
        self.assertIsNot(ent2[4], held); self.assertEqual(len(held), 5, "the served list was never extended in place")
        _append(self.path, [], partial=partial[cut:] + "\n")
        ent3 = em._read_jsonl_entry(self.path)
        self.assertEqual([r["uuid"] for r in ent3[4]], [_uuid(i) for i in range(8)], "the completed line is picked up once its newline lands")
        self.assertEqual(ent3[2], os.path.getsize(self.path)); self.assertEqual(ent3[6], gen)
        self.assertEqual(list(ent3[7]), _disk_offsets(self.path))

    def test_a_same_size_rewrite_under_a_new_mtime_reads_from_zero_with_a_new_generation(self):
        _write(self.path, [_small(i) for i in range(6)])
        ent = em._read_jsonl_entry(self.path)
        size, gen = os.path.getsize(self.path), ent[6]
        _write(self.path, [_small(i, text="HELLO") for i in range(6)])       # the same bytes count, other content
        self.assertEqual(os.path.getsize(self.path), size)
        st = os.stat(self.path); os.utime(self.path, (st.st_atime, st.st_mtime + 10))
        ent2 = em._read_jsonl_entry(self.path)
        self.assertEqual([r["message"]["content"] for r in ent2[4]], ["HELLO %d" % i for i in range(6)])
        self.assertNotEqual(ent2[6], gen, "a rewrite is a fresh generation"); self.assertEqual(ent2[5], 0)
        self.assertEqual(ent2[2], size); self.assertEqual(list(ent2[7]), _disk_offsets(self.path))

    def test_a_shrink_reads_from_zero_with_a_new_generation(self):
        _write(self.path, [_small(i) for i in range(8)])
        gen = em._read_jsonl_entry(self.path)[6]
        _write(self.path, [_small(i, text="short") for i in range(3)])
        ent = em._read_jsonl_entry(self.path)
        self.assertEqual([r["uuid"] for r in ent[4]], [_uuid(i) for i in range(3)])
        self.assertNotEqual(ent[6], gen); self.assertEqual(ent[2], os.path.getsize(self.path))
        self.assertEqual(list(ent[7]), _disk_offsets(self.path)); self.assertEqual(len(ent[7]), 6)

    def test_a_checkpoint_tail_entry_streams_from_its_offset_and_a_whole_reader_upgrades_it(self):
        """A fold checkpoint, then a fresh process (the cache cleared, the checkpoint dir set again) folding a file that
        grew since: the reader restores a TAIL entry and streams only offset..EOF (the restore's read, from a nonzero
        base). A whole reader then upgrades it to the whole file under the same generation, counted as an upgrade."""
        ck = os.path.join(self.dir, "ck"); os.makedirs(ck)
        em.set_checkpoint_dir(lambda: Path(ck))
        try:
            _write(self.path, [_small(i) for i in range(30)])
            cache = {}
            self.assertEqual(em.fold_records(cache, self.path, lambda: 0, lambda st, o: st + 1, ckpt="streamPeakFold"), 30)
            self.assertTrue(em.checkpoint_write(self.path))
            cut = os.path.getsize(self.path)
            with em._JSONL_CACHE_LOCK:
                em._JSONL_CACHE.clear(); em._RECORD_CACHE_STATS["wholeReads"] = {}
            cache.clear(); em.set_checkpoint_dir(lambda: Path(ck))      # a fresh process: nothing held, the checkpoint on disk
            _append(self.path, [_small(i) for i in range(30, 35)])     # and the file grew since the checkpoint
            self.assertEqual(em.fold_records(cache, self.path, lambda: 0, lambda st, o: st + 1, ckpt="streamPeakFold"), 35)
            with em._JSONL_CACHE_LOCK:
                tail = em._JSONL_CACHE[self.path]
            self.assertEqual(tail[5], 30, "a TAIL entry: 30 records precede its first")
            self.assertEqual([r["uuid"] for r in tail[4]], [_uuid(i) for i in range(30, 35)], "streamed from the checkpoint's offset")
            self.assertEqual(list(tail[7]), _disk_offsets(self.path)[60:], "its offsets are the file's own, from the cut on")
            self.assertEqual(tail[7][0], cut)
            self.assertEqual(em.record_cache_stats()["wholeReads"], {}, "the restore's tail read is not a whole read")

            def a_whole_reader():
                return em._read_jsonl_entry(self.path)
            ent = a_whole_reader()
            self.assertEqual(ent[5], 0); self.assertEqual(ent[6], tail[6], "the upgrade keeps the generation: the prefix stood")
            self.assertEqual([r["uuid"] for r in ent[4]], [_uuid(i) for i in range(35)])
            self.assertEqual(list(ent[7]), _disk_offsets(self.path)); self.assertEqual(ent[2], os.path.getsize(self.path))
            self.assertEqual(list(em.record_cache_stats()["wholeReads"]), ["upgrade<-a_whole_reader"])
        finally:
            em.set_checkpoint_dir(None)


class ReadCountersKeepTheirMeaning(unittest.TestCase):
    """(d) /perf's whole-read bytes for a from-zero read are the source bytes, and the per-path count is what was pulled:
    the whole file plus the guard capture, then, per append, the guard verify, the delta and the capture."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reader-count-")
        self.path = os.path.join(self.dir, "leaf.jsonl")
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0; em._RECORD_CACHE_STATS["wholeReads"] = {}

    def tearDown(self):
        _clear_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_whole_read_bytes_and_bytes_pulled_per_path(self):
        _write(self.path, [_small(i) for i in range(50)])
        size = os.path.getsize(self.path); guard = em._JSONL_TAIL_GUARD
        self.assertGreater(size, guard)

        def a_named_reader():
            return em._read_jsonl_entry(self.path)
        self.assertEqual(len(a_named_reader()[4]), 50)
        self.assertEqual(em.record_cache_stats()["wholeReads"], {"zero<-a_named_reader": {"count": 1, "bytes": size}},
                         "a from-zero read's whole-read bytes are the source bytes")
        pulled = em.read_bytes_report()[self.path]
        self.assertEqual(pulled, size + guard, "the whole file, then the guard capture")
        _append(self.path, [_small(i) for i in range(50, 53)])
        delta = os.path.getsize(self.path) - size
        self.assertEqual(len(em._read_jsonl_entry(self.path)[4]), 53)
        self.assertEqual(em.read_bytes_report()[self.path] - pulled, guard + delta + guard,
                         "an append pulls the guard verify, the appended bytes and the new guard capture")
        self.assertEqual(list(em.record_cache_stats()["wholeReads"]), ["zero<-a_named_reader"], "an append is not a whole read")


class AReaderNeverChasesAWriter(unittest.TestCase):
    """The read ends where the caller's stat saw the file end: a writer appending while the stream decodes cannot extend
    it (review find, 2026-09-15). Simulated by an open() that appends one record to the file after the reader's stat and
    before its first line is read; the record must not be in that read and must arrive with the next, as an append."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reader-chase-")
        self.path = os.path.join(self.dir, "leaf.jsonl")
        _clear_cache()

    def tearDown(self):
        _clear_cache()

    def test_a_record_appended_after_the_stat_waits_for_the_next_read(self):
        import builtins
        with open(self.path, "wb") as f:
            for i in range(50):
                f.write(json.dumps({"i": i}).encode() + b"\n")
        late = json.dumps({"late": True}).encode() + b"\n"
        real_open = builtins.open

        def open_then_append(path, mode="r", *a, **k):
            fh = real_open(path, mode, *a, **k)
            if str(path) == self.path and "b" in mode and "r" in mode:
                with real_open(self.path, "ab") as w:
                    w.write(late)                              # the writer lands between the reader's stat and its read
            return fh

        with mock.patch.object(em, "open", open_then_append, create=True):
            ent = em._read_jsonl_entry(self.path)
        self.assertEqual(len(ent[4]), 50, "the late record lies past the end the stat captured")
        self.assertEqual(ent[2], ent[1], "consumed up to the size the stat saw")
        ent2 = em._read_jsonl_entry(self.path)
        self.assertEqual(len(ent2[4]), 51, "the next read takes it as an append")
        self.assertEqual(ent2[6], ent[6], "same generation: an append, not a rewrite")
        self.assertEqual(ent2[4][-1], {"late": True})


if __name__ == "__main__":
    unittest.main()
