#!/usr/bin/env python3
"""T222 (the user 2026-09-01): romp stops needing a hand edit when Anthropic ships a model.

MODEL_VERSIONS is now the SEED of a live catalog: the kernel queries the Models API on its own
credential and merges new version ids into the families — ADD-ONLY (an id the API omits but the seed
knows stays; key-scoped visibility differs per account), newest-first by each id's own version tuple,
labels from display_name. The seed is also the LOUD fallback: an unreachable API or a credential-less
box serves seed+cache and says so (stderr + /version.modelCatalog), never a quietly stale list. The
refresh is EVENT-keyed — boot, plus the exact staleness event of a claude-* id reaching a set path or
the pick store that the merged list does not know — never a timer. A durable cache
(STATE/model-catalog.json) means a dead API never blanks a picker.

The Models API leg runs against a HERMETIC fake here (a local HTTP server; the kernel's fetch URL is
redirected), never the network: CI has no credential and must never make one. Synthetic fixtures
throughout (a made-up "claude-opus-9-9" stands in for a future release).
"""
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.request
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — bin/romp-kernel resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["ROMP_MODELS_URL"] = "http://127.0.0.1:9/v1/models"   # a dead port until a test points it at the fake
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_mc", os.path.join(BIN, "romp-kernel")).load_module()
sb = SourceFileLoader("romp_sdk_backend_mc", os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).load_module()
jd = km.jd

# The rows a live account might see (2026-09-01 shape): a genuinely new version, dated snapshots, a
# suffixed variant, the pre-4 naming — every kind the merge must classify. Synthetic future id included.
FAKE_ROWS = [
    {"id": "claude-opus-9-9", "display_name": "Claude Opus 9.9", "created_at": "2027-01-01T00:00:00Z"},
    {"id": "claude-fable-5-1", "display_name": "Claude Fable 5.1", "created_at": "2026-08-28T00:00:00Z"},
    {"id": "claude-opus-4-5-20251101", "display_name": "Claude Opus 4.5", "created_at": "2025-11-24T00:00:00Z"},
    {"id": "claude-opus-4-6-fast", "display_name": "Opus 4.6 Fast", "created_at": "2026-02-10T00:00:00Z"},
    {"id": "claude-3-5-sonnet-20241022", "display_name": "Claude Sonnet 3.5 (New)", "created_at": "2024-10-22T00:00:00Z"},
    {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5", "created_at": "2025-10-15T00:00:00Z"},
]


def _reset_catalog():
    """Back to the shipped seed — every test starts from what the build carries."""
    km._apply_model_catalog(km._MODEL_SEED, "seed")
    km._catalog_status.update({"fetchedAt": None, "lastError": None, "inflight": False})
    km._catalog_asked.clear()


class MergeRules(unittest.TestCase):
    """merge_model_catalog is pure — the rules, red-first."""

    def test_a_new_version_joins_its_family_newest_first_with_the_api_label(self):
        merged = km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS)
        self.assertEqual(merged["opus"][0], {"value": "claude-opus-9-9", "label": "Opus 9.9"})
        self.assertEqual([v["value"] for v in merged["opus"]][1:],
                         [v["value"] for v in km._MODEL_SEED["opus"]], "the seed order survives beneath it")

    def test_add_only_a_seed_id_the_api_omits_stays(self):
        # the live list (2026-09-01) carries claude-opus-4-5-20251101 but NOT the dateless
        # claude-opus-4-5 the seed lists — key-scoped visibility; the seed entry must never vanish
        merged = km.merge_model_catalog(km._MODEL_SEED, [r for r in FAKE_ROWS if r["id"] != "claude-opus-9-9"])
        self.assertIn("claude-opus-4-5", [v["value"] for v in merged["opus"]])
        for fam, vs in km._MODEL_SEED.items():
            for v in vs:
                self.assertIn(v["value"], [m["value"] for m in merged[fam]], "%s dropped" % v["value"])

    def test_routing_ids_never_join(self):
        merged = km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS)
        flat = [v["value"] for vs in merged.values() for v in vs]
        for bad in ("claude-opus-4-5-20251101", "claude-opus-4-6-fast", "claude-3-5-sonnet-20241022",
                    "claude-haiku-4-5-20251001"):
            self.assertNotIn(bad, flat, "%s is a deployment/routing id, not a pickable version" % bad)
        self.assertIsNone(km._catalog_family("claude-opus-4-5-20251101"))
        self.assertIsNone(km._catalog_family("claude-opus-4-6-fast"))
        self.assertIsNone(km._catalog_family("claude-3-5-sonnet-20241022"))
        self.assertIsNone(km._catalog_family("opus"))
        self.assertEqual(km._catalog_family("claude-fable-5-1"), "fable")

    def test_an_already_seeded_id_is_not_doubled(self):
        merged = km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS)
        self.assertEqual([v["value"] for v in merged["fable"]].count("claude-fable-5-1"), 1)
        self.assertEqual(merged["fable"][0]["label"], "Fable 5.1", "the seed's own label stands")

    def test_version_ordering_is_by_the_ids_own_tuple(self):
        self.assertGreater(km._version_key("claude-opus-5"), km._version_key("claude-opus-4-8"))
        self.assertGreater(km._version_key("claude-fable-5-1"), km._version_key("claude-fable-5"))
        self.assertGreater(km._version_key("claude-opus-4-10"), km._version_key("claude-opus-4-9"),
                           "numeric, not lexical")

    def test_labels_derive_from_display_name_or_the_id(self):
        self.assertEqual(km._catalog_label("Claude Sonnet 6", "claude-sonnet-6"), "Sonnet 6")
        self.assertEqual(km._catalog_label("", "claude-sonnet-6-2"), "Sonnet 6.2")
        self.assertEqual(km._catalog_label(None, "claude-haiku-5"), "Haiku 5")

    def test_merge_is_pure(self):
        before = json.dumps(km._MODEL_SEED, sort_keys=True)
        km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS)
        self.assertEqual(json.dumps(km._MODEL_SEED, sort_keys=True), before)


