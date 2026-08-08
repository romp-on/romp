#!/usr/bin/env python3
"""The notification bells (the user 2026-07-28): a session-level bell (timeline lane / tab menu →
session-flags "notify") and a per-card bell (feed card right-click → notify-cards.json) arm OS-level
notifications, fired by the kernel when an armed card ENTERS needs_input (blocked on you) or completed.
Detection diffs each fresh feed build against the previous one — the exact event the columns move on —
and the first build after a kernel start is a silent baseline (existing state is status, not news).
Synthetic ids/names only."""
import json
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_nb", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd


def _card(iid, sid, col, text="Fix the login flow", **kw):
    d = {"itemId": iid, "sid": sid, "name": "web", "column": col, "text": text}
    d.update(kw)
    return d


def _feed(*cards):
    return {"type": "feed", "asks": [dict(c) for c in cards]}


class NotifyCardStore(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_default_is_empty(self):
        self.assertEqual(km._notify_cards(), {})

    def test_set_get_then_unset_drops_the_entry(self):
        km._set_notify_card("TESTSID:g1", True)
        self.assertEqual(km._notify_cards(), {"TESTSID:g1": True})
        km._set_notify_card("TESTSID:g1", False)
        self.assertEqual(km._notify_cards(), {}, "disarming removes the key, not stores False")

    def test_cache_invalidates_on_write(self):
        self.assertEqual(km._notify_cards(), {})            # primes the (empty) read path
        km._set_notify_card("TESTSID:g1", True)
        self.assertTrue(km._notify_cards().get("TESTSID:g1"), "the (mtime_ns,size) cache key sees the write")

    def test_prune_drops_only_ids_that_left_the_feed(self):
        km._set_notify_card("TESTSID:g1", True)
        km._set_notify_card("TESTSID:g2", True)
        km._prune_notify_cards({"TESTSID:g2"})
        self.assertEqual(km._notify_cards(), {"TESTSID:g2": True})
        # nothing gone → no write (the file's mtime is the feed-cache sig; a no-op must not churn it)
        p = jd.STATE / "notify-cards.json"
        before = p.stat().st_mtime_ns
        km._prune_notify_cards({"TESTSID:g2"})
        self.assertEqual(p.stat().st_mtime_ns, before)


class FeedNotifications(unittest.TestCase):
    """The diff detector: [(title, body)] per armed card newly in needs_input/completed."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        km._NOTIFY_PREV[0] = None

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_the_first_build_is_a_silent_baseline(self):
        km._set_session_flag("TESTSID", "notify", True)
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(out, [], "existing state on start is status, not news (freshNeedsYou policy)")

    def test_a_session_armed_card_entering_needs_input_notifies(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "romp: web")
        self.assertTrue(out[0][1].startswith("Needs you: "), out[0][1])
        self.assertIn("Fix the login flow", out[0][1])
        # the sid rides every notification so a push tap can land ON the session that fired
        # (the user 2026-08-08 — their first real push opened the app on a different session)
        self.assertEqual(out[0][2], "TESTSID")

    def test_an_unarmed_transition_is_silent(self):
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(out, [], "no bell armed → no notification, however the card moves")

    def test_a_card_armed_card_completing_notifies(self):
        km._set_notify_card("TESTSID:g1", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "completed")))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0][1].startswith("Completed: "), out[0][1])

    def test_holding_a_column_does_not_refire(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(out, [], "still blocked is not news — only the ENTRY event notifies")

    def test_reblocking_after_an_answer_notifies_again(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))     # answered
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(out), 1, "a NEW block after the answer is a new event")

    def test_a_card_appearing_already_blocked_notifies(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed())                     # baseline consumed on an empty feed
        out = km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "needs_input")))
        self.assertEqual(len(out), 1, "work can SURFACE blocked — appearing there is entering there")

    def test_a_provisional_placeholder_never_notifies(self):
        km._set_session_flag("TESTSID", "notify", True)
        km._feed_notifications(_feed())
        out = km._feed_notifications(
            _feed(_card("TESTSID:g1", "TESTSID", "needs_input", provisional=True)))
        self.assertEqual(out, [], "placeholder churn is not a stable card")

    def test_an_armed_card_leaving_the_feed_is_pruned(self):
        km._set_notify_card("TESTSID:g1", True)
        km._feed_notifications(_feed(_card("TESTSID:g1", "TESTSID", "working")))
        km._feed_notifications(_feed())                     # cleared/archived → id never comes back
        self.assertEqual(km._notify_cards(), {}, "the store tracks the live feed, not history")


class SystemNotify(unittest.TestCase):
    def test_darwin_shells_out_to_osascript_with_escaped_strings(self):
        calls = []
        saved_popen, saved_platform = km.subprocess.Popen, sys.platform
        km.subprocess.Popen = lambda cmd, **kw: calls.append(cmd)
        sys.platform = "darwin"
        try:
            km._system_notify('romp: web', 'Needs you: fix the "login" flow')
        finally:
            km.subprocess.Popen = saved_popen
            sys.platform = saved_platform
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "osascript")
        self.assertEqual(calls[0][1], "-e")
        self.assertIn('with title "romp: web"', calls[0][2])
        self.assertIn('\\"login\\"', calls[0][2], "quotes are escaped into the AppleScript string")

    def test_a_missing_binary_never_raises(self):
        saved = km.subprocess.Popen

        def boom(cmd, **kw):
            raise OSError("no such binary")
        km.subprocess.Popen = boom
        try:
            km._system_notify("t", "b")                    # must not raise — best-effort by contract
        finally:
            km.subprocess.Popen = saved


class NotifyWiring(unittest.TestCase):
    """Source pins: the flag reaches every payload + the detector rides fresh feed builds."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()

    def test_the_session_flag_rides_both_session_payloads(self):
        # the timeline lane row AND the chat session payload both echo the flag (lane bell + tab menu)
        self.assertEqual(self.src.count('"notify": _session_flag(sid, "notify")'), 2)

    def test_every_ask_carries_its_card_arming(self):
        self.assertIn('_a["notify"] = True if _ncards.get(_a["itemId"]) else None', self.src)

    def test_the_ws_handler_persists_the_card_toggle(self):
        self.assertIn('msg.get("type") == "cardNotify"', self.src)
        self.assertIn('_set_notify_card(str(msg["itemId"]), bool(msg.get("value")))', self.src)

    def test_the_store_mtime_busts_the_feed_cache(self):
        self.assertIn('(jd.STATE / "notify-cards.json", "__ncards__")', self.src,
                      "arming a card must reach the next build, not wait out the sig")

    def test_fresh_feed_builds_drive_the_notifier(self):
        # the detector runs where the fresh build lands — the one choke point every push shares
        # (the sid joined the tuple 2026-08-08 so the push sink can aim its tap-to-open)
        self.assertIn("for _t, _b, _sid in _feed_notifications(feed):", self.src)
        self.assertIn("_system_notify(_t, _b)", self.src)


if __name__ == "__main__":
    unittest.main()
