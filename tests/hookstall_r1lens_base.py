"""Review of the hostAck fix (R1 lens): synthetic interleavings the author's tests do not cover.

All data synthetic: placeholder sid, invented pids and start stamps, no real session content.
A small fake host model replays from (attach ack + 1) to its journal's next offset, as session_host._attach does,
and every offset handed to the session is recorded per host identity, so a replay of a consumed record shows as a
duplicate and a skipped record shows as a gap.
"""
import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"
if importlib.util.find_spec("claude_agent_sdk") is None:
    _tag = "python%d.%d" % sys.version_info[:2]
    for _sp in sorted(Path(os.path.expanduser("~/.local/state/romp/sdkvenv/lib")).glob(_tag + "/site-packages")):
        sys.path.insert(0, str(_sp))
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))
ht = sb._ht()

SID = "7a7a7a7a-2222-4333-8444-0000000000d9"     # private synthetic sid
LOOP_PREFIX = getattr(sb, "SESSION_LOOP_THREAD_PREFIX", "sdk:")
H1 = {"pid": 4242, "start": "h1"}
C1 = {"pid": 4343, "start": "c1"}
H2 = {"pid": 5151, "start": "h2"}
C2 = {"pid": 5252, "start": "c2"}


def _hello(h, c, nxt):
    return {"t": "hello", "host": dict(h), "cli": dict(c), "journal": {"next": nxt}}


def _rec(off):
    return {"t": "out", "offset": off, "data": {"type": "assistant", "message": {"content": []}}}


def _drain_writer(be, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        w = getattr(be, "_reg_writer", None) or getattr(be, "_host_ack_thread", None)
        if w is None:
            return
        w.join(max(0.0, deadline - time.time()))


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.logs = []
        self.be = self._backend()
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "alive": True,
                                         "hostAck": {"host": "4242:h1", "cli": "4343:c1", "offset": 5}})
        self._lease(H1, C1)
        self._starts = {4242: "h1", 4343: "c1", 5151: "h2", 5252: "c2"}
        self._orig_start = sb.proc_start
        sb.proc_start = lambda p, run=None: self._starts.get(p)
        self.addCleanup(setattr, sb, "proc_start", self._orig_start)
        self.s = self._session()
        # what each host has handed over, per identity, in order
        self.handed = {}
        self.acks = []          # (host, offset) the registry holds after every _update_reg_with
        self._spy(self.be)

    def _backend(self):
        return sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)

    def _session(self):
        return types.SimpleNamespace(sid=SID, name="web", _host=None, _host_ack_t=0.0, _host_end_grace=None,
                                     _on_cli_stderr=lambda line: None, _host_is_attach=False, _host_reexec_wait="",
                                     _host_reexec_closed=False, _host_reexec_from=None,
                                     _seed_for_dead_cli=lambda cli: None)

    def _spy(self, be):
        orig = be._update_reg_with

        def spy(sid, make_fields):
            orig(sid, make_fields)
            a = (sb.read_reg(Path(self.d), SID) or {}).get("hostAck")
            if isinstance(a, dict):
                self.acks.append((a.get("host"), a.get("offset")))
        be._update_reg_with = spy

    def _lease(self, h, c):
        sb.write_lease(Path(self.d), {"sid": SID, "pid": c["pid"], "start": c["start"], "t": time.time(),
                                      "holder": {"kind": "host", "pid": h["pid"], "start": h["start"]}})

    def _reg_ack(self):
        return (sb.read_reg(Path(self.d), SID) or {}).get("hostAck") or {}

    async def _attach_and_consume(self, h, c, journal_next, upto=None):
        """The real reconnect (_host_transport_for), then the fake host's replay from ack+1 to `upto` (default: the
        journal's end), each record handed over through the transport's real _take (on_ack -> _write_host_ack)."""
        t = await self.be._host_transport_for(self.s, None, None)
        assert t is not None
        resume = t.ack_offset
        t.hello = _hello(h, c, journal_next)
        t.replay_end = journal_next
        ident = "%s:%s" % (h["pid"], h["start"])
        end = journal_next if upto is None else upto
        for off in range(max(resume + 1, 0), end):
            self.s._host_ack_t = 0.0          # past the one-second throttle: every record queues a write
            t._take(_rec(off))
            self.handed.setdefault(ident, []).append(off)
        return t, resume

    def _detach(self):
        """The connect loop's finally for a live host: the last ack, then the session drops its transport."""
        if self.s._host is not None and getattr(self.s._host, "exit_info", None) is None:
            self.be._write_host_ack(self.s, force=True)
        self.s._host = None

    def _assert_each_once(self, ident, first, last):
        got = self.handed.get(ident, [])
        self.assertEqual(sorted(got), got, "%s: out of order %r" % (ident, got))
        dups = sorted({o for o in got if got.count(o) > 1})
        self.assertEqual(dups, [], "%s: records replayed after the kernel consumed them: %r" % (ident, dups))
        self.assertEqual(got, list(range(first, last + 1)), "%s: records skipped or missing: %r" % (ident, got))

    def _assert_monotonic_per_host(self):
        last = {}
        for host, off in self.acks:
            if host in last:
                self.assertGreaterEqual(off, last[host], "hostAck moved backwards for %s: %r" % (host, self.acks))
            last[host] = off


