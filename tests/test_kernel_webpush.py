#!/usr/bin/env python3
"""Web Push for the bell events (plans/ios-app.md proposal 2).

Covers the four layers separately, so a failure names its layer:

  * routes — /sw.js and /push/vapid-key are token-gated (they serve to the authed shell only);
    POST /push/subscribe validates, stores at 0600, and refuses loudly when the crypto
    dependency is missing; /push/unsubscribe prunes.
  * the worker — push + notificationclick ONLY. A fetch handler would fight the stale-bundle
    machinery (?v= cache-bust + the rstale banner), which assumes the network serves every load.
  * crypto — RFC 8291 aes128gcm round-trip: encrypt with the kernel's writer, decrypt with an
    independent receiver-side derivation from a browser keypair minted HERE, at run time (no
    credential-shaped literals in fixtures — repo rule). RFC 8292 VAPID: parse the header, verify
    the ES256 signature against the advertised key, check the claims.
  * the sink — _push_notify mirrors (title, body) to every subscription, sends the card gist and
    NOTHING more, prunes on the dead-subscription signal, and stands down silently when no
    device ever subscribed.

The cryptography package is required here (CI installs it; the kernel treats it as a soft
dependency and fails loudly without it — test_subscribe_without_crypto_is_a_loud_500).
"""
import io
import json
import os
import time
import threading
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
import tempfile

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import serialization
    HAVE_CRYPTO = True
except ImportError:                                   # pragma: no cover — CI installs it
    HAVE_CRYPTO = False

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
# Belt over conftest's suspenders. Under pytest, conftest.py rebinds XDG_STATE_HOME to a tempdir
# before any test module imports — but a RAW `python3 tests/test_kernel_webpush.py` skips conftest,
# and this file DELETES push state in _clear_push_state: on 2026-08-08 a raw run aimed that at the
# LIVE store, wiping the maintainer's phone subscription and rotating the real VAPID key (which
# orphans every subscription bound to it). Rebind the state root here, unconditionally, BEFORE the
# kernel module loads and captures jd.STATE into its path constants.
from pathlib import Path
_STATE_TD = tempfile.TemporaryDirectory()
jd.STATE = Path(_STATE_TD.name)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel_webpush", os.path.join(BIN, "romp-kernel")).load_module()


