#!/usr/bin/env python3
"""The lazy assembly index's materialized-atom LRU holds each turn's atom list WEAKLY, and a dropped assembly entry releases
its index's entries at once (measured 2026-09-15). The LRU mapped (id(atoms), row) to (atoms, row): a strong reference to
the LazyAtoms, and through it to the LazyIndex, its rows and its document, for every materialized atom, evicted only past
the cap; every restore mints a new index, so 262 restores over 22 sessions sat the LRU exactly at its cap of 1,026,886
entries holding mostly superseded generations (about 1.2 GiB at 1.3 to 2.1 KB an atom), and live atoms evicted by stale
ones were rebuilt (7.4 M row decodes). Pinned here: a tree nobody holds is collected with its index and its entries expire
at the next pass over the cap; an assembly entry that is dropped or replaced (the whole parse's put over a full cache, the
demotion, the refused row) releases its index's entries and the current index's memo is untouched; a retained old view
rebuilds a released slot through its own index, registers again, and takes nothing with it when it goes; the eviction pass
tells a live entry (evictions) from a collected list's (expired) and touches no live slot for the latter; the LRU touch
still orders evictions; an atom handed out is a fixed value across an eviction and a release; two threads building one
slot share one object and one entry; a dead entry under a reused id is absent to the new list. Synthetic documents only."""
import copy
import gc
import json
import os
import sys
import threading
import unittest
import weakref
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import test_asm_checkpoint as T                                   # noqa: E402  the stage 4a harness: the state root is floored
em, G = T.em, T.G                                                 #             before the event model loads, no host is started
SID = T.SID


def _syn_rows(n, tag):
    """n synthesized atom rows (a `syn` row needs no record): the index builds each as its scalars plus its inline message."""
    return [json.dumps({"syn": True, "s": {"uuid": "11111111-2222-3333-4444-%04x%08d" % (ord(tag[0]), k), "type": "assistant",
                                           "t": 1000 + k, "author": "impl"},
                        "m": {"role": "assistant", "content": [{"type": "text", "text": "row %d of %s" % (k, tag)}]}}) for k in range(n)]


def _mint(n, tag):
    """A real LazyIndex over n synthetic rows and one LazyAtoms over all of them, the way a restore mints a pre-cut turn's."""
    ix = em.LazyIndex({"atoms": _syn_rows(n, tag), "records": [], "fsids": []}, SID, "/TESTDIR/%s.jsonl" % tag)
    return ix, em.LazyAtoms(ix, range(n))


def _entries(list_id=None):
    """(live, dead) over the LRU's entries, all of them or those keyed under one list's id."""
    with em._MAT_LOCK:
        ents = [(k, v) for k, v in em._MAT_LRU.items() if list_id is None or k[0] == list_id]
    def alive(v):                                       # (ref, row) on the branch; (list, row) on a source that held the list
        head = v[0]                                     #  strongly, so the module reds there on the retention assertions
        return (head() if isinstance(head, weakref.ref) else head) is not None
    live = sum(1 for _, v in ents if alive(v))
    return live, len(ents) - live


def _built(la):
    """Which slots of la hold a built atom, read through the list's storage (no build)."""
    return [i for i in range(len(la)) if list.__getitem__(la, i) is not em._UNMAT]


def _reset():
    with em._MAT_LOCK:                                             # the LRU and its counters are process-wide: per test
        em._MAT_LRU.clear()
        em._ASM_INDEX_STATS.update(materialized=0, materializedBy={}, materializedByStage={}, resident=0, evictions=0,
                                   released=0, expired=0)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self._cap = em._MAT_CAP
        _reset()

    def tearDown(self):
        em._MAT_CAP = self._cap
        _reset()


