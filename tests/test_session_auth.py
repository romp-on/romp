#!/usr/bin/env python3
"""Per-session billing (the user 2026-08-08): some sessions on the Claude login, some on the API key.

The mechanics under test:
  * The manager environment's ANTHROPIC_API_KEY is CLAIMED OUT of os.environ once per process
    (work_api_key) — the SDK's transport hands the CLI this process's env wholesale, so an ambient
    key would bill every session regardless of its pick. Injection is explicit per session: _options
    adds the key only when the session's effective auth says so, and a login session launches with a
    genuinely clean env (the CLI treats even an EMPTY var as key-mode-without-a-key and refuses with
    "Not logged in" — verified live 2026-08-08 — so removal is the only correct strip).
  * set_auth mirrors set_effort: persist + authPending + reconnect to apply (auth is connect-time).
  * The CLI's init apiKeySource is compared against what _options actually launched with — a landing
    on the wrong side is a session billing the wrong account, flagged into the problems ring.
  * spend.json buckets carry a `key` sub-count for key-billed turns, so the rail's API readout on a
    mixed host sums ONLY the key's turns (_spend_windows(keyed_only=True)); a login turn's computed
    cost is dollars nobody is billed.
  * An auth failure ("Not logged in", a 401 key) is an ON-YOU api-error class (authErr): retrying
    re-presents the same dead credential forever, so it blocks visibly and is never auto-retried.
  * Availability gates every selector: the kernel offers the choice only when BOTH a signed-in login
    (_claude_account) and a manager-env key exist — one choice is no choice, the control disappears.

Synthetic sids/paths only; no real key material (the fixture key is an invented string).
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ.pop("ROMP_SUPERVISED", None)  # a romp-managed shell inherits it; these tests stage the unsupervised startup-key case
# The manager env file is a LIVE key source now (kernel/keysource.py), so floor it too: without this
# a bare (non-pytest) run of this file on a machine with a real ~/.config/romp/service.env resolves
# the developer's actual key instead of the fixture's. conftest.py holds the same floor for pytest.
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
os.environ.pop("ROMP_API_KEY_REF", None)
sb = SourceFileLoader("romp_sdk_backend_auth", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
km = SourceFileLoader("romp_kernel_auth", os.path.join(BIN, "romp-kernel")).load_module()

FAKE_KEY = "sk-ant-api03-TESTFIXTUREKEYNOTREAL-wxyz"


class _Keyed(unittest.TestCase):
    """Base: a backend whose process env carried the fixture key (the stash is module-global and
    once-per-process, so each test re-arms it explicitly and restores the world after)."""

    KEY = FAKE_KEY

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._env_before = os.environ.pop("ANTHROPIC_API_KEY", None)
        # These classes pin the UNDECLARED launch-intent comparison: a box-wide declaration in the
        # runner's shell (a deployed box exports it, and kernel-spawned sessions inherit it) must
        # not flip the mismatch pins.
        self._exp_auth_before = os.environ.pop("ROMP_EXPECTED_AUTH", None)
        self._stash_before = sb._WORK_KEY
        sb._WORK_KEY = None                       # force a fresh claim from the env
        # the key-account fast-mode probe is a real HTTPS GET — never from a test. Cases that
        # exercise the policy arm their own answers (FastOrgPermissionFollowsBilling).
        self._fetch_before = sb._fetch_key_fast_org
        sb._fetch_key_fast_org = lambda key: None
        sb._FAST_ORG_VERDICTS.clear()
        if self.KEY:
            os.environ["ANTHROPIC_API_KEY"] = self.KEY
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        sb._WORK_KEY = self._stash_before
        sb._fetch_key_fast_org = self._fetch_before
        sb._FAST_ORG_VERDICTS.clear()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if self._env_before is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._env_before
        os.environ.pop("ROMP_EXPECTED_AUTH", None)
        if self._exp_auth_before is not None:
            os.environ["ROMP_EXPECTED_AUTH"] = self._exp_auth_before

    def _sess(self, n=1, **reg):
        return sb.SdkSession(self.be, {"sid": "11111111-2222-3333-4444-%012d" % n,
                                       "name": "s%d" % n, "cwd": "/tmp", **reg})


class WorkKeyStash(_Keyed):
    def test_the_key_is_claimed_out_of_the_environment_exactly_once(self):
        self.assertEqual(self.be.work_key, FAKE_KEY)
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ,
                         "an ambient key would bill EVERY session — the transport merges options.env "
                         "over this process's env, so the strip must happen here")
        # a re-constructed backend (the WS handler's lazy construction, tests) still finds it
        be2 = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        self.assertEqual(be2.work_key, FAKE_KEY)

    def test_an_empty_var_counts_as_no_key(self):
        sb._WORK_KEY = None
        os.environ["ANTHROPIC_API_KEY"] = ""
        self.assertEqual(sb.work_api_key(), "")


class EffectiveAuth(_Keyed):
    def test_explicit_pick_wins_and_unset_preserves_the_ambient_world(self):
        self.assertEqual(self._sess(1, auth="login").effective_auth(), "login")
        self.assertEqual(self._sess(2, auth="key").effective_auth(), "key")
        # unset + a key in the manager env = key, exactly what the pre-selector world did
        self.assertEqual(self._sess(3).effective_auth(), "key")

    def test_a_junk_registry_value_is_ignored(self):
        self.assertEqual(self._sess(4, auth="both-please").auth, "")


class EffectiveAuthKeyless(_Keyed):
    KEY = ""

    def test_an_explicit_key_pick_never_becomes_login_when_no_key_exists(self):
        self.assertEqual(self._sess(1).effective_auth(), "login")
        self.assertEqual(self._sess(2, auth="key").effective_auth(), "key")
        self.assertEqual(self.be.default_auth({"auth": "key"}), "key")


class _OptionsHarness(_Keyed):
    """Base for anything that calls _options directly."""

    def setUp(self):
        super().setUp()
        # ClaudeAgentOptions is a parameter (a dict stands in) and the in-function import only needs
        # HookMatcher — stub the module when the real dependency is absent (CI without the venv), so
        # the one behavior this feature must never get wrong is tested everywhere
        import sys
        import types
        self._fake_sdk = "claude_agent_sdk" not in sys.modules and not sb.sdk_importable()
        if self._fake_sdk:
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: kw
            sys.modules["claude_agent_sdk"] = fake

    def tearDown(self):
        import sys
        if self._fake_sdk:
            sys.modules.pop("claude_agent_sdk", None)
        super().tearDown()

    def _options_kw(self, sess):
        return self.be._options(sess, dict)


class OptionsInjection(_OptionsHarness):
    def test_a_key_session_gets_the_key_and_a_login_session_a_clean_env(self):
        kw = self._options_kw(self._sess(1, auth="key"))
        self.assertEqual(kw["env"].get("ANTHROPIC_API_KEY"), FAKE_KEY)
        kw2 = self._options_kw(self._sess(2, auth="login"))
        self.assertNotIn("ANTHROPIC_API_KEY", kw2["env"],
                         "removal, not blanking: an empty var reads as key-mode-without-a-key")

    def test_options_records_what_it_launched_with_for_the_init_check(self):
        s = self._sess(3, auth="key")
        self._options_kw(s)
        self.assertTrue(s._launched_keyed)
        s2 = self._sess(4, auth="login")
        self._options_kw(s2)
        self.assertFalse(s2._launched_keyed)

    def test_a_key_pick_with_no_key_refuses_to_launch_on_the_login(self):
        self.be.work_key = ""
        with self.assertRaisesRegex(sb._keysrc.KeySourceError, "no API key source"):
            self._options_kw(self._sess(3, auth="key"))


class FastOrgPermissionFollowsBilling(_OptionsHarness):
    """Fast-mode permission follows BILLING (the user 2026-08-14): the CLI's availability probe asks
    the saved claude.ai login whenever one exists, even on a session whose inference bills the
    injected key — so _options asks the paying account itself (key_fast_org_env, fetch stubbed here)
    and hands the CLI the switch matching the answer. Both directions matter: an enabled key account
    skips the CLI's wrong-account probe, a disabled one forces fast mode off."""

    def _env(self, answer, n=1):
        sb._fetch_key_fast_org = lambda key: answer
        return self._options_kw(self._sess(n, auth="key"))["env"]

    def test_an_enabled_key_account_skips_the_clis_wrong_account_probe(self):
        env = self._env(True)
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1")
        self.assertNotIn("CLAUDE_CODE_DISABLE_FAST_MODE", env)
        self.assertEqual(env.get("ANTHROPIC_API_KEY"), FAKE_KEY,
                         "the skip rides WITH the key — same connect, same account")

    def test_a_disabled_key_account_forces_fast_mode_off(self):
        env = self._env(False)
        self.assertEqual(env.get("CLAUDE_CODE_DISABLE_FAST_MODE"), "1")
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", env,
                         "the wrong-account probe could say YES to a fast mode the payer turned off")

    def test_no_answer_and_no_history_leaves_the_cli_default(self):
        env = self._env(None)
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", env)
        self.assertNotIn("CLAUDE_CODE_DISABLE_FAST_MODE", env,
                         "no answer is no licence to skip — the CLI's own check stands")

    def test_a_failure_stands_on_the_last_definitive_answer(self):
        self._env(True, n=1)
        env = self._env(None, n=2)
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1",
                         "a transient network failure must not strip a verified permission")

    def test_a_flip_is_adopted_not_cached_over(self):
        self._env(True, n=1)
        env = self._env(False, n=2)
        self.assertEqual(env.get("CLAUDE_CODE_DISABLE_FAST_MODE"), "1")
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", env)

    def test_login_sessions_never_ask_the_key_account(self):
        calls = []
        sb._fetch_key_fast_org = lambda key: calls.append(key) or True
        env = self._options_kw(self._sess(3, auth="login"))["env"]
        self.assertEqual(calls, [], "a login session's probe already asks the account that pays")
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", env)


