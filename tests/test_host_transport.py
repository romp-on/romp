#!/usr/bin/env python3
"""The kernel's side of the per-session host (T315, stage 4 of #1317): the HostTransport over a fake host
socket and over an orphan journal, the settings, the spawn specification, the host scopes in the sweep,
the six-method Transport pin, the spec-tracks-the-SDK pin, and the backend wiring pins.

Hermetic: temp state roots, a fake host server inside the test (an asyncio Unix server speaking the
frame protocol), synthetic ids, no real CLI. Tests needing the SDK skip without it.
"""
import asyncio
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"          # no scopes: a test's children sit in the tester's own scope
# the SDK, when this machine has the venv bin/romp-sdk-setup builds (the kernel's own _ensure_sdk_on_path
# does the same at boot); CI has none and the SDK-gated tests skip there
if importlib.util.find_spec("claude_agent_sdk") is None:
    _tag = "python%d.%d" % sys.version_info[:2]
    for _sp in sorted(Path(os.path.expanduser("~/.local/state/romp/sdkvenv/lib")).glob(_tag + "/site-packages")):
        sys.path.insert(0, str(_sp))
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))
ht = sb._ht()
sh = ht.sh
SDK = importlib.util.find_spec("claude_agent_sdk") is not None
SID = "11111111-2222-3333-4444-0000000000b1"


class Settings(unittest.TestCase):
    def test_hosts_default_on_and_the_file_is_the_toggle(self):
        """T348 (the user 2026-09-11): hosts are on for everyone on this version; the file turns them off per machine."""
        d = tempfile.mkdtemp()
        self.assertTrue(ht.session_hosts_on(d), "a machine with no file is on")
        for word in ("off", "0", "false", "no", " Off\n", "OFF"):
            Path(d, "session-hosts").write_text(word)
            self.assertFalse(ht.session_hosts_on(d), "the toggle: a file saying %r is off" % word)
        for word in ("on", "1", "true", "yes", "On\n"):
            Path(d, "session-hosts").write_text(word)
            self.assertTrue(ht.session_hosts_on(d), "a file saying %r stays on" % word)
        for blank in ("", "  \n\t"):
            Path(d, "session-hosts").write_text(blank)
            self.assertTrue(ht.session_hosts_on(d), "an empty file, or one holding only whitespace, is the default: on")
        Path(d, "session-hosts").write_text("maybe")
        self.assertFalse(ht.session_hosts_on(d), "a word that is not one of the on words is off, as before")
        self.assertEqual(ht.session_hosts_read(d), (False, "maybe"), "one read hands the branch its verdict and the log the value")
        self.assertEqual(ht.session_hosts_read(tempfile.mkdtemp()), (True, ""), "…and (on, nothing) for a machine with no file")
        src = inspect.getsource(ht.session_hosts_read)
        self.assertIn("(True if not value else value.lower() in SESSION_HOSTS_ON_WORDS), value", src, "the default is on: no file, or an empty one")
        self.assertEqual(ht.SESSION_HOSTS_ON_WORDS, ("on", "1", "true", "yes"))

    def test_the_default_is_stated_where_the_reader_and_the_docs_speak_of_it(self):
        """Every place that states the default says on (T348): the reader's comment, the backend's log line for the
        off branch, and the reference's paragraph on session hosts."""
        # the pins compare whitespace-FLATTENED text (comment continuations joined, line breaks folded), so a re-wrap
        # or a re-aligned comment column leaves them standing; only the statements themselves are held
        flat = lambda s: " ".join(re.sub(r"\n\s*#", "", s).split())
        src = flat(open(os.path.join(ROOT, "kernel", "host_transport.py")).read())
        self.assertIn("leaves them on (on by default since T348", src, "the setting's comment names the default and its origin")
        bsrc = open(os.path.join(ROOT, "kernel", "sdk_backend.py")).read()
        self.assertIn("the session-hosts file reads %r, not an on word; running the CLI as a kernel child", bsrc,
                      "the off branch names what the file holds: any content that is not an on word, not only off")
        self.assertIn("hosts_on, hosts_value = _ht().session_hosts_read(self.state_dir)", bsrc,
                      "ONE read for the branch and its log: a flip between two reads cannot log a value the branch did not decide on")
        self.assertIn('if state == "none" and not hosts_on:', bsrc)
        self.assertIn("% (sess.name, hosts_value))", bsrc, "the log names the value the branch read")
        self.assertNotIn("session_hosts_value(", bsrc, "no second read of the file on that road")
        self.assertNotIn("session-hosts is off;", bsrc, "the old line, which read as the default, is gone")
        doc = open(os.path.join(ROOT, "docs", "reference.md")).read()
        i = doc.index("A session can outlive the kernel that started it.")
        para = flat(doc[i:i + 1200])
        self.assertIn("By default, on every machine on this version, a new session's CLI runs under a small per-session host process", para)
        self.assertIn("Write `off` to it to run a machine's sessions as plain kernel children again", para)
        self.assertIn("`on`, `1`, `true` and `yes` read as on; an empty file, or one holding only whitespace, is the default, on; any other content reads as off", para,
                      "the accepted words, the empty file and the stray word, all three stated")
        self.assertIn("becomes hosted at its next respawn, whatever prompts it", para, "the rollout: a respawn of any kind")
        self.assertNotIn("off by default", para, "the reference no longer says off by default")
        self.assertNotIn("the devbox opts in first", para, "the rollout wording went with the opt-in")

    def test_the_runners_floored_state_root_reads_hosts_off(self):
        """The belt (T348): tests/conftest.py writes `off` into the state root it floors for the run and re-asserts it per
        test, so no test spawns a real host by omission under the new default. Skipped outside that runner."""
        root = os.path.join(os.environ.get("XDG_STATE_HOME", ""), "romp")
        marker = os.path.join(root, "session-hosts")
        if not os.path.exists(marker):
            self.skipTest("the runner's floor (tests/conftest.py) is not in play")
        self.assertEqual(Path(marker).read_text().strip(), "off")
        self.assertEqual(ht.session_hosts_read(root), (False, "off"), "a fresh floored root reads hosts off")
        Path(marker).unlink()                                     # a test that removes it gets it back before the next test
        self.assertTrue(ht.session_hosts_on(root), "…and a bare root is on, which is exactly what the belt prevents")

    def test_the_grace_default_and_its_file(self):
        d = tempfile.mkdtemp()
        self.assertEqual(ht.session_host_grace_s(d), sh.UNATTACHED_GRACE_DEFAULT_S)
        Path(d, "session-host-grace").write_text("120")
        self.assertEqual(ht.session_host_grace_s(d), 120.0)
        Path(d, "session-host-grace").write_text("junk")
        self.assertEqual(ht.session_host_grace_s(d), sh.UNATTACHED_GRACE_DEFAULT_S, "junk falls back, loudly enough by being the default")


class SpawnSpec(unittest.TestCase):
    def test_the_spec_carries_the_plain_fields_and_the_permission_tool_and_never_callables(self):
        opts = types.SimpleNamespace(cli_path="/x/romp-cli-scope", cwd=Path("/tmp/proj"), env={"ROMP_SID": SID, "ROMP_CLI_REAL": "/x/claude"},
                                     permission_mode="default", resume=SID, extra_args={"resume-session-at": "u1"},
                                     mcp_servers="/x/postal.json", system_prompt={"type": "preset", "preset": "claude_code", "append": "hi"},
                                     model="m", effort="high", include_partial_messages=False, enable_file_checkpointing=True,
                                     max_buffer_size=100, can_use_tool=lambda *a: None, hooks={"Stop": []}, stderr=lambda l: None,
                                     permission_prompt_tool_name=None, session_id=None)
        spec = ht.spawn_spec(opts, SID, "web", "/state", "abc12345", 900)
        self.assertEqual(spec["permission_prompt_tool_name"], "stdio", "the callback's presence becomes the flag")
        self.assertEqual((spec["cwd"], spec["resume"], spec["extra_args"], spec["sid"], spec["version"]),
                         ("/tmp/proj", SID, {"resume-session-at": "u1"}, SID, "abc12345"))
        self.assertNotIn("hooks", spec); self.assertNotIn("stderr", spec); self.assertNotIn("can_use_tool", spec)
        self.assertEqual(spec["hook_timeout_s"], sh.HOOK_TIMEOUT_S)
        d = tempfile.mkdtemp()
        p = ht.write_spawn_spec(d, SID, spec)
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(p.parent).st_mode & 0o777), "0o700")
        self.assertEqual(json.loads(p.read_text())["env"]["ROMP_SID"], SID)

    @unittest.skipUnless(SDK, "the SDK is not importable here")
    def test_the_spec_fields_track_what_the_sdk_transport_reads(self):
        import claude_agent_sdk._internal.transport.subprocess_cli as scli
        import inspect
        src = inspect.getsource(scli.SubprocessCLITransport._build_command) + inspect.getsource(scli.SubprocessCLITransport.connect)
        read = set(re.findall(r"self\._options\.([a-z_]+)", src)) | {"cwd"}
        callables_or_sdk_side = {"stderr", "user", "session_store", "sandbox", "task_budget", "max_budget_usd", "betas",
                                 "plugins", "agents", "output_format", "tools",
                                 "include_hook_events", "strict_mcp_config", "resume_drops_turn", "permission_prompt_tool_name"}
        self.assertIn("thinking", sh.SPEC_FIELDS, "the thinking-summaries toggle reaches a hosted CLI")
        missing = read - set(sh.SPEC_FIELDS) - callables_or_sdk_side
        self.assertEqual(missing, set(), "fields the SDK reads that the spec does not carry: %r" % sorted(missing))


