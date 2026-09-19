#!/usr/bin/env python3
"""kernel/judge.py binds ONE module-level `_top_of`, "the top-level ancestor of nid (cycle-safe)", which returns the
last id its parent walk reached. On a chain whose parentId names no node (the node a rewind swept, a whole-node pop, a
rebase that raced) that is the dangling parent id, never None; the closer helpers (_newest_filed, _node_carded) read
that shape through `nodes.get(x)` and `top in live`, so a dangling top files nothing and renders nowhere. A second
definition once sat above `_demote_session_mints`, added with the origin rule (T319) and returning None on such a
chain; Python binds the later definition, so that body never ran, and every caller has always seen the id-returning
one. This module pins the single definition (by ast, so a comment naming the def neither satisfies nor breaks it), the
walk's answers on a rooted chain, a dead-ended chain and a cycle, what the closer helpers answer on a dead-ended chain,
and what each of the origin rule's callers does with one: the guarded readers (`nodes.get(top) or {}`) treat the
dangling id as no top, and the minting rule reads it as no placement, so a process step nests under the reply's ask,
else the open top nearest in words, else files nothing, as its docstring says, instead of raising KeyError on the
dangling id.

Synthetic fixtures only: a PRIVATE synthetic sid (never the shared placeholder, per the goal-store fixture rule), an
invented project, no store file and no journal (tearDown clears this sid's override journal all the same)."""
import ast
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_top_of", os.path.join(BIN, "romp-judge"))

SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee1"     # this module's PRIVATE synthetic sid
T0 = 1781100000


def gid(n):
    return SID + ":g%d" % n


GHOST = gid(0)                                   # a parent id no node carries: the node a rewind swept


def node(n, text, parent=None, **extra):
    nd = {"id": gid(n), "text": text, "parentId": parent, "nodeComplete": False, "cleared": False,
          "t": T0 + n, "promptUuid": "u%d" % n}
    nd.update(extra)
    return nd


def make_store(*nodes):
    return {"rompUuid": SID, "seq": len(nodes), "placements": {}, "nodes": {nd["id"]: nd for nd in nodes}}


class _Base(unittest.TestCase):
    def tearDown(self):
        # the goal-store fixture rule: this sid's override journal never outlives the module
        try:
            os.remove(str(jd._overrides_dir() / (SID + ".jsonl")))
        except FileNotFoundError:
            pass

    def _forest(self, **tops):
        """Three chains in one store: ROOTED (g3 under g2 under the top g1), DEAD-ENDED (g5 under g4, whose parent
        GHOST is no node), and a two-node CYCLE (g6 and g7). `tops` adds fields to g1 and g4, the highest surviving
        node of each of the first two chains, so a case can give both the same fields and vary the chain alone."""
        return make_store(
            node(1, "Add retries to the notes-api client", **tops),
            node(2, "Wrote the retry loop", parent=gid(1)),
            node(3, "Ran the retry tests", parent=gid(2)),
            node(4, "Rename the widget colours", parent=GHOST, **tops),
            node(5, "Listed the colour tokens", parent=gid(4)),
            node(6, "Cycle head", parent=gid(7)),
            node(7, "Cycle tail", parent=gid(6)),
        )