class InitMismatchIsLoud(_Keyed):
    def test_a_cli_landing_on_the_wrong_side_reaches_the_problems_ring(self):
        s = self._sess(1, auth="login")
        s._launched_keyed = False
        self.be._note_auth_source(s, "ANTHROPIC_API_KEY")   # found a key some other way (apiKeyHelper…)
        texts = [p["text"] for p in self.be.problems(10)]
        self.assertTrue(any("billing the API key" in t for t in texts),
                        "a session billing the wrong account must never pass silently")

    def test_the_agreeing_init_stays_quiet(self):
        s = self._sess(2, auth="key")
        s._launched_keyed = True
        self.be._note_auth_source(s, "ANTHROPIC_API_KEY")
        self.assertFalse([p for p in self.be.problems(10) if "billing" in p["text"]])


class SetAuth(_Keyed):
    def test_persists_pending_and_seeds_the_next_session(self):
        sid = self.be.spawn("n", "/tmp")
        self.assertTrue(self.be.set_auth(sid, "login"))
        reg = sb.read_reg(self.be.state_dir, sid)
        self.assertEqual(reg["auth"], "login")
        self.assertTrue(reg["authPending"], "the applying reconnect hasn't happened yet — badge dots")
        self.assertEqual(sb.read_sdk_defaults(self.be.state_dir).get("auth"), "login")
        # …and the next spawn seeds from it
        sid2 = self.be.spawn("m", "/tmp")
        self.assertEqual(sb.read_reg(self.be.state_dir, sid2).get("auth"), "login")

    def test_the_picker_pick_beats_the_remembered_default(self):
        sb.write_sdk_default(self.be.state_dir, auth="login")
        sid = self.be.spawn("n", "/tmp", auth="key")
        self.assertEqual(sb.read_reg(self.be.state_dir, sid)["auth"], "key")

    def test_refuses_junk_and_a_key_pick_on_a_keyless_manager(self):
        sid = self.be.spawn("n", "/tmp")
        self.assertFalse(self.be.set_auth(sid, "credit-card"))
        self.be.work_key = ""
        self.assertFalse(self.be.set_auth(sid, "key"),
                         "nothing to inject — refuse rather than half-apply; the UI never offers it")

    def test_a_live_session_reconnects_and_gets_the_ack_chip(self):
        sid = self.be.spawn("n", "/tmp")
        s = self._sess(9)
        s.sid = sid
        called = []
        s.request_reconnect = lambda: called.append(True)
        self.be.sessions[sid] = s
        self.assertTrue(self.be.set_auth(sid, "key"))
        self.assertTrue(called, "auth is connect-time — the reconnect is what applies it")
        self.assertEqual(s.auth, "key")
        self.assertEqual(s._auth_pending, "key")
        chips = [a for a in self.be._live.get(sid, {}).values() if a.get("command") == "/auth"]
        self.assertEqual(len(chips), 1, "an idle session's switch must still show SOMETHING in the chat")

    def test_a_stranded_pending_flag_heals_on_construction(self):
        sid = self.be.spawn("n", "/tmp")
        self.be._update_reg(sid, authPending=True)
        self._sess(1, sid=sid)
        s = sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))
        self.assertFalse(sb.read_reg(self.be.state_dir, sid).get("authPending"),
                         "a fresh construction applies the reg on its next connect — pending is over")

    def test_snapshot_and_dormant_rows_both_carry_the_choice(self):
        sid = self.be.spawn("n", "/tmp", auth="login")
        s = sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))
        self.assertEqual(s.snapshot()["auth"], "login")
        rows = self.be.live_sessions()
        self.assertEqual(rows[sid]["auth"], "login", "a dormant session's gear must read the same truth")


