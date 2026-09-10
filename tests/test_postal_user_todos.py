#!/usr/bin/env python3
"""The two postal tools for user todos (plans/user-todos.md): add_user_todo registers a need with
the person the agent works for (the kernel mints and returns the id); withdraw_user_todo takes it
back. Construction is set_working's exact shape — one MCP_TOOLS schema entry + one _mcp_call
branch each, backed by kernel routes the way _publish_working posts /working.

Pinned here:
- both tools are registered, with the right required fields;
- register posts to /usertodo AS THE CALLING SESSION (postal resolves identity from the CLI
  process env, so a subagent's call files under its parent session — documented, not fixed);
- register echoes the kernel-minted id back to the agent, with the withdraw contract in the
  same breath;
- every failure is LOUD: no session identity, no text, an unreachable kernel, an unknown or
  already-cleared id — never a silent success;
- a withdraw of a row the person already answered or dismissed, or one this session already
  withdrew, is a plain non-error answer that says what happened and when (the kernel's
  state / at / owner account, 2026-09-07): the need no longer stands, which is what the caller
  wanted; only an id that is not this session's own, or unknown, is an error (Account);
- the per-install SWITCH (the user 2026-09-03, OFF by default): while the kernel's
  user-todos-enabled.json does not say yes, tools/list omits both tools and a call anyway is
  refused plainly, before any post — read from the file per call, because the bus is its own
  long-lived process and a gear flip must land without a restart (Switch);
- a session ALREADY connected learns of the flip too (2026-09-07): the stdio server declares
  tools.listChanged at initialize, its heartbeat thread polls the switch file (one stat per
  tick) and writes an unsolicited notifications/tools/list_changed line when the offered list
  changes, so the pair appears or disappears within a few seconds in both directions; the
  request loop and the poll thread share stdout under one lock (ListChanged).

The veil on the DESCRIPTIONS and result texts (no romp machinery named) is scanned by
test_injected_voice.py. SYNTHETIC fixtures only.
"""
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_usertodos", os.path.join(BIN, "romp-postal-service"))

SID = "11111111-2222-3333-4444-555555555555"


def _switch(on):
    """Write the kernel's per-install switch file the way _set_user_todos does (or remove it)."""
    p = pm.USER_TODOS_SWITCH
    if on is None:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"enabled": bool(on), "gt": 1}))


class ToolSurface(unittest.TestCase):
    def _tool(self, name):
        return next((t for t in pm.MCP_TOOLS if t["name"] == name), None)

    def test_both_tools_are_registered(self):
        self.assertIsNotNone(self._tool("add_user_todo"))
        self.assertIsNotNone(self._tool("withdraw_user_todo"))

    def test_add_requires_text_and_offers_optional_detail(self):
        t = self._tool("add_user_todo")
        self.assertEqual(t["inputSchema"]["required"], ["text"])
        self.assertIn("detail", t["inputSchema"]["properties"])

    def test_withdraw_requires_the_id(self):
        t = self._tool("withdraw_user_todo")
        self.assertEqual(t["inputSchema"]["required"], ["id"])

    def test_descriptions_speak_as_the_person_you_work_for(self):
        for name in ("add_user_todo", "withdraw_user_todo"):
            self.assertIn("person you work for", self._tool(name)["description"])

    def test_add_teaches_withdrawal_at_registration_time(self):
        # withdrawal support mechanism #1 (plans/user-todos.md): the agent learns the contract
        # in the same breath it files the need
        self.assertIn("withdraw_user_todo", self._tool("add_user_todo")["description"])