def _b64u(b):
    import base64
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _mint_browser_keys():
    """What a real subscription carries, minted fresh per test run: a P-256 keypair (p256dh) and
    a 16-byte auth secret. Assembled at run time on purpose — a longhand fake key in a fixture
    would trip the very secret scanner that guards this repo."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)
    return priv, _b64u(pub), _b64u(os.urandom(16))


def _serve_get(path, headers=None):
    """The real do_GET over a fake socket (the auth-hardening harness): (status, body_bytes)."""
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


def _clear_push_state():
    for name in ("push-subscriptions.json", "push-vapid.json"):
        try:
            (jd.STATE / name).unlink()
        except OSError:
            pass


class ServiceWorkerRoute(unittest.TestCase):
    def test_sw_is_gated_and_push_only(self):
        status, _ = _serve_get("/sw.js")
        self.assertEqual(status, 403, "the worker serves to the authed shell only")
        status, body = _serve_get("/sw.js", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 200)
        js = body.decode()
        self.assertIn("addEventListener('push'", js)
        self.assertIn("addEventListener('notificationclick'", js)
        # NO fetch handler, ever: a caching worker would fight the stale-bundle detection,
        # which assumes the network serves every load (plans/ios-app.md)
        self.assertNotIn("'fetch'", js)
        self.assertNotIn("caches", js)
        # an UPDATED worker must take over immediately — this one owns no caches, so 'waiting'
        # only delays fixes (the sid-blind predecessor kept handling taps, the user 2026-08-08)
        self.assertIn("skipWaiting()", js)
        self.assertIn("clients.claim()", js)

    def test_sw_click_lands_on_the_session_that_fired(self):
        # the user 2026-08-08: the first real push opened the app on a DIFFERENT session. The sid
        # rides the notification's data; a live window gets it over postMessage, a cold start gets
        # it in the URL for the shell's POST /reveal.
        _, body = _serve_get("/sw.js", headers={"X-Romp-Token": km.TOKEN})
        js = body.decode()
        self.assertIn("data:{sid:", js)
        self.assertIn("pushReveal", js)
        self.assertIn("/?push-reveal=", js)
        # ...and the closed-app badge count comes from the payload
        self.assertIn("setAppBadge", js)


@unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
class VapidKeys(unittest.TestCase):
    def setUp(self):
        _clear_push_state()

    def test_key_route_is_gated_and_stable(self):
        status, _ = _serve_get("/push/vapid-key")
        self.assertEqual(status, 403)
        status, body = _serve_get("/push/vapid-key", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 200)
        k1 = json.loads(body.decode())["key"]
        import base64
        raw = base64.urlsafe_b64decode(k1 + "=" * (-len(k1) % 4))
        self.assertEqual((len(raw), raw[0]), (65, 0x04), "uncompressed P-256 point")
        # stable across calls: a subscription is bound to the key it was minted with
        _, body2 = _serve_get("/push/vapid-key", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(json.loads(body2.decode())["key"], k1)

    def test_private_key_is_0600(self):
        km._vapid_keys()
        mode = (jd.STATE / "push-vapid.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


@unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
class Rfc8291Encryption(unittest.TestCase):
    def test_round_trip_against_an_independent_receiver(self):
        # decrypt with the RECEIVER's half of RFC 8291, derived here from first principles —
        # ua private key + auth secret → same IKM → cek/nonce → AESGCM open
        ua_priv, p256dh, auth_b64 = _mint_browser_keys()
        payload = json.dumps({"title": "romp: web", "body": "Needs you: pick a migration"}).encode()
        blob = km._webpush_encrypt(payload, p256dh, auth_b64)

        salt, rs, idlen = blob[:16], int.from_bytes(blob[16:20], "big"), blob[20]
        self.assertEqual((rs, idlen), (4096, 65), "RFC 8188 header: rs=4096, keyid=an EC point")
        as_pub_raw, ct = blob[21:21 + idlen], blob[21 + idlen:]
        as_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub_raw)
        ua_pub_raw = ua_priv.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

        import base64
        auth = base64.urlsafe_b64decode(auth_b64 + "=" * (-len(auth_b64) % 4))
        hkdf = lambda s, ikm, info, n: HKDF(algorithm=hashes.SHA256(), length=n,
                                            salt=s, info=info).derive(ikm)
        ikm = hkdf(auth, ua_priv.exchange(ec.ECDH(), as_pub),
                   b"WebPush: info\x00" + ua_pub_raw + as_pub_raw, 32)
        cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, ct, None)
        self.assertEqual(plain[-1:], b"\x02", "last-record delimiter")
        self.assertEqual(plain[:-1], payload)

    def test_seams_make_it_deterministic(self):
        # same salt + same ephemeral key → same bytes; fresh defaults → different bytes (real
        # sends never reuse a salt/key pair)
        _, p256dh, auth = _mint_browser_keys()
        eph = ec.generate_private_key(ec.SECP256R1())
        salt = os.urandom(16)
        a = km._webpush_encrypt(b"x", p256dh, auth, _salt=salt, _eph=eph)
        b = km._webpush_encrypt(b"x", p256dh, auth, _salt=salt, _eph=eph)
        c = km._webpush_encrypt(b"x", p256dh, auth)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


@unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
class VapidAuth(unittest.TestCase):
    def setUp(self):
        _clear_push_state()

    def test_header_verifies_and_claims_the_push_origin(self):
        import base64
        hdr = km._vapid_auth("https://push.example.net/send/abc123")
        self.assertTrue(hdr.startswith("vapid t="))
        jwt, key = hdr[len("vapid t="):].split(", k=")
        h64, c64, s64 = jwt.split(".")
        dec = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        self.assertEqual(json.loads(dec(h64)), {"alg": "ES256", "typ": "JWT"})
        claims = json.loads(dec(c64))
        # audience is the push SERVICE's origin (Apple's/Google's relay), never the full endpoint
        self.assertEqual(claims["aud"], "https://push.example.net")
        self.assertGreater(claims["exp"], time.time())
        self.assertTrue(claims["sub"].startswith("mailto:"))
        # signature verifies against the key the header itself advertises (k=)
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), dec(key))
        sig = dec(s64)
        der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        pub.verify(der, ("%s.%s" % (h64, c64)).encode(), ec.ECDSA(hashes.SHA256()))  # raises on mismatch


class SubscribeRoutes(unittest.TestCase):
    """POST /push/subscribe|unsubscribe over the real handler on loopback (the ServeSecurity
    pattern — a fake socket cannot exercise Content-Length body reads)."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _clear_push_state()

    def _post(self, path, body, token=True):
        import urllib.request, urllib.error
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = km.TOKEN
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def _sub_body(self):
        if HAVE_CRYPTO:
            _, p256dh, auth = _mint_browser_keys()
        else:
            p256dh, auth = _b64u(b"\x04" + os.urandom(64)), _b64u(os.urandom(16))
        return {"endpoint": "https://push.example.net/send/dev-" + _b64u(os.urandom(6)),
                "keys": {"p256dh": p256dh, "auth": auth}}

    @unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
    def test_subscribe_stores_at_0600_and_unsubscribe_prunes(self):
        sub = self._sub_body()
        code, _ = self._post("/push/subscribe", sub)
        self.assertEqual(code, 200)
        f = jd.STATE / "push-subscriptions.json"
        self.assertEqual(f.stat().st_mode & 0o777, 0o600,
                         "endpoints are capability URLs — the store gets the token treatment")
        self.assertIn(sub["endpoint"], km._push_subs())
        # same device re-subscribing overwrites, never duplicates
        code, _ = self._post("/push/subscribe", sub)
        self.assertEqual((code, len(km._push_subs())), (200, 1))
        code, _ = self._post("/push/unsubscribe", {"endpoint": sub["endpoint"]})
        self.assertEqual((code, km._push_subs()), (200, {}))

    def test_subscribe_requires_the_token(self):
        code, _ = self._post("/push/subscribe", self._sub_body(), token=False)
        self.assertEqual(code, 403)

    def test_garbage_is_a_400_not_a_stored_row(self):
        for bad in ({}, {"endpoint": "http://not-https", "keys": {"p256dh": "x", "auth": "y"}},
                    {"endpoint": "https://push.example.net/x", "keys": {}}):
            code, _ = self._post("/push/subscribe", bad)
            self.assertEqual(code, 400, bad)
        self.assertEqual(km._push_subs(), {})

    def test_subscribe_without_crypto_is_a_loud_500(self):
        # the fail-loudly rule: a subscription the kernel can never deliver to must be REFUSED
        # with the missing package named, not stored and silently starved
        with mock.patch.object(km, "_PUSH_CRYPTO", [False]):
            code, body = self._post("/push/subscribe", self._sub_body())
        self.assertEqual(code, 500)
        self.assertIn("cryptography", body)
        self.assertEqual(km._push_subs(), {})


