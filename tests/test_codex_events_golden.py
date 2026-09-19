#!/usr/bin/env python3
"""Contract tests for the Codex notification → transcript-record normalizer (kernel/codex_events.py).

Each scenario feeds SYNTHETIC app-server v2 notifications (invented prompts, placeholder ids — never
real session data, per CLAUDE.md) through ThreadNormalizer and pins the records it emits: the uuid
chain, the held-final-message flush (stop_reason placement), tool_use/tool_result pairing, the
compaction stitch, and the error settle. The integration class then writes a full stream's records
to disk and runs the REAL parse_session over the file — the property the whole design rests on:
a Codex-materialized transcript parses into the same Session→Turn→Atom tree a Claude one does.

Run:    python3 tests/test_codex_events_golden.py
"""
import os
import sys
import tempfile
import json
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

# Hermetic state BEFORE the loads (same floor as test_event_model_golden.py).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model", os.path.join(ROOT, "bin", "romp-event-model"))
cx = load_source("romp_codex_events", os.path.join(ROOT, "kernel", "codex_events.py"))

NOW = 1781100000                                   # fixed test clock
TID = "01911111-2222-7333-8444-555555555555"       # synthetic Codex thread id (UUIDv7-shaped)
SID = "11111111-2222-3333-4444-555555555555"       # the session's stable romp uuid
MS = NOW * 1000


def norm(**kw):
    kw.setdefault("thread_id", TID)
    kw.setdefault("cwd", "/TESTDIR")
    kw.setdefault("version", "codex-cli 0.0.0-test")
    kw.setdefault("model", "gpt-5-test")
    kw.setdefault("clock", lambda: NOW)
    return cx.ThreadNormalizer(**kw)


# ── synthetic wire builders (camelCase, the shapes v2_all.py pins) ──
def item_started(item, ms=MS, turn="t1"):
    return "item/started", {"threadId": TID, "turnId": turn, "startedAtMs": ms, "item": item}


def item_completed(item, ms=MS, turn="t1"):
    return "item/completed", {"threadId": TID, "turnId": turn, "completedAtMs": ms, "item": item}


def turn_started(turn="t1"):
    return "turn/started", {"threadId": TID, "turn": {"id": turn, "items": []}}


def turn_completed(turn="t1", status="completed", error=None):
    t = {"id": turn, "items": [], "status": status}
    if error:
        t["error"] = error
    return "turn/completed", {"threadId": TID, "turn": t}


def token_usage(inp=1000, out=50, cached=800, reasoning=25, last_total=1875, window=272000,
                cum_total=999999):
    # last != total on purpose: `last` is the context-occupancy proxy, `total` the thread-cumulative
    # cost number — a fixture where they coincide would hide a reader picking the wrong one
    return "thread/tokenUsage/updated", {
        "threadId": TID, "turnId": "t1",
        "tokenUsage": {"last": {"inputTokens": inp, "outputTokens": out,
                                "cachedInputTokens": cached, "reasoningOutputTokens": reasoning,
                                "totalTokens": last_total},
                       "total": {"inputTokens": inp * 10, "outputTokens": out * 10,
                                 "cachedInputTokens": cached * 10,
                                 "reasoningOutputTokens": reasoning * 10, "totalTokens": cum_total},
                       "modelContextWindow": window}}


def assert_chain(tc, recs, seed=None):
    """The linearity invariant every stream must hold: uuids unique, each record parented on the
    one before it (compact boundaries via logicalParentUuid, parentUuid null by design), never a
    self-parent. Collisions orphan prior history in the FileAdapter walk (review finding #1)."""
    seen = set()
    prev = seed
    for r in recs:
        tc.assertNotIn(r["uuid"], seen, "duplicate uuid %s" % r["uuid"])
        tc.assertNotEqual(r["uuid"], r.get("parentUuid"), "self-parent %s" % r["uuid"])
        if r.get("subtype") == "compact_boundary":
            tc.assertIsNone(r["parentUuid"])
            tc.assertEqual(r["logicalParentUuid"], prev)
        else:
            tc.assertEqual(r.get("parentUuid"), prev, "chain break at %s" % r["uuid"])
        seen.add(r["uuid"])
        prev = r["uuid"]


def user_item(text, iid="u1"):
    return {"type": "userMessage", "id": iid, "content": [{"type": "text", "text": text}]}


def agent_item(text, iid="a1"):
    return {"type": "agentMessage", "id": iid, "text": text}


def compaction_item(iid):
    # the wire shape of a contextCompaction item on Codex 0.153.3: id and type, nothing else (the
    # runtime exposes no summary text and no token counts for a compaction)
    return {"type": "contextCompaction", "id": iid}


def feed(n, *events):
    out = []
    for method, params in events:
        out.extend(n.handle(method, params))
    return out


