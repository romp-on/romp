#!/usr/bin/env python3
"""A typed /clear (or /new) on a Codex session starts a fresh conversation for the SAME session (2026-09-19).

The guard in front of the setter route (tests/test_codex_slash_guard.py) refuses a slash command a Codex session
cannot take; this is the first head registered in its handler table. The route hands "/clear" and "/new" to
SessionBackend.clear through the drive-op FIFO — idle it runs now, mid-turn (or behind a queue, compacting, under
an account hold) it parks as a visible "/clear" chip and the pusher's drain fires it at the turn's end — and a
refusal is said on the delivering socket with the session and the press named, filed for POST /send, and kept on
the bell. The drain continues past a clear (it opens no turn: a message typed after the /clear lands on the fresh
conversation in press order), leaves a "busy" head in place with no clock and no in-flight record, and routes a
("command", "/clear") op parked before the native clear existed to the same verb. The read side needs nothing new:
the new thread is a new transcript file whose first record is a ROOT, so the episode boundary tick records the
boundary, settles the open cards, rings the bell and the chat inserts the "Conversation cleared" head card whose
lazy body resolves the old file as a sibling in the same projects directory (the CodexClearEpisode class runs the
real tick over synthetic files and pins that chain).

Fixtures are synthetic: private placeholder sids (parked ops, notices, goal stores and episode logs key by sid,
and another module's journal must never land on these), an invented copy id in the kernel's echo form, the demo
world's session names, invented prompts. The stub below is the kernel's view of a backend with the verb: clear(sid)
answers a scripted string and records; send/set_effort record; busy answers None (the drain's _after_turn_opening
reads it); _session/owns/live_sessions answer for the module's sid the way _session_backend's durable-row read does."""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
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
km = load_source("romp_kernel_codex_clear", os.path.join(BIN, "romp-kernel"))
jd = km.jd
# Per-session hosts are on by default: a state root without this file starts a real host for any session a
# backend connects. Nothing here connects one; the rule is unconditional for a module that mints its own root.
_HOSTS = Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts")
_HOSTS.parent.mkdir(parents=True, exist_ok=True)
_HOSTS.write_text("off")

SID = "11111111-2222-4333-8444-0c1e0a0e0001"      # private to this module: the route and drain classes
SID_EPI = "11111111-2222-4333-8444-0c1e0a0e0002"  # the episode-chain class (its own goal store and episode log)
SID_REAL = "11111111-2222-4333-8444-0c1e0a0e0003" # the real-backend class (a registry row is written for it)
SID_REAL2 = "11111111-2222-4333-8444-0c1e0a0e0004"
QID = "echo:" + "cd" * 16                          # the press-minted copy id, in the kernel's echo form
NOW = 1750000000


def until(fn, timeout=5.0, step=0.01):
    dl = time.time() + timeout
    while time.time() < dl:
        if fn():
            return True
        time.sleep(step)
    return False


class _Backend:
    """The kernel's view of a backend that has the verb."""
    def __init__(self):
        self.calls = []
        self.texts = []      # the words each clear was asked with (2026-09-19)
        self.answer = ""

    def clear(self, sid, text="/clear"):
        self.calls.append(("clear", sid))
        self.texts.append(text)
        return self.answer

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True

    def set_effort(self, sid, v):
        self.calls.append(("effort", v))
        return True

    def busy(self, sid):
        return None

    def _session(self, sid):
        return {"sid": sid} if sid == SID else None

    def owns(self, sid):
        return sid == SID

    def live_sessions(self):
        return {}


def _forget(sid):
    """Drop the sid's parked ops from memory AND the disk mirror, and every per-sid latch these paths touch."""
    km._pending_ops.pop(sid, None)
    km._save_pending_ops()
    km._moving.discard(sid)
    km._drain_hold.pop(sid, None)
    km._inflight_ops.pop(sid, None)
    km._model_switch_pending.pop(sid, None)