class PushSink(unittest.TestCase):
    def setUp(self):
        _clear_push_state()

    def test_wired_beside_system_notify(self):
        # the sink hangs off the SAME loop as _system_notify — the armed-bell diff on fresh feed
        # builds — so it inherits the transition-event detection and the silent first-build
        # baseline by construction, rather than re-deriving either
        import inspect
        src = inspect.getsource(km._cached_feed)
        self.assertIn("_system_notify(_t, _b)", src)
        self.assertIn("_push_notify(_t, _b, _sid, _badge)", src)
        self.assertIn("_badge_push(_badge)", src)

    def test_no_subscriptions_means_no_work(self):
        with mock.patch.object(km, "_push_send_one") as send:
            km._push_notify("romp: web", "Needs you")
        send.assert_not_called()

    def test_delivers_gist_only_and_prunes_dead_endpoints(self):
        km._save_push_subs({
            "https://push.example.net/send/live": {
                "endpoint": "https://push.example.net/send/live",
                "keys": {"p256dh": "k", "auth": "a"}},
            "https://push.example.net/send/dead": {
                "endpoint": "https://push.example.net/send/dead",
                "keys": {"p256dh": "k", "auth": "a"}},
        })
        seen = {}
        done = threading.Event()

        def fake_send(sub, payload):
            seen[sub["endpoint"]] = payload
            if len(seen) == 2:
                done.set()
            return not sub["endpoint"].endswith("/dead")

        with mock.patch.object(km, "_push_send_one", side_effect=fake_send), \
             mock.patch.object(km, "_push_crypto", return_value=True):
            km._push_notify("romp: web", "Needs you: pick a migration", "SID-web", 3)
            self.assertTrue(done.wait(5), "the send thread ran")
            # pruning happens after the sends; poll briefly for the store write
            for _ in range(100):
                if "https://push.example.net/send/dead" not in km._push_subs():
                    break
                time.sleep(0.05)
        self.assertEqual(set(km._push_subs()), {"https://push.example.net/send/live"},
                         "404/410 prunes; success stays")
        body = json.loads(list(seen.values())[0].decode())
        # the plan's privacy note, pinned: the payload is the card's gist (title + body) plus two
        # pieces of ROUTING metadata — the sid the tap should land on and the badge count — and
        # nothing more (no brief, no transcript), even though the content is E2E-encrypted
        self.assertEqual(set(body), {"title", "body", "sid", "badge"})
        self.assertEqual((body["title"], body["sid"], body["badge"]), ("romp: web", "SID-web", 3))

    def test_missing_crypto_with_subscriptions_says_so(self):
        km._save_push_subs({"https://push.example.net/send/x": {
            "endpoint": "https://push.example.net/send/x",
            "keys": {"p256dh": "k", "auth": "a"}}})
        with mock.patch.object(km, "_PUSH_CRYPTO", [False]), \
             mock.patch.object(km.sys, "stderr", new=io.StringIO()) as err, \
             mock.patch.object(km, "_push_send_one") as send:
            km._push_notify("romp: web", "Needs you")
        send.assert_not_called()
        self.assertIn("cryptography", err.getvalue(), "a starving phone is never silent")


