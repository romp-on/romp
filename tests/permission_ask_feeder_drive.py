"""Drive the REAL turn feeder (SdkBackend.inputs under the real _amain), the real stream forward and the real ask coroutines
over a synthetic session, with a STUB claude_agent_sdk client: no CLI is spawned. Run as its own process by
tests/test_permission_ask_state_survives_the_stream.py (the stub module must not sit in the test process's sys.modules for
every other test). Prints one JSON line of named checks. Synthetic only (placeholder ids, invented text)."""
import asyncio
import json
import os
import sys
import tempfile
import types

ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
_XDG = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG
os.environ.pop("ROMP_STATE_DIR", None)
os.environ.pop("ROMP_MANAGER_PID", None)
os.makedirs(os.path.join(_XDG, "romp"), exist_ok=True)
open(os.path.join(_XDG, "romp", "session-hosts"), "w").write("off\n")   # a minted state root pins the per-session hosts off


class _Result:
    def __init__(self, behavior=None, **kw):
        self.behavior = behavior; self.__dict__.update(kw)


class _Any:
    def __init__(self, *a, **kw):
        self.__dict__.update(kw)


class TextBlock:
    def __init__(self, text): self.text = text


class ToolResultBlock:
    def __init__(self, tool_use_id, content): self.tool_use_id, self.content, self.is_error = tool_use_id, content, False


class UserMessage:
    def __init__(self, uuid, blocks): self.uuid, self.content, self.parent_tool_use_id = uuid, blocks, None


class AssistantMessage:
    def __init__(self, uuid, blocks): self.uuid, self.content, self.model, self.parent_tool_use_id = uuid, blocks, "claude-fable-5-1", None


FED, CONNECTED = [], None


class StubClient:
    """The SDK client's surface the session's main loop uses: query() iterates the feeder generator exactly as the SDK does;
    the message stream carries nothing (the stream's atoms are pushed through _forward by the driver)."""
    def __init__(self, options=None, transport=None): self._stop = None
    async def __aenter__(self): CONNECTED.set(); return self
    async def __aexit__(self, *a): return False
    async def query(self, agen):
        async for item in agen:
            FED.append(item)
    async def receive_messages(self):
        self._stop = asyncio.Event()
        await self._stop.wait()
        if False:
            yield None
    async def get_context_usage(self): raise RuntimeError("stub")
    async def get_server_info(self): raise RuntimeError("stub")