class Retention(Synthetic):
    def test_a_tree_nobody_holds_is_collected_and_its_entries_expire_at_the_next_pass(self):
        """Red on main: the LRU's strong reference pinned the list, and the index behind it, past the last consumer."""
        k = 5
        ix, la = _mint(k, "a")
        held = [la[i] for i in range(k)]
        self.assertEqual(_entries(id(la)), (k, 0))
        wi, wl = weakref.ref(ix), weakref.ref(la)
        del ix, la
        gc.collect()
        self.assertIsNone(wl(), "the LRU held the list strongly")
        self.assertIsNone(wi(), "...and the index, its rows and its document behind it")
        self.assertEqual(_entries(), (0, k), "its entries stand dead until a pass reaches them")
        self.assertEqual(em.asm_index_stats()["resident"], k, "resident counts them until they expire")
        self.assertEqual([a["t"] for a in held], [1000 + i for i in range(k)], "the atoms a consumer holds are values, whole")
        em._MAT_CAP = 1                                            # the next pass over the cap: a live list builds one atom
        ix2, la2 = _mint(1, "b")
        la2[0]
        st = em.asm_index_stats()
        self.assertEqual(_entries(), (1, 0), "the dead entries left from the old end, the live one stands")
        self.assertEqual((st["expired"], st["evictions"], st["resident"]), (k, 0, 1))

    def test_release_pops_the_indexs_entries_and_a_second_release_is_a_no_op(self):
        ix, la = _mint(4, "a")
        for i in range(3):
            la[i]
        r0 = em.asm_index_stats()["resident"]
        self.assertEqual(ix.release(), 3)
        st = em.asm_index_stats()
        self.assertEqual((st["released"], st["resident"]), (3, r0 - 3))
        self.assertEqual(_entries(id(la)), (0, 0)); self.assertEqual(_built(la), [])
        self.assertEqual(ix.release(), 0); self.assertEqual(em.asm_index_stats()["released"], 3)
        em._asm_release(None); em._asm_release({"atoms": []})    # no entry, a whole parse's entry: nothing to release


class Accounting(Synthetic):
    def test_live_entries_are_evicted_and_collected_ones_expire_touching_no_live_slot(self):
        em._MAT_CAP = 4
        ixb, lb = _mint(2, "b")
        lb[0]; lb[1]                                               # B's two entries first: the old end
        ixa, la = _mint(6, "a")
        la[0]; la[1]                                               # then A's two: the LRU is exactly at its cap
        self.assertEqual(em.asm_index_stats()["resident"], 4)
        del ixb, lb
        gc.collect()
        self.assertEqual(_entries(), (2, 2), "B's entries stand dead at the old end")
        la[2]                                                      # one over the cap: the oldest, B's dead entry, expires
        st = em.asm_index_stats()
        self.assertEqual((st["expired"], st["evictions"]), (1, 0))
        self.assertEqual(_built(la), [0, 1, 2], "no slot of the live list is touched for a dead entry")
        la[3]
        st = em.asm_index_stats()
        self.assertEqual((st["expired"], st["evictions"]), (2, 0)); self.assertEqual(_entries(), (4, 0))
        la[4]                                                      # the oldest is now A's own slot 0: a live eviction
        st = em.asm_index_stats()
        self.assertEqual((st["expired"], st["evictions"], st["resident"]), (2, 1, 4))
        self.assertIs(list.__getitem__(la, 0), em._UNMAT, "the evicted slot reads the placeholder through the storage")
        self.assertEqual(_built(la), [1, 2, 3, 4])
        la[1]                                                      # the LRU touch: slot 1 is young again
        la[5]                                                      # ...so the eviction takes slot 2, not 1
        self.assertEqual(_built(la), [1, 3, 4, 5]); self.assertEqual(em.asm_index_stats()["evictions"], 2)


