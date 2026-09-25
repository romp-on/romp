#!/usr/bin/env python3
"""T398 (2026-09-12): a boot parsed two documented live leaves whole with no fallback counted, and nothing on GET /perf named the
road the parse took. The assembly's road counters (serve, fold, restore, full with its reason, bypass, the g:<reason> demotions)
ride asmCheckpoint.parse and the boot-health row, beside asmCheckpoint.removed, the document files removed per reason."""
import json
import os
import sys
import unittest
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import em, G, SID, NOW, Harness, kernel_module, _strip   # noqa: E402


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
        self.assertEqual(parse.get("restore:afterDemote"), 1, "the demoted entry falls to the restore road (T402): %s" % parse)
        self.assertEqual(parse.get("full:demoted", 0), 0, "%s" % parse)
        row = self._boot_row_parse()
        self.assertEqual((row.get("g:rewrite"), row.get("restore:afterDemote")), (1, 1), "the boot row's values: %r" % row)

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

    def test_a_document_write_clears_the_refusal_slot(self):
        """Follow-up, low 1: a refusal recorded by one parse was never cleared at a document write and the restore road returned
        before the pop, so after a fresh document was written a later parse with NO document standing booked full:refused."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("slot", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        em._asm_ckpt_file(path).write_bytes(b"not a document")
        self.fresh(); self._reset(); self.parse(path)
        self.assertEqual(em.asm_checkpoint_stats()["parse"].get("full:refused"), 1)
        em._asm_ckpt_note(path, "corrupt")                                  # a JUDGE read's refusal, recorded in the slot
        self.assertTrue(self.doc(path), "a fresh document written")          # the write clears the slot
        self.fresh(); modes = []; self.parse(path, modes); self.assertEqual(modes, ["restore"])   # the restore road: no pop
        cp = em._asm_ckpt_file(path); cp.unlink(); cp.with_name(cp.name + ".meta").unlink(missing_ok=True)
        self.fresh(); self._reset(); self.parse(path)
        parse = em.asm_checkpoint_stats()["parse"]
        self.assertEqual((parse.get("full:noDocument"), parse.get("full:refused", 0)), (1, 0), "no document standing: noDocument: %s" % parse)

    def test_every_road_counter_write_goes_through_the_locked_helper(self):
        """Follow-up, low 3: the lock covered two of ten writes; every increment goes through _asm_stat under _ASM_CKPT_LOCK."""
        import inspect, re
        src = inspect.getsource(em)
        bare = [l.strip() for l in src.splitlines() if "_ASM_STATS[" in l and ("+=" in l or "= _ASM_STATS.get(" in l)
                and "(key, 0) + n" not in l]                                # the helper's own line
        self.assertEqual(bare, [], "bare writes: %r" % bare)
        self.assertGreaterEqual(len(re.findall(r"_asm_stat\(", src)), 10)
        self.assertIn("with _ASM_CKPT_LOCK:", inspect.getsource(em._asm_stat))

    def test_the_boot_sweep_counts_the_documents_it_removes(self):
        """Follow-up, low 6: `removed["sweep"]` had no test."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("swept", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        em._ASM_CKPT_STATS["removed"] = {}
        os.unlink(path)                                                     # the transcript vanishes: the sweep removes its document
        em.checkpoint_sweep()
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {"sweep": 1})
        self.assertFalse(em._asm_ckpt_file(path).exists())

    SIBLING = "cccccccc-0000-0000-0000-000000000000"

    def _five_shape_file(self, name, resume=False, tip_fork=False, abandoned_compaction=False):
        """Three turns, an attached compaction, two turns: the document's pre-cut records u1 a1 u2 a2 u3 a3 (the spine tip a3).
        With `resume`, a sibling file beside the leaf holds x1 x2 and u1 parents x2 (a two-file lineage). With `tip_fork`, the
        tip a3 has a second, abandoned pre-cut child a_d before the compaction (a rollback onto a3, then the compaction whose
        logical parent is a3): whether a3 is a fork is decided by the tail, so no tail may parent it (round four, medium 1)."""
        t0 = NOW
        recs = [G.uline(t0, "first ask", "u1", "x2" if resume else None), G.aline(t0 + 10, "first reply", "a1", "u1", stop="end_turn"),
                G.uline(t0 + 20, "second ask", "u2", "a1"), G.aline(t0 + 30, "second reply", "a2", "u2", stop="end_turn"),
                G.uline(t0 + 40, "third ask", "u3", "a2"), G.aline(t0 + 50, "third reply", "a3", "u3", stop="end_turn")]
        if tip_fork:
            recs.append(G.aline(t0 + 55, "an abandoned reply", "a_d", "a3", stop="end_turn"))
        if abandoned_compaction:
            # round five, medium 1: an auto-compaction at a3, abandoned (its branch rewound away), then the next compaction anchored
            # at a3 again, so the cut falls at the second boundary and the tip a3 has a pre-cut child whose ROW parent is null
            recs += [G.compact_line(t0 + 60, "b0", "a3", trigger="auto"), G.compact_summary_line(t0 + 61, "s0", "b0"),
                     G.uline(t0 + 62, "abandoned after the first compaction", "u3x", "s0"),
                     G.aline(t0 + 63, "abandoned reply", "a3x", "u3x", stop="end_turn")]
        recs += [G.compact_line(t0 + 600, "b1", "a3"), G.compact_summary_line(t0 + 601, "s1", "b1"),
                G.uline(t0 + 610, "after the compaction", "u4", "s1"), G.aline(t0 + 620, "fourth reply", "a4", "u4", stop="tool_use"),
                G.uline(t0 + 630, "then more", "u5", "a4"), G.aline(t0 + 640, "fifth reply", "a5", "u5", stop="end_turn")]
        #        the fourth turn never settles (its reply stopped for a tool whose result the file never recorded; the user typed
        #        again): since stage one b (2026-09-15) the cut is the boundary before the last SETTLED turn with a follower, and
        #        every shape below is about the compaction's cut with a3 the pre-cut tip, so the compaction's turn must be that
        #        turn: with the fourth turn settled the cut would sit before u4 and the tip would be the summary s1
        path = self.write(name, recs)
        if resume:
            sib = os.path.join(os.path.dirname(path), self.SIBLING + ".jsonl")
            with open(sib, "w") as fh:
                for r in [G.uline(t0 - 100, "sibling parent ask", "x1"), G.aline(t0 - 80, "sibling reply", "x2", "x1", stop="end_turn")]:
                    fh.write(json.dumps(r) + "\n")
            return path, t0, sib
        return path, t0

    def _parse_lineage(self, path, cands, modes=None):
        tree = em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=list(cands),
                                states=None, postal_log=[], now=NOW, asm_mode_out=modes)
        self._trees[path] = tree
        return tree

    def _cold(self, path, cands=None):
        self.fresh(); saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
        try:
            return _strip(self._parse_lineage(path, cands or [path]))
        finally:
            em._CKPT_DIR_FN = saved

    def _append(self, path, recs):
        with open(path, "a") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

    def _served(self, path, cands=None):
        """The tree the parse serves now, hydrated and stripped, with the parse counters."""
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        tree = self._parse_lineage(path, cands or [path])
        em.hydrate(tree, SID)                                               # a restored tree's bodies, so the strip can read them
        return _strip(tree), em.asm_checkpoint_stats()["parse"], em.record_cache_stats()["wholeReads"]

    # The tail shapes (appended past the document's cut) and whether the tail chains onto the document: "restore" when every
    # uuid-bearing record parents a TAIL record (the boundary read past as the tail's root) or the pre-cut spine tip when the
    # document proves it childless, "whole" otherwise (round four: a tip with a pre-cut child is decided by the tail).
    SHAPES = {
        "a_rewind_pre_interior": (lambda t0: [G.uline(t0 + 700, "a rewind before the cut", "u_rw", "a1")], "whole"),
        "b_clear_fork": (lambda t0: [G.uline(t0 + 700, "a clear fork", "u_fork", None)], "whole"),
        "c_api_error_spur_at_opener": (lambda t0: [G.api_error_line(t0 + 700, "e1", "u5"), G.uline(t0 + 710, "after the spur", "u6", "e1")], "restore"),
        "d_rewind_post_cut": (lambda t0: [G.uline(t0 + 700, "a rewind after the cut", "u_rw2", "a4")], "restore"),
        "e_rewind_spine_tip": (lambda t0: [G.uline(t0 + 700, "a rewind onto the cut", "u_rw3", "a3")], "restore"),
        #   (e: a3 has no pre-cut child here, so a first child cannot move the active branch; with a pre-cut child it refuses:
        #    the tip-fork test below)
        "p1_no_parent_key": (lambda t0: [{k: v for k, v in G.uline(t0 + 700, "no parent key", "u_nk").items() if k != "parentUuid"}], "whole"),
        # p4, r7 and q never reach the demotion leg's restore: a summary in the delta trips the gates' own summary demotion,
        # and since 2026-09-24 that demotion falls to the restore only while the tail past the cut is under the churn bound's
        # share, which this small file's tail is past (whole, restore:pastShare), and a sidechain delta that leaves the main
        # line's leaf in place folds without any demotion; the chain rule meets them at the BOOT restore (the boot leg below),
        # where p4 and r7 refuse and q restores
        "p4_summary_parented_into_the_interior": (lambda t0: [G.compact_summary_line(t0 + 700, "s9", "a1")], "summary"),
        "r7_sidechain_parented_into_the_interior": (lambda t0: [dict(G.uline(t0 + 700, "a side ask", "sc1", "a1"), isSidechain=True),
                                                                G.uline(t0 + 710, "then the main line", "u6", "a5")], "fold"),
        "r8_sidechain_last_in_file": (lambda t0: [dict(G.uline(t0 + 700, "a side ask", "sc1", "a1"), isSidechain=True)], "whole"),
        #   (r8 alone: the sidechain record is the file's last, so it is the new leaf and the gates' descent walk demotes; the
        #    chain rule then refuses its interior parent, where the round-three rule read the sidechain past and re-rooted)
        "q_sidechain_rooted_in_the_tail": (lambda t0: [dict(G.uline(t0 + 700, "a side ask", "sc1", "a5"), isSidechain=True),
                                                       dict(G.aline(t0 + 705, "side reply", "sca1", "sc1", stop="end_turn"), isSidechain=True)], "fold"),
        "f_two_step_rewind": (lambda t0: [G.uline(t0 + 700, "a rewind after the cut", "u_r1", "a4"), G.aline(t0 + 710, "its reply", "a_r1", "u_r1", stop="end_turn"),
                                          G.uline(t0 + 720, "then a rewind before the cut", "u_r2", "a2")], "whole"),
        "g_later_crossing": (lambda t0: [G.uline(t0 + 700, "chains on", "u6", "a5"), G.aline(t0 + 710, "reply", "a6", "u6", stop="end_turn"),
                                         G.uline(t0 + 720, "then crosses into the interior", "u7", "a1")], "whole"),
        "h_orphan_parent": (lambda t0: [G.uline(t0 + 700, "an orphan", "u_o", "00000000-no-such-record")], "whole"),
        "i_interior_api_error_alone": (lambda t0: [G.api_error_line(t0 + 700, "e2", "a1")], "whole"),
        "j_interior_api_error_with_reply": (lambda t0: [G.api_error_line(t0 + 700, "e2", "a1"), G.uline(t0 + 710, "after the spur", "u6", "e2")], "whole"),
        "k_stop_hook_summary_spur": (lambda t0: [G.stop_hook_line(t0 + 700, "sh1", "a2")], "whole"),
        "l_boundary_in_tail": (lambda t0: [G.compact_line(t0 + 700, "b2", "a5"), G.compact_summary_line(t0 + 701, "s2", "b2"),
                                           G.uline(t0 + 710, "after a second compaction", "u6", "s2")], "boundary"),
    }

    def _documented(self, name, resume=False, **shape):
        """A five-shape file with its document standing and its entry restored from it; the counters reset."""
        made = self._five_shape_file(name, resume=resume, **shape)
        path, cands = made[0], ([made[0], made[2]] if resume else [made[0]])
        self.fresh(); self._parse_lineage(path, cands); self.assertTrue(self.doc(path))
        self.fresh(); self._parse_lineage(path, cands); self._reset()          # the entry stands, restored from the document
        return made

    def _check(self, name, tree, parse, reads, road, path, cands=None, reason="descent", boot=False):
        if reason is not None:
            self.assertEqual(parse.get("g:" + reason), 1, "%s: the gates demoted the entry for %s: %s" % (name, reason, parse))
        if road == "restore":
            self.assertEqual(parse.get("restore"), 1, "%s: the restore road: %s" % (name, parse))
            self.assertEqual(parse.get("restore:afterDemote", 0), 0 if boot else 1, "%s: %s" % (name, parse))
            if not boot and reason != "rewrite":                          # a boot's parse primes the leaf's reader entry from zero
                self.assertEqual(reads, {}, "%s: no whole read" % name)   #  before the assembly (unchanged here); a rewrite IS a
                #                                                            from-zero read of the leaf
        elif road == "whole":
            self.assertEqual(parse.get("restore:chainRefused"), 1, "%s: the tail does not chain onto the document: %s" % (name, parse))
            self.assertEqual(parse.get("full:refused" if boot else "full:demoted"), 1, "%s: the whole parse: %s" % (name, parse))
        elif road == "fold":
            self.assertEqual(parse.get("fold"), 1, "%s: no gate demotes this delta: the fold road, as before: %s" % (name, parse))
        else:
            self.assertEqual(parse.get("full:demoted"), 1, "%s: the gates' own %s demotion parses whole: %s" % (name, road, parse))
            if road in ("boundary", "summary"):
                # a compaction's demotion falls to the restore only while the tail past the cut is under the churn bound's share
                # of the pre-cut bytes (2026-09-24); the five-shape file's pre-cut part is three short turns and its tail is past
                # the share, so the restore is declined before any proof
                self.assertEqual((parse.get("restore:pastShare"), parse.get("restore", 0), parse.get("restore:chainRefused", 0)), (1, 0, 0),
                                 "%s: the restore declined for the share: %s" % (name, parse))
        self.assertEqual(tree, self._cold(path, cands), "%s: the tree equals a cold whole parse" % name)

    def test_a_restore_over_a_document_stands_only_when_the_tail_chains_onto_it(self):
        """T402 rounds one and two: a restore over a document (after a descent demotion here) serves the document's pre-cut
        verdicts as they stand, so a tail that re-parents into the pre-cut interior, forks from a null root, anchors a system spur
        before the cut or names a parent no transcript holds must refuse to the whole parse, whatever the record's type; a tail
        whose every parent is the pre-cut spine tip or a tail record chains on. Twelve shapes, each against a cold whole parse."""
        for name, (tail, road) in self.SHAPES.items():
            with self.subTest(shape=name):
                path, t0 = self._documented("shape-" + name)
                self._append(path, tail(t0))
                em._read_jsonl_entry(path, tail_ok=True)                   # the entry grows; the gates see the delta
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, road, path, reason={"boundary": "boundary", "summary": "summary", "fold": None}.get(road, "descent"))

    def test_a_tail_onto_the_spine_tip_parses_whole_because_the_tail_decides_the_tips_fork(self):
        """Round four, medium 1: exempting the spine tip unconditionally was unsound. A document whose tip a3 has a second,
        abandoned pre-cut child (a rollback onto a3, then the compaction whose logical parent is a3) plus a tail spur onto a3
        restored stale pre-cut verdicts; the rows carry every pre-cut parent, so such a tip is no longer exempt: p7 the spur
        alone, p8 the spur with a reply, e the rewind onto the tip, each parses whole and equals a cold parse. (A childless
        tip stays exempt: the live manual /compact chains its wrappers onto one, the golden detached scenario.)"""
        shapes = {"p7_spur_onto_the_tip": lambda t0: [G.api_error_line(t0 + 700, "e3", "a3")],
                  "p8_spur_onto_the_tip_with_reply": lambda t0: [G.api_error_line(t0 + 700, "e3", "a3"), G.uline(t0 + 710, "after the spur", "u6", "e3")],
                  "e_rewind_onto_the_tip": self.SHAPES["e_rewind_spine_tip"][0]}
        for name, tail in shapes.items():
            with self.subTest(shape=name):
                made = self._five_shape_file("tipfork-" + name, tip_fork=True); path, t0 = made
                self.fresh(); self._parse_lineage(path, [path]); self.assertTrue(self.doc(path), "the writer accepts the tip's fork")
                self.fresh(); self._parse_lineage(path, [path]); self._reset()
                self._append(path, tail(t0))
                em._read_jsonl_entry(path, tail_ok=True)
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, "whole", path, reason="descent")

    # Round five: the tip's childlessness is a WRITE-time fact from the resolved graph (a compaction anchored on the tip is a
    # child though its row parent is null), and a tail boundary is held to its EFFECTIVE parent (logical, else the preserved
    # segment's tail, anchor or head), as the parse resolves it.
    TIP_CHILD_SHAPES = {
        "m1_spur_onto_the_tip": lambda t0: [G.api_error_line(t0 + 700, "e5", "a3")],
        "m1_spur_onto_the_tip_with_prompt": lambda t0: [G.api_error_line(t0 + 700, "e5", "a3"), G.uline(t0 + 710, "after the spur", "u6", "e5")],
        "m1_rewind_onto_the_tip": lambda t0: [G.uline(t0 + 700, "a rewind onto the tip", "u_rw5", "a3")],
    }
    BOUNDARY_SHAPES = {   # (tail, the boot road): a second compaction in the tail
        "j1_boundary_into_the_interior": (lambda t0: [G.compact_line(t0 + 700, "b2", "a1"), G.compact_summary_line(t0 + 701, "s2", "b2"),
                                                       G.uline(t0 + 710, "after the second compaction", "u7", "s2"), G.aline(t0 + 720, "reply", "a7", "u7", stop="end_turn")], "whole"),
        "j2_boundary_into_the_interior_later": (lambda t0: [G.uline(t0 + 700, "more", "u6", "a5"), G.aline(t0 + 705, "reply", "a6", "u6", stop="end_turn"),
                                                             G.compact_line(t0 + 710, "b2", "a1"), G.compact_summary_line(t0 + 711, "s2", "b2"),
                                                             G.uline(t0 + 720, "after", "u7", "s2")], "whole"),
        "j3_boundary_onto_an_unknown_uuid": (lambda t0: [G.compact_line(t0 + 700, "b2", "00000000-no-such-record"), G.compact_summary_line(t0 + 701, "s2", "b2"),
                                                          G.uline(t0 + 710, "after", "u7", "s2")], "whole"),
        "j4_broken_stitch_whose_preserved_tail_is_the_interior": (lambda t0: [G.compact_line_broken(t0 + 700, "b2", "00000000-no-such-record", "a1"),
                                                                              G.compact_summary_line(t0 + 701, "s2", "b2"), G.uline(t0 + 710, "after", "u7", "s2")], "whole"),
        "l_boundary_onto_a_tail_record": (lambda t0: [G.compact_line(t0 + 700, "b2", "a5"), G.compact_summary_line(t0 + 701, "s2", "b2"),
                                                       G.uline(t0 + 710, "after", "u7", "s2")], "restore"),
    }

    def _seeded_vs_cold(self, name, path, expect_refused):
        self.fresh(); self._reset()
        n0 = em.asm_checkpoint_stats()["parse"].get("seeded:chainRefused", 0)
        seeded_m = em.chain_membership(path, [path], rompuuid=SID); seeded_r = em.file_rewound(path, rompuuid=SID)
        refused = em.asm_checkpoint_stats()["parse"].get("seeded:chainRefused", 0) - n0
        saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
        try:
            self.fresh(); cold_m = em.chain_membership(path, [path], rompuuid=SID); cold_r = em.file_rewound(path, rompuuid=SID)
        finally:
            em._CKPT_DIR_FN = saved
        self.assertEqual(seeded_m, cold_m, "%s: the membership equals the cold walk's" % name)
        self.assertEqual(seeded_r, cold_r, "%s: the rewound set equals the cold walk's" % name)
        self.assertEqual(refused, 2 if expect_refused else 0, "%s: both readers refused the document, or neither" % name)

    def test_a_tip_with_an_abandoned_pre_cut_compaction_is_not_childless(self):
        """Round five, medium 1: the childless-tip proof read each row's RAW parent, but the graph follows parentUuid or
        logicalParentUuid; a pre-cut compaction anchored on the tip has a null row parent, so the tip passed as childless and a
        tail spur onto it restored frozen verdicts where a cold parse eclipses the abandoned branch. The bit is decided at write
        time from the resolved graph: the three shapes parse whole on the descent road, at boot and through both seeded readers."""
        for name, tail in self.TIP_CHILD_SHAPES.items():
            with self.subTest(shape=name, road="descent"):
                path, t0 = self._documented("tipchild-" + name, abandoned_compaction=True)
                import gzip as _gz
                self.assertIs(json.loads(_gz.decompress(em._asm_ckpt_file(path).read_bytes())).get("tipChildless"), False, "the document says the tip has a child")
                self._append(path, tail(t0)); em._read_jsonl_entry(path, tail_ok=True)
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, "whole", path, reason="descent")
            with self.subTest(shape=name, road="boot"):
                path, t0 = self._documented("tipchild-boot-" + name, abandoned_compaction=True)
                self._append(path, tail(t0)); self.fresh(); self._reset()
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, "whole", path, reason=None, boot=True)
            with self.subTest(shape=name, road="seeded"):
                path, t0 = self._documented("tipchild-seeded-" + name, abandoned_compaction=True)
                self._append(path, tail(t0))
                self._seeded_vs_cold(name, path, expect_refused=True)
        with self.subTest(shape="plain_tip_is_proven_childless"):
            path, t0 = self._documented("tipchild-plain")
            import gzip as _gz
            self.assertIs(json.loads(_gz.decompress(em._asm_ckpt_file(path).read_bytes())).get("tipChildless"), True)

    def test_a_tail_boundary_is_held_to_its_effective_parent(self):
        """Round five, medium 2: a tail compact_boundary was read past by type with no look at its anchor, so a compaction
        re-anchored into the pre-cut interior (a rewind before compacting) or onto an unknown uuid restored at the next boot over
        a document it invalidated. The boundary's effective parent is resolved as the parse resolves it and must be in the tail
        or the proven tip; an ordinary boundary onto a tail record restores. On the descent road every boundary in the delta
        takes the gates' own boundary demotion and parses whole: since 2026-09-24 that demotion falls to the restore only while
        the tail past the cut is under the churn bound's share, and this small file's tail is past it (restore:pastShare); the
        rule meets these shapes at boot and through the seeded readers."""
        for name, (tail, boot_road) in self.BOUNDARY_SHAPES.items():
            with self.subTest(shape=name, road="descent"):
                path, t0 = self._documented("tailb-" + name)
                self._append(path, tail(t0)); em._read_jsonl_entry(path, tail_ok=True)
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, "boundary", path, reason="boundary")
            with self.subTest(shape=name, road="boot"):
                path, t0 = self._documented("tailb-boot-" + name)
                self._append(path, tail(t0)); self.fresh(); self._reset()
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, boot_road, path, reason=None, boot=True)
            with self.subTest(shape=name, road="seeded"):
                path, t0 = self._documented("tailb-seeded-" + name)
                self._append(path, tail(t0))
                self._seeded_vs_cold(name, path, expect_refused=(boot_road == "whole"))

    # Round six: reachability, not membership. Tails that re-root themselves while every parent names a tail record.
    REROOT_SHAPES = {
        "x4_self_link_last": lambda t0: [G.uline(t0 + 700, "a self-linked record", "u_self", "u_self")],
        "x13_self_link_mid_tail_with_reply": lambda t0: [G.uline(t0 + 700, "a self-linked record", "u_self", "u_self"),
                                                         G.aline(t0 + 710, "reply under it", "a_self", "u_self", stop="end_turn")],
        "x11_boundary_cycle": lambda t0: [G.compact_line(t0 + 700, "b2", "u7"), G.compact_summary_line(t0 + 701, "s2", "b2"),
                                          G.uline(t0 + 710, "closes the cycle", "u7", "s2")],
        "x5_anchorless_boundary_segment_names_a_tail_record": lambda t0: [
            {"type": "system", "subtype": "compact_boundary", "timestamp": G.iso(t0 + 700), "uuid": "b2", "parentUuid": None, "isMeta": False,
             "compactMetadata": {"trigger": "auto", "preTokens": 1, "preservedSegment": {"headUuid": "a5", "anchorUuid": "a5", "tailUuid": "a5"}}},
            G.compact_summary_line(t0 + 701, "s2", "b2"), G.uline(t0 + 710, "after", "u7", "s2")],
        "x6_anchorless_boundary_segment_names_the_tip": lambda t0: [
            {"type": "system", "subtype": "compact_boundary", "timestamp": G.iso(t0 + 700), "uuid": "b2", "parentUuid": None, "isMeta": False,
             "compactMetadata": {"trigger": "auto", "preTokens": 1, "preservedSegment": {"headUuid": "a3", "anchorUuid": "a3", "tailUuid": "a3"}}},
            G.compact_summary_line(t0 + 701, "s2", "b2"), G.uline(t0 + 710, "after", "u7", "s2")],
    }

    def test_a_tail_that_reroots_itself_is_refused_by_reachability(self):
        """Round six, medium: the predicate tested set membership (each parent names a tail record or the proven tip), so a
        self-linked record (a root to the parse), a boundary cycle, or an anchorless boundary whose preserved segment names a
        tail record or the tip was approved while a cold parse dropped the pre-cut conversation; the dangerous face was the
        seeded readers (file_rewound naming six records the cold walk files as clear). Every tail record's parent chain must
        now REACH the proven tip; five shapes on the descent road, at boot and through both readers, against a cold parse."""
        for name, tail in self.REROOT_SHAPES.items():
            boundary = "boundary" in name
            with self.subTest(shape=name, road="descent"):
                path, t0 = self._documented("reroot-" + name)
                self._append(path, tail(t0)); em._read_jsonl_entry(path, tail_ok=True)
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, "boundary" if boundary else "whole", path, reason="boundary" if boundary else "descent")
            with self.subTest(shape=name, road="boot"):
                path, t0 = self._documented("reroot-boot-" + name)
                self._append(path, tail(t0)); self.fresh(); self._reset()
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, "whole", path, reason=None, boot=True)
            with self.subTest(shape=name, road="seeded"):
                path, t0 = self._documented("reroot-seeded-" + name)
                self._append(path, tail(t0))
                self._seeded_vs_cold(name, path, expect_refused=True)

    # Rounds seven and eight: a repeated uuid is resolved as the parse resolves it (the LAST record's parent wins, one node per
    # uuid) and reachability decides it; a reuse of a pre-cut uuid closes a cycle across the spine and refuses outright. Real
    # transcripts repeat uuids (a count-only scan: about one session in thirty, the verbatim same-parent duplicate almost all
    # of them), so refusing every repeat cost the restore on those sessions at every boot and reader call (round eight).
    DUP_SHAPES = {   # (tail, the boot road)
        "y1_repeated_uuid_second_copy_a_null_root": (lambda t0: [G.uline(t0 + 700, "first copy", "d1", "a5"), G.uline(t0 + 701, "second copy", "d1", None)], "whole"),
        "y2_tail_record_reusing_a_pre_cut_uuid": (lambda t0: [G.uline(t0 + 700, "reuses a pre-cut uuid", "a2", "a5")], "whole"),
        "y3_repeated_uuid_second_copy_self_linked": (lambda t0: [G.uline(t0 + 700, "first copy", "d1", "a5"), G.uline(t0 + 701, "second copy", "d1", "d1")], "whole"),
        "y4_verbatim_duplicate": (lambda t0: [G.uline(t0 + 700, "the same record twice", "d1", "a5"), G.uline(t0 + 700, "the same record twice", "d1", "a5")], "restore"),
        "y5_same_parent_duplicate_with_a_reply_under_the_second": (lambda t0: [G.uline(t0 + 700, "a copy", "d1", "a5"), G.uline(t0 + 700, "a copy", "d1", "a5"),
                                                                              G.aline(t0 + 710, "reply under it", "a_d1", "d1", stop="end_turn")], "restore"),
        "y6_differing_parent_duplicate_last_copy_chains": (lambda t0: [G.uline(t0 + 700, "first copy", "d1", "a1"), G.uline(t0 + 701, "last copy", "d1", "a5")], "restore"),
    }

    def test_a_repeated_uuid_resolves_as_the_parse_does_and_a_reused_pre_cut_uuid_is_refused(self):
        """Round seven, medium: the reachability memo was keyed by uuid and the record map last-wins, so the second record with a
        uuid took the first copy's verdict while the parse keeps the LAST record's parent; a tail record reusing a pre-cut uuid
        closed a cycle across the spine. Round eight: refusing every repeat cost the restore on the real sessions that carry
        verbatim duplicates, so a repeat is one node with the last copy's parent and reachability decides it (y1 and y3 refuse,
        their last copy a non-tip root; y4, y5 and y6 restore equal to cold, the parse's own answer); the pre-cut reuse (y2) still
        refuses. At 77c26a93 the verbatim duplicate restored at boot and through the readers already, and refused only where the
        descent walk demoted first. Three roads, cold parse."""
        for name, (tail, boot_road) in self.DUP_SHAPES.items():
            with self.subTest(shape=name, road="descent"):
                path, t0 = self._documented("dup-" + name)
                self._append(path, tail(t0)); em._read_jsonl_entry(path, tail_ok=True)
                tree, parse, reads = self._served(path)
                if boot_road == "whole":
                    self.assertEqual(parse.get("restore:afterDemote", 0), 0, "%s: never restored: %s" % (name, parse))
                self.assertEqual(tree, self._cold(path), "%s: the tree equals a cold whole parse" % name)
            with self.subTest(shape=name, road="boot"):
                path, t0 = self._documented("dup-boot-" + name)
                self._append(path, tail(t0)); self.fresh(); self._reset()
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, boot_road, path, reason=None, boot=True)
            with self.subTest(shape=name, road="seeded"):
                path, t0 = self._documented("dup-seeded-" + name)
                self._append(path, tail(t0))
                self._seeded_vs_cold(name, path, expect_refused=(boot_road == "whole"))

    def _strip_bit(self, path):
        """The standing document as a kernel before the childless bit wrote it."""
        import gzip as _gz
        cp = em._asm_ckpt_file(path); doc = json.loads(_gz.decompress(cp.read_bytes())); doc.pop("tipChildless", None)
        cp.write_bytes(_gz.compress(json.dumps(doc, separators=(",", ":")).encode("utf-8"), compresslevel=6))

    def test_a_document_the_chain_proof_refused_is_rewritten_by_the_whole_parse_that_follows(self):
        """Follow-up: a standing document without the childless bit was refused at EVERY boot until the session happened to move
        (sixteen of twenty-six sessions paid a whole parse per boot); the whole parse that follows a chain refusal writes the
        document then and there, so the next restore takes it. The same for a refusal by the tail's shape: the parse writes what
        it can (the writer decides), and the restore that follows answers by the rule."""
        with self.subTest(shape="a_document_without_the_bit"):
            path, t0 = self._documented("rewrite-bit"); self._strip_bit(path)
            self.fresh(); self._reset()
            tree, parse, reads = self._served(path)
            self.assertEqual((parse.get("restore:chainRefused"), parse.get("full:refused"), parse.get("write:afterRefusal")), (1, 1, 1), parse)
            import gzip as _gz
            self.assertIs(json.loads(_gz.decompress(em._asm_ckpt_file(path).read_bytes())).get("tipChildless"), True, "the rewritten document carries the bit")
            self.fresh(); self._reset()
            tree2, parse2, reads2 = self._served(path)
            self.assertEqual((parse2.get("restore"), parse2.get("restore:chainRefused", 0), parse2.get("full", 0)), (1, 0, 0), "the next restore takes it: %s" % parse2)
            self.assertEqual(tree2, self._cold(path))
        with self.subTest(shape="a_rerooted_tail"):
            path, t0 = self._documented("rewrite-reroot")
            self._append(path, self.REROOT_SHAPES["x4_self_link_last"](t0))
            self.fresh(); self._reset(); w0 = em.asm_checkpoint_stats()["written"]
            tree, parse, reads = self._served(path)
            self.assertEqual(parse.get("restore:chainRefused"), 1, parse)
            self.assertEqual(parse.get("write:afterRefusal", 0) + parse.get("write:afterRefusalSkipped", 0), 1,
                             "the whole parse offered its document to the writer: %s (skipped: %s)" % (parse, em.asm_checkpoint_stats()["skipped"]))
            self.assertEqual(tree, self._cold(path))

    def test_a_document_refused_for_the_tails_shape_is_marked_and_no_road_retries_while_the_leaf_stands(self):
        """Follow-up round two, medium 1: the rewrite converges only for the missing bit; for a tail whose SHAPE refuses (a
        re-rooted tail here, x4) the same cut reproduces the same refusal, so every boot paid the proof and the rewrite attempt
        again. The refusal is marked in the sidecar at the leaf's stat: three boots write once (the first parse's attempt) and
        run the proof zero times after the mark; both seeded readers walk cold without the proof; the mark clears when the leaf
        moves. The missing-bit case keeps its one rewrite (the test above)."""
        import gzip as _gz
        path, t0 = self._documented("marked-x4")
        self._append(path, self.REROOT_SHAPES["x4_self_link_last"](t0))
        rk = os.path.realpath(path)
        for boot in (1, 2, 3):
            with self.subTest(boot=boot):
                self.fresh(); self._reset(); w0 = em.asm_checkpoint_stats()["written"]
                tree, parse, reads = self._served(path)
                if boot == 1:
                    self.assertEqual(parse.get("restore:chainRefused"), 1, parse)
                    self.assertEqual(parse.get("write:afterRefusal", 0) + parse.get("write:afterRefusalSkipped", 0), 1, "one write attempt: %s" % parse)
                    meta = json.loads(em._asm_ckpt_file(path).with_name(em._asm_ckpt_file(path).name + ".meta").read_text())
                    self.assertEqual(meta.get("refused", {}).get("reason"), "shape", meta)
                    self.assertTrue(em._asm_refusal_stands(path))
                else:
                    self.assertEqual(parse.get("restore:refusedStanding"), 1, "%s: the mark stands: no proof" % boot)
                    self.assertEqual(parse.get("restore:chainRefused", 0), 0, "%s: the proof did not run" % boot)
                    self.assertEqual(parse.get("write:afterRefusal", 0) + parse.get("write:afterRefusalSkipped", 0), 0, "%s: no rewrite attempt" % boot)
                    self.assertEqual(em.asm_checkpoint_stats()["written"] - w0, 0)
                self.assertEqual(parse.get("full:refused"), 1, parse)
                self.assertEqual(tree, self._cold(path))
        with self.subTest(road="seeded"):
            self.fresh(); self._reset()
            n0 = em.asm_checkpoint_stats()["parse"].get("seeded:refusedStanding", 0)
            seeded_m = em.chain_membership(path, [path], rompuuid=SID); seeded_r = em.file_rewound(path, rompuuid=SID)
            parse = em.asm_checkpoint_stats()["parse"]
            self.assertEqual(parse.get("seeded:refusedStanding", 0) - n0, 2, "both readers walked cold on the mark: %s" % parse)
            self.assertEqual(parse.get("seeded:chainRefused", 0), 0, "no proof ran")
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                self.fresh(); cold_m = em.chain_membership(path, [path], rompuuid=SID); cold_r = em.file_rewound(path, rompuuid=SID)
            finally:
                em._CKPT_DIR_FN = saved
            self.assertEqual((seeded_m, seeded_r), (cold_m, cold_r))
        with self.subTest(road="the leaf moves"):
            self._append(path, [G.uline(t0 + 800, "more", "u_more", "u_self")])
            self.assertFalse(em._asm_refusal_stands(path), "a moved leaf clears the mark")
            self.fresh(); self._reset()
            tree, parse, reads = self._served(path)
            self.assertEqual(parse.get("restore:refusedStanding", 0), 0, "the proof runs again for the moved leaf: %s" % parse)
            self.assertEqual(tree, self._cold(path))

    def test_a_declined_unproven_rewrite_is_marked_so_the_proof_is_not_repeated(self):
        """Round three, low 3: an unproven refusal (the missing bit) whose offered rewrite the writer DECLINES repeated the proof,
        the second walk, the build and the offer at every boot with no mark; a declined offer marks the sidecar like a shape."""
        path, t0 = self._documented("declined-bit"); self._strip_bit(path)
        self.fresh(); self._reset()
        saved = em.asm_checkpoint_write
        em.asm_checkpoint_write = lambda *a, **k: False              # the writer declines (a document past the cap, say)
        try:
            tree, parse, reads = self._served(path)
        finally:
            em.asm_checkpoint_write = saved
        self.assertEqual((parse.get("restore:chainRefused"), parse.get("write:afterRefusalSkipped")), (1, 1), parse)
        self.assertTrue(em._asm_refusal_stands(path), "a declined offer leaves the mark")
        self.fresh(); self._reset()
        tree, parse, reads = self._served(path)
        self.assertEqual((parse.get("restore:refusedStanding"), parse.get("restore:chainRefused", 0)), (1, 0), "no second proof: %s" % parse)
        self.assertEqual(tree, self._cold(path))

    def test_the_seeded_readers_read_the_mark_once_per_call(self):
        """Round three, low 4: each reader evaluated the standing mark twice per call, reading the sidecar twice. The document
        carries a STANDING mark here (the five-lows read, low 3: a healthy document with no mark read once at the base too)."""
        path, t0 = self._documented("once")
        self.assertTrue(em._asm_mark_refused(path, "shape", SID)); self.assertTrue(em._asm_refusal_stands(path))
        calls = []
        real = em._asm_refusal_stands
        em._asm_refusal_stands = lambda p: (calls.append(p), real(p))[1]
        try:
            self.fresh(); em.chain_membership(path, [path], rompuuid=SID); n1 = len(calls)
            em.file_rewound(path, rompuuid=SID); n2 = len(calls) - n1
        finally:
            em._asm_refusal_stands = real
        self.assertEqual((n1, n2), (1, 1), "one sidecar read per reader call")

    def test_the_mark_writes_under_its_own_tmp_name_and_reads_back(self):
        """Round three, low 2: the mark's read-modify-write shared the writer's and the refresh's tmp name under a different lock;
        it takes the writer's key lock, writes under a tmp name of its own and reads the file back."""
        import inspect
        src = inspect.getsource(em._asm_mark_refused)
        self.assertIn('".mark.%d.%x.tmp"', src); self.assertIn("_asm_key_lock(key)", src)
        path, t0 = self._documented("mark-own")
        self.assertTrue(em._asm_mark_refused(path, "shape", SID))
        self.assertTrue(em._asm_refusal_stands(path))
        meta = em._asm_ckpt_file(path).with_name(em._asm_ckpt_file(path).name + ".meta")
        self.assertEqual([p.name for p in meta.parent.glob("*.tmp")], [], "no tmp left behind")

    def test_a_cyclic_resolved_graph_writes_a_document_whose_spine_ends_at_the_first_revisit(self):
        """Follow-up (the demote gate's later low): a reused uuid can make the resolved graph cyclic, and the writer's spine walk
        ran to its 500,000 guard and stored the collected chain; the fix bounded the walk by the record count and REFUSED the
        document on a cycle. Stage one of the process split (2026-09-14, plans/checkpoint-cycle-walk.md): the walk ends at the
        first revisit as the parse's active_path does, the document is written, no cycle skip is counted, and the restore
        equals the cold parse (34 of the 76 live transcripts over 10 MB were refused this way, every chat build a cold parse)."""
        t0 = NOW
        recs = [G.uline(t0, "first ask", "u1", "a1"), G.aline(t0 + 10, "first reply", "a1", "u1", stop="end_turn"),   # u1 <-> a1
                G.compact_line(t0 + 600, "b1", "a1"), G.compact_summary_line(t0 + 601, "s1", "b1"),
                G.uline(t0 + 610, "after", "u4", "s1"), G.aline(t0 + 620, "reply", "a4", "u4", stop="end_turn")]
        path = self.write("cycle", recs)
        cold = self._cold(path)
        self.fresh(); self._parse_lineage(path, [path])
        sk0 = dict(em.asm_checkpoint_stats()["skipped"])
        self.assertTrue(self.doc(path), "a document for a cyclic graph: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("cycle", 0) - sk0.get("cycle", 0), 0, "no cycle skip")
        self.fresh(); self._reset()
        tree, parse, reads = self._served(path)
        self.assertEqual((parse.get("restore"), parse.get("restore:chainRefused", 0)), (1, 0), parse)
        self.assertEqual(tree, cold, "restored equals the cold parse: the spine is the chain up to the first revisit")

    def test_a_uuid_less_record_bearing_a_parent_is_not_a_node_of_the_walk(self):
        """Follow-up (the demote gate's later low): the parse indexes nothing for a record without a uuid, so a null or interior
        parent on such a record must not cost a whole parse; it is not walked, and the restore stands equal to a cold parse."""
        path, t0 = self._documented("uuidless")
        rec = {k: v for k, v in G.uline(t0 + 700, "no uuid", "u_x", "a1").items() if k != "uuid"}
        self._append(path, [rec]); self.fresh(); self._reset()
        tree, parse, reads = self._served(path)
        self.assertEqual((parse.get("restore"), parse.get("restore:chainRefused", 0)), (1, 0), parse)
        self.assertEqual(tree, self._cold(path))

    def test_the_seeded_readers_take_the_chain_rule_and_fall_to_the_cold_walk(self):
        """Round four, medium 2: chain_membership and file_rewound seeded an adapter from the document with no chain rule, and
        the goal sweep archived on their answer (over a rewound tail the seeded signature named one eclipsed record where the
        cold walk kept it). Both take the predicate before seeding; a refused document means the cold walk, counted."""
        for name, road in (("a_rewind_pre_interior", "whole"), ("i_interior_api_error_alone", "whole"), ("c_api_error_spur_at_opener", "restore")):
            with self.subTest(shape=name):
                path, t0 = self._documented("seeded-" + name)
                self._append(path, self.SHAPES[name][0](t0))
                self.fresh(); self._reset()                                  # no entry: both readers reach for the document
                n0 = em.asm_checkpoint_stats()["parse"].get("seeded:chainRefused", 0)
                seeded_m = em.chain_membership(path, [path], rompuuid=SID)
                seeded_r = em.file_rewound(path, rompuuid=SID)
                refused = em.asm_checkpoint_stats()["parse"].get("seeded:chainRefused", 0) - n0
                saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
                try:
                    self.fresh()
                    cold_m = em.chain_membership(path, [path], rompuuid=SID); cold_r = em.file_rewound(path, rompuuid=SID)
                finally:
                    em._CKPT_DIR_FN = saved
                self.assertEqual(seeded_m, cold_m, "%s: the membership equals the cold walk's" % name)
                self.assertEqual(seeded_r, cold_r, "%s: the rewound set equals the cold walk's" % name)
                self.assertEqual(refused, 2 if road == "whole" else 0, "%s: both readers refused the document, or neither" % name)

    def test_the_chain_rule_runs_for_a_rewrite_demotion_after_the_caches_own_eviction(self):
        """Round two, medium 1: the rule ran for descent only, and a plain LRU eviction of the leaf's record entry (the cache's byte
        budget) makes the next parse's reason g:rewrite, which reached the restore unguarded. The entry is evicted through the
        cache's own loop (a budget of one byte and a read of another file), never popped by hand."""
        for name in ("a_rewind_pre_interior", "b_clear_fork", "c_api_error_spur_at_opener"):
            with self.subTest(shape=name):
                tail, road = self.SHAPES[name]
                path, t0 = self._documented("evict-" + name)
                self._append(path, tail(t0))
                other = self.write("evict-other-" + name, [G.uline(t0, "another file", "o1")])
                saved = em._JSONL_CACHE_BUDGET_BYTES; em._JSONL_CACHE_BUDGET_BYTES = 1
                try:
                    em._read_jsonl_entry(other, tail_ok=False)             # the insert evicts the leaf's entry: the cache's own loop
                finally:
                    em._JSONL_CACHE_BUDGET_BYTES = saved
                with em._JSONL_CACHE_LOCK:
                    self.assertNotIn(path, em._JSONL_CACHE, "the leaf's record entry evicted")
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, road, path, reason="rewrite")

    def test_a_nonleaf_demotion_is_refused_by_the_loads_lineage_witness_before_any_chain_rule(self):
        """Round two: the nonleaf demotion driven. A lineage file that grew (or was touched) demotes the entry as nonleaf; the
        document's own byte checks refuse it first (the skipped file's stat witness: fallbacks.lineage), and the parse is whole,
        equal to a cold parse. The rule's other nonleaf road, a lineage file's reader entry replaced under a new generation
        (the gates' line for a file with a cut of its own), is not reachable from these shapes: the writer skips every lineage
        file whose records all sort before the cut, which a resume parent's do, a fork of its own stamped after the compaction
        included (probed: pre 3 of 3, skip). The chain rule itself is one call inside _asm_restore, shared by every reason."""
        path, t0, sib = self._documented("nonleaf-grown", resume=True)
        self._append(sib, [G.uline(t0 - 60, "the sibling grows", "x3", "x2")])
        em._read_jsonl_entry(sib, tail_ok=True)
        fb0 = em.asm_checkpoint_stats()["fallbacks"].get("lineage", 0)
        tree, parse, reads = self._served(path, [path, sib])
        self.assertEqual((parse.get("g:nonleaf"), parse.get("full:demoted"), parse.get("restore:chainRefused", 0), parse.get("restore", 0)),
                         (1, 1, 0, 0), "%s" % parse)
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("lineage", 0) - fb0, 1, "the load's lineage witness refused it")
        self.assertEqual(tree, self._cold(path, [path, sib]))

    def test_the_chain_rule_runs_for_the_boot_restore_too(self):
        """Round two, item A: with no assembly entry (every kernel boot, every eviction of the entry) the restore ran with no tail
        check, so a document standing over a rewound or forked tail restored as round one described at the next start. The one
        rule runs on every restore over a document; a refused boot restore falls to the whole parse and books restore:chainRefused."""
        boot_roads = {"p4_summary_parented_into_the_interior": "whole", "r7_sidechain_parented_into_the_interior": "whole",
                      "r8_sidechain_last_in_file": "whole", "q_sidechain_rooted_in_the_tail": "restore"}
        for name in ("a_rewind_pre_interior", "b_clear_fork", "i_interior_api_error_alone", "e_rewind_spine_tip", "p1_no_parent_key",
                     "p4_summary_parented_into_the_interior", "r7_sidechain_parented_into_the_interior", "r8_sidechain_last_in_file",
                     "q_sidechain_rooted_in_the_tail", "c_api_error_spur_at_opener", "d_rewind_post_cut"):
            with self.subTest(shape=name):
                tail, road = self.SHAPES[name]; road = boot_roads.get(name, road)
                path, t0 = self._documented("boot-" + name)
                self._append(path, tail(t0))
                self.fresh(); self._reset()                                  # a boot: no entry, the document on disk
                tree, parse, reads = self._served(path)
                self._check(name, tree, parse, reads, road, path, reason=None, boot=True)

    def test_a_refusal_recorded_inside_the_writes_window_survives_its_pop(self):
        """Follow-up, low B: the write's pop also discarded a refusal a judge recorded against the document just published, between
        the replace and the pop, so that parse booked noDocument. The slot is stamped; the write pops only an older refusal."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("window", records(), sent=sent)
        self.fresh(); self.parse(path)
        real = em._asm_sidecar
        def sidecar_and_a_refusal(doc):
            em._asm_ckpt_note(path, "guard")                                 # a judge refuses the document inside the write's window
            return real(doc)
        em._asm_sidecar = sidecar_and_a_refusal
        self.addCleanup(setattr, em, "_asm_sidecar", real)
        self.assertTrue(self.doc(path))
        em._asm_sidecar = real
        rk = os.path.realpath(path)
        with em._ASM_CKPT_LOCK:
            self.assertIn(rk, em._ASM_CKPT_REFUSED, "the refusal recorded inside the window survives the write's pop")

    def test_a_refusal_recorded_during_the_build_is_popped_by_the_write(self):
        """Round one, low 1: the window opened at the write's first line, so a refusal a judge recorded DURING the document build
        (against the document still on disk, which the note unlinks) survived the pop and mislabelled the next parse. The stamp is
        taken just before the replace: a refusal before it was against the retired document and goes; one after it stands."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("build", records(), sent=sent)
        self.fresh(); self.parse(path)
        real = em._carry_encode
        def carry_and_a_refusal(st):
            em._asm_ckpt_note(path, "guard")                                 # a judge refuses the OLD document while this one builds
            return real(st)
        em._carry_encode = carry_and_a_refusal
        self.addCleanup(setattr, em, "_carry_encode", real)
        self.assertTrue(self.doc(path))
        rk = os.path.realpath(path)
        with em._ASM_CKPT_LOCK:
            self.assertNotIn(rk, em._ASM_CKPT_REFUSED, "a refusal recorded before the replace was against the retired document: popped")

    def test_the_writes_docstring_stands(self):
        """Round one, low 2: the window's stamp was inserted above the docstring, which made the string a bare expression."""
        self.assertTrue((em.asm_checkpoint_write.__doc__ or "").startswith("Write the leaf's assembly checkpoint"),
                        "asm_checkpoint_write.__doc__: %r" % (em.asm_checkpoint_write.__doc__,))

    def test_the_nudge_gates_failure_leg_is_counted_and_said_as_its_docstring_claims(self):
        """Low E and its round-one pin: the docstring claims the leg is counted under failed and said on stderr; the test drives
        the leg (a planner that raises) and checks both, not a word's absence in the source."""
        import contextlib, io
        km = kernel_module(); jd = km.jd
        self.assertIn("counted under failed and said on stderr", km._nudge_placement_gate.__doc__ or "")
        saved = jd.plan_units
        self.addCleanup(setattr, jd, "plan_units", saved)
        def failing_planner(session, store, **kw):
            raise RuntimeError("synthetic gate failure")
        jd.plan_units = failing_planner
        km._NUDGE_GATE_STATS["failed"] = 0
        turns = [{"id": "t1", "t": 1.0, "end": 2.0, "ended": True, "atoms": [], "trigger": None}]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(km._nudge_placement_gate(SID, turns, {"placements": {}}), "a failed gate answers not unplanned")
        self.assertEqual(km._NUDGE_GATE_STATS["failed"], 1, "counted under failed")
        self.assertIn("auto-nudge placement gate", err.getvalue(), "said on stderr")
        self.assertIn("synthetic gate failure", err.getvalue(), "with its traceback")
    def test_whole_reads_and_hydrations_are_counted_under_the_calling_threads_stage(self):
        """T401: the first instrumented boot said jobs.autoNudge read 162.8 MB, and the callers' rows could not say which caller
        inside that job read it. The kernel marks the thread's stage for each tick job and the push; the event model counts
        every whole read and hydration under (stage, caller) too, `none` outside the cycle."""
        km = kernel_module()
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("bystage", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}; em._RECORD_CACHE_STATS["wholeReadsByStage"] = {}
        em._ASM_CKPT_STATS["hydratedByStage"] = {}
        tree = km._job_stage("probe", lambda: self.parse(path))           # a restore inside a job: no whole read
        km._job_stage("probe", lambda: em.hydrate([a for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None][:2], by="probeReader"))
        km._job_stage("probe", lambda: em._read_jsonl_entry(path, tail_ok=False))   # a whole read inside the job
        st = em.record_cache_stats()
        by_stage = st["wholeReadsByStage"]
        self.assertTrue(any(k.startswith("jobs.probe:upgrade<-") for k in by_stage), "the whole read under its job: %r" % by_stage)
        hb = em.asm_checkpoint_stats()["hydratedByStage"]
        self.assertTrue(any(k.startswith("jobs.probe:probeReader") for k in hb), "the hydration under its job: %r" % hb)
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(path, None)
        em._read_jsonl_entry(path, tail_ok=False)                          # outside any stage
        self.assertTrue(any(k.startswith("none:zero<-") for k in em.record_cache_stats()["wholeReadsByStage"]), "outside the cycle: none")
        self.assertIsNone(km._current_read_stage(), "the mark returns after the job")

    def test_the_push_mark_is_restored_on_every_exit_and_the_pushs_reads_count_under_push(self):
        """T401 round one, medium: _push set the thread's mark inline and restored it at its end, so a caught build failure returned
        before the restore and the pusher thread stayed marked `push` for the process's life, booking every later read outside a
        stage under push:. The mark is a decorator with a finally. And a read inside the push books push:<kind><-<caller>."""
        km = kernel_module()
        saved = (km._chat_tab_sessions, km._live_map, km.NAMES)
        def restore():
            km._chat_tab_sessions, km._live_map, km.NAMES = saved
        self.addCleanup(restore)
        km._live_map = lambda: {}; km.NAMES = {}
        def boom(now, live_map):
            raise RuntimeError("a synthetic build failure")
        km._chat_tab_sessions = boom
        import io
        err = io.StringIO(); saved_err = sys.stderr; sys.stderr = err
        try:
            km._push([{"app": "chat", "wid": "lab", "send": lambda *a, **k: None, "alive": True, "dedup": {}}], live_map={})
        except Exception:
            pass
        finally:
            sys.stderr = saved_err
        self.assertIsNone(km._current_read_stage(), "the mark is restored after a build failure (the leak)")
        # a read inside the push books push:<kind><-<caller>
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("pushread", records(), sent=sent)
        self.fresh()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReadsByStage"] = {}
        km._chat_tab_sessions = lambda now, live_map: (em._read_jsonl_entry(path, tail_ok=False), [])[1]   # a whole read inside the push
        try:
            km._push([{"app": "chat", "wid": "lab", "send": lambda *a, **k: None, "alive": True, "dedup": {}}], live_map={})
        except Exception:
            pass
        rows = em.record_cache_stats()["wholeReadsByStage"]
        self.assertTrue(any(k.startswith("push:zero<-") for k in rows), "the push's read under its mark: %r" % rows)
        self.assertIsNone(km._current_read_stage())
        self.assertTrue(hasattr(km._push, "__wrapped__"), "the push carries the stage decorator (set at entry, restored in a finally)")

    def test_the_mark_returns_through_a_raise_and_a_connect_push_is_marked_connect(self):
        """T401 round two, lows 1 and 2: the round-one test drove a CAUGHT build failure, which returns normally into the wrapper, so
        a decorator restoring after the call would have passed it; the finally is pinned by a body that raises out. And a handler
        thread's connect push (Handler._push_one: connect=True) was marked push too, booking a browser reload's reads into the
        cycle's push: rows; it is marked connect."""
        km = kernel_module()
        km._STAGE_TL.name = "before"
        self.addCleanup(setattr, km._STAGE_TL, "name", None)
        def raiser():
            self.assertEqual(km._current_read_stage(), "probe")
            raise RuntimeError("synthetic")
        with self.assertRaises(RuntimeError):
            km._stage_marked("probe")(raiser)()
        self.assertEqual(km._current_read_stage(), "before", "the previous mark returns after a raise (the finally)")
        km._STAGE_TL.name = None
        saved = km._chat_push_scopes_close                                 # the push's first call outside any try
        self.addCleanup(setattr, km, "_chat_push_scopes_close", saved)
        seen = []
        def close_and_raise():
            seen.append(km._current_read_stage())
            raise RuntimeError("synthetic, out of the push")
        km._chat_push_scopes_close = close_and_raise
        client = {"app": "chat", "wid": "lab", "send": lambda *a, **k: None, "alive": True, "dedup": {}}
        with self.assertRaises(RuntimeError):
            km._push([client], connect=True, live_map={})
        with self.assertRaises(RuntimeError):
            km._push([client], live_map={})
        self.assertEqual(seen, ["connect", "push"], "a fresh client's push is marked connect; the pusher's push, push")
        self.assertIsNone(km._current_read_stage(), "restored after a raise out of the push")

if __name__ == "__main__":
    unittest.main()
