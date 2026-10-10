"""R1/R2 lens on 49020be91: the reviewer's two failing interleavings adapted to the new design, plus the review-1
scenarios re-run with the detach's last ack QUEUED (hostAckFinal) instead of written on the loop.

All data synthetic: placeholder sid, invented pids and start stamps, hostname TESTHOST, no real session content.
"""
import asyncio
import os
import sys
import threading
import time
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hookstall_r1lens_base as orig  # noqa: E402

sb, ht, SID, LOOP_PREFIX = orig.sb, orig.ht, orig.SID, orig.LOOP_PREFIX
H1, C1, H2, C2 = orig.H1, orig.C1, orig.H2, orig.C2
_hello, _rec, _drain_writer = orig._hello, orig._rec, orig._drain_writer
OTHER = "7a7a7a7a-2222-4333-8444-00000000ffff"     # another synthetic session, owner of the writer's slow item


class _OtherWriterBlock:
    """Parks the registry writer inside ANOTHER session's slow item (no _reg_lock held), so this session's queued
    writes wait behind it, and a session-end flush of SID does not wait for it (it is not SID's)."""

    def __init__(self, be):
        self.be, self.gate, self.inside = be, threading.Event(), threading.Event()

    def __enter__(self):
        def block():
            self.inside.set()
            self.gate.wait(20.0)
        th = threading.Thread(target=lambda: self.be._reg_job(("test-block", OTHER), block),
                              name=LOOP_PREFIX + "other-block", daemon=True)
        th.start()
        th.join(5.0)
        assert self.inside.wait(5.0), "the writer never started the blocking item"
        return self

    def release(self):
        self.gate.set()
        _drain_writer(self.be)

    def __exit__(self, *exc):
        self.release()
        return False


class _Adapted(orig._Base):
    def setUp(self):
        super().setUp()
        self.s.backend = self.be
        # these roads leave the host to reconnect in the same thread (a real session sets _reconnect before that
        # leave); a test whose leave is the drain's detach sets `detached` (review 4: a final leave writes its ack at once)
        self.s._reconnect = True

    def _leave(self):
        """The REAL connect-loop finally (SdkSession._leave_host) on this namespace session."""
        sb.SdkSession._leave_host(self.s)

    def _run_on_loop(self, fn, timeout=15.0, name=None):
        out = {}

        def run():
            try:
                out["value"] = asyncio.run(fn())
            except BaseException as e:
                out["error"] = e
        th = threading.Thread(target=run, name=name or (LOOP_PREFIX + "web-r1"), daemon=True)
        th.start()
        th.join(timeout)
        self.assertFalse(th.is_alive(), "the session loop never finished")
        if "error" in out:
            raise out["error"]
        return out.get("value")

    def _consume_h1(self, first, last, journal_next):
        t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", first - 1)
        t1.hello = _hello(H1, C1, journal_next)
        t1.replay_end = 0
        self.s._host = t1
        for off in range(first, last + 1):
            self.s._host_ack_t = 0.0
            t1._take(_rec(off))
            self.handed.setdefault("4242:h1", []).append(off)
        return t1

    def _orphan_replay(self, be, h, n):
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
                unittest.mock.patch.object(be, "_replay_drain", lambda *a, **k: asyncio.sleep(0)):
            asyncio.run(be._host_orphan_recover(self.s, None, None, None, died=False))
        return replayed


