#!/usr/bin/env python3
"""Kernel half of the SDK-lifecycle hardening (2026-07-05):

  * parked-ops persistence — _pending_ops mirrors to pending-ops.json on every mutation and is
    restored at boot, so a kernel restart can't silently drop messages the user queued against a
    busy session;
  * POST /interrupt + /end — the headless control routes mirroring the WS drive ops (before this a
    session could be FED without a browser but never STOPPED);
  * wiring pins — main() installs the SIGTERM drain handler and constructs the SDK backend eagerly
    with reconcile=True (source-pinned, same style as test_sdk_kernel's dispatch pins);
  * _sdk() single-flight — the eager boot thread races handler threads, and an unlocked
    check-then-act built 2-3 duplicate SdkBackends whose reconciles reaped each other's live CLIs
    (2026-07-06 kill storm: sessions dying with exit 143 mid-turn).

XDG_STATE_HOME is pointed at a temp dir BEFORE the kernel module loads, so jd.STATE — and with it
pending-ops.json and every state write — stays out of the live user state (see
[[distiller-giveup-rearm]]: leaking test state into ~/.local/state/romp corrupts live behavior).
"""
import json
import os
import tempfile
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_headless", os.path.join(BIN, "romp-kernel")).load_module()

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

# The tmux PROMPT HOLD (_hold_drain: a tmux-shaped delivery holds the sid for a moment, tested in
# tests/test_kernel_parked_ops_liveness.py) is a separate axis: off here, so back-to-back
# _apply_pending_ops calls stand for successive cycles.
km._TMUX_PROMPT_HOLD_S = 0.0


class PendingOpsPersistence(unittest.TestCase):
    def setUp(self):
        km._pending_ops.clear()
        try:
            os.unlink(km._PENDING_OPS_FILE)
        except OSError:
            pass

    def test_state_dir_is_isolated(self):
        self.assertTrue(str(km._PENDING_OPS_FILE).startswith(_STATE_TMP),
                        "the test must never write the live pending-ops.json")

    def test_park_mirrors_to_disk_and_reload_restores(self):
        km._park_op("sid-1", ("send", "queued while busy", "human"))
        km._park_op("sid-1", ("model", "opus"))
        km._park_op("sid-1", ("model", "sonnet"))     # replace-in-place also persists
        on_disk = json.loads(km._PENDING_OPS_FILE.read_text())
        self.assertEqual(on_disk, {"sid-1": [["send", "queued while busy", "human"],
                                             ["model", "sonnet"]]})
        # a fresh kernel's boot path restores the same queues, as tuples
        self.assertEqual(km._load_pending_ops(),
                         {"sid-1": [("send", "queued while busy", "human"), ("model", "sonnet")]})

    def test_delivery_shrinks_the_disk_mirror(self):
        km._park_op("sid-2", ("effort", "high"))
        fake = mock.Mock()
        with mock.patch.object(km, "_compacting_now", return_value=False), \
             mock.patch.object(km, "_working_now", return_value=False), \
             mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)):
            km._apply_pending_ops()
        fake.set_effort.assert_called_once_with("sid-2", "high")
        self.assertEqual(km._load_pending_ops(), {}, "a delivered op leaves the disk mirror")

    def test_missing_file_loads_empty(self):
        self.assertEqual(km._load_pending_ops(), {})


