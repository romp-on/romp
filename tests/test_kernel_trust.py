#!/usr/bin/env python3
"""Per-host trust model, kernel side: the remotes registry stores a trust level (trusted|directed|
isolated), defaulting to directed; set_trust validates + persists it; _remote_public/_tunnels expose it
(the channel the bus reads); the /tunnels/trust route drives it; and _quarantine_cards surfaces a held
message from a directed peer as a needs-you feed card.

Synthetic only — hermetic temp STATE, placeholder hostnames/mids, invented notes-domain sessions.
"""
import http.client
import io
import json
import sys
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _row(host, **extra):
    r = {"host": host, "kernel_port": 29855, "local_port": 5000, "bus_port": 5001, "token": "t",
         "proc": None, "status": "up", "detail": "", "sids": [], "trust": "directed"}
    r.update(extra)
    return r


class SetTrust(unittest.TestCase):
    def setUp(self):
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")

    def test_default_is_directed_in_public_view(self):
        pub = km._remote_public(km._remotes["TESTHOST"])
        self.assertEqual(pub["trust"], "directed")

    def test_set_trust_updates_and_persists(self):
        pub, err = km.set_trust("TESTHOST", "trusted")
        self.assertIsNone(err)
        self.assertEqual(pub["trust"], "trusted")
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted")
        # persistence: reload from remotes.json and confirm the level survived
        km._remotes_save()
        km._remotes.clear()
        km._remotes_load()
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted")

    def test_set_trust_rejects_bad_level(self):
        pub, err = km.set_trust("TESTHOST", "whatever")
        self.assertIsNone(pub)
        self.assertIn("trust must be one of", err)

    def test_set_trust_unattached_host_is_origin_only(self):
        # Trust is judged BY ORIGIN at delivery (the user 2026-07-25): a host with no tunnel here —
        # its mail arrives relayed through a hub — can carry a tier. The level lands in the
        # remembered-hosts table and reaches the bus as an origin-only row.
        calls = []
        saved = km._notify_bus_origin_trust
        km._notify_bus_origin_trust = lambda h, t: calls.append((h, t)) or True
        try:
            pub, err = km.set_trust("FARBOX", "trusted")
        finally:
            km._notify_bus_origin_trust = saved
        self.assertIsNone(err)
        self.assertEqual(pub, {"host": "FARBOX", "trust": "trusted", "originOnly": True})
        self.assertEqual(km.known_trust("FARBOX"), "trusted", "the remembered table IS the store")
        self.assertEqual(calls, [("FARBOX", "trusted")], "the bus learns the origin row now")

    def test_push_origin_trust_rows_covers_only_unattached(self):
        # The supervisor pushes remembered-but-unattached tiers once per (host, level); attached
        # hosts stay the (up, trust)-keyed full notify's job.
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")
        km._known.clear()                         # order-independent: other tests seed this table
        km._known_note("TESTHOST", "trusted")     # attached → not this path's job
        km._known_note("FARBOX", "isolated")      # unattached → pushed
        km._origin_trust_pushed.clear()
        calls = []
        saved = km._notify_bus_origin_trust
        km._notify_bus_origin_trust = lambda h, t: calls.append((h, t)) or True
        try:
            km._push_origin_trust_rows()
            km._push_origin_trust_rows()          # memoized: no duplicate push
            km._known_note("FARBOX", "directed")  # a level CHANGE re-pushes
            km._push_origin_trust_rows()
        finally:
            km._notify_bus_origin_trust = saved
        self.assertEqual(calls, [("FARBOX", "isolated"), ("FARBOX", "directed")])

    def test_load_defaults_missing_trust_to_directed(self):
        # a pre-trust remotes.json row (no "trust" key) reads back as directed
        km._remotes.clear()
        km._remotes["OLD"] = {k: v for k, v in _row("OLD").items() if k != "trust"}
        km._remotes_save()
        km._remotes.clear()
        km._remotes_load()
        self.assertEqual(km._remotes["OLD"]["trust"], "directed")


class TrustRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", path, json.dumps(body),
                  {"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, data

    def test_route_sets_trust(self):
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")
        code, data = self._post("/tunnels/trust", {"host": "TESTHOST", "trust": "isolated"})
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["tunnel"]["trust"], "isolated")

    def test_route_rejects_bad_level(self):
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")
        code, data = self._post("/tunnels/trust", {"host": "TESTHOST", "trust": "bogus"})
        self.assertEqual(code, 400)
        self.assertFalse(data["ok"])

    def test_route_unattached_host_sets_origin_trust(self):
        km._remotes.clear()
        saved = km._notify_bus_origin_trust
        km._notify_bus_origin_trust = lambda h, t: True
        try:
            code, data = self._post("/tunnels/trust", {"host": "GHOST", "trust": "trusted"})
        finally:
            km._notify_bus_origin_trust = saved
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["tunnel"], {"host": "GHOST", "trust": "trusted", "originOnly": True})


