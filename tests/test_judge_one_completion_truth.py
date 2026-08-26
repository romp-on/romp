#!/usr/bin/env python3
"""One truth for "complete", one node per to-do key (the user 2026-08-12/13).

The incident this pins, reproduced synthetically: a grouper merge left two goal nodes carrying the
same agentTask mirror key; plan-sync's old key→nid dict kept only the LAST, so the shadowed OPEN
mirror never heard its live to-do complete, is_complete's open_task authority veto held the umbrella
at 'working' — and the nudge backstop, reading the RAW nodeComplete flag instead of the rollup's own
verdict, silently dropped the goal every tick. 19 hours, no mover, no indication. Three mechanisms
deleted/merged:
  * plan-sync reconciles EVERY node holding a key (the lossy by_key compression is gone), heals a
    duplicated key toward done (the done twin is never re-opened), and surfaces the collision loudly;
  * _rebase_onto_disk keys deletion on the merge's own durable record (mergedFrom tombstones) instead
    of snapshot presence, so a stale pre-merge writer can no longer resurrect a merged-away node —
    the door the duplicate came through — and a dropped duplicate's placements re-point to the
    survivor so the planner cannot re-mint it through the unplaced-segments door;
  * the nudge ladder's four completion predicates collapse onto the rollup exports (status +
    confirming) — the same truth the feed renders — and load_goals re-runs rollup after an override
    replay that wrote anything, so those exports are never staler than a user gesture.

Synthetic stores only: placeholder UUID, invented goal text, tmp CLAUDE_CONFIG_DIR task stores.
"""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
jd = SourceFileLoader("romp_judge_onetruth", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_onetruth", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000


def _node(nid, **kw):
    d = {"id": nid, "parentId": None, "nodeComplete": False, "blocked": False, "cleared": False,
         "t": T0, "mt": T0, "text": "invented goal", "log": []}
    d.update(kw)
    return d


def _store(nodes):
    return jd._guard_nodes({"rompUuid": SID, "seq": len(nodes) + 10, "nodes": nodes,
                            "placements": {}, "status": {}})


def _task_store(items):
    """A tmp CLAUDE_CONFIG_DIR live task store for SID; returns the config dir."""
    cfg = tempfile.mkdtemp()
    d = os.path.join(cfg, "tasks", SID)
    os.makedirs(d)
    for key, status in items.items():
        with open(os.path.join(d, "%s.json" % key), "w") as f:
            json.dump({"id": key, "subject": "step %s" % key, "status": status}, f)
    return cfg


class DuplicateKeyHeals(unittest.TestCase):
    """The live g17 shape: umbrella + open twin shadowing a completed item, healed with no surgery."""

    def _incident_store(self):
        # T101 note: containers dissolve in rollup now, so the shape uses a PLAIN parent — the
        # subject here (duplicate to-do keys healing) never depended on the umbrella tag
        top = _node(SID + ":g1", nodeComplete=True)
        done_twin = _node(SID + ":g2", parentId=SID + ":g1",
                          agentTask={"key": "5", "status": "done", "raw": "completed"},
                          agentBornOpen=True, agentDone=True, nodeComplete=True)
        shadowed = _node(SID + ":g3", parentId=SID + ":g1",
                         agentTask={"key": "5", "status": "open", "raw": "in_progress"},
                         agentBornOpen=True)
        genuinely_open = _node(SID + ":g4", parentId=SID + ":g1",
                               agentTask={"key": "4", "status": "open", "raw": "pending"},
                               agentBornOpen=True)
        return _store({n["id"]: n for n in (top, done_twin, shadowed, genuinely_open)})

    def test_the_shadowed_open_mirror_hears_its_items_completion(self):
        store = self._incident_store()
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = _task_store({"5": "completed", "4": "in_progress"})
        try:
            changed = jd._sync_declared_plan(store, {"leafFsid": SID}, "seg1", T0 + 100)
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
        self.assertTrue(changed)
        g3 = store["nodes"][SID + ":g3"]
        self.assertEqual((g3.get("agentTask") or {}).get("status"), "done",
                         "every holder hears the key's events — no last-wins shadowing")
        self.assertTrue(g3.get("agentDone"))
        g4 = store["nodes"][SID + ":g4"]
        self.assertEqual((g4.get("agentTask") or {}).get("status"), "open",
                         "the genuinely open item keeps the veto honest")
        jd.rollup_status(store, session_closed=False)
        self.assertEqual(store["status"].get(SID + ":g1"), "working",
                         "the umbrella still waits on the REAL open item — the veto shrank to the truth")

    def test_completing_the_last_item_completes_the_umbrella(self):
        store = self._incident_store()
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = _task_store({"5": "completed", "4": "completed"})
        try:
            jd._sync_declared_plan(store, {"leafFsid": SID}, "seg1", T0 + 100)
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
        jd.rollup_status(store, session_closed=False)
        self.assertIn(store["status"].get(SID + ":g1"), ("completed", "working"),
                      "with every mirror done the open_task veto is gone")
        self.assertNotEqual((store["nodes"][SID + ":g3"].get("agentTask") or {}).get("status"), "open")

    def test_the_done_twin_is_never_reopened_and_the_collision_is_loud(self):
        store = self._incident_store()
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = _task_store({"5": "in_progress", "4": "pending"})
        try:
            jd._sync_declared_plan(store, {"leafFsid": SID}, "seg1", T0 + 100)
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
        g2 = store["nodes"][SID + ":g2"]
        self.assertEqual((g2.get("agentTask") or {}).get("status"), "done",
                         "reconciliation must not mint a card move the agent never made")
        self.assertTrue(g2.get("agentDone"))
        rows = [json.loads(l) for l in open(jd.ERRORS).read().splitlines() if l.strip()]
        self.assertTrue(any(r.get("err") == "task-key-collision" and r.get("fsid") == SID for r in rows),
                        "a live duplicate key surfaces loudly while self-healing")


class RebaseTombstones(unittest.TestCase):
    """The door the duplicate came through: snapshot presence is not truth — the merge event is."""

    def _merged_disk(self):
        surv = _node(SID + ":g1", mergedFrom=[{"id": SID + ":g2", "text": "twin", "why": "same work", "at": T0 + 50}])
        return _store({surv["id"]: surv})

    def test_a_stale_writer_cannot_resurrect_a_merged_node(self):
        disk = self._merged_disk()
        (jd.GOALDIR).mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(disk))
        try:
            stale = _store({SID + ":g1": _node(SID + ":g1"),
                            SID + ":g2": _node(SID + ":g2", log=[{"ev_t": T0 + 60, "at": T0 + 60,
                                                                  "src": "planner", "kind": "step"}])})
            stale["placements"] = {"segA": SID + ":g2"}
            stale["lastNode"] = SID + ":g2"
            jd._rebase_onto_disk(SID, stale)
            self.assertNotIn(SID + ":g2", stale["nodes"], "a tombstoned id is deleted, never adopted or kept")
            self.assertEqual(stale["placements"].get("segA"), SID + ":g1",
                             "a dropped duplicate's placements re-point to the survivor — no unplaced re-mint door")
            self.assertEqual(stale["lastNode"], SID + ":g1")
            surv_log = stale["nodes"][SID + ":g1"].get("log") or []
            self.assertTrue(any(e.get("ev_t") == T0 + 60 for e in surv_log),
                            "the dead identity goes; its EVENTS fold into the survivor (append-only covenant)")
        finally:
            (jd.GOALDIR / (SID + ".json")).unlink()

    def test_the_reverse_direction_and_chained_merges(self):
        # memory did the merge; disk still holds the dupe → not re-adopted
        disk = _store({SID + ":g1": _node(SID + ":g1"), SID + ":g2": _node(SID + ":g2")})
        (jd.GOALDIR).mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(disk))
        try:
            mem = self._merged_disk()
            jd._rebase_onto_disk(SID, mem)
            self.assertNotIn(SID + ":g2", mem["nodes"])
        finally:
            (jd.GOALDIR / (SID + ".json")).unlink()
        # chained: A merged into B, then B merged into C → C names both
        nodes = {SID + ":gA": jd.GuardedNode(_node(SID + ":gA")),
                 SID + ":gB": jd.GuardedNode(_node(SID + ":gB",
                                                   mergedFrom=[{"id": SID + ":gA", "text": "a", "why": "w", "at": T0}])),
                 SID + ":gC": jd.GuardedNode(_node(SID + ":gC"))}
        store = _store({})
        store["nodes"] = nodes
        jd._merge_nodes(store, SID + ":gB", SID + ":gC", T0 + 1, "chained")
        ids = {r.get("id") for r in (store["nodes"][SID + ":gC"].get("mergedFrom") or [])}
        self.assertEqual(ids, {SID + ":gA", SID + ":gB"}, "chained merges keep every tombstone")


