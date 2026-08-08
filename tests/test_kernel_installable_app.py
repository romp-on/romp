#!/usr/bin/env python3
"""The installable dashboard (plans/ios-app.md proposal 1): manifest + icons + Apple metas.

What these pin, and why it is behavior and not source position (the auth-hardening doctrine —
asking the handler is the only thing that can catch a route on the wrong side of the gate):

  * /manifest.webmanifest and the three install icons answer 200 with NO credentials. Browsers
    fetch <link rel=manifest> and the manifest's icon list with credentials OMITTED, so a
    token-gated manifest 403s at the exact moment "Add to Home Screen" consults it. These four
    responses carry brand strings and brand art only.
  * Everything else under /media/ stays token-gated — the exemption is a fixed three-name
    allowlist, not a prefix.
  * The install metas ride the LANDING SHELL only: the chat/feed/timeline/fleet pages are panes
    inside it, never install targets, and the login page stays self-contained.

Synthetic only — no real session data anywhere in this file.
"""
import io
import json
import os
import struct
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel_installable", os.path.join(BIN, "romp-kernel")).load_module()


def _serve_get(path, headers=None):
    """Drive the REAL do_GET dispatcher over a fake socket; return (status, body_bytes, headers)."""
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
    h.send_header = lambda k, v: captured.setdefault("headers", {}).__setitem__(k, v)
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_GET()
    return captured.get("status"), h.wfile.getvalue(), captured.get("headers", {})


def _png_size(b):
    """Width/height straight from the IHDR chunk — no image library needed to pin a dimension."""
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", b[16:24])


class ManifestRoute(unittest.TestCase):
    def test_manifest_serves_without_credentials(self):
        status, body, headers = _serve_get("/manifest.webmanifest")   # no token, no cookie
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/manifest+json")
        m = json.loads(body.decode())
        self.assertEqual(m["display"], "standalone")                  # the whole point: no browser chrome
        self.assertEqual(m["start_url"], "/")
        # both colors are the SHELL's background (#1e1e1e), so the standalone frame never
        # flashes a foreign color around the boot splash
        self.assertEqual((m["background_color"], m["theme_color"]), ("#1e1e1e", "#1e1e1e"))
        srcs = {i["src"] for i in m["icons"]}
        self.assertEqual(srcs, {"/media/romp-app-192.png", "/media/romp-app-512.png"})

    def test_manifest_carries_brand_strings_only(self):
        # the exemption's justification, pinned: nothing beyond name/colors/icon paths may ride it
        m = json.loads(km._APP_MANIFEST)
        self.assertEqual(set(m), {"name", "short_name", "start_url", "display",
                                  "background_color", "theme_color", "icons"})


class InstallIcons(unittest.TestCase):
    def test_icons_serve_without_credentials_and_are_real_pngs(self):
        for name, px in (("romp-touch-180.png", 180), ("romp-app-192.png", 192),
                         ("romp-app-512.png", 512)):
            status, body, headers = _serve_get("/media/" + name)      # no token, no cookie
            self.assertEqual(status, 200, name)
            self.assertEqual(headers.get("Content-Type"), "image/png", name)
            self.assertEqual(_png_size(body), (px, px), name)

    def test_the_rest_of_media_stays_gated(self):
        # the exemption is the three-name allowlist, not a /media/ prefix: any other asset —
        # including one that EXISTS — still needs the token
        status, _, _ = _serve_get("/media/romp-swirl-glyph.svg")
        self.assertEqual(status, 403)
        status, _, _ = _serve_get("/media/romp-wordmark.png")
        self.assertEqual(status, 403)

    def test_gated_media_still_serves_with_the_token(self):
        status, body, _ = _serve_get("/media/romp-swirl-glyph.svg",
                                     headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 200)
        self.assertIn(b"<svg", body)


class InstallMetas(unittest.TestCase):
    def test_shell_head_carries_the_install_surface(self):
        html = km._landing()
        self.assertIn("<link rel=manifest href=/manifest.webmanifest>", html)
        self.assertIn("<link rel=apple-touch-icon href=/media/romp-touch-180.png>", html)
        self.assertIn("<meta name=apple-mobile-web-app-capable content=yes>", html)
        # black (opaque), NOT black-translucent: opaque keeps the webview below the status bar,
        # so the standalone app needs no top safe-area handling
        self.assertIn("<meta name=apple-mobile-web-app-status-bar-style content=black>", html)
        self.assertIn("<meta name=theme-color content='#1e1e1e'>", html)

    def test_panes_are_not_install_targets(self):
        # the iframes live INSIDE the installed shell; a manifest on any of them would offer
        # "Add to Home Screen" for a bare pane
        for page in (km._chat_page(), km._feed_page(), km._timeline_page(), km._fleet_page()):
            self.assertNotIn("rel=manifest", page)
            self.assertNotIn("apple-mobile-web-app-capable", page)

    def test_login_page_stays_out_of_it(self):
        # the token login page is deliberately self-contained (tests/test_token_login_page.py);
        # it must not advertise installability either — installing the LOGIN page is never right
        self.assertNotIn("rel=manifest", km._TOKEN_LOGIN_HTML)
        self.assertNotIn("apple-touch-icon", km._TOKEN_LOGIN_HTML)


if __name__ == "__main__":
    unittest.main()
