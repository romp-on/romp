#!/usr/bin/env python3
"""T398 (2026-09-12): a boot parsed two documented live leaves whole with no fallback counted, and nothing on GET /perf named the
road the parse took. The assembly's road counters (serve, fold, restore, full with its reason, bypass, the g:<reason> demotions)
ride asmCheckpoint.parse and the boot-health row, beside asmCheckpoint.removed, the document files removed per reason."""
import os
import sys
import unittest
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import em, G, SID, NOW, Harness, kernel_module   # noqa: E402


class AssemblyRoadCounters(Harness):
    def setUp(self):
        super().setUp()
        kernel_module()                                   # the kernel's first load refreshes the event model's globals: load it
        #                                                   before any counter is read, so the row and the test see one dict

    def _reset(self):
        for k in list(em._ASM_STATS):
            em._ASM_STATS[k] = 0

    def _boot_row_parse(self):
        """The boot-health row's `parse` as the kernel writes it, over THIS test's event model (the kernel module holds its own
        instance; the row reads whichever `em` the kernel names)."""
        km = kernel_module()
        saved = (km._append_restart_cut, km._BOOT_HEALTH_DONE[0], km.em); rows = []
        km._append_restart_cut = lambda row: rows.append(row); km._BOOT_HEALTH_DONE[0] = False; km.em = em
        try:
            km._boot_health_first_cycle(1.0)
        finally:
            km._append_restart_cut, km._BOOT_HEALTH_DONE[0], km.em = saved
        self.assertEqual(len(rows), 1)
        return rows[0]["parse"]

    def test_the_roads_ride_the_perf_block_with_the_full_parses_reason(self):
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("roads", records(), sent=sent)
        self.fresh(); self._reset()
        self.parse(path)                                                  # no document yet: a full parse, its reason named
        parse = em.asm_checkpoint_stats()["parse"]
        self.assertEqual(parse.get("full:noDocument"), 1, "%s" % parse)
        self.assertEqual(parse["full"], 1)
        self.assertTrue(self.doc(path))
        self.fresh(); self._reset()
        self.parse(path)                                                  # the document restores
        parse = em.asm_checkpoint_stats()["parse"]
        self.assertEqual(parse.get("restore"), 1, "%s" % parse)
        self.parse(path)                                                  # the same tree served from the entry
        self.assertEqual(em.asm_checkpoint_stats()["parse"]["serve"], 1)
        for k in ("serve", "fold", "full", "bypass", "fallback"):
            self.assertIn(k, parse)

    def test_a_from_zero_read_that_replaces_the_leafs_entry_demotes_and_is_counted_as_such(self):
        """The cascade shape under study: an assembly entry stands, a whole reader replaces the leaf's record entry under a new
        generation, and the next parse's gates demote the entry to a FULL parse that consults no document and counted nothing
        anyone could see; now `g:rewrite` and `full:demoted` name it."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("demote", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh(); self.parse(path); self._reset()                     # the entry stands, restored from the document
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(path, None)
        em._read_jsonl_entry(path, tail_ok=False)                          # a from-zero read: a fresh generation
        self.parse(path)
        parse = em.asm_checkpoint_stats()["parse"]
        self.assertEqual(parse.get("g:rewrite"), 1, "%s" % parse)
        self.assertEqual(parse.get("full:demoted"), 1, "%s" % parse)
        row = self._boot_row_parse()
        self.assertEqual((row.get("g:rewrite"), row.get("full:demoted")), (1, 1), "the boot row's values: %r" % row)

    def test_a_refused_standing_document_is_booked_as_refused_not_as_none(self):
        """Round one, medium: the restore's refusal unlinked the document before _assemble decided the reason by a stat, so a
        corrupt standing document was booked full:noDocument and a boot whose documents were all refused read as a boot that
        had none. The refusal is recorded per path before the unlink and the parse books full:refused from it."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("removed", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        em._asm_ckpt_file(path).write_bytes(b"not a document")
        em._ASM_CKPT_STATS["removed"] = {}
        self.fresh(); self._reset(); self.parse(path)
        st = em.asm_checkpoint_stats()
        self.assertEqual(st["removed"], {"fallback:corrupt": 1}, "%s" % st["removed"])
        self.assertFalse(em._asm_ckpt_file(path).exists())
        self.assertEqual((st["parse"].get("full:refused"), st["parse"].get("full:noDocument", 0)), (1, 0),
                         "a document that stood and did not verify: refused, not none: %s" % st["parse"])
        self.assertEqual(self._boot_row_parse().get("full:refused"), 1, "the boot row carries it")

    def test_the_boot_health_row_carries_the_parses_roads(self):
        km = kernel_module()
        saved = (km._append_restart_cut, km._BOOT_HEALTH_DONE[0])
        rows = []
        km._append_restart_cut = lambda row: rows.append(row)
        km._BOOT_HEALTH_DONE[0] = False
        try:
            km._boot_health_first_cycle(1.0)
        finally:
            km._append_restart_cut, km._BOOT_HEALTH_DONE[0] = saved
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0].get("parse"), dict, "%r" % rows[0])
        for k in ("serve", "fold", "restore", "full", "bypass", "fallback"):
            self.assertIn(k, rows[0]["parse"], "seeded keys: a row without one means zero: %r" % rows[0]["parse"])
        self.assertEqual(rows[0]["parse"], em.asm_checkpoint_stats()["parse"], "the row is the perf block's parse, values and all")


if __name__ == "__main__":
    unittest.main()
