#!/usr/bin/env python3
"""Model selectors expose VERSIONS (the user 2026-08-25): shorthand aliases resolve to the newest —
opus became Opus 5 — silently losing legacy versions that remain live on the API. The kernel's
/models now carries each family's versions (dateless alias ids, verified against the claude-api
reference) plus a DEFAULT: the most recent version the user picked for that family (model-picks.json,
a viewer pref like colormap), else the newest. The pick memory hooks the ONE choke point every set
path flows through (_set_model_or_park). Synthetic only — hermetic temp STATE."""
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
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_mv", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd


class Catalog(unittest.TestCase):
    """The version catalog's own invariants."""

    def test_every_family_in_the_picker_has_a_version_list(self):
        for c in km.MODEL_CHOICES:
            self.assertIn(c["value"], km.MODEL_VERSIONS, c["value"])
            self.assertTrue(km.MODEL_VERSIONS[c["value"]], c["value"])

    def test_versions_are_dateless_aliases_newest_first(self):
        # ids verified against the claude-api reference (2026-08-25) — dateless aliases only,
        # and the head of each list is the family's newest (what the bare shorthand resolves to)
        for fam, vs in km.MODEL_VERSIONS.items():
            for v in vs:
                self.assertNotRegex(v["value"], r"-20\d{6}$", "dated snapshot id leaked in")
        self.assertEqual(km.MODEL_VERSIONS["opus"][0]["value"], "claude-opus-5")
        self.assertEqual(km.MODEL_VERSIONS["sonnet"][0]["value"], "claude-sonnet-5")
        self.assertIn("claude-opus-4-8", [v["value"] for v in km.MODEL_VERSIONS["opus"]],
                      "legacy versions live on the API stay pickable")

    def test_reverse_map_covers_every_version(self):
        for fam, vs in km.MODEL_VERSIONS.items():
            for v in vs:
                self.assertEqual(km._VERSION_FAMILY[v["value"]], fam)


class PickMemory(unittest.TestCase):
    """Per-family last-picked defaults: written at the choke point, read into /models."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)

    def tearDown(self):
        jd.STATE = self._state
        self.td.cleanup()

    def test_a_version_pick_becomes_the_family_default_and_persists(self):
        self.assertEqual(km._model_picks(), {})
        km._note_model_pick("claude-opus-4-8")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-8"})
        # the store is the file (a restart re-reads it) — pin the on-disk shape
        self.assertEqual(json.loads((jd.STATE / km.MODEL_PICKS_FILE_NAME).read_text()),
                         {"opus": "claude-opus-4-8"})

    def test_a_family_shorthand_never_downgrades_an_explicit_legacy_pick(self):
        # THE ALIAS RULE (the user's design): clicking a family sends the REMEMBERED version, and a
        # bare shorthand reaching the setter records nothing — so an explicit Opus 4.8 pick is never
        # silently replaced by "opus" resolving to the newest.
        km._note_model_pick("claude-opus-4-8")
        km._note_model_pick("opus")
        km._note_model_pick("default")
        km._note_model_pick("total-nonsense")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-8"})

    def test_the_choke_point_records_picks_from_every_surface(self):
        # _set_model_or_park is what the WS setModel op, the chat /model command, and POST /new's
        # prefs all call — one hook covers every surface.
        class _BE:
            def set_model(self, sid, value):
                return True
        km._set_model_or_park(_BE(), "11111111-2222-3333-4444-555555555555", "claude-sonnet-4-6")
        self.assertEqual(km._model_picks().get("sonnet"), "claude-sonnet-4-6")

    def test_a_stale_or_foreign_entry_falls_back_on_read(self):
        (jd.STATE / km.MODEL_PICKS_FILE_NAME).write_text(json.dumps(
            {"opus": "claude-opus-9-9", "sonnet": "claude-opus-4-8", "haiku": "claude-haiku-4-5"}))
        self.assertEqual(km._model_picks(), {"haiku": "claude-haiku-4-5"},
                         "unknown ids and cross-family entries never poison the default")


class ModelsRoute(unittest.TestCase):
    """GET /models carries versions + default per family."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)

    def tearDown(self):
        jd.STATE = self._state
        self.td.cleanup()

    def _models(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/models" % self.port,
            headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def test_each_family_carries_versions_and_a_default(self):
        d = self._models()
        rows = {m["value"]: m for m in d["models"]}
        self.assertEqual([v["value"] for v in rows["opus"]["versions"]],
                         [v["value"] for v in km.MODEL_VERSIONS["opus"]])
        self.assertEqual(rows["opus"]["default"], "claude-opus-5",
                         "no pick yet → the family's newest")
        self.assertIn("color", rows["opus"], "the colormap tint still rides every family row")

    def test_the_default_follows_the_users_last_pick(self):
        km._note_model_pick("claude-opus-4-8")
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["default"], "claude-opus-4-8")
        self.assertEqual(rows["sonnet"]["default"], "claude-sonnet-5", "other families unaffected")


if __name__ == "__main__":
    unittest.main()
