#!/usr/bin/env python3
"""The courier link survives planner-first placement (the user 2026-08-23): a peer-delegate segment
the planner placed before the courier saw it minted no courier goal, so the SENDER's handoff waited
on a completion event that could never fire (12 live handoffs, five ~240h old). Three fixes, all
executed here: (1) the courier's link-only repair attaches links[] to the placed goal's TOP —
never origin, which keeps its born-from meaning; (2) run_propagate completes the sender's tracking
node from links[] exactly as from origin; (3) a DORMANT sender's incomplete handoff converts to the
dead-wait procedural block. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

SENDER = "11111111-2222-3333-4444-555555555555"
RECIP = "66666666-7777-8888-9999-000000000000"
MID = "msg-aaaa-1111"
T = 1_787_500_000


def _seed_sender():
    st = jd.load_goals(SENDER)
    st["nodes"][SENDER + ":g1"] = jd.GuardedNode({
        "id": SENDER + ":g1", "text": "↪ delegated to api: build the exporter", "parentId": None,
        "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": T, "mt": T,
        "handoff": {"peer": RECIP, "msgId": MID}, "log": []})
    jd.save_goals(SENDER, st)


def _seed_recipient(placed_under_existing=True):
    st = jd.load_goals(RECIP)
    st["nodes"][RECIP + ":g5"] = jd.GuardedNode({
        "id": RECIP + ":g5", "text": "Ship the data layer", "parentId": None,
        "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": T, "mt": T, "log": []})
    if placed_under_existing:
        st["placements"]["seg-d1"] = RECIP + ":g5"
    jd.save_goals(RECIP, st)


class CourierLinkRepair(unittest.TestCase):
    def setUp(self):
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/dev/null", None, "web"), (RECIP, "/dev/null", None, "api")]
        _seed_sender()
        _seed_recipient()

    def tearDown(self):
        jd.discover = self._saved
        for f in jd.GOALDIR.glob("*"):
            f.unlink()

    def test_link_attaches_to_the_placed_top_and_is_idempotent(self):
        st = jd.load_goals(RECIP)
        self.assertTrue(jd._attach_courier_link(st, "seg-d1", MID))
        st = jd.load_goals(RECIP)
        links = st["nodes"][RECIP + ":g5"].get("links") or []
        self.assertEqual(links, [{"peer": SENDER, "goalId": SENDER + ":g1", "msgId": MID}],
                         "the link rides links[], never origin — born-from stays truthful")
        self.assertFalse(jd._attach_courier_link(st, "seg-d1", MID), "idempotent by msgId")

    def test_no_sender_backref_means_no_link(self):
        st = jd.load_goals(RECIP)
        self.assertFalse(jd._attach_courier_link(st, "seg-d1", "msg-unknown-9999"))
        self.assertNotIn("links", dict(jd.load_goals(RECIP)["nodes"][RECIP + ":g5"]))

    def test_propagate_completes_the_sender_from_links(self):
        st = jd.load_goals(RECIP)
        jd._attach_courier_link(st, "seg-d1", MID)
        st = jd.load_goals(RECIP)
        jd.record_verdict(st, st["nodes"][RECIP + ":g5"], "closer", "done", T + 100, why="shipped")
        jd.rollup_status(st, True)
        jd.save_goals(RECIP, st)
        n = jd.run_propagate(now=T + 200)
        self.assertGreaterEqual(n, 1)
        snd = jd.load_goals(SENDER)["nodes"][SENDER + ":g1"]
        self.assertTrue(snd.get("nodeComplete"), "the handoff checks off from the repaired link")

    def test_a_refless_completed_node_never_kills_the_pass(self):
        # THE 2026-08-23 REGRESSION: the refs loop's body sat OUTSIDE the loop, so the first completed
        # node with NO origin/links hit an unbound name and the exception killed run_propagate — and
        # with it every stage after it in the triage pass (grouper, consolidator, distiller), which is
        # how "everything stuck on Distilling" happened. A plain completed node must be a no-op.
        st = jd.load_goals(RECIP)
        st["nodes"][RECIP + ":g0"] = jd.GuardedNode({
            "id": RECIP + ":g0", "text": "An ordinary finished goal", "parentId": None,
            "nodeComplete": True, "blocked": False, "cleared": False, "trail": [],
            "t": T - 100, "mt": T - 100, "log": []})
        jd.save_goals(RECIP, st)
        jd.run_propagate(now=T + 200)   # must not raise, and must not touch the sender

    def test_every_ref_completes_its_own_sender_node(self):
        # Multi-ref: origin AND a repaired link each complete their sender's tracking node — the
        # mis-indented loop processed only the LAST ref.
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":g2"] = jd.GuardedNode({
            "id": SENDER + ":g2", "text": "↪ delegated to api: second thread of the exporter",
            "parentId": None, "nodeComplete": False, "blocked": False, "cleared": False,
            "trail": [], "t": T, "mt": T, "handoff": {"peer": RECIP, "msgId": "msg-bbbb-2222"}, "log": []})
        jd.save_goals(SENDER, st)
        st = jd.load_goals(RECIP)
        nd = st["nodes"][RECIP + ":g5"]
        nd["origin"] = {"peer": SENDER, "goalId": SENDER + ":g1", "msgId": MID}
        nd["links"] = [{"peer": SENDER, "goalId": SENDER + ":g2", "msgId": "msg-bbbb-2222"}]
        jd.record_verdict(st, nd, "closer", "done", T + 100, why="shipped")
        jd.rollup_status(st, True)
        jd.save_goals(RECIP, st)
        jd.run_propagate(now=T + 200)
        snd = jd.load_goals(SENDER)
        self.assertTrue(snd["nodes"][SENDER + ":g1"].get("nodeComplete"), "the origin's sender node completes")
        self.assertTrue(snd["nodes"][SENDER + ":g2"].get("nodeComplete"), "the link's sender node completes too")

    def test_courier_scan_carries_the_repair_branch(self):
        src = open(os.path.join(BIN, "romp-judge")).read()
        self.assertIn("_attach_courier_link(cstore, seg[\"id\"], pm0[1])", src)
        self.assertIn('_seg_peer_kind(seg) == "delegate"', src)


class DormantHandoffConverts(unittest.TestCase):
    def setUp(self):
        _seed_sender()
        d = jd.STATE / "states"
        d.mkdir(parents=True, exist_ok=True)
        (d / (SENDER + ".jsonl")).write_text(json.dumps({"state": "idle", "t": T + 50}) + "\n")
        # the names-registry launch record: what marks a reg-less sid as one the owner scan can
        # answer for — without it the corroborator reads the sid as transcript-derived and stands down
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SENDER).write_text("web\t~/notes-api\t#3355aa\t#ffffff\n")
        km._PREV_ALIVE = None
        self.nudged = {}
        # hermetic liveness (the corroboration the sweep runs since the deadwait-probe change): the
        # owner scan answers WITHOUT this synthetic sid, so the death is corroborated — the world
        # these tests assert. Same fixture as test_dead_wait_block.py; without it the corroborator
        # returns None (reg-less sid, no owner answer) and the sweep rightly stands down.
        km._TMUX.available = lambda: True
        km._TMUX.alive_sids = lambda t=3: set()

    def tearDown(self):
        for nm in ("available", "alive_sids"):
            km._TMUX.__dict__.pop(nm, None)   # instance attrs shadow the class methods; drop them
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        for f in (jd.STATE / "states").glob("*"):
            f.unlink()
        (jd.NAMES / SENDER).unlink(missing_ok=True)

    def test_dormant_sender_handoff_blocks_with_the_dead_wait_why(self):
        km._dead_wait_sweep(set(), self.nudged, T + 900)
        nd = jd.load_goals(SENDER)["nodes"][SENDER + ":g1"]
        self.assertTrue(nd.get("blocked"), "an unpropagatable handoff on a dead sender needs the user")
        self.assertTrue(str(nd.get("blockWhy") or "").startswith(jd.DEAD_WAIT_WHY_PREFIX))
        self.assertIn("delegation to", nd.get("blockWhy") or "")
        km._dead_wait_sweep(set(), self.nudged, T + 990)
        km._PREV_ALIVE = None
        self.assertEqual(len([k for k in self.nudged if k == SENDER + ":g1"]), 1, "once per node")


if __name__ == "__main__":
    unittest.main()