class _WriterBlock:
    """Blocks the registry writer thread inside an item that does NOT hold _reg_lock (a slow item), so every keyed
    write queued after it waits; the forced ack (synchronous, lock free) still lands."""

    def __init__(self, be):
        self.be, self.gate, self.inside = be, threading.Event(), threading.Event()

    def __enter__(self):
        def block():
            self.inside.set()
            self.gate.wait(20.0)
        th = threading.Thread(target=lambda: self.be._reg_job(("test-block", SID), block),
                              name=LOOP_PREFIX + "web-block", daemon=True)
        th.start()
        th.join(5.0)
        assert self.inside.wait(5.0), "the writer never started the blocking item"
        return self

    def __exit__(self, *exc):
        self.gate.set()
        _drain_writer(self.be)
        return False


class TwoQuickReconnectsSameHost(_Base):
    """Two reconnects in quick succession on the same host identity while the registry writer is blocked."""

    def _run_on_loop(self, fn, timeout=15.0, name=None):
        out = {}

        def run():
            try:
                out["value"] = asyncio.run(fn())
            except BaseException as e:   # surfaced below
                out["error"] = e
        th = threading.Thread(target=run, name=name or (LOOP_PREFIX + "web-r1"), daemon=True)
        th.start()
        th.join(timeout)
        self.assertFalse(th.is_alive(), "the session loop never finished")
        if "error" in out:
            raise out["error"]
        return out.get("value")

    def test_writer_blocked_two_reconnects_then_late_queue_lands(self):
        with _WriterBlock(self.be):
            async def main():
                t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 5)
                t1.hello = _hello(H1, C1, 10)
                t1.replay_end = 0
                self.s._host = t1
                for off in range(6, 10):
                    self.s._host_ack_t = 0.0
                    t1._take(_rec(off))
                    self.handed.setdefault("4242:h1", []).append(off)
                self._detach()
                t2, r2 = await self._attach_and_consume(H1, C1, 13)      # reconnect 1, at once
                self._detach()
                t3, r3 = await self._attach_and_consume(H1, C1, 16)      # reconnect 2, at once
                return r2, r3
            r2, r3 = self._run_on_loop(main)
            reg_while_blocked = dict(self._reg_ack())
        # the late queued per-record write ran after the writer unblocked: it reads the session's CURRENT transport
        self.assertEqual(r2, 9)
        self.assertEqual(r3, 12)
        self.assertEqual(reg_while_blocked.get("offset"), 12, "the forced acks land while the writer is blocked")
        self._assert_each_once("4242:h1", 6, 15)
        self._assert_monotonic_per_host()
        self.assertEqual(self._reg_ack().get("offset"), 15)
        self._detach()
        self.assertEqual(self._reg_ack().get("offset"), 15)

    def test_lock_held_two_reconnects_forced_acks_fail(self):
        """The registry never learns anything (every forced write fails): the carry alone must carry both reconnects."""
        real = self.be._update_reg_with

        def failing(sid, make_fields):
            raise OSError("synthetic: registry write failed")
        self.be._update_reg_with = failing

        async def main():
            t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 5)
            t1.hello = _hello(H1, C1, 10)
            t1.replay_end = 0
            self.s._host = t1
            for off in range(6, 10):
                self.s._host_ack_t = 0.0
                t1._take(_rec(off))
                self.handed.setdefault("4242:h1", []).append(off)
            self._detach()
            _, r2 = await self._attach_and_consume(H1, C1, 13)
            self._detach()
            _, r3 = await self._attach_and_consume(H1, C1, 16)
            self._detach()
            return r2, r3
        r2, r3 = self._run_on_loop(main)
        _drain_writer(self.be)
        self.be._update_reg_with = real
        self.assertEqual(self._reg_ack().get("offset"), 5, "setup: the registry stayed stale")
        self.assertEqual((r2, r3), (9, 12))
        self._assert_each_once("4242:h1", 6, 15)

    def test_late_writer_item_at_every_point_never_moves_back(self):
        """The queued per-record write can land at ANY point of the reconnects: run it by hand at each one."""
        points = []

        async def main():
            t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 5)
            t1.hello = _hello(H1, C1, 10)
            t1.replay_end = 0
            self.s._host = t1
            for off in range(6, 10):
                self.s._host_ack_t = 0.0
                t1._take(_rec(off))
                self.handed.setdefault("4242:h1", []).append(off)
            self.be._write_host_ack_now(self.s); points.append(self._reg_ack().get("offset"))
            self._detach()
            self.be._write_host_ack_now(self.s); points.append(self._reg_ack().get("offset"))
            t2 = await self.be._host_transport_for(self.s, None, None)   # attached, no hello yet
            self.be._write_host_ack_now(self.s); points.append(self._reg_ack().get("offset"))
            t2.hello = _hello(H1, C1, 13)
            t2.replay_end = 13
            self.be._write_host_ack_now(self.s); points.append(self._reg_ack().get("offset"))
            for off in range(t2.ack_offset + 1, 13):
                self.s._host_ack_t = 0.0
                t2._take(_rec(off))
                self.handed.setdefault("4242:h1", []).append(off)
            self._detach()
            t3 = await self.be._host_transport_for(self.s, None, None)
            t3.hello = _hello(H1, C1, 14)
            self.be._write_host_ack_now(self.s); points.append(self._reg_ack().get("offset"))
            for off in range(t3.ack_offset + 1, 14):
                t3._take(_rec(off))
                self.handed.setdefault("4242:h1", []).append(off)
            self._detach()
        self._run_on_loop(main, name="test-main")   # off the loop: the by-hand writer items take the lock directly
        _drain_writer(self.be)
        self.assertEqual(points, sorted(points), points)
        self._assert_each_once("4242:h1", 6, 13)
        self._assert_monotonic_per_host()


