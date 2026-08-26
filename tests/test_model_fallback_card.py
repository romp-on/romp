#!/usr/bin/env python3
"""A silent mid-turn model swap mints a COMPLETED card (the user 2026-08-23, approved 08-19 and
revived): the API fell back without a request, and the swap was invisible before this. The card
pops into Completed with a done why naming the swap; a user-driven /model pick (pending marker) or
the first learn never mints. SYNTHETIC fixtures."""
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
sb = SourceFileLoader("romp_sdk_backend",
                      os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class FallbackCard(unittest.TestCase):
    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()

    def test_mints_a_completed_top_naming_the_swap(self):
        gid = jd.mint_fallback_card(SID, "claude-fable-5", "claude-sonnet-5", ev_t=1_787_500_000)
        self.assertTrue(gid)
        store = jd.load_goals(SID)
        nd = store["nodes"][gid]
        self.assertTrue(nd["nodeComplete"])
        self.assertIn("fell back to claude-sonnet-5", nd["doneWhy"])
        self.assertIn("Model changed automatically: claude-fable-5 → claude-sonnet-5", nd["text"])
        self.assertEqual(store["status"].get(gid), "completed", "pops straight into Completed")
        dones = [e for e in nd["log"] if e.get("kind") == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["src"], "romp", "kernel-authored bookkeeping, never a question")

    def test_the_backend_transition_gates_are_pinned(self):
        src = open(os.path.join(HERE, "..", "kernel", "sdk_backend.py")).read()
        self.assertIn("if self.model and not self._model_pending and not cleared \\", src,
                      "first learns and user-driven picks never mint")
        self.assertIn('on_model_fallback', src)
        ksrc = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("jd.mint_fallback_card(sid, frm, to)", ksrc, "the kernel wires the hook at boot")


class DowngradeOnlyGate(unittest.TestCase):
    """The card mints ONLY on a known down-tier transition (the user 2026-08-23, whose own upgrade
    to a bigger model wore a "fallback" card): romp only sees its own picks pending, so a /model
    typed inside the CLI arrives as an unrequested transition too — and a capacity fallback never
    moves a session up-tier."""

    def test_rank_and_downgrade_shapes(self):
        # exec just the pure helper block: loading the whole backend pulls the live SDK dependency
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        i = src.index("_MODEL_TIERS")
        j = src.index("\nclass SdkSession", i)
        sb = type("NS", (), {})
        ns = {}
        exec(src[i:j], ns)
        sb._model_downgrade = staticmethod(ns["_model_downgrade"])
        self.assertTrue(sb._model_downgrade("claude-fable-5", "claude-sonnet-5"))
        self.assertTrue(sb._model_downgrade("Opus 5", "claude-haiku-4-5"))
        self.assertFalse(sb._model_downgrade("claude-opus-5", "claude-fable-5"), "an upgrade is the user's doing")
        self.assertFalse(sb._model_downgrade("claude-sonnet-5", "claude-sonnet-4-5"), "lateral within a family: no card")
        self.assertFalse(sb._model_downgrade("claude-opus-5", "some-experimental-model"), "unknown target: no card")
        self.assertFalse(sb._model_downgrade("", "claude-haiku-4-5"), "unknown source: no card")

    def test_the_learn_path_wires_the_gate(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        self.assertIn("and _model_downgrade(self.model, pm):", src)


class SidechainNeverLearns(unittest.TestCase):
    """Subagent traffic must never teach the session its model (the user 2026-08-24): a Task/Agent
    subagent streams its OWN AssistantMessages tagged parent_tool_use_id, routinely on a LOWER tier
    than the parent, and learning one filed a false "Model changed automatically" downgrade card and
    wrote the subagent's model over the registry liveModel the statusline badge reads. _on_message
    takes the SDK message classes as PARAMETERS (the lazy-import seam), so the branch runs
    hermetically with synthetic classes."""

    class _SM:                       # SystemMessage stand-in — never instantiated here
        pass

    class _RM:                       # ResultMessage stand-in — never instantiated here
        pass

    class _AM:                       # AssistantMessage stand-in
        def __init__(self, model, ptid=None):
            self.model = model
            self.parent_tool_use_id = ptid
            self.error = None
            self.content = []

    def _session(self):
        calls = {"fallback": [], "reg": []}

        class Backend:
            state_dir = None

            def on_model_fallback(sid, frm, to):        # looked up on the CLASS, called unbound —
                calls["fallback"].append((frm, to))     # exactly how the kernel wires the hook

            def _update_reg(self, sid, **kw):
                calls["reg"].append(kw)

            def _deliver_rename_ping(self, s):
                return False   # settle hook (2026-08-25); no ping in these worlds

            def _poke(self):
                pass

            def _forward(self, sess, msg):              # the unconditional kernel forward at the branch tail
                pass

            def _log(self, *a, **k):
                pass

        s = object.__new__(sb.SdkSession)               # just the state the branch touches
        s.backend = Backend()
        s.sid = SID
        s.name = "web"
        s.model = "Fable 5"
        s._model_pending = ""
        s.retrying = False
        s.retry_count = 0
        s.retry_info = None
        return s, calls

    def _msg(self, s, msg):
        s._on_message(msg, self._AM, self._RM, self._SM)

    def test_a_tagged_downgrade_teaches_and_mints_nothing(self):
        s, calls = self._session()
        self._msg(s, self._AM("claude-opus-5", ptid="toolu_01AAAAAAAAAAAAAAAAAAAAAA"))
        self.assertEqual(calls["fallback"], [], "a subagent's own turns never read as a capacity fallback")
        self.assertEqual(s.model, "Fable 5", "self.model untouched")
        self.assertEqual(calls["reg"], [], "registry liveModel untouched")

    def test_a_genuine_main_loop_downgrade_still_mints_exactly_once(self):
        s, calls = self._session()
        self._msg(s, self._AM("claude-opus-5"))         # untagged: the main loop itself fell back
        self.assertEqual(calls["fallback"], [("Fable 5", "Opus 5")])
        self.assertEqual(s.model, "Opus 5")
        self.assertEqual(calls["reg"][-1].get("liveModel"), "Opus 5")

    def test_livemodel_stays_main_loop_across_a_subagent_burst(self):
        s, calls = self._session()
        for i in range(3):                              # an Explore fan-out: three sidechain turns, lower tier
            self._msg(s, self._AM("claude-opus-5", ptid="toolu_01BBBBBBBBBBBBBBBBBB%02d" % i))
        self._msg(s, self._AM("claude-fable-5"))        # the main loop comes back unchanged
        self.assertEqual(s.model, "Fable 5")
        self.assertEqual(calls["fallback"], [])
        self.assertEqual(calls["reg"], [], "an unchanged main-loop model is a no-op write")

    def test_the_sidechain_guard_is_pinned_at_the_source(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        i = src.index("elif isinstance(msg, AssistantMessage):")
        j = src.index("elif isinstance(msg, ResultMessage):", i)
        branch = src[i:j]
        self.assertIn('if getattr(msg, "parent_tool_use_id", None):', branch)
        self.assertLess(branch.index("parent_tool_use_id"), branch.index("self._learn_model("),
                        "the sidechain drop gates the learn, mirroring msg_to_atom's drop")


class DedupeBackstop(unittest.TestCase):
    """One open card per identical swap (the user 2026-08-24): mint_fallback_card returns None while
    an identical UNCLEARED frm→to card sits on the board — existence-keyed, never a time cooldown.
    Clearing the card (either shape: the feed's view-clear in cleared.jsonl, or a /clear boundary's
    verdict flag) is the re-arming event.

    OWN sid, deliberately: load_goals replays the per-sid user-override journal on every load, and
    other test modules journal user gestures against the shared placeholder SID — a journaled resolve
    for a colliding "<SID>:g1" id replayed onto our store mid-test and folded the cleared card back
    to done/uncleared, so the dedupe (correctly, per its own rule) blocked the re-arm. A sid nobody
    else touches keeps these tests about the dedupe, not the journal."""

    DSID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        for fp in (jd.STATE / "cleared.jsonl", jd._overrides_dir() / (self.DSID + ".jsonl")):
            try:
                fp.unlink()
            except OSError:
                pass

    def test_an_identical_open_card_blocks_a_second_mint(self):
        g1 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000)
        self.assertTrue(g1)
        self.assertIsNone(jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_060))
        store = jd.load_goals(self.DSID)
        same = [nd for nd in store["nodes"].values()
                if nd.get("text") == "Model changed automatically: Fable 5 → Opus 5"]
        self.assertEqual(len(same), 1, "however many identical observations, one card")

    def test_a_different_swap_mints_beside_the_open_card(self):
        self.assertTrue(jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000))
        self.assertTrue(jd.mint_fallback_card(self.DSID, "Opus 5", "Sonnet 5", ev_t=1_787_500_001),
                        "a different frm→to is new information, not a repeat")

    def test_a_feed_view_clear_rearms_the_mint(self):
        g1 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000)
        with open(jd.STATE / "cleared.jsonl", "a") as f:      # the feed clear's authoritative record
            f.write('{"id": "%s"}\n' % g1)
        g2 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_200)
        self.assertTrue(g2, "the user's cross-off is the re-arming event")
        self.assertNotEqual(g1, g2)

    def test_a_verdict_clear_rearms_the_mint(self):
        g1 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000)
        store = jd.load_goals(self.DSID)
        self.assertTrue(jd.record_verdict(store, store["nodes"][g1], "romp", "clear",
                                          1_787_500_100, why="dropped with the cleared conversation"))
        jd.rollup_status(store, True)
        jd.save_goals(self.DSID, store)
        g2 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_200)
        self.assertTrue(g2, "a boundary-settle clear re-arms too")


if __name__ == "__main__":
    unittest.main()