class QuarantineCards(unittest.TestCase):
    def _write_held(self, mid, frm="api", to="web", origin="TESTHOST", body="ship the parser fix"):
        qdir = km.jd.STATE / "postal" / "quarantine"
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / (mid + ".json")).write_text(json.dumps(
            {"mid": mid, "to": to, "toId": "sess-web", "frm": frm, "frmId": "id-api",
             "body": body, "kind": "coordinate", "origin": origin, "at": 1000}))

    def setUp(self):
        qdir = km.jd.STATE / "postal" / "quarantine"
        if qdir.exists():
            for f in qdir.glob("*.json"):
                f.unlink()

    def test_builds_a_needs_you_card(self):
        self._write_held("qc-1")
        cards = km._quarantine_cards(2000, set())
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual(c["itemId"], "quarantine:qc-1")
        self.assertEqual(c["column"], "needs_input")
        self.assertEqual(c["blocked"]["state"], "quarantine")
        self.assertEqual(c["blocked"]["frm"], "api")
        self.assertEqual(c["blocked"]["to"], "web")
        self.assertEqual(c["blocked"]["origin"], "TESTHOST")
        self.assertEqual(c["blocked"]["body"], "ship the parser fix")

    def test_card_is_compact_title_plus_gist(self):
        """The card reads "New message" under the RECIPIENT session's name, with the bus-style 90-char
        gist for the one-line body (the user 2026-07-26 — the full body lives in the decision modal)."""
        self._write_held("qc-4", body="  ship   the\nparser fix  " + "x" * 200)
        c = km._quarantine_cards(2000, set())[0]
        self.assertEqual(c["text"], "New message")
        gist = c["blocked"]["gist"]
        self.assertTrue(gist.startswith("ship the parser fix"), gist)
        self.assertEqual(len(gist), 90, "whitespace-collapsed and clamped like the federation gossip gist")

    def test_the_card_carries_both_ENDS_of_the_delivery(self):
        """The route the card draws (the user 2026-07-29): sender host + session, recipient session, and
        the recipient's host, which for a locally-held message is THIS machine — a local sid has no host
        prefix, so the payload has to name it or the receiving end cannot be named at all."""
        self._write_held("qc-5")
        c = km._quarantine_cards(2000, set())[0]
        b = c["blocked"]
        for k in ("origin", "frm", "to", "body", "gist"):
            self.assertIn(k, b, "the card names %s" % k)
        self.assertTrue(b["origin"], "the sending HOST")
        self.assertTrue(b["frm"], "the sending SESSION")
        self.assertEqual(c["name"], b["to"], "the card sits under the recipient session")

    def test_the_feed_payload_names_this_machine(self):
        import inspect
        self.assertIn('"selfHost": _self_host(),', inspect.getsource(km.build_feed))

    def test_cleared_card_is_hidden(self):
        self._write_held("qc-2")
        self.assertEqual(km._quarantine_cards(2000, {"quarantine:qc-2"}), [])

    def test_no_dir_is_empty(self):
        # nothing held → no cards, no crash
        self.assertEqual(km._quarantine_cards(2000, set()), [])


