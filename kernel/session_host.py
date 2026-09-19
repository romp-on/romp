#!/usr/bin/env python3
"""romp-session-host: the per-session process that owns one Claude Code CLI (stage 4 of the
restart-surviving sessions program, T315; design: the T315 design note, the T295 report section 2a).

The kernel used to be the CLI's parent: its pipes were the CLI's stdio, so a kernel restart ended
every turn. The host takes the parent's place. It spawns the CLI from a SPAWN SPECIFICATION the kernel
writes (`spawn.json`: the plain-data fields of the SDK's ClaudeAgentOptions), through the SDK's own
SubprocessCLITransport when the SDK is importable (so the command line and the environment are the
SDK's, byte for byte), reads the CLI's stdout WITHOUT PAUSE and appends every message to an append-only
JOURNAL (through a writer task, so a slow disk never pauses the reader), serves one Unix socket the kernel
attaches to, holds the stage 1 LEASE as the holder, tracks every OPEN control request (a permission, a hook
callback) until a kernel's answer, the CLI's cancel or, for a hook parked with no kernel attached, its own
neutral answer before the CLI's deadline, relays stderr, and ends the CLI by closing its stdin and waiting.
A kernel that attaches after a restart replays the journal from the offset it last acknowledged, receives
every still-open request again, and sends its own initialize, which the CLI accepts as a replacement of its
hook table (the T303 probe).

The socket protocol is newline-delimited JSON frames, field `t` naming the frame:
  kernel → host: attach {kernel:{pid,start,version}, ack:N}, in {data}, ack {offset}, signal {sig},
                 end {grace}, detach, ping
  host → kernel: hello {host, cli:{pid, start, fsid, spawnedAt, login}, journal:{next}, parked:[ids]}, out {offset, data}, stderr {line},
                 exit {code, signal, cause}, fault {kind, text}, busy {kernel}, pong
One kernel is attached at a time. Connection loss without `detach` is a kernel death to the host; a
`detach` is not (the host keeps running). An unattached host whose CLI is idle past the grace ends it.

Secrets: the spec carries the environment overlay (a key helper command, PATH additions), so nothing
from the spec or any environment value is ever written to host.log, the journal or a frame.

Sibling modules are reached the kernel's way (kernel/loadsource.py under stable names); the lease
helpers come from sdk_backend, which imports without the SDK and runs nothing at import.
"""
from __future__ import annotations
import asyncio
import json
import os
import signal
import socket
import sys
import time
import traceback
from pathlib import Path

# ── protocol constants ──────────────────────────────────────────────────────────────────────────
PROTOCOL_VERSION = 1
LEASE_HEARTBEAT_S = 3.0          # the stage 1 cadence (sdk_backend.LEASE_HEARTBEAT_S; pinned equal by a test)
HOOK_TIMEOUT_S = 540.0           # what the kernel registers on every hook matcher (inside the CLI's 600 s default)
HOOK_SELF_ANSWER_S = 480.0       # a parked hook is answered by the host after this much parking, unattached
END_GRACE_DEFAULT_S = 120.0      # `end` without a grace: the long bound (conserve_close, request_reconnect)
END_GRACE_KILL_S = 5.0           # the kernel's kill: `end` with this bound
UNATTACHED_GRACE_DEFAULT_S = 900.0   # an idle CLI with no kernel attached for this long is ended (a setting)
JOURNAL_SEGMENT_BYTES = 64 * 1024 * 1024
READER_BEHIND_RECORDS = 5000     # records read but not yet on disk before the host says so
ACK_NONE = -1
EXIT_FLUSH_S = 2.0               # how long the exiting host waits for an attached kernel to take its last frames
REEXEC_DRAIN_S = 5.0        # the handover waits this long for an attached kernel to drain its socket backlog, else defers
GAP_TYPE = "romp-journal-gap"     # a record the journal could not write: a marker keeps the numbering, readers skip it
UNREADABLE = (None, 0)    # an index entry whose segment is gone (acknowledged and deleted): read_from skips it
END_SENTINEL = object()          # on the stdin pump: close the CLI's stdin after everything queued before it

# The neutral answer the host gives a parked hook callback when no kernel returned in time, PER EVENT
# KIND: the empty output, which is what romp's own hook callbacks return when they have nothing to say
# (no `decision`, no `permissionDecision`, so the CLI applies its normal flow). Kept as a table, not a
# single assumed object, so a future PreToolUse hook gets its own entry (an empty output there is also
# neutral: the CLI falls through to the permission flow). A test pins that every event the kernel's
# _options registers has a row here.
HOOK_NEUTRAL_OUTPUT = {
    "Stop": {},
    "UserPromptSubmit": {},
    "SubagentStart": {},
    "SubagentStop": {},
    "PostToolUse": {},
    "PostToolUseFailure": {},
    "PreToolUse": {},
}

# The ClaudeAgentOptions fields the spawn specification carries: every plain-data field the SDK's
# SubprocessCLITransport reads to build the command line and the environment. The kernel writes exactly
# these; a test compares the list against the fields `_build_command` and `connect` reference.
SPEC_FIELDS = ("cli_path", "cwd", "env", "permission_mode", "permission_prompt_tool_name", "resume",
               "session_id", "resume_session_at", "fork_session", "extra_args", "settings", "mcp_servers",
               "system_prompt", "model", "effort", "include_partial_messages", "enable_file_checkpointing",
               "max_buffer_size", "setting_sources", "add_dirs", "allowed_tools", "disallowed_tools",
               "max_turns", "continue_conversation", "fallback_model", "thinking", "max_thinking_tokens")
# Spec keys that are the host's own, not option fields
SPEC_HOST_KEYS = ("sid", "name", "version", "hook_timeout_s", "hook_self_answer_s", "unattached_grace_s",
                  "state_dir", "protocol", "reader_behind_records",
                  "login")    # the IDENTIFIER of the stored login the launch bills ("" = the machine's own), echoed in the
#                               hello's cli.login so an attaching kernel stamps the login the launch USED, not the one
#                               today's availability would pick (2026-09-14); never a token, key or other credential value
# Testing seams the spec may carry (never set by the kernel): a delay per journal write, an offset whose
# write raises. They exist so the reader-behind fault and the journal-fault path can be driven in a test.
SPEC_TEST_KEYS = ("_test_journal_delay_s", "_test_journal_fault_at", "_test_journal_gap_fault")


def encode_frame(obj: dict) -> bytes:
    """One frame: compact JSON plus a newline."""
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class FrameReader:
    """Newline-delimited JSON frames out of arbitrary byte chunks. A line that is not JSON is dropped
    with a note (the peer is ours; a corrupt line is a bug, never a protocol branch)."""

    def __init__(self, on_bad=None):
        self._buf = b""
        self._on_bad = on_bad

    def feed(self, chunk: bytes):
        self._buf += chunk
        out = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line, self._buf = self._buf[:nl], self._buf[nl + 1:]
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                if self._on_bad:
                    self._on_bad(len(line))
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out


# ── the journal ─────────────────────────────────────────────────────────────────────────────────
def _segment_first(name: str):
    """The first offset a segment file name carries (`journal-<firstoffset>.jsonl`), or None."""
    stem = name[:-len(".jsonl")] if name.endswith(".jsonl") else name
    if not stem.startswith("journal-"):
        return None
    tail = stem[len("journal-"):]
    return int(tail) if tail.isdigit() else None