class HostScopes(unittest.TestCase):
    def test_host_scope_units_match_our_sessions_only(self):
        u = ht.host_scope_unit(SID, 1757374800000)
        self.assertEqual(u, "romp-host-11111111-1757374800000")
        listing = ["%s.scope loaded active running x" % u, "romp-host-99999999-1.scope loaded active running y",
                   "romp-session-11111111-4242-1.scope loaded active running z"]
        self.assertEqual(ht.host_scope_units(listing, [SID]), {u + ".scope": "11111111"})

    def test_the_sweep_stops_a_host_scope_whose_lease_does_not_hold_and_spares_a_live_one(self):
        d = tempfile.mkdtemp(); be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        live, dead = SID, "22222222-2222-3333-4444-0000000000b2"
        sb.write_lease(d, {"sid": live, "fsid": live, "pid": 999999999, "start": "1", "holder": {"pid": 999999998, "start": "2", "kind": "host"}, "version": "", "t": time.time()})
        sb.write_lease(d, {"sid": dead, "fsid": dead, "pid": 999999997, "start": "1", "holder": {"pid": 999999996, "start": "2", "kind": "host"}, "version": "", "t": time.time()})
        starts = {999999999: "1", 999999998: "2"}      # the live pair is alive; the dead host's pids are gone
        listing = "romp-host-11111111-1.scope loaded active running a\nromp-host-22222222-2.scope loaded active running b\n"
        runs = []
        def run(argv, **kw):
            runs.append(list(argv)); return mock.Mock(stdout=listing if argv == sb.HOST_SCOPE_LIST_ARGV else "", returncode=0)
        with mock.patch.object(sb, "proc_start", lambda p, run=None: starts.get(p)):
            n = be._stop_leftover_scopes([live, dead], run=run)
        stops = [a[-1] for a in runs if a[:3] == ["systemctl", "--user", "stop"]]
        self.assertEqual((n, stops), (1, ["romp-host-22222222-2.scope"]))


class LeaseClassification(unittest.TestCase):
    def test_attach_orphan_none(self):
        now = 1000.0
        st = lambda starts: (lambda p: starts.get(p))
        host_lease = {"sid": SID, "pid": 5, "start": "a", "holder": {"pid": 6, "start": "b", "kind": "host"}, "t": now}
        self.assertEqual(ht.host_lease_state(host_lease, now, st({5: "a", 6: "b"})), "attach")
        self.assertEqual(ht.host_lease_state(host_lease, now, st({5: "a"})), "orphan", "the host is gone")
        self.assertEqual(ht.host_lease_state(dict(host_lease, t=now - 100), now, st({5: "a", 6: "b"})), "orphan", "a stale beat")
        kernel_lease = dict(host_lease, holder={"pid": 6, "start": "b"})
        self.assertEqual(ht.host_lease_state(kernel_lease, now, st({5: "a", 6: "b"})), "none")
        self.assertEqual(ht.host_lease_state(None, now), "none")


# ── the transport against a fake host ──────────────────────────────────────────────────────────
class FakeHost:
    """An asyncio Unix server speaking the host's frames from a scripted journal."""

    def __init__(self, path, records, busy=False, exit_after=None, answer_init=None):
        self.path, self.records, self.busy, self.exit_after = path, records, busy, exit_after
        self.answer_init = answer_init      # None: every control request answered; else answer_init(attach_no) -> bool
        self.attaches = 0
        self.got = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, path=self.path)

    async def _client(self, reader, writer):
        fr = sh.FrameReader()
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                for f in fr.feed(chunk):
                    self.got.append(f)
                    if f["t"] == "attach":
                        if self.busy:
                            writer.write(sh.encode_frame({"t": "busy", "kernel": {"pid": 7}})); await writer.drain(); continue
                        self.attaches += 1
                        answering = self.answer_init is None or self.answer_init(self.attaches)
                        writer.write(sh.encode_frame({"t": "hello", "protocol": 1, "host": {"pid": 10, "start": "h", "version": "v"},
                                                      "cli": {"pid": 11, "start": "c", "fsid": SID}, "journal": {"next": len(self.records)}, "parked": []}))
                        for off, rec in enumerate(self.records):
                            if off > int(f.get("ack", -1)):
                                writer.write(sh.encode_frame({"t": "out", "offset": off, "data": rec}))
                        writer.write(sh.encode_frame({"t": "stderr", "line": "a stderr line"}))
                        if self.exit_after is not None:
                            writer.write(sh.encode_frame({"t": "exit", "code": self.exit_after, "cause": "died"}))
                        await writer.drain()
                    elif f["t"] == "end":
                        writer.write(sh.encode_frame({"t": "exit", "code": 0, "cause": "end"})); await writer.drain()
                    elif f["t"] == "in":
                        obj = json.loads(f["data"])
                        if obj.get("type") == "control_request" and answering:
                            writer.write(sh.encode_frame({"t": "out", "offset": len(self.records), "data": {
                                "type": "control_response", "response": {"subtype": "success", "request_id": obj["request_id"], "response": {}}}}))
                            await writer.drain()
                    elif f["t"] == "ping":
                        writer.write(sh.encode_frame({"t": "pong"})); await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            return

    def close(self):
        if self.server:
            self.server.close()


def run(coro):
    return asyncio.run(coro)


class TransportOverSocket(unittest.TestCase):
    def _path(self):
        # under the system temp dir the tests package recorded (ROMP_TESTS_SYSTEM_TMPDIR), outside the run's private
        # root, so the AF_UNIX path fits sun_path under xdist nesting; outside the root is outside the exit sweep's
        # scope too, so the dir is removed here, when its test is (six per run leaked before this cleanup)
        d = tempfile.mkdtemp(dir=os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or None)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return os.path.join(d, "h.sock")

    def test_attach_replays_from_the_ack_then_acks_and_side_frames_reach_their_callbacks(self):
        recs = [{"type": "system", "subtype": "init", "session_id": SID}, {"type": "assistant", "n": 1}, {"type": "result", "n": 2}]
        async def go():
            fh = FakeHost(self._path(), recs); await fh.start()
            acks, stderr, hellos = [], [], []
            t = ht.HostTransport(fh.path, kernel={"pid": 1, "start": "k", "version": "v"}, ack=0, on_ack=acks.append,
                                 on_stderr=stderr.append, on_hello=hellos.append)
            await t.connect()
            self.assertTrue(t.is_ready()); self.assertEqual(hellos[0]["cli"]["pid"], 11)
            got = []
            async def read():
                async for m in t.read_messages():
                    got.append(m)
                    if len(got) == 3:
                        break
            # the Query's shape: the reader is started, then the initialize is written; the records are held until
            # its answer has been handed over, then follow in order
            reader = asyncio.ensure_future(read())
            await asyncio.sleep(0.05)
            await t.write(json.dumps({"type": "control_request", "request_id": "req_1_x", "request": {"subtype": "initialize"}}))
            await asyncio.wait_for(reader, 5)
            self.assertEqual(got[0]["type"], "control_response", "the initialize's answer comes first, ahead of the replay")
            self.assertEqual([m.get("n") for m in got[1:]], [1, 2], "then the replay, from after the acknowledged offset 0")
            self.assertEqual(stderr, ["a stderr line"])
            self.assertEqual(acks[-1], 2, "the held records' offsets were acknowledged as consumed; the answer's (3) waits for the stream to move on")
            t.detach_mode = True
            await t.end_input()                       # detach mode: no `end` goes out
            await t.close()
            await asyncio.sleep(0.1)
            kinds = [f["t"] for f in fh.got]
            self.assertIn("detach", kinds); self.assertNotIn("end", kinds)
            self.assertEqual(fh.got[0]["ack"], 0)
            fh.close()
        run(go())

    def test_a_replay_of_three_hundred_records_still_answers_the_initialize_first(self):
        # the commit 2-3 review's second finding: the SDK's message stream buffers 100 records before its reader
        # blocks, and the initialize's answer used to sit behind the whole replay in the one ordered stream
        recs = [{"type": "assistant", "n": i} for i in range(300)]
        async def go():
            fh = FakeHost(self._path(), recs); await fh.start()
            acks = []
            t = ht.HostTransport(fh.path, kernel={"pid": 1}, on_ack=acks.append)
            await t.connect()
            got = []
            async def read():
                async for m in t.read_messages():
                    got.append(m)
                    if len(got) == 301:
                        break
            reader = asyncio.ensure_future(read())
            await asyncio.sleep(0.1)                     # the replay is already flowing (and held)
            await t.write(json.dumps({"type": "control_request", "request_id": "req_0_init", "request": {"subtype": "initialize"}}))
            await asyncio.wait_for(reader, 10)
            self.assertEqual(got[0]["type"], "control_response")
            self.assertEqual([m["n"] for m in got[1:]], list(range(300)), "every replayed record, in order, after the answer")
            # the answer's own offset (300) is acknowledged only when the stream moves on past the held records;
            # a reader that stopped here has acknowledged exactly what it consumed, nothing beyond
            self.assertEqual(t.ack_offset, 299)
            self.assertEqual((acks[0], acks[-1]), (0, 299), "the ack advanced with each held record, never jumping to the answer's offset first")
            self.assertEqual(acks, sorted(acks))
            fh.close()
        run(go())

    def test_a_connect_that_never_completed_detaches_on_close_and_never_ends_the_cli(self):
        async def go():
            fh = FakeHost(self._path(), [{"type": "assistant"}]); await fh.start()
            t = ht.HostTransport(fh.path, kernel={"pid": 1}, end_grace=7)
            await t.connect()                            # attached, but no initialize answered yet
            await t.close()
            await asyncio.sleep(0.1)
            kinds = [f["t"] for f in fh.got]
            self.assertIn("detach", kinds); self.assertNotIn("end", kinds, "a failed attach must not end the turn it failed to join")
            fh.close()
        run(go())

    def test_busy_refuses_and_a_non_zero_exit_raises_like_the_subprocess_transport(self):
        async def go():
            fh = FakeHost(self._path(), [], busy=True); await fh.start()
            t = ht.HostTransport(fh.path, kernel={"pid": 1})
            with self.assertRaises(ht.CLIConnectionError):
                await t.connect()
            fh.close()
            fh2 = FakeHost(self._path(), [{"type": "assistant"}], exit_after=3); await fh2.start()
            t2 = ht.HostTransport(fh2.path, kernel={"pid": 1})
            await t2.connect()
            got = []
            with self.assertRaises(ht.ProcessError):
                async for m in t2.read_messages():
                    got.append(m)
            self.assertEqual(len(got), 1); self.assertEqual(t2.exit_info["code"], 3)
            fh2.close()
        run(go())

    def test_end_input_and_close_send_end_with_the_grace_when_not_detaching(self):
        async def go():
            fh = FakeHost(self._path(), []); await fh.start()
            t = ht.HostTransport(fh.path, kernel={"pid": 1}, end_grace=7)
            await t.connect()
            await t.end_input()
            await asyncio.sleep(0.1)
            ends = [f for f in fh.got if f["t"] == "end"]
            self.assertEqual(ends[0]["grace"], 7)
            await t.signal("INT")
            await asyncio.sleep(0.1)
            self.assertIn({"t": "signal", "sig": "INT"}, fh.got)
            fh.close()
        run(go())


