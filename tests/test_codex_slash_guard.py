#!/usr/bin/env python3
"""A Codex session never receives a slash command as prose (2026-09-19).

The Claude CLI executes a slash command that arrives as a top-level prompt, so the kernel's send path hands
every slash-shaped text it does not own straight to the backend. Codex's app-server has no slash parser: the
same text reached the model as a prompt ("/clear" on a long thread got a polite reply and cleared nothing;
the battery's compact click sent the word "/compact" and stamped a compacting cue for a compaction that
never began). The guard lives in the route every entry point already takes (_route_meta_command): for a
Codex session it lets /model and /effort through to the setter arms and refuses everything else by the same
predicate that parks (_is_slash_command), with the words on the delivering socket (or a broadcast when no
socket carried the op), in `state` for POST /send and `romp send`, and on the bell's ring. The parked-op
drain routes already-parked Codex command ops through the same refusal; _compact_or_park refuses ahead of
any park or stamp; the composer's "/" palette lists what a Codex session takes. Since the native clear
registered its heads (tests/test_codex_clear_route.py), /clear and /new are TAKEN, not refused: the refused
examples here are /compact and the rest — and a /clear that is not the whole message (a second line after it),
which the route refuses rather than clearing with the rest of the text dropped (2026-09-19).

Fixtures are synthetic: a private placeholder sid (parked ops and notices are keyed by sid, and another
module's journal must never land on this one), an invented copy id in the kernel's echo form, the demo
world's session name. The Codex fake below is the kernel's view of CodexBackend: `send(sid, text)` takes
neither `user` nor `qid` (the real backend's shape, which _send_with_id and _user_send read by signature), the
two setters answer True, and `_session`/`owns` answer for the one sid so _session_backend's durable-row read
(the /commands route) resolves it to "codex" without a live registry."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs
# conftest's floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["ROMP_MANAGER_PORT"] = "1"             # a dead port, never an inherited live one
km = load_source("romp_kernel_codex_slash", os.path.join(BIN, "romp-kernel"))
# Per-session hosts are on by default: a state root without this file starts a real host for any session a
# backend connects. Nothing here connects one; the rule is unconditional for a module that mints its own root.
_HOSTS = Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts")
_HOSTS.parent.mkdir(parents=True, exist_ok=True)
_HOSTS.write_text("off")

SID = "11111111-2222-4333-8444-0c0d0e0f1011"      # private to this module
SID2 = "11111111-2222-4333-8444-0c0d0e0f1012"     # the end-to-end class's own (a real registry row is written for it)
QID = "echo:" + "ab" * 16                          # the press-minted copy id, in the kernel's echo form (_wire_qid admits it)


class _CodexFake:
    """The kernel's view of CodexBackend for these paths: send/set_model/set_effort record and accept; no
    set_fast (the real backend's returns False; the route must never reach it for Codex); `_session` and
    `owns` answer for SID alone, the durable-row read _session_backend makes for a dead or live Codex lane."""
    def __init__(self):
        self.calls = []

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True

    def clear(self, sid, text="/clear"):
        self.calls.append(("clear", sid))              # the native clear's verb; never reached by a refusal
        return ""

    def set_model(self, sid, v):
        self.calls.append(("model", v))
        return True

    def set_effort(self, sid, v):
        self.calls.append(("effort", v))
        return True

    def _session(self, sid):
        return {"sid": sid} if sid == SID else None

    def owns(self, sid):
        return sid == SID

    def live_sessions(self):
        return {}                                    # the liveness merge asks every backend; this one runs nothing live


def _forget(sid):
    """Drop the sid's parked ops from memory AND the disk mirror, and every per-sid latch these paths touch."""
    km._pending_ops.pop(sid, None)
    km._save_pending_ops()
    km._moving.discard(sid)
    km._drain_hold.pop(sid, None)
    km._model_switch_pending.pop(sid, None)


class _Guarded(unittest.TestCase):
    """The shared fixture: the Codex fake is THE Codex backend (`be is _codex()` is the identity every arm tests)
    and owns SID; the gates the setters and the drain read are quiet; the cue stamp and the chat broadcast are
    recorders; the ring's length is noted so a test can assert its own row."""
    def setUp(self):
        self.be = _CodexFake()
        self.sent, self.broadcast, self.marked = [], [], []
        self.client = {"send": lambda t: self.sent.append(json.loads(t))}
        _forget(SID)
        self.addCleanup(_forget, SID)
        stubs = {
            "_codex": lambda: self.be,
            "_sdk": lambda: None,                        # no SdkBackend is built for a lookup that ends at Codex
            "_compacting_now": lambda sid, **k: False,
            "_working_now": lambda sid: False,
            "_limit_hold": lambda sid: None,             # the account gate is its own axis
            "_kernel_knows": lambda sid: True,
            "_push_soon": lambda *a, **k: None,
            "_host_for_sid": lambda sid: None,
            "_mark_compacting": lambda sid: self.marked.append(str(sid)),
            "_send_to_app": lambda app, msg: self.broadcast.append((app, msg)),
            "_mark_model_pending": lambda *a, **k: None,
            "_note_model_pick": lambda *a, **k: None,
        }
        for name, stub in stubs.items():
            p = mock.patch.object(km, name, stub)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be))
        p.start()
        self.addCleanup(p.stop)
        self.ring0 = len(km._SYNC_NOTICES)

    def _last_notice(self):
        self.assertGreater(len(km._SYNC_NOTICES), self.ring0, "a refusal files one row on the bell's ring")
        return km._SYNC_NOTICES[-1]


