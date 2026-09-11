#!/usr/bin/env python3
"""POST /auth and GET /auth (the user 2026-09-11): the tab menu's per-session Billing pick — the setAuth WS
op — as a one-shot token-gated route beside /fork, so a terminal, a script or another machine's plugin can
read or switch what a live session bills without hand-driving the WS, and `romp billing` has a door. Until
this route the pick existed only as the WS message, so the long-running sessions created before the picker
existed could not be given one from outside a browser.

Drives the REAL Handler over HTTP (the test_rename_route.py pattern). Only the named seams are stubbed: the
backend is a fake that records the setter call and answers like SdkBackend.set_auth (a verdict, a reason
through auth_unavailable_why), the liveness merge is a fixed row shaped like Sessions.live's, the remote
seams (_host_for_sid, _remote_forward) are stubbed the way test_kernel_remote_end_interrupt.py stubs them,
and the park gate (_ops_gate) is forced for the mid-turn case. On origin/main every request here is a 404
"not found" in text/plain: the route does not exist. Synthetic only: session `web`, a placeholder sid, host
TESTHOST, an invented account label."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_auth_route", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
FAR = {"host": "TESTHOST", "local_port": 1, "token": "t"}
ACCT = "someone@example.com"
REPLY_KEYS = {"ok", "queued", "id", "name", "auth", "authLive", "authPending", "authPicked", "acct"}


class _BE:
    """SdkBackend's contract: auth_pick_refusal names why a pick cannot apply ("" when it can) and is asked
    BEFORE any park; set_auth is the verdict. A taken pick lands on the liveness row the way the real
    setter's does (auth = the pick, authPending until the reconnect confirms), so the reply is shown to be
    read AFTER the setter, not before."""

    def __init__(self, row, ok=True, why=""):
        self.row, self.ok, self.why, self.calls, self.asked = row, ok, why, [], []

    def set_auth(self, sid, value):
        self.calls.append((sid, value))
        if self.ok:
            self.row.update(auth=value, authPending=True, authPicked=True)
        return self.ok

    def auth_pick_refusal(self, sid, side):
        self.asked.append((sid, side))
        return self.why


class _CodexLike:
    """A backend with no billing switch (CodexBackend: auth is machine-global) that is BUSY: set_auth's
    default False and no auth_pick_refusal, a turn in flight."""

    def set_auth(self, sid, value):
        return False

    def busy(self, sid):
        return True


class AuthRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        # the liveness row the status push builds from, for an SDK session picked onto the key
        self.row = {"state": "waiting", "backend": "sdk", "auth": "key", "authLive": "key",
                    "authPending": False, "authPicked": True}
        self.be = _BE(self.row)
        names = ("_tmux_sessions", "_live_names", "_kernel_knows", "_name_of", "_claude_account_label",
                 "_push_soon", "_host_for_sid", "_ops_gate", "_mark_views_dirty")
        self._saved = {k: getattr(km, k) for k in names}
        self._saved_live, self._saved_backend = km.Sessions.live, km.Sessions.backend_for
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: {"web": SID}
        km._kernel_knows = lambda sid: str(sid) == SID
        km._name_of = lambda sid: "web" if str(sid) == SID else ""
        km._claude_account_label = lambda: ACCT
        km._push_soon = lambda: None
        km._host_for_sid = lambda sid: None
        km._ops_gate = lambda sid: False                 # quiet: nothing parks unless a test says so
        km._mark_views_dirty = lambda: None
        km.Sessions.live = staticmethod(lambda: {SID: self.row})
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._pending_ops.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(km, k, v)
        km.Sessions.live, km.Sessions.backend_for = self._saved_live, self._saved_backend
        km._pending_ops.clear()

    def _req(self, method, path, body=None, token=True, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = os.environ["ROMP_SERVE_TOKEN"]
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method,
                                     data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode()
                return r.status, (json.loads(text) if text.startswith("{") else text)
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            return e.code, (json.loads(text) if text.startswith("{") else text)

    def _post(self, body, **kw):
        return self._req("POST", "/auth", body, **kw)

    # ---- the switch, by name and by sid ----
    def test_a_live_name_switches_through_the_setter_and_answers_the_hover_fields(self):
        st, r = self._post({"name": "web", "value": "login"})
        self.assertEqual(st, 200)
        self.assertEqual(self.be.calls, [(SID, "login")], "one setter call, the WS op's own door")
        self.assertEqual(set(r), REPLY_KEYS, "the tab hover's fields and nothing else — no key material")
        self.assertEqual((r["ok"], r["queued"], r["id"], r["name"]), (True, False, SID, "web"))
        self.assertEqual((r["auth"], r["authPending"], r["authPicked"], r["acct"]), ("login", True, True, ACCT),
                         "read AFTER the setter: the pick, applying, now an explicit pick")

    def test_a_sid_target_reaches_the_setter_directly(self):
        st, r = self._post({"id": SID, "value": "key"})
        self.assertEqual((st, r["ok"], r["id"], r["name"]), (200, True, SID, "web"))
        self.assertEqual(self.be.calls, [(SID, "key")])

    # ---- refusals ----
    def test_a_bad_value_is_refused_without_acting(self):
        st, r = self._post({"id": SID, "value": "credit-card"})
        self.assertEqual(st, 200)
        self.assertIs(r["ok"], False)
        self.assertEqual(r["error"], 'value must be "login" or "key"')
        self.assertEqual(self.be.calls, [], "nothing reached the backend")

    def test_missing_fields_are_a_400(self):
        st, r = self._post({})
        self.assertEqual((st, r["ok"], r["error"]), (400, False, "id or name required"))
        st, r = self._post({"value": "key"})
        self.assertEqual(st, 400)
        self.assertEqual(self.be.calls, [])

    def test_a_malformed_body_is_a_400_naming_what_arrived(self):
        st, r = self._post(None, raw=b"[]")
        self.assertEqual((st, r["error"]), (400, "body must be a JSON object, got []"))
        st, r = self._post(None, raw=b"{not json")
        self.assertEqual((st, r["error"]), (400, "body is not JSON"))

    def test_an_unknown_name_is_a_plain_refusal(self):
        st, r = self._post({"name": "nope", "value": "key"})
        self.assertEqual(st, 200)
        self.assertIs(r["ok"], False)
        self.assertEqual(r["error"], 'no live session named "nope" (a dormant one can be reached by sid)')
        self.assertEqual(self.be.calls, [])

    def test_the_backends_refusal_carries_its_own_reason_before_any_write(self):
        # the T124 login gate / the managed-helper gate: auth_pick_refusal names why, and the setter is never
        # reached — the reason is asked BEFORE the park or the write (review of 178968e6)
        self.be.why = "no Claude login signed in on this machine"
        st, r = self._post({"id": SID, "value": "login"})
        self.assertEqual(st, 200)
        self.assertIs(r["ok"], False)
        self.assertEqual(r["error"], "Couldn't switch the account this session bills: no Claude login signed in on this machine.")
        self.assertEqual(self.be.asked, [(SID, "login")], "the backend was asked for its reason")
        self.assertEqual(self.be.calls, [], "and the setter was never reached")

    def test_a_tmux_session_has_no_such_control(self):
        # the real tmux backend: SessionBackend.set_auth's default False, and no auth_pick_refusal
        km.Sessions.backend_for = staticmethod(lambda sid: km._TMUX)
        st, r = self._post({"id": SID, "value": "key"})
        self.assertEqual(st, 200)
        self.assertIs(r["ok"], False)
        self.assertIn("a terminal (tmux) or Codex session has no such control", r["error"])

    def test_a_busy_session_without_the_switch_is_refused_not_parked(self):
        # review of 178968e6: _ops_gate parks whenever the backend is busy, so a mid-turn tmux or Codex pick
        # used to answer queued:true and sit in the FIFO until the drain handed it to a backend with nothing
        # to apply — the switch never happened and nobody was told. Capability is checked BEFORE the park.
        km._ops_gate = lambda sid: True                  # busy: the gate would park
        for be, label in ((km._TMUX, "tmux"), (_CodexLike(), "codex")):
            km.Sessions.backend_for = staticmethod(lambda sid, be=be: be)
            st, r = self._post({"id": SID, "value": "key"})
            self.assertEqual(st, 200, label)
            self.assertIs(r["ok"], False, label)
            self.assertNotIn("queued", r, label)
            self.assertIn("has no such control", r["error"], label)
            self.assertEqual(km._pending_ops.get(SID), None, "%s: nothing parked" % label)

    def test_the_ws_door_refuses_a_busy_session_without_the_switch_and_parks_nothing(self):
        # the same guard through _drive's setAuth arm (the tab menu's door): the toast names the refusal and the
        # FIFO stays empty — before, the pick parked behind the turn and was dropped at the drain, silently
        km._ops_gate = lambda sid: True
        for be, label in ((km._TMUX, "tmux"), (_CodexLike(), "codex")):
            km.Sessions.backend_for = staticmethod(lambda sid, be=be: be)
            out = []
            self.assertTrue(km._drive({"type": "setAuth", "id": SID, "value": "key"}, {"send": out.append}), label)
            frames = [json.loads(x) for x in out]
            warns = [f for f in frames if f.get("type") == "warn"]
            self.assertEqual(len(warns), 1, "%s: one refusal toast: %r" % (label, frames))
            self.assertIn("a terminal (tmux) or Codex session has no such control", warns[0]["text"], label)
            self.assertEqual(km._pending_ops.get(SID), None, "%s: nothing parked" % label)

    def test_a_side_the_box_cannot_bill_is_refused_before_the_park_too(self):
        km._ops_gate = lambda sid: True                  # busy SDK session, but the pick names a side it cannot bill
        self.be.why = "no apiKeyHelper configured"
        st, r = self._post({"id": SID, "value": "key"})
        self.assertEqual((st, r["ok"]), (200, False))
        self.assertEqual(r["error"], "Couldn't switch the account this session bills: no apiKeyHelper configured.")
        self.assertEqual(km._pending_ops.get(SID), None, "refused now, never parked for a turn end that changes nothing")
        self.assertEqual(self.be.calls, [])

    def test_an_ended_session_is_refused_for_a_switch_and_a_read(self):
        # review of 178968e6: an ended SDK session (its record says alive: false) was admitted by sid — the
        # names registry still knows it and owns() only stats the record — so the pick was persisted and the
        # box's default reseeded from a dead session, and the reply carried an empty auth. The backend's
        # predicate names it, before any write; the read is refused the same way, never "billing unknown".
        ended = "that session has ended; a dormant one, still alive but not running, can be reached by sid"
        self.be.why = ended
        st, r = self._post({"id": SID, "value": "login"})
        self.assertEqual((st, r["ok"]), (200, False))
        self.assertEqual(r["error"], "Couldn't switch the account this session bills: %s." % ended)
        self.assertEqual(self.be.calls, [], "nothing written for a dead session")
        self.assertEqual(km._pending_ops.get(SID), None)
        st, r = self._req("GET", "/auth?id=%s" % SID)
        self.assertEqual((st, r["ok"], r["error"]), (200, False, ended))
        self.assertEqual(self.be.asked[-1], (SID, ""), "a read asks with no side: only the record can refuse it")

    def test_a_token_less_request_is_refused_before_the_body_is_read(self):
        st, body = self._post({"id": SID, "value": "key"}, token=False)
        self.assertEqual(st, 403)
        self.assertEqual(self.be.calls, [], "the gate holds: a co-tenant cannot switch what a session bills")

    # ---- parked mid-turn / compacting ----
    def test_a_mid_turn_pick_parks_and_says_so_truthfully(self):
        km._ops_gate = lambda sid: True                  # the session is mid-turn or compacting
        self.row.update(auth="login", authLive="login")
        st, r = self._post({"name": "web", "value": "key"})
        self.assertEqual(st, 200)
        self.assertEqual((r["ok"], r["queued"]), (True, True))
        self.assertEqual(self.be.calls, [], "parked: the setter runs when the turn ends, not now")
        self.assertEqual(km._pending_ops.get(SID), [("auth", "key")], "the same FIFO the menu's pick parks in")
        self.assertEqual((r["auth"], r["authPending"]), ("login", False), "still the old side until it fires")

    # ---- the read ----
    def test_get_reads_the_billing_without_acting(self):
        self.row.update(auth="login", authLive="login", authPicked=True)
        st, r = self._req("GET", "/auth?id=web")
        self.assertEqual(st, 200)
        self.assertEqual(set(r), REPLY_KEYS)
        self.assertEqual((r["ok"], r["queued"], r["auth"], r["authLive"], r["authPending"], r["authPicked"], r["acct"]),
                         (True, False, "login", "login", False, True, ACCT))
        self.assertEqual(self.be.calls, [], "a read changes nothing")
        st, r = self._post({"id": SID})                  # a value-less POST is the same read
        self.assertEqual((st, r["ok"], r["auth"]), (200, True, "login"))
        self.assertEqual(self.be.calls, [])

    def test_an_unpicked_session_reads_unpicked(self):
        # the long-running sessions created before the pick existed: auth is the box's fallback, and
        # authPicked says no pick stands behind it (the surfaces render "unpicked" off this field)
        self.row.update(authPicked=False)
        st, r = self._req("GET", "/auth?id=%s" % SID)
        self.assertEqual((st, r["ok"], r["auth"], r["authPicked"]), (200, True, "key", False))

    def test_get_without_an_id_is_a_400(self):
        st, r = self._req("GET", "/auth")
        self.assertEqual((st, r["ok"], r["error"]), (400, False, "id or name required"))

    def test_get_is_token_gated_too(self):
        st, body = self._req("GET", "/auth?id=web", token=False)
        self.assertEqual(st, 403, "the reply names a login account: never for a token-less reader")


class RemoteAuthRoute(unittest.TestCase):
    """A sid _host_for_sid places on TESTHOST: forwarded over its tunnel with the body, the /send arm's shape."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _forward(self, method, path, body, far_reply):
        crossed = []

        def rec(r, p, b):
            crossed.append((r, p, b))
            return far_reply

        def no_local(sid):
            self.fail("a remote session's billing must not touch the local backend")

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method, data=data,
                                     headers={"Content-Type": "application/json",
                                              "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with mock.patch.object(km, "_tmux_sessions", lambda: {}), \
             mock.patch.object(km, "_live_names", lambda tm: {}), \
             mock.patch.object(km, "_host_for_sid", lambda sid: dict(FAR)), \
             mock.patch.object(km, "_remote_forward", rec), \
             mock.patch.object(km.Sessions, "backend_for", staticmethod(no_local)), \
             mock.patch.object(km.Sessions, "live", staticmethod(no_local)):
            with urllib.request.urlopen(req, timeout=10) as r:
                code, resp = r.status, json.loads(r.read().decode())
        self.assertEqual(len(crossed), 1, "exactly one forward over the tunnel")
        return code, resp, crossed[0]

    def test_the_switch_crosses_with_its_value_and_a_dead_tunnel_is_not_an_ok(self):
        code, resp, (r, path, body) = self._forward("POST", "/auth", {"id": SID, "value": "login"}, None)
        self.assertEqual((r["host"], path), ("TESTHOST", "/auth"))
        self.assertEqual(body, {"id": SID, "value": "login"}, "the pick rides to the far kernel")
        self.assertEqual(code, 200)
        self.assertIs(resp.get("ok"), False, "a dead tunnel is not an ok — nothing was switched")
        self.assertIn("TESTHOST", resp["error"], "the reply names the host that isn't answering")
        self.assertIn("billing not switched", resp["error"])

    def test_the_far_reply_rides_back_verbatim(self):
        far = {"ok": True, "queued": True, "id": SID, "name": "api", "auth": "key", "authLive": "",
               "authPending": False, "authPicked": True, "acct": ""}
        code, resp, _ = self._forward("POST", "/auth", {"id": SID, "value": "login"}, dict(far))
        self.assertEqual((code, resp), (200, far), "the far kernel's answer, queued flag and all, never rewritten")
        refusal = {"ok": False, "error": "x"}
        code, resp, _ = self._forward("POST", "/auth", {"id": SID, "value": "key"}, dict(refusal))
        self.assertEqual((code, resp), (200, refusal), "its refusal comes back as itself")

    def test_a_read_crosses_without_a_value(self):
        code, resp, (_, path, body) = self._forward("GET", "/auth?id=%s" % SID, None, None)
        self.assertEqual((path, body), ("/auth", {"id": SID}), "a read carries no value: the far kernel reads")
        self.assertIs(resp.get("ok"), False)
        self.assertIn("billing not read", resp["error"])


class StatusCarriesAuthPicked(unittest.TestCase):
    """The liveness merge and the status push carry `authPicked` beside `auth` (source-level: the merge is a
    static method over the SDK backend's rows, the push a per-session dict deep in the pusher)."""

    def test_the_merge_carries_the_backend_rows_flag(self):
        class FakeSdk:
            def live_sessions(self):
                return {SID: {"state": "waiting", "auth": "key", "authPicked": False}}
        with mock.patch.object(km, "_sdk", lambda: FakeSdk()), \
             mock.patch.object(km._TMUX, "live_sessions", lambda: {}):
            row = km.Sessions.live()[SID]
        self.assertEqual((row["auth"], row["authPicked"]), ("key", False))

    def test_the_status_push_reads_it_off_the_merged_row(self):
        import inspect
        src = inspect.getsource(km)
        i = src.index('"auth": tm.get("auth", ""),')
        self.assertIn('"authPicked": bool(tm.get("authPicked")),', src[i:i + 3000],
                      "the status dict carries authPicked beside auth")


if __name__ == "__main__":
    unittest.main()
