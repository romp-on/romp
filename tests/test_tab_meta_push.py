#!/usr/bin/env python3
"""The chat strip's LIVE carrier for headless edits (the user 2026-08-24): a one-shot POST
/rename | /color | /tag must render in the chat pane with no reload — and the design says the
recurring tabOrder push is the carrier (name + identity color per tab, plus the views/tags blob),
never per-route confirm frames. These tests pin the KERNEL half of that loop end to end: each POST
lands in its authoritative store, the NEXT push cycle's tabOrder frame carries the fresh value to a
chat client (assembled per cycle, no caching between), the per-client dedup passes the changed frame
(and keeps suppressing unchanged ones), and /rename pokes the pusher awake like its siblings already
do. Drives the REAL Handler over HTTP + the REAL _push (the test_color_route.py pattern).
Synthetic only: the notes-api demo world (web), TESTHOST paths, placeholder UUIDs."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel_tmp", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class TabMetaPush(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.names = Path(self.tmp) / "names"
        self.names.mkdir()
        (self.names / SID).write_text("web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        self._saved = (km.NAMES, km.jd.STATE, km._tmux_sessions, km._live_names,
                       km._mark_views_dirty, km.Sessions.backend_for,
                       km._chat_tab_sessions, km._cached_feed)
        km.NAMES = self.names
        km.jd.STATE = Path(self.tmp) / "state"
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._pal_cache.update({"name": km.pal.DEFAULT, "mt": None})
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: {self._name(): SID}
        self.dirty = []
        km._mark_views_dirty = lambda: self.dirty.append(1)
        names = self.names

        class BE:  # the rename path a dormant/live session takes: the names registry's first field
            def rename(self, sid, name):
                rec = (names / sid).read_text().split("\t")
                rec[0] = name
                (names / sid).write_text("\t".join(rec))
                return True
        km.Sessions.backend_for = staticmethod(lambda sid: BE())
        # ONE shown session whose row reads the registry live — the same store the real
        # _chat_tab_sessions labels rows from — so the push assembles from current truth each cycle.
        km._chat_tab_sessions = lambda now, tmux: [
            {"sid": SID, "name": self._name(), "path": os.path.join(self.tmp, "none.jsonl"),
             "anchor": SID}]
        km._cached_feed = lambda *a, **k: None   # no feed build — this pins the tabOrder frame only
        self.frames = []
        self.client = {"app": "chat", "alive": True, "sent": {},
                       "send": lambda s: self.frames.append(json.loads(s))}

    def tearDown(self):
        (km.NAMES, km.jd.STATE, km._tmux_sessions, km._live_names,
         km._mark_views_dirty, km.Sessions.backend_for,
         km._chat_tab_sessions, km._cached_feed) = self._saved
        km._pal_cache.update({"name": km.pal.DEFAULT, "mt": None})

    def _name(self):
        return (self.names / SID).read_text().split("\t")[0]

    def _post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def _tab_orders(self):
        return [f for f in self.frames if f.get("type") == "tabOrder"]

    def _cycle(self):
        km._push([self.client])

    def test_rename_rides_the_next_push_cycle(self):
        self._cycle()
        self.assertEqual(self._tab_orders()[-1]["tabs"][0]["name"], "web")
        r = self._post("/rename", {"target": "web", "name": "api"})
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(self.dirty, "a successful rename marks views dirty — the pusher wakes NOW, "
                                    "not at the backstop (its siblings /color and /tag already do)")
        n = len(self._tab_orders())
        self._cycle()
        tabs = self._tab_orders()[-1]["tabs"]
        self.assertGreater(len(self._tab_orders()), n, "the changed frame passes the per-client dedup")
        self.assertEqual(tabs[0]["name"], "api", "the very next cycle's tabOrder carries the new label")

    def test_recolor_rides_the_next_push_cycle(self):
        self._cycle()
        self.assertEqual(self._tab_orders()[-1]["tabs"][0]["color"]["bg"], "#1EA1EB")
        r = self._post("/color", {"target": "web", "bg": "#54B204"})
        self.assertTrue(r.get("ok"), r)
        self._cycle()
        c = self._tab_orders()[-1]["tabs"][0]["color"]
        self.assertEqual(c["bg"], "#54B204", "the next cycle's tabOrder carries the new identity color")
        self.assertTrue(c.get("fg"), "the palette's fg word rides with it")

    def test_tag_edit_rides_the_next_push_cycle(self):
        self._cycle()
        base = self._tab_orders()[-1]["views"]
        self.assertFalse(any(t.get("name") == "workers" for t in (base.get("tags") or [])))
        r = self._post("/tag", {"name": "workers", "add": ["web"]})
        self.assertTrue(r.get("ok"), r)
        self._cycle()
        tags = self._tab_orders()[-1]["views"].get("tags") or []
        mine = [t for t in tags if t.get("name") == "workers"]
        self.assertTrue(mine, "the next cycle's tabOrder views blob carries the new tag")
        self.assertIn(SID, mine[0].get("members") or [], "…with the session filed under it")

    def test_an_unchanged_cycle_is_deduped_but_never_a_changed_one(self):
        self._cycle()
        n = len(self._tab_orders())
        self._cycle()
        self.assertEqual(len(self._tab_orders()), n,
                         "no change → the per-client dedup suppresses the re-send")
        self._post("/rename", {"target": "web", "name": "tests"})
        self._cycle()
        self.assertGreater(len(self._tab_orders()), n,
                           "a name change defeats the dedup — tabs/views must never join "
                           "_DEDUP_VOLATILE")


if __name__ == "__main__":
    unittest.main()