class CodexSlashRefused(_Guarded):
    def test_a_typed_compact_never_reaches_the_backend(self):
        # the composer's press: the sendMessage arm, with the copy id the client minted
        self.assertTrue(km._drive({"type": "sendMessage", "id": SID, "text": "/compact", "qid": QID}, self.client))
        self.assertEqual(self.be.calls, [], "the backend never saw the text: no send, no echo, no prompt")
        self.assertEqual(len(self.sent), 1, "one frame on the delivering socket")
        frame = self.sent[0]
        self.assertEqual((frame["type"], frame["sid"], frame["qid"]), ("warn", SID, QID),
                         "the warn names the session and the press, so the chat retires the bubble it drew")
        self.assertIn("runs in Codex", frame["text"])
        self.assertIn("nothing was sent", frame["text"])
        self.assertIn("/compact", frame["text"], "the refused command is named")
        row = self._last_notice()
        self.assertEqual(row["kind"], "refused")
        self.assertIn(frame["text"], row["text"], "the bell keeps the same words after the toast fades")
        self.assertFalse(row["ok"])
        self.assertNotIn(SID, km._pending_ops, "a refusal is not an op: nothing parked")

    def test_the_refused_set_is_the_parked_set(self):
        for text in ("/compact", "/fast on", "/autocompact off", "/help", "/model", "/compact\nand more",
                     "/clear\nand more", "/mcp servers"):   # a taken head that is not the whole message is refused too
            state = {}
            self.assertTrue(km._route_meta_command(self.be, SID, text, self.client, state=state), text)
            self.assertIn("refused", state, text)
            self.assertEqual(self.be.calls, [], text)
        # a bare "/mcp" never reaches the kernel from the composer (the client opens the MCP panel), so the palette's
        # /mcp entry and this route agree; with words after it, it is a command Codex has not got, like the rest
        for text in ("/tmp/x is a path", "hello /compact", "prose"):
            state = {}
            self.assertFalse(km._route_meta_command(self.be, SID, text, self.client, state=state), text)
            self.assertEqual(state, {}, "%r is not slash-shaped: the route says nothing and the caller sends it" % text)
            self.assertIs(km._is_slash_command(text), False, "the refused set is exactly the parked set")
        self.assertEqual(self.be.calls, [])

    def test_the_setters_still_land_and_a_wrong_shape_is_refused_with_the_hint(self):
        state = {}
        self.assertTrue(km._route_meta_command(self.be, SID, "/model gpt-5-test", self.client, state=state))
        self.assertEqual(self.be.calls[-1], ("model", "gpt-5-test"))
        self.assertNotIn("refused", state)
        state = {}
        self.assertTrue(km._route_meta_command(self.be, SID, "/effort high", self.client, state=state))
        self.assertEqual(self.be.calls[-1], ("effort", "high"))
        self.assertNotIn("refused", state)
        n = len(self.be.calls)
        for text in ("/model", "/model one two"):
            state = {}
            self.assertTrue(km._route_meta_command(self.be, SID, text, self.client, state=state), text)
            self.assertIn("takes one Codex value here", state["refused"], text)
            self.assertIn("/model gpt-5", state["refused"], "the hint shows the shape that lands")
        state = {}
        self.assertTrue(km._route_meta_command(self.be, SID, "/fast on", self.client, state=state))
        self.assertIn("has no /fast", state["refused"], "no 'isn't connected' toast for a session that has no fast mode")
        self.assertEqual(len(self.be.calls), n, "a refused shape reaches no setter")

    def test_no_gate_is_evaluated_by_a_refusal(self):
        gate_calls = []
        with mock.patch.object(km, "_ops_gate", lambda sid: (gate_calls.append(str(sid)), False)[1]):
            for text in ("/help", "/compact", "/model"):
                self.assertTrue(km._route_meta_command(self.be, SID, text, self.client, state={}), text)
        self.assertEqual(gate_calls, [], "a refusal evaluates no drive-op gate (the setter arms' invariant, extended)")

    def test_post_send_answers_with_the_words(self):
        ok, err, queued = km._deliver_text(SID, "/compact")
        self.assertEqual((ok, queued), (False, False))
        self.assertIn("runs in Codex", err)
        self.assertIn("nothing was sent", err)
        self.assertEqual(self.be.calls, [])
        self.assertEqual(km._deliver_text(SID, "/effort high"), (True, "", False), "a setter command still answers ok")
        self.assertEqual(self.be.calls, [("effort", "high")])

    def test_a_dead_codex_lane_keeps_the_unowned_words(self):
        # a dead Codex session routes to _UNOWNED (CodexBackend.owns is False once dead): the Codex arm does not
        # fire for it, and the sender hears the unowned refusal POST /send renames to the caller's word
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: km._UNOWNED)):
            ok, err, queued = km._deliver_text(SID, "/clear")
        self.assertFalse(ok)
        self.assertIn("not delivered", err)
        self.assertTrue(err.startswith("no running backend owns "), "the shape the /send route renames by sid")
        self.assertNotIn("runs in Codex", err)
        self.assertEqual(self.be.calls, [])