class HeadlessRoutes(unittest.TestCase):
    """POST /interrupt and /end over the REAL handler on loopback (the ServeSecurity pattern)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_interrupt_route_mirrors_the_ws_op(self):
        fake = mock.Mock()
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)):
            code, resp = self._post("/interrupt", {"name": "runaway"})
        self.assertEqual((code, resp), (200, {"ok": True}))
        fake.interrupt.assert_called_once()
        sid = fake.interrupt.call_args[0][0]
        self.assertIn(str(sid), km._interrupt_clicked,
                      "the chat chip flips to 'interrupting' exactly like the WS op")

    def test_end_route_kills_and_announces_close(self):
        fake = mock.Mock()
        sent = []
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            code, resp = self._post("/end", {"id": "sid-x"})
        self.assertEqual((code, resp), (200, {"ok": True}))
        fake.kill.assert_called_once()
        self.assertIn(("chat", {"type": "closed", "id": fake.kill.call_args[0][0]}), sent)

    def test_send_route_reports_queued_vs_sent(self):
        # `queued` says which arm the send took (2026-09-03): an agent sending ITSELF a slash command from
        # inside its own turn read 'ok' and could not know the command was parked until that turn ended
        fake = mock.Mock()
        fake.busy.return_value = None
        km._pending_ops.clear()
        try:
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: True):
                code, resp = self._post("/send", {"id": "sid-q", "text": "/frobnicate now"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": True}))
            self.assertEqual(list(km._pending_ops.values()), [[("command", "/frobnicate now", None)]])
            fake.send.assert_not_called()
            km._pending_ops.clear()
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: False):
                code, resp = self._post("/send", {"id": "sid-q", "text": "/frobnicate now"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": False}))
            fake.send.assert_called_once()
            self.assertEqual(fake.send.call_args[0][1], "/frobnicate now")
        finally:
            km._pending_ops.clear()

    def test_send_route_reports_a_parked_meta_command_as_queued(self):
        # /model, /effort and /fast take the kernel's own setters (_route_meta_command), which park under
        # the same gate as a text send — the route must say `queued` for them too (review find, 2026-09-03)
        fake = mock.Mock()
        fake.busy.return_value = None
        km._pending_ops.clear()
        try:
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: True):
                code, resp = self._post("/send", {"id": "sid-m", "text": "/effort high"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": True}))
            self.assertEqual(list(km._pending_ops.values()), [[("effort", "high")]], "parked as the setter's op")
            fake.set_effort.assert_not_called()
            km._pending_ops.clear()
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: False):
                code, resp = self._post("/send", {"id": "sid-m", "text": "/effort high"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": False}))
            fake.set_effort.assert_called_once()
        finally:
            km._pending_ops.clear()

    def test_send_route_passes_a_remote_kernels_queued_through(self):
        # a session living on another kernel: its answer's `queued` rides back to the caller; an older
        # remote without the field reads as not queued (today's behaviour)
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: {"ok": True, "queued": True}):
            code, resp = self._post("/send", {"id": "sid-r", "text": "/frobnicate now"})
        self.assertEqual((code, resp), (200, {"ok": True, "queued": True}))
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: {"ok": True}):
            code, resp = self._post("/send", {"id": "sid-r", "text": "hello"})
        self.assertEqual((code, resp), (200, {"ok": True, "queued": False}))
        # …and a far kernel's REFUSAL rides back as itself, never rewritten into an ok (review find, #904)
        refusal = {"ok": False, "error": "isolation: the target session's mailbox is OFF"}
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: dict(refusal)):
            code, resp = self._post("/send", {"id": "sid-r", "text": "hello"})
        self.assertEqual((code, resp), (200, refusal))

    def test_missing_who_is_a_400(self):
        code, resp = self._post("/interrupt", {})
        self.assertEqual(code, 400)
        self.assertFalse(resp.get("ok"))


class CodexRuntimeSelection(unittest.TestCase):
    def test_path_codex_does_not_override_sdk_runtime(self):
        fake_mod = mock.Mock()
        fake_loader = mock.Mock()
        fake_loader.load_module.return_value = fake_mod
        with mock.patch.object(km, "_codex_backend", None), \
             mock.patch.object(km, "SourceFileLoader", return_value=fake_loader), \
             mock.patch.object(km.shutil, "which", return_value="/TESTBIN/codex"):
            backend = km._codex()
            self.assertIs(backend, fake_mod.CodexBackend.return_value)
            self.assertIs(km._codex(), backend)
        fake_mod.CodexBackend.assert_called_once()
        self.assertIsNone(fake_mod.CodexBackend.call_args.kwargs.get("codex_bin"),
                          "the SDK must resolve its bundled runtime even when codex is on PATH")


class SdkSingleFlight(unittest.TestCase):
    """Concurrent _sdk() calls must construct exactly ONE backend. The 2026-07-06 storm: the eager
    boot thread + handler threads each passed the unlocked `if _sdk_backend is None` check and built
    their own SdkBackend; every duplicate ran its own boot reconcile, and each reconcile reaped the
    others' freshly-resumed LIVE CLIs (no ppid filter then), killing sessions mid-turn."""

    def test_concurrent_calls_build_one_backend(self):
        import threading
        built = []
        gate = threading.Event()

        class FakeBackend:
            def __init__(self):
                gate.wait(2)                       # hold construction open so every racer arrives
                built.append(self)

        fake_mod = mock.Mock()
        fake_mod.SdkBackend = lambda *a, **k: FakeBackend()
        fake_loader = mock.Mock()
        fake_loader.load_module.return_value = fake_mod
        prev = km._sdk_backend
        try:
            km._sdk_backend = None
            results = [None] * 6
            with mock.patch.object(km, "SourceFileLoader", return_value=fake_loader), \
                 mock.patch.object(km, "_ensure_sdk_on_path", return_value=True):
                ts = [threading.Thread(target=lambda i=i: results.__setitem__(i, km._sdk()))
                      for i in range(6)]
                for t in ts:
                    t.start()
                gate.set()
                for t in ts:
                    t.join(5)
            self.assertEqual(len(built), 1, "one construction, however many racers")
            self.assertTrue(all(r is built[0] for r in results),
                            "every caller gets the same singleton")
        finally:
            km._sdk_backend = prev


class WiringPins(unittest.TestCase):
    """Source pins (the test_sdk_kernel style) for boot/shutdown wiring that can't run in-process:
    delivering a real SIGTERM would os._exit the test runner."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            cls.src = f.read()

    def test_sigterm_handler_installed_in_main(self):
        self.assertIn("signal.signal(signal.SIGTERM, _graceful_term)", self.src)
        self.assertIn("be.drain(", self.src, "the handler drains the SDK backend")
        self.assertIn("os._exit(0)", self.src, "and always exits — a hung drain can't wedge the restart")

    def test_backend_constructed_eagerly_with_reconcile(self):
        self.assertIn("reconcile=True", self.src,
                      "the kernel opts into the boot reconcile (tests construct without it)")
        self.assertIn("threading.Thread(target=_sdk, daemon=True).start()", self.src,
                      "main() constructs the backend at boot so the reconcile isn't lazy")

    def test_graceful_term_never_constructs_the_backend(self):
        body = self.src.split("def _graceful_term", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("_sdk()", body,
                         "shutdown must use the existing singleton only — constructing the backend "
                         "while dying makes no sense and can hang the drain")


if __name__ == "__main__":
    unittest.main()
