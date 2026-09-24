#!/usr/bin/env python3
"""A fork knows what it is and never acts as its parent (the user 2026-09-24).

A comment thread started from a session with a background agent in flight took the parent's copied history
(the parent's role, its plans) at face value and relaunched the parent's audit. Three things let it:
- its first message was only the quote and the comment, nothing saying what the conversation was;
- the CLI, loading the parent's history for the fork, found the parent's background agent with no completion
  record and told the fork, as a task notification, that it "didn't finish before the previous session ended";
- the SessionStart hook's output never reached it (the CLI runs the hook for a fork cut with
  --resume-session-at and drops what it returns), so nothing else in its context disagreed.

What is pinned here, at the backend (the kernel's doors are pinned in test_comment_threads.py and
test_kernel_fork.py):
- a plain fork's FIRST message opens with the line the fork was armed with, exactly once, in the new message;
- every connect of a session born as a fork carries the CLI's source-alive boundary, so the parent's copied
  history is not scanned for lost background work, and the fork's own later work still is;
- romp touches no transcript on the way: the parent's file is untouched and the fork's copy is the CLI's.

SYNTHETIC fixtures only (placeholder UUIDs, the notes-api demo world)."""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"   # the conftest floor, re-asserted for a bare unittest run
sb = load_source("romp_sdk_backend_fork_identity", os.path.join(BIN, "romp_sdk_backend.py"))

PARENT = "11111111-2222-3333-4444-555555555555"
FORK = "66666666-7777-8888-9999-aaaaaaaaaaaa"
LINE = "I split this off; carry on separately."   # an invented opening line: the backend carries any text
T0 = 1781100000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parent_records():
    """A parent with a background agent launched and never finished, the shape the CLI reports as lost, plus
    the CLI's own queue bookkeeping rows."""
    return [
        {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(T0), "sessionId": PARENT,
         "content": "audit the notes-api"},
        {"type": "queue-operation", "operation": "dequeue", "timestamp": iso(T0), "sessionId": PARENT},
        {"type": "user", "uuid": "u1", "parentUuid": None, "timestamp": iso(T0 + 1), "sessionId": PARENT,
         "message": {"role": "user", "content": "audit the notes-api"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": iso(T0 + 2), "sessionId": PARENT,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_synthetic1", "name": "Agent",
              "input": {"description": "Audit the notes-api", "prompt": "audit", "run_in_background": True}}]}},
        {"type": "user", "uuid": "u2", "parentUuid": "a1", "timestamp": iso(T0 + 3), "sessionId": PARENT,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_synthetic1", "content": "Async agent launched."}]},
         "toolUseResult": {"status": "async_launched", "agentId": "a0000synthetic01",
                           "description": "Audit the notes-api"}},
        {"type": "assistant", "uuid": "a2", "parentUuid": "u2", "timestamp": iso(T0 + 4), "sessionId": PARENT,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "The audit is running."}]}},
    ]


class _Queue:
    """The SdkSession surface send() and unqueue() touch: the queue."""

    def __init__(self):
        self.queued = []

    def enqueue(self, text, qid=None, qts=None, paths=None):
        self.queued.append((text, qid))

    def unqueue(self, idx, expect=None, qid=None):
        for i, (t, q) in enumerate(self.queued):
            if q == qid:
                return self.queued.pop(i)[0]
        return None