class ParkedBeforeTheGuard(_Guarded):
    def test_a_parked_command_and_compact_are_drained_through_the_refusal(self):
        # ops parked before the guard existed (pending-ops.json survives a restart), or by a caller that skips the
        # route: drained ONCE through the refusal, the setter behind them still delivers on the same pass
        km._pending_ops[SID] = [("command", "/help", "human", QID, True), ("compact",), ("effort", "high")]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("effort", "high")], "neither refused op reached the backend; the setter did")
        self.assertEqual(self.marked, [], "no compacting cue for a compaction that never started")
        self.assertIn(km._pending_ops.get(SID), (None, []), "both refused ops popped, never replayed")
        warns = [(app, msg) for app, msg in self.broadcast if msg.get("type") == "warn"]
        self.assertEqual(len(warns), 2, "one broadcast per refused op, on the chat panes: %r" % (self.broadcast,))
        for app, msg in warns:
            self.assertEqual(app, "chat")
            self.assertEqual(msg["sid"], SID)
            self.assertIn("runs in Codex", msg["text"])
            self.assertNotIn("the session's backend refused it", msg["text"], "the generic toast does not follow the words")
        self.assertEqual(warns[0][1].get("qid"), QID, "the parked copy's id rides, so the chat retires its bubble")
        self.assertIn("/help", warns[0][1]["text"])
        self.assertNotIn("qid", warns[1][1], "a compact op carries no press id")
        self.assertIn("/compact", warns[1][1]["text"])
        self.assertFalse(any("the session's backend refused it" in str(msg.get("text")) for _, msg in self.broadcast))