class _Base(unittest.TestCase):
    """The stub is THE Codex backend (`be is _codex()` is the identity the route's arm tests) and owns SID; the
    drive-op gate is a counter answering `verdict`, the drain's gates are quiet, the chat broadcast and the cue
    stamp are recorders, and the bell ring's length is noted so a test can assert its own row."""
    def setUp(self):
        self.be = _Backend()
        self.sent, self.broadcast, self.gate_calls = [], [], []
        self.verdict = False
        self.client = {"send": lambda t: self.sent.append(json.loads(t))}
        _forget(SID)
        self.addCleanup(_forget, SID)
        stubs = {
            "_codex": lambda: self.be,
            "_sdk": lambda: None,                        # no SdkBackend is built for a lookup that ends at Codex
            "_ops_gate": lambda sid: (self.gate_calls.append(str(sid)), self.verdict)[1],
            "_compacting_now": lambda sid, **k: False,
            "_working_now": lambda sid: False,
            "_limit_hold": lambda sid: None,             # the account gate is its own axis
            "_kernel_knows": lambda sid: True,
            "_push_soon": lambda *a, **k: None,
            "_host_for_sid": lambda sid: None,
            "_send_to_app": lambda app, msg: self.broadcast.append((app, msg)),
            "_mark_compacting": lambda sid: None,
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

    def _warns(self):
        return [msg for app, msg in self.broadcast if app == "chat" and msg.get("type") == "warn"]


class CodexClearRoute(_Base):
    def test_a_codex_clear_takes_the_route_never_the_send(self):
        for text in ("/clear", "/new", "/clear now"):
            self.be.calls.clear(); self.sent.clear(); self.gate_calls.clear()
            state = {}
            self.assertTrue(km._route_meta_command(self.be, SID, text, self.client, state=state, qid=QID), text)
            self.assertEqual(self.be.calls, [("clear", SID)], text)
            self.assertEqual(state, {"queued": False}, text)
            self.assertEqual(self.sent, [], "a clear that ran says nothing on the socket: the chip is the acknowledgment")
            self.assertEqual(self.gate_calls, [SID], "ONE drive-op gate read, the /effort arm's cost")
            self.assertNotIn(SID, km._pending_ops)
        self.be.calls.clear()
        # not this head: "/clearx" is another slash command the guard refuses; "please /clear" is prose the caller sends
        state = {}
        self.assertTrue(km._route_meta_command(self.be, SID, "/clearx", self.client, state=state))
        self.assertIn("has no /clearx", state["refused"])
        state = {}
        self.assertFalse(km._route_meta_command(self.be, SID, "please /clear", self.client, state=state))
        self.assertEqual(state, {})
        self.assertEqual(self.be.calls, [], "neither reached the verb")
        # a message that merely OPENS with the head (a paste, a Shift-Enter slip): slash-shaped, since the predicate's
        # trailing whitespace admits a newline, but not the command alone — the composer's confirm never gated it and
        # the verb takes no text, so the lines after the head would reach no one. Refused in words that name what the
        # message must be, never a clear (review find, 2026-09-19).
        for text in ("/clear\nand more", "/new\nand more"):
            self.sent.clear()
            state = {}
            self.assertTrue(km._route_meta_command(self.be, SID, text, self.client, state=state, qid=QID), repr(text))
            self.assertEqual(self.be.calls, [], "the verb never ran for %r" % text)
            self.assertNotIn("queued", state, "nothing parked either")
            self.assertIn(text.split()[0], state["refused"], "the refusal names the command")
            self.assertIn("must be the whole message", state["refused"])
            self.assertEqual([(f["type"], f["sid"], f["qid"]) for f in self.sent], [("warn", SID, QID)],
                             "one warn on the delivering socket, with the press named, so the chat retires its bubble")
            self.assertNotIn(SID, km._pending_ops)
        # Claude's /clear keeps its path: with another object as the Codex backend, this one is not it
        other = _Backend()
        with mock.patch.object(km, "_codex", lambda: other):
            self.assertFalse(km._route_meta_command(self.be, SID, "/clear", self.client, state={}))
        self.assertEqual((self.be.calls, other.calls), ([], []), "the CLI executes the text; no verb on either")

    def test_a_busy_or_gated_clear_parks_as_a_clear_op(self):
        self.verdict = True                               # mid-turn, behind a queue, compacting or under a hold
        state = {}
        self.assertTrue(km._route_meta_command(self.be, SID, "/clear", self.client, state=state, qid=QID))
        self.assertEqual(km._pending_ops[SID], [("clear", "/clear")], "a visible '/clear' chip in press order")
        self.assertIs(state["queued"], True)
        self.assertEqual(self.be.calls, [], "parked: the drain asks the backend at the turn's end")
        self.assertEqual(self.sent, [])
        _forget(SID)
        self.verdict = False
        self.be.answer = "busy"                           # the worker's lock sliver after the turn's end
        state = {}
        self.assertTrue(km._route_meta_command(self.be, SID, "/new", self.client, state=state))
        self.assertEqual(self.be.calls, [("clear", SID)])
        self.assertEqual(km._pending_ops[SID], [("clear", "/new")], "parked after the answer; the drain retries")
        self.assertIs(state["queued"], True)
        self.assertEqual(self.sent, [], "'busy' is an internal answer, never shown")
        self.assertEqual(km._parked_md(("clear",)), "/clear", "the queued chip's body, and the cancel handshake's")

    def test_a_typed_new_or_clear_with_words_carries_them_to_the_verb_and_the_chip(self):
        # The composer retires its optimistic bubble by the exact text it sent, and the chip is the backend's
        # acknowledgment: a typed /new acknowledged by a literal "/clear" chip left the sending bubble standing, and
        # a /new parked mid-turn drew a queued "/clear" chip beside it (review find, 2026-09-19). The words as typed
        # ride the verb and the parked op; a one-slot op from a mirror written before the slot existed still renders.
        for text in ("/new", "/clear now", "  /new  "):
            self.be.texts.clear()
            self.assertTrue(km._route_meta_command(self.be, SID, text, self.client, state={}))
            self.assertEqual(self.be.texts, [text.strip()], repr(text))
        self.verdict = True
        self.assertTrue(km._route_meta_command(self.be, SID, "/new", self.client, state={}, qid=QID))
        self.assertEqual(km._pending_ops[SID], [("clear", "/new")], "the parked op carries the words")
        self.assertEqual(km._parked_md(km._pending_ops[SID][0]), "/new", "the queued chip shows what was typed")
        self.assertEqual(km._parked_md(("clear",)), "/clear", "an older mirror's one-slot op still renders")
        self.assertIsNone(km._cancel_parked(SID, 0, "/new"), "the chip's cancel handshake reads the same words")
        self.assertNotIn(SID, km._pending_ops)
        self.verdict = False
        self.be.answer = "busy"
        self.be.texts.clear()
        self.assertTrue(km._route_meta_command(self.be, SID, "/clear now", self.client, state={}))
        self.assertEqual((self.be.texts, km._pending_ops[SID]), (["/clear now"], [("clear", "/clear now")]),
                         "a 'busy' answer parks the same words")

    def test_a_refused_clear_is_said_and_filed(self):
        why = "this session has ended — revive it first"
        self.be.answer = why
        state = {}
        self.assertTrue(km._route_meta_command(self.be, SID, "/clear", self.client, state=state, qid=QID))
        self.assertEqual(self.sent, [{"type": "warn", "text": why, "sid": SID, "qid": QID}],
                         "the frame names the session and the press: the chat retires the bubble it drew and puts the words back")
        self.assertEqual(state, {"refused_clear": why, "queued": False})
        row = self._last_notice()
        self.assertEqual(row["kind"], "refused")
        self.assertIn(why, row["text"])
        self.assertNotIn(SID, km._pending_ops, "a refusal is not an op")
        # POST /send and `romp send` answer the backend's own words, never the unowned sentence
        self.assertEqual(km._deliver_text(SID, "/clear"), (False, why, False))
        warns = self._warns()
        self.assertEqual(len(warns), 1, "no socket carried that one: the chat panes hear it as a broadcast")
        self.assertEqual((warns[0]["id"], warns[0]["sid"], warns[0]["text"]), (SID, SID, why))
        self.assertNotIn("qid", warns[0], "a POST route's press has no copy id")

    def test_the_palette_lists_the_two_heads(self):
        names = [c["name"] for c in km._CODEX_COMMANDS]
        self.assertEqual(names[:2], ["clear", "new"], "the composer's '/' list offers what the route now takes")
        self.assertEqual(set(km._CODEX_SLASH_HANDLERS), {"/clear", "/new"})
        self.assertIs(km._CODEX_SLASH_HANDLERS["/clear"], km._CODEX_SLASH_HANDLERS["/new"], "one operation, two words")


class CodexClearDrain(_Base):
    def test_the_drain_fires_a_parked_clear_then_continues_to_the_message_behind_it(self):
        km._pending_ops[SID] = [("clear",), ("send", "then this", None)]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("clear", SID), ("send", "then this")],
                         "a clear opens no turn: the message behind it lands on the fresh conversation, in press order")
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn(SID, km._inflight_ops)
        self.assertNotIn(SID, km._drain_hold)
        self.assertEqual(self._warns(), [])

    def test_a_busy_answer_leaves_the_head_with_no_clock_and_no_inflight_record(self):
        self.be.answer = "busy"
        km._pending_ops[SID] = [("clear",), ("send", "then this", None)]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("clear", SID)], "the pass ends at the busy clear: nothing behind it fires")
        self.assertEqual(km._pending_ops[SID], [("clear",), ("send", "then this", None)], "the head stays for the next cycle")
        self.assertNotIn(SID, km._drain_hold, "no clock: the turn-end poke's cycle (or the backstop) is the retry")
        self.assertNotIn(SID, km._inflight_ops, "nothing was handed over, so nothing is recorded…")
        self.assertIsNone(km._cancel_parked(SID, 0, "/clear"), "…and the chip's cancel still takes it")
        self.assertEqual(km._pending_ops[SID], [("send", "then this", None)])
        self.assertEqual(self._warns(), [], "'busy' is never shown")

    def test_a_refused_clear_in_the_drain_warns_and_pops(self):
        why = "Couldn't start a fresh conversation: thread/start failed"
        self.be.answer = why
        km._pending_ops[SID] = [("clear",), ("effort", "high")]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("clear", SID), ("effort", "high")], "popped; the setting behind it still delivers")
        self.assertNotIn(SID, km._pending_ops)
        self.assertEqual(self._warns(), [{"type": "warn", "id": SID, "sid": SID, "text": why}])
        self.assertEqual(self._last_notice()["kind"], "refused")

    def test_a_clear_command_parked_before_the_upgrade_runs_the_clear(self):
        # pending-ops.json survives a restart: a ("command", "/clear") op a Codex session parked before the native
        # clear existed is a clear, not the guard's refusal; a refusal of it carries the parked copy's id
        km._pending_ops[SID] = [("command", "/new", "human", QID, True), ("send", "after it", None)]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("clear", SID), ("send", "after it")])
        self.assertNotIn(SID, km._pending_ops)
        self.assertEqual(self._warns(), [])
        self.be.answer = "this session has ended — revive it first"
        km._pending_ops[SID] = [("command", "/clear", "human", QID, True)]
        km._save_pending_ops()
        km._apply_pending_ops()
        warns = self._warns()
        self.assertEqual(len(warns), 1)
        self.assertEqual((warns[0]["sid"], warns[0]["qid"], warns[0]["text"]), (SID, QID, self.be.answer))
        self.assertNotIn(SID, km._pending_ops)

    def test_the_drain_hands_the_parked_words_to_the_verb(self):
        # a parked /new must not land as a /clear chip (review find, 2026-09-19): the drain's arm passes the words the
        # op was parked with — a ("clear", text) op, a ("command", "/new") op parked before the upgrade, and a one-slot
        # ("clear",) op from an older mirror, which takes the contract's default
        km._pending_ops[SID] = [("clear", "/new"), ("command", "/clear now", "human", QID, True), ("clear",)]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(self.be.texts, ["/new", "/clear now", "/clear"])
        self.assertNotIn(SID, km._pending_ops)
        self.assertEqual(self._warns(), [])

    def test_the_drain_and_the_fold_read_one_clear_predicate(self):
        # One rule for what a parked op IS (review find, 2026-09-19): the drain ran a ("command", "/new") op as a clear
        # while the chat's fold keyed on the kind "clear" or the literal "/clear", so that op's queued chip drew beside
        # the live "Clearing conversation…" element. _parked_clear_op is the predicate both read: a ("clear", …) op of
        # any length, or a one-line ("command", …) op whose head is a registered clear head, on the Codex backend.
        be = self.be
        for op in (("clear",), ("clear", "/new"), ("command", "/clear", "human", QID, True),
                   ("command", "/new", "human", QID, True), ("command", "  /clear now  ", "human", QID, True)):
            self.assertTrue(km._parked_clear_op(op, be), op)
        for op in (("command", "/new\nand more", "human", QID, True), ("command", "/compact", "human", QID, True),
                   ("command", "/clearx", "human", QID, True), ("command", "", "human", QID, True),
                   ("send", "/new", None), ("effort", "high"), ("command",)):
            self.assertFalse(km._parked_clear_op(op, be), op)
        other = _Backend()      # not the Codex singleton: a ("command", "/clear") there is that backend's own command
        self.assertFalse(km._parked_clear_op(("command", "/new", "human", QID, True), other))
        self.assertFalse(km._parked_clear_op(("command", "/clear", "human", QID, True), None))
        self.assertTrue(km._parked_clear_op(("clear", "/new"), other), "a clear op is a clear on any backend")
        # the drain's arm consults it for the op at its head, with the session's backend
        seen = []
        real = km._parked_clear_op
        with mock.patch.object(km, "_parked_clear_op", lambda op, b: (seen.append((op, b)), real(op, b))[1]):
            km._pending_ops[SID] = [("command", "/new", "human", QID, True)]
            km._save_pending_ops()
            km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("clear", SID)])
        self.assertEqual(self.be.texts, ["/new"])
        self.assertIn((("command", "/new", "human", QID, True), be), seen, "the drain read the predicate for its head")
        self.assertNotIn(SID, km._pending_ops)
        self.assertEqual(self._warns(), [])

    def test_a_command_op_with_a_second_line_is_refused_in_the_drain_never_cleared(self):
        # The route's whole-message rule holds in the drain too (review find, 2026-09-19): a pre-upgrade ("command", …)
        # op whose text has a second line under the head is not a clear (the lines after it would reach no one) — it
        # takes the guard's refusal ONCE, with the press named so the chat retires its bubble, and the message parked
        # behind it still delivers; without the rule the op ran as a clear with its second line dropped.
        for head in ("/clear", "/new"):
            self.broadcast.clear()
            self.be.calls.clear()
            km._pending_ops[SID] = [("command", head + "\nand more", "human", QID, True), ("send", "after it", None)]
            km._save_pending_ops()
            km._apply_pending_ops()
            self.assertEqual(self.be.calls, [("send", "after it")],
                             "%s: the verb never ran; the send behind it delivered" % head)
            warns = self._warns()
            self.assertEqual(len(warns), 1, head)
            self.assertEqual((warns[0]["sid"], warns[0]["qid"]), (SID, QID), "one warn, with the press named")
            self.assertIn(head, warns[0]["text"])
            self.assertIn("must be the whole message", warns[0]["text"])
            self.assertNotIn(SID, km._pending_ops, "nothing parked")
            self.assertEqual(self._last_notice()["kind"], "refused")

    def test_a_backend_without_the_verb_gets_the_contracts_refusal(self):
        class Bare:
            def busy(self, sid):
                return None

            def live_sessions(self):
                return {}
        bare = Bare()
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: bare)):
            km._pending_ops[SID] = [("clear",)]
            km._save_pending_ops()
            km._apply_pending_ops()
        warns = self._warns()
        self.assertEqual(len(warns), 1)
        self.assertIn("fresh conversation", warns[0]["text"])
        self.assertNotIn("backend", warns[0]["text"], "a toast in the user's terms")
        self.assertNotIn(SID, km._pending_ops, "popped, never replayed forever")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _urec(t, uuid, text, tid, parent=None, cwd=""):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": tid,
            "cwd": cwd, "version": "codex", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _arec(t, uuid, text, tid, parent, cwd=""):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": tid,
            "cwd": cwd, "version": "codex",
            "message": {"role": "assistant", "model": "gpt-5-test", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


class CodexClearEpisode(unittest.TestCase):
    """The read side after a Codex clear, over the REAL episode tick and the real builders: two materialized
    files under one projects directory (the registry row names the new one), a goal store with one open top.
    Green with or without the backend change — it pins the design's claim that the thread swap is all the
    chain needs. Its own state root, rebound through jd._rebind_state (GOALDIR, EPIDIR, CODEXDIR and the rest
    follow), and no backend over it: _codex answers None, so Sessions.backend_for is the unowned route."""
    OLD_TID = "aaaaaaaa-1111-4222-8333-000000000001"
    NEW_TID = "aaaaaaaa-1111-4222-8333-000000000002"
    TOP = "wire the notes-api auth"

    def setUp(self):
        self._saved_state, self._saved_projects = jd.STATE, jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "claude-projects"       # no Claude transcripts here; Codex rows come from the registry
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        (jd.STATE / "session-hosts").write_text("off")
        self.cwd = os.path.realpath(os.path.join(self._td, "notes-api"))
        os.makedirs(self.cwd, exist_ok=True)
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / SID_EPI).write_text("cx\t%s\n" % self.cwd)
        jd.CODEXDIR.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text(json.dumps({SID_EPI: {
            "tid": self.NEW_TID, "name": "cx", "cwd": self.cwd, "dead": False, "model": "", "effort": "",
            "mode": "sandboxed", "queue": [], "note": "", "color": "", "launchError": None}}))
        import re
        enc = re.sub(r"[^A-Za-z0-9]", "-", self.cwd)
        self.proj = jd.CODEXDIR / "projects" / enc
        self.old_path = self.proj / (self.OLD_TID + ".jsonl")
        self.new_path = self.proj / (self.NEW_TID + ".jsonl")
        _write_jsonl(self.old_path, [
            _urec(NOW - 3600, "u1", "set up the api server", self.OLD_TID, cwd=self.cwd),
            _arec(NOW - 3590, "a1", "done - the notes-api server runs on port 8080", self.OLD_TID, "u1", cwd=self.cwd),
        ])
        _write_jsonl(self.new_path, [
            _urec(NOW - 50, "n1", "hello again", self.NEW_TID, cwd=self.cwd),
            _arec(NOW - 40, "n2", "fresh start - what next?", self.NEW_TID, "n1", cwd=self.cwd),
        ])
        gid = "%s:g1" % SID_EPI
        jd.save_goals(SID_EPI, {"rompUuid": SID_EPI, "seq": 1, "placements": {},
                                "nodes": {gid: {"id": gid, "text": self.TOP, "parentId": None, "nodeComplete": False,
                                                "blocked": False, "cleared": False, "trail": [], "t": 1, "mt": 1, "log": []}},
                                "status": {gid: "working"}})
        self.gid = gid
        for name, stub in {"_codex": lambda: None, "_sdk": lambda: None}.items():
            p = mock.patch.object(km, name, stub)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        jd.PROJECTS = self._saved_projects
        shutil.rmtree(self._td, ignore_errors=True)

    def test_the_new_files_root_head_is_the_boundary_and_the_old_file_renders_as_the_cleared_episode(self):
        self.assertIs(km.Sessions.backend_for(SID_EPI), km._UNOWNED, "no backend over this root")
        km._episode_boundary_check(SID_EPI, str(self.old_path), NOW)
        self.assertEqual([(r["head"], r["fsid"]) for r in jd.episode_rows(SID_EPI)], [("u1", self.OLD_TID)],
                         "the old file earned the seed row at its own first record")
        self.assertEqual(jd.load_goals(SID_EPI)["nodes"][self.gid].get("cleared"), False, "a seed settles nothing")
        km._episode_boundary_check(SID_EPI, str(self.new_path), NOW)
        rows = jd.episode_rows(SID_EPI)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[-1]["head"], rows[-1]["fsid"]), ("n1", self.NEW_TID), "the fresh thread's root head is the boundary")
        settles = jd.episode_settles(SID_EPI)
        self.assertEqual([d["text"] for d in settles["n1"]["settled"]], [self.TOP], "the open top settled with its conversation")
        cleared = [json.loads(l) for l in (jd.STATE / "cleared.jsonl").read_text().splitlines() if l.strip()]
        self.assertIn(self.gid, [r["id"] for r in cleared])
        self.assertTrue(jd.load_goals(SID_EPI)["nodes"][self.gid].get("cleared"))
        m = km.build_session(SID_EPI, NOW)
        self.assertIsNotNone(m, "discovery lists the session at the registry's current tid")
        head = m["events"][0]
        self.assertEqual(head["kind"], "clear", "the 'Conversation cleared' head card leads the fresh conversation")
        self.assertEqual(head["dropped"], [self.TOP])
        ep = km.build_episode(SID_EPI, NOW)
        self.assertNotIn("error", ep, "the old file is found as a sibling in the same projects directory: %r" % ep.get("error"))
        joined = " ".join(str(e.get("md") or "") for e in ep["events"])
        self.assertIn("notes-api server runs", joined, "the cleared conversation renders from the old file")
        self.assertNotIn("hello again", joined, "…and only it")
        notices = km._boundary_clear_notices([{"sid": SID_EPI, "name": "cx"}])
        self.assertEqual(notices[0]["titles"], [self.TOP], "the bell rings once with the dropped card")

    def test_the_twin_renders_in_the_fresh_conversation_until_the_boundary_tick_then_closes_the_episode(self):
        # The chip's durable twin is stamped at the clear, between the old file's last record and the fresh
        # conversation's first (a real session's order). Until the boundary tick the live build has no floor past
        # it, so the twin renders in the fresh conversation; the tick records the boundary at the new head's time,
        # the live build drops every note at or before it, and the twin renders only as the last gesture row of
        # the old file's episode (review find, 2026-09-19).
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "states" / (SID_EPI + ".jsonl")).write_text(json.dumps({"t": NOW - 100, "cmdGesture": "/clear"}) + "\n")
        km._episode_boundary_check(SID_EPI, str(self.old_path), NOW)          # the seed: one row, no floor
        kinds = [e.get("kind") for e in km.build_session(SID_EPI, NOW)["events"]]
        self.assertIn("cmdGesture", kinds, "before the tick the twin renders in the fresh conversation: %r" % kinds)
        self.assertNotIn("clear", kinds)
        km._episode_boundary_check(SID_EPI, str(self.new_path), NOW)          # the boundary, at the new head's time
        kinds = [e.get("kind") for e in km.build_session(SID_EPI, NOW)["events"]]
        self.assertEqual(kinds[0], "clear", "the head card leads the fresh conversation: %r" % kinds)
        self.assertNotIn("cmdGesture", kinds, "the twin is under the floor: no /clear chip in the fresh conversation")
        ep = km.build_episode(SID_EPI, NOW)
        self.assertNotIn("error", ep, ep.get("error"))
        last = ep["events"][-1]
        self.assertEqual((last.get("kind"), last.get("cmd")), ("cmdGesture", "/clear"),
                         "the episode ends with the /clear row: %r" % [e.get("kind") for e in ep["events"]])


