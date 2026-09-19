#!/usr/bin/env python3
"""The kernel's side of the per-session host (stage 4 of #1317, T315; kernel/session_host.py is the host,
the T315 design note is the design): a Transport the SDK client drives over the host's Unix socket, the
same class over an orphan journal file (one consumer path for live attach and for replay after a host
death), the spawn specification the kernel writes for a host, the settings (the session-hosts toggle, on by
default, and the host grace), and the host-lease classification the backend attaches by.

The SDK's `Transport` is documented as unstable; `HostTransport` implements its six methods (connect,
write, read_messages, close, is_ready, end_input) and a test pins the set against the abstract class.
When the SDK is not importable the class still exists, duck-typed, so the pure parts test without it.
"""
from __future__ import annotations
import asyncio
import collections
import json
import os
import re
import time
from pathlib import Path

try:  # the SDK's abstract base when present; a plain object otherwise (the six methods are the contract)
    from claude_agent_sdk._internal.transport import Transport as _Base    # type: ignore
    from claude_agent_sdk._errors import CLIConnectionError, ProcessError   # type: ignore
except Exception:  # pragma: no cover - the SDK-less test venv
    class _Base:  # type: ignore
        pass

    class CLIConnectionError(Exception):  # type: ignore
        pass

    class ProcessError(Exception):  # type: ignore
        def __init__(self, message, exit_code=None, stderr=None):
            super().__init__(message)
            self.exit_code = exit_code
            self.stderr = stderr

import importlib.util
import sys
_HERE = Path(__file__).resolve().parent
_ls_spec = importlib.util.spec_from_file_location("romp_loadsource", str(_HERE / "loadsource.py"))
_ls_mod = importlib.util.module_from_spec(_ls_spec)
_ls_spec.loader.exec_module(_ls_mod)
load_source = _ls_mod.load_source
sh = sys.modules.get("romp_session_host") or load_source("romp_session_host", _HERE / "session_host.py")

# ── settings (bare value files under the state directory, like tmux-backend) ─────────────────────
SESSION_HOSTS_SETTING = "session-hosts"            # the toggle: "off" (or 0 / false / no) turns hosts off on this
                                                   # machine; "on", or no file at all, leaves them on (on by default
                                                   # since T348, the user 2026-09-11; off by default before)
SESSION_HOST_GRACE_SETTING = "session-host-grace"  # seconds an unattached idle CLI lives (default 900)
HOST_SCOPE_PREFIX = "romp-host-"
_HOST_SCOPE_RE = re.compile(r"romp-host-([0-9a-fA-F]{1,8})-(\d+)\.scope\Z")
ACK_BATCH = 64            # acknowledge at least every this many records…
ACK_INTERVAL_S = 0.1      # …or this often
SOCKET_WAIT_S = 20.0      # how long a spawn waits for the host's socket before it is a launch failure


def _setting(state_dir, name: str, default: str) -> str:
    try:
        v = (Path(state_dir) / name).read_text().strip()
    except OSError:
        return default
    return v or default


SESSION_HOSTS_ON_WORDS = ("on", "1", "true", "yes")


def session_hosts_read(state_dir) -> "tuple[bool, str]":
    """ONE read of the setting: (on, value). `value` is the file's stripped text, "" with no file. On unless the file
    says otherwise: a machine with no file is on; an empty file (or one holding only whitespace) is the default, on; a
    file saying off, 0, false or no is the toggle; any other word reads as off too. A caller that decides and then logs
    reads once through this, so the decision and the value it names agree (a flip between two reads cannot contradict)."""
    value = _setting(state_dir, SESSION_HOSTS_SETTING, "")
    return (True if not value else value.lower() in SESSION_HOSTS_ON_WORDS), value


def session_hosts_on(state_dir) -> bool:
    """Whether NEW sessions start through a host (session_hosts_read's verdict). Read at each connect, so a flip needs no
    restart: a plain-child session becomes hosted at its next respawn, a new one at once."""
    return session_hosts_read(state_dir)[0]