class Dispatch(unittest.TestCase):
    def setUp(self):
        self._saved = (pm._kernel_post, pm._self_identity, pm._heartbeat)
        self.posts = []
        self.canned = {"ok": True, "todoId": "ut-9f2c1a34"}
        pm._kernel_post = lambda path, body, timeout=4.0: (self.posts.append((path, body)) or self.canned)
        pm._self_identity = lambda: (SID, "api")   # one identity resolution per call: _mcp_call reads _self_identity() before its argument checks
        pm._heartbeat = lambda *a, **k: None
        _switch(True)                                # the switch is OFF by default (2026-09-03): these pin ON

    def tearDown(self):
        pm._kernel_post, pm._self_identity, pm._heartbeat = self._saved
        _switch(None)

    # ── add_user_todo ──────────────────────────────────────────────────────────────────────────
    def test_register_posts_to_the_kernel_as_the_calling_session(self):
        out, err = pm._mcp_call("add_user_todo", {"text": "Need the auth-scheme decision to wire login",
                                                  "detail": "OAuth vs cookie"})
        self.assertFalse(err)
        self.assertEqual(self.posts, [("/usertodo", {"id": SID,
                                                     "text": "Need the auth-scheme decision to wire login",
                                                     "detail": "OAuth vs cookie"})])

    def test_register_echoes_the_minted_id_and_the_withdraw_contract(self):
        out, err = pm._mcp_call("add_user_todo", {"text": "Need a test credential"})
        self.assertFalse(err)
        self.assertIn("ut-9f2c1a34", out)
        self.assertIn("withdraw_user_todo", out, "the contract rides the confirmation")

    def test_register_without_detail_posts_an_empty_detail(self):
        out, err = pm._mcp_call("add_user_todo", {"text": "Need the port"})
        self.assertFalse(err)
        self.assertEqual(self.posts[0][1]["detail"], "", "the kernel stores no key for an empty detail")

    def test_register_without_text_is_refused_before_any_post(self):
        out, err = pm._mcp_call("add_user_todo", {"text": "   "})
        self.assertTrue(err)
        self.assertEqual(self.posts, [])

    def test_register_outside_a_session_is_refused(self):
        pm._self_identity = lambda: (None, None)   # no session resolved
        out, err = pm._mcp_call("add_user_todo", {"text": "Need the port"})
        self.assertTrue(err)
        self.assertEqual(self.posts, [])

    def test_register_failure_is_loud_never_a_silent_drop(self):
        # an unsaved need the agent believes is filed is exactly the vanishing this exists to stop
        self.canned = None                                # unreachable kernel / non-2xx
        out, err = pm._mcp_call("add_user_todo", {"text": "Need the port"})
        self.assertTrue(err)
        self.assertIn("NOT", out, "says plainly the person will not see it")

    def test_register_without_a_minted_id_is_loud_too(self):
        # the kernel answered, but with no id (a forward that failed on the far side)
        self.canned = {"ok": False, "todoId": ""}
        out, err = pm._mcp_call("add_user_todo", {"text": "Need the port"})
        self.assertTrue(err)
        self.assertIn("NOT", out)

    # ── withdraw_user_todo ─────────────────────────────────────────────────────────────────────
    def test_withdraw_posts_the_id_pair_and_confirms(self):
        self.canned = {"ok": True}
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertFalse(err)
        self.assertEqual(self.posts, [("/usertodo/withdraw", {"id": SID, "todoId": "ut-9f2c1a34"})])
        self.assertIn("Withdrawn", out)

    def test_withdraw_refused_by_a_kernel_without_the_account_is_loud(self):
        # a kernel that predates the state / at / owner account (2026-09-07) answers ok:false alone:
        # the one-size answer it always got, still an error; nothing is invented about the row
        self.canned = {"ok": False, "error": "no open todo with that id"}
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-deadbeef"})
        self.assertTrue(err, "a loud, plain answer — never a silent success")
        self.assertIn("Nothing changed", out)
        self.assertNotIn(" at ", out, "no time to report")

    def test_withdraw_with_an_unreachable_kernel_says_it_still_stands(self):
        # None is also what _kernel_post makes of the route's 502 for a remote session whose
        # kernel gave no account (a dead tunnel, an older remote): the row still stands there
        self.canned = None
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertTrue(err)
        self.assertIn("still stands", out)

    def test_withdraw_without_an_id_is_refused(self):
        out, err = pm._mcp_call("withdraw_user_todo", {})
        self.assertTrue(err)
        self.assertEqual(self.posts, [])

    def test_withdraw_outside_a_session_is_refused(self):
        pm._self_identity = lambda: (None, None)   # no session resolved
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertTrue(err)
        self.assertEqual(self.posts, [])


