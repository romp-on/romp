#!/usr/bin/env python3
"""Which tab you are looking at is YOURS (the user 2026-07-29).

Two dashboards on one kernel are two pairs of eyes, and one should not move the other. But every
cross-pane reveal went through _send_to_app, which pushes to EVERY client of an app, so opening a session
in one window switched tabs in the other, and clicking a distilled summary jumped both to the same turn.

Each dashboard now reports a `wid` (the shell mints one per browser tab in sessionStorage, shared with its
same-origin pane iframes and passed on the WS connect), and a reveal handled inside a WS op is aimed at
the asker's wid alone. An empty wid keeps the broadcast, so a client that reports none behaves as before.

Synthetic clients only; no sockets.
"""
import inspect
import os
import re
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_pvf", os.path.join(BIN, "romp-kernel"))


def _client(app, wid, sink):
    return {"app": app, "wid": wid, "alive": True, "send": lambda s: sink.append((wid, s))}


class TargetedSend(unittest.TestCase):
    def setUp(self):
        self.sink = []
        self._saved = list(km._clients)
        km._clients[:] = [_client("chat", "win-A", self.sink), _client("chat", "win-B", self.sink),
                          _client("feed", "win-A", self.sink)]

    def tearDown(self):
        km._clients[:] = self._saved

    def test_only_the_asking_dashboard_hears_it(self):
        km._send_to_view("chat", {"type": "focus", "id": "s1"}, "win-A")
        self.assertEqual([w for w, _ in self.sink], ["win-A"], "the other window is left where it was")

    def test_a_wid_names_a_dashboard_not_a_pane(self):
        # every pane of one window shares the wid, so a shell reveal reaches that window's shell only
        km._send_to_view("feed", {"type": "x"}, "win-A")
        self.assertEqual(len(self.sink), 1)
        self.sink.clear()
        km._send_to_view("feed", {"type": "x"}, "win-B")
        self.assertEqual(self.sink, [], "win-B has no feed pane open")

    def test_no_wid_still_broadcasts(self):
        # an older client, or a surface that supplies no id, must not silently receive nothing
        km._send_to_view("chat", {"type": "focus", "id": "s1"}, "")
        self.assertEqual(sorted(w for w, _ in self.sink), ["win-A", "win-B"])


class Wiring(unittest.TestCase):
    def test_the_ops_a_user_clicks_are_aimed_at_the_asker(self):
        src = inspect.getsource(km.Handler)
        # openByName/pickResult, openSession, deepLink and showOnTimeline all have the client in hand
        self.assertGreaterEqual(src.count("_reveal_chat_for(client,"), 3)
        self.assertIn("_reveal_or_confirm(msg[\"sid\"], _show_on_timeline_focus(msg), client)", src,
                      "the distilled-summary jump is one viewer's navigation")
        self.assertIn("\"anchorKind\": msg.get(\"anchorKind\")}, client)", src, "…and so is a deep link")

    def test_the_shell_mints_one_id_per_tab_and_the_panes_read_it(self):
        html = km._landing()
        self.assertIn("sessionStorage.getItem('romp:wid')", html)
        self.assertIn("sessionStorage.setItem('romp:wid'", html)
        # the pane's own shim prefers ?wid= (the VS Code host supplies one) then the shell's per-tab id
        shim = km._shim("chat", 1)
        self.assertIn('get("wid")', shim)
        self.assertIn('window.sessionStorage.getItem("romp:wid")', shim)

    def test_the_id_is_minted_before_any_pane_can_connect(self):
        # 2026-09-09 (the served tap test, tests/test_notification_tap_resume_browser.py, two runs in
        # three on localhost): the mint sat in the BODY script, after the iframes, and the chat pane's shim read
        # sessionStorage and connected first — its socket carried no wid, so a reveal the kernel aimed at this
        # dashboard's wid found no chat socket to deliver to and parked for a ready that never comes on a live
        # page. A phone's first-ever load runs the same race. The mint lives in the head, ahead of <body> and
        # so of the first <iframe>: no pane can connect before the id exists.
        html = km._landing()
        mint = html.index("sessionStorage.setItem('romp:wid'")
        self.assertLess(mint, html.index("<body"), "minted in the head")
        self.assertLess(mint, html.index("<iframe"), "…before any pane is even parsed")

    def test_an_empty_wid_falls_through_to_the_broadcast(self):
        self.assertIn("if not wid:\n        return _send_to_app(app, msg)",
                      inspect.getsource(km._send_to_view))

    def test_the_socket_actually_puts_the_reported_wid_ON_the_client(self):
        # The half that was missing (found 2026-07-30): the shell minted the id, the panes sent it and
        # _send_to_view filtered on it, but the WS handler never read it off the query — so every client
        # carried no wid, every wid resolved to "", and the targeted send fell through to the broadcast
        # it was written to replace. The tests above all hand-build clients WITH a wid, so none saw it.
        src = inspect.getsource(km.Handler)
        self.assertIn('wid = (q.get("wid") or [""])[0]', src, "the connect query is where a dashboard names itself")
        self.assertIn('client, sendq, lock = _new_ws_client(app, wid, self.connection', src, "…and it has to reach the client dict")
        self.assertIn('client = {"app": app, "wid": wid,', inspect.getsource(km._new_ws_client),
                      "…which the factory builds with it (the liveness change of 2026-09-03 moved the construction there)")

    def test_a_federated_pane_names_its_dashboard_to_the_REMOTE_kernel_too(self):
        # A remote kernel sees one anonymous client per federated pane unless the wid rides the relay
        # dial, so its per-viewer sends would broadcast and one window's jump would move every other.
        fed = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "federation.ts"), encoding="utf-8").read()
        self.assertIn("&wid=${encodeURIComponent(w)}", fed)
        # the relay forwards the whole query verbatim (only `token` is rewritten), so the wid survives
        self.assertIn('q["token"] = [rtok]', inspect.getsource(km.Handler._remote_ws))


