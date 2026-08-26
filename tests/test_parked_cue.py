#!/usr/bin/env python3
"""The PARKED cue (the user 2026-08-24 audit): an open row nothing has happened under — no
delegation edge, no witnessed verdict row — while YOUNGER siblings gained "↪ delegated to" handoff
edges, wears a quiet {n} marker; it retires the instant the row's own subtree gains a delegation
edge or ANY witnessed verdict row (the deciding events; synth reconstructions are not rulings).
Threshold 1, handoff-only — both measured over every live store's replay: true positives accumulate
exactly one leapfrogging sibling before retiring, and sibling VERDICT activity flaps with judge
batch cadence, which is not new information about this row. Mint x retire, pinned synthetically."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from inspect import getsource

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_parked", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


def _world(nodes_spec):
    """nodes: {short_id: (parent_short, t, extras)} -> (nodes, children) in store shape."""
    nodes = {}
    for sid_, (parent, t, extras) in nodes_spec.items():
        nid = SID + ":" + sid_
        nd = {"id": nid, "text": "item " + sid_, "parentId": (SID + ":" + parent) if parent else None,
              "nodeComplete": False, "blocked": False, "cleared": False, "t": t, "log": []}
        nd.update(extras or {})
        nodes[nid] = nd
    children = {}
    for nid, nd in nodes.items():
        children.setdefault(nd.get("parentId"), []).append(nid)
    return nodes, children


def _parked(nodes_spec):
    nodes, children = _world(nodes_spec)
    return {k.split(":")[1]: v for k, v in km._parked_rows(nodes, children).items()}


HO = {"handoff": {"peer": "66666666-7777-8888-9999-000000000000", "msgId": "m1"}}


class MintGrid(unittest.TestCase):
    def test_the_audit_shape_one_younger_dispatch_mints_the_cue(self):
        # the specimen: an older open ask under an umbrella; a younger sibling's subtree gains a
        # handoff edge -> the older one was passed over, n=1
        got = _parked({"u": (None, 50, {}),
                       "a": ("u", 100, {}),
                       "b": ("u", 200, {}),
                       "b1": ("b", 210, HO)})
        self.assertEqual(got, {"a": 1})

    def test_each_further_dispatch_raises_the_count(self):
        got = _parked({"u": (None, 50, {}),
                       "a": ("u", 100, {}),
                       "b": ("u", 200, HO),
                       "c": ("u", 300, HO)})
        self.assertEqual(got.get("a"), 2, "two younger siblings dispatched past it")

    def test_an_older_siblings_dispatch_does_not_count(self):
        got = _parked({"u": (None, 50, {}),
                       "old": ("u", 40, HO),
                       "a": ("u", 100, {})})
        self.assertNotIn("a", got, "only YOUNGER siblings leapfrog — older traffic predates the ask")

    def test_top_level_siblings_count_too(self):
        got = _parked({"a": (None, 100, {}),
                       "b": (None, 200, {}),
                       "b1": ("b", 210, HO)})
        self.assertEqual(got, {"a": 1}, "tops are siblings under the None parent")

    def test_sibling_verdict_activity_alone_never_mints(self):
        # measured strictly noisier (judge batch cadence): a younger sibling with rulings but no
        # handoff edge is not a leapfrog
        got = _parked({"u": (None, 50, {}),
                       "a": ("u", 100, {}),
                       "b": ("u", 200, {"log": [{"ev_t": 250, "src": "closer", "kind": "done",
                                                 "at": 250}]})})
        self.assertEqual(got, {})


class RetireGrid(unittest.TestCase):
    def test_own_delegation_edge_retires_it(self):
        got = _parked({"u": (None, 50, {}),
                       "a": ("u", 100, {}),
                       "a1": ("a", 400, HO),
                       "b": ("u", 200, HO)})
        self.assertNotIn("a", got, "its own dispatch is the deciding event — the wait is over")

    def test_any_witnessed_verdict_row_retires_it(self):
        for kind in ("done", "block", "awaiting", "unblock"):
            got = _parked({"u": (None, 50, {}),
                           "a": ("u", 100, {"log": [{"ev_t": 300, "src": "closer", "kind": kind,
                                                     "at": 300}]}),
                           "b": ("u", 200, HO)})
            self.assertNotIn("a", got, "a %s row is a ruling — the judges have seen it" % kind)

    def test_a_synth_row_is_not_history(self):
        # a migration reconstruction is not a ruling (judge _synth_log) — the cue stands
        got = _parked({"u": (None, 50, {}),
                       "a": ("u", 100, {"log": [{"ev_t": 100, "src": "judge", "kind": "done",
                                                 "at": 900, "synth": True}]}),
                       "b": ("u", 200, HO)})
        self.assertEqual(got.get("a"), 1)

    def test_a_row_deeper_in_the_subtree_also_retires(self):
        got = _parked({"u": (None, 50, {}),
                       "a": ("u", 100, {}),
                       "a1": ("a", 150, {"log": [{"ev_t": 300, "src": "planner", "kind": "done",
                                                  "at": 300}]}),
                       "b": ("u", 200, HO)})
        self.assertNotIn("a", got, "activity anywhere under it means it was not passed over")

    def test_resolved_rows_never_wear_it(self):
        for extras in ({"nodeComplete": True}, {"blocked": True}, {"cleared": True}):
            got = _parked({"u": (None, 50, {}),
                           "a": ("u", 100, dict(extras)),
                           "b": ("u", 200, HO)})
            self.assertNotIn("a", got)

    def test_an_only_child_never_wears_it(self):
        got = _parked({"u": (None, 50, {}), "a": ("u", 100, {})})
        self.assertEqual(got, {})

    def test_a_parent_cycle_degrades_instead_of_crashing(self):
        # a corrupted store (two rebased reparent writers can compose a cycle neither wrote) must
        # never kill the feed build — the walk terminates (review 2026-08-24, verified crash)
        nodes, children = _world({"x": ("y", 100, {}), "y": ("x", 200, {}),
                                  "z": ("y", 300, HO), "w": ("y", 50, {})})
        km._parked_rows(nodes, children)   # must return, whatever it answers for the cycle members


class BuildFeedWiring(unittest.TestCase):
    def test_the_row_field_ships_gated_on_the_open_render_state(self):
        src = getsource(km.build_feed)
        self.assertIn("parked_rows = _parked_rows(nodes, children)", src)
        self.assertIn('"parked": ({"n": parked_rows[nid]} if (st == "open" and nid in parked_rows)'
                      " else None)", src)


if __name__ == "__main__":
    unittest.main()
