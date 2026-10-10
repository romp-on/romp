#!/usr/bin/env python3
"""A TAIL entry of the record cache weighs the bytes it holds (review find, 2026-09-15).

The shared reader keeps one entry per transcript; the byte budget and GET /perf recordCache.bytes count each
entry's weight (_entry_weight), and past the budget the least recently used entries go. A tail entry, the
records past an assembly checkpoint's cut (base > 0), weighed `size - ent[2]`; but ent[2] is the CONSUMED END
offset, which stands at the file's size after every newline-terminated read, so every tail entry weighed 0:
a restored session's held tail and every append to it were invisible to the budget and under-read by the
counter (a 1 MiB tail read through the reader reported weight 0 and cache bytes 0). The weight is now the
file's size less the offset of the first held record (offs[0]), and 0 while a tail holds no record.

Drives the real reader over synthetic transcripts in a private directory: a prefix the checkpoint has, then
records past its cut, read with tail_ok and tail_from as the assembly's restore reads them. Synthetic only."""
import array
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — the module resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model_tail_weight", os.path.join(BIN, "romp-event-model"))


def _w(file_bytes):
    """The weight of an entry holding `file_bytes` of a file: its resident estimate since 2026-09-28
    (tests/test_record_cache_resident_weight.py); the arithmetic below is in file bytes as it always was."""
    return int(file_bytes * em.RECORD_CACHE_RESIDENT_PER_FILE_BYTE)


def _rec(i, text="x"):
    return (json.dumps({"uuid": "11111111-2222-3333-4444-%012d" % i, "type": "assistant", "text": text}) + "\n").encode()


