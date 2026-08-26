#!/usr/bin/env python3
"""jd.drop_goals_after(fsid, cut_t): a chat delete/edit rolls the CONVERSATION back to just before cut_t; the
cards MINTED from the now-abandoned turns (node["t"] >= cut_t) are orphans, so they are archived out of the
live store — whole subtrees. Deliberately narrow: verdicts an abandoned turn applied to a PRE-EXISTING card
are left alone (the user chose this simpler shape over surgically reverting the append-only diary + the
durable override journal). All fixtures are SYNTHETIC (placeholder UUIDs, invented text).
"""
import json
import os
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600
CUT = T0 + 50          # everything minted at/after CUT is abandoned; T0..T0+49 survives


class RevertBase(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        self.td.cleanup()

    def _store(self):
        return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
                "placements": {}, "status": {}}

    def _nid(self, seq):
        return "%s:g%d" % (SID, seq)


class DropGoalsAfter(RevertBase):
    def test_a_card_minted_after_the_cut_is_archived_whole_subtree(self):
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Survivor"}], [])          # born < CUT
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "mint", "why": "x", "text": "Born in range"}], jd.open_menu(s))
        jd.apply_plan(s, "s3", T0 + 110, [{"do": "sub", "why": "x", "under": 2, "text": "sub of born"}], jd.open_menu(s))
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        archived = jd.drop_goals_after(SID, CUT)
        self.assertEqual(archived, 2, "the born-in-range top AND its sub are archived")
        live = jd.load_goals(SID)["nodes"]
        self.assertIn(self._nid(1), live, "the survivor stays in the live store")
        self.assertNotIn(self._nid(2), live, "the born-in-range top is gone from the live store")
        self.assertNotIn(self._nid(3), live, "its sub goes with it")
        arch = jd.load_goal_archive(SID)["nodes"]
        self.assertIn(self._nid(2), arch, "the born-in-range top moved to the archive")
        self.assertIn(self._nid(3), arch, "the sub moved with it")

    def test_a_born_in_range_sub_under_a_surviving_parent_is_archived_alone(self):
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Survivor top"}], [])
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "sub", "why": "x", "under": 1, "text": "late sub"}], jd.open_menu(s))
        jd.save_goals(SID, s)
        archived = jd.drop_goals_after(SID, CUT)
        self.assertEqual(archived, 1, "only the late sub is archived; its pre-cut parent survives")
        live = jd.load_goals(SID)["nodes"]
        self.assertIn(self._nid(1), live)
        self.assertNotIn(self._nid(2), live)

    def test_removing_a_born_in_range_blocked_sub_re_rolls_the_surviving_parents_status(self):
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Parent"}], [])
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "sub", "why": "x", "under": 1, "text": "late blocked sub"}],
                      jd.open_menu(s))
        jd.apply_plan(s, "s3", T0 + 110, [{"do": "block", "why": "owed", "goal": 2}], jd.open_menu(s))  # block the sub
        jd.rollup_status(s, session_closed=False)
        self.assertEqual(s["status"][self._nid(1)], "blocked", "premise: the late sub's block rolls up to the parent")
        jd.save_goals(SID, s)
        jd.drop_goals_after(SID, CUT)
        n = jd.load_goals(SID)
        self.assertEqual(n["status"][self._nid(1)], "working",
                         "with the born-in-range blocked sub gone, the parent re-rolls to working")

    def test_nothing_in_range_is_a_noop(self):
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "G"}], [])
        jd.save_goals(SID, s)
        self.assertEqual(jd.drop_goals_after(SID, CUT), 0, "no card born at/after the cut → nothing archived")
        self.assertIn(self._nid(1), jd.load_goals(SID)["nodes"], "the pre-cut card is untouched")

    def test_a_pre_existing_cards_verdicts_are_left_alone(self):
        # deliberate scope: a verdict an abandoned turn applied to a card born BEFORE the cut is NOT reverted
        # (that would need diary + override-journal surgery). Only born-in-range cards are dropped.
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Pre-existing"}], [])
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "block", "why": "owed", "goal": 1}], jd.open_menu(s))  # ev_t after CUT
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        self.assertEqual(jd.drop_goals_after(SID, CUT), 0, "the pre-existing card is not born-in-range → not archived")
        n = jd.load_goals(SID)
        self.assertTrue(n["nodes"][self._nid(1)]["blocked"],
                        "its block (from a now-abandoned turn) is intentionally left in place")

    def test_empty_store_is_a_noop(self):
        jd.save_goals(SID, self._store())
        self.assertEqual(jd.drop_goals_after(SID, CUT), 0)

    def test_a_user_restored_card_is_never_re_swept_by_a_later_overlapping_rewind(self):
        # A card the user restored out of an EARLIER rewind's sweep (durable rewindRestored stamp)
        # merely time-overlaps a LATER rewind's cut range: that rewind's evidence is about the
        # current chain's tail, not the restored card's long-dead minting branch, so re-archiving
        # it — and popping the stamp, erasing even the reconciler's shield — moved a card on zero
        # new information, silently overriding an explicit user gesture. The t-keyed selection
        # spares the stamp like the reconciler does; only the user's own gesture re-kills a
        # restored card. Exercised with kept=None on purpose: the exemption must hold on the
        # degraded no-kept-set path too.
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Survivor"}], [])
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "mint", "why": "x", "text": "Restored earlier"}],
                      jd.open_menu(s))
        jd.apply_plan(s, "s3", T0 + 110, [{"do": "mint", "why": "x", "text": "Doomed"}],
                      jd.open_menu(s))
        jd.rollup_status(s, session_closed=False)
        s["rewindRestored"] = {self._nid(2): NOW - 50}   # the earlier restore's durable stamp
        jd.save_goals(SID, s)
        self.assertEqual(jd.drop_goals_after(SID, CUT), 1,
                         "only the unrestored in-range card is swept")
        live = jd.load_goals(SID)
        self.assertIn(self._nid(2), live["nodes"], "the restored card survives the overlapping take")
        self.assertEqual(live.get("rewindRestored", {}).get(self._nid(2)), NOW - 50,
                         "…with its durable stamp intact — the reconciler's shield is not popped")
        self.assertNotIn(self._nid(3), live["nodes"], "the genuinely in-range card is still swept")
        self.assertIn(self._nid(3), jd.load_goal_archive(SID)["nodes"])
        self.assertNotIn(self._nid(2), jd.load_goal_archive(SID)["nodes"])