class Journal:
    """Append-only record of every message the CLI emitted, one JSON object per line, in SEGMENTS named by
    the offset of their FIRST record (`journal-<firstoffset>.jsonl`) under the host directory. A record's
    OFFSET is its ordinal since the CLI started, global across segments; the file names carry the
    numbering, so a reader that finds only later segments (earlier ones acknowledged and deleted) still
    numbers every record right. The in-memory index maps an offset to (segment, byte position) so a reader
    can start anywhere. A new segment starts at a turn boundary (a `result` record) once the current one
    exceeds `segment_bytes`; a segment whose last record has been ACKNOWLEDGED and that is not the current
    one is deleted at the next turn boundary."""

    def __init__(self, directory, segment_bytes=JOURNAL_SEGMENT_BYTES):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.segment_bytes = int(segment_bytes)
        self.next_offset = 0
        self.acked = ACK_NONE
        self._index: list[tuple[int, int]] = []       # offset -> (segment first offset, byte position)
        self._seg_last: dict[int, int] = {}           # segment first offset -> last offset in it
        self.gaps: set = set()                        # offsets with no bytes on disk (a double write failure)
        self._seg = 0
        self._fh = None
        self._pos = 0
        self._open_segment(0)

    @classmethod
    def reopen(cls, directory, segment_bytes=JOURNAL_SEGMENT_BYTES):
        """The journal of a host that re-executed itself (the re-exec road): the index rebuilt from the segment files on
        disk, the next offset from the last record, gaps from gaps.json, and the LAST segment opened for append at its
        end. Nothing is written; a directory with no segments is an empty journal at offset 0. The rebuilt index equals
        the live one entry for entry (tests/test_session_host.py pins the equality against the live journal): a gap is
        one zero-length entry at the position of the record after it, in the segment that holds that record, wherever
        the gap fell (round two of the re-exec review: a gap at a segment's head was counted at the previous segment's
        tail AND the next one's head, so every offset past it read the record before); an offset whose segment is gone
        (acknowledged and deleted) is UNREADABLE, as the live journal marks it at the deletion."""
        j = cls.__new__(cls)
        j.dir = Path(directory)
        j.dir.mkdir(parents=True, exist_ok=True)
        j.segment_bytes = int(segment_bytes)
        j.acked = ACK_NONE
        j._index = []
        j._seg_last = {}
        j._fh = None
        j._pos = 0
        try:
            j.gaps = set(json.loads((j.dir / "gaps.json").read_text()))
        except Exception:
            j.gaps = set()
        segs = sorted((f, p) for p in j.dir.glob("journal-*.jsonl") for f in [_segment_first(p.name)] if f is not None)
        n = 0
        pos = 0
        for first, p in segs:
            while n < first:                                    # offsets before this segment that no file holds: deleted segments
                j._index.append(UNREADABLE); n += 1
            pos = 0
            with open(p, "rb") as fh:
                for line in fh:
                    while n in j.gaps:                          # gaps before this record: zero-length entries at its position
                        j._index.append((first, pos)); j._seg_last[first] = n; n += 1
                    j._index.append((first, pos))
                    j._seg_last[first] = n
                    pos += len(line)
                    n += 1
        if segs:
            while n in j.gaps:                                  # gaps after the last record, at the end of the last segment
                j._index.append((segs[-1][0], pos)); j._seg_last[segs[-1][0]] = n; n += 1
        j.next_offset = n
        j._seg = segs[-1][0] if segs else 0
        j._open_segment(j._seg)
        return j

    def _path(self, seg: int) -> Path:
        return self.dir / ("journal-%d.jsonl" % seg)

    def _open_segment(self, first: int) -> None:
        if self._fh is not None:
            self._fh.close()
        self._seg = first
        # UNBUFFERED: a write that fails leaves nothing pending in a buffer to land later at a stale position
        # (a buffered handle keeps the bytes of a failed flush and writes them on the next one; the commit-5
        # review's finding a)
        self._fh = open(self._path(first), "ab", buffering=0)
        self._pos = self._fh.tell()

    def append(self, record: dict) -> int:
        """Append one record; returns its offset. Written straight to the file (unbuffered), so a kernel that
        attaches reads what the host wrote. Raises on a write failure, and then NOTHING has moved: the
        segment is truncated back to the last good position and no index, position or offset changed; the
        caller decides (the writer task notes a gap)."""
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        off = self.next_offset
        try:
            written = 0
            while written < len(line):          # loop-ok: a raw write may be partial; bounded by the line's length
                n = self._fh.write(line[written:])
                if not n:
                    raise OSError("short write to the journal")
                written += n
        except Exception:
            try:
                os.ftruncate(self._fh.fileno(), self._pos)
                self._fh.seek(self._pos)
            except Exception:
                # the segment cannot be put back (a partial write that will not truncate): its byte positions are
                # unreliable from here, so it is left behind and a fresh segment starts at this offset (finding 11)
                try:
                    self._open_segment(off)
                except Exception:
                    pass
            raise
        self._index.append((self._seg, self._pos))
        self._seg_last[self._seg] = off
        self._pos += len(line)
        self.next_offset = off + 1
        if record.get("type") == "result":
            self._turn_boundary()
        return off

    def note_gap(self, off: int) -> None:
        """A record whose write failed twice (the record and its gap marker): the index gains a ZERO-LENGTH
        entry at the current position and the offset advances, so index and offsets stay in lockstep (a
        reader skips the entry; the commit-5 review's finding b)."""
        if off != self.next_offset:
            return
        self._index.append((self._seg, self._pos))
        self._seg_last[self._seg] = off
        self.gaps.add(off)
        self.next_offset = off + 1
        self._persist_gaps()

    def _persist_gaps(self) -> None:
        """gaps.json for the ORPHAN reader (which has no index): written to a temp name and renamed over, never
        in place. On the very full disk that made the gap an in-place rewrite truncates first and then fails,
        erasing the record the reader needs (the commit-8 review's item 2); a rename either lands the new file
        whole or leaves the old one standing."""
        tmp = self.dir / "gaps.json.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(json.dumps(sorted(self.gaps)).encode("utf-8"))
            os.replace(tmp, self.dir / "gaps.json")
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _turn_boundary(self) -> None:
        # rotate when the current segment is past its size; drop segments the kernel has fully acknowledged
        if self._pos >= self.segment_bytes:
            self._open_segment(self.next_offset)
        for seg in sorted(self._seg_last):
            if seg == self._seg:
                continue
            if self._seg_last[seg] <= self.acked:
                try:
                    os.unlink(self._path(seg))
                except OSError:
                    pass
                for off in range(seg, self._seg_last[seg] + 1):     # the entries of a deleted segment: unreadable, as a reopen rebuilds them
                    self._index[off] = UNREADABLE
                self._seg_last.pop(seg, None)

    def ack(self, offset: int) -> None:
        if offset > self.acked:
            self.acked = min(int(offset), self.next_offset - 1)

    def segments(self) -> list[int]:
        """The first offsets of the segments still on disk, in order (the current one included, records or not)."""
        return sorted(set(self._seg_last) | {self._seg})

    def read_from(self, offset: int, end: int | None = None):
        """Yield (offset, record) for every record from `offset` up to `end` (exclusive; the live count when
        None), by the index: each record is read at its recorded position, so a gap (an unrecorded one, a
        zero-length index entry, or a written gap marker) is skipped without disturbing the numbering. A
        record whose segment was deleted (acknowledged long ago) is skipped too."""
        offset = max(0, int(offset))
        stop = self.next_offset if end is None else min(int(end), self.next_offset)
        fh, cur_seg = None, None
        try:
            while offset < stop:
                if offset in self.gaps:
                    offset += 1
                    continue
                seg, pos = self._index[offset]
                if seg is None:                                 # the segment is gone (acknowledged and deleted)
                    offset += 1
                    continue
                if seg != cur_seg:
                    if fh is not None:
                        fh.close()
                    cur_seg = seg
                    try:
                        fh = open(self._path(seg), "rb")
                    except OSError:
                        fh = None
                if fh is None:
                    offset += 1
                    continue
                fh.seek(pos)
                line = fh.readline()
                try:
                    rec = json.loads(line) if line.strip() else None
                except ValueError:
                    rec = None
                if rec is not None and rec.get("type") != GAP_TYPE:
                    yield offset, rec
                offset += 1
        finally:
            if fh is not None:
                fh.close()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _opens_turn(obj: dict) -> bool:
    """Whether a `user` line fed to the CLI opens a turn the CLI will answer with a result: a message whose content
    carries text (a string, or a text block). A user line carrying only tool results or nothing is bookkeeping the CLI
    absorbs into the running turn, never a turn of its own, so counting it left the open-turn count high for good."""
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(c, dict) and c.get("type") == "text" and str(c.get("text") or "").strip() for c in content)
    return False


