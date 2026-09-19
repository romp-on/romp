#!/usr/bin/env python3
"""The per-session host (T315, stage 4 of the restart-surviving sessions program): the pure pieces
(frames, the journal, the parked table, the neutral hook answers) and the host as a real process driving
the fake CLI (tests/fixtures/fake_claude.py) while this test plays the kernel over the Unix socket.

Hermetic: a temp state root per test, the fake CLI on a temp path, no scopes (the host is a plain child
here), every process killed by the test, synthetic ids. The host runs on its built-in pipe transport when
the SDK is not importable (CI, the plain test venv); one test runs the SDK transport when the machine has
the SDK venv, and skips otherwise.
"""
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the loads
os.environ.pop("ROMP_STATE_DIR", None)
sh = load_source("romp_session_host", os.path.join(ROOT, "kernel", "session_host.py"))
sb = load_source("romp_sdk_backend_host", os.path.join(BIN, "romp_sdk_backend.py"))
FAKE = os.path.join(HERE, "fixtures", "fake_claude.py")
SDK_SITE = next(iter(sorted(Path(os.path.expanduser("~/.local/state/romp/sdkvenv/lib")).glob(
    "python%d.%d/site-packages" % sys.version_info[:2]))), None) if os.path.isdir(os.path.expanduser("~/.local/state/romp/sdkvenv")) else None
SID = "11111111-2222-3333-4444-0000000000a1"          # the romp sid the lease is filed under
FSID = "11111111-2222-3333-4444-0000000000f1"         # the fake CLI's own conversation id (distinct on purpose)


class Frames(unittest.TestCase):
    def test_frames_round_trip_and_junk_is_dropped(self):
        fr = sh.FrameReader()
        a = sh.encode_frame({"t": "ping"}) + b"not json\n" + sh.encode_frame({"t": "in", "data": "x"})[:5]
        got = fr.feed(a)
        self.assertEqual(got, [{"t": "ping"}])
        got = fr.feed(sh.encode_frame({"t": "in", "data": "x"})[5:] + b"\n\n")
        self.assertEqual(got, [{"t": "in", "data": "x"}])


class JournalRules(unittest.TestCase):
    def test_offsets_are_ordinals_and_reads_start_anywhere(self):
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        for i in range(5):
            self.assertEqual(j.append({"type": "assistant", "n": i}), i)
        self.assertEqual([o for o, _ in j.read_from(0)], [0, 1, 2, 3, 4])
        self.assertEqual([r["n"] for _, r in j.read_from(3)], [3, 4])
        self.assertEqual(list(j.read_from(5)), [])
        self.assertEqual([r["n"] for _, r in sh.read_journal_dir(d, 2)], [2, 3, 4], "the orphan reader agrees")

    def test_segments_rotate_at_a_turn_boundary_and_acked_ones_are_dropped(self):
        d = tempfile.mkdtemp(); j = sh.Journal(d, segment_bytes=200)
        for i in range(6):
            j.append({"type": "assistant", "pad": "x" * 60, "n": i})
        self.assertEqual(j.segments(), [0], "no rotation before a result record")
        j.append({"type": "result", "n": 6})
        self.assertEqual(j.segments(), [0, 7], "a result past the size starts a new segment, named by its first offset")
        self.assertTrue((Path(d) / "journal-7.jsonl").exists())
        for i in range(7, 10):
            j.append({"type": "assistant", "n": i})
        j.ack(6)                                    # everything in the first segment
        j.append({"type": "result", "n": 10})
        self.assertEqual(j.segments(), [7], "a fully acknowledged, non-current segment is deleted at the next boundary")
        self.assertEqual([r["n"] for _, r in j.read_from(7)], [7, 8, 9, 10], "the rest still reads")
        self.assertEqual([r["n"] for _, r in j.read_from(0)], [7, 8, 9, 10], "a read below the dropped segment skips it")
        # the ORPHAN reader numbers records from the surviving segment's first offset, not from zero (the
        # review's finding 1: an orphan replay after a deletion used to misnumber the very records it exists for)
        self.assertEqual([(o, r["n"]) for o, r in sh.read_journal_dir(d, 0)], [(7, 7), (8, 8), (9, 9), (10, 10)])
        self.assertEqual([o for o, _ in sh.read_journal_dir(d, 9)], [9, 10])

    def test_a_failed_raw_write_leaves_nothing_behind_and_the_numbering_holds(self):
        # the commit-5 review's finding a: a buffered handle kept a failed write's bytes and landed them later at a
        # stale position; the journal now writes unbuffered and truncates back to the last good position
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        j.append({"type": "assistant", "n": 0})
        real = j._fh
        class Raw:
            def __init__(self): self.failed = False
            def write(self, b):
                if not self.failed:
                    self.failed = True
                    raise OSError(28, "no space left on device")
                return real.write(b)
            def fileno(self): return real.fileno()
            def seek(self, *a): return real.seek(*a)
            def tell(self): return real.tell()
            def close(self): return real.close()
        j._fh = Raw()
        size_before = os.path.getsize(j._path(0))
        with self.assertRaises(OSError):
            j.append({"type": "assistant", "n": 1})
        self.assertEqual((j.next_offset, len(j._index), os.path.getsize(j._path(0))), (1, 1, size_before), "nothing moved on the failure")
        j.append({"type": sh.GAP_TYPE, "offset": 1})          # the writer's gap marker takes offset 1
        j.append({"type": "assistant", "n": 2})
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(0)], [(0, 0), (2, 2)], "the marker is skipped, the numbering holds")
        self.assertEqual([(o, r["n"]) for o, r in sh.read_journal_dir(d, 0)], [(0, 0), (2, 2)], "the orphan reader agrees")
        # finding b: a marker that fails too becomes a zero-length index entry; index and offsets stay in lockstep
        j.note_gap(3)
        j.append({"type": "result", "n": 4})
        self.assertEqual(j.next_offset, 5); self.assertEqual(len(j._index), 5)
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(2)], [(2, 2), (4, 4)])
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(3, 5)], [(4, 4)], "a replay spanning the hole reads past it")
        self.assertEqual([(o, r["n"]) for o, r in sh.read_journal_dir(d, 0)], [(0, 0), (2, 2), (4, 4)],
                         "the orphan reader, with no index, numbers past the unrecorded gap from gaps.json")

    def test_a_write_that_will_not_truncate_leaves_the_segment_behind_for_a_fresh_one(self):
        # the commit 6-7 review's item 11, untested until now: a partial write that ftruncate cannot undo makes the
        # segment's byte positions unreliable; a fresh segment starts at the failed offset and the numbering holds
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        j.append({"type": "assistant", "n": 0})
        real = j._fh
        class Raw:
            def __init__(self): self.failed = False
            def write(self, b):
                if not self.failed:
                    self.failed = True
                    real.write(b[:5])                       # a partial line lands, then the disk is gone
                    raise OSError(28, "no space left on device")
                return real.write(b)
            def fileno(self): return real.fileno()
            def seek(self, *a): return real.seek(*a)
            def tell(self): return real.tell()
            def close(self): return real.close()
        j._fh = Raw()
        with mock.patch.object(sh.os, "ftruncate", side_effect=OSError(5, "input/output error")):
            with self.assertRaises(OSError):
                j.append({"type": "assistant", "n": 1})
        self.assertEqual(j._seg, 1, "a fresh segment, named by the failed offset")
        self.assertTrue(os.path.exists(j._path(1)))
        self.assertEqual(j.next_offset, 1, "nothing advanced")
        j.append({"type": sh.GAP_TYPE, "offset": 1})
        j.append({"type": "result", "n": 2})
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(0)], [(0, 0), (2, 2)], "the old segment still serves record 0; the new one the rest")
        self.assertEqual([(o, r["n"]) for o, r in sh.read_journal_dir(d, 0)], [(0, 0), (2, 2)], "the orphan reader numbers across both")

    def test_the_gap_record_on_disk_survives_a_rewrite_that_fails(self):
        # item 2 (medium): gaps.json was rewritten in place; on the full disk that made the gap the truncation
        # succeeded and the write failed, erasing the record the orphan reader needs
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        j.append({"type": "assistant", "n": 0})
        j.note_gap(1)
        self.assertEqual(json.loads(Path(d, "gaps.json").read_text()), [1])
        real_open = open
        class TruncatesThenFails:
            """the full disk's shape: the open (and its truncation) succeeds, the write does not"""
            def __init__(self, f): self.f = f
            def write(self, b): raise OSError(28, "no space left on device")
            def __enter__(self): return self
            def __exit__(self, *a): self.f.close(); return False
        def failing_open(p, *a, **kw):
            f = real_open(p, *a, **kw)
            return TruncatesThenFails(f) if (str(p).startswith(d) and "gaps" in str(p)) else f
        j.append({"type": "assistant", "n": 2})
        with mock.patch("builtins.open", failing_open):
            j.note_gap(3)                                   # the rewrite fails after its truncation; the previous record must stand
        self.assertEqual(json.loads(Path(d, "gaps.json").read_text()), [1], "the old record stands, not an empty file")
        self.assertEqual(sorted(p.name for p in Path(d).glob("gaps*")), ["gaps.json"], "no temp file left behind")
        j.append({"type": "result", "n": 4})
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(0)], [(0, 0), (2, 2), (4, 4)])

    def test_a_replay_read_is_bounded_by_its_end(self):
        # finding 2: the replay covers the records that existed when the attach began; later ones follow from the
        # live backlog, so a drain that yields during the replay cannot send a record twice
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        for i in range(5):
            j.append({"type": "assistant", "n": i})
        self.assertEqual([o for o, _ in j.read_from(0, 3)], [0, 1, 2])
        self.assertEqual([o for o, _ in j.read_from(2, 99)], [2, 3, 4], "an end past the journal reads to the end")
        self.assertEqual(list(j.read_from(3, 3)), [])