class ReconnectToANewHost(_Base):
    """After the old host H1 handed over through 9, a NEW host process H2 (other pid and start) holds the lease with
    a fresh journal: neither the registry's H1 offset nor the H1 carry may make the connect skip H2's records."""

    def _consume_h1_through_9(self):
        t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 5)
        t1.hello = _hello(H1, C1, 10)
        t1.replay_end = 0
        self.s._host = t1
        for off in range(6, 10):
            self.s._host_ack_t = 0.0
            t1._take(_rec(off))
        self._detach()
        self.assertEqual(self._reg_ack(), {"host": "4242:h1", "cli": "4343:c1", "offset": 9})
        self.assertEqual(self.be._host_ack_carry.get(SID), ("4242:h1", 9))

    def test_attach_to_new_host_replays_its_whole_journal(self):
        self._consume_h1_through_9()
        self._lease(H2, C2)
        t2, r = asyncio.run(self._attach_and_consume(H2, C2, 5))
        self.assertEqual(r, -1, "the H1 offset was applied to the new host H2")
        self._assert_each_once("5151:h2", 0, 4)
        _drain_writer(self.be)
        self._detach()
        self.assertEqual(self._reg_ack(), {"host": "5151:h2", "cli": "5252:c2", "offset": 4},
                         "the new host's lower offset was dropped as 'backwards' against the old host's")
        self.assertEqual(self.be._host_ack_carry.get(SID), ("5151:h2", 4))

    def test_new_host_two_quick_reconnects_registry_still_names_old_host(self):
        """H2 consumed 0..2, the writer is blocked and the forced write fails, so the registry still names H1 at 9: the
        second connect must resume from H2's carried 2, not from H1's 9 (which would skip H2's 3..9)."""
        self._consume_h1_through_9()
        self._lease(H2, C2)
        real = self.be._update_reg_with

        def failing(sid, make_fields):
            raise OSError("synthetic: registry write failed")
        self.be._update_reg_with = failing
        try:
            with _WriterBlock(self.be):
                t2, r2 = asyncio.run(self._attach_and_consume(H2, C2, 12, upto=3))
                self._detach()
                t3, r3 = asyncio.run(self._attach_and_consume(H2, C2, 12))
                self._detach()
        finally:
            self.be._update_reg_with = real
        self.assertEqual(self._reg_ack().get("host"), "4242:h1", "setup: the registry still names the old host")
        self.assertEqual((r2, r3), (-1, 2))
        self._assert_each_once("5151:h2", 0, 11)

    def test_orphan_road_for_new_host_ignores_old_host_offsets(self):
        self._consume_h1_through_9()
        replayed = self._orphan(H2, 5)
        self.assertEqual(replayed, [0], "the dead NEW host's journal must replay whole: %r" % (replayed,))

    def test_orphan_road_for_new_host_uses_its_own_carry(self):
        self._consume_h1_through_9()
        self._lease(H2, C2)
        t2, r = asyncio.run(self._attach_and_consume(H2, C2, 12, upto=3))      # H2 consumed 0..2
        t2.exit_info = {"t": "exit", "cause": "died", "code": -9}
        # registry left naming H1 (as if every H2 write were still queued when H2 died)
        reg = sb.read_reg(Path(self.d), SID)
        reg["hostAck"] = {"host": "4242:h1", "cli": "4343:c1", "offset": 9}
        sb.write_reg(Path(self.d), SID, reg)
        self._detach()
        replayed = self._orphan(H2, 12)
        self.assertEqual(replayed, [3], "the dead H2's tail must resume at 3: %r" % (replayed,))

    def test_fresh_kernel_with_old_host_registry_attaches_new_host_from_zero(self):
        self._consume_h1_through_9()
        self._lease(H2, C2)
        self.be = self._backend()          # a kernel restart: no carry
        self._spy(self.be)
        t2, r = asyncio.run(self._attach_and_consume(H2, C2, 5))
        self.assertEqual(r, -1)

    def _orphan(self, h, n):
        hdir = ht.host_dir(Path(self.d), SID)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / "identity.json").write_text('{"pid": %d, "start": "%s"}' % (h["pid"], h["start"]))
        with open(hdir / "journal-0.jsonl", "w") as fh:
            for off in range(n):
                fh.write('{"type": "assistant", "n": %d}\n' % off)
        sb.remove_lease(Path(self.d), SID)
        replayed = []

        class FakeClient:
            def __init__(self, options=None, transport=None):
                replayed.append(transport.ack_offset + 1)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
        fake = types.ModuleType("claude_agent_sdk")
        fake.ClaudeSDKClient = FakeClient
        with unittest.mock.patch.dict(sys.modules, {"claude_agent_sdk": fake}), \
                unittest.mock.patch.object(self.be, "_replay_drain", lambda *a, **k: asyncio.sleep(0)):
            asyncio.run(self.be._host_orphan_recover(self.s, None, None, None, died=False))
        return replayed