class QuarantineRefusal(unittest.TestCase):
    """The bus refusing a verdict answers the asking pane BY THE MESSAGE it was about (review find,
    2026-09-08). The feed latches Approve/Deny ("Delivering…") on the click and re-arms them on the kernel's
    reply for that request. The reply used to be a bare `warn`, which the feed never handled, so a refused
    verdict left both buttons disabled until the card was re-sent, and a held message the bus refused to
    act on is exactly the card that is never re-sent."""

    def setUp(self):
        self._saved = km._bus_quarantine_act, km._mark_views_dirty
        self.sent, self.dirtied = [], []
        km._mark_views_dirty = lambda: self.dirtied.append(True)
        self.client = {"app": "feed", "wid": "w1", "alive": True,
                       "send": lambda raw: self.sent.append(json.loads(raw))}

    def tearDown(self):
        km._bus_quarantine_act, km._mark_views_dirty = self._saved

    def test_a_refused_verdict_names_the_message_it_answers(self):
        km._bus_quarantine_act = lambda body: (False, "the recipient is no longer live")
        km.Handler._dispatch_ws(None, {"type": "quarantineDecision", "mid": "qc-7", "action": "approve",
                                       "sid": "11111111-2222-3333-4444-555555555555"}, self.client)
        self.assertEqual(self.sent, [{"type": "quarantineRefused", "mid": "qc-7",
                                      "text": "quarantine: the recipient is no longer live"}],
                         "the reply carries the held message's id, so the feed re-arms that card's buttons alone")
        self.assertEqual(self.dirtied, [], "a refused verdict changes no view")

    def test_an_accepted_verdict_answers_nothing_and_rebuilds_the_views(self):
        # the bus removed the held file: the next build drops the card (event-based), nothing to say
        km._bus_quarantine_act = lambda body: (True, "")
        km.Handler._dispatch_ws(None, {"type": "quarantineDecision", "mid": "qc-8", "action": "deny"}, self.client)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.dirtied, [True])


class MirrorTrust(unittest.TestCase):
    """mirror_trust (the user 2026-07-26): sets OUR level for a host as ITS level for US, through the
    tunnel forward + that machine's serve token — the human with both tokens acting on both kernels.
    Deliberately the ONLY reciprocity: a peer can never open our gate by declaring trust."""

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.pop("boxa", None)

    def _stub_remote(self, seen):
        from http.server import BaseHTTPRequestHandler

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                seen["token"] = self.headers.get("X-Romp-Token")
                seen["body"] = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                out = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_mirror_posts_our_level_through_the_tunnel_with_the_remote_token(self):
        seen = {}
        port = self._stub_remote(seen)
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=port, token="remote-tok", trust="trusted")
        res, err = km.mirror_trust("boxa")
        self.assertIsNone(err)
        self.assertEqual(res, {"host": "boxa", "trust": "trusted"})
        self.assertEqual(seen["path"], "/tunnels/trust", "the remote's own persisting route does the write")
        self.assertEqual(seen["token"], "remote-tok", "authorized with THAT machine's serve token")
        self.assertEqual(seen["body"], {"host": km._self_host(), "trust": "trusted"},
                         "the remote is told to hold THIS machine at our current level")

    def test_mirror_without_a_tunnel_errors_plainly(self):
        res, err = km.mirror_trust("nosuchhost")
        self.assertIsNone(res)
        self.assertTrue(err.startswith("no attached"), err)

    def test_mirror_without_a_token_errors_plainly(self):
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=1, token="", trust="trusted")
        res, err = km.mirror_trust("boxa")
        self.assertIsNone(res)
        self.assertIn("no admin path", err)


def _stub_kernel(cleanup_with, seen=None, tunnels=None):
    """A pretend remote kernel on a loopback port: records any POST into `seen` (mirror/remote-trust
    tests) and answers GET /tunnels with `tunnels` (pairs tests)."""
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def _out(self, payload):
            out = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_POST(self):
            if seen is not None:
                seen["path"] = self.path
                seen["token"] = self.headers.get("X-Romp-Token")
                seen["body"] = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            self._out({"ok": True})

        def do_GET(self):
            self._out(tunnels or {})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cleanup_with(srv.shutdown)
    return srv.server_address[1]


class RemoteTrust(unittest.TestCase):
    """remote_trust (the user 2026-08-11): the hub sets ON one attached machine ITS trust level for
    another host — the popover's 'Between your machines' rows. Same boundary as mirror_trust: the
    write crosses the on-host tunnel with that machine's own serve token, and that machine's own
    /tunnels/trust route persists it; a peer can never raise its own standing."""

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.pop("boxa", None)

    def test_writes_the_pair_level_through_the_holders_tunnel(self):
        seen = {}
        port = _stub_kernel(self.addCleanup, seen=seen)
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=port, token="remote-tok", trust="directed")
        res, err = km.remote_trust("boxa", "boxb", "trusted")
        self.assertIsNone(err)
        self.assertEqual(res, {"onHost": "boxa", "host": "boxb", "trust": "trusted"})
        self.assertEqual(seen["path"], "/tunnels/trust", "the holder's own persisting route does the write")
        self.assertEqual(seen["token"], "remote-tok", "authorized with the HOLDING machine's serve token")
        self.assertEqual(seen["body"], {"host": "boxb", "trust": "trusted"},
                         "boxa is told to hold boxb at the chosen level")

    def test_bad_level_refused_before_any_dial(self):
        res, err = km.remote_trust("boxa", "boxb", "bogus")
        self.assertIsNone(res)
        self.assertIn("trust must be one of", err)

    def test_without_a_tunnel_errors_plainly(self):
        res, err = km.remote_trust("nosuchhost", "boxb", "trusted")
        self.assertIsNone(res)
        self.assertTrue(err.startswith("no attached"), err)

    def test_a_machine_never_tiers_its_own_mail(self):
        res, err = km.remote_trust("boxa", "boxa", "trusted")
        self.assertIsNone(res)
        self.assertIn("own mail", err)


