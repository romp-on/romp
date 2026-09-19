#!/usr/bin/env python3
"""LIVE smoke test for the Codex backend — drives REAL turns against the real `codex app-server`.

Deliberately not named test_* : it needs a `codex login` on the machine and bills two small turns
to the logged-in account, so CI never runs it. Run by hand when validating the backend against a
new Codex release:

    romp-codex-setup
    ROMP_SMOKE_MODEL=gpt-6-astra ROMP_SMOKE_EFFORT=low ROMP_SMOKE_MODE=auto python3 tests/smoke_codex_live.py

Exercises the seams the unit tests fake: a real turn's notification stream materializing the
transcript (items → tokenUsage → completed), the parse of that file into ended turns, live
interrupt of a running command, the normalizer's skipped-vocabulary counter on real payloads, and
the postal tools' wire shape (2026-09-19): `dynamicTools` accepted at thread/start and thread/resume,
the model's call arriving as `item/tool/call` bound to the calling thread, the `{success,
contentItems}` reply accepted, no approval or reviewer step in the way (Sandboxed or Auto), and the
call rendered in the transcript. That half never enters the sandbox, so it runs under the
danger-full-access override too; the sandbox negative (a command cannot read a file outside the
workspace) runs only under the shipped profile and prints SKIPPED otherwise. Last passed on runtime
0.153.3 (2026-09-19, the design lane's probe of the same wire). Uses a scratch state dir + a scratch
cwd; touches nothing live.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
# tests/test_state_dir_override.py executes this file by path from the repository root, where
# romp_load is not importable; a script run from tests/ has this directory on sys.path already.
sys.path.insert(0, HERE)
from romp_load import load_source  # noqa: E402

RUNTIME_STATE = Path(os.environ.get("ROMP_STATE_DIR") or
                     str(Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")) / "romp"))
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
cb = load_source("romp_codex_backend_live", os.path.join(ROOT, "kernel", "codex_backend.py"))
em = load_source("romp_event_model_live", os.path.join(ROOT, "bin", "romp-event-model"))


def until(fn, timeout, step=0.25, what=""):
    dl = time.time() + timeout
    while time.time() < dl:
        if fn():
            return True
        time.sleep(step)
    print("TIMEOUT waiting for %s" % what)
    return False


def main():
    state = Path(tempfile.mkdtemp(prefix="romp-codex-smoke-state-"))
    workdir = Path(tempfile.mkdtemp(prefix="romp-codex-smoke-cwd-"))
    if os.environ.get("ROMP_SMOKE_SANDBOX") == "danger-full-access":
        # Boxes whose kernel restricts unprivileged user namespaces can't run Codex's bwrap
        # sandbox at all (bwrap: setting up uid map: Permission denied) — every command/patch
        # fails. This override exercises the SAME protocol machinery without the sandbox; the
        # shipped default stays romp's custom profile with one runtime workspace root.
        cb.TURN_SANDBOX = {"type": "dangerFullAccess"}
        print("(sandbox override: dangerFullAccess — bwrap unavailable on this box)")
    if not cb.ensure_codex_sdk(RUNTIME_STATE):
        print("SMOKE SKIPPED: run romp-codex-setup first")
        return 2
    runtime = cb._runtime.runtime_path(RUNTIME_STATE)
    postal_calls = []                                     # (tool, sid, name, args) the backend handed the callable
    notices = []                                          # chat notices: a tool call must raise no warn

    def smoke_postal(tool, sid, name, args):
        postal_calls.append((tool, sid, name, args))
        return True, "SMOKE-POSTAL-OK %s" % tool
    be = cb.CodexBackend(str(state), codex_bin=str(runtime), postal=smoke_postal,
                         notify=lambda app, msg: notices.append(msg))
    if not be.available():
        print("SMOKE SKIPPED: %s" % (be._client_err or "codex backend unavailable"))
        return 2

    print("== spawn")
    sid = be.spawn("smoke", str(workdir))
    err = be.launch_error(sid)
    assert not err, "spawn launch_error: %r" % err
    print("   sid=%s tid=%s model=%s" % (sid, be._sessions[sid].tid, be._sessions[sid].model))
    mode = os.environ.get("ROMP_SMOKE_MODE", "sandboxed")
    assert be.set_mode(sid, mode), "unsupported smoke mode: %s" % mode
    model = os.environ.get("ROMP_SMOKE_MODEL")
    effort = os.environ.get("ROMP_SMOKE_EFFORT")
    if model:
        assert be.set_model(sid, model), "unsupported smoke model: %s" % model
    if effort:
        assert be.set_effort(sid, effort), "unsupported smoke effort: %s" % effort

    print("== turn 1: file-writing task")
    be.send(sid, "Create a file named hello.txt in the current directory containing exactly "
                 "'hello from codex', then reply with one short sentence confirming it.")
    assert until(lambda: be.live_sessions()[sid]["state"] == "working", 60, what="turn 1 start")
    assert until(lambda: be.live_sessions()[sid]["state"] == "waiting" and not be.busy(sid), 300,
                 what="turn 1 settle")
    path = Path(be.transcript_path(sid))
    recs = [json.loads(l) for l in path.read_text().splitlines()]
    types = [(r["type"], (r.get("message") or {}).get("stop_reason")) for r in recs]
    print("   %d records: %s" % (len(recs), types))
    assert recs, "no records materialized"
    assert recs[0]["type"] == "user" and recs[0].get("promptSource") == "sdk"
    settles = [r for r in recs if r["type"] == "assistant"
               and (r.get("message") or {}).get("stop_reason") == "end_turn"]
    assert settles, "no end_turn settle record"
    if settles[-1].get("message", {}).get("usage"):
        print("   usage on settle: %s" % settles[-1]["message"]["usage"])
    hello = workdir / "hello.txt"
    assert hello.is_file() and hello.read_text().strip() == "hello from codex", "file-writing turn failed"
    print("   hello.txt has the expected content")
    ctx = be.live_sessions()[sid]["context"]
    print("   context%%: %s" % ctx)

    print("== parse through the real event model")
    s = em.parse_session(str(path), rompuuid=sid, candidate_files=[str(path)],
                         now=time.time(), sdk_human=True)
    ended = [t.get("ended") for t in s["turns"]]
    print("   turns=%d ended=%s" % (len(s["turns"]), ended))
    assert s["turns"] and all(ended), "turn 1 did not parse as ended"
    assert s["turns"][0]["atoms"][0].get("author") == "human"

    print("== turn 2: interrupt a long-running command")
    be.send(sid, "Run the shell command `sleep 300` and wait for it to finish.")
    assert until(lambda: be.live_sessions()[sid]["state"] == "working", 60, what="turn 2 start")
    # wait for the sleep command's tool_use to MATERIALIZE (the command is genuinely running),
    # then cut it — a fixed grace raced turns that settled early (a sandbox-refused sleep)
    def sleep_started():
        for record in map(json.loads, path.read_text().splitlines()):
            for block in record.get("message", {}).get("content", []):
                if block.get("type") == "tool_use" and "sleep 300" in json.dumps(block.get("input", {})):
                    return True
        return False
    assert until(sleep_started, 90, what="sleep tool_use record")
    assert be.live_sessions()[sid]["state"] == "working", "turn settled before interrupt"
    assert be.interrupt(sid), "interrupt refused"
    assert until(lambda: be.live_sessions()[sid]["state"] == "waiting", 120, what="turn 2 settle")
    recs = [json.loads(l) for l in path.read_text().splitlines()]
    tail_txt = json.dumps(recs[-3:])
    print("   interrupt record present: %s" % ("[Request interrupted" in tail_txt))
    s = em.parse_session(str(path), rompuuid=sid, candidate_files=[str(path)],
                         now=time.time(), sdk_human=True)
    print("   turns=%d all ended=%s" % (len(s["turns"]), all(t.get("ended") for t in s["turns"])))
    assert len(s["turns"]) >= 2, "interrupted turn merged with the next parse"

    print("== turn 3: the postal tools as dynamic tools (wire shape)")
    tid = be._sessions[sid].tid

    def tool_records():
        uses, results = [], []
        for record in map(json.loads, path.read_text().splitlines()):
            for block in record.get("message", {}).get("content", []):
                if block.get("type") == "tool_use" and block.get("name", "").startswith("mcp__romp-postal-service__"):
                    uses.append(block)
                if block.get("type") == "tool_result" and str(block.get("tool_use_id", "")).startswith("exec-"):
                    results.append(block)
        return uses, results
    be.send(sid, "Call the list_agents tool once, then reply with exactly the text it returned and nothing else.")
    assert until(lambda: postal_calls, 120, what="the list_agents call to reach the postal callable")
    assert until(lambda: be.live_sessions()[sid]["state"] == "waiting" and not be.busy(sid), 300,
                 what="turn 3 settle")
    tool, csid, cname, cargs = postal_calls[0]
    print("   call: tool=%s sid-matches=%s name=%s args=%s" % (tool, csid == sid, cname, cargs))
    assert tool == "list_agents", "the model called %s" % tool
    assert csid == sid and cname == "smoke", "the call was bound to the wrong session (threadId %s)" % tid
    uses, results = tool_records()
    print("   transcript: %d tool_use, %d tool_result" % (len(uses), len(results)))
    assert uses and uses[0]["name"] == "mcp__romp-postal-service__list_agents", "no dynamicToolCall item rendered"
    assert results and "SMOKE-POSTAL-OK" in str(results[0].get("content")), "the reply text did not come back"
    assert not any(r.get("is_error") for r in results), "the reply was not accepted as a success"
    assert not [n for n in notices if n.get("type") == "warn"], "a tool call raised a warn notice: %r" % notices
    recs = [json.loads(l) for l in path.read_text().splitlines()]
    final = [r for r in recs if r["type"] == "assistant" and (r.get("message") or {}).get("stop_reason") == "end_turn"]
    print("   model's reply carries the tool text: %s" % ("SMOKE-POSTAL-OK" in json.dumps(final[-1]["message"])))

    print("== resume keeps the registration (thread/resume with the same params)")
    assert be.kill(sid)
    assert be.resume("smoke", sid)
    n0 = len(postal_calls)
    be.send(sid, "Call the check_inbox tool once, then reply with exactly the text it returned and nothing else.")
    assert until(lambda: len(postal_calls) > n0, 120, what="the check_inbox call after resume")
    assert until(lambda: be.live_sessions()[sid]["state"] == "waiting" and not be.busy(sid), 300,
                 what="turn 4 settle")
    assert postal_calls[n0][0] == "check_inbox" and postal_calls[n0][1] == sid
    print("   call after resume: tool=%s sid-matches=True" % postal_calls[n0][0])

    print("== sandbox negative: a command cannot read a file outside the workspace")
    if cb.TURN_SANDBOX is not None:
        print("   SKIPPED: the danger-full-access override is on, so nothing confines the command")
    else:
        sentinel = state / "smoke-sentinel"               # stands in for the serve token: outside the workspace
        sentinel.write_text("SMOKE-SENTINEL-7\n")
        be.send(sid, "Run the shell command `cat %s` and reply with its output verbatim, or with the error "
                     "message verbatim if it fails." % sentinel)
        assert until(lambda: be.live_sessions()[sid]["state"] == "working", 60, what="turn 5 start")
        assert until(lambda: be.live_sessions()[sid]["state"] == "waiting" and not be.busy(sid), 300,
                     what="turn 5 settle")
        tail = path.read_text()
        assert "SMOKE-SENTINEL-7" not in tail, "a sandboxed command read a file outside the workspace"
        print("   the sentinel never reached the transcript (the tools are the only door to the token)")

    norm = be._sessions[sid].norm
    print("== normalizer skipped vocabulary (phase-2 items seen live): %s" % (norm.skipped or "{}"))
    print("== chain check")
    prev = None
    for r in recs:
        if r.get("subtype") == "compact_boundary":
            assert r["logicalParentUuid"] == prev
        else:
            assert r.get("parentUuid") == prev, "chain break at %s" % r["uuid"]
        prev = r["uuid"]
    print("   linear, %d records, no breaks" % len(recs))

    be.kill(sid)
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
