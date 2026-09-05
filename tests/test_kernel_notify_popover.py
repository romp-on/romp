#!/usr/bin/env python3
"""The bell popover (2026-09-05): the bell's tap opens a small anchored card whose rows are the
switches ONE tap used to flip together, plus the turn-finished switch and a test button.

What these pin, by layer:

  * the store — "*turns" is a second reserved key in notify-cards.json: GET/POST /notify-turns
    read and flip it, its own shell push repaints every dashboard, and the prune that drops ids
    whose card left the feed keeps BOTH reserved keys.
  * the test button — POST /push/test sends ONE notification to the asking device's subscription
    and returns the push service's answer {ok, status, detail}; an unknown endpoint says so; a
    dead one (404/410) is pruned exactly like the fan-out prunes it; missing crypto is the same
    loud 500 the subscribe route gives. _push_post is the one HTTP path under both; _push_send_one
    keeps its prune semantics (False on 404/410 ONLY) on top of it.
  * the turn-finished push — _turn_notify_tick fires on a session's turn-end KEY moving (the Stop
    hook's lastStopAt, or a STOPPED states/ transition), only with the master AND the switch on
    and the session unmuted, with a silent first-sight baseline; the event travels to trusted
    peers the bell-event way.
  * the one-buzz-per-turn-end rule — _buzz_claim: the bell event and the turn push both claim
    (sid, turn-end key); the first to file buzzes and the other yields; bell events never
    suppress each other; a sid-less event always passes.
  * the shell — the popover markup (four rows), the menu TOKENS with their dark fallbacks, the
    theme blocks that define them, the Escape chain, the WS repaint, and the bell glyph reading
    THIS device (master AND subscribed).

Synthetic only: placeholder sids, the notes-api demo sessions (web/api), invented reply text.
"""
import io
import json
import os
import threading
import time
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads (the webpush test's 2026-08-08 lesson: a raw run must never
# touch the live push store or rotate the real VAPID key).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
from pathlib import Path
_STATE_TD = tempfile.TemporaryDirectory()
jd.STATE = Path(_STATE_TD.name)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel_notify_popover", os.path.join(BIN, "romp-kernel")).load_module()

SID_WEB = "11111111-2222-4333-8444-555555555501"
SID_API = "11111111-2222-4333-8444-555555555502"


def _serve_get(path, headers=None):
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    h.headers = dict(headers or {})
    h.path = path
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO()
    h.close_connection = True
    captured = {}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_GET()
    return captured.get("status"), h.wfile.getvalue()


def _reset_store():
    for name in ("notify-cards.json", "push-subscriptions.json", "session-flags.json"):
        try:
            (jd.STATE / name).unlink()
        except OSError:
            pass
    km._notify_cards_cache.clear()
    km._TURN_PREV.clear()
    km._PUSH_BUZZED.clear()


class _LoopbackMixin:
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body, token=True, raw=None):
        import urllib.request, urllib.error
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = km.TOKEN
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()


