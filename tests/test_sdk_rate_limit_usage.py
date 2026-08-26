#!/usr/bin/env python3
"""SDK-sourced /usage bars (the user 2026-06-30).

The rail's rate-limit bars (5h Session + 7d Weekly) read usage.json. Under tmux that file is written by
Claude Code's statusline.sh; an SDK session has NO statusline, so the bars went stale/blank there. The
Agent SDK's DESIGNED source is the RateLimitEvent stream — each carries one window's utilization (0.0-1.0)
+ resets_at (utilization only in the allowed_warning band — see RateLimitUsageStaleness in
test_sdk_backend.py for the status-aware merge). SdkBackend._record_rate_limit folds each event into the
SAME usage.json shape, so the same kernel _usage() reader lights the bars for SDK sessions too. Synthetic
fixtures (duck-typed info)."""
import inspect
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py")).load_module()


def _info(rate_limit_type, utilization, resets_at=None):
    """A duck-typed RateLimitInfo (only the fields _record_rate_limit reads)."""
    return SimpleNamespace(rate_limit_type=rate_limit_type, utilization=utilization, resets_at=resets_at)


class RecordRateLimit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = Path(self.td.name)
        self.be = sb.SdkBackend(self.state, "claude", lambda *a: None)

    def tearDown(self):
        self.td.cleanup()

    def _usage(self):
        return json.loads((self.state / "usage.json").read_text())

    def test_accumulates_both_windows_into_the_statusline_shape(self):
        # each window arrives as its OWN event; the merged file must carry BOTH (utilization → pct)
        self.be._record_rate_limit(_info("five_hour", 0.10, 1782787200))
        self.be._record_rate_limit(_info("seven_day", 0.11, 1783364400))
        u = self._usage()
        self.assertEqual(u["five_hour"], {"pct": 10, "resets_at": 1782787200})
        self.assertEqual(u["seven_day"], {"pct": 11, "resets_at": 1783364400})
        self.assertIsInstance(u["t"], int)

    def test_a_seven_day_event_does_not_null_the_statusline_five_hour(self):
        # Regression (the user 2026-06-30, who reported the session limit disappeared): usage.json is account-wide and also
        # written by the tmux statusline. A seven_day-only SDK event must MERGE — preserve the five_hour another
        # writer set — not clobber the whole file from our partial accumulator.
        (self.state / "usage.json").write_text(json.dumps({
            "t": 1, "five_hour": {"pct": 10, "resets_at": 1782787200},   # as the statusline wrote it
            "seven_day": {"pct": 11, "resets_at": 1783364400}}))
        self.be._record_rate_limit(_info("seven_day", 0.52, 1783364400))   # SDK sees ONLY a seven_day event
        u = self._usage()
        self.assertEqual(u["five_hour"], {"pct": 10, "resets_at": 1782787200}, "the Session (5h) window is preserved")
        self.assertEqual(u["seven_day"]["pct"], 52, "the weekly window is updated from the event")

    def test_weekly_takes_the_binding_highest_seven_day_variant(self):
        # opus / sonnet sub-limits also feed the weekly bar; the BINDING (highest) one wins
        self.be._record_rate_limit(_info("seven_day", 0.11, 1))
        self.be._record_rate_limit(_info("seven_day_opus", 0.40, 2))
        self.assertEqual(self._usage()["seven_day"]["pct"], 40, "the highest weekly variant is the binding limit")

    def test_overage_and_unmodeled_windows_are_ignored(self):
        self.be._record_rate_limit(_info("five_hour", 0.25, 5))
        self.be._record_rate_limit(_info("overage", 0.99, 9))       # not a bar window
        self.be._record_rate_limit(_info(None, 0.99, 9))            # no type
        u = self._usage()
        self.assertEqual(u["five_hour"]["pct"], 25)
        self.assertIsNone(u["seven_day"], "overage/None never populate the weekly bar")

    def test_null_utilization_with_a_window_id_still_establishes_the_live_window(self):
        # The CLI attaches utilization ONLY in the allowed_warning band (2026-07-02 cadence data: 4 of 452
        # events); a plain `allowed` event carries None. It still names the LIVE window (resets_at), so it
        # writes pct=0 for a window we know nothing else about — bars light up honest-low instead of staying
        # blank/stale until the warning band.
        self.be._record_rate_limit(_info("five_hour", None, 5))
        self.assertEqual(self._usage()["five_hour"], {"pct": 0, "resets_at": 5})

    def test_an_event_with_no_utilization_and_no_window_id_writes_nothing(self):
        self.be._record_rate_limit(_info("five_hour", None, None))
        self.assertFalse((self.state / "usage.json").exists(), "nothing to say -> nothing written")

    def test_a_differently_dated_reading_of_the_same_window_never_resets_the_bar(self):
        # The bug (the user 2026-08-02: the 5h bar read 18% against a real 45%). romp has TWO sources for
        # one window and they date its boundary differently — the get_usage snapshot carries the /usage
        # endpoint's 23:00, the event stream carries the API headers' 23:10. Equality read that 10-minute
        # gap as a fresh window and reset the exact reading to 0 within a second of it landing, leaving
        # the rail to creep back up from zero on the rare events that carry a number at all.
        exact = 1785711600                                   # what the get_usage snapshot wrote
        (self.state / "usage.json").write_text(json.dumps({
            "t": 1, "five_hour": {"pct": 45, "resets_at": exact}, "seven_day": None, "fable": None}))
        self.be._record_rate_limit(_info("five_hour", None, exact + 600))   # the header's reading, no utilization
        five = self._usage()["five_hour"]
        self.assertEqual(five["pct"], 45, "an unknown utilization may never lower a known reading")
        self.assertEqual(five["resets_at"], exact, "the exact boundary stands; the event only refines usage")

    def test_a_real_roll_still_starts_the_next_window_at_zero(self):
        # The slack must not swallow an actual roll: a 5h window's reset moves by hours, not minutes.
        (self.state / "usage.json").write_text(json.dumps({
            "t": 1, "five_hour": {"pct": 96, "resets_at": 1785711600}, "seven_day": None, "fable": None}))
        self.be._record_rate_limit(_info("five_hour", None, 1785711600 + 5 * 3600))
        five = self._usage()["five_hour"]
        self.assertEqual(five["pct"], 0, "a rolled window starts empty")
        self.assertEqual(five["resets_at"], 1785711600 + 5 * 3600)

    def test_same_window_is_decided_with_slack_not_equality(self):
        self.assertTrue(sb._same_window(1785711600, 1785712200), "10 minutes apart is one window, said twice")
        self.assertTrue(sb._same_window(1785711600, 1785711600))
        self.assertFalse(sb._same_window(1785711600, 1785711600 + 5 * 3600), "a 5h roll is a new window")
        self.assertFalse(sb._same_window(None, 1785711600), "an absent stamp names no window")

    def test_a_higher_reading_still_climbs_within_the_window(self):
        (self.state / "usage.json").write_text(json.dumps({
            "t": 1, "five_hour": {"pct": 45, "resets_at": 1785711600}, "seven_day": None, "fable": None}))
        self.be._record_rate_limit(_info("five_hour", 0.61, 1785711600 + 600))
        self.assertEqual(self._usage()["five_hour"]["pct"], 61, "in-window usage climbs on a known number")

    def test_the_kernel_usage_reader_lights_the_bars_from_what_the_sdk_wrote(self):
        # End-to-end: the SDK writer + the kernel reader agree on usage.json (no statusline in the loop).
        self.be._record_rate_limit(_info("five_hour", 0.10, 1782787200))
        self.be._record_rate_limit(_info("seven_day", 0.11, 1783364400))
        SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
        SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
        os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
        os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
        km = SourceFileLoader("romp_kernel_rl", os.path.join(BIN, "romp-kernel")).load_module()
        saved = km.jd.STATE
        km.jd.STATE = self.state
        try:
            u = km._usage()
        finally:
            km.jd.STATE = saved
        self.assertIsNotNone(u)
        self.assertEqual(u["fiveHour"]["pct"], 10)
        self.assertEqual(u["sevenDay"]["pct"], 11)
        self.assertEqual(u["fiveHour"]["resetsAt"], 1782787200)


