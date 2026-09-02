#!/usr/bin/env python3
"""SdkBackend (bin/romp_sdk_backend.py) — the non-tmux session backend.

Two layers:
  * Pure translation logic (AskUserQuestion <-> the existing askLive picker shape,
    state/registry files) is tested WITHOUT the SDK, so it runs in CI.
  * The async runner + the can_use_tool round-trip is tested with a FAKE
    ClaudeSDKClient (monkeypatched in), skipped where claude_agent_sdk is absent.
    This exercises the headline path: a user turn -> the model calls
    AskUserQuestion -> it surfaces as an askLive picker -> the UI answers ->
    PermissionResultAllow(updated_input={questions, answers}) goes back.
"""
import os
import json
import threading
import time
import tempfile
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py")).load_module()


class PureTranslation(unittest.TestCase):
    def test_single_question_to_live(self):
        q = {"question": "Pick one", "header": "H", "multiSelect": False,
             "options": [{"label": "A", "description": "aa"}, {"label": "B", "description": "bb"}]}
        ask = sb.ask_question_to_live(q, 0, 1)
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(ask["header"], "H")
        self.assertFalse(ask["multiSelect"])
        real = [o for o in ask["options"] if o["label"] != sb.TYPE_SOMETHING]
        self.assertEqual([o["n"] for o in real], [1, 2])
        self.assertEqual(ask["options"][0]["label"], "A")
        self.assertEqual(ask["options"][0]["desc"], "aa")
        self.assertFalse(ask["options"][0]["selected"])
        # the "add your own" affordance is ALWAYS offered, as a trailing meta option (the user 2026-06-27)
        self.assertEqual(ask["options"][-1]["label"], sb.TYPE_SOMETHING)
        self.assertEqual(ask["options"][-1]["n"], 3, "contiguous after the real options")
        self.assertNotIn("progress", ask)        # single question -> no "n of m"

    def test_add_your_own_offered_for_multi_and_shows_typed_customs(self):
        q = {"question": "Pick many", "header": "H", "multiSelect": True,
             "options": [{"label": "A"}, {"label": "B"}]}
        ask = sb.ask_question_to_live(q, 0, 1, selected={1}, customs=["my own thing"])
        labels = [o["label"] for o in ask["options"]]
        self.assertEqual(labels, ["A", "B", "my own thing", sb.TYPE_SOMETHING])
        self.assertTrue(ask["options"][2]["checked"], "a typed custom shows as a checked row")
        self.assertFalse(ask["options"][3]["checked"], "the meta add-your-own row is not checked")
        self.assertEqual([o["n"] for o in ask["options"]], [1, 2, 3, 4], "contiguous ordinals")

    def test_multi_question_marks_checked_and_progress(self):
        q = {"question": "Pick many", "header": "H", "multiSelect": True,
             "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]}
        ask = sb.ask_question_to_live(q, 1, 3, selected={2})
        self.assertEqual(ask["kind"], "multi")
        self.assertTrue(ask["options"][1]["checked"])      # option 2 toggled on
        self.assertFalse(ask["options"][0]["checked"])
        self.assertEqual(ask["progress"], {"i": 2, "n": 3})

    def test_preview_passthrough(self):
        q = {"question": "q", "header": "h", "multiSelect": False,
             "options": [{"label": "A", "preview": "<b>mock</b>"}]}
        ask = sb.ask_question_to_live(q, 0, 1)
        self.assertEqual(ask["options"][0]["preview"], "<b>mock</b>")

    def test_label_for_target(self):
        q = {"options": [{"label": "cats"}, {"label": "dogs"}]}
        self.assertEqual(sb.label_for_target(q, 1), "cats")
        self.assertEqual(sb.label_for_target(q, "2"), "dogs")
        self.assertEqual(sb.label_for_target(q, 9), "9")        # out of range -> verbatim
        self.assertEqual(sb.label_for_target(q, "custom text"), "custom text")

    def test_build_answers(self):
        qs = [{"question": "Q1"}, {"question": "Q2"}, {"question": "Q3"}]
        ans = sb.build_answers(qs, {0: "a", 2: ["x", "y"]})
        self.assertEqual(ans, {"Q1": "a", "Q3": ["x", "y"]})    # Q2 unanswered -> omitted

    def test_permission_to_live(self):
        ask = sb.permission_to_live("Bash", {"command": "rm -rf /tmp/x"})
        self.assertTrue(ask["permission"])
        self.assertEqual([o["label"] for o in ask["options"]], ["Allow", "Deny"])
        self.assertIn("rm -rf", ask["question"])
        self.assertNotIn("preview", ask, "a Bash command has no diff preview")

    def test_permission_uses_sdk_title_and_description(self):
        class Ctx:
            title = "Claude wants to read foo.txt"
            description = "Read a file in the project"
            suggestions = []
        ask = sb.permission_to_live("Read", {"file_path": "foo.txt"}, Ctx())
        self.assertEqual(ask["question"], "Claude wants to read foo.txt", "uses the SDK's own prompt sentence")
        self.assertEqual(ask["options"][0]["desc"], "Read a file in the project")

    def test_permission_adds_allow_and_remember_when_suggestions_present(self):
        class Ctx:
            title = None; description = None
            suggestions = ["<a PermissionUpdate>"]   # truthy → offer the remember option
        ask = sb.permission_to_live("Bash", {"command": "ls"}, Ctx())
        labels = [o["label"] for o in ask["options"]]
        self.assertEqual(labels, ["Allow", "Allow & don't ask again", "Deny"])
        self.assertEqual([o["n"] for o in ask["options"]], [1, 2, 3], "ordinals stay 1..N for arrow-nav")

    def test_edit_permission_carries_a_diff_preview(self):
        ask = sb.permission_to_live("Edit", {"file_path": "a.py", "old_string": "x = 1\ny = 2",
                                              "new_string": "x = 1\ny = 3"})
        self.assertEqual(ask["previewKind"], "diff")
        self.assertIn("a.py", ask["preview"])
        self.assertIn("-y = 2", ask["preview"])
        self.assertIn("+y = 3", ask["preview"])

    def test_tool_preview_write_plan_multiedit_and_none(self):
        # Write → all-additions diff
        kind, txt = sb.tool_preview("Write", {"file_path": "n.txt", "content": "hello\nworld"})
        self.assertEqual(kind, "diff")
        self.assertIn("+hello", txt); self.assertIn("+world", txt)
        # ExitPlanMode → the plan verbatim, kind "plan"
        kind, txt = sb.tool_preview("ExitPlanMode", {"plan": "1. do this\n2. then that"})
        self.assertEqual(kind, "plan"); self.assertIn("do this", txt)
        # MultiEdit → concatenated diffs
        kind, txt = sb.tool_preview("MultiEdit", {"file_path": "m.py",
            "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}]})
        self.assertEqual(kind, "diff"); self.assertIn("-a", txt); self.assertIn("+d", txt)
        # a tool with nothing visual → None
        self.assertIsNone(sb.tool_preview("Bash", {"command": "ls"}))
        self.assertIsNone(sb.tool_preview("ExitPlanMode", {"plan": ""}))

    def test_tool_preview_clips_huge_diffs(self):
        big = "\n".join("line %d" % i for i in range(1000))
        _, txt = sb.tool_preview("Write", {"file_path": "big.txt", "content": big})
        self.assertIn("more lines)", txt, "an enormous preview is capped, not sent whole")
        self.assertLess(len(txt.splitlines()), 1000)

    def test_pretty_model(self):
        self.assertEqual(sb.pretty_model("claude-opus-4-8"), "Opus 4.8")
        self.assertEqual(sb.pretty_model("claude-sonnet-4-6"), "Sonnet 4.6")
        self.assertEqual(sb.pretty_model("claude-haiku-4-5-20251001"), "Haiku 4.5")  # trailing date dropped
        self.assertEqual(sb.pretty_model("claude-fable-5"), "Fable 5")               # no minor version
        self.assertEqual(sb.pretty_model(""), "")
        self.assertEqual(sb.pretty_model("some-custom-id"), "some-custom-id")        # unrecognised → verbatim

    def test_model_label(self):
        # the live (init/assistant-echoed) name always wins once known
        self.assertEqual(sb.model_label("Opus 4.8", "opus"), "Opus 4.8")
        self.assertEqual(sb.model_label("Opus 4.8", ""), "Opus 4.8")
        # before the live name arrives, show a best-effort label from the CHOSEN model so the badge isn't
        # blank on a freshly-created SDK session (the user 2026-06-24)
        self.assertEqual(sb.model_label("", "opus"), "Opus")                 # CLI alias → capitalised
        self.assertEqual(sb.model_label("", "sonnet"), "Sonnet")
        self.assertEqual(sb.model_label("", "claude-opus-4-8"), "Opus 4.8")  # raw id → pretty_model
        # 'default'/unset → blank: the REAL default name fills in from the init message (eager-connect pokes it)
        self.assertEqual(sb.model_label("", "default"), "")
        self.assertEqual(sb.model_label("", ""), "")

    def test_identity_color_stable_and_in_palette(self):
        bg, fg = sb.pick_identity_color("11111111-2222-3333-4444-555555555555")
        self.assertIn(bg, sb._pal.PALETTES[sb._pal.DEFAULT]["bg"])   # no state_dir → the default set
        self.assertIn(fg, ("black", "white"))
        self.assertEqual(sb.pick_identity_color("11111111-2222-3333-4444-555555555555"), (bg, fg))  # stable per sid

    def test_identity_color_follows_the_active_palette(self):
        # the gear's Session-colors pick (STATE/palette) steers what NEW sessions are assigned
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "palette").write_text("phase")
            bg, fg = sb.pick_identity_color("11111111-2222-3333-4444-555555555555", td)
            p = sb._pal.PALETTES["phase"]
            self.assertIn(bg, p["bg"])
            self.assertEqual(fg, p["fg"][p["bg"].index(bg)])


# --- live tail (in-memory stream → atoms, ahead of disk). Pure: fakes match by type-name, no SDK. ---
class _TextBlock:
    def __init__(self, text): self.text = text
class _ToolUseBlock:
    def __init__(self, id, name, inp): self.id, self.name, self.input = id, name, inp
class _AssistantMessage:
    def __init__(self, content, model="claude-x", uuid="a1", stop_reason="end_turn"):
        self.content, self.model, self.uuid, self.stop_reason = content, model, uuid, stop_reason
class _ToolResultBlock:
    def __init__(self, tool_use_id, content="", is_error=False):
        self.tool_use_id, self.content, self.is_error = tool_use_id, content, is_error
class _UserMessage:
    def __init__(self, content, uuid="u1", tool_use_result=None):
        self.content, self.uuid, self.tool_use_result = content, uuid, tool_use_result
class _ResultMessage:
    uuid = "r1"
# rename so type(...).__name__ matches what msg_to_atom checks
_TextBlock.__name__ = "TextBlock"; _ToolUseBlock.__name__ = "ToolUseBlock"
_ToolResultBlock.__name__ = "ToolResultBlock"
_AssistantMessage.__name__ = "AssistantMessage"; _UserMessage.__name__ = "UserMessage"
_ResultMessage.__name__ = "ResultMessage"


class LiveTail(unittest.TestCase):
    def test_msg_to_atom_assistant(self):
        m = _AssistantMessage([_TextBlock("hi"), _ToolUseBlock("t1", "Bash", {"command": "ls"})])
        a = sb.msg_to_atom(m, "sid9", "fsidA", 100)
        self.assertEqual(a["type"], "assistant")
        self.assertEqual(a["uuid"], "a1")
        self.assertEqual(a["session_id"], "sid9")
        self.assertEqual(a["t"], 100)
        self.assertEqual(a["fsid"], "fsidA")
        self.assertEqual(a["message"]["content"],
                         [{"type": "text", "text": "hi"},
                          {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}])

    def test_msg_to_atom_user_and_nonrenderable(self):
        a = sb.msg_to_atom(_UserMessage([_TextBlock("hello")]), "s", "f", 5)
        self.assertEqual(a["type"], "user")
        self.assertEqual(a["message"]["content"], [{"type": "text", "text": "hello"}])
        self.assertIsNone(sb.msg_to_atom(_ResultMessage(), "s", "f", 5))   # result has no renderable content
        self.assertIsNone(sb.msg_to_atom(_AssistantMessage([]), "s", "f", 5))  # empty content → None

    def test_tool_result_atom_carries_the_structured_tooluseresult(self):
        # The SDK's UserMessage exposes the record-level structured result (the CLI streams the
        # transcript record's toolUseResult on the wire as `tool_use_result`; the SDK parser maps it
        # onto UserMessage.tool_use_result — verified against claude-agent-sdk 0.2.132 / CLI 2.1.224).
        # The live atom must carry it exactly like the file adapter's atom does, or a JUST-answered
        # AskUserQuestion renders via the lossy regex scrape until the disk record is parsed, and
        # Edit's diffRows lag a parse behind the stream.
        tur = {"questions": ["Pick a color"], "answers": {"Pick a color": "Blue"}}
        m = _UserMessage([_ToolResultBlock("t1", "answered")], tool_use_result=tur)
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertEqual(a["type"], "user")
        self.assertEqual(a["toolUseResult"], tur)
        self.assertEqual(a["message"]["content"][0]["type"], "tool_result")

    def test_string_tooluseresult_is_not_carried(self):
        # A dismissed picker records toolUseResult as a plain STRING — dict form only, the same gate
        # as the file adapter (no consumer reads the string form).
        m = _UserMessage([_ToolResultBlock("t1", "dismissed", is_error=True)],
                         tool_use_result="dismissed the questions")
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertNotIn("toolUseResult", a)

    def test_tooluseresult_without_a_tool_result_block_is_not_carried(self):
        # The file adapter's other gate: only an atom that carries a tool_result block may carry the
        # record-level dict — a text-only message never does.
        m = _UserMessage([_TextBlock("hello")], tool_use_result={"x": 1})
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertNotIn("toolUseResult", a)

    def test_an_unconsumed_shape_is_not_carried(self):
        # The consumed-keys gate: a Read result's dict embeds the WHOLE read file, and nothing
        # reads it off the atom — carrying every dict held ~a fifth of transcript bytes in the
        # parse cache by reference. Only the consumed shapes ride (answers, structuredPatch).
        m = _UserMessage([_ToolResultBlock("t9", "file contents…")],
                         tool_use_result={"type": "text", "file": {"content": "x" * 512}})
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertNotIn("toolUseResult", a)

    def test_the_consumed_key_set_cannot_drift_from_the_file_adapter_s(self):
        # sdk_backend loads standalone (no event-model import), so the set is MIRRORED — this pin
        # is what keeps the two halves widening together.
        em2 = SourceFileLoader("romp_event_model_drift", os.path.join(BIN, "romp-event-model")).load_module()
        self.assertEqual(sb.TUR_CONSUMED_KEYS, em2.TUR_CONSUMED_KEYS)

    def test_command_stdout_stream_becomes_a_turn_ENDING_assistant_atom(self):
        # the user 2026-07-02: client.set_model() makes the CLI stream its feedback as a UserMessage
        # wrapped in <local-command-stdout>. As a raw USER atom it opened a turn no reply would ever
        # close — the chat chip read "working" forever (fresh session + set model) while the timeline
        # (disk-only) showed nothing. The live atom must get the FILE adapter's classification: a
        # synthetic ASSISTANT command atom whose stop_reason end_turn CLOSES the turn.
        m = _UserMessage([_TextBlock("<local-command-stdout>Set model to sonnet (claude-sonnet-5)</local-command-stdout>")])
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertEqual(a["type"], "assistant", "command OUTPUT is a synthetic assistant atom, like the file adapter")
        self.assertTrue(a["command"])
        self.assertEqual(a["message"]["stop_reason"], "end_turn", "the turn CLOSES — no phantom working chip")
        self.assertEqual(a["message"]["content"], [{"type": "text", "text": "Set model to sonnet (claude-sonnet-5)"}])

    def test_command_name_stream_becomes_the_command_chip_user_atom(self):
        m = _UserMessage([_TextBlock("<command-name>/model</command-name>\n"
                                     "<command-message>model</command-message>\n"
                                     "<command-args>sonnet</command-args>")])
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertEqual(a["type"], "user")
        self.assertEqual(a["command"], "/model", "the invocation carries the command flag (the chat's chip)")
        self.assertEqual(a["message"]["content"], [{"type": "text", "text": "/model sonnet"}])

    def test_other_command_wrappers_are_noise(self):
        m = _UserMessage([_TextBlock("<local-command-caveat>whatever</local-command-caveat>")])
        self.assertIsNone(sb.msg_to_atom(m, "s", "f", 5), "non-invocation/output wrappers stay dropped, like the file adapter")

    def test_image_read_placeholder_is_dropped(self):
        # A Read of an image makes Claude Code stream a synthetic UserMessage carrying only this
        # placeholder alongside the image block. On disk it's isMeta (the file adapter skips it), but the
        # STREAM has no such flag, so as a raw user atom it rendered as a bare "you typed this" bubble
        # mid-conversation (the user 2026-07-23, who saw the box and asked what it was). The tool that fed
        # the image already shows in the rail, so the echo is dropped.
        m = _UserMessage([_TextBlock("[Image: original 2496x572, displayed at 2000x458. "
                                     "Multiply coordinates by 1.25 to map to original image.]")])
        self.assertIsNone(sb.msg_to_atom(m, "s", "f", 5), "the synthetic image-read echo is not a message")

    def test_image_paste_chip_is_not_dropped(self):
        # A composer paste chip is `[Image #N]` (no colon) and always rides WITH the human's typed text,
        # never alone — so it must NOT be swept up by the echo skip. A genuine human turn survives.
        m = _UserMessage([_TextBlock("look at [Image #1] and tell me what's wrong")])
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertIsNotNone(a)
        self.assertEqual(a["type"], "user")

    def test_live_store_and_prune(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        be._live["s"] = {"a1": {"uuid": "a1", "t": 2}, "echo:x": {"uuid": "echo:x", "t": 1, "_echo_text": "hi"}}
        self.assertEqual([a["uuid"] for a in be.live_atoms("s")], ["echo:x", "a1"])   # sorted by t
        be.prune_live("s", {"a1"}, {"hi"})     # a1 now on disk; echo text "hi" now on disk
        self.assertEqual(be.live_atoms("s"), [])

    def test_turn_settle_retires_unlanded_live_work_atoms(self):
        """A usage-limit retry storm streams work atoms whose attempt the API errors away — the CLI writes
        no transcript record for them, so the uuid-match prune can never retire them, and their live_work
        held the merged turn open FOREVER: the chat chip read WORKING with a 3h+ timer on a settled
        session (the user 2026-07-03). The turn's ResultMessage proves the stream is over — settle must
        drop unlanded WORK atoms, and ONLY them: an input echo keeps the dropped-send visibility, and a
        command atom has its own human-floor retirement."""
        import asyncio
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        be._live[s.sid] = {"w1": {"uuid": "w1", "t": 5},
                           "echo:hi": {"uuid": "echo:hi", "t": 1, "_echo_text": "hi"},
                           "c1": {"uuid": "c1", "t": 3, "command": True}}
        s.inflight = 1

        async def run():
            s._on_message(_ResultMessage(), _AssistantMessage, _ResultMessage, type("S", (), {}))
            await asyncio.sleep(0)
        asyncio.run(run())
        self.assertEqual(set(be._live[s.sid]), {"echo:hi", "c1"},
                         "settle drops unlanded WORK atoms; echoes + command feedback survive")

    def test_session_gone_retires_unlanded_live_work_atoms(self):
        """The process dying mid-turn (laptop sleep, a killed CLI) is the other stream-over event: no
        ResultMessage is ever coming, so _on_session_gone must retire the work atoms — else the phantom
        WORKING chip outlives the process indefinitely."""
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        be._live[s.sid] = {"w1": {"uuid": "w1", "t": 5}}
        be._on_session_gone(s)
        self.assertNotIn(s.sid, be._live, "no stream left → no work atom may hold the turn open")

    def test_forwards_sends_is_true_for_the_sdk(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        self.assertTrue(be.forwards_sends(),
                        "the SDK forwards its own sends (mid-turn + fold + interrupt-hold) — the kernel hands "
                        "composer sends straight over instead of parking them (the user 2026-07-17)")

    def test_queued_turns_survive_an_interrupt_and_release_when_the_turn_settles(self):
        """The user 2026-07-17: messages queued while working, then interrupt → interrupt AND THEN send them
        all. An interrupted turn's ResultMessage still settles inflight to 0, and must leave the queued turns
        intact and WAKE the input generator so they feed (inputs() holds the queue while inflight>0 AND
        _interrupted, releasing at settle)."""
        import asyncio
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        s.enqueue("first queued")
        s.enqueue("second queued")
        self.assertEqual(s.pending(), ["first queued", "second queued"], "held in _pending, not fed to the CLI yet")
        s.inflight = 1                                  # a turn is in flight...
        s._interrupted = True                          # ...and the user interrupted it
        s._input_wake = asyncio.Event()                # inputs() owns this; stub it here to observe the release

        async def run():
            s._on_message(_ResultMessage(), _AssistantMessage, _ResultMessage, type("S", (), {}))
            await asyncio.sleep(0)
        asyncio.run(run())
        self.assertEqual(s.pending(), ["first queued", "second queued"],
                         "the interrupt did NOT drop the queue — the messages are still there to send")
        self.assertEqual(s.inflight, 0, "the interrupted turn settled to idle")
        self.assertFalse(s._interrupted, "settle cleared the interrupt flag → inputs() stops blocking the queue")
        self.assertTrue(s._input_wake.is_set(),
                        "settle woke the input generator to feed the held turns as a fresh turn")

    def test_inputs_generator_holds_the_queue_while_interrupted(self):
        # The hold half of the guarantee above: the input generator must NOT feed the next turn into a CLI
        # whose current turn is interrupted/wedged (inflight>0 AND _interrupted); it releases only once the
        # ResultMessage settles inflight to 0. Source-pinned because inputs() is a nested closure.
        import inspect
        src = inspect.getsource(sb.SdkSession)
        self.assertIn("blocked = self.inflight > 0 and self._interrupted", src,
                      "inputs() holds queued turns while a turn is interrupted/wedged")

    def test_image_echo_pruned_by_human_floor_when_text_cant_match(self):
        # The screenshots-piling-up bug (the user 2026-06-25): an image send's echo text is the raw composer
        # text (an image path), but the transcript extracts the path into an image block, so the echoed path
        # is NOT in tx_user_texts and the text-prune can never retire it → every screenshot echo accumulates.
        # The FIFO floor retires it once the transcript's newest genuine-human turn is at/after its send time.
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        be._live["s"] = {"echo:img": {"uuid": "echo:img", "t": 100, "_echo_text": "/abs/shot.png"}}
        be.prune_live("s", set(), set())               # no text/uuid match, no floor → echo persists (the bug)
        self.assertEqual([a["uuid"] for a in be.live_atoms("s")], ["echo:img"])
        be.prune_live("s", set(), set(), human_floor=120)   # a later genuine-human turn landed → FIFO-retire it
        self.assertEqual(be.live_atoms("s"), [])
        # a not-yet-landed echo (send time AFTER the floor) must survive
        be._live["s"] = {"echo:new": {"uuid": "echo:new", "t": 200, "_echo_text": "/abs/new.png"}}
        be.prune_live("s", set(), set(), human_floor=120)
        self.assertEqual([a["uuid"] for a in be.live_atoms("s")], ["echo:new"])
        # the floor must NOT retire a real stream atom (no _echo_text) — those prune by uuid only
        be._live["s"]["a9"] = {"uuid": "a9", "t": 50}
        be.prune_live("s", set(), set(), human_floor=300)
        self.assertEqual([a["uuid"] for a in be.live_atoms("s")], ["a9"])

    def test_stale_command_atom_pruned_by_human_floor(self):
        # a COMMAND atom (the CLI's streamed /model feedback) from a TURN-LESS control request may never
        # get a transcript record to land against (the user 2026-07-02) — the floor retires it once a
        # genuine human turn postdates it, so the stale confirmation never rides inside later turns.
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        cmd = {"uuid": "c1", "t": 100, "command": True, "type": "assistant"}
        be._live["s"] = {"c1": dict(cmd)}
        be.prune_live("s", set(), set())                    # no floor yet → the confirmation stays visible
        self.assertEqual([a["uuid"] for a in be.live_atoms("s")], ["c1"])
        be.prune_live("s", set(), set(), human_floor=150)   # a later genuine human turn → retire it
        self.assertEqual(be.live_atoms("s"), [])
        be._live["s"] = {"c2": {"uuid": "c2", "t": 200, "command": True, "type": "assistant"}}
        be.prune_live("s", set(), set(), human_floor=150)   # newer than the floor → survives
        self.assertEqual([a["uuid"] for a in be.live_atoms("s")], ["c2"])

    def test_send_adds_optimistic_echo(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        be._ensure = lambda sid: type("S", (), {"enqueue": lambda self, t: None})()   # no real session thread
        self.assertTrue(be.send("s", "type this"))
        echoes = [a for a in be.live_atoms("s") if a.get("_echo_text") == "type this"]
        self.assertEqual(len(echoes), 1)
        self.assertEqual(echoes[0]["type"], "user")
        self.assertEqual(echoes[0]["author"], "human", "a typed message echoes as a human (blue) bubble")
        self.assertEqual(echoes[0]["message"]["content"], [{"type": "text", "text": "type this"}])

    def test_set_effort_synthesizes_the_command_atom(self):
        """The effort reconnect leaves NO transcript record — without a synthesized "/effort X" atom an
        idle-session effort change showed nothing in the chat while a busy (parked) one showed a queued
        chip: the same pick, acknowledged or not depending on timing (the user 2026-07-05). Mirrors the
        set_model synthesis exactly."""
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-555555555555"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        be.sessions[sid] = s
        self.assertTrue(be.set_effort(sid, "high"))
        cmds = [a for a in be.live_atoms(sid) if a.get("command") == "/effort"]
        self.assertEqual(len(cmds), 1, "one live '/effort high' invocation atom, like set_model's '/model X'")
        self.assertEqual(cmds[0]["_echo_text"], "/effort high")
        self.assertEqual(cmds[0]["message"]["content"], [{"type": "text", "text": "/effort high"}])
        self.assertEqual(cmds[0]["author"], "human")

    def test_set_effort_on_a_dormant_session_stays_quiet(self):
        # no live thread → the value applies on the next connect; nothing to echo into (no _live entry)
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-666666666666"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        self.assertTrue(be.set_effort(sid, "low"))
        self.assertEqual(be.live_atoms(sid), [])

    def _live_fast_session(self, d, sid, unlocked=False):
        """A constructed SdkSession whose thread READS alive (set_fast's gate) without spawning a CLI.
        `unlocked` simulates a connection made WITH the fastMode flag (the _connect_once snapshot)."""
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        s._fast_unlocked = unlocked
        be.sessions[sid] = s
        return be, s

    def test_set_fast_on_an_unlocked_connection_delivers_the_slash_command(self):
        """A connection made WITH the fastMode flag-settings opt-in interprets the literal '/fast on|off'
        (the CLI's descriptor is marked supportsNonInteractive; without the flag it refuses the command
        outright — verified against claude 2.1.224). So an unlocked session takes the live send in BOTH
        directions, no reconnect: the echo is the chat's acknowledgement, the flip is optimistic, and
        fast_mode_state on the next init re-asserts the truth. The reg mirrors every toggle — the
        persisted ask that drives the next connect's flag, so a lingering opt-in is impossible."""
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-777777777777"
        be, s = self._live_fast_session(d, sid, unlocked=True)
        self.assertTrue(s._fast_unlocked, "precondition: THIS connection carries the opt-in flag")
        reconnects = []
        s.request_reconnect = lambda: reconnects.append(1)
        self.assertTrue(be.set_fast(sid, "on"))
        self.assertEqual(s.pending(), ["/fast on"], "the literal command is queued for the CLI to interpret")
        echoes = [a for a in be.live_atoms(sid) if a.get("_echo_text") == "/fast on"]
        self.assertEqual(len(echoes), 1, "the send path's echo IS the chat acknowledgement")
        self.assertEqual(s.fast, "on", "optimistic flip — init re-asserts")
        self.assertEqual(s.snapshot()["fast"], "on", "the badge reads it from the snapshot")
        self.assertTrue(sb.read_reg(d, sid)["fast"], "the reg mirrors the toggle")
        self.assertTrue(be.set_fast(sid, "off"), "…and OFF is a live send too, not a reconnect")
        self.assertEqual(s.pending(), ["/fast on", "/fast off"])
        self.assertFalse(sb.read_reg(d, sid)["fast"], "the reg mirrors the off as well")
        self.assertEqual(reconnects, [], "an unlocked connection never reconnects for a toggle")

    def test_set_fast_first_opt_in_reconnects_to_apply_the_flag(self):
        # The current connection was made WITHOUT the flag, so the CLI would refuse the literal send;
        # the opt-in applies at the (re)connect that carries it — request_reconnect, the /effort
        # machinery: immediately if idle, at the end of the current turn if busy.
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-888888888888"
        be, s = self._live_fast_session(d, sid, unlocked=False)
        reconnects = []
        s.request_reconnect = lambda: reconnects.append(1)
        self.assertTrue(be.set_fast(sid, "on"))
        self.assertEqual(len(reconnects), 1, "the flag is connect-time here → reconnect to apply")
        self.assertEqual(s.pending(), [], "no literal send — this connection would refuse it")
        self.assertTrue(s.fast_opt, "the next _options carries the flag")
        self.assertEqual(s.fast, "on", "optimistic for the badge; init re-asserts")
        self.assertTrue(sb.read_reg(d, sid)["fast"])

    def test_set_fast_off_on_a_locked_connection_is_a_no_op_beyond_the_reg(self):
        # No flag at connect → fast mode is already off on this connection; there is nothing to send
        # and nothing to reconnect for. The reg still flips — it is the persisted ask.
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-999999999999"
        be, s = self._live_fast_session(d, sid, unlocked=False)
        sb.write_reg(d, sid, dict(sb.read_reg(d, sid), fast=True))   # a stale on-disk ask to clear
        reconnects = []
        s.request_reconnect = lambda: reconnects.append(1)
        self.assertTrue(be.set_fast(sid, "off"))
        self.assertEqual(s.pending(), [], "nothing to send — the connection never had fast mode")
        self.assertEqual(reconnects, [], "and nothing to reconnect for")
        self.assertFalse(sb.read_reg(d, sid)["fast"], "but the ask is cleared, so the next connect is plain")
        self.assertFalse(s.fast_opt)

    def test_set_fast_on_a_dormant_session_persists_and_applies_at_the_next_connect(self):
        # No live thread → nothing to send and nothing to reconnect; the reg carries the ask and the
        # NEXT connect applies it (fast_opt seeds from the reg, _options writes the flag file).
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-aaaaaaaaaaaa"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        self.assertTrue(be.set_fast(sid, "on"), "accepted: the pick applies at the next connect")
        self.assertTrue(sb.read_reg(d, sid)["fast"])
        self.assertEqual(be.live_atoms(sid), [], "nothing claimed in the chat — no live CLI took it")
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp", "fast": True})
        self.assertTrue(s.fast_opt, "a fresh construction picks the ask up from the reg")
        # the per-connection unlock is snapshotted from fast_opt exactly where _connect_once builds
        # the options that carry the flag, so the two can never disagree
        import inspect
        self.assertIn("self._fast_unlocked = self.fast_opt", inspect.getsource(sb.SdkSession._amain))

    def test_set_fast_refuses_bad_values_and_unknown_sids(self):
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-bbbbbbbbbbbb"
        be, s = self._live_fast_session(d, sid, unlocked=True)
        self.assertFalse(be.set_fast(sid, "maybe"))
        self.assertEqual(s.pending(), [], "a bad value never reaches the CLI")
        be2 = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        self.assertFalse(be2.set_fast("no-such-sid", "on"))

    def test_fast_mode_is_never_remembered_as_the_seed_for_new_sessions(self):
        # Fast mode draws credits at a higher rate and has its own rate limit, so it stays per-session —
        # the same call ultracode makes, and the reason romp never spreads it to every new session.
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = be.spawn("a", d)
        self.assertTrue(be.set_fast(sid, "on"))
        self.assertNotIn("fast", sb.read_sdk_defaults(d), "never becomes the default for the next session")
        self.assertFalse(sb.read_reg(d, be.spawn("b", d)).get("fast"), "a NEW session starts plain")
        self.assertTrue(sb.read_reg(d, sid)["fast"], "but THIS session keeps it")

    def test_init_reports_fast_mode_state_into_the_snapshot(self):
        """fast_mode_state rides the CLI's init message ("on"/"off"/"cooldown", plus a disabled_reason
        when the org/model can't use it) — the AUTHORITATIVE source behind the chat's fast badge. An
        init WITHOUT the field (older CLI) must leave the state unknown, never fabricate "off"."""
        import asyncio
        class _Sys:
            def __init__(self, data): self.subtype = "init"; self.data = data
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-aaaaaaaaaaaa"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        async def _noop(): pass
        s._do_refresh_context = _noop
        self.assertEqual(s.snapshot()["fast"], "", "unknown until an init lands → no badge")

        async def run(data):
            s._on_message(_Sys(data), _AssistantMessage, _ResultMessage, _Sys)
            await asyncio.sleep(0)                       # let the ensure_future'd refresh run
        asyncio.run(run({"fast_mode_state": "on"}))
        self.assertEqual(s.fast, "on")
        self.assertEqual(s.snapshot()["fast"], "on")
        self.assertEqual(s.snapshot()["fastReason"], "")
        asyncio.run(run({"fast_mode_state": "off", "fast_mode_disabled_reason": "Fast mode is org-disabled"}))
        self.assertEqual(s.fast, "off")
        self.assertEqual(s.snapshot()["fastReason"], "Fast mode is org-disabled",
                         "a disabled_reason rides along so the kernel can hide the toggle")
        asyncio.run(run({}))                             # an init with no field → the last truth STANDS
        self.assertEqual(s.fast, "off", "absent field never overwrites the known state")

    def test_the_opt_in_required_reason_never_hides_the_toggle(self):
        """The CLI stamps 'sdk_opt_in_required' on EVERY connect made without the fastMode
        flag-settings opt-in (verified live 2026-08-10 on 2.1.226 — opus/fable/sonnet headless
        connects alike). Treating it like a real refusal hid the chat toggle on every SDK session:
        the control that GRANTS the opt-in was gated on already having it (the user 2026-08-10,
        who switched to Opus and found no toggle anywhere). It reads as 'available, currently off';
        every other reason still hides the dead control."""
        import asyncio
        class _Sys:
            def __init__(self, data): self.subtype = "init"; self.data = data
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-cccccccccccc"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        async def _noop(): pass
        s._do_refresh_context = _noop

        async def run(data):
            s._on_message(_Sys(data), _AssistantMessage, _ResultMessage, _Sys)
            await asyncio.sleep(0)
        asyncio.run(run({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}))
        self.assertEqual(s.fast, "off")
        self.assertEqual(s.snapshot()["fastReason"], "", "the curable opt-in reason shows the badge")
        asyncio.run(run({"fast_mode_state": "off", "fast_mode_disabled_reason": "Fast mode is org-disabled"}))
        self.assertEqual(s.snapshot()["fastReason"], "Fast mode is org-disabled",
                         "a real refusal still hides the toggle")

    def test_a_refusal_answering_the_users_ask_warns_clears_it_and_restores_the_badge(self):
        """A disabled_reason that lands while the user's ask is ARMED (fast_opt — they picked On) is
        the CLI ANSWERING that ask, not standing state to hide behind. Adopting it silently was the
        vanishing-button bug (the user 2026-08-11, whose tap on a phone-width statusline was refused
        with extra_usage_disabled): the reason hid the very toggle they had just clicked, no word
        why, and the reg's ask stayed armed forever. The refusal is LOUD now — one warn toast naming
        the humanized reason — the ask clears (fast_opt + reg, so the opt-in flag doesn't linger),
        and a flagless reconnect is requested: its connect-time re-probe reports the blanked
        sdk_opt_in_required, so the badge comes BACK instead of staying vanished."""
        import asyncio
        class _Sys:
            def __init__(self, data): self.subtype = "init"; self.data = data
        d = tempfile.mkdtemp()
        notes = []
        be = sb.SdkBackend(d, "/bin/true", lambda app, msg: notes.append((app, msg)))
        sid = "11111111-2222-3333-4444-eeeeeeeeeeee"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        be.sessions[sid] = s
        async def _noop(): pass
        s._do_refresh_context = _noop
        reconnects = []
        s.request_reconnect = lambda: reconnects.append(1)
        async def run(data):
            s._on_message(_Sys(data), _AssistantMessage, _ResultMessage, _Sys)
            await asyncio.sleep(0)

        def warns():
            return [m for _, m in notes if isinstance(m, dict) and m.get("type") == "warn"]

        self.assertTrue(be.set_fast(sid, "on"))          # arms the ask (locked connection → reconnect)
        self.assertEqual(len(reconnects), 1)
        asyncio.run(run({"fast_mode_state": "off",
                         "fast_mode_disabled_reason": "extra_usage_disabled"}))
        self.assertEqual(len(warns()), 1, "ONE toast says why — a refusal is never a silent vanish")
        self.assertIn("extra usage", warns()[0]["text"], "the reason is humanized, not the raw token")
        self.assertFalse(s.fast_opt, "the ask is answered — cleared, not left armed forever")
        self.assertFalse(sb.read_reg(d, sid)["fast"], "…and cleared on disk, so the next connect is plain")
        self.assertEqual(len(reconnects), 2, "a flagless reconnect re-probes so the badge comes back")
        # the same refusal WITHOUT an armed ask stays the quiet dead-control hide it always was
        asyncio.run(run({"fast_mode_state": "off",
                         "fast_mode_disabled_reason": "extra_usage_disabled"}))
        self.assertEqual(len(warns()), 1, "no fresh ask → no re-warn")
        self.assertEqual(len(reconnects), 2, "…and no reconnect churn")
        self.assertEqual(s.snapshot()["fastReason"], "extra_usage_disabled",
                         "the reason still hides the dead control when nobody asked")

    def test_the_toggle_turns_own_init_yields_to_the_sent_word_once(self):
        """A literal '/fast on' send opens a turn whose init still reports the state at turn START —
        one word stale, since the toggle applies after it. Taking it verbatim stomps set_fast's
        optimistic flip until the NEXT turn's init, so the badge reads off for a whole turn right
        after the CLI acknowledged the toggle. That single stale, reason-less word yields to the
        send's one-shot expectation; the next init wins unconditionally, and a disabled_reason
        (real refusal evidence) always wins immediately."""
        import asyncio
        class _Sys:
            def __init__(self, data): self.subtype = "init"; self.data = data
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-bbbbbbbbbbbb"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        s._fast_unlocked = True
        be.sessions[sid] = s
        async def _noop(): pass
        s._do_refresh_context = _noop
        async def run(data):
            s._on_message(_Sys(data), _AssistantMessage, _ResultMessage, _Sys)
            await asyncio.sleep(0)

        self.assertTrue(be.set_fast(sid, "on"))
        self.assertEqual(s.fast, "on", "optimistic flip on the live send")
        self.assertEqual(s._fast_expect, "on", "…armed for the send's own turn-init")
        asyncio.run(run({"fast_mode_state": "off"}))     # the toggle turn's init: one word stale
        self.assertEqual(s.fast, "on", "the same-turn stale word yields")
        asyncio.run(run({"fast_mode_state": "off"}))     # any LATER init is authoritative
        self.assertEqual(s.fast, "off", "one-shot — the next init wins")

        self.assertTrue(be.set_fast(sid, "on"))          # armed again…
        asyncio.run(run({"fast_mode_state": "off",
                         "fast_mode_disabled_reason": "Fast mode is org-disabled"}))
        self.assertEqual(s.fast, "off", "…but a disabled_reason beats the expectation immediately")
        self.assertEqual(s.snapshot()["fastReason"], "Fast mode is org-disabled")

    def test_fast_state_survives_a_kernel_restart_via_the_reg(self):
        """The init message only streams WITH a turn, so in-memory-only fast state meant every kernel
        restart blanked every session's badge until it next spoke (the user 2026-08-10, who found the
        toggle nowhere). The state persists to the reg on adoption (liveFast/liveFastReason, the
        liveModel pattern) and seeds the next construction — and a DORMANT session (no thread at all
        after a restart) reports it straight from the reg in live_sessions."""
        import asyncio
        class _Sys:
            def __init__(self, data): self.subtype = "init"; self.data = data
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-dddddddddddd"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        async def _noop(): pass
        s._do_refresh_context = _noop
        async def run(data):
            s._on_message(_Sys(data), _AssistantMessage, _ResultMessage, _Sys)
            await asyncio.sleep(0)
        asyncio.run(run({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}))
        reg = sb.read_reg(d, sid)
        self.assertEqual(reg["liveFast"], "off", "adoption persists the state")
        self.assertEqual(reg["liveFastReason"], "", "…with the curable reason already blanked")
        # "the kernel restarts": a fresh construction from the same reg — the badge is there pre-turn
        s2 = sb.SdkSession(be, sb.read_reg(d, sid))
        self.assertEqual(s2.fast, "off", "seeded from the reg, no init needed")
        self.assertEqual(s2.fast_reason, "")
        # …and a session with NO thread at all reports it straight from the reg
        live = be.live_sessions()
        self.assertEqual(live[sid]["fast"], "off", "a dormant session's badge survives the restart")
        self.assertEqual(live[sid]["fastReason"], "")

    def test_connect_adopts_fast_state_from_the_initialize_response(self):
        """A turn-less connect emits NO init message, so a session that merely reconnected (every
        session, after a kernel restart) knew nothing until it next spoke — and a /model switch never
        produced a badge at all. The initialize response the SDK stores at connect (get_server_info)
        carries the same fast_mode_state/fast_mode_disabled_reason fields (verified live 2026-08-10 on
        2.1.226), and _connect_once adopts it right beside the pre-turn context/model refresh."""
        import asyncio
        import inspect
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-eeeeeeeeeeee"
        sb.write_reg(d, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True})
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        self.assertEqual(s.fast, "", "unknown before the connect")
        class _Client:
            async def get_server_info(self):
                return {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}
        s.client = _Client()
        asyncio.run(s._do_adopt_server_info())
        self.assertEqual(s.fast, "off", "the badge exists pre-turn")
        self.assertEqual(s.fast_reason, "", "the curable opt-in reason never hides the toggle")
        self.assertEqual(sb.read_reg(d, sid)["liveFast"], "off", "…and it persisted")
        # the adoption is wired into the connect path, beside the pre-turn context refresh
        self.assertIn("_do_adopt_server_info", inspect.getsource(sb.SdkSession._amain))

    def test_send_echo_authors_a_romp_nudge_as_romp_not_human(self):
        # the bug (the user 2026-06-28): an auto-nudge sent through send() echoed as a BLUE HUMAN "Follow-up"
        # instead of the GRAY "from romp" auto-nudge it is, because the echo hardcoded author="human". The echo
        # must author from the same markers the event model uses on the real atom.
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        be._ensure = lambda sid: type("S", (), {"enqueue": lambda self, t: None})()
        body = "Status on the goal above?\n<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: s:g1 -->"
        be.send("s", body)
        e = next(a for a in be.live_atoms("s") if a.get("_echo_text") == body)
        self.assertEqual(e["author"], "romp", "a romp-injected nudge echoes as romp, not human")
        self.assertTrue(e.get("rompAuto"), "the romp-auto marker → the romp-logo (auto-nudge) flag")
        # a NUDGE-BUTTON injection (romp-injected, NOT romp-auto) is romp but not auto
        be.send("s", "do X\n<!-- romp-injected --><!-- romp-goal-id: s:g1 -->")
        e2 = next(a for a in be.live_atoms("s") if a.get("_echo_text", "").startswith("do X"))
        self.assertEqual(e2["author"], "romp")
        self.assertFalse(e2.get("rompAuto"), "a Nudge-button injection is romp but not auto (no romp-logo)")

    def test_context_pct_from_sdk_get_context_usage(self):
        """The context bar reads the SDK's OWN number, not a window guess (the user 2026-06-24: SDK read 14%
        where tmux read 3% on a 1M-context model — a wrong-window inference). _do_refresh_context calls
        get_context_usage() — the control request behind the CLI's /context, which already divides by the real
        window and accounts for the autocompact buffer — and stores its `percentage` (rounded) + live `model`,
        persisting both so they survive idle/restart. None until the first refresh lands."""
        import asyncio
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-555555555555"
        s = sb.SdkSession(be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        self.assertIsNone(s._ctx_pct(), "no refresh yet → no context bar")

        class _Client:
            def __init__(self, payload): self._p = payload
            async def get_context_usage(self): return self._p
        s.client = _Client({"percentage": 2.7, "model": "claude-opus-4-8"})
        asyncio.run(s._do_refresh_context())
        self.assertEqual(s._ctx_pct(), 3, "stores the SDK's rounded percentage — no window inference")
        self.assertEqual(s.model, "Opus 4.8", "adopts the SDK-reported model id")
        self.assertEqual(sb.read_reg(d, sid).get("liveCtx"), 3, "persists ctx so the bar survives idle/restart")
        self.assertEqual(sb.read_reg(d, sid).get("liveModel"), "Opus 4.8", "persists the live model too")

        s.client = _Client({"percentage": 88, "model": "claude-opus-4-8"})
        asyncio.run(s._do_refresh_context())
        self.assertEqual(s._ctx_pct(), 88, "tracks the live value across turns")
        self.assertFalse(s._ctx_over, "88% is inside the window — no overflow flag")

        # the CLI documents percentage as "0-100+": past 100 the tokens exceed the CURRENT model's
        # window (a 1M→200k model switch does this instantly). The battery stays clamped at 100,
        # but the overflow is surfaced (ctxOver) instead of clamped into a silent, wrong-looking
        # 100% (the user 2026-09-02, who switched models and read the full battery as a bug).
        s.client = _Client({"percentage": 147, "model": "claude-haiku-4-5"})
        asyncio.run(s._do_refresh_context())
        self.assertEqual(s._ctx_pct(), 100, "the gauge value itself stays clamped")
        self.assertTrue(s._ctx_over, "…but the overflow is news, not noise")
        self.assertTrue(s.snapshot()["ctxOver"], "the snapshot ships it to the statusline")
        self.assertTrue(sb.read_reg(d, sid).get("liveCtxOver"),
                        "persisted beside liveCtx so a dormant/restarted session keeps saying so")
        s.client = _Client({"percentage": 61, "model": "claude-haiku-4-5"})
        asyncio.run(s._do_refresh_context())
        self.assertFalse(s._ctx_over, "dropping back inside the window clears the flag")
        self.assertFalse(sb.read_reg(d, sid).get("liveCtxOver"))

    def test_context_refresh_queued_when_one_is_in_flight(self):
        """A model/effort switch can ask for a context refresh while the turn-end refresh is still in
        flight. The in-flight guard used to DROP that call silently, leaving the old model's percentage
        standing until the next turn (the user 2026-09-02) — now it queues exactly one rerun, so the
        number reflects the newest world."""
        import asyncio
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})

        class _Counting:
            def __init__(self): self.calls = 0
            async def get_context_usage(self): self.calls += 1; return {"percentage": 40}
        s.client = _Counting()
        s._ctx_refreshing = True                       # a refresh is mid-flight
        asyncio.run(s._do_refresh_context())
        self.assertEqual(s.client.calls, 0, "the guarded call never races the in-flight one")
        self.assertTrue(s._ctx_refresh_again, "…but it is REMEMBERED, not dropped")
        s._ctx_refreshing = False
        asyncio.run(s._do_refresh_context())           # the in-flight one finishing runs the queued ask
        self.assertEqual(s.client.calls, 2, "the queued rerun fires after the live refresh lands")
        self.assertFalse(s._ctx_refresh_again, "the queue holds ONE rerun, not a storm")

    def test_assistant_model_sets_badge_but_synthetic_does_not_corrupt_it(self):
        """The model 'doesn't show' mid-conversation (the user 2026-06-24): injected/synthetic assistant turns
        carry model='<synthetic>', which an unguarded assign wrote straight onto the statusline + timeline
        badge (both read self.model via the snapshot). A real model id sets the badge; '<synthetic>' must be
        ignored so the last real model sticks."""
        class _Sys:                                              # a stand-in for SystemMessage (isinstance arg only)
            pass
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        s._on_message(_AssistantMessage([_TextBlock("hi")], model="claude-opus-4-8"),
                      _AssistantMessage, _ResultMessage, _Sys)
        self.assertEqual(s.model, "Opus 4.8", "a real model id sets the badge")
        s._on_message(_AssistantMessage([_TextBlock("x")], model="<synthetic>"),
                      _AssistantMessage, _ResultMessage, _Sys)
        self.assertEqual(s.model, "Opus 4.8", "a synthetic turn must NOT overwrite the real model")

    def test_compact_boundary_refreshes_context_now(self):
        """After /compact the active context drops to the summary, but the % used to refresh only on the next
        turn's ResultMessage. The CLI auto-runs a continuation turn after /compact that can work for minutes,
        so until it settled the bar kept showing the STALE pre-compact % (the user 2026-06-30, who compacted
        but it still said 72%). A compact_boundary system message must re-pull the % on the boundary itself."""
        import asyncio
        class _Sys:                                              # stand-in for SystemMessage (isinstance + subtype)
            def __init__(self, subtype): self.subtype = subtype; self.data = {}
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        calls = []
        async def _fake_refresh(): calls.append(True)
        s._do_refresh_context = _fake_refresh

        async def run():
            s._on_message(_Sys("compact_boundary"), _AssistantMessage, _ResultMessage, _Sys)
            await asyncio.sleep(0)                               # let the ensure_future'd refresh run
        asyncio.run(run())
        self.assertEqual(len(calls), 1, "compact_boundary re-pulls the context % immediately, not next turn")

    def test_interrupt_flips_to_waiting_before_the_blocking_control_request(self):
        """client.interrupt() BLOCKS until the CLI acknowledges the interrupt, which can take seconds mid-
        stream (the CLI won't ack until the model call hits a boundary). If _interrupted — the flag that makes
        the snapshot read 'waiting' — were set only AFTER that await, a stopped turn keeps reading 'working'
        the whole time (the user 2026-06-30, who interrupted but it said working for a while). The flag must
        flip up front, so the lane reflects the stop the instant the user hits it."""
        import asyncio
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        s.inflight = 1
        seen = {}
        class _Client:
            async def interrupt(self):
                seen["interrupted_at_send"] = s._interrupted   # what the UI already reads when the request goes out
        s.client = _Client()
        asyncio.run(s._do_interrupt())
        self.assertTrue(seen.get("interrupted_at_send"),
                        "the snapshot already read 'waiting' BEFORE the blocking control request")
        self.assertEqual(s.snapshot()["state"], "waiting", "an interrupted in-flight turn reads 'waiting', not 'working'")

    def test_snapshot_exposes_the_in_flight_interrupt_flag(self):
        """The kernel's chip + feed 'interrupting' badge keys on this snapshot field (== _interrupted), NOT the
        transcript tail — the tail retires at the ResultMessage BEFORE the stop record lands, so keying on it
        flickered 'Interrupting…' → 'Working' (the user 2026-07-07). The flag spans the whole in-flight window:
        True from dispatch, False once the aborted turn's ResultMessage settles it."""
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        self.assertFalse(s.snapshot()["interrupting"], "idle → not interrupting")
        s._interrupted = True
        self.assertTrue(s.snapshot()["interrupting"], "an in-flight interrupt surfaces on the snapshot")
        s._interrupted = False
        self.assertFalse(s.snapshot()["interrupting"], "cleared once the ResultMessage settles it")

    def test_interrupt_sets_the_flag_synchronously_at_dispatch(self):
        """interrupt() schedules the async _do_interrupt, but the kernel stamps _interrupt_clicked and pushes
        the instant it returns — so _interrupted must already be True SYNCHRONOUSLY, not one event-loop tick
        later (else the first snapshot after the click misses it and the badge flickers, the user 2026-07-07)."""
        import asyncio
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        loop = asyncio.new_event_loop()
        try:
            s.loop = loop
            s.client = object()   # truthy — interrupt() only guards on `self.loop and self.client`
            self.assertFalse(s._interrupted)
            s.interrupt()         # loop never runs → the scheduled _do_interrupt does NOT execute
            self.assertTrue(s._interrupted, "the flag flips synchronously, before the scheduled _do_interrupt")
            self.assertTrue(s.snapshot()["interrupting"], "so the very next snapshot already reads 'interrupting'")
        finally:
            loop.close()


class LiveSubagents(unittest.TestCase):
    """SubagentStart/SubagentStop hooks track the Task subagents running RIGHT NOW — the transparency the tmux
    backend never had (the user 2026-06-30). The live set keeps the session 'working' while any run (covers a
    BACKGROUNDED subagent that outlives the main turn) and surfaces a count on the lane."""

    def _sess(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        return sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})

    def test_start_and_stop_track_the_live_set(self):
        import asyncio
        s = self._sess()
        async def run():
            await s._subagent_start_hook({"agent_id": "a1", "agent_type": "code-reviewer"}, None, None)
            await s._subagent_start_hook({"agent_id": "a2", "agent_type": "general-purpose"}, None, None)
            self.assertEqual({d["type"] for d in s._live_subagents()}, {"code-reviewer", "general-purpose"})
            await s._subagent_stop_hook({"agent_id": "a1"}, None, None)
            self.assertEqual([d["type"] for d in s._live_subagents()], ["general-purpose"])
            await s._subagent_stop_hook({"agent_id": "a2"}, None, None)
            self.assertEqual(s._live_subagents(), [], "the set empties when the last subagent stops")
        asyncio.run(run())

    def test_snapshot_exposes_the_live_subagents(self):
        import asyncio
        s = self._sess()
        asyncio.run(s._subagent_start_hook({"agent_id": "a1", "agent_type": "code-reviewer"}, None, None))
        snap = s.snapshot()
        self.assertEqual([d["type"] for d in snap["subagents"]], ["code-reviewer"],
                         "the snapshot carries the live subagents for the lane")

    def test_backgrounded_subagent_keeps_the_session_working(self):
        """The main turn settled (inflight 0) but a backgrounded Task subagent is still running: the session is
        still working, not idle. Clears itself when the subagent stops."""
        import asyncio
        s = self._sess()
        s.inflight = 0                                          # main turn already ended
        self.assertEqual(s.snapshot()["state"], "waiting", "no subagents → idles to waiting")
        asyncio.run(s._subagent_start_hook({"agent_id": "a1", "agent_type": "explorer"}, None, None))
        self.assertEqual(s.snapshot()["state"], "working", "a live backgrounded subagent reads 'working'")
        asyncio.run(s._subagent_stop_hook({"agent_id": "a1"}, None, None))
        self.assertEqual(s.snapshot()["state"], "waiting", "back to idle once it stops")

    def test_a_parked_permission_still_wins_over_subagents(self):
        """A live subagent must NOT mask a permission/picker prompt that needs the user — parked wins."""
        import asyncio
        s = self._sess()
        s.inflight = 1
        s.backend._pending_ask[s.sid] = {"kind": "single"}     # parked on a prompt
        asyncio.run(s._subagent_start_hook({"agent_id": "a1", "agent_type": "x"}, None, None))
        sb.append_state(s.backend.state_dir, s.sid, "permission")
        self.assertEqual(s.snapshot()["state"], "permission", "needs-you still surfaces over a running subagent")


class SnapshotParkedOnAsk(unittest.TestCase):
    """A RUNNING SDK session parked in can_use_tool/_ask_user on a permission/picker prompt must snapshot
    as that needs-input state, NOT 'working'. The turn stays inflight through the wait, so the old snapshot
    reported 'working' and the kernel never floored the card to blocked (the user 2026-06-27: an SDK
    AskUserQuestion didn't register as blocked the way tmux's does)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        self.sid = "11111111-2222-3333-4444-555555555555"
        self.s = sb.SdkSession(self.be, {"sid": self.sid, "name": "n", "cwd": "/tmp"})

    def test_inflight_and_not_parked_is_working(self):
        self.s.inflight = 1
        self.assertEqual(self.s.snapshot()["state"], "working", "actively producing → working")

    def test_parked_on_a_picker_reports_picker(self):
        self.s.inflight = 1                                    # turn still in flight (mid-tool)
        sb.append_state(self.d, self.sid, "picker")            # _ask_user logs it before raising the picker
        self.be._pending_ask[self.sid] = {"kind": "single"}   # the ask is up, awaiting the user
        self.assertEqual(self.s.snapshot()["state"], "picker",
                         "parked on an AskUserQuestion picker → needs-input, not working")

    def test_parked_on_a_permission_reports_permission(self):
        self.s.inflight = 1
        sb.append_state(self.d, self.sid, "permission")
        self.be._pending_ask[self.sid] = {"permission": True}
        self.assertEqual(self.s.snapshot()["state"], "permission",
                         "parked on a tool-permission Allow/Deny → needs-input")

    def test_back_to_working_after_the_ask_clears(self):
        self.s.inflight = 1
        sb.append_state(self.d, self.sid, "picker")
        self.be._pending_ask[self.sid] = {"kind": "single"}
        self.be._pending_ask.pop(self.sid, None)              # user answered → _clear_ask
        sb.append_state(self.d, self.sid, "working")          # _ask_user's finally re-logs working
        self.assertEqual(self.s.snapshot()["state"], "working",
                         "ask resolved, turn resumes → working again")


class StateAndRegistryFiles(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_state_log_roundtrip(self):
        sb.append_state(self.d, "sid1", "working", t=100)
        sb.append_state(self.d, "sid1", "waiting", t=200)
        self.assertEqual(sb.last_state(self.d, "sid1"), {"t": 200, "state": "waiting"})
        # matches the kernel's format: one JSON object per line
        p = os.path.join(self.d, "states", "sid1.jsonl")
        with open(p) as f:
            self.assertEqual(len(f.read().strip().splitlines()), 2)

    def test_names_file_format(self):
        sb.write_name(self.d, "sid2", "alpha", "/work/dir", "#fff", "black")
        with open(os.path.join(self.d, "names", "sid2")) as f:
            line = f.read().rstrip("\n")
        self.assertEqual(line.split("\t"), ["alpha", "/work/dir", "#fff", "black"])

    def test_registry_roundtrip(self):
        sb.write_reg(self.d, "sid3", {"sid": "sid3", "name": "n", "alive": True})
        self.assertEqual(sb.read_reg(self.d, "sid3")["name"], "n")
        sb.write_reg(self.d, "sid4", {"sid": "sid4", "alive": False})
        regs = {r["sid"]: r for r in sb.list_regs(self.d)}
        self.assertEqual(set(regs), {"sid3", "sid4"})
        self.assertTrue(regs["sid3"]["alive"])

    def test_spawn_assigns_identity_color(self):
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sid = be.spawn("c", self.d)
        with open(os.path.join(self.d, "names", sid)) as f:
            parts = f.read().rstrip("\n").split("\t")
        self.assertTrue(parts[2].startswith("#"), "SDK session gets an identity colour like tmux ones")

    def test_dormant_session_reports_waiting_not_stale_inflight(self):
        """False blocked/approval state (the user 2026-06-24): after a kernel restart an alive SDK session's
        thread is gone, but its state log still reads its last in-flight state. A NOT-running session can't be
        mid-turn, so live_sessions must report 'waiting' — else the UI shows it blocked/needs-approval with no
        prompt to resolve (the prompt died with the thread). A running session is unaffected (snapshot path)."""
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sid = be.spawn("reorder_like", self.d)            # reg(alive) + a 'waiting' state; NOT started (no thread)
        for stale in ("working", "permission", "picker", "compacting", "retrying"):
            sb.append_state(self.d, sid, stale)           # ...it went mid-turn, then the kernel restarted
            ls = be.live_sessions()                        # registry-only path (session not running here)
            self.assertEqual(ls[sid]["state"], "waiting",
                             "a dormant session must read 'waiting', not the stale '%s'" % stale)

    def test_dormant_session_shows_persisted_live_model(self):
        """A default-model SDK session has no chosen 'model' in its reg, so a dormant/post-restart read showed
        a BLANK model badge — and the timeline hides effort too when model is empty (the user 2026-06-24).
        _learn_model persists the observed model as reg['liveModel']; live_sessions' registry path uses it so
        the badge survives dormancy."""
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sid = be.spawn("def_model", self.d)               # reg has effort/mode but NO chosen 'model'
        self.assertEqual(be.live_sessions()[sid]["model"], "", "no model known yet → blank")
        sb.write_reg(self.d, sid, {**sb.read_reg(self.d, sid), "liveModel": "Opus 4.8"})   # as _learn_model persists
        self.assertEqual(be.live_sessions()[sid]["model"], "Opus 4.8",
                         "a dormant session shows the persisted live model, not a blank badge")

    def test_dormant_session_shows_persisted_context(self):
        """Context fill must SURVIVE idle/restart, like the model (the user 2026-06-24: no context bar). The
        backend persists each turn's % as reg['liveCtx']; the registry path surfaces it so a dormant session
        still shows its last context, instead of a blank bar."""
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sid = be.spawn("ctxsess", self.d)
        self.assertEqual(be.live_sessions()[sid]["ctx"], "", "no context fill yet → blank")
        sb.write_reg(self.d, sid, {**sb.read_reg(self.d, sid), "liveCtx": 42})   # as a turn persists
        self.assertEqual(be.live_sessions()[sid]["ctx"], 42, "dormant shows the persisted context fill")

    def test_kernel_restart_heals_stale_awaiting(self):
        """A background task's completion is lost on kernel restart: the awaiting:true overlay never gets its
        awaiting:false, because the Stop hook that writes it died with the thread — so the session reads
        working/awaiting forever and climbs a ghost work-timer (reorder_bug 2026-06-24, verified). On
        (re)construction the backend heals every alive, not-running session: a stale awaiting:true →
        awaiting:false. Idempotent — no write when already cleared/absent."""
        sb.write_reg(self.d, "sid_aw", {"sid": "sid_aw", "name": "n", "cwd": "/tmp", "alive": True})
        sb.append_awaiting(self.d, "sid_aw", True, "1 background task(s) running")
        self.assertIs(sb.last_awaiting(self.d, "sid_aw"), True)
        sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)        # kernel restart re-constructs the backend
        self.assertIs(sb.last_awaiting(self.d, "sid_aw"), False,
                      "a dormant session's stale awaiting is cleared on kernel start")
        path = os.path.join(self.d, "states", "sid_aw.jsonl")
        before = len(open(path).read().splitlines())
        sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)        # already false → no redundant write
        self.assertEqual(len(open(path).read().splitlines()), before, "idempotent: no spam when already cleared")

    def test_heal_does_not_touch_a_session_with_no_awaiting(self):
        """The heal writes nothing for a session that never set an awaiting overlay (last_awaiting → None)."""
        sb.write_reg(self.d, "sid_plain", {"sid": "sid_plain", "name": "n", "cwd": "/tmp", "alive": True})
        sb.append_state(self.d, "sid_plain", "waiting")
        before = len(open(os.path.join(self.d, "states", "sid_plain.jsonl")).read().splitlines())
        sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        after = len(open(os.path.join(self.d, "states", "sid_plain.jsonl")).read().splitlines())
        self.assertEqual(before, after, "no awaiting overlay → nothing to heal")
        self.assertIsNone(sb.last_awaiting(self.d, "sid_plain"))

    def test_restart_does_NOT_heal_a_stale_working_state(self):
        """A turn in flight when the kernel restarts is killed mid-stream (e.g. the user hit Refresh mid-turn),
        so no ResultMessage ever writes "waiting" — the state log is left at "working". The backend must LEAVE
        that "working" in place: the auto-nudge GENUINE-STOP gate (_last_state_value in _PROGRESSING_STATES)
        then correctly SKIPS the session — it was interrupted, not stopped, and must not be nudged (the user
        2026-06-29: Refresh was nudging in-progress sessions). We used to heal "working"→"waiting" here, which
        opened that gate and caused the spurious nudge; that heal is gone. The dormant in-flight→waiting heal
        is DISPLAY-only and lives in live_sessions, not in the state LOG."""
        sb.write_reg(self.d, "sid_w", {"sid": "sid_w", "name": "n", "cwd": "/tmp", "alive": True})
        sb.append_state(self.d, "sid_w", "working")                     # a turn that never got its ResultMessage
        sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)        # kernel restart re-constructs the backend
        self.assertEqual(sb.last_state(self.d, "sid_w").get("state"), "working",
                         "a refresh-interrupted session keeps 'working' so the auto-nudge stop-gate skips it")

    def test_restart_still_heals_a_stale_awaiting_overlay(self):
        """The awaiting OVERLAY heal stays (a dormant session can't have live background tasks), even though the
        state heal is gone — they're independent. The awaiting heal appends an awaiting-only record, so the
        latest STATE-bearing record (what the kernel's _last_state_value reads, ignoring overlays) must still
        be "working" — never rewritten to "waiting"."""
        import json as _j
        sb.write_reg(self.d, "sid_a", {"sid": "sid_a", "name": "n", "cwd": "/tmp", "alive": True})
        sb.append_state(self.d, "sid_a", "working")
        sb.append_awaiting(self.d, "sid_a", True, "1 background task(s) running")
        sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        self.assertEqual(sb.last_awaiting(self.d, "sid_a"), False, "stale awaiting:true is cleared on restart")
        states = []
        with open(os.path.join(self.d, "states", "sid_a.jsonl")) as f:
            for line in f:
                rec = _j.loads(line)
                if isinstance(rec.get("state"), str):
                    states.append(rec["state"])
        self.assertEqual(states[-1], "working", "the latest STATE record is untouched (no 'waiting' heal)")

    def test_new_session_does_not_guess_a_model(self):
        """A brand-new SDK session's model is UNKNOWN until it connects — we DON'T guess it from the fleet (the
        user 2026-06-24: implement the designed way, not heuristics). spawn writes no liveModel; the lane shows
        blank until eager-connect-on-open pulls the real model from get_context_usage()/the init message. A
        peer already knowing its own model must NOT bleed onto the new session's badge."""
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        s1 = be.spawn("first", self.d)
        sb.write_reg(self.d, s1, {**sb.read_reg(self.d, s1), "liveModel": "Opus 4.8"})   # s1 learned its own model
        s2 = be.spawn("second", self.d)
        self.assertIsNone(sb.read_reg(self.d, s2).get("liveModel"), "no fleet-wide model guess on spawn")
        self.assertEqual(be.live_sessions()[s2]["model"], "", "blank until it connects and reports its own")

    def _last_awaiting(self, sid):
        import json as _j
        rec = None
        with open(os.path.join(self.d, "states", sid + ".jsonl")) as f:
            for line in f:
                o = _j.loads(line)
                if "awaiting" in o:
                    rec = o
        return rec

    def test_awaiting_overlay_shape(self):
        # bugz's reader scans for the latest line with an "awaiting" key (interleaved with state records)
        sb.append_state(self.d, "a1", "working")
        sb.append_awaiting(self.d, "a1", True, "2 background task(s) running")
        rec = self._last_awaiting("a1")
        self.assertEqual(rec["awaiting"], True)
        self.assertEqual(rec["why"], "2 background task(s) running")
        sb.append_awaiting(self.d, "a1", False)
        rec = self._last_awaiting("a1")
        self.assertEqual(rec["awaiting"], False)
        self.assertNotIn("why", rec)               # false clears, no why

    def test_stop_hook_clears_awaiting_ignoring_bg_shell_tasks(self):
        # The Stop hook CLEARS awaiting (false), even with run_in_background SHELL tasks still outstanding
        # (the user 2026-07-07): a leftover backgrounded shell task must not pin an idle session to a
        # working flavor. Only real subagents (the live snapshot) leave a session awaiting, so the Stop
        # hook ignores inp['background_tasks'] and just clears any stale awaiting:true.
        import asyncio
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sess = sb.SdkSession(be, {"sid": "h1", "name": "n", "cwd": self.d, "mode": "acceptEdits"})
        asyncio.run(sess._stop_hook({"background_tasks": [{"id": "t1"}, {"id": "t2"}]}, None, None))
        self.assertEqual(self._last_awaiting("h1")["awaiting"], False,
                         "leftover run_in_background shell tasks do NOT make the session awaiting")
        asyncio.run(sess._stop_hook({"background_tasks": []}, None, None))   # nothing outstanding → still cleared
        self.assertEqual(self._last_awaiting("h1")["awaiting"], False)


class SetModelModePure(unittest.TestCase):
    """set_model / set_mode persist to the registry even with no live session thread (CI-safe,
    no SDK). The live control-channel apply is covered by AskRoundTrip.test_set_model_and_mode_apply_live."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def test_set_model_persists_to_registry(self):
        sid = self.be.spawn("m", self.d)                  # writes reg; no session thread until send()
        self.assertTrue(self.be.set_model(sid, "opus"))
        self.assertEqual(sb.read_reg(self.d, sid)["model"], "opus")
        self.assertFalse(self.be.set_model("no-such-sid", "opus"))

    def test_set_model_on_a_dormant_session_resolves_the_badge_not_stale(self):
        # the user 2026-07-03: set_model stamped the chosen alias but left reg.liveModel stale, and
        # model_label PREFERS liveModel → the badge kept the OLD name (track: chose fable, showed Opus 4.8).
        # A dormant session (no live thread, no turn coming) resolves to the chosen alias's label NOW —
        # never stale, never trapped on switching-dots.
        sid = self.be.spawn("m", self.d)
        sb.write_reg(self.d, sid, {**sb.read_reg(self.d, sid), "liveModel": "Opus 4.8"})   # a stale prior live name
        self.assertTrue(self.be.set_model(sid, "fable"))
        reg = sb.read_reg(self.d, sid)
        # a bare alias has no version until the real name lands, so the best-effort label is "Fable" — the
        # point is it reflects the PICK (not the stale "Opus 4.8"); the real "Fable 5" fills in on next connect.
        self.assertEqual(reg["liveModel"], "Fable", "the dormant badge reflects the pick, not the stale prior")
        self.assertFalse(reg.get("modelPending"), "nothing is coming to resolve dots → resolve immediately, no trap")
        self.assertEqual(sb.model_label(reg["liveModel"], reg["model"]), "Fable")

    def test_alias_label_and_model_reflects_alias(self):
        self.assertEqual(sb._alias_label("opus"), "Opus")
        self.assertEqual(sb._alias_label("claude-opus-4-8"), "Opus 4.8")
        self.assertEqual(sb._alias_label("default"), "")
        self.assertEqual(sb._alias_label(""), "")
        self.assertTrue(sb._model_reflects_alias("Opus 4.8", "opus"), "the bare alias is a substring of the pretty name")
        self.assertTrue(sb._model_reflects_alias("Fable 5", "fable"))
        self.assertTrue(sb._model_reflects_alias("Opus 4.8", "claude-opus-4-8"), "a full id alias matches on its family word")
        self.assertFalse(sb._model_reflects_alias("Fable 5", "opus"), "the OLD name does not reflect the new pick")
        self.assertTrue(sb._model_reflects_alias("anything", "default"), "default matches the resolved name")
        self.assertFalse(sb._model_reflects_alias("", "opus"), "no live name yet → not resolved")

    def test_learn_model_clears_pending_only_when_the_new_name_lands(self):
        sess = sb.SdkSession(self.be, {"sid": "p", "name": "n", "cwd": self.d, "model": "fable"})
        sess.model = "Fable 5"
        sess._model_pending = "opus"                       # a switch to opus is resolving
        sess._learn_model("Fable 5")                       # a still-in-flight fable turn reports the OLD name
        self.assertEqual(sess._model_pending, "opus", "the old name does not clear the switch — dots stay")
        sess._learn_model("Opus 4.8")                      # the new model finally streams
        self.assertEqual(sess._model_pending, "", "the matching name clears the switch — dots stop")
        self.assertEqual(sess.model, "Opus 4.8")
        self.assertFalse(sb.read_reg(self.d, "p").get("modelPending") or False)

    def test_session_gone_mid_switch_does_not_trap_the_dots(self):
        sess = sb.SdkSession(self.be, {"sid": "g", "name": "n", "cwd": self.d, "model": "opus"})
        sess._model_pending = "opus"
        self.be._on_session_gone(sess)
        self.assertEqual(sess._model_pending, "", "a thread that dies mid-switch resolves the pending marker")
        self.assertFalse(sb.read_reg(self.d, "g").get("modelPending") or False)
        self.assertEqual(sb.read_reg(self.d, "g").get("liveModel"), "Opus", "and lands a best-effort label, not blank")

    def test_set_mode_persists_to_registry(self):
        sid = self.be.spawn("m", self.d)
        self.assertTrue(self.be.set_mode(sid, "plan"))
        self.assertEqual(sb.read_reg(self.d, sid)["mode"], "plan")

    def test_bypass_applies_to_THIS_session_and_is_never_remembered(self):
        # The picker offers Bypass on SDK sessions (the user 2026-08-15). Every other mode is a
        # preference worth inheriting, so it seeds the next new session — but spawn() reads that seed
        # with nothing in the create UI showing it, so remembering BYPASS would hand every session you
        # started afterwards an unprompted agent, off one click on one tab. It stays where you set it.
        sid = self.be.spawn("m", self.d)
        self.assertTrue(self.be.set_mode(sid, "plan"))
        self.assertEqual(sb.read_sdk_defaults(self.d).get("mode"), "plan", "an ordinary mode is remembered")

        self.assertTrue(self.be.set_mode(sid, "bypassPermissions"))
        self.assertEqual(sb.read_reg(self.d, sid)["mode"], "bypassPermissions",
                         "it DOES apply to the session you set it on")
        self.assertEqual(sb.read_sdk_defaults(self.d).get("mode"), "plan",
                         "…and leaves the remembered default alone, rather than clobbering it")
        self.assertEqual(sb.read_reg(self.d, self.be.spawn("m2", self.d))["mode"], "plan",
                         "so the NEXT new session comes up prompting, not bypassing")

    def test_bypass_is_not_remembered_even_as_the_first_mode_ever_picked(self):
        # The guard cannot be "keep the previous default" alone: with no default written yet there is
        # nothing to keep, and spawn()'s own fallback has to be what a new session lands on.
        sid = self.be.spawn("m", self.d)
        self.assertTrue(self.be.set_mode(sid, "bypassPermissions"))
        self.assertNotEqual(sb.read_sdk_defaults(self.d).get("mode"), "bypassPermissions")
        self.assertNotEqual(sb.read_reg(self.d, self.be.spawn("m2", self.d))["mode"], "bypassPermissions")

    def test_chosen_model_read_from_reg_on_construct(self):
        sess = sb.SdkSession(self.be, {"sid": "x", "name": "n", "cwd": self.d, "model": "sonnet"})
        self.assertEqual(sess.chosen_model, "sonnet")
        plain = sb.SdkSession(self.be, {"sid": "y", "name": "n", "cwd": self.d})
        self.assertEqual(plain.chosen_model, "")          # default: no model flag → CLI default

    def test_spawn_sets_default_effort_and_set_effort_validates(self):
        sid = self.be.spawn("e", self.d)
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], sb.DEFAULT_EFFORT)   # explicit default so the picker shows a true value
        self.assertTrue(self.be.set_effort(sid, "low"))
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], "low")
        self.assertFalse(self.be.set_effort(sid, "ultra"))   # not a real level → rejected
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], "low")   # reg unchanged after a bad value
        self.assertFalse(self.be.set_effort("no-such-sid", "low"))

    def test_effort_read_from_reg_on_construct(self):
        s1 = sb.SdkSession(self.be, {"sid": "a", "name": "n", "cwd": self.d, "effort": "max"})
        self.assertEqual(s1.effort, "max")
        s2 = sb.SdkSession(self.be, {"sid": "b", "name": "n", "cwd": self.d})
        self.assertEqual(s2.effort, sb.DEFAULT_EFFORT)   # no reg effort → default (so the picker is never empty)


class RememberedDefaults(unittest.TestCase):
    """A NEW SDK session seeds its model+effort from the user's LAST pick on any session (remembered
    globally in sdk-defaults.json), falling back to the hardcoded defaults — and the seed lands in the new
    session's OWN reg, so the badge can't desync from what _options launches with (the user 2026-06-27).
    CI-safe: spawn/set_model/set_effort all work with no live session thread."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def test_fresh_install_uses_hardcoded_defaults(self):
        sid = self.be.spawn("a", self.d)
        reg = sb.read_reg(self.d, sid)
        self.assertEqual(reg["effort"], sb.DEFAULT_EFFORT)
        self.assertNotIn("model", reg)               # nothing remembered → account default (no model override)
        self.assertEqual(sb.read_sdk_defaults(self.d), {})

    def test_set_effort_is_remembered_and_seeds_the_next_session(self):
        s1 = self.be.spawn("a", self.d)
        self.assertTrue(self.be.set_effort(s1, "low"))
        self.assertEqual(sb.read_sdk_defaults(self.d).get("effort"), "low", "the pick is remembered globally")
        s2 = self.be.spawn("b", self.d)                                   # a NEW session, created AFTER the pick
        self.assertEqual(sb.read_reg(self.d, s2)["effort"], "low", "new session seeds the remembered effort")
        self.assertEqual(self.be.live_sessions()[s2]["effort"], "low", "and the badge shows exactly that")

    def test_set_model_is_remembered_and_seeds_the_next_session(self):
        s1 = self.be.spawn("a", self.d)
        self.assertTrue(self.be.set_model(s1, "sonnet"))
        self.assertEqual(sb.read_sdk_defaults(self.d).get("model"), "sonnet")
        s2 = self.be.spawn("b", self.d)
        self.assertEqual(sb.read_reg(self.d, s2)["model"], "sonnet", "new session seeds the remembered model")
        # no desync: what the dormant badge shows pre-connect == the alias it will actually launch with
        self.assertEqual(self.be.live_sessions()[s2]["model"], "Sonnet")
        self.assertEqual(sb.SdkSession(self.be, sb.read_reg(self.d, s2)).chosen_model, "sonnet")

    def test_remembering_model_does_not_clobber_remembered_effort(self):
        s1 = self.be.spawn("a", self.d)
        self.be.set_effort(s1, "max")
        self.be.set_model(s1, "opus")                                    # touches model only
        d = sb.read_sdk_defaults(self.d)
        self.assertEqual((d.get("model"), d.get("effort")), ("opus", "max"))

    def test_fresh_session_defaults_to_acceptEdits_mode(self):
        sid = self.be.spawn("a", self.d)
        self.assertEqual(sb.read_reg(self.d, sid)["mode"], "acceptEdits", "no remembered mode → acceptEdits")

    def test_set_mode_is_remembered_and_seeds_the_next_session(self):
        # the bug (the user 2026-06-27): mode wasn't remembered like model/effort, so every new SDK session
        # came up acceptEdits regardless of the user's preferred mode. (This used to demonstrate the point
        # with bypassPermissions, which is now the ONE mode deliberately left out of the memory — see
        # SetModelModePure.test_bypass_applies_to_THIS_session_and_is_never_remembered. Any other mode
        # still seeds, which is what this covers.)
        s1 = self.be.spawn("a", self.d)
        self.assertTrue(self.be.set_mode(s1, "auto"))
        self.assertEqual(sb.read_sdk_defaults(self.d).get("mode"), "auto", "remembered globally")
        s2 = self.be.spawn("b", self.d)                                  # a NEW session, created AFTER the pick
        self.assertEqual(sb.read_reg(self.d, s2)["mode"], "auto", "new session seeds the mode")
        self.assertEqual(self.be.live_sessions()[s2]["mode"], "auto", "and the badge shows it")

    def test_a_hand_written_bypass_default_is_still_honoured(self):
        # The carve-out lives in set_mode, NOT in spawn: romp declines to remember bypass off a click,
        # but it does not overrule someone who wrote the default themselves. The escape hatch stays open
        # for anyone who genuinely wants every new session unprompted.
        sb.write_sdk_default(self.d, mode="bypassPermissions")
        self.assertEqual(sb.read_reg(self.d, self.be.spawn("a", self.d))["mode"], "bypassPermissions")

    def test_remembering_mode_does_not_clobber_model_or_effort(self):
        s1 = self.be.spawn("a", self.d)
        self.be.set_effort(s1, "low"); self.be.set_model(s1, "opus"); self.be.set_mode(s1, "plan")
        d = sb.read_sdk_defaults(self.d)
        self.assertEqual((d.get("model"), d.get("effort"), d.get("mode")), ("opus", "low", "plan"))

    def test_resetting_model_to_default_clears_the_override_for_new_sessions(self):
        s1 = self.be.spawn("a", self.d)
        self.be.set_model(s1, "sonnet")
        self.be.set_model(s1, "default")                                 # user resets to the account default
        self.assertEqual(sb.read_sdk_defaults(self.d).get("model"), "default")
        s2 = self.be.spawn("b", self.d)
        self.assertNotIn("model", sb.read_reg(self.d, s2), "remembered 'default' → no model override (account default)")

    def test_bad_remembered_effort_falls_back_to_hardcoded(self):
        sb.write_sdk_default(self.d, effort="ultra")                     # a level that isn't valid (e.g. stale file)
        sid = self.be.spawn("a", self.d)
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], sb.DEFAULT_EFFORT)


class LiveAskReplay(unittest.TestCase):
    """A blocked SDK session's prompt must REPLAY, not vanish (the user 2026-06-24: blocked-no-prompt). _emit_ask
    STORES the ask (not just a bool) so the kernel's _ask_poll can re-push it to any chat client that connects /
    refocuses / reloads after the ask was raised; _clear_ask removes it on answer/cancel. Before this, the ask
    was a one-shot push and _ask_poll pane-scraped the (pane-less) SDK session, found nothing, and cleared the
    prompt every 1.2s tick."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.backend = sb.SdkBackend(self.d, "/bin/true", lambda app, msg: None)

    def test_emit_stores_ask_for_replay_and_clear_removes(self):
        class _Sess:
            sid = "11111111-2222-3333-4444-555555555555"
        sess = _Sess()
        ask = {"kind": "single", "header": "Pet",
               "options": [{"n": 1, "label": "cats"}, {"n": 2, "label": "dogs"}]}
        self.assertIsNone(self.backend.current_ask(sess.sid))       # nothing pending yet
        self.backend._emit_ask(sess, ask)
        self.assertEqual(self.backend.current_ask(sess.sid), ask)   # stored verbatim → _ask_poll replays it
        self.backend._clear_ask(sess)
        self.assertIsNone(self.backend.current_ask(sess.sid))       # answered/cancelled → gone


# --- Runner + can_use_tool bridge (needs the SDK message classes) ---
try:
    import claude_agent_sdk as _sdk
    _HAVE_SDK = True
except Exception:
    _HAVE_SDK = False


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class AskRoundTrip(unittest.TestCase):
    """Drive a full turn through a fake client and assert the AskUserQuestion
    answer round-trips as PermissionResultAllow(updated_input)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._orig_client = _sdk.ClaudeSDKClient

        QUESTION = {"questions": [{
            "question": "Cats or dogs?", "header": "Pet", "multiSelect": False,
            "options": [{"label": "cats", "description": "c"}, {"label": "dogs", "description": "d"}],
        }]}

        class _Ctx:
            title = display_name = decision_reason = None

        import asyncio as _aio

        class FakeClient:
            """Models the real split: query(iterable) WRITES each turn (blocking until the
            iterable ends), receive_messages() yields outputs independently. So this only
            works if the backend runs feeder + receiver CONCURRENTLY — if it awaited query()
            first (the bug the live smoke caught), the never-ending input generator would
            starve the receive loop and this test would hang."""
            instances = []

            def __init__(self, options=None, transport=None):
                self.options = options
                self.captured = None
                self.interrupted = False
                self.model_calls = []        # records set_model() over the control channel
                self.mode_calls = []         # records set_permission_mode()
                self._turnq = _aio.Queue()
                FakeClient.instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def query(self, prompt, session_id="default"):
                async for turn in prompt:                # writes each turn (blocks like the real one)
                    await self._turnq.put(turn)

            async def interrupt(self):
                self.interrupted = True

            async def set_model(self, model=None):
                self.model_calls.append(model)

            async def set_permission_mode(self, mode):
                self.mode_calls.append(mode)

            async def get_context_usage(self):
                # The DESIGNED control request behind the CLI's /context. Unlike the message stream, it
                # answers PRE-TURN — returning the live model id + % the instant the control channel is up.
                # This is the source the eager-connect publish relies on (verified against the real SDK).
                return {"percentage": 2, "model": "claude-x"}

            async def receive_messages(self):
                # FAITHFUL to the real CLI: a turn-less streaming connection emits NO message at all — the
                # `init` SystemMessage rides the FIRST turn's response, NOT connect. (The old fake yielded
                # init on connect, which let the broken pre-turn path pass CI while real sessions stayed
                # blank until message 1 — the user 2026-06-27.) So init is yielded only once a turn arrives.
                first = True
                while True:
                    await self._turnq.get()              # next enqueued user turn
                    if first:
                        first = False
                        yield _sdk.SystemMessage("init", {
                            "model": "claude-x", "permissionMode": "acceptEdits",
                            "session_id": (self.options.session_id or "fsid")})
                    allow = await self.options.can_use_tool("AskUserQuestion", QUESTION, _Ctx())
                    self.captured = allow
                    yield _sdk.AssistantMessage(content=[_sdk.TextBlock("ok")], model="claude-x")
                    yield _sdk.ResultMessage("success", 1, 1, False, 1, "fsid")

        _sdk.ClaudeSDKClient = FakeClient
        self.Fake = FakeClient
        FakeClient.instances = []

        self.notes = []

        def notify(app, msg):
            self.notes.append((app, msg))
            if msg.get("type") == "askLive":            # the UI auto-answers option 1 (cats)
                self.backend.on_ask(msg["id"], "answer", 1)

        self.backend = sb.SdkBackend(self.d, "/bin/true", notify)

    def tearDown(self):
        _sdk.ClaudeSDKClient = self._orig_client

    def _wait(self, pred, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def test_spawn_then_ask_round_trip(self):
        sid = self.backend.spawn("alpha", self.d)
        # spawn registers identity + registry + initial state, all on disk
        self.assertTrue(sb.read_reg(self.d, sid)["alive"])
        self.assertTrue(os.path.exists(os.path.join(self.d, "names", sid)))
        self.assertIn(sid, self.backend.live_sessions())

        self.assertTrue(self.backend.send(sid, "hello"))
        self.assertTrue(self._wait(lambda: self.Fake.instances and self.Fake.instances[0].captured),
                        "can_use_tool never returned an answer")

        allow = self.Fake.instances[0].captured
        self.assertEqual(allow.behavior, "allow")
        self.assertEqual(allow.updated_input["answers"], {"Cats or dogs?": "cats"})
        self.assertEqual(allow.updated_input["questions"], [{
            "question": "Cats or dogs?", "header": "Pet", "multiSelect": False,
            "options": [{"label": "cats", "description": "c"}, {"label": "dogs", "description": "d"}]}])

        # an askLive went out and was later cleared
        kinds = [m.get("type") for _app, m in self.notes]
        self.assertIn("askLive", kinds)
        self.assertIn("askLiveClear", kinds)

        # state settled working -> waiting after the result
        self.assertTrue(self._wait(
            lambda: sb.last_state(self.d, sid).get("state") == "waiting"))

    def test_connect_publishes_model_and_ctx_without_a_turn(self):
        """Eager-connect (used at createSession + on chat open) brings up the session WITHOUT a user turn, and
        its model AND context % must publish on OPEN — like a tmux session shows them on launch (the user
        2026-06-27). The streaming `init` SystemMessage does NOT arrive until the first turn (the FakeClient
        now models this), so connect resolves both from get_context_usage() — the designed control request
        that answers pre-turn. REGRESSION: the old code keyed model/ctx resolution off that init message, so a
        freshly-created SDK session showed neither until the first message was sent."""
        sid = self.backend.spawn("eager", self.d)
        self.assertEqual(self.backend.live_sessions()[sid]["model"], "", "no model before connect")
        self.assertEqual(self.backend.live_sessions()[sid]["ctx"], "", "no context before connect")
        self.assertTrue(self.backend.connect(sid))
        self.assertTrue(self._wait(lambda: self.backend.live_sessions().get(sid, {}).get("model")),
                        "eager-connect must publish the model via get_context_usage — no init message, no turn")
        snap = self.backend.live_sessions()[sid]
        self.assertEqual(snap["model"], "claude-x", "model resolves from get_context_usage on connect")
        self.assertEqual(snap["ctx"], 2, "context % resolves from get_context_usage on connect")
        # prove no user turn was ever fed: the ask callback never fired
        self.assertTrue(self.Fake.instances, "a client was created on connect")
        self.assertIsNone(self.Fake.instances[0].captured, "no turn was sent — can_use_tool must not have run")
        self.assertFalse(self.backend.connect("no-such-sid"))

    def test_kill_marks_dead(self):
        sid = self.backend.spawn("beta", self.d)
        self.backend.send(sid, "hi")
        self._wait(lambda: self.Fake.instances and self.Fake.instances[0].captured)
        self.assertTrue(self.backend.kill(sid))
        self.assertFalse(sb.read_reg(self.d, sid)["alive"])
        self.assertNotIn(sid, self.backend.live_sessions())

    def test_set_model_and_mode_apply_live(self):
        """The model/mode pickers go over the SDK CONTROL channel (set_model / set_permission_mode),
        not a /model or /effort slash injection into the prompt stream (which the SDK ignores)."""
        sid = self.backend.spawn("ctrl", self.d)
        self.assertTrue(self.backend.send(sid, "hi"))
        self.assertTrue(self._wait(lambda: self.Fake.instances and self.Fake.instances[0].captured),
                        "session never connected")
        c = self.Fake.instances[0]
        self.backend.set_model(sid, "opus")
        self.assertTrue(self._wait(lambda: "opus" in c.model_calls),
                        "model alias not sent via set_model control request")
        self.backend.set_model(sid, "default")
        self.assertTrue(self._wait(lambda: None in c.model_calls),
                        "'default' must reset via set_model(None)")
        self.backend.set_mode(sid, "plan")
        self.assertTrue(self._wait(lambda: "plan" in c.mode_calls),
                        "mode not sent via set_permission_mode control request")
        # persisted so a reconnect keeps the choice; _options carries chosen_model
        self.assertEqual(sb.read_reg(self.d, sid)["model"], "default")
        self.assertEqual(sb.read_reg(self.d, sid)["mode"], "plan")
        sess = self.backend.sessions[sid]
        sess.chosen_model = "sonnet"
        opts = self.backend._options(sess, _sdk.ClaudeAgentOptions)
        self.assertEqual(opts.model, "sonnet")

    def test_effort_change_reconnects_with_new_flag(self):
        """effort is a connect-time CLI flag, so changing it RECONNECTS the client (a 2nd ClaudeSDKClient)
        with the new --effort, resuming the same conversation — not a /effort slash the SDK ignores."""
        sid = self.backend.spawn("eff", self.d)
        self.assertTrue(self.backend.send(sid, "hi"))
        self.assertTrue(self._wait(lambda: self.Fake.instances and self.Fake.instances[0].captured),
                        "first connection never completed a turn")
        self.assertEqual(self.Fake.instances[0].options.effort, sb.DEFAULT_EFFORT)   # spawned at the default
        self.assertTrue(self.backend.set_effort(sid, "low"))
        self.assertTrue(self._wait(lambda: len(self.Fake.instances) >= 2),
                        "effort change did not reconnect the client")
        c2 = self.Fake.instances[1]
        self.assertEqual(c2.options.effort, "low")          # reconnected with the new flag
        self.assertEqual(c2.options.resume, sid)             # resume continues the SAME conversation, not a fresh session
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], "low")
        self.backend.kill(sid)


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class CustomAnswerRoundTrip(unittest.TestCase):
    """The 'add your own' free-text affordance on AskUserQuestion (the user 2026-06-27). _ask_one is driven
    through a scripted sequence of actions: each emitted askLive triggers the next action via on_ask, exactly
    as the kernel routes a UI click. Single-select returns the typed text; multi accumulates customs and
    includes them on Submit; unchecking a typed custom removes it."""

    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.actions = []
        def notify(app, msg):
            if msg.get("type") == "askLive" and self.actions:
                kind, payload = self.actions.pop(0)
                self.backend.on_ask(msg["id"], kind, payload)
        self.backend = sb.SdkBackend(self.d, "/bin/true", notify)
        self.sess = sb.SdkSession(self.backend, {"sid": self.SID, "name": "n", "cwd": self.d})
        self.backend.sessions[self.SID] = self.sess

    def _ask(self, question, actions):
        import asyncio
        self.actions = list(actions)
        async def go():
            self.sess.loop = asyncio.get_running_loop()
            return await self.sess._ask_one(question, 0, 1)
        return asyncio.run(go())

    def test_single_select_returns_typed_custom_answer(self):
        q = {"question": "Pick", "header": "H", "multiSelect": False,
             "options": [{"label": "A"}, {"label": "B"}]}
        res = self._ask(q, [("custom", "something else entirely")])
        self.assertEqual(res, "something else entirely")

    def test_multi_select_includes_custom_with_checked_options(self):
        q = {"question": "Pick many", "header": "H", "multiSelect": True,
             "options": [{"label": "A"}, {"label": "B"}]}
        res = self._ask(q, [("toggle", 1), ("custom", "extra"), ("submit", None)])
        self.assertEqual(res, ["A", "extra"])

    def test_multi_select_unchecking_a_custom_removes_it(self):
        q = {"question": "Pick many", "header": "H", "multiSelect": True,
             "options": [{"label": "A"}, {"label": "B"}]}
        # add a custom (becomes option n=3), then toggle n=3 off, then submit → nothing chosen
        res = self._ask(q, [("custom", "oops"), ("toggle", 3), ("submit", None)])
        self.assertEqual(res, [])


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class PermissionAndPlanRoundTrip(unittest.TestCase):
    """Drive _can_use_tool / _approve_plan directly and assert the PermissionResult the SDK gets back.
    The picker answer is delivered via resolve_ask (call_soon_threadsafe), exactly as the kernel does
    on an inbound on_ask. (the user 2026-06-27: plan approval + diffs + allow-and-don't-ask-again.)"""

    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.answer = "1"
        def notify(app, msg):
            if msg.get("type") == "askLive":
                self.last_ask = msg["ask"]
                self.backend.on_ask(msg["id"], "answer", self.answer)
        self.backend = sb.SdkBackend(self.d, "/bin/true", notify)
        self.sess = sb.SdkSession(self.backend, {"sid": self.SID, "name": "n", "cwd": self.d})
        self.backend.sessions[self.SID] = self.sess
        self.sess.inflight = 1                      # mid-turn (a permission interrupts a live turn)

    def _drive(self, coro_fn):
        import asyncio
        async def go():
            self.sess.loop = asyncio.get_running_loop()
            return await coro_fn()
        return asyncio.run(go())

    def test_allow_and_dont_ask_again_returns_updated_permissions(self):
        # the remember path just echoes the SDK's own suggestions back verbatim, so a sentinel suffices
        upd = _sdk.PermissionUpdate(type="setMode", mode="acceptEdits")
        class Ctx:
            title = None; description = None; suggestions = [upd]
        self.answer = "2"                            # "Allow & don't ask again"
        res = self._drive(lambda: self.sess._can_use_tool("Bash", {"command": "ls"}, Ctx()))
        self.assertEqual(res.behavior, "allow")
        self.assertEqual(res.updated_permissions, [upd], "the SDK's own suggestion is echoed back as the rule")

    def test_plain_allow_carries_no_rule(self):
        class Ctx:
            title = None; description = None; suggestions = []
        self.answer = "1"
        res = self._drive(lambda: self.sess._can_use_tool("Bash", {"command": "ls"}, Ctx()))
        self.assertEqual(res.behavior, "allow")
        self.assertIsNone(res.updated_permissions)

    def test_deny_is_deny(self):
        class Ctx:
            title = None; description = None; suggestions = []
        self.answer = "2"                            # with no suggestions, option 2 is Deny
        res = self._drive(lambda: self.sess._can_use_tool("Bash", {"command": "ls"}, Ctx()))
        self.assertEqual(res.behavior, "deny")

    def test_plan_proceed_allows(self):
        self.answer = "1"
        res = self._drive(lambda: self.sess._approve_plan({"plan": "do the thing"}))
        self.assertEqual(res.behavior, "allow")
        self.assertIsNone(res.updated_permissions)
        self.assertEqual(self.last_ask["previewKind"], "plan")
        self.assertIn("do the thing", self.last_ask["preview"])

    def test_plan_accept_edits_sets_mode(self):
        self.answer = "2"                            # "Yes, auto-accept edits"
        res = self._drive(lambda: self.sess._approve_plan({"plan": "go"}))
        self.assertEqual(res.behavior, "allow")
        self.assertEqual(res.updated_permissions[0].type, "setMode")
        self.assertEqual(res.updated_permissions[0].mode, "acceptEdits")

    def test_plan_keep_planning_denies(self):
        self.answer = "3"
        res = self._drive(lambda: self.sess._approve_plan({"plan": "go"}))
        self.assertEqual(res.behavior, "deny")