class RemoteRevealReachesTheShell(unittest.TestCase):
    """A remote host's session must jump the same way a local one does (the user 2026-07-30, on a phone).

    On mobile ONE pane is on screen, so a jump is invisible unless the shell switches to Chat. The kernel
    sends that switch to its app=shell clients — but a federated dashboard opens a socket per host for
    each PANE and never for the shell, so a click on a remote card routes to that host's kernel and its
    reveal reaches nobody: the distilled summaries of remote sessions read as dead text. The chat pane
    knows it took the focus whichever kernel sent it, so it asks the shell for itself.
    """

    def setUp(self):
        self.render = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "render.ts"), encoding="utf-8").read()

    def test_the_chat_pane_asks_the_shell_to_come_forward_when_it_takes_a_focus(self):
        self.assertIn('window.parent.postMessage({ romp: "reveal", pane: "chat" }, "*")', self.render)
        self.assertIn('else if (m.type === "focus") {\n    revealSelfPane();', self.render)

    def test_the_dead_session_prompt_comes_forward_too(self):
        # confirmRevive is DRAWN in the chat pane — a modal on a pane you can't see is no modal at all
        self.assertIn('else if (m.type === "confirmRevive" && m.id) {\n    revealSelfPane();', self.render)

    def test_the_mobile_shell_switches_tabs_on_that_window_message(self):
        # the receiving half already existed (the Fleet pill posts the same shape); this pins the contract.
        # reveal() = un-hide a desktop-toggled-off pane FIRST, then the mobile tab switch (the user
        # 2026-08-13 — see tests/test_shell_reveal_unhide.py).
        self.assertIn("if(m.romp==='reveal'&&m.pane)reveal(m.pane);", km._LANDING_MOBILE_JS)


