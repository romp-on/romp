#!/usr/bin/env python3
"""T354: a kernel restart must not re-bill a hosted session's CLI lifetime. A host ATTACH keeps the CLI process, whose
total_cost_usd is cumulative; the kernel persists the session's cost watermark on its registry row at every result,
seeds from it at the attach when it names the surviving CLI, records nothing for a first result with no matching
watermark, and still records a fresh process's whole first total.

SYNTHETIC fixtures only: placeholder ids, an invented session, a fake host hello.
"""
import asyncio
import collections
import inspect
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the loads
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-00000000a354"


class _ResultMessage:
    subtype = "success"


class _AssistantMessage:
    pass


def _result(total, tokens_in, session=""):
    r = _ResultMessage()
    r.total_cost_usd = total
    if session:
        r.session_id = session
    r.model_usage = {"claude-x": {"inputTokens": tokens_in, "outputTokens": 0, "cacheReadInputTokens": 0,
                                  "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": 0.0}}
    r.usage = {"input_tokens": 9999}
    return r


class Rebill(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        Path(self.d, "session-hosts").write_text("off")   # this backend never connects; the pin is the suite's rule
        self.lines = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=lambda m, **k: self.lines.append(str(m)))
        self.be._forward = lambda sess, msg: None
        self.be._turn_completed = lambda sid: None
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True})

    def _session(self, attach=False, hello_cli=("4242", "s1"), journal_next=10, tags=None):
        s = sb.SdkSession(self.be, {"sid": SID, "name": "web", "cwd": self.d})
        async def _noop(): pass
        s._do_refresh_context = _noop
        s._do_refresh_usage = _noop
        if attach:
            s._host_is_attach = True
            # the host's hello names the journal's next offset at the attach (records before it are the replay); the
            # transport queues one tag per result record it hands over, and the fold pops them in order
            s._host = types.SimpleNamespace(hello={"host": {"pid": 1, "start": "h"}, "cli": {"pid": hello_cli[0], "start": hello_cli[1]},
                                                   "journal": {"next": journal_next}},
                                            ack_offset=journal_next, replay_end=journal_next, journal_dir=None, exit_info=None, detach_mode=False,
                                            result_tags=collections.deque(tags or []))
        s._seed_spend_watermarks()                          # the connect-time step
        return s

    def _run(self, s, r):
        async def go():
            s._on_message(r, _AssistantMessage, _ResultMessage, type("S", (), {}))
            await asyncio.sleep(0)
        asyncio.run(go())

    def _day(self):
        p = Path(self.d, "spend.json")
        if not p.exists():
            return {}
        return json.loads(p.read_text())["days"].get(time.strftime("%Y-%m-%d"), {})

    def _turns(self):
        p = Path(self.d, "turns.jsonl")
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def _cost_state(self):
        return (sb.read_reg(Path(self.d), SID) or {}).get("costState")

    def test_a_fresh_process_seeds_zero_and_records_its_whole_first_total_and_persists_the_watermark(self):
        s = self._session()
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "fresh"))
        self._run(s, _result(3.5, 100))
        self.assertAlmostEqual(self._day()["usd"], 3.5, msg="a fresh process: the first total is the turn's own")
        cs = self._cost_state()
        self.assertEqual((cs["total"], cs["cli"], cs["tokens"]["input_tokens"]), (3.5, "", 100), "the watermark, on the registry, every result")
        self._run(s, _result(5.0, 160))
        self.assertAlmostEqual(self._day()["usd"], 5.0)
        self.assertEqual(self._cost_state()["total"], 5.0)
        rows = self._turns()
        self.assertEqual([r["cumulativeUsd"] for r in rows], [3.5, 5.0], "each turn row carries the CLI's cumulative total")
        self.assertEqual(rows[0].get("spendBaseline"), "fresh")
        self.assertNotIn("spendBaseline", rows[1], "only a first result names its baseline")

    def test_an_attach_to_the_surviving_cli_seeds_from_the_registry_and_records_one_turns_delta(self):
        # the previous kernel's last result left the watermark for CLI 4242:s1 at $500 (the staircase's cause was
        # seeding zero here: the lifetime's $512.50 would have been one turn)
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {"input_tokens": 90000}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True, hello_cli=("4242", "s1"))
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "attach-pending"), "nothing folded until the hello names the CLI")
        self._run(s, _result(512.5, 90400))
        self.assertAlmostEqual(self._day()["usd"], 12.5, msg="only this turn's delta over the seeded watermark")
        self.assertEqual(self._day()["tokIn"], 400, "the token watermarks seed too")
        self.assertEqual(s._spend_baseline, "seeded")
        self.assertEqual(self._cost_state()["total"], 512.5)
        self.assertTrue(any("attached to its surviving CLI (4242:s1): watermarks seeded at the registry's cumulative $500.00" in l for l in self.lines), self.lines)
        self.assertEqual(self._turns()[0]["spendBaseline"], "seeded")
        self.assertEqual(self._turns()[0]["cumulativeUsd"], 512.5)
        self.assertEqual([r for r in self.be.problems() if "spend" in str(r.get("text") or "")], [], "an info line, never a problem row")

    def test_an_attach_with_no_matching_watermark_records_nothing_for_the_first_result_and_says_so(self):
        for cs in (None, {"total": 300.0, "tokens": {}, "cli": "other:proc", "t": 1}):
            sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True})
            if cs:
                self.be._update_reg(SID, costState=cs)
            Path(self.d, "spend.json").unlink(missing_ok=True)
            Path(self.d, "turns.jsonl").unlink(missing_ok=True)
            self.lines.clear()
            s = self._session(attach=True, hello_cli=("4242", "s1"))
            self._run(s, _result(512.5, 90400))
            self.assertEqual(self._day(), {}, "the lifetime's total is not a turn: nothing folded (%r)" % cs)
            self.assertEqual(s._spend_baseline, "attach-unknown")
            self.assertTrue(any("attached to a surviving CLI (4242:s1) with no matching watermark on record" in l for l in self.lines), self.lines)
            self.assertTrue(any("this turn's own cost is unknowable and nothing was folded" in l for l in self.lines), self.lines)
            self.assertEqual(self._cost_state()["total"], 512.5, "the watermark is set from here…")
            self.assertEqual(self._cost_state()["cli"], "4242:s1")
            rows = self._turns()
            self.assertEqual((rows[0]["spendBaseline"], rows[0]["cumulativeUsd"], rows[0]["usd"]), ("attach-unknown", 512.5, 0.0))
            self._run(s, _result(520.0, 91000))
            self.assertAlmostEqual(self._day()["usd"], 7.5, msg="…so the next result records its own delta")
            self.assertEqual(self._day()["tokIn"], 600)

    def test_a_first_result_above_the_mark_names_which_process_it_was(self):
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True)
        self._run(s, _result(900.0, 10))                    # a $400 turn over the seed: recorded, said as the attach case
        self.assertAlmostEqual(self._day()["usd"], 400.0)
        self.assertTrue(any("first result after a host attach cost $400.00" in l and "seeded from the registry at $500.00" in l for l in self.lines), self.lines)
        self.lines.clear()
        s2 = self._session()
        self._run(s2, _result(300.0, 10))                   # a fresh process's big first turn: the old text
        self.assertTrue(any("first result after connect cost $300.00" in l and "a fresh CLI process starts its cost at zero" in l for l in self.lines), self.lines)

    def test_the_attach_flag_is_cleared_on_every_road_but_an_attach(self):
        # M6 (the review of the fix): a flag left True by an earlier attach in this object's life made the seed wait for
        # a registry watermark on the hosts-off road, where the CLI is a kernel child that dies with the kernel, so a
        # rollback to hosts off recorded nothing for the fresh child's first turn, once per connect
        s = self._session()
        s._host_is_attach = True
        Path(self.d, "session-hosts").write_text("off")
        async def go():
            return await self.be._host_transport_for(s, None, (None, None, None))
        self.assertIsNone(asyncio.run(go()), "the setting off: a kernel child, no transport")
        self.assertFalse(s._host_is_attach, "the hosts-off road clears the flag")
        s._seed_spend_watermarks()
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "fresh"))
        self._run(s, _result(3.5, 100))
        self.assertAlmostEqual(self._day()["usd"], 3.5, msg="the fresh child's first turn is recorded in full")
        import inspect
        src = inspect.getsource(sb.SdkBackend._host_transport_for)
        self.assertLess(src.index("sess._host_is_attach = False"), src.index('if state == "attach":'),
                        "cleared before any branch; set True on the attach branch alone")
        self.assertEqual(src.count("sess._host_is_attach = False"), 1)

    def test_a_dead_hosts_journal_replay_seeds_from_the_dead_clis_watermark_before_it_drains(self):
        # M7: the tail's result rows carry the DEAD CLI's cumulative and drain through the result branch before the
        # connect's seed runs; a zero watermark there folded the dead lifetime as one turn
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {"input_tokens": 90000}, "cli": "4242:s1", "t": 1})
        s = self._session()                                 # a boot's fresh object: no host, no hello
        s._seed_for_dead_cli("4242:s1")
        self.assertEqual((s._last_cost_total, s._spend_baseline), (500.0, "seeded"))
        self.assertTrue(any("replaying the journal of its dead CLI (4242:s1): watermarks seeded at the registry's cumulative $500.00" in l for l in self.lines), self.lines)
        self._run(s, _result(512.5, 90400))                 # the dead CLI's last result, replayed
        self.assertAlmostEqual(self._day()["usd"], 12.5, msg="only the replayed turn's own delta")
        self.assertEqual(self._day()["tokIn"], 400)
        s._seed_spend_watermarks()                          # the connect's seed for the spawn that follows
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "fresh"))
        self.lines.clear()
        s2 = self._session()
        s2._seed_for_dead_cli("other:proc")                 # the watermark names another process
        self.assertEqual(s2._spend_baseline, "attach-unknown")
        self.assertTrue(any("replaying the journal of a dead CLI (other:proc) with no matching watermark on record" in l for l in self.lines), self.lines)
        self._run(s2, _result(700.0, 10))
        self.assertAlmostEqual(self._day()["usd"], 12.5, msg="nothing folded for a lifetime whose turn share is unknowable")
        s3 = self._session()
        s3._seed_for_dead_cli("")                           # no lease and no ack naming the host: unnamed
        self.assertEqual(s3._spend_baseline, "attach-unknown")
        import inspect
        src = inspect.getsource(sb.SdkBackend._host_orphan_recover)
        self.assertLess(src.index("sess._seed_for_dead_cli(cli)"), src.index("self._replay_drain("), "seeded before the drain")
        self.assertIn('cli = "%s:%s" % (lease.get("pid"), lease.get("start"))', src, "the lease names the dead CLI")
        self.assertIn('cli = str(ack.get("cli") or "")', src, "else hostAck, when it names this host")

    def test_a_reconnect_that_skips_the_host_connect_seeds_fresh_the_flag_gone_with_the_transport(self):
        # M1 of the review of this pull request: the connect calls _host_transport_for only with hosts on or a lease that
        # applies, so a reconnect in the same thread after a rollback to hosts off (an effort or auth switch) never
        # reached the clear there; the stale True made the seed wait for a watermark the kernel child never has, and
        # the fresh child's first turn recorded nothing, then the watermark was written under an empty CLI identity.
        # The connect's finally now drops the flag with the transport, and the seed trusts the flag only with the
        # transport in hand: this is the state that finally leaves (host None, flag stale) fed to the seed
        s = self._session()
        s._host_is_attach = True
        s._host = None
        s._seed_spend_watermarks()
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "fresh"), "no transport: a fresh kernel child")
        self._run(s, _result(3.5, 100))
        self.assertAlmostEqual(self._day()["usd"], 3.5, msg="the child's first turn is recorded in full")
        self.assertEqual(self._cost_state()["cli"], "", "a kernel child's watermark names no CLI")
        import inspect
        src = inspect.getsource(sb)
        i = src.index("self._host = None" + chr(10) + "                    self._host_intent = False" + chr(10))
        self.assertIn("self._host_is_attach = False", src[i:i + 900], "the connect's finally drops the flag with the transport")
        self.assertLess(src.index("self._host_is_attach = False", i), src.index("if self.ended or not self._reconnect:", i))
        self.assertIn('if getattr(self, "_host_is_attach", False) and getattr(self, "_host", None) is not None:',
                      inspect.getsource(sb.SdkSession._seed_spend_watermarks))

    def test_a_redelivered_result_is_known_by_the_tag_the_transport_queued_and_folds_nothing_whatever_its_total(self):
        # round four of the review: the transport tags each RESULT record with its own offset as it reads it and the
        # fold pops the tags in order; a record before the journal's next at the attach (10 here) is a replay and folds
        # nothing, whatever its total; a record from 10 on is live. The SDK's buffered reader runs a record AHEAD of
        # the handler, so all tags sit queued before the first result is handled (the +1 lookahead the review measured)
        def tag(off):
            return {"offset": off, "replay": off < 10}
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {"input_tokens": 90000}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True, hello_cli=("4242", "s1"), journal_next=10, tags=[tag(7), tag(8), tag(9), tag(10), tag(11)])
        s._host.ack_offset = 11                              # the transport is already past every replayed record: the +1 lookahead
        self._run(s, _result(480.0, 89000))                 # replayed, below the watermark
        self.assertEqual(self._day(), {}, "a replay folds nothing")
        self.assertEqual((s._last_cost_total, s._last_usage_totals["input_tokens"]), (500.0, 90000), "the watermarks did not move")
        self._run(s, _result(500.0, 90000))                 # replayed: the very result the watermark came from
        self._run(s, _result(512.5, 90400))                 # replayed and ABOVE the watermark: still a replay, folds nothing
        self.assertEqual(self._day(), {}, "position alone decides a replay, never the total")
        self._run(s, _result(520.0, 91000))                 # live (offset 10): the plain delta over the watermark
        self.assertAlmostEqual(self._day()["usd"], 20.0)
        self.assertEqual(self._day()["tokIn"], 1000)
        self._run(s, _result(526.0, 91200))                 # live (offset 11)
        self.assertAlmostEqual(self._day()["usd"], 26.0)
        rows = self._turns()
        self.assertEqual([r["usd"] for r in rows], [0.0, 0.0, 0.0, 20.0, 6.0])
        self.assertEqual([r.get("redelivered", False) for r in rows], [True, True, True, False, False], "the rows say so")
        self.assertEqual([r.get("journalOffset") for r in rows], [7, 8, 9, 10, 11], "and carry the record's offset")
        self.assertEqual([r["cumulativeUsd"] for r in rows], [480.0, 500.0, 512.5, 520.0, 526.0])
        self.assertEqual(self._cost_state()["session"], "", "the CLI's session epoch rides costState (empty on these doubles)")

    def test_a_replay_across_a_mid_life_clear_bills_nothing(self):
        # the round-three MEDIUM: the dead kernel folded a pre-clear result (8.00), bracketed the /clear, folded a
        # post-clear result (5.00, costState 5.00) and died with hostAck lagging; the attach seeds 5.00 and replays
        # offsets 2 to 4; read from totals, the pre-clear 8 > 5 folded $3 and dragged the watermark to 8, and the
        # replayed post-clear 5 read as live and folded whole: $8 billed for $0
        self.be._update_reg(SID, costState={"total": 5.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True, journal_next=5, tags=[{"offset": 2, "replay": True}, {"offset": 4, "replay": True}, {"offset": 5, "replay": False}])
        s._host.ack_offset = 5
        self._run(s, _result(8.0, 10))                      # the pre-clear result, replayed
        self._run(s, _result(5.0, 10))                      # the post-clear result, replayed, handled at the post-next offset
        self.assertEqual(self._day(), {}, "$0: both are replays")
        self.assertEqual(s._last_cost_total, 5.0, "the watermark stayed at the post-clear 5")
        self._run(s, _result(7.0, 10))                      # live
        self.assertAlmostEqual(self._day()["usd"], 2.0)
        self.assertEqual([r.get("redelivered", False) for r in self._turns()], [True, True, False])

    def test_a_dead_hosts_journal_replays_through_the_real_orphan_road_and_bills_nothing_it_already_folded(self):
        # round five of the review, the HIGH: the orphan road built the replay transport but never made it the session's,
        # so the fold saw no tags and a dead host's tail across a /clear billed $8 for $0. Driven through the real
        # _host_orphan_recover with a stub SDK client that reads the real replay transport and hands its records over
        import sys
        from unittest import mock
        hostmod = load_source("romp_host_transport_orphan", os.path.join(ROOT, "kernel", "host_transport.py"))
        sh = hostmod.sh
        hdir = hostmod.host_dir(Path(self.d), SID) if hasattr(hostmod, "host_dir") else Path(self.d, "hosts", SID)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / "identity.json").write_text(json.dumps({"pid": 9, "start": "h9"}))
        recs = [{"type": "assistant", "n": 0},
                {"type": "result", "subtype": "success", "total_cost_usd": 8.0, "n": 1},    # pre-clear, folded by the dead kernel
                {"type": "system", "subtype": "init", "n": 2},                               # the /clear's init flip
                {"type": "result", "subtype": "success", "total_cost_usd": 0.0, "n": 3},    # the /clear's own zero-cost result
                {"type": "result", "subtype": "success", "total_cost_usd": 5.0, "n": 4}]    # post-clear, folded (costState 5.0), unacked
        with open(hdir / "journal-0.jsonl", "w") as f:
            for r in recs:
                f.write(json.dumps(r) + chr(10))
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True,
                                         "hostAck": {"host": "9:h9", "cli": "4242:s1", "offset": 1}})   # acked through the pre-clear result
        self.be._update_reg(SID, costState={"total": 5.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s = self._session()
        classes = (_AssistantMessage, _ResultMessage, type("S", (), {}))
        def to_msg(rec):
            if rec.get("type") == "result":
                r = _ResultMessage(); r.total_cost_usd = rec.get("total_cost_usd"); r.model_usage = {}; r.usage = {}; r.session_id = "e2"
                return r
            return object()
        class Client:
            """The SDK client's shape as the orphan road uses it: connects the transport (which makes the reader's queue),
            then hands every record the reader yields to the drain; the initialize is marked answered so the reader
            ends without its bounded wait for an answer this stub never asks for."""
            def __init__(self, options=None, transport=None): self.transport = transport
            async def __aenter__(self):
                await self.transport.connect()
                self.transport._init_answered = True
                return self
            async def __aexit__(self, *a): return False
            async def receive_messages(self):
                async for rec in self.transport.read_messages():
                    yield to_msg(rec)
        mod = types.ModuleType("claude_agent_sdk"); mod.ClaudeSDKClient = Client
        async def go():
            await self.be._host_orphan_recover(s, types.SimpleNamespace(), None, classes, died=False)
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": mod}):
            asyncio.run(go())
        self.assertEqual(self._day(), {}, "$0: the tail past the ack is a replay of what the dead kernel folded (the /clear's own result and the post-clear result)")
        rows = self._turns()
        self.assertEqual([(r.get("usd"), r.get("redelivered", False)) for r in rows], [(None, False), (0.0, True)],
                         "the /clear's zero-cost result (no dollars folded, its tag consumed), then the post-clear result, replayed")
        self.assertEqual(self._cost_state()["cli"], "4242:s1", "persisted under the dead CLI's identity")
        self.assertIsNone(s._host, "the replay transport was the session's for the drain alone")
        # the same road with hostAck naming ANOTHER host: the whole journal replays, and bills nothing
        Path(self.d, "spend.json").unlink(missing_ok=True); Path(self.d, "turns.jsonl").unlink(missing_ok=True)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / "identity.json").write_text(json.dumps({"pid": 9, "start": "h9"}))
        with open(hdir / "journal-0.jsonl", "w") as f:
            for r in recs:
                f.write(json.dumps(r) + chr(10))
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True, "hostAck": {"host": "1:other", "cli": "4242:s1", "offset": 3}})
        self.be._update_reg(SID, costState={"total": 5.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s2 = self._session()
        async def go2():
            await self.be._host_orphan_recover(s2, types.SimpleNamespace(), None, classes, died=False)
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": mod}):
            asyncio.run(go2())
        self.assertEqual(self._day(), {}, "$0 for the whole-journal replay too (8.00 above the watermark is a replay all the same)")

    def test_a_zero_cost_result_consumes_its_tag_so_later_results_read_their_own(self):
        # round five's MEDIUM: popped only inside the total > 0 gate, a /clear's zero-cost result left its tag at the
        # head and the first live result was read as the replay
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True, journal_next=10, tags=[{"offset": 9, "replay": True}, {"offset": 10, "replay": False}, {"offset": 11, "replay": False}])
        z = _result(0.0, 0)                                   # the /clear's own result: nothing to fold, but a record with a tag
        self._run(s, z)
        self.assertEqual(len(s._host.result_tags), 2, "its tag is gone")
        self._run(s, _result(520.0, 10))                      # live (offset 10): folded as its own delta, not as the replay
        self.assertAlmostEqual(self._day()["usd"], 20.0)
        self._run(s, _result(526.0, 10))                      # live (offset 11)
        self.assertAlmostEqual(self._day()["usd"], 26.0)
        self.assertEqual([(r.get("usd"), r.get("redelivered", False)) for r in self._turns()], [(None, False), (20.0, False), (6.0, False)],
                         "the zero-cost result's own row (no dollars), then the two live ones, none read as a replay")
        self.assertEqual(self._cost_state()["session"], "", "the epoch is stamped from live results (these doubles carry none)")

    def test_a_replayed_first_result_under_an_unknown_baseline_sets_the_watermark_so_the_next_live_turn_is_its_own(self):
        # live on the fix's first boot (2026-09-11 22:37Z): every hosted session's replayed first result was attach-unknown
        # and folded nothing, right, but the duplicate branch left the watermark at zero, and the next LIVE result folded
        # the whole cumulative once per session, each by its own lifetime (a synthetic sequence below)
        s = self._session(attach=True, hello_cli=("4242", "s1"), journal_next=10, tags=[{"offset": 9, "replay": True}, {"offset": 10, "replay": False}])
        self.assertEqual(s._spend_baseline, "attach-pending")
        self._run(s, _result(308.15, 90000))                # replayed, no watermark on record: attach-unknown
        self.assertEqual(self._day(), {}, "the lifetime's total is not a turn")
        rows = self._turns()
        self.assertEqual((rows[0].get("usd"), rows[0]["spendBaseline"], rows[0].get("redelivered")), (0.0, "attach-unknown", True))
        self.assertEqual((s._last_cost_total, s._last_usage_totals.get("input_tokens")), (308.15, 90000), "the watermarks start from the replayed lifetime")
        self._run(s, _result(309.71, 90400))                # the next LIVE result
        self.assertAlmostEqual(self._day()["usd"], 1.56, msg="its own delta, not the whole cumulative")
        self.assertEqual(self._day()["tokIn"], 400)
        self.assertEqual(self._cost_state()["total"], 309.71)

    def test_the_seeded_road_is_unchanged_by_the_unknown_baseline_rule(self):
        # a replay below a KNOWN watermark moves nothing, and an unfolded replay above it is absorbed by the next live
        # delta (the round-five behaviour the verifier read as right); only the unknown baseline seeds from a replay
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {"input_tokens": 90000}, "cli": "4242:s1", "t": 1})
        s2 = self._session(attach=True, journal_next=10, tags=[{"offset": 8, "replay": True}, {"offset": 9, "replay": True}, {"offset": 10, "replay": False}])
        self._run(s2, _result(480.0, 89000)); self._run(s2, _result(512.5, 90400))
        self.assertEqual(self._day(), {}, "replays fold nothing")
        self.assertEqual((s2._last_cost_total, s2._last_usage_totals["input_tokens"]), (500.0, 90000), "the seeded watermarks stand")
        self._run(s2, _result(520.0, 91000))
        self.assertAlmostEqual(self._day()["usd"], 20.0, msg="the unfolded 12.5 rides the live delta")
        self.assertEqual(self._day()["tokIn"], 1000)

    def test_every_replayed_record_under_an_unknown_baseline_advances_the_watermarks_until_the_first_live_result(self):
        # the fix's second round: a whole-journal replay (hostAck naming another host) hands over several results; keyed
        # on the connect's first result alone, the watermark stayed at the first replay's total and the next live result
        # folded the span (replays 100/200/300 then live 301.50 billed 201.50 and 2100 tokens where 1.50 and 100 were due)
        s = self._session(attach=True, hello_cli=("4242", "s1"), journal_next=10,
                          tags=[{"offset": 7, "replay": True}, {"offset": 8, "replay": True}, {"offset": 9, "replay": True},
                                {"offset": 10, "replay": False}])
        for total, toks in ((100.0, 1000), (200.0, 2000), (300.0, 3000)):
            self._run(s, _result(total, toks, session="e1"))
            self.assertEqual((s._last_cost_total, s._last_usage_totals.get("input_tokens")), (total, toks), "each replay advances the watermarks")
            self.assertEqual((self._cost_state()["total"], self._cost_state()["session"]), (total, "e1"),
                             "and persists them with the replayed epoch (the road 1469's guard compares against)")
        self.assertEqual(self._day(), {}, "replays fold nothing")
        self.assertEqual([(r.get("usd"), r.get("redelivered")) for r in self._turns()], [(0.0, True)] * 3)
        self._run(s, _result(301.5, 3100, session="e1"))     # the first LIVE result closes the window
        self.assertAlmostEqual(self._day()["usd"], 1.5, msg="its own delta over the LAST replay, not the span")
        self.assertEqual(self._day()["tokIn"], 100)
        self.assertEqual((self._cost_state()["total"], self._cost_state()["session"]), (301.5, "e1"))
        self.assertFalse(s._spend_unknown_open, "closed by the live result")

    def test_the_transport_tags_each_result_record_with_its_offset_as_it_reads_it(self):
        # the transport unit of the rule: the hello's journal.next bounds the replay; records the reader hands over are
        # tagged in _take before the yield; the last replayed record tagged at next - 1 is a replay even though the
        # transport's ack offset has moved on when the consumer handles it
        ht = load_source("romp_host_transport_tags", os.path.join(ROOT, "kernel", "host_transport.py"))
        t = ht.HostTransport.__new__(ht.HostTransport)
        t.ack_offset, t.replay_end, t.result_tags, t.on_ack = 5, None, collections.deque(), None
        t.hello = None
        t._advance = lambda out: None
        t.replay_end = 10
        for off, typ in ((8, "result"), (9, "assistant"), (9, "result"), (10, "result"), (11, "result")):
            t._take({"offset": off, "data": {"type": typ, "subtype": "success"}})
        self.assertEqual(list(t.result_tags), [{"offset": 8, "replay": True}, {"offset": 9, "replay": True},
                                               {"offset": 10, "replay": False}, {"offset": 11, "replay": False}],
                         "one tag per RESULT record, in order; the assistant record is not tagged")
        t.result_tags.clear()
        t._tag(3, {"type": "result"}, replay=True)          # the orphan reader's road: a replay whatever the window
        self.assertEqual(list(t.result_tags), [{"offset": 3, "replay": True}])
        import inspect
        src = inspect.getsource(ht.HostTransport._take)
        self.assertLess(src.index("self._tag(out.get(\"offset\"), out[\"data\"])"), src.index("return out[\"data\"]"), "tagged before the hand-over")
        rm = inspect.getsource(ht.HostTransport._read_journal)
        self.assertIn("self._tag(off, rec, replay=True)", rm, "the orphan journal's reader tags every record a replay")
        self.assertIn('self.replay_end = int(((hello or {}).get("journal") or {}).get("next"))', inspect.getsource(ht.HostTransport))

    def test_a_live_total_below_the_watermark_is_a_counter_reset_on_every_road_and_never_latches_zero(self):
        # the round-two HIGH: read from the total alone, every real reset was a duplicate and the session stayed at $0
        # (a) a /clear as the first turn after an attach: the event zeroes the counter and retires the pending seed
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True)
        self.assertEqual(s._spend_baseline, "attach-pending")
        s._last_cost_total = 0.0; s._last_usage_totals = {}          # what the /clear event does…
        if s._spend_baseline == "attach-pending":                    # …and the retirement it now carries (pinned below)
            s._spend_baseline = "fresh"
        s._host.ack_offset = 10
        self._run(s, _result(3.0, 10)); self._run(s, _result(8.0, 10))
        self.assertAlmostEqual(self._day()["usd"], 8.0, msg="3 then 5, never 0 and 0")
        src = inspect.getsource(sb.SdkSession._on_message)
        i = src.index("self._last_cost_total = 0.0" + chr(10) + "                    self._last_usage_totals = {}")
        self.assertIn('if getattr(self, "_spend_baseline", "fresh") == "attach-pending":', src[i:i + 700], "the /clear event retires a pending attach seed")
        # (b) a /clear the kernel never saw (unbracketed, in flight when the kernel died): live results below the seed
        Path(self.d, "spend.json").unlink(missing_ok=True); Path(self.d, "turns.jsonl").unlink(missing_ok=True)
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {}, "cli": "4242:s1", "t": 1})   # (a) moved the watermark to 8
        s2 = self._session(attach=True)
        s2._host.ack_offset = 10
        self._run(s2, _result(512.5, 10))                   # the seeded 500: 12.5
        self._run(s2, _result(3.0, 10)); self._run(s2, _result(8.0, 10))
        self.assertAlmostEqual(self._day()["usd"], 12.5 + 3.0 + 5.0, msg="a live total below the watermark is a reset: folded whole, then its delta")
        # (c) a resumed transcript's cost-state seed (baseline fresh) under a spawned host, against a print-mode CLI
        Path(self.d, "spend.json").unlink(missing_ok=True); Path(self.d, "turns.jsonl").unlink(missing_ok=True)
        s3 = self._session()
        s3._host = types.SimpleNamespace(hello={"host": {"pid": 1, "start": "h"}, "cli": {"pid": "7", "start": "s7"}, "journal": {"next": 0}},
                                         ack_offset=0, replay_end=0, result_tags=collections.deque([{"offset": 0, "replay": False}, {"offset": 1, "replay": False}]),
                                         journal_dir=None, exit_info=None, detach_mode=False)
        s3._last_cost_total = 500.0                          # the cost-state record's seed
        self._run(s3, _result(3.0, 10)); self._run(s3, _result(8.0, 10))
        self.assertAlmostEqual(self._day()["usd"], 8.0, msg="3 then 5 on a spawn's empty replay window")
        # (d) an orphan journal's reader replays only: below the dead CLI's watermark folds nothing, above it folds
        Path(self.d, "spend.json").unlink(missing_ok=True); Path(self.d, "turns.jsonl").unlink(missing_ok=True)
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {}, "cli": "4242:s1", "t": 1})   # (b) moved it again
        s4 = self._session()
        s4._seed_for_dead_cli("4242:s1")                     # seeded at 500
        s4._host = types.SimpleNamespace(hello=None, journal_dir="/nonexistent/journal", ack_offset=8, exit_info=None, detach_mode=False,
                                         result_tags=collections.deque([{"offset": 7, "replay": True}, {"offset": 8, "replay": True}]))
        self._run(s4, _result(480.0, 10))
        self.assertEqual(self._day(), {}, "at or below the dead CLI's watermark: folded by the kernel that died")
        self._run(s4, _result(512.5, 10))
        self.assertAlmostEqual(self._day()["usd"], 12.5, msg="above it: spend no kernel folded (the orphan road keeps the watermark line)")
        self.assertEqual(self._cost_state()["cli"], "4242:s1", "the orphan replay persists the watermark under the DEAD CLI's identity (the round-four low)")
        s4._seed_spend_watermarks()
        self.assertEqual(s4._cli_ident(), "", "the connect's seed ends the replay identity")

    def test_a_replayed_tail_with_no_result_leaves_the_spawn_to_seed_fresh_and_an_init_in_the_tail_never_clobbers_the_dead_seed(self):
        # low b: a dead host's tail with no result record folds nothing and the connect's seed for the spawn that follows
        # starts at zero; low a: the init record's cwd re-seed is for a resumed FRESH process only
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s = self._session()
        s._seed_for_dead_cli("4242:s1")
        self.assertEqual(s._spend_baseline, "seeded")
        s._seed_spend_watermarks()                          # nothing replayed; the spawn's seed
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "fresh"))
        self.assertEqual(self._day(), {})
        import inspect
        src = inspect.getsource(sb)
        want = ("if loaded_sid and getattr(self, \"_spend_first_result\", False) " + chr(92) + chr(10)
                + "                        and getattr(self, \"_spend_baseline\", \"fresh\") == \"fresh\":")
        self.assertIn(want, src, "the init's cwd re-seed runs for a fresh baseline only, never over a registry or dead-CLI seed")

    def test_the_seed_and_the_result_are_pinned_to_the_registry_road(self):
        import inspect
        seed = inspect.getsource(sb.SdkSession._seed_spend_watermarks)
        self.assertIn('self._spend_baseline = "attach-pending"', seed)
        self.assertIn("self._last_cost_total = 0.0   # a fresh CLI process starts its cumulative cost at zero", seed, "the fresh seed is unchanged")
        on = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn("self._seed_from_reg_cost_state()", on)
        self.assertIn("self._persist_cost_state(self._last_cost_total)", on, "the watermark is written at every result (the kept watermark, not a duplicate's lower total)")
        self.assertIn('costState={"total": float(total), "tokens": dict(self._last_usage_totals),', inspect.getsource(sb.SdkSession._persist_cost_state))


if __name__ == "__main__":
    unittest.main()