class CompactOnCodex(_Guarded):
    def test_compact_or_park_refuses_stamps_nothing_parks_nothing(self):
        state = {}
        self.assertIsNone(km._compact_or_park(self.be, SID, state=state), "neither parked nor fired")
        self.assertIn("has no /compact", state["refused"])
        self.assertEqual(self.marked, [], "no cue")
        self.assertNotIn(SID, km._pending_ops, "no park")
        self.assertEqual(self.be.calls, [], "no send of the word")
        warns = [msg for app, msg in self.broadcast if app == "chat" and msg.get("type") == "warn"]
        self.assertEqual(len(warns), 1, "no socket reaches here: the chat panes hear it as a broadcast")
        self.assertEqual(warns[0]["sid"], SID)
        self.assertEqual(self._last_notice()["kind"], "refused")

    def test_compact_request_answers_ok_false(self):
        with mock.patch.object(km.Sessions, "live", staticmethod(lambda: {SID: {"state": "waiting", "backend": "codex"}})):
            res = km._compact_request(SID)
        self.assertIs(res["ok"], False)
        self.assertIn("has no /compact", res["error"])
        self.assertNotIn("queued", res)
        self.assertEqual(self.marked, [])


class Palette(_Guarded):
    def test_commands_for_a_codex_sid_is_the_codex_list(self):
        with mock.patch.object(km, "_session_backend", lambda sid, tm: "codex"):
            cmds, warming = km._commands_for_sid(SID)
        self.assertEqual((cmds, warming), ([dict(c) for c in km._CODEX_COMMANDS], False))
        self.assertEqual([c["name"] for c in cmds], ["clear", "new", "model", "effort", "mcp"])
        self.assertIsNot(cmds[0], km._CODEX_COMMANDS[0], "a copy: the constant is never handed out to be mutated")
        seen = []
        with mock.patch.object(km, "_session_backend", lambda sid, tm: "sdk"), \
             mock.patch.object(km, "_commands_for_cwd", lambda cwd: (seen.append(cwd), (["x"], True))[1]):
            self.assertEqual(km._commands_for_sid(SID), (["x"], True))
        self.assertEqual(seen, [km._cwd_of(SID)], "every other sid keeps the per-cwd probe")

    def test_the_durable_row_answers_for_the_fake(self):
        # the fixture's own claim: _session_backend reads the Codex backend's row when no live metadata names one
        self.assertEqual(km._session_backend(SID, None), "codex")


class Served(_Guarded):
    """The routes over the real handler on loopback (the headless-ops idiom). The Codex identity is the fixture's:
    `_codex` is the fake, whose `_session(SID)` row is what _session_backend reads for GET /commands."""
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, path, body=None):
        import urllib.request, urllib.error
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST" if body is not None else "GET", data=data,
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_post_compact_answers_the_words(self):
        with mock.patch.object(km.Sessions, "live", staticmethod(lambda: {SID: {"state": "waiting", "backend": "codex"}})):
            code, resp = self._req("/compact", {"id": SID})
        self.assertEqual(code, 200)
        self.assertIs(resp["ok"], False)
        self.assertIn("has no /compact", resp["error"])
        self.assertEqual(self.marked, [])

    def test_post_send_answers_the_words(self):
        code, resp = self._req("/send", {"id": SID, "text": "/compact"})
        self.assertEqual(code, 200)
        self.assertIs(resp["ok"], False)
        self.assertIn("runs in Codex", resp["error"])
        self.assertIn("nothing was sent", resp["error"])
        self.assertEqual(self.be.calls, [])

    def test_get_commands_for_a_codex_sid(self):
        code, resp = self._req("/commands?sid=" + SID)
        self.assertEqual(code, 200)
        self.assertEqual([c["name"] for c in resp["commands"]], ["clear", "new", "model", "effort", "mcp"])
        self.assertIs(resp["warming"], False)


