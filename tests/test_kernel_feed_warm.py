"""build_feed is CACHE-ONLY on a cold start, and its background parse-warmer must NOT compete with the chat.

The feed's CARDS come from the goal store (cheap); the working-dots / deep-link anchors / API-error+awaiting
badges / provisional card read the transcript parse ONLY if it's already cached (_parse_cached), so the cards
paint at once on a cold kernel start (the user 2026-06-26). The dedicated warmer (_warm_fleet_bg) fills the
cache for a FEED-ONLY window — but a chat or timeline client already parses the same fleet into the same
cache, so the warmer must skip then, or it steals GIL from the chat's active-tab reshape on a cold restart.
"""
import inspect
import os
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class FeedCacheOnly(unittest.TestCase):
    def test_build_feed_reads_the_parse_cache_only_never_a_cold_parse(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn("ps = _parse_cached(s[\"path\"])", src, "the working-dot reads the CACHED parse, no cold parse")
        self.assertIn("cold_parse = True", src)
        self.assertIn("_warm_fleet_bg(now)", src, "an unparsed living session kicks the background warmer")
        # the parse-derived enrichments are all gated on `ps` (cached) so the cold first paint is just cards
        self.assertIn("if (ps and not who_working) else None", src)        # API-error floor
        self.assertIn("if ps else None", src)                              # awaiting badge
        self.assertIn("if not had_working and perm_top is None and ps:", src)   # provisional card


class WarmerDoesNotCompeteWithChat(unittest.TestCase):
    def setUp(self):
        self._saved = list(km._clients)
        km._warming[0] = False

    def tearDown(self):
        with km._clients_lock:
            km._clients[:] = self._saved
        km._warming[0] = False

    def _set_clients(self, apps):
        with km._clients_lock:
            km._clients[:] = [{"app": a, "send": lambda s: None, "sent": {}, "alive": True} for a in apps]

    def test_has_parsing_client_is_true_for_chat_or_timeline_only(self):
        self._set_clients(["feed"])
        self.assertFalse(km._has_parsing_client(), "a feed-only window has no parser of the fleet")
        self._set_clients(["feed", "chat"])
        self.assertTrue(km._has_parsing_client())
        self._set_clients(["timeline"])
        self.assertTrue(km._has_parsing_client())

    def test_warmer_is_a_no_op_when_a_chat_client_is_connected(self):
        self._set_clients(["feed", "chat"])
        km._warm_fleet_bg(0)
        self.assertFalse(km._warming[0], "the warmer must NOT start while the chat parses the fleet itself")

    def test_warmer_is_a_no_op_with_no_clients(self):
        self._set_clients([])
        km._warm_fleet_bg(0)
        self.assertFalse(km._warming[0])


if __name__ == "__main__":
    unittest.main()