fake = types.ModuleType("claude_agent_sdk")
fake.ClaudeSDKClient = StubClient
fake.PermissionResultAllow = type("PermissionResultAllow", (_Result,), {})
fake.PermissionResultDeny = type("PermissionResultDeny", (_Result,), {})
_made = {}
def _mk(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return _made.setdefault(name, type(name, (_Any,), {}))
fake.__getattr__ = _mk
sys.modules["claude_agent_sdk"] = fake

from romp_load import load_source   # noqa: E402
sb = load_source("romp_sdk_backend_feeder_drive", os.path.join(ROOT, "bin", "romp_sdk_backend.py"))
SID = "11111111-2222-3333-4444-777777777702"
CHECKS = {}


def check(name, cond, detail=None):
    CHECKS[name] = [bool(cond), detail]


def _states(be):
    p = os.path.join(be.state_dir, "states", SID + ".jsonl")
    return [json.loads(l)["state"] for l in open(p) if l.strip() and "state" in json.loads(l)] if os.path.exists(p) else []


class Ctx:
    suggestions = None


async def main():
    global CONNECTED
    root = tempfile.mkdtemp()
    open(os.path.join(root, "session-hosts"), "w").write("off\n")
    be = sb.SdkBackend(root, "/bin/true", lambda *a, **k: None)
    sess = sb.SdkSession(be, {"sid": SID, "name": "web", "cwd": tempfile.mkdtemp(), "mode": "acceptEdits"})
    CONNECTED, parked = asyncio.Event(), asyncio.Event()
    real_emit = be._emit_ask
    def emit(s, ask):
        real_emit(s, ask); parked.set()
    be._emit_ask = emit
    amain = asyncio.ensure_future(sess._amain())
    await asyncio.wait_for(CONNECTED.wait(), 15)
    await asyncio.sleep(0.3)
    sess.inflight = 1                                    # a turn is in flight when the tool asks
    # (a) a parked permission, then the composer's text: the REAL feeder pops it
    ask_task = asyncio.ensure_future(sess._can_use_tool("Bash", {"command": "ls"}, Ctx()))
    await asyncio.wait_for(parked.wait(), 10)
    check("parked_marks_permission", _states(be)[-1] == "permission", _states(be))
    before = list(_states(be))
    sess.enqueue("a message typed into the composer while the prompt stands")
    await asyncio.sleep(0.6)
    check("text_fed", any("typed into the composer" in json.dumps(f) for f in FED), len(FED))
    check("feeder_left_permission", _states(be) == before, _states(be))
    check("ask_still_parked", be.current_ask(SID) is not None)
    # (b) the stream during the ask
    be._forward(sess, UserMessage("aaaaaaaa-0000-0000-0000-000000000011", [ToolResultBlock("toolu_11", "ok")]))
    be._forward(sess, AssistantMessage("aaaaaaaa-0000-0000-0000-000000000012", [TextBlock("a chunk")]))
    check("stream_left_permission", _states(be)[-1] == "permission", _states(be))
    # (c) the answer settles: exactly one working line
    n = len(_states(be))
    ok = sess.resolve_ask("answer", "1")
    res = await asyncio.wait_for(ask_task, 10)
    await asyncio.sleep(0.3)
    check("answer_delivered", ok and type(res).__name__ == "PermissionResultAllow", type(res).__name__)
    check("settle_marks_working_once", _states(be)[n:] == ["working"], _states(be)[-3:])
    check("ask_cleared", be.current_ask(SID) is None)
    # (d) the control: nothing parked, the feeder's pop marks working
    sess.inflight = 0; sess._mark("waiting"); fed_n = len(FED)
    # the feeder feeds ONE text at a time: inputs() holds every further feed while sess._untaken (the last fed text the
    # CLI has not yet taken) is set, and only _on_message clears it on a streamed turn frame (_untaken_taken). This drive
    # pushes its stream through be._forward and never reaches _on_message, so the take is stood in for by hand before
    # each later enqueue
    sess._untaken = None
    sess.enqueue("a second message, nothing parked")
    await asyncio.sleep(0.6)
    check("control_pop_marks_working", _states(be)[-1] == "working" and len(FED) == fed_n + 1, (_states(be)[-2:], len(FED) - fed_n))
    # (e) a parked picker, the same way
    sess.inflight = 1; parked.clear()
    picker = asyncio.ensure_future(sess._ask_user({"questions": [{"question": "Which way?", "header": "Route",
                                                                   "options": [{"label": "left"}, {"label": "right"}], "multiSelect": False}]}))
    await asyncio.wait_for(parked.wait(), 10)
    before = list(_states(be)); fed_n = len(FED)
    sess._untaken = None                                 # the take of leg (d)'s text, stood in for as above
    sess.enqueue("a message while the picker stands")
    await asyncio.sleep(0.6)
    check("picker_text_fed_state_stands", len(FED) == fed_n + 1 and _states(be) == before and before[-1] == "picker", (before[-1], _states(be)[-1]))
    sess.resolve_ask("answer", 1)
    await asyncio.wait_for(picker, 10)
    await asyncio.sleep(0.3)
    check("picker_settle_marks_working", _states(be)[-1] == "working", _states(be)[-3:])
    sess.ended = True; amain.cancel()
    try:
        await asyncio.wait_for(amain, 5)
    except (asyncio.CancelledError, Exception):
        pass
    print("CHECKS:" + json.dumps(CHECKS), flush=True)


asyncio.run(main())