class PairsSnapshot(unittest.TestCase):
    """pairs_snapshot: the hub reads each attached machine's own trust table over its tunnel and
    assembles per-pair directions. '' = the holder has no explicit row (the bus treats a relayed
    origin as directed); None + a named per-host error = that machine was unreadable this pass —
    surfaced, never silently dropped (fail loudly)."""

    def setUp(self):
        # these tests assert the WHOLE hosts map, and the kernel module is ONE object per process for
        # every test file that loads it under this name — so start from an empty map and put back
        # exactly what was there (under xdist another file's leftover row landed in the snapshot,
        # 2026-09-02); popping only this class's own hosts left any other file's row in place
        with km._remotes_lock:
            self._saved = dict(km._remotes)
            km._remotes.clear()

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes.update(self._saved)

    def test_pairs_read_both_directions_from_each_machines_own_table(self):
        # boxa holds boxb via an attached-tunnel row; boxb holds boxa via a relay (viaReach) row —
        # the three row kinds a tier can live on are all consulted.
        pa = _stub_kernel(self.addCleanup, tunnels={"tunnels": [{"host": "boxb", "trust": "trusted"}],
                                                    "known": [], "viaReach": []})
        pb = _stub_kernel(self.addCleanup, tunnels={"tunnels": [], "known": [],
                                                    "viaReach": [{"host": "boxa", "trust": "isolated"}]})
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=pa, token="ta")
            km._remotes["boxb"] = _row("boxb", local_port=pb, token="tb")
        snap = km.pairs_snapshot()
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["hosts"], {"boxa": {"ok": True}, "boxb": {"ok": True}})
        self.assertEqual(snap["pairs"], [{"a": "boxa", "b": "boxb", "ab": "trusted", "ba": "isolated"}])

    def test_no_explicit_row_reads_empty_never_an_invented_level(self):
        pa = _stub_kernel(self.addCleanup, tunnels={"tunnels": [], "known": [], "viaReach": []})
        pb = _stub_kernel(self.addCleanup, tunnels={"tunnels": [],
                                                    "known": [{"host": "boxa", "trust": "trusted"}],
                                                    "viaReach": []})
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=pa, token="ta")
            km._remotes["boxb"] = _row("boxb", local_port=pb, token="tb")
        snap = km.pairs_snapshot()
        self.assertEqual(snap["pairs"], [{"a": "boxa", "b": "boxb", "ab": "", "ba": "trusted"}])

    def test_an_unreadable_machine_carries_its_error_not_a_guess(self):
        pa = _stub_kernel(self.addCleanup, tunnels={"tunnels": [{"host": "boxb", "trust": "directed"}]})
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=pa, token="ta")
            km._remotes["boxb"] = _row("boxb", status="down")
        snap = km.pairs_snapshot()
        self.assertEqual(snap["hosts"]["boxb"], {"ok": False, "error": "not connected"})
        self.assertEqual(snap["pairs"], [{"a": "boxa", "b": "boxb", "ab": "directed", "ba": None}])