class ParkedRules(unittest.TestCase):
    def _req(self, rid, kind, event=None):
        req = {"subtype": kind}
        if kind == "hook_callback":
            req.update(callback_id="hook_0", input={"hook_event_name": event})
        return {"type": "control_request", "request_id": rid, "request": req}

    def test_park_cancel_answer_and_due_hooks(self):
        p = sh.Parked(self_answer_s=100)
        p.park(self._req("a", "can_use_tool"), 1, now=1000, attached=False)
        p.park(self._req("b", "hook_callback", "Stop"), 2, now=1000, attached=False)
        p.park(self._req("c", "hook_callback", "PostToolUse"), 3, now=1050, attached=False)
        self.assertEqual(p.ids(), ["a", "b", "c"])
        self.assertEqual([o for o, _ in p.records()], [1, 2, 3], "records in offset order, for the re-send on attach")
        self.assertEqual(p.due_hooks(1099), [])
        self.assertEqual(p.due_hooks(1100), ["b"], "a hook is due after the self-answer wait; a permission never is")
        self.assertEqual(p.due_hooks(1200), ["b", "c"])
        # a request delivered LIVE is tracked too (finding 4); its unattended clock starts only when the kernel leaves
        p.park(self._req("d", "hook_callback", "Stop"), 4, now=1000, attached=True)
        self.assertNotIn("d", p.due_hooks(5000), "attached: never self-answered")
        p.detached(now=5000)
        self.assertIn("d", p.due_hooks(5100), "unattended since the detach, due after the wait")
        p.attached()
        self.assertNotIn("d", p.due_hooks(9999), "a kernel is back: its answer is awaited")
        self.assertTrue(p.cancel("c")); self.assertFalse(p.cancel("c"))
        self.assertTrue(p.answer("b")); self.assertFalse(p.answer("b"), "a second answer is a late duplicate")
        self.assertEqual(p.ids(), ["a", "d"])
        self.assertEqual(sh.neutral_hook_response("b", "Stop"),
                         {"type": "control_response", "response": {"subtype": "success", "request_id": "b", "response": {}}})

    def test_every_hook_event_the_kernel_registers_has_a_neutral_answer(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
        start = src.index("hooks={\"Stop\"")
        block = src[start:src.index("permission_mode=sess.mode", start)]
        events = set(__import__("re").findall(r'"([A-Z][A-Za-z]+)": \[HookMatcher', block))
        self.assertTrue(events, "the kernel's hook table was found")
        self.assertTrue(events <= set(sh.HOOK_NEUTRAL_OUTPUT), "missing neutral answers: %r" % (events - set(sh.HOOK_NEUTRAL_OUTPUT)))
        self.assertEqual(sh.LEASE_HEARTBEAT_S, sb.LEASE_HEARTBEAT_S, "the host beats at the stage 1 cadence")
        self.assertLess(sh.HOOK_SELF_ANSWER_S, sh.HOOK_TIMEOUT_S)
        self.assertLess(sh.HOOK_TIMEOUT_S, 600.0, "inside the CLI's default hook budget (the T303 probe)")


# ── the host as a process, this test as the kernel ─────────────────────────────────────────────
class KernelSide:
    """A tiny synchronous kernel stand-in over the host's socket."""

    def __init__(self, sock_path, timeout=10.0, rcvbuf=None):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(timeout)
        if rcvbuf:                                       # a kernel that holds little: the host's writer backs up behind it
            self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(rcvbuf))
        self.s.connect(sock_path)
        self.fr = sh.FrameReader()
        self.frames = []

    def send(self, frame):
        self.s.sendall(sh.encode_frame(frame))

    def recv_until(self, pred, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:   # loop-ok: a bounded socket read
            for f in self.frames:
                if pred(f):
                    return f
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            self.frames.extend(self.fr.feed(chunk))
        for f in self.frames:
            if pred(f):
                return f
        raise AssertionError("no frame matched; got %r" % [f.get("t") for f in self.frames])

    def outs(self):
        return [f for f in self.frames if f.get("t") == "out"]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class JournalReopenOracle(unittest.TestCase):
    """Journal.reopen (the re-exec road) against the live journal it rebuilds: equality, not plausibility."""

    @staticmethod
    def _state(j):
        reads = {o: [(a, b.get("n")) for a, b in j.read_from(o, o + 1)] for o in range(j.next_offset)}
        return {"index": list(j._index), "next": j.next_offset, "segLast": dict(j._seg_last), "seg": j._seg, "pos": j._pos,
                "gaps": set(j.gaps), "segments": j.segments(), "all": [(o, r.get("n")) for o, r in j.read_from(0)], "each": reads}

    def _live(self, d, delete):
        j = sh.Journal(d, segment_bytes=100)
        j.append({"type": "assistant", "pad": "x" * 80, "n": 0})
        j.append({"type": "result", "n": 1})               # past the size: the next segment starts at 2
        j.note_gap(2)                                      # a gap at the new segment's HEAD (round two: counted at both segments)
        j.append({"type": "assistant", "n": 3})
        j.note_gap(4)                                      # a gap between two records
        j.append({"type": "assistant", "pad": "y" * 80, "n": 5})
        j.append({"type": "result", "n": 6})               # the next segment starts at 7
        if delete:
            j.ack(6)
        j.append({"type": "assistant", "n": 7})
        j.append({"type": "result", "n": 8})               # the boundary that deletes the acknowledged segments, when asked
        j.note_gap(9)                                      # a gap after the last record
        return j

    def test_a_reopened_journal_equals_the_live_one_entry_for_entry(self):
        """The shape the verifier drove (2026-09-18): segment_bytes 100, a rotation, a gap at the new segment's head, three
        records. Live reads gave (3, 3), (4, 4), (5, 5); cold gave (4, 3), (5, 4): the head gap entered the index twice, so
        every offset past it read the record before, the newest was unreachable, and appends kept the drift. Here the whole
        state is compared: index, next offset, the per-segment last offsets, the open segment and position, the gaps, the
        segment list, a read of everything and a read of each offset alone; with and without an early segment deleted."""
        for delete in (False, True):
            d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, True)
            j = self._live(d, delete)
            files = sorted(p.name for p in Path(d).glob("journal-*.jsonl"))
            self.assertEqual(files, ["journal-7.jsonl"] if delete else ["journal-0.jsonl", "journal-2.jsonl", "journal-7.jsonl"])
            live = self._state(j)
            j.close()
            r = sh.Journal.reopen(d, segment_bytes=100)
            cold = self._state(r)
            for key in live:
                self.assertEqual(cold[key], live[key], "%s differs after the reopen (segment deleted: %r)" % (key, delete))
            if delete:
                self.assertEqual(live["all"], [(7, 7), (8, 8)], "a deleted segment's offsets read as nothing, live and cold alike")
            else:
                self.assertEqual(live["all"], [(0, 0), (1, 1), (3, 3), (5, 5), (6, 6), (7, 7), (8, 8)])
            off = r.append({"type": "result", "n": 10})
            self.assertEqual((off, [x for x in r.read_from(9)]), (10, [(10, {"type": "result", "n": 10})]), "and it appends where it should")
            r.close()

    def test_an_empty_directory_reopens_at_offset_zero(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, True)
        r = sh.Journal.reopen(d)
        self.assertEqual((r.next_offset, r._index, r.segments()), (0, [], [0]))
        r.close()