class TopOf(_Base):
    def test_the_module_defines_top_of_once_the_id_returning_body(self):
        # By ast, not by source text: a comment or docstring that mentions the def neither satisfies nor breaks the
        # pin. With no linter in CI, this is what stops a second definition from shadowing the one every caller runs.
        with open(os.path.realpath(jd.__file__), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        defs = [n.lineno for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_top_of"]
        self.assertEqual(len(defs), 1, "one module-level def _top_of, at lines %s" % defs)
        self.assertEqual(jd._top_of.__doc__, "The top-level ancestor of nid (cycle-safe).",
                         "the surviving body is the closer helpers' (a reworded docstring means updating this pin)")

    def test_a_top_is_its_own_top_and_a_rooted_chain_walks_to_it(self):
        nodes = self._forest()["nodes"]
        self.assertEqual(jd._top_of(nodes, gid(1)), gid(1))
        self.assertEqual(jd._top_of(nodes, gid(2)), gid(1))
        self.assertEqual(jd._top_of(nodes, gid(3)), gid(1))

    def test_a_dead_ended_chain_returns_the_last_id_reached_never_none(self):
        nodes = self._forest()["nodes"]
        self.assertNotIn(GHOST, nodes)
        for nid in (gid(4), gid(5)):
            top = jd._top_of(nodes, nid)
            self.assertIsNotNone(top, "the id-returning contract the closer helpers read")
            self.assertEqual(top, GHOST, "the dangling parent id, the last id the walk reached")
            self.assertNotIn(top, nodes)
        self.assertEqual(jd._top_of(nodes, gid(9)), gid(9), "an id no node carries is its own top")

    def test_a_cycle_closes_the_walk_without_hanging(self):
        nodes = self._forest()["nodes"]
        self.assertEqual(jd._top_of(nodes, gid(6)), gid(6), "the walk stops at the first id it revisits")
        self.assertEqual(jd._top_of(nodes, gid(7)), gid(7))


class T319CallersOnADeadEndedChain(_Base):
    """The origin rule's callers, each given the rooted chain and the dead-ended chain with the same fields on the
    chain's highest surviving node; only the chain differs."""

    def test_the_delegator_is_read_from_the_top_so_a_dead_ended_chain_has_none(self):
        store = self._forest(origin={"peer": "web"})
        self.assertEqual(jd._delegator_of(store, gid(3)), "web", "rooted: the top's courier-planted origin")
        self.assertIsNone(jd._delegator_of(store, gid(5)), "dead-ended: the top is the ghost, which has no origin")
        self.assertIsNone(jd._delegator_of(store, gid(4)), "the node's OWN origin is never the top's")

    def test_a_block_under_a_dead_ended_chain_stays_the_users_own(self):
        store = self._forest(origin={"peer": "web"})
        handoff = {"peer": "web", "t": T0 + 50}
        store["nodes"][gid(3)]["handoff"] = dict(handoff)
        store["nodes"][gid(5)]["handoff"] = dict(handoff)
        why = "which colour should the widget use?"
        self.assertEqual(jd.block_addressee_via(store, store["nodes"][gid(3)], why), ("web", "ask"),
                         "rooted under a delegated top: the block waits on the peer the handoff names")
        self.assertEqual(jd.block_addressee_via(store, store["nodes"][gid(5)], why), (None, None),
                         "dead-ended: no delegator, so the block is the user's own decision")

    def test_the_peer_edge_under_a_dead_ended_chain_opens_the_ask_window_at_the_step_itself(self):
        # block_addressee_via stands down at _delegator_of before it reaches this reader, so it is called directly. The
        # open-ask window opens at the top's mint when the top is a node, else at the step's own; no open ask is out,
        # so the answer is the delegator's relay, and a dead-ended chain has no delegator.
        store = self._forest(origin={"peer": "web"})
        calls, real = [], jd._open_ask_peers
        jd._open_ask_peers = lambda sid, since=0: calls.append((sid, since)) or []
        self.addCleanup(setattr, jd, "_open_ask_peers", real)
        why = "which colour should the widget use?"
        self.assertEqual(jd._block_peer_edge(store, store["nodes"][gid(3)], why), ("web", "delegator", 0),
                         "rooted: no open ask, so the block is relayed to the delegator")
        self.assertEqual(jd._block_peer_edge(store, store["nodes"][gid(5)], why), (None, "delegator", 0),
                         "dead-ended: no delegator to relay to")
        self.assertEqual(calls, [(SID, T0 + 1), (SID, T0 + 5)],
                         "the window opens at the top's mint (g1), and at the step's own mint when the top is no node")

    def test_a_split_under_a_dead_ended_chain_still_promotes_the_step_and_borrows_no_anchor(self):
        store = self._forest(askAnchor="human", askAnchorRecord={"kind": "typed"}, promptMsgId="m1")
        op = [{"do": "split", "goal": 1, "why": "a drifted tangent"}]
        self.assertEqual(jd.apply_group(store, [{"id": gid(3)}], list(op), T0 + 500), 1)
        g3 = store["nodes"][gid(3)]
        self.assertIsNone(g3["parentId"])
        self.assertEqual((g3.get("askAnchor"), g3.get("promptMsgId")), ("human", "m1"),
                         "rooted: the human top's verdict comes along with the promoted step")
        self.assertEqual(jd.apply_group(store, [{"id": gid(5)}], list(op), T0 + 500), 1)
        g5 = store["nodes"][gid(5)]
        self.assertIsNone(g5["parentId"], "the step still becomes a card of its own")
        self.assertNotIn("askAnchor", g5, "dead-ended: the ghost has no verdict to borrow")
        self.assertNotIn("promptMsgId", g5)

    def test_a_process_mint_placed_under_a_dead_ended_chain_nests_under_the_nearest_open_top(self):
        """A seam tail (or a target, or a recorded placement) naming a node under a dead-ended chain hands the rule
        the dangling id as the top. The rule reads that as no placement, the way its other callers read it, and the
        process step takes the docstring's next road: the open top nearest in words, else nothing. Before the guard
        the nest dereferenced the dangling id and the planner pass crashed on a KeyError naming it, every pass,
        until the store changed. A second open top whose words are the mint's own tells the two roads apart: the
        seam's top wins while the chain is rooted, the nearest words only once it is not."""
        store = self._forest()
        store["nodes"][gid(8)] = node(8, "Review the widget colours guard")   # the open top nearest the mint's words
        menu = [{"id": nid} for nid in store["nodes"]]
        mint = {"do": "mint", "why": "a nightly review round", "text": "Guard review of the widget colours"}
        def nested_under(n):
            return [{"do": "sub", "why": mint["why"], "parentId": gid(n), "text": mint["text"],
                     "born": {"kind": "session", "via": "work", "why": mint["why"],
                              "parentText": store["nodes"][gid(n)]["text"]}}]
        rooted = {"id": "s1", "trigger": None, "atoms": [], "seamOf": {"top": gid(3), "text": "..."}}
        self.assertEqual(jd._demote_session_mints([dict(mint)], rooted, store, menu, None, False), nested_under(1),
                         "rooted (the control): the step nests under the seam's own top, not the top nearest its words")
        self.assertEqual(jd._demote_session_mints([dict(mint)], rooted, store, menu, gid(8), False), nested_under(1),
                         "the seam's top is read before the target")
        dead = {"id": "s1", "trigger": None, "atoms": [], "seamOf": {"top": gid(5), "text": "..."}}
        self.assertEqual(jd._demote_session_mints([dict(mint)], dead, store, menu, None, False), nested_under(8),
                         "dead-ended: no placement, so the step nests under the open top nearest its words (g4's "
                         "parent is the ghost, so g4 is not a top)")
        self.assertEqual(jd._demote_session_mints([dict(mint)], dead, store, [], None, False), [],
                         "dead-ended with no open top: nothing the user asked for to nest under, so the step files "
                         "nothing (not a human segment, so the ops are not restored)")


class ClosersOnADeadEndedChain(_Base):
    """The closer helpers read `_top_of`'s answer as it is: a dangling top is a top no node carries, so the walk from it
    reaches no filing and the node under it renders on no card. Both would answer the same for None, so neither
    depends on the id-returning shape; these pins say what they do with it."""

    def test_the_newest_filing_is_read_from_the_top_so_a_dead_ended_chain_has_none(self):
        nodes = self._forest()["nodes"]
        nodes[gid(3)]["log"] = [{"at": T0 + 900}]
        nodes[gid(5)]["log"] = [{"at": T0 + 900}]
        children = {}
        for nid, nd in nodes.items():                # the closers' own child map, keyed by parentId, the ghost included
            children.setdefault(nd.get("parentId"), []).append(nid)
        self.assertEqual(children[GHOST], [gid(4)], "the dangling id is a key of the child map")
        self.assertEqual(jd._newest_filed(nodes, children, gid(3)), T0 + 900,
                         "rooted: the walk from g1 reaches g3's row")
        self.assertEqual(jd._newest_filed(nodes, children, gid(5)), 0,
                         "dead-ended: the walk starts at the ghost, finds no node there and never descends to g5's row")

    def test_a_node_under_a_dead_ended_chain_renders_on_no_card(self):
        live = self._forest()["nodes"]
        self.assertTrue(jd._node_carded(live, gid(3)), "rooted: the chain lands on a live top")
        self.assertFalse(jd._node_carded(live, gid(5)), "dead-ended: the top is the ghost, which is no node")
        self.assertFalse(jd._node_carded(live, gid(4)), "the node whose own parent is the ghost")
        self.assertFalse(jd._node_carded(live, gid(6)), "a cycle lands on no top")


if __name__ == "__main__":
    unittest.main()