class PairRoutes(unittest.TestCase):
    """Route wiring for the pair section: GET /tunnels/pairs answers the snapshot; POST
    /tunnels/trust-remote proxies the write and maps errors to honest statuses."""

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
        with km._remotes_lock:
            km._remotes.clear()

    def _call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, None if body is None else json.dumps(body),
                  {"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, data

    def test_pairs_route_answers_empty_world(self):
        code, data = self._call("GET", "/tunnels/pairs")
        self.assertEqual(code, 200)
        self.assertEqual(data, {"ok": True, "hosts": {}, "pairs": []})

    def test_trust_remote_route_round_trips_to_the_stub(self):
        seen = {}
        port = _stub_kernel(self.addCleanup, seen=seen)
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=port, token="remote-tok")
        code, data = self._call("POST", "/tunnels/trust-remote",
                                {"onHost": "boxa", "host": "boxb", "trust": "trusted"})
        self.assertEqual(code, 200)
        self.assertEqual(data, {"ok": True, "set": {"onHost": "boxa", "host": "boxb", "trust": "trusted"}})
        self.assertEqual(seen["body"], {"host": "boxb", "trust": "trusted"})

    def test_trust_remote_route_maps_errors_to_statuses(self):
        code, data = self._call("POST", "/tunnels/trust-remote",
                                {"onHost": "boxa", "host": "boxb", "trust": "bogus"})
        self.assertEqual((code, data["ok"]), (400, False))
        code, data = self._call("POST", "/tunnels/trust-remote",
                                {"onHost": "ghost", "host": "boxb", "trust": "trusted"})
        self.assertEqual((code, data["ok"]), (404, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class BusPortRecord(unittest.TestCase):
    """The kernel's loopback dials of the bus read the bus's own port record ahead of the environment (2026-09-18): a kernel
    and a bus that read ROMP_POSTAL_PORT from different environments dialed different ports, and a held message's approve
    reached a bus that never held it ("no held message"). Hermetic: the record under this test's state root, a stub bus on a
    free port standing for the bus that holds the file."""

    def setUp(self):
        self.rec = km.jd.STATE / "postal" / "postal-port"
        self.rec.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.rec.unlink()
        except FileNotFoundError:
            pass
        km._BUS_PORT_SAID[0] = None
        self._saved_ens = km._BUS_ENSURED[0]; km._BUS_ENSURED[0] = True   # this kernel ensured its bus: the record may be trusted
        self._err, self._saved_bp = io.StringIO(), km.BUS_PORT
        self._saved_stderr = sys.stderr; sys.stderr = self._err

    def tearDown(self):
        sys.stderr = self._saved_stderr
        km.BUS_PORT = self._saved_bp
        km._BUS_ENSURED[0] = self._saved_ens
        km._BUS_PORT_SAID[0] = None
        try:
            self.rec.unlink()
        except FileNotFoundError:
            pass

    def _rec(self, port, pid=None, tok=None):
        """A record as the bus writes it: this kernel's token mark unless a foreign one is asked for."""
        return json.dumps({"port": port, "pid": os.getpid() if pid is None else pid, "tok": km._bus_token_mark() if tok is None else tok})

    def _stub_bus(self, seen):
        from http.server import BaseHTTPRequestHandler   # the module's own idiom: imported where the stub is built
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                seen.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
                out = json.dumps({"ok": True}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_the_record_wins_over_the_environment_and_the_environment_is_the_fallback(self):
        km.BUS_PORT = 1                                            # the environment's word: a port nothing answers on
        self.assertEqual(km._bus_port(), 1, "no record: the environment")
        self.rec.write_text(self._rec(45678))
        self.assertEqual(km._bus_port(), 45678, "the record names the bound port, a live pid and this kernel's token mark: it wins")
        self.rec.write_text(self._rec(45679, pid=2 ** 22 + 12345))   # a pid that does not run: a stale record
        self.assertEqual(km._bus_port(), 1, "a stale record (its pid gone) is ignored: the environment")
        self.rec.write_text(self._rec(45680, tok="0123456789abcdef"))   # another bus's record: a token mark that is not ours
        self.assertEqual(km._bus_port(), 1, "a foreign record (another bus, another world, a reused pid) is ignored: the environment")
        self.rec.write_text(json.dumps({"port": 45681, "pid": os.getpid()}))   # a record with no mark (an older bus): never trusted
        self.assertEqual(km._bus_port(), 1)
        # a kernel that ensured NO bus (client-only, a lab's, an in-process test's) dials the environment whatever the record says
        self.rec.write_text(self._rec(45682))
        km._BUS_ENSURED[0] = False
        self.assertEqual(km._bus_port(), 1, "no ensure, no record: the environment")
        km._BUS_ENSURED[0] = True
        self.assertEqual(km._bus_port(), 45682, "the ensure is the event that makes the bus this kernel's")
        self.rec.write_text("torn")
        self.assertEqual(km._bus_port(), 1, "a torn record: the environment, never a raise")
        self.rec.write_text(self._rec(0))
        self.assertEqual(km._bus_port(), 1, "a record with no port: the environment")

    def test_the_census_line_says_the_port_and_its_source_once_and_names_a_mismatch_with_the_environment(self):
        km.BUS_PORT = 25302
        km._bus_port(); km._bus_port()
        lines = [l for l in self._err.getvalue().splitlines() if "postal bus dialed" in l]
        self.assertEqual(lines, ["romp-kernel: postal bus dialed on 127.0.0.1:25302 from the environment"], "said once, no mismatch when the environment is the source")
        self.rec.write_text(self._rec(45678))
        km._bus_port(); km._bus_port()
        lines = [l for l in self._err.getvalue().splitlines() if "postal bus dialed" in l]
        self.assertEqual(len(lines), 2, "a change is said again, once")
        self.assertIn("127.0.0.1:45678 from the record (ROMP_POSTAL_PORT says 25302: the environment and the bus disagree; the record wins)", lines[1])

    def test_a_kernel_on_one_port_and_a_bus_bound_on_another_still_reach_the_bus_that_holds_the_file(self):
        # the fault of 2026-09-18, red at main: the kernel's environment names a port nothing answers on while the bus that holds
        # the message is bound elsewhere and says so in its record; the approve reaches the record's bus
        seen = []
        bus_port = self._stub_bus(seen)
        km.BUS_PORT = 1
        self.rec.write_text(self._rec(bus_port))
        ok, err = km._bus_quarantine_act({"mid": "px-1.2_abc.TESTHOST", "action": "approve", "sid": "11111111-2222-3333-4444-555555555555"})
        self.assertEqual((ok, err), (True, "bus HTTP 200"), "the record's bus answered: %r" % err)
        self.assertEqual(seen[0][0], "/quarantine/act"); self.assertEqual(seen[0][1]["mid"], "px-1.2_abc.TESTHOST"); self.assertEqual(seen[0][1]["sid"], "11111111-2222-3333-4444-555555555555")

    def test_a_record_another_world_left_cannot_redirect_a_dial_a_test_or_an_operator_pointed_elsewhere(self):
        # the suite found it first (2026-09-18): every kernel test module shares one event-model state root, so a record one
        # world wrote outlived it, and a module that pointed BUS_PORT at its own stub bus reached the machine's real bus
        # instead ("token required"). The mark closes it: a record is trusted only when its token mark is this kernel's own.
        seen = []
        stub = self._stub_bus(seen)
        km.BUS_PORT = stub
        self.rec.write_text(json.dumps({"port": 25302, "pid": os.getpid(), "tok": "not-this-kernels-mark"}))
        ok, err = km._bus_quarantine_act({"mid": "px-1.2_abc.TESTHOST", "action": "approve"})
        self.assertEqual((ok, err), (True, "bus HTTP 200"), "the dial followed the override to the stub, not the foreign record")
        self.assertEqual(seen[0][0], "/quarantine/act")

    def test_the_ensure_exiting_zero_is_what_arms_the_record(self):
        # the flag is set by _ensure_postal_bus on a zero exit and by nothing else; a refused ensure (the fixed port under a test)
        # leaves it off, so a hermetic kernel never trusts a record
        saved = km.subprocess.run
        class R:
            def __init__(self, code): self.returncode, self.stderr = code, "refused"
        try:
            km._BUS_ENSURED[0] = False
            km.subprocess.run = lambda *a, **kw: R(1)
            km._ensure_postal_bus()
            self.assertFalse(km._BUS_ENSURED[0], "a refused ensure arms nothing")
            km.subprocess.run = lambda *a, **kw: R(0)
            km._ensure_postal_bus()
            self.assertTrue(km._BUS_ENSURED[0], "the ensure that answered arms the record")
        finally:
            km.subprocess.run = saved

    def test_the_decision_op_carries_the_recipient_sid_to_the_bus(self):
        bodies = []
        saved = km._bus_quarantine_act, km._mark_views_dirty
        km._bus_quarantine_act = lambda body: (bodies.append(body), (True, ""))[1]
        km._mark_views_dirty = lambda: None
        try:
            sent = []
            client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
            km.Handler._dispatch_ws(None, {"type": "quarantineDecision", "mid": "px-1.2_abc.TESTHOST", "action": "deny",
                                           "sid": "11111111-2222-3333-4444-555555555555", "feedback": "not now"}, client)
        finally:
            km._bus_quarantine_act, km._mark_views_dirty = saved
        self.assertEqual(bodies, [{"mid": "px-1.2_abc.TESTHOST", "action": "deny", "sid": "11111111-2222-3333-4444-555555555555", "feedback": "not now"}],
                         "the route strips the host; the bus is told which session the decision is for")


if __name__ == "__main__":
    unittest.main()
