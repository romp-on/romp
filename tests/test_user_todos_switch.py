#!/usr/bin/env python3
"""The USER TODOS feature switch (the user 2026-09-03): "Waiting on you" is switchable, DEFAULT OFF,
per install — STATE/user-todos-enabled.json = {"enabled": bool, "gt": ms}, the file-editing /
thinking-summaries idiom exactly (gesture-clock stand-down, settingStale reply, atomic write, loud
write failure). NOT user-todos.json: that file is the todo STORE (sid → records), and a settings
blob written there would replace the store on the next register — so the store guards its own shape.

Pinned here, kernel side:
- the helpers: absent / garbled / false all read OFF, the setter writes value + stamp, a stale
  gesture stands down loudly and keeps the stored value, an unstamped apply arms the store, a
  failed write is loud and applies nothing, _setting_kept_value words the stale reply, and the
  stamp report (_GT_STORES / _setting_stored_gt / settingsGt) carries the store like its siblings;
- the WS op setUserTodos (beside setThinkingSummaries), /version's top-level `userTodos`, and the
  PER-INSTALL contract: no propagation table names it (federation's KERNEL_SETTING is pinned on the
  node side, user-todos-switch.test.ts);
- what OFF does on each kernel surface, each loud: POST /usertodo and /usertodo/withdraw answer
  409 with a one-line reason and write nothing (before any remote forward); /usertodo/context
  answers enabled:false with an empty block; userTodoAnswer / userTodoDismiss warn and inject /
  stamp nothing; and the payloads every UI surface reads ship EMPTY — build_session's field +
  split-card event, build_feed's map — because _open_user_todos is the one gated read (the nudge
  stand-down and the escalation floor read it too). The store keeps every row; ON shows them again;
- the badge: _needs_you_count reads the switch too and, while OFF, is the pre-feature count (every
  real needs-input card, per card), so an install that never turned the feature on sees no change
  in the number its icon wears; ON, the widened rule applies (BadgeArithmetic, test_user_todos.py);
- the sig folds: a flip busts the owning session's chat cache and the feed cache with no store write;
- the boot notice: N open rows stored while OFF → one stderr line; ON, or zero rows → silence;
- the store-shape guard: a user-todos.json that is not sid → list (a settings blob, a JSON list,
  unparsable text) reads as EMPTY, says so once per file version, and every writer REFUSES to
  overwrite that version (fail loudly, never silently replace) until the file is fixed or removed;
  and no writer can MINT a key the reader rejects: _add_user_todo raises before any write and
  POST /usertodo answers 400 (before the switch and before any forward), so a store that already
  holds a row stays readable.

SYNTHETIC fixtures only: placeholder UUIDs, the notes-api demo world.
"""
import ast
import contextlib
import inspect
import io
import json
import os
import tempfile
import types
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
km = load_source("romp_kernel_utswitch", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "22222222-3333-4444-5555-666666666666"
NOW = 1781200000
T_OLD, T_NEW = 1_700_000_000_000, 1_700_000_060_000


class _Sandbox(unittest.TestCase):
    """Per-test STATE sandbox + cache reset — the _StoreSandbox idiom of test_user_todos.py. The
    switch file is ABSENT at the start of every test: that is the shipped default, OFF."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._user_todos_cache.clear()
        km._user_todos_bad.clear()
        km._user_todos_switch_bad.clear()
        km._UT_FLOOR_ARM.clear()                     # the floor's per-sid arm record is process state (tests/README)

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()
        km._user_todos_cache.clear()
        km._user_todos_bad.clear()
        km._user_todos_switch_bad.clear()
        km._UT_FLOOR_ARM.clear()

    @property
    def switch(self):
        return jd.STATE / km.USER_TODOS_SWITCH_FILE


def _serve_post(path, body=None, headers=None):
    """Drive the REAL do_POST dispatcher over a fake socket (the auth-hardening harness)."""
    raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    hdrs = dict(headers or {})
    hdrs.setdefault("Content-Length", str(len(raw)))
    h.headers = hdrs
    h.path = path
    h.command = "POST"
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO(raw)
    h.close_connection = True
    captured = {}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_POST()
    return captured.get("status"), h.wfile.getvalue()


def _post(path, body):
    code, out = _serve_post(path, body, {"X-Romp-Token": km.TOKEN})
    try:
        return code, json.loads(out.decode() or "{}")
    except ValueError:
        return code, {}


_ROMP_WORDS = None


def _romp_words():
    """The veil's vocabulary — ROMP_WORDS in tests/test_injected_voice.py, the words only romp knows —
    read out of that file as a literal, so the two session-facing texts pinned here are scanned
    with exactly the list the injected bodies and tool descriptions are."""
    global _ROMP_WORDS
    if _ROMP_WORDS is None:
        tree = ast.parse(Path(HERE, "test_injected_voice.py").read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ROMP_WORDS" for t in node.targets):
                _ROMP_WORDS = [word for word, _why in ast.literal_eval(node.value)]
                break
        else:
            raise AssertionError("ROMP_WORDS not found in tests/test_injected_voice.py")
    return _ROMP_WORDS


_PM = None


def _bus():
    """The postal bus, loaded once (the test_injected_voice idiom). Its two user-todo tools word the
    kernel's answers for the agent, and the wording of the cases pinned here — an unreadable store,
    an over-long note, a switch that flipped under the post — has no other home."""
    global _PM
    if _PM is None:
        _PM = load_source("romp_postal_utswitch", os.path.join(BIN, "romp-postal-service"))
    return _PM


class TheSwitch(_Sandbox):
    def test_the_file_is_not_the_store(self):
        self.assertEqual(km.USER_TODOS_SWITCH_FILE, "user-todos-enabled.json")
        self.assertNotEqual(km.USER_TODOS_SWITCH_FILE, "user-todos.json",
                            "user-todos.json is the todo STORE — a setting written there corrupts it")

    def test_absent_is_off_the_shipped_default(self):
        self.assertFalse(self.switch.exists())
        self.assertFalse(km._user_todos_on())
        self.assertFalse(self.switch.exists(), "reading never creates the file")

    def test_garbled_or_false_reads_off(self):
        # …and a file that is not a switch file is SAID, once per file version (the store guard's
        # idiom), so a hand-edit that turned the feature off is not a silent mystery: the absent
        # file (the shipped default) and a real `false` stay silent
        def read():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                on = km._user_todos_on()
            return on, err.getvalue()
        self.assertEqual(read(), (False, ""), "absent: the shipped default, silently")
        self.switch.write_text("not json")
        on, err = read()
        self.assertFalse(on)
        self.assertEqual(err.count("\n"), 1, err)
        self.assertIn("is not a switch file", err)
        self.assertIn("reading it as OFF", err)
        self.assertIn(str(self.switch), err, "names the file")
        self.assertEqual(read(), (False, ""), "the same file version is not re-announced")
        self.switch.write_text(json.dumps(["enabled"]))
        on, err = read()
        self.assertFalse(on)
        self.assertEqual(err.count("\n"), 1, "a JSON list is the next version: one more line")
        self.switch.write_text(json.dumps({"enabled": False, "gt": 5}))
        self.assertEqual(read(), (False, ""), "a real false is not an error")
        self.switch.write_text(json.dumps({"enabled": True, "gt": 6}))
        self.assertEqual(read(), (True, ""))

    def test_the_setter_writes_value_and_stamp_and_the_reader_sees_it(self):
        self.assertEqual(km._set_user_todos(True, gt=T_OLD), T_OLD)
        self.assertEqual(json.loads(self.switch.read_text()), {"enabled": True, "gt": T_OLD})
        self.assertTrue(km._user_todos_on())
        self.assertEqual(km._set_user_todos(False, gt=T_NEW), T_NEW)
        self.assertFalse(km._user_todos_on())

    def test_a_stale_gesture_stands_down_loudly_and_keeps_the_stored_value(self):
        km._set_user_todos(True, gt=T_NEW)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_user_todos(False, gt=T_OLD))
        self.assertTrue(km._user_todos_on(), "the newer choice survives the stale flush")
        self.assertIn("user-todos", err.getvalue())
        self.assertIn("stale gesture stood down", err.getvalue())
        self.assertEqual(km._pop_stale_notice(), {"setting": "user-todos", "storedGt": T_NEW, "gt": T_OLD},
                         "the stand-down is recorded for the settingStale reply")

    def test_equal_stamps_keep_the_stored_value(self):
        km._set_user_todos(True, gt=T_NEW)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_user_todos(False, gt=T_NEW))
        self.assertTrue(km._user_todos_on())

    def test_an_equal_stamp_with_the_same_value_is_a_silent_echo(self):
        km._set_user_todos(True, gt=T_NEW)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_user_todos(True, gt=T_NEW))
        self.assertEqual(err.getvalue(), "", "the gesture's own echo: nothing to apply, nothing to say")
        self.assertIsNone(km._pop_stale_notice(), "…and no settingStale notice for the socket")

    def test_an_unstamped_apply_arms_the_store_against_stale_flushes(self):
        stamp = km._set_user_todos(True)
        self.assertIsInstance(stamp, int)
        self.assertGreater(stamp, T_NEW, "an unstamped set records its arrival time")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_user_todos(False, gt=T_OLD), "an old flush cannot walk it back")
        self.assertTrue(km._user_todos_on())

    def test_a_file_without_the_field_reads_as_gt_zero(self):
        self.switch.write_text(json.dumps({"enabled": True}))
        self.assertEqual(km._set_user_todos(False, gt=1), 1, "any stamped gesture applies over it")
        self.assertFalse(km._user_todos_on())

    def test_a_failed_write_is_loud_none_and_applies_nothing(self):
        km._set_user_todos(True, gt=T_OLD)
        err = io.StringIO()
        with mock.patch.object(km, "_atomic_write", side_effect=OSError("disk full")), \
                contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_user_todos(False, gt=T_NEW))
        self.assertIn("setting user-todos: write failed", err.getvalue())
        self.assertTrue(km._user_todos_on(), "the stored choice survives the failed write")
        self.assertIsNone(km._pop_stale_notice(), "an OSError is not a stand-down: no stale frame")

    def test_the_stale_reply_words_the_kept_value(self):
        self.assertIs(km._setting_kept_value("user-todos"), False)
        km._set_user_todos(True)
        self.assertIs(km._setting_kept_value("user-todos"), True)

    def test_the_stamp_report_carries_the_store_like_its_siblings(self):
        # /version's settingsGt teaches the gear's gesture clock every store's last-applied stamp;
        # a store the report leaves out is one a device with a slow clock can never outrank
        self.assertIn("user-todos", km._GT_STORES)
        self.assertEqual(km._setting_stored_gt("user-todos"), 0, "absent reads 0 — nothing to outrank")
        km._set_user_todos(True, gt=T_OLD)
        self.assertEqual(km._setting_stored_gt("user-todos"), T_OLD)
        self.assertEqual(km._version_info()["settingsGt"]["user-todos"], T_OLD)
        self.switch.write_text(json.dumps({"enabled": True}))
        self.assertEqual(km._setting_stored_gt("user-todos"), 0, "a file without the field reads 0")


class TheWsOpAndVersion(_Sandbox):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s)), "alive": True}
        self._dirty = km._mark_views_dirty
        self.dirtied = []
        km._mark_views_dirty = lambda: self.dirtied.append(True)

    def tearDown(self):
        km._mark_views_dirty = self._dirty
        super().tearDown()

    def dispatch(self, msg):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, self.client)
        return err.getvalue()

    def test_setUserTodos_applies_and_repaints(self):
        self.dispatch({"type": "setUserTodos", "enabled": True, "gt": T_OLD})
        self.assertTrue(km._user_todos_on())
        self.assertEqual(json.loads(self.switch.read_text())["gt"], T_OLD, "the gesture stamp lands")
        self.assertEqual(self.dirtied, [True], "a flip changes every payload with no store write: repaint now")
        self.assertEqual(self.sent, [], "a clean apply sends nothing back")

    def test_a_stale_setUserTodos_answers_the_delivering_socket_with_settingStale(self):
        self.dispatch({"type": "setUserTodos", "enabled": True, "gt": T_NEW})
        self.dispatch({"type": "setUserTodos", "enabled": False, "gt": T_OLD})
        self.assertTrue(km._user_todos_on())
        stale = [m for m in self.sent if m.get("type") == "settingStale"]
        # the frame's shape is every gt-gated setting's (test_setting_gesture_order.py): the kept
        # value, the refused gesture's own stamp, and the gesture echoed back WITHOUT its stamp so
        # the toast's re-issue can never reuse the stale one
        self.assertEqual(stale, [{"type": "settingStale", "setting": "user-todos", "storedGt": T_NEW,
                                  "gt": T_OLD, "kept": True,
                                  "gesture": {"type": "setUserTodos", "enabled": False}}])
        self.assertEqual(len(self.dirtied), 1, "a stood-down gesture repaints nothing — no new information")

    def test_a_full_disk_toggle_does_not_tear_the_ws_down(self):
        with mock.patch.object(km, "_atomic_write", side_effect=OSError("disk full")):
            try:
                err = self.dispatch({"type": "setUserTodos", "enabled": True, "gt": T_NEW})
            except OSError:
                self.fail("an OSError escaped _dispatch_ws — the reader loop reads it as a socket failure")
        self.assertIn("user-todos", err)
        self.assertTrue(self.client["alive"])
        self.assertFalse(km._user_todos_on())
        self.assertEqual(self.dirtied, [], "nothing applied, nothing to repaint")

    def test_a_valueless_op_is_ignored(self):
        self.dispatch({"type": "setUserTodos"})
        self.assertFalse(self.switch.exists())

    def test_version_reports_the_switch_top_level_beside_thinking_summaries(self):
        for want in (False, True):
            km._set_user_todos(want)
            v = km._version_info()
            self.assertIs(v["userTodos"], want)
            self.assertIn("thinkingSummaries", v)
        self.assertNotIn("userTodos", v["settings"],
                         "per-install like thinkingSummaries: not in the mesh-comparison dict (never a 'mixed' mark)")

    def test_per_install_no_propagation_table_names_it(self):
        src = inspect.getsource(km)
        self.assertNotIn('"userTodos", _set_user_todos', src, "the /judge-settings propagation shape")
        self.assertNotIn("_propagate_judge_settings(\"user-todos\"", src)
        # the dispatcher says so where the op is handled
        at = src.index('msg.get("type") == "setUserTodos"')
        self.assertIn("NOT in federation.ts's KERNEL_SETTING", src[at:at + 900])


class OffOnTheRoutes(_Sandbox):
    """POST /usertodo and /usertodo/withdraw refuse with a 409 + one-line reason while OFF; the store
    is untouched; /usertodo/context says enabled:false with an empty block. ON: as before."""

    def setUp(self):
        super().setUp()
        self._push = (km._push_all, km._push_soon)
        self.pushed = []
        km._push_all = lambda *a, **k: (_ for _ in ()).throw(AssertionError("synchronous _push_all"))
        km._push_soon = lambda: self.pushed.append(True)

    def tearDown(self):
        km._push_all, km._push_soon = self._push
        super().tearDown()

    def test_register_is_refused_409_and_writes_nothing(self):
        code, res = _post("/usertodo", {"id": SID, "text": "Need the auth-scheme decision"})
        self.assertEqual(code, 409)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "user todos are turned off on this machine")
        self.assertFalse((jd.STATE / "user-todos.json").exists(), "nothing written")
        self.assertEqual(self.pushed, [], "nothing changed, nothing to push")

    def test_shape_errors_still_come_first(self):
        self.assertEqual(_post("/usertodo", {"id": SID})[0], 400)
        self.assertEqual(_post("/usertodo/withdraw", {"id": SID})[0], 400)

    def test_the_refusal_comes_before_any_remote_forward(self):
        # the switch is per machine: the kernel the bus asked answers for itself, and a remote
        # kernel's own copy of the route applies its own switch to a forwarded ask
        with mock.patch.object(km, "_host_for_sid", lambda sid: "otherhost"), \
                mock.patch.object(km, "_remote_forward",
                                  side_effect=AssertionError("forwarded while off")), \
                mock.patch.object(km, "_remote_forward_status",          # the withdraw route's forward
                                  side_effect=AssertionError("forwarded while off")):
            self.assertEqual(_post("/usertodo", {"id": SID, "text": "Need the staging port"})[0], 409)
            self.assertEqual(_post("/usertodo/withdraw", {"id": SID, "todoId": "ut-9f2c1a34"})[0], 409)

    def test_withdraw_is_refused_409_and_the_row_stays_open(self):
        km._set_user_todos(True)
        _, res = _post("/usertodo", {"id": SID, "text": "Need the staging port"})
        tid = res["todoId"]
        km._set_user_todos(False)
        code, out = _post("/usertodo/withdraw", {"id": SID, "todoId": tid})
        self.assertEqual(code, 409)
        self.assertFalse(out["ok"])
        self.assertIn("turned off on this machine", out["error"])
        row = km._user_todos()[SID][0]
        self.assertNotIn("resolved", row, "the ask still stands in the store")

    def test_context_answers_enabled_false_and_an_empty_block_despite_open_rows(self):
        km._set_user_todos(True)
        km._add_user_todo(SID, "Need the auth-scheme decision to wire login")
        code, res = _post("/usertodo/context", {"id": SID})
        self.assertEqual((code, res["enabled"]), (200, True))
        self.assertIn("Notes you still have open", res["block"])
        km._set_user_todos(False)
        code, res = _post("/usertodo/context", {"id": SID})
        self.assertEqual(code, 200, "a read: OFF is an honest 200, not an error")
        self.assertIs(res["enabled"], False)
        self.assertEqual(res["block"], "", "the hook injects nothing")

    def test_turning_it_back_on_shows_the_stored_rows_again(self):
        km._set_user_todos(True)
        tid = km._add_user_todo(SID, "Need the auth-scheme decision")
        km._set_user_todos(False)
        self.assertEqual(km._open_user_todos(SID), [])
        km._set_user_todos(True)
        self.assertEqual([t["id"] for t in km._open_user_todos(SID)], [tid], "kept on disk the whole time")

    def test_on_the_routes_work_as_before(self):
        km._set_user_todos(True)
        code, res = _post("/usertodo", {"id": SID, "text": "Need the fixture format pick"})
        self.assertEqual((code, res["ok"]), (200, True))
        code, out = _post("/usertodo/withdraw", {"id": SID, "todoId": res["todoId"]})
        self.assertEqual((code, out["ok"]), (200, True))


class OffOnTheDriveOps(_Sandbox):
    """userTodoAnswer / userTodoDismiss warn (the op's typed failure) and touch nothing while OFF —
    a dashboard can still show a row it was handed before the switch flipped."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s))}
        self._saved = (km._name_of, km._sdk, km._send_or_park, km._push_soon)
        km._name_of = lambda sid: "web" if sid == SID else None
        km._sdk = lambda: None
        self.injected = []
        km._send_or_park = lambda be, sid, text, echo=None, user_todo=None: self.injected.append((sid, text)) or True
        km._push_soon = lambda: None
        km._set_user_todos(True)
        self.tid = km._add_user_todo(SID, "Need the auth-scheme decision to wire login")
        km._set_user_todos(False)

    def tearDown(self):
        km._name_of, km._sdk, km._send_or_park, km._push_soon = self._saved
        super().tearDown()

    def _warns(self):
        return [m for m in self.sent if m.get("type") == "warn"]

    def test_answer_warns_and_injects_nothing(self):
        handled = km._drive({"type": "userTodoAnswer", "id": SID, "todoId": self.tid, "text": "OAuth"}, self.client)
        self.assertTrue(handled)
        self.assertEqual(self.injected, [])
        self.assertEqual(len(self._warns()), 1)
        self.assertIn("turned off on this machine", self._warns()[0]["text"])
        self.assertNotIn("already settled", self._warns()[0]["text"],
                         "the switch is named — not the stale-row story the gated read would suggest")
        self.assertNotIn("resolved", km._user_todos()[SID][0])

    def test_dismiss_warns_and_stamps_nothing(self):
        km._drive({"type": "userTodoDismiss", "id": SID, "todoId": self.tid}, self.client)
        self.assertEqual(len(self._warns()), 1)
        self.assertIn("turned off on this machine", self._warns()[0]["text"])
        self.assertNotIn("resolved", km._user_todos()[SID][0])

    def test_the_warning_speaks_to_the_person_not_the_machinery(self):
        # the toast is read by the user at the dashboard, so it may name the gear; it must still say
        # what did NOT happen (the CLAUDE.md loudness rule: never a silent no-op)
        self.assertIn("nothing was sent", km._USER_TODOS_OFF_WARN)
        self.assertIn("nothing changed", km._USER_TODOS_OFF_WARN)
        self.assertIn("gear", km._USER_TODOS_OFF_WARN, "…and where to turn it on")

    def test_on_again_the_same_gestures_land(self):
        km._set_user_todos(True)
        km._drive({"type": "userTodoDismiss", "id": SID, "todoId": self.tid}, self.client)
        self.assertEqual(self._warns(), [])
        self.assertEqual(km._user_todos()[SID][0]["resolved"]["kind"], "dismissed")


class _PayloadSandbox(unittest.TestCase):
    """A build_session fixture (one named session, a two-row transcript, no global CLAUDE.md) with
    one row stored and the switch left OFF — the OFF-side payload pins and the unreadable-store
    card share it."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"
        cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"
        names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
                      km._read_task_store, km._fold_tasks, km._tmux_sessions, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global.md"           # keep a real ~/.claude/CLAUDE.md out of the fixture
        km._read_task_store = lambda fsid, fold=None: []
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._parse_cache.clear()
        km._user_todos_cache.clear()
        km._user_todos_bad.clear()
        km._UT_FLOOR_ARM.clear()
        rows = [
            {"type": "user", "uuid": "u1", "timestamp": "2026-06-01T00:00:00Z",
             "sessionId": SID, "message": {"role": "user", "content": "wire the login routes"}},
            {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": "2026-06-01T00:00:05Z",
             "sessionId": SID,
             "message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "starting on the open routes"}]}},
        ]
        self.tpath.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        km._set_user_todos(True)
        self.tid = km._add_user_todo(SID, "Need the auth-scheme decision to wire login", "OAuth vs cookie")
        km._set_user_todos(False)

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
         km._read_task_store, km._fold_tasks, km._tmux_sessions, km._GLOBAL_CLAUDE_MD) = self.saved
        km._parse_cache.clear()
        km._user_todos_cache.clear()
        km._user_todos_bad.clear()
        km._UT_FLOOR_ARM.clear()
        self.td.cleanup()

    def _todo_events(self, payload):
        return [e for e in payload["events"] if e.get("kind") == "todo"]


class OffOnThePayloads(_PayloadSandbox):
    """The kernel ships NO rows while OFF, so the client needs no logic of its own: build_session's
    `userTodos` field is [] and no split-card event carries rows; build_feed's map is {}. ON: the
    same store fills both. _open_user_todos is the one gated read."""

    def test_build_session_ships_an_empty_field_and_no_rows_while_off(self):
        payload = km.build_session(SID, NOW)
        self.assertEqual(payload["userTodos"], [])
        self.assertEqual(self._todo_events(payload), [], "no split card either: the section has nothing to show")
        self.assertEqual(len(km._user_todos()[SID]), 1, "…while the store still holds the row")

    def test_the_everyday_card_is_byte_identical_while_off(self):
        # the agent's own to-do card (Claude's task store, an in-progress item) with the switch OFF
        # and a row IN the store serializes exactly as it did before the seam existed: no `userTodos`
        # key at all. The ON-side pin (BuildSessionSeam's pre-existing-shape test) runs with the
        # switch on, so until this test the OFF claim held by shared code — _open_user_todos's one
        # gated read — and not by anything that built the card.
        km._read_task_store = lambda fsid, fold=None: [
            {"id": "1", "subject": "Build the fixtures", "activeForm": None, "status": "pending"}]
        payload = km.build_session(SID, NOW)
        evs = self._todo_events(payload)
        self.assertEqual(len(evs), 1)
        self.assertEqual(set(evs[0]), {"kind", "tasks"})
        self.assertEqual(payload["userTodos"], [])

    def test_the_same_build_shows_the_row_once_on(self):
        km._set_user_todos(True)
        km._parse_cache.clear()
        payload = km.build_session(SID, NOW)
        self.assertEqual([t["id"] for t in payload["userTodos"]], [self.tid])
        self.assertEqual(self._todo_events(payload)[0]["userTodos"], payload["userTodos"])

    def test_a_flip_busts_the_owning_sessions_chat_cache(self):
        # the chat sig folds this sid's rows (_user_todo_fp); a flip changes the card with no store
        # write, so the fold must change too — or a background tab keeps painting the stale card
        off = km._user_todo_fp(SID)
        km._set_user_todos(True)
        on = km._user_todo_fp(SID)
        self.assertNotEqual(off, on)
        self.assertEqual(on, km._user_todo_fp(SID), "byte-stable while the switch holds")
        self.assertIsNone(km._user_todo_fp(SID2), "a sid with no rows is untouched by the switch")

    def test_the_context_block_is_empty_while_off(self):
        self.assertEqual(km._user_todo_context_block(SID), "", "the gated read empties the block too")
        km._set_user_todos(True)
        self.assertIn("Need the auth-scheme decision to wire login", km._user_todo_context_block(SID))

    def test_build_feed_ships_an_empty_map_while_off_and_the_counts_once_on(self):
        sessions = [{"sid": SID, "name": "web", "path": "/nonexistent/%s.jsonl" % SID, "anchor": 0, "mtime": 0}]
        with mock.patch.object(km, "_alive_sessions", lambda now, tmux: list(sessions)), \
                mock.patch.object(km, "_warm_fleet_bg", lambda now: None):
            self.assertEqual(km.build_feed(NOW, {}).get("userTodos"), {})
            km._set_user_todos(True)
            self.assertEqual(km.build_feed(NOW, {}).get("userTodos"), {SID: 1})

    def test_the_feed_sig_watches_the_switch_file(self):
        with mock.patch.object(km, "_alive_sessions", lambda now, tmux: []), \
                mock.patch.object(km, "_warm_fleet_bg", lambda now: None):
            before = km._fleet_view_sig(NOW, {})
            km._set_user_todos(True)
            after = km._fleet_view_sig(NOW, {})
        self.assertNotEqual(before, after)

    def test_open_user_todos_is_the_one_gated_read(self):
        src = inspect.getsource(km._open_user_todos)
        self.assertIn("if not _user_todos_on():\n        return []", src)
        self.assertEqual(km._open_user_todos(SID), [])
        # the nudge stand-down, the escalation floor and the push latch read it (grep-provable wiring)
        ksrc = inspect.getsource(km)
        self.assertIn("_todo_standdown = bool(_open_user_todos(sid))", ksrc)
        self.assertIn("_ut_open = _open_user_todos(fsid)", ksrc)
        self.assertIn('frozenset(t["id"] for t in _open_user_todos(str(sid)))', ksrc)


class UnreadableStoreOnThePayload(_PayloadSandbox):
    """The shape guard's refusal used to be stderr-only: a flagged store read as EMPTY on every
    user-facing surface, so the session's open requests simply vanished from the card with nothing
    saying why. While the switch is ON and the store is the flagged version, build_session's todo
    event carries `userTodosError` — its OWN key: the event's `error` is the task store's, and the
    renderer's error branch supplants the agent's checklist, so a request-store error riding it
    hid the checklist under a heading that blamed the wrong store — and the chat sig sees the
    store go bad even for a sid with no rows of its own."""

    def _corrupt(self):
        (jd.STATE / "user-todos.json").write_text(json.dumps({"enabled": True, "gt": 1}))

    def test_build_session_carries_the_error_on_the_todo_event(self):
        km._set_user_todos(True)
        km._parse_cache.clear()
        self._corrupt()
        with contextlib.redirect_stderr(io.StringIO()):
            payload = km.build_session(SID, NOW)
        self.assertEqual(payload["userTodos"], [], "the flagged store reads empty…")
        evs = self._todo_events(payload)
        self.assertEqual(len(evs), 1, "…and the card says WHY instead of showing nothing")
        self.assertIn("Can't read", evs[0]["userTodosError"])
        self.assertIn("user-todos.json", evs[0]["userTodosError"])
        self.assertNotIn(str(Path.home()), evs[0]["userTodosError"], "the path is shown with ~, never the home dir")
        self.assertEqual(evs[0]["tasks"], [])
        self.assertNotIn("error", evs[0], "the task store's key is not borrowed")

    def test_the_request_store_error_rides_its_own_key_beside_the_checklist(self):
        # a healthy task store and a flagged request store: the agent's checklist still renders
        # (rows on `tasks`, no `error`) and the request store's cause rides `userTodosError`. On
        # the shared key the renderer's error branch dropped the checklist and headed the card
        # "To-do · unavailable" — false, since Claude's task store WAS read.
        km._set_user_todos(True)
        km._parse_cache.clear()
        km._read_task_store = lambda fsid, fold=None: [
            {"id": "1", "subject": "Build the fixtures", "activeForm": None, "status": "pending"}]
        self._corrupt()
        with contextlib.redirect_stderr(io.StringIO()):
            payload = km.build_session(SID, NOW)
        evs = self._todo_events(payload)
        self.assertEqual(len(evs), 1)
        self.assertEqual(len(evs[0]["tasks"]), 1, "the checklist stays")
        self.assertNotIn("error", evs[0], "the task store read fine")
        self.assertIn("Can't read romp's request store", evs[0]["userTodosError"])

    def test_the_error_is_quiet_while_off_and_gone_once_the_file_is_fixed(self):
        self._corrupt()
        with contextlib.redirect_stderr(io.StringIO()):
            payload = km.build_session(SID, NOW)        # OFF: the surfaces are quiet, the stderr line stands alone
        self.assertEqual(self._todo_events(payload), [])
        km._set_user_todos(True)
        km._parse_cache.clear()
        (jd.STATE / "user-todos.json").write_text(json.dumps({SID: [
            {"id": self.tid, "text": "Need the auth-scheme decision to wire login",
             "detail": "OAuth vs cookie", "createdT": NOW}]}))
        payload = km.build_session(SID, NOW)
        self.assertNotIn("userTodosError", self._todo_events(payload)[0], "a fixed file: the rows, no error")
        self.assertNotIn("error", self._todo_events(payload)[0])
        self.assertEqual([t["id"] for t in payload["userTodos"]], [self.tid])

    def test_the_chat_sig_sees_the_store_go_bad_with_no_rows_of_its_own(self):
        # SID2 has no rows: its fold was None either way, so a cached tab would never learn the
        # store broke — the fold names the unreadable store while the switch is on
        km._set_user_todos(True)
        self.assertIsNone(km._user_todo_fp(SID2))
        self._corrupt()
        with contextlib.redirect_stderr(io.StringIO()):
            bad = km._user_todo_fp(SID2)
            self.assertIsNotNone(bad)
            self.assertEqual(bad, km._user_todo_fp(SID2), "byte-stable while the file stands")
            km._set_user_todos(False)
            self.assertIsNone(km._user_todo_fp(SID2), "OFF: the switch changes nothing this card shows")


class OffOnTheBadge(_Sandbox):
    """The badge's arithmetic is part of the feature, so the switch covers it: while OFF,
    _needs_you_count is what it was before user todos existed — every real needs-input card, per
    card — and an install that never turned the feature on sees no change in the number its icon
    wears. ON, the widened rule applies (plans/user-todos.md (d): open todos plus hard-stopped
    sessions once each; BadgeArithmetic in test_user_todos.py pins it in full)."""

    FEED = {"asks": [{"itemId": "S1:g1", "sid": "S1", "column": "needs_input"},
                     {"itemId": "S1:g2", "sid": "S1", "column": "needs_input"},
                     {"itemId": "S2:g1", "sid": "S2", "column": "needs_input", "provisional": True},
                     {"itemId": "S3:g1", "sid": "S3", "column": "working"}],
            "userTodos": {"S4": 2}}

    def test_off_counts_every_real_needs_input_card_per_card(self):
        # two blocked goal cards of one session read 2 — the pre-feature count — not 1; the map is
        # not read (a frame built while off carries an empty one anyway; a stale one adds nothing)
        self.assertEqual(km._needs_you_count(self.FEED), 2)

    def test_on_the_widened_rule_applies_to_the_same_frame(self):
        km._set_user_todos(True)
        # the hard-stopped session counts once as itself, plus the two open todos of S4
        self.assertEqual(km._needs_you_count(self.FEED), 3)

    def test_off_reads_a_frame_with_the_pre_feature_expression_alone(self):
        # a floored card (blocked.state userTodos) and a filled map can only reach the counter on a
        # frame built while ON; read while OFF, the card is one real needs-input card like any other
        # and nothing else on the frame is counted as its todos
        feed = {"asks": [{"itemId": "S1:g1", "sid": "S1", "column": "needs_input",
                          "blocked": {"state": "userTodos", "count": 2}}], "userTodos": {"S1": 2}}
        self.assertEqual(km._needs_you_count(feed), 1)
        km._set_user_todos(True)
        self.assertEqual(km._needs_you_count(feed), 2, "on: the floor is a presentation of the two todos")


class RegisterForward(_Sandbox):
    """POST /usertodo for a sid another kernel owns: the forward keeps the remote's STATUS (the
    withdraw route's path, _remote_forward_status), so a remote whose switch is off answers 409
    naming the host, and a dead tunnel, an older kernel, or an answer this kernel cannot read
    answers 502 with the cause — never a 200 {"ok": false} that the bus folds into "try again
    shortly". API-only: a session's own tool posts to its own host's kernel, where _host_for_sid
    is None; the route is API for any token holder all the same."""

    def setUp(self):
        super().setUp()
        km._set_user_todos(True)
        self._saved = (km._host_for_sid, km._remote_forward_status, km._push_all, km._push_soon)
        km._host_for_sid = lambda sid: {"host": "TESTHOST", "local_port": 1, "token": ""}
        km._push_all = km._push_soon = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("nothing changed locally, nothing to push"))
        self.calls = []

    def tearDown(self):
        km._host_for_sid, km._remote_forward_status, km._push_all, km._push_soon = self._saved
        super().tearDown()

    def _forward(self, st, res):
        km._remote_forward_status = lambda r, path, body: (self.calls.append((path, body)) or (st, res))
        with contextlib.redirect_stderr(io.StringIO()):
            return _post("/usertodo", {"id": SID, "text": "Need the staging port", "detail": "8443?"})

    def test_a_minted_id_is_relayed_as_before(self):
        code, res = self._forward(200, {"ok": True, "todoId": "ut-9f2c1a34"})
        self.assertEqual((code, res), (200, {"ok": True, "todoId": "ut-9f2c1a34"}))
        self.assertEqual(self.calls, [("/usertodo", {"id": SID, "text": "Need the staging port", "detail": "8443?"})])

    def test_a_remote_switch_that_is_off_is_a_409_naming_the_host(self):
        code, res = self._forward(409, None)
        self.assertEqual(code, 409)
        self.assertFalse(res["ok"])
        self.assertIn("turned off", res["error"])
        self.assertIn("TESTHOST", res["error"])
        self.assertEqual(res["host"], "TESTHOST")

    def test_a_dead_tunnel_an_old_kernel_and_an_unreadable_answer_are_502_with_the_cause(self):
        for st, res_in, why in ((0, None, "not answering"), (404, None, "predates"), (500, None, "HTTP 500"),
                                (200, None, "without a todo id"), (200, {"ok": False}, "without a todo id")):
            with self.subTest(status=st, body=res_in):
                code, res = self._forward(st, res_in)
                self.assertEqual(code, 502)
                self.assertFalse(res["ok"])
                self.assertIn(why, res["error"])
                self.assertEqual(res["host"], "TESTHOST")
        self.assertEqual(km._user_todos(), {}, "the local store is never written for a remote sid")


class RemoteForwardStatusPin(unittest.TestCase):
    """_remote_forward_status — and _remote_forward, which reads its answer off it — on a 200 whose
    body is not JSON: (200, None) and NO redial, because the far side answered and the tunnel is
    fine. The base parsed inside the try and demanded a redial for that; the change is deliberate
    (a malformed body is version skew or a proxy page, not a dead tunnel) and pinned here for the
    ten pre-existing callers that inherit it."""

    _R = {"host": "TESTHOST", "local_port": 1, "token": ""}

    @staticmethod
    def _conn(status=200, body=b"", raise_on_request=None):
        class _Resp:
            def read(self):
                return body
        _Resp.status = status

        class _Conn:
            def __init__(self, *a, **k):
                pass

            def request(self, *a, **k):
                if raise_on_request is not None:
                    raise raise_on_request

            def getresponse(self):
                return _Resp()

            def close(self):
                pass
        return _Conn

    def test_a_non_json_200_is_200_none_with_no_redial(self):
        redials = []
        with mock.patch.object(km.http.client, "HTTPConnection", self._conn(200, b"<html>not json</html>")), \
                mock.patch.object(km, "_demand_redial", lambda host, why: redials.append((host, why))):
            self.assertEqual(km._remote_forward_status(self._R, "/usertodo", {"id": SID}), (200, None))
            self.assertIsNone(km._remote_forward(self._R, "/usertodo", {"id": SID}))
        self.assertEqual(redials, [], "a malformed body still proves the far side spoke")

    def test_a_non_200_keeps_its_status_and_a_refused_connection_is_0_with_the_redial(self):
        redials = []
        with mock.patch.object(km.http.client, "HTTPConnection", self._conn(404, b"")), \
                mock.patch.object(km, "_demand_redial", lambda host, why: redials.append((host, why))):
            self.assertEqual(km._remote_forward_status(self._R, "/usertodo", {}), (404, None))
        self.assertEqual(redials, [])
        with mock.patch.object(km.http.client, "HTTPConnection",
                               self._conn(raise_on_request=ConnectionRefusedError())), \
                mock.patch.object(km, "_demand_redial", lambda host, why: redials.append((host, why))):
            self.assertEqual(km._remote_forward_status(self._R, "/usertodo", {}), (0, None))
        self.assertEqual(redials, [("TESTHOST", "refused")], "a dead tunnel is still user demand for a redial")


class BootNotice(_Sandbox):
    def _notice(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = km._user_todos_off_boot_notice()
        return n, err.getvalue()

    def test_open_rows_behind_an_off_switch_are_announced_once(self):
        km._set_user_todos(True)
        km._add_user_todo(SID, "Need the auth-scheme decision")
        km._add_user_todo(SID2, "Need the staging port")
        tid = km._add_user_todo(SID2, "Need your pick of the two layouts")
        km._resolve_user_todo(SID2, tid, "withdrawn")
        km._set_user_todos(False)
        n, err = self._notice()
        self.assertEqual(n, 2, "open rows only")
        self.assertIn("2 user todo(s) are stored but the feature is off", err)
        self.assertIn("Turn it on in the gear", err)

    def test_silent_when_on_or_when_nothing_is_stored(self):
        self.assertEqual(self._notice(), (0, ""))
        km._set_user_todos(True)
        km._add_user_todo(SID, "Need the auth-scheme decision")
        self.assertEqual(self._notice(), (0, ""), "ON: the rows are visible, nothing to announce")

    def test_wired_into_main_after_both_loss_boot_passes(self):
        # either loss pass may REOPEN rows a dead kernel left mid-answer; the notice counts after
        # both so the number it says is the number the store holds
        src = inspect.getsource(km.main)
        self.assertIn("_user_todos_off_boot_notice()", src)
        self.assertLess(src.index("_user_todo_loss_boot_pass"), src.index("_user_todos_off_boot_notice"))
        self.assertLess(src.index("_tmux_paste_loss_boot_pass"), src.index("_user_todos_off_boot_notice"))
        self.assertLess(src.index("_user_todos_off_boot_notice"), src.index("_boot_warm()"))


class StoreShapeGuard(_Sandbox):
    """user-todos.json is the STORE. A file there that is not sid → list reads as empty, loudly,
    and no writer may overwrite that version: a settings blob written into the store by hand is
    what this guards against — without it the next register would replace the whole store."""

    def setUp(self):
        super().setUp()
        km._set_user_todos(True)
        self.store = jd.STATE / "user-todos.json"

    def _read(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = km._user_todos()
        return d, err.getvalue()

    def test_a_settings_blob_in_the_store_reads_empty_and_loud_once_per_version(self):
        self.store.write_text(json.dumps({"enabled": True, "gt": 1}))
        d, err = self._read()
        self.assertEqual(d, {})
        self.assertIn("is not a todo store", err)
        self.assertIn("enabled, gt", err, "names what it found")
        self.assertIn("user-todos-enabled.json", err, "…and where the switch actually lives")
        self.assertIn("refusing to overwrite", err)
        km._user_todos_cache.clear()
        self.assertEqual(self._read(), ({}, ""), "the same file version is not re-announced")

    def test_every_writer_refuses_to_overwrite_the_flagged_version(self):
        self.store.write_text(json.dumps({"enabled": True, "gt": 1}))
        before = self.store.read_text()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                km._add_user_todo(SID, "Need the auth-scheme decision")
            with self.assertRaises(RuntimeError):
                km._resolve_user_todo(SID, "ut-deadbeef", "dismissed") or km._write_user_todos({SID: []})
        self.assertEqual(self.store.read_text(), before, "the unreadable store is intact")

    def test_the_register_route_fails_loudly_never_a_200(self):
        self.store.write_text(json.dumps({"enabled": True, "gt": 1}))
        before = self.store.read_text()
        saved = (km._push_all, km._push_soon)
        km._push_all = km._push_soon = lambda *a, **k: None
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                code, _ = _post("/usertodo", {"id": SID, "text": "Need the auth-scheme decision"})
        finally:
            km._push_all, km._push_soon = saved
        self.assertNotEqual(code, 200, "the bus must not echo 'saved' back to the agent")
        self.assertEqual(self.store.read_text(), before)

    def test_unparsable_text_and_a_json_list_are_guarded_the_same_way(self):
        for junk in ("not json", json.dumps([{"id": "ut-1"}]), json.dumps({SID: {"id": "ut-1"}})):
            km._user_todos_cache.clear(); km._user_todos_bad.clear()
            self.store.write_text(junk)
            d, err = self._read()
            self.assertEqual(d, {}, junk)
            self.assertIn("is not a todo store", err, junk)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(RuntimeError):
                km._write_user_todos({})

    def test_a_key_that_is_not_a_session_id_is_not_a_store_either(self):
        # the keys are path components downstream (per-sid marks, states rows): a dotted or
        # slashed key is never a sid, so a dict carrying one is not this store
        for bad in ({"../x": []}, {".hidden": []}, {"": []}):
            self.assertFalse(km._user_todo_store_shaped(bad), bad)

    # The register path is the ONE writer that mints a NEW key. A key the reader rejects would flag
    # the whole file: every open row reads as empty on every surface and every later write (a
    # register, an answer stamp, a dismiss, a withdraw, a reopen) raises until the file is
    # hand-edited. So the writer refuses the key before it is ever written, and the store stays
    # shaped. The stamp/reopen helpers only touch EXISTING keys and need no such check.
    _BAD_SIDS = ("TESTHOST:" + SID, "web/" + SID, "." + SID, "a" * 129)

    def test_the_writer_refuses_a_sid_the_reader_would_reject(self):
        for bad in self._BAD_SIDS:
            with self.assertRaises(ValueError, msg=bad):
                km._add_user_todo(bad, "Need the staging port")
        self.assertFalse(self.store.exists(), "nothing written for a refused key")
        km._add_user_todo(SID, "Need the staging port")           # a real sid still registers
        for bad in self._BAD_SIDS:
            with self.assertRaises(ValueError, msg=bad):
                km._add_user_todo(bad, "Need the staging port")
        d, err = self._read()
        self.assertEqual(err, "", "the store never became unreadable")
        self.assertEqual(set(d), {SID})
        self.assertTrue(km._user_todo_store_shaped(json.loads(self.store.read_text())))
        self.assertEqual(km._user_todos_bad, {})

    def test_the_register_route_answers_400_for_a_malformed_id_before_the_switch_and_the_forward(self):
        saved = (km._push_all, km._push_soon)
        km._push_all = km._push_soon = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("nothing changed, nothing to push"))
        try:
            for bad in self._BAD_SIDS:
                code, res = _post("/usertodo", {"id": bad, "text": "Need the staging port"})
                self.assertEqual((code, res), (400, {"ok": False, "error": "id must be a session id"}), bad)
            self.assertFalse(self.store.exists(), "nothing written")
            # before the switch: while OFF the malformed id is still the shape error, not the 409
            km._set_user_todos(False)
            self.assertEqual(_post("/usertodo", {"id": self._BAD_SIDS[0], "text": "Need the staging port"})[0], 400)
            km._set_user_todos(True)
            # before any forward: a malformed id is refused here, never relayed to another kernel
            with mock.patch.object(km, "_host_for_sid", lambda sid: "otherhost"), \
                    mock.patch.object(km, "_remote_forward",
                                      side_effect=AssertionError("forwarded a malformed id")):
                self.assertEqual(_post("/usertodo", {"id": self._BAD_SIDS[0], "text": "Need the staging port"})[0], 400)
            # the well-formed ask still lands (and is the first thing that wakes the pusher)
            pushed = []
            km._push_soon = lambda: pushed.append(True)
            code, res = _post("/usertodo", {"id": SID, "text": "Need the staging port"})
            self.assertEqual((code, res["ok"], pushed), (200, True, [True]))
        finally:
            km._push_all, km._push_soon = saved

    def test_a_malformed_id_on_the_route_leaves_a_stored_row_readable(self):
        # the route against a store that already holds a row: the 400 leaves the file byte-identical
        # and the row still reads. One written bad key would have flagged the whole file and hidden
        # this row on every surface — the reader pin above, seen from the one writer an agent drives
        good = km._add_user_todo(SID, "Need the auth-scheme decision")
        before = self.store.read_text()
        saved = (km._push_all, km._push_soon)
        km._push_all = km._push_soon = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("nothing changed, nothing to push"))
        try:
            for bad in self._BAD_SIDS:
                code, res = _post("/usertodo", {"id": bad, "text": "Need the staging port"})
                self.assertEqual(code, 400, bad)
                self.assertFalse(res.get("ok"), bad)
        finally:
            km._push_all, km._push_soon = saved
        self.assertEqual(self.store.read_text(), before, "the route wrote nothing")
        km._user_todos_cache.clear()                                # re-read the file, not the cache
        d, err = self._read()
        self.assertEqual(err, "", "the store is still a todo store")
        self.assertEqual([t["id"] for t in d[SID]], [good], "the stored row still reads")
        self.assertEqual(km._user_todos_bad, {})

    def test_a_shaped_store_is_never_flagged(self):
        self.store.write_text(json.dumps({SID: [{"id": "ut-1", "text": "x", "createdT": 1}], SID2: []}))
        d, err = self._read()
        self.assertEqual(err, "")
        self.assertEqual(set(d), {SID, SID2})
        self.assertEqual(km._user_todos_bad, {})
        km._add_user_todo(SID, "Need the staging port")           # writes go through
        self.assertEqual(len(km._user_todos()[SID]), 2)

    def test_the_empty_store_and_a_missing_file_are_shaped(self):
        self.assertTrue(km._user_todo_store_shaped({}))
        self.assertEqual(self._read(), ({}, ""))
        km._add_user_todo(SID, "Need the staging port")
        self.assertEqual(len(km._user_todos()[SID]), 1)

    def test_fixing_or_removing_the_file_lets_writes_through_again(self):
        self.store.write_text(json.dumps({"enabled": True, "gt": 1}))
        with contextlib.redirect_stderr(io.StringIO()):
            self._read()
            with self.assertRaises(RuntimeError):
                km._add_user_todo(SID, "x")
            self.store.unlink()                                  # removed: nothing left to protect
            tid = km._add_user_todo(SID, "Need the auth-scheme decision")
        self.assertEqual(km._user_todos()[SID][0]["id"], tid)
        self.assertEqual(km._user_todos_bad, {}, "a shaped read clears the flag")

    def test_the_boot_notice_and_the_gated_read_survive_a_corrupt_store(self):
        self.store.write_text(json.dumps({"enabled": True, "gt": 1}))
        km._set_user_todos(False)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._user_todos_off_boot_notice(), 0)
            self.assertEqual(km._open_user_todos(SID), [])

    # ── an UNREADABLE store answers "can't read", never a definite state (2026-09-07) ──────────
    # The guard reads a flagged store as EMPTY for the writers' sake. A reader that answers a
    # definite state off that empty read tells the agent its own row does not exist ("No note of
    # yours") and the person that a row was "already settled", when the truth is that the kernel
    # could not read the file. _user_todos_unreadable is the read those answerers consult first.

    def _corrupt(self):
        self.store.write_text(json.dumps({"enabled": True, "gt": 1}))
        with contextlib.redirect_stderr(io.StringIO()):
            km._user_todos_cache.clear()
            km._user_todos()

    def _no_push(self):
        saved = (km._push_all, km._push_soon)
        km._push_all = km._push_soon = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("nothing changed, nothing to push"))
        return saved

    def test_unreadable_tracks_the_flagged_file_version(self):
        self.assertFalse(km._user_todos_unreadable(), "no file: an empty store, not an unreadable one")
        tid = km._add_user_todo(SID, "Need the staging port")
        self.assertFalse(km._user_todos_unreadable())
        self.store.write_text(json.dumps({"enabled": True, "gt": 1}))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(km._user_todos_unreadable(), "the read that flags is the read this makes")
            self.assertTrue(km._user_todos_unreadable(), "…and it holds while that version stands")
        self.store.write_text(json.dumps({SID: [{"id": tid, "text": "Need the staging port", "createdT": 1}]}))
        self.assertFalse(km._user_todos_unreadable(), "a fixed file reads again")
        self.store.write_text("not json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(km._user_todos_unreadable())
        self.store.unlink()
        self.assertFalse(km._user_todos_unreadable(), "a removed file is the empty store")

    def test_a_withdraw_against_an_unreadable_store_accounts_unreadable_not_unknown(self):
        tid = km._add_user_todo(SID, "Need the auth-scheme decision")
        self._corrupt()
        acct = km._withdraw_user_todo(SID, tid)
        self.assertFalse(acct["ok"])
        self.assertEqual((acct["state"], acct["at"], acct["owner"]), ("unknown", None, None),
                         "owner None: the kernel cannot say whose the row is, let alone its state")
        self.assertIn("unreadable", acct["error"])
        saved = self._no_push()
        try:
            code, out = _post("/usertodo/withdraw", {"id": SID, "todoId": tid})
        finally:
            km._push_all, km._push_soon = saved
        # the account rides a 200 like the malformed-stamp account does: the bus's transport drops
        # the body of every non-2xx, and the agent must hear WHICH nothing-to-do this was
        self.assertEqual(code, 200)
        self.assertFalse(out["ok"])
        self.assertNotIn("no open todo", out["error"], "never the 'already settled' story")
        self.assertIn("unreadable", out["error"])
        self.assertIsNone(out["owner"])
        self.assertEqual(self.store.read_text(), json.dumps({"enabled": True, "gt": 1}), "nothing rewritten")

    def test_the_register_route_answers_503_on_an_unreadable_store(self):
        self._corrupt()
        saved = self._no_push()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                code, res = _post("/usertodo", {"id": SID, "text": "Need the auth-scheme decision"})
        finally:
            km._push_all, km._push_soon = saved
        self.assertEqual(code, 503, "a plain refusal with the cause, not a 500 traceback")
        self.assertFalse(res["ok"])
        self.assertIn("unreadable", res["error"])
        self.assertEqual(self.store.read_text(), json.dumps({"enabled": True, "gt": 1}))

    def test_the_drive_ops_warn_unreadable_not_settled(self):
        tid = km._add_user_todo(SID, "Need the auth-scheme decision")
        self._corrupt()
        sent, injected = [], []
        client = {"send": lambda s: sent.append(json.loads(s))}
        with mock.patch.object(km, "_name_of", lambda sid: "web"), \
                mock.patch.object(km, "_sdk", lambda: None), \
                mock.patch.object(km, "_send_or_park",
                                  lambda be, sid, text, echo=None, user_todo=None: injected.append(text) or True), \
                mock.patch.object(km, "_push_soon", lambda: None), \
                contextlib.redirect_stderr(io.StringIO()):
            km._drive({"type": "userTodoAnswer", "id": SID, "todoId": tid, "text": "OAuth"}, client)
            km._drive({"type": "userTodoDismiss", "id": SID, "todoId": tid}, client)
        warns = [m["text"] for m in sent if m.get("type") == "warn"]
        self.assertEqual(len(warns), 2, sent)
        for w in warns:
            self.assertNotIn("already settled", w)
            self.assertIn("can't read", w.lower())
            self.assertIn("nothing changed", w.lower())
        self.assertEqual(injected, [], "nothing reaches the session")
        self.assertEqual(self.store.read_text(), json.dumps({"enabled": True, "gt": 1}))


class BusWording(unittest.TestCase):
    """What the two postal tools tell the agent for the cases the kernel side above adds. The bus
    reaches the kernel through _kernel_post, whose transport answers None for every non-2xx, so
    the tool words only what it can tell: a body the kernel answered, its own pre-checks, and a
    re-read of the switch file it shares with the kernel."""

    def setUp(self):
        self.pm = pm = _bus()
        self._saved = (pm._kernel_post, pm._self_identity, pm._heartbeat)
        self.posts = []
        self.canned = None
        pm._kernel_post = lambda path, body, timeout=4.0: (self.posts.append((path, body)) or self.canned)
        pm._self_identity = lambda: (SID, "api")   # one identity resolution per call: _mcp_call reads _self_identity() before its argument checks
        pm._heartbeat = lambda *a, **k: None
        pm.USER_TODOS_SWITCH.parent.mkdir(parents=True, exist_ok=True)
        pm.USER_TODOS_SWITCH.write_text(json.dumps({"enabled": True, "gt": 1}))

    def tearDown(self):
        pm = self.pm
        pm._kernel_post, pm._self_identity, pm._heartbeat = self._saved
        pm.USER_TODOS_SWITCH.unlink(missing_ok=True)

    def _no_machinery(self, text):
        # the veil test_injected_voice.py keeps: the agent has never heard of any of these. Its
        # ROMP_WORDS is the one list (a hand copy here drifted from it once), read as a literal
        # rather than imported — that module loads the kernel and the bus at import time under
        # its own state root. "kernel" stays a local extra: the injected bodies that list also
        # scans name it on purpose, so widening ROMP_WORDS is a separate call.
        for word in _romp_words() + ["kernel"]:
            self.assertNotIn(word, text.lower(), (word, text))

    def test_the_bus_switch_reader_says_once_when_the_file_is_not_a_switch_file(self):
        # the kernel's _user_todos_on rule, on the bus's own copy of the reader (it is a separate
        # long-lived process reading the same file): absent and a real false are silent, anything
        # else is one stderr line per file version
        pm = self.pm
        pm._user_todos_switch_bad.clear()

        def read():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                on = pm._user_todos_on()
            return on, err.getvalue()
        pm.USER_TODOS_SWITCH.unlink()
        self.assertEqual(read(), (False, ""), "absent is silent")
        pm.USER_TODOS_SWITCH.write_text("not json")
        on, err = read()
        self.assertEqual((on, err.count("\n")), (False, 1), err)
        self.assertIn("is not a switch file", err)
        self.assertEqual(read(), (False, ""), "once per file version")
        pm.USER_TODOS_SWITCH.write_text(json.dumps(["enabled"]))
        self.assertEqual(read()[1].count("\n"), 1)
        pm.USER_TODOS_SWITCH.write_text(json.dumps({"enabled": False, "gt": 5}))
        self.assertEqual(read(), (False, ""))
        pm.USER_TODOS_SWITCH.write_text(json.dumps({"enabled": True, "gt": 6}))
        self.assertEqual(read(), (True, ""))

    def test_a_refusal_the_kernel_made_after_the_switch_flipped_is_worded_as_the_switch(self):
        # the bus checks the switch, then posts; the kernel's 409 (its own read of the same file)
        # reaches the bus as None, so the bus re-reads the switch to word it — the one refusal it
        # can tell apart without the status, and the one the agent would otherwise retry forever
        pm = self.pm

        def post(path, body, timeout=4.0):
            self.posts.append((path, body))
            pm.USER_TODOS_SWITCH.write_text(json.dumps({"enabled": False, "gt": 2}))   # flipped under the post
            return None
        pm._kernel_post = post
        text, is_err = pm._mcp_call("add_user_todo", {"text": "Need the port"})
        self.assertTrue(is_err)
        self.assertEqual(len(self.posts), 1)
        self.assertIn("turned off on this machine", text)
        self.assertNotIn("try again", text)
        self._no_machinery(text)

    def test_an_oversize_note_is_refused_at_the_tool_with_the_one_line_advice(self):
        # the kernel's caps, mirrored by name so the tool can word the refusal itself — the 400 the
        # route would answer reaches the bus as None, which reads as "try again shortly"
        pm = self.pm
        self.assertEqual((pm.USER_TODO_TEXT_CAP, pm.USER_TODO_DETAIL_CAP), (500, 4000))
        self.canned = {"ok": True, "todoId": "ut-9f2c1a34"}
        text, is_err = pm._mcp_call("add_user_todo", {"text": "Need the port", "detail": "x" * 100_000})
        self.assertTrue(is_err)
        self.assertEqual(self.posts, [], "refused before any post")
        self.assertIn("one line", text)
        self.assertIn("rest in your reply", text)
        self._no_machinery(text)
        text, is_err = pm._mcp_call("add_user_todo", {"text": "n" * 501})
        self.assertTrue(is_err)
        self.assertEqual(self.posts, [])
        text, is_err = pm._mcp_call("add_user_todo", {"text": "N" * 500, "detail": "d" * 4000})
        self.assertFalse(is_err, "at the cap it posts and the note is filed")
        self.assertEqual(len(self.posts), 1)
        self.assertIn("Noted", text)

    def test_withdraw_against_an_unreadable_store_says_so_not_no_note_of_yours(self):
        self.canned = {"ok": False, "state": "unknown", "at": None, "owner": None,
                       "error": "the request store is unreadable (see the kernel log)"}
        text, is_err = self.pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertTrue(is_err)
        self.assertNotIn("of yours", text, "the row may well be the asker's: the kernel could not look")
        self.assertIn("ut-9f2c1a34", text)
        self.assertIn("Nothing changed", text)
        self.assertIn("read", text.lower())
        self._no_machinery(text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
