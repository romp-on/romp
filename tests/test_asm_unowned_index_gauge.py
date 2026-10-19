#!/usr/bin/env python3
"""/perf heap.unownedIndexes counts the lazy indexes alive that no assembly entry owns (2026-10-19, kernel/event_model.py
asm_unowned_index_report, LazyIndex.row_bytes; kernel/kernel.py _heap_stats).

A restored assembly entry holds a lazy index: the document's rows as bytes and its decoded record rows. A tree built from
the entry holds the same index, and the judges' parse store keeps one tree per session, so when the entry leaves the store
(a cap eviction, a demotion, the fold's fallback, a row refusal) the index can live on with nothing bounding it: a kernel 23
hours up held 51 live indexes against 23 restored entries. lazyIndexes counted them all and could not say which were
orphans. This gauge does: it frees nothing, it only locates the holder.

Pinned here: an index its entry owns is not counted; the same index held past its entry (by a tree a caller kept) is
counted, with its row bytes and records; /perf serves the same numbers; the entry back in the store takes it off the count.
Synthetic transcripts only: invented prompts, placeholder session ids."""
import contextlib
import gc
import os
import sys
import unittest
import weakref
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_asm_checkpoint as T                                   # noqa: E402  the stage 4a harness, its event model and state root
from test_asm_checkpoint_served import transcript                 # noqa: E402  many turns, compacting

# the harness minted its own state root at import (outside conftest's belt): session hosts off in it, as the testing rule asks
Path(os.environ["XDG_STATE_HOME"], "romp").mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off")

em = T.em
SID = T.SID


@contextlib.contextmanager
def _collector_off():
    """Only reference counting frees inside: an index another test module left unowned cannot vanish between two reads."""
    gc.collect()
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


class UnownedGauge(T.Harness):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.km = T.kernel_module()                                # loaded FIRST: the kernel's load re-executes the event model into
        #                                                            the same module, which rebinds its live-index set, so an index
        #                                                            built before the load would be in a set nobody reads

    def _restored(self, name):
        """A session written, documented, then restored in a fresh process: (path, its assembly key, the tree, the index)."""
        path = self.write(name, transcript(T.NOW - 86400 * 3, turns=40, compact_every=20))
        self.parse(path)
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        self.fresh()
        modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"], "parsed from its document")
        key = (os.path.realpath(path), SID, False)
        with em._ASM_LOCK:
            ix = em._ASM_CACHE[key]["index"]
        self.assertIsNotNone(ix, "a restored entry with a lazy index")
        self._trees.clear()
        return path, key, tree, ix

    def test_an_index_held_past_its_entry_is_counted_and_an_owned_one_is_not(self):
        path, key, tree, ix = self._restored("gauge")
        with _collector_off():
            base = em.asm_unowned_index_report()                  # other test modules may hold small indexes past their entries
            self.assertEqual(set(base), {"count", "rowBytes", "records"})
            with em._ASM_LOCK:
                entry = em._ASM_CACHE.pop(key)                    # the entry leaves; the tree a caller kept still holds its index
            try:
                rep = em.asm_unowned_index_report()
                self.assertEqual(rep["count"] - base["count"], 1, "the orphaned index is counted")
                self.assertEqual(rep["rowBytes"] - base["rowBytes"], sum(len(b) for b in ix.rowb), "with its rows' bytes")
                self.assertEqual(rep["records"] - base["records"], len(ix.records), "and its record rows")
                self.assertGreater(rep["rowBytes"] - base["rowBytes"], 0)
                self.assertEqual(self.km._PerfStats().snapshot()["heap"]["unownedIndexes"], rep, "/perf serves the same read")
            finally:
                with em._ASM_LOCK:
                    em._ASM_CACHE[key] = entry
            self.assertEqual(em.asm_unowned_index_report(), base, "owned again, it leaves the count")
        del tree, ix, entry

    def test_an_index_nothing_holds_leaves_the_count(self):
        """The gauge reads a weak set: once the entry and every tree let go, the index is gone and so is its count."""
        path, key, tree, ix = self._restored("gone")
        with _collector_off():
            base = em.asm_unowned_index_report()
            with em._ASM_LOCK:
                entry = em._ASM_CACHE.pop(key)
            self.assertEqual(em.asm_unowned_index_report()["count"] - base["count"], 1)
            em._asm_release(entry)                                # what the store does for an entry it drops
            ref = weakref.ref(ix)
            del entry, tree, ix
        gc.collect()
        self.assertIsNone(ref(), "nothing holds the index now")
        self.assertLessEqual(em.asm_unowned_index_report()["count"], base["count"], "the freed index is not counted")


if __name__ == "__main__":
    unittest.main()