def _fake_ws_client(app, wid):
    """Just enough of a _clients row for the reveal/badge paths: send() records the parsed JSON."""
    got = []
    return {"app": app, "wid": wid, "alive": True,
            "send": lambda s: got.append(json.loads(s))}, got


class RevealAiming(unittest.TestCase):
    """A push tap lands ON the session that fired (the user 2026-08-08). The cold-start half:
    POST /reveal parks the focus keyed by the asking window's wid, delivered on the exact event
    it waits for — that window's chat pane saying ready — and aimed at that pane alone."""

    def setUp(self):
        km._PENDING_REVEAL[0] = None
        self._added = []

    def tearDown(self):
        with km._clients_lock:
            for c in self._added:
                if c in km._clients:
                    km._clients.remove(c)
        km._PENDING_REVEAL[0] = None

    def _register(self, app, wid):
        c, got = _fake_ws_client(app, wid)
        with km._clients_lock:
            km._clients.append(c)
        self._added.append(c)
        return c, got

    def test_connected_pane_gets_it_now_dead_session_gets_revive(self):
        c, got = self._register("chat", "W1")
        with mock.patch.object(km, "_tmux_sessions", return_value={"SID-live": {}}):
            self.assertTrue(km._reveal_request("SID-live", "W1"))
        self.assertEqual(got, [{"type": "focus", "id": "SID-live", "live": True}])
        self.assertIsNone(km._PENDING_REVEAL[0], "delivered → nothing parked")
        # a DEAD session never silently reveals — the revive prompt instead (_reveal_or_confirm's split)
        got.clear()
        with mock.patch.object(km, "_tmux_sessions", return_value={}), \
             mock.patch.object(km, "_name_of", return_value="web"):
            km._reveal_request("SID-gone", "W1")
        self.assertEqual(got[0]["type"], "confirmRevive")

    def test_boot_race_parks_then_ready_consumes_aimed_by_wid(self):
        # the norm: the shell's fetch beats its chat iframe's WS, so nothing is connected yet
        with mock.patch.object(km, "_tmux_sessions", return_value={"SID-live": {}}):
            self.assertFalse(km._reveal_request("SID-live", "W-phone"))
            self.assertEqual(km._PENDING_REVEAL[0], {"sid": "SID-live", "wid": "W-phone"})
            # another dashboard's pane saying ready must NOT steal it (the 2026-07-29 rule)
            other, other_got = _fake_ws_client("chat", "W-desktop")
            km._consume_pending_reveal(other)
            self.assertEqual(other_got, [])
            self.assertIsNotNone(km._PENDING_REVEAL[0])
            # a non-chat pane of the RIGHT window doesn't take it either
            feed, feed_got = _fake_ws_client("feed", "W-phone")
            km._consume_pending_reveal(feed)
            self.assertEqual(feed_got, [])
            # the aimed pane arrives → delivered once, latch cleared
            mine, mine_got = _fake_ws_client("chat", "W-phone")
            km._consume_pending_reveal(mine)
            self.assertEqual(mine_got, [{"type": "focus", "id": "SID-live", "live": True}])
            self.assertIsNone(km._PENDING_REVEAL[0])
            km._consume_pending_reveal(mine)
            self.assertEqual(len(mine_got), 1, "consumed means consumed")

    def test_a_widless_park_matches_the_first_chat_pane(self):
        # sessionStorage blocked → the shell has no wid; better the first chat pane than a dropped tap
        with mock.patch.object(km, "_tmux_sessions", return_value={"S": {}}):
            km._reveal_request("S", "")
            c, got = _fake_ws_client("chat", "W-any")
            km._consume_pending_reveal(c)
        self.assertEqual(got[0]["id"], "S")


