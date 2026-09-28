#!/usr/bin/env python3
"""Model selectors expose VERSIONS (the user 2026-08-25): shorthand aliases resolve to the newest —
opus became Opus 5 — silently losing legacy versions that remain live on the API. The kernel's
/models now carries each family's versions (dateless alias ids, verified against the claude-api
reference) plus a DEFAULT: the most recent version the user picked for that family (model-picks.json,
a viewer pref like colormap), else the family ALIAS itself (the seed table's head used to stand in
for the alias, so a bare family click pinned every session to claude-fable-5 while the CLI's own
`fable` alias moved on to Fable 5.1). The pick memory hooks the ONE choke point every set path flows
through (_set_model_or_park). The seed table is a SEED: ids a running session's CLI reports
(reg.liveModelId) join the version lists, marked `learned`, until the catalog fetch folds them in.
Synthetic only — hermetic temp STATE."""
import contextlib
import inspect
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_mv", os.path.join(BIN, "romp-kernel"))
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
        # T222 (the user 2026-09-01): Fable 5.1 heads the fable family — verified live against the
        # Models API (created 2026-08-28) and the installed CLI (2.1.257) before it was seeded
        self.assertEqual(km.MODEL_VERSIONS["fable"][0], {"value": "claude-fable-5-1", "label": "Fable 5.1"})
        self.assertIn("claude-fable-5", [v["value"] for v in km.MODEL_VERSIONS["fable"]],
                      "the legacy fable stays pickable (add-only, never a silent drop)")
        self.assertIn("claude-opus-4-8", [v["value"] for v in km.MODEL_VERSIONS["opus"]],
                      "legacy versions live on the API stay pickable")

    def test_reverse_map_covers_every_version(self):
        for fam, vs in km.MODEL_VERSIONS.items():
            for v in vs:
                self.assertEqual(km._VERSION_FAMILY[v["value"]], fam)

    def test_id_helpers_tolerate_anything(self):
        self.assertEqual(km._model_id_parts("claude-fable-5-1[1m]"), ("fable", 5, 1))
        self.assertEqual(km._model_id_parts("claude-opus-4-5-20251101"), ("opus", 4, 5))
        self.assertIsNone(km._model_id_parts("us.anthropic.claude-opus-4-8-v1:0"))
        self.assertIsNone(km._model_id_parts("opus"))
        self.assertEqual(km._model_id_label("claude-fable-5-1"), "Fable 5.1")
        self.assertEqual(km._model_id_label("<synthetic>"), "", "a module-level helper never raises on junk")


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

    def test_the_floating_gesture_clears_the_familys_pin_and_sends_the_alias(self):
        # once a family carried a pin there was NO picker gesture back to floating: the family row
        # sends the pin, the submenu lists only explicit ids, and a typed "/model opus" leaves the
        # pick memory alone by design. The submenu's "Latest" row is that gesture — an explicit user
        # act, so it may move state — carried as `floating` on the op.
        class _BE:
            calls = []

            def set_model(self, sid, value):
                self.calls.append(value)
                return True
        be = _BE()
        sid = "11111111-2222-3333-4444-555555555555"
        km._set_model_or_park(be, sid, "claude-opus-4-8")
        km._set_model_or_park(be, sid, "claude-sonnet-4-6")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-8", "sonnet": "claude-sonnet-4-6"})
        km._set_model_or_park(be, sid, "opus", floating=True)
        self.assertEqual(be.calls[-1], "opus", "the alias rides to the backend verbatim — resolved at the CLI hand-off (SdkSession._cli_model)")
        self.assertEqual(km._model_picks(), {"sonnet": "claude-sonnet-4-6"}, "only THAT family's pin is forgotten")
        # the flag means nothing on a non-alias value: a version id with it pins as usual, and a
        # floating alias with no pin to clear is a plain alias send
        km._set_model_or_park(be, sid, "claude-opus-4-8", floating=True)
        self.assertEqual(km._model_picks().get("opus"), "claude-opus-4-8")
        km._set_model_or_park(be, sid, "haiku", floating=True)
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-8", "sonnet": "claude-sonnet-4-6"})
        # and without the flag the alias still never touches the memory (the standing design)
        km._set_model_or_park(be, sid, "opus")
        self.assertEqual(km._model_picks().get("opus"), "claude-opus-4-8")

    def _clients(self):
        """Four fake connected clients — a chat, a timeline, a FEED client (VS Code's feed panel hosts the
        settings gear) and a SETTINGS client (the dashboard's /settings page, where its pickers live) — on
        the kernel's live roster; the frames they receive, decoded. Removed again in tearDown-order by the
        returned callable."""
        got = {"chat": [], "timeline": [], "feed": [], "settings": []}
        fakes = [{"app": app, "wid": "w1", "alive": True,
                  "send": (lambda s, _a=app: got[_a].append(json.loads(s)))} for app in got]
        with km._clients_lock:
            km._clients.extend(fakes)

        def drop():
            with km._clients_lock:
                km._clients[:] = [c for c in km._clients if c not in fakes]
        self.addCleanup(drop)
        return got

    def test_a_pick_or_a_forget_tells_every_open_picker_to_re_read_models(self):
        # both webviews read a family's `default` from a /models list fetched ONCE at page load and
        # never refreshed, and nothing mutated it after a pick. So after Latest un-pinned a family on
        # the kernel, the same tab's next family click still sent the stale pinned id and silently
        # RE-PINNED; another dashboard's pick moved the default without this tab knowing. The kernel
        # emits a models frame whenever the pick memory CHANGES — event-keyed, never a poll — and
        # every picker re-fetches on it.
        got = self._clients()
        km._note_model_pick("claude-opus-4-8")
        for app in ("chat", "timeline", "feed", "settings"):
            self.assertEqual([f["type"] for f in got[app]], ["models"], app)
            self.assertIsInstance(got[app][0]["rev"], int)
        rev = got["chat"][0]["rev"]
        km._note_model_pick("claude-opus-4-8")            # write-on-change: nothing moved, nothing said
        km._note_model_pick("opus")                        # an alias records nothing
        km._forget_model_pick("sonnet")                    # no pin to forget
        self.assertEqual(len(got["chat"]), 1)
        km._forget_model_pick("opus")                      # the Latest gesture
        self.assertEqual([f["type"] for f in got["chat"]], ["models", "models"])
        self.assertEqual(got["chat"][1]["rev"], rev + 1, "a moving counter — a client can tell frames apart")
        self.assertEqual(len(got["timeline"]), 2)
        self.assertEqual(len(got["feed"]), 2)
        self.assertEqual(len(got["settings"]), 2)

    def test_the_frame_reaches_every_document_that_hosts_the_settings_gear(self):
        # the settings gear, whose judge-tier family rows send the cached list's `default`, lives on the
        # dashboard's own /settings page (app settings, ui/webview/settings-page.ts; the user 2026-09-10)
        # and, for VS Code's feed panel, still in the FEED bundle (feed.ts requires gear.js; VS Code's
        # settings command posts openSettings into that webview). A frame to the chat and timeline apps
        # alone never reaches the gear where it actually opens, and its first-open cache keeps sending
        # a stale pinned default after another picker un-pinned. Every app that hosts a picker hears it.
        got = self._clients()
        km._note_model_pick("claude-sonnet-4-6")
        self.assertEqual([f["type"] for f in got["settings"]], ["models"], "the settings page hears the pin")
        self.assertEqual([f["type"] for f in got["feed"]], ["models"], "so does VS Code's feed panel")
        self.assertEqual(got["settings"][0], got["chat"][0], "the same frame every picker host gets")
        km._forget_model_pick("sonnet")
        self.assertEqual([f["type"] for f in got["settings"]], ["models", "models"], "…and the un-pin")

    def test_a_refused_version_is_forgotten_only_while_it_is_still_the_pin(self):
        # the CLI's refusal reaches the kernel through the backend's on_model_refused hook; the family's
        # pin goes ONLY if it still holds the refused id — a newer accepted pin for the family is not
        # this refusal's to touch (a writer whose evidence predates the diary stands down)
        got = self._clients()
        sid = "11111111-2222-3333-4444-555555555555"
        km._note_model_pick("claude-opus-4-8")
        km._model_pick_refused(sid, "claude-opus-4-8")
        self.assertEqual(km._model_picks(), {}, "the refused pin is forgotten")
        self.assertEqual([f["type"] for f in got["chat"]], ["models", "models"], "…and the pickers hear it")
        km._note_model_pick("claude-opus-4-8")
        km._note_model_pick("claude-opus-4-7")             # the newer pick
        km._model_pick_refused(sid, "claude-opus-4-8")     # the older one's refusal lands late
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-7"}, "the newer pin stands")
        km._model_pick_refused(sid, "opus")                # an alias was never a pin: no-op
        km._model_pick_refused(sid, "total-nonsense")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-7"})
        # the kernel installs the hook where it wires the fallback card — pinned like that one
        src = inspect.getsource(km._sdk_locked)
        self.assertIn("on_model_refused = staticmethod(_model_pick_refused)", src)

    def test_a_superseded_refusals_forget_drops_only_the_refused_pin_the_newer_pick_survives(self):
        # the backend fires on_model_refused for a refusal the session's own NEWER pick superseded too
        # (test_sdk_backend pins that). Safe for the newer pick because this side compares-and-swaps by
        # value: the newer pick's pin — same family or another — is untouched, and only the refused
        # id's own pin goes.
        sid = "11111111-2222-3333-4444-555555555555"
        km._note_model_pick("claude-fable-9-9")             # A, refused later
        km._note_model_pick("claude-sonnet-4-6")            # B, the newer pick, another family
        km._model_pick_refused(sid, "claude-fable-9-9")     # A's refusal lands after B
        self.assertEqual(km._model_picks(), {"sonnet": "claude-sonnet-4-6"},
                         "A's pin goes — a family click no longer sends the refused id; B's stands")
        km._note_model_pick("claude-fable-9-9")             # A again
        km._note_model_pick("claude-fable-5-1")             # B in the SAME family: the pin is B now
        km._model_pick_refused(sid, "claude-fable-9-9")
        self.assertEqual(km._model_picks(), {"sonnet": "claude-sonnet-4-6", "fable": "claude-fable-5-1"},
                         "the newer pick's pin survives its predecessor's refusal")