def read_journal_dir(directory, offset: int = 0):
    """Read an ORPHAN journal (its host is gone) from `offset` to the end without an index: segments in
    first-offset order, each record numbered from its segment's first offset, so acknowledged-and-deleted
    early segments cost nothing but the records they held. Pure on the files."""
    d = Path(directory)
    segs = sorted((f, p) for p in d.glob("journal-*.jsonl") for f in [_segment_first(p.name)] if f is not None)
    try:
        gaps = set(json.loads((d / "gaps.json").read_text()))
    except Exception:
        gaps = set()
    for first, p in segs:
        n = first
        while n in gaps:            # an unrecorded gap at the segment's head
            n += 1
        with open(p, "rb") as fh:
            for line in fh:
                if n >= offset:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        rec = None
                    if rec is not None and rec.get("type") != GAP_TYPE:
                        yield n, rec
                n += 1
                while n in gaps:    # an unrecorded gap between two records on disk: the numbering skips it
                    n += 1


# ── open control requests ───────────────────────────────────────────────────────────────────────
class Parked:
    """EVERY control request the CLI sent that nobody has answered yet, attached or not: a kernel's
    control_response retires it, the CLI's cancel drops it, and a hook parked while no kernel is attached
    is answered by the host itself after `self_answer_s`. On attach the open ids ride `hello` and their
    records are sent again (whatever the acknowledged offset says: a request delivered live to a kernel that
    then died was received, never answered). `answered` remembers ids already answered, so a late duplicate
    answer is dropped. `unattached_since` marks when a request began waiting with no kernel."""

    def __init__(self, self_answer_s=HOOK_SELF_ANSWER_S):
        self.self_answer_s = float(self_answer_s)
        self.open: dict[str, dict] = {}        # request id -> {kind, offset, t, callback_id, event, record, unattached_since}
        self.answered: set[str] = set()

    def park(self, request: dict, offset: int, now: float, attached: bool) -> None:
        rid = str(request.get("request_id") or "")
        req = request.get("request") if isinstance(request.get("request"), dict) else {}
        if not rid or rid in self.answered:
            return
        self.open[rid] = {"kind": str(req.get("subtype") or ""), "offset": offset, "t": now,
                          "callback_id": req.get("callback_id"), "tool_use_id": req.get("tool_use_id"),
                          "event": _hook_event_of(req), "record": request,
                          "unattached_since": None if attached else now}

    def detached(self, now: float) -> None:
        """The kernel left: every open request starts its unattended clock now (if not already running)."""
        for v in self.open.values():
            if v.get("unattached_since") is None:
                v["unattached_since"] = now

    def attached(self) -> None:
        for v in self.open.values():
            v["unattached_since"] = None

    def cancel(self, rid: str) -> bool:
        return self.open.pop(str(rid), None) is not None

    def answer(self, rid: str) -> bool:
        """A response reached the CLI for `rid`: True when it was open (first answer), False when it was
        already answered or never parked (a dead kernel's leftover, dropped by the caller)."""
        rid = str(rid)
        self.open.pop(rid, None)
        if rid in self.answered:
            return False
        self.answered.add(rid)
        return True

    def due_hooks(self, now: float) -> list[str]:
        """The hook callbacks that have waited `self_answer_s` or longer with no kernel attached, oldest first."""
        due = [(v["unattached_since"], rid) for rid, v in self.open.items()
               if v["kind"] == "hook_callback" and v.get("unattached_since") is not None
               and now - v["unattached_since"] >= self.self_answer_s]
        return [rid for _, rid in sorted(due)]

    def ids(self) -> list[str]:
        return sorted(self.open, key=lambda r: self.open[r]["offset"])

    def records(self) -> list[tuple[int, dict]]:
        """The open requests' journal offsets and records, in offset order (re-sent on attach)."""
        return sorted(((v["offset"], v["record"]) for v in self.open.values()), key=lambda x: x[0])


def _hook_event_of(req: dict) -> str:
    inp = req.get("input") if isinstance(req.get("input"), dict) else {}
    return str(inp.get("hook_event_name") or "")


def neutral_hook_response(request_id: str, event: str) -> dict:
    """The control_response frame the host writes for a parked hook it answers itself."""
    return {"type": "control_response",
            "response": {"subtype": "success", "request_id": request_id,
                         "response": dict(HOOK_NEUTRAL_OUTPUT.get(event, {}))}}


# ── the spawn specification ─────────────────────────────────────────────────────────────────────
def spec_to_options(spec: dict, stderr_cb):
    """A ClaudeAgentOptions from the spec's plain-data fields, plus the host's stderr relay. Only the
    SDK's fields; the host's own keys (SPEC_HOST_KEYS, SPEC_TEST_KEYS) are left out. Requires the SDK."""
    from claude_agent_sdk import ClaudeAgentOptions
    kw = {k: spec[k] for k in SPEC_FIELDS if k in spec and spec[k] is not None}
    kw["stderr"] = stderr_cb
    return ClaudeAgentOptions(**kw)


class PipeCliTransport:
    """The host's built-in transport for a CLI when the SDK is NOT importable: the hermetic tests and
    CI, which install no SDK. Same duck-typed surface as the SDK's SubprocessCLITransport (connect,
    read_messages, write, end_input, close, pid); the command line carries the stream-json flags and
    the spec's permission tool and resume fields only. A real install always has the SDK (the kernel
    refuses SDK sessions without it), so this never drives a real CLI; it drives the fake one."""

    def __init__(self, spec: dict, stderr_cb):
        self.spec = spec
        self._stderr_cb = stderr_cb
        self.proc = None
        self._stderr_task = None

    @property
    def pid(self):
        return self.proc.pid if self.proc else None

    def _argv(self) -> list[str]:
        s = self.spec
        argv = [str(s["cli_path"]), "--output-format", "stream-json", "--verbose", "--input-format", "stream-json"]
        if s.get("permission_prompt_tool_name"):
            argv += ["--permission-prompt-tool", str(s["permission_prompt_tool_name"])]
        if s.get("permission_mode"):
            argv += ["--permission-mode", str(s["permission_mode"])]
        if s.get("resume"):
            argv.append("--resume=%s" % s["resume"])
        if s.get("session_id"):
            argv.append("--session-id=%s" % s["session_id"])
        for flag, value in (s.get("extra_args") or {}).items():
            argv.append("--%s" % flag if value is None else "--%s=%s" % (flag, value))
        return argv

    async def connect(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"
        env.update({str(k): str(v) for k, v in (self.spec.get("env") or {}).items()})
        if self.spec.get("cwd"):
            env["PWD"] = str(self.spec["cwd"])
        self.proc = await asyncio.create_subprocess_exec(
            *self._argv(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=self.spec.get("cwd") or None, env=env,
            limit=int(self.spec.get("max_buffer_size") or 100 * 1024 * 1024))
        self._stderr_task = asyncio.ensure_future(self._relay_stderr())

    async def _relay_stderr(self):
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                self._stderr_cb(line.decode("utf-8", "replace").rstrip("\n"))
        except Exception:
            return

    async def read_messages(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj
        await self.proc.wait()

    async def write(self, data: str) -> None:
        self.proc.stdin.write(data.encode("utf-8"))
        await self.proc.stdin.drain()

    async def end_input(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:
            pass

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            await self.proc.wait()

    @property
    def returncode(self):
        return self.proc.returncode if self.proc else None


class _AdoptedProcess:
    """A CLI this host did not spawn but inherited across its own execve (the re-exec road): the pid, the pipe
    descriptors as asyncio streams, and a returncode learned from waitpid on a thread (the CLI is still this
    process's child, since exec keeps the pid). Same surface PipeCliTransport reads: pid, stdin, stdout, stderr,
    returncode, wait(), kill()."""
    def __init__(self, pid: int, stdin, stdout, stderr):
        self.pid = int(pid)
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self.returncode = None
        self.exited = False           # gone, exit unknown (reaped elsewhere): returncode stays None, the exit frame's word for unknown
        self._waiter = None

    async def wait(self):
        if self.returncode is None:
            if self._waiter is None:
                loop = asyncio.get_running_loop()
                self._waiter = loop.run_in_executor(None, self._waitpid)
            await self._waiter
        return self.returncode

    def _waitpid(self):
        try:
            _pid, status = os.waitpid(self.pid, 0)
        except ChildProcessError:
            self.exited = True                             # reaped elsewhere: gone, exit unknown (round two: a zero here read as a clean exit)
            return
        self.returncode = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)
        self.exited = True

    def kill(self):
        if self.exited:
            return                                          # the pid may be another process's by now
        os.kill(self.pid, signal.SIGKILL)


class AdoptedCliTransport(PipeCliTransport):
    """PipeCliTransport over a CLI inherited across the host's execve: the same read, write, end and close roads, the
    process an _AdoptedProcess built on the handoff's descriptors. `connect` adopts instead of spawning."""
    def __init__(self, spec: dict, stderr_cb, handoff: dict):
        super().__init__(spec, stderr_cb)
        self.handoff = handoff

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        fds = self.handoff["fds"]
        if not _pipe_fds_match_cli(self.handoff["cli_pid"], fds):     # the handoff is trusted only once the CLI's own table agrees
            raise RuntimeError("the handoff's descriptors are not the CLI's pipes")
        limit = int(self.spec.get("max_buffer_size") or 100 * 1024 * 1024)
        stdout_reader = asyncio.StreamReader(limit=limit)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdout_reader), os.fdopen(int(fds["stdout"]), "rb", buffering=0))
        stderr_reader = asyncio.StreamReader(limit=limit)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stderr_reader), os.fdopen(int(fds["stderr"]), "rb", buffering=0))
        w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, os.fdopen(int(fds["stdin"]), "wb", buffering=0))
        stdin_writer = asyncio.StreamWriter(w_transport, w_protocol, None, loop)
        self.proc = _AdoptedProcess(int(self.handoff["cli_pid"]), stdin_writer, stdout_reader, stderr_reader)
        self._stderr_task = asyncio.ensure_future(self._relay_stderr())