class StopTask(unittest.TestCase):
    """The bg-task box's Stop button rides the SDK's designed stop_task control request (the user
    2026-08-04). The box shows tasks by TOOL-USE id (the kernel's transcript scan); the control request
    takes the CLI's lifecycle task id (_bg_tasks' key) — request_stop_task resolves either form. A
    refusal returns False so the kernel can WARN, never a silent no-op; tmux inherits the ABC's False
    (its box never shows live tasks)."""

    def _sess(self):
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        return sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": d})

    def test_resolves_the_tool_use_id_to_the_lifecycle_task_id(self):
        sess = self._sess()
        sess._bg_tasks["task-1"] = {"desc": "sweep", "type": "bash", "since": 1, "toolUseId": "toolu_9", "lastTool": ""}
        dispatched = []
        sess.loop = type("L", (), {"call_soon_threadsafe": staticmethod(lambda cb: dispatched.append(cb))})()
        sess.client = object()
        self.assertTrue(sess.request_stop_task("toolu_9"), "the box's tool-use id resolves")
        self.assertTrue(sess.request_stop_task("task-1"), "the lifecycle id itself is accepted too")
        self.assertEqual(len(dispatched), 2, "the control request is scheduled on the loop thread")

    def test_unknown_task_or_dead_client_refuses(self):
        sess = self._sess()
        self.assertFalse(sess.request_stop_task("toolu_missing"), "unknown/terminal task → False → kernel warns")
        sess._bg_tasks["task-1"] = {"toolUseId": "toolu_9"}
        self.assertFalse(sess.request_stop_task("toolu_9"), "no connected client → False, never a crash")

    def test_backend_routes_by_sid_and_tmux_defaults_false(self):
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        self.assertFalse(be.stop_task("no-such-sid", "toolu_9"))
        import importlib.util as _u
        spec_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                     "kernel", "session_backend.py")).read()
        self.assertIn("def stop_task(self, sid: str, task_id: str) -> bool:", spec_src)
        self.assertIn("return False", spec_src.split("def stop_task", 1)[1][:600],
                      "the ABC default is False — tmux has no such control")

    def test_kernel_op_warns_on_refusal(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "romp-kernel")) as f:
            src = f.read()
        self.assertIn('"stopTask"', src[src.index("ID_OPS"):src.index("ID_OPS") + 700],
                      "stopTask routes by session id like every session op")
        self.assertIn('elif t == "stopTask" and msg.get("taskId"):', src)
        self.assertIn("Couldn't stop that background task", src, "a refusal warns the user — never a silent no-op")