class Chain(unittest.TestCase):
    def test_simple_turn(self):
        n = norm()
        recs = feed(n,
                    turn_started(),
                    item_completed(user_item("fix the flaky test")),
                    item_completed(agent_item("Done — the sleep is now an event wait.")),
                    token_usage(),
                    turn_completed())
        self.assertEqual([r["type"] for r in recs], ["user", "assistant"])
        u, a = recs
        self.assertEqual(u["promptSource"], "sdk")
        self.assertIsNone(u["parentUuid"])
        self.assertEqual(a["parentUuid"], u["uuid"])
        self.assertEqual(a["message"]["stop_reason"], "end_turn")     # held, flushed by turn end
        self.assertEqual(a["message"]["model"], "gpt-5-test")
        self.assertEqual(a["message"]["usage"]["input_tokens"], 1000)
        self.assertEqual(a["message"]["usage"]["output_tokens"], 75)  # output + reasoning folded
        self.assertEqual(a["message"]["usage"]["cache_read_input_tokens"], 800)
        self.assertEqual(u["cwd"], "/TESTDIR")
        self.assertTrue(u["timestamp"].endswith("Z"))

    def test_multi_call_turn_sums_usage(self):
        # Codex sends one tokenUsage frame per model call, `last` = THAT call's numbers, and a tool
        # round trip is a further call — so a turn's settle must carry the frames' SUM. Keeping only
        # the newest frame reported about 1/N of an N-call turn (1200 / 35 / 900 here, not 2200 /
        # 110 / 1700), and the kernel's analytics sum one usage per assistant record, so a session's
        # Codex turns under-counted by their call count with nothing to flag it.
        n = norm()
        recs = feed(n,
                    turn_started(),
                    item_completed(user_item("go")),
                    item_completed(agent_item("Looking first", "a1")),
                    token_usage(inp=1000, out=50, cached=800, reasoning=25),
                    item_started({"type": "commandExecution", "id": "c1", "command": "ls"}),
                    item_completed({"type": "commandExecution", "id": "c1", "command": "ls",
                                    "aggregatedOutput": "x", "exitCode": 0}),
                    item_completed(agent_item("Done", "a2")),
                    token_usage(inp=1200, out=30, cached=900, reasoning=5),
                    turn_completed())
        settle = recs[-1]
        self.assertEqual(settle["message"]["stop_reason"], "end_turn")
        usage = settle["message"]["usage"]
        self.assertEqual(usage["input_tokens"], 2200)
        self.assertEqual(usage["output_tokens"], 110)               # (50+25) + (30+5), reasoning folded
        self.assertEqual(usage["cache_read_input_tokens"], 1700)
        mid = [r for r in recs if r["type"] == "assistant" and
               r["message"]["content"][0].get("type") == "text"][0]
        self.assertIsNone(mid["message"]["stop_reason"])            # flushed mid-turn by the command
        self.assertNotIn("usage", mid["message"], "the turn's numbers land on the settle, once")
        assert_chain(self, recs)
        # the sum is THIS turn's: the settle took it, so the next turn starts from nothing
        recs2 = feed(n,
                     turn_started("t2"),
                     item_completed(user_item("again", "u2")),
                     item_completed(agent_item("Still fine", "a3")),
                     token_usage(inp=500, out=10, cached=100, reasoning=0),
                     turn_completed("t2"))
        self.assertEqual(recs2[-1]["message"]["usage"]["input_tokens"], 500)
        self.assertEqual(recs2[-1]["message"]["usage"]["output_tokens"], 10)

    def test_mid_turn_text_flushes_null(self):
        n = norm()
        recs = feed(n,
                    item_completed(agent_item("Looking at the config first.", "a1")),
                    item_started({"type": "commandExecution", "id": "c1", "command": "cat cfg.toml"}),
                    item_completed({"type": "commandExecution", "id": "c1", "command": "cat cfg.toml",
                                    "aggregatedOutput": "key=1", "exitCode": 0}),
                    item_completed(agent_item("The config is fine.", "a2")),
                    turn_completed())
        stops = [(r["message"]["content"][0].get("type"), r["message"].get("stop_reason"))
                 for r in recs if r["type"] == "assistant"]
        # mid-turn text: stop null (flushed by the command); final text: end_turn (flushed by turn end)
        self.assertEqual(stops, [("text", None), ("tool_use", None), ("text", "end_turn")])

    def test_command_execution_pairs(self):
        n = norm()
        recs = feed(n,
                    item_started({"type": "commandExecution", "id": "c1", "command": "pytest -x"}),
                    item_completed({"type": "commandExecution", "id": "c1", "command": "pytest -x",
                                    "aggregatedOutput": "1 failed", "exitCode": 1,
                                    "durationMs": 1200}))
        use, res = recs
        blk = use["message"]["content"][0]
        self.assertEqual((blk["type"], blk["name"], blk["input"]["command"]),
                         ("tool_use", "Bash", "pytest -x"))
        rblk = res["message"]["content"][0]
        self.assertEqual(rblk["tool_use_id"], blk["id"])
        self.assertEqual(rblk["content"], "1 failed")         # plain string — the shape the readers render
        self.assertTrue(rblk["is_error"])
        self.assertEqual(res["toolUseResult"]["exitCode"], 1)
        self.assertNotIn("promptSource", res)                 # a tool_result is not a prompt

    def test_command_output_capped(self):
        n = norm()
        big = "x" * (cx.TOOL_OUTPUT_CAP + 500)
        recs = feed(n, item_completed({"type": "commandExecution", "id": "c1",
                                       "command": "yes", "aggregatedOutput": big, "exitCode": 0}))
        text = recs[-1]["message"]["content"][0]["content"]
        self.assertLess(len(text), len(big))
        self.assertIn("truncated", text)

    def test_file_change_kinds(self):
        n = norm()
        recs = feed(n, item_completed({"type": "fileChange", "id": "f1", "status": "completed",
                                       "changes": [
                                           {"path": "/TESTDIR/new.py", "kind": "add", "diff": "+print(1)"},
                                           {"path": "/TESTDIR/old.py", "kind": {"type": "update"},
                                            "diff": "-a\n+b"}]}))
        names = [r["message"]["content"][0]["name"] for r in recs if r["type"] == "assistant"]
        self.assertEqual(names, ["Write", "Edit"])
        diffs = [r["message"]["content"][0]["content"] for r in recs if r["type"] == "user"]
        self.assertEqual(diffs, ["+print(1)", "-a\n+b"])
        assert_chain(self, recs)

    def test_mcp_and_reasoning(self):
        n = norm()
        recs = feed(n,
                    item_completed({"type": "reasoning", "id": "r1",
                                    "content": ["The failure is in the fixture."], "summary": []}),
                    item_completed({"type": "mcpToolCall", "id": "m1", "server": "postal",
                                    "tool": "send_message", "arguments": {"to": "web"},
                                    "status": "failed",
                                    "error": {"message": "no such peer"}}))
        think = recs[0]["message"]["content"][0]
        self.assertEqual((think["type"], think["thinking"]),
                         ("thinking", "The failure is in the fixture."))
        use = recs[1]["message"]["content"][0]
        self.assertEqual(use["name"], "mcp__postal__send_message")
        self.assertEqual(use["input"], {"to": "web"})
        self.assertTrue(recs[2]["message"]["content"][0]["is_error"])

    def test_a_dynamic_tool_call_renders_as_the_postal_tool_pair(self):
        # a Codex DYNAMIC tool call (the kernel-serviced postal tools, 2026-09-19) lands as the tool_use/tool_result
        # pair an mcpToolCall does, named mcp__romp-postal-service__<tool>: exact parity with a Claude session's
        # postal send in chat, the timeline and the judges' view. The item has no server or error field (the SDK
        # model: arguments, contentItems, durationMs, id, namespace, status, success, tool); its text is the
        # inputText content items. test_phase2_vocabulary_counted_not_silent stays green: it pins plan and
        # subAgentActivity only, and a dynamic tool call is no longer counted as skipped.
        args = {"to": "web", "body": "the tests are green", "kind": "coordinate"}
        n = norm()
        recs = feed(n,
                    item_started({"type": "dynamicToolCall", "id": "exec-1", "namespace": None,
                                  "tool": "send_message", "arguments": args, "status": "inProgress",
                                  "contentItems": None, "success": None, "durationMs": None}),
                    item_completed({"type": "dynamicToolCall", "id": "exec-1", "namespace": None,
                                    "tool": "send_message", "arguments": args, "status": "completed",
                                    "contentItems": [{"type": "inputText", "text": "Delivered to 'web'."}],
                                    "success": True, "durationMs": 3}))
        self.assertEqual([r["type"] for r in recs], ["assistant", "user"])
        use = recs[0]["message"]["content"][0]
        self.assertEqual((use["type"], use["id"], use["name"], use["input"]),
                         ("tool_use", "exec-1", "mcp__romp-postal-service__send_message", args))
        res = recs[1]["message"]["content"][0]
        self.assertEqual((res["type"], res["tool_use_id"], res["content"]),
                         ("tool_result", "exec-1", "Delivered to 'web'."))
        self.assertNotIn("is_error", res)
        self.assertEqual(n.skipped, {})
        assert_chain(self, recs)

    def test_a_failed_or_namespaced_dynamic_tool_call(self):
        n = norm()
        # attached at completed only (a resume mid-turn): the invocation is written first; a namespace names the
        # server; success False is the error signal
        recs = feed(n, item_completed({"type": "dynamicToolCall", "id": "exec-2", "namespace": "notes",
                                       "tool": "check_inbox", "arguments": {}, "status": "failed",
                                       "contentItems": [{"type": "inputText", "text": "Unknown tool: check_inbox"}],
                                       "success": False, "durationMs": 1}))
        self.assertEqual([r["type"] for r in recs], ["assistant", "user"])
        self.assertEqual(recs[0]["message"]["content"][0]["name"], "mcp__notes__check_inbox")
        res = recs[1]["message"]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertEqual(res["content"], "Unknown tool: check_inbox")
        # status failed with no content and success unknown: still an error, with a placeholder the reader can see
        recs = feed(n, item_completed({"type": "dynamicToolCall", "id": "exec-3", "namespace": None,
                                       "tool": "set_working", "arguments": {"text": "x"}, "status": "failed",
                                       "contentItems": None, "success": None, "durationMs": None}))
        res = recs[1]["message"]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertEqual(res["content"], "[tool call failed]")
        self.assertEqual(n.skipped, {})

    def test_user_message_dedup(self):
        n = norm()
        recs = feed(n,
                    item_started(user_item("hello", "u1")),
                    item_completed(user_item("hello", "u1")))
        self.assertEqual(len(recs), 1)

    def test_several_inputs_land_as_one_text_block_each(self):
        # The backend starts a turn from its WHOLE queue, one input per queued send, and the app-server
        # answers one userMessage item carrying them all. Each input is its own text block: a user record
        # with several text blocks is a shape the kernel already reads per block (_atom_user_texts, behind
        # the echo prune and the pending-bubble pass; romp bundles its own injected messages that way).
        # Joined into one block ("first send\nsecond send") the record matches no send's echo, and the
        # echoes of a turn started from several sends never retire. A placeholder for a non-text input is
        # a block of its own.
        n = norm()
        two = {"type": "userMessage", "id": "u1",
               "content": [{"type": "text", "text": "first send"},
                           {"type": "text", "text": "  second send\n"}]}
        recs = feed(n, turn_started(), item_completed(two))
        self.assertEqual([r["type"] for r in recs], ["user"], "one record for the one item")
        self.assertEqual(recs[0]["message"]["content"],
                         [{"type": "text", "text": "first send"}, {"type": "text", "text": "second send"}],
                         "a block per input, each stripped of outer whitespace")
        mixed = {"type": "userMessage", "id": "u2",
                 "content": [{"type": "text", "text": "   "},
                             {"type": "localImage", "path": "/TESTDIR/shot.png"},
                             {"type": "skill", "name": "review"},
                             {"type": "text", "text": "and the tests"}]}
        recs = feed(n, item_completed(mixed))
        self.assertEqual([b["text"] for b in recs[0]["message"]["content"]],
                         ["[Image: /TESTDIR/shot.png]", "review", "and the tests"],
                         "a blank input is dropped; placeholders keep their own block")
        # one text input writes exactly what it wrote before: one block, outer whitespace stripped
        recs = feed(n, item_completed(user_item("  fix the flaky test\n", "u3")))
        self.assertEqual(recs[0]["message"]["content"], [{"type": "text", "text": "fix the flaky test"}])
        self.assertEqual(feed(n, item_completed(user_item("  ", "u4"))), [], "every input blank: no record")

    def test_compaction_stitch(self):
        """Deprecated-runtime path (2026-09-19): the thread/compacted notification, which Codex
        0.153.3 never sends (it emits the contextCompaction item instead), still writes the boundary
        when no item did."""
        n = norm()
        recs = feed(n,
                    item_completed(user_item("start", "u1")),
                    item_completed(agent_item("ok", "a1")),
                    turn_completed(),
                    ("thread/compacted", {"threadId": TID, "turnId": "t2"}),
                    item_completed(user_item("continue", "u2")))
        cb = recs[2]
        self.assertEqual((cb["type"], cb["subtype"]), ("system", "compact_boundary"))
        self.assertIsNone(cb["parentUuid"])
        self.assertEqual(cb["logicalParentUuid"], recs[1]["uuid"])   # the pre-compaction leaf
        self.assertEqual(recs[3]["parentUuid"], cb["uuid"])          # the chain continues off it

    def test_turn_failed_is_error_card(self):
        n = norm()
        recs = feed(n,
                    item_completed(user_item("do a thing")),
                    turn_completed(status="failed", error={"message": "quota exhausted"}))
        err = recs[-1]
        self.assertTrue(err["isApiErrorMessage"])
        self.assertEqual(err["message"]["content"][0]["text"], "quota exhausted")
        self.assertEqual(err["message"]["stop_reason"], "end_turn")

    def test_retryable_error_writes_nothing(self):
        n = norm()
        self.assertEqual(feed(n, ("error", {"threadId": TID, "turnId": "t1", "willRetry": True,
                                            "error": {"message": "overloaded"}})), [])

    def test_phase2_vocabulary_counted_not_silent(self):
        n = norm()
        recs = feed(n,
                    item_completed({"type": "plan", "id": "p1", "text": "1. look 2. fix"}),
                    item_completed({"type": "subAgentActivity", "id": "s1", "kind": "spawned",
                                    "agentPath": "x", "agentThreadId": "t9"}))
        self.assertEqual(recs, [])
        self.assertEqual(n.skipped, {"plan": 1, "subAgentActivity": 1})

    def test_interrupt_materializes_settle_record(self):
        n = norm()
        recs = feed(n,
                    item_completed(user_item("run it")),
                    item_started({"type": "commandExecution", "id": "c1", "command": "sleep 99"}),
                    turn_completed(status="interrupted"))
        # the CLI-convention interrupt record ends the turn (is_interrupt_record) — without it the
        # NEXT prompt is absorbed into this turn instead of opening its own (review finding #2)
        self.assertEqual([r["type"] for r in recs], ["user", "assistant", "user"])
        last = recs[-1]
        self.assertTrue(em._text_of(last["message"]["content"]).startswith("[Request interrupted"))
        self.assertNotIn("promptSource", last)
        self.assertFalse(n.turn_open)
        assert_chain(self, recs)

    def test_context_tracking_uses_last_not_cumulative(self):
        n = norm()
        feed(n, token_usage(last_total=54000, window=272000, cum_total=500000))
        self.assertEqual(n.context, (54000, 272000))   # `total` would read 184% full by turn ten


