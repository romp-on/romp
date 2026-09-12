#!/usr/bin/env python3
"""POST /rename (the user 2026-08-23): the renameSession WS op as a one-shot token-gated route, the
exact sibling of /fork — agents rename sessions without WS surgery. Sessions are uuid-keyed with the
name as a label, so nothing breaks; the by-name poisoning guard mirrors /fork's. Drives the REAL
Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class RenameRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.renames = []
        renames = self.renames

        class BE:
            def rename(self, sid, name):
                renames.append((sid, name))
                return True
        self._saved = (km.Sessions.backend_for, km._live_map, km._live_names, km._mark_views_dirty)
        km.Sessions.backend_for = staticmethod(lambda sid: BE())
        km._live_map = lambda: {}
        km._live_names = lambda tm: {"web": SID}
        km._mark_views_dirty = lambda: None

    def tearDown(self):
        (km.Sessions.backend_for, km._live_map, km._live_names, km._mark_views_dirty) = self._saved

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/rename" % self.port, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_live_name_renames_through_the_backend(self):
        st, r = self._post({"target": "web", "name": "cross_model"})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("id"), SID)
        self.assertEqual(self.renames, [(SID, "cross_model")],
                         "routed through be.rename so every surface resyncs")

    def test_a_sid_target_reaches_the_backend_directly(self):
        st, r = self._post({"target": SID, "name": "intuition_building"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.renames[0][0], SID)

    def test_the_poisoning_guard_refuses_a_live_new_name(self):
        st, r = self._post({"target": SID, "name": "web"})
        self.assertFalse(r.get("ok"))
        self.assertIn("already running", r.get("error") or "")
        self.assertEqual(self.renames, [])

    def test_bad_names_and_unknown_targets_are_loud(self):
        st, r = self._post({"target": "web", "name": "bad name!"})
        self.assertIn("letters, digits", r.get("error") or "")
        st, r = self._post({"target": "nope", "name": "fine-name"})
        self.assertIn("no live session named", r.get("error") or "")
        st, r = self._post({"target": "web"})
        self.assertEqual(st, 400)


class NonObjectBodies(unittest.TestCase):
    """Every session-management route takes a JSON OBJECT. A body that decodes to anything else -- an
    array, a string, a number, null -- used to reach `(b or {}).get(...)` (a truthy non-dict passes the
    `or`) and raise AttributeError into do_POST's catch-all: a 500 whose body was a Python traceback
    naming absolute paths. Now every one of them answers 400 in the route family's JSON shape, naming
    what arrived, and acts on nothing."""
    ROUTES = ("/new", "/fork", "/rename", "/move", "/color", "/watch-pr", "/watch", "/tag", "/group",
              "/update-dismiss", "/working", "/deliver", "/walk-root", "/redial", "/logins")

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post_raw(self, path, raw):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=raw,
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_a_non_object_body_is_a_400_naming_what_arrived_never_a_500(self):
        for path in self.ROUTES:
            for raw, echo in ((b"[]", "[]"), (b'"x"', '"x"'), (b"1", "1"), (b"null", "null"), (b"[1]", "[1]")):
                st, body = self._post_raw(path, raw)
                self.assertEqual(st, 400, "%s %r -> %s %s" % (path, raw, st, body[:160]))
                self.assertNotIn("Traceback", body)
                r = json.loads(body)
                self.assertIs(r.get("ok"), False, (path, raw))
                self.assertEqual(r.get("error"), "body must be a JSON object, got " + echo, (path, raw))

    def test_an_undecodable_body_is_a_400_too_not_a_guess_at_its_fields(self):
        for path in self.ROUTES:
            st, body = self._post_raw(path, b"{not json")
            self.assertEqual(st, 400, (path, st, body[:160]))
            self.assertEqual(json.loads(body).get("error"), "body is not JSON", path)

    def test_a_long_string_body_echoes_clipped_inside_its_quotes(self):
        # a 100 KB string must not come back as a 100 KB error, and the cut must land INSIDE the quotes
        # with a marker -- a slice of the serialized text took the closing quote with it
        st, body = self._post_raw("/rename", json.dumps("x" * 100_000).encode())
        self.assertEqual(st, 400)
        err = json.loads(body)["error"]
        self.assertEqual(err, 'body must be a JSON object, got "' + "x" * 60 + '\u2026"')
        self.assertLess(len(err), 100)

    def test_a_container_holding_a_long_string_echoes_well_formed_at_any_depth(self):
        # review find, 2026-09-08: the echo was well formed only for a top-level string; a container
        # holding a long one was sliced as serialized text and came back with its quote and bracket
        # open. Every string is now cut inside its quotes, a container at the element level, and the
        # nesting is bounded, so the echo parses whatever arrived
        cases = (
            (["x" * 100_000], ["x" * 60 + "\u2026"]),
            ([{"a": ["y" * 5000]}], [{"a": ["y" * 60 + "\u2026"]}]),
            (list(range(1000)), [0, 1, 2, 3, 4, 5, 6, 7, "\u2026"]),
            ([[[[[[1]]]]]], [[[[["\u2026"]]]]]),
            ([{"k%d" % i: "v" * 200 for i in range(50)}], None),
            (int("7" * 4000), "7" * 60 + "\u2026"),          # a number past the bound: a marked string,
        )                                                    # since a cut decimal is a mid-token cut
        for raw, want in cases:
            st, body = self._post_raw("/rename", json.dumps(raw).encode())
            self.assertEqual(st, 400, (str(raw)[:80], body[:160]))
            err = json.loads(body)["error"]
            self.assertTrue(err.startswith("body must be a JSON object, got "), err)
            echo = err[len("body must be a JSON object, got "):]
            self.assertLess(len(echo), 300, "bounded whatever the size of what arrived")
            parsed = json.loads(echo)                        # well formed: the whole point
            if want is not None:
                self.assertEqual(parsed, want, (str(raw)[:80], echo))
        st, body = self._post_raw("/rename", json.dumps([{"a": ["y" * 5000]}]).encode())
        self.assertEqual(json.loads(body)["error"], 'body must be a JSON object, got [{"a": ["' + "y" * 60 + '\u2026"]}]')


if __name__ == "__main__":
    unittest.main()