def session_host_grace_s(state_dir) -> float:
    try:
        v = float(_setting(state_dir, SESSION_HOST_GRACE_SETTING, str(sh.UNATTACHED_GRACE_DEFAULT_S)))
    except ValueError:
        return sh.UNATTACHED_GRACE_DEFAULT_S
    return v if v > 0 else sh.UNATTACHED_GRACE_DEFAULT_S


def host_dir(state_dir, sid: str) -> Path:
    return Path(state_dir) / "hosts" / str(sid)


def host_sock(state_dir, sid: str) -> Path:
    return Path(state_dir) / "hosts" / (str(sid)[:8] + ".sock")


def host_scope_unit(sid: str, t: int | None = None) -> str:
    """The host's own transient scope on Linux: `romp-host-<sid8>-<t>.scope` (the pid is not known before
    the spawn; the sweep keys on the sid's lease, not on a pid)."""
    return "%s%s-%d" % (HOST_SCOPE_PREFIX, str(sid)[:8], int(t if t is not None else time.time() * 1000))


def host_scope_units(list_lines: list[str], sids) -> dict[str, str]:
    """{unit: sid8} for the host scopes of OUR sessions in a `systemctl --user list-units` listing."""
    sid8 = {str(s)[:8].lower(): str(s) for s in sids if s}
    out = {}
    for ln in list_lines:
        head = ln.strip().split(None, 1)
        if not head:
            continue
        m = _HOST_SCOPE_RE.match(head[0])
        if m and m.group(1).lower() in sid8:
            out[head[0]] = m.group(1).lower()
    return out


# ── the spawn specification ────────────────────────────────────────────────────────────────────
def spawn_spec(opts, sid: str, name: str, state_dir, version: str, grace_s: float) -> dict:
    """The plain-data fields of a ClaudeAgentOptions the host rebuilds it from (sh.SPEC_FIELDS), plus the
    host's own keys. `permission_prompt_tool_name` is set to "stdio" when the kernel has a can_use_tool
    (the SDK client would set it from the callback; the host has no callback). Paths become strings."""
    spec = {}
    for k in sh.SPEC_FIELDS:
        v = getattr(opts, k, None)
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            spec[k] = v
        elif isinstance(v, os.PathLike):
            spec[k] = str(v)
        elif isinstance(v, (list, tuple)):
            spec[k] = [str(x) if isinstance(x, os.PathLike) else x for x in v]
        elif isinstance(v, dict):
            spec[k] = json.loads(json.dumps(v, default=str))
        else:
            spec[k] = str(v)
    if getattr(opts, "can_use_tool", None) and not spec.get("permission_prompt_tool_name"):
        spec["permission_prompt_tool_name"] = "stdio"
    spec.update({"sid": str(sid), "name": str(name), "version": str(version or ""), "state_dir": str(state_dir),
                 "protocol": sh.PROTOCOL_VERSION, "hook_timeout_s": sh.HOOK_TIMEOUT_S,
                 "hook_self_answer_s": sh.HOOK_SELF_ANSWER_S, "unattached_grace_s": float(grace_s)})
    return spec


def write_spawn_spec(state_dir, sid: str, spec: dict) -> Path:
    """`hosts/<sid>/spawn.json`, the directory at 0700 and the file at 0600: the spec carries the
    environment overlay."""
    d = host_dir(state_dir, sid)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    p = d / "spawn.json"
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(spec, f)
    os.chmod(p, 0o600)
    return p