class ApiKeyAuthUsage(unittest.TestCase):
    """API-key auth is a PER-SESSION fact (the user 2026-08-08): with a real key in the service env,
    only the sessions whose project approved it used it, while every other session rode the
    subscription login — yet the old backend-global flag let whichever init arrived LAST speak for all
    of them, wiping the login's windows and re-wiping on every keyed session's reconnect. The flag now
    lives on the session: it gates that session's own get_usage polls and its RateLimitEvent records,
    and NOTHING wipes usage.json — the login's lifecycle is tracked read-side (the acct stamp, plus
    kernel _usage()'s no-login spend arm)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        self.p = Path(self.d) / "usage.json"
        self.p.write_text(json.dumps({"t": 1785898746,
                                      "five_hour": {"pct": 46, "resets_at": 1785907200},
                                      "seven_day": {"pct": 21, "resets_at": 1786388400}}))
        self.s = sb.SdkSession(self.be, {"sid": "11111111-2222-3333-4444-000000000001",
                                         "name": "n", "cwd": "/tmp"})

    def test_a_keyed_init_marks_only_its_own_session_and_never_wipes(self):
        before = self.p.read_text()
        self.be._note_auth_source(self.s, "ANTHROPIC_API_KEY")
        self.assertTrue(self.s.api_key_auth)
        self.assertEqual(self.p.read_text(), before,
                         "one keyed session never speaks for the login's windows — no wipe, ever")
        other = sb.SdkSession(self.be, {"sid": "11111111-2222-3333-4444-000000000002",
                                        "name": "m", "cwd": "/tmp"})
        self.assertFalse(other.api_key_auth, "the flag is the session's, not the backend's")

    def test_the_string_none_is_a_subscription_login_not_an_api_key(self):
        """The CLI has said "no API key" two ways: the field absent (verified 2026-08-04) and — since
        about CLI 2.1.222 — the literal string 'none' (both hosts' journals, 2026-08-08)."""
        self.be._note_auth_source(self.s, "none")
        self.assertFalse(self.s.api_key_auth, "'none' means NO api key — a subscription login")
        self.be._note_auth_source(self.s, "ANTHROPIC_API_KEY")
        self.assertTrue(self.s.api_key_auth)
        self.be._note_auth_source(self.s, "none")
        self.assertFalse(self.s.api_key_auth, "'none' flips a keyed session back, like the absent field")

    def test_a_keyed_sessions_rate_limit_events_never_reach_the_login_windows(self):
        # the key's limits are ANOTHER allowance — recording them contaminated the login's bars
        calls = []
        self.be._record_rate_limit = lambda info: calls.append(info)
        class _RL:
            rate_limit_info = {"kind": "five_hour"}
        self.be._forward = lambda sess, msg: None
        self.s.api_key_auth = True
        self.s._on_message(_RL(), _AssistantMessage, _ResultMessage, type("S", (), {}))
        self.assertEqual(calls, [], "a keyed session's events are ignored")
        self.s.api_key_auth = False
        self.s._on_message(_RL(), _AssistantMessage, _ResultMessage, type("S", (), {}))
        self.assertEqual(len(calls), 1, "a subscription session's events record as ever")

    def test_refresh_skips_keyed_sessions_and_polls_a_subscription_one(self):
        keyed = self.s
        keyed.api_key_auth = True
        keyed.client, keyed.loop, keyed.ended = object(), object(), False
        sub = sb.SdkSession(self.be, {"sid": "11111111-2222-3333-4444-000000000003",
                                      "name": "m", "cwd": "/tmp"})
        sub.client, sub.loop, sub.ended = object(), object(), False
        polled = []
        keyed.refresh_usage = lambda: polled.append("keyed") or True
        sub.refresh_usage = lambda: polled.append("sub") or True
        self.be.sessions = {keyed.sid: keyed, sub.sid: sub}
        self.be.refresh_usage()
        self.assertEqual(polled, ["sub"], "the click heals off the login's session, never the keyed one")

    def test_init_reads_the_field_and_the_poll_is_gated_per_session(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                "kernel", "sdk_backend.py")).read()
        self.assertIn('self.backend._note_auth_source(self, d.get("apiKeySource"))', src,
                      "the init message is the one in-band auth signal, handed the SESSION")
        self.assertIn("if self.api_key_auth:", src.split("async def _do_refresh_usage", 1)[1][:1600],
                      "get_usage is gated on the SESSION's own auth — it only times out on a keyed one")


