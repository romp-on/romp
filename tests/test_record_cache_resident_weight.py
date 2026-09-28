#!/usr/bin/env python3
"""The record cache weighs what its entries hold in MEMORY, and its default budget is capped (2026-09-28).

The shared reader keeps each JSONL file's parsed records; past a byte budget the least recently used entries go. Each
entry weighed its FILE bytes while the parsed records take about three times that in memory (the kernel's own reader,
measured through RSS in a fresh process on three live transcripts of 14, 287 and 599 MiB: 2.66, 3.07 and 3.05 resident
bytes per file byte), and the default budget was half of MemTotal in those file bytes: about 126 GiB on a 252 GiB
devbox, so the cache could hold about three times the machine's memory and never evicted. Entries now weigh their file
bytes times the measured multiplier, the default budget is a quarter of memory, floored at 4 GiB and capped at 64 GiB,
and the per-thread handle a fold's read leaves for its pin no longer keeps an entry alive past that pin.

Drives the real reader over synthetic JSONL in a private directory. Synthetic only."""
import json
import os
import tempfile
import threading
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load: the module resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model_resident_weight", os.path.join(BIN, "romp-event-model"))

GIB = 1024 ** 3


def _write(path, n, text="x" * 400):
    with open(path, "w") as f:
        for i in range(n):
            f.write(json.dumps({"uuid": "11111111-2222-3333-4444-%012d" % i, "type": "assistant",
                                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}) + "\n")
    return path


class _Cache(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="resident-weight-")
        self.addCleanup(self._td.cleanup)
        self.dir = self._td.name
        self._budget = em._JSONL_CACHE_BUDGET_BYTES
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0

    def tearDown(self):
        em._JSONL_CACHE_BUDGET_BYTES = self._budget
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        em._LAST_ENTRY.ent = None

    def _file(self, name, n=200):
        return _write(os.path.join(self.dir, name), n)

    def _consistent(self):
        return em._JSONL_CACHE_BYTES[0] == sum(em._entry_weight(e) for e in em._JSONL_CACHE.values())


