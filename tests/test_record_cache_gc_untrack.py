#!/usr/bin/env python3
"""The record cache's decoded records are out of the cyclic collector's view, and eviction still frees them (2026-10-06).

A full (generation-two) collection walks every container the collector tracks. The record cache holds tens of
gigabytes of decoded transcript json on a busy machine, and with it in view each full pause grew with the cache (about
28 s per full collection on a live kernel holding 61.8 GB). The records are acyclic json freed by reference counting,
so the reader untracks each record's dicts and lists at decode and the entry's outer list where the entry is built.
Pinned here: every container in a cached entry is untracked after a whole read and after an append (the appended
records and the fresh outer list alike); the collector's object list does not hold them; the content is unchanged; an
eviction frees them with the collector disabled; a dict a caller later puts a container into re-tracks itself; and
ROMP_RECORD_CACHE_GC_UNTRACK=off keeps them tracked. Synthetic data in a private directory only."""
import gc
import json
import os
import sys
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()       # hermetic state BEFORE the load: the root resolves at import
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model_gc_untrack", os.path.join(BIN, "romp-event-model"))


def _rec(i):
    return {"uuid": "11111111-2222-3333-4444-%012d" % i, "type": "assistant" if i % 2 else "user",
            "cwd": "/w/notes-api",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "invented text %d" % i},
                {"type": "tool_use", "id": "toolu_%d" % i, "name": "Bash",
                 "input": {"command": "make test", "args": ["-k", "fixture", {"depth": [1, 2, {"x": None}]}]}}],
                "usage": {"input_tokens": i, "cache_creation": {"ephemeral_1h_input_tokens": 0}}}}


def _containers(obj):
    out, stack = [], [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, (dict, list)):
            out.append(o)
            stack.extend(o.values() if isinstance(o, dict) else o)
    return out


class _Cache(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="gc-untrack-")
        self.addCleanup(self._td.cleanup)
        self.path = os.path.join(self._td.name, "web.jsonl")
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        self._on = getattr(em, "_GC_UNTRACK_ON", None)   # absent on a tree without the change: the assertions below say so

    def tearDown(self):
        if self._on is not None:
            em._GC_UNTRACK_ON = self._on
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        em._LAST_ENTRY.ent = None

    def _write(self, start, n, mode="w"):
        with open(self.path, mode) as f:
            for i in range(start, start + n):
                f.write(json.dumps(_rec(i)) + "\n")

    def _tracked(self, records):
        return [c for r in records for c in _containers(r) if gc.is_tracked(c)]


class Untracked(_Cache):
    def test_the_c_call_resolves_and_the_default_is_on(self):
        self.assertIsNotNone(em._PY_GC_UNTRACK, "PyObject_GC_UnTrack did not resolve through ctypes")
        self.assertTrue(em._record_cache_gc_untrack_enabled({}))

    def test_a_whole_read_leaves_no_cached_container_tracked(self):
        self._write(0, 50)
        ent = em._read_jsonl_entry(self.path)
        records = ent[4]
        self.assertEqual(len(records), 50)
        self.assertFalse(gc.is_tracked(records), "the entry's outer list is still in the collector's view")
        self.assertEqual(self._tracked(records), [])
        self.assertEqual(records, [_rec(i) for i in range(50)])     # the content is exactly the decode's

    def test_a_full_collection_does_not_hold_the_records(self):
        self._write(0, 20)
        records = em._read_jsonl_entry(self.path)[4]
        ids = {id(c) for r in records for c in _containers(r)} | {id(records)}
        gc.collect(2)
        self.assertEqual(ids & {id(o) for o in gc.get_objects()}, set(),
                         "a cached container is on the collector's lists, so a full collection walks it")

    def test_an_append_untracks_the_new_records_and_the_new_outer_list(self):
        self._write(0, 10)
        first = em._read_jsonl_entry(self.path)[4]
        self._write(10, 7, mode="a")
        ent = em._read_jsonl_entry(self.path)
        records = ent[4]
        self.assertIsNot(records, first)                         # the reader builds a new list per append
        self.assertEqual(len(records), 17)
        self.assertFalse(gc.is_tracked(records))
        self.assertEqual(self._tracked(records), [])
        self.assertEqual(records[10:], [_rec(i) for i in range(10, 17)])

    def test_eviction_frees_the_records_by_reference_counting_alone(self):
        self._write(0, 2000)
        gc.collect()
        gc.disable()
        try:
            before = sys.getallocatedblocks()
            em._read_jsonl_entry(self.path)
            em._LAST_ENTRY.ent = None
            loaded = sys.getallocatedblocks()
            with em._JSONL_CACHE_LOCK:
                em._cache_pop_locked(self.path)
            self.assertEqual(len(em._JSONL_CACHE), 0)
            after = sys.getallocatedblocks()
        finally:
            gc.enable()
        grew = loaded - before
        self.assertGreater(grew, 20000)                          # the records were really held
        self.assertLess(after - before, grew * 0.05,
                        "evicting the entry did not return its blocks without the cyclic collector")

    def test_a_dict_a_caller_puts_a_container_into_is_tracked_again(self):
        self._write(0, 3)
        rec = em._read_jsonl_entry(self.path)[4][0]
        self.assertFalse(gc.is_tracked(rec))
        rec["annotation"] = {"k": [1]}
        self.assertTrue(gc.is_tracked(rec), "CPython re-tracks a dict when a container is put in it")

    def test_the_off_switch_keeps_the_records_tracked(self):
        for v in ("off", "0", "false", " OFF "):
            self.assertFalse(em._record_cache_gc_untrack_enabled({"ROMP_RECORD_CACHE_GC_UNTRACK": v}))
        self.assertTrue(em._record_cache_gc_untrack_enabled({"ROMP_RECORD_CACHE_GC_UNTRACK": "on"}))
        em._GC_UNTRACK_ON = False
        self._write(0, 5)
        records = em._read_jsonl_entry(self.path)[4]
        self.assertTrue(gc.is_tracked(records))
        self.assertTrue(all(gc.is_tracked(r) for r in records))

    def test_the_walk_handles_deep_nesting_without_recursion(self):
        deep = cur = []
        for _ in range(5000):
            nxt = [{"a": []}]
            cur.append(nxt); cur = nxt[0]["a"]
        em._gc_untrack_json(deep)
        self.assertEqual([c for c in _containers(deep) if gc.is_tracked(c)], [])


if __name__ == "__main__":
    unittest.main()