class ReviewRegressions(unittest.TestCase):
    """Record-level pins for the 2026-08-13 adversarial-review findings, and (2026-09-19) for the
    in-turn compaction record. The thread/compacted goldens here and in Chain and ParseIntegration
    pin the DEPRECATED-RUNTIME notification path: Codex 0.153.3 never sends that notification (its
    app-server emits a contextCompaction item on the running turn instead, the tests below), but a
    runtime that does still writes the same boundary through it, once per compaction."""

    def test_double_compaction_unique_boundaries(self):
        """Deprecated-runtime path (2026-09-19): the thread/compacted notification, which Codex
        0.153.3 never sends (it emits the contextCompaction item instead), still writes the boundary
        when no item did."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1")),
                    item_completed(agent_item("chunk one", "a1")),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(agent_item("chunk two", "a2")),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(agent_item("done", "a3")),
                    turn_completed("t1"))
        cbs = [r["uuid"] for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(cbs), 2)
        self.assertEqual(len(set(cbs)), 2, "boundary uuids must not collide")
        assert_chain(self, recs)

    def test_compaction_flushes_held_reply_first(self):
        """Deprecated-runtime path (2026-09-19): the thread/compacted notification, which Codex
        0.153.3 never sends (it emits the contextCompaction item instead), still writes the boundary
        when no item did."""
        n = norm()
        recs = feed(n,
                    item_completed(user_item("go", "u1")),
                    item_completed(agent_item("all done", "a1")),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}))
        # the held reply is pre-compaction content: it lands BEFORE the boundary, and the stitch
        # points at it (review finding #4 — a same-second tie otherwise steals the reply)
        self.assertEqual([r["type"] for r in recs], ["user", "assistant", "system"])
        self.assertEqual(recs[2]["logicalParentUuid"], recs[1]["uuid"])

    def test_terminal_error_then_failed_settle_single_card(self):
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("do a thing", "u1")),
                    ("error", {"threadId": TID, "turnId": "t1", "willRetry": False,
                               "error": {"message": "quota exhausted"}}),
                    turn_completed("t1", status="failed", error={"message": "quota exhausted"}))
        cards = [r for r in recs if r.get("isApiErrorMessage")]
        self.assertEqual(len(cards), 1, "one failure, one card (review finding #5)")
        assert_chain(self, recs)

    def test_two_terminal_errors_keep_chain(self):
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("try it", "u1")),
                    ("error", {"threadId": TID, "turnId": "t1", "willRetry": False,
                               "error": {"message": "err one"}}),
                    ("error", {"threadId": TID, "turnId": "t1", "willRetry": False,
                               "error": {"message": "err two"}}))
        assert_chain(self, recs)   # the second settle must uniquify, never self-parent

    def test_declined_command_is_error(self):
        n = norm()
        recs = feed(n, item_completed({"type": "commandExecution", "id": "c1",
                                       "command": "rm -rf /", "status": "declined",
                                       "exitCode": None, "aggregatedOutput": ""}))
        blk = recs[-1]["message"]["content"][0]
        self.assertTrue(blk["is_error"], "a sandbox refusal must never read as success")
        self.assertIn("declined", blk["content"])

    def test_failed_patch_is_error(self):
        n = norm()
        recs = feed(n, item_completed({"type": "fileChange", "id": "f1", "status": "failed",
                                       "changes": [{"path": "/TESTDIR/x.py", "kind": "update",
                                                    "diff": "-a\n+b"}]}))
        blk = recs[-1]["message"]["content"][0]
        self.assertTrue(blk["is_error"])
        self.assertIn("failed", blk["content"])

    def test_usage_never_leaks_across_turns(self):
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("one", "u1")),
                    token_usage(inp=111),
                    turn_completed("t1", status="failed", error={"message": "boom"}),
                    turn_started("t2"),
                    item_completed(user_item("two", "u2")),
                    item_completed(agent_item("fine now", "a2")),
                    turn_completed("t2"))
        settle = [r for r in recs if r["type"] == "assistant" and
                  r["message"].get("stop_reason") == "end_turn" and
                  not r.get("isApiErrorMessage")][-1]
        self.assertNotIn("usage", settle["message"], "turn 1's usage on turn 2's settle (finding #9)")

    def test_restart_seed_prevents_replay_damage(self):
        # the file already carries u1 + the c1 tool_use; the process restarted between the
        # command's started and completed (review finding #6)
        n = norm(last_uuid="c1", seen_uuids={"u1", "c1"})
        recs = feed(n,
                    item_completed(user_item("run the suite", "u1")),       # replayed → dropped
                    item_completed({"type": "commandExecution", "id": "c1", "command": "pytest",
                                    "aggregatedOutput": "ok", "exitCode": 0}))
        self.assertEqual(len(recs), 1)                    # just the result — no re-minted call
        self.assertEqual(recs[0]["parentUuid"], "c1")
        self.assertNotEqual(recs[0]["uuid"], "c1")
        assert_chain(self, recs, seed="c1")

    def test_drain_flushes_held_message(self):
        n = norm()
        feed(n, item_completed(agent_item("almost forgot this", "a1")))
        recs = n.drain()
        self.assertEqual(len(recs), 1)
        self.assertIsNone(recs[0]["message"]["stop_reason"])   # the turn genuinely didn't settle
        self.assertEqual(n.drain(), [])

    def test_in_turn_auto_compaction_item_writes_one_boundary(self):
        """THE DEFECT (2026-09-19): Codex 0.153.3 compacts inside a running turn by emitting a
        contextCompaction item on that turn and never sends thread/compacted (deprecated), so a
        session that overflowed mid-turn got no compact_boundary record at all: no divider in the
        chat, an unbroken window for every boundary reader. The item's completed is the exact signal
        (it fires only after the runtime replaced the history) and writes the boundary. Red on stock
        at the count: 0 != 1."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one", "a1"), MS + 1000),
                    item_started(compaction_item("cc1"), MS + 5000),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(agent_item("chunk two", "a2"), MS + 12000),
                    turn_completed("t1"))
        cbs = [r for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(cbs), 1, "one compaction, one boundary")
        cb = cbs[0]
        self.assertEqual(cb["uuid"], "cc1", "keyed by the wire item id")
        self.assertIsNone(cb["parentUuid"])
        self.assertEqual(cb["logicalParentUuid"], recs[1]["uuid"],
                         "the held reply flushed first; the stitch points at it")
        self.assertEqual(recs[3]["parentUuid"], "cc1", "the continuation chains off the boundary")
        self.assertEqual(cb["compactMetadata"], {"trigger": "auto"})
        self.assertEqual(cb["timestamp"], cx._iso(MS + 9000, lambda: NOW),
                         "the wire's completedAtMs, not the clock")
        self.assertEqual([r["type"] for r in recs], ["user", "assistant", "system", "assistant"])
        self.assertEqual(recs[-1]["message"]["stop_reason"], "end_turn")
        self.assertNotIn("contextCompaction", n.skipped, "written, not counted as skipped vocabulary")
        assert_chain(self, recs)

    def test_in_turn_compaction_item_then_notification_one_boundary(self):
        """A runtime that sends BOTH signals for one compaction (the item, then thread/compacted for
        the same turn) gets one boundary, written by whichever arrives first: boundaries per turn =
        max(items seen, notifications seen). Red on stock at the uuid ('cb-t1' != 'cc1'): the
        notification wrote it because the item wrote nothing. The notification-first order is green
        on stock and pins the mirror."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one", "a1"), MS + 1000),
                    item_started(compaction_item("cc1"), MS + 5000),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(agent_item("chunk two", "a2"), MS + 12000),
                    turn_completed("t1"))
        cbs = [r for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(cbs), 1)
        self.assertEqual(cbs[0]["uuid"], "cc1")
        assert_chain(self, recs)
        # the mirror: notification first, then the item; one boundary, the notification's
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one", "a1"), MS + 1000),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_started(compaction_item("cc1"), MS + 5000),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(agent_item("chunk two", "a2"), MS + 12000),
                    turn_completed("t1"))
        cbs = [r for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(cbs), 1)
        self.assertEqual(cbs[0]["uuid"], "cb-t1")
        assert_chain(self, recs)

    def test_two_in_turn_compactions_two_boundaries(self):
        """Two compactions in one turn, each as an item followed by a notification, write exactly
        two boundaries named by the item ids (distinct, so the chain never collides). Red on stock
        at the uuid set: the notifications wrote 'cb-t1' and 'cb-t1~1'."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one", "a1"), MS + 1000),
                    item_started(compaction_item("cc1"), MS + 5000),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(agent_item("chunk two", "a2"), MS + 12000),
                    item_started(compaction_item("cc2"), MS + 15000),
                    item_completed(compaction_item("cc2"), MS + 19000),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(agent_item("done", "a3"), MS + 20000),
                    turn_completed("t1"))
        cbs = [r["uuid"] for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(cbs), 2)
        self.assertEqual(set(cbs), {"cc1", "cc2"})
        assert_chain(self, recs)

    def test_replayed_compaction_item_writes_no_second_boundary(self):
        """A contextCompaction item re-delivered across a reconnect or restart writes nothing more.
        Three faces: the first delivery wrote the boundary (red on stock at the count, 0 != 1, since
        no item writes there); the file already carries it, a normalizer seeded from the file (green
        on stock); a thread/compacted had already paired it, so the first delivery was skipped (green
        on stock, and the guard the skip path needs: counting alone would let the re-delivery
        outnumber the notification and write a second boundary)."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(agent_item("chunk two", "a2"), MS + 12000),
                    turn_completed("t1"))
        cbs = [r for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(cbs), 1)
        assert_chain(self, recs)
        n = norm(seen_uuids={"cc1"}, last_uuid="a0")
        self.assertEqual(feed(n, item_completed(compaction_item("cc1"), MS + 9000)), [])
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(agent_item("chunk two", "a2"), MS + 12000),
                    turn_completed("t1"))
        cbs = [r["uuid"] for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(cbs, ["cb-t1"])
        assert_chain(self, recs)

    def test_notification_without_turn_id_always_writes(self):
        """A thread/compacted with NO turnId (the shape a standalone compaction's writer would
        synthesize; every wire notification carries one) is never paired against a turn's items, so
        it always writes, with its own trigger: the dedup keys on the notification's own turnId,
        never on the normalizer's last-turn fallback, which would swallow this boundary whenever that
        turn had carried an in-turn compaction. Red on stock at the COUNT (1 != 2): the item wrote
        nothing, so only the notification's boundary exists."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(agent_item("chunk two", "a2"), MS + 12000),
                    turn_completed("t1"),
                    ("thread/compacted", {"threadId": TID, "trigger": "manual"}))
        cbs = [r for r in recs if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(cbs), 2)
        self.assertEqual(cbs[0]["uuid"], "cc1")
        self.assertEqual(cbs[1]["compactMetadata"], {"trigger": "manual"})
        assert_chain(self, recs)

    def test_compaction_counters_never_raise_on_an_unseen_turn(self):
        """The per-turn compaction counters count a turn they have never seen instead of raising: a
        raise inside handle aborts the live turn (the backend's worker interrupts it and writes the
        abandoned card). The item arm with no turn/started before it and a notification for another
        unseen turn each write one boundary. Red on stock at the item half's count (0 != 1)."""
        n = norm()
        recs = feed(n, item_completed(compaction_item("cc7"), MS + 9000, turn="t7"))
        self.assertEqual(len([r for r in recs if r.get("subtype") == "compact_boundary"]), 1)
        more = feed(n, ("thread/compacted", {"threadId": TID, "turnId": "t8"}))
        self.assertEqual(len([r for r in more if r.get("subtype") == "compact_boundary"]), 1)
        assert_chain(self, recs + more)