class AdaptedReviewerTests(_Adapted):
    """The two reviewer tests that fail at fb5c44b65, adapted as the author describes."""

    def test_writer_blocked_two_reconnects_adapted(self):
        """Original minus the one line asserting the forced ack lands while the writer is blocked; the detach is the
        real _leave_host. Reconnects must still resume exactly, and the late queue must land monotonically."""
        with orig._WriterBlock(self.be):
            async def main():
                self._consume_h1(6, 9, 10)
                self._leave()
                t2, r2 = await self._attach_and_consume(H1, C1, 13)
                self._leave()
                t3, r3 = await self._attach_and_consume(H1, C1, 16)
                return r2, r3
            r2, r3 = self._run_on_loop(main)
            reg_while_blocked = dict(self._reg_ack())
        self.assertEqual((r2, r3), (9, 12))
        self.assertEqual(reg_while_blocked.get("offset"), 5, "setup: nothing landed while the writer was blocked")
        self._assert_each_once("4242:h1", 6, 15)
        self._assert_monotonic_per_host()
        self.assertEqual(self._reg_ack().get("offset"), 15)
        self._leave()                                    # off the loop: written at once
        self.assertEqual(self._reg_ack().get("offset"), 15)

    def test_next_kernel_after_cli_death_through_real_leave_host(self):
        """Original restart case, the loop's detach replaced by the real _leave_host (exit_info died -> departed,
        synchronous after the session's queued jobs). The next kernel must replay nothing."""
        reg = sb.read_reg(Path(self.d), SID)
        reg["hostAck"] = {"host": "4242:h1", "cli": "4343:c1", "offset": 2}
        sb.write_reg(Path(self.d), SID, reg)
        t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 2)
        t1.hello = _hello(H1, C1, 40)
        t1.replay_end = 0
        self.s._host = t1

        def loop():
            for off in range(3, 40):
                self.s._host_ack_t = 0.0
                t1._take(_rec(off))
            t1.exit_info = {"t": "exit", "cause": "died", "code": -9}
            self._leave()
        th = threading.Thread(target=loop, name=LOOP_PREFIX + "web-died2", daemon=True)
        self.be._reg_lock.acquire()
        try:
            th.start()
            th.join(3.0)
            self.assertTrue(th.is_alive(), "setup: the departed write waits on the lock (a session-ending path)")
        finally:
            self.be._reg_lock.release()
        th.join(10.0)
        self.assertFalse(th.is_alive())
        self.be._reg_flush(5.0)
        _drain_writer(self.be)
        reg_after = dict(self._reg_ack())
        self.assertEqual(reg_after.get("offset"), 39, reg_after)
        nxt = self._backend()                            # the next kernel: no carry
        replayed = self._orphan_replay(nxt, H1, 40)
        self.assertEqual(replayed, [], "next kernel replayed consumed records from %r (reg %r)" % (replayed, reg_after))


