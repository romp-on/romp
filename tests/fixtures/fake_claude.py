#!/usr/bin/env python3
"""A fake Claude Code CLI for the session-host tests: speaks the stream-json protocol over stdio the way
the real one does, with SCRIPTED behaviour driven by the text of each user message, so a test can stage
a long turn, a permission request, a hook callback or a cancel without a model call. No real prompt or
transcript text anywhere: every string here is invented.

Behaviour:
  * `-v` / `--version` on the command line: prints a version line and exits (the SDK's version check).
  * A `control_request` on stdin is answered with a success `control_response` (an initialize gets
    `hooks_applied`; the hook callback ids it registers are remembered per event).
  * A `user` message runs one TURN: a `system`/`init` record first (once), then an `assistant` record,
    then the scripted extras, then a `result` record. Tokens in the message text:
      sleep=N          the turn lasts N seconds before its result (default 0.2)
      ask=permission   emit a `can_use_tool` control request and wait for the response before the result
      hook=<Event>     emit a `hook_callback` control request for that event's first registered callback id
                       (or `hook_0` if none) and wait for the response
      cancel-after=N   cancel an unanswered control request after N seconds (the CLI's own timeout)
      after=N          wait N seconds before the extras above (so a test can detach first)
      pad=N            the turn's assistant record carries N bytes of filler (one record that fills a socket)
      autocompact=N    the CLI compacts on its own mid-turn: a `system`/`status` record with status "compacting" after the
                       assistant record, N seconds, then the status null record carrying compact_result (the CLI 2.1.257's
                       stream shape for an automatic compaction; a manual /compact emits the same pair)
  * FAKE_CLI_TRIGGER (env, a path; FAKE_CLI_TRIGGER_DELAY seconds, FAKE_CLI_TRIGGER_COUNT rows): once the file
    appears, that many bookkeeping `assistant` records that long after, OUTSIDE any turn (the host's turn count
    reads zero while they arrive), each with n=trigger<i>: output arriving while nothing was asked.
      merge=1          at the end of the turn, fold every user message queued meanwhile INTO this turn and answer
                       them all with this one result (the real CLI's shape for messages typed while it works)
  * Stdin end-of-file: finish the current turn, then exit 0 (probe finding 4).
  * SIGINT, or an `interrupt` control request (the SDK's interrupt()): the current turn ends with an
    `interrupted` result, as the real CLI's does.
  * FAKE_CLI_LOG (env): every stdin line is appended there, so a test can see what reached the CLI.
  * FAKE_CLI_TRANSCRIPT_DIR (env): the turn's records are also appended to <dir>/<session id>.jsonl,
    a transcript stand-in, so a test can count writers.
"""
import json
import os
import signal
import sys
import threading
import time
import uuid

def _argv_session_id():
    """Like the real CLI: a `--resume=<id>` or `--session-id=<id>` on the command line IS the session id."""
    for i, a in enumerate(sys.argv[1:], 1):
        for flag in ("--resume", "--session-id"):
            if a.startswith(flag + "="):
                return a.split("=", 1)[1]
            if a == flag and i < len(sys.argv) - 1:
                return sys.argv[i + 1]
    return None


SESSION_ID = os.environ.get("FAKE_CLI_SESSION_ID") or _argv_session_id() or str(uuid.uuid4())
_out_lock = threading.Lock()
_log = os.environ.get("FAKE_CLI_LOG")
_tdir = os.environ.get("FAKE_CLI_TRANSCRIPT_DIR")
_hooks: dict[str, list[str]] = {}
_responses: dict[str, dict] = {}
_resp_cv = threading.Condition()
_interrupted = threading.Event()
_init_sent = False


def emit(obj: dict) -> None:
    line = json.dumps(obj, separators=(",", ":"))
    with _out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    if _tdir and obj.get("type") in ("assistant", "user", "result", "system"):
        try:
            os.makedirs(_tdir, exist_ok=True)
            with open(os.path.join(_tdir, SESSION_ID + ".jsonl"), "a") as f:
                f.write(line + "\n")
        except OSError:
            pass