class TurnSwitchStore(_LoopbackMixin, unittest.TestCase):
    """GET/POST /notify-turns — the turn-finished switch's kernel half, a second reserved key
    beside the master in notify-cards.json."""

    def setUp(self):
        _reset_store()

    def test_default_off_and_gated(self):
        status, _ = _serve_get("/notify-turns")
        self.assertEqual(status, 403, "behind the serve token like every page")
        status, body = _serve_get("/notify-turns", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual((status, json.loads(body)), (200, {"on": False}))
        self.assertFalse(km._notify_turns_on())

    def test_post_flips_persists_and_broadcasts_its_own_message(self):
        sent = []
        with mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            code, body = self._post("/notify-turns", {"on": True})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True, "on": True}))
        self.assertTrue(km._notify_turns_on())
        self.assertEqual(json.loads((jd.STATE / "notify-cards.json").read_text()), {"*turns": True},
                         "persisted beside the master, as its own key")
        self.assertIn(("shell", {"type": "notifyTurns", "on": True}), sent,
                      "every open dashboard's row repaints — a distinct message, so the master's stays as it was")
        # the master is untouched by the turn switch, in both directions
        self.assertFalse(km._notify_all_on())
        code, _ = self._post("/notify-turns", {"on": False})
        self.assertEqual(code, 200)
        self.assertFalse(km._notify_turns_on())
        self.assertEqual(km._notify_cards(), {}, "off deletes the key rather than pinning False")

    def test_post_requires_token_and_refuses_garbage(self):
        code, _ = self._post("/notify-turns", {"on": True}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/notify-turns", None, raw=b"not json")
        self.assertEqual(code, 400)
        self.assertFalse(km._notify_turns_on())

    def test_prune_keeps_both_reserved_keys(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        km._set_notify_card("%s:g1" % SID_WEB, False, SID_WEB)     # a mute, kept while its card lives
        km._prune_notify_cards({"%s:g1" % SID_WEB})
        self.assertEqual(km._notify_cards(), {"*": True, "*turns": True, "%s:g1" % SID_WEB: False})
        km._prune_notify_cards(set())                              # the card left the feed
        self.assertEqual(km._notify_cards(), {"*": True, "*turns": True},
                         "neither reserved key is a card; only card ids prune")

    def test_the_turn_key_never_reads_as_a_card(self):
        km._set_notify_turns(True)
        cards = km._notify_cards()
        # the master is off, so no card is effectively armed — the turn key must not leak into that
        self.assertFalse(km._notify_card_effective(cards, "%s:g1" % SID_WEB, SID_WEB))


class PushPost(unittest.TestCase):
    """_push_post against a real loopback push service double — status and detail as the caller
    would see them; _push_send_one's prune rule on top."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        cls.script = {"status": 201, "body": b""}

        class Svc(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self.send_response(cls.script["status"])
                self.send_header("Content-Length", str(len(cls.script["body"])))
                self.end_headers()
                self.wfile.write(cls.script["body"])

            def log_message(self, *a):
                pass

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Svc)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _sub(self):
        return {"endpoint": "http://127.0.0.1:%d/send/dev-1" % self.port,
                "keys": {"p256dh": "k", "auth": "a"}}

    def _post(self, status, body=b""):
        type(self).script = {"status": status, "body": body}
        with mock.patch.object(km, "_webpush_encrypt", return_value=b"ciphertext"), \
             mock.patch.object(km, "_vapid_auth", return_value="vapid t=x, k=y"):
            return km._push_post(self._sub(), b"{}")

    def test_accepted_reports_the_2xx(self):
        status, detail = self._post(201)
        self.assertEqual(status, 201)
        self.assertTrue(detail)

    def test_a_refusal_carries_status_and_the_services_reason(self):
        status, detail = self._post(403, b'{"reason":"BadJwtToken"}')
        self.assertEqual(status, 403)
        self.assertIn("BadJwtToken", detail, "the service's own reason reaches the user")

    def test_no_answer_is_status_zero_with_the_error(self):
        with mock.patch.object(km, "_webpush_encrypt", return_value=b"x"), \
             mock.patch.object(km, "_vapid_auth", return_value="vapid t=x, k=y"):
            status, detail = km._push_post({"endpoint": "http://127.0.0.1:1/x",
                                            "keys": {"p256dh": "k", "auth": "a"}}, b"{}")
        self.assertEqual(status, 0)
        self.assertTrue(detail)

    def test_send_one_prunes_on_404_and_410_only(self):
        # the fan-out's contract, unchanged by the refactor: False = dead subscription, and NOTHING
        # else — a 5xx, a 403, a timeout all keep the subscription
        outcomes = {404: False, 410: False, 201: True, 403: True, 500: True, 0: True}
        for status, keep in outcomes.items():
            with mock.patch.object(km, "_push_post", return_value=(status, "x")):
                self.assertEqual(km._push_send_one(self._sub(), b"{}"), keep, status)


class PushTestRoute(_LoopbackMixin, unittest.TestCase):
    """POST /push/test over the real handler: one notification to the asking device, the push
    service's answer back."""

    def setUp(self):
        _reset_store()
        self.ep = "https://push.example.net/send/dev-test"
        km._save_push_subs({self.ep: {"endpoint": self.ep, "keys": {"p256dh": "k", "auth": "a"}}})

    def _test(self, outcome):
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=outcome) as pp:
            code, body = self._post("/push/test", {"endpoint": self.ep})
        return code, json.loads(body), pp

    def test_accepted(self):
        code, res, pp = self._test((201, "Created"))
        self.assertEqual(code, 200)
        self.assertEqual(res, {"ok": True, "status": 201, "detail": "Created"})
        # ONE send, to THIS subscription, a plain title and one sentence
        (sub, payload), _ = pp.call_args
        self.assertEqual(sub["endpoint"], self.ep)
        d = json.loads(payload.decode())
        self.assertEqual(d["title"], "romp")
        self.assertTrue(d["body"])
        self.assertEqual(set(d), {"title", "body"}, "a test carries no sid to jump to and no badge")

    def test_a_refusal_comes_back_verbatim(self):
        code, res, _ = self._test((403, "Forbidden: {\"reason\":\"BadJwtToken\"}"))
        self.assertEqual(code, 200)
        self.assertEqual(res["ok"], False)
        self.assertEqual(res["status"], 403)
        self.assertIn("BadJwtToken", res["detail"])
        self.assertIn(self.ep, km._push_subs(), "a refusal short of dead keeps the subscription")

    def test_a_dead_subscription_is_pruned_and_says_so(self):
        code, res, _ = self._test((410, "Gone"))
        self.assertEqual((code, res["ok"], res["status"]), (200, False, 410))
        self.assertIn("removed", res["detail"])
        self.assertNotIn(self.ep, km._push_subs(), "the same prune the fan-out applies")

    def test_no_answer_is_status_zero(self):
        _, res, _ = self._test((0, "URLError: timed out"))
        self.assertEqual((res["ok"], res["status"]), (False, 0))
        self.assertIn("timed out", res["detail"])

    def test_an_unknown_endpoint_says_not_subscribed(self):
        with mock.patch.object(km, "_push_post") as pp:
            code, body = self._post("/push/test", {"endpoint": "https://push.example.net/send/other"})
        self.assertEqual(code, 200)
        res = json.loads(body)
        self.assertEqual((res["ok"], res["status"]), (False, 0))
        self.assertIn("isn't subscribed", res["detail"])
        pp.assert_not_called()

    def test_missing_crypto_is_the_loud_500(self):
        with mock.patch.object(km, "_PUSH_CRYPTO", [False]):
            code, body = self._post("/push/test", {"endpoint": self.ep})
        self.assertEqual(code, 500)
        self.assertIn("cryptography", body)

    def test_gated_and_validated(self):
        code, _ = self._post("/push/test", {"endpoint": self.ep}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/push/test", {})
        self.assertEqual(code, 400)
        code, _ = self._post("/push/test", None, raw=b"nope")
        self.assertEqual(code, 400)


def _stamp_stop(sid, t):
    """The Stop hook's ledger stamp — the SDK session's authoritative turn-end fact."""
    d = jd.STATE / "sdk"
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".json")).write_text(json.dumps({"lastStopAt": int(t)}))


