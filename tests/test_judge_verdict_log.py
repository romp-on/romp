#!/usr/bin/env python3
"""The verdict log: record_verdict + the fold (docs/goal-state.md).

record_verdict = the gate AND the recorder fused: a verdict that passes may_apply appends an event to
the node's append-only log before the caller writes the flags (flags stay authoritative until the P3.3
flip). _fold_node_state derives the node's verdict state from the log alone; the property that kills
the replay bug class: SHUFFLING the log never changes the fold (ordering is reconstructed, not
assumed). _shadow_fold_check writes fold-vs-flags divergences for logBorn tops (E4). Synthetic only."""
import json
import os
import random
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_vlog", os.path.join(BIN, "romp-judge")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
G1 = SID + ":g1"
T = 1781100000


def node(**kw):
    nd = {"id": G1, "text": "Ship it", "parentId": None, "nodeComplete": False,
          "blocked": False, "cleared": False, "trail": [], "t": T - 500, "mt": T - 100}
    nd.update(kw)
    return nd


class RecordVerdict(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        self.store = {"rompUuid": SID, "nodes": {G1: node()}, "placements": {}, "status": {}}

    def test_allowed_verdict_appends_denied_leaves_no_trace(self):
        nd = self.store["nodes"][G1]
        self.assertTrue(jd.record_verdict(self.store, nd, "judge", "done", T, why="shipped", seg="s1"))
        self.assertEqual(len(nd["log"]), 1)
        e = nd["log"][0]
        self.assertEqual((e["src"], e["kind"], e["ev_t"], e["why"], e["seg"]),
                         ("judge", "done", T, "shipped", "s1"))
        nd["followupAt"] = T + 100                        # the user acts later
        self.assertFalse(jd.record_verdict(self.store, nd, "judge", "done", T + 50, why="stale replay"))
        self.assertEqual(len(nd["log"]), 1, "a gated-out verdict leaves NO event")

    def test_log_cap_truncates_oldest(self):
        nd = self.store["nodes"][G1]
        for i in range(jd.LOG_CAP + 10):
            jd.record_verdict(self.store, nd, "judge", "done", T + i)
        self.assertEqual(len(nd["log"]), jd.LOG_CAP)
        self.assertTrue(nd["logTrunc"])
        self.assertEqual(nd["log"][0]["ev_t"], T + 10, "oldest dropped")


class TheFold(unittest.TestCase):
    def test_replayed_stale_done_loses_by_ordering_not_by_a_guard(self):
        # yesterday's bug class, told in fold terms: events may ARRIVE in any order; evidence
        # ordering decides. A stale done (ev_t before the user's reopen) appended LATE still loses.
        nd = node(log=[
            {"ev_t": T + 100, "src": "user", "kind": "reopen", "at": T + 100},
            {"ev_t": T + 50, "src": "judge", "kind": "done", "at": T + 999},   # replayed late
        ])
        self.assertEqual(jd._fold_node_state(nd), "open")

    def test_done_at_the_floor_lands_block_at_the_floor_voids(self):
        base = [{"ev_t": T + 100, "src": "user", "kind": "reopen", "at": T + 100}]
        nd = node(log=base + [{"ev_t": T + 100, "src": "judge", "kind": "done", "at": T + 101}])
        self.assertEqual(jd._fold_node_state(nd), "done", "the resolving turn shares the stamp: lands")
        nd = node(log=base + [{"ev_t": T + 100, "src": "judge", "kind": "block", "at": T + 101}])
        self.assertEqual(jd._fold_node_state(nd), "open", "a block computed from the answered ask: void")
        nd = node(log=base + [{"ev_t": T + 101, "src": "judge", "kind": "block", "at": T + 102}])
        self.assertEqual(jd._fold_node_state(nd), "blocked", "a genuinely new ask blocks")

    def test_shuffle_invariance(self):
        log = [
            {"ev_t": T + 10, "src": "judge", "kind": "done", "at": T + 11},
            {"ev_t": T + 20, "src": "user", "kind": "reopen", "at": T + 20},
            {"ev_t": T + 30, "src": "judge", "kind": "block", "at": T + 31},
            {"ev_t": T + 40, "src": "user", "kind": "reopen", "at": T + 40},
            {"ev_t": T + 50, "src": "judge", "kind": "done", "at": T + 51},
            {"ev_t": T + 15, "src": "judge", "kind": "block", "at": T + 300},  # stale replay
        ]
        want = jd._fold_node_state(node(log=list(log)))
        self.assertEqual(want, "done")
        rng = random.Random(7)
        for _ in range(20):
            rng.shuffle(log)
            self.assertEqual(jd._fold_node_state(node(log=list(log))), want,
                             "the fold must be invariant to arrival order")

    def test_agent_done_ordering_and_clear(self):
        # "the agent is never gated" governs write ACCEPTANCE (may_apply); the fold still orders by
        # evidence. The user reopening AFTER the agent's done wins (later evidence, higher authority)...
        nd = node(log=[
            {"ev_t": T + 100, "src": "user", "kind": "reopen", "at": T + 100},
            {"ev_t": T + 50, "src": "agent", "kind": "done", "at": T + 200},
        ])
        self.assertEqual(jd._fold_node_state(nd), "open", "the user's later reopen outranks the agent's earlier done")
        # ...an agent done AT the floor lands (unlike a judge block there), and after it, plainly
        nd = node(log=[
            {"ev_t": T + 100, "src": "user", "kind": "reopen", "at": T + 100},
            {"ev_t": T + 100, "src": "agent", "kind": "done", "at": T + 200},
        ])
        self.assertEqual(jd._fold_node_state(nd), "done")
        nd = node(log=[{"ev_t": T, "src": "judge", "kind": "done", "at": T},
                       {"ev_t": T + 1, "src": "user", "kind": "clear", "at": T + 1}])
        self.assertEqual(jd._fold_node_state(nd), "cleared")


class TheFlip(unittest.TestCase):
    """P3.3: the log is the authority; flags are a materialized cache rollup rewrites from history."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def test_migration_preserves_every_legacy_state(self):
        # pre-dual-write stores (flags, no diaries): migrate_store — the boot sweep's per-store step —
        # must change NOTHING visible, by construction (was the lazy in-rollup backfill; window closed
        # 2026-07-07, so tests model legacy stores by calling it explicitly).
        legacy = {
            SID + ":g1": dict(node(), id=SID + ":g1", nodeComplete=True),                       # done
            SID + ":g2": dict(node(), id=SID + ":g2", blocked=True, followupAt=T - 100),        # blocked past a follow-up
            SID + ":g3": dict(node(), id=SID + ":g3", cleared=True),                            # user-cleared
            SID + ":g4": dict(node(), id=SID + ":g4", everDone=True),                           # legacy retired flag — popped, plain open
        }
        store = {"rompUuid": SID, "placements": {}, "status": {}, "nodes": legacy}
        self.assertTrue(jd.migrate_store(store))
        jd.rollup_status(store, True)
        st = store["status"]
        self.assertEqual((st[SID + ":g1"], st[SID + ":g2"], st[SID + ":g3"], st[SID + ":g4"]),
                         ("completed", "blocked", "cleared", "working"))
        self.assertNotIn("everDone", store["nodes"][SID + ":g4"],
                         "the retired everDone flag is popped by the boot sweep")
        for nd in store["nodes"].values():
            self.assertIn("log", nd, "every node leaves migration with a diary")
            self.assertNotIn("logBorn", nd, "the logBorn marker is retired — the diary key IS the marker")
            self.assertTrue(all(e.get("synth") for e in nd["log"] if e["kind"] != "settle"),
                            "migrated VERDICT events are tagged synth (the completed top's settle is a"
                            " REAL event — this pass genuinely settled it for the first time)")
        before = {nid: (nd["nodeComplete"], nd["blocked"], nd["cleared"]) for nid, nd in store["nodes"].items()}
        jd.rollup_status(store, True)                 # idempotent: a second pass changes nothing
        after = {nid: (nd["nodeComplete"], nd["blocked"], nd["cleared"]) for nid, nd in store["nodes"].items()}
        self.assertEqual(before, after)
        self.assertFalse(jd.migrate_store(store), "a second migrate is a no-op")

    def test_unmigrated_flagged_node_is_frozen_not_wiped(self):
        # FAIL LOUDLY: a verdict-flagged node with NO diary key means the boot sweep missed it. Deriving
        # would wipe its state (an empty fold is open) — instead the flags freeze and the error surfaces.
        legacy = dict(node(), nodeComplete=True)      # no log key at all
        store = {"rompUuid": SID, "placements": {}, "status": {}, "nodes": {G1: legacy}}
        jd.rollup_status(store, True)
        self.assertTrue(legacy["nodeComplete"], "frozen, not wiped")
        self.assertEqual(store["status"][G1], "completed")
        errs = (jd.STATE / "judge-errors.jsonl")
        self.assertTrue(errs.exists() and "unmigrated-node" in errs.read_text(),
                        "…and the miss is surfaced in judge-errors.jsonl")

    def test_history_overwrites_an_out_of_band_flag_write(self):
        # THE TEETH: a flag mutated without an event is restored from history on the next rollup
        nd = node(logBorn=True, nodeComplete=False,
                  log=[{"ev_t": T, "src": "judge", "kind": "done", "at": T}])
        store = {"rompUuid": SID, "placements": {}, "status": {}, "nodes": {G1: nd}}
        jd.rollup_status(store, True)
        self.assertTrue(nd["nodeComplete"], "the log's done outranks the wiped flag")
        self.assertEqual(store["status"][G1], "completed")
        # and the reverse: a hand-set done with NO history is demoted
        ghost = dict(node(), id=SID + ":g2", logBorn=True, nodeComplete=True, log=[])
        store["nodes"][SID + ":g2"] = ghost
        jd.rollup_status(store, True)
        self.assertFalse(ghost["nodeComplete"], "a flag with no history behind it does not survive")
        self.assertEqual(store["status"][SID + ":g2"], "working")

    def test_rolled_up_children_keep_their_tree_derived_cache(self):
        top = node(logBorn=True, nodeComplete=True,
                   log=[{"ev_t": T, "src": "judge", "kind": "done", "at": T}])
        kid = dict(node(), id=SID + ":g2", parentId=G1, logBorn=True, log=[])
        store = {"rompUuid": SID, "placements": {}, "status": {}, "nodes": {G1: top, SID + ":g2": kid}}
        jd.rollup_status(store, True)                 # roll-down resolves the open child under the done top
        self.assertTrue(kid["nodeComplete"] and kid["rolledUp"])
        jd.rollup_status(store, True)                 # materialize must not fight roll-down across passes
        self.assertTrue(kid["nodeComplete"] and kid["rolledUp"])
        self.assertEqual(store["status"][G1], "completed")


class DualWriteThroughTheSites(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def test_planner_done_records_event_and_writes_no_sampler_file(self):
        store = {"rompUuid": SID, "nodes": {G1: node()}, "placements": {}, "status": {}, "lastNode": G1}
        menu = [{"id": G1, "text": "Ship it"}]
        jd.apply_plan(store, "seg-x", T, [{"do": "done", "goal": 1, "why": "shipped"}], menu, place_key="seg-x")
        log = store["nodes"][G1]["log"]
        self.assertEqual([(e["src"], e["kind"]) for e in log], [("planner", "done")])
        self.assertEqual(log[0]["seg"], "seg-x")
        # the E6 sampler retired 2026-08-23 (P4 closed without consolidation: 597 samples, 75%
        # focus-held) — a planner done writes NO side file anymore
        self.assertFalse((jd.STATE / "eager-done-samples.jsonl").exists())

    def test_followup_reopen_records_a_user_reopen(self):
        # (user_move was removed 2026-07-25; the card reply is the surviving user-reopen producer)
        store = {"rompUuid": SID, "placements": {}, "status": {},
                 "nodes": {G1: node(nodeComplete=True)}}
        jd.rollup_status(store, True)
        jd.save_goals(SID, store)
        jd.optimistic_followup(SID, G1, now=T + 500)
        st = jd.load_goals(SID)
        kinds = [(e["src"], e["kind"], e["ev_t"]) for e in st["nodes"][G1]["log"]]
        self.assertIn(("user", "reopen", T + 500), kinds)

    def test_placement_unblock_is_event_backed(self):
        # newest-wins: filing new work on a blocked branch un-blocks it — and post-P3.3 that clear must
        # be an EVENT, or the next materialize re-blocks from the diary (found 2026-07-07).
        store = {"rompUuid": SID, "seq": 1, "nodes": {G1: node()}, "placements": {}, "status": {}}
        jd.apply_close(store, [store["nodes"][G1]], {"block": {1: "pick a name"}}, t=T)
        jd.rollup_status(store, False)
        self.assertEqual(store["status"][G1], "blocked")
        menu = [{"id": G1, "text": "Ship it"}]
        jd.apply_plan(store, "seg-w", T + 60, [{"do": "sub", "under": 1, "text": "did the pick", "why": "answered"}],
                      menu, place_key="seg-w")
        jd.rollup_status(store, False)
        self.assertEqual(store["status"][G1], "working", "new work on the branch un-blocks it and it STAYS un-blocked")
        kinds = [(e["src"], e["kind"]) for e in store["nodes"][G1]["log"]]
        self.assertIn(("planner", "unblock"), kinds)

    def test_closer_verdicts_record(self):
        store = {"rompUuid": SID, "nodes": {G1: node()}, "placements": {}, "status": {}}
        jd.apply_close(store, [store["nodes"][G1]], {"block": {1: "pick a name"}}, t=T)
        self.assertEqual([(e["src"], e["kind"], e["why"]) for e in store["nodes"][G1]["log"]],
                         [("closer", "block", "pick a name")])

    def test_mark_node_done_events_descendant_unblocks(self):
        # 2026-07-07: _mark_node_done cleared descendant blocks EVENTLESSLY — the fold re-blocked them on
        # the next materialize and the ledger showed a stale ⏸ under a ✓ parent until settle rolled it up.
        store = {"rompUuid": SID, "seq": 3, "nodes": {G1: node()}, "placements": {}, "status": {}}
        kid = dict(node(), id=SID + ":g2", parentId=G1)
        store["nodes"][SID + ":g2"] = kid
        jd.apply_close(store, [kid], {"block": {1: "pick a name"}}, t=T)
        jd.rollup_status(store, False)
        self.assertTrue(kid["blocked"])
        jd.record_verdict(store, store["nodes"][G1], "planner", "done", T + 60, why="shipped")
        jd._mark_node_done(store, G1, "shipped", T + 60)
        jd.rollup_status(store, False)
        self.assertFalse(kid["blocked"], "the child's unblock survives materialize — it is an EVENT now")
        self.assertIn(("planner", "unblock"), [(e["src"], e["kind"]) for e in kid["log"]])

    def test_moot_block_heal_is_evented_and_heals_once(self):
        # the rollup heal that clears a stale block on a completed subtree now records an unblock event,
        # so it fires ONCE instead of re-fighting the fold every pass.
        store = {"rompUuid": SID, "seq": 2, "nodes": {G1: node()}, "placements": {}, "status": {}}
        jd.apply_close(store, [store["nodes"][G1]], {"block": {1: "pick a name"}}, t=T)
        # a bottom-up completion path that never touches the node's own block: an agent-done child + an
        # explicit done on the top via a plain record (no _mark_node_done subtree walk)
        jd.record_verdict(store, store["nodes"][G1], "closer", "done", T + 60, why="shipped")
        store["nodes"][G1]["nodeComplete"] = True
        jd.rollup_status(store, False)
        self.assertFalse(store["nodes"][G1]["blocked"])
        kinds = [(e["src"], e["kind"]) for e in store["nodes"][G1]["log"]]
        self.assertEqual(kinds.count(("romp", "unblock")), 0,
                         "the done itself outranks the older block in the fold — no heal needed here")
        jd.rollup_status(store, False)                # and a second pass appends nothing new
        self.assertEqual([(e["src"], e["kind"]) for e in store["nodes"][G1]["log"]], kinds)

    def test_settle_topup_preserves_stamps_of_already_migrated_nodes(self):
        # nodes migrated during the week BEFORE settle became an event (logBorn, real logs) still carry
        # hand-written settledDone/settledAt stamps; deriving from the log alone would WIPE them and a
        # completed card would flicker back through the settle gate. _backfill_settle synthesizes the
        # missing settle event once.
        nd = node(nodeComplete=True, settledDone=True, settledAt=T + 80,
                  log=[{"ev_t": T + 50, "src": "closer", "kind": "done", "at": T + 50}])
        store = {"rompUuid": SID, "seq": 1, "nodes": {G1: nd}, "placements": {}, "status": {}}
        jd.migrate_store(store)                       # the boot sweep runs the settle top-up
        jd.rollup_status(store, True)
        self.assertEqual(nd.get("settledAt"), T + 80, "the legacy stamp survives via the synth settle event")
        self.assertTrue(nd.get("settledDone"))
        settles = [e for e in nd["log"] if e["kind"] == "settle"]
        self.assertEqual([e["ev_t"] for e in settles], [T + 80])
        self.assertFalse(jd.migrate_store(store), "idempotent: no second synth")
        jd.rollup_status(store, True)
        self.assertEqual(len([e for e in nd["log"] if e["kind"] == "settle"]), 1)

    def test_agent_reopen_is_evented(self):
        # the agent re-opening its own to-do used to drop OUR done flag eventlessly — the fold re-DONE'd
        # it on the next materialize. Now it records an agent reopen event.
        store = {"rompUuid": SID, "seq": 1, "nodes": {G1: node(logBorn=True, agentDone=True)},
                 "placements": {}, "status": {}}
        nd = store["nodes"][G1]
        jd.record_verdict(store, nd, "agent", "done", T, why="crossed off")
        jd.rollup_status(store, True)
        self.assertTrue(nd["nodeComplete"])
        jd.record_verdict(store, nd, "agent", "reopen", T + 10, why="the agent re-opened its own to-do")
        jd.rollup_status(store, True)
        self.assertFalse(nd["nodeComplete"], "the agent reopen event survives materialize")

    def test_a_stale_seq_never_mints_over_a_live_node(self):
        # found 2026-07-07: a store with a stale/absent `seq` minted …:g1 OVER the live G1, whose
        # parent was G1 itself — a self-parent cycle that hung every ancestor walk (the frozen
        # full-suite runs). The mint must skip occupied ids; the live node survives untouched.
        store = {"rompUuid": SID, "nodes": {G1: node()}, "placements": {}, "status": {}}
        menu = [{"id": G1, "text": "Ship it"}]
        jd.apply_plan(store, "seg-c", T + 60, [{"do": "sub", "under": 1, "text": "a step", "why": "w"}],
                      menu, place_key="seg-c")
        self.assertEqual(store["nodes"][G1]["text"], "Ship it", "the live node is not overwritten")
        kids = [n for n in store["nodes"].values() if n.get("parentId") == G1]
        self.assertEqual([k["text"] for k in kids], ["a step"])
        self.assertNotEqual(kids[0]["id"], G1)


if __name__ == "__main__":
    unittest.main()
