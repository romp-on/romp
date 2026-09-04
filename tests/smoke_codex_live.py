#!/usr/bin/env python3
"""LIVE smoke test for the Codex backend — drives REAL turns against the real `codex app-server`.

Deliberately not named test_* : it needs a `codex login` on the machine and bills two small turns
to the logged-in account, so CI never runs it. Run by hand when validating the backend against a
new Codex release:

    romp-codex-setup
    ROMP_SMOKE_MODEL=gpt-6-astra ROMP_SMOKE_EFFORT=low ROMP_SMOKE_MODE=auto python3 tests/smoke_codex_live.py

Exercises the seams the unit tests fake: a real turn's notification stream materializing the
transcript (items → tokenUsage → completed), the parse of that file into ended turns, live
interrupt of a running command, and the normalizer's skipped-vocabulary counter on real payloads.
Uses a scratch state dir + a scratch cwd; touches nothing live.
"""
import json
import os
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

RUNTIME_STATE = Path(os.environ.get("ROMP_STATE_DIR") or
                     str(Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "romp"))
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
cb = SourceFileLoader("romp_codex_backend_live", os.path.join(ROOT, "kernel", "codex_backend.py")).load_module()
em = SourceFileLoader("romp_event_model_live", os.path.join(ROOT, "bin", "romp-event-model")).load_module()


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
    be = cb.CodexBackend(str(state), codex_bin=str(runtime))
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