class TailEntryWeight(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="tail-weight-")
        self.addCleanup(self._td.cleanup)
        self.dir = self._td.name
        self._budget = em._JSONL_CACHE_BUDGET_BYTES
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0

    def tearDown(self):
        em._JSONL_CACHE_BUDGET_BYTES = self._budget
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0

    def _tail_file(self, name, prefix_records=1, tail_records=1, text_bytes=200_000):
        """A transcript whose first `prefix_records` a checkpoint holds and whose rest is the tail: returns
        (path, prefix_len, tail_from) with tail_from the (offset, count, guard) triple the restore hands the
        reader — the guard being the bytes just before the cut, as the assembly checkpoint records them."""
        path = os.path.join(self.dir, name)
        prefix = b"".join(_rec(i) for i in range(prefix_records))
        tail = b"".join(_rec(100 + i, "t" * text_bytes) for i in range(tail_records))
        with open(path, "wb") as f:
            f.write(prefix + tail)
        guard = prefix[-em._JSONL_TAIL_GUARD:]
        return path, len(prefix), (len(prefix), prefix_records, guard)

    def _consistent(self):
        return em._JSONL_CACHE_BYTES[0] == sum(em._entry_weight(e) for e in em._JSONL_CACHE.values())

    def test_a_tail_entry_weighs_the_bytes_it_holds(self):
        # red on main: the entry's weight and recordCache.bytes were 0 for this 200 KB tail
        path, plen, tf = self._tail_file("web.jsonl")
        ent = em._read_jsonl_entry(path, tail_ok=True, tail_from=tf)
        size = os.path.getsize(path)
        self.assertEqual((ent[5], len(ent[4])), (1, 1), "a tail entry: base 1, one held record")
        self.assertEqual(ent[7][0], plen, "the first held record sits at the cut")
        self.assertEqual(em._entry_weight(ent), _w(size - plen))
        self.assertEqual(em.record_cache_stats()["bytes"], _w(size - plen))
        self.assertTrue(self._consistent())

    def test_an_append_to_a_tail_grows_its_weight_by_the_appended_bytes(self):
        path, plen, tf = self._tail_file("web.jsonl")
        em._read_jsonl_entry(path, tail_ok=True, tail_from=tf)
        w1 = em._JSONL_CACHE_BYTES[0]
        with open(path, "ab") as f:
            f.write(_rec(200, "u" * 50_000))
        ent = em._read_jsonl_entry(path, tail_ok=True, tail_from=tf)
        self.assertEqual(len(ent[4]), 2, "the append landed on the tail")
        self.assertEqual(em._JSONL_CACHE_BYTES[0], _w(os.path.getsize(path) - plen))
        self.assertGreater(em._JSONL_CACHE_BYTES[0], w1)
        self.assertTrue(self._consistent())

    def test_an_upgrade_to_the_whole_file_weighs_the_whole_file(self):
        path, plen, tf = self._tail_file("web.jsonl")
        em._read_jsonl_entry(path, tail_ok=True, tail_from=tf)
        ent = em._read_jsonl_entry(path)                       # a whole reader: the tail is upgraded to the file
        self.assertEqual(ent[5], 0)
        self.assertEqual(em._entry_weight(ent), _w(os.path.getsize(path)))
        self.assertEqual(em._JSONL_CACHE_BYTES[0], _w(os.path.getsize(path)), "one entry, the whole file")
        self.assertTrue(self._consistent())

    def test_a_tail_holding_no_record_weighs_nothing(self):
        # the cut at the file's end: nothing past it, nothing held, nothing counted
        path, plen, _tf = self._tail_file("web.jsonl", prefix_records=2, tail_records=0)
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            guard = f.read()[-em._JSONL_TAIL_GUARD:]
        ent = em._read_jsonl_entry(path, tail_ok=True, tail_from=(size, 2, guard))
        self.assertEqual((ent[5], len(ent[4])), (2, 0))
        self.assertEqual(em._entry_weight(ent), 0)
        self.assertEqual(em._JSONL_CACHE_BYTES[0], 0)
        # ...and a checkpoint's bare restored entry (the 7-tuple before its first read) the same
        bare = (0.0, size, size, guard, [], 2, 0)
        self.assertEqual(em._entry_weight(bare), 0)

    def test_a_tail_with_records_but_no_offsets_is_weighed_whole_never_zero(self):
        # a shape no current writer produces (records held, no offsets to say where they start): a bound that
        # under-counts is no bound, so such an entry counts the whole file, over rather than under
        held = [{"uuid": "11111111-2222-3333-4444-000000000009", "type": "assistant"}]
        self.assertEqual(em._entry_weight((0.0, 5000, 5000, b"", held, 1, 0, array.array("q"))), _w(5000))
        self.assertEqual(em._entry_weight((0.0, 5000, 5000, b"", held, 1, 0)), _w(5000), "a 7-tuple holding records: the same")

    def test_the_budget_sees_tail_entries(self):
        # red on main: two tails of ~200 KB each under a budget of 1.5 tails never evicted (they weighed 0)
        a, plen_a, tf_a = self._tail_file("web.jsonl")
        b, plen_b, tf_b = self._tail_file("api.jsonl")
        one = _w(os.path.getsize(a) - plen_a)
        em._JSONL_CACHE_BUDGET_BYTES = int(one * 1.5)
        ev0 = em._RECORD_CACHE_STATS["budgetEvictions"]
        em._read_jsonl_entry(a, tail_ok=True, tail_from=tf_a)
        em._read_jsonl_entry(b, tail_ok=True, tail_from=tf_b)
        self.assertEqual(em._RECORD_CACHE_STATS["budgetEvictions"] - ev0, 1, "the second tail evicts the first")
        self.assertEqual(set(em._JSONL_CACHE), {b})
        self.assertLessEqual(em._JSONL_CACHE_BYTES[0], em._JSONL_CACHE_BUDGET_BYTES)
        self.assertTrue(self._consistent())

    def test_a_pop_subtracts_what_the_insert_added(self):
        path, plen, tf = self._tail_file("web.jsonl")
        em._read_jsonl_entry(path, tail_ok=True, tail_from=tf)
        self.assertGreater(em._JSONL_CACHE_BYTES[0], 0)
        with em._JSONL_CACHE_LOCK:
            w = em._cache_pop_locked(path)
        self.assertEqual(w, _w(os.path.getsize(path) - plen))
        self.assertEqual(em._JSONL_CACHE_BYTES[0], 0)


if __name__ == "__main__":
    unittest.main()