# ── host-lease classification ──────────────────────────────────────────────────────────────────
def host_lease_state(lease, now: float, start=None) -> str:
    """'attach' when a valid lease is held by a host (holder.kind == 'host'); 'orphan' when a host-held
    lease does not hold (its host or CLI is gone, or its beat is stale) and a journal may need replaying;
    'none' when there is no host lease at all (no lease, or one held by a kernel)."""
    if not isinstance(lease, dict):
        return "none"
    holder = lease.get("holder") if isinstance(lease.get("holder"), dict) else {}
    if holder.get("kind") != "host":
        return "none"
    sb = sys.modules.get("romp_sdk_backend") or load_source("romp_sdk_backend_leases", _HERE / "sdk_backend.py")
    return "attach" if sb.lease_state(lease, now, start) == "valid" else "orphan"


# ── the transport ──────────────────────────────────────────────────────────────────────────────
async def request_reexec(sock_path, python: str, launcher: str, version: str, timeout: float = 10.0) -> dict:
    """Ask the host behind `sock_path` to re-exec itself into the code at `launcher` under `python` (the kernel's own):
    one connection, one `reexec` frame, one answer, then closed. The answer is the host's `reexec` frame ({"ok": True,
    "when": "now" | "at-turn-end"} or {"ok": False, "reason"}); a host that closes without one, one older than the frame
    (a `fault` answer), or no answer within `timeout` reads as {"ok": False, "reason": ...}. Never raises."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(sock_path)), timeout)
    except Exception as e:
        return {"ok": False, "reason": "connect: %s" % type(e).__name__}
    try:
        writer.write(sh.encode_frame({"t": "reexec", "python": str(python), "launcher": str(launcher), "version": str(version)}))
        await writer.drain()
        fr = sh.FrameReader()
        deadline = time.time() + timeout
        while time.time() < deadline:                      # loop-ok: a bounded read for the one answer frame
            try:
                chunk = await asyncio.wait_for(reader.read(65536), max(0.05, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            if not chunk:
                return {"ok": False, "reason": "the host closed the socket without an answer"}
            for f in fr.feed(chunk):
                if f.get("t") == "reexec":
                    return {"ok": bool(f.get("ok")), "when": f.get("when"), "reason": f.get("reason")}
                if f.get("t") == "fault":
                    return {"ok": False, "reason": "the host does not know the request (%s)" % f.get("kind")}
        return {"ok": False, "reason": "no answer within %.0f s" % timeout}
    finally:
        try:
            writer.close()
        except Exception:
            pass


class HostTransport(_Base):
    """The SDK's Transport over the host's socket (live) or over an orphan journal (replay).

    Live: `connect()` opens the socket, sends `attach` with the acknowledged offset and waits for
    `hello` (a `busy` answer raises CLIConnectionError); `read_messages()` yields every `out` frame's
    record in order, replay then live, acknowledging batches back to the host and to `on_ack`; `write()`
    forwards a line as an `in` frame at once; `end_input()` sends `end` with this transport's grace,
    unless the transport is in DETACH mode, where the kernel is leaving and the host keeps the CLI;
    `close()` sends `detach` in detach mode, else `end` and a bounded wait for the exit frame. `stderr`,
    `fault` and `exit` frames go to their callbacks; an `exit` frame ends the read stream (a non-zero code
    raises ProcessError, as the subprocess transport does).

    Replay (`HostTransport.from_journal`): `read_messages()` yields the journal's records from the offset
    to the end and then ends; `write()` of a control_request synthesizes the success response the Query
    waits on (no CLI is there), every other write is dropped and counted; end_input and close are no-ops.
    The session's receive loop is the same either way, which is the point."""

    def __init__(self, sock_path=None, *, kernel=None, ack=sh.ACK_NONE, end_grace=sh.END_GRACE_DEFAULT_S,
                 on_ack=None, on_hello=None, on_stderr=None, on_exit=None, on_fault=None, journal_dir=None, on_reexec=None):
        self.sock_path = str(sock_path) if sock_path else None
        self.kernel = dict(kernel or {})
        self.ack_offset = int(ack)
        self.replay_end = None          # the journal's next offset at the attach (the hello's journal.next): records before
        #                                 it are the replay, records from it on are live (T354's spend fold)
        self.result_tags = collections.deque()   # one tag per RESULT record handed over, in order: {"offset", "replay"};
        #                                 the consumer reads the transport through a buffered stream a record ahead, so the
        #                                 transport's current offset is never the handled record's; the tag is
        self.end_grace = float(end_grace)
        self.on_ack, self.on_hello, self.on_stderr, self.on_exit, self.on_fault = on_ack, on_hello, on_stderr, on_exit, on_fault
        self.on_reexec = on_reexec      # the host's `reexec-now`: it is about to exec into the kernel's code and close this socket
        self.journal_dir = str(journal_dir) if journal_dir else None
        self.detach_mode = False
        self.hello = None
        self.exit_info = None
        self._reader = None
        self._writer = None
        self._ready = False
        self._closed = False
        self._synth: asyncio.Queue | None = None
        self.dropped_writes = 0
        self._last_ack_sent = self.ack_offset
        self._last_ack_t = 0.0
        self._early: list = []          # frames that arrived with hello, before the reader started
        self._fr = sh.FrameReader()
        self._init_answered = False
        self._my_requests: set = set()  # control_request ids this transport wrote (the Query's initialize first)
        self._hold: list = []           # replayed records held until the initialize's answer has been yielded
        self._init_pending = True       # live mode: the Query's initialize has not been answered yet

    @classmethod
    def from_journal(cls, journal_dir, ack=sh.ACK_NONE, **kw):
        return cls(None, ack=ack, journal_dir=journal_dir, **kw)

    # ── Transport ──
    async def connect(self) -> None:
        if self.journal_dir:
            self._synth = asyncio.Queue()
            self._ready = True
            return
        self._reader, self._writer = await asyncio.open_unix_connection(self.sock_path)
        self._writer.write(sh.encode_frame({"t": "attach", "kernel": self.kernel, "ack": self.ack_offset}))
        await self._writer.drain()
        fr = sh.FrameReader()
        hello = None
        while hello is None:
            chunk = await self._reader.read(65536)
            if not chunk:
                raise CLIConnectionError("the host closed the socket before hello")
            for f in fr.feed(chunk):
                if f.get("t") == "hello":
                    hello = f
                elif f.get("t") == "busy":
                    raise CLIConnectionError("another kernel is attached to this host: %r" % (f.get("kernel"),))
                else:
                    self._early.append(f)
        self.hello = hello
        try:
            self.replay_end = int(((hello or {}).get("journal") or {}).get("next"))
        except (TypeError, ValueError):
            self.replay_end = None
        self._fr = fr
        self._ready = True
        if self.on_hello:
            self.on_hello(hello)

    def is_ready(self) -> bool:
        return self._ready and not self._closed

    async def write(self, data: str) -> None:
        if self.journal_dir:
            await self._synth_write(data)
            return
        if not self._ready or self._closed:
            raise CLIConnectionError("host transport is not ready for writing")
        try:
            obj = json.loads(data)
            if isinstance(obj, dict) and obj.get("type") == "control_request" and obj.get("request_id"):
                self._my_requests.add(str(obj["request_id"]))
        except ValueError:
            pass
        self._writer.write(sh.encode_frame({"t": "in", "data": data.rstrip("\n")}))
        await self._writer.drain()

    async def end_input(self) -> None:
        if self.journal_dir or self._closed or not self._writer:
            return
        if self.detach_mode:
            return          # the kernel is leaving; the host keeps the CLI running
        await self._send({"t": "end", "grace": self.end_grace})

    async def end_and_close(self) -> None:
        """End the host's CLI and leave, from a transport that never ran a Query (the kernel's kill of a
        session with no object, T315): `end` with this transport's grace whatever the initialize gate says
        (close() alone reads an unanswered initialize as a connect that never completed and DETACHES, which
        keeps the CLI: the commit-15 review's first item), a bounded wait for the exit frame, then the socket."""
        if self.journal_dir or self._closed or not self._writer:
            return
        try:
            await self._send({"t": "end", "grace": self.end_grace})
            await self._wait_exit(self.end_grace + 5.0)
        except Exception:
            pass
        self._closed = True
        try:
            self._writer.close()
            await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
        except Exception:
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.journal_dir:
            if self._synth is not None:
                await self._synth.put(None)
            return
        try:
            if self.detach_mode or self._init_pending:
                # a kernel leaving, or a connect that never completed (the initialize unanswered): the host
                # keeps its CLI either way — a failed attach must never END the turn it failed to join
                await self._flush_ack()
                await self._send({"t": "detach"})
            else:
                await self._send({"t": "end", "grace": self.end_grace})
                await self._wait_exit(self.end_grace + 5.0)
        except Exception:
            pass
        try:
            self._writer.close()
            await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
        except Exception:
            pass

    def read_messages(self):
        return self._read_journal() if self.journal_dir else self._read_socket()

    # ── live ──
    async def _send(self, frame: dict) -> None:
        self._writer.write(sh.encode_frame(frame))
        await self._writer.drain()

    async def signal(self, sig: str) -> None:
        """An interrupt rung as a host request ('INT' or 'KILL')."""
        await self._send({"t": "signal", "sig": str(sig)})

    async def _flush_ack(self) -> None:
        if self.ack_offset > self._last_ack_sent and self._writer and not self._writer.is_closing():
            await self._send({"t": "ack", "offset": self.ack_offset})
            self._last_ack_sent = self.ack_offset
            self._last_ack_t = time.time()

    async def _wait_exit(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while self.exit_info is None and time.time() < deadline:
            try:
                chunk = await asyncio.wait_for(self._reader.read(65536), timeout=max(0.05, deadline - time.time()))
            except (asyncio.TimeoutError, Exception):
                return
            if not chunk:
                return
            for f in self._fr.feed(chunk):
                self._dispatch_side(f)

    def _dispatch_side(self, f: dict):
        """A non-record frame; returns the record for an `out` frame, else None."""
        t = f.get("t")
        if t == "out":
            return f
        if t == "stderr" and self.on_stderr:
            self.on_stderr(str(f.get("line") or ""))
        elif t == "exit":
            self.exit_info = f
            if self.on_exit:
                self.on_exit(f)
        elif t == "fault" and self.on_fault:
            self.on_fault(f)
        elif t == "reexec-now" and self.on_reexec:
            self.on_reexec(f)
        return None

    def _advance(self, out: dict) -> None:
        """Acknowledge one record: acknowledged means RECEIVED by this process (not persisted); the offset moves
        as the record is handed over, and derived state is rebuilt from the transcript and the journal."""
        self.ack_offset = max(self.ack_offset, int(out.get("offset", self.ack_offset)))
        if self.on_ack:
            self.on_ack(self.ack_offset)

    def _take(self, out: dict):
        self._advance(out)
        self._tag(out.get("offset"), out["data"])
        return out["data"]

    def _tag(self, offset, data, replay=None) -> None:
        """A RESULT record's own offset, queued for the spend fold (T354): the consumer pops one per result it handles,
        in order, so it reads the record's position, never the transport's current offset (a buffered reader runs a
        record ahead). `replay` is decided against the hello's journal.next unless the caller knows (an orphan journal
        replays only)."""
        if not isinstance(data, dict) or data.get("type") != "result":
            return
        try:
            off = int(offset)
        except (TypeError, ValueError):
            off = None                                    # a frame with no offset (no host writes one; a protocol change)
        if replay is None:
            replay = off is not None and self.replay_end is not None and off < self.replay_end   # unknown position: live,
            #                                                                                       never a replay that folds nothing
        self.result_tags.append({"offset": off if off is not None else -1, "replay": bool(replay)})

    def _answers_mine(self, data) -> bool:
        if not isinstance(data, dict) or data.get("type") != "control_response":
            return False
        rid = str(((data.get("response") or {}).get("request_id")) or "")
        return rid in self._my_requests

    async def _read_socket(self):
        pending = list(self._early)
        self._early = []
        while True:
            for f in pending:
                out = self._dispatch_side(f)
                if out is not None:
                    # The Query's initialize must be answered AHEAD of a replay: the SDK client connects, starts
                    # its reader and awaits the initialize before anything consumes the message stream, whose
                    # buffer holds 100 records; a replay longer than that would block the reader with the
                    # initialize's answer still behind it (the commit 2-3 review's second finding). So every
                    # record is held until the first answer to a request this transport wrote has been handed
                    # over; then the held records follow, in order, and live delivery resumes.
                    if self._init_pending:
                        if self._answers_mine(out.get("data")):
                            # the answer is handed over first but acknowledged LAST: its offset sits past the
                            # whole held replay, and an ack that jumped there before the held records were
                            # handed over would lose them to every later kernel (the commit 6-7 review's third)
                            self._init_pending = False
                            yield out["data"]
                            held, self._hold = self._hold, []
                            for h in held:
                                yield self._take(h)
                            self._advance(out)
                        else:
                            self._hold.append(out)
                    else:
                        yield self._take(out)
                    if self.ack_offset - self._last_ack_sent >= ACK_BATCH or time.time() - self._last_ack_t >= ACK_INTERVAL_S:
                        await self._flush_ack()
                if self.exit_info is not None:
                    held, self._hold = self._hold, []       # the CLI is gone: whatever was held goes out first
                    for h in held:
                        yield self._take(h)
                    await self._flush_ack()
                    code = self.exit_info.get("code")
                    if code not in (0, None):
                        raise ProcessError("the CLI exited with code %s (%s)" % (code, self.exit_info.get("cause")),
                                           exit_code=code, stderr="see the host's log")
                    return
            pending = []
            try:
                chunk = await self._reader.read(65536)
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception:
                chunk = b""
            if not chunk:
                if self.detach_mode or self._closed:
                    return
                raise CLIConnectionError("the host's socket closed")
            pending = self._fr.feed(chunk)

    # ── replay ──
    async def _synth_write(self, data: str) -> None:
        try:
            obj = json.loads(data)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and obj.get("type") == "control_request":
            req = obj.get("request") if isinstance(obj.get("request"), dict) else {}
            resp = {"commands": [], "hooks_applied": []} if req.get("subtype") == "initialize" else {}
            if req.get("subtype") == "initialize":
                self._init_answered = True
            await self._synth.put({"type": "control_response",
                                   "response": {"subtype": "success", "request_id": obj.get("request_id"), "response": resp}})
        else:
            self.dropped_writes += 1

    async def _read_journal(self):
        # the Query's initialize is answered first (it waits on it), then the journal, then the end
        while True:
            try:
                item = self._synth.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                return
            yield item
        for off, rec in sh.read_journal_dir(self.journal_dir, self.ack_offset + 1):
            self.ack_offset = off
            if self.on_ack:
                self.on_ack(off)
            self._tag(off, rec, replay=True)             # an orphan journal's records are all replays
            yield rec
            # answers the Query asked for meanwhile ride between records
            while not self._synth.empty():
                item = self._synth.get_nowait()
                if item is None:
                    return
                yield item
        # the Query writes its initialize right after start(); answer it before ending the stream (the SDK
        # treats the end as the CLI's exit), bounded so a client that never asks still ends
        deadline = time.time() + 5.0
        while not self._init_answered and time.time() < deadline:
            try:
                item = await asyncio.wait_for(self._synth.get(), timeout=max(0.05, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            if item is None:
                return
            yield item
        self.exit_info = {"t": "exit", "code": 0, "cause": "replay-end"}