class RevealRoute(unittest.TestCase):
    """POST /reveal over the real handler (the ServeSecurity pattern)."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        km._PENDING_REVEAL[0] = None

    def _post(self, path, body, token=True):
        import urllib.request, urllib.error
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = km.TOKEN
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_parks_for_the_named_wid(self):
        code, body = self._post("/reveal", {"sid": "SID-x", "wid": "W-x"})
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["delivered"])
        self.assertEqual(km._PENDING_REVEAL[0], {"sid": "SID-x", "wid": "W-x"})

    def test_requires_token_and_sid(self):
        code, _ = self._post("/reveal", {"sid": "S"}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/reveal", {"wid": "W"})
        self.assertEqual(code, 400)
        self.assertIsNone(km._PENDING_REVEAL[0])


class Badge(unittest.TestCase):
    """Proposal 3: the app icon wears the needs-you count."""

    def setUp(self):
        km._BADGE_LAST[0] = None

    def test_counts_real_needs_input_cards_only(self):
        feed = {"asks": [
            {"itemId": "a", "column": "needs_input"},
            {"itemId": "b", "column": "needs_input", "provisional": True},   # placeholder churn
            {"itemId": "c", "column": "working"},
            {"itemId": "d", "column": "completed"},
        ]}
        self.assertEqual(km._needs_you_count(feed), 1)

    def test_pushes_to_shells_only_on_change(self):
        sent = []
        with mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            km._badge_push(2)
            km._badge_push(2)          # same number again — a re-send would be a pointless wake
            km._badge_push(0)          # dropping to zero IS a change: the icon must clear
        self.assertEqual(sent, [("shell", {"type": "badge", "n": 2}),
                                ("shell", {"type": "badge", "n": 0})])


class LandingRevealPins(unittest.TestCase):
    def test_shell_carries_both_halves_of_the_tap(self):
        html = km._landing()
        self.assertIn("pushReveal", html)          # live window: SW message → focus into the chat iframe
        self.assertIn("push-reveal", html)         # cold start: URL param → POST /reveal {sid, wid}
        self.assertIn("'/reveal'", html)
        self.assertIn("romp:wid", html)            # aimed by the shell's own per-window id

    def test_shell_ws_trues_up_the_badge(self):
        html = km._landing()
        self.assertIn("{type:'ready'}", html)      # connect → the kernel answers with the current count
        self.assertIn("setAppBadge", html)
        self.assertIn("clearAppBadge", html)       # zero clears, never leaves a stale number


class RailBell(unittest.TestCase):
    """The desktop rail carries the same bell as the mobile tab bar (the user 2026-08-08). Since
    2026-09-05 the pair OPENS THE POPOVER (tests/test_kernel_notify_popover.py) whose rows are the
    switches: the kernel-wide master (the user 2026-08-09's model: on = every task notifies unless
    its own bell mutes it) and this device's push subscription, pulled apart so a phone turning
    itself off no longer silences every device."""

    def test_shell_serves_both_bells_and_one_flow_drives_them(self):
        status, body = _serve_get("/", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertIn("id=rail-bell hidden", page, "the bells ship hidden; the wiring reveals them at boot")
        self.assertIn("id=mbell hidden", page, "the mobile bell is unchanged")
        # ONE wiring drives the pair — reveal, paint and busy all iterate the same list — so the
        # two bells can never disagree about the master's state
        self.assertIn("querySelectorAll('#mbell,#rail-bell')", page)
        self.assertNotIn("getElementById('mbell')", page, "the single-bell wiring is gone")
        # the rail bell paints its states exactly like the mobile one
        self.assertIn(".rail-acts #rail-bell.on{color:var(--accent)}", page)
        self.assertIn(".rail-acts #rail-bell.busy{opacity:.45}", page)
        # the on-state must survive the light theme, whose .rail-act recolor outspecifies the bare
        # `.on` rule — with no light restatement on and off rendered pixel-identical there (the
        # user 2026-09-02)
        self.assertIn("body.theme-light .rail-act.on{color:var(--accent)}", page)
        # …and OFF reads as a slashed bell — the app's one bell-off idiom (feed card bell, timeline
        # lane bell) — never a color difference alone
        self.assertEqual(page.count("class='bell-slash'"), 2, "both bells carry the slash glyph")
        self.assertIn(".bell-slash{display:none}", page)
        self.assertIn("#rail-bell:not(.on) .bell-slash,#mbell:not(.on) .bell-slash{display:block}", page)

    def test_the_bell_opens_the_popover_whose_rows_are_the_switches(self):
        # (2026-09-05: was "the bell is the master switch, not a device toggle" — the tap now opens
        # the popover, and the All-devices row is the master while This-device is the subscription)
        _, body = _serve_get("/", headers={"X-Romp-Token": km.TOKEN})
        page = body.decode()
        # kernel-authoritative paint of the master: GET /notify-all at boot and the shell WS push
        # on every toggle, so every dashboard's row agrees
        self.assertIn("fetch('/notify-all')", page)
        self.assertIn("window.__rompNotifyAllPaint", page)
        self.assertIn("m.type==='notifyAll'", page, "the shell WS repaints every open dashboard")
        self.assertIn("post('/notify-all',{on:want})", page)
        self.assertIn("id=rbell-pop", page)
        self.assertIn("data-act=all", page)
        self.assertIn("data-act=dev", page)
        # the bell shows everywhere — the master matters even where the Push API is missing (the
        # kernel box still gets osascript, other devices still buzz); only the device row gates
        self.assertNotIn("('Notification' in window))return", page,
                         "the old whole-bell capability bail is gone")
        self.assertIn("var canPush=", page)
        # the permission ask still runs in the tap's own stack (iOS voids the gesture across awaits)
        self.assertIn("Notification.requestPermission():null", page)
        # the glyph is THIS device's truth now: master AND subscribed (tests/test_kernel_notify_popover.py)
        self.assertIn("var lit=isOn&&(canPush?devOn:true)", page)


class MasterBellRoute(unittest.TestCase):
    """GET/POST /notify-all — the master bell's kernel half (the user 2026-08-09). Live server for
    the POST (the SubscribeRoutes pattern: a fake socket cannot exercise Content-Length reads)."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        try:
            (jd.STATE / "notify-cards.json").unlink()
        except OSError:
            pass
        km._notify_cards_cache.clear()

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

    def test_get_is_gated_and_reads_the_store(self):
        status, _ = _serve_get("/notify-all")
        self.assertEqual(status, 403, "the master state is behind the serve token like every page")
        status, body = _serve_get("/notify-all", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual((status, json.loads(body)), (200, {"on": False}))

    def test_post_flips_and_broadcasts(self):
        sent = []
        with mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            code, body = self._post("/notify-all", {"on": True})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True, "on": True}))
        self.assertTrue(km._notify_all_on())
        self.assertIn(("shell", {"type": "notifyAll", "on": True}), sent,
                      "every open dashboard's bell repaints on the toggle, not just the clicker's")
        _, body = _serve_get("/notify-all", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(json.loads(body), {"on": True})
        code, _ = self._post("/notify-all", {"on": False})
        self.assertEqual(code, 200)
        self.assertFalse(km._notify_all_on())

    def test_post_requires_token_and_refuses_garbage(self):
        code, _ = self._post("/notify-all", {"on": True}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/notify-all", None, raw=b"not json")
        self.assertEqual(code, 400)
        self.assertFalse(km._notify_all_on(), "a refused body must not flip the master")


if __name__ == "__main__":
    unittest.main()