def _pipe_fds_match_cli(pid, fds: dict) -> bool:
    """On Linux, whether the three descriptors are the CLI's own pipes: each compared by inode with the CLI's
    /proc/<pid>/fd/0, 1 and 2. Elsewhere True (no table to read). Read by the old process before it hands over and by
    the new one before it trusts the handoff (round two of the re-exec review: the docs promised the second read)."""
    if not sys.platform.startswith("linux"):
        return True
    for name, n in (("stdin", 0), ("stdout", 1), ("stderr", 2)):
        try:
            if os.stat("/proc/%d/fd/%d" % (int(pid), n)).st_ino != os.fstat(int(fds[name])).st_ino:
                return False
        except (OSError, KeyError, TypeError, ValueError):
            return False
    return True


def cli_pipe_fds(transport) -> dict | None:
    """The CLI's three pipe descriptors as this host holds them: stdin's write end, stdout's and stderr's read ends,
    from the transport's asyncio pipe transports (the SDK's anyio Process wraps an asyncio Process; the pipe fallback
    holds one directly). None when any is missing. On Linux each is confirmed against the CLI's own descriptor table
    (/proc/<pid>/fd/0,1,2 by pipe inode), so a wrong descriptor is a refusal, never a CLI fed from the wrong end."""
    proc = getattr(transport, "_process", None) or getattr(transport, "proc", None)
    aproc = getattr(proc, "_process", None) or proc                # anyio.Process -> asyncio.subprocess.Process
    ptrans = getattr(aproc, "_transport", None)
    if ptrans is None:
        return None
    out = {}
    for name, n in (("stdin", 0), ("stdout", 1), ("stderr", 2)):
        pt = ptrans.get_pipe_transport(n) if hasattr(ptrans, "get_pipe_transport") else None
        pipe = pt.get_extra_info("pipe") if pt is not None else None
        try:
            out[name] = int(pipe.fileno())
        except Exception:
            return None
    pid = getattr(aproc, "pid", None)
    if pid and not _pipe_fds_match_cli(pid, out):
        return None
    return out


def sdk_importable() -> bool:
    import importlib.util
    return importlib.util.find_spec("claude_agent_sdk") is not None


def _cli_alive(transport) -> bool:
    """Whether the transport's CLI process is still running (None when unknown)."""
    proc = getattr(transport, "_process", None) or getattr(transport, "proc", None)
    rc = getattr(proc, "returncode", None) if proc is not None else None
    return proc is not None and rc is None