class _ModelsServer(unittest.TestCase):
    """A kernel HTTP handler on a loopback port + a hermetic STATE, for the /models route tests."""

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


class ModelsRoute(_ModelsServer):
    """GET /models carries versions + default per family."""

    def test_each_family_carries_versions_and_a_default(self):
        d = self._models()
        rows = {m["value"]: m for m in d["models"]}
        self.assertEqual([v["value"] for v in rows["opus"]["versions"]],
                         [v["value"] for v in km.MODEL_VERSIONS["opus"]])
        self.assertIn("color", rows["opus"], "the colormap tint still rides every family row")

    def test_the_payload_carries_the_pick_memorys_revision_the_frame_announces(self):
        # every picker answers a models frame with a fresh GET /models and applies whatever lands — so
        # two overlapping fetches (a frame during the page-load fetch; two quick frames) can resolve
        # out of order and the STALE list wins until the next change. The payload carries the same
        # `rev` the frame does, so a consumer can keep the newest it has applied and drop an older
        # response that lands late.
        got = self._clients()
        d = self._models()
        self.assertIsInstance(d.get("rev"), int, "the payload is stamped")
        self.assertEqual(d["rev"], km._models_rev[0])
        km._note_model_pick("claude-opus-4-8")
        self.assertEqual(self._models()["rev"], got["chat"][0]["rev"],
                         "after a change the payload's rev IS the frame's — one counter, two carriers")
        self.assertGreater(self._models()["rev"], d["rev"])

    def _clients(self):
        got = {"chat": []}
        fakes = [{"app": "chat", "wid": "w1", "alive": True, "send": lambda s: got["chat"].append(json.loads(s))}]
        with km._clients_lock:
            km._clients.extend(fakes)

        def drop():
            with km._clients_lock:
                km._clients[:] = [c for c in km._clients if c not in fakes]
        self.addCleanup(drop)
        return got

    def test_a_family_with_no_pick_defaults_to_its_alias_not_the_seed_head(self):
        # THE BUG: the default fell to MODEL_VERSIONS[fam][0], so a bare "Fable" click pinned the
        # session to claude-fable-5 — and when the CLI's `fable` alias advanced to Fable 5.1, every
        # picker-set session stayed behind. The alias is what the CLI resolves LIVE (the authoritative
        # source for "newest"), so a family click sends the alias.
        rows = {m["value"]: m for m in self._models()["models"]}
        for fam in ("fable", "opus", "sonnet", "haiku"):
            self.assertEqual(rows[fam]["default"], fam, "no pick yet → the family alias, never a pinned id")

    def test_the_default_follows_the_users_last_pick(self):
        km._note_model_pick("claude-opus-4-8")
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["default"], "claude-opus-4-8")
        self.assertEqual(rows["sonnet"]["default"], "sonnet", "other families unaffected: still the alias")

    def test_an_explicit_version_pick_pins_and_a_family_click_never_downgrades_it(self):
        # the version submenu is the ONE place a pin comes from; a later family click (which now
        # carries the alias) records nothing, so the remembered pin stands
        class _BE:
            calls = []

            def set_model(self, sid, value):
                self.calls.append(value)
                return True
        be = _BE()
        sid = "11111111-2222-3333-4444-555555555555"
        km._set_model_or_park(be, sid, "claude-opus-4-8")
        km._set_model_or_park(be, sid, "opus")
        self.assertEqual(be.calls, ["claude-opus-4-8", "opus"], "both reach the backend verbatim")
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["default"], "claude-opus-4-8", "the explicit pin survives the alias click")