class RealBackendClear(unittest.TestCase):
    """The route and the drain over the REAL CodexBackend with the codex-backend module's own scripted client,
    installed as the kernel's singleton so `be is _codex()` holds against the real object and Sessions.backend_for
    resolves through it. The per-session gates are real except the account and compaction ones (quiet)."""
    @classmethod
    def setUpClass(cls):
        saved = os.environ["XDG_STATE_HOME"]
        try:
            cls.cbt = load_source("romp_codex_backend_tests_for_clear", os.path.join(HERE, "test_codex_backend.py"))
        finally:
            os.environ["XDG_STATE_HOME"] = saved     # that module floors its own root at import; this one keeps its own

    def setUp(self):
        self.fake = self.cbt.FakeClient()
        # Over km.jd.STATE read AT SETUP, with a scrub after (2026-09-19): load_source re-executes judge.py into
        # ONE shared module object, so under a suite jd.STATE is whichever module bound it last, and the kernel's
        # read side (_path_of, discovery) resolves a Codex transcript through that shared module at run time. The
        # backend must write where that read side reads, and without the scrub its registry rows, names/ entries
        # and both threads' transcripts stayed in a root two other modules' discovery walks read (two extra
        # discovered sessions there). Green alone, red only under the whole suite.
        self.root = km.jd.STATE
        self.be = self.cbt.cb.CodexBackend(self.root, client_factory=lambda: self.fake)
        with km._codex_lock:
            self._saved_singleton = km._codex_backend
            km._codex_backend = self.be
        self.sent = []
        self.client = {"send": lambda t: self.sent.append(json.loads(t))}
        for name, stub in {"_sdk": lambda: None, "_push_soon": lambda *a, **k: None,
                           "_compacting_now": lambda sid, **k: False, "_limit_hold": lambda sid: None}.items():
            p = mock.patch.object(km, name, stub)
            p.start()
            self.addCleanup(p.stop)
        for sid in (SID_REAL, SID_REAL2):
            _forget(sid)
            self.addCleanup(_forget, sid)
        self._reg = self.root / "codex" / "registry.json"
        self._reg_before = self._reg.read_bytes() if self._reg.exists() else None
        self._lock = self.root / "codex" / "registry.lock"
        self._lock_before = self._lock.exists()
        projects = self.root / "codex" / "projects"
        self._files_before = set(projects.rglob("*")) if projects.exists() else set()
        self.addCleanup(self._scrub)                    # after tearDown (cleanup order): no worker writes then

    def _scrub(self):
        """Leave the root as found: every transcript the test's threads wrote (a clear swaps the tid, so the old
        thread's file is no longer reachable through the row), the names/ entries, the registry rows, the chip's
        durable twin (states/<sid>.jsonl, the clear's cmdGesture record) for both sids, and the registry's lock
        sidecar when set-up found none (the save creates it)."""
        projects = self.root / "codex" / "projects"
        if projects.exists():
            for path in sorted(set(projects.rglob("*")) - self._files_before, key=lambda q: -len(q.parts)):
                try:
                    path.unlink() if path.is_file() else path.rmdir()
                except OSError:
                    pass
        for sid in (SID_REAL, SID_REAL2):
            for path in (self.root / "names" / sid, self.root / "states" / (sid + ".jsonl")):
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
        if not self._lock_before:
            try:
                self._lock.unlink()
            except FileNotFoundError:
                pass

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

    def _registry_tid(self, sid):
        return json.loads(self.be._reg_path().read_text())[sid]["tid"]

    # each test its own cwd: the kernel's root is shared across tests and every fresh scripted client mints
    # "T-1" again, so two sessions under one cwd would share a transcript file and seed each other's dedup
    def test_a_typed_clear_swaps_the_thread_and_leaves_the_chip_in_the_fresh_conversation(self):
        sid = self.be.spawn("web", "/TESTDIR-typed", sid=SID_REAL)
        self.assertIs(km.Sessions.backend_for(sid), self.be, "the real singleton owns the row")
        self.assertTrue(self.be.send(sid, "first synthetic turn"))
        self.assertTrue(self.cbt._lock_free(self.be, sid))
        state = {}
        self.assertTrue(km._route_meta_command(self.be, sid, "/clear", self.client, state=state, qid=QID))
        self.assertEqual(state, {"queued": False})
        self.assertEqual(self.sent, [], "no warn: the clear ran")
        self.assertEqual(self.fake.called("thread_start")[-1][1]["sessionStartSource"], "clear")
        self.assertEqual(self._registry_tid(sid), "T-2")
        self.assertIs(km._clearing_now(sid), False, "the bracket dropped when the tid became durable")
        atoms = self.be.live_atoms(sid)
        self.assertEqual([(a["_echo_text"], a["command"]) for a in atoms], [("/clear", "/clear")])
        self.assertEqual(self.fake.called("turn_start")[-1][1], "T-1", "no turn was started by the clear itself")
        self.assertEqual(self.be._session(sid).queue, [], "the durable queue took nothing")
        m = km.build_session(sid, int(time.time()))
        self.assertIsNotNone(m, "discovery follows the registry to the fresh file")
        kinds = [e.get("kind") for e in m["events"]]
        users = [e for e in m["events"] if e.get("kind") == "user"]
        self.assertEqual([e["md"] for e in users], ["/clear"],
                         "the fresh conversation shows the acknowledging chip and nothing of the old one: %r" % kinds)
        self.assertNotIn("clearing", kinds)
        self.assertNotIn("queued", kinds)
        # The merge's OWN verdict, not the backend's row (review find, 2026-09-19): the chip and lane read
        # _session_chip over the live-merged turns, and a command chip must never reopen the fresh conversation's
        # turn — on a thread with no turn yet the merge synthesizes the live turn, and its `ended` IS the verdict.
        # Sessions.live below is the backend half, which reads "waiting" whether or not the merge agrees.
        self.assertEqual(m["status"]["state"], "ready", "the chat's chip: a command chip is never live work")
        merged = km._merge_live_atoms(km._parse_cached(km._path_of(sid)), sid)
        self.assertTrue(merged["turns"], "the merge synthesized the fresh conversation's live turn for the chip")
        self.assertTrue(merged["turns"][-1]["ended"], "…and it reads ended: %r" % merged["turns"][-1])
        self.assertFalse(km._session_working(merged["turns"]), "no working cue from the merged turns")
        self.assertEqual(km.Sessions.live()[sid]["state"], "waiting", "the backend's row agrees")

    def test_a_typed_new_leaves_a_new_chip_and_a_parked_one_folds_under_the_clearing_indicator(self):
        # The kernel road for the composer's bubble (review find, 2026-09-19): the words as typed reach the chip, so
        # the bubble retires by exact text; and the fold that hides the queued chip behind the live "Clearing
        # conversation…" element keys on the op's KIND, since a parked native clear renders as the words it was
        # typed with, not the literal "/clear" the fold used to look for.
        sid = self.be.spawn("web", "/TESTDIR-new", sid=SID_REAL)
        self.assertTrue(self.be.send(sid, "first synthetic turn"))
        self.assertTrue(self.cbt._lock_free(self.be, sid))
        state = {}
        self.assertTrue(km._route_meta_command(self.be, sid, "/new", self.client, state=state, qid=QID))
        self.assertEqual(state, {"queued": False})
        self.assertEqual([(a["_echo_text"], a["command"]) for a in self.be.live_atoms(sid)], [("/new", "/new")])
        m = km.build_session(sid, int(time.time()))
        self.assertEqual([e["md"] for e in m["events"] if e.get("kind") == "user"], ["/new"], "the chat shows the words typed")
        km._pending_ops[sid] = [("clear", "/new")]        # a second one, parked mid-turn and now with the backend
        km._save_pending_ops()
        m = km.build_session(sid, int(time.time()))
        queued = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual([x["md"] for x in queued[0]["texts"]], ["/new"], "the queued chip shows what was typed")
        with mock.patch.object(km, "_clearing_now", lambda s: str(s) == sid):
            m = km.build_session(sid, int(time.time()))
        kinds = [e.get("kind") for e in m["events"]]
        self.assertIn("clearing", kinds, "the live element represents the running clear")
        self.assertNotIn("queued", kinds, "…and the parked chip folds under it, keyed on the op's kind: %r" % kinds)
        # A ("command", "/new") op reaches the drain by roads that skip the route into _send_or_park (a pending-ops.json
        # written before the heads registered, a notice card's plain action, a follow-up with no item id), and the drain
        # runs it as a clear; keyed on the kind "clear" or the literal "/clear", the fold let this chip draw beside the
        # live element (review find, 2026-09-19). The fold now reads the drain's own predicate, _parked_clear_op.
        km._pending_ops[sid] = [("command", "/new", "human", QID, True)]
        km._save_pending_ops()
        m = km.build_session(sid, int(time.time()))
        queued = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual([x["md"] for x in queued[0]["texts"]], ["/new"], "the command op renders as typed")
        with mock.patch.object(km, "_clearing_now", lambda s: str(s) == sid):
            m = km.build_session(sid, int(time.time()))
        kinds = [e.get("kind") for e in m["events"]]
        self.assertIn("clearing", kinds)
        self.assertNotIn("queued", kinds, "a command op the drain runs as a clear folds by the drain's own rule: %r" % kinds)

    def test_the_cycle_that_drains_a_parked_clear_builds_the_fresh_file_from_the_hook_on(self):
        # The drain runs inside a pusher cycle whose memos (_live_scope.sessions, .paths) its own gates filled BEFORE
        # the verb; the verb's push hook (push_session -> _push_session_now) built on that thread, and the old file
        # rendered with the chip appended, in the hook's frame and in the cycle's post-drain build alike, the fresh
        # conversation appearing a cycle later (review find, 2026-09-19). The hook resets the row memo and drops the
        # sid's path under an open scope, so what it resolves during the verb is the row as it is now. The scope is
        # opened here the way _pusher_cycle opens it, and the hook records what its own frame resolved.
        sid = self.be.spawn("web", "/TESTDIR-cycle", sid=SID_REAL)
        self.assertTrue(self.be.send(sid, "first synthetic turn"))
        self.assertTrue(self.cbt._lock_free(self.be, sid))
        old_path = str(self.be.transcript_path(sid))
        resolved = []

        def hook(s):
            km._push_session_now(s)
            resolved.append(km._path_of(s))               # what a build in the hook's frame resolves
        self.be.push_session = hook
        km._pending_ops[sid] = [("clear", "/clear")]
        km._save_pending_ops()
        km._live_scope.snapshot = km._live_map()
        km._live_scope.paths, km._live_scope.sessions = {}, {}
        try:
            self.assertEqual(km._path_of(sid), old_path, "the gates fill the memo with the old row")
            km._apply_pending_ops()
            after = km._path_of(sid)                      # the cycle's post-drain build
        finally:
            km._live_scope.snapshot = km._live_scope.paths = km._live_scope.sessions = None
        new_path = str(self.be.transcript_path(sid))
        self.assertEqual(self._registry_tid(sid), "T-2")
        self.assertNotEqual(new_path, old_path)
        self.assertEqual(resolved[-1], new_path, "the hook's own frame resolved the fresh file during the verb: %r" % resolved)
        self.assertEqual(after, new_path, "and so does every build after it in the cycle")
        self.assertNotIn(sid, km._pending_ops)

    def test_a_clear_parked_mid_turn_fires_on_the_turn_end_poke_with_no_clock(self):
        sid = self.be.spawn("api", "/TESTDIR-parked", sid=SID_REAL2)
        self.cbt._hold_turn_open(self.fake)
        self.assertTrue(self.be.send(sid, "long"))
        self.assertTrue(until(lambda: self.be.live_sessions()[sid]["state"] == "working"), "the turn is open")
        drained = []
        real_poke = self.be.poke

        def poke():
            # the kernel's poke wakes the pusher, whose cycle runs the drain: run it HERE, on the poke itself
            if not self.be.busy(sid) and not drained:
                km._apply_pending_ops()
                drained.append(len(self.fake.called("thread_start")))
            real_poke()
        self.be.poke = poke
        state = {}
        self.assertTrue(km._route_meta_command(self.be, sid, "/clear", self.client, state=state, qid=QID))
        self.assertEqual(km._pending_ops[sid], [("clear", "/clear")], "mid-turn: parked, a visible chip")
        self.assertIs(state["queued"], True)
        self.assertEqual(len(self.fake.called("thread_start")), 1)
        m = km.build_session(sid, int(time.time()))
        queued = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual([x["md"] for x in queued[0]["texts"]], ["/clear"], "the chat shows the parked /clear")
        self.assertTrue(self.be.interrupt(sid))
        self.assertTrue(until(lambda: drained), "the turn-end poke ran the drain")
        self.assertEqual(drained, [2], "the parked clear fired on that poke: one thread minted, never 'busy'")
        self.assertEqual(self.fake.called("thread_start")[-1][1]["sessionStartSource"], "clear")
        self.assertEqual(self._registry_tid(sid), "T-2")
        self.assertNotIn(sid, km._pending_ops)
        self.assertNotIn(sid, km._drain_hold, "no clock was armed")
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
