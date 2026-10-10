"""Review 5 of hook-callback-stall, must-fix 3 (synthetic data only; placeholder sid, invented pids): a reconnecting
leave queues its last read-position save (hostAckFinal); a kernel restart that lands before the new transport's hello
must still find that save written ahead of the session's own backlog, not behind it."""
import asyncio
import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from test_hookstall_restart_backlog import _Adapted, _OtherWriterBlock, sb, SID, LOOP_PREFIX, H1, C1, _hello  # noqa
from hookstall_r1lens_base import _rec as sb_rec  # noqa: E402

BUDGET = 1.575


class DrainDuringReconnect(_Adapted):
    """A deliberate reconnect's leave queues hostAckFinal behind the session's backlog; the drain lands before the
    new transport's hello, so the drain's own leave has no hello to write and never promotes the queued final."""

    def _run(self, n_items, item_s, new_transport_hello):
        def slow_item():
            time.sleep(item_s)

        def thread_body():
            async def main():
                self._consume_h1(6, 9, 10)
                for _ in range(n_items):
                    self.be._reg_job(None, slow_item, sid=SID)
                # reconnecting leave (the real finally; _reconnect is True in setUp)
                sb.SdkSession._leave_host(self.s)
                # the loop top reconnects: a new transport to the same live host, hello not yet in
                t2 = self.be._new_host_transport(self.s, "/nonexistent.sock", 9)
                if new_transport_hello:
                    t2.hello = _hello(H1, C1, 10)
                self.s._host = t2
                # the drain
                self.s.detached = True
                self.s._reconnect = False
                sb.SdkSession._leave_host(self.s)
            asyncio.run(main())
            with self.be._session_ending(SID):
                pass
        with _OtherWriterBlock(self.be) as blk:
            th = threading.Thread(target=thread_body, name=LOOP_PREFIX + "web-rc", daemon=True)
            t0 = time.time()
            th.start()
            th.join(BUDGET)
            self.be._reg_flush(max(0.25, t0 + BUDGET - time.time()))
            reg_at_exit = dict(self._reg_ack())
            blk.gate.set()
        nxt = self._backend()
        self.s.backend = nxt
        self.s._host = None
        t3 = asyncio.run(nxt._host_transport_for(self.s, None, None))
        print("\n[drain-during-reconnect hello=%s] registry at exit %r; next kernel resumes from %d (consumed through 9)"
              % (new_transport_hello, reg_at_exit, t3.ack_offset + 1))
        return t3.ack_offset

    def test_drain_before_new_hello_with_backlog(self):
        self.assertEqual(self._run(10, 0.2, False), 9)

    def test_drain_after_new_hello_with_backlog(self):
        self.assertEqual(self._run(10, 0.2, True), 9)



class DrainDuringReconnectLiveWriter(_Adapted):
    """Same road with the writer running normally: the per-record ack (front of the queue) lands offset 6, records 7..9
    arrive inside the one-second throttle, then the reconnect's leave queues the final behind the session's backlog."""

    def test_trailing_records_replay(self):
        def slow_item():
            time.sleep(0.2)

        def thread_body():
            async def main():
                for _ in range(10):
                    self.be._reg_job(None, slow_item, sid=SID)
                t1 = self._consume_h1(6, 6, 10)
                time.sleep(0.5)                       # the per-record ack (front) lands 6
                for off in (7, 8, 9):                 # inside the throttle: no per-record ack queued
                    t1._take(sb_rec(off))
                sb.SdkSession._leave_host(self.s)     # reconnecting leave: final queued at the back
                t2 = self.be._new_host_transport(self.s, "/nonexistent.sock", 9)
                self.s._host = t2                     # attach in progress, no hello yet
                self.s.detached = True                # the drain
                self.s.ended = True
                sb.SdkSession._leave_host(self.s)
            asyncio.run(main())
            with self.be._session_ending(SID):
                pass
        th = threading.Thread(target=thread_body, name=LOOP_PREFIX + "web-rc2", daemon=True)
        t0 = time.time()
        th.start()
        th.join(BUDGET)
        self.be._reg_flush(max(0.25, t0 + BUDGET - time.time()))
        reg_at_exit = dict(self._reg_ack())
        nxt = self._backend()
        self.s.backend = nxt
        self.s._host = None
        t3 = asyncio.run(nxt._host_transport_for(self.s, None, None))
        print("\n[live-writer drain-during-reconnect] registry at exit %r; acks %r; next kernel resumes from %d"
              % (reg_at_exit, self.acks, t3.ack_offset + 1))
        self.assertEqual(t3.ack_offset, 9)



class DrainWhileReconnectAttachIsSlow(_Adapted):
    """The reconnect's attach (or the options build before it) is still running when the drain's bound passes: the
    session thread never reaches another leave, so only the writer can land the queued final, and it is behind the
    session's backlog."""

    def test_queued_final_behind_backlog_at_bound(self):
        def slow_item():
            time.sleep(0.2)
        release = threading.Event()

        def thread_body():
            async def main():
                for _ in range(16):
                    self.be._reg_job(None, slow_item, sid=SID)
                t1 = self._consume_h1(6, 6, 10)
                time.sleep(0.5)
                for off in (7, 8, 9):
                    t1._take(sb_rec(off))
                sb.SdkSession._leave_host(self.s)     # reconnecting leave
                release.wait(10)                      # the attach still in progress at the bound
            asyncio.run(main())
        th = threading.Thread(target=thread_body, name=LOOP_PREFIX + "web-rc3", daemon=True)
        t0 = time.time()
        th.start()
        time.sleep(0.6)
        self.s.detached = True                        # the drain
        self.s.ended = True
        th.join(BUDGET)
        self.be._reg_flush(max(0.25, t0 + 0.6 + BUDGET - time.time()))
        reg_at_exit = dict(self._reg_ack())
        release.set()
        print("\n[slow-attach drain] registry at exit %r; acks %r" % (reg_at_exit, self.acks))
        self.assertEqual(reg_at_exit.get("offset"), 9)


if __name__ == "__main__":
    unittest.main()