class Account(unittest.TestCase):
    """The kernel's ACCOUNT on an ok:false (state / at / owner, 2026-09-07) picks the answer. Two
    sessions read the one-size "No open note of yours" error as a failure and folded a MET need
    into an error path; now a row the person answered or dismissed, or one this session already
    withdrew, is a plain answer with no error flag, and only a not-yours or unknown id is an error.
    PRIVATE synthetic sid (the fixture rule)."""

    SID = "7c7c7c7c-1111-4222-8333-944444444444"
    AT = 1781200000

    def setUp(self):
        self._saved = (pm._kernel_post, pm._self_identity, pm._heartbeat)
        self.posts = []
        self.canned = {}
        pm._kernel_post = lambda path, body, timeout=4.0: (self.posts.append((path, body)) or self.canned)
        pm._self_identity = lambda: (self.SID, "api")   # one identity resolution per call: _mcp_call reads _self_identity() before its argument checks
        pm._heartbeat = lambda *a, **k: None
        _switch(True)

    def tearDown(self):
        pm._kernel_post, pm._self_identity, pm._heartbeat = self._saved
        _switch(None)

    def _withdraw(self, **acct):
        self.canned = dict({"ok": False, "error": "no open todo with that id"}, **acct)
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertEqual(self.posts[-1], ("/usertodo/withdraw", {"id": self.SID, "todoId": "ut-9f2c1a34"}))
        return out, err

    def test_answered_by_the_person_is_plain_with_the_time_and_no_error(self):
        out, err = self._withdraw(state="answered", at=self.AT, owner=True)
        self.assertFalse(err, "the need is met: not the agent's failure")
        self.assertIn("Already closed", out)
        self.assertIn("the person you work for answered 'ut-9f2c1a34'", out)
        self.assertIn(pm._when_words(self.AT), out, "says when")
        self.assertTrue(out.endswith("Nothing to withdraw."), out)

    def test_dismissed_by_the_person_is_plain_with_the_time_and_no_error(self):
        out, err = self._withdraw(state="dismissed", at=self.AT, owner=True)
        self.assertFalse(err)
        self.assertIn("Already closed", out)
        self.assertIn("the person you work for dismissed 'ut-9f2c1a34'", out)
        self.assertIn(pm._when_words(self.AT), out)
        self.assertTrue(out.endswith("Nothing to withdraw."), out)

    def test_already_withdrawn_by_this_session_is_plain_and_no_error(self):
        out, err = self._withdraw(state="withdrawn", at=self.AT, owner=True)
        self.assertFalse(err)
        self.assertIn("Already withdrawn: 'ut-9f2c1a34' was taken back", out)
        self.assertIn(pm._when_words(self.AT), out)
        self.assertTrue(out.endswith("Nothing changed."), out)

    def test_an_unknown_id_is_the_error_it_always_was(self):
        out, err = self._withdraw(state="unknown", at=None, owner=False)
        self.assertTrue(err)
        self.assertEqual(out, "No note 'ut-9f2c1a34' of yours. Nothing changed.")

    def test_the_askers_own_row_in_an_unreadable_shape_is_an_error_that_says_so(self):
        # the kernel's account for a malformed closing stamp is state unknown with owner True and
        # an error naming the stamp: neither "not yours" nor closed
        why = "malformed closing stamp on ut-9f2c1a34: resolved=True (a stamp is {kind: answered | dismissed | withdrawn, t})"
        out, err = self._withdraw(state="unknown", at=None, owner=True, error=why)
        self.assertTrue(err)
        self.assertIn("Couldn't read the record of 'ut-9f2c1a34'", out)
        self.assertIn(why, out, "relays what the kernel could not read")
        self.assertNotIn("of yours", out)
        self.assertIn("Nothing changed", out)
        self.assertIn("say it directly", out, "the agent's move when the record cannot say")
        for word in ("romp", "card", "board", "goal", "cleared", "dismissal", "nudge", "<!--"):
            self.assertNotIn(word, out.lower(), "%r names machinery the agent cannot see" % word)
        # without any error text from the kernel the sentence still stands on its own
        self.canned = {"ok": False, "state": "unknown", "at": None, "owner": True}
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertTrue(err)
        self.assertIn("its closing record is unreadable", out)

    def test_not_the_askers_row_is_an_error_whatever_its_state_says(self):
        # the kernel reports another session's rows as unknown; a kernel that ever described one
        # would still be answered on `owner` first, so no agent hears "the person answered it"
        # about a note that was never its own
        out, err = self._withdraw(state="answered", at=self.AT, owner=False)
        self.assertTrue(err)
        self.assertIn("No note 'ut-9f2c1a34' of yours", out)

    def test_a_closed_row_with_no_time_known_drops_the_time_phrase(self):
        out, err = self._withdraw(state="answered", owner=True)
        self.assertFalse(err)
        self.assertEqual(out, "Already closed: the person you work for answered 'ut-9f2c1a34'. "
                              "Nothing to withdraw.")

    def test_every_plain_answer_still_says_what_happened(self):
        # LOUD, never a silent success: each non-error answer names the id and the outcome
        for state, word in (("answered", "answered"), ("dismissed", "dismissed"), ("withdrawn", "withdrawn")):
            out, err = self._withdraw(state=state, at=self.AT, owner=True)
            self.assertFalse(err)
            self.assertIn("ut-9f2c1a34", out)
            self.assertIn(word, out)
            self.assertTrue(out.startswith("Already"), out)

    def test_ok_true_is_withdrawn_whatever_else_rides_along(self):
        self.canned = {"ok": True, "state": "withdrawn", "at": self.AT, "owner": True}
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertFalse(err)
        self.assertIn("Withdrawn", out)