class RollupAfterReplay(unittest.TestCase):
    def test_a_journaled_user_resolve_settles_the_loaded_status(self):
        store = _store({SID + ":g1": _node(SID + ":g1")})
        jd.rollup_status(store, session_closed=False)
        (jd.GOALDIR).mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        od = jd._overrides_dir()
        od.mkdir(parents=True, exist_ok=True)
        (od / (SID + ".jsonl")).write_text(json.dumps(
            {"op": "resolve", "node": SID + ":g1", "t": T0 + 500}) + "\n")
        try:
            loaded = jd.load_goals(SID)
            self.assertEqual(loaded["status"].get(SID + ":g1"), "completed",
                             "the replay WROTE, so load re-runs rollup — the exports the one-truth "
                             "readers now trust are never staler than the user's gesture")
        finally:
            (jd.GOALDIR / (SID + ".json")).unlink()
            (od / (SID + ".jsonl")).unlink()


class OneTruthOnTheNudgeLadder(unittest.TestCase):
    def test_a_vetoed_umbrella_stays_in_the_fire_list(self):
        fresh = _store({SID + ":g1": _node(SID + ":g1", nodeComplete=True)})
        fresh["status"] = {SID + ":g1": "working"}    # the rollup REFUSED the completion (open to-do veto)
        fresh["confirming"] = []
        kept = km._nudge_fire_list(fresh, [(SID + ":g1", "why")])
        self.assertEqual([k[0] for k in kept], [SID + ":g1"],
                         "the raw nodeComplete flag no longer kills the wedged card's only backstop")

    def test_a_genuinely_confirming_top_is_dropped(self):
        fresh = _store({SID + ":g1": _node(SID + ":g1", nodeComplete=True)})
        fresh["status"] = {SID + ":g1": "working"}
        fresh["confirming"] = [SID + ":g1"]           # done verdict in, settle pending — the rollup's word
        self.assertEqual(km._nudge_fire_list(fresh, [(SID + ":g1", "why")]), [])

    def test_a_resolved_status_is_dropped(self):
        fresh = _store({SID + ":g1": _node(SID + ":g1")})
        fresh["status"] = {SID + ":g1": "blocked"}
        fresh["confirming"] = []
        self.assertEqual(km._nudge_fire_list(fresh, [(SID + ":g1", "why")]), [])


if __name__ == "__main__":
    unittest.main()