class SpendRecord(unittest.TestCase):
    """Under API-key auth the rail shows SPEND where the subscription bars sat (the user 2026-08-04):
    each ResultMessage's total_cost_usd accumulates into spend.json by LOCAL date. Recorded on every
    result regardless of auth (display is gated, the record is not); pruned to 90 days."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        self.p = Path(self.d) / "spend.json"

    def _today(self):
        return time.strftime("%Y-%m-%d")

    def test_accumulates_cost_turns_and_tokens_by_day_and_hour(self):
        self.be._record_spend(0.02, {"input_tokens": 100, "output_tokens": 40,
                                     "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 60})
        self.be._record_spend(0.03, {"input_tokens": 10, "output_tokens": 5})
        o = json.loads(self.p.read_text())
        d = o["days"][self._today()]
        self.assertAlmostEqual(d["usd"], 0.05)
        self.assertEqual(d["turns"], 2)
        self.assertEqual((d["tokIn"], d["tokOut"], d["tokCacheR"], d["tokCacheW"]), (110, 45, 1000, 60))
        h = o["hours"][time.strftime("%Y-%m-%dT%H")]   # the rolling 5h/7d windows read these buckets
        self.assertAlmostEqual(h["usd"], 0.05)
        self.assertEqual(h["turns"], 2)

    def test_a_result_without_usage_still_records_cost(self):
        self.be._record_spend(0.02)
        d = json.loads(self.p.read_text())["days"][self._today()]
        self.assertEqual((d["usd"], d["tokIn"], d["tokOut"]), (0.02, 0, 0))

    def test_ignores_missing_zero_and_junk_costs(self):
        for v in (None, 0, -1, "0.5"):
            self.be._record_spend(v)
        self.assertFalse(self.p.exists(), "no real cost → no file, never a confident zero")

    def test_prunes_to_ninety_days(self):
        days = {"2020-01-%02d" % (i + 1) for i in range(25)}
        self.p.write_text(json.dumps({"days": {k: {"usd": 1, "turns": 1} for k in days}}))
        self.be._record_spend(0.01)
        kept = json.loads(self.p.read_text())["days"]
        self.assertLessEqual(len(kept), 90)
        self.assertIn(self._today(), kept)

    def test_by_sid_attribution_with_keyed_split(self):
        # T100 (the nightly optimizer's accepted ask): key-billed cost PER SESSION. Two sids, one
        # keyed and one login — the bucket totals stay whole, and each sid carries its own keyed
        # dimension. Private synthetic sids (the goal-store fixture rule).
        self.be._record_spend(0.02, {"input_tokens": 100, "output_tokens": 40}, keyed=True,
                              sid="aaaa1111-spend-attr-1")
        self.be._record_spend(0.03, {"input_tokens": 10, "output_tokens": 5}, keyed=False,
                              sid="aaaa1111-spend-attr-2")
        self.be._record_spend(0.05, {"input_tokens": 1, "output_tokens": 1}, keyed=True,
                              sid="aaaa1111-spend-attr-1")
        d = json.loads(self.p.read_text())["days"][self._today()]
        self.assertAlmostEqual(d["usd"], 0.10)
        by = d["bySid"]
        s1, s2 = by["aaaa1111-spend-attr-1"], by["aaaa1111-spend-attr-2"]
        self.assertAlmostEqual(s1["usd"], 0.07)
        self.assertEqual((s1["turns"], s1["tok"]), (2, 142))
        self.assertAlmostEqual(s1["key"]["usd"], 0.07, msg="the keyed split rides the sid — the optimizer's dimension")
        self.assertEqual((s1["key"]["turns"], s1["key"]["tok"]), (2, 142))
        self.assertAlmostEqual(s2["usd"], 0.03)
        self.assertNotIn("key", s2, "a login-only sid carries no keyed split — its cost is dollars nobody is billed")
        h = json.loads(self.p.read_text())["hours"][time.strftime("%Y-%m-%dT%H")]
        self.assertAlmostEqual(h["bySid"]["aaaa1111-spend-attr-1"]["usd"], 0.07, msg="the hour buckets attribute too")

    def test_a_thread_turn_bills_the_owning_session(self):
        # T144: highlight-reply comment threads minted phantom cheap sessions in spend.json and
        # undercounted the owner (one session split $360.31 own + $3.11 + $5.36 fork sids). The
        # settle passes threadOf when present — assert at the recorder level with the owner's sid.
        OWNER = "aaaa1111-spend-own-1"
        self.be._record_spend(0.02, keyed=True, sid=OWNER)                  # the owner's own turn
        self.be._record_spend(0.03, keyed=True, sid=OWNER)                  # a thread turn, billed via threadOf
        d = json.loads(self.p.read_text())["days"][self._today()]
        self.assertAlmostEqual(d["bySid"][OWNER]["usd"], 0.05,
                               msg="whole-session truth: the thread's spend lands under the owner")
        self.assertEqual(len(d["bySid"]), 1, "no phantom fork sid appears")

    def test_the_settle_seam_prefers_thread_of(self):
        # the session-object seam, executed: thread_of set → the owner's sid reaches the recorder;
        # promotion clears it → the session bills itself from that moment
        sid = "aaaa1111-spend-own-2"
        own = "aaaa1111-spend-own-3"
        sb.write_reg(Path(self.d), sid, {"sid": sid, "name": "c1", "cwd": "/tmp", "threadOf": own})
        s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), sid))
        self.assertEqual(s.thread_of, own, "the durable reg field seeds the object")
        self.assertEqual(s.thread_of or s.sid, own, "the settle's sid expression bills the owner")
        self.be.sessions[sid] = s
        sb.write_name(Path(self.d), sid, "promoted", "/tmp", "#123456", "white")  # promote needs no name write order here
        self.assertTrue(self.be.promote_thread(sid, "promoted"))
        self.assertEqual(s.thread_of, "", "a promoted session bills ITSELF from this moment")
        self.assertEqual(s.thread_of or s.sid, sid)

    def test_fork_spend_spanning_days_stays_under_the_owner(self):
        # the day-spanning fixture: a thread that worked before and after midnight accumulates
        # under the OWNER in each day's bucket independently (buckets key on local date at fold
        # time; the owner sid is constant, so both days read whole-session truth)
        OWNER = "aaaa1111-spend-own-4"
        yesterday = {"usd": 1.0, "turns": 2, "bySid": {OWNER: {"usd": 1.0, "turns": 2, "tok": 5}}}
        self.p.write_text(json.dumps({"days": {"2020-01-01": yesterday}}))
        self.be._record_spend(0.04, keyed=True, sid=OWNER)                  # after midnight, same owner
        days = json.loads(self.p.read_text())["days"]
        self.assertAlmostEqual(days["2020-01-01"]["bySid"][OWNER]["usd"], 1.0, msg="yesterday untouched")
        self.assertAlmostEqual(days[self._today()]["bySid"][OWNER]["usd"], 0.04,
                               msg="today's bucket bills the same owner — no phantom split at midnight")

    def test_legacy_rows_and_sidless_folds_stay_lossless(self):
        # a pre-T100 bucket (no bySid) folds cleanly, and a sid-less fold never drops attribution
        # already there (lossless legacy, the T18 discipline)
        self.p.write_text(json.dumps({"days": {self._today(): {"usd": 1.0, "turns": 3}}}))
        self.be._record_spend(0.02, keyed=True, sid="aaaa1111-spend-attr-3")
        d = json.loads(self.p.read_text())["days"][self._today()]
        self.assertAlmostEqual(d["usd"], 1.02)
        self.assertAlmostEqual(d["bySid"]["aaaa1111-spend-attr-3"]["usd"], 0.02,
                               msg="attribution begins mid-history without touching the legacy totals")
        self.be._record_spend(0.01)   # a sid-less caller
        d = json.loads(self.p.read_text())["days"][self._today()]
        self.assertAlmostEqual(d["usd"], 1.03)
        self.assertAlmostEqual(d["bySid"]["aaaa1111-spend-attr-3"]["usd"], 0.02,
                               msg="the sid-less fold carried the existing bySid forward")

    def test_by_sid_prunes_with_its_bucket(self):
        # the maps live INSIDE the buckets, so the 90d prune takes them with it — no second ledger
        # to sweep and no orphaned attribution
        days = {"2020-04-%02d" % (i % 30 + 1): {"usd": 1, "turns": 1,
                                                "bySid": {"aaaa1111-spend-attr-4": {"usd": 1, "turns": 1, "tok": 0}}}
                for i in range(30)}
        days.update({"2020-%02d-01" % (m + 1): {"usd": 1, "turns": 1} for m in range(12)})
        days.update({"2021-%02d-01" % (m + 1): {"usd": 1, "turns": 1} for m in range(12)})
        days.update({"2022-%02d-%02d" % (m + 1, d + 1): {"usd": 1, "turns": 1}
                     for m in range(12) for d in range(4)})
        self.assertGreater(len(days), 90, "the fixture really overflows the window")
        self.p.write_text(json.dumps({"days": days}))
        self.be._record_spend(0.01, sid="aaaa1111-spend-attr-5")
        kept = json.loads(self.p.read_text())["days"]
        self.assertLessEqual(len(kept), 90)
        self.assertIn(self._today(), kept)
        self.assertNotIn("2020-04-01", kept, "the oldest bucket left — and its bySid map with it, atomically")
        self.assertIn("bySid", kept[self._today()])

    def test_result_message_records_and_the_kernel_serves_it(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                "kernel", "sdk_backend.py")).read()
        self.assertIn("self.backend._record_spend(delta, turn_u, keyed=self.api_key_auth,", src,
                      "the settle folds THIS turn's DELTAS — cost AND tokens are cumulative per process")
        self.assertIn("sid=self.thread_of or self.sid)   # the rail's spend", src,
                      "a comment THREAD bills its OWNING session (T144); a plain session bills itself "
                      "(T100's per-session attribution, completed)")
        self.assertIn("self._last_cost_total = 0.0   # a fresh CLI process starts its cumulative cost at zero",
                      src, "each connect resets the watermark with its new process")
        self.assertIn("self._last_usage_totals = {}  # …and its cumulative token counters", src,
                      "the token watermarks reset with the same new process")
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "romp-kernel")) as f:
            ksrc = f.read()
        self.assertIn('if o.get("apiKey") or (not _claude_account() and (jd.STATE / "spend.json").exists()):',
                      ksrc, "_usage serves spend on the legacy marker OR a login-less machine with recorded spend")
        self.assertIn('"spend": _spend_windows()', ksrc)
        self.assertIn("def _spend_windows(keyed_only=False):", ksrc)   # keyed_only: the mixed-host API sum (test_session_auth)

    def test_cumulative_process_totals_fold_as_per_turn_deltas(self):
        """The CLI's total_cost_usd AND its usage dict are CUMULATIVE per process (the result event
        carries totalCostUSD beside `usage: this.totalUsage`): folding the raw values re-added the
        whole session-so-far on every turn, compounding the readouts into fiction — the dollars first
        (the user 2026-08-08, who did not believe the bottom line), then the tokens (same day, round
        two: the hover's 5h/7d/month dollars-per-token ratios diverged wildly because each window
        carried a different inflation factor). Fold deltas for both; reset the watermarks with each
        new CLI process; treat a shrunken counter as a reset we missed."""
        import asyncio
        sid = "11111111-2222-3333-4444-bbbbbbbbbbbb"
        s = sb.SdkSession(self.be, {"sid": sid, "name": "n", "cwd": "/tmp"})
        self.be._forward = lambda sess, msg: None
        self.be._turn_completed = lambda sid: None
        async def _noop(): pass
        s._do_refresh_context = _noop
        s._do_refresh_usage = _noop
        def _result(total, tok_in):
            r = _ResultMessage()
            r.total_cost_usd = total
            r.usage = {"input_tokens": tok_in}
            return r
        async def run(total, tok_in):
            s._on_message(_result(total, tok_in), _AssistantMessage, _ResultMessage, type("S", (), {}))
            await asyncio.sleep(0)
        def day():
            return json.loads(self.p.read_text())["days"][self._today()]
        asyncio.run(run(1.0, 100))   # first turn of the process: delta = the whole counter
        asyncio.run(run(2.5, 140))   # second turn: deltas = 1.5 / 40 tokens, NOT another 2.5 / 140
        d = day()
        self.assertAlmostEqual(d["usd"], 2.5, msg="two turns fold to the process total, never more")
        self.assertEqual(d["tokIn"], 140, "tokens fold as deltas of the totalUsage counter too")
        self.assertEqual(d["turns"], 2)
        s._last_cost_total = 0.0     # the connect reset: a fresh CLI process starts at zero…
        s._last_usage_totals = {}    # …on both counters
        asyncio.run(run(0.8, 30))
        self.assertAlmostEqual(day()["usd"], 3.3)
        self.assertEqual(day()["tokIn"], 170)
        asyncio.run(run(0.5, 20))    # a counter BELOW the watermark = a reset we missed → fold it whole
        self.assertAlmostEqual(day()["usd"], 3.8)
        self.assertEqual(day()["tokIn"], 190)


class RewindFiles(unittest.TestCase):
    """The bubble's restore-files affordance rides the SDK's designed rewind_files control request (the
    user 2026-08-04): the WORKSPACE goes back to its state before a user message; the conversation is
    untouched (edit/delete cover that). Checkpointing is enabled on every session so the checkpoints
    exist. A refusal returns False so the kernel warns — never a silent no-op; tmux inherits the ABC's
    False. No SDK import needed here (dispatch is deferred), so this runs in every venv."""

    def _sess(self):
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        return be, sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": d})

    def test_dispatches_on_the_loop_when_connected(self):
        _, sess = self._sess()
        dispatched = []
        sess.loop = type("L", (), {"call_soon_threadsafe": staticmethod(lambda cb: dispatched.append(cb))})()
        sess.client = object()
        self.assertTrue(sess.request_rewind_files("aaaa-uuid"))
        self.assertEqual(len(dispatched), 1, "the control request is scheduled on the loop thread")

    def test_refuses_without_a_client_or_uuid(self):
        _, sess = self._sess()
        self.assertFalse(sess.request_rewind_files("aaaa-uuid"), "no connected client → False → kernel warns")
        sess.loop = object(); sess.client = object()
        self.assertFalse(sess.request_rewind_files(""), "no uuid → refuse, never a blind restore")

    def test_backend_routes_by_sid_and_tmux_defaults_false(self):
        be, _ = self._sess()
        self.assertFalse(be.rewind_files("no-such-sid", "aaaa-uuid"))
        spec_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                     "kernel", "session_backend.py")).read()
        self.assertIn("def rewind_files(self, sid: str, uuid: str) -> bool:", spec_src)
        self.assertIn("return False", spec_src.split("def rewind_files", 1)[1][:600],
                      "the ABC default is False — tmux has no such control")

    def test_checkpointing_is_on_and_the_kernel_op_warns_on_refusal(self):
        base = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        sdk_src = open(os.path.join(base, "kernel", "sdk_backend.py")).read()
        self.assertIn("enable_file_checkpointing=True", sdk_src,
                      "every session checkpoints, so any user message is a restore point")
        with open(os.path.join(base, "bin", "romp-kernel")) as f:
            src = f.read()
        self.assertIn('"rewindFiles"', src[src.index("ID_OPS"):src.index("ID_OPS") + 700])
        self.assertIn('elif t == "rewindFiles" and msg.get("uuid"):', src)
        self.assertIn("Couldn't restore files", src, "a refusal warns the user — never a silent no-op")


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class OptionsAssembly(unittest.TestCase):
    """_options must use the SDK's DESIGNED option fields, not the extra_args CLI-flag escape hatch (the user
    2026-06-24: implement things the way the SDK designed them). The romp harness prompt is appended via
    system_prompt={"type":"preset","preset":"claude_code","append":...} (types.py SystemPromptPreset) — the
    documented field — NOT extra_args={"append-system-prompt"}, the passthrough reserved for flags with no field."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _sess(self, be):
        return sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555",
                                  "name": "n", "cwd": self.d, "mode": "acceptEdits"})

    def test_append_prompt_uses_designed_system_prompt_not_extra_args(self):
        p = os.path.join(self.d, "append.txt")
        with open(p, "w") as f:
            f.write("ROMP HARNESS PROMPT")
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, append_prompt_path=p)
        opts = be._options(self._sess(be), _sdk.ClaudeAgentOptions)
        self.assertEqual(opts.system_prompt,
                         {"type": "preset", "preset": "claude_code", "append": "ROMP HARNESS PROMPT"},
                         "harness prompt appended via the designed system_prompt preset field")
        self.assertNotIn("append-system-prompt", opts.extra_args or {},
                         "must NOT route the append through the extra_args CLI-flag escape hatch")

    def test_no_append_prompt_leaves_the_default_system_prompt(self):
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)   # no append_prompt_path
        opts = be._options(self._sess(be), _sdk.ClaudeAgentOptions)
        self.assertIsNone(opts.system_prompt, "no harness prompt → leave the CLI's default system prompt")

    def test_ultracode_maps_to_xhigh_plus_the_settings_key(self):
        # "ultracode" (the user 2026-08-04) is not a typed EffortLevel: the CLI's documented per-session
        # hook is the `ultracode` settings key (--settings / flag-settings layer), and ultracode IS xhigh
        # plus standing workflow orchestration — so the typed field carries xhigh and the key rides a
        # settings file. Never through extra_args.
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sess = self._sess(be)
        sess.effort = "ultracode"
        opts = be._options(sess, _sdk.ClaudeAgentOptions)
        self.assertEqual(opts.effort, "xhigh", "the typed field carries the effort half of ultracode")
        self.assertTrue(opts.settings and sb.FLAG_SETTINGS_DIR in opts.settings)
        with open(opts.settings) as f:
            self.assertEqual(json.load(f), {"ultracode": True})
        self.assertNotIn("effort", opts.extra_args or {})

    def test_plain_efforts_pass_through_with_no_settings_file(self):
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sess = self._sess(be)
        sess.effort = "max"
        opts = be._options(sess, _sdk.ClaudeAgentOptions)
        self.assertEqual(opts.effort, "max")
        self.assertIsNone(opts.settings, "no ultracode and no fast mode → no flag-settings file")

    def test_fast_mode_rides_the_flag_settings_opt_in(self):
        # The CLI REFUSES fast mode to any non-interactive client ("Fast mode is not available in the
        # Agent SDK") unless `fastMode` is true in the flag-settings layer — options.settings is that
        # layer, and this key is the host's designed opt-in (verified against claude 2.1.224).
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sess = self._sess(be)
        sess.fast_opt = True
        opts = be._options(sess, _sdk.ClaudeAgentOptions)
        self.assertTrue(opts.settings, "fast mode needs a flag-settings file to opt in through")
        with open(opts.settings) as f:
            self.assertEqual(json.load(f), {"fastMode": True})

    def test_ultracode_and_fast_mode_share_one_settings_file(self):
        # options.settings takes ONE path, so both keys must compose — an earlier shape wrote a fixed
        # single-key file and the second setting would have silently dropped the first.
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sess = self._sess(be)
        sess.effort, sess.fast_opt = "ultracode", True
        opts = be._options(sess, _sdk.ClaudeAgentOptions)
        with open(opts.settings) as f:
            self.assertEqual(json.load(f), {"ultracode": True, "fastMode": True})

    def test_each_session_gets_its_own_flag_settings_file(self):
        # The file is per-sid, not one shared file: its content now VARIES by session, so a shared path
        # would hand one session's fast mode to every other session that reads it.
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        a, b = self._sess(be), self._sess(be)
        b.sid = "99999999-8888-7777-6666-555555555555"
        a.fast_opt, b.fast_opt = True, False
        b.effort = "ultracode"
        pa = be._options(a, _sdk.ClaudeAgentOptions).settings
        pb = be._options(b, _sdk.ClaudeAgentOptions).settings
        self.assertNotEqual(pa, pb, "two sessions, two settings files")
        with open(pa) as f:
            self.assertEqual(json.load(f), {"fastMode": True})
        with open(pb) as f:
            self.assertEqual(json.load(f), {"ultracode": True}, "b's file is untouched by a's fast mode")

    def test_ultracode_is_a_choice_everywhere_but_never_the_seeded_default(self):
        self.assertIn("ultracode", sb.EFFORT_LEVELS, "the SDK backend accepts the pick")
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "romp-kernel")) as f:
            self.assertIn('("low", "medium", "high", "xhigh", "max", "ultracode")', f.read(),
                          "the effort dropdown (kernel EFFORT_CHOICES) offers ultracode")
        # per-session by design (the CLI: "this session only") — picking it must not seed NEW sessions
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-555555555555"
        sb.write_reg(self.d, sid, {"sid": sid, "name": "n", "cwd": self.d})
        self.assertTrue(be.set_effort(sid, "ultracode"))
        self.assertNotEqual(sb.read_sdk_defaults(self.d).get("effort"), "ultracode",
                            "ultracode never becomes the default for the next new session")
        self.assertEqual(sb.read_reg(self.d, sid)["effort"], "ultracode", "but THIS session keeps it")

    def test_raises_max_buffer_size_well_above_the_1mb_default(self):
        # A single >1MB stdout message (a big Read / grep result / echoed image) would otherwise crash the
        # receive loop → tear down the client → close stdin → the CLI rejects any PENDING picker with "Tool
        # permission stream closed before response received" (the user 2026-07-04, reproduced live).
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        opts = be._options(self._sess(be), _sdk.ClaudeAgentOptions)
        self.assertEqual(opts.max_buffer_size, sb.SDK_MAX_BUFFER, "romp sets the buffer cap explicitly")
        self.assertGreaterEqual(opts.max_buffer_size, 32 * 1024 * 1024,
                                "well past any realistic single message, so a picker never dies on overflow")


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class ApiRetryState(unittest.TestCase):
    """An api_retry storm (API rate-limit/overload) must surface as a distinct 'retrying' state, not a
    silent 'working', so a stall reads as an API issue (the user 2026-06-23). Cleared on real output."""

    def test_api_retry_shows_retrying_then_clears(self):
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sess = sb.SdkSession(be, {"sid": "r1", "name": "n", "cwd": d, "mode": "acceptEdits"})
        sess.inflight = 1                                        # a turn is in flight
        self.assertEqual(sess.snapshot()["state"], "working")
        sess._on_message(_sdk.SystemMessage("api_retry", {}), _sdk.AssistantMessage, _sdk.ResultMessage, _sdk.SystemMessage)
        self.assertTrue(sess.retrying)
        self.assertEqual(sess.snapshot()["state"], "retrying", "api_retry stall reads as 'retrying'")
        sess._on_message(_sdk.AssistantMessage(content=[_sdk.TextBlock("hi")], model="m"),
                         _sdk.AssistantMessage, _sdk.ResultMessage, _sdk.SystemMessage)
        self.assertFalse(sess.retrying)
        self.assertEqual(sess.snapshot()["state"], "working", "real output clears 'retrying'")

    def test_api_retry_detail_is_captured_and_cleared(self):
        # the payload's own numbers + the error behind the backoff ride snapshot["retryInfo"] so the
        # chat's retrying element can say WHAT is failing and when the next try fires (the user 2026-07-10)
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sess = sb.SdkSession(be, {"sid": "r2", "name": "n", "cwd": d, "mode": "acceptEdits"})
        sess.inflight = 1
        t0 = time.time()
        sess._on_message(_sdk.SystemMessage("api_retry", {"number": 3, "max_retries": 10,
                                                          "retry_delay_ms": 5000, "error_status": 529,
                                                          "error": "overloaded_error: overloaded"}),
                         _sdk.AssistantMessage, _sdk.ResultMessage, _sdk.SystemMessage)
        info = sess.snapshot()["retryInfo"]
        self.assertEqual(info["attempt"], 3)
        self.assertEqual(info["max"], 10)
        self.assertEqual(info["status"], 529)
        self.assertIn("overloaded", info["error"])
        self.assertAlmostEqual(info["retryAt"], t0 + 5, delta=2)
        sess._on_message(_sdk.AssistantMessage(content=[_sdk.TextBlock("hi")], model="m"),
                         _sdk.AssistantMessage, _sdk.ResultMessage, _sdk.SystemMessage)
        self.assertIsNone(sess.snapshot()["retryInfo"], "recovery clears the detail with the flag")

    def test_api_retry_with_a_bare_payload_still_counts(self):
        # only error_status has been SEEN on the wire — every other field must degrade to None / the
        # local attempt count, never crash or invent numbers
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        sess = sb.SdkSession(be, {"sid": "r3", "name": "n", "cwd": d, "mode": "acceptEdits"})
        sess.inflight = 1
        for want in (1, 2):
            sess._on_message(_sdk.SystemMessage("api_retry", {}), _sdk.AssistantMessage,
                             _sdk.ResultMessage, _sdk.SystemMessage)
            info = sess.snapshot()["retryInfo"]
            self.assertEqual(info["attempt"], want, "falls back to the local storm count")
        self.assertIsNone(info["max"])
        self.assertIsNone(info["status"])
        self.assertIsNone(info["error"])
        self.assertIsNone(info["retryAt"])
        # a turn-end Result clears the detail too (an unrecovered storm leaves no stale info)
        sess._on_message(_sdk.ResultMessage("success", 1, 1, False, 1, "fsid"),
                         _sdk.AssistantMessage, _sdk.ResultMessage, _sdk.SystemMessage)
        self.assertIsNone(sess.snapshot()["retryInfo"])


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class InterruptSettlesStall(unittest.TestCase):
    """Interrupt must drop a session out of 'working' even if the turn never produces a ResultMessage
    (e.g. it's stuck in an API-retry backoff) — the snapshot reads 'working' purely from inflight>0, so a
    user interrupt that the CLI is slow to honour would otherwise leave it 'working' forever."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._orig = _sdk.ClaudeSDKClient
        import asyncio as _aio

        class StallClient:
            instances = []

            def __init__(self, options=None, transport=None):
                self.options = options
                self.interrupted = False
                self._turnq = _aio.Queue()
                StallClient.instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def query(self, prompt, session_id="default"):
                async for turn in prompt:
                    await self._turnq.put(turn)

            async def interrupt(self):
                self.interrupted = True

            async def receive_messages(self):
                yield _sdk.SystemMessage("init", {"session_id": self.options.session_id or "fsid"})
                await self._turnq.get()              # consume the turn → inflight goes to 1...
                while True:
                    await _aio.sleep(3600)            # ...then STALL forever (never a ResultMessage)

        _sdk.ClaudeSDKClient = StallClient
        self.Fake = StallClient
        StallClient.instances = []
        self.backend = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        _sdk.ClaudeSDKClient = self._orig

    def _wait(self, pred, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def test_interrupt_settles_a_stalled_turn(self):
        sid = self.backend.spawn("stall", self.d)
        self.backend.send(sid, "go")
        self.assertTrue(self._wait(lambda: self.backend.live_sessions().get(sid, {}).get("state") == "working"),
                        "a stalled in-flight turn reads as working")
        self.assertTrue(self.backend.interrupt(sid))
        self.assertTrue(self._wait(lambda: self.Fake.instances and self.Fake.instances[0].interrupted),
                        "client.interrupt() was sent")
        self.assertTrue(self._wait(lambda: self.backend.live_sessions().get(sid, {}).get("state") != "working"),
                        "after interrupt the session is no longer 'working' even with no ResultMessage")


class InterruptAckSettlesIdle(unittest.TestCase):
    """A stop that races the turn's own death must not strand the 'Interrupting…' flag (the user
    2026-07-20: stop pressed one second after the turn died of its API-retry storm — the ResultMessage
    had already settled inflight to 0 and cleared _interrupted; _do_interrupt then re-latched it against
    an idle CLI, and with no turn left there was no clearing event, so an idle session wore
    'Interrupting…' until the next fed turn / the kernel's 120s cap). The completed control round-trip
    is the settle when nothing is in flight; a live turn keeps the flag for its ResultMessage."""

    def _session(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        return be, sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})

    def test_ack_with_nothing_in_flight_clears_the_flag(self):
        import asyncio
        be, s = self._session()
        class _Client:
            async def interrupt(self): pass
        s.client = _Client()
        asyncio.run(s._do_interrupt())          # inflight == 0 throughout: the turn is already over
        self.assertFalse(s._interrupted, "the ack settles the stop when there is no turn — no stranded flag")
        self.assertFalse(s.snapshot()["interrupting"], "an idle session never wears 'Interrupting…'")

    def test_failed_ack_with_nothing_in_flight_clears_too(self):
        import asyncio
        be, s = self._session()
        class _Client:
            async def interrupt(self): raise RuntimeError("no current client")
        s.client = _Client()
        asyncio.run(s._do_interrupt())          # idle-session refusal: logged, nothing to escalate...
        self.assertFalse(s._interrupted, "...and nothing left in flight → the flag settles down")

    def test_escalated_signal_on_an_idle_session_does_not_latch(self):
        import types
        be, s = self._session()
        be._session_cli_pid = lambda sess: 4242          # a CLI process exists...
        real_os = sb.os
        sb.os = types.SimpleNamespace(kill=lambda pid, sig: None)   # ...and the signal lands
        try:
            s._signal_cli(sb.signal.SIGINT, "sigint")
        finally:
            sb.os = real_os
        self.assertFalse(s._interrupted,
                         "no turn in flight → nothing this signal stops → no settle event exists to clear a latch")


class PendingQueue(unittest.TestCase):
    """The visible pending queue (no SDK / no loop needed) — enqueue holds turns in a list that
    pending_queued exposes, so the kernel can render the 'queued' indicator for SDK sessions."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def _sess(self, sid="q1"):
        s = sb.SdkSession(self.be, {"sid": sid, "name": "n", "cwd": self.d, "mode": "acceptEdits"})
        self.be.sessions[sid] = s          # register WITHOUT starting the thread (no loop)
        return s

    def test_enqueue_holds_in_pending_oldest_first(self):
        s = self._sess()
        s.enqueue("first"); s.enqueue("second")
        self.assertEqual(s.pending(), ["first", "second"])              # both held, in order
        self.assertEqual(self.be.pending_queued("q1"), ["first", "second"])

    def test_pending_returns_a_copy(self):
        s = self._sess()
        s.enqueue("a")
        snap = s.pending(); snap.append("mutated")
        self.assertEqual(s.pending(), ["a"])                            # internal list untouched

    def test_unqueue_removes_by_index_and_returns_text(self):
        # the chat's cancel affordance: pull a still-queued message back out by its position (the user 2026-06-27)
        s = self._sess("q3")
        s.enqueue("alpha"); s.enqueue("beta"); s.enqueue("gamma")
        self.assertEqual(self.be.unqueue("q3", 1), "beta")              # cancel the middle one
        self.assertEqual(s.pending(), ["alpha", "gamma"])              # the rest keep order
        self.assertIsNone(self.be.unqueue("q3", 9), "out-of-range idx is a safe no-op")
        self.assertIsNone(self.be.unqueue("no-such-sid", 0), "unknown session → None")

    def test_unqueue_clears_the_optimistic_echo(self):
        # the bug (the user 2026-06-27): canceling a queued message popped its text back to the composer but
        # it kept rendering as a solid-blue SENT bubble, because send()'s optimistic echo — which normally
        # prunes when the real user atom lands in the transcript — was never cleared for a message that never
        # lands. unqueue must drop the echo too.
        sid = "qe1"
        s = self._sess(sid)
        s.enqueue("cancel me")
        self.be._live.setdefault(sid, {})["echo:x"] = {"type": "user", "_echo_text": "cancel me",
            "message": {"role": "user", "content": [{"type": "text", "text": "cancel me"}]}}
        self.assertEqual(self.be.unqueue(sid, 0), "cancel me")
        self.assertEqual(s.pending(), [])
        self.assertFalse(any(a.get("_echo_text") == "cancel me" for a in self.be._live.get(sid, {}).values()),
                         "the optimistic echo is gone → the canceled message no longer reads as sent")

    def test_pending_queued_empty_for_unknown_or_idle(self):
        self.assertEqual(self.be.pending_queued("no-such-sid"), [])     # not an SDK session
        self._sess("q2")
        self.assertEqual(self.be.pending_queued("q2"), [])             # session exists, nothing queued

    def test_unqueue_expect_relocates_under_the_lock(self):
        # the user 2026-07-20: the kernel's drift guard located the message in a SNAPSHOT, then popped
        # by raw index — the input generator consuming entries in between could cancel the WRONG
        # message. `expect` moves the verify inside the lock: a shifted index re-locates by exact text.
        s = self._sess("qx1")
        s.enqueue("alpha"); s.enqueue("beta")
        s.unqueue(0)                                     # the queue shifts after the caller's snapshot
        self.assertEqual(self.be.unqueue("qx1", 1, "beta"), "beta", "stale idx 1 re-locates to 'beta'")
        self.assertEqual(s.pending(), [])

    def test_unqueue_expect_missing_is_a_loud_miss_not_a_wrong_pop(self):
        # the already-forwarded case (no recall exists once the CLI has the message): the exact text is
        # gone from _pending → None, and the OTHER queued message is untouched — the caller turns the
        # None into the 'too late' toast instead of the old silent fake-delete.
        s = self._sess("qx2")
        s.enqueue("survivor")
        self.assertIsNone(self.be.unqueue("qx2", 0, "already forwarded"))
        self.assertEqual(s.pending(), ["survivor"], "a miss never pops a different message")

    def test_queue_recallable_only_while_a_recall_can_win(self):
        # the ✕ affordance gate (the user 2026-07-20): during a running UN-HELD turn the input
        # generator forwards a queued send into the CLI within milliseconds — a cancel there can only
        # lose, so the bubble must not offer one. The two romp-side HOLDS (interrupt, pending rewind)
        # and the idle/connecting states keep the queue genuinely recallable.
        s = self._sess("qr1")
        self.assertTrue(self.be.queue_recallable("qr1"), "idle → entries sit in _pending, recallable")
        s.inflight = 1
        self.assertFalse(self.be.queue_recallable("qr1"), "running un-held turn → forwards instantly")
        s._interrupted = True
        self.assertTrue(self.be.queue_recallable("qr1"), "interrupt hold → the queue is romp-held")
        s._interrupted = False
        s._rewind_to = "11111111-2222-3333-4444-555555555555"
        self.assertTrue(self.be.queue_recallable("qr1"), "pending rewind holds the queue too")
        s._rewind_armed = True
        self.assertFalse(self.be.queue_recallable("qr1"), "armed rewind releases the hold")
        self.assertTrue(self.be.queue_recallable("no-such-sid"), "unknown session fails toward the ✕")


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class PendingQueueLoop(unittest.TestCase):
    """End-to-end: a turn enqueued while another is IN FLIGHT is FORWARDED to the SDK immediately
    (next-tool-boundary delivery), not held until the in-flight turn ends (the user 2026-06-27).
    It leaves romp's pending queue as soon as it's fed. (A wedged/interrupted turn still holds the
    queue — see InterruptWithQueue.)"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._orig_client = _sdk.ClaudeSDKClient
        import asyncio as _aio

        class GatedClient:
            instances = []
            received = []                  # turn texts actually fed to the SDK, in order
            release = threading.Event()    # test sets this to let the in-flight turn complete

            def __init__(self, options=None, transport=None):
                self.options = options
                self._turnq = _aio.Queue()
                GatedClient.instances.append(self)

            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

            async def query(self, prompt, session_id="default"):
                async for turn in prompt:                 # the feeder writes each released turn here
                    await self._turnq.put(turn)

            async def interrupt(self): pass
            async def set_model(self, model=None): pass
            async def set_permission_mode(self, mode): pass

            async def receive_messages(self):
                yield _sdk.SystemMessage("init", {
                    "model": "claude-x", "permissionMode": "acceptEdits",
                    "session_id": (self.options.session_id or "fsid")})
                while True:
                    turn = await self._turnq.get()
                    GatedClient.received.append(turn["message"]["content"][0]["text"])
                    while not GatedClient.release.is_set():
                        await _aio.sleep(0.01)            # hold the turn 'in flight' until released
                    GatedClient.release.clear()
                    yield _sdk.ResultMessage("success", 1, 1, False, 1, "fsid")

        _sdk.ClaudeSDKClient = GatedClient
        self.Gated = GatedClient
        GatedClient.instances = []
        GatedClient.received = []
        GatedClient.release = threading.Event()
        self.backend = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        self.Gated.release.set()                          # unblock any in-flight turn so the thread can exit
        _sdk.ClaudeSDKClient = self._orig_client

    def _wait(self, pred, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.01)
        return False

    def test_second_turn_forwarded_immediately_mid_flight(self):
        sid = self.backend.spawn("q", self.d)
        self.assertTrue(self.backend.send(sid, "A"))
        self.assertTrue(self._wait(lambda: self.Gated.received == ["A"]), "A never reached the SDK")

        # B is enqueued while A is in flight: it is FORWARDED straight to the stream — it does NOT linger in
        # romp's queue waiting for A to finish (the user 2026-06-27). pending clears immediately.
        self.assertTrue(self.backend.send(sid, "B"))
        self.assertTrue(self._wait(lambda: self.backend.pending_queued(sid) == []),
                        "B should be forwarded to the SDK at once, not held in romp's queue")

        # The mock's receiver serializes (one turn at a time, like the CLI), so B SURFACES after A is released
        # — but it was already fed. Order is preserved.
        self.Gated.release.set()
        self.assertTrue(self._wait(lambda: self.Gated.received == ["A", "B"]),
                        "B is delivered after A, in order")


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class InterruptWithQueue(unittest.TestCase):
    """Interrupt must NOT settle inflight or release the next queued turn itself — that's the
    double-count api caught (the forced interrupt-settle AND the aborted turn's ResultMessage both
    decrement, so the next turn is released early while the prior is still counted). Modeled
    deterministically with a STALLED interrupt (no ResultMessage, like InterruptSettlesStall) so
    there is no result-ordering race: the interrupted turn stays in flight, so its queued follower
    must WAIT (honest queue-pause; the CLI is stuck) rather than be force-fed into a wedged CLI.
      fix : B stays queued, session reads 'waiting' (inflight held, display only).
      bug : the forced settle frees inflight → B is popped + fed into the stuck CLI."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._orig = _sdk.ClaudeSDKClient
        import asyncio as _aio

        class StallClient:
            instances = []
            received = []                  # turn texts actually fed to the SDK, in order

            def __init__(self, options=None, transport=None):
                self.options = options
                self.interrupted = False
                self._turnq = _aio.Queue()
                StallClient.instances.append(self)

            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

            async def query(self, prompt, session_id="default"):
                async for turn in prompt:
                    await self._turnq.put(turn)

            async def interrupt(self):
                self.interrupted = True    # interrupt sent, but the wedged turn never produces a result

            async def receive_messages(self):
                yield _sdk.SystemMessage("init", {"session_id": self.options.session_id or "fsid"})
                while True:
                    turn = await self._turnq.get()
                    StallClient.received.append(turn["message"]["content"][0]["text"])
                    await _aio.sleep(3600)           # stall this turn forever (never a ResultMessage)

        _sdk.ClaudeSDKClient = StallClient
        self.Fake = StallClient
        StallClient.instances = []
        StallClient.received = []
        self.backend = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        _sdk.ClaudeSDKClient = self._orig

    def _wait(self, pred, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.01)
        return False

    def test_interrupt_holds_a_turn_queued_after_it(self):
        sid = self.backend.spawn("x", self.d)
        self.backend.send(sid, "A")
        self.assertTrue(self._wait(lambda: self.Fake.received == ["A"]), "A is in flight")

        # Interrupt the (wedged) A. Interrupt must only FLAG it — not drop inflight.
        self.assertTrue(self.backend.interrupt(sid))
        self.assertTrue(self._wait(lambda: self.backend.live_sessions().get(sid, {}).get("state") == "waiting"),
                        "interrupted turn reads 'waiting' (inflight held, _interrupted set)")

        # Mid-turn forwarding is SUSPENDED while interrupted: a turn queued AFTER the interrupt is HELD, not
        # force-fed into the stuck CLI (the double-count / wedge hazard). It releases once the wedged turn
        # settles inflight to 0 (the user 2026-06-27).
        self.backend.send(sid, "B")
        time.sleep(0.3)
        self.assertEqual(self.backend.pending_queued(sid), ["B"],
                         "B stays queued behind the interrupted turn — not fed into a stuck CLI")
        self.assertEqual(self.Fake.received, ["A"], "B was NOT fed to the SDK while interrupted")
        self.assertEqual(self.backend.live_sessions().get(sid, {}).get("state"), "waiting",
                         "still 'waiting' — inflight held, not double-counted")