class AdoptedCli(unittest.TestCase):
    """The CLI a re-executed host inherits: the handoff's descriptors are trusted only once the CLI's own table agrees, and
    an exit the new process cannot learn is unknown, never a clean zero (round two of the re-exec review)."""

    def test_the_adoption_confirms_the_descriptors_against_the_clis_own_table(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("the confirmation reads /proc")
        match = getattr(sh, "_pipe_fds_match_cli", None)
        self.assertIsNotNone(match, "one confirmation body for the old process and the new one (the base confirmed in the old process only)")
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def stop():
            child.kill(); child.wait(timeout=10)
        self.addCleanup(stop)
        ours = {"stdin": child.stdin.fileno(), "stdout": child.stdout.fileno(), "stderr": child.stderr.fileno()}
        self.assertTrue(match(child.pid, ours), "the child's own pipes agree")
        r, w = os.pipe(); self.addCleanup(os.close, r); self.addCleanup(os.close, w)
        wrong = dict(ours, stdout=r)
        self.assertFalse(match(child.pid, wrong), "another pipe under the CLI's stdout name does not")
        t = sh.AdoptedCliTransport({"max_buffer_size": 65536}, lambda line: None, {"cli_pid": child.pid, "fds": wrong})
        loop = asyncio.new_event_loop()
        try:
            with self.assertRaises(RuntimeError, msg="the adoption refuses a handoff the CLI's table contradicts"):
                loop.run_until_complete(t.connect())
        finally:
            loop.close()

    def test_an_exit_the_adopted_process_cannot_learn_is_unknown_not_clean(self):
        p = subprocess.Popen(["true"]); p.wait()           # reaped already: waitpid raises ChildProcessError
        a = sh._AdoptedProcess(p.pid, None, None, None)
        a._waitpid()
        self.assertEqual((a.returncode, getattr(a, "exited", None)), (None, True),
                         "gone with the exit unknown: returncode None (the exit frame's word for unknown), never 0")


class HostProcess(unittest.TestCase):
    """Each test starts one host on the fake CLI in a private state root and kills everything after."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, True)
        self.fake_log = os.path.join(self.state, "fake-cli.log")
        self.tdir = os.path.join(self.state, "transcripts")

    def _spec(self, **over):
        d = Path(self.state) / "hosts" / SID
        d.mkdir(parents=True, mode=0o700)
        spec = {"sid": SID, "name": "web", "version": "abc12345", "state_dir": self.state, "protocol": 1,
                "cli_path": FAKE, "cwd": self.state, "permission_prompt_tool_name": "stdio", "permission_mode": "default",
                "env": {"FAKE_CLI_LOG": self.fake_log, "FAKE_CLI_TRANSCRIPT_DIR": self.tdir, "FAKE_CLI_SESSION_ID": FSID,
                        "ROMP_CANARY_SECRET": "canary-" + uuid.uuid4().hex},
                "max_buffer_size": 1024 * 1024, "hook_self_answer_s": 2, "unattached_grace_s": 3600}
        extra = over.pop("env_extra", None)
        spec.update(over)
        if extra:
            spec["env"].update(extra)
        p = d / "spawn.json"
        p.write_text(json.dumps(spec)); p.chmod(0o600)
        return str(p), spec

    def _start(self, sdk=False, **over):
        spec_path, spec = self._spec(**over)
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        env.pop("ROMP_SDK_SITE", None)
        if sdk:
            env["ROMP_SDK_SITE"] = str(SDK_SITE)
        else:
            env["ROMP_SDK_SITE"] = os.path.join(self.state, "no-sdk-here")
        host = subprocess.Popen([sys.executable, os.path.join(BIN, "romp-session-host"), spec_path],
                                stdout=subprocess.DEVNULL, stderr=open(os.path.join(self.state, "host.stderr"), "w"), env=env,
                                start_new_session=True)
        self.addCleanup(self._kill_group, host)
        sock = Path(self.state) / "hosts" / (SID[:8] + ".sock")
        deadline = time.time() + 15
        while time.time() < deadline and not (sock.exists() and self._lease()):   # loop-ok: a bounded wait on two events
            if host.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNone(host.poll(), "the host is running: " + open(os.path.join(self.state, "host.stderr")).read()[-800:])
        self.assertTrue(sock.exists(), "the socket exists")
        self.assertTrue(self._lease(), "the lease is written once the CLI has a pid")
        return host, str(sock), spec

    @staticmethod
    def _kill_group(proc):
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)

    def _lease(self):
        return sb.read_lease(self.state, SID)

    def _hostlog(self):
        p = Path(self.state) / "hosts" / SID / "host.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def _attach(self, sock, ack=-1, pid=4242):
        k = KernelSide(sock)
        k.send({"t": "attach", "kernel": {"pid": pid, "start": "1", "version": "abc12345"}, "ack": ack})
        hello = k.recv_until(lambda f: f.get("t") == "hello")
        return k, hello

    def _user(self, text):
        return json.dumps({"type": "user", "message": {"role": "user", "content": text}})

    def _journal_landed(self, n, timeout=15):
        """The journal once the writer has landed `n` records: the host forwards a record to the kernel at once and
        journals it on its own writer task, so a frame on the socket says nothing about the disk yet (the writer may
        lag by design, and a slow runner's disk shows it: the macOS cell read three records where the socket had four,
        2026-09-16). The event waited on is the n-th record on disk, never a fixed pause."""
        d = os.path.join(self.state, "hosts", SID)
        deadline = time.time() + timeout
        journal = list(sh.read_journal_dir(d))
        while time.time() < deadline and len(journal) < n:                # loop-ok: the event is the writer's n-th record on disk
            time.sleep(0.005)
            journal = list(sh.read_journal_dir(d))
        return journal

    def test_the_lease_and_the_hello_carry_the_clis_spawn_time_once_and_the_specs_login(self):
        """The host is the authority for when ITS CLI spawned: the lease's spawnedAt is stamped once at the spawn and stands
        across the beats (before 2026-09-14 every beat rewrote it with the beat's time, so a kernel copying it would have
        moved the CLI's epoch at every attach), the hello's cli carries the same value, and cli.login echoes the spec's login
        identifier, so the kernel that first sees the CLI stamps its epoch and the login its launch billed."""
        host, sock, spec = self._start(login="login-rec-1")
        lease = self._lease()
        self.assertIsInstance(lease.get("spawnedAt"), int)
        k, hello = self._attach(sock)
        self.assertEqual((hello["cli"]["spawnedAt"], hello["cli"]["login"], hello["cli"]["pid"]),
                         (lease["spawnedAt"], "login-rec-1", lease["pid"]))
        deadline = time.time() + 3 * sh.LEASE_HEARTBEAT_S
        while time.time() < deadline and self._lease().get("t") == lease["t"]:   # loop-ok: a bounded wait on the next beat
            time.sleep(0.1)
        later = self._lease()
        self.assertNotEqual(later["t"], lease["t"], "a beat rewrote the lease")
        self.assertEqual(later["spawnedAt"], lease["spawnedAt"], "the spawn time stands across the beat")

    def test_a_turn_flows_through_the_host_and_is_journaled_under_a_host_held_lease(self):
        host, sock, spec = self._start()
        lease = self._lease()
        self.assertEqual((lease["holder"]["pid"], lease["holder"]["kind"], lease["version"]), (host.pid, "host", "abc12345"))
        self.assertEqual(sb.lease_state(lease, time.time()), "valid")
        k, hello = self._attach(sock)
        self.assertEqual((hello["cli"]["pid"], hello["journal"]["next"], hello["parked"], hello["inflight"]), (lease["pid"], 0, [], 0))
        k.send({"t": "in", "data": json.dumps({"type": "control_request", "request_id": "req_0_aaaa",
                                                 "request": {"subtype": "initialize", "hooks": {"Stop": [{"matcher": None, "hookCallbackIds": ["hook_0"], "timeout": 540}]}}})})
        resp = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_response")
        self.assertEqual(resp["data"]["response"]["request_id"], "req_0_aaaa")
        k.send({"t": "in", "data": self._user("hello sleep=0.3")})
        res = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        offsets = [f["offset"] for f in k.outs()]
        self.assertEqual(offsets, list(range(len(offsets))), "offsets are ordinals from zero")
        kinds = [f["data"]["type"] for f in k.outs()]
        self.assertEqual(kinds, ["control_response", "system", "assistant", "result"])
        journal = self._journal_landed(len(kinds))                        # the writer lands them behind the socket, by design
        self.assertEqual([r["type"] for _, r in journal], kinds, "the journal holds every record the CLI emitted, in order")
        self.assertEqual(self._lease()["fsid"], FSID, "the lease's conversation id follows the init (it started as the romp sid)")
        k.send({"t": "ack", "offset": res["offset"]})
        # secrets: the canary environment value appears nowhere the host writes or sends
        canary = spec["env"]["ROMP_CANARY_SECRET"]
        blob = json.dumps(self._hostlog()) + json.dumps([r for _, r in journal]) + json.dumps(k.frames)
        self.assertNotIn(canary, blob, "no environment value in host.log, the journal or a frame")
        self.assertNotIn("FAKE_CLI_LOG", json.dumps(self._hostlog()), "no spec content in host.log")
        k.close()

    def test_the_journal_lands_every_record_behind_the_socket_when_the_writer_lags(self):
        """The reorder forced through the host's own seam: a writer that lands each record 0.4 s late. The socket
        delivers the whole turn first (the host never pauses the reader for the disk); a journal read at that instant
        holds fewer records than the socket did (the macOS cell's failure, 2026-09-16, with no seam: a slower disk), and
        the journal holds them all, in order, once the writer has landed them. The test reads the journal at the frame
        and again at the event, so the ordering the host promises (every record, in order, eventually) is what is pinned,
        never the instant the disk catches up."""
        host, sock, spec = self._start(_test_journal_delay_s=0.4)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("hello sleep=0")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        kinds = [f["data"]["type"] for f in k.outs()]
        self.assertEqual(kinds[-1], "result")
        at_the_frame = list(sh.read_journal_dir(os.path.join(self.state, "hosts", SID)))
        self.assertLess(len(at_the_frame), len(kinds), "with the writer lagging, the disk trails the socket at the frame (the shape the assertion must not read)")
        journal = self._journal_landed(len(kinds), timeout=20)
        self.assertEqual([r["type"] for _, r in journal], kinds, "and holds every record, in the socket's order, once the writer landed them")
        k.close()

    def _reexec(self, k, version="def67890", launcher=None):
        k.send({"t": "reexec", "python": sys.executable, "launcher": launcher or os.path.join(BIN, "romp-session-host"), "version": version})
        return k.recv_until(lambda f: f.get("t") == "reexec", timeout=15)

    def _lease_version_becomes(self, version, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:                                   # loop-ok: the event is the re-executed host's lease
            l = self._lease()
            if l and str(l.get("version") or "") == version and (Path(self.state) / "hosts" / (SID[:8] + ".sock")).exists():
                return l
            time.sleep(0.05)
        return self._lease()

    def test_a_host_re_execs_into_the_kernels_code_keeping_its_cli_journal_and_pid(self):
        """The version-skew fix (2026-09-18): a host kept its code for its session's life, so a host bug outlived every kernel
        deploy. Asked by a newer kernel, an idle host execs itself into the kernel's launcher on the same pid with the CLI's
        pipes held across the exec: the same CLI pid, the same host pid, the journal continued from its files, one attach
        after, and a send that round-trips."""
        host, sock, spec = self._start()                                # spec version abc12345, the "old" code
        k, hello0 = self._attach(sock)
        k.send({"t": "in", "data": self._user("before sleep=0")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        n0 = len(k.outs()); cli_pid = self._lease()["pid"]
        spec_path = Path(self.state) / "hosts" / SID / "spawn.json"
        spec_path.write_text(json.dumps(dict(json.loads(spec_path.read_text()), version="def67890")))   # the kernel rewrites it first
        ans = self._reexec(k)
        self.assertEqual((ans.get("ok"), ans.get("when")), (True, "now"), "an idle host accepts at once (the base has no such frame): %r" % ans)
        k.close()
        lease = self._lease_version_becomes("def67890")
        self.assertEqual(lease.get("version"), "def67890", "the lease follows the code the host now runs")
        self.assertEqual((lease["pid"], lease["holder"]["pid"]), (cli_pid, host.pid), "the same CLI, the same host pid across the exec")
        self.assertIsNone(host.poll(), "the host process lives on (exec, not a restart)")
        k2, hello = self._attach(sock, ack=n0 - 1, pid=4343)
        self.assertEqual((hello["host"]["version"], hello["cli"]["pid"], hello["journal"]["next"], hello["inflight"]),
                         ("def67890", cli_pid, n0, 0), "the hello names the new version, the same CLI, the journal where it was, no open turn")
        k2.send({"t": "in", "data": self._user("after sleep=0")})
        res = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        self.assertEqual(res["offset"], n0 + 1, "the journal numbering continues across the exec (assistant, then result)")
        journal = list(sh.read_journal_dir(os.path.join(self.state, "hosts", SID)))
        self.assertEqual([r["type"] for _, r in journal][-4:], ["assistant", "result", "assistant", "result"], "one journal, both turns")
        log = self._hostlog()
        self.assertEqual([r["kind"] for r in log if r["kind"] in ("reexec", "reexeced")], ["reexec", "reexeced"], "the exec and the adoption, once each")
        self.assertEqual(sum(1 for r in log if r["kind"] == "attached"), 2, "one attach before, one after; none lost to a death")
        self.assertEqual([r["kind"] for r in log if r["kind"] in ("reexec", "socket-ready", "reexeced")],
                         ["socket-ready", "reexec", "socket-ready", "reexeced"],
                         "the re-executed host serves its socket BEFORE it writes the lease and logs the adoption (round two: the lease "
                         "came first, and a kernel that read it could connect to a path nobody served yet)")
        ready_t = [r["t"] for r in log if r["kind"] == "socket-ready"][-1]
        self.assertGreaterEqual(lease["t"], ready_t - 0.001, "the lease with the new version is stamped after the socket is served")
        self.assertFalse((Path(self.state) / "hosts" / SID / "reexec.json").exists(), "the handoff file is consumed")
        k2.close()

    def test_a_re_exec_asked_mid_turn_waits_for_the_turns_result_then_tells_the_kernel(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        spec_path = Path(self.state) / "hosts" / SID / "spawn.json"
        spec_path.write_text(json.dumps(dict(json.loads(spec_path.read_text()), version="def67890")))
        k.send({"t": "in", "data": self._user("slow sleep=1.2")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant", timeout=15)
        ans = self._reexec(k)
        self.assertEqual((ans.get("ok"), ans.get("when")), (True, "at-turn-end"), "a turn is open: deferred to its result, never a timer")
        res = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        now = k.recv_until(lambda f: f.get("t") == "reexec-now", timeout=15)
        self.assertTrue(now, "the kernel is told before the socket closes")
        kinds = [f.get("t") for f in k.frames]
        self.assertLess(kinds.index("reexec-now"), len(kinds), "the result came first, then the handover")
        self.assertGreater(kinds.index("reexec-now"), [i for i, f in enumerate(k.frames) if f.get("t") == "out" and f["data"].get("type") == "result"][0])
        k.close()
        lease = self._lease_version_becomes("def67890")
        self.assertEqual(lease.get("version"), "def67890")
        k2, hello = self._attach(sock, ack=res["offset"], pid=4343)
        self.assertEqual((hello["host"]["version"], hello["inflight"]), ("def67890", 0))
        k2.send({"t": "in", "data": self._user("after sleep=0")})
        k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        k2.close()

    def _log_row(self, pred, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:                                   # loop-ok: the event is the host.log row the test waits for
            for r in self._hostlog():
                if pred(r):
                    return r
            time.sleep(0.02)
        return None

    def _transcript_records(self):
        p = Path(self.tdir) / (FSID + ".jsonl")
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def test_output_arriving_while_a_slow_kernel_drains_defers_the_handover_and_loses_nothing(self):
        """Round three of the re-exec review (2026-09-19): the quiet check ran BEFORE the kernel's drain was awaited and was never
        re-read, so a kernel behind on its socket (a burst it had not read) opened up to five seconds in which the CLI's output
        entered the stream buffer while the reader was held, and the exec discarded it: with three bookkeeping rows emitted a
        second after the request, the journal kept the one row the in-flight read had taken and lost the other two, the counts
        agreed (no drift row, no fault, no line), and a turn had re-opened right before the exec. Now the kernel's backlog is
        drained first and the check is the last thing before the exec: a kernel that does not drain within the bound defers the
        handover (`reexec-deferred`, kernel-behind), the rows are read and journaled meanwhile, the next result hands over, and
        the journal equals what the CLI emitted, record for record."""
        trigger = os.path.join(self.state, "trigger")
        host, sock, spec = self._start(env_extra={"FAKE_CLI_TRIGGER": trigger, "FAKE_CLI_TRIGGER_DELAY": "1.0", "FAKE_CLI_TRIGGER_COUNT": "3"})
        k = KernelSide(sock, timeout=30.0, rcvbuf=2048)                 # a kernel that will not read for a while
        k.send({"t": "attach", "kernel": {"pid": 4242, "start": "1", "version": "abc12345"}, "ack": -1})
        k.recv_until(lambda f: f.get("t") == "hello")
        sp = Path(self.state) / "hosts" / SID / "spawn.json"
        sp.write_text(json.dumps(dict(json.loads(sp.read_text()), version="def67890")))
        k.send({"t": "in", "data": self._user("padded pad=600000 sleep=0")})
        self.assertEqual([r["type"] for _, r in self._journal_landed(3, timeout=20)], ["system", "assistant", "result"],
                         "the turn is over in the host (its writer backs up behind the kernel, which has not read)")
        open(trigger, "w").close()                                       # a second from now: three rows outside any turn
        k2 = KernelSide(sock)                                            # the request over a second connection: the answer must not queue behind the burst
        ans = self._reexec(k2)
        self.assertEqual((ans.get("ok"), ans.get("when")), (True, "now"), "idle: accepted at once: %r" % ans)
        row = self._log_row(lambda r: r["kind"] in ("reexec-deferred", "reexec"), timeout=20)
        self.assertEqual((row or {}).get("kind"), "reexec-deferred", "a kernel behind on its socket defers the handover, never an exec over "
                         "output the reader could not take (the base execed after the drain's bound and lost two rows): %r" % row)
        self.assertEqual(row.get("reason"), "kernel-behind")
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("n") == "trigger2", timeout=30)   # the kernel catches up
        k.send({"t": "in", "data": self._user("after sleep=0")})
        k.recv_until(lambda f: f.get("t") == "reexec-now", timeout=25)
        seen = {f["offset"]: (f["data"].get("type"), f["data"].get("uuid")) for f in k.outs()}
        k.close(); k2.close()
        self.assertEqual(self._lease_version_becomes("def67890", timeout=25).get("version"), "def67890", "the next result handed over")
        emitted = [(r["type"], r.get("uuid")) for r in self._transcript_records()]
        journal = self._journal_landed(len(emitted), timeout=20)
        self.assertEqual([(r["type"], r.get("uuid")) for _, r in journal], emitted, "the journal holds every record the CLI emitted, the three "
                         "bookkeeping rows included")
        self.assertEqual([o for o, _ in journal], list(range(len(emitted))))
        by_offset = {o: (r["type"], r.get("uuid")) for o, r in journal}
        self.assertEqual({o: by_offset.get(o) for o in seen}, seen, "every frame the kernel saw sits at its own offset")
        log = self._hostlog()
        self.assertEqual([r["kind"] for r in log if r["kind"] in ("reexec-deferred", "reexec", "reexeced")], ["reexec-deferred", "reexec", "reexeced"])
        self.assertTrue(any(r["kind"] == "turn-reopened" for r in log), "the bookkeeping rows re-opened a turn, which the after turn's result closed")
        self.assertEqual([r for r in log if r["kind"] in ("journal-offset-drift", "journal-write-failed")], [])

    def test_a_turn_re_opened_during_the_handovers_flush_defers_it(self):
        """The other deferral (a pin: the base deferred here too, before its drain): a row arriving while the journal flush runs
        (the writer lagging through the spec's seam) re-opens a turn, and the handover waits for that turn's result."""
        trigger = os.path.join(self.state, "trigger")
        host, sock, spec = self._start(_test_journal_delay_s=0.4, env_extra={"FAKE_CLI_TRIGGER": trigger, "FAKE_CLI_TRIGGER_DELAY": "0", "FAKE_CLI_TRIGGER_COUNT": "1"})
        k, _ = self._attach(sock)
        sp = Path(self.state) / "hosts" / SID / "spawn.json"
        sp.write_text(json.dumps(dict(json.loads(sp.read_text()), version="def67890")))
        k.send({"t": "in", "data": self._user("one sleep=0")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        open(trigger, "w").close()
        ans = self._reexec(k)
        self.assertEqual((ans.get("ok"), ans.get("when")), (True, "now"))
        row = self._log_row(lambda r: r["kind"] in ("reexec-deferred", "reexec"), timeout=20)
        self.assertEqual(((row or {}).get("kind"), (row or {}).get("reason"), (row or {}).get("inflight")), ("reexec-deferred", "output-arriving", 1), "%r" % row)
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("n") == "trigger0", timeout=15)
        k.send({"t": "in", "data": self._user("two sleep=0")})
        k.recv_until(lambda f: f.get("t") == "reexec-now", timeout=25)
        k.close()
        self.assertEqual(self._lease_version_becomes("def67890", timeout=25).get("version"), "def67890")
        emitted = [(r["type"], r.get("uuid")) for r in self._transcript_records()]
        journal = self._journal_landed(len(emitted), timeout=20)
        self.assertEqual([(r["type"], r.get("uuid")) for _, r in journal], emitted)

    def _transcript_types(self):
        p = Path(self.tdir) / (FSID + ".jsonl")
        return [json.loads(l).get("type") for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def _transcript_results(self, n, timeout=25):
        deadline = time.time() + timeout
        types_ = self._transcript_types()
        while time.time() < deadline and types_.count("result") < n:      # loop-ok: the event is the fake CLI's n-th result
            time.sleep(0.02)
            types_ = self._transcript_types()
        return types_

    def test_a_lagging_writer_and_a_queued_turn_lose_no_record_at_the_handover(self):
        """Round two of the re-exec review (2026-09-18): the reader ran on through the handover, so with the writer lagging
        (the spec's own seam) and a second message queued behind the first turn, the rows read after the journal flush were
        counted in the handoff and lost with the process: the disk held three records where the hello said five, every later
        record landed one offset below the offset the kernel was told (journal-offset-drift rows), and a replay served the
        third turn's rows under the second turn's offsets. Now the reader is held at the handover, a turn that re-opened
        meanwhile defers the exec to its own result, and every record read is on disk before the exec: the journal holds
        exactly what the CLI emitted, numbered contiguously, every frame's offset is its journal offset, no drift row."""
        host, sock, spec = self._start(_test_journal_delay_s=0.4)
        k, _ = self._attach(sock)
        sp = Path(self.state) / "hosts" / SID / "spawn.json"
        sp.write_text(json.dumps(dict(json.loads(sp.read_text()), version="def67890")))
        k.send({"t": "in", "data": self._user("first slow sleep=1.5")})
        k.send({"t": "in", "data": self._user("second sleep=0")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant", timeout=15)
        ans = self._reexec(k)
        self.assertEqual((ans.get("ok"), ans.get("when")), (True, "at-turn-end"))
        k.recv_until(lambda f: f.get("t") == "reexec-now", timeout=25)
        seen = {f["offset"]: f["data"].get("type") for f in k.outs()}
        k.close()
        lease = self._lease_version_becomes("def67890", timeout=25)
        self.assertEqual(lease.get("version"), "def67890", "the exec happened")
        d = os.path.join(self.state, "hosts", SID)
        k2, hello = self._attach(sock, ack=-1, pid=4343)
        k2.send({"t": "in", "data": self._user("third sleep=0")})
        emitted = self._transcript_results(3)
        self.assertEqual(emitted.count("result"), 3, "three turns ran: %r" % emitted)
        last = k2.recv_until(lambda f: f.get("t") == "out" and f["offset"] == len(emitted) - 1, timeout=20)
        self.assertEqual(last["data"].get("type"), "result", "the last frame is the third turn's result at the last offset")
        journal = self._journal_landed(len(emitted), timeout=20)
        self.assertEqual([r.get("type") for _, r in journal], emitted, "the journal holds every record the CLI emitted, across the exec")
        self.assertEqual([o for o, _ in journal], list(range(len(emitted))), "numbered contiguously: nothing skipped, nothing twice")
        by_offset = {o: r.get("type") for o, r in journal}
        seen.update({f["offset"]: f["data"].get("type") for f in k2.outs()})
        self.assertEqual({o: by_offset.get(o) for o in seen}, seen, "every frame the kernels saw sits at its own offset in the journal")
        self.assertEqual([r for r in self._hostlog() if r["kind"] in ("journal-offset-drift", "journal-write-failed")], [], "no drift")
        self.assertEqual([r["kind"] for r in self._hostlog() if r["kind"] in ("reexec", "reexeced")], ["reexec", "reexeced"], "one exec")
        k2.close()

    def test_a_re_exec_it_cannot_do_is_refused_and_the_host_serves_on(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        ans = self._reexec(k, launcher=os.path.join(self.state, "no-such-launcher"))
        self.assertEqual((ans.get("ok"), ans.get("reason")), (False, "no such launcher"), "a bad launcher is a refusal, not an exec")
        k.send({"t": "in", "data": self._user("still here sleep=0")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        self.assertEqual(self._lease().get("version"), "abc12345", "the old host, the old version, still serving")
        self.assertEqual([r["kind"] for r in self._hostlog() if r["kind"].startswith("reexec")], ["reexec-refused"])
        k.close()
    def test_a_turn_that_folded_a_queued_message_leaves_no_open_turn_for_the_next_attach(self):
        """The stuck-Working shape (2026-09-18): the host counted every user line fed and took one off per result, so a
        turn that folded a second message (the real CLI answers messages typed while it works with the running turn's
        single result) left the count at one for good, and every kernel that attached afterwards adopted it: Ready on the
        page, Working to the drain, every send parked. One result now closes everything fed."""
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("one sleep=0.6 merge=1")})
        k.send({"t": "in", "data": self._user("two sleep=0")})                   # queued while the turn runs; folded into it
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        self.assertEqual(sum(1 for f in k.outs() if f["data"].get("type") == "result"), 1, "one result answered both messages")
        k.send({"t": "detach"}); k.close()
        k2, hello = self._attach(sock, ack=-1, pid=4343)
        self.assertEqual(hello["inflight"], 0, "no open turn after the fold's result (the base reported one: fed two, resulted one)")
        k2.send({"t": "in", "data": self._user("three sleep=0")})                 # and the idle CLI takes the next send at once
        k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result" and f["data"].get("uuid") not in
                      {g["data"].get("uuid") for g in k.outs() if g["data"].get("type") == "result"}, timeout=15)
        k2.close()

    def test_a_queued_line_the_cli_runs_as_its_own_turn_re_opens_the_count_and_holds_the_grace_gate(self):
        """Round two of 1838: zeroing the count on every result read 0 during a genuinely separate queued turn (two lines
        fed before the first result, the CLI running the second as its own turn): the hello said idle, the drain fed into
        the running turn, and the unattached grace gate ended a working CLI. The count re-opens from the OUTPUT side: the
        second turn's first assistant row after the result. A fold (one result for both) still reads 0."""
        host, sock, spec = self._start(unattached_grace_s=1.5)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("one sleep=0.3")})
        k.send({"t": "in", "data": self._user("two sleep=1.0")})                   # queued; the fake runs it as its own turn
        first = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant" and f["offset"] > first["offset"], timeout=15)
        k.send({"t": "detach"}); k.close()                                          # unattached while the second turn runs
        k2, hello = self._attach(sock, ack=first["offset"], pid=4343)
        self.assertEqual(hello["inflight"], 1, "the second turn counts as open once its output started (the fold-only settle read 0)")
        second = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result" and f["offset"] > first["offset"], timeout=15)
        k2.send({"t": "detach"}); k2.close()
        k3, hello3 = self._attach(sock, ack=second["offset"], pid=4344)
        self.assertEqual((hello3["inflight"], hello3["exited"]), (0, False), "idle after the second result, the CLI alive")
        kinds = [r["kind"] for r in self._hostlog()]
        self.assertNotIn("unattached-grace-expired", kinds, "the grace gate saw the open turn and did not end a working CLI")
        self.assertEqual(kinds.count("turn-reopened"), 1, "the re-open happened once, for the queued turn")
        k3.close()

    def test_only_a_user_line_carrying_text_opens_a_turn(self):
        fn = getattr(sh, "_opens_turn", None)
        self.assertIsNotNone(fn, "the host decides which user lines open a turn (the base counted every one)")
        self.assertTrue(fn({"type": "user", "message": {"role": "user", "content": "hello"}}))
        self.assertTrue(fn({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}))
        self.assertFalse(fn({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}))
        self.assertFalse(fn({"type": "user", "message": {"role": "user", "content": ""}}))
        self.assertFalse(fn({"type": "user"}))

    def test_a_detached_kernel_reattaches_and_replays_from_its_ack_while_the_turn_kept_running(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("long sleep=2.5")})
        first = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "ack", "offset": first["offset"]})
        k.send({"t": "detach"}); k.close()
        time.sleep(0.5)
        self.assertIsNone(host.poll(), "the host keeps running the turn")
        self.assertEqual(sb.lease_state(self._lease(), time.time()), "valid", "and keeps the lease")
        k2, hello = self._attach(sock, ack=first["offset"], pid=4343)
        self.assertEqual(hello["inflight"], 1, "the host reports the open turn, so the new kernel knows it is mid-turn")
        res = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=10)
        offs = [f["offset"] for f in k2.outs()]
        self.assertEqual(offs[0], first["offset"] + 1, "replay starts after the acknowledged offset")
        self.assertEqual(offs, list(range(offs[0], offs[0] + len(offs))), "replay then live, in order, no gap")
        self.assertEqual(res["data"]["result"], "done", "the turn the first kernel started finished under the second")
        log_kinds = [r["kind"] for r in self._hostlog()]
        self.assertIn("detached", log_kinds); self.assertEqual(log_kinds.count("attached"), 2)
        k2.close()

    def test_a_permission_request_parks_while_unattached_and_the_late_answer_reaches_the_cli(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("please ask=permission after=0.6 sleep=0.2")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "request-open" for r in self._hostlog()):   # loop-ok
            time.sleep(0.05)
        opened = [r for r in self._hostlog() if r["kind"] == "request-open"]
        self.assertEqual([r["attached"] for r in opened], [False], "the request opened while no kernel was attached")
        k2, hello = self._attach(sock, ack=-1)
        self.assertEqual(len(hello["parked"]), 1, "the parked request is named on attach")
        rid = hello["parked"][0]
        req = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(req["data"]["request_id"], rid)
        k2.send({"t": "in", "data": json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                                                            "response": {"behavior": "allow", "updatedInput": {}}}})})
        res = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        texts = [c["text"] for f in k2.outs() if f["data"].get("type") == "assistant" for c in f["data"]["message"]["content"]]
        self.assertIn("permission answered", texts, "the late answer reached the CLI and the turn went on")
        # a second answer for the same id is a late duplicate the host drops
        k2.send({"t": "in", "data": json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": {}}})})
        k2.send({"t": "ping"}); k2.recv_until(lambda f: f.get("t") == "pong")
        self.assertIn("late-answer-dropped", [r["kind"] for r in self._hostlog()])
        k2.close()

    def test_a_parked_hook_is_answered_by_the_host_at_the_deadline_and_said_loudly(self):
        host, sock, spec = self._start(hook_self_answer_s=1)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": json.dumps({"type": "control_request", "request_id": "req_0_bbbb",
                                                 "request": {"subtype": "initialize", "hooks": {"Stop": [{"matcher": None, "hookCallbackIds": ["hook_3"], "timeout": 540}]}}})})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_response")
        k.send({"t": "in", "data": self._user("go hook=Stop after=0.6 sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "hook-self-answered" for r in self._hostlog()):   # loop-ok
            time.sleep(0.1)
        rows = [r for r in self._hostlog() if r["kind"] == "hook-self-answered"]
        self.assertEqual(len(rows), 1, "the host answered the parked hook itself once")
        self.assertEqual((rows[0]["event"], rows[0]["callbackId"]), ("Stop", "hook_3"))
        self.assertGreaterEqual(rows[0]["parkedS"], 1.0)
        k2, hello = self._attach(sock)
        self.assertEqual(hello["parked"], [], "nothing left parked")
        k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        texts = [c["text"] for f in k2.outs() if f["data"].get("type") == "assistant" for c in f["data"]["message"]["content"]]
        self.assertIn("hook answered", texts, "the CLI took the neutral answer and went on")
        k2.close()

    def test_a_cancel_from_the_cli_drops_the_parked_request(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("go ask=permission after=0.6 cancel-after=0.5 sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "request-cancelled" for r in self._hostlog()):   # loop-ok
            time.sleep(0.1)
        self.assertIn("request-cancelled", [r["kind"] for r in self._hostlog()])
        k2, hello = self._attach(sock)
        self.assertEqual(hello["parked"], [])
        k2.close()

    def test_end_closes_stdin_and_the_cli_exits_then_the_lease_goes(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        k.send({"t": "end", "grace": 30})
        ex = k.recv_until(lambda f: f.get("t") == "exit")
        self.assertEqual((ex["cause"], ex["code"]), ("end", 0))
        host.wait(timeout=10)
        self.assertEqual(host.returncode, 0)
        self.assertIsNone(self._lease(), "the lease is removed when the host leaves")
        self.assertFalse(os.path.exists(sock), "the socket is removed")
        k.close()

    def test_end_with_a_short_grace_forces_a_cli_that_will_not_leave(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("slow sleep=30")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "end", "grace": 1})
        ex = k.recv_until(lambda f: f.get("t") == "exit", timeout=15)
        self.assertEqual(ex["cause"], "end-forced")
        self.assertIn("end-forced", [r["kind"] for r in self._hostlog()])
        host.wait(timeout=10)
        k.close()

    def test_signal_reaches_the_cli_and_a_second_kernel_is_told_busy(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("slow sleep=30")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        other = KernelSide(sock)
        other.send({"t": "attach", "kernel": {"pid": 9, "start": "1", "version": ""}, "ack": -1})
        self.assertEqual(other.recv_until(lambda f: f.get("t") == "busy")["kernel"]["pid"], 4242)
        other.close()
        k.send({"t": "signal", "sig": "INT"})
        res = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=10)
        self.assertEqual(res["data"]["result"], "interrupted")
        k.close()

    def test_an_unattached_idle_cli_is_ended_after_the_grace(self):
        host, sock, spec = self._start(unattached_grace_s=1)
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        k.send({"t": "detach"}); k.close()
        host.wait(timeout=15)
        kinds = [r["kind"] for r in self._hostlog()]
        self.assertIn("unattached-grace-expired", kinds)
        self.assertEqual(kinds[-1], "host-exited")
        self.assertIsNone(self._lease())

    def test_socket_loss_without_detach_is_a_kernel_death_the_host_survives(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("long sleep=2")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.close()                                   # no detach: the kernel died
        time.sleep(0.5)
        self.assertIsNone(host.poll())
        self.assertIn("kernel-lost", [r["kind"] for r in self._hostlog()])
        k2, hello = self._attach(sock)
        res = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=10)
        self.assertEqual(res["data"]["result"], "done")
        k2.close()

    def test_a_request_delivered_live_to_a_kernel_that_dies_is_still_open_and_re_sent_on_attach(self):
        # finding 4: the kernel RECEIVED the permission request (and acknowledged past it) but died before the
        # user answered; the next kernel must see the request again or the CLI hangs on it forever
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("please ask=permission after=0.3 sleep=0.2")})
        req = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        k.send({"t": "ack", "offset": req["offset"]}); k.send({"t": "ping"}); k.recv_until(lambda f: f.get("t") == "pong")
        k.close()                                       # the kernel dies with the request unanswered
        k2, hello = self._attach(sock, ack=req["offset"], pid=4343)
        self.assertEqual(hello["parked"], [req["data"]["request_id"]], "still open, named on attach")
        again = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(again["offset"], req["offset"], "re-sent with its original offset although acknowledged")
        rid = req["data"]["request_id"]
        k2.send({"t": "in", "data": json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                                                            "response": {"behavior": "allow", "updatedInput": {}}}})})
        k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        texts = [c["text"] for f in k2.outs() if f["data"].get("type") == "assistant" for c in f["data"]["message"]["content"]]
        self.assertIn("permission answered", texts)
        k2.close()

    def test_a_journal_write_fault_is_a_fault_frame_not_the_clis_death(self):
        # finding 3: a failed journal write used to end the read loop and be reported as the CLI dying
        host, sock, spec = self._start(_test_journal_fault_at=1)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.2")})
        res = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        faults = [f for f in k.frames if f.get("t") == "fault"]
        self.assertEqual([f["kind"] for f in faults], ["journal-write-failed"])
        self.assertIsNone(host.poll(), "the host and its CLI are still running")
        kinds = [r["kind"] for r in self._hostlog()]
        self.assertIn("journal-write-failed", kinds); self.assertNotIn("cli-exited", kinds)
        # live delivery was complete (the kernel got every record) even though offset 1 is missing from the journal
        self.assertEqual([f["offset"] for f in k.outs()], list(range(len(k.outs()))))
        offs = [o for o, _ in sh.read_journal_dir(os.path.join(self.state, "hosts", SID))]
        self.assertNotIn(1, offs, "the failed record is a gap the readers skip")
        self.assertEqual(offs, [o for o in range(len(k.outs())) if o != 1], "the numbering around the gap holds")
        k.send({"t": "end", "grace": 10})
        ex = k.recv_until(lambda f: f.get("t") == "exit")
        self.assertEqual(ex["cause"], "end")
        k.close()

    def test_a_slow_journal_writer_raises_the_reader_behind_fault_and_the_reader_keeps_reading(self):
        # finding 5: the check used to be dead (the write ran on the reader's path); with the writer on its own
        # task, a throttled writer lets the reader run ahead and the fault fires once
        host, sock, spec = self._start(_test_journal_delay_s=0.4, reader_behind_records=1)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("one sleep=0.1")})
        k.send({"t": "in", "data": self._user("two sleep=0.1")})
        fault = k.recv_until(lambda f: f.get("t") == "fault" and f.get("kind") == "reader-behind", timeout=15)
        self.assertTrue(fault)
        first = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result" and f is not first, timeout=15)
        self.assertEqual(sum(1 for f in k.outs() if f["data"].get("type") == "result"), 2,
                         "both turns' results reached the kernel live while the writer lagged")
        self.assertEqual([r["kind"] for r in self._hostlog()].count("reader-behind"), 1)
        k.close()

    def test_a_second_end_with_a_shorter_grace_pulls_the_deadline_in(self):
        # finding 8
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("slow sleep=30")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "end", "grace": 60})
        k.send({"t": "end", "grace": 1})
        t0 = time.time()
        ex = k.recv_until(lambda f: f.get("t") == "exit", timeout=15)
        self.assertEqual(ex["cause"], "end-forced")
        self.assertLess(time.time() - t0, 10, "the shorter grace won")
        self.assertIn("end-grace-shortened", [r["kind"] for r in self._hostlog()])
        k.close()

    def test_the_heartbeat_keeps_the_lease_valid_past_a_short_ttl(self):
        # finding 10: every other check happens within a beat of the write; this one waits past a TTL shorter
        # than the wait, so only real beats keep the lease valid
        host, sock, spec = self._start()
        with mock.patch.object(sb, "LEASE_TTL_S", 4.0):
            time.sleep(5.0)
            lease = self._lease()
            self.assertEqual(sb.lease_state(lease, time.time()), "valid", "beats kept it fresh: t=%r now=%r" % (lease.get("t"), time.time()))
        self.assertGreater(lease["t"], spec_t if (spec_t := 0) else 0)

    def test_a_double_journal_fault_never_breaks_a_later_attach(self):
        # finding b: an unrecorded gap used to leave the index short of the offsets; the next replay across it
        # raised inside the host's client loop and the kernel read a bare EOF
        host, sock, spec = self._start(_test_journal_fault_at=1, _test_journal_gap_fault=True)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.2")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "journal-gap-unrecorded" for r in self._hostlog()):   # loop-ok
            time.sleep(0.05)
        k2, hello = self._attach(sock, ack=-1, pid=4343)
        k2.send({"t": "ping"}); k2.recv_until(lambda f: f.get("t") == "pong")
        offs = [f["offset"] for f in k2.outs()]
        self.assertEqual(offs, [o for o in range(hello["journal"]["next"]) if o != 1], "everything but the hole replayed, no fault, no EOF")
        self.assertNotIn("client-loop-failed", [r["kind"] for r in self._hostlog()])
        self.assertIsNone(host.poll())
        k2.close()

    def test_an_attach_while_the_writer_lags_sends_the_unwritten_records_from_memory(self):
        # finding c: records read but not yet journaled were neither replayed nor backlogged. The shape that lost
        # them to a replay built from two snapshots (the journal as of the attach, then memory): more than two
        # hundred records landed when the kernel attaches, so the replay reaches its drain, a kernel not reading
        # yet and send buffers small enough (the seam) that the drain WAITS, and records the writer lands during
        # that wait, gone from memory before a second pass could read them. Only a replay that resolves each
        # offset at its own moment sends every record exactly once (the commit-8 review's item 9).
        turns = 160                                                       # 1 init + 160 (assistant, result) pairs
        total = 1 + 2 * turns
        host, sock, spec = self._start(_test_journal_delay_s=0.01, _test_socket_small_buffers=True)
        k, _ = self._attach(sock)
        for i in range(turns):
            k.send({"t": "in", "data": self._user("turn%d sleep=0" % i)})
        k.recv_until(lambda f: sum(1 for g in k.outs() if g["data"].get("type") == "result") == turns, timeout=60)
        k.send({"t": "detach"}); k.close()
        seg = Path(self.state) / "hosts" / SID / "journal-0.jsonl"
        landed = lambda: sum(1 for _ in open(seg, "rb")) if seg.exists() else 0
        deadline = time.time() + 30
        while time.time() < deadline and landed() < 210:                  # loop-ok: the event is the writer's 210th record on disk
            time.sleep(0.002)
        self.assertLess(landed(), total, "records are still unwritten when the second kernel attaches (else the shape is not exercised)")
        k2, hello = self._attach(sock, ack=-1, pid=4343)
        self.assertEqual(hello["journal"]["next"], total, "the hello counted every record read, landed or not")
        deadline = time.time() + 30
        while time.time() < deadline and landed() < total:                # loop-ok: the writer landing the last record, while the replay waits on us
            time.sleep(0.01)
        self.assertEqual(landed(), total)
        k2.recv_until(lambda f: sum(1 for g in k2.outs() if g["data"].get("type") == "result") == turns, timeout=60)
        k2.send({"t": "ping"}); k2.recv_until(lambda f: f.get("t") == "pong")
        offs = [f["offset"] for f in k2.outs()]
        self.assertEqual(offs, list(range(total)), "every record once, in order, whether from disk, memory or the backlog; host log: %r"
                         % [(r["kind"], r.get("at"), r.get("error")) for r in self._hostlog()][-8:])
        k2.close()

    def test_an_end_right_after_a_line_lets_the_line_reach_the_cli_first(self):
        # finding d: `end` used to close stdin ahead of lines still queued on the pump
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        # one socket write carrying both frames: the host reads them together and queues the line, then the end
        # sentinel, on the stdin pump; no sleep, no timing (the review's item 10)
        k.s.sendall(sh.encode_frame({"t": "in", "data": self._user("last words sleep=0.1")}) + sh.encode_frame({"t": "end", "grace": 20}))
        ex = k.recv_until(lambda f: f.get("t") == "exit", timeout=15)
        self.assertEqual(ex["cause"], "end")
        self.assertIn("last words", open(self.fake_log).read(), "the queued line reached the CLI before its stdin closed")
        self.assertEqual(sum(1 for f in k.outs() if f["data"].get("type") == "result"), 1, "and its turn ran to its result")
        k.close()

    def test_an_open_request_whose_journal_write_failed_is_still_re_sent_on_attach(self):
        # finding e: the re-send came from the journal, which skips a gap; it comes from the parked table now
        host, sock, spec = self._start(_test_journal_fault_at=2)      # 0 init, 1 assistant, 2 the permission request
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("please ask=permission after=0.3 sleep=0.2")})
        req = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(req["offset"], 2)
        k.send({"t": "ack", "offset": 1}); k.send({"t": "ping"}); k.recv_until(lambda f: f.get("t") == "pong")
        k.close()
        k2, hello = self._attach(sock, ack=1, pid=4343)
        self.assertEqual(hello["parked"], [req["data"]["request_id"]])
        again = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(again["data"]["request_id"], req["data"]["request_id"], "re-sent from the table although its journal write failed")
        k2.close()

    @unittest.skipUnless(SDK_SITE, "the SDK venv is not on this machine; the pipe transport covered the host")
    def test_the_sdk_transport_drives_the_fake_cli_the_same_way(self):
        host, sock, spec = self._start(sdk=True)
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        spawned = [r for r in self._hostlog() if r["kind"] == "cli-spawned"]
        self.assertEqual(spawned[0]["transport"], "sdk", "the SDK's own SubprocessCLITransport spawned the CLI")
        k.send({"t": "end", "grace": 10})
        k.recv_until(lambda f: f.get("t") == "exit")
        k.close()


if __name__ == "__main__":
    unittest.main()