class TransportOverJournal(unittest.TestCase):
    def test_the_replay_answers_the_initialize_and_yields_the_journal_then_ends(self):
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        for rec in ({"type": "system", "subtype": "init"}, {"type": "assistant", "n": 1}, {"type": "result", "n": 2}):
            j.append(rec)
        j.close()
        async def go():
            acks = []
            t = ht.HostTransport.from_journal(d, ack=0, on_ack=acks.append)
            await t.connect()
            got = []
            async def read():
                async for m in t.read_messages():
                    got.append(m)
            reader = asyncio.ensure_future(read())
            await asyncio.sleep(0.05)
            await t.write(json.dumps({"type": "control_request", "request_id": "req_0_i", "request": {"subtype": "initialize"}}))
            await t.write(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}))   # dropped, counted
            await asyncio.wait_for(reader, 10)
            kinds = [m["type"] for m in got]
            self.assertEqual(kinds, ["assistant", "result", "control_response"])
            self.assertEqual(got[2]["response"]["request_id"], "req_0_i")
            self.assertEqual(acks, [1, 2]); self.assertEqual(t.dropped_writes, 1)
            self.assertEqual(t.exit_info["cause"], "replay-end")
            await t.close()
        run(go())


class BackendHostRules(unittest.TestCase):
    """The backend's host rules driven, not pinned by source: the drain's intent latch, the hello's open-turn
    adoption and slot release, a thread attached at boot, a hosted thread's notices, an ended host's lease race,
    the ack that a dead host must not get, and the kill switch after an orphan."""

    def _be(self, short=False):
        # short: the state dir under the system temp dir (tests/README.md's ROMP_TESTS_SYSTEM_TMPDIR), so a fake host's
        # AF_UNIX socket path under it stays inside sun_path's 104 bytes on every platform and xdist nesting. A short
        # dir is outside the run's private root and so outside the exit sweep's scope: removed here, when its test is
        d = tempfile.mkdtemp(dir=(os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or None) if short else None); logs = []
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=logs.append); be._test_logs = logs
        return d, be

    def _serve_fake_host(self, d, silent=False):
        """A FakeHost (or a server that accepts and never writes hello, `silent`) on the backend's host socket, on a
        loop thread of its own; returns (host, stop)."""
        import threading
        sock = str(ht.host_sock(d, SID))
        os.makedirs(os.path.dirname(sock), exist_ok=True)   # the host's directory, which a real spawn creates
        host = FakeHost(sock, [])
        loop = asyncio.new_event_loop()
        started = threading.Event()
        async def mute(reader, writer):
            await asyncio.Event().wait()                     # accepts, never writes hello
        async def start():
            if silent:
                host.server = await asyncio.start_unix_server(mute, path=sock)
            else:
                await host.start()
        def serve():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(start()); started.set()
            loop.run_forever()
        threading.Thread(target=serve, daemon=True).start()
        self.assertTrue(started.wait(5), "the fake host serves its socket")
        def stop():
            loop.call_soon_threadsafe(host.close); loop.call_soon_threadsafe(loop.stop)
        return host, stop

    def test_the_drain_latches_a_session_mid_attach_by_intent(self):
        d, be = self._be()
        s = types.SimpleNamespace(sid=SID, name="web", inflight=1, ended=False, thread=None, _host=None, _host_intent=True,
                                  detached=False, shutdown=lambda: None)
        be.sessions[SID] = s
        res = be.drain(timeout=1)
        self.assertEqual(res["cutTurns"], [], "a session mid-attach is detached, never cut")
        self.assertTrue(s.detached)

    def test_hello_adopts_the_hosts_open_turns_and_releases_the_slot_only_for_an_attach(self):
        d, be = self._be()
        fired = []
        t = types.SimpleNamespace(hello={"host": {"pid": 1, "start": "a"}, "cli": {"pid": 2, "start": "b"}}, ack_offset=5)
        s = types.SimpleNamespace(sid=SID, name="web", inflight=0, _host=t, _host_is_attach=True, _fire_boot_settled=lambda: fired.append(1))
        be._on_host_hello(s, {"host": {"pid": 1, "start": "a"}, "cli": {"pid": 2, "start": "b"}, "journal": {"next": 6}, "parked": [], "inflight": 1})
        self.assertEqual((s.inflight, fired), (1, [1]), "mid-turn adopted; the boot slot released for an attach")
        s2 = types.SimpleNamespace(sid=SID, name="web", inflight=1, _host=t, _host_is_attach=False, _fire_boot_settled=lambda: fired.append(2))
        be._on_host_hello(s2, {"host": {"pid": 1, "start": "a"}, "cli": {}, "journal": {"next": 0}, "parked": [], "inflight": 0})
        self.assertEqual((s2.inflight, fired), (1, [1]), "a lower count never lowers ours; a spawn's hello leaves the slot to the init record")

    def test_the_hello_decides_the_fresh_cli_by_identity_and_tolerates_older_shapes(self):
        """The fresh-CLI decision at the hello (the connect loop's pins drive it through the loop; this one drives the handler):
        the hello's cli.pid:cli.start against the reg's spawnedAtCli. Equal: nothing. Different with a spawn time: the block
        with the host's value and the launch login from cli.login (the options' login when the hello lacks it). Different
        without a spawn time (older host code): the identity recorded, nothing else. No identity at all: nothing stamped,
        a log line, never a raise."""
        d, be = self._be()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "cwd": d, "spawnedAt": 1700000000, "spawnedAtCli": "2:b"})
        s = sb.SdkSession(be, sb.read_reg(Path(d), SID)); s._launched_login = "kept"
        base = {"host": {"pid": 1, "start": "a"}, "journal": {"next": 0}, "parked": [], "inflight": 0}
        def reg():
            r = sb.read_reg(Path(d), SID)
            return (r.get("spawnedAt"), r.get("spawnedAtCli"), s._launched_login)
        for c in (None, {}, {"fsid": SID}, {"pid": 5}, {"start": "x"}, "not a dict"):
            h = dict(base)
            if c is not None:
                h["cli"] = c
            be._on_host_hello(s, h)
        self.assertEqual(reg(), (1700000000, "2:b", "kept"), "no identity: nothing moves")
        self.assertEqual(sum("names no CLI identity" in m for m in be._test_logs), 6)
        be._on_host_hello(s, dict(base, cli={"pid": 2, "start": "b", "spawnedAt": 1700009999, "login": "other"}))
        self.assertEqual(reg(), (1700000000, "2:b", "kept"), "the CLI the reg names: nothing, whatever the hello says")
        be._on_host_hello(s, dict(base, cli={"pid": 3, "start": "c"}))
        self.assertEqual(reg(), (1700000000, "3:c", "kept"), "older host code: the identity recorded, the epoch and login stand")
        self.assertTrue(any("recorded, nothing stamped" in m for m in be._test_logs))
        be._on_host_hello(s, dict(base, cli={"pid": 4, "start": "d", "spawnedAt": 1700005000, "login": "launch-login"}))
        self.assertEqual(reg(), (1700005000, "4:d", "launch-login"), "a fresh CLI: the host's spawn time and the launch's login")
        self.assertEqual(sb.read_reg(Path(d), SID).get("launchedLogin"), "launch-login")
        s._options_login = "opts-login"
        be._on_host_hello(s, dict(base, cli={"pid": 5, "start": "e", "spawnedAt": 1700006000}))
        self.assertEqual(reg(), (1700006000, "5:e", "opts-login"), "a hello without cli.login: the options' login")
        be._on_host_hello(s, dict(base, cli={"pid": 6, "start": "f", "spawnedAt": True, "login": "x"}))
        self.assertEqual(reg(), (1700006000, "6:f", "opts-login"), "a bool spawn time reads as absent")
        be._on_host_hello(s, dict(base, cli={"pid": 7, "start": "g", "spawnedAt": "1700007000", "login": "x"}))
        self.assertEqual(reg(), (1700006000, "7:g", "opts-login"), "a string spawn time reads as absent")
        # the empty-login reading (the follow-up's read, low 3): "" IS an identifier, the machine's own login, stamped as the
        # host echoes what the launch billed; only an ABSENT field falls to the options' login
        be._on_host_hello(s, dict(base, cli={"pid": 8, "start": "h", "spawnedAt": 1700008000, "login": ""}))
        self.assertEqual(reg(), (1700008000, "8:h", ""), "an empty cli.login stamps the machine's own login, not the options'")
        self.assertEqual(sb.read_reg(Path(d), SID).get("launchedLogin"), "")

    def test_a_hosted_comment_thread_attaches_at_boot_and_gets_no_dead_life_notices(self):
        d, be = self._be()
        tsid = "33333333-2222-3333-4444-0000000000b3"
        reg = {"sid": tsid, "name": "t1", "cwd": d, "mode": "default", "effort": "high", "lastSid": tsid, "alive": True,
               "threadOf": SID, "bgTasks": [{"id": "x", "desc": "a task"}], "pendingAsk": True, "spawnedAt": 1}
        sb.write_reg(Path(d), tsid, reg)
        sb.write_lease(d, {"sid": tsid, "fsid": tsid, "pid": 999999999, "start": "1", "holder": {"pid": 999999998, "start": "2", "kind": "host"}, "version": "", "t": time.time()})
        starts = {999999999: "1", 999999998: "2"}
        started = []
        with mock.patch.object(sb, "proc_start", lambda p, run=None: starts.get(p)), \
             mock.patch.object(sb.SdkSession, "start", lambda self: started.append(self.sid)):
            be._boot_reconcile([dict(reg)])
            self.assertIn(tsid, be._boot_attach_sids, "a thread with a live host attaches at boot (the attach check runs before the thread skip)")
            be._ensure(tsid)
        queue = (sb.read_reg(Path(d), tsid) or {}).get("queue") or []
        self.assertEqual(queue, [], "no killed-question or dead-task notice for a thread whose host kept it alive")

    def test_a_host_this_kernel_ended_is_ended_not_died_and_its_stale_ack_is_never_written(self):
        d, be = self._be()
        t = types.SimpleNamespace(hello={"host": {"pid": 7, "start": "h"}, "cli": {"pid": 8, "start": "c"}}, ack_offset=3, exit_info=None)
        s = types.SimpleNamespace(sid=SID, name="web", _host=t, _host_ack_t=0.0)
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True})
        be._host_ended(s, {"t": "exit", "code": 0, "cause": "end"})
        self.assertEqual(be._host_recently_ended[SID], "7:h")
        lease = {"sid": SID, "holder": {"pid": 7, "start": "h", "kind": "host"}}
        self.assertEqual(be._holder_ident(lease), "7:h", "the ended host is recognized by identity, so its lease race is a wait, not a host.died")
        t.exit_info = {"t": "exit", "code": 0}
        be._write_host_ack(s, force=True)
        self.assertNotIn("hostAck", sb.read_reg(Path(d), SID) or {}, "no ack written for a host that reported its exit")

    def test_with_the_setting_off_an_orphan_lease_is_recovered_and_no_host_is_spawned(self):
        d, be = self._be()
        Path(d, "session-hosts").write_text("off")
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID})
        sb.write_lease(d, {"sid": SID, "fsid": SID, "pid": 999999997, "start": "1", "holder": {"pid": 999999996, "start": "2", "kind": "host"}, "version": "", "t": time.time()})
        s = types.SimpleNamespace(sid=SID, name="web", _host_intent=True, _host=None, _host_is_attach=False,
                                _seed_for_dead_cli=lambda cli: None)   # the replay's watermark seed (T354): a no-op on this stand-in
        with mock.patch.object(sb, "proc_start", lambda p, run=None: None):
            out = asyncio.run(be._host_transport_for(s, types.SimpleNamespace(), (None, None, None)))
        self.assertIsNone(out, "the kill switch holds after the orphan road: a plain SDK subprocess, no new host")
        self.assertFalse(s._host_intent)
        self.assertIsNone(sb.read_lease(d, SID), "the dead host's lease is cleared")
        kinds = [json.loads(l)["kind"] for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines()]
        self.assertIn("host.died", kinds)

    # ── the fifth review fold (commit 10) ──
    def _sdk_stub(self):
        """claude_agent_sdk with a ClaudeSDKClient that is an async context manager and nothing else: the orphan
        road imports it for the replay client; the replay itself is observed through from_journal and _replay_drain."""
        mod = types.ModuleType("claude_agent_sdk")
        class ClaudeSDKClient:
            def __init__(self, options=None, transport=None): self.transport = transport
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        mod.ClaudeSDKClient = ClaudeSDKClient
        return mod

    def _leftover(self, d, ident="5:h", records=3):
        hd = ht.host_dir(d, SID); hd.mkdir(parents=True, exist_ok=True)
        pid, start = ident.split(":")
        (hd / "identity.json").write_text(json.dumps({"pid": int(pid), "start": start}))
        with open(hd / "journal-0.jsonl", "w") as f:
            for i in range(records):
                f.write(json.dumps({"type": "assistant" if i % 2 == 0 else "result", "n": i}) + "\n")
        return hd

    def _kinds(self, d):
        p = Path(d) / sb.SESSION_EVENTS_FILE
        return [json.loads(l)["kind"] for l in p.read_text().splitlines()] if p.exists() else []

    def test_with_the_setting_off_a_lease_less_leftover_is_replayed_and_cleared(self):
        # item 1 (medium): the connect guard keyed on the lease alone, so a host that ended unattended (its lease
        # removed) left its unconsumed tail and its directory behind for good with the setting off
        d, be = self._be()
        Path(d, "session-hosts").write_text("off")
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID,
                                     "hostAck": {"host": "5:h", "cli": "6:c", "offset": 0}, "hostLogPos": 3})
        hd = self._leftover(d, "5:h", records=3)
        s = types.SimpleNamespace(sid=SID, name="web", _host_intent=True, _host=None, _host_is_attach=False,
                                _seed_for_dead_cli=lambda cli: None)   # the replay's watermark seed (T354): a no-op on this stand-in
        self.assertTrue(be._host_lease_applies(s), "a leftover directory is a host that held this session: the road runs whatever the setting")
        acks, drained = [], []
        async def drain(sess, client, msg_classes): drained.append(client.transport)
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": self._sdk_stub()}), \
             mock.patch.object(ht.HostTransport, "from_journal", classmethod(lambda cls, hdir, ack=-1, **kw: acks.append(ack) or types.SimpleNamespace(hdir=hdir))), \
             mock.patch.object(be, "_replay_drain", drain):
            out = asyncio.run(be._host_transport_for(s, types.SimpleNamespace(), (None, None, None)))
        self.assertIsNone(out, "the kill switch still holds: a plain SDK subprocess after the replay")
        self.assertEqual(acks, [0], "the tail past the ack this host's identity vouches for was replayed")
        self.assertEqual(len(drained), 1)
        self.assertIn("host.tail-replayed", self._kinds(d))
        self.assertFalse(hd.exists(), "the directory is cleared after the replay")
        reg = sb.read_reg(Path(d), SID) or {}
        self.assertNotIn("hostAck", reg); self.assertNotIn("hostLogPos", reg)
        s2 = types.SimpleNamespace(sid=SID, name="web")
        self.assertFalse(be._host_lease_applies(s2), "and nothing is left to apply")

    def test_a_leftover_with_nothing_past_the_ack_files_no_tail_replayed_row(self):
        # item 4: a failed spawn's leftovers (an empty journal, an identity, no lease) are cleared quietly
        d, be = self._be()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID,
                                     "hostAck": {"host": "5:h", "cli": "6:c", "offset": 2}})
        hd = self._leftover(d, "5:h", records=3)          # offsets 0..2, all acknowledged
        s = types.SimpleNamespace(sid=SID, name="web", _host_intent=True, _host=None, _host_is_attach=False,
                                _seed_for_dead_cli=lambda cli: None)   # the replay's watermark seed (T354): a no-op on this stand-in
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": self._sdk_stub()}), \
             mock.patch.object(be, "_replay_drain", mock.AsyncMock()):
            asyncio.run(be._host_orphan_recover(s, types.SimpleNamespace(), None, (None, None, None), died=False))
        self.assertNotIn("host.tail-replayed", self._kinds(d), "nothing to replay, no row")
        self.assertFalse(hd.exists())
        (hd2 := self._leftover(d, "5:h", records=0))
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": self._sdk_stub()}), \
             mock.patch.object(be, "_replay_drain", mock.AsyncMock()):
            asyncio.run(be._host_orphan_recover(s, types.SimpleNamespace(), None, (None, None, None), died=False))
        self.assertNotIn("host.tail-replayed", self._kinds(d), "an empty journal: no row either")
        self.assertFalse(hd2.exists())

    def test_the_orphan_road_trusts_hostack_only_for_the_host_that_wrote_the_identity(self):
        # item 7: host A's acknowledged offset must not seed host B's replay
        d, be = self._be()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID,
                                     "hostAck": {"host": "1:a", "cli": "2:c", "offset": 1}})
        self._leftover(d, "9:b", records=3)
        seeds = []
        s = types.SimpleNamespace(sid=SID, name="web", _host_intent=True, _host=None, _host_is_attach=False,
                                _seed_for_dead_cli=seeds.append)   # the replay's watermark seed (T354): recorded here
        acks = []
        capture = classmethod(lambda cls, hdir, ack=-1, **kw: acks.append(ack) or types.SimpleNamespace(hdir=hdir))
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": self._sdk_stub()}), \
             mock.patch.object(ht.HostTransport, "from_journal", capture), mock.patch.object(be, "_replay_drain", mock.AsyncMock()):
            asyncio.run(be._host_orphan_recover(s, types.SimpleNamespace(), None, (None, None, None), died=False))
        self.assertEqual(acks, [-1], "another host's ack: the whole journal is replayed")
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID,
                                     "hostAck": {"host": "9:b", "cli": "2:c", "offset": 1}})
        self._leftover(d, "9:b", records=3)
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": self._sdk_stub()}), \
             mock.patch.object(ht.HostTransport, "from_journal", capture), mock.patch.object(be, "_replay_drain", mock.AsyncMock()):
            asyncio.run(be._host_orphan_recover(s, types.SimpleNamespace(), None, (None, None, None), died=False))
        self.assertEqual(acks, [-1, 1], "this host's ack: the replay starts past it")
        self.assertEqual(seeds, ["", "2:c"], "the watermark seed runs before each replay (T354 M7), the CLI named by hostAck only when the ack is this host's")

    def test_a_host_this_kernel_ended_gets_a_bounded_wait_for_its_lease_and_no_host_died_row(self):
        # item 6: the behaviour, not the bookkeeping: an `end` this kernel asked for races the reconnect; the stale
        # lease is waited out (bounded), never walked as a death
        d, be = self._be()
        Path(d, "session-hosts").write_text("off")
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID})
        sb.write_lease(d, {"sid": SID, "fsid": SID, "pid": 999999997, "start": "1", "holder": {"pid": 999999996, "start": "2", "kind": "host"}, "version": "", "t": time.time()})
        be._host_recently_ended[SID] = "999999996:2"
        s = types.SimpleNamespace(sid=SID, name="web", _host_intent=True, _host=None, _host_is_attach=False,
                                _seed_for_dead_cli=lambda cli: None)   # the replay's watermark seed (T354): a no-op on this stand-in
        import threading
        remover = threading.Timer(0.4, lambda: sb.remove_lease(d, SID)); remover.start()
        try:
            t0 = time.time()
            with mock.patch.object(sb, "proc_start", lambda p, run=None: None), \
                 mock.patch.object(be, "_host_orphan_recover", mock.AsyncMock()) as rec:
                out = asyncio.run(be._host_transport_for(s, types.SimpleNamespace(), (None, None, None)))
        finally:
            remover.cancel()
        self.assertIsNone(out)
        self.assertGreaterEqual(time.time() - t0, 0.35, "the connect waited for the lease's removal")
        self.assertIsNone(sb.read_lease(d, SID), "the host removed its lease; nothing of ours reaped it")
        self.assertFalse(rec.called, "no orphan road: the host ended, it did not die")
        self.assertNotIn("host.died", self._kinds(d))
        self.assertNotIn(SID, be._host_recently_ended)

    def test_an_attach_that_never_completes_stands_the_session_down_instead_of_the_crash_heal(self):
        # item 3: past the retry bound on a wedged live host the failure used to run crash.heal then crash.loop and
        # queue the crash-resume nudge for a CLI that never died
        d, be = self._be()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID, "queue": ["kept"]})
        s = types.SimpleNamespace(sid=SID, name="web", backend=be, inflight=1, detached=False, _reconnect=True,
                                  _host_attach_retries=4, _host=types.SimpleNamespace(hello={"host": {"pid": 5, "start": "h"}}))
        sb.SdkSession._host_stand_down(s, TimeoutError("initialize"))
        self.assertTrue(s.detached, "detached: the session-gone path settles nothing and heals nothing")
        self.assertFalse(s._reconnect); self.assertEqual((s.inflight, s._host_attach_retries), (0, 0))
        kinds = self._kinds(d)
        self.assertIn("host.attach-failed", kinds); self.assertNotIn("crash.heal", kinds)
        rows = [json.loads(l) for l in (Path(d) / "states" / (SID + ".jsonl")).read_text().splitlines()]
        self.assertEqual(rows[-1]["state"], "waiting", "waiting on its host; the next send tries the attach again")
        self.assertEqual((sb.read_reg(Path(d), SID) or {}).get("queue"), ["kept"], "no crash-resume nudge")

    def _marked(self, d, holder="999999996:2", **reg):
        base = {"sid": SID, "name": "web", "alive": True, "lastSid": SID, "hostAttachFailed": {"host": holder, "t": 1.0, "tries": 4}}
        base.update(reg)
        sb.write_reg(Path(d), SID, base)

    def _host_lease(self, d, cli=999999997, host=999999996, t=None):
        sb.write_lease(d, {"sid": SID, "fsid": SID, "pid": cli, "start": "1", "holder": {"pid": host, "start": "2", "kind": "host"},
                           "version": "", "t": time.time() if t is None else t})

    _LEASE_CASES = [
        # name, lease (None = absent), live pids, holds
        ("attach by the marked host", dict(cli=999999997, host=999999996), {999999997: "1", 999999996: "2"}, True),
        ("attach by another host", dict(cli=999999997, host=999999995), {999999997: "1", 999999995: "2"}, False),
        ("the host is gone (holder-gone)", dict(cli=999999997, host=999999996), {999999997: "1"}, False),
        ("the CLI is gone (no-live-process)", dict(cli=999999997, host=999999996), {999999996: "2"}, False),
        ("a stale beat", dict(cli=999999997, host=999999996, t=time.time() - 100), {999999997: "1", 999999996: "2"}, False),
        ("no lease at all", None, {999999997: "1", 999999996: "2"}, False),
    ]

    def test_the_stand_down_marker_holds_only_for_a_live_lease_by_the_very_host(self):
        # the commit-12 review's first item: the guard compared identities with no liveness check, so a killed host's
        # stale lease kept every automatic ensure standing down and the orphan road never ran. _ensure is driven on a
        # FRESH marker per state (the commit-13 review's fifth item: a helper call first had already dropped it)
        for name, lease, starts, holds in self._LEASE_CASES:
            with self.subTest("ensure: " + name):
                d, be = self._be()
                self._marked(d)
                if lease:
                    self._host_lease(d, **lease)
                started = []
                with mock.patch.object(sb, "proc_start", lambda p, run=None, st=starts: st.get(p)), \
                     mock.patch.object(sb.SdkSession, "start", lambda self: started.append(self.sid)):
                    out = be._ensure(SID)
                reg = sb.read_reg(Path(d), SID) or {}
                if holds:
                    self.assertIsNone(out, "stands down"); self.assertIn("hostAttachFailed", reg, "the marker stays")
                    self.assertEqual(started, [])
                else:
                    self.assertIsNotNone(out, "new information: the session starts (the orphan road or a fresh spawn runs in its thread)")
                    self.assertNotIn("hostAttachFailed", reg, "the marker is dropped on the way in")
                    self.assertEqual(started, [SID])
        for name, lease, starts, holds in self._LEASE_CASES:
            with self.subTest("helper: " + name):
                d, be = self._be()
                self._marked(d)
                if lease:
                    self._host_lease(d, **lease)
                with mock.patch.object(sb, "proc_start", lambda p, run=None, st=starts: st.get(p)):
                    self.assertEqual(be._attach_stand_down_holds(SID), holds)
                self.assertEqual("hostAttachFailed" in (sb.read_reg(Path(d), SID) or {}), holds, "the helper drops a marker that no longer holds")

    def test_a_stand_down_with_no_live_attach_lease_writes_no_marker(self):
        # the fifth item: a host that left in the window before the stand-down read its lease made a marker naming
        # 'None:None', which every later lease-less ensure matched forever
        for lease_state, lease, starts in (("none", None, {}), ("orphan", dict(cli=999999997, host=999999996), {})):
            with self.subTest(lease_state):
                d, be = self._be()
                sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID})
                if lease:
                    self._host_lease(d, **lease)
                s = types.SimpleNamespace(sid=SID, name="web", backend=be, inflight=1, detached=False, _reconnect=True,
                                          _host_attach_retries=4, _host=types.SimpleNamespace(hello={"host": {"pid": 5, "start": "h"}}))
                with mock.patch.object(sb, "proc_start", lambda p, run=None, st=starts: st.get(p)):
                    sb.SdkSession._host_stand_down(s, TimeoutError("initialize"))
                reg = sb.read_reg(Path(d), SID) or {}
                self.assertNotIn("hostAttachFailed", reg, "no marker without a live host lease: the next connect walks the orphan road")
                self.assertTrue(s.detached)
        # and WITH a live attach lease the marker names the lease holder
        d, be = self._be()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID})
        self._host_lease(d)
        s = types.SimpleNamespace(sid=SID, name="web", backend=be, inflight=1, detached=False, _reconnect=True,
                                  _host_attach_retries=4, _host=types.SimpleNamespace(hello={"host": {"pid": 5, "start": "h"}}))
        with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)):
            sb.SdkSession._host_stand_down(s, TimeoutError("initialize"))
        self.assertEqual(((sb.read_reg(Path(d), SID) or {}).get("hostAttachFailed") or {}).get("host"), "999999996:2")

    def test_an_automatic_send_is_queued_behind_the_stand_down_and_the_users_send_lifts_it(self):
        # the commit-12 review's second item (only the user's message lifts) and the commit-13 review's first (a refused
        # automatic message was DROPPED after its sender's ledger row had said fired): the stand-down refuses the
        # attach, never the message: an automatic send lands in the persisted queue mirror and rides the attach the
        # user's next message makes, in order
        d, be = self._be()
        self._marked(d); self._host_lease(d)
        started = []
        with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)), \
             mock.patch.object(sb.SdkSession, "start", lambda self: started.append(self.sid)):
            self.assertTrue(be.send(SID, "<!-- romp-injected --><!-- romp-auto --> a nudge"), "accepted: queued, not dropped")
            self.assertTrue(be.send(SID, "a watch notice <!-- romp-tag: watch -->"))
            reg = sb.read_reg(Path(d), SID) or {}
            self.assertEqual(reg.get("queue"), ["<!-- romp-injected --><!-- romp-auto --> a nudge", "a watch notice <!-- romp-tag: watch -->"])
            self.assertEqual([m["text"] for m in reg.get("queueMeta")], reg["queue"], "the mirror's meta aligns with the queue")
            self.assertIn("hostAttachFailed", reg, "the marker stands"); self.assertEqual(started, [], "no thread, no attach")
            self.assertEqual([q["md"] for q in be.pending_queued_meta(SID)], reg["queue"], "the chat shows them queued")
            self.assertFalse(be.deliver(SID, "peer mail"), "postal mail is refused as before (the bus keeps its copy)")
            self.assertTrue(be.send(SID, "the user's words", user=True))
        reg = sb.read_reg(Path(d), SID) or {}
        self.assertNotIn("hostAttachFailed", reg, "the user's message lifts the marker before the ensure")
        self.assertEqual(started, [SID], "one start, for the user's send")
        s = be.sessions[SID]
        self.assertEqual(s._pending[:2], ["<!-- romp-injected --><!-- romp-auto --> a nudge", "a watch notice <!-- romp-tag: watch -->"],
                         "the session seeds the queued automatic messages first, then the user's")
        self.assertEqual(s._pending[2], "the user's words")

    def test_a_queued_send_that_races_a_lift_lands_in_the_live_session(self):
        # the commit-14 review's first item: the queue-behind appended to the mirror after an unlocked check; a user's
        # send lifting the marker in that window started a session whose queue seed had already been read, and the
        # next _persist_queue erased the automatic text. The re-check under _reg_lock declines and send() falls through.
        d, be = self._be()
        self._marked(d); self._host_lease(d)
        got = []
        live = types.SimpleNamespace(sid=SID, thread=types.SimpleNamespace(is_alive=lambda: True),
                                     enqueue=lambda t, qid=None, qts=None: got.append(t))
        real_holds = be._attach_stand_down_holds
        def holds_then_lift(sid, reg=None):
            r = real_holds(sid, reg)
            be._lift_attach_stand_down(sid)            # the user's send wins the race: marker gone, session live
            be.sessions[sid] = live
            return r
        with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)), \
             mock.patch.object(be, "_attach_stand_down_holds", holds_then_lift):
            self.assertTrue(be.send(SID, "<!-- romp-injected --> a nudge"))
        self.assertEqual(got, ["<!-- romp-injected --> a nudge"], "enqueued on the live session, not landed in a mirror")
        self.assertEqual((sb.read_reg(Path(d), SID) or {}).get("queue") or [], [], "nothing written to the mirror")

    def test_a_dead_session_refuses_an_automatic_send_before_the_stand_down_is_consulted(self):
        # the second item: the alive check lived inside _ensure, unreachable while the marker held, so a stood-down
        # session the user had ended kept accepting automatic messages into a dead reg's mirror
        d, be = self._be()
        self._marked(d, alive=False); self._host_lease(d)
        with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)):
            self.assertFalse(be.send(SID, "<!-- romp-injected --> a nudge"), "refused, as e030cd45 did: the watch row retries")
        self.assertEqual((sb.read_reg(Path(d), SID) or {}).get("queue") or [], [])

    def test_kill_drops_the_marker_and_ends_the_host_a_stood_down_session_left_running(self):
        # the second item's other half: kill() on a stood-down session with no object flipped alive and left the
        # host and its CLI running under a live lease; now it drops the marker and ends the host through its lease
        d, be = self._be()
        self._marked(d); self._host_lease(d)
        ended = []
        with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)), \
             mock.patch.object(be, "_end_host_by_lease", lambda sid: ended.append(sid) or True):
            self.assertTrue(be.kill(SID))
        reg = sb.read_reg(Path(d), SID) or {}
        self.assertFalse(reg.get("alive")); self.assertNotIn("hostAttachFailed", reg)
        self.assertEqual(ended, [SID], "the live host is ended through its lease")
        # and the real helper declines when no live host lease holds
        d2, be2 = self._be()
        sb.write_reg(Path(d2), SID, {"sid": SID, "name": "web", "alive": True})
        self.assertFalse(be2._end_host_by_lease(SID))

    def test_two_automatic_messages_with_the_same_words_are_both_queued(self):
        # the fourth item: a by-text dedupe dropped a legitimately repeated notice and reported it accepted
        d, be = self._be()
        self._marked(d); self._host_lease(d)
        with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)):
            self.assertTrue(be.send(SID, "romp watch: the condition holds"))
            self.assertTrue(be.send(SID, "romp watch: the condition holds"))
        self.assertEqual((sb.read_reg(Path(d), SID) or {}).get("queue"), ["romp watch: the condition holds"] * 2)

    def test_kill_sends_end_to_the_stood_down_host_and_concurrent_ends_start_one_thread(self):
        # the commit-15 review's first item: the end-by-lease connected and closed with no request written, and a
        # transport whose initialize was never answered DETACHES on close (the host kept its CLI); it sends `end`
        # with the kill bound now. And the commit-17 review's first item: two Ends for one sid at once (the
        # dashboard's and `romp end`) opened two sockets; check, create, register and start run under one lock
        d, be = self._be(short=True)
        self._marked(d); self._host_lease(d)
        host, stop = self._serve_fake_host(d)
        import threading
        try:
            with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2", 999999995: "3", 999999994: "4"}.get(p)):
                self.assertEqual(ht.host_lease_state(sb.read_lease(d, SID), time.time()), "attach")
                self.assertTrue(be.kill(SID))
                th = be._end_threads[SID]
                th.join(10)
                self.assertFalse(th.is_alive(), "the end thread finishes: the fake host answers `end` with an exit frame")
                kinds = [f.get("t") for f in host.got]
                self.assertIn("end", kinds, "the host receives END, not a detach: %r; log: %r" % (kinds, be._test_logs[-6:]))
                self.assertNotIn("detach", kinds)
                self.assertEqual(next(f for f in host.got if f.get("t") == "end")["grace"], sh.END_GRACE_KILL_S)
                self.assertNotIn("hostAttachFailed", sb.read_reg(Path(d), SID) or {})
                # the end thread is STARTED under the backend's lock (an unstarted thread reads as not alive to a
                # concurrent checker, so check, create, register and start share one acquisition)
                class Held:
                    def __init__(self, lock): self.lock, self.depth = lock, 0
                    def __enter__(self): self.lock.__enter__(); self.depth += 1; return self
                    def __exit__(self, *a): self.depth -= 1; return self.lock.__exit__(*a)
                held = be._lock = Held(be._lock)
                starts = []
                real_start = threading.Thread.start
                def start_recording(thread):
                    if thread.name.startswith("end-host:"):
                        starts.append(held.depth > 0)
                    return real_start(thread)
                # a NEW host holds the lease (the first was told to end and a later End for it opens no socket): its End starts a thread
                lease = sb.read_lease(d, SID); lease["holder"] = dict(lease["holder"], pid=999999995, start="3"); sb.write_lease(d, lease)
                with mock.patch.object(threading.Thread, "start", start_recording):
                    self.assertTrue(be._end_host_by_lease(SID))
                    be._end_threads[SID].join(10)
                self.assertEqual(starts, [True], "the end thread starts while the lock is held")
                # two concurrent Ends for a host not yet told to end: one thread, one socket
                lease = sb.read_lease(d, SID); lease["holder"] = dict(lease["holder"], pid=999999994, start="4"); sb.write_lease(d, lease)
                host.got.clear()
                results = []
                racers = [threading.Thread(target=lambda: results.append(be._end_host_by_lease(SID))) for _ in range(2)]
                for r in racers: r.start()
                for r in racers: r.join(5)
                be._end_threads[SID].join(10)
                self.assertEqual(results, [True, True])
                self.assertEqual(sum(1 for f in host.got if f.get("t") == "attach"), 1, "one socket for two concurrent Ends: %r" % [f.get("t") for f in host.got])
                self.assertEqual(sum(1 for l in be._test_logs if "already under way" in l or "already told to end" in l), 1,
                                 "the second End was refused a socket whether the first thread was still alive or had finished: %r" % be._test_logs[-4:])
        finally:
            stop()

    def test_a_second_end_after_the_first_finished_opens_no_socket_and_a_new_holder_ends_normally(self):
        # main's Python 3.11 job (2026-09-12): two concurrent Ends opened two sockets ("attach, end, attach, end") when the
        # first thread had already finished; the guard remembers the lease holder it told to end
        d, be = self._be(short=True)
        self._marked(d); self._host_lease(d)
        host, stop = self._serve_fake_host(d)
        try:
            with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2", 999999995: "3"}.get(p)):
                self.assertTrue(be._end_host_by_lease(SID))
                be._end_threads[SID].join(10)
                self.assertEqual([f.get("t") for f in host.got], ["attach", "end"])
                self.assertTrue(be._end_host_by_lease(SID), "a later End for the same host is a no-op that reports done")
                self.assertEqual([f.get("t") for f in host.got], ["attach", "end"], "no second socket to a host already told to end")
                self.assertEqual(sum(1 for l in be._test_logs if "already told to end" in l), 1)
                # a NEW host took the lease under the same sid: it is a different holder and ends normally
                lease = sb.read_lease(d, SID)
                lease["holder"] = dict(lease["holder"], pid=999999995, start="3")
                sb.write_lease(d, lease)
                self.assertTrue(be._end_host_by_lease(SID))
                be._end_threads[SID].join(10)
                self.assertEqual([f.get("t") for f in host.got], ["attach", "end", "attach", "end"], "the new holder is ended")
        finally:
            stop()

    def test_an_end_by_lease_gives_up_on_a_host_that_never_says_hello_and_a_later_end_is_not_blocked(self):
        # the commit-17 review's third item: the 5 s bound on hello had no test
        d, be = self._be(short=True)
        self._marked(d); self._host_lease(d)
        host, stop = self._serve_fake_host(d, silent=True)
        try:
            with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)):
                self.assertTrue(be._end_host_by_lease(SID))
                first = be._end_threads[SID]
                first.join(12)
                self.assertFalse(first.is_alive(), "the thread gives up on the silent host and exits")
                self.assertTrue(any("no hello within" in l for l in be._test_logs), "the timeout is logged: %r" % be._test_logs[-4:])
                self.assertTrue(be._end_host_by_lease(SID))
                self.assertIsNot(be._end_threads[SID], first, "a later End starts a fresh thread: no stuck 'already under way'")
                be._end_threads[SID].join(12)
        finally:
            stop()

    def test_a_message_queued_between_the_ensure_read_and_the_insert_reaches_the_live_session(self):
        # the third item: _ensure seeded the SdkSession from a reg dict read before the insert; a queue-behind on
        # _reg_lock alone could append in between (no session yet), and the seed missed it. The seed is re-read
        # under _reg_lock after the insert.
        d, be = self._be()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True, "lastSid": SID, "cwd": d, "queue": ["first"]})
        late, qid = "<!-- romp-injected --> a nudge", "echo:" + "b" * 32
        def move_between(sid, reg=None):
            with be._reg_lock:                          # what a racing writer does after _ensure's read: a queue-behind
                cur = sb.read_reg(Path(d), sid)         # APPENDS with an identity; the boot reconcile PREPENDS its nudge
                cur["queue"] = [sb.CRASH_RESUME_NUDGE] + (cur.get("queue") or []) + [late]
                cur["queueMeta"] = [{"text": late, "qid": qid, "qts": 5}]
                sb.write_reg(Path(d), sid, cur)
            return False
        with mock.patch.object(be, "_attach_stand_down_holds", move_between), \
             mock.patch.object(sb.SdkSession, "start", lambda self: None):
            s = be._ensure(SID)
        self.assertIsNotNone(s)
        self.assertEqual(list(s._pending), [sb.CRASH_RESUME_NUDGE, "first", late], "the live list is the mirror, in mirror order (a prepend and an append)")
        self.assertEqual([m and m.get("qid") for m in s._pending_meta], [None, None, qid], "the late text's identity lands on it, not on a seed copy")

    def test_a_boot_leaves_a_stood_down_session_alone_and_counts_no_attach(self):
        # the fourth item: the boot counted an attach, added the sid to the boot set and wrote the reconcile.boot row
        # before the stagger's ensure refused; the attach a later send made then filed host.attached with boot=True
        d, be = self._be()
        self._marked(d, cwd=d, mode="default", effort="high")
        self._host_lease(d)
        started = []
        with mock.patch.object(sb, "proc_start", lambda p, run=None: {999999997: "1", 999999996: "2"}.get(p)), \
             mock.patch.object(sb.SdkSession, "start", lambda self: started.append(self.sid)):
            be._boot_reconcile([sb.read_reg(Path(d), SID)])
        self.assertNotIn(SID, be._boot_attach_sids, "no boot-attach entry for a session the boot stood down from")
        self.assertEqual(started, [], "not started at boot")
        self.assertIn("hostAttachFailed", sb.read_reg(Path(d), SID) or {}, "a boot is not new information; the marker stays")
        self.assertTrue(any("stays stood down" in l for l in be._test_logs), "the boot says so")