@unittest.skipUnless(_HAVE_SDK, "claude_agent_sdk not installed")
class ReconnectReconcilesInflight(unittest.TestCase):
    """A reconnect abandons the previous client; a turn it left IN FLIGHT can never get its ResultMessage on
    the new connection (that client and its receive loop are gone), so inflight — and the 'working' signal it
    drives — would be stranded elevated FOREVER: the session reads 'working' indefinitely though it's idle
    (the user 2026-07-01, who started a new session and immediately switched the model, after which it
    said it was working indefinitely though it had just changed the model and was ready). request_reconnect defers
    while inflight>0, but a race (it fired at inflight==0, then the input generator started a turn before the
    teardown ran) still strands one. The reconnect must reconcile inflight to idle.
      fix : after the reconnect, inflight == 0 and the session reads 'waiting'.
      bug : inflight stays 1 across the reconnect → snapshot 'working' forever."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._orig = _sdk.ClaudeSDKClient
        import asyncio as _aio

        class StallClient:
            instances = []
            received = []                  # turn texts actually fed to the SDK, in order

            def __init__(self, options=None, transport=None):
                self.options = options
                self._turnq = _aio.Queue()
                StallClient.instances.append(self)

            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

            async def query(self, prompt, session_id="default"):
                async for turn in prompt:
                    await self._turnq.put(turn)

            async def interrupt(self): pass
            async def set_model(self, model=None): pass
            async def get_context_usage(self): return {"percentage": 2, "model": "claude-x"}

            async def receive_messages(self):
                yield _sdk.SystemMessage("init", {"session_id": self.options.session_id or "fsid"})
                while True:
                    turn = await self._turnq.get()
                    StallClient.received.append(turn["message"]["content"][0]["text"])
                    await _aio.sleep(3600)           # stall this turn forever (never a ResultMessage)

        _sdk.ClaudeSDKClient = StallClient
        self.Fake = StallClient
        StallClient.instances = []
        StallClient.received = []
        self.backend = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        _sdk.ClaudeSDKClient = self._orig

    def _wait(self, pred, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.01)
        return False

    def test_reconnect_settles_a_turn_stranded_by_the_teardown(self):
        sid = self.backend.spawn("recon", self.d)
        self.backend.send(sid, "A")                              # first turn → inflight 1, state 'working'
        self.assertTrue(self._wait(lambda: self.Fake.received == ["A"]), "A never reached the SDK (in flight)")
        s = self.backend.sessions[sid]
        self.assertEqual(s.inflight, 1)
        self.assertEqual(s.snapshot()["state"], "working", "the in-flight turn reads 'working'")
        # Force a reconnect WHILE the turn is in flight — models the race where request_reconnect fired at
        # inflight==0 but the input generator started a turn before _amain tore the client down.
        s.loop.call_soon_threadsafe(lambda: (setattr(s, "_reconnect", True), s._wake_set()))
        self.assertTrue(self._wait(lambda: len(self.Fake.instances) >= 2), "the reconnect did not rebuild the client")
        self.assertTrue(self._wait(lambda: s.inflight == 0),
                        "inflight must reconcile to 0 across the reconnect (else 'working' is stranded forever)")
        self.assertEqual(s.snapshot()["state"], "waiting",
                         "idle on the new connection reads 'waiting', not a stranded 'working'")
        self.assertEqual(sb.last_state(self.d, sid).get("state"), "waiting",
                         "the state log agrees — else snapshot's last_state branch re-reads the stale 'working'")
        self.backend.kill(sid)


class RateLimitUsageStaleness(unittest.TestCase):
    """usage.json (the /usage rail's 5h + weekly bars) is written by BOTH the tmux statusline — the CLI's
    CURRENT rate_limits handed to it every render, always fresh — and the SDK backend's _record_rate_limit,
    which is STATUS-AWARE (the user 2026-07-02): 19h of cadence instrumentation showed the CLI attaches
    `utilization` only in the allowed_warning band; `allowed` and `rejected` events carry None. The old
    util-only path dropped 97% of events — every REJECTED one included, so romp never showed the limit —
    and after an account switch nothing replaced the old account's reading. Now `status` + `resets_at`
    (always present) drive the merge: rejected=100, the event's resets_at names the LIVE window, and a
    same-window unknown utilization keeps the file's pct."""

    def _info(self, rlt, util, resets_at, status="allowed"):
        class _I:
            pass
        i = _I()
        i.rate_limit_type, i.utilization, i.resets_at, i.status = rlt, util, resets_at, status
        return i

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)

    def _usage(self):
        return json.loads((Path(self.d) / "usage.json").read_text())

    def _write_usage(self, data):
        (Path(self.d) / "usage.json").write_text(json.dumps(data))

    def test_sdk_event_does_not_clobber_the_statuslines_fresh_five_hour(self):
        now = int(time.time())
        # the tmux statusline wrote a FRESH snapshot (current rate_limits every render): 5h=69%, wk=65%.
        self._write_usage({"t": now, "five_hour": {"pct": 69, "resets_at": now + 3600},
                                       "seven_day": {"pct": 65, "resets_at": now + 500000}})
        # a seven_day RateLimitEvent fires — it touches ONLY its own window, never the five_hour.
        self.be._record_rate_limit(self._info("seven_day", 0.65, now + 500000))
        out = self._usage()
        self.assertEqual(out["five_hour"]["pct"], 69,
                         "the statusline's fresh five_hour must survive an SDK seven_day write, not be clobbered")
        self.assertEqual(out["seven_day"]["pct"], 65)

    def test_higher_pct_wins_within_the_same_window(self):
        # Same window (same resets_at): usage only climbs within a window, so the higher pct is the newer read.
        now = int(time.time())
        self._write_usage({"t": now, "five_hour": {"pct": 69, "resets_at": now + 3600}, "seven_day": None})
        self.be._record_rate_limit(self._info("five_hour", 0.10, now + 3600))   # a stale low read, same window
        self.assertEqual(self._usage()["five_hour"]["pct"], 69, "a stale lower read must not lower the bar")

    def test_a_genuinely_newer_window_replaces_an_older_one(self):
        # A later resets_at IS a newer window (the old one reset): the fresh low read is correct, take it.
        now = int(time.time())
        self._write_usage({"t": now, "five_hour": {"pct": 90, "resets_at": now + 60}, "seven_day": None})
        self.be._record_rate_limit(self._info("five_hour", 0.05, now + 5 * 3600))   # window reset → new window
        self.assertEqual(self._usage()["five_hour"]["pct"], 5, "a genuinely newer window replaces the old one")

    def test_rejected_with_null_utilization_reads_100(self):
        # The CLI attaches NO utilization to a rejected event — but rejected IS the limit. Dropping it (the
        # old util-only gate) is how the user hit the session limit without romp ever showing it.
        now = int(time.time())
        self.be._record_rate_limit(self._info("five_hour", None, now + 3600, status="rejected"))
        seg = self._usage()["five_hour"]
        self.assertEqual(seg["pct"], 100, "rejected = the window is full, even with utilization=None")
        self.assertEqual(seg["resets_at"], now + 3600)

    def test_allowed_event_unsticks_a_dead_windows_reading(self):
        # ACCOUNT SWITCH (the user 2026-07-02): the file still holds the OLD account's near-limit reading;
        # the live CLI (new account) streams allowed events with utilization=None for a DIFFERENT window.
        # The event's resets_at names the LIVE window — the dead reading is replaced, not left to win.
        now = int(time.time())
        self._write_usage({"t": now, "five_hour": {"pct": 98, "resets_at": now + 4 * 3600}, "seven_day": None})
        self.be._record_rate_limit(self._info("five_hour", None, now + 3600, status="allowed"))
        seg = self._usage()["five_hour"]
        self.assertEqual(seg["resets_at"], now + 3600, "the live CLI's window replaces the dead one — even with an EARLIER reset")
        self.assertEqual(seg["pct"], 0, "unknown usage in a fresh window reads 0 (statusline/warning events refine it)")

    def test_allowed_event_with_null_utilization_keeps_the_same_windows_pct(self):
        # Same window, no utilization → the event adds nothing about the pct; keep the file's reading AND
        # don't rewrite the file (t = the last real reading, so the tooltip's "updated … ago" stays honest).
        now = int(time.time())
        self._write_usage({"t": now - 999, "five_hour": {"pct": 42, "resets_at": now + 3600}, "seven_day": None})
        self.be._record_rate_limit(self._info("five_hour", None, now + 3600, status="allowed"))
        out = self._usage()
        self.assertEqual(out["five_hour"]["pct"], 42)
        self.assertEqual(out["t"], now - 999, "a no-op event must not refresh t")

    def test_allowed_caps_a_stale_100_at_99(self):
        # An `allowed` event PROVES the account is not limited: a same-window pct claiming 100 (an earlier
        # rejected, or a stale statusline write) is capped to 99 so the limited banner can't outlive the CLI.
        now = int(time.time())
        self._write_usage({"t": now, "five_hour": {"pct": 100, "resets_at": now + 3600}, "seven_day": None})
        self.be._record_rate_limit(self._info("five_hour", None, now + 3600, status="allowed"))
        self.assertEqual(self._usage()["five_hour"]["pct"], 99)

    def test_write_failure_is_logged_not_swallowed(self):
        # the user 2026-07-02 (who suspected an error somewhere was being swallowed): a failed
        # usage.json write must land in the backend log, never a bare `except: pass`.
        now = int(time.time())
        self.be.state_dir = Path(self.d) / "gone" / "deeper"   # no parent → the tmp write raises
        self.be._record_rate_limit(self._info("five_hour", 0.5, now + 3600))
        self.assertTrue(any("usage.json write failed" in str(m) for m in self.logs),
                        "the write failure is logged")

    def test_fable_window_lands_in_its_own_key(self):
        # `seven_day_overage_included` = the included Fable 5 weekly allowance (Claude Code /usage labels it
        # "Fable 5 limit", the user 2026-07-02) -> usage.json `fable`, the rail's third bar. It must not
        # touch the 5h/weekly windows, and they must not clobber it.
        now = int(time.time())
        self._write_usage({"t": now, "five_hour": {"pct": 21, "resets_at": now + 3600}, "seven_day": None})
        self.be._record_rate_limit(self._info("seven_day_overage_included", 0.34, now + 500000))
        out = self._usage()
        self.assertEqual(out["fable"], {"pct": 34, "resets_at": now + 500000})
        self.assertEqual(out["five_hour"]["pct"], 21, "the 5h window is untouched")
        self.be._record_rate_limit(self._info("five_hour", 0.5, now + 3600))
        self.assertEqual(self._usage()["fable"]["pct"], 34, "a 5h event must not clobber the fable window")

    def test_rejected_fable_window_reads_100(self):
        now = int(time.time())
        self.be._record_rate_limit(self._info("seven_day_overage_included", None, now + 500000, status="rejected"))
        self.assertEqual(self._usage()["fable"]["pct"], 100, "hitting the Fable 5 limit shows, even util-less")

    def test_get_usage_snapshot_maps_all_three_windows_exactly(self):
        # get_usage (a CLI control request the SDK doesn't wrap; found 2026-07-02) returns the /usage
        # screen's own limits[] with an EXACT percent per window — the ONLY in-band source for the
        # Fable-scoped weekly, and exact numbers below the warning band for the others.
        snap = {"rate_limits": {"limits": [
            {"kind": "session", "percent": 54, "resets_at": "2026-07-03T00:59:59+00:00"},
            {"kind": "weekly_all", "percent": 39, "resets_at": "2026-07-06T18:59:59+00:00"},
            {"kind": "weekly_scoped", "percent": 45, "resets_at": "2026-07-06T18:59:59+00:00",
             "scope": {"model": {"id": None, "display_name": "Fable"}}},
        ]}}
        self.be._record_usage_snapshot(snap)
        out = self._usage()
        self.assertEqual(out["five_hour"]["pct"], 54)
        self.assertEqual(out["seven_day"]["pct"], 39)
        self.assertEqual(out["fable"]["pct"], 45, "the Fable-scoped weekly lands as the third bar")
        self.assertIsInstance(out["fable"]["resets_at"], int, "ISO resets_at is stored as an epoch")

    def test_get_usage_snapshot_is_authoritative_but_preserves_unmapped_windows(self):
        now = int(time.time())
        self._write_usage({"t": now - 500, "five_hour": {"pct": 98, "resets_at": now + 60},
                           "seven_day": None, "fable": {"pct": 7, "resets_at": now + 500000}})
        self.be._record_usage_snapshot({"rate_limits": {"limits": [
            {"kind": "session", "percent": 12, "resets_at": now + 3600}]}})
        out = self._usage()
        self.assertEqual(out["five_hour"], {"pct": 12, "resets_at": now + 3600},
                         "a full-snapshot window OVERWRITES — no max-merge games for the exact source")
        self.assertEqual(out["fable"]["pct"], 7, "a window the snapshot lacks keeps its file value")

    def test_get_usage_snapshot_no_change_no_rewrite(self):
        now = int(time.time())
        self._write_usage({"t": now - 999, "five_hour": {"pct": 12, "resets_at": now + 3600},
                           "seven_day": None, "fable": None})
        self.be._record_usage_snapshot({"rate_limits": {"limits": [
            {"kind": "session", "percent": 12, "resets_at": now + 3600}]}})
        self.assertEqual(self._usage()["t"], now - 999, "an identical snapshot must not refresh t")

    def test_turn_end_schedules_the_usage_refresh(self):
        import inspect
        src = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn("asyncio.ensure_future(self._do_refresh_usage())", src,
                      "every turn end refreshes the exact /usage snapshot (event-based)")
        q = inspect.getsource(sb.SdkSession._do_refresh_usage)
        self.assertIn('_send_control_request({"subtype": "get_usage"})', q,
                      "the designed CLI control request, via the SDK transport")

    def test_on_message_routes_rate_limit_events_to_the_recorder(self):
        s = sb.SdkSession(self.be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": self.d})

        class _Msg:            # a duck-typed RateLimitEvent (the branch keys off `rate_limit_info`)
            pass
        m = _Msg()
        m.rate_limit_info = self._info("five_hour", 0.42, int(time.time()) + 3600)

        class _T:              # dummy type classes — the msg is none of Assistant/Result/System
            pass
        s._on_message(m, _T, _T, _T)
        self.assertEqual(self._usage()["five_hour"]["pct"], 42, "the event landed in usage.json")


class TurnStateIsEventNotCount(unittest.TestCase):
    """The working signal is an EVENT (the CLI's ResultMessage), never a feed-vs-result COUNT. A
    ResultMessage settles the session to idle in ONE step, no matter how many messages were forwarded
    into the turn — the phantom-working fix (the user 2026-07-09): a mid-turn forward did inflight += 1
    but the CLI emits ONE Result for the merged turn, so the old `inflight -= 1; if 0:` guard never ran
    the settle and the session read 'working' forever."""

    def _sess(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})
        return be, s

    def _tail(self, be, s):
        return sb.last_state_value(Path(be.state_dir), s.sid)

    def _result(self, s):
        import asyncio

        async def run():
            s._on_message(_ResultMessage(), _AssistantMessage, _ResultMessage, type("S", (), {}))
            await asyncio.sleep(0)
        asyncio.run(run())

    def test_a_single_result_settles_waiting_from_any_inflight(self):
        # inflight 2 = a message forwarded MID-TURN (each feed += 1); the CLI folds both into one turn and
        # emits ONE Result. The old guard left inflight at 1 → no 'waiting', phantom-working forever.
        for n in (1, 2, 3):
            be, s = self._sess()
            sb.append_state(Path(be.state_dir), s.sid, "working")
            s.inflight = n
            s._cli_working = True
            self._result(s)
            self.assertEqual(self._tail(be, s), "waiting",
                             f"one Result settles idle from inflight={n} — never gated on a count")
            self.assertEqual(s.inflight, 0, "a Result drains the CLI in one step, not a decrement")
            self.assertFalse(s._cli_working, "the settle clears the busy flag")

    def test_result_retires_unlanded_work_atoms_from_high_inflight(self):
        # the retire that the phantom-working session never got: a forwarded-then-merged turn (inflight 2)
        # whose stream atom never landed on disk must still drop at the Result.
        be, s = self._sess()
        be._live[s.sid] = {"w1": {"uuid": "w1", "t": 5},
                           "echo:hi": {"uuid": "echo:hi", "t": 1, "_echo_text": "hi"}}
        s.inflight = 2
        self._result(s)
        self.assertEqual(set(be._live.get(s.sid, {})), {"echo:hi"},
                         "unlanded WORK atom drops even from inflight>1; the echo survives")

    def test_stream_work_atom_reasserts_working_after_a_premature_settle(self):
        # If a state write ever settles ahead of real output (a separate turn queued in the CLI that
        # streams after the previous Result), the live stream is the truth: a genuine work atom re-stamps
        # 'working'. This is what makes the signal self-heal without a counter.
        be, s = self._sess()
        s._mark("waiting")
        self.assertFalse(s._cli_working)
        be._forward(s, _AssistantMessage([_TextBlock("more work")], uuid="w9"))
        self.assertEqual(self._tail(be, s), "working", "a streamed work atom re-asserts working")
        self.assertTrue(s._cli_working)

    def test_stream_reassert_is_transition_only_and_skips_echo_and_command(self):
        be, s = self._sess()
        statef = Path(be.state_dir) / "states" / (s.sid + ".jsonl")
        s._mark("working")                                   # already working
        before = statef.read_text().count("\n")
        be._forward(s, _AssistantMessage([_TextBlock("x")], uuid="w1"))   # already working → no new stamp
        after = statef.read_text().count("\n")
        self.assertEqual(before, after, "no redundant 'working' stamp while already working")
        # A command line (e.g. a streamed /model confirmation) is NOT active work → never re-asserts.
        s._mark("waiting")
        cmd = _UserMessage([_TextBlock("<command-name>/model</command-name>\n"
                                       "<command-message>model</command-message>\n"
                                       "<command-args>sonnet</command-args>")], uuid="c1")
        be._forward(s, cmd)
        self.assertEqual(self._tail(be, s), "waiting", "a command atom must not re-assert working")
        self.assertFalse(s._cli_working)

    def test_mark_tracks_the_busy_flag(self):
        be, s = self._sess()
        s._mark("working");    self.assertTrue(s._cli_working)
        s._mark("permission"); self.assertFalse(s._cli_working, "a non-working state clears the flag")
        s._mark("working");    self.assertTrue(s._cli_working)
        s._mark("waiting");    self.assertFalse(s._cli_working)


