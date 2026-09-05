"""Subagent transcripts (plans/subagent-transcripts.md, 2026-09-05): the whole conversation of any
Agent/Task subagent is openable from the dashboard, live and after.

Kernel half, pinned here:
  - discovery + join: the subagents/ sidecar map (toolUseId -> agentId, dir-mtime cached) and the
    launch ack's toolUseResult.agentId both name the agent on the parent's Agent tool event;
  - the Agent tool event carries toolUseId + agentId; a RUNNING background agent drops its launch-ack
    output and takes agentRunning + the agentGist preview (last 3 tool calls, count, stamps); a landed
    <task-notification> puts its <result> in the event's output (the report fold) and the gist goes;
  - build_subagent renders the agent's own file through build_session's pipeline (sidechain mode), a
    capped tail with `truncated`, `running` from the parent's pairing + the live sets, and a LOUD
    error for a missing file;
  - openSubagent / closeSubagent register a viewer on the client; the pusher pass re-sends only when
    the file changed, and stops after close;
  - a bg-tasks row carries agentId only when the launch is an agent;
  - the chat fold never seals a running agent's launch turn, and folds byte-identically to a full build.
Synthetic data only: placeholder ids, TESTHOST-free paths, the notes-api demo world."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = SourceFileLoader("romp_kernel_subagents", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd
em = km.em

SID = "11111111-2222-3333-4444-555555555555"
AID_BG = "a1111111111111111"      # the background agent
AID_FG = "a2222222222222222"      # the foreground agent
TU_BG = "toolu_bg_0001"
TU_FG = "toolu_fg_0001"
NOW = int(time.time())
T0 = NOW - 600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def urec(t, uuid, content, parent=None, **extra):
    r = {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": iso(t), "sessionId": SID,
         "cwd": "/tmp/notes-api", "version": "2.1.0", "gitBranch": "main",
         "message": {"role": "user", "content": content}}
    r.update(extra)
    return r


def arec(t, uuid, blocks, parent, stop="end_turn"):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": iso(t), "sessionId": SID,
            "cwd": "/tmp/notes-api", "version": "2.1.0", "gitBranch": "main",
            "message": {"role": "assistant", "model": "claude-opus-5", "content": blocks, "stop_reason": stop}}


def tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid, content, is_error=False):
    return {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}


def notif_text(tid, status, result, aid=AID_BG):
    return ("<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>%s</tool-use-id>\n"
            "<output-file>/tmp/claude-1000/-tmp-notes-api/%s/tasks/%s.output</output-file>\n"
            "<status>%s</status>\n<summary>Agent \"check the api tests\" came to rest</summary>\n"
            "<result>%s</result>\n</task-notification>" % (aid, tid, SID, aid, status, result))


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


def append_jsonl(path, rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.utime(path, None)


class World(unittest.TestCase):
    """A notes-api session `web` whose transcript launches one BACKGROUND agent (its ack is async) and one
    FOREGROUND agent (its tool_result is the report), each with its own file under subagents/."""

    def setUp(self):
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        self.cwd = os.path.realpath(os.path.join(self._td, "notes-api"))
        os.makedirs(self.cwd, exist_ok=True)
        self.proj = jd._proj_dir(self.cwd)
        self.proj.mkdir(parents=True)
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / SID).write_text("web\t" + self.cwd)
        self.tpath = self.proj / (SID + ".jsonl")
        self.subdir = self.proj / SID / "subagents"
        self.subdir.mkdir(parents=True)
        # ── the parent transcript: turn 1 launches the background agent, turn 2 runs a foreground one
        ack = {"isAsync": True, "status": "async_launched", "agentId": AID_BG, "description": "check the api tests",
               "outputFile": "/tmp/claude-1000/-tmp-notes-api/%s/tasks/%s.output" % (SID, AID_BG),
               "taskType": "local_agent"}
        self.parent = [
            urec(T0, "u1", "check the api tests in the background, then tidy the readme"),
            arec(T0 + 2, "a1", [{"type": "text", "text": "Dispatching an agent for the tests."},
                                tool_use(TU_BG, "Agent", {"description": "check the api tests",
                                                          "prompt": "Run the notes-api test suite and report failures.",
                                                          "subagent_type": "general-purpose", "run_in_background": True})],
                 "u1", stop="tool_use"),
            urec(T0 + 3, "r1", [tool_result(TU_BG, "Async agent launched. Agent ID: %s. Output file: %s"
                                            % (AID_BG, ack["outputFile"]))], "a1", toolUseResult=ack),
            arec(T0 + 4, "a2", [{"type": "text", "text": "The agent is running; I'll tidy the readme meanwhile."}], "r1"),
            urec(T0 + 60, "u2", "now have a subagent summarise the readme", "a2"),
            arec(T0 + 62, "a3", [tool_use(TU_FG, "Agent", {"description": "summarise the readme",
                                                           "prompt": "Read README.md and summarise it in three lines.",
                                                           "subagent_type": "general-purpose"})], "u2", stop="tool_use"),
            urec(T0 + 90, "r3", [tool_result(TU_FG, "README summary: the notes-api serves notes over HTTP.")], "a3",
                 toolUseResult={"agentId": AID_FG, "content": [{"type": "text", "text": "README summary…"}],
                                "totalDurationMs": 28000}),
            arec(T0 + 92, "a4", [{"type": "text", "text": "Summary in hand."}], "r3"),
        ]
        write_jsonl(self.tpath, self.parent)
        self.last = "a4"                 # the transcript's leaf uuid — every append chains onto it (a null
        #                                  parent would read as a /clear fork and drop the earlier turns)
        # ── sidecars + agent files
        (self.subdir / ("agent-%s.meta.json" % AID_BG)).write_text(json.dumps(
            {"agentType": "general-purpose", "description": "check the api tests", "spawnDepth": 1, "toolUseId": TU_BG}))
        (self.subdir / ("agent-%s.meta.json" % AID_FG)).write_text(json.dumps(
            {"agentType": "general-purpose", "description": "summarise the readme", "spawnDepth": 1, "toolUseId": TU_FG}))
        self.bg_file = self.subdir / ("agent-%s.jsonl" % AID_BG)
        self.fg_file = self.subdir / ("agent-%s.jsonl" % AID_FG)
        write_jsonl(self.bg_file, self._agent_records(AID_BG, T0 + 5, [
            ("Read", {"file_path": "/tmp/notes-api/tests/test_api.py"}, "def test_list(): ..."),
            ("Bash", {"command": "uv run pytest -q tests/", "description": "run the api tests"}, "3 passed, 1 failed"),
            ("Grep", {"pattern": "def test_", "path": "/tmp/notes-api/tests"}, "tests/test_api.py:1"),
            ("Read", {"file_path": "/tmp/notes-api/api/notes.py"}, "def list_notes(): ..."),
        ]))
        write_jsonl(self.fg_file, self._agent_records(AID_FG, T0 + 63, [
            ("Read", {"file_path": "/tmp/notes-api/README.md"}, "# notes-api\n…"),
        ], closing="README summary: the notes-api serves notes over HTTP."))
        self.tm = {}                     # dormant: no live CLI snapshot → the spawned-at ghost gate
        # an SDK snapshot as the status build reads it (model/effort/context keys), for the Liveness tests
        self.live = {"state": "waiting", "since": T0, "model": "", "effort": "", "context": None,
                     "compactPct": None, "color": None, "backend": "sdk"}
        km._tmux_sessions = lambda: self.tm
        for cache in (km._chat_fold, km._parse_cache, km._SUBAGENT_META_CACHE, km._AGENT_GIST_CACHE,
                      km._AGENT_LAUNCH_CACHE, km._SUBAGENT_FRAMES, km._bgtasks_cache, km._bgall_cache):
            cache.clear()

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _agent_records(self, aid, t, calls, closing="All four tests pass now; one flaky case was re-run."):
        recs = [dict(urec(t, "%s-u0" % aid, "Do the delegated work.", None), isSidechain=True, agentId=aid)]
        prev = "%s-u0" % aid
        for i, (name, inp, out) in enumerate(calls):
            a = "%s-a%d" % (aid, i)
            r = "%s-r%d" % (aid, i)
            recs.append(dict(arec(t + 2 * i + 1, a, [tool_use("%s-tu%d" % (aid, i), name, inp)], prev, stop="tool_use"),
                             isSidechain=True, agentId=aid))
            recs.append(dict(urec(t + 2 * i + 2, r, [tool_result("%s-tu%d" % (aid, i), out)], a), isSidechain=True, agentId=aid))
            prev = r
        recs.append(dict(arec(t + 2 * len(calls) + 3, "%s-fin" % aid, [{"type": "text", "text": closing}], prev),
                         isSidechain=True, agentId=aid))
        return recs

    def _events(self):
        m = km.build_session(SID, NOW, self.tm)
        self.assertIsNotNone(m)
        return m["events"]

    def _agent_events(self):
        return {e["toolUseId"]: e for e in self._events() if e.get("kind") == "tool" and e.get("name") == "Agent"}

    def _land_notification(self, status="completed", result="All four tests pass now; one flaky case was re-run."):
        append_jsonl(self.tpath, [urec(T0 + 400, "n1", notif_text(TU_BG, status, result), self.last)])
        self.last = "n1"

    def _follow_ups(self, n):
        """n small complete turns appended after the launch, chained onto the leaf."""
        for i in range(n):
            u, a = "u%d" % (10 + i), "a%d" % (10 + i)
            append_jsonl(self.tpath, [urec(T0 + 300 + 10 * i, u, "small follow-up %d" % i, self.last),
                                      arec(T0 + 302 + 10 * i, a, [{"type": "text", "text": "ok %d" % i}], u)])
            self.last = a


class DiscoveryAndJoin(World):
    def test_sidecar_map_joins_tool_use_id_to_agent_id_and_is_cached_on_the_dir_mtime(self):
        m = km._subagent_meta_map(str(self.tpath))
        self.assertEqual(m[TU_BG]["agentId"], AID_BG)
        self.assertEqual(m[TU_BG]["agentType"], "general-purpose")
        self.assertEqual(m[TU_BG]["description"], "check the api tests")
        self.assertEqual(m[TU_FG]["agentId"], AID_FG)
        self.assertIs(km._subagent_meta_map(str(self.tpath)), m, "an unchanged directory serves the cached map")
        # a THIRD sidecar lands: the directory's mtime moves and the map re-reads (event-based, no timer)
        (self.subdir / "agent-a3333333333333333.meta.json").write_text(json.dumps({"toolUseId": "toolu_x", "agentType": "Explore"}))
        os.utime(self.subdir, (time.time() + 5, time.time() + 5))
        m2 = km._subagent_meta_map(str(self.tpath))
        self.assertEqual(m2["toolu_x"]["agentId"], "a3333333333333333")
        # a malformed name or a non-agent id never enters the map
        self.assertNotIn("", m2)
        self.assertEqual(km._subagent_meta_map(str(self.proj / "nope.jsonl")), {}, "no subagents dir → empty, no raise")

    def test_the_agent_tool_event_carries_the_block_id_and_the_agent_id_from_both_sources(self):
        evs = self._agent_events()
        self.assertEqual(set(evs), {TU_BG, TU_FG}, "toolUseId is the tool_use BLOCK id, distinct from the record uuid")
        self.assertEqual(evs[TU_BG]["uuid"], "a1", "uuid stays the record's")
        self.assertEqual(evs[TU_BG]["agentId"], AID_BG)
        self.assertEqual(evs[TU_FG]["agentId"], AID_FG)
        # the join survives the sidecar going missing: the ack's toolUseResult.agentId is the second source
        (self.subdir / ("agent-%s.meta.json" % AID_FG)).unlink()
        os.utime(self.subdir, (time.time() + 5, time.time() + 5))
        km._parse_cache.clear(); km._chat_fold.clear()
        self.assertEqual(self._agent_events()[TU_FG]["agentId"], AID_FG)

    def test_every_tool_event_carries_its_block_id(self):
        # the join key is generic — nothing else on the wire named the tool_use block
        self.assertTrue(all(e.get("toolUseId") for e in self._events() if e.get("kind") == "tool"))


class RunningPreview(World):
    def test_a_running_background_agent_drops_the_ack_and_shows_the_last_three_calls(self):
        ev = self._agent_events()[TU_BG]
        self.assertTrue(ev.get("agentAsync"))
        self.assertTrue(ev.get("agentRunning"))
        self.assertEqual(ev["output"], "", "the launch ack is not a report — an empty output reads as running")
        self.assertFalse(ev["isError"])
        g = ev["agentGist"]
        self.assertEqual(g["calls"], 4)
        self.assertEqual([r["tool"] for r in g["recent"]], ["Bash", "Grep", "Read"], "the last 3, newest LAST")
        self.assertEqual(g["recent"][0]["desc"], "run the api tests", "input.description wins")
        self.assertEqual(g["recent"][1]["desc"], "def test_", "else the pattern")
        self.assertEqual(g["recent"][2]["desc"], "/tmp/notes-api/api/notes.py", "else the file path")
        self.assertEqual(g["since"], iso(T0 + 5))
        self.assertEqual(g["last"], iso(T0 + 5 + 2 * 4 + 3))
        self.assertTrue(all(r.get("ts") for r in g["recent"]))

    def test_the_preview_follows_the_agent_file_as_it_grows(self):
        self._agent_events()
        append_jsonl(self.bg_file, [dict(arec(T0 + 40, "%s-a9" % AID_BG,
                                              [tool_use("%s-tu9" % AID_BG, "Edit", {"file_path": "/tmp/notes-api/api/notes.py"})],
                                              "%s-fin" % AID_BG, stop="tool_use"), isSidechain=True, agentId=AID_BG)])
        km._parse_cache.clear(); km._chat_fold.clear()
        g = self._agent_events()[TU_BG]["agentGist"]
        self.assertEqual(g["calls"], 5)
        self.assertEqual(g["recent"][-1]["tool"], "Edit")

    def test_a_foreground_agent_keeps_its_report_and_shows_no_preview(self):
        ev = self._agent_events()[TU_FG]
        self.assertNotIn("agentGist", ev)
        self.assertNotIn("agentRunning", ev)
        self.assertNotIn("agentAsync", ev)
        self.assertIn("README summary", ev["output"], "the sync tool_result IS the report")

    def test_the_gist_desc_rule_is_the_head_vocabulary(self):
        self.assertEqual(km._tool_gist_desc("Bash", {"command": "ls -la\nwc -l", "description": "list files"}), "list files")
        self.assertEqual(km._tool_gist_desc("Bash", {"command": "ls -la\nwc -l"}), "ls -la")
        self.assertEqual(km._tool_gist_desc("Edit", {"file_path": "/tmp/notes-api/x.py", "old_string": "a"}), "/tmp/notes-api/x.py")
        self.assertEqual(km._tool_gist_desc("Read", "not a dict"), "")
        self.assertEqual(len(km._tool_gist_desc("Bash", {"command": "x" * 500})), 80, "clipped")


class LandedReport(World):
    def test_the_landed_notification_becomes_the_report_and_the_preview_goes(self):
        self._land_notification()
        km._parse_cache.clear(); km._chat_fold.clear()
        ev = self._agent_events()[TU_BG]
        self.assertEqual(ev["output"], "All four tests pass now; one flaky case was re-run.",
                         "the <result> text replaces the launch ack in the report fold")
        self.assertFalse(ev["isError"])
        self.assertNotIn("agentGist", ev)
        self.assertNotIn("agentRunning", ev)
        self.assertEqual(ev["agentId"], AID_BG, "the join stays after the report lands")
        # the task-notification notice card in the transcript is untouched: the user turn still carries it
        notes = [e for e in self._events() if e.get("kind") == "user" and any("<result>" in r for r in (e.get("reminders") or []))]
        self.assertEqual(len(notes), 1)

    def test_a_failed_notification_marks_the_report_as_an_error(self):
        self._land_notification(status="failed", result="The suite could not start: missing dependency.")
        km._parse_cache.clear(); km._chat_fold.clear()
        ev = self._agent_events()[TU_BG]
        self.assertTrue(ev["isError"])
        self.assertIn("missing dependency", ev["output"])

    def test_the_notification_parser_captures_the_result(self):
        n = em._parse_task_notification(notif_text(TU_BG, "completed", "done: 4/4"))
        self.assertEqual(n["result"], "done: 4/4")
        self.assertEqual(n["tool_use_id"], TU_BG)
        self.assertEqual(em._parse_task_notification("<task-notification><status>completed</status></task-notification>")["result"], "")


class Liveness(World):
    def test_sdk_live_sets_decide_running_for_a_background_agent(self):
        # the SDK snapshot names the live agents (SubagentStart/Stop) → running while listed …
        self.tm = {SID: dict(self.live, subagents=[{"type": "general-purpose", "since": T0, "agentId": AID_BG}], bgTasks=[])}
        ev = self._agent_events()[TU_BG]
        self.assertTrue(ev.get("agentRunning"))
        # … and NOT running the moment neither live set names it, even with no notification landed:
        # the ack stands as the honest output (nothing will come back), no preview
        self.tm = {SID: dict(self.live, subagents=[], bgTasks=[])}
        km._chat_fold.clear()
        ev = self._agent_events()[TU_BG]
        self.assertNotIn("agentRunning", ev)
        self.assertNotIn("agentGist", ev)
        self.assertIn("Async agent launched", ev["output"])
        # the task-lifecycle set (tool-use id) is the other live source
        self.tm = {SID: dict(self.live, subagents=[], bgTasks=[{"toolUseId": TU_BG, "desc": "x"}])}
        km._chat_fold.clear()
        self.assertTrue(self._agent_events()[TU_BG].get("agentRunning"))

    def test_a_dormant_session_uses_the_cli_epoch_ghost_gate(self):
        # a launch OLDER than the current CLI epoch died with the previous CLI: not running
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"sid": SID, "spawnedAt": T0 + 100}))
        ev = self._agent_events()[TU_BG]
        self.assertNotIn("agentRunning", ev)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"sid": SID, "spawnedAt": T0 - 100}))
        km._chat_fold.clear()
        self.assertTrue(self._agent_events()[TU_BG].get("agentRunning"))


class Viewer(World):
    def test_build_subagent_renders_the_agent_file_through_the_chat_pipeline(self):
        fr = km.build_subagent(SID, AID_BG, NOW, self.tm)
        self.assertEqual(fr["type"], "subagent")
        self.assertEqual((fr["id"], fr["agentId"]), (SID, AID_BG))
        self.assertNotIn("error", fr)
        self.assertEqual(fr["meta"], {"agentType": "general-purpose", "description": "check the api tests",
                                      "spawnDepth": 1, "toolUseId": TU_BG})
        self.assertTrue(fr["running"])
        self.assertFalse(fr["truncated"])
        kinds = [e["kind"] for e in fr["events"]]
        self.assertIn("user", kinds)
        self.assertIn("tool", kinds)
        self.assertIn("assistant", kinds)
        tools = [e for e in fr["events"] if e["kind"] == "tool"]
        self.assertEqual([t["name"] for t in tools], ["Read", "Bash", "Grep", "Read"])
        self.assertEqual(tools[1]["output"], "3 passed, 1 failed", "tool_results fill the same way the chat's do")
        self.assertTrue(all(t.get("toolUseId") for t in tools))
        self.assertIn("one flaky case", fr["events"][-1]["md"])
        # the parent's side-store notes never leak into the agent's render (sidechain mode)
        self.assertFalse(any(e["kind"] in ("retried", "retryGaveUp", "effortApplied", "cmdGesture") for e in fr["events"]))

    def test_running_flips_when_the_notification_lands_and_a_foreground_agent_reads_finished(self):
        self.assertTrue(km.build_subagent(SID, AID_BG, NOW, self.tm)["running"])
        self.assertFalse(km.build_subagent(SID, AID_FG, NOW, self.tm)["running"], "its tool_result settled it")
        self._land_notification()
        km._parse_cache.clear()
        self.assertFalse(km.build_subagent(SID, AID_BG, NOW, self.tm)["running"])

    def test_a_foreground_agent_mid_turn_reads_running(self):
        # cut the parent transcript before the foreground agent's tool_result landed
        write_jsonl(self.tpath, self.parent[:6])
        km._parse_cache.clear()
        self.assertTrue(km.build_subagent(SID, AID_FG, NOW, self.tm)["running"])

    def test_the_shipped_tail_is_capped_and_says_so(self):
        saved = km.SUBAGENT_EVENT_CAP
        try:
            km.SUBAGENT_EVENT_CAP = 3
            fr = km.build_subagent(SID, AID_BG, NOW, self.tm)
            self.assertTrue(fr["truncated"])
            self.assertEqual(len(fr["events"]), 3)
            self.assertIn("one flaky case", fr["events"][-1]["md"], "a TAIL — the newest events survive the cut")
        finally:
            km.SUBAGENT_EVENT_CAP = saved

    def test_a_missing_file_is_a_loud_error_never_a_blank(self):
        self.bg_file.unlink()
        fr = km.build_subagent(SID, AID_BG, NOW, self.tm)
        self.assertIn("error", fr)
        self.assertIn("missing", fr["error"])
        self.assertNotIn("events", fr)
        self.assertIn("error", km.build_subagent(SID, "not-an-agent", NOW, self.tm))
        self.assertIn("error", km.build_subagent("99999999-0000-0000-0000-000000000000", AID_BG, NOW, self.tm))

    def test_the_file_is_found_under_a_forked_fsid_dir(self):
        # after a /clear fork the parent transcript is a NEW file, but the agent's dir keeps the old fsid
        moved = self.proj / "66666666-7777-8888-9999-000000000000"
        shutil.move(str(self.proj / SID), str(moved))
        km._SUBAGENT_META_CACHE.clear()
        self.assertEqual(km._subagent_file(str(self.tpath), AID_BG), moved / "subagents" / ("agent-%s.jsonl" % AID_BG))
        self.assertIsNone(km._subagent_file(str(self.tpath), "a9999999999999999"))


class _Client(dict):
    """A ws client record as _dispatch_ws sees it: frames land in `out`."""

    def __init__(self):
        super().__init__()
        self.out = []
        self.update({"app": "chat", "wid": "w1", "alive": True, "sent": {}, "dlock": __import__("threading").RLock(),
                     "send": lambda s: self.out.append(json.loads(s))})


class OpenClose(World):
    def _dispatch(self, client, msg):
        km.Handler._dispatch_ws(object.__new__(km.Handler), msg, client)

    def test_open_answers_now_and_registers_the_viewer_then_close_stops_the_pushes(self):
        c = _Client()
        self._dispatch(c, {"type": "openSubagent", "id": SID, "agentId": AID_BG})
        self.assertEqual(len(c.out), 1)
        fr = c.out[0]
        self.assertEqual((fr["type"], fr["agentId"]), ("subagent", AID_BG))
        self.assertTrue(fr["running"])
        self.assertIn((SID, AID_BG), c["subagents"])
        # the pusher pass: an UNCHANGED agent re-sends nothing (the dedup slot) …
        km._push_subagents([c], NOW, self.tm)
        self.assertEqual(len(c.out), 1)
        # … a grown file re-sends the fresh frame …
        append_jsonl(self.bg_file, [dict(arec(T0 + 40, "%s-a9" % AID_BG, [{"type": "text", "text": "Nearly done."}],
                                              "%s-fin" % AID_BG), isSidechain=True, agentId=AID_BG)])
        km._parse_cache.clear()
        km._push_subagents([c], NOW, self.tm)
        self.assertEqual(len(c.out), 2)
        self.assertEqual(c.out[1]["events"][-1]["md"], "Nearly done.")
        # … and a landed notification re-sends too (the running flag is part of the change key)
        self._land_notification()
        km._parse_cache.clear()
        km._push_subagents([c], NOW, self.tm)
        self.assertEqual(len(c.out), 3)
        self.assertFalse(c.out[2]["running"])
        # close: unregistered, and no further frames however the file moves
        self._dispatch(c, {"type": "closeSubagent", "id": SID, "agentId": AID_BG})
        self.assertNotIn((SID, AID_BG), c["subagents"])
        append_jsonl(self.bg_file, [dict(arec(T0 + 50, "%s-a10" % AID_BG, [{"type": "text", "text": "Really done."}],
                                              "%s-a9" % AID_BG), isSidechain=True, agentId=AID_BG)])
        km._parse_cache.clear()
        km._push_subagents([c], NOW, self.tm)
        self.assertEqual(len(c.out), 3)
        # a REOPEN of the unchanged agent is answered again (the dedup slot was dropped on close)
        self._dispatch(c, {"type": "openSubagent", "id": SID, "agentId": AID_BG})
        self.assertEqual(len(c.out), 4)

    def test_open_on_a_missing_file_pushes_the_error_frame(self):
        c = _Client()
        self.bg_file.unlink()
        self._dispatch(c, {"type": "openSubagent", "id": SID, "agentId": AID_BG})
        self.assertEqual(len(c.out), 1)
        self.assertIn("missing", c.out[0]["error"])


class BgRows(World):
    def test_an_agent_row_carries_its_agent_id_and_a_shell_row_does_not(self):
        # add a background shell command beside the agent launch
        append_jsonl(self.tpath, [
            arec(T0 + 100, "a5", [tool_use("toolu_sh_1", "Bash", {"command": "uv run pytest -q", "run_in_background": True,
                                                                   "description": "run the suite"})], self.last, stop="tool_use"),
            urec(T0 + 101, "r5", [tool_result("toolu_sh_1", "Command running in background with ID: b12345abc")], "a5",
                 toolUseResult={"isAsync": True, "status": "async_launched",
                                "outputFile": "/tmp/claude-1000/-tmp-notes-api/%s/tasks/b12345abc.output" % SID}),
        ])
        box = km._bg_tasks(str(self.tpath))
        rows = {r["id"]: r for r in box["tasks"]}
        self.assertEqual(rows[TU_BG]["agentId"], AID_BG)
        self.assertNotIn("agentId", rows["toolu_sh_1"], "a shell task is not a subagent")

    def test_the_output_symlink_basename_is_the_third_join_source(self):
        self.assertEqual(km._agent_id_of_output("/tmp/claude-1000/x/%s/tasks/%s.output" % (SID, AID_BG)), AID_BG)
        self.assertIsNone(km._agent_id_of_output("/tmp/claude-1000/x/%s/tasks/b12345abc.output" % SID))
        self.assertIsNone(km._agent_id_of_output(""))


class Fold(World):
    def _n(self):
        e = km._chat_fold_get(SID)
        return e["n"] if e else 0

    def test_a_running_agents_launch_turn_is_never_sealed_and_the_fold_matches_a_full_build(self):
        # three turns after the launch: the boundary stays before turn 1 while the agent runs
        self._follow_ups(3)
        km._parse_cache.clear()
        inc = self._events()
        self.assertLessEqual(self._n(), 1, "the launch turn (index 0) holds the seal boundary while its agent runs")
        km._chat_fold.clear()
        ref = self._events()
        self.assertEqual(json.dumps(inc, sort_keys=True), json.dumps(ref, sort_keys=True))
        # the notification lands: the agent's turn seals like any other, and the sealed card shows the report
        self._land_notification()
        km._parse_cache.clear()
        self._events(); self._events()
        self.assertGreaterEqual(self._n(), 2, "settled → sealed")
        ev = self._agent_events()[TU_BG]
        self.assertIn("one flaky case", ev["output"])
        inc2 = self._events()
        km._chat_fold.clear()
        self.assertEqual(json.dumps(inc2, sort_keys=True), json.dumps(self._events(), sort_keys=True))

    def test_a_sealed_pending_agent_demotes_when_its_report_lands(self):
        # dormant + the ghost gate says the launch is dead → sealed as PENDING (ack stands); a late
        # notification must still reach the sealed card
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"sid": SID, "spawnedAt": T0 + 100}))
        self._follow_ups(2)
        self._events(); self._events()
        self.assertGreaterEqual(self._n(), 2, "a dead launch does not hold the seal")
        self.assertTrue(any(a[0] == TU_BG and a[2] for a in km._chat_fold_get(SID)["agents"]), "remembered as pending")
        self.assertIn("Async agent launched", self._agent_events()[TU_BG]["output"])
        self._land_notification()
        km._parse_cache.clear()
        ev = self._agent_events()[TU_BG]
        self.assertIn("one flaky case", ev["output"], "the sealed card was rebuilt with the landed report")


if __name__ == "__main__":
    unittest.main()