class Pins(unittest.TestCase):
    @unittest.skipUnless(SDK, "the SDK is not importable here")
    def test_host_transport_implements_the_sdks_six_transport_methods(self):
        from claude_agent_sdk._internal.transport import Transport
        abstract = set(getattr(Transport, "__abstractmethods__", set()))
        self.assertEqual(abstract, {"connect", "write", "read_messages", "close", "is_ready", "end_input"})
        self.assertTrue(issubclass(ht.HostTransport, Transport))
        self.assertEqual(set(getattr(ht.HostTransport, "__abstractmethods__", set())), set())

    def test_backend_wiring(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
        self.assertIn("async with ClaudeSDKClient(options=opts, transport=transport) as client:", src)
        self.assertIn("transport = await self.backend._host_transport_for(self, opts,", src)
        self.assertIn("if self.loop and self.client and not self.detached:", src, "shutdown never interrupts a detached session")
        self.assertIn("if self.backend.session_hosts_on() or self.backend._host_lease_applies(self):", src, "a live host lease is attached whatever the setting")
        self.assertIn("if not sess.ended and not sess.detached:", src, "a latched detach is not a crash")
        self.assertIn("s._host.detach_mode = True", src, "the drain detaches")
        self.assertIn('if s.inflight and getattr(s, "_host", None) is None and not getattr(s, "_host_intent", False)]', src, "an attached (or attaching) session is never a cut")
        self.assertIn("s._host.end_grace = _ht().sh.END_GRACE_KILL_S", src, "kill gets the short bound")
        self.assertIn('== "attach":', src, "boot attach-first")
        self.assertIn('append_session_event(self.state_dir, "host.attached"', src)
        self.assertIn("m.timeout = _ht().sh.HOOK_TIMEOUT_S", src, "hooks carry the bound under a host")
        self.assertIn("self._host_stand_down(e)\n                    break", src, "past the attach bound the session stands down and LEAVES the loop, never the crash heal")
        self.assertIn("        if user:\n            self._lift_attach_stand_down(sid)", src, "the USER's send is the word that lifts a stand-down; an automatic one is not")
        self.assertIn("                    connected = True\n                    self._host_attach_retries = 0", src,
                      "the retry counter resets inside the connected block: consecutive incomplete attaches only (the commit-10 review's first item)")
        ksrc = open(os.path.join(ROOT, "kernel", "kernel.py")).read()
        self.assertIn("if _send_with_id(be, sid, text, qid, user=user, paths=paths) is False:", ksrc, "the park-or-send route hands on who speaks and what rode along; the caller that knows classifies (T315; the attachment list, T373)")
        self.assertIn('user="<!-- romp-tag: " not in text', ksrc, "POST /send: an untagged send is the user's, a tagged one a machine's")   # the one delivery door since T370
        self.assertIn('return "user" in inspect.signature(fn).parameters', ksrc, "read from the signature, so a stand-in send without the keyword is called as before")

    def test_construction_reads_no_setting_and_the_file_is_the_toggle_read_on_each_ask(self):
        d = tempfile.mkdtemp(); be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        self.assertTrue(be.session_hosts_on(), "on by default (T348): a bare state dir")
        Path(d, "session-hosts").write_text("off")
        self.assertFalse(be.session_hosts_on(), "the file is the toggle, read on each ask, no restart")

    def test_host_log_rows_are_filed_once_per_line(self):
        d = tempfile.mkdtemp(); logs = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=logs.append)
        sess = types.SimpleNamespace(sid=SID, name="web", _host=None)
        hd = ht.host_dir(d, SID); hd.mkdir(parents=True)
        (hd / "host.log").write_text(json.dumps({"t": 1, "kind": "attached"}) + "\n"
                                     + json.dumps({"t": 2, "kind": "hook-self-answered", "event": "Stop", "callbackId": "hook_0", "parkedS": 480}) + "\n"
                                     + json.dumps({"t": 3, "kind": "end-forced", "cliPid": 5}) + "\n")
        be._file_host_log_rows(sess)
        rows = [json.loads(l) for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["host.hook-self-answered", "host.end-forced"])
        self.assertEqual((rows[0]["sid"], rows[0]["event"], rows[0]["t"]), (SID, "Stop", 2))
        be._file_host_log_rows(sess)
        rows2 = [json.loads(l) for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines()]
        self.assertEqual(len(rows2), 2, "no line is filed twice")
        self.assertEqual(sum(1 for l in logs if "hook" in l and "itself" in l), 1)