class BgTaskLifecycle(unittest.TestCase):
    """The CLI's DESIGNED background-task lifecycle stream (system/task_started → task_progress* →
    task_updated/task_notification) feeds SdkSession._bg_tasks — the live 'what's running in the
    background' set that lets an idle session waiting on a timer/watcher read AWAITING instead of plain
    idle (the user 2026-07-11: nimbus's 20-minute campaign timer). Terminal statuses clear from EITHER
    terminal message kind (a TaskStop can suppress the notification); a progress event self-heals an
    unknown id, so a backend that attached mid-task still converges."""

    class _Sys:                                   # stand-in for SystemMessage (isinstance + subtype + data)
        def __init__(self, subtype, data):
            self.subtype = subtype
            self.data = data

    def _sess(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        return sb.SdkSession(be, {"sid": "11111111-2222-3333-4444-555555555555", "name": "n", "cwd": "/tmp"})

    def _feed(self, s, subtype, data):
        s._on_message(self._Sys(subtype, data), _AssistantMessage, _ResultMessage, self._Sys)

    def test_task_events_mirror_the_live_set_to_the_reg(self):
        # the in-memory set dies with the backend; the reg mirror is what lets the NEXT boot's
        # reconcile tell the session its tasks were killed (the user 2026-07-11). A normal task end
        # clears the mirror too, so it never false-alarms.
        s = self._sess()
        self._feed(s, "task_started", {"task_id": "b1", "description": "watcher"})
        reg = sb.read_reg(s.backend.state_dir, s.sid)
        self.assertEqual([t["desc"] for t in reg["bgTasks"]], ["watcher"])
        self._feed(s, "task_notification", {"task_id": "b1", "status": "completed",
                                            "output_file": "/tmp/x.output", "summary": "done"})
        self.assertEqual(sb.read_reg(s.backend.state_dir, s.sid)["bgTasks"], [])

    def test_session_gone_with_live_tasks_enqueues_the_death_notice_and_wakes(self):
        # the CLI died while the kernel lives: its bg tasks died with it — the session must HEAR that
        # (else it waits forever on a dead timer) and get woken to act on it. Our own drain (`ended`)
        # skips: the mirror survives for the next boot's reconcile instead.
        s = self._sess()
        be = s.backend
        self._feed(s, "task_started", {"task_id": "b1", "description": "power watcher"})
        be._ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (be._ensured.append(sid), on_boot_settled and on_boot_settled())
        be._on_session_gone(s)
        reg = sb.read_reg(be.state_dir, s.sid)
        self.assertEqual(len(reg["queue"]), 1)
        self.assertIn("power watcher", reg["queue"][0])
        self.assertIn("romp-system", reg["queue"][0])
        self.assertEqual(reg["bgTasks"], [], "reported — never re-notify for the same deaths")
        self.assertEqual(be._ensured, [s.sid], "the session is woken to hear it")
        # drain/shutdown (`ended`): no notice now — the mirror stays for the next boot's reconcile
        s2 = self._sess()
        self._feed(s2, "task_started", {"task_id": "b2", "description": "timer"})
        s2.ended = True
        be2 = s2.backend
        be2._ensured = []
        be2._ensure = lambda sid, on_boot_settled=None: (be2._ensured.append(sid), on_boot_settled and on_boot_settled())
        be2._on_session_gone(s2)
        self.assertEqual(be2._ensured, [])
        self.assertEqual([t["desc"] for t in sb.read_reg(be2.state_dir, s2.sid)["bgTasks"]], ["timer"])

    def test_construction_heals_stranded_pending_switch_flags(self):
        # a pending /model / /effort switch that died with the previous process must not strand the
        # switching-dots (the user 2026-07-11): a fresh construction applies both at its next connect,
        # so pending is over — heal the reg at __init__.
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-555555555555"
        sb.write_reg(be.state_dir, sid, {"sid": sid, "name": "n", "cwd": "/tmp", "alive": True,
                                         "effortPending": True, "modelPending": True})
        sb.SdkSession(be, sb.read_reg(be.state_dir, sid))
        reg = sb.read_reg(be.state_dir, sid)
        self.assertFalse(reg.get("effortPending"))
        self.assertFalse(reg.get("modelPending"))

    def test_started_tracks_the_task_and_the_snapshot_carries_it(self):
        s = self._sess()
        self._feed(s, "task_started", {"task_id": "b1", "description": "20-minute timer for campaign-start check",
                                       "task_type": "local_bash", "tool_use_id": "tu_1"})
        snap = s.snapshot()["bgTasks"]
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["desc"], "20-minute timer for campaign-start check")
        self.assertEqual(snap[0]["toolUseId"], "tu_1", "carries the tool_use id — the join to the box's scan rows")

    def test_notification_always_ends_its_task(self):
        s = self._sess()
        self._feed(s, "task_started", {"task_id": "b1", "description": "watcher"})
        self._feed(s, "task_notification", {"task_id": "b1", "status": "completed",
                                            "output_file": "/tmp/x.output", "summary": "done"})
        self.assertEqual(s.snapshot()["bgTasks"], [])

    def test_updated_ends_only_on_a_terminal_patch_status(self):
        s = self._sess()
        self._feed(s, "task_started", {"task_id": "b1", "description": "watcher"})
        self._feed(s, "task_updated", {"task_id": "b1", "patch": {"end_time": 123}})   # no status → NOT terminal
        self.assertEqual(len(s.snapshot()["bgTasks"]), 1, "a status-less patch is not a terminal event")
        for status in ("killed", "failed", "stopped", "completed"):
            self._feed(s, "task_started", {"task_id": "t_" + status, "description": "x"})
            self._feed(s, "task_updated", {"task_id": "t_" + status, "patch": {"status": status}})
        self.assertEqual([t["desc"] for t in s.snapshot()["bgTasks"]], ["watcher"],
                         "every terminal status clears its task; the untouched one survives")

    def test_progress_self_heals_an_unknown_task_and_refreshes_fields(self):
        s = self._sess()
        self._feed(s, "task_progress", {"task_id": "b9", "description": "long build",
                                        "usage": {}, "last_tool_name": "Bash"})
        snap = s.snapshot()["bgTasks"]
        self.assertEqual((snap[0]["desc"], snap[0]["lastTool"]), ("long build", "Bash"),
                         "a progress event for an id we never saw ADDS it (mid-task attach converges)")

    def test_a_task_id_less_event_is_ignored(self):
        s = self._sess()
        self._feed(s, "task_started", {"description": "no id"})
        self.assertEqual(s.snapshot()["bgTasks"], [])


