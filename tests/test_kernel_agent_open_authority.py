"""build_feed / build_session must honor the AUTHORITATIVE-open tier (the user 2026-07-01): an agentTask-open
node — or an umbrella holding one — is NEVER rendered 'done', even when a nodeComplete ancestor would roll
'done' down onto it. Mirrors the judge's rollup_status. Without it, an open to-do under a flat-completed
umbrella came back status:"done" — the mark drew the hollow auth ring (CSS) but the hover read "jump to where
this got checked off" on an unchecked item.

All fixtures SYNTHETIC (placeholder UUIDs, invented text).
"""
import inspect
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _kids(nodes):
    ch = {}
    for nid, nd in nodes.items():
        ch.setdefault(nd.get("parentId"), []).append(nid)
    return ch


class AgentOpenSet(unittest.TestCase):
    def test_open_leaf_and_all_its_ancestors_are_in_the_set(self):
        # umbrella T is nodeComplete, with a genuinely-done child A and an agentTask-OPEN child B
        nodes = {
            "T": {"id": "T", "parentId": None, "nodeComplete": True},
            "A": {"id": "A", "parentId": "T", "nodeComplete": True},
            "B": {"id": "B", "parentId": "T", "nodeComplete": False, "agentTask": {"key": "2", "status": "open"}},
        }
        s = km._agent_open_set(nodes, _kids(nodes))
        self.assertEqual(s, {"T", "B"}, "the open item AND every ancestor holding it are authoritative-open")
        self.assertNotIn("A", s, "a genuinely-done sibling is not in the set")

    def test_nested_open_marks_the_whole_ancestor_chain(self):
        nodes = {
            "T": {"id": "T", "parentId": None, "nodeComplete": True},
            "M": {"id": "M", "parentId": "T", "nodeComplete": True},
            "L": {"id": "L", "parentId": "M", "nodeComplete": False, "agentTask": {"key": "9", "status": "open"}},
        }
        self.assertEqual(km._agent_open_set(nodes, _kids(nodes)), {"T", "M", "L"})

    def test_done_agenttask_is_not_open(self):
        nodes = {
            "T": {"id": "T", "parentId": None, "nodeComplete": True},
            "A": {"id": "A", "parentId": "T", "nodeComplete": True, "agentTask": {"key": "1", "status": "done"}},
        }
        self.assertEqual(km._agent_open_set(nodes, _kids(nodes)), set(), "a crossed-off to-do is not authoritative-open")

    def test_no_agenttask_nodes_empty_set(self):
        nodes = {"T": {"id": "T", "parentId": None, "nodeComplete": True},
                 "A": {"id": "A", "parentId": "T", "nodeComplete": False}}
        self.assertEqual(km._agent_open_set(nodes, _kids(nodes)), set())


class DoneComputationHonorsAuthority(unittest.TestCase):
    """Source-pins: both projections gate their done-derivation on the authoritative-open set (this repo
    tests build_feed/build_session by inspecting their source, since they read the fleet off disk)."""
    def test_build_feed_flatten_excludes_agent_open_from_done(self):
        src = inspect.getsource(km._feed_session_entry)
        self.assertIn("agent_open = _agent_open_set(nodes, children)", src)
        self.assertIn("and nid not in agent_open", src, "flatten's done must exclude the authoritative-open subtree")

    def test_build_session_ledger_excludes_agent_open_from_done(self):
        # the ledger's walk lives in the shared _goal_tree_walk since the Outline's provisional row
        # (plans/outline-pane-provisional-row.md, 2026-09-15): the done-derivation's gate is pinned there, and BOTH
        # callers, build_session and the provisional assembly, are pinned to the walk, so neither can re-derive it
        src = inspect.getsource(km._goal_tree_walk)
        self.assertIn("g_agent_open = _agent_open_set(gnodes, gkids)", src)
        self.assertIn("aopen = (nid in g_agent_open) and not clr", src)
        self.assertIn("_goal_tree_walk(sid, gstore, seg_trig, seg_work, anchors=True, pr_repo=_pr_repo)", inspect.getsource(km.build_session), "the ledger takes the shared walk")
        self.assertIn("_goal_tree_walk(sid, gstore, anchors=False)", inspect.getsource(km._provisional_ledger), "the Outline's provisional row takes the same walk")
        self.assertNotIn("_agent_open_set(", inspect.getsource(km.build_session), "no private done-derivation beside the walk")


if __name__ == "__main__":
    unittest.main()