class Review1ScenariosQueuedFinal(_Adapted):
    """Review 1's roads with the detach's last ack queued under hostAckFinal."""

    def test_two_quick_reconnects_other_session_blocks_writer_and_writes_fail(self):
        real = self.be._update_reg_with

        def failing(sid, make_fields):
            raise OSError("synthetic: registry write failed")
        self.be._update_reg_with = failing
        try:
            with _OtherWriterBlock(self.be):
                async def main():
                    self._consume_h1(6, 9, 10)
                    self._leave()
                    _, r2 = await self._attach_and_consume(H1, C1, 13)
                    self._leave()
                    _, r3 = await self._attach_and_consume(H1, C1, 16)
                    self._leave()
                    return r2, r3
                r2, r3 = self._run_on_loop(main)
        finally:
            self.be._update_reg_with = real
        self.assertEqual((r2, r3), (9, 12))
        self._assert_each_once("4242:h1", 6, 15)
        self.assertEqual(self._reg_ack().get("offset"), 5, "setup: every write failed")

    def test_late_final_never_moves_same_host_back(self):
        """The queued per-record job (ahead in line, read at write time) lands the reconnect's newer offset; the queued
        final of the earlier detach (captured, older) lands after it and must be dropped by the monotonic merge."""
        with _OtherWriterBlock(self.be) as blk:
            async def main():
                t1 = self._consume_h1(6, 8, 10)
                self.s._host_ack_t = 0.0
                t1._take(_rec(9))                       # queues the per-record job (key hostAck)
                self.handed["4242:h1"].append(9)
                self._leave()                           # queues hostAckFinal(H1, 9) BEHIND it
                t2 = await self.be._host_transport_for(self.s, None, None)
                t2.hello = _hello(H1, C1, 14)
                for off in range(t2.ack_offset + 1, 14):
                    t2._take(_rec(off))                 # inside the 1 s throttle: the hostAck job is not re-queued
                    self.handed["4242:h1"].append(off)
                return t2.ack_offset
            r = self._run_on_loop(main)
            order = [k[0] for k in list(self.be._reg_jobs)]
            blk.release()
        self.assertEqual(r, 13)
        # both are read-position saves (review 5): the final joins the END of the saves block, behind the per-record
        # job queued before it, so the order holds whether or not hostAckFinal is one of REG_ACK_KEYS
        self.assertEqual(order, ["hostAck", "hostAckFinal"], order)
        self._assert_each_once("4242:h1", 6, 13)
        self._assert_monotonic_per_host()
        self.assertEqual(self._reg_ack().get("offset"), 13, self.acks)

    def test_new_host_after_detach_orphan_then_spawn(self):
        """H1 alive at the detach (final queued, writer parked), H1 dies before the reconnect: the orphan road must
        resume from the carry, not the stale registry; the new host H2 starts at -1; after the writer unparks, the
        registry must not leave the session pointing at a dead host once H2 has acked."""
        with _OtherWriterBlock(self.be) as blk:
            async def main():
                self._consume_h1(6, 9, 10)
                self._leave()
                return None
            self._run_on_loop(main)
            # H1 dies: its journal holds 0..11 (10 and 11 never handed over)
            replayed = self._orphan_replay(self.be, H1, 12)
            self.assertEqual(replayed, [10], "orphan replay must resume past the carried 9: %r" % (replayed,))
            self.assertNotIn("hostAck", sb.read_reg(Path(self.d), SID) or {}, "the orphan road dropped hostAck")
            self._lease(H2, C2)

            async def main2():
                self.s._host_ack_t = 0.0
                return await self._attach_and_consume(H2, C2, 4)
            t2, r = self._run_on_loop(main2)
            self.assertEqual(r, -1)
            blk.release()
        self._assert_each_once("5151:h2", 0, 3)
        self.assertEqual(self._reg_ack().get("host"), "5151:h2", "registry left naming a host that is gone: %r" % (self.acks,))

    def test_new_host_acks_inside_throttle_leave_registry_naming_dead_host(self):
        """Same road, but H2's records all arrive inside the one-second throttle that the detach's forced call armed:
        the per-record job queued before the detach (ahead of the final) writes H2. The stale final then wrote H1 over
        it and a fresh kernel resumed H2 from -1 (a whole-journal replay); since review 4 the queued final writes only
        while the carry still names its host, so the registry keeps H2 and a fresh kernel resumes past what was read."""
        with _OtherWriterBlock(self.be) as blk:
            async def main():
                t1 = self._consume_h1(6, 8, 10)
                self.s._host_ack_t = 0.0
                t1._take(_rec(9))                       # per-record job queued (ahead)
                self.handed["4242:h1"].append(9)
                self._leave()                           # final queued (behind); _host_ack_t armed now
                return None
            self._run_on_loop(main)
            self._orphan_replay(self.be, H1, 10)        # nothing past 9: cleared quietly
            self._lease(H2, C2)

            async def main2():
                t = await self.be._host_transport_for(self.s, None, None)
                t.hello = _hello(H2, C2, 4)
                for off in range(t.ack_offset + 1, 4):
                    t._take(_rec(off))                  # no throttle reset: inside the detach's one second
                    self.handed.setdefault("5151:h2", []).append(off)
                return t
            self._run_on_loop(main2)
            blk.release()
        reg = dict(self._reg_ack())
        # fresh kernel attaching the live H2
        self.be = self._backend()
        self._spy(self.be)
        self.s.backend = self.be
        t = asyncio.run(self.be._host_transport_for(self.s, None, None))
        print("\n[throttle-window] registry after unpark: %r; fresh kernel resumes H2 from %d" % (reg, t.ack_offset + 1))
        self.assertEqual(reg.get("host"), "5151:h2", "the stale final landed last: %r" % (self.acks,))
        self.assertEqual(t.ack_offset, 3, "a fresh kernel replays H2 from %d" % (t.ack_offset + 1))

    def test_fresh_kernel_after_clean_detach_with_writer_parked(self):
        """A kernel restart: the session detaches (host alive), the writer is parked on another session's work, the
        session thread then exits (its _session_ending flush). The next kernel attaches H1 and must replay nothing."""
        with _OtherWriterBlock(self.be):
            def thread_body():
                async def main():
                    self._consume_h1(6, 9, 10)
                    self.s.detached = True                 # SdkBackend.drain latches this before shutdown()
                    self._leave()
                asyncio.run(main())
                with self.be._session_ending(SID):     # SdkSession._run's finally
                    pass
            th = threading.Thread(target=thread_body, name=LOOP_PREFIX + "web-exit", daemon=True)
            th.start()
            th.join(10.0)
            self.assertFalse(th.is_alive())
            reg_at_exit = dict(self._reg_ack())
        self.assertEqual(reg_at_exit.get("offset"), 9, "the session's exit flush did not write the final ack")
        self.be = self._backend()
        self._spy(self.be)
        self.s.backend = self.be
        t2, r = asyncio.run(self._attach_and_consume(H1, C1, 10))
        self.assertEqual(r, 9)
        self._assert_each_once("4242:h1", 6, 9)

    def test_fresh_kernel_when_exit_bound_passes_before_the_thread_flush(self):
        """The window 49020be91 opened: between _leave_host and the thread's exit flush the final ack was only queued,
        so a drain bound passing there with the writer parked left the next kernel resuming from the older registry
        offset (replaying 6..9). Review 4's must-fix 1 writes the drain detach's ack at once, inside _leave_host, as
        a0d8e9751 did: the next kernel resumes past 9."""
        with _OtherWriterBlock(self.be):
            async def main():
                self._consume_h1(6, 9, 10)
                self.s.detached = True                     # SdkBackend.drain latches this before shutdown()
                self._leave()
            self._run_on_loop(main)                     # loop done; the thread's exit flush has not run yet
            left = self.be._reg_flush(0.25)            # the drain's bound expires
            reg_at_exit = dict(self._reg_ack())
            nxt = self._backend()                      # the process exits; the next kernel
            self.s.backend = nxt
            self.s._host = None
            t2 = asyncio.run(nxt._host_transport_for(self.s, None, None))
        print("\n[exit-window] queued at the bound: %d; registry at exit: %r; next kernel resumes from %d"
              % (left, reg_at_exit, t2.ack_offset + 1))
        self.assertEqual(reg_at_exit.get("offset"), 9, reg_at_exit)
        self.assertEqual(t2.ack_offset, 9, "the next kernel replays %d..9" % (t2.ack_offset + 1))