class ResidentWeight(_Cache):
    def test_an_entry_weighs_its_estimated_resident_bytes_not_its_file_bytes(self):
        path = self._file("web.jsonl")
        em._read_jsonl_incremental(path)
        size = os.path.getsize(path)
        w = em._entry_weight(em._JSONL_CACHE[path])
        self.assertGreaterEqual(w, int(2.5 * size), "parsed records take about 3x their file bytes; the entry weighed %d "
                                                     "for a %d-byte file (the base weighed the file bytes alone)" % (w, size))
        self.assertEqual(w, int(size * em.RECORD_CACHE_RESIDENT_PER_FILE_BYTE))
        self.assertEqual(em.record_cache_stats()["bytes"], w, "recordCache.bytes reads the same estimate")
        self.assertTrue(self._consistent())

    def test_eviction_happens_once_the_weighted_budget_is_exceeded(self):
        """Two files whose FILE bytes fit the budget with room to spare, and whose parsed records do not: the second evicts
        the first. At the base both stayed, which is the 'never evicts' of a budget counted in file bytes."""
        a, b = self._file("web.jsonl"), self._file("api.jsonl")
        size = os.path.getsize(a)
        em._JSONL_CACHE_BUDGET_BYTES = int(size * 3)                 # three files' worth of file bytes
        ev0 = em._RECORD_CACHE_STATS["budgetEvictions"]
        em._read_jsonl_incremental(a)
        em._read_jsonl_incremental(b)
        self.assertEqual(em._RECORD_CACHE_STATS["budgetEvictions"] - ev0, 1, "the second file's records evict the first's")
        self.assertEqual(set(em._JSONL_CACHE), {b})
        self.assertLessEqual(em._JSONL_CACHE_BYTES[0], em._JSONL_CACHE_BUDGET_BYTES)
        self.assertTrue(self._consistent())

    def test_a_tail_entry_weighs_the_resident_estimate_of_the_bytes_it_holds(self):
        path = self._file("web.jsonl", n=20)
        with open(path, "rb") as f:
            data = f.read()
        cut = data.index(b"\n", len(data) // 2) + 1                  # the checkpoint's cut: a record boundary mid-file
        guard = data[cut - em._JSONL_TAIL_GUARD:cut]
        ent = em._read_jsonl_entry(path, tail_ok=True, tail_from=(cut, data[:cut].count(b"\n"), guard))
        self.assertGreater(ent[5], 0, "a tail entry")
        self.assertEqual(em._entry_weight(ent), int((len(data) - cut) * em.RECORD_CACHE_RESIDENT_PER_FILE_BYTE))
        self.assertTrue(self._consistent())


class DefaultBudget(unittest.TestCase):
    """A quarter of MemTotal, never under 4 GiB, never over 64 GiB, in the entries' resident estimate."""

    def test_capped_on_a_large_machine(self):
        self.assertEqual(em._record_cache_default_budget_bytes("MemTotal:  268435456 kB\n"), 64 * GIB,
                         "a 256 GiB machine: capped at 64 GiB (the base gave half, 128 GiB, in file bytes)")
        self.assertEqual(em._record_cache_default_budget_bytes("MemTotal:  1073741824 kB\n"), 64 * GIB, "1 TiB: the same cap")

    def test_a_quarter_between_the_floor_and_the_cap(self):
        self.assertEqual(em._record_cache_default_budget_bytes("MemTotal:  67108864 kB\nMemFree: 1 kB\n"), 16 * GIB,
                         "a 64 GiB machine: a quarter")

    def test_never_under_four_gib(self):
        self.assertEqual(em._record_cache_default_budget_bytes("MemTotal:  8000000 kB\n"), 4 * GIB)
        self.assertEqual(em._record_cache_default_budget_bytes("garbage"), 4 * GIB, "no MemTotal: the floor")

    def test_the_environment_still_sets_it_outright(self):
        src = open(em.__file__).read()
        self.assertIn('int(float(os.environ["ROMP_RECORD_CACHE_BUDGET_MB"]) * 1024 * 1024)', src)
        self.assertIn("else _record_cache_default_budget_bytes()", src)


class NoEntryKeptPastItsPin(_Cache):
    """_LAST_ENTRY.ent let a fold pin the entry its own read returned even if another thread evicted it in between; but it
    stayed set until that thread's next read, so every long-lived thread kept its last entry alive outside the budget,
    however large and however long ago evicted."""

    def test_a_plain_read_parks_no_entry(self):
        path = self._file("web.jsonl")
        em._read_jsonl_incremental(path)
        self.assertFalse(getattr(em._LAST_ENTRY, "ent", None) is not None, "only a fold's read, which pins next, parks its entry")

    def test_a_fold_pins_an_entry_evicted_between_its_read_and_its_pin_then_lets_go(self):
        path = self._file("web.jsonl", n=30)
        real = em._tail_read
        reads = []

        def read_then_evict(p, failed):
            recs = real(p, failed)
            reads.append(1)
            with em._JSONL_CACHE_LOCK:                                # another thread's insert evicts it before the pin
                em._cache_pop_locked(str(p))
            return recs
        em._tail_read = read_then_evict
        self.addCleanup(setattr, em, "_tail_read", real)
        out = em.fold_records({}, path, lambda: 0, lambda n, r: n + 1)
        self.assertEqual(out, 30)
        self.assertEqual(len(reads), 1, "the pin found the evicted entry through the read's own handle: no second read")
        self.assertFalse(getattr(em._LAST_ENTRY, "ent", None) is not None, "and the handle is released at the pin (the base kept it)")

    def test_the_handle_is_per_thread_and_released_on_every_thread(self):
        path = self._file("web.jsonl")
        seen = []

        def work():
            em.fold_records({}, path, lambda: 0, lambda n, r: n + 1)
            seen.append(getattr(em._LAST_ENTRY, "ent", None) is None)
        t = threading.Thread(target=work)
        t.start(); t.join(10)
        self.assertEqual(seen, [True], "released on the thread that folded")


if __name__ == "__main__":
    unittest.main()