class WhenWords(unittest.TestCase):
    """_when_words: the closing time as a phrase a reader can place (today, yesterday, or a dated
    day), in local time. TZ is pinned to UTC for the run so the expected strings are exact."""

    NOON = 1781179200            # 2026-06-11 12:00:00 UTC

    def setUp(self):
        import time as _t
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        _t.tzset()

    def tearDown(self):
        import time as _t
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        _t.tzset()

    def test_same_day_is_today(self):
        self.assertEqual(pm._when_words(self.NOON - 3600, now=self.NOON), " at 11:00 today")

    def test_the_day_before_is_yesterday(self):
        self.assertEqual(pm._when_words(self.NOON - 86400, now=self.NOON), " at 12:00 yesterday")

    def test_older_carries_the_date(self):
        self.assertEqual(pm._when_words(self.NOON - 3 * 86400, now=self.NOON), " on 2026-06-08 at 12:00")

    def test_a_future_stamp_carries_the_date_too(self):
        # a remote kernel's clock ahead of ours: never "today" for a time that has not come
        self.assertEqual(pm._when_words(self.NOON + 2 * 86400, now=self.NOON), " on 2026-06-13 at 12:00")

    def test_no_time_is_the_empty_phrase(self):
        for t in (None, 0, "", "soon", -5, 10 ** 20):
            self.assertEqual(pm._when_words(t, now=self.NOON), "", repr(t))

    def test_defaults_to_the_clock(self):
        import time as _t
        self.assertTrue(pm._when_words(int(_t.time())).endswith(" today"))