# ── the host ────────────────────────────────────────────────────────────────────────────────────
class SessionHost:
    """One host process: see the module docstring. Constructed from the spec path; `run()` is the
    whole life."""

    def __init__(self, spec_path, lease_api=None, now=None, reexec_path=None):
        self.spec_path = Path(spec_path)
        with open(self.spec_path) as f:
            self.spec = json.load(f)
        self.reexec = None                     # the handoff this process was re-executed with (the re-exec road), else None
        if reexec_path:
            with open(reexec_path) as f:
                self.reexec = json.load(f)
        self._reexec_pending = None            # (python, launcher) once a kernel asked and a turn was open: run at its result
        self._reexec_running = False           # a handover is being decided or done: a second request is refused meanwhile
        self._reader_hold = None               # an Event the stdout reader waits on between records; cleared for the handover
        self._journal_skip: set = set()        # offsets the handover wrote itself; the writer passes them by
        self.sid = str(self.spec["sid"])
        self.name = str(self.spec.get("name") or self.sid[:8])
        self.state_dir = Path(self.spec["state_dir"])
        self.dir = self.spec_path.parent
        self.sock_path = self.state_dir / "hosts" / (self.sid[:8] + ".sock")
        self.log_path = self.dir / "host.log"
        self.journal = Journal.reopen(self.dir) if self.reexec else Journal(self.dir)
        self.parked = Parked(float(self.spec.get("hook_self_answer_s") or HOOK_SELF_ANSWER_S))
        self.grace_s = float(self.spec.get("unattached_grace_s") or UNATTACHED_GRACE_DEFAULT_S)
        self.reader_behind_records = int(self.spec.get("reader_behind_records") or READER_BEHIND_RECORDS)
        self.version = str(self.spec.get("version") or "")
        self.now = now or time.time
        self.lease_api = lease_api or _lease_api()
        self.transport = None
        self.cli_pid = None
        self.cli_start = None
        self.cli_spawned_at = None      # the CLI's spawn time, set ONCE in _spawn: the lease and the hello carry it as the CLI's
        #                                 epoch (before 2026-09-14 the lease stamped the beat's time under the same name)
        self.fsid = str(self.spec.get("resume") or self.spec.get("session_id") or "")
        self.attached = None            # the attached kernel's writer, or None
        self.kernel = None
        self.inflight = 0               # open turns: a text-bearing user line fed opens one; a result closes ALL of them (the CLI folds
        #                                 queued lines into the running turn and answers with one result); an output row after the result
        #                                 (an assistant or user row: the CLI running a queued line as its own turn) re-opens one
        self.idle_since = self.now()
        self.exit_info = None
        self.ending = None              # (deadline, cause) once `end` was requested
        self._replaying = False
        self._replay_end = 0
        self._live_backlog: list[tuple[int, dict]] = []
        self._server = None
        self._stop = None
        self._read_count = 0            # records read off the CLI (the writer task journals them in order)
        self._unwritten: dict = {}      # offset -> record read but not yet on disk (sent from memory on an attach)
        self._journal_gap_offsets: set = set()   # offsets whose record never reached the journal (a gap marker or nothing)
        self._journal_q: asyncio.Queue | None = None
        self._journal_faults = 0
        self._reader_behind_noted = False
        self._stdin_q: asyncio.Queue | None = None   # the kernel's `in` lines, written by their own task
        if self.reexec:                                # the state the previous code of this same process handed over
            h = self.reexec
            self.cli_pid = int(h["cli_pid"]); self.cli_start = h.get("cli_start"); self.cli_spawned_at = h.get("cli_spawned_at")
            self.fsid = str(h.get("fsid") or self.fsid)
            self._read_count = int(h.get("read_count") or self.journal.next_offset)
            self.inflight = int(h.get("inflight") or 0)
            self.journal.acked = int(h.get("acked", ACK_NONE))
            for rec in h.get("parked") or []:
                try:
                    self.parked.park(rec["record"], int(rec["offset"]), float(rec.get("t") or self.now()), attached=False)
                except Exception:
                    pass
            self.parked.answered.update(str(x) for x in (h.get("answered") or []))

    # ── host.log: never a spec field, never an environment value ──
    def log(self, kind: str, **fields) -> None:
        row = {"t": round(self.now(), 3), "kind": str(kind)}
        for k, v in fields.items():
            if v is None:
                continue
            row[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        except Exception:
            pass

    @staticmethod
    def _where(e: BaseException) -> str:
        """The failing frame of an exception, never its text (it could carry a line of the CLI's output)."""
        tb = traceback.extract_tb(e.__traceback__)
        return "%s:%d:%s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].name) if tb else "?"

    # ── lease ──
    def _write_lease(self) -> None:
        if self.cli_pid is None or self.cli_start is None:
            return
        lease = {"sid": self.sid, "fsid": self.fsid or self.sid, "name": self.name, "pid": int(self.cli_pid),
                 "start": self.cli_start, "holder": {"pid": os.getpid(), "start": self.lease_api["proc_start"](os.getpid()) or "",
                                                     "kind": "host"},
                 "version": self.version, "spawnedAt": self.cli_spawned_at, "t": self.now()}
        try:
            self.lease_api["write_lease"](self.state_dir, lease)
        except Exception as e:
            self.log("lease-write-failed", error=type(e).__name__)

    async def _beat(self) -> None:
        while self.exit_info is None:
            await asyncio.sleep(LEASE_HEARTBEAT_S)
            self._write_lease()

    # ── the CLI ──
    def _on_stderr(self, line: str) -> None:
        if self.attached is not None:
            self._send(self.attached, {"t": "stderr", "line": line})

    async def _spawn(self) -> None:
        if sdk_importable():
            from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

            async def _no_prompt():
                if False:
                    yield {}
            self.transport = SubprocessCLITransport(prompt=_no_prompt(), options=spec_to_options(self.spec, self._on_stderr))
            await self.transport.connect()
            self.cli_pid = self.transport._process.pid
            self.log("cli-spawned", transport="sdk", cliPid=self.cli_pid)
        else:
            self.transport = PipeCliTransport(self.spec, self._on_stderr)
            await self.transport.connect()
            self.cli_pid = self.transport.pid
            self.log("cli-spawned", transport="pipe-fallback", cliPid=self.cli_pid)
        self.cli_start = self.lease_api["proc_start"](self.cli_pid)
        self.cli_spawned_at = int(self.now())
        self._write_lease()

    async def _adopt(self) -> None:
        """The re-exec road's spawn: the CLI this process already holds, over the descriptors the handoff names (confirmed
        against the CLI's own table first); the handoff file goes. The lease with THIS code's version is written by run()
        once the socket is served, so a kernel that reads the new version finds a listener (round two of the review: the
        lease came first, and a kernel could pass its wait and connect to a path nobody served yet)."""
        self.transport = AdoptedCliTransport(self.spec, self._on_stderr, self.reexec)
        await self.transport.connect()
        if self.cli_start is None:
            self.cli_start = self.lease_api["proc_start"](self.cli_pid)
        try:
            os.unlink(str(self.dir / "reexec.json"))
        except OSError:
            pass

    def _reexec_check(self, frame: dict):
        """(python, launcher) for a kernel's re-exec request, or a refusal reason: both paths must exist, and the CLI's
        descriptors must be found (and, on Linux, confirmed against the CLI's own table)."""
        python, launcher = str(frame.get("python") or ""), str(frame.get("launcher") or "")
        if not python or not os.path.isfile(python) or not os.access(python, os.X_OK):
            return None, "no such interpreter"
        if not launcher or not os.path.isfile(launcher):
            return None, "no such launcher"
        if self.transport is None or self.cli_pid is None or self.exit_info is not None:
            return None, "no CLI to hand over"
        if self._reexec_running:
            return None, "a re-exec is in progress"
        if cli_pipe_fds(self.transport) is None:
            return None, "the CLI's descriptors could not be confirmed"
        return (python, launcher), None

    async def _reexec_now(self, python: str, launcher: str) -> None:
        """The handover. Hold the stdout reader (no further record is taken off the CLI; bytes not yet read stay in the
        pipe, which survives the exec), drain what is queued for the CLI and for the disk, drain the attached kernel's
        socket backlog (bounded), and then, with NO await from here to the exec, decide: the CLI is quiet (no turn
        re-opened, the reader's stream buffer holds no bytes) or the exec is deferred to the next result, the event, the
        reader released meanwhile. A kernel that does not drain within the bound defers it too. Quiet: any record the
        reader took meanwhile (its read was already in flight) written synchronously so the handoff's count and the
        journal agree; the handoff file; the descriptors marked inheritable; `reexec-now` written to the kernel, whose
        backlog is empty, so the frame goes to the socket at once; the socket closed; the exec of this same process into
        the kernel's code. A failure before the exec is a `reexec-failed` line, a fault to the kernel, and the host goes
        on as it was. Round two of the review: the reader ran on through the handover, so records read after the flush
        were counted in the handoff and lost with the process; round three: the quiet check preceded the drain's await,
        so output arriving while a slow kernel drained entered the stream buffer after the check and was lost."""
        self._reexec_pending = None
        self._reexec_running = True
        told = False
        limits_set = False
        written: list = []
        w = self.attached
        try:
            self._reader_hold.clear()
            flushed = asyncio.Event()
            self._stdin_q.put_nowait(("flush", flushed)); await asyncio.wait_for(flushed.wait(), 30)
            landed = asyncio.Event()
            self._journal_q.put_nowait(("flush", landed)); await asyncio.wait_for(landed.wait(), 30)
            fds = cli_pipe_fds(self.transport)
            if fds is None:
                raise RuntimeError("descriptors")
            if w is not None:
                # the kernel's backlog first, to empty (the buffer limits at zero, so drain waits for the last byte): the
                # frame written after the check then reaches the socket at once, and nothing is decided under an await
                try:
                    w.transport.set_write_buffer_limits(0, 0); limits_set = True
                    await asyncio.wait_for(w.drain(), REEXEC_DRAIN_S)
                except asyncio.TimeoutError:
                    self._defer(python, launcher, "kernel-behind")
                    return
                except Exception:
                    pass                                    # a closed or failed writer: nothing to drain; the frame goes nowhere
            pending = self._stdout_pending_bytes()          # no await from here to the exec
            if self.inflight > 0 or pending:
                self._defer(python, launcher, "output-arriving", pendingBytes=pending)
                return
            for off in sorted(self._unwritten):
                if off == self.journal.next_offset:
                    self.journal.append(self._unwritten[off])
                    written.append(off)
            if self._read_count != self.journal.next_offset:
                raise RuntimeError("count")
            handoff = {"cli_pid": int(self.cli_pid), "cli_start": self.cli_start, "cli_spawned_at": self.cli_spawned_at, "fsid": self.fsid,
                       "fds": fds, "read_count": self._read_count, "inflight": self.inflight, "acked": self.journal.acked,
                       "parked": [{"offset": v["offset"], "t": v["t"], "record": v["record"]} for v in self.parked.open.values()],
                       "answered": sorted(self.parked.answered), "version_from": self.version, "t": self.now()}
            hpath = self.dir / "reexec.json"
            fd = os.open(str(hpath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(handoff, f)
            for fdn in fds.values():
                os.set_inheritable(int(fdn), True)
            buffered = None
            if w is not None and self.attached is w:
                told = True
                self._send(w, {"t": "reexec-now"})
                try:
                    buffered = w.transport.get_write_buffer_size()   # 0: the frame is in the socket; else the kernel's socket is full
                except Exception:
                    buffered = None
            self.log("reexec", python=python, launcher=launcher, journalNext=self.journal.next_offset, frameBuffered=buffered)
            self.journal.close()
            self._server.close()
            argv = [python, launcher, str(self.spec_path), "--reexec", str(hpath)]
            os.execv(python, argv)                          # the same pid, the same children, the descriptors marked above
        except Exception as e:
            self.log("reexec-failed", error=type(e).__name__, at=self._where(e))
            self._journal_skip.update(written)
            try:
                os.unlink(str(self.dir / "reexec.json"))
            except OSError:
                pass
            try:                                            # the exec did not happen: the journal reopens and the socket serves again
                if self.journal._fh is None:
                    self.journal = Journal.reopen(self.dir)
                if self._server is not None and self._server.is_serving() is False:
                    self._server = await asyncio.start_unix_server(self._on_client, path=str(self.sock_path))
                    os.chmod(self.sock_path, 0o600)
            except Exception as e2:
                self.log("reexec-recover-failed", error=type(e2).__name__)
            if self.attached is not None:
                self._send(self.attached, {"t": "fault", "kind": "reexec-failed", "text": type(e).__name__})
                if told:                                    # the kernel plans a reconnect: give it the stream's end it waits for
                    w2 = self.attached
                    self._detach(w2)
                    try:
                        w2.close()
                    except Exception:
                        pass
        finally:
            self._reexec_running = False
            if limits_set and w is not None and not w.is_closing():
                try:
                    w.transport.set_write_buffer_limits()
                except Exception:
                    pass
            if self._reader_hold is not None:
                self._reader_hold.set()

    def _defer(self, python: str, launcher: str, reason: str, **fields) -> None:
        """The handover waits for the next result (the event) and the reader runs on meanwhile: output arriving (a turn
        re-opened, bytes of a record not yet parsed) or a kernel that did not drain its socket within the bound."""
        self._reexec_pending = (python, launcher)
        self._reader_hold.set()
        self.log("reexec-deferred", reason=reason, inflight=self.inflight, **fields)

    def _stdout_pending_bytes(self):
        """Bytes read off the CLI's stdout pipe and not yet parsed into a record, as far as the transport shows them (the
        asyncio stream's buffer, under both transports); None when there is no such buffer to read. Bytes still in the
        pipe survive an exec; these would not, so the handover waits while there are any. Not visible here: a partial
        line the SDK's own framer holds between chunks, which only a record the CLI is mid-write on can leave."""
        proc = getattr(self.transport, "_process", None) or getattr(self.transport, "proc", None)
        aproc = getattr(proc, "_process", None) or proc
        buf = getattr(getattr(aproc, "stdout", None), "_buffer", None)
        return len(buf) if buf is not None else None

    async def _read_cli(self) -> None:
        """The stdout reader: never pauses for the disk (the writer task journals), never dies on one
        record's handling (a fault is a host.log row, the reading goes on). The stream's end is the CLI's
        exit; nothing else is."""
        try:
            stream = self.transport.read_messages().__aiter__()
            while True:
                if self._reader_hold is not None and not self._reader_hold.is_set():
                    await self._reader_hold.wait()      # the handover's hold: no record taken off the CLI while it is decided
                try:
                    msg = await stream.__anext__()
                except StopAsyncIteration:
                    break
                off = self._read_count
                self._read_count = off + 1
                try:
                    self._unwritten[off] = msg
                    self._journal_q.put_nowait((off, msg))
                    self._track(msg, off)
                    if self._read_count - self.journal.next_offset > self.reader_behind_records and not self._reader_behind_noted:
                        self._reader_behind_noted = True
                        self.log("reader-behind", read=self._read_count, journaled=self.journal.next_offset)
                        if self.attached is not None:
                            self._send(self.attached, {"t": "fault", "kind": "reader-behind", "text": "the journal lags the CLI's output"})
                    if self.attached is not None:
                        if self._replaying and off >= self._replay_end:
                            self._live_backlog.append((off, msg))
                        elif not self._replaying:
                            self._send(self.attached, {"t": "out", "offset": off, "data": msg})
                except Exception as e:
                    self.log("record-handling-failed", offset=off, error=type(e).__name__, at=self._where(e))
        except Exception as e:
            self.log("cli-stream-ended", error=type(e).__name__, at=self._where(e))
        cause = self.ending[1] if self.ending is not None else "died"
        code = getattr(getattr(self.transport, "_process", None), "returncode", None)
        if code is None:
            code = getattr(self.transport, "returncode", None)
        await self._journal_q.put(None)           # the writer drains what it has, then stops
        self.exit_info = {"t": "exit", "code": code, "signal": None, "cause": cause}
        self.log("cli-exited", code=code, cause=cause)
        if self._stop is not None:
            self._stop.set()

    async def _journal_writer(self) -> None:
        """The one writer of the journal, off the reader's path. A write that raises (a full disk, a bad
        descriptor) is a `journal-write-failed` row and a fault frame, and the reading and the live
        forwarding go on: a journal fault is never the CLI's death. A record read but not yet written sits in
        `_unwritten` until it lands, so an attach can send it from memory instead of waiting for the disk."""
        delay = float(self.spec.get("_test_journal_delay_s") or 0)
        fault_at = self.spec.get("_test_journal_fault_at")
        gap_fault = bool(self.spec.get("_test_journal_gap_fault"))
        while True:
            item = await self._journal_q.get()
            if item is None:
                return
            if isinstance(item, tuple) and item and item[0] == "flush":   # the re-exec's drain: everything ahead is on disk
                item[1].set()
                continue
            off, msg = item
            if off in self._journal_skip:                   # written by a handover that then failed: on disk already
                self._journal_skip.discard(off)
                self._unwritten.pop(off, None)
                continue
            if delay:
                await asyncio.sleep(delay)
            try:
                if fault_at is not None and int(fault_at) == off:
                    raise OSError(28, "test seam: the journal write fails at offset %d" % off)
                got = self.journal.append(msg)
                if got != off:
                    self.log("journal-offset-drift", expected=off, got=got)
            except Exception as e:
                self._journal_faults += 1
                self.log("journal-write-failed", offset=off, error=type(e).__name__, at=self._where(e))
                self._journal_gap_offsets.add(off)
                if self.attached is not None:
                    self._send(self.attached, {"t": "fault", "kind": "journal-write-failed", "text": type(e).__name__})
                # keep the numbering with a gap marker (readers skip it): the record is lost to replay, the live
                # kernel already has it; a marker that fails too becomes a zero-length index entry, so index and
                # offsets stay in lockstep and no later replay can trip over the hole
                try:
                    if gap_fault:
                        raise OSError(28, "test seam: the gap marker write fails too")
                    self.journal.append({"type": GAP_TYPE, "offset": off, "error": type(e).__name__})
                except Exception as e2:
                    self.log("journal-gap-unrecorded", offset=off, error=type(e2).__name__)
                    self.journal.note_gap(off)
            finally:
                self._unwritten.pop(off, None)

    def _track(self, msg: dict, off: int) -> None:
        """Bookkeeping per message: the fsid from the init, the turn count, open requests."""
        mt = msg.get("type")
        if mt == "system" and msg.get("subtype") == "init" and msg.get("session_id"):
            if str(msg["session_id"]) != self.fsid:
                self.fsid = str(msg["session_id"])
                self._write_lease()
        elif mt == "result":
            # ONE result closes everything fed: the CLI folds messages queued while it works into the running turn
            # and answers them with a single result, so a fed-minus-resulted count stays above zero forever after a
            # fold (the kernel's own settle learned this on 2026-07-09; the host re-created it, and every attach then
            # adopted the stale count from the hello: a session that read Ready yet swallowed every send, 2026-09-18).
            # A line the CLI runs as a SEPARATE turn instead re-opens the count from the output side below: its first
            # assistant or user row after this result (its own user line was counted before the result, so nothing on
            # the input side can tell a fold from a queue; the output can).
            self.inflight = 0
            self.idle_since = self.now()
            if self._reexec_pending is not None:                  # a kernel asked mid-turn: the turn's end is the event
                python, launcher = self._reexec_pending
                asyncio.ensure_future(self._reexec_now(python, launcher))
        elif mt in ("assistant", "user") and self.inflight == 0:
            self.inflight = 1                              # output arriving with no turn counted: a queued line running as its own turn
            self.log("turn-reopened", offset=off)
        elif mt == "control_request":
            self.parked.park(msg, off, self.now(), attached=self.attached is not None)
            self.log("request-open", requestId=str(msg.get("request_id") or ""),
                     subtype=str((msg.get("request") or {}).get("subtype") or ""), attached=self.attached is not None)
        elif mt == "control_cancel_request":
            if self.parked.cancel(str(msg.get("request_id") or "")):
                self.log("request-cancelled", requestId=str(msg.get("request_id") or ""))

    async def _self_answer_loop(self) -> None:
        while self.exit_info is None:
            await asyncio.sleep(1.0)
            if self.attached is not None:
                continue
            for rid in self.parked.due_hooks(self.now()):
                entry = self.parked.open.get(rid) or {}
                event = entry.get("event") or ""
                frame = neutral_hook_response(rid, event)
                try:
                    await self.transport.write(json.dumps(frame) + "\n")
                except Exception as e:
                    self.log("self-answer-failed", requestId=rid, error=type(e).__name__)
                    continue
                since = entry.get("unattached_since") or self.now()
                self.parked.answer(rid)
                self.log("hook-self-answered", requestId=rid, event=event, callbackId=str(entry.get("callback_id") or ""),
                         toolUseId=str(entry.get("tool_use_id") or ""), parkedS=round(self.now() - float(since), 1))

    async def _grace_loop(self) -> None:
        while self.exit_info is None:
            await asyncio.sleep(1.0)
            now = self.now()
            if self.ending is not None:
                if now >= self.ending[0]:
                    self.ending = (float("inf"), "end-forced")
                    self.log("end-forced", cliPid=self.cli_pid)
                    try:
                        os.kill(int(self.cli_pid), signal.SIGKILL)
                    except (ProcessLookupError, TypeError):
                        pass
                continue
            if self.attached is None and self.inflight == 0 and now - self.idle_since >= self.grace_s:
                self.log("unattached-grace-expired", idleS=round(now - self.idle_since, 1))
                await self._end(END_GRACE_DEFAULT_S, "eof-grace")

    async def _end(self, grace: float, cause: str) -> None:
        """Graceful end: close the CLI's stdin (through the stdin pump, so every line queued before it is
        written first: an answer followed by a kill must reach the CLI) and let the CLI finish. A second end
        with a shorter grace pulls the deadline in; a longer one never pushes it out."""
        deadline = self.now() + float(grace)
        if self.ending is not None:
            if deadline < self.ending[0]:
                self.ending = (deadline, self.ending[1])
                self.log("end-grace-shortened", graceS=float(grace))
            return
        self.ending = (deadline, cause)
        self.log("end-requested", cause=cause, graceS=float(grace))
        self._stdin_q.put_nowait(END_SENTINEL)

    # ── the socket ──
    def _send(self, writer, frame: dict) -> None:
        try:
            writer.write(encode_frame(frame))
        except Exception:
            pass

    async def _flush(self, writer, timeout: float = EXIT_FLUSH_S) -> None:
        """Bounded drain of a writer: the tail of `out` frames and the exit frame must reach a slow kernel."""
        try:
            await asyncio.wait_for(writer.drain(), timeout=timeout)
        except Exception:
            pass

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One kernel connection. Frames are dispatched as they arrive; `in` lines go to the stdin pump
        (their own task), so a `signal` or an `end` behind a large `in` is acted on at once even when the
        CLI's stdin pipe is full."""
        fr = FrameReader()
        attached_here = False
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                for frame in fr.feed(chunk):
                    t = frame.get("t")
                    if t == "attach":
                        if self.attached is not None and self.attached is not writer:
                            self._send(writer, {"t": "busy", "kernel": self.kernel})
                            continue
                        attached_here = True
                        await self._attach(writer, frame)
                    elif t == "ping":
                        self._send(writer, {"t": "pong"})
                    elif t == "reexec":
                        target, why = self._reexec_check(frame)
                        if target is None:
                            self.log("reexec-refused", reason=why)
                            self._send(writer, {"t": "reexec", "ok": False, "reason": why})
                        elif self.inflight > 0:
                            self._reexec_pending = target
                            self.log("reexec-deferred", inflight=self.inflight)
                            self._send(writer, {"t": "reexec", "ok": True, "when": "at-turn-end"})
                        else:
                            self._send(writer, {"t": "reexec", "ok": True, "when": "now"})
                            try:
                                await writer.drain()
                            except Exception:
                                pass
                            asyncio.ensure_future(self._reexec_now(*target))
                    elif not attached_here:
                        self._send(writer, {"t": "fault", "kind": "not-attached", "text": "attach first"})
                    elif t == "in":
                        self._queue_in(str(frame.get("data") or ""))
                    elif t == "ack":
                        try:
                            self.journal.ack(int(frame.get("offset")))
                        except (TypeError, ValueError):
                            pass
                    elif t == "signal":
                        sig = {"INT": signal.SIGINT, "KILL": signal.SIGKILL, "TERM": signal.SIGTERM}.get(str(frame.get("sig") or "").upper())
                        if sig is not None and self.cli_pid:
                            try:
                                os.kill(int(self.cli_pid), sig)
                                self.log("signalled", sig=str(frame.get("sig")))
                            except ProcessLookupError:
                                pass
                    elif t == "end":
                        g = frame.get("grace")
                        await self._end(float(g) if g is not None else END_GRACE_DEFAULT_S, "end")
                    elif t == "detach":
                        self.log("detached", kernelPid=(self.kernel or {}).get("pid"))
                        self._detach(writer)
                        attached_here = False
                        return
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        except Exception as e:
            # a fault in the host's own handling of this kernel (never the CLI's): say so on the socket and in the
            # log, close this connection, keep the host and its CLI (the commit-5 review's finding b)
            self.log("client-loop-failed", error=type(e).__name__, at=self._where(e))
            self._send(writer, {"t": "fault", "kind": "host-fault", "text": "%s at %s" % (type(e).__name__, self._where(e))})
        finally:
            if attached_here and self.attached is writer:
                self.log("kernel-lost", kernelPid=(self.kernel or {}).get("pid"))
                self._detach(writer)
            try:
                writer.close()
            except Exception:
                pass

    def _detach(self, writer) -> None:
        if self.attached is writer:
            self.attached = None
            self.kernel = None
            self.parked.detached(self.now())
            self.idle_since = self.now() if self.inflight == 0 else self.idle_since

    async def _attach(self, writer, frame: dict) -> None:
        self.kernel = frame.get("kernel") if isinstance(frame.get("kernel"), dict) else {}
        try:
            ack = int(frame.get("ack", ACK_NONE))
        except (TypeError, ValueError):
            ack = ACK_NONE
        # Attached FIRST, so every record the reader takes from here on goes to the live backlog; the replay
        # covers the records read before this moment: from the journal as far as the writer has landed them,
        # from memory (`_unwritten`) for the rest. No settle wait, nothing dropped, no record twice (the
        # commit-5 review's finding c).
        self.attached = writer
        self._replaying = True
        self._live_backlog = []
        read_at_attach = self._read_count
        self._replay_end = read_at_attach
        self.parked.attached()
        self.log("attached", kernelPid=self.kernel.get("pid"), ack=ack, next=read_at_attach)
        self._send(writer, {"t": "hello", "protocol": PROTOCOL_VERSION,
                            "host": {"pid": os.getpid(), "start": self.lease_api["proc_start"](os.getpid()) or "", "version": self.version},
                            "cli": {"pid": self.cli_pid, "start": self.cli_start, "fsid": self.fsid,
                                    "spawnedAt": self.cli_spawned_at,                 # the CLI's epoch, the kernel's reg copies it
                                    "login": str(self.spec.get("login") or "")},       # the login identifier the launch billed
                            "journal": {"next": read_at_attach}, "parked": self.parked.ids(),
                            "inflight": self.inflight,      # the open turns, so an attaching kernel knows it is mid-turn
                            "exited": self.exit_info is not None})
        # each offset is resolved at ITS moment: from memory while unwritten, from the journal once landed, so
        # a record the writer lands during a drain yield is never between two snapshots (finding 4 of the
        # commit 6-7 review); a gap (a failed write) yields nothing and the parked table covers a request there
        if self.spec.get("_test_socket_small_buffers"):
            # a test seam: tiny send buffers, so the replay's drain below actually WAITS on a kernel that is not
            # reading yet and the writer lands records during that wait (the shape that lost records to a replay
            # built from two snapshots; the commit-8 review's item 9)
            sock = writer.transport.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
            writer.transport.set_write_buffer_limits(high=4096)
        n = 0
        for off in range(max(ack + 1, 0), read_at_attach):
            rec = self._unwritten.get(off)
            if rec is None:
                rec = next((r for _, r in self.journal.read_from(off, off + 1)), None)
            if rec is not None:
                self._send(writer, {"t": "out", "offset": off, "data": rec})
            n += 1
            if n % 200 == 0:
                await writer.drain()
        # every request still open is sent again FROM THE TABLE, whatever the acknowledged offset says and
        # whether or not its journal write landed: a kernel that received it and died never answered, and the
        # new kernel's Query must see it to answer it (findings 4 and e)
        for off, rec in self.parked.records():
            if off <= ack or off in self._journal_gap_offsets:
                self._send(writer, {"t": "out", "offset": off, "data": rec})
        for off, rec in self._live_backlog:
            self._send(writer, {"t": "out", "offset": off, "data": rec})
        self._live_backlog = []
        self._replaying = False
        if self.exit_info is not None:
            self._send(writer, self.exit_info)
        await writer.drain()

    def _queue_in(self, data: str) -> None:   # (the turn-opening test is _opens_turn, module level, so the kernel's tests can pin it)
        """One line from the kernel for the CLI's stdin: bookkeeping now, the write on the stdin pump. A user
        message opens a turn; a control_response for a request already answered is dropped."""
        try:
            obj = json.loads(data)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            if obj.get("type") == "user" and _opens_turn(obj):
                self.inflight += 1
            elif obj.get("type") == "control_response":
                rid = str(((obj.get("response") or {}).get("request_id")) or "")
                if rid in self.parked.answered:
                    self.log("late-answer-dropped", requestId=rid)
                    return
                self.parked.answer(rid)
        self._stdin_q.put_nowait(data if data.endswith("\n") else data + "\n")

    async def _stdin_pump(self) -> None:
        """The one writer of the CLI's stdin: a full pipe blocks this task alone, never the socket reader. The
        end sentinel closes stdin in its turn, after every line queued ahead of it."""
        while True:
            data = await self._stdin_q.get()
            if data is None:
                return
            if isinstance(data, tuple) and data and data[0] == "flush":   # the re-exec's drain: everything ahead reached the CLI
                data[1].set()
                continue
            if data is END_SENTINEL:
                try:
                    await self.transport.end_input()
                except Exception as e:
                    self.log("end-input-failed", error=type(e).__name__)
                continue
            try:
                await self.transport.write(data)
            except Exception as e:
                self.log("write-failed", error=type(e).__name__)
                if self.attached is not None:
                    self._send(self.attached, {"t": "fault", "kind": "write-failed", "text": type(e).__name__})

    # ── life ──
    async def run(self) -> int:
        self._stop = asyncio.Event()
        self._journal_q = asyncio.Queue()
        self._stdin_q = asyncio.Queue()
        self.log("host-started", hostPid=os.getpid())
        try:                                            # the identity hostAck is keyed by, for a reader with no hello
            (self.dir / "identity.json").write_text(json.dumps({"pid": os.getpid(), "start": self.lease_api["proc_start"](os.getpid()) or ""}))
        except OSError:
            pass
        # the CLI first, the socket second: a kernel that finds the socket finds a CLI behind it (an attach
        # before the spawn would report no CLI pid and fail its first write)
        try:
            if self.reexec:
                await self._adopt()
            else:
                await self._spawn()
        except Exception as e:
            self.log("cli-adopt-failed" if self.reexec else "cli-spawn-failed", error=type(e).__name__, at=self._where(e))
            self.exit_info = {"t": "exit", "code": None, "signal": None, "cause": "spawn-failed", "error": type(e).__name__}
            return 1
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.sock_path.unlink()
        except OSError:
            pass
        self._server = await asyncio.start_unix_server(self._on_client, path=str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        self.log("socket-ready", sock=str(self.sock_path.name))
        if self.reexec:                                 # the new version's lease AFTER the socket: a reader of the lease finds a listener
            self._write_lease()
            self.log("reexeced", cliPid=self.cli_pid, version=self.version, journalNext=self.journal.next_offset, readCount=self._read_count)
        self._reader_hold = asyncio.Event()
        self._reader_hold.set()
        tasks = [asyncio.ensure_future(self._journal_writer()), asyncio.ensure_future(self._read_cli()),
                 asyncio.ensure_future(self._beat()), asyncio.ensure_future(self._self_answer_loop()),
                 asyncio.ensure_future(self._grace_loop()), asyncio.ensure_future(self._stdin_pump())]
        await self._stop.wait()
        # the CLI is gone: the writer drains, then an attached kernel gets the tail and the exit frame, drained
        try:
            await asyncio.wait_for(tasks[0], timeout=EXIT_FLUSH_S)
        except Exception:
            pass
        if self.attached is not None:
            self._send(self.attached, self.exit_info)
            await self._flush(self.attached)
        await self._stdin_q.put(None)
        for t in tasks:
            t.cancel()
        try:
            await self.transport.close()          # the pipes and the process object, before the loop closes
        except Exception:
            pass
        try:
            self.lease_api["remove_lease"](self.state_dir, self.sid)
        except Exception:
            pass
        self.journal.close()
        self._server.close()
        try:
            self.sock_path.unlink()
        except OSError:
            pass
        self.log("host-exited")
        return 0


def _lease_api() -> dict:
    """The stage 1 lease helpers from kernel/sdk_backend.py, loaded the kernel's way (a file-path load under
    a stable module name; the kernel's own copy when this runs inside the kernel)."""
    import importlib.util
    here = Path(__file__).resolve().parent
    sb = sys.modules.get("romp_sdk_backend")
    if sb is None:
        spec = importlib.util.spec_from_file_location("romp_loadsource", str(here / "loadsource.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        sb = mod.load_source("romp_sdk_backend_hostside", here / "sdk_backend.py")
    return {"write_lease": sb.write_lease, "remove_lease": sb.remove_lease, "proc_start": sb.proc_start}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[1] == "--reexec":
        host = SessionHost(argv[0], reexec_path=argv[2])
    elif len(argv) == 1:
        host = SessionHost(argv[0])
    else:
        sys.stderr.write("usage: romp-session-host <spawn.json> [--reexec <reexec.json>]\n")
        return 2
    try:
        return asyncio.run(host.run())
    except Exception:
        host.log("host-crashed", error=traceback.format_exc().splitlines()[-1][:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
