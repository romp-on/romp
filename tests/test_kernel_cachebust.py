#!/usr/bin/env python3
"""Tests for the kernel's bundle cache-busting (the stale-client fix). The browser was running an old
cached feed.js across kernel restarts; the fix versions every bundle url with the build mtime and
serves HTML no-cache, so a reload always pulls fresh JS. We pin the page-builder + version helpers
(the HTTP layer isn't unit-tested in this repo — see test_kernel.py's header).
"""
import os
from importlib.machinery import SourceFileLoader
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_cb", os.path.join(BIN, "romp-kernel")).load_module()


def test_dist_ver_is_int():
    assert isinstance(km._dist_ver(), int)


def test_feed_page_versions_its_bundle():
    html = km._feed_page()
    assert "/dist/feed.js?v=" in html
    assert "/dist/feed.css?v=" in html


def test_chat_page_versions_its_bundle():
    html = km._chat_page()
    assert "/dist/render.js?v=" in html
    assert "/dist/styles.css?v=" in html


def test_version_info_shape():
    info = km._version_info()
    for k in ("kernel_sha", "pid", "started", "uptime_s", "dist_ver", "bundles"):
        assert k in info, k
    assert isinstance(info["bundles"], dict)


def test_version_info_carries_no_repo_path():
    # /version is AUTH-EXEMPT, so it must reveal nothing that grants leverage. rompDir used to ride
    # here and the VS Code extension turned it into an execFile("bash", …) target — whatever answered
    # the port chose the directory a shell ran in. The extension resolves its own install path now
    # (update-target.ts), so rompDir has no reader and must not be published.
    info = km._version_info()
    assert "rompDir" not in info, "rompDir must not ride the auth-exempt /version (exec-target leak)"


def test_send_emits_cache_control():
    # _send(..., cache="no-cache") must add the Cache-Control header. Drive it with a tiny fake handler
    # capturing send_header calls (no socket).
    sent = []

    class Fake:
        _send = km.Handler._send

        def send_response(self, *a):
            pass

        def send_header(self, k, v):
            sent.append((k, v))

        def end_headers(self):
            pass

        class wfile:
            @staticmethod
            def write(_b):
                pass

    Fake().wfile = Fake.wfile
    Fake._send(Fake(), 200, "hi", "text/plain", cache="no-cache")
    assert ("Cache-Control", "no-cache") in sent


def test_landing_shows_build_staleness_banner():
    # the combined shell injects a centered Reload/Dismiss notification, shown when the served
    # dist_ver exceeds the version this tab loaded (baked in at serve time, not a placeholder).
    html = km._landing()
    assert "id=rstale" in html
    assert "rstale-reload" in html and "rstale-dismiss" in html
    assert "__LOADEDVER__" not in html, "load-time version must be interpolated, not a placeholder"
    assert "/version" in html and "location.reload()" in html


def test_gear_panel_drops_inline_stale_hint():
    # the gear stays the version display, but the proactive reload alert now lives in the banner,
    # so the gear no longer shows its own inline "stale" hint (one stale surface, not two).
    # the gear moved into the feed BUNDLE (ui/webview/gear.js, 2026-07-13)
    import pathlib
    gear = (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()
    assert "id=rgear" in gear, "the version gear should remain"
    assert "reload</span>" not in gear and "reload</span>" not in km._feed_page()