class Switch(unittest.TestCase):
    """The per-install switch, bus side (the user 2026-09-03). The kernel writes
    STATE/user-todos-enabled.json = {"enabled": bool, "gt": ms}; the bus reads THAT file (never
    user-todos.json, which is the todo store) on every tools/list and every call."""

    def setUp(self):
        self._saved = (pm._kernel_post, pm._self_identity, pm._heartbeat)
        self.posts = []
        pm._kernel_post = lambda path, body, timeout=4.0: (self.posts.append((path, body))
                                                           or {"ok": True, "todoId": "ut-9f2c1a34"})
        pm._self_identity = lambda: (SID, "api")   # one identity resolution per call: _mcp_call reads _self_identity() before its argument checks
        pm._heartbeat = lambda *a, **k: None
        _switch(None)

    def tearDown(self):
        pm._kernel_post, pm._self_identity, pm._heartbeat = self._saved
        _switch(None)

    def test_the_switch_reads_the_kernels_file_not_the_store(self):
        self.assertEqual(pm.USER_TODOS_SWITCH.name, "user-todos-enabled.json")
        self.assertEqual(pm.USER_TODOS_SWITCH.parent, pm.STATE.parent, "the kernel's STATE dir")
        self.assertNotEqual(pm.USER_TODOS_SWITCH.name, "user-todos.json", "that file is the todo STORE")

    def test_absent_garbled_or_false_all_read_off(self):
        self.assertFalse(pm._user_todos_on(), "no file = the shipped default, OFF")
        _switch(False)
        self.assertFalse(pm._user_todos_on())
        pm.USER_TODOS_SWITCH.write_text("not json")
        self.assertFalse(pm._user_todos_on(), "a garbled file must not turn the feature on")
        pm.USER_TODOS_SWITCH.write_text(json.dumps(["enabled"]))
        self.assertFalse(pm._user_todos_on())
        _switch(True)
        self.assertTrue(pm._user_todos_on())

    def test_reading_never_creates_the_file(self):
        pm._user_todos_on()
        self.assertFalse(pm.USER_TODOS_SWITCH.exists(), "shipping never turns it on")

    def test_the_tools_list_omits_both_tools_while_off_and_offers_them_while_on(self):
        names_off = {t["name"] for t in pm._tools_offered()}
        self.assertNotIn("add_user_todo", names_off)
        self.assertNotIn("withdraw_user_todo", names_off)
        self.assertEqual(names_off, {t["name"] for t in pm.MCP_TOOLS} - set(pm.USER_TODO_TOOLS),
                         "every OTHER tool is still offered")
        _switch(True)
        self.assertEqual(pm._tools_offered(), pm.MCP_TOOLS, "on: the full list, same objects")

    def test_the_list_is_read_per_call_no_restart_needed(self):
        # the bus is a separate long-lived process: a gear flip must land on the next list/call
        self.assertNotIn("add_user_todo", {t["name"] for t in pm._tools_offered()})
        _switch(True)
        self.assertIn("add_user_todo", {t["name"] for t in pm._tools_offered()})
        _switch(False)
        self.assertNotIn("add_user_todo", {t["name"] for t in pm._tools_offered()})

    def test_the_stdio_server_answers_tools_list_from_the_gated_list(self):
        import inspect
        src = inspect.getsource(pm.mcp)
        self.assertIn('"tools": _tools_offered()', src, "tools/list goes through the gate")
        self.assertNotIn('"tools": MCP_TOOLS}', src, "…never the raw constant")

    def test_a_call_anyway_is_refused_plainly_before_any_post(self):
        # a session that connected while the switch was on still holds the tool
        out, err = pm._mcp_call("add_user_todo", {"text": "Need the auth-scheme decision"})
        self.assertTrue(err)
        self.assertIn("turned off on this machine", out)
        self.assertIn("will NOT see it", out, "the agent must not believe the need was filed")
        out, err = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})
        self.assertTrue(err)
        self.assertIn("turned off on this machine", out)
        self.assertEqual(self.posts, [], "nothing reached the kernel")

    def test_the_refusal_outranks_the_identity_and_shape_checks(self):
        # the switch is the first gate: a session with no identity or no text hears the same one
        # plain reason, never a second-order refusal about a call that could not have succeeded
        pm._self_identity = lambda: (None, None)   # no session resolved
        self.assertIn("turned off", pm._mcp_call("add_user_todo", {"text": "  "})[0])
        self.assertIn("turned off", pm._mcp_call("withdraw_user_todo", {})[0])
        self.assertEqual(self.posts, [])

    def test_the_refusals_keep_the_veil(self):
        # the same vocabulary rule the descriptions ride (test_injected_voice.py sweeps the live
        # branches; the OFF branches are rendered there too) — pinned here at the source of the text
        for text in (pm.USER_TODOS_OFF_ADD, pm.USER_TODOS_OFF_WITHDRAW):
            for word in ("romp", "card", "board", "goal", "gear", "nudge", "cleared", "dismissal"):
                self.assertNotIn(word, text.lower(), "%r names machinery the agent cannot see" % word)

    def test_on_the_call_goes_through_as_before(self):
        _switch(True)
        out, err = pm._mcp_call("add_user_todo", {"text": "Need the auth-scheme decision"})
        self.assertFalse(err)
        self.assertEqual([p[0] for p in self.posts], ["/usertodo"])