class SpendKeyedSplit(_Keyed):
    def test_key_turns_fold_into_the_key_subcount_and_login_turns_do_not(self):
        self.be._record_spend(1.0, {"input_tokens": 10, "output_tokens": 5}, keyed=True)
        self.be._record_spend(2.0, {"input_tokens": 100}, keyed=False)
        d = json.loads((Path(self.d) / "spend.json").read_text())
        day = d["days"][time.strftime("%Y-%m-%d")]
        self.assertEqual(day["usd"], 3.0, "the total keeps every turn (display gates, the record doesn't)")
        self.assertEqual(day["key"]["usd"], 1.0, "…but the key sub-count holds ONLY the key-billed turn")
        self.assertEqual(day["key"]["turns"], 1)
        self.assertEqual(day["key"]["tok"], 15)

    def test_spend_windows_carry_a_rolling_hour(self):
        # the hover's API-spend section leads with "1 hour" (the user 2026-08-15): the last hour by
        # the same rolling bucket math as day, so a burst shows up without waiting for the day sum
        self.be._record_spend(2.0, {"input_tokens": 10}, keyed=True)
        real_state = km.jd.STATE
        try:
            km.jd.STATE = Path(self.d)
            win = km._spend_windows()
            # …and an old bucket (3h ago) stays out of the hour window while the day keeps it
            import json as _json, time as _time
            sp = _json.loads((Path(self.d) / "spend.json").read_text())
            oldkey = _time.strftime("%Y-%m-%dT%H", _time.localtime(_time.time() - 3 * 3600))
            sp.setdefault("hours", {})[oldkey] = {"usd": 7.0, "turns": 1, "tokIn": 5}
            (Path(self.d) / "spend.json").write_text(_json.dumps(sp))
            win2 = km._spend_windows()
        finally:
            km.jd.STATE = real_state
        self.assertEqual(win["hour"]["usd"], 2.0)
        self.assertEqual(win2["hour"]["usd"], 2.0, "a 3h-old bucket is outside the rolling hour")
        self.assertEqual(win2["day"]["usd"], 9.0, "…but inside the rolling day")

    def test_spend_windows_keyed_only_reads_the_subcounts(self):
        self.be._record_spend(1.5, {"input_tokens": 10}, keyed=True)
        self.be._record_spend(4.0, None, keyed=False)
        real_state = km.jd.STATE
        try:
            km.jd.STATE = Path(self.d)
            total = km._spend_windows()
            keyed = km._spend_windows(keyed_only=True)
        finally:
            km.jd.STATE = real_state
        self.assertEqual(total["month"]["usd"], 5.5)
        self.assertEqual(keyed["month"]["usd"], 1.5,
                         "a login turn's computed cost is dollars nobody is billed — never in the API sum")
        self.assertEqual(keyed["month"]["turns"], 1)