class ExactSnapshotRefresh(unittest.TestCase):
    """The exact get_usage snapshot is the only source that carries a true percent for every window; the
    event stream attaches one only in the warning band. So a refresh that never happens is not a small
    loss — it is the difference between the real number and a floor that creeps up from zero. It may
    never fail quietly (the user 2026-08-02)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.logs = []
        self.be = sb.SdkBackend(Path(self.td.name), "claude", lambda *a: None,
                                log=lambda m: self.logs.append(m))

    def tearDown(self):
        self.td.cleanup()

    def _session(self, scheduled, **kw):
        kw.setdefault("api_key_auth", False)   # per-session auth (2026-08-08): a keyed one is no candidate
        kw.setdefault("auth_live", "login")    # …whose init CONFIRMED the login (the all-keyed one-shot
        #   re-arms only on that; "" would model a pre-init unknown — see test_expected_auth.py)
        s = SimpleNamespace(client=object(), loop=object(), ended=False, **kw)
        s.refresh_usage = lambda: scheduled
        return s

    def test_every_live_session_is_tried_not_just_the_first(self):
        # One session whose loop has gone away used to be enough to make the click do nothing at all,
        # while the rail went on presenting its last reading as current.
        asked = []
        dead = self._session(False)
        live = self._session(True)
        for s in (dead, live):
            s.refresh_usage = (lambda s=s: (asked.append(s), s is live)[1])
        self.be.sessions = {"a": dead, "b": live}
        self.be.refresh_usage()
        self.assertEqual(asked, [dead, live], "the refusal moves on to the next candidate")

    def test_it_stops_at_the_first_session_that_takes_it(self):
        asked = []
        a, b = self._session(True), self._session(True)
        for s in (a, b):
            s.refresh_usage = (lambda s=s: (asked.append(s), True)[1])
        self.be.sessions = {"a": a, "b": b}
        self.be.refresh_usage()
        self.assertEqual(asked, [a], "one snapshot is enough — these windows are account-wide")

    def test_nobody_able_to_run_it_is_said_out_loud(self):
        s = self._session(False)
        self.be.sessions = {"a": s}
        self.be.refresh_usage()
        self.assertTrue(any("usage refresh" in m for m in self.logs),
                        "a refresh that could not run must not look like one that did")

    def test_a_failing_control_request_is_logged_rather_than_swallowed(self):
        src = inspect.getsource(sb.SdkSession._do_refresh_usage)
        self.assertNotIn("except Exception:\n            r = None", src,
                         "the bare swallow is what made a dead refresh indistinguishable from a live one")
        self.assertIn("_log(", src)

    def test_the_context_refresh_keeps_its_own_poke(self):
        # `changed` is computed by the context refresh; the poke that reads it had been left in
        # refresh_usage, where the name is undefined — so /usage clicks raised NameError and a moved
        # context % waited for the backstop instead of pushing.
        self.assertIn("if changed:", inspect.getsource(sb.SdkSession._do_refresh_context))
        self.assertNotIn("changed", inspect.getsource(sb.SdkSession.refresh_usage))


if __name__ == "__main__":
    unittest.main()