class ListChanged(unittest.TestCase):
    """A flip of the switch reaches a session that is ALREADY connected (2026-09-07). Before this,
    tools/list read the file live, so a NEW connection saw the right list, but a session running
    while the gear was flipped kept the list it had until its next restart or revival: the shim
    declared no listChanged capability and never wrote notifications/tools/list_changed. Now the
    heartbeat thread also watches the switch file (one stat per tick; the file is read only when
    its mtime or size moved) and, once the client has finished initializing, writes the
    notification when the OFFERED list changed — a flip in either direction, never a rewrite that
    keeps the value. The request loop and the poller share stdout, so both write under one lock.

    Drives the REAL shim (bin/romp-postal-service mcp) as a subprocess over its stdio, hermetic:
    the bus and kernel ports point at a closed port, the sessions seam is an empty list, no session
    identity, and the switch poll is shortened through its env knob so the whole class takes
    seconds. A poll interval this short is a test setting; the shipped default is pinned to a
    few seconds, which is what the guide promises."""
    POLL = 0.2

    def setUp(self):
        _switch(None)
        self.tmp = tempfile.mkdtemp()
        seam = os.path.join(self.tmp, "sessions.json")
        with open(seam, "w") as f:
            f.write("[]")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]          # bound-then-released: nothing answers there
        s.close()
        env = dict(os.environ)
        for k in ("CLAUDE_CODE_SESSION_ID", "ROMP_SID"):
            env.pop(k, None)
        env.update({
            # pm's OWN state root, so the shim reads the very file _switch() writes. Not
            # os.environ["XDG_STATE_HOME"]: under xdist every test module is imported into each
            # worker and a sibling module re-points that variable at import (test_injected_voice),
            # while pm.USER_TODOS_SWITCH was fixed when THIS module loaded. ROMP_STATE_DIR outranks
            # XDG in the shim, the same way a live kernel's export does.
            "ROMP_STATE_DIR": str(pm.STATE.parent),
            "ROMP_POSTAL_SWITCH_POLL": str(self.POLL),
            "ROMP_POSTAL_PORT": str(closed),
            "ROMP_KERNEL_PORT": str(closed),
            "ROMP_POSTAL_CLIENT_ONLY": "1",   # with peers off: ensure() never spawns a bus
            "ROMP_POSTAL_PEERS": "0",
            "ROMP_SESSIONS_FILE": seam,
            "ROMP_SERVE_TOKEN": "test-token",
        })
        self.p = subprocess.Popen([sys.executable, os.path.join(BIN, "romp-postal-service"), "mcp"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def tearDown(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
            self.p.wait(timeout=10)
        _switch(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def _recv(self, timeout):
        """The next stdout line as JSON, or None when nothing arrived within `timeout` (or EOF)."""
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return None
        return json.loads(line) if line else None

    def _init(self):
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "t", "version": "1"}}})
        res = self._recv(30)
        self.assertIsNotNone(res, "the shim answered initialize")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def _tools(self, rid):
        self._send({"jsonrpc": "2.0", "id": rid, "method": "tools/list"})
        for _ in range(4):   # an unsolicited notification may interleave; skip it, keep the reply
            res = self._recv(30)
            self.assertIsNotNone(res, "the shim answered tools/list")
            if res.get("id") == rid:
                return {t["name"] for t in res["result"]["tools"]}
        self.fail("no tools/list reply")

    def _await_list_changed(self):
        """The unsolicited line: no id, the list_changed method, within a few polls (generous under load)."""
        note = self._recv(max(15.0, self.POLL * 20))
        self.assertIsNotNone(note, "an unsolicited notifications/tools/list_changed line arrived")
        self.assertEqual(note.get("method"), "notifications/tools/list_changed")
        self.assertNotIn("id", note, "a notification, not a response")
        self.assertEqual(note.get("jsonrpc"), "2.0")

    def test_initialize_declares_list_changed(self):
        res = self._init()
        self.assertEqual(res["id"], 1)
        self.assertIs(res["result"]["capabilities"]["tools"].get("listChanged"), True,
                      "the client is told to expect notifications/tools/list_changed")

    def test_a_flip_on_reaches_the_connected_session(self):
        self._init()
        self.assertNotIn("add_user_todo", self._tools(2), "off at connect: not offered")
        _switch(True)
        self._await_list_changed()
        names = self._tools(3)
        self.assertIn("add_user_todo", names)
        self.assertIn("withdraw_user_todo", names)

    def test_a_flip_off_reaches_it_too(self):
        _switch(True)
        self._init()
        self.assertIn("add_user_todo", self._tools(2), "on at connect: offered")
        _switch(False)
        self._await_list_changed()
        self.assertNotIn("add_user_todo", self._tools(3))
        _switch(True)   # and back on, the same session, a third notice
        self._await_list_changed()
        self.assertIn("add_user_todo", self._tools(4))

    def test_nothing_changed_nothing_said(self):
        self._init()
        self.assertIsNone(self._recv(self.POLL * 6), "several polls with the file untouched: silence")
        _switch(False)   # absent -> written as false: the offered list is the same
        self.assertIsNone(self._recv(self.POLL * 6), "a write that keeps the value is not a list change")

    def test_the_default_poll_is_a_few_seconds(self):
        # the guide's promise: a connected session gains or loses the tools within a few seconds
        self.assertGreaterEqual(pm.SWITCH_POLL, 1.0, "not a busy loop")
        self.assertLessEqual(pm.SWITCH_POLL, 5.0)

    def test_both_stdout_writers_take_the_one_lock(self):
        import inspect
        src = inspect.getsource(pm.mcp)
        self.assertIn("out_lock = threading.Lock()", src)
        self.assertIn("with out_lock:", src, "reply() writes a whole line under the lock")
        self.assertIn('"capabilities": {"tools": {"listChanged": True}}', src)
        self.assertIn('"method": "notifications/tools/list_changed"', src)
        self.assertIn("_switch_poll_loop, args=(", src, "the poll has its own thread: the heartbeat loop "
                      "ends once the bus calls the session local, and the poll must outlive it")


