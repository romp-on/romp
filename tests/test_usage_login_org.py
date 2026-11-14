"""One Claude account in two organizations is two logins (2026-11).

`claude /login` on an account that belongs to two organizations (a personal plan and a work organization,
say) lands in one of them, and each has its own usage windows and its own bill. The login digest used to
hash the account alone, so a re-login into the other organization kept the digest: usage.json's windows,
written while the first organization was signed in, stayed attributed to the second. A full weekly window
of the first then held every login session's input and gated every login-billed judge call until that
window's reset, though the organization now signed in had no such limit.

The digest now covers the account and its organization; every reader of usage.json's windows honors the
stamp. Synthetic uuids only.
"""
import hashlib
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_loginorg", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_loginorg", os.path.join(BIN, "romp_sdk_backend.py"))
cred = km.jd._cred

ACCOUNT = "11111111-2222-3333-4444-555555555555"
ORG_PERSONAL = "66666666-7777-8888-9999-000000000000"
ORG_WORK = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _oa(org):
    oa = {"accountUuid": ACCOUNT, "emailAddress": "user@example.com"}
    if org:
        oa["organizationUuid"] = org
    return oa


class Digest(unittest.TestCase):
    def test_two_organizations_of_one_account_are_two_logins(self):
        self.assertNotEqual(cred.login_digest(_oa(ORG_PERSONAL)), cred.login_digest(_oa(ORG_WORK)))
        self.assertEqual(len(cred.login_digest(_oa(ORG_WORK))), 12)

    def test_a_login_with_no_organization_keeps_the_account_only_digest(self):
        self.assertEqual(cred.login_digest(_oa(None)), hashlib.sha256(ACCOUNT.encode()).hexdigest()[:12])

    def test_no_account_is_no_digest(self):
        self.assertEqual(cred.login_digest({}), "")
        self.assertEqual(cred.login_digest(None), "")

    def test_the_digest_carries_no_identifier(self):
        d = cred.login_digest(_oa(ORG_WORK))
        self.assertNotIn(ACCOUNT[:8], d)
        self.assertNotIn(ORG_WORK[:8], d)


class _Home(unittest.TestCase):
    """A temp HOME whose ~/.claude.json names the login, and a usage.json in the kernel's state."""
    _t = 1_800_000_000

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self._saved = dict(km._ACCT_CACHE)
        km._ACCT_CACHE["mtime"] = -1.0
        cred._LOGIN_CACHE["mtime"] = -1.0
        self.usage_p = km.jd.STATE / "usage.json"
        self.usage_p.parent.mkdir(parents=True, exist_ok=True)
        self._usage_saved = self.usage_p.read_text() if self.usage_p.exists() else None

    def tearDown(self):
        if self._home is not None:
            os.environ["HOME"] = self._home
        km._ACCT_CACHE.update(self._saved)
        km._ACCT_CACHE["mtime"] = -1.0
        cred._LOGIN_CACHE["mtime"] = -1.0
        if self._usage_saved is None:
            self.usage_p.unlink(missing_ok=True)
        else:
            self.usage_p.write_text(self._usage_saved)

    def sign_in(self, org):
        _Home._t += 1
        p = os.path.join(self.home, ".claude.json")
        with open(p, "w") as f:
            json.dump({"oauthAccount": _oa(org)}, f)
        os.utime(p, (_Home._t, _Home._t))

    def write_full_week(self, stamp_org):
        reset = int(time.time()) + 3 * 86400
        self.usage_p.write_text(json.dumps({
            "t": int(time.time()), "acct": cred.login_digest(_oa(stamp_org)),
            "five_hour": {"pct": 10, "resets_at": reset - 86400},
            "seven_day": {"pct": 100, "resets_at": reset}, "fable": None}))


class AnotherOrganizationsWindows(_Home):
    def test_the_bars_drop_another_organizations_full_week(self):
        self.write_full_week(ORG_PERSONAL)
        self.sign_in(ORG_WORK)
        u = km._usage() or {}
        self.assertIsNone(u.get("sevenDay"), "the personal organization's window is not the work login's")
        self.assertFalse(u.get("limited"))

    def test_the_bars_keep_the_same_organizations_reading(self):
        self.write_full_week(ORG_WORK)
        self.sign_in(ORG_WORK)
        u = km._usage() or {}
        self.assertEqual((u.get("sevenDay") or {}).get("pct"), 100)
        self.assertTrue((u.get("limited") or {}).get("sevenDay"))

    def test_the_input_hold_does_not_park_on_another_organizations_cap(self):
        self.write_full_week(ORG_PERSONAL)
        self.sign_in(ORG_WORK)
        saved = km._launch_error
        try:
            km._launch_error = lambda sid: None
            self.assertIsNone(km._limit_hold("11111111-2222-3333-4444-000000000001"))
        finally:
            km._launch_error = saved

    def test_the_billing_switch_offer_reads_only_this_logins_cap(self):
        self.write_full_week(ORG_PERSONAL)
        self.sign_in(ORG_WORK)
        self.assertIsNone(km._usage_cap_open())
        self.write_full_week(ORG_WORK)
        self.assertEqual((km._usage_cap_open() or {}).get("window"), "seven_day")

    def test_a_judge_give_up_is_not_blamed_on_another_organizations_limit(self):
        self.write_full_week(ORG_PERSONAL)
        self.sign_in(ORG_WORK)
        self.assertFalse(km.jd._giveup_cause()[1])
        self.write_full_week(ORG_WORK)
        self.assertTrue(km.jd._giveup_cause()[1])

    def test_the_judge_rate_gate_reads_the_stamp(self):
        import inspect
        src = inspect.getsource(km.jd)
        gate = src[src.index('u = json.loads((STATE / "usage.json").read_text()) if auth == "login" else {}'):][:400]
        self.assertIn("_cred.usage_is_current_login(u)", gate)


class Stamp(_Home):
    def test_the_writer_and_the_reader_agree_on_the_digest(self):
        self.sign_in(ORG_WORK)
        self.assertEqual(sb.acct_digest(), km._claude_account())
        self.assertEqual(sb.acct_digest(), cred.current_login_digest())

    def test_an_unstamped_legacy_reading_counts_as_current(self):
        self.sign_in(ORG_WORK)
        self.assertTrue(cred.usage_is_current_login({"seven_day": {"pct": 100}}))

    def test_an_unreadable_login_file_cannot_tell_and_counts_as_current(self):
        self.assertTrue(cred.usage_is_current_login({"acct": "aaaaaaaaaaaa"}, current=None))

    def test_a_logout_drops_every_stamped_reading(self):
        self.assertFalse(cred.usage_is_current_login({"acct": "aaaaaaaaaaaa"}, current=""))


if __name__ == "__main__":
    unittest.main()