class RefusedPick(_ModelsServer):
    """The CLI refused a version the picker recorded as its family's pin."""

    def test_a_refused_version_pick_returns_the_family_default_to_the_alias(self):
        class _BE:
            def set_model(self, sid, value):
                return True
        sid = "11111111-2222-3333-4444-555555555555"
        km._set_model_or_park(_BE(), sid, "claude-opus-4-8")
        opus = next(m for m in self._models()["models"] if m["value"] == "opus")
        self.assertEqual(opus["default"], "claude-opus-4-8", "recorded before the CLI rules, as designed")
        km._model_pick_refused(sid, "claude-opus-4-8")      # the backend's hook, on the CLI's error
        opus = next(m for m in self._models()["models"] if m["value"] == "opus")
        self.assertEqual(opus["default"], "opus", "the family row sends the alias again — not the refused id")


class LearnedVersions(_ModelsServer):
    """The seed table is a SEED, not the catalog: a model id a running session's CLI actually reported
    (reg.liveModelId, persisted by the SDK backend's _learn_model) joins its family's version list —
    the CLI is the authoritative source for what it serves — and is marked so the pickers can say it
    is new rather than hide a live model behind a stale menu."""

    def _reg(self, sid, **fields):
        d = jd.STATE / "sdk"
        d.mkdir(parents=True, exist_ok=True)
        (d / (sid + ".json")).write_text(json.dumps({"sid": sid, "name": "web", "cwd": "/tmp", "alive": True, **fields}))

    def test_a_reported_id_outside_the_seed_table_joins_its_family_marked_learned(self):
        self._reg("11111111-2222-3333-4444-555555555501", liveModel="Opus 5.1", liveModelId="claude-opus-5-1")
        rows = {m["value"]: m for m in self._models()["models"]}
        vs = rows["opus"]["versions"]
        self.assertEqual(vs[0], {"value": "claude-opus-5-1", "label": "Opus 5.1", "learned": True},
                         "newest first — the learned 5.1 lands ahead of the seed's 5")
        self.assertEqual([v["value"] for v in vs[1:]], [v["value"] for v in km.MODEL_VERSIONS["opus"]])
        self.assertEqual(rows["opus"]["default"], "opus", "a learned id changes nothing about the alias default")

    def test_a_learned_id_is_pickable_and_remembered_like_a_seed_one(self):
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-5-1")
        km._note_model_pick("claude-opus-5-1")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-5-1"})
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["default"], "claude-opus-5-1")

    def test_ids_the_seed_already_covers_or_that_are_not_first_party_add_nothing(self):
        # a dated snapshot of a seed version shares its label → the seed's dateless alias covers it;
        # a provider-prefixed id and a non-model string are not the shape the pickers send
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-4-5-20251101")
        self._reg("11111111-2222-3333-4444-555555555502", liveModelId="us.anthropic.claude-opus-4-8-v1:0")
        self._reg("11111111-2222-3333-4444-555555555503", liveModelId="<synthetic>")
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual([v["value"] for v in rows["opus"]["versions"]],
                         [v["value"] for v in km.MODEL_VERSIONS["opus"]])

    def test_a_context_tag_is_stripped_and_duplicates_collapse(self):
        # the CLI spells a 1M-context variant with a [1m] tail; two sessions on the same id → one row
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-sonnet-5-1[1m]")
        self._reg("11111111-2222-3333-4444-555555555502", liveModelId="claude-sonnet-5-1")
        rows = {m["value"]: m for m in self._models()["models"]}
        learned = [v for v in rows["sonnet"]["versions"] if v.get("learned")]
        self.assertEqual(learned, [{"value": "claude-sonnet-5-1", "label": "Sonnet 5.1", "learned": True}])

    def test_a_dated_snapshot_report_offers_the_dateless_alias(self):
        # a CLI may report the dated snapshot it is running; the pickable value is the DATELESS
        # alias — the snapshot retires, the alias follows the version
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-sonnet-5-1-20260901")
        rows = {m["value"]: m for m in self._models()["models"]}
        learned = [v for v in rows["sonnet"]["versions"] if v.get("learned")]
        self.assertEqual(learned, [{"value": "claude-sonnet-5-1", "label": "Sonnet 5.1", "learned": True}])

    def test_the_judge_tiers_accept_a_learned_version_the_gear_offers(self):
        # the gear's judge/index/distill/comment selects are built from /models' versions, learned
        # rows included — so the kernel must accept what it offered, and still refuse an id nobody
        # has ever served (the setter is effect-only: assert the state file)
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-5-1")
        km._set_judge_model("claude-opus-5-1")
        self.assertEqual(jd._state_str("judge-model", ""), "claude-opus-5-1", "offered → accepted")
        km._set_judge_model("claude-opus-9-9")
        self.assertEqual(jd._state_str("judge-model", ""), "claude-opus-5-1", "never served → refused, nothing changes")

    def test_a_pin_whose_reporting_session_is_gone_survives_on_disk(self):
        # the read filter hides a pin its family can't currently vouch for; the WRITE must not erase
        # it — a later pick for another family merges into the raw file, so the pin resolves again
        # the moment a session reports the id
        (jd.STATE / km.MODEL_PICKS_FILE_NAME).write_text(json.dumps({"opus": "claude-opus-5-1"}))
        self.assertEqual(km._model_picks(), {}, "no session vouches for it → hidden on read")
        km._note_model_pick("claude-sonnet-4-6")
        self.assertEqual(json.loads((jd.STATE / km.MODEL_PICKS_FILE_NAME).read_text()),
                         {"opus": "claude-opus-5-1", "sonnet": "claude-sonnet-4-6"}, "still on disk")
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-5-1")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-5-1", "sonnet": "claude-sonnet-4-6"})

    def _replace_reg(self, sid, **fields):
        # the SDK backend's write_reg shape — tmp + os.replace, a NEW inode per write. A test that
        # REWRITES a reg uses this, not _reg: an in-place write_text keeps the inode, and a same-size
        # rewrite inside one timestamp tick would leave (mtime_ns, size, ino) unchanged
        d = jd.STATE / "sdk"
        tmp = d / (sid + ".json.tmp")
        tmp.write_text(json.dumps({"sid": sid, "name": "web", "cwd": "/tmp", "alive": True, **fields}))
        os.replace(tmp, d / (sid + ".json"))

    @contextlib.contextmanager
    def _counting_reg_reads(self):
        """The reg files under STATE/sdk the kernel READS while held — only those: the /models route
        also reads the pick store, the CLI-block file and the catalog cache, so an unfiltered counter
        is never zero."""
        reads, real, sdk = [], Path.read_text, str(jd.STATE / "sdk") + os.sep

        def read_text(p, *a, **k):
            if str(p).startswith(sdk):
                reads.append(p.name)
            return real(p, *a, **k)
        with mock.patch.object(Path, "read_text", read_text):
            yield reads

    def test_a_warm_scan_re_reads_no_reg_whose_file_is_unchanged(self):
        # THE COST: the scan runs on the pickers' hot paths — every /models read (one per picker open
        # and per models frame), every pick via _version_family, the SDK loop's refusal hook — and
        # re-opened and JSON-parsed every reg per call. A reg whose file has not changed is parsed once.
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-5-1")
        self._reg("11111111-2222-3333-4444-555555555502", liveModelId="claude-sonnet-5-1")
        self._reg("11111111-2222-3333-4444-555555555503", liveModelId="claude-haiku-5")
        with self._counting_reg_reads() as cold:
            first = km._learned_versions()
        self.assertEqual(sorted(cold), ["11111111-2222-3333-4444-5555555555%02d.json" % n for n in (1, 2, 3)],
                         "the cold scan reads each reg once — so the counter below is proven to see reads")
        self.assertEqual(sorted(first), ["haiku", "opus", "sonnet"])
        with self._counting_reg_reads() as reads:
            second = km._learned_versions()
        self.assertEqual(reads, [], "nothing changed → nothing re-read")
        self.assertEqual(second, first)

    def test_a_changed_or_new_reg_is_seen_and_a_deleted_one_drops_on_the_next_call(self):
        # the file is the truth, so the cache keys on exactly what changes when the file does —
        # (mtime_ns, size, inode), the key list_regs' _REG_CACHE uses; write_reg's os.replace mints
        # a new inode per write, so a rewrite is never mistaken for the file it replaced
        sid1, sid2 = "11111111-2222-3333-4444-555555555501", "11111111-2222-3333-4444-555555555502"
        self._reg(sid1, liveModelId="claude-opus-5-1")
        self.assertEqual([v["value"] for v in km._learned_versions()["opus"]], ["claude-opus-5-1"])
        self._replace_reg(sid1, liveModelId="claude-opus-5-2")          # the session's CLI moved on
        self.assertEqual([v["value"] for v in km._learned_versions()["opus"]], ["claude-opus-5-2"],
                         "rewritten → re-read, the old id gone with the old file")
        self._reg(sid2, liveModelId="claude-sonnet-5-1")                 # a new session
        learned = km._learned_versions()
        self.assertEqual([v["value"] for v in learned["sonnet"]], ["claude-sonnet-5-1"], "new → seen")
        self.assertEqual([v["value"] for v in learned["opus"]], ["claude-opus-5-2"])
        (jd.STATE / "sdk" / (sid1 + ".json")).unlink()                    # the session is gone
        self.assertNotIn("opus", km._learned_versions(), "deleted → dropped")
        self.assertNotIn(str(jd.STATE / "sdk" / (sid1 + ".json")), km._learned_reg_cache, "…and its entry with it")

    def test_a_garbled_or_non_dict_reg_is_skipped_and_a_failed_parse_is_retried(self):
        # exactly what the uncached scan skipped: a file json.loads cannot parse, a JSON value that
        # is not a dict, a dict with no liveModelId — each contributes nothing and the good reg still
        # lands. A failed read or parse is NOT cached (the next call retries it, so a healed file gets
        # its turn); a parsed non-dict is (its answer cannot change until the file does)
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-5-1")
        self._reg("11111111-2222-3333-4444-555555555502")                 # no liveModelId yet
        d = jd.STATE / "sdk"
        (d / "broken.json").write_text("{not json")
        (d / "list.json").write_text("[]")
        learned = km._learned_versions()
        self.assertEqual({f: [v["value"] for v in vs] for f, vs in learned.items()}, {"opus": ["claude-opus-5-1"]})
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["versions"][0]["value"], "claude-opus-5-1",
                         "the route still answers, learned row included")
        self.assertNotIn(str(d / "broken.json"), km._learned_reg_cache, "a failed parse is not cached")
        with self._counting_reg_reads() as reads:
            km._learned_versions()
        self.assertEqual(reads, ["broken.json"], "the retry is the ONLY re-read: every parsed reg, dict or not, is warm")

    def test_a_bare_family_alias_never_scans_the_regs(self):
        # every bare family click sends the alias ('fable'); _note_model_pick asks _version_family
        # whether that is a pin, and an alias is never a catalog id — so the lookup fell through to
        # the reg scan on every click. A learned row's value is always a first-party VERSION id, so a
        # value _model_id_parts rejects can match none: answered before the scan.
        self._reg("11111111-2222-3333-4444-555555555501", liveModelId="claude-opus-5-1")
        with mock.patch.object(km, "_learned_versions", wraps=km._learned_versions) as scan:
            self.assertEqual(km._version_family("fable"), "")
            self.assertEqual(km._version_family("default"), "")
            self.assertEqual(km._version_family("total-nonsense"), "")
            self.assertEqual(scan.call_count, 0, "no version shape → no scan")
            self.assertEqual(km._version_family("claude-opus-5-1"), "opus", "a version-shaped value still consults the regs")
            self.assertEqual(scan.call_count, 1)