class Values(Synthetic):
    def test_an_atom_handed_out_is_unchanged_by_an_eviction_and_by_a_release(self):
        em._MAT_CAP = 2
        ix, la = _mint(3, "a")
        a0 = la[0]; snap0 = copy.deepcopy(a0)
        la[1]; la[2]                                               # evicts slot 0
        self.assertIs(list.__getitem__(la, 0), em._UNMAT); self.assertEqual(a0, snap0)
        a1 = la[1]; snap1 = copy.deepcopy(a1)
        self.assertEqual(ix.release(), 2)
        self.assertEqual(a1, snap1, "release resets the slot, never the atom a consumer holds")
        self.assertEqual(_entries(id(la)), (0, 0))
        again = la[1]
        self.assertEqual(again, snap1, "a rebuilt atom equals the first build"); self.assertIsNot(again, a1)
        self.assertEqual(_entries(id(la)), (1, 0), "...and registers again under weak ownership")

    def test_two_threads_building_one_slot_share_one_object_and_one_entry(self):
        ix, la = _mint(1, "a")
        real, gate = ix.build, threading.Barrier(2)
        def slow(k):
            gate.wait(5)                                           # both threads inside the build before either takes the lock
            return real(k)
        ix.build = slow
        out = [None, None]
        ths = [threading.Thread(target=lambda n=n: out.__setitem__(n, la._at(0))) for n in range(2)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(10)
        self.assertIsNotNone(out[0]); self.assertIs(out[0], out[1]); self.assertIs(list.__getitem__(la, 0), out[0])
        self.assertEqual(_entries(id(la)), (1, 0)); self.assertEqual(em.asm_index_stats()["materialized"], 1)


class IdReuse(Synthetic):
    def test_a_dead_entry_under_a_reused_id_is_absent_to_the_new_list(self):
        ix, la = _mint(2, "a")
        gx, gone = _mint(1, "g")
        dead = weakref.ref(gone)
        del gx, gone
        gc.collect()
        self.assertIsNone(dead())
        with em._MAT_LOCK:                                         # a collected list's entries under the NEW list's key, as an id
            em._MAT_LRU[(id(la), 0)] = (dead, 0)                   #  recycled after a collection leaves them
            em._MAT_LRU[(id(la), 1)] = (dead, 1)
        a0 = la[0]                                                 # the build road: the stale entry is absent, la registers itself
        ent = em._MAT_LRU[(id(la), 0)]
        self.assertIs(ent[0](), la)
        self.assertEqual(next(reversed(em._MAT_LRU)), (id(la), 0), "at the young end, not in the stale entry's old position")
        self.assertEqual(em.asm_index_stats()["expired"], 1)
        a1 = la[1]                                                 # a second build over a stale entry
        self.assertEqual(em.asm_index_stats()["expired"], 2)
        with em._MAT_LOCK:
            em._MAT_LRU[(id(la), 1)] = (dead, 1)                   # a stale entry over a BUILT slot: the hit road's defensive check
        self.assertIs(la[1], a1)
        self.assertIs(em._MAT_LRU[(id(la), 1)][0](), la, "the hit road registers its own live object and touches nothing foreign")
        self.assertEqual(em.asm_index_stats()["expired"], 3)
        self.assertIs(la[0], a0); self.assertEqual(_entries(id(la)), (2, 0))


class DroppedEntries(T.Harness):
    """The drop sites, driven through the assembly itself: a restored leaf's entry (its index in `index`) leaves the cache and
    its index's entries leave the LRU."""

    def setUp(self):
        super().setUp()
        self._cap, self._max = em._MAT_CAP, em._ASM_CACHE_MAX
        _reset()

    def tearDown(self):
        em._MAT_CAP, em._ASM_CACHE_MAX = self._cap, self._max
        _reset()
        super().tearDown()

    def documented(self, name, tag):
        """A compacting scenario written and parsed whole, its document written: ready for a restore."""
        records, sent = G.SINGLE_FILE[name]
        path = self.write("%s-%s" % (tag, name), records(), sent=sent)
        self.parse(path)
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        return path

    def restore(self, path):
        modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"])
        return tree

    @staticmethod
    def key(path):
        return (os.path.realpath(path), SID, False)

    def test_the_caches_eviction_of_its_oldest_entry_releases_that_index_and_leaves_the_current_one_alone(self):
        px = self.documented("compaction_atom", "x")
        py = self.documented("manual_compact_detached", "y")
        self.fresh()
        tx, ty = self.restore(px), self.restore(py)                # X's entry is the older of the two
        lx, ly = tx["turns"][0]["atoms"], ty["turns"][0]["atoms"]
        self.assertIsInstance(lx, em.LazyAtoms); self.assertIsInstance(ly, em.LazyAtoms)
        kx, ky = len(lx), len(ly)
        firsts = [dict(a) for a in lx]; [dict(a) for a in ly]      # every atom of both first turns built
        ex, ey = em._ASM_CACHE[self.key(px)], em._ASM_CACHE[self.key(py)]
        self.assertIs(ex["index"], lx._index, "the entry names the index its pre-turns build through")
        self.assertIs(ey["index"], ly._index)
        self.assertEqual(_entries(id(lx)), (kx, 0)); self.assertEqual(_entries(id(ly)), (ky, 0))
        r0 = em.asm_index_stats()["resident"]
        em._ASM_CACHE_MAX = 2                                      # the cache full: a third leaf's whole parse evicts its oldest
        records, sent = G.SINGLE_FILE["author_kinds"]
        pz = self.write("z-author_kinds", records(), sent=sent)
        modes = []
        self.parse(pz, modes)
        self.assertEqual(modes, ["full"])
        self.assertNotIn(self.key(px), em._ASM_CACHE); self.assertIn(self.key(py), em._ASM_CACHE)
        st = em.asm_index_stats()
        self.assertEqual(st["released"], kx, "the evicted entry's index gave every built atom back")
        self.assertEqual(st["resident"], r0 - kx)
        self.assertEqual(_entries(id(lx)), (0, 0)); self.assertEqual(_built(lx), [])
        self.assertEqual(_entries(id(ly)), (ky, 0), "the current index's memo is untouched")
        self.assertEqual(_built(ly), list(range(ky)))
        self.assertEqual([dict(a) for a in lx], firsts, "the retained old view rebuilds through its own index, equal to before")
        self.assertEqual(_entries(id(lx)), (kx, 0), "...and registers again")

    def test_a_demoted_entry_releases_its_index_and_a_retained_view_takes_nothing_with_it(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        recs = records()
        path = self.write("compaction_atom", recs)
        self.parse(path); self.assertTrue(self.doc(path))
        self.fresh()
        tree = self.restore(path)
        la = tree["turns"][0]["atoms"]
        k = len(la); self.assertGreater(k, 1)
        first = [dict(a) for a in la]
        ix = em._ASM_CACHE[self.key(path)]["index"]
        self.assertIs(ix, la._index)
        wi, wl = weakref.ref(ix), weakref.ref(la)
        t1 = self.after(recs, 100)                                 # a compaction landing in the tail after the document: the
        with open(path, "a") as f:                                 #  entry is stale and demotes to a whole parse
            f.write(json.dumps(G.compact_line(t1, "b_new", recs[-1].get("uuid"))) + "\n")
            f.write(json.dumps(G.compact_summary_line(t1 + 1, "s_new", "b_new")) + "\n")
        modes = []
        self.parse(path, modes)
        self.assertEqual(modes, ["full"])
        st = em.asm_index_stats()
        self.assertEqual(st["released"], k, "the demoted entry's index gave its atoms back")
        self.assertEqual(_entries(id(la)), (0, 0)); self.assertEqual(_built(la), [])
        self.assertEqual(dict(la[0]), first[0], "the retained view rebuilds a released slot, equal to the first build")
        self.assertIs(em._MAT_LRU[(id(la), 0)][0](), la, "...registered again under weak ownership")
        self.assertEqual(_entries(), (1, 0), "the whole parse's tree has no lazy atoms: that entry is the one")
        del tree, la, ix
        self._trees.clear()
        gc.collect()
        self.assertIsNone(wl(), "the view dropped, nothing of the old generation stays alive")
        self.assertIsNone(wi())
        self.assertEqual(_entries(), (0, 1), "its entry stands dead...")
        em._MAT_CAP = 1
        ix2, la2 = _mint(1, "b")
        la2[0]
        st = em.asm_index_stats()
        self.assertEqual(_entries(), (1, 0), "...and expires at the next pass")
        self.assertEqual((st["expired"], st["evictions"]), (1, 0))

    def test_a_refused_row_drops_the_entry_and_releases_its_index(self):
        path = self.documented("compaction_atom", "r")
        self.fresh()
        tree = self.restore(path)
        la = tree["turns"][0]["atoms"]
        k = len(la); self.assertGreater(k, 1)
        for i in range(1, k):
            la[i]
        self.assertEqual(_entries(id(la)), (k - 1, 0))
        la._index.rowb[la._rows[0]] = b"{not json"
        with self.assertRaises(em.LazyIndexError):
            la[0]
        self.assertNotIn(self.key(path), em._ASM_CACHE, "the document's entry is dropped, as before")
        st = em.asm_index_stats()
        self.assertEqual(st["released"], k - 1, "...and its index's built atoms left the LRU with it")
        self.assertEqual(_entries(id(la)), (0, 0)); self.assertEqual(_built(la), [])


if __name__ == "__main__":
    unittest.main()