@unittest.skipUnless(SDK, "the SDK is not importable here (the end-to-end run needs ClaudeSDKClient)")
class EndToEnd(unittest.TestCase):
    """A backend with hosts ON drives the fake CLI through a real host: a turn started under one kernel finishes
    under the next after a drain that detaches instead of cutting, and a kill ends the host's CLI gracefully.
    Hermetic: a private state root, the fake CLI as claude_bin, no scopes, every process ended by the test."""
    FAKE = os.path.join(HERE, "fixtures", "fake_claude.py")

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(self._sweep)
        Path(self.d, "session-hosts").write_text("on")
        Path(self.d, "session-host-grace").write_text("600")
        # the fake CLI's transcript stand-in: it inherits the backend's environment through the host, and the
        # test reads the result text from it (an interrupted turn would say so)
        self.tdir = os.path.join(self.d, "transcripts")
        self._env_before = os.environ.get("FAKE_CLI_TRANSCRIPT_DIR")
        os.environ["FAKE_CLI_TRANSCRIPT_DIR"] = self.tdir
        self.addCleanup(self._restore_env)
        self.sid = str(__import__("uuid").uuid4())
        for sub in ("sdk", "states", "names"):
            os.makedirs(os.path.join(self.d, sub), exist_ok=True)
        cwd = os.path.join(self.d, "proj"); os.makedirs(cwd)
        sb.write_reg(Path(self.d), self.sid, {"sid": self.sid, "name": "web", "cwd": cwd, "alive": True, "mode": "bypassPermissions",
                                              "effort": "high", "lastSid": self.sid})
        self.logs = []

    def _restore_env(self):
        if self._env_before is None:
            os.environ.pop("FAKE_CLI_TRANSCRIPT_DIR", None)
        else:
            os.environ["FAKE_CLI_TRANSCRIPT_DIR"] = self._env_before

    def _sweep(self):
        for be in getattr(self, "_bes", []):
            try:
                be.drain(timeout=3)
            except Exception:
                pass
        lease = sb.read_lease(self.d, self.sid)
        if lease:
            for key in ("holder", None):
                pid = (lease.get(key) or {}).get("pid") if key else lease.get("pid")
                try:
                    os.kill(int(pid), 9)
                except Exception:
                    pass

    def _backend(self):
        be = sb.SdkBackend(self.d, self.FAKE, lambda *a, **k: None, log=self.logs.append, code_version="t315")
        self._bes = getattr(self, "_bes", []) + [be]
        return be

    def _wait(self, pred, timeout=20, what=""):
        deadline = time.time() + timeout
        while time.time() < deadline:   # loop-ok: a bounded wait on an observable event
            if pred():
                return True
            time.sleep(0.1)
        self.fail("timed out waiting for %s\nlog tail: %s" % (what, "\n".join(self.logs[-15:])))

    def test_a_turn_survives_a_drain_and_finishes_under_the_next_backend(self):
        be = self._backend()
        self.assertTrue(be.send(self.sid, "start long sleep=6"))
        self._wait(lambda: (sb.read_lease(self.d, self.sid) or {}).get("holder", {}).get("kind") == "host", what="a host-held lease")
        lease = sb.read_lease(self.d, self.sid)
        self._wait(lambda: len(list(sh.read_journal_dir(ht.host_dir(self.d, self.sid)))) >= 2, what="the init and assistant records in the journal")
        self._wait(lambda: isinstance((sb.read_reg(Path(self.d), self.sid) or {}).get("hostAck"), dict), what="hostAck in the registry")
        res = be.drain(timeout=5)
        self.assertEqual(res["cutTurns"], [], "an attached session is detached, never cut")
        self.assertEqual(res["reaped"], 0)
        time.sleep(0.5)
        lease2 = sb.read_lease(self.d, self.sid)
        self.assertIsNotNone(lease2, "the host keeps its lease across the kernel's drain")
        self.assertEqual((lease2["pid"], lease2["holder"]["pid"]), (lease["pid"], lease["holder"]["pid"]), "same CLI, same host")
        self.assertEqual(sb.lease_state(lease2, time.time()), "valid")
        # the next kernel: boot attach-first
        import subprocess as _sp
        ps_lines = _sp.run(sb.PS_ARGV, capture_output=True, text=True, timeout=10).stdout.splitlines()
        census = sb.lease_census(ps_lines, [self.sid], os.getpid(), sb.list_leases(self.d), version="t315")
        mine = [l for l in ps_lines if self.sid in l]
        self.assertEqual(census["problems"], [], "the census before the second boot: owned=%r orphans=%r dead=%r; ps lines: %r; lease=%r"
                         % (census["owned"], census["orphans"], census["dead_leases"], mine, sb.read_lease(self.d, self.sid)))
        # the setting is turned OFF while the host lives: a live host lease is attached regardless (the setting
        # governs new spawns), never a second CLI beside the host's (the commit 2-3 review's third finding)
        Path(self.d, "session-hosts").write_text("off")
        be2 = self._backend()
        be2._boot_reconcile([sb.read_reg(Path(self.d), self.sid)])
        self._wait(lambda: sum(1 for l in self._events() if l.get("kind") == "host.attached") >= 2, what="the second host.attached row")
        att = [l for l in self._events() if l.get("kind") == "host.attached"]
        self.assertEqual([a["boot"] for a in att], [False, True], "the first backend attached at spawn, the second at boot; log: %s"
                         % "\n".join(l for l in self.logs if "boot reconcile" in l or "host (" in l)[-2500:])
        self.assertGreater(att[1]["replayFrom"], 0, "the boot attach replayed from the acknowledged offset, not from zero")
        self._wait(lambda: sb.last_state_value(Path(self.d), self.sid) == "waiting", timeout=30, what="the turn's result under the second backend")
        self.assertEqual(sb.read_lease(self.d, self.sid)["pid"], lease["pid"], "one CLI process the whole way: one writer")
        transcripts = list(Path(self.tdir).glob("*.jsonl"))
        self.assertEqual(len(transcripts), 1, "one transcript stand-in")
        results = [json.loads(l)["result"] for l in transcripts[0].read_text().splitlines() if '"type":"result"' in l]
        self.assertEqual(results, ["done"], "the turn's normal completion: the drain neither cut nor interrupted it (the first finding)")
        self.assertFalse(any("restarted" in t for t in ((sb.read_reg(Path(self.d), self.sid) or {}).get("queue") or [])),
                         "no continuation notice for a turn that was never cut")
        # kill: end with the short bound; the host ends the CLI and leaves, the lease goes
        be2.kill(self.sid)
        self._wait(lambda: sb.read_lease(self.d, self.sid) is None, timeout=20, what="the lease removed after kill")
        kinds = [l["kind"] for l in self._events()]
        self.assertNotIn("host.died", kinds)

    def _events(self):
        p = Path(self.d) / sb.SESSION_EVENTS_FILE
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