class RebaseTombstones(RevertBase):
    """The sweep leaves a DURABLE deletion marker (store rewindSwept) that _rebase_onto_disk honors —
    the mergedFrom lesson applied to rewinds. Without it any one-shot sweep, however keyed, loses to
    the next concurrent save: presence-in-a-snapshot is not truth, and the adopt-wholesale branch
    republished swept nodes (proven five times in live stores — nodes resident in live AND archive
    at once, live twins gathering diary rows on a conversation that no longer exists)."""

    def _seed(self):
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Survivor"}], [])
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "mint", "why": "x", "text": "Doomed"}], jd.open_menu(s))
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        return self._nid(2)

    def test_a_pre_sweep_loader_saving_post_sweep_does_not_republish(self):
        # ordering (i): writer A loads, the sweep archives + saves, A saves — A's rebase must read
        # the tombstone from DISK and drop its stale copy instead of adopting it back
        doomed = self._seed()
        a = jd.load_goals(SID)                        # writer A's pre-sweep snapshot (holds `doomed`)
        self.assertEqual(jd.drop_goals_after(SID, CUT), 1)
        a["nodes"][self._nid(1)]["mt"] = T0 + 5       # A did unrelated work, then publishes
        jd.save_goals(SID, a)
        live = jd.load_goals(SID)
        self.assertNotIn(doomed, live["nodes"], "the swept node stays swept through A's rebase")
        self.assertIn(doomed, jd.load_goal_archive(SID)["nodes"], "…and lives on in the archive only")
        self.assertIn(doomed, live.get("rewindSwept", {}), "the tombstone itself survived the rebase")

    def test_the_sweeps_own_save_rebasing_over_a_midflight_publish_does_not_readopt(self):
        # ordering (ii): a concurrent pass publishes between the sweep's load and its save — the
        # sweep's rebase must not adopt back from disk the very node it just popped
        doomed = self._seed()
        orig = jd.save_goal_archive
        def hijack(fsid, arch):                       # runs INSIDE drop_goals_after, between its load
            orig(fsid, arch)                          # and its save — the mid-flight window
            w = jd.load_goals(SID)                    # the concurrent writer still sees `doomed` live
            w["nodes"][doomed]["mt"] = T0 + 200
            jd.save_goals(SID, w)                     # …and publishes it (disk rev moves)
        jd.save_goal_archive = hijack
        try:
            self.assertEqual(jd.drop_goals_after(SID, CUT), 1)
        finally:
            jd.save_goal_archive = orig
        live = jd.load_goals(SID)
        self.assertNotIn(doomed, live["nodes"], "the sweep's rebase did not re-adopt its own pop")
        self.assertIn(doomed, jd.load_goal_archive(SID)["nodes"])

    def test_a_concurrent_archiver_cannot_drop_the_other_writers_payloads(self):
        """goals-archive is a blind RMW (save_goal_archive has none of save_goals' rev discipline),
        and the rewind work made concurrent same-fsid archivers routine with systematically
        DIFFERENT move sets (t-keyed sweep vs identity-keyed reconcile). Un-serialized, the writer
        holding a stale archive base dropped the other writer's nodes from the archive while the
        rewindSwept union kept them out of the live store — in NEITHER file, silent permanent loss.
        The whole RMW now holds jd._GOAL_ARCH_LOCK, so the second writer reloads a base that
        already carries the first writer's nodes. Orchestration: writer B starts first and its
        archive save stalls mid-window; writer A (the full t-keyed sweep) runs against it."""
        s = self._store()
        jd.apply_plan(s, "s1", T0 + 100, [{"do": "mint", "why": "x", "text": "Doomed one"}], [])
        jd.apply_plan(s, "s2", T0 + 110, [{"do": "mint", "why": "x", "text": "Doomed two"}], jd.open_menu(s))
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        n1, n2 = self._nid(1), self._nid(2)
        orig = jd.save_goal_archive
        main = threading.current_thread()
        b_at_save, a_done = threading.Event(), threading.Event()
        def stalling_save(fsid, arch):
            if threading.current_thread() is not main:
                b_at_save.set()          # B is mid-RMW: its base predates A's sweep
                a_done.wait(0.5)         # un-serialized, A's whole sweep lands inside this window
            orig(fsid, arch)
        b = threading.Thread(target=lambda: jd.archive_goal_nodes(
            SID, jd.load_goals(SID), {n2}, T0 + 200))
        jd.save_goal_archive = stalling_save
        try:
            b.start()
            b_at_save.wait(5)
            jd.drop_goals_after(SID, T0 + 90)          # writer A: the t-keyed sweep takes BOTH nodes
            a_done.set()
            b.join(10)
        finally:
            jd.save_goal_archive = orig
        self.assertFalse(b.is_alive(), "writer B finished")
        arch = jd.load_goal_archive(SID)["nodes"]
        live = jd.load_goals(SID)["nodes"]
        for nid in (n1, n2):
            self.assertIn(nid, arch, "every swept payload survives in the archive: %s" % nid)
            self.assertNotIn(nid, live)

    def test_an_undo_clear_journal_restore_pops_the_tombstone(self):
        # a user restore outranks the marker: the journal re-inserts the node AND clears its
        # tombstone — and STAMPS rewindRestored, so the next rebase (which re-unions the stale
        # disk marker) orders the two events and lets the restore win instead of re-deleting
        # what the user brought back. The row's t postdates the sweep, as any real restore does
        # (both are wall-clock event times; the ordering is exactly what the stamps encode).
        doomed = self._seed()
        jd.drop_goals_after(SID, CUT)
        arch = jd.load_goal_archive(SID)
        payload = dict(arch["nodes"].pop(doomed))     # the undo pulled it OUT of the archive…
        jd.save_goal_archive(SID, arch)
        rt = int(time.time()) + 10                    # …after the sweep, as restores always are
        jd.append_restore(SID, {doomed: payload}, {}, rt)   # …and journaled the payload
        live = jd.load_goals(SID)                     # replay re-inserts (in neither store nor archive)
        self.assertIn(doomed, live["nodes"])
        self.assertNotIn(doomed, live.get("rewindSwept", {}), "the restore popped the tombstone")
        self.assertEqual(live.get("rewindRestored", {}).get(doomed), rt,
                         "…and left the durable restore stamp in its place")
        jd.save_goals(SID, live)                      # a follow-on rebase cycle must not re-delete it
        again = jd.load_goals(SID)
        self.assertIn(doomed, again["nodes"])

    def test_a_stale_writer_cannot_resurrect_a_popped_tombstone_after_a_restore(self):
        # a pass holds a pre-restore snapshot (marker present, node swept) across a 30-80s model
        # call; the user restores; the pass publishes. Its stale marker re-unions — the restore
        # stamp must neutralize it, or the just-restored node is re-killed and ends in NEITHER
        # file (the review reproduced exactly that: marker back, node gone, archive empty).
        doomed = self._seed()
        jd.drop_goals_after(SID, CUT)
        stale = jd.load_goals(SID)                    # the pass's snapshot: marker present, node gone
        arch = jd.load_goal_archive(SID)              # the user restores via the journal (the
        payload = dict(arch["nodes"].pop(doomed))     # kernel undo-clear's exact moves)
        jd.save_goal_archive(SID, arch)
        jd.append_restore(SID, {doomed: payload}, {}, int(time.time()) + 10)
        live = jd.load_goals(SID)
        self.assertIn(doomed, live["nodes"], "premise: the restore landed")
        jd.save_goals(SID, live)                      # restored state published
        stale["nodes"][self._nid(1)]["mt"] = T0 + 5   # the stale pass did unrelated work…
        jd.save_goals(SID, stale)                     # …and publishes across the restore
        # the raw published file is the assertion that actually pins the rebase's restore-wins
        # ordering: load_goals' journal replay re-inserts the node in memory on every load, so a
        # file-level re-kill was healed before the load-based asserts below ever ran — this test
        # passed on the pre-fix kernel and under an eff-ignores-restores mutant (the review
        # proved both), while feed/raw-snapshot readers lost the node until the next healed load
        raw = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertIn(doomed, raw["nodes"],
                      "…in the RAW published file itself — load_goals' journal replay heals "
                      "in memory and must not be what keeps this test green")
        after = jd.load_goals(SID)
        self.assertIn(doomed, after["nodes"], "the stale marker lost to the restore stamp")
        # and the node is in exactly one place — never resident in live AND archive at once
        self.assertNotIn(doomed, jd.load_goal_archive(SID)["nodes"])