class RealBackendGuard(unittest.TestCase):
    """One run over the real CodexBackend, since every class above forces the Codex identity by patching `_codex`:
    the backend is built over the kernel's own state root with the codex-backend module's own scripted FakeClient
    (no extension needed: a refusal reaches no client call), installed as the kernel's singleton so `be is
    _codex()` holds against the real object, and the composer's press drives the real _drive arm."""
    @classmethod
    def setUpClass(cls):
        saved = os.environ["XDG_STATE_HOME"]
        try:
            cls.cbt = load_source("romp_codex_backend_tests_for_guard", os.path.join(HERE, "test_codex_backend.py"))
        finally:
            os.environ["XDG_STATE_HOME"] = saved     # that module floors its own root at import; this one keeps its own

    def setUp(self):
        self.fake = self.cbt.FakeClient()
        # Over the KERNEL's own root (NAMES.parent), never km.jd.STATE (2026-09-19): load_source re-executes
        # judge.py into ONE shared module object, so under a suite jd.STATE is whichever module bound it
        # last, while the kernel's NAMES was bound at its own import. A backend over jd.STATE wrote its
        # registry row, names/ entry and thread transcript into a root two other modules' discovery walks
        # read (an extra discovered session there), and _name_of read a names/ dir the entry was not in
        # (the bell row fell back to the sid). Green alone, red only under the whole suite.
        self.root = km.NAMES.parent
        self.be = self.cbt.cb.CodexBackend(self.root, client_factory=lambda: self.fake)
        with km._codex_lock:
            self._saved_singleton = km._codex_backend
            km._codex_backend = self.be
        self.sent = []
        self.client = {"send": lambda t: self.sent.append(json.loads(t))}
        for name, stub in {"_sdk": lambda: None, "_push_soon": lambda *a, **k: None,
                           "_compacting_now": lambda sid, **k: False, "_working_now": lambda sid: False,
                           "_limit_hold": lambda sid: None}.items():
            p = mock.patch.object(km, name, stub)
            p.start()
            self.addCleanup(p.stop)
        _forget(SID2)
        self.addCleanup(_forget, SID2)
        self._reg = self.root / "codex" / "registry.json"
        self._reg_before = self._reg.read_bytes() if self._reg.exists() else None
        self.addCleanup(self._scrub)                    # after tearDown (cleanup order): no worker writes then

    def _scrub(self):
        """Leave the root as found: the thread transcript, the names/ entry and the registry row SID2's spawn
        wrote (a solo run's root IS the shared jd.STATE, and a bare unittest run has no per-run floor)."""
        try:
            tp = self.be.transcript_path(SID2)
        except Exception:
            tp = None
        for path in (tp, self.root / "names" / SID2):
            if path is not None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        if self._reg_before is None:
            try:
                self._reg.unlink()
            except FileNotFoundError:
                pass
        else:
            self._reg.write_bytes(self._reg_before)

    def tearDown(self):
        for _, sess in self.be._session_items():        # a worker returns when it wakes to a dead session
            with sess.lock:
                sess.dead = True
            sess.kick.set()
        self.fake.close()                               # the pump returns when its client reads as closed
        with km._codex_lock:
            km._codex_backend = self._saved_singleton
        deadline = time.time() + 5
        while time.time() < deadline and any(t.is_alive() for t in self._threads()):
            time.sleep(0.02)

    def _threads(self):
        out = []
        for _, sess in self.be._session_items():
            w = getattr(sess, "worker", None)
            if w is not None:
                out.append(w)
        return out

    def test_a_typed_compact_reaches_no_client_call_and_no_queue(self):
        sid = self.be.spawn("web", "/TESTDIR", sid=SID2)
        self.assertEqual(sid, SID2)
        self.assertIs(km.Sessions.backend_for(SID2), self.be, "the real singleton owns the row")
        self.assertTrue(km._drive({"type": "sendMessage", "id": SID2, "text": "/compact", "qid": QID}, self.client))
        self.assertEqual(self.fake.called("turn_start"), [], "no turn opened with the word")
        self.assertEqual(self.fake.called("turn_steer"), [], "no steer with the word")
        self.assertEqual(self.be.live_atoms(SID2), [], "no echo minted: the backend's send never ran")
        self.assertEqual(self.be._session(SID2).queue, [], "the durable queue took nothing")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((self.sent[0]["type"], self.sent[0]["sid"], self.sent[0]["qid"]), ("warn", SID2, QID))
        self.assertIn("has no /compact", self.sent[0]["text"])
        self.assertEqual(len(self.fake.called("thread_start")), 1, "no thread minted either: a refusal is not a clear")
        self.assertIn("web: ", km._SYNC_NOTICES[-1]["text"], "the bell row names the session")


if __name__ == "__main__":
    unittest.main()