class RestartAfterCliDeathWhileWriterLags(_Base):
    """Not a reconnect in the same kernel: the CLI dies while the registry writer lags, then the KERNEL restarts (a
    clean drain) before the session reconnects. The carry dies with the kernel; what does the next kernel replay?"""

    def test_next_kernel_orphan_road_after_cli_death(self):
        reg = sb.read_reg(Path(self.d), SID)
        reg["hostAck"] = {"host": "4242:h1", "cli": "4343:c1", "offset": 2}
        sb.write_reg(Path(self.d), SID, reg)
        t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 2)
        t1.hello = _hello(H1, C1, 40)
        t1.replay_end = 0
        self.s._host = t1

        def loop():
            for off in range(3, 40):                  # handed over through 39 while another thread holds the lock
                self.s._host_ack_t = 0.0
                t1._take(_rec(off))
            t1.exit_info = {"t": "exit", "cause": "died", "code": -9}
            self._detach()
        th = threading.Thread(target=loop, name=LOOP_PREFIX + "web-died2", daemon=True)
        self.be._reg_lock.acquire()
        try:
            th.start()
            th.join(3.0)
        finally:
            self.be._reg_lock.release()
        th.join(10.0)
        flush = getattr(self.be, "_reg_flush", None)
        if flush:
            flush(5.0)                                # the clean drain waits for the writer
        _drain_writer(self.be)
        reg_after = dict(self._reg_ack())
        # the next kernel: a fresh backend, no carry, reads only the registry
        self.be = self._backend()
        self._spy(self.be)
        sb.remove_lease(Path(self.d), SID)
        hdir = ht.host_dir(Path(self.d), SID)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / "identity.json").write_text('{"pid": 4242, "start": "h1"}')
        with open(hdir / "journal-0.jsonl", "w") as fh:
            for off in range(40):
                fh.write('{"type": "assistant", "n": %d}\n' % off)
        replayed = []

        class FakeClient:
            def __init__(self, options=None, transport=None):
                replayed.append(transport.ack_offset + 1)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
        fake = types.ModuleType("claude_agent_sdk")
        fake.ClaudeSDKClient = FakeClient
        with unittest.mock.patch.dict(sys.modules, {"claude_agent_sdk": fake}), \
                unittest.mock.patch.object(self.be, "_replay_drain", lambda *a, **k: asyncio.sleep(0)):
            asyncio.run(self.be._host_orphan_recover(self.s, None, None, None, died=False))
        print("\n[restart-after-cli-death] registry hostAck after the drain: %r; next kernel replayed from: %r"
              % (reg_after, replayed))
        self.assertEqual(replayed, [], "the next kernel replayed records the old kernel had consumed, from %r "
                                       "(registry after the drain: %r)" % (replayed, reg_after))


if __name__ == "__main__":
    unittest.main()
