#!/usr/bin/env python3
"""The Fleet view gets its per-session `ledgers` even when NO chat client is open (the user 2026-06-29). The
fleet connects as its OWN app (`fleet`) and rides the feed payload; the push builds chat_sessions (and thus
feed["ledgers"]) for want_chat OR want_fleet, and routes the feed payload to feed AND fleet clients. Before
this, the fleet rode app=feed and only got ledgers as a side effect of a chat build — so opening it alone
showed an empty/loading screen until a chat push happened. Source pins on bin/romp-kernel's push + fleet page.
"""
import os
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
SRC = open(os.path.join(os.path.dirname(HERE), "bin", "romp-kernel")).read()


class FleetLedgers(unittest.TestCase):
    def test_fleet_is_its_own_app_in_the_push(self):
        self.assertIn('want_fleet = any(c["app"] in ("fleet", "hive") for c in targets)', SRC)
        # the fleet rides the feed payload, so want_feed must include it
        self.assertIn('want_feed = any(c["app"] in ("feed", "fleet", "hive", "chat") for c in targets)', SRC)

    def test_chat_sessions_are_built_for_a_fleet_only_push(self):
        # the ledger slices come from chat_sessions; build them for want_chat OR want_fleet (not chat-only)
        self.assertIn("if want_chat or want_fleet:", SRC)

    def test_the_feed_payload_routes_to_both_feed_and_fleet_clients(self):
        self.assertIn('if c["app"] in ("feed", "fleet", "hive"):', SRC)

    def test_ledgers_are_attached_from_chat_sessions(self):
        self.assertIn('feed["ledgers"] = [{"sid": m["id"]', SRC)

    def test_the_fleet_page_connects_as_app_fleet(self):
        self.assertIn('_pane_spin("fleet-list"), _shim("fleet", v)', SRC)


if __name__ == "__main__":
    unittest.main()