def tokens(text: str) -> dict:
    out = {}
    for tok in text.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def wait_response(rid: str, cancel_after: float | None) -> dict | None:
    deadline = None if cancel_after is None else time.time() + cancel_after
    with _resp_cv:
        while rid not in _responses:
            remaining = None if deadline is None else deadline - time.time()
            if remaining is not None and remaining <= 0:
                break
            _resp_cv.wait(timeout=0.2 if remaining is None else min(0.2, remaining))
        got = _responses.pop(rid, None)
    if got is None and cancel_after is not None:
        emit({"type": "control_cancel_request", "request_id": rid})
    return got


def run_turn(text: str) -> None:
    global _init_sent
    opts = tokens(text)
    if not _init_sent:
        _init_sent = True
        emit({"type": "system", "subtype": "init", "session_id": SESSION_ID, "model": "fake-model",
              "cwd": os.getcwd(), "tools": [], "apiKeySource": "none"})
    rec = {"type": "assistant", "message": {"role": "assistant", "model": "fake-model", "content": [{"type": "text", "text": "working on it"}]},
           "session_id": SESSION_ID, "uuid": str(uuid.uuid4())}
    if "pad" in opts:
        rec["pad"] = "x" * int(opts["pad"])
    emit(rec)
    cancel_after = float(opts["cancel-after"]) if "cancel-after" in opts else None
    if "after" in opts:                       # the scripted extras wait this long (a test detaches meanwhile)
        time.sleep(float(opts["after"]))
    if opts.get("ask") == "permission":
        rid = str(uuid.uuid4())
        emit({"type": "control_request", "request_id": rid,
              "request": {"subtype": "can_use_tool", "tool_name": "Write", "input": {"file_path": "note.txt", "content": "x"},
                          "tool_use_id": "toolu_" + rid[:8]}})
        got = wait_response(rid, cancel_after)
        emit({"type": "assistant", "message": {"role": "assistant", "model": "fake-model", "content": [{"type": "text",
              "text": "permission " + ("answered" if got else "cancelled")}]}, "session_id": SESSION_ID, "uuid": str(uuid.uuid4())})
    if "hook" in opts:
        event = opts["hook"]
        cid = (_hooks.get(event) or ["hook_0"])[0]
        rid = str(uuid.uuid4())
        emit({"type": "control_request", "request_id": rid,
              "request": {"subtype": "hook_callback", "callback_id": cid, "tool_use_id": "toolu_" + rid[:8],
                          "input": {"hook_event_name": event, "session_id": SESSION_ID}}})
        got = wait_response(rid, cancel_after)
        emit({"type": "assistant", "message": {"role": "assistant", "model": "fake-model", "content": [{"type": "text",
              "text": "hook " + ("answered" if got else "cancelled")}]}, "session_id": SESSION_ID, "uuid": str(uuid.uuid4())})
    if "autocompact" in opts:                 # the CLI's own compaction bracket on the stream (never a transcript row)
        emit({"type": "system", "subtype": "status", "status": "compacting", "uuid": str(uuid.uuid4()), "session_id": SESSION_ID})
        time.sleep(float(opts["autocompact"]))
        emit({"type": "system", "subtype": "status", "status": None, "compact_result": {"trigger": "auto", "preTokens": 180000},
              "uuid": str(uuid.uuid4()), "session_id": SESSION_ID})
    sleep = float(opts.get("sleep", "0.2"))
    end = time.time() + sleep
    while time.time() < end and not _interrupted.is_set():   # loop-ok: a bounded wait on the scripted turn length
        time.sleep(0.05)
    if "merge" in opts:                       # the fold: the queued messages join this turn, one result for all of them
        while not _turns.empty():             # loop-ok: bounded by the queue's length
            try:
                _turns.get_nowait()
            except Exception:
                break
    emit({"type": "result", "subtype": "success", "is_error": False, "duration_ms": int(sleep * 1000), "duration_api_ms": 1,
          "num_turns": 1, "result": "interrupted" if _interrupted.is_set() else "done", "session_id": SESSION_ID,
          "total_cost_usd": 0.0, "usage": {"input_tokens": 1, "output_tokens": 1}, "uuid": str(uuid.uuid4())})
    _interrupted.clear()