class SameSecondTieOrdering(RevertBase):
    """Sweep and restore stamps are whole seconds and the rebase gives ties to the restore — so a
    restore and a superseding event in ONE wall-clock second used to collapse into the wrong
    winner (or into both at once). The converged ordering: every superseding event stamps PAST the
    marker it pops (a re-sweep tombstones strictly after the restore it observed; a restore stamps
    at-or-above the marker it popped, winning its designed tie), and archive_goal_nodes un-archives
    whatever its own save-rebase hands back — so after ANY interleaving of sweeps, restores and
    stale writers, a node id is live or archived, never both. All raw-file asserts on purpose:
    load_goals' journal replay heals some of these shapes in memory and must not be what keeps a
    test green (the lesson of the hollow stale-writer test this suite used to carry)."""

    S = NOW              # the shared wall-clock second every collision in this class lands in
    RT = NOW + 10        # the restore gesture's second, when it is NOT the colliding event

    def _seed(self):
        s = self._store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Survivor"}], [])
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "mint", "why": "x", "text": "Doomed"}], jd.open_menu(s))
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        return self._nid(2)

    def _sweep_then_restore(self, doomed, tomb_t, rt):
        """Sweep the doomed card at tomb_t, then user-restore it at rt (the undo-clear's exact
        moves: pop the archive copy, journal the payload, let the replay re-insert), and publish
        the restored world."""
        jd.archive_goal_nodes(SID, jd.load_goals(SID), {doomed}, tomb_t)
        arch = jd.load_goal_archive(SID)
        payload = dict(arch["nodes"].pop(doomed))
        jd.save_goal_archive(SID, arch)
        jd.append_restore(SID, {doomed: payload}, {}, rt)
        live = jd.load_goals(SID)
        self.assertIn(doomed, live["nodes"], "premise: the restore landed")
        jd.save_goals(SID, live)

    def _raw(self):
        return json.loads((jd.GOALDIR / (SID + ".json")).read_text())

    def test_a_same_second_re_sweep_outlasts_a_stale_post_restore_snapshots_rebase(self):
        # sweep@S-10 → restore@RT → a genuinely NEW sweep re-takes the card in the RESTORE'S OWN
        # second (an undo-restore on the WS thread and a branch-take on the settle thread are
        # concurrent — one second apart is routine). The re-sweep observed the stamp, so its
        # tombstone orders strictly after it; a judge pass whose snapshot still carries the popped
        # restore stamp then publishes, re-unioning it — pre-fix the S==S tie read as restore-wins
        # and resurrected the node into the live store while its payload sat in the archive.
        doomed = self._seed()
        self._sweep_then_restore(doomed, self.S - 10, self.RT)
        stale = jd.load_goals(SID)                    # the pass's snapshot: node live, restored=RT
        jd.archive_goal_nodes(SID, jd.load_goals(SID), {doomed}, self.RT)   # the re-take, same second
        raw = self._raw()
        self.assertGreater(raw["rewindSwept"][doomed], self.RT,
                           "the tombstone stamps strictly after the restore it popped")
        self.assertNotIn(doomed, raw["nodes"], "the re-sweep's own save held its tombstone")
        stale["nodes"][self._nid(1)]["mt"] = T0 + 7   # the stale writer did unrelated work…
        jd.save_goals(SID, stale)                     # …and publishes the popped stamp back
        raw = self._raw()
        self.assertNotIn(doomed, raw["nodes"], "the re-union of the popped restore stamp lost")
        self.assertIn(doomed, jd.load_goal_archive(SID)["nodes"], "…and the node is archive-only")

    def test_a_same_second_re_sweep_survives_its_own_save_rebase_over_a_midflight_publish(self):
        # the same tie, hit by the sweep ITSELF: any concurrent publish between the re-sweep's load
        # and its save bumps the rev, so its save_goals rebases — and pre-fix its rebase re-adopted
        # the very node it had just archived (its own fresh tombstone neutralized by the equal
        # restore stamp coming back off the mid-flight writer's snapshot).
        doomed = self._seed()
        self._sweep_then_restore(doomed, self.S - 10, self.RT)
        mid = jd.load_goals(SID)                      # the mid-flight writer: node live, restored=RT
        orig = jd.save_goal_archive
        fired = []
        def hijack(fsid, arch):                       # runs INSIDE archive_goal_nodes, between its
            orig(fsid, arch)                          # archive save and its goals save
            if not fired:
                fired.append(1)
                mid["nodes"][self._nid(1)]["mt"] = T0 + 9
                jd.save_goals(SID, mid)               # …and publishes (disk rev moves)
        jd.save_goal_archive = hijack
        try:
            jd.archive_goal_nodes(SID, jd.load_goals(SID), {doomed}, self.RT)
        finally:
            jd.save_goal_archive = orig
        raw = self._raw()
        self.assertNotIn(doomed, raw["nodes"], "the sweep's rebase did not re-adopt its own pop")
        self.assertGreater(raw["rewindSwept"][doomed], self.RT)
        self.assertIn(doomed, jd.load_goal_archive(SID)["nodes"], "archive-only — never dual-resident")

    def test_a_restore_in_the_same_second_as_the_sweep_it_pops_still_wins(self):
        # the DESIGNED tie is untouched: sweep@S, user restore in the same second — the restore
        # stamps at the popped marker's value and the rebase gives it the tie, so a stale writer
        # re-unioning the old marker still loses.
        doomed = self._seed()
        jd.archive_goal_nodes(SID, jd.load_goals(SID), {doomed}, self.S)
        stale = jd.load_goals(SID)                    # pre-restore snapshot: marker present, node gone
        arch = jd.load_goal_archive(SID)
        payload = dict(arch["nodes"].pop(doomed))
        jd.save_goal_archive(SID, arch)
        jd.append_restore(SID, {doomed: payload}, {}, self.S)   # the same-second undo
        live = jd.load_goals(SID)
        self.assertIn(doomed, live["nodes"], "premise: the journal replay re-inserted it")
        self.assertEqual(live["rewindRestored"][doomed], self.S,
                         "stamped at the popped marker — the tie the restore is designed to win")
        jd.save_goals(SID, live)
        stale["nodes"][self._nid(1)]["mt"] = T0 + 7
        jd.save_goals(SID, stale)
        raw = self._raw()
        self.assertIn(doomed, raw["nodes"], "the restore won its tie in the raw published file")
        self.assertNotIn(doomed, jd.load_goal_archive(SID)["nodes"], "…live-only, never dual-resident")

    def test_a_sweep_blind_to_the_restore_never_leaves_a_dual_resident(self):
        # the ONE tie strict stamping cannot break: the sweeping writer's snapshot predates the
        # whole sweep/restore cycle (the node is live in it with NO stamp to pop and bump past), so
        # its tombstone lands bare-equal against the restore stamp on disk. The restore wins the
        # tie — by design — and the rebase re-adopts the node; pre-fix the sweep's fresh archive
        # copy stayed behind: live AND archived at once, permanently (the reconciler exempts
        # restored ids and nothing ever removed the copy). The post-save backstop un-archives
        # exactly what the rebase handed back.
        doomed = self._seed()
        snap = jd.load_goals(SID)                     # the blind writer's snapshot, loaded first
        self._sweep_then_restore(doomed, self.S - 10, self.RT)
        jd.archive_goal_nodes(SID, snap, {doomed}, self.RT)   # blind re-take, tied with the restore
        raw = self._raw()
        self.assertIn(doomed, raw["nodes"], "the restore won the tie — the node is live")
        self.assertNotIn(doomed, jd.load_goal_archive(SID)["nodes"],
                         "…and the blind sweep's archive copy is gone: never resident in both")
        again = jd.load_goals(SID)                    # the state is stable, not a one-publish fluke
        again["nodes"][self._nid(1)]["mt"] = T0 + 9
        jd.save_goals(SID, again)
        raw = self._raw()
        self.assertIn(doomed, raw["nodes"])
        self.assertNotIn(doomed, jd.load_goal_archive(SID)["nodes"])

    def test_a_blind_sweep_strictly_after_the_restore_still_deletes(self):
        # control for the backstop: fresh evidence one second later beats the restore — the node
        # archives cleanly and the backstop touches nothing (back is empty).
        doomed = self._seed()
        snap = jd.load_goals(SID)
        self._sweep_then_restore(doomed, self.S - 10, self.RT)
        jd.archive_goal_nodes(SID, snap, {doomed}, self.RT + 1)
        raw = self._raw()
        self.assertNotIn(doomed, raw["nodes"], "a strictly newer sweep still deletes")
        self.assertIn(doomed, jd.load_goal_archive(SID)["nodes"], "archive-only")


if __name__ == "__main__":
    unittest.main()