if __name__ == "__main__":
    unittest.main()


class RestartDuringOwnBacklog(_Adapted):
    """A kernel restart while this session has its own registry work queued (hook items that are slow because the
    registry lock is contended, the condition the branch exists for). The drain joins the session thread for its
    budget (EXIT_DRAIN_BUDGET_S, 0.35 x 4.5 s = 1.575 s by default), then the process exits (daemon threads die)."""

    BUDGET = 1.575
    ITEM_S = 0.2           # each queued item's registry write under a contended lock
    N_ITEMS = 10

    def test_final_ack_waits_behind_the_sessions_own_backlog(self):
        def slow_item():
            time.sleep(self.ITEM_S)

        def thread_body():
            async def main():
                self._consume_h1(6, 9, 10)
                for _ in range(self.N_ITEMS):            # this session's hook items, queued before the detach
                    self.be._reg_job(None, slow_item, sid=SID)
                self.s.detached = True                   # SdkBackend.drain latches this before shutdown()
                leave = getattr(sb.SdkSession, "_leave_host", None)
                if leave is not None:
                    leave(self.s)
                else:
                    self._detach()
            asyncio.run(main())
            fin = getattr(self.be, "_session_ending", None)
            if fin is not None:
                with fin(SID):                           # SdkSession._run's finally
                    pass
        th = threading.Thread(target=thread_body, name=LOOP_PREFIX + "web-exit", daemon=True)
        t0 = time.time()
        th.start()
        th.join(self.BUDGET)
        flush = getattr(self.be, "_reg_flush", None)
        if flush is not None:
            flush(max(0.25, t0 + self.BUDGET - time.time()))
        reg_at_exit = dict(self._reg_ack())               # the process exits here
        nxt = self._backend()
        self.s.backend = nxt
        self.s._host = None
        t2 = asyncio.run(nxt._host_transport_for(self.s, None, None))
        print("\n[own-backlog] registry at exit: %r; next kernel resumes from %d (consumed through 9)"
              % (reg_at_exit, t2.ack_offset + 1))
        self.assertEqual(t2.ack_offset, 9, "the next kernel replays %d..9, records the old kernel consumed"
                         % (t2.ack_offset + 1))

    def test_departed_ack_waits_behind_the_sessions_own_backlog(self):
        """Same budget, the CLI died instead (review 3's must-fix 2 road): _write_host_ack_departed runs the session's
        backlog first, then writes; a restart whose drain bound passes mid-backlog loses the departed offset."""
        def slow_item():
            time.sleep(self.ITEM_S)
        reg = sb.read_reg(Path(self.d), SID)
        reg["hostAck"] = {"host": "4242:h1", "cli": "4343:c1", "offset": 2}
        sb.write_reg(Path(self.d), SID, reg)

        def thread_body():
            async def main():
                t1 = self._consume_h1(3, 39, 40)
                for _ in range(self.N_ITEMS):
                    self.be._reg_job(None, slow_item, sid=SID)
                t1.exit_info = {"t": "exit", "cause": "died", "code": -9}
                sb.SdkSession._leave_host(self.s)
            asyncio.run(main())
        th = threading.Thread(target=thread_body, name=LOOP_PREFIX + "web-died3", daemon=True)
        t0 = time.time()
        th.start()
        th.join(self.BUDGET)
        self.be._reg_flush(max(0.25, t0 + self.BUDGET - time.time()))
        reg_at_exit = dict(self._reg_ack())
        nxt = self._backend()
        replayed = self._orphan_replay(nxt, H1, 40)
        print("\n[departed-backlog] registry at exit: %r; next kernel replayed from %r" % (reg_at_exit, replayed))
        self.assertEqual(replayed, [], "the next kernel replays from %r" % (replayed,))
