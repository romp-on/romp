#!/usr/bin/env python3
"""The cap-death billing-switch OFFER (the user's binding ruling, 2026-08-30: a session must NEVER
silently switch billing in either direction — romp offers, only the explicit pick switches).
_cap_switch_offer mints only when every leg holds: a plain retryable API error (the on-you classes
carry their own remedies), a login-account window at its cap with a readable reset ahead, THIS
session billing the login, and a key on hand to offer. Self-expires with the window. The pick's one
path is the setAuth route a gear billing pick takes — pinned by census below. Synthetic fixtures."""
import json
import os
import re
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_capoffer", os.path.join(BIN, "romp-kernel"))

SID = "aaaa3008-0000-0000-0000-000000000001"
ERR = {"text": "API Error 529 overloaded", "status": 529}


class CapSwitchOffer(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.jd.STATE, km._live_map, km._auth_key_present)
        km.jd.STATE = Path(self.td.name)
        km._live_map = lambda: {SID: {"authLive": "login", "auth": "key"}}
        km._auth_key_present = lambda: True
        self._cap(100, 3600)

    def tearDown(self):
        (km.jd.STATE, km._live_map, km._auth_key_present) = self._saved
        self.td.cleanup()

    def _cap(self, pct, resets_in, bucket="five_hour"):
        (km.jd.STATE / "usage.json").write_text(json.dumps(
            {bucket: {"pct": pct, "resets_at": int(time.time()) + resets_in}}))

    def test_minted_for_a_login_billed_cap_death(self):
        v = km._cap_switch_offer(SID, ERR)
        self.assertEqual(v["window"], "five_hour")
        self.assertGreater(v["resetsAt"], time.time())

    def test_never_for_a_key_billed_session(self):
        km._live_map = lambda: {SID: {"authLive": "key", "auth": "login"}}
        self.assertIsNone(km._cap_switch_offer(SID, ERR),
                          "a key-billed error is not a cap death — nothing to offer")

    def test_never_for_the_on_you_error_classes(self):
        for k in ("tooLong", "spendLimit", "modelLimit", "authErr", "refusal"):
            self.assertIsNone(km._cap_switch_offer(SID, dict(ERR, **{k: True})),
                              k + " carries its own remedy — no billing offer rides it")

    def test_never_without_a_key_to_offer(self):
        km._auth_key_present = lambda: False
        self.assertIsNone(km._cap_switch_offer(SID, ERR))

    def test_never_when_no_window_is_capped(self):
        self._cap(90, 3600)
        self.assertIsNone(km._cap_switch_offer(SID, ERR))

    def test_retires_with_the_window(self):
        self._cap(100, -5)                            # resets_at already passed — the deciding event
        self.assertIsNone(km._cap_switch_offer(SID, ERR))

    def test_no_path_flips_billing_without_the_explicit_pick_both_directions(self):
        # CENSUS PIN: the ONLY set_auth call sites in the kernel are the setAuth route's helper and
        # the parked-op replay of that same user pick — no auto path, either direction. A new caller
        # lands here first.
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read_text()
        sites = [l.strip() for l in src.splitlines() if re.search(r"\bbe\.set_auth\(", l)]
        self.assertEqual(len(sites), 2, "exactly the helper + the parked replay: %r" % sites)
        self.assertTrue(any("return be.set_auth(sid, value)" in l for l in sites))
        self.assertTrue(any("be.set_auth(sid, op[1])" in l for l in sites))
        self.assertIn('elif t == "setAuth" and lg.parse_pick(msg.get("value"))[0]:', src,
                      "the route is a user gesture, and the ONLY door (T346: 'login' | 'key' | 'login:<id>')")
        self.assertNotIn("set_auth", src[src.index("def _cap_switch_offer"):
                                         src.index("def _judge_limit_view")],
                         "the offer itself never switches anything")


if __name__ == "__main__":
    unittest.main()