class UsagePayloadMixed(unittest.TestCase):
    """_usage() attaches the keyed spend BESIDE the login's bars — only when key turns actually
    exist, so a host that never uses its key shows nothing extra. No key material rides along:
    the API label is constant, so the payload carries no tail (the user 2026-08-08, evening)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.real_state = km.jd.STATE
        km.jd.STATE = Path(self.d)
        self.real_key = km._auth_key_present
        (Path(self.d) / "usage.json").write_text(json.dumps(
            {"t": 1000, "five_hour": {"pct": 40, "resets_at": None}}))   # unstamped = legacy, keeps bars

    def tearDown(self):
        km.jd.STATE = self.real_state
        km._auth_key_present = self.real_key

    def _spend(self, keyed_turns):
        day = time.strftime("%Y-%m-%d")
        hour = time.strftime("%Y-%m-%dT%H")
        b = {"usd": 2.0, "turns": 3, "tokIn": 1, "tokOut": 1, "tokCacheR": 0, "tokCacheW": 0}
        if keyed_turns:
            b["key"] = {"usd": 0.5, "turns": keyed_turns, "tok": 7}
        (Path(self.d) / "spend.json").write_text(json.dumps({"days": {day: b}, "hours": {hour: b}}))

    def test_bars_carry_the_keyed_spend_when_key_turns_exist(self):
        km._auth_key_present = lambda: True
        self._spend(keyed_turns=2)
        u = km._usage()
        self.assertTrue(u["fiveHour"], "the login's bars stay the headline")
        self.assertEqual(u["spend"]["month"]["usd"], 0.5, "keyed-only, never the total")
        self.assertNotIn("apiTail", u, "no key fragment travels — the rail's label is the constant 'API'")

    def test_no_key_turns_or_no_key_means_no_spend_beside_the_bars(self):
        km._auth_key_present = lambda: True
        self._spend(keyed_turns=0)
        self.assertNotIn("spend", km._usage())
        km._auth_key_present = lambda: False
        self._spend(keyed_turns=2)
        self.assertNotIn("spend", km._usage())

    def test_the_spend_only_arm_carries_no_key_material_either(self):
        km._auth_key_present = lambda: True
        (Path(self.d) / "usage.json").write_text(json.dumps({"t": 1000, "apiKey": True}))
        self._spend(keyed_turns=0)
        u = km._usage()
        self.assertTrue(u["apiKey"])
        self.assertNotIn("apiTail", u)


class AuthErrorClass(unittest.TestCase):
    def test_the_cli_and_api_phrasings_classify_and_transients_do_not(self):
        for text in ("Not logged in · Please run /login",
                     "Failed to authenticate. API Error: 401 API key is invalid.",
                     "invalid x-api-key",
                     "OAuth token has expired",
                     'API Error: 401 {"type":"error","error":{"type":"authentication_error"}}'):
            self.assertTrue(km._is_auth_error(text), text)
        for text in ("500 server_error", "Request timed out", "prompt is too long",
                     "You've reached your Fable 5 limit", ""):
            self.assertFalse(km._is_auth_error(text), text)

    def test_it_is_an_on_you_class_end_to_end(self):
        import inspect
        # the classification lives in _api_error_scan since the tail-window split; _api_error is the
        # widening driver around it, so read the pair rather than pinning which half holds the flag
        self.assertIn('"authErr": _is_auth_error(text)',
                      inspect.getsource(km._api_error) + inspect.getsource(km._api_error_scan))
        self.assertIn('"apiAuthErr": bool(aerr and aerr.get("authErr"))', inspect.getsource(km.build_session))
        feed = inspect.getsource(km.build_feed)
        self.assertIn('aerr.get("authErr") or aerr.get("refusal"))))', feed, "the card floors to needs-you")
        self.assertIn("sign-in or API key isn't working", feed, "the card names the real remedy")


class Availability(unittest.TestCase):
    """The selector exists only when BOTH choices are real — everywhere it could appear."""

    def setUp(self):
        self.real_sdk, self.real_acct, self.real_label = km._sdk, km._claude_account, km._claude_account_label

    def tearDown(self):
        km._sdk, km._claude_account, km._claude_account_label = self.real_sdk, self.real_acct, self.real_label

    def _world(self, key, acct, label="user@example.com"):
        km._sdk = lambda: type("B", (), {"work_key_configured": bool(key)})()
        km._claude_account = lambda: acct
        km._claude_account_label = lambda: (label if acct else "")

    def test_both_gates_the_selector_and_no_key_material_travels(self):
        self._world(FAKE_KEY, "aaaaaaaaaaaa")
        self.assertTrue(km._auth_both())
        self.assertTrue(km._auth_key_present())
        a = km._auth_avail()
        self.assertEqual((a["login"], a["key"]), (True, True))
        # the login is NAMED, so 'Login' can say which account it means (the user 2026-08-09)
        self.assertEqual(a["acct"], "user@example.com")
        # No fragment of the key leaves the kernel — not the key, not even its last-4 tail
        # (the user 2026-08-08, evening: a tail is still key material; 'API key' is label enough,
        # and host names already tell keys apart in the per-host hover).
        self.assertNotIn("tail", a)
        self.assertNotIn(FAKE_KEY, json.dumps(a), "the key itself never travels")
        self.assertNotIn("wxyz", json.dumps(a), "…and neither does any substring of it")

    def test_one_choice_still_reports_availability_for_the_written_out_row(self):
        # One real choice hides the CONTROLS, but the picker row still WRITES OUT which auth applies
        # (the user 2026-08-09) — so availability itself is always reported, only _auth_both flips.
        self._world("", "aaaaaaaaaaaa")
        self.assertFalse(km._auth_both())
        a = km._auth_avail()
        self.assertEqual((a["login"], a["key"], a["acct"]), (True, False, "user@example.com"))
        self._world(FAKE_KEY, "")
        self.assertFalse(km._auth_both())
        a = km._auth_avail()
        self.assertEqual((a["login"], a["key"], a["acct"]), (False, True, ""))

    def test_the_session_payload_always_carries_auth_and_gates_only_the_controls(self):
        import inspect
        src = inspect.getsource(km.build_session)
        # auth rides ungated (the user 2026-08-09: the tab hover says Billing on one-auth machines
        # too); authBoth is the separate gate the CONTROLS key on; authAcct names the login.
        self.assertIn('"auth": tm.get("auth", "")', src)
        self.assertIn('"authBoth": _auth_both()', src)
        self.assertIn('"authAcct": _claude_account_label()', src)
        self.assertNotIn("authTail", src, "the status payload carries the choice, never key material")

    def test_the_label_reads_the_oauth_account_and_misses_quietly(self):
        # The label comes from the CLI's own store (~/.claude.json oauthAccount): emailAddress first,
        # displayName as the fallback, "" when nothing is signed in — never an exception.
        real_home = os.environ.get("HOME")
        home = tempfile.mkdtemp()
        os.environ["HOME"] = home
        try:
            km._ACCT_CACHE["mtime"] = -2.0   # bust the mtime cache; the path just changed under it
            self.assertEqual(km._claude_account_label(), "", "no file → no label, no error")
            p = Path(home) / ".claude.json"
            p.write_text(json.dumps({"oauthAccount": {"accountUuid": "11111111-2222-3333-4444-555555555555",
                                                      "emailAddress": "user@example.com",
                                                      "displayName": "A User"}}))
            km._ACCT_CACHE["mtime"] = -2.0
            self.assertEqual(km._claude_account_label(), "user@example.com")
            self.assertTrue(km._claude_account(), "the digest still reads beside the label")
            p.write_text(json.dumps({"oauthAccount": {"accountUuid": "11111111-2222-3333-4444-555555555555",
                                                      "displayName": "A User"}}))
            km._ACCT_CACHE["mtime"] = -2.0
            self.assertEqual(km._claude_account_label(), "A User", "displayName is the fallback")
        finally:
            if real_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = real_home
            km._ACCT_CACHE["mtime"] = -2.0   # never leak the temp world into later tests

    def test_the_picker_learns_availability_on_the_session_list(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"authAvail": _auth_avail()', src)


class SwitchCycleTruthTable(_Keyed):
    """T124 (2026-08-27, the user's key->login switch where 'the UI is not what is happening'):
    the full switch cycle, every landing shape, asserted at the session state dict — the exact
    fields the Billing row renders from (auth/authLive/authPending; the render mapping is pinned
    in ui/webview/auth-selector.test.ts). The contract: intent shows AS PENDING intent through the
    reconnect window (never as applied fact), a confirmed contradiction is visible, and a pick the
    box cannot apply refuses at pick time."""

    def _state(self, s):
        d = s.snapshot()
        return (d["auth"], d["authLive"], d["authPending"])

    def test_the_cycle_on_a_logged_in_box(self):
        self.be.login_ok = lambda: True                      # the simulated logged-in box
        sid = "11111111-2222-3333-4444-00000000t124"
        sb.write_reg(Path(self.d), sid, {"sid": sid, "name": "misc", "cwd": "/tmp"})
        s = self._sess(41, sid=sid)
        self.be.sessions[sid] = s
        # rest: keyed (the env key), confirmed by an init
        self.be._note_auth_source(s, "ANTHROPIC_API_KEY")
        self.assertEqual(self._state(s), ("key", "key", False), "rest: confirmed key, row says API key")
        # SWITCH key->login: the pending window renders as pending — never applied fact
        self.assertTrue(self.be.set_auth(sid, "login"))
        self.assertEqual(self._state(s), ("login", "", True),
                         "the pick shows as PENDING intent (authLive cleared, authPending up) until an init confirms")
        # landing shape 1: the CLI confirms the login → truth within one init
        s._auth_pending = ""                                  # the connect clears the dots (event-based)
        self.be._note_auth_source(s, "none")
        self.assertEqual(self._state(s), ("login", "login", False), "confirmed login — plain Login")
        # SWITCH login->key, landing shape 2: the CLI lands on the WRONG side (an apiKeyHelper world:
        # picked login again later, but a helper re-injects the key) — the contradiction is VISIBLE
        self.assertTrue(self.be.set_auth(sid, "key"))
        s._launched_keyed = True                              # the applying reconnect injected the key (_options)
        s._auth_pending = ""
        self.be._note_auth_source(s, "none")                  # picked key; the CLI reports login
        auth, live, pending = self._state(s)
        self.assertEqual((auth, pending), ("key", False))
        self.assertEqual(live, "login", "the wrong-side landing rides authLive → the row's ⚠ shape")
        self.assertTrue(any("billing the login" in p["text"] for p in self.be.problems(20)),
                        "…and the problems ring names it")

    def test_the_cycle_on_a_login_less_box(self):
        self.be.login_ok = lambda: False                     # THIS devbox's real shape (env-key-only)
        sid = "11111111-2222-3333-4444-00000000t125"
        sb.write_reg(Path(self.d), sid, {"sid": sid, "name": "misc2", "cwd": "/tmp"})
        s = self._sess(42, sid=sid)
        self.be.sessions[sid] = s
        self.be._note_auth_source(s, "ANTHROPIC_API_KEY")
        self.assertFalse(self.be.set_auth(sid, "login"),
                         "the pick the box cannot apply refuses AT PICK TIME — never accept-then-fail")
        self.assertEqual(self._state(s), ("key", "key", False),
                         "nothing moved: no pending window, no intent shown, the row keeps the truth")


class DrivePlumbing(unittest.TestCase):
    """setAuth rides the same drive/park path as the other per-session switches (source pins)."""

    def test_the_op_is_routed_parked_and_replayed(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"setAuth", "endSession"', src.replace("\n", " "), "an ID_OPS member")
        self.assertIn('elif t == "setAuth" and msg.get("value") in ("login", "key"):', src)
        self.assertIn("def _set_auth_or_park(be, sid, value):", src)
        self.assertIn('_park_op(sid, ("auth", value))', src)
        self.assertIn('elif op[0] == "auth":', src)
        self.assertIn("be.set_auth(sid, op[1])", src)

    def test_create_paths_pass_the_pick_through(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("def _create_sdk_session(nm, cwd, auth=\"\", prefs=None, client=None, env=None):", src)
        self.assertEqual(src.count('auth=(a if a in ("login", "key") else "")'), 2,
                         "the WS op and POST /new both pass it")

    def test_the_abc_names_the_control_and_tmux_refuses(self):
        sbc = open(os.path.join(os.path.dirname(HERE), "kernel", "session_backend.py")).read()
        self.assertIn("def set_auth(self, sid: str, value: str) -> bool:", sbc)
        self.assertIn("return False", sbc.split("def set_auth", 1)[1][:900])


if __name__ == "__main__":
    unittest.main()