class AliasMigration(unittest.TestCase):
    """One-time boot pass, mirroring the CLI's own 2.1.257 `migration_fable5_to_fable_alias`: a stored
    model equal to a family's PRE-FIX seed head (what a bare family click used to record) becomes the
    family alias, so the next reconnect spawns `--model fable` and follows the CLI's newest. An explicit
    non-head pick is a deliberate pin and is left alone. Idempotent, loud, synthetic state."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        (jd.STATE / "sdk").mkdir(parents=True)
        (jd.STATE / "sdk-defaults.json").write_text(json.dumps({"model": "claude-fable-5", "effort": "xhigh"}))
        (jd.STATE / km.MODEL_PICKS_FILE_NAME).write_text(json.dumps({"fable": "claude-fable-5", "opus": "claude-opus-4-8"}))
        self._reg("a", model="claude-fable-5", liveModel="Fable 5", alive=True)
        self._reg("b", model="claude-opus-4-8", alive=True)
        self._reg("c", alive=False)
        self._reg("d", model="claude-sonnet-5", alive=False)
        self._reg("e", model="claude-fable-5-1")

    def tearDown(self):
        jd.STATE = self._state
        self.td.cleanup()

    def _reg(self, sid, **fields):
        (jd.STATE / "sdk" / (sid + ".json")).write_text(json.dumps({"sid": sid, "name": sid, "cwd": "/tmp", **fields}))

    def _read(self, sid):
        return json.loads((jd.STATE / "sdk" / (sid + ".json")).read_text())

    def _snapshot(self):
        return {p.name: p.read_text() for p in jd.STATE.rglob("*.json")}

    def test_seed_heads_become_aliases_everywhere_and_explicit_pins_stand(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = km._model_alias_boot_pass()
        self.assertEqual(n, 5, "defaults + the fable pick + regs a, d and e")
        self.assertEqual(json.loads((jd.STATE / "sdk-defaults.json").read_text()), {"model": "fable", "effort": "xhigh"},
                         "the remembered default follows the alias; effort untouched")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-8"},
                         "a head recorded as a pick is dropped — an absent pick IS the alias in that store")
        a = self._read("a")
        self.assertEqual(a["model"], "fable", "the next reconnect spawns --model fable")
        self.assertEqual((a["liveModel"], a["alive"], a["name"]), ("Fable 5", True, "a"), "nothing else on the reg moves")
        self.assertEqual(self._read("b")["model"], "claude-opus-4-8", "an explicit legacy pin is deliberate")
        self.assertNotIn("model", self._read("c"), "a session on the account default stays that way")
        self.assertEqual(self._read("d")["model"], "sonnet", "every family's pre-fix head migrates, dead regs too")
        self.assertEqual(self._read("e")["model"], "fable",
                         "fable's 5.1 head: every family click since 2026-09-01 wrote it, so it migrates like the others")
        self.assertIn("model-alias", err.getvalue())
        self.assertIn("fable", err.getvalue(), "the stderr line names what moved")

    def test_idempotent_and_silent_once_done(self):
        with contextlib.redirect_stderr(io.StringIO()):
            km._model_alias_boot_pass()
        before = self._snapshot()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._model_alias_boot_pass(), 0)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(err.getvalue(), "", "nothing to say when nothing moved")

    def test_nothing_to_migrate_on_a_fresh_state(self):
        for p in list(jd.STATE.rglob("*.json")):
            p.unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._model_alias_boot_pass(), 0)
        self.assertTrue((jd.STATE / km.MODEL_ALIAS_MIGRATION_MARKER).exists(),
                        "a clean pass over nothing is still the one run — stamped, so it never re-arms")

    def test_a_post_fix_explicit_pick_of_a_seed_head_survives_the_next_boot(self):
        # without a completion record EVERY kernel restart would rewrite any stored model equal to a
        # seed head — including a deliberate post-fix submenu pick of that very id (three of the four
        # heads are the CURRENT releases a user pins against a future .1). That contradicts the
        # "an explicit legacy pin stands" guarantee. A migration runs once.
        with contextlib.redirect_stderr(io.StringIO()):
            km._model_alias_boot_pass()
        # the user now pins Fable 5 from the submenu — on a session, as the remembered default, and it
        # lands in sdk-defaults as every set_model does
        self._reg("f", model="claude-fable-5", alive=True)
        km._note_model_pick("claude-fable-5")
        (jd.STATE / "sdk-defaults.json").write_text(json.dumps({"model": "claude-fable-5", "effort": "xhigh"}))
        before = self._snapshot()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._model_alias_boot_pass(), 0, "the next boot is not a migration")
        self.assertEqual(self._snapshot(), before, "the explicit pick stands everywhere it was written")
        self.assertEqual(self._read("f")["model"], "claude-fable-5")
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-8", "fable": "claude-fable-5"})
        self.assertEqual(err.getvalue(), "")

    def test_the_marker_gates_the_pass_and_is_stamped_after_the_first_run(self):
        marker = jd.STATE / km.MODEL_ALIAS_MIGRATION_MARKER
        self.assertFalse(marker.exists(), "a state that never booted the fix carries no marker")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._model_alias_boot_pass(), 5)
        rec = json.loads(marker.read_text())
        self.assertIsInstance(rec.get("t"), int, "stamped with the completion time")
        self.assertEqual(rec.get("moved"), 5)
        # a marker from a previous boot means SKIP ENTIRELY — even over state that looks migratable
        # (a head pinned on purpose after the fix looks exactly like pre-fix residue; the marker is
        # what tells them apart)
        self._reg("g", model="claude-opus-5")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._model_alias_boot_pass(), 0)
        self.assertEqual(self._read("g")["model"], "claude-opus-5")
        self.assertEqual(err.getvalue(), "")

    def test_a_failed_read_withholds_the_marker_so_the_next_boot_retries(self):
        # "returned" is not "succeeded" (the rewind migration's rule): a reg the pass could not READ is
        # a reg it did not migrate, so no marker is written and the pass re-arms at the next boot. A
        # TRANSIENT failure — the file is there, this boot could not open it (a garbled file is the
        # other kind, see the next test).
        self._reg("locked", model="claude-fable-5")
        real_text, real_bytes = Path.read_text, Path.read_bytes

        def refuse(p):
            if p.name == "locked.json":
                raise PermissionError(13, "Permission denied", str(p))

        def read_text(p, *a, **k):
            refuse(p)
            return real_text(p, *a, **k)

        def read_bytes(p, *a, **k):        # the pass reads bytes — the OS refuses either way
            refuse(p)
            return real_bytes(p, *a, **k)
        err = io.StringIO()
        with mock.patch.object(Path, "read_text", read_text), mock.patch.object(Path, "read_bytes", read_bytes), \
                contextlib.redirect_stderr(err):
            n = km._model_alias_boot_pass()
        self.assertEqual(n, 5, "everything readable still migrates")
        self.assertFalse((jd.STATE / km.MODEL_ALIAS_MIGRATION_MARKER).exists())
        self.assertIn("no marker written", err.getvalue())
        self.assertIn("locked.json", err.getvalue(), "the line names the file the next boot will retry")
        self.assertEqual(self._read("locked")["model"], "claude-fable-5", "untouched — it will migrate when readable")

    def test_a_garbled_store_is_named_loudly_and_never_withholds_the_marker(self):
        # a file json.loads cannot parse holds no pin ANY reader can see — read_reg, read_sdk_defaults
        # and the picks loader all return None/{} over it — so retrying over it can never migrate
        # anything; counting it a failure would never stamp, and re-float every deliberate post-fix pin
        # at every boot without ever naming the file. Garbage is PERMANENT: name the path loudly, treat
        # it as nothing to migrate, and stamp.
        (jd.STATE / "sdk" / "broken.json").write_text("{not json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = km._model_alias_boot_pass()
        self.assertEqual(n, 5, "everything readable migrates")
        self.assertTrue((jd.STATE / km.MODEL_ALIAS_MIGRATION_MARKER).exists(), "stamped — the pass is done")
        self.assertIn("broken.json", err.getvalue(), "the garbled file is NAMED")
        self.assertNotIn("no marker written", err.getvalue())
        self.assertEqual((jd.STATE / "sdk" / "broken.json").read_text(), "{not json", "never rewritten by the pass")
        # the same for a garbled defaults store, and the next boot is silent: the user's post-fix
        # pin of a seed head stands (the very re-float a missing marker would cause)
        for p in list(jd.STATE.rglob("*.json")):
            p.unlink()
        (jd.STATE / "sdk-defaults.json").write_text("garbage")
        self._reg("f", model="claude-fable-5")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._model_alias_boot_pass(), 0, "the marker gates it")
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self._read("f")["model"], "claude-fable-5")
        (jd.STATE / km.MODEL_ALIAS_MIGRATION_MARKER).unlink()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._model_alias_boot_pass(), 1)
        self.assertIn("sdk-defaults.json", err.getvalue())
        self.assertTrue((jd.STATE / km.MODEL_ALIAS_MIGRATION_MARKER).exists())

    def test_a_store_that_is_not_utf8_is_garbage_named_loudly_and_the_rest_still_migrates(self):
        # splitting reading (catching OSError) from parsing (catching ValueError) misses a file holding
        # non-UTF-8 bytes: Path.read_text raises UnicodeDecodeError in the READ — a ValueError, not an
        # OSError — which would escape both arms, abort the whole pass at that file (every later reg
        # unmigrated), print a traceback, and re-arm at every boot since the marker never stamps. No
        # reader can see a pin in such a file (read_reg, read_sdk_defaults and the picks loader all
        # return None/{} over it), so it is garbage like any other: named by path, nothing to migrate,
        # and the pass runs to completion and stamps.
        (jd.STATE / "sdk-defaults.json").write_bytes(b'{"model": "claude-fable-5", "note": "caf\xe9"}')   # Latin-1 byte
        (jd.STATE / "sdk" / "latin.json").write_bytes(b'{"sid": "latin", "model": "claude-fable-5\xe9"}')
        self._reg("z", model="claude-opus-5")     # sorts after latin.json — must still migrate
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = km._model_alias_boot_pass()
        self.assertEqual(n, 5, "the fable pick + regs a, d, e and z — everything readable migrates")
        self.assertEqual(self._read("z")["model"], "opus", "a reg sorted AFTER the garbled one still migrates")
        self.assertEqual(self._read("a")["model"], "fable")
        self.assertTrue((jd.STATE / km.MODEL_ALIAS_MIGRATION_MARKER).exists(), "stamped — the pass is done")
        out = err.getvalue()
        self.assertNotIn("Traceback", out, "a garbled file is a named line, never a crash")
        self.assertIn("latin.json", out, "the garbled reg is NAMED")
        self.assertIn("sdk-defaults.json", out, "so is the garbled defaults store")
        self.assertNotIn("no marker written", out)
        self.assertEqual((jd.STATE / "sdk" / "latin.json").read_bytes(), b'{"sid": "latin", "model": "claude-fable-5\xe9"}',
                         "never rewritten by the pass")

    def test_the_pass_is_wired_into_main_before_the_backend_constructs(self):
        # the ordering IS the correctness: the pass rewrites reg `model` fields, which become
        # chosen_model the moment the SDK backend constructs — and _boot_warm's alive-session read
        # constructs it. main() must run the pass first, and once.
        src = inspect.getsource(km.main)
        i = src.index("_model_alias_boot_pass()")
        self.assertLess(i, src.index("_boot_warm()"), "before _boot_warm constructs the backend")
        self.assertLess(i, src.index("target=_sdk"), "and before the explicit _sdk thread")
        self.assertEqual(src.count("_model_alias_boot_pass("), 1, "called from exactly one place")


class RoutedContextTag(unittest.TestCase):
    """The CLI spells a 1M-context variant with a [1m] tail (`fable[1m]`, `claude-opus-4-8[1m]`). The
    kernel vouches for the tagged family alias like the tagged id, and its pending-switch check reads
    through the tag — the pretty live name never carries one."""

    def test_the_kernels_pending_check_reads_through_the_tag(self):
        # the literal "fable[1m]" is never a substring of "Fable 5.1", so the switching-dots would ride
        # the kernel's stamp to its cap instead of resolving on the event
        self.assertTrue(km._alias_reflects("Fable 5.1", "fable[1m]"))
        self.assertTrue(km._alias_reflects("Opus 4.8", "claude-opus-4-8[1m]"))
        self.assertFalse(km._alias_reflects("Fable 5.1", "opus[1m]"))

    def test_a_tagged_family_alias_is_vouched_for(self):
        # `claude-fable-5-1[1m]` routes (the id parser strips the tag); `fable[1m]` must too, or it
        # falls to the CLI as literal text — the registry bypass the routing exists to close
        self.assertTrue(km._vouched_model("fable[1m]"))
        self.assertTrue(km._vouched_model("claude-fable-5-1[1m]"))
        self.assertFalse(km._vouched_model("fable[2m]x"), "a tag is a trailing [..] only")
        self.assertFalse(km._vouched_model("opsu[1m]"))


class AliasResolution(_ModelsServer):
    """The family alias is resolved by the KERNEL (the user 2026-09-28: "don't rely on the lagging opus alias"). A bare
    family click and the version submenu's Latest row send `opus`, and the CLI's own resolution lagged a release:
    `--model opus` on Claude Code 2.1.280 ran claude-opus-5 a week after 5.5 shipped, while this kernel's catalog
    listed 5.5. The SDK backend now asks _resolve_model_alias for the id to hand the CLI (at connect and at a live
    switch), the stored layers keep the alias (floating, never a pin), and /models names each family's resolution
    (`resolves`) so the pickers can say what a family click runs. Synthetic: a made-up claude-opus-9-9."""

    def setUp(self):
        super().setUp()
        self._table = {fam: [dict(v) for v in vs] for fam, vs in km.MODEL_VERSIONS.items()}
        km._resolved_refused.clear()

    def tearDown(self):
        km._apply_model_catalog(self._table, "seed")     # the running table, as this test found it
        km._resolved_refused.clear()
        super().tearDown()

    def _grow(self, *ids):
        km._apply_model_catalog(km.merge_model_catalog(km._MODEL_SEED, [{"id": i} for i in ids]), "api")

    def test_a_bare_alias_resolves_to_its_familys_newest_catalog_version(self):
        self._grow("claude-opus-9-9")
        self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-9-9")
        self.assertEqual(km._resolve_model_alias("opus[1m]"), "claude-opus-9-9[1m]", "the context tag rides along")
        self.assertEqual(km._resolve_model_alias(" Opus "), "claude-opus-9-9", "read as _model_id_clean reads a pick")
        self.assertEqual(km._resolve_model_alias("fable"), km.MODEL_VERSIONS["fable"][0]["value"], "the seed's own head when nothing grew")

    def test_everything_but_a_family_alias_passes_verbatim(self):
        self._grow("claude-opus-9-9")
        for v in ("claude-opus-5", "claude-opus-9-9", "claude-opus-5[1m]", "default", "", "gpt-6-astra", "nonsense", None):
            self.assertEqual(km._resolve_model_alias(v), str(v or ""), repr(v))

    def test_a_version_the_installed_cli_refused_is_skipped_for_the_next_newest(self):
        self._grow("claude-opus-9-9")
        (jd.STATE / "cli-model-blocks.json").write_text(json.dumps(
            {"claude-opus-9-9": {"needs": "9.9.9", "cli": "2.1.280", "t": 1}}))     # T222's durable version block
        self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-5", "the next newest")
        km._note_resolved_model_refused("claude-opus-5")                          # a live refusal, this kernel life
        self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-4-8", "skipped too")

    def test_every_candidate_refused_falls_back_to_the_alias_itself(self):
        blocks = {v["value"]: {"needs": "9", "cli": "1", "t": 1} for v in km.MODEL_VERSIONS["haiku"]}
        (jd.STATE / "cli-model-blocks.json").write_text(json.dumps(blocks))
        self.assertEqual(km._resolve_model_alias("haiku"), "haiku", "the CLI's own resolution, as before")
        self.assertEqual(km._resolve_model_alias("haiku[1m]"), "haiku[1m]")

    def test_a_tagged_refusal_is_a_refusal_of_the_version(self):
        # the backend files what the CLI saw: for opus[1m] that is claude-opus-9-9[1m] (an adversarial review,
        # 2026-09-28: filed tagged, neither store ever matched the untagged rows, and every connect retried the id)
        self._grow("claude-opus-9-9")
        km._note_resolved_model_refused("claude-opus-9-9[1m]")
        self.assertIn("claude-opus-9-9", km._resolved_refused, "stored untagged")
        self.assertEqual(km._resolve_model_alias("opus[1m]"), "claude-opus-5[1m]", "skipped, tag kept")
        self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-5", "skipped for the bare alias too")
        km._resolved_refused.clear()
        (jd.STATE / "cli-model-blocks.json").write_text(json.dumps(
            {"claude-opus-9-9[1m]": {"needs": "9.9.9", "cli": "2.1.280", "t": 1}}))    # a block a tagged id left behind
        self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-5", "a tagged block key reads as the version")

    def test_a_version_block_the_installed_cli_now_meets_has_lapsed(self):
        # T222's block is filed by the CLI that refused; once the binary on disk meets the minimum it named, the block
        # must let go — the alias resolves past a blocked version, so the reply that used to clear it never comes
        # (an adversarial review, 2026-09-28). An unreadable binary version keeps every block.
        self._grow("claude-opus-9-9")
        (jd.STATE / "cli-model-blocks.json").write_text(json.dumps(
            {"claude-opus-9-9": {"needs": "2.1.280", "cli": "2.1.270", "t": 1}}))
        probe = km._installed_cli_version
        try:
            km._installed_cli_version = lambda: (2, 1, 279)
            self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-5", "the binary is still short: blocked")
            rows = {m["value"]: m for m in self._models()["models"]}
            self.assertIn("needs CLI", rows["opus"]["versions"][0]["label"])
            km._installed_cli_version = lambda: (2, 1, 280)
            self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-9-9", "the binary meets the minimum: the block lapsed")
            rows = {m["value"]: m for m in self._models()["models"]}
            self.assertEqual(rows["opus"]["versions"][0]["label"], "Opus 9.9", "and the row no longer says needs")
            self.assertEqual(rows["opus"]["resolves"], "claude-opus-9-9")
            km._installed_cli_version = lambda: None
            self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-5", "unknown binary version: every block stands")
        finally:
            km._installed_cli_version = probe

    def test_the_installed_cli_version_is_read_once_per_binary(self):
        self.assertEqual(km._version_tuple("2.1.280 (Claude Code)"), (2, 1, 280))
        self.assertEqual(km._version_tuple("v9.0"), (9, 0))
        self.assertEqual(km._version_tuple("no number here"), ())
        fake = Path(self.td.name) / "claude-fake"
        fake.write_text("#!/bin/sh\necho '2.1.280 (Claude Code)'\n"); fake.chmod(0o755)
        saved = os.environ.get("ROMP_CLAUDE_BIN")
        try:
            os.environ["ROMP_CLAUDE_BIN"] = str(fake); km._CLI_VERSION_CACHE.clear()
            self.assertEqual(km._installed_cli_version(), (2, 1, 280))
            fake.chmod(0o000)                                          # unrunnable now; the same stat → the cached answer
            self.assertEqual(km._installed_cli_version(), (2, 1, 280), "one probe per binary")
            fake.chmod(0o755); fake.write_text("#!/bin/sh\necho 'nothing'\n")   # a new binary (its stat moved): probed again
            self.assertIsNone(km._installed_cli_version(), "an upgrade on disk is read at the next call")
            os.environ["ROMP_CLAUDE_BIN"] = "/bin/false"; km._CLI_VERSION_CACHE.clear()
            self.assertIsNone(km._installed_cli_version(), "another binary's version line (coreutils' 'false 9.4') is not Claude Code's")
            fake.write_text("#!/bin/sh\necho 'Claude Code'\n"); os.environ["ROMP_CLAUDE_BIN"] = str(fake); km._CLI_VERSION_CACHE.clear()
            self.assertIsNone(km._installed_cli_version(), "no number: unknown")
            os.environ["ROMP_CLAUDE_BIN"] = str(Path(self.td.name) / "absent"); km._CLI_VERSION_CACHE.clear()
            self.assertIsNone(km._installed_cli_version())
        finally:
            km._CLI_VERSION_CACHE.clear()
            if saved is None:
                os.environ.pop("ROMP_CLAUDE_BIN", None)
            else:
                os.environ["ROMP_CLAUDE_BIN"] = saved

    def test_a_version_a_running_sessions_cli_reports_counts(self):
        # a learned row (reg.liveModelId under STATE/sdk) is the catalog's live lookahead: the alias reaches it
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / "11111111-2222-3333-4444-555555555555.json").write_text(
            json.dumps({"sid": "11111111-2222-3333-4444-555555555555", "liveModelId": "claude-opus-9-9"}))
        self.assertEqual(km._resolve_model_alias("opus"), "claude-opus-9-9")

    def test_models_names_what_each_family_resolves_to_and_the_family_click_still_sends_the_alias(self):
        self._grow("claude-opus-9-9")
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["resolves"], "claude-opus-9-9")
        self.assertEqual(rows["opus"]["default"], "opus", "floating: the click sends the alias, the kernel resolves it")
        for fam in km._MODEL_VALUES:
            self.assertEqual(rows[fam]["resolves"], km._resolve_model_alias(fam), fam)

    def test_a_pin_is_the_default_while_resolves_still_names_the_newest(self):
        self._grow("claude-opus-9-9")
        km._note_model_pick("claude-opus-4-8")
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["default"], "claude-opus-4-8", "the family click runs the pin")
        self.assertEqual(rows["opus"]["resolves"], "claude-opus-9-9", "what its Latest row would run")

    def test_a_blocked_newest_moves_resolves_on_the_route_too(self):
        self._grow("claude-opus-9-9")
        (jd.STATE / "cli-model-blocks.json").write_text(json.dumps(
            {"claude-opus-9-9": {"needs": "9.9.9", "cli": "2.1.280", "t": 1}}))
        rows = {m["value"]: m for m in self._models()["models"]}
        self.assertEqual(rows["opus"]["resolves"], "claude-opus-5")
        self.assertIn("needs CLI", rows["opus"]["versions"][0]["label"], "the refused row still says why, as before")

    def test_the_refusal_memo_tells_every_picker_once(self):
        rev0 = km._models_rev[0]
        km._note_resolved_model_refused("claude-opus-5")
        self.assertEqual(km._models_rev[0], rev0 + 1, "a models frame: the pickers re-read `resolves`")
        km._note_resolved_model_refused("claude-opus-5")
        self.assertEqual(km._models_rev[0], rev0 + 1, "the same id again moves nothing")
        km._note_resolved_model_refused("")
        self.assertEqual(km._models_rev[0], rev0 + 1, "an empty id is no refusal")


if __name__ == "__main__":
    unittest.main()
