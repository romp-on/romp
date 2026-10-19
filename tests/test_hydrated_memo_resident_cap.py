#!/usr/bin/env python3
"""The hydrated-body memo's cap counts RESIDENT bytes, and its evictions cost the same at any size (2026-10-19,
kernel/event_model.py _HYDRATED, _hydrated_weight, _hydrated_put).

The memo keeps every record a lazy atom's body was read from, least recently used first out, under a byte cap. Until
2026-10-19 each entry was weighed at its record's length on disk against MemTotal / 32, and a decoded record holds more than
its disk bytes (1.5 to 1.8 times on sampled real records; about 4.6 times for the synthetic ones here): a kernel 23 hours up
held 2.2 million bodies counted at 7.75 GB and weighing 12 to 14 GB, and never evicted one. Each entry is now weighed by its
decoded size and the default fraction is MemTotal / 64. And the memo was a plain dict evicted from its front: a plain dict
leaves an empty slot for each key deleted there, which the next eviction's next(iter()) steps over, so an eviction cost time
in proportion to the evictions since the dict last resized. It is an OrderedDict now.

Pinned here, against a second instrument that shares none of the weighing's arithmetic (tracemalloc, which counts the
allocator's blocks): an entry's weight is not below the memory its record holds, where the disk length is (a); a synthetic
workload of many restored sessions, hydrated past the cap, leaves the memo holding no more resident memory than the cap,
evicting oldest first, with hits counted on a second read of the same bodies (b); /perf heap.hydrated reads the weight, the
cap and the counters (c); an insert that evicts costs about the same in a memo of 100,000 entries as in one of 1,000 (d);
a record put twice is counted once (e). Synthetic transcripts only: invented prompts, placeholder session ids."""
import gc
import json
import os
import statistics
import sys
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_asm_checkpoint as T                                   # noqa: E402  the stage 4a harness, its event model and state root
from test_event_model_golden import iso                          # noqa: E402

# the harness minted its own state root at import (outside conftest's belt): session hosts off in it, as the testing rule asks
Path(os.environ["XDG_STATE_HOME"], "romp").mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off")

em = T.em
SID = T.SID
WORDS = ("cap", "retry", "budget", "lane", "queue", "notes", "index", "route", "cache", "header", "token", "page", "limit",
         "shard", "batch", "flush", "merge", "parse", "fold", "window")


def rich_transcript(t0, turns, compact_every, seed=7, tag="s"):
    """Records shaped like an agent's working transcript: a prompt, an assistant reply that calls two tools with small
    structured inputs and carries usage metadata, the tool results (a content list and a toolUseResult), and a closing
    reply; a compaction every `compact_every` turns so a restore has a pre-cut part. Record ids carry `tag`, so sessions
    never share a memo key. All text invented."""
    import random
    rnd = random.Random(seed)
    words = lambda n: " ".join(rnd.choice(WORDS) for _ in range(n))
    recs, parent, t = [], None, t0
    for k in range(turns):
        if k and k % compact_every == 0:
            b, s = "%s-b%d" % (tag, k), "%s-s%d" % (tag, k)
            recs.append({"type": "system", "subtype": "compact_boundary", "uuid": b, "parentUuid": None, "logicalParentUuid": parent,
                         "timestamp": iso(t), "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
            recs.append({"type": "user", "uuid": s, "parentUuid": b, "timestamp": iso(t + 1), "isCompactSummary": True,
                         "message": {"role": "user", "content": "summary so far: " + words(60)}})
            parent = s; t += 2
        u, a1, r1, a2 = ("%s-%s%d" % (tag, c, k) for c in "uarz")
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed",
                     "message": {"role": "user", "content": words(12) + " %d" % k}})
        tools = [{"type": "tool_use", "id": "tu_%s_%d_%d" % (tag, k, i), "name": ("Read", "Bash")[i],
                  "input": {"file_path": "/w/notes-api/src/%s_%d.py" % (rnd.choice(WORDS), i), "offset": i * 40, "limit": 40,
                            "description": words(4)}} for i in range(2)]
        usage = {"input_tokens": 12 + k, "output_tokens": 300, "cache_read_input_tokens": 40000 + k,
                 "cache_creation_input_tokens": 512, "service_tier": "standard"}
        recs.append({"type": "assistant", "uuid": a1, "parentUuid": u, "timestamp": iso(t + 5),
                     "message": {"id": "msg_%06d" % k, "model": "claude-test", "role": "assistant", "type": "message",
                                 "content": [{"type": "text", "text": words(8)}] + tools, "stop_reason": "tool_use",
                                 "usage": usage}})
        recs.append({"type": "user", "uuid": r1, "parentUuid": a1, "timestamp": iso(t + 6),
                     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tl["id"],
                                                              "content": [{"type": "text", "text": words(6)}]} for tl in tools]},
                     "toolUseResult": {"stdout": words(6), "stderr": "", "interrupted": False, "isImage": False}})
        recs.append({"type": "assistant", "uuid": a2, "parentUuid": r1, "timestamp": iso(t + 20),
                     "message": {"id": "msg_%06db" % k, "model": "claude-test", "role": "assistant", "type": "message",
                                 "content": [{"type": "text", "text": words(10)}], "stop_reason": "end_turn", "usage": dict(usage)}})
        parent = a2; t += 60
    return recs