class SwitchWatch(unittest.TestCase):
    """The change detector behind the poll, in-process and deterministic: one stat per call, the
    file read only when its signature moved, True only when the OFFERED list changed."""

    def setUp(self):
        _switch(None)

    def tearDown(self):
        _switch(None)

    def test_first_call_baselines_and_a_flip_is_one_true(self):
        w = pm._SwitchWatch()
        self.assertFalse(w.flipped(), "nothing moved since construction")
        _switch(True)
        self.assertTrue(w.flipped(), "off -> on")
        self.assertFalse(w.flipped(), "already reported")
        _switch(False)
        self.assertTrue(w.flipped(), "on -> off")
        self.assertFalse(w.flipped())

    def test_a_rewrite_that_keeps_the_value_is_not_a_change(self):
        _switch(True)
        w = pm._SwitchWatch()
        pm.USER_TODOS_SWITCH.write_text(json.dumps({"enabled": True, "gt": 424242}))   # the kernel restamping gt
        self.assertFalse(w.flipped())
        _switch(None)
        self.assertTrue(w.flipped(), "the file going away reads as OFF, a change from on")
        _switch(False)
        self.assertFalse(w.flipped(), "absent -> false: the same offered list")

    def test_it_stats_and_reads_only_on_a_moved_signature(self):
        w = pm._SwitchWatch()
        reads = []
        saved = pm._user_todos_on
        pm._user_todos_on = lambda: reads.append(1) or False
        try:
            w.flipped(); w.flipped(); w.flipped()
            self.assertEqual(reads, [], "an unchanged file is never read, only stat'ed")
            _switch(False)
            w.flipped()
            self.assertEqual(reads, [1], "one read per moved signature")
        finally:
            pm._user_todos_on = saved


if __name__ == "__main__":
    unittest.main()
