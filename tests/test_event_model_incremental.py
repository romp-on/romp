#!/usr/bin/env python3
"""Append-incremental transcript reads (_read_jsonl_incremental): the dashboard re-parsed a 40MB
streaming transcript from byte zero on every push (the user 2026-07-05, the responsiveness audit) —
now a grown file loads only its appended bytes. The contract under test:

  - incremental results are BYTE-FOR-BYTE equivalent to a cold full read, whatever the append pattern;
  - a trailing partial line (a writer caught mid-append) is never consumed early and never lost;
  - anything that isn't a pure append (shrink, rewrite, same-size touch) falls back to a full re-read;
  - the served records list is never extended in place (a concurrent reader's list is stable).

SYNTHETIC fixtures only (placeholder uuids, invented text).
"""
import json
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
em = SourceFileLoader("romp_event_model_inc", os.path.join(BIN, "romp-event-model")).load_module()


def _rec(i, text="hello"):
    return {"type": "user", "uuid": "11111111-2222-3333-4444-%012d" % i,
            "timestamp": "2026-07-05T10:%02d:00Z" % (i % 60),
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


class IncrementalRead(unittest.TestCase):
    def setUp(self):
        em._JSONL_CACHE.clear()
        fd, self.p = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)

    def tearDown(self):
        em._JSONL_CACHE.clear()
        os.unlink(self.p)

    def _fresh(self):
        """A cold full read of the current file — the equivalence oracle."""
        return list(em._read_jsonl(self.p))

    def test_appends_accumulate_equivalently(self):
        _write(self.p, [_rec(i) for i in range(3)])
        self.assertEqual(em._read_jsonl_incremental(self.p), self._fresh())
        _append(self.p, [_rec(i) for i in range(3, 7)])
        self.assertEqual(em._read_jsonl_incremental(self.p), self._fresh(), "grown file == cold read")
        _append(self.p, [_rec(7)])
        self.assertEqual(em._read_jsonl_incremental(self.p), self._fresh(), "and again")

    def test_unchanged_file_serves_the_cache(self):
        _write(self.p, [_rec(0)])
        a = em._read_jsonl_incremental(self.p)
        self.assertIs(em._read_jsonl_incremental(self.p), a, "same (mtime,size) → the very same list, no I/O")

    def test_partial_trailing_line_is_deferred_not_lost(self):
        full = json.dumps(_rec(1))
        _write(self.p, [_rec(0)], partial=full[:20])          # writer caught mid-line
        got = em._read_jsonl_incremental(self.p)
        self.assertEqual(got, [_rec(0)], "the half-written line is not consumed (and not crashed on)")
        _append(self.p, [], partial=full[20:] + "\n")         # the writer finishes the line
        self.assertEqual(em._read_jsonl_incremental(self.p), [_rec(0), _rec(1)],
                         "the completed line arrives whole on the next read")

    def test_shrunk_file_falls_back_to_a_full_reread(self):
        _write(self.p, [_rec(i) for i in range(5)])
        em._read_jsonl_incremental(self.p)
        _write(self.p, [_rec(9)])                             # rewrite, smaller
        self.assertEqual(em._read_jsonl_incremental(self.p), [_rec(9)])

    def test_larger_rewrite_with_a_different_prefix_is_detected(self):
        _write(self.p, [_rec(0)])
        em._read_jsonl_incremental(self.p)
        _write(self.p, [_rec(8, text="rewritten with a much longer body so the file grows past the original"),
                        _rec(9)])                             # rewrite that happens to be LARGER
        self.assertEqual(em._read_jsonl_incremental(self.p), self._fresh(),
                         "the tail-guard mismatch forces a full re-read — never a corrupt splice")

    def test_same_size_new_mtime_falls_back_to_a_full_reread(self):
        _write(self.p, [_rec(0)])
        size0 = os.path.getsize(self.p)
        em._read_jsonl_incremental(self.p)
        _write(self.p, [_rec(1)])                             # SAME byte length, different content
        self.assertEqual(os.path.getsize(self.p), size0, "fixture invariant: the rewrite keeps the size")
        os.utime(self.p, (os.path.getmtime(self.p) + 5,) * 2)   # force a visibly newer mtime
        self.assertEqual(em._read_jsonl_incremental(self.p), [_rec(1)],
                         "same size but a new mtime is a REWRITE → full re-read, never the stale cache")

    def test_served_list_is_never_extended_in_place(self):
        _write(self.p, [_rec(0)])
        first = em._read_jsonl_incremental(self.p)
        held = list(first)
        _append(self.p, [_rec(1)])
        second = em._read_jsonl_incremental(self.p)
        self.assertEqual(first, held, "a concurrent reader's list is stable across a grow")
        self.assertIsNot(second, first)
        self.assertEqual(second, held + [_rec(1)])

    def test_missing_file_returns_empty_and_drops_the_entry(self):
        _write(self.p, [_rec(0)])
        em._read_jsonl_incremental(self.p)
        os.unlink(self.p)
        self.assertEqual(em._read_jsonl_incremental(self.p), [])
        self.assertNotIn(self.p, em._JSONL_CACHE)
        _write(self.p, [_rec(2)])                             # recreate for tearDown's unlink

    def test_file_adapter_reads_incrementally(self):
        import inspect
        src = inspect.getsource(em.FileAdapter.__init__)
        self.assertIn("_read_jsonl_incremental(fp)", src,
                      "the transcript hot path must use the incremental reader")