@unittest.skipUnless(SDK, "the SDK is not importable here")
class AttachStandDown(unittest.TestCase):
    """The real connect loop against a fake host whose initialize never answers (the commit-10 review): exactly
    four attaches, one host.attach-failed row, one waiting row, then NO further attach from the timer sweep, a
    boot or a plain connect until a send arrives; and the retry counter counts CONSECUTIVE incomplete attaches
    only (a completed connect resets it). The SDK's initialize timeout is forced to one second through the Query
    seam; the host lease names this test process as both CLI and host and is beaten by a thread."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(self._sweep)
        Path(self.d, "session-hosts").write_text("on")
        for sub in ("sdk", "states", "names", "hosts"):
            os.makedirs(os.path.join(self.d, sub), exist_ok=True)
        self.sid = str(__import__("uuid").uuid4())
        cwd = os.path.join(self.d, "proj"); os.makedirs(cwd)
        sb.write_reg(Path(self.d), self.sid, {"sid": self.sid, "name": "web", "cwd": cwd, "alive": True, "mode": "bypassPermissions",
                                              "effort": "high", "lastSid": self.sid})
        me, start = os.getpid(), sb.proc_start(os.getpid())
        self.holder = "%s:%s" % (me, start)
        self._beating = True
        def beat():
            while self._beating:      # loop-ok: the lease heartbeat, ended by the test's cleanup
                sb.write_lease(self.d, {"sid": self.sid, "fsid": self.sid, "pid": me, "start": start,
                                        "holder": {"pid": me, "start": start, "kind": "host"}, "version": "t315", "t": time.time()})
                time.sleep(1.0)
        import threading
        self.beater = threading.Thread(target=beat, daemon=True); self.beater.start()
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append, code_version="t315")
        import claude_agent_sdk._internal.query as q
        orig = q.Query.__init__
        def fast_init(self_, *a, **kw):
            kw["initialize_timeout"] = 1.0
            return orig(self_, *a, **kw)
        patcher = mock.patch.object(q.Query, "__init__", fast_init); patcher.start(); self.addCleanup(patcher.stop)
        self.loop = asyncio.new_event_loop()
        self.host = None

    def _serve(self, **kw):
        sock = str(ht.host_sock(self.d, self.sid))
        self.host = FakeHost(sock, [], **kw)
        import threading
        started = threading.Event()
        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.host.start()); started.set()
            self.loop.run_forever()
        threading.Thread(target=run_loop, daemon=True).start()
        started.wait(5)

    def _sweep(self):
        self._beating = False
        try:
            self.be.drain(timeout=3)
        except Exception:
            pass
        if self.host:
            self.loop.call_soon_threadsafe(self.host.close)
        self.loop.call_soon_threadsafe(self.loop.stop)
        sb.remove_lease(self.d, self.sid)

    def _wait(self, pred, timeout=30, what=""):
        deadline = time.time() + timeout
        while time.time() < deadline:   # loop-ok: a bounded wait on an observable event
            if pred():
                return True
            time.sleep(0.1)
        self.fail("timed out waiting for %s\nlog tail: %s" % (what, "\n".join(self.logs[-15:])))

    def _kinds(self):
        p = Path(self.d) / sb.SESSION_EVENTS_FILE
        return [json.loads(l)["kind"] for l in p.read_text().splitlines()] if p.exists() else []

    def _states(self):
        p = Path(self.d) / "states" / (self.sid + ".jsonl")
        return [json.loads(l).get("state") for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def test_four_unanswered_attaches_stand_the_session_down_once_until_a_send(self):
        self._serve(answer_init=lambda n: False)
        self.assertTrue(self.be.send(self.sid, "hello"))
        self._wait(lambda: "host.attach-failed" in self._kinds(), timeout=40, what="the stand-down row")
        self._wait(lambda: not (self.be.sessions.get(self.sid) and self.be.sessions[self.sid].thread.is_alive()), what="the thread's exit")
        time.sleep(3.0)                       # two more retry periods: nothing may attach again on its own
        self.assertEqual(self.host.attaches, 4, "exactly four attaches, then the loop is left")
        self.assertEqual(self._kinds().count("host.attach-failed"), 1)
        self.assertEqual(self._states().count("waiting"), 1, "one waiting row from the stand-down; states: %r" % self._states())
        self.assertNotIn("crash.heal", self._kinds()); self.assertNotIn("crash.loop", self._kinds())
        reg = sb.read_reg(Path(self.d), self.sid) or {}
        self.assertEqual((reg.get("hostAttachFailed") or {}).get("host"), self.holder, "the marker names the lease holder that would not answer")
        self.assertNotIn(sb.CRASH_RESUME_NUDGE, reg.get("queue") or [], "no crash-resume nudge for a CLI that never died")
        # the automatic doors stay shut while the marker names the current lease holder
        self.assertIsNone(self.be._ensure(self.sid), "a plain ensure (the sweep, a heal) stands down")
        self.assertFalse(self.be.connect(self.sid))
        self.be._boot_reconcile([sb.read_reg(Path(self.d), self.sid)])
        time.sleep(1.5)
        self.assertEqual(self.host.attaches, 4, "no attach from the sweep, the connect or the boot")
        # romp's own automatic message (the default send) is QUEUED behind the stand-down: accepted, no attach
        self.assertTrue(self.be.send(self.sid, "<!-- romp-injected --><!-- romp-auto --> a nudge"))
        time.sleep(1.5)
        self.assertEqual(self.host.attaches, 4, "an automatic send never lifts the stand-down")
        reg = sb.read_reg(Path(self.d), self.sid) or {}
        self.assertIn("hostAttachFailed", reg)
        self.assertIn("<!-- romp-injected --><!-- romp-auto --> a nudge", reg.get("queue") or [], "queued in the persisted mirror")
        # the USER's message is new information: the marker clears, the attach is tried again, and both ride it in order
        self.host.answer_init = lambda n: n >= 5
        self.assertTrue(self.be.send(self.sid, "again", user=True))
        self._wait(lambda: self.host.attaches >= 5, what="the fifth attach, for the user's send")
        self.assertNotIn("hostAttachFailed", sb.read_reg(Path(self.d), self.sid) or {})
        def user_texts():
            out = []
            for f in list(self.host.got):
                if f.get("t") == "in":
                    try:
                        obj = json.loads(f["data"])
                    except ValueError:
                        continue
                    if obj.get("type") == "user":
                        c = obj["message"]["content"]
                        out.append(c if isinstance(c, str) else c[0].get("text"))
            return out
        self._wait(lambda: len(user_texts()) >= 3, what="every message fed to the CLI through the host")
        self.assertEqual(user_texts()[:3], ["hello", "<!-- romp-injected --><!-- romp-auto --> a nudge", "again"],
                         "original send order: the first message the stand-down held, the queued automatic one, then the user's new one")

    def test_the_retry_counter_counts_consecutive_incomplete_attaches_only(self):
        # attaches 1, 3, 5, 7 never complete; 2, 4, 6 do (and are reconnected on request): the fourth timeout in the
        # session's life is the FIRST of a new run, a retry, never a stand-down
        self._serve(answer_init=lambda n: n % 2 == 0)
        self.assertTrue(self.be.connect(self.sid), "an eager connect, no turn: request_reconnect acts at once only on an idle session")
        for target in (2, 4, 6):
            self._wait(lambda t=target: self.host.attaches >= t and self.be.sessions.get(self.sid) is not None
                       and self.be.sessions[self.sid].client is not None, what="a completed connect on attach %d" % target)
            self.be.sessions[self.sid].request_reconnect()
        self._wait(lambda: self.host.attaches >= 8, timeout=40, what="the eighth attach: the seventh's timeout was a retry")
        self.assertNotIn("host.attach-failed", self._kinds(), "three recovered timeouts and one more never reach the bound")
        self.assertNotIn("hostAttachFailed", sb.read_reg(Path(self.d), self.sid) or {})


if __name__ == "__main__":
    unittest.main()