def _traced():
    gc.collect()
    return tracemalloc.get_traced_memory()[0]


class EntryWeight(unittest.TestCase):
    """(a) An entry's weight is what its record holds in memory, not its length on disk."""

    def test_the_weight_is_not_below_the_memory_the_entry_holds_and_the_disk_length_is(self):
        lines = [json.dumps(r) for r in rich_transcript(T.NOW - 86400, turns=60, compact_every=1000)
                 if r.get("type") in ("user", "assistant")]
        disk = sum(len(ln.encode("utf-8")) + 1 for ln in lines)
        weigh = getattr(em, "_hydrated_weight", None)             # read loosely: a build before 2026-10-19 weighed an entry at
        #                                                            its disk length, and this test reds on that, not on the name
        tracemalloc.start()
        try:
            base = _traced()
            held = []
            for ln in lines:
                rec = json.loads(ln)                              # the memo keeps the decoded record whole, as hydrate() does
                held.append((rec, weigh(rec) if weigh is not None else len(ln.encode("utf-8")) + 1))
                del rec
            resident = _traced() - base
        finally:
            tracemalloc.stop()
        weight = sum(e[1] for e in held)
        self.assertGreater(resident, 1.2 * disk,
                           "the workload decodes to more than its disk bytes, as real records do (resident %d, disk %d)"
                           % (resident, disk))
        self.assertGreaterEqual(weight, 0.9 * resident,
                                "each entry weighs what it holds (weight %d, resident %d by tracemalloc, disk %d)"
                                % (weight, resident, disk))
        self.assertLessEqual(weight, 2.0 * resident, "an estimate, not a multiple: weight %d, resident %d" % (weight, resident))