def handle(line: str) -> None:
    """One stdin line, on the READER thread: control traffic is answered or recorded at once (a turn in
    progress must be able to receive its response), user messages queue for the turn runner."""
    if _log:
        try:
            with open(_log, "a") as f:
                f.write(line.rstrip("\n") + "\n")
        except OSError:
            pass
    try:
        obj = json.loads(line)
    except ValueError:
        return
    t = obj.get("type")
    if t == "control_request":
        req = obj.get("request") or {}
        rid = obj.get("request_id")
        resp = {"subtype": "success", "request_id": rid, "response": {}}
        if req.get("subtype") == "interrupt":
            _interrupted.set()            # like the real CLI: the current turn ends with an interrupted result
        if req.get("subtype") == "initialize":
            hooks = req.get("hooks") or {}
            _hooks.clear()
            for ev, matchers in hooks.items():
                for m in matchers or []:
                    _hooks.setdefault(ev, []).extend(m.get("hookCallbackIds") or [])
            resp["response"] = {"commands": [], "hooks_applied": sorted(_hooks)}
        emit({"type": "control_response", "response": resp})
    elif t == "control_response":
        rid = str(((obj.get("response") or {}).get("request_id")) or "")
        with _resp_cv:
            _responses[rid] = obj
            _resp_cv.notify_all()
    elif t == "user":
        msg = obj.get("message") or {}
        content = msg.get("content")
        text = content if isinstance(content, str) else " ".join(
            c.get("text", "") for c in (content or []) if isinstance(c, dict))
        _turns.put(text)


_turns: "queue.Queue[str | None]" = None


def _reader() -> None:
    for line in sys.stdin:
        if line.strip():
            handle(line)
    _turns.put(None)          # stdin end-of-file: finish what is queued, then leave


def _trigger_watch(path: str, delay: float, count: int) -> None:
    deadline = time.time() + 120
    while time.time() < deadline and not os.path.exists(path):   # loop-ok: the scripted trigger, bounded
        time.sleep(0.02)
    if not os.path.exists(path):
        return
    time.sleep(delay)
    for i in range(count):                                       # loop-ok: a fixed count from the test
        emit({"type": "assistant", "message": {"role": "assistant", "model": "fake-model", "content": [{"type": "text", "text": "bookkeeping"}]},
              "session_id": SESSION_ID, "uuid": str(uuid.uuid4()), "n": "trigger%d" % i})


def main() -> int:
    global _turns
    if len(sys.argv) > 1 and sys.argv[1] in ("-v", "--version"):
        print("2.1.257 (Claude Code)")
        return 0
    import queue
    _turns = queue.Queue()
    signal.signal(signal.SIGINT, lambda *a: _interrupted.set())
    threading.Thread(target=_reader, daemon=True).start()
    trig = os.environ.get("FAKE_CLI_TRIGGER")
    if trig:
        threading.Thread(target=_trigger_watch, args=(trig, float(os.environ.get("FAKE_CLI_TRIGGER_DELAY") or 1.0),
                                                      int(os.environ.get("FAKE_CLI_TRIGGER_COUNT") or 1)), daemon=True).start()
    while True:
        text = _turns.get()
        if text is None:
            return 0          # end-of-file: the current turn (if any) has finished above (probe finding 4)
        run_turn(text)


if __name__ == "__main__":
    sys.exit(main())