class ApplyInPlace(unittest.TestCase):
    """The running table is mutated in place, so every consumer sees the merge without re-import."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        _reset_catalog()

    def tearDown(self):
        _reset_catalog()
        jd.STATE = self._state
        self.td.cleanup()

    def test_every_consumer_sees_a_newly_applied_id(self):
        opus_list = km.MODEL_VERSIONS["opus"]           # a reference held before the apply
        self.assertNotIn("claude-opus-9-9", km._VERSION_FAMILY)
        self.assertNotIn("claude-opus-9-9", km._JUDGE_MODEL_VALUES)
        added = km._apply_model_catalog(km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS), "api")
        self.assertEqual(added, ["claude-opus-9-9"])
        self.assertIs(km.MODEL_VERSIONS["opus"], opus_list, "the list object is mutated, never rebound")
        self.assertEqual(opus_list[0]["value"], "claude-opus-9-9")
        self.assertEqual(km._VERSION_FAMILY["claude-opus-9-9"], "opus")
        self.assertIn("claude-opus-9-9", km._JUDGE_MODEL_VALUES, "the judge setters accept it now")
        self.assertEqual(km._catalog_status["added"], ["claude-opus-9-9"])
        self.assertEqual(km._catalog_status["source"], "api")

    def test_a_pick_unknown_before_the_refresh_is_honored_after_it(self):
        # pick-migration, the forward direction: the user's pin names a model this build never heard
        # of — dropped as foreign today; honored the moment the catalog learns the id
        (jd.STATE / km.MODEL_PICKS_FILE_NAME).write_text(json.dumps({"opus": "claude-opus-9-9"}))
        self.assertEqual(km._model_picks(), {}, "unknown before the refresh")
        km._apply_model_catalog(km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS), "api")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-9-9"})

    def test_a_legacy_fable_pin_stays_valid_and_is_moved_by_a_new_pick(self):
        # pick-migration, the other direction (the switch sweep's contract): claude-fable-5 remains a
        # KNOWN version after 5.1 joins, so a lingering pin is honored, not auto-dropped — the sweep
        # must actively re-pick 5.1 for family-newest to win; and one pick of 5.1 moves the pin
        (jd.STATE / km.MODEL_PICKS_FILE_NAME).write_text(json.dumps({"fable": "claude-fable-5"}))
        self.assertEqual(km._model_picks(), {"fable": "claude-fable-5"})
        km._note_model_pick("claude-fable-5-1")
        self.assertEqual(km._model_picks(), {"fable": "claude-fable-5-1"})
        self.assertEqual(km.MODEL_VERSIONS["fable"][0]["value"], "claude-fable-5-1",
                         "no pin at all → family-newest is 5.1")


class _FakeModelsAPI(BaseHTTPRequestHandler):
    rows = list(FAKE_ROWS)
    status = 200
    seen = []          # (path, x-api-key present, anthropic-version) per request
    page_size = 100

    def do_GET(self):
        type(self).seen.append((self.path, bool(self.headers.get("x-api-key")),
                                self.headers.get("anthropic-version")))
        if self.status != 200:
            self.send_response(self.status); self.end_headers(); self.wfile.write(b'{"type":"error"}')
            return
        if not self.headers.get("x-api-key"):
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"type":"error"}')
            return
        rows = list(type(self).rows)
        after = None
        if "after_id=" in self.path:
            after = urllib.parse.unquote(self.path.split("after_id=", 1)[1].split("&")[0])
            ids = [r["id"] for r in rows]
            rows = rows[ids.index(after) + 1:] if after in ids else []
        page = rows[:self.page_size]
        body = {"data": page, "has_more": len(rows) > len(page),
                "first_id": page[0]["id"] if page else None, "last_id": page[-1]["id"] if page else None}
        out = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


class FetchAndFallback(unittest.TestCase):
    """The Models API leg against a hermetic fake — success caches and merges; every failure is loud."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelsAPI)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        self._url, self._fn = km.MODELS_API_URL, getattr(jd, "_WORK_KEY_FN", None)
        self._login_fn = jd._LOGIN_AUTH_ENV_FN
        jd._LOGIN_AUTH_ENV_FN = None
        self._source_env = {k: os.environ.get(k) for k in
                            ("ROMP_SERVICE_ENV_FILE", "ROMP_API_KEY_REF", "ANTHROPIC_AUTH_TOKEN")}
        os.environ["ROMP_SERVICE_ENV_FILE"] = str(Path(self.td.name) / "service.env")
        os.environ.pop("ROMP_API_KEY_REF", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        self._env_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "synthetic-test-credential"   # the fake sees only this (never a key-shaped string: the pre-commit scanner)
        jd._WORK_KEY_FN = None
        km.MODELS_API_URL = "http://127.0.0.1:%d/v1/models" % self.port
        _FakeModelsAPI.rows, _FakeModelsAPI.status, _FakeModelsAPI.page_size = list(FAKE_ROWS), 200, 100
        _FakeModelsAPI.seen = []
        os.environ.pop("ROMP_MODEL_CATALOG", None)
        _reset_catalog()

    def tearDown(self):
        _reset_catalog()
        km.MODELS_API_URL = self._url
        jd._WORK_KEY_FN = self._fn
        jd._LOGIN_AUTH_ENV_FN = self._login_fn
        for key, value in self._source_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self._env_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._env_key
        jd.STATE = self._state
        self.td.cleanup()

    def _clients(self):
        """Fake dashboard clients on the kernel's client list — one per app that hosts a picker — so the
        models frame's fan-out can be asserted (the test_model_versions idiom)."""
        got = []
        fakes = [{"app": app, "wid": "w-" + app, "alive": True,
                  "send": (lambda s, a=app: got.append((a, json.loads(s))))} for app in ("chat", "timeline", "feed")]
        with km._clients_lock:
            km._clients.extend(fakes)

        def drop():
            with km._clients_lock:
                km._clients[:] = [c for c in km._clients if c not in fakes]
        self.addCleanup(drop)
        return got

    def _refresh(self, reason="test"):
        err = io.StringIO()
        with redirect_stderr(err):
            started = km._refresh_model_catalog(reason, _async=False)
        return started, err.getvalue()

    def test_success_merges_caches_and_announces_the_additions(self):
        started, log = self._refresh()
        self.assertTrue(started)
        self.assertEqual(km.MODEL_VERSIONS["opus"][0]["value"], "claude-opus-9-9")
        self.assertEqual(km._catalog_status["source"], "api")
        self.assertIsNone(km._catalog_status["lastError"])
        self.assertIsInstance(km._catalog_status["fetchedAt"], int)
        self.assertIn("claude-opus-9-9", log, "the additions are announced, not silent")
        cache = json.loads((jd.STATE / km.MODEL_CATALOG_FILE_NAME).read_text())
        self.assertEqual([r["id"] for r in cache["models"]], [r["id"] for r in FAKE_ROWS])
        path, keyed, ver = _FakeModelsAPI.seen[0]
        self.assertTrue(keyed, "the kernel's own credential rides the request")
        self.assertEqual(ver, "2023-06-01")
        self.assertIn("limit=100", path)

    def test_paging_follows_has_more(self):
        _FakeModelsAPI.page_size = 2
        started, _ = self._refresh()
        self.assertTrue(started)
        self.assertEqual(len(_FakeModelsAPI.seen), 3, "6 rows / 2 per page = 3 requests")
        self.assertTrue(any("after_id=" in p for p, _, _ in _FakeModelsAPI.seen))
        self.assertEqual(km.MODEL_VERSIONS["opus"][0]["value"], "claude-opus-9-9")

    def test_a_server_error_is_loud_and_leaves_the_seed_standing(self):
        _FakeModelsAPI.status = 500
        started, log = self._refresh("boot")
        self.assertTrue(started)
        self.assertEqual(km.MODEL_VERSIONS["opus"][0]["value"], "claude-opus-5", "seed intact")
        self.assertEqual(km._catalog_status["source"], "seed")
        self.assertIn("HTTPError", km._catalog_status["lastError"])
        self.assertIn("Models API unreachable", log)
        self.assertIn("serving the seed list", log)
        self.assertFalse((jd.STATE / km.MODEL_CATALOG_FILE_NAME).exists(), "no cache is written from a failure")

    def test_a_dead_endpoint_is_loud_too(self):
        km.MODELS_API_URL = "http://127.0.0.1:%d/v1/models" % _free_port()
        started, log = self._refresh()
        self.assertTrue(started)
        self.assertIn("URLError", km._catalog_status["lastError"])
        self.assertIn("Models API unreachable", log)

    def test_no_credential_says_so_and_serves_the_seed(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        started, log = self._refresh("boot")
        self.assertTrue(started)
        self.assertEqual(_FakeModelsAPI.seen, [], "no request without a credential")
        self.assertIn("no API credential", km._catalog_status["lastError"])
        self.assertIn("no API credential the kernel can use", log)
        self.assertEqual(km.MODEL_VERSIONS["fable"][0]["value"], "claude-fable-5-1", "seed still serves")

    def test_provider_failure_never_falls_back_to_an_ambient_key_or_token(self):
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "synthetic-ambient-token"
        jd._WORK_KEY_FN = Mock(side_effect=jd._keysrc.KeySourceError("1Password retrieval failed"))
        started, log = self._refresh()
        self.assertTrue(started)
        jd._WORK_KEY_FN.assert_called_once_with()
        self.assertEqual(_FakeModelsAPI.seen, [])
        self.assertIn("1Password retrieval failed", km._catalog_status["lastError"])
        self.assertNotIn("synthetic-ambient-token", log)
        self.assertNotIn("synthetic-test-credential", log)
        self.assertFalse(km._catalog_status["inflight"])

    def test_runtime_reference_is_resolved_once_per_catalog_refresh_without_persistence(self):
        ref = "op://test-vault/test-item/api-key"
        os.environ["ROMP_API_KEY_REF"] = ref
        with patch.object(jd._keysrc.subprocess, "run",
                          return_value=Mock(returncode=0, stdout=b"synthetic-runtime-key")) as run:
            self._refresh()
            self.assertEqual(run.call_count, 1)
            self._refresh()
            self.assertEqual(run.call_count, 2)
        self.assertTrue(_FakeModelsAPI.seen)
        for file in Path(self.td.name).rglob("*"):
            if file.is_file():
                self.assertNotIn(b"synthetic-runtime-key", file.read_bytes())

    def test_invalid_reference_prevents_requests_and_does_not_use_legacy_key(self):
        Path(os.environ["ROMP_SERVICE_ENV_FILE"]).write_text("ROMP_API_KEY_REF=\n")
        with patch.object(jd._keysrc.subprocess, "run") as run:
            started, _ = self._refresh()
        self.assertTrue(started)
        run.assert_not_called()
        self.assertEqual(_FakeModelsAPI.seen, [])
        self.assertIn("ROMP_API_KEY_REF", km._catalog_status["lastError"])

    def test_a_fetch_that_adds_ids_tells_every_open_picker_to_re_read_models(self):
        # the refresh used to call _push_soon() here, its comment claiming the pickers re-read /models
        # on the next frame — but nothing re-reads the choice lists after page load, so an open
        # dashboard kept the page-load list until a reload. The event the pickers DO act on is the
        # models frame (chat/comment loadModelChoices, the timeline's refreshModels, the gear's
        # adoptChoices/paintChoices): the fetch that grows the list emits it, with the rev the payload carries.
        got = self._clients()
        rev0 = km._models_rev[0]
        started, _ = self._refresh("boot")
        self.assertTrue(started)
        frames = [(a, f) for a, f in got if f.get("type") == "models"]
        self.assertEqual(sorted(a for a, _ in frames), ["chat", "feed", "timeline"],
                         "one frame to every app that hosts a picker")
        self.assertTrue(all(f["rev"] > rev0 for _, f in frames), "the frame carries a rev newer than before")
        self.assertEqual({f["rev"] for _, f in frames}, {km._models_rev[0]}, "the /models payload's counter")
        got.clear()
        self._refresh("boot")                    # the same rows again: nothing joined, so nothing rings
        self.assertEqual([f for _, f in got if f.get("type") == "models"], [], "a fetch that adds nothing is silent")

    def test_the_cache_serves_when_the_api_later_dies(self):
        # a dead API never blanks a picker: the previous fetch's rows install at boot from the cache
        self._refresh()
        _reset_catalog()
        self.assertNotIn("claude-opus-9-9", km._VERSION_FAMILY)
        n = km._load_model_catalog_cache()
        self.assertEqual(n, len(FAKE_ROWS))
        self.assertIn("claude-opus-9-9", km._VERSION_FAMILY)
        self.assertEqual(km._catalog_status["source"], "cache")
        _FakeModelsAPI.status = 500
        started, log = self._refresh("boot")
        self.assertIn("claude-opus-9-9", km._VERSION_FAMILY, "the failure never rolls the cache back")
        self.assertIn("serving the cache list (1 extra id(s) beyond the seed)", log)

    def test_off_switch_never_fetches(self):
        os.environ["ROMP_MODEL_CATALOG"] = "off"
        try:
            started, log = self._refresh()
        finally:
            os.environ.pop("ROMP_MODEL_CATALOG", None)
        self.assertFalse(started)
        self.assertEqual(_FakeModelsAPI.seen, [])
        self.assertEqual(log, "")

    def test_version_route_reports_the_catalog(self):
        self._refresh()
        v = km._version_info()["modelCatalog"]
        self.assertEqual(v["source"], "api")
        self.assertEqual(v["added"], ["claude-opus-9-9"])
        self.assertIsNone(v["lastError"])


class StalenessEvent(unittest.TestCase):
    """The refresh fires on the exact event — an unknown claude-* id — once per id, never on a clock."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        os.environ["ROMP_MODEL_CATALOG"] = "off"   # the event's FIRING is what these test, not the fetch
        _reset_catalog()
        self.fired = []
        self._orig = km._refresh_model_catalog
        km._refresh_model_catalog = lambda reason, _async=True: (self.fired.append(reason), True)[1]

    def tearDown(self):
        km._refresh_model_catalog = self._orig
        os.environ.pop("ROMP_MODEL_CATALOG", None)
        _reset_catalog()
        jd.STATE = self._state
        self.td.cleanup()

    def test_an_unknown_version_id_fires_exactly_once(self):
        self.assertTrue(km._note_unknown_model("claude-opus-9-9"))
        self.assertFalse(km._note_unknown_model("claude-opus-9-9"), "dedup by id — no second refresh")
        self.assertEqual(self.fired, ["unknown model id claude-opus-9-9"])

    def test_known_ids_aliases_snapshots_and_garbage_never_fire(self):
        for v in ("claude-opus-5", "claude-fable-5-1", "opus", "default", "claude-opus-4-5-20251101",
                  "claude-opus-4-6-fast", "total-nonsense", "", None):
            self.assertFalse(km._note_unknown_model(v), repr(v))
        self.assertEqual(self.fired, [])

    def test_the_pick_store_and_the_set_path_are_wired(self):
        (jd.STATE / km.MODEL_PICKS_FILE_NAME).write_text(json.dumps({"opus": "claude-opus-9-9"}))
        km._model_picks()
        self.assertEqual(self.fired, ["unknown model id claude-opus-9-9"])
        km._note_model_pick("claude-sonnet-7")          # the choke point every set surface flows through
        self.assertEqual(self.fired[-1], "unknown model id claude-sonnet-7")

    def test_a_reported_id_the_catalog_lacks_fires_the_refresh(self):
        # a running session's CLI reporting an id the catalog does not list is exactly the staleness
        # the event names: the id joins the pickers marked `learned` at once AND the catalog is asked
        # to catch up, once per id — after which the mark drops
        _reg(jd.STATE, "11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-5-1")
        learned = km._learned_versions()
        self.assertEqual([v["value"] for v in learned["opus"]], ["claude-opus-5-1"])
        self.assertEqual(self.fired, ["unknown model id claude-opus-5-1"])
        km._learned_versions()
        self.assertEqual(len(self.fired), 1, "once per id per kernel life — every /models read re-derives the list")

    def test_a_sighting_during_an_inflight_refresh_is_not_spent(self):
        # the refresh is single-flight: an id first sighted WHILE one is inflight (the boot fetch —
        # exactly when a running session's CLI first reports a new release) gets no refresh started
        # for it. Its one refresh per kernel life must not be spent on that no-op: if the inflight
        # fetch lands without the id (a failed fetch, a key that cannot see it), the next sighting is
        # the id's own refresh. The REAL gate first, then the landing.
        km._refresh_model_catalog = self._orig
        os.environ.pop("ROMP_MODEL_CATALOG", None)
        km._catalog_status["inflight"] = True
        try:
            self.assertFalse(km._note_unknown_model("claude-opus-9-9"), "inflight → nothing started")
        finally:
            km._catalog_status["inflight"] = False
            os.environ["ROMP_MODEL_CATALOG"] = "off"
        self.assertNotIn("claude-opus-9-9", km._catalog_asked, "nothing started, so nothing spent")
        km._refresh_model_catalog = lambda reason, _async=True: (self.fired.append(reason), True)[1]
        self.assertTrue(km._note_unknown_model("claude-opus-9-9"), "the next sighting asks for real")
        self.assertEqual(self.fired, ["unknown model id claude-opus-9-9"])
        self.assertFalse(km._note_unknown_model("claude-opus-9-9"), "…and THAT was the id's one refresh")


def _reg(state, sid, **fields):
    """A synthetic session reg under STATE/sdk — what _learned_versions reads liveModelId from."""
    d = state / "sdk"
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".json")).write_text(json.dumps({"sid": sid, "name": "web", "cwd": "/tmp", "alive": True, **fields}))


class CliBlocks(unittest.TestCase):
    """A version the installed CLI refuses by minimum version says so on its /models row (T222)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        _reset_catalog()

    def tearDown(self):
        _reset_catalog()
        jd.STATE = self._state
        self.td.cleanup()

    REFUSAL = ("API Error: 400 Claude Code 2.1.221 does not support this model; version 2.1.251 or "
               "newer is required. Run 'claude update', or update the Claude desktop app, then try again.")

    def test_the_backend_records_the_refusal_and_a_real_reply_clears_it(self):
        self.assertTrue(sb.note_cli_model_block(jd.STATE, "claude-fable-5-1", self.REFUSAL))
        d = json.loads((jd.STATE / sb.CLI_MODEL_BLOCKS_FILE).read_text())
        self.assertEqual(d["claude-fable-5-1"]["needs"], "2.1.251")
        self.assertEqual(d["claude-fable-5-1"]["cli"], "2.1.221")
        self.assertFalse(sb.note_cli_model_block(jd.STATE, "claude-fable-5-1", "API Error: 529 Overloaded"),
                         "only the version refusal records — never another error")
        self.assertFalse(sb.note_cli_model_block(jd.STATE, "opus", self.REFUSAL), "aliases are not version rows")
        self.assertTrue(sb.clear_cli_model_block(jd.STATE, "claude-fable-5-1"))
        self.assertFalse(sb.clear_cli_model_block(jd.STATE, "claude-fable-5-1"))
        self.assertEqual(json.loads((jd.STATE / sb.CLI_MODEL_BLOCKS_FILE).read_text()), {})

    def test_the_models_row_wears_the_block_until_it_lifts(self):
        sb.note_cli_model_block(jd.STATE, "claude-fable-5-1", self.REFUSAL)
        row = km._with_cli_block({"value": "claude-fable-5-1", "label": "Fable 5.1"}, km._cli_model_blocks())
        self.assertEqual(row["cliNeeds"], "2.1.251")
        self.assertEqual(row["label"], "Fable 5.1 — needs CLI ≥ 2.1.251")
        plain = km._with_cli_block({"value": "claude-fable-5", "label": "Fable 5"}, km._cli_model_blocks())
        self.assertNotIn("cliNeeds", plain)
        self.assertEqual(plain["label"], "Fable 5")
        sb.clear_cli_model_block(jd.STATE, "claude-fable-5-1")
        lifted = km._with_cli_block({"value": "claude-fable-5-1", "label": "Fable 5.1"}, km._cli_model_blocks())
        self.assertEqual(lifted["label"], "Fable 5.1")

    def test_the_stream_hook_is_wired_at_the_error_settle(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        self.assertIn("note_cli_model_block(self.backend.state_dir, self.chosen_model or self.model,", src)
        self.assertIn("clear_cli_model_block(self.backend.state_dir, _mid)", src)


class ModelsRoute(unittest.TestCase):
    """GET /models serves the MERGED list plus any CLI block — every picker reads this one route. The
    catalog owns the version LIST; the alias owns the DEFAULT."""

    SID = "11111111-2222-3333-4444-555555555555"

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
        os.environ["ROMP_MODEL_CATALOG"] = "off"
        _reset_catalog()

    def tearDown(self):
        _reset_catalog()
        os.environ.pop("ROMP_MODEL_CATALOG", None)
        jd.STATE = self._state
        self.td.cleanup()

    def _models(self):
        req = urllib.request.Request("http://127.0.0.1:%d/models" % self.port,
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def test_the_route_serves_the_merged_versions_and_the_block(self):
        km._apply_model_catalog(km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS), "api")
        sb.note_cli_model_block(jd.STATE, "claude-opus-9-9", CliBlocks.REFUSAL)
        rows = {m["value"]: m for m in self._models()["models"]}
        opus = rows["opus"]["versions"]
        self.assertEqual(opus[0]["value"], "claude-opus-9-9")
        self.assertEqual(opus[0]["cliNeeds"], "2.1.251")
        self.assertIn("needs CLI ≥ 2.1.251", opus[0]["label"])
        self.assertEqual(rows["fable"]["versions"][0], {"value": "claude-fable-5-1", "label": "Fable 5.1"})
        self.assertEqual(rows["fable"]["default"], "fable",
                         "no pin → the family ALIAS, which the CLI resolves to its newest live — never the list's head")

    def test_a_family_default_is_the_alias_even_when_the_catalog_knows_a_newer_head(self):
        # the catalog may lead a family with an id newer than the seed knew, and a bare family click
        # STILL sends the alias — a pinned head was the bug (every picker-set session stayed on
        # claude-fable-5 while `fable` moved on)
        km._apply_model_catalog(km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS), "api")
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["versions"][0]["value"], "claude-opus-9-9", "the catalog's head leads the list")
        for fam in ("fable", "opus", "sonnet", "haiku"):
            self.assertEqual(rows[fam]["default"], fam, "%s: no pick → the alias" % fam)

    def test_versions_are_the_catalog_union_the_learned_ids_deduped_by_id(self):
        km._apply_model_catalog(km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS), "api")
        _reg(jd.STATE, "11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-9-9")   # the catalog knows it
        _reg(jd.STATE, "11111111-2222-3333-4444-555555555502", liveModelId="claude-opus-5-1")   # the catalog lacks it
        vs = {m["value"]: m for m in self._models()["models"]}["opus"]["versions"]
        self.assertEqual([v["value"] for v in vs],
                         ["claude-opus-9-9", "claude-opus-5-1", "claude-opus-5", "claude-opus-4-8",
                          "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5"],
                         "one list, newest first, the learned id slotted by its own version tuple")
        self.assertFalse(vs[0].get("learned"), "a reported id the catalog lists is the catalog's row — not doubled, not marked")
        self.assertTrue(vs[1].get("learned"), "a reported id the catalog lacks joins, marked")
        # the catalog catches up (the refresh the sighting fired lands): the mark drops, still one row
        km._apply_model_catalog(km.merge_model_catalog(
            km._MODEL_SEED, FAKE_ROWS + [{"id": "claude-opus-5-1", "display_name": "Claude Opus 5.1"}]), "api")
        vs = {m["value"]: m for m in self._models()["models"]}["opus"]["versions"]
        rows51 = [v for v in vs if v["value"] == "claude-opus-5-1"]
        self.assertEqual(len(rows51), 1)
        self.assertFalse(rows51[0].get("learned"), "the catalog owns the row now")
        self.assertEqual(rows51[0]["label"], "Opus 5.1")

    def test_a_catalog_id_is_pickable_as_a_pin_and_a_refusal_forgets_it(self):
        # an id only the catalog knows (no seed row, no session reporting it) is a version the pick
        # memory may record — read at call time, so it counts from the moment the catalog learned it —
        # and the CLI refusing it forgets the pin like any other (the on_model_refused hook)
        km._apply_model_catalog(km.merge_model_catalog(km._MODEL_SEED, FAKE_ROWS), "api")
        self.assertTrue(km._vouched_model("claude-opus-9-9"), "the composer's /model vouches for a catalog id")
        self.assertEqual(km._version_family("claude-opus-9-9"), "opus")
        km._note_model_pick("claude-opus-9-9")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-9-9"})
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["default"], "claude-opus-9-9", "the family row sends the pin")
        self.assertEqual(rows["sonnet"]["default"], "sonnet", "other families: still the alias")
        km._model_pick_refused(self.SID, "claude-opus-9-9")       # the backend's hook, on the CLI's error
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["default"], "opus", "forgotten → the alias again")
        self.assertEqual(km._model_picks(), {})


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


if __name__ == "__main__":
    unittest.main()