class CapBoundsResidentMemory(T.Harness):
    """(b) Many restored sessions hydrated past the cap: the memo holds no more resident memory than the cap."""

    N = 6

    def _sessions(self):
        paths = []
        for i in range(self.N):
            path = self.write("hyd-%d" % i, rich_transcript(T.NOW - 86400 * (i + 2), turns=40, compact_every=20, seed=i,
                                                            tag="h%d" % i))
            self.parse(path)
            self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
            paths.append(path)
        return paths

    def _hydrate_all(self, paths):
        """Restore and hydrate every session's pre-cut atoms, dropping each tree after, as a judge pass walking them would."""
        n = 0
        for p in paths:
            modes = []
            tree = self.parse(p, modes)
            self.assertEqual(modes, ["restore"], "parsed from its document")
            n += em.hydrate(tree, SID)
            del tree
            self._trees.clear()
        return n

    def _drop_other_holders(self):
        """Everything but the memo lets go of the bodies: the assembly entries (their indexes released), the record cache."""
        with em._ASM_LOCK:
            entries = list(em._ASM_CACHE.values())
            em._ASM_CACHE.clear()
        for e in entries:
            em._asm_release(e)
        del entries
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        em._TRAILING_CACHE.clear()

    def test_a_workload_past_the_cap_leaves_the_memo_under_it_in_resident_memory(self):
        paths = self._sessions()
        self.fresh()
        with mock.patch.object(em, "_HYDRATED_CAP", 1 << 40):
            self._hydrate_all(paths)                              # the whole workload's weight, uncapped
        with em._ASM_CKPT_LOCK:
            whole, n_whole = em._HYDRATED_BYTES[0], len(em._HYDRATED)
            biggest = max(e[1] for e in em._HYDRATED.values())
        self.assertGreater(n_whole, 600, "a workload of several hundred bodies")
        cap = whole // 3                                          # the workload is three times the cap
        self.fresh()
        stats0 = dict(getattr(em, "_HYDRATED_STATS", None) or {})   # read loosely: the bound below is what a build before
        #                                                              2026-10-19 fails on, not a missing counter
        tracemalloc.start()
        try:
            with mock.patch.object(em, "_HYDRATED_CAP", cap):
                self._hydrate_all(paths)
                with em._ASM_CKPT_LOCK:
                    counted, kept = em._HYDRATED_BYTES[0], len(em._HYDRATED)
                self.assertLessEqual(counted, cap, "the counted weight stays under the cap")
                self.assertLess(kept, n_whole, "older bodies were evicted")
                self._drop_other_holders()
                before = _traced()
                with em._ASM_CKPT_LOCK:
                    em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
                freed = before - _traced()
        finally:
            tracemalloc.stop()
        self.assertLessEqual(freed, int(1.1 * cap) + biggest,
                             "the memo held no more resident memory than its cap: freed %d on clearing it, cap %d (the whole "
                             "workload weighs %d)" % (freed, cap, whole))
        self.assertGreater(freed, cap // 4, "the measurement saw the memo's bodies (freed %d)" % freed)
        st = em._HYDRATED_STATS
        self.assertGreater(st["evictions"] - stats0["evictions"], 0, "the evictions are counted")
        self.assertGreater(st["evictedBytes"] - stats0["evictedBytes"], 0)

    def test_a_second_read_of_the_same_bodies_is_served_from_the_memo_and_counted(self):
        paths = self._sessions()[:2]
        self.fresh()
        self._hydrate_all(paths[:1])
        h0, read0 = em._HYDRATED_STATS["hits"], em._ASM_CKPT_STATS["hydratedAtoms"]
        modes = []
        tree = self.parse(paths[0], modes)                        # the entry is warm: served, its atoms lazy again or not
        lazy = [a for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None]
        if not lazy:                                              # a served entry kept its bodies: restore cold to get lazy atoms
            self.fresh_keep_memo()
            tree = self.parse(paths[0], modes)
            lazy = [a for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None]
        self.assertTrue(lazy, "a restored tree's pre-cut atoms are lazy")
        self.assertEqual(em.hydrate(lazy), len(lazy))
        self.assertEqual(em._ASM_CKPT_STATS["hydratedAtoms"], read0, "no body read from disk again")
        self.assertGreaterEqual(em._HYDRATED_STATS["hits"] - h0, len(lazy), "each one served from the memo, counted")

    def fresh_keep_memo(self):
        """A restart's in-memory side but for the memo: the next parse restores, and its lazy atoms meet a warm memo."""
        with em._ASM_CKPT_LOCK:
            saved, nbytes = dict(em._HYDRATED), em._HYDRATED_BYTES[0]
        self.fresh()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.update(saved); em._HYDRATED_BYTES[0] = nbytes


class DefaultCap(unittest.TestCase):
    """The default is a sixty-fourth of physical memory with a 1 GiB floor, counted in resident bytes; the setting overrides."""

    def test_the_default_fraction_and_floor(self):
        src = open(em.__file__).read()
        self.assertIn('_HYDRATED_CAP = _env_or("ROMP_HYDRATED_CAP_MB", max(1024 ** 3, _machine_memory_bytes() // 64), 1024 * 1024)', src)
        mem = em._machine_memory_bytes()
        if os.environ.get("ROMP_HYDRATED_CAP_MB") is None:
            self.assertEqual(em._HYDRATED_CAP, max(1024 ** 3, mem // 64))
        with mock.patch.dict(os.environ, {"ROMP_HYDRATED_CAP_MB": "2048"}):
            self.assertEqual(em._env_or("ROMP_HYDRATED_CAP_MB", 1, 1024 * 1024), 2048 * 1024 * 1024, "the setting raises it")


class PerfReadsTheMemo(unittest.TestCase):
    """(c) /perf heap.hydrated: the entries, their weight, the cap and the counters."""

    def test_the_gauge_carries_the_weight_cap_and_counters(self):
        km = T.kernel_module()
        h = km._PerfStats().snapshot()["heap"]["hydrated"]
        self.assertEqual(set(h), {"entries", "bytes", "capBytes", "hits", "evictions", "evictedBytes"})
        with em._ASM_CKPT_LOCK:
            self.assertEqual((h["entries"], h["bytes"], h["capBytes"]), (len(em._HYDRATED), em._HYDRATED_BYTES[0], em._HYDRATED_CAP))
        self.assertEqual((h["hits"], h["evictions"], h["evictedBytes"]),
                         (em._HYDRATED_STATS["hits"], em._HYDRATED_STATS["evictions"], em._HYDRATED_STATS["evictedBytes"]))


def _put_before_the_change(u, ent):
    """The insert hydrate() ran inline before 2026-10-19, on the module's own memo: what (d) and (e) run on a build that has
    no _hydrated_put, so a copy of this file at an older base reds on the behavior, not on a missing name."""
    em._HYDRATED[u] = ent; em._HYDRATED_BYTES[0] += ent[1]
    while em._HYDRATED_BYTES[0] > em._HYDRATED_CAP and em._HYDRATED:
        old = em._HYDRATED.pop(next(iter(em._HYDRATED)))
        em._HYDRATED_BYTES[0] -= old[1]


class SecondPutReplacesTheFirst(unittest.TestCase):
    """(e) A record put twice is counted once."""

    def test_a_second_put_of_one_uuid_replaces_the_first_and_its_weight(self):
        """Two readers of one record (a resumed session's file repeats its parent's record ids, and the two files read under
        different stripes): the second put replaces the first entry, takes the LRU tail, and the first one's weight leaves
        with it. Before 2026-10-19 the second put overwrote the entry in place, kept its old position, and the first weight
        stayed counted for good, so the memo's sum drifted up and evicted bodies it did not need to."""
        put = getattr(em, "_hydrated_put", None) or _put_before_the_change
        with mock.patch.object(em, "_HYDRATED", type(em._HYDRATED)()), \
             mock.patch.object(em, "_HYDRATED_BYTES", [0]), \
             mock.patch.object(em, "_HYDRATED_CAP", 1 << 30), \
             mock.patch.object(em, "_HYDRATED_STATS", {"hits": 0, "evictions": 0, "evictedBytes": 0}, create=True):
            put("11111111-2222-3333-4444-000000000001", ({"n": 1}, 700))
            put("11111111-2222-3333-4444-000000000002", ({"n": 2}, 300))
            put("11111111-2222-3333-4444-000000000001", ({"n": 1}, 500))   # the same record, read again
            self.assertEqual(em._HYDRATED_BYTES[0], 800, "the summed weight counts each body once")
            self.assertEqual(list(em._HYDRATED), ["11111111-2222-3333-4444-000000000002",
                                                  "11111111-2222-3333-4444-000000000001"], "the newer put is the LRU tail")


class EvictionCostIsFlat(unittest.TestCase):
    """(d) Once the memo is full every insert evicts the oldest entry, and that must cost about the same whatever the memo's
    size. On a plain dict it did not: 1.4 microseconds an insert at 1,000 entries and 48 at 100,000 in the lab, paid with the
    memo's lock and the interpreter lock held, where an OrderedDict takes 0.8 and 1.2 (2026-10-19). The memo's size is set by
    its cap, about a million entries for a 4 GB cap, so the plain dict's cost grew with the machine."""

    W = 100                                                       # every synthetic entry weighs this much: the cap holds size entries
    BATCH = 2000

    def _median_insert(self, size, inserts):
        """Fill a fresh memo of the module's own type to `size` entries under a cap that holds exactly that many, then time
        `inserts` more, each evicting one, in batches; the median batch's time per insert, so a load spike on a shared machine
        moves a few batches and not the answer."""
        put = getattr(em, "_hydrated_put", None) or _put_before_the_change
        ent = ({}, self.W)
        with mock.patch.object(em, "_HYDRATED", type(em._HYDRATED)()), \
             mock.patch.object(em, "_HYDRATED_BYTES", [0]), \
             mock.patch.object(em, "_HYDRATED_CAP", size * self.W), \
             mock.patch.object(em, "_HYDRATED_STATS", {"hits": 0, "evictions": 0, "evictedBytes": 0}, create=True):
            for i in range(size):
                put("fill-%d" % i, ent)
            self.assertEqual(len(em._HYDRATED), size)
            per, done = [], 0
            while done < inserts:
                keys = ["ins-%d" % (done + j) for j in range(self.BATCH)]
                t0 = time.perf_counter()
                for k in keys:
                    put(k, ent)
                per.append((time.perf_counter() - t0) / self.BATCH)
                done += self.BATCH
            self.assertEqual(len(em._HYDRATED), size, "each insert evicted one entry")
            self.assertEqual(next(iter(em._HYDRATED)), "ins-%d" % (done - size), "the oldest went first")
        return statistics.median(per)

    def test_an_eviction_costs_the_same_in_a_large_memo_as_in_a_small_one(self):
        small = self._median_insert(1000, 40_000)
        large = self._median_insert(100_000, 250_000)             # a full turn of a plain dict's resize cycle at this size
        self.assertLessEqual(large, 4 * small + 1e-6,
                             "an insert that evicts costs %.2f us at 100,000 entries against %.2f us at 1,000: the eviction "
                             "scans past deleted slots" % (large * 1e6, small * 1e6))


if __name__ == "__main__":
    unittest.main()