class _Backend(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        # a test that mints its own state root pins per-session hosts off (repo CLAUDE.md, 2026-09-11)
        Path(self.d, "session-hosts").write_text("off")
        self._fetch_before = sb._fetch_key_fast_org
        sb._fetch_key_fast_org = lambda key: None
        self.addCleanup(setattr, sb, "_fetch_key_fast_org", self._fetch_before)
        if "claude_agent_sdk" not in sys.modules and not sb.sdk_importable():
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
            sys.modules["claude_agent_sdk"] = fake
            self.addCleanup(sys.modules.pop, "claude_agent_sdk", None)
        self.be = sb.SdkBackend(Path(self.d), "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        self.cwd = os.path.join(self.d, "notes-api")
        os.makedirs(self.cwd)
        self.be.spawn("api", self.cwd, sid=PARENT)
        self.parent_path = sb.transcript_path(self.cwd, PARENT)
        os.makedirs(os.path.dirname(self.parent_path), exist_ok=True)
        Path(self.parent_path).write_text("".join(json.dumps(r) + "\n" for r in parent_records()))
        self.q = _Queue()
        self.be._ensure = lambda sid: self.q          # no CLI: send() hands its text to this queue

    def reg(self, sid):
        return json.loads(Path(self.d, "sdk", sid + ".json").read_text())


class FirstMessageOpener(_Backend):
    def test_the_first_message_opens_with_the_line_and_spends_it(self):
        self.be.fork("api-2", PARENT, "a2", sid=FORK, opener=LINE)
        self.assertEqual(self.reg(FORK).get("forkOpener"), LINE)
        self.assertTrue(self.be.send(FORK, "Now add pagination to the list endpoint.", user=True))
        self.assertTrue(self.be.send(FORK, "And a test for it.", user=True))
        self.assertEqual([t for t, _q in self.q.queued],
                         [LINE + "\n\nNow add pagination to the list endpoint.", "And a test for it."],
                         "the line opens the first NEW message, once; nothing earlier is touched")
        self.assertNotIn("forkOpener", self.reg(FORK))

    def test_the_echo_carries_what_the_cli_is_given(self):
        # the echo, the queue and the record the CLI writes all hold the same text, so the landing pairs
        # them as ever; the chat hides the line on display (kernel _strip_fork_opener)
        self.be.fork("api-2", PARENT, "a2", sid=FORK, opener=LINE)
        self.be.send(FORK, "Now add pagination.", user=True)
        echoes = [a.get("_echo_text") for a in (self.be._live.get(FORK) or {}).values()]
        self.assertEqual(echoes, [LINE + "\n\nNow add pagination."])

    def test_a_slash_command_passes_through_and_leaves_the_line_armed(self):
        self.be.fork("api-2", PARENT, "a2", sid=FORK, opener=LINE)
        self.be.send(FORK, "/model opus", user=True)
        self.be.send(FORK, "Now add pagination.", user=True)
        self.assertEqual([t for t, _q in self.q.queued], ["/model opus", LINE + "\n\nNow add pagination."])

    def test_a_clear_disarms_the_line(self):
        # a /clear starts a conversation with none of the parent's history in it: the line would be false there
        self.be.fork("api-2", PARENT, "a2", sid=FORK, opener=LINE)
        self.be.send(FORK, "/clear", user=True)
        self.be.send(FORK, "Start over on the exporter.", user=True)
        self.assertEqual([t for t, _q in self.q.queued], ["/clear", "Start over on the exporter."])
        self.assertNotIn("forkOpener", self.reg(FORK))

    def test_a_first_message_pulled_back_out_of_the_queue_gives_the_line_back(self):
        self.be.fork("api-2", PARENT, "a2", sid=FORK, opener=LINE)
        self.be.send(FORK, "Now add pagination.", qid="q-1", user=True)
        self.be.sessions[FORK] = self.q                   # the running session the chat's cancel reaches
        self.assertEqual(self.be.unqueue(FORK, 0, qid="q-1"), "Now add pagination.",
                         "the user gets back what they typed")
        self.assertEqual(self.reg(FORK).get("forkOpener"), LINE, "and the next message opens with the line")
        self.be.send(FORK, "Now add cursor pagination.", qid="q-2", user=True)
        self.assertEqual(self.q.queued[-1][0], LINE + "\n\nNow add cursor pagination.")

    def test_a_message_queued_behind_a_stand_down_carries_the_line(self):
        self.be.fork("api-2", PARENT, "a2", sid=FORK, opener=LINE)
        queued = []
        self.be._attach_stand_down_holds = lambda sid, reg=None: True
        self.be._queue_behind_stand_down = lambda sid, text, qid=None: queued.append(text) or True
        self.assertTrue(self.be.send(FORK, "Where does the audit stand?"))
        self.assertEqual(queued, [LINE + "\n\nWhere does the audit stand?"])
        self.assertEqual(self.q.queued, [], "queued behind the stand-down, never handed over twice")

    def test_a_session_that_was_never_forked_sends_its_text_untouched(self):
        self.be.send(PARENT, "Now add pagination.", user=True)
        self.assertEqual([t for t, _q in self.q.queued], ["Now add pagination."])


class SourceAliveBoundary(_Backend):
    """Every connect of a session born as a fork tells the CLI its source is alive (RESUME_SOURCE_ALIVE_ENV)."""

    def options(self, sid):
        return self.be._options(sb.SdkSession(self.be, self.reg(sid)), dict)

    def test_the_fork_launch_carries_the_boundary_and_the_history_is_left_to_the_cli(self):
        before = Path(self.parent_path).read_bytes()
        self.be.fork("api-2", PARENT, "a2", sid=FORK, opener=LINE)
        kw = self.options(FORK)
        # the CLI makes the copy from the parent's own file: resume it, fork, pin the new id, cut
        self.assertEqual(kw["resume"], PARENT)
        self.assertTrue(kw["fork_session"])
        self.assertEqual(kw["session_id"], FORK)
        self.assertEqual(kw["extra_args"]["resume-session-at"], "a2")
        boundary = self.reg(FORK)["forkBoundaryAt"]
        self.assertEqual(kw["env"][sb.RESUME_SOURCE_ALIVE_ENV], FORK + "|" + boundary,
                         "the id the CLI runs as for this load, so its file-history copy still happens")
        self.assertRegex(boundary, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", "the CLI's ISO shape")
        stamps = [r["timestamp"] for r in parent_records()]
        self.assertTrue(all(s < boundary for s in stamps), "every copied record predates the boundary")
        # romp rewrote nothing: the parent's bytes stand and no transcript exists for the fork until the CLI
        # writes its copy (whose message records are the parent's, measured on 2.1.280)
        self.assertEqual(Path(self.parent_path).read_bytes(), before)
        self.assertFalse(os.path.exists(sb.transcript_path(self.cwd, FORK)))

    def test_every_later_connect_resends_the_same_boundary(self):
        self.be.fork("api-2", PARENT, "", sid=FORK)
        self.options(FORK)
        boundary = self.reg(FORK)["forkBoundaryAt"]
        # the CLI's init spent the fork flags: the session now resumes its own conversation
        self.be._update_reg(FORK, forkOf="", forkAt="", lastSid=FORK)
        kw = self.options(FORK)
        self.assertNotIn("fork_session", kw)
        self.assertEqual(kw["resume"], FORK)
        self.assertEqual(kw["env"][sb.RESUME_SOURCE_ALIVE_ENV], FORK + "|" + boundary,
                         "a revive or a kernel restart must not report the parent's work either")

    def test_a_retried_fork_launch_restamps(self):
        # a fork whose CLI died before init re-copies the parent at the retry, so the boundary moves with it
        self.be.fork("api-2", PARENT, "", sid=FORK)
        sess = types.SimpleNamespace(sid=FORK, _fork_boundary="")
        writes = []
        sb.fork_source_alive_env(sess, {"fork_session": True, "session_id": FORK, "resume": PARENT},
                                 lambda sid, **f: writes.append(f), now=T0 + 100)
        first = sess._fork_boundary
        sb.fork_source_alive_env(sess, {"fork_session": True, "session_id": FORK, "resume": PARENT},
                                 lambda sid, **f: writes.append(f), now=T0 + 200)
        self.assertLess(first, sess._fork_boundary)
        self.assertEqual(writes, [{"forkBoundaryAt": first}, {"forkBoundaryAt": sess._fork_boundary}])
        self.assertEqual(first, "2026-06-10T14:01:40.000Z")

    def test_a_session_that_was_never_a_fork_carries_no_boundary(self):
        self.assertNotIn(sb.RESUME_SOURCE_ALIVE_ENV, self.options(PARENT)["env"])

    def test_a_thread_is_a_fork_too(self):
        self.be.fork("api-comment-1", PARENT, "a2", sid=FORK, thread_of=PARENT)
        self.assertIn(sb.RESUME_SOURCE_ALIVE_ENV, self.options(FORK)["env"])
        self.assertNotIn("forkOpener", self.reg(FORK), "a thread's first message is framed where it is made")


if __name__ == "__main__":
    unittest.main()