class LiveAtomKinds(unittest.TestCase):
    """live_atom_kinds — the read-only DEBUG summary the kernel's chat-divergence tripwire logs (the
    2026-07-25 stale-"running" chat: the live atoms that held the turn open were cleared by a restart
    before anyone could read them; this accessor is how the tripwire captures them in time)."""

    def test_summarizes_live_atoms_without_mutating(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        sid = "11111111-2222-3333-4444-555555555555"
        self.assertEqual(be.live_atom_kinds(sid), [], "no live tail → empty")
        be._live[sid] = {
            "k1": {"uuid": "u1", "type": "assistant", "t": 10,
                   "message": {"content": [{"type": "text", "text": "streamed reply"}]}},
            "k2": {"uuid": "u2", "type": "user", "t": 11, "_echo_text": "hi",
                   "message": {"content": "hi"}},
            "k3": "not-a-dict"}
        got = {a["uuid"]: a for a in be.live_atom_kinds(sid)}
        self.assertEqual(set(got), {"u1", "u2"}, "non-dict entries are skipped, atoms summarized")
        self.assertEqual((got["u1"]["type"], got["u1"]["hasText"], got["u1"]["echo"]),
                         ("assistant", True, False))
        self.assertTrue(got["u2"]["echo"])
        self.assertEqual(len(be._live[sid]), 3, "read-only: the live tail is untouched")


class RegListCache(unittest.TestCase):
    """list_regs' per-file parse cache (the 2026-08-10 CPU fix): the registry keeps a reg for every
    session EVER created, and list_regs sits on the liveness snapshot the kernel takes several times
    a second — re-parsing hundreds of unchanged files per sweep was a measured slice of the pusher's
    CPU burn. The cache keys on (mtime_ns, size, inode) — the file stays the truth — and hands out
    copies, so the _update_reg read-modify-write pattern can never corrupt it."""

    def setUp(self):
        self.sd = Path(tempfile.mkdtemp())
        (self.sd / "sdk").mkdir()
        sb.write_reg(self.sd, "aaaa", {"sid": "aaaa", "alive": True, "name": "web"})
        sb.write_reg(self.sd, "bbbb", {"sid": "bbbb", "alive": False, "name": "api"})

    def test_unchanged_files_serve_cached_parses_with_equal_content(self):
        first = sorted(sb.list_regs(self.sd), key=lambda r: r["sid"])
        second = sorted(sb.list_regs(self.sd), key=lambda r: r["sid"])
        self.assertEqual(first, second)
        self.assertEqual([r["sid"] for r in first], ["aaaa", "bbbb"])

    def test_a_mutated_result_never_leaks_into_the_cache(self):
        one = next(r for r in sb.list_regs(self.sd) if r["sid"] == "aaaa")
        one["name"] = "clobbered"          # the _update_reg pattern mutates its read
        again = next(r for r in sb.list_regs(self.sd) if r["sid"] == "aaaa")
        self.assertEqual(again["name"], "web", "the cache hands out copies, never its own dict")

    def test_a_rewrite_is_picked_up(self):
        # write_reg replaces the file (new inode), so even a same-instant rewrite misses the cache
        sb.list_regs(self.sd)              # warm
        sb.write_reg(self.sd, "aaaa", {"sid": "aaaa", "alive": True, "name": "renamed"})
        got = next(r for r in sb.list_regs(self.sd) if r["sid"] == "aaaa")
        self.assertEqual(got["name"], "renamed")

    def test_a_deleted_reg_drops_out(self):
        sb.list_regs(self.sd)              # warm
        (self.sd / "sdk" / "bbbb.json").unlink()
        self.assertEqual([r["sid"] for r in sb.list_regs(self.sd)], ["aaaa"])


class PushSessionCallback(unittest.TestCase):
    """_push_session — the connect handshake's targeted one-session push (2026-08-10). The handshake is
    the exact event the kernel's opening chip stands down on, and a plain pusher wake left that flip
    riding the next FULL push cycle (seconds on a busy fleet) — so the flip pushes its one session
    directly. Threaded, because the callback builds+serializes a payload and must never run on the
    session's asyncio loop thread."""

    SID = "11111111-2222-3333-4444-555555555555"

    def test_threaded_callback_receives_the_sid(self):
        got, done = [], threading.Event()
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None,
                           push_session=lambda sid: (got.append(sid), done.set()))
        be._push_session(self.SID)
        self.assertTrue(done.wait(5), "the callback fires, on its own thread")
        self.assertEqual(got, [self.SID])

    def test_without_the_callback_it_falls_back_to_the_pusher_wake(self):
        # an older kernel (or a test) that didn't wire push_session still gets the pre-existing
        # behavior: the periodic pusher wake, never a silent no-op
        woke = []
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None,
                           push=lambda: woke.append(True))
        be._push_session(self.SID)
        self.assertEqual(woke, [True])

    def test_a_failing_callback_is_contained_and_logged(self):
        logs = []   # the ctor may log too (a missing-SDK note) — poll for THIS failure's line

        def boom(sid):
            raise RuntimeError("nope")

        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None,
                           push_session=boom, log=logs.append)
        be._push_session(self.SID)   # must not raise into the caller (the connect path)
        deadline = time.time() + 5
        while time.time() < deadline and not any("session push" in str(m) for m in logs):
            time.sleep(0.01)
        self.assertTrue(any("session push" in str(m) for m in logs),
                        "the failure is reported, not swallowed: %r" % logs)


if __name__ == "__main__":
    unittest.main()