class CreateOpenReviveAreAimedToo(unittest.TestCase):
    """Creating, opening, forking or reviving a session moved EVERY dashboard's chat (the user
    2026-08-16, whose chats kept switching to sessions some other surface had just touched — a second
    window's click, or a bare `romp new` in a terminal). The 2026-07-29 per-viewer fix missed these
    four: its wiring test counted _reveal_chat_for(client, …) call sites, and the branches it did
    cover satisfied the count. Every reveal now names its asker, and an op with NO asking dashboard
    (the CLI's POST /new) moves nobody — the new tab still reaches every window via the push, just
    unselected."""

    def setUp(self):
        self.sink = []
        self._saved_clients = list(km._clients)
        self.win_a = _client("chat", "win-A", self.sink)
        km._clients[:] = [self.win_a, _client("chat", "win-B", self.sink)]

    def tearDown(self):
        km._clients[:] = self._saved_clients

    def test_opening_a_session_moves_the_asking_window_alone(self):
        saved = (km._live_map, km._sdk, km._push_all)
        km._live_map = lambda: {"s1": "web"}
        km._sdk = lambda: None
        km._push_all = lambda: None
        try:
            km._open_or_revive("s1", client=self.win_a)
        finally:
            (km._live_map, km._sdk, km._push_all) = saved
        self.assertEqual([w for w, _ in self.sink], ["win-A"], "win-B keeps the tab it was reading")

    def test_a_create_with_no_asking_dashboard_moves_nobody(self):
        class _BE:
            def spawn(self, nm, cwd, bg, fg, auth="", env=None):
                return "sid-new"

            def connect(self, sid):
                pass
        saved = (km._sdk, km._pick_identity_color, km._mark_views_dirty, km._push_session_now, km._live_map)
        km._sdk = lambda: _BE()
        km._pick_identity_color = lambda: ("#123456", "#ffffff")
        km._mark_views_dirty = lambda: None
        km._push_session_now = lambda sid: None
        km._live_map = lambda: {}   # the create door's live snapshot (names reserved atomically) — never the machine's live sessions
        try:
            km._create_sdk_session("web", "/tmp")                     # the CLI's POST /new: no dashboard in hand
            self.assertEqual(self.sink, [], "a terminal/script create yanks no window's chat")
            km._create_sdk_session("api", "/tmp", client=self.win_a)  # the picker's create: the asker follows it
        finally:
            (km._sdk, km._pick_identity_color, km._mark_views_dirty, km._push_session_now, km._live_map) = saved
        self.assertEqual([w for w, _ in self.sink], ["win-A"], "…and only the asker")

    def test_the_ops_that_make_or_wake_sessions_name_their_asker(self):
        src = inspect.getsource(km.Handler)
        self.assertIn('_open_or_revive(msg["id"], live=bool(msg.get("live")), client=client)', src,
                      "openSession — the click-op the 2026-07-29 fix missed")
        flat = re.sub(r"\s+", "", src)   # the create calls wrap; pin them whitespace-blind
        # the picker's create wraps too since tab groups (parent/tags ride the same call); the PROPERTY
        # is unchanged: the asker's client is named
        self.assertIn('_sid,extra=_create_sdk_session(nm,cwd,auth=(aiflg.parse_pick(a)[0]else""),client=client,', flat,
                      "the picker's createSession follows on the asking window")
        # POST /new threads env=env_req through the same call (its args carry inline comments, so the
        # pin walks the span rather than matching one literal); the PROPERTY is unchanged: no client
        self.assertIn('sid,extra=_create_sdk_session(nm,cwd,auth=(aiflg.parse_pick(a)[0]else""),prefs=b,', flat,
                      "POST /new (the CLI) has no dashboard in hand, and so names none")
        start = flat.index('sid,extra=_create_sdk_session(nm,cwd,auth=(aiflg.parse_pick(a)[0]else""),prefs=b,')
        call = flat[start:flat.index('tags=tags_req)', start) + len('tags=tags_req)')]   # the call's last arg since tab groups
        self.assertNotIn('client', call, "POST /new (the CLI) has no dashboard in hand, and so names none")
        self.assertIn('threading.Thread(target=_revive_session, args=(msg["id"], client), daemon=True)', src,
                      "the revive thread carries its asker across to the focus that clears the loader")
        self.assertIn('_fork_session(sid, str(msg.get("uuid") or ""), str(msg["name"]), client=client)',
                      inspect.getsource(km._drive), "a fork's new tab arrives focused for the forker alone")
        self.assertIn('_comment_promote(sid, str(msg["tid"]), str(msg["name"]), client=client)',
                      inspect.getsource(km._drive), "…and so does a promoted comment thread's")

    def test_the_broadcast_reveal_helper_is_gone(self):
        # _reveal_chat pushed to EVERY chat and shell client; with all four callers retired, keeping
        # it around is an invitation to reintroduce the yank. _reveal_chat_for is the only door, and
        # a caller with no dashboard in hand reveals to nobody rather than passing it None (an empty
        # wid falls through to the legacy broadcast, by design, for clients that report none).
        self.assertFalse(hasattr(km, "_reveal_chat"))


if __name__ == "__main__":
    unittest.main()