class ParseSessionEquivalence(unittest.TestCase):
    """parse_session over a growing transcript == a cold parse of the same bytes, at every step."""

    def setUp(self):
        em._JSONL_CACHE.clear()
        fd, self.p = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)

    def tearDown(self):
        em._JSONL_CACHE.clear()
        os.unlink(self.p)

    def test_growing_transcript_parses_identically(self):
        base = "11111111-2222-3333-4444-"
        recs = []
        for i in range(6):
            u, parent = base + "%012d" % i, (base + "%012d" % (i - 1) if i else None)
            role = "user" if i % 2 == 0 else "assistant"
            recs.append({"type": role, "uuid": u, "parentUuid": parent,
                         "timestamp": "2026-07-05T10:00:%02dZ" % i,
                         "message": {"role": role, "content": "step %d" % i}})
        for cut in (2, 4, 6):
            _write(self.p, recs[:cut])
            warm = em.parse_session(self.p)                   # rides the (possibly incremental) cache
            em._JSONL_CACHE.clear()
            cold = em.parse_session(self.p)                   # forced full read
            self.assertEqual(warm, cold, "prefix of %d records: warm == cold" % cut)
            _write(self.p, recs[:cut])                        # restore for the next append step
            em._read_jsonl_incremental(self.p)                # warm the cache at this prefix


def _manual_compact_recs():
    """A transcript containing a LIVE manual /compact's on-disk shape (synthetic content;
    shape mirrors the live corpus): the DETACHED boundary + summary side branch appended
    first at completion time, then the command wrappers, then the stdout — plus the
    post-compact growth appended separately. Returns (prefix, growth)."""
    b = lambda i: "11111111-2222-3333-4444-%012d" % i
    ts = lambda s: "2026-07-05T10:%02d:%02dZ" % (s // 60, s % 60)
    prefix = [
        {"type": "user", "uuid": b(0), "parentUuid": None, "timestamp": ts(0),
         "promptSource": "typed", "message": {"role": "user", "content": "start the refactor"}},
        {"type": "assistant", "uuid": b(1), "parentUuid": b(0), "timestamp": ts(10),
         "message": {"role": "assistant", "content": [{"type": "text", "text": "refactor done"}],
                     "stop_reason": "end_turn"}},
        {"type": "system", "subtype": "compact_boundary", "uuid": b(2), "parentUuid": None,
         "logicalParentUuid": b(1), "timestamp": ts(40),
         "compactMetadata": {"trigger": "manual", "preTokens": 120000, "postTokens": 5000}},
        {"type": "user", "uuid": b(3), "parentUuid": b(2), "timestamp": ts(40),
         "isCompactSummary": True,
         "message": {"role": "user", "content": "synthetic compact summary"}},
        {"type": "user", "uuid": b(4), "parentUuid": b(1), "timestamp": ts(30),
         "isMeta": True, "promptId": "p1", "message": {"role": "user", "content": "/compact"}},
        {"type": "user", "uuid": b(5), "parentUuid": b(4), "timestamp": ts(30), "promptId": "p1",
         "message": {"role": "user", "content": "<command-name>/compact</command-name>"}},
        {"type": "user", "uuid": b(6), "parentUuid": b(5), "timestamp": ts(40), "promptId": "p1",
         "message": {"role": "user",
                     "content": "<local-command-stdout>Compacted</local-command-stdout>"}},
    ]
    growth = [
        {"type": "user", "uuid": b(7), "parentUuid": b(6), "timestamp": ts(60),
         "promptSource": "typed",
         "message": {"role": "user", "content": "carry on after the compact"}},
        {"type": "assistant", "uuid": b(8), "parentUuid": b(7), "timestamp": ts(70),
         "message": {"role": "assistant", "content": [{"type": "text", "text": "carried on"}],
                     "stop_reason": "end_turn"}},
    ]
    return prefix, growth


class ManualCompactAdoptionEquivalence(unittest.TestCase):
    """A transcript that grows PAST a live manual /compact must produce the same adopted
    compact atom incrementally as cold: the adoption repair (_adopt_detached_compactions)
    reads only the assembled graph and must never mutate the cache's shared record lists,
    so the grew-branch reuse stays intact across parses that ran the repair."""

    def setUp(self):
        em._JSONL_CACHE.clear()
        fd, self.p = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)

    def tearDown(self):
        em._JSONL_CACHE.clear()
        os.unlink(self.p)

    def _cards(self, out):
        return [a for t in out["turns"] for a in t["atoms"]
                if a.get("subtype") == "compact_boundary"]

    def test_growth_past_a_manual_compact_adopts_identically_warm_and_cold(self):
        prefix, growth = _manual_compact_recs()
        _write(self.p, prefix)
        first = em.parse_session(self.p)                  # cold; primes the cache
        self.assertEqual(len(self._cards(first)), 1,
                         "the detached boundary is adopted before any growth")
        _append(self.p, growth)
        warm = em.parse_session(self.p)                   # rides the grew-branch reuse
        self.assertEqual(em.parse_session(self.p), warm,
                         "a re-parse from the warm cache is stable — the repair mutated no cached record")
        em._JSONL_CACHE.clear()
        cold = em.parse_session(self.p)
        self.assertEqual(warm, cold, "grown past the compact: warm == cold, adoption included")
        self.assertEqual(len(self._cards(cold)), 1, "exactly one adopted card either way")


if __name__ == "__main__":
    unittest.main()