def _append_state(sid, state, t):
    d = jd.STATE / "states"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / (sid + ".jsonl"), "a") as f:
        f.write(json.dumps({"t": int(t), "state": state}) + "\n")


def _transcript(sid, text):
    p = jd.STATE / ("transcript-%s.jsonl" % sid[-2:])
    rec = {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    p.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n" + json.dumps(rec) + "\n")
    return str(p)


class TurnFinishedPush(unittest.TestCase):
    """_turn_notify_tick: a session's turn-end key moving is the event; both switches gate it."""

    def setUp(self):
        _reset_store()
        for p in (jd.STATE / "sdk", jd.STATE / "states"):
            for f in p.glob("*") if p.exists() else []:
                f.unlink()
        self.path = _transcript(SID_WEB, "Done: the login flow now redirects to the notes list.\n\nDetails below.")
        self.alive = [{"sid": SID_WEB, "name": "web", "path": self.path}]
        self.tmux = {SID_WEB: {"state": "waiting"}}

    def _tick(self):
        pushed, fwd = [], []
        with mock.patch.object(km, "_alive_sessions", return_value=self.alive), \
             mock.patch.object(km, "_push_notify", side_effect=lambda *a, **k: pushed.append((a, k))), \
             mock.patch.object(km, "_push_forward", side_effect=lambda evs: fwd.append(evs)):
            fired = km._turn_notify_tick(time.time(), self.tmux)
        return fired, pushed, fwd

    def test_first_sight_is_a_silent_baseline_then_a_new_end_fires(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _stamp_stop(SID_WEB, 1000)
        fired, pushed, fwd = self._tick()
        self.assertEqual((fired, pushed, fwd), ([], [], []), "existing state is status, not news")
        _stamp_stop(SID_WEB, 1001)
        fired, pushed, fwd = self._tick()
        self.assertEqual(fired, [{"title": "web", "body": "Done: the login flow now redirects to the notes list.",
                                  "sid": SID_WEB}])
        (args, kw), = pushed
        self.assertEqual(args, ("web", "Done: the login flow now redirects to the notes list.", SID_WEB))
        self.assertNotIn("badge", kw, "the count rides its own push")
        self.assertEqual(fwd, [fired], "the same event travels to trusted peers, the bell-event way")
        # the same key again is nothing new
        self.assertEqual(self._tick()[0], [])

    def test_both_switches_must_be_on(self):
        _stamp_stop(SID_WEB, 1000)
        self._tick()                                       # baseline
        for master, turns in ((False, False), (True, False), (False, True)):
            km._set_notify_all(master)
            km._set_notify_turns(turns)
            _stamp_stop(SID_WEB, 1001 + int(master) + 2 * int(turns))
            self.assertEqual(self._tick()[0], [], (master, turns))
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _stamp_stop(SID_WEB, 1010)
        self.assertEqual(len(self._tick()[0]), 1, "…and with both on the next end fires")

    def test_switching_on_later_never_replays_old_ends(self):
        _stamp_stop(SID_WEB, 1000)
        self._tick()
        _stamp_stop(SID_WEB, 1001)                          # an end that happened while off
        self._tick()
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.assertEqual(self._tick()[0], [], "the memo advanced while off; nothing to replay")

    def test_a_muted_session_stays_quiet(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        km._set_notify_session(SID_WEB, False)              # its own bell off = a mute under the master
        _stamp_stop(SID_WEB, 1000)
        self._tick()
        _stamp_stop(SID_WEB, 1001)
        self.assertEqual(self._tick()[0], [])

    def test_an_empty_reply_gets_a_plain_body(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.alive[0]["path"] = str(jd.STATE / "no-such-transcript.jsonl")
        _stamp_stop(SID_WEB, 1000)
        self._tick()
        _stamp_stop(SID_WEB, 1001)
        self.assertEqual(self._tick()[0][0]["body"], "finished a turn")

    def test_the_fallback_key_counts_only_stopped_transitions(self):
        # a tmux session: no Stop-hook ledger; states/ is the record — and a turn STARTING (working)
        # must never read as an end
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _append_state(SID_WEB, "waiting", 1000)
        self.assertEqual(km._turn_end_key(SID_WEB), 1000)
        self._tick()                                        # baseline
        _append_state(SID_WEB, "working", 1001)
        self.assertEqual(km._turn_end_key(SID_WEB), 0, "a start is not an end")
        self.assertEqual(self._tick()[0], [])
        _append_state(SID_WEB, "waiting", 1002)
        self.assertEqual(len(self._tick()[0]), 1, "the stop after it is")

    def test_wired_into_the_pusher_cycle_after_the_feed_build(self):
        import inspect
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_turn_notify_tick(now, tmux)", src)
        self.assertLess(src.index("_push_all(tmux=tmux)"), src.index("_turn_notify_tick(now, tmux)"),
                        "the feed builds first, so a same-settle bell event files its buzz first")


class FirstLine(unittest.TestCase):
    def test_first_non_empty_line_clipped(self):
        self.assertEqual(km._first_line("\n\n  Shipped it.  \nmore"), "Shipped it.")
        self.assertEqual(km._first_line(""), "")
        long = "x" * 200
        out = km._first_line(long)
        self.assertEqual(len(out), 120)
        self.assertTrue(out.endswith("…"))


class OneBuzzPerTurnEnd(unittest.TestCase):
    """The no-double-buzz rule, both orders."""

    def setUp(self):
        _reset_store()
        for f in (jd.STATE / "sdk").glob("*") if (jd.STATE / "sdk").exists() else []:
            f.unlink()

    def test_turn_first_then_the_bell_event_yields(self):
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "turn"))
        self.assertFalse(km._buzz_claim(SID_WEB, 1001, "bell"), "the phone already buzzed for this stop")
        self.assertTrue(km._buzz_claim(SID_WEB, 1002, "bell"), "a later turn end is new information")

    def test_bell_first_then_the_turn_push_yields(self):
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "bell"))
        self.assertFalse(km._buzz_claim(SID_WEB, 1001, "turn"))
        self.assertTrue(km._buzz_claim(SID_WEB, 1002, "turn"))

    def test_bell_events_never_suppress_each_other(self):
        # two cards of one session moving in one build buzz twice — exactly as before the rule
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "bell"))
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "bell"))
        self.assertFalse(km._buzz_claim(SID_WEB, 1001, "turn"), "…and the turn push still yields to them")

    def test_sessions_are_independent_and_sidless_always_passes(self):
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "turn"))
        self.assertTrue(km._buzz_claim(SID_API, 1001, "bell"))
        self.assertTrue(km._buzz_claim("", 0, "bell"))
        self.assertTrue(km._buzz_claim("", 0, "bell"))

    def test_the_tick_yields_to_a_bell_claim(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        path = _transcript(SID_WEB, "Which migration should I keep?")
        alive = [{"sid": SID_WEB, "name": "web", "path": path}]
        with mock.patch.object(km, "_alive_sessions", return_value=alive), \
             mock.patch.object(km, "_push_notify") as pn, \
             mock.patch.object(km, "_push_forward") as pf:
            _stamp_stop(SID_WEB, 1000)
            km._turn_notify_tick(time.time(), {SID_WEB: {}})            # baseline
            _stamp_stop(SID_WEB, 1001)
            # the judges ruled inside the same cycle: the card moved and its bell event filed first
            self.assertTrue(km._buzz_claim(SID_WEB, km._turn_end_key(SID_WEB), "bell"))
            self.assertEqual(km._turn_notify_tick(time.time(), {SID_WEB: {}}), [])
            pn.assert_not_called()
            pf.assert_not_called()

    def test_the_feed_path_claims_before_it_pushes(self):
        import inspect
        src = inspect.getsource(km._cached_feed)
        self.assertIn('_buzz_claim(_sid, _turn_end_key(_sid), "bell")', src)
        self.assertLess(src.index("_buzz_claim("), src.index("_push_notify(_t, _b, _sid, _badge)"))
        self.assertLess(src.index("_system_notify(_t, _b)"), src.index("_buzz_claim("),
                        "the desktop notice is not the buzz and never yields")


class RelayOfTurnEvents(unittest.TestCase):
    def test_a_turn_shaped_event_mirrors_with_the_origin_on_its_sid(self):
        # the relay's tolerant surgery (the federation test's contract): a session-named title
        # passes as composed, the sid gains the origin so a tap routes through the merged dashboard
        with km._remotes_lock:
            km._remotes["boxa"] = {"host": "boxa", "kernel_port": 1, "local_port": 1, "token": "tok",
                                   "proc": None, "status": "up", "trust": "trusted"}
        try:
            raw = json.dumps({"origin": "boxa", "events": [{"title": "web", "body": "Done: shipped.", "sid": SID_WEB}]}).encode()
            h = km.Handler.__new__(km.Handler)
            h.client_address = ("127.0.0.1", 0)
            h.headers = {"X-Romp-Token": km.TOKEN, "Content-Length": str(len(raw))}
            h.path = "/push/relay"
            h.command = "POST"
            h.request_version = "HTTP/1.1"
            h.wfile = io.BytesIO()
            h.rfile = io.BytesIO(raw)
            h.close_connection = True
            h.send_response = lambda code, *a: None
            h.send_header = lambda k, v: None
            h.end_headers = lambda: None
            h.log_message = lambda *a: None
            with mock.patch.object(km, "_push_notify") as pn:
                h.do_POST()
            pn.assert_called_once_with("web", "Done: shipped.", "boxa:" + SID_WEB)
        finally:
            with km._remotes_lock:
                km._remotes.pop("boxa", None)


class ShellPopover(unittest.TestCase):
    """The rendered landing: the popover's markup and skin, the wiring around it."""

    @classmethod
    def setUpClass(cls):
        cls.html = km._landing()

    def test_the_four_rows(self):
        h = self.html
        self.assertIn("id=rbell-back hidden", h)
        self.assertIn("id=rbell-pop role=dialog", h)
        for act in ("data-act=all", "data-act=dev", "data-act=turns"):
            self.assertIn("class=rbp-row %s role=switch" % act, h)
        self.assertIn(">All devices<", h)
        self.assertIn(">This device<", h)
        self.assertIn(">Also when a turn finishes<", h)
        self.assertIn("id=rbp-test data-act=test>Send a test notification<", h)
        # the one-sentence "why" under each switch
        self.assertIn("Arms every device, and the desktop of every machine you've attached.", h)
        self.assertIn("Buzzes every time any session finishes a turn. With many sessions running, that is a lot of buzzing.", h)
        self.assertIn("id=rbp-test-out", h)

    def test_the_bell_opens_it_and_the_rows_are_the_switches(self):
        js = km._LANDING_PUSH_JS
        self.assertIn("open(bl)", js)
        self.assertNotIn("post('/notify-all',{on:want}).then(function(){\nisOn=want;paint();                       // the master flipped", js,
                         "the bell's own tap no longer flips the master")
        self.assertIn("if(act==='all')", js)
        self.assertIn("post('/notify-all',{on:want})", js)
        self.assertIn("if(act==='dev')", js)
        self.assertIn("Notification.requestPermission():null", js)      # still inside the tap's own stack
        self.assertIn("devSubscribe(perm0):devUnsubscribe()", js)
        self.assertIn("if(act==='turns')", js)
        self.assertIn("post('/notify-turns',{on:wantT})", js)
        self.assertIn("if(act==='test')", js)
        self.assertIn("post('/push/test',{endpoint:s.endpoint})", js)
        # the test button acknowledges at once and self-restores; the answer lands under it
        self.assertIn("testBtn.disabled=true", js)
        self.assertIn("testBtn.textContent='Sending…'", js)
        self.assertIn("testBtn.disabled=false;testBtn.textContent=label", js)
        self.assertIn("'The push service accepted it.'", js)
        self.assertIn("This device isn't subscribed yet.", js)
        self.assertIn("'The push service refused it: '+d.status", js)

    def test_the_glyph_reflects_this_device_and_the_tooltip_names_the_off_half(self):
        js = km._LANDING_PUSH_JS
        self.assertIn("var lit=isOn&&(canPush?devOn:true)", js)
        self.assertIn("b.classList.toggle('on',lit)", js)
        self.assertIn("'Notifications off for all devices'", js)
        self.assertIn("'Notifications off on this device'", js)
        self.assertIn("'Notifications off — for all devices, and on this device'", js)
        # blocked / unavailable push is SAID, in the row, with the way back
        self.assertIn("perm()==='denied'", js)
        self.assertIn("Notifications are blocked for this site.", js)
        self.assertIn("add romp to the Home Screen first", js)
        self.assertIn("el.classList.contains('off'))return", js, "a blocked row does not fire the request")

    def test_dismissal_and_repaint_wiring(self):
        h = self.html
        self.assertIn("window.__rompCloseBellPop=close", h)
        self.assertIn("if(bp&&!bp.hidden&&window.__rompCloseBellPop){window.__rompCloseBellPop();closed=true;}", h,
                      "Escape closes it through the shell's shared chain")
        self.assertIn("if(e.target===back)close()", h, "an outside tap lands on the backdrop and closes")
        self.assertIn("m.type==='notifyTurns'&&window.__rompNotifyTurnsPaint", h)
        self.assertIn("fetch('/notify-turns')", h)
        self.assertIn("z-index:205", h)

    def test_the_menu_tokens_with_their_dark_fallbacks(self):
        h = self.html
        # the shell defines the tokens (it loads no sheet) — dark byte-equal to styles.css's :root,
        # the light block in the light palette
        self.assertIn(":root{--menu-bg:#252526;--menu-fg:#cccccc;--menu-border:rgba(255,255,255,0.12);"
                      "--menu-hover:rgba(255,255,255,0.09);--radius-menu:6px;--shadow-menu:0 4px 12px rgba(0,0,0,0.35);"
                      "--check-bg:#1EA1EB}", h)
        self.assertIn("body.theme-light{--menu-bg:#FBF6EF;--menu-fg:#1F1E1D;--menu-border:rgba(0,0,0,0.12);"
                      "--menu-hover:rgba(0,0,0,0.06);--shadow-menu:0 4px 12px rgba(31,26,20,0.16);--check-bg:#C2410C}", h)
        pop = h[h.index("#rbell-pop{"):h.index("#rbp-test-out{")]
        self.assertIn("background:var(--menu-bg,#252526)", pop)
        self.assertIn("color:var(--menu-fg,#cccccc)", pop)
        self.assertIn("border:1px solid var(--menu-border,rgba(255,255,255,0.12))", pop)
        self.assertIn("border-radius:var(--radius-menu,6px)", pop)
        self.assertIn("box-shadow:var(--shadow-menu,0 4px 12px rgba(0,0,0,0.35))", pop)
        self.assertIn("background:var(--menu-hover,rgba(255,255,255,0.09))", pop)
        self.assertIn("font:12px/1.45 'Inter'", pop)
        self.assertIn(".rbp-sub{font-size:0.82em;opacity:.6}", pop)
        # no raw dark literal outside a var() fallback slot
        import re
        bare = re.sub(r"var\((--[\w-]+)\s*,\s*(?:[^()]|\([^()]*\))*\)", r"var(\1)", pop)
        for lit in ("#252526", "#cccccc", "rgba(255,255,255", "rgba(0,0,0,0.35)", "#1EA1EB"):
            self.assertNotIn(lit, bare, lit)
        self.assertIn("background:var(--accent)", pop, "the on-state pill is accent chrome")


if __name__ == "__main__":
    unittest.main()
