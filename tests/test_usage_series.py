#!/usr/bin/env python3
"""The usage hover's time series (the user 2026-08-13): window utilization gets a recorded past
(usage-history.json, appended by both usage writers, hourly max-pct with roll-aware overwrites, 192h
bound), spend.json's hour buckets ship as a dense $/hour series, and a pure API-key host — which never
gets a usage.json at all — finally reaches the no-login spend arm instead of reporting {} (the devbox,
whose spend was missing from the fleet sum). SYNTHETIC fixtures only.

Clock discipline: every writer/reader takes an injectable `now`, and these tests FREEZE it (FIXED) —
an import-time "current hour" diverges from a call-time stamp whenever the suite straddles an hour
boundary, which is exactly how the 23:00 UTC CI run failed while local runs passed. Where a path still
reads the real clock (_usage), assertions derive the index from the payload's own h0, never from an
assumed position."""
import json
import os
import pathlib
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_useries", os.path.join(BIN, "romp-kernel")).load_module()
sb = SourceFileLoader("romp_sdkb_useries", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
jd = km.jd

FIXED = 1765000000.0                 # frozen mid-hour instant; every key below derives from it


def _h(n=0):
    """Hour key n hours before FIXED, in the stores' own format."""
    return time.strftime("%Y-%m-%dT%H", time.localtime(FIXED - n * 3600))


class UsageHistoryLedger(unittest.TestCase):
    """_record_usage_history: hourly max-pct per window, roll-aware, bounded, atomic."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.b = object.__new__(sb.SdkBackend)
        self.b.state_dir = pathlib.Path(self.td.name)
        self.b._log = lambda *a, **k: None

    def tearDown(self):
        self.td.cleanup()

    def _read(self):
        return json.loads((pathlib.Path(self.td.name) / "usage-history.json").read_text())

    def test_max_pct_per_hour_and_roll_takes_the_fresh_reading(self):
        self.b._record_usage_history({"acct": "a1", "five_hour": {"pct": 30, "resets_at": 100}}, now=FIXED)
        self.b._record_usage_history({"acct": "a1", "five_hour": {"pct": 20, "resets_at": 100}}, now=FIXED)
        ent = self._read()["hours"][_h()]
        self.assertEqual(ent["five_hour"], {"pct": 30, "ra": 100},
                         "within one window the hour keeps its MAX (usage only climbs)")
        self.b._record_usage_history({"acct": "a1", "five_hour": {"pct": 5, "resets_at": 200}}, now=FIXED)
        ent = self._read()["hours"][_h()]
        self.assertEqual(ent["five_hour"], {"pct": 5, "ra": 200},
                         "a ROLL (new resets_at) takes the fresh reading outright")

    def test_windows_accumulate_independently_and_prune_bounds_the_file(self):
        self.b._record_usage_history({"acct": "a1", "seven_day": {"pct": 11, "resets_at": 7},
                                      "fable": {"pct": 3, "resets_at": 9}}, now=FIXED)
        ent = self._read()["hours"][_h()]
        self.assertEqual(ent["seven_day"]["pct"], 11)
        self.assertEqual(ent["fable"]["pct"], 3)
        self.assertNotIn("five_hour", ent, "a window the reading lacks stays absent — never a made-up 0")
        hours = {_h(i): {"acct": "a1", "five_hour": {"pct": 1, "ra": 1}} for i in range(1, 250)}
        (pathlib.Path(self.td.name) / "usage-history.json").write_text(json.dumps({"hours": hours}))
        self.b._record_usage_history({"acct": "a1", "five_hour": {"pct": 50, "resets_at": 1}}, now=FIXED)
        self.assertLessEqual(len(self._read()["hours"]), 192, "8 days of hours, never unbounded")


class SeriesPayloads(unittest.TestCase):
    """_spend_series: dense arrays + base hour; honest gaps. (The winSeries assembler that lived
    beside it is gone — the user 2026-08-14 wanted only the fleet $/h graph; rail-spend.test.ts
    pins the removal. usage-history.json keeps recording — sdk_backend _record_usage_history.)"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._claude_account)
        jd.STATE = pathlib.Path(self.td.name)
        km._claude_account = lambda: "me"    # pinned: the foreign-skip below must not float on the
        # machine's real login (CI has none, which turns the skip inert)

    def tearDown(self):
        jd.STATE, km._claude_account = self.saved
        self.td.cleanup()

    def test_spend_series_places_hours_and_splits_keyed(self):
        (jd.STATE / "spend.json").write_text(json.dumps({"hours": {
            _h(): {"usd": 2.5, "key": {"usd": 2.0, "turns": 1, "tok": 5}},
            _h(3): {"usd": 1.0},
        }, "days": {}}))
        ss = km._spend_series(now=FIXED)
        self.assertEqual(len(ss["usd"]), 192)
        self.assertEqual(ss["usd"][-1], 2.5)
        self.assertEqual(ss["usd"][-4], 1.0)
        self.assertEqual(ss["usd"][-2], 0.0, "an hour with no turns genuinely spent $0 — money has a true zero")
        self.assertEqual(km._spend_series(keyed_only=True, now=FIXED)["usd"][-1], 2.0)
        self.assertEqual(km._spend_series(keyed_only=True, now=FIXED)["usd"][-4], 0.0,
                         "an hour with no key sub-counter contributes nothing to the keyed series")

    def test_empty_stores_return_none(self):
        self.assertIsNone(km._spend_series(now=FIXED))


class ApiKeyHostReportsSpend(unittest.TestCase):
    """The devbox fix: no usage.json at all + spend.json present + no login → the no-login spend arm
    answers (spend + spendSeries), never {} (kernel _usage used to bail on the missing file)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._claude_account)
        jd.STATE = pathlib.Path(self.td.name)
        km._claude_account = lambda: ""      # the devbox shape: NO login — never the dev machine's real one

    def tearDown(self):
        jd.STATE, km._claude_account = self.saved
        self.td.cleanup()

    def test_missing_usage_json_still_reports_key_spend(self):
        # _usage() runs on the REAL clock, so the fixture stamps the real current hour and the
        # assertion asks the payload's own h0 where that hour landed — position-independent.
        hour_key = time.strftime("%Y-%m-%dT%H")
        (jd.STATE / "spend.json").write_text(json.dumps({
            "hours": {hour_key: {"usd": 3.0, "turns": 2, "tok": 10}},
            "days": {time.strftime("%Y-%m-%d"): {"usd": 3.0, "turns": 2, "tok": 10}}}))
        u = km._usage()
        self.assertIsNotNone(u, "a keyed host with recorded spend must never answer {}")
        self.assertTrue(u.get("apiKey"))
        self.assertEqual(u["spend"]["day"]["usd"], 3.0)
        i = km._series_index(hour_key, u["spendSeries"]["h0"])
        self.assertEqual(u["spendSeries"]["usd"][i], 3.0, "…and ships the $/hour series for the hover graph")

    def test_nothing_recorded_still_answers_none(self):
        self.assertIsNone(km._usage(), "a fresh box with neither login nor spend has nothing to show")

    def test_spend_carries_its_own_freshness_stamp(self):
        # the user 2026-08-24: the windows' "updated 9h 38m ago" (usage.json's t — which nothing
        # writes under key auth) sat directly above the spend section and read as the spend's age.
        # The payload now stamps the spend's OWN last-record moment: spend.json's mtime, an event
        # time (the recorder writes per turn result), so the hover can say when the last charge
        # actually landed — and a frozen number visibly ages instead of hiding behind the windows.
        hour_key = time.strftime("%Y-%m-%dT%H")
        p = jd.STATE / "spend.json"
        p.write_text(json.dumps({"hours": {hour_key: {"usd": 1.0, "turns": 1, "tok": 5}},
                                 "days": {time.strftime("%Y-%m-%d"): {"usd": 1.0, "turns": 1, "tok": 5}}}))
        os.utime(p, (1000000000, 1000000000))
        u = km._usage()
        self.assertEqual(u.get("spendAt"), 1000000000, "the stamp is the record file's own mtime")

    def test_view_and_tag_state_never_filters_the_spend_aggregation(self):
        # the user 2026-08-24 asked whether hidden/tagged sessions count toward the spend. They MUST:
        # the series is machine-level API-key billing, recorded per turn result before any view
        # exists, and read straight from spend.json's buckets. This pins that a views blob hiding
        # and tagging everything changes NOTHING about the sums — if a view/tag filter ever leaks
        # into the aggregation, this breaks.
        hour_key = time.strftime("%Y-%m-%dT%H")
        (jd.STATE / "spend.json").write_text(json.dumps({
            "hours": {hour_key: {"usd": 7.5, "turns": 3, "tok": 30}},
            "days": {time.strftime("%Y-%m-%d"): {"usd": 7.5, "turns": 3, "tok": 30}}}))
        before = km._usage()
        (jd.STATE / "timeline-views.json").write_text(json.dumps({
            "active": "g1", "hidden": ["11111111-2222-3333-4444-555555555555"],
            "tags": [{"id": "g1", "name": "workers", "color": "#DD42FF",
                      "members": ["22222222-3333-4444-5555-666666666666"]}]}))
        km._flags_cache.clear()
        after = km._usage()
        self.assertEqual(after["spend"], before["spend"],
                         "hiding/tagging sessions must never change the billed sums")
        self.assertEqual(after["spendSeries"], before["spendSeries"],
                         "…or the $/h series behind the graph")


class RemoteUsageStaleness(unittest.TestCase):
    def test_an_answered_empty_payload_clears_instead_of_freezing(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('r.pop("usage", None)', src,
                      "a host that ANSWERS with nothing to show clears its row — only an unanswered "
                      "poll (blip/rate-gate) keeps the last good reading")


if __name__ == "__main__":
    unittest.main()