class ParseIntegration(unittest.TestCase):
    """The design's load-bearing property: the materialized file parses like a Claude transcript."""

    def _parse(self, recs):
        d = Path(tempfile.mkdtemp())
        p = d / ("%s.jsonl" % TID)
        p.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return em.parse_session(str(p), rompuuid=SID, name="cx", dir="/TESTDIR",
                                candidate_files=[str(p)], now=NOW, sdk_human=True)

    def test_two_turns_with_tools(self):
        n = norm()
        ms = MS
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("add a retry to the fetcher", "u1"), ms),
                    item_completed({"type": "reasoning", "id": "r1", "content": ["Find the fetcher."]},
                                   ms + 1000),
                    item_started({"type": "commandExecution", "id": "c1",
                                  "command": "grep -rn fetch src/"}, ms + 2000),
                    item_completed({"type": "commandExecution", "id": "c1",
                                    "command": "grep -rn fetch src/",
                                    "aggregatedOutput": "src/f.py:12", "exitCode": 0}, ms + 3000),
                    item_completed(agent_item("Added the retry with backoff.", "a1"), ms + 4000),
                    turn_completed("t1"),
                    turn_started("t2"),
                    item_completed(user_item("now add a test for it", "u2"), ms + 60000),
                    item_completed(agent_item("Test added and passing.", "a2"), ms + 61000),
                    turn_completed("t2"))
        s = self._parse(recs)
        self.assertEqual(len(s["turns"]), 2)
        t1, t2 = s["turns"]
        self.assertTrue(t1["ended"] and t2["ended"])
        self.assertEqual(t1["atoms"][0]["author"], "human")          # sdk_human: the human bubble
        types = [b for a in t1["atoms"] for b in em._block_types(a["message"]["content"])]
        self.assertIn("thinking", types)
        self.assertIn("tool_use", types)
        self.assertIn("tool_result", types)
        self.assertEqual(t2["atoms"][-1]["message"]["stop_reason"], "end_turn")

    def test_compaction_survives_parse(self):
        """Deprecated-runtime path (2026-09-19): the thread/compacted notification, which Codex
        0.153.3 never sends (it emits the contextCompaction item instead), still writes the boundary
        when no item did."""
        n = norm()
        recs = feed(n,
                    item_completed(user_item("long refactor", "u1"), MS),
                    item_completed(agent_item("chunk one done", "a1"), MS + 1000),
                    turn_completed("t1"),
                    ("thread/compacted", {"threadId": TID, "turnId": "t2"}),
                    item_completed(user_item("keep going", "u2"), MS + 9000),
                    item_completed(agent_item("chunk two done", "a2"), MS + 10000),
                    turn_completed("t2"))
        s = self._parse(recs)
        texts = [em._text_of(a["message"]["content"])
                 for t in s["turns"] for a in t["atoms"] if a.get("message")]
        # the stitch holds: pre-compaction turns are still on the active path
        self.assertTrue(any("long refactor" in x for x in texts))
        self.assertTrue(any("chunk two done" in x for x in texts))

    def test_error_settle_becomes_error_atom(self):
        n = norm()
        recs = feed(n,
                    item_completed(user_item("do a thing", "u1"), MS),
                    turn_completed("t1", status="failed", error={"message": "boom"}))
        s = self._parse(recs)
        flags = [a.get("isApiError") for t in s["turns"] for a in t["atoms"]]
        self.assertIn(True, flags)

    def test_interrupt_then_next_prompt_opens_its_own_turn(self):
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("run it", "u1"), MS),
                    item_started({"type": "commandExecution", "id": "c1", "command": "sleep 99"},
                                 MS + 1000),
                    turn_completed("t1", status="interrupted"),
                    turn_started("t2"),
                    item_completed(user_item("never mind, just lint", "u2"), MS + 9000),
                    item_completed(agent_item("Linted.", "a2"), MS + 10000),
                    turn_completed("t2"))
        s = self._parse(recs)
        # without the interrupt record the second prompt was ABSORBED into the interrupted turn
        # (review finding #2) — it must be its own trigger
        self.assertEqual(len(s["turns"]), 2)
        self.assertEqual(s["turns"][1]["trigger"]["uuid"], "u2")
        self.assertTrue(s["turns"][0]["ended"] and s["turns"][1]["ended"])

    def test_double_compaction_keeps_early_history(self):
        """Deprecated-runtime path (2026-09-19): the thread/compacted notification, which Codex
        0.153.3 never sends (it emits the contextCompaction item instead), still writes the boundary
        when no item did."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one", "a1"), MS + 1000),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(agent_item("chunk two", "a2"), MS + 9000),
                    ("thread/compacted", {"threadId": TID, "turnId": "t1"}),
                    item_completed(agent_item("done", "a3"), MS + 20000),
                    turn_completed("t1"))
        s = self._parse(recs)
        texts = [em._text_of(a["message"]["content"])
                 for t in s["turns"] for a in t["atoms"] if a.get("message")]
        # colliding boundary uuids used to orphan everything before the first compaction
        self.assertTrue(any("start the refactor" in x for x in texts))
        self.assertTrue(any("done" in x for x in texts))

    def test_in_turn_compaction_survives_parse(self):
        """An in-turn compaction (the contextCompaction item on the running turn) parses like a
        Claude one: the boundary is an atom, both sides of it stay on the active path, and the
        boundary opens its own turn that carries the continuation and reads ended. Only the
        boundary's turn is asserted ended: the pre-boundary reply flushed with stop null, so its
        turn reads open, as a Claude pre-boundary segment does. Red on stock: no boundary atom."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one done", "a1"), MS + 1000),
                    item_started(compaction_item("cc1"), MS + 5000),
                    item_completed(compaction_item("cc1"), MS + 9000),
                    item_completed(agent_item("chunk two done", "a2"), MS + 12000),
                    turn_completed("t1"))
        s = self._parse(recs)
        atoms = [a for t in s["turns"] for a in t["atoms"]]
        self.assertTrue(any(a.get("subtype") == "compact_boundary" for a in atoms),
                        "the boundary is an atom")
        texts = [em._text_of(a["message"]["content"]) for a in atoms if a.get("message")]
        self.assertTrue(any("start the refactor" in x for x in texts))
        self.assertTrue(any("chunk two done" in x for x in texts))
        bt = next(t for t in s["turns"] if t["atoms"][0].get("subtype") == "compact_boundary")
        self.assertTrue(any("chunk two done" in em._text_of(a["message"]["content"])
                            for a in bt["atoms"] if a.get("message")),
                        "the continuation lands in the boundary's own turn")
        self.assertTrue(bt["ended"], "the continuation's end_turn ends that turn")

    def test_pre_turn_compaction_keeps_the_prompt_as_trigger(self):
        """The common Codex shape: a turn that ended over the limit compacts at the START of the
        next one, so the contextCompaction item completes BEFORE that turn's userMessage is recorded.
        The parse yields a boundary-only turn and then the prompt's own turn with the prompt as its
        trigger. The prompt repeats no earlier text, so this golden holds with or without the read
        side's replay-window change; the repeated-prompt case is the next golden's. Red on stock: no
        boundary turn precedes the prompt's."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one done", "a1"), MS + 1000),
                    turn_completed("t1"),
                    turn_started("t2"),
                    item_started(compaction_item("cc2"), MS + 5000, turn="t2"),
                    item_completed(compaction_item("cc2"), MS + 9000, turn="t2"),
                    item_completed(user_item("now write the tests", "u2"), MS + 9500, turn="t2"),
                    item_completed(agent_item("tests written", "a2"), MS + 12000, turn="t2"),
                    turn_completed("t2"))
        s = self._parse(recs)
        trig = [(t["trigger"] or {}).get("uuid") for t in s["turns"]]
        self.assertIn("u2", trig, "the prompt after a pre-turn compaction is still a trigger")
        i = trig.index("u2")
        self.assertTrue(i > 0 and s["turns"][i - 1]["atoms"][0].get("subtype") == "compact_boundary",
                        "a boundary-only turn precedes the prompt's")
        self.assertEqual(len(s["turns"]), 3)
        self.assertTrue(all(t["ended"] for t in s["turns"]))

    def test_pre_turn_compaction_repeated_prompt_stays_the_trigger(self):
        """The same pre-turn shape with the second prompt repeating the first VERBATIM (the live
        case: a canned follow-up sent more than once, "continue" twice). The parser's post-compaction
        replay guard once armed at every attached boundary and dropped the next user record whose
        text it had already seen, a guard built for the Claude CLI's boundary, summary, replayed-tail
        shape; a Codex boundary is followed directly by the person's next prompt, with no summary
        record between, so that prompt vanished from the chat and the turns and its reply filed
        triggerless under the boundary's turn. This golden pins the landing order (2026-09-19): it
        is red on a tree that writes the Codex boundary without the event-model change that arms the
        guard on the summary record, and green stacked on it, so a wrong merge order fails here
        instead of dropping prompts. It also cross-pins the boundary shape the normalizer writes
        against the parser, which that change's own test hand-builds."""
        n = norm()
        recs = feed(n,
                    turn_started("t1"),
                    item_completed(user_item("start the refactor", "u1"), MS),
                    item_completed(agent_item("chunk one done", "a1"), MS + 1000),
                    turn_completed("t1"),
                    turn_started("t2"),
                    item_started(compaction_item("cc2"), MS + 5000, turn="t2"),
                    item_completed(compaction_item("cc2"), MS + 9000, turn="t2"),
                    item_completed(user_item("start the refactor", "u2"), MS + 9500, turn="t2"),
                    item_completed(agent_item("chunk two done", "a2"), MS + 12000, turn="t2"),
                    turn_completed("t2"))
        s = self._parse(recs)
        atoms = [a["uuid"] for t in s["turns"] for a in t["atoms"]]
        self.assertIn("u2", atoms, "the repeated prompt is an atom, not a dropped replay")
        trig = [(t["trigger"] or {}).get("uuid") for t in s["turns"]]
        self.assertIn("u2", trig, "the repeated prompt triggers its own turn")
        i = trig.index("u2")
        self.assertTrue(i > 0 and s["turns"][i - 1]["atoms"][0].get("subtype") == "compact_boundary",
                        "a boundary-only turn precedes the prompt's")
        self.assertTrue(any("chunk two done" in em._text_of(a["message"]["content"])
                            for a in s["turns"][i]["atoms"] if a.get("message")),
                        "the reply lands in the repeated prompt's own turn, not the boundary's")
        self.assertEqual(len(s["turns"]), 3)
        self.assertTrue(all(t["ended"] for t in s["turns"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
