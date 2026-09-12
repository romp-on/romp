#!/usr/bin/env python3
"""Per-session billing (the user 2026-08-08): some sessions on the Claude login, some on the API key.

The mechanics under test:
  * romp holds no API key (2026-09-08): the key side of the pick is Claude Code's own apiKeyHelper,
    read from its settings and never run for a launch. _options injects no key for any pick; a LOGIN
    pick disables the box's helper for that one process through the per-session settings layer
    ("apiKeyHelper": "", the value the CLI takes as unset, verified on 2.1.257), because in the CLI's
    precedence the helper outranks every login form. A launched session's environment never carries
    ANTHROPIC_API_KEY.
  * set_auth mirrors set_effort: persist + authPending + reconnect to apply (auth is connect-time).
  * The CLI's init apiKeySource is compared against what _options actually launched with — a landing
    on the wrong side is a session billing the wrong account, flagged into the problems ring.
  * spend.json buckets carry a `key` sub-count for key-billed turns, so the rail's API readout on a
    mixed host sums ONLY the key's turns (_spend_windows(keyed_only=True)); a login turn's computed
    cost is dollars nobody is billed.
  * An auth failure ("Not logged in", a 401 key) is an ON-YOU api-error class (authErr): retrying
    re-presents the same dead credential forever, so it blocks visibly and is never auto-retried.
  * Availability is REPORTED, never a gate (the user 2026-09-08: the picker never disappears): the
    status payload carries authAvail with the reason for any side the box cannot bill, the Billing menu
    lists both and greys that one, and a pick the box cannot bill falls at launch to the side that
    exists (a stale login pick on a box with no login launches keyed; a key pick on a helper-less box
    with a login launches on the login), said once per session, with authPickUnavailable in status.

Synthetic sids/paths only; the fixture helper prints an invented string no validator would take for a key.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ.pop("ROMP_SUPERVISED", None)  # a romp-managed shell inherits it; these tests stage the unsupervised startup-key case
# The kernel's boot check reads the manager env file, and the helper lives in Claude Code's settings:
# floor both, so a bare (non-pytest) run on a configured box reads neither the developer's service.env
# nor their real settings. conftest.py holds the same floors for pytest.
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
for _n in ("ANTHROPIC_API_KEY", "ROMP_API_KEY_CMD", "ROMP_API_KEY_REF"):
    os.environ.pop(_n, None)
sb = load_source("romp_sdk_backend_auth", os.path.join(BIN, "romp_sdk_backend.py"))
km = load_source("romp_kernel_auth", os.path.join(BIN, "romp-kernel"))

FAKE_KEY = "synthetic-helper-output-auth"           # what the fixture helper prints: not key-shaped on purpose


def _stage_helper(cfg, out=FAKE_KEY):
    """A fixture apiKeyHelper in a synthetic user settings.json under `cfg`: read for availability, run
    only by the kernel-side probes that ask for the key."""
    script = Path(cfg) / "helper.sh"
    script.write_text("#!/bin/sh\necho '%s'\n" % out)
    script.chmod(0o700)
    (Path(cfg) / "settings.json").write_text(json.dumps({"apiKeyHelper": str(script)}))


class _Keyed(unittest.TestCase):
    """Base: a backend on a box whose Claude Code settings carry a fixture apiKeyHelper (KEY truthy), or
    none (KEY ""). The settings live in a per-test CLAUDE_CONFIG_DIR, so no test reads a real one."""

    KEY = FAKE_KEY

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        # These classes pin the UNDECLARED launch-intent comparison: a box-wide declaration in the
        # runner's shell (a deployed box exports it, and kernel-spawned sessions inherit it) must
        # not flip the mismatch pins.
        self._exp_auth_before = os.environ.pop("ROMP_EXPECTED_AUTH", None)
        # the key-account fast-mode probe is a real HTTPS GET — never from a test. Cases that
        # exercise the policy arm their own answers (FastOrgPermissionFollowsBilling).
        self._fetch_before = sb._fetch_key_fast_org
        sb._fetch_key_fast_org = lambda key: None
        sb._FAST_ORG_VERDICTS.clear()
        sb._cred.forget_helper_key()
        self._managed_before = sb._cred.managed_settings_path         # a bare run must not read the box's managed file
        sb._cred.managed_settings_path = lambda: os.path.join(self.cfg, "no-managed-settings.json")
        self._tokens_before = sb._STARTUP_AUTH_ENV
        sb._STARTUP_AUTH_ENV = {}                                     # no login token claimed unless a test stages one
        if self.KEY:
            _stage_helper(self.cfg, self.KEY)
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        sb._fetch_key_fast_org = self._fetch_before
        sb._FAST_ORG_VERDICTS.clear()
        sb._cred.forget_helper_key()
        sb._cred.managed_settings_path = self._managed_before
        sb._STARTUP_AUTH_ENV = self._tokens_before
        os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before
        os.environ.pop("ROMP_EXPECTED_AUTH", None)
        if self._exp_auth_before is not None:
            os.environ["ROMP_EXPECTED_AUTH"] = self._exp_auth_before

    def _no_helper(self):
        """Remove the fixture helper: the box now has no key side."""
        try:
            os.unlink(os.path.join(self.cfg, "settings.json"))
        except OSError:
            pass
        sb._cred.forget_helper_key()

    def _settings_of(self, kw):
        """The per-session settings payload a launch handed the CLI, {} when none."""
        p = kw.get("settings")
        return json.loads(Path(p).read_text()) if p else {}

    def _sess(self, n=1, **reg):
        return sb.SdkSession(self.be, {"sid": "11111111-2222-3333-4444-%012d" % n,
                                       "name": "s%d" % n, "cwd": "/tmp", **reg})


class HelperAvailability(_Keyed):
    """The key side exists exactly when Claude Code's settings carry a helper; romp never holds the value."""

    def test_a_configured_helper_makes_the_key_side_available_without_running_it(self):
        self.assertTrue(self.be.key_available)
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ, "no key ever enters this process's environment")
        self.assertFalse(hasattr(self.be, "work_key"), "the backend holds no key attribute at all")

    def test_no_helper_means_no_key_side(self):
        self._no_helper()
        self.assertFalse(self.be.key_available)
        self.assertEqual(self.be.default_auth({}), "login")


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
            # an attribute-bearing stand-in, like the SDK's dataclass: with hosts on (the default since T348) the
            # options loop sets each matcher's `timeout` to the host's hook bound, which a plain dict refused
            fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
            sys.modules["claude_agent_sdk"] = fake

    def tearDown(self):
        import sys
        if self._fake_sdk:
            sys.modules.pop("claude_agent_sdk", None)
        super().tearDown()

    def _options_kw(self, sess):
        return self.be._options(sess, dict)


class OptionsInjection(_OptionsHarness):
    def test_no_pick_ever_puts_a_key_in_the_environment_and_a_login_pick_disables_the_helper(self):
        kw = self._options_kw(self._sess(1, auth="key"))
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"], "a key pick launches plain: the CLI runs the helper itself")
        self.assertNotIn("apiKeyHelper", self._settings_of(kw), "and nothing disables it")
        kw2 = self._options_kw(self._sess(2, auth="login"))
        self.assertNotIn("ANTHROPIC_API_KEY", kw2["env"])
        self.assertEqual(self._settings_of(kw2).get("apiKeyHelper"), "",
                         "the login pick disables the box's helper for this one process (the CLI's precedence "
                         "puts the helper above every login form)")
        kw3 = self._options_kw(self._sess(3))
        self.assertNotIn("ANTHROPIC_API_KEY", kw3["env"], "unpicked: the CLI decides, romp injects nothing")
        self.assertNotIn("apiKeyHelper", self._settings_of(kw3))

    def test_options_records_what_it_launched_with_for_the_init_check(self):
        s = self._sess(3, auth="key")
        self._options_kw(s)
        self.assertTrue(s._launched_keyed)
        s2 = self._sess(4, auth="login")
        self._options_kw(s2)
        self.assertFalse(s2._launched_keyed)

    def test_the_claimed_login_tokens_ride_every_launch_that_bills_the_login(self):
        """The kernel claims ANTHROPIC_AUTH_TOKEN and CLAUDE_CODE_OAUTH_TOKEN out of its environment at boot;
        they ride a login pick AND an unpicked session on a box with no helper (its billing IS the login, and
        the judges' login path restores the same tokens), never a key-billed launch (a bearer outranks the
        helper). Review 2026-09-08: the first cut restored them for the explicit pick only."""
        sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        kw = self._options_kw(self._sess(1, auth="login"))
        self.assertEqual(kw["env"].get("CLAUDE_CODE_OAUTH_TOKEN"), "synthetic-login-token", "a login pick")
        kw = self._options_kw(self._sess(2))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", kw["env"], "unpicked on a helper box: the key, no bearer beside it")
        kw = self._options_kw(self._sess(3, auth="key"))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", kw["env"], "a key pick")
        self._no_helper()
        kw = self._options_kw(self._sess(4))
        self.assertEqual(kw["env"].get("CLAUDE_CODE_OAUTH_TOKEN"), "synthetic-login-token",
                         "unpicked on a helper-less box: the login is what it bills, so its token rides")
        kw = self._options_kw(self._sess(5, auth="key"))
        self.assertEqual(kw["env"].get("CLAUDE_CODE_OAUTH_TOKEN"), "synthetic-login-token",
                         "an explicit key pick with no helper falls to the login this box HAS (2026-09-08), so "
                         "its token rides like any login launch")
        self.be.login_ok = lambda: False
        kw = self._options_kw(self._sess(6, auth="key"))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", kw["env"], "…and with no login either, the CLI decides: no bearer")
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])

    def test_a_login_pick_cannot_apply_under_a_managed_helper_and_says_so(self):
        """A managed helper outranks the per-session layer in the CLI's precedence, so a login pick could not
        disable it: set_auth refuses with the reason, and a pick that predates the managed helper launches
        KEYED (no suppression written, the launch honest about its side) and is said once per session, never
        billed quietly (review 2026-09-08; per-session since the one-auth picker work the same day)."""
        managed = os.path.join(self.cfg, "managed.json")
        Path(managed).write_text(json.dumps({"apiKeyHelper": os.path.join(self.cfg, "helper.sh")}))
        sb._cred.managed_settings_path = lambda: managed
        sid = self.be.spawn("n", "/tmp")
        self.assertFalse(self.be.set_auth(sid, "login"))
        self.assertEqual(self.be.last_auth_refusal, sb._cred.WHY_MANAGED_HELPER)
        rows = [p["text"] for p in self.be.problems(10) if "managed settings" in p["text"]]
        self.assertEqual(len(rows), 1, "refused with the reason, in the problem ring")
        s6, s7 = self._sess(6, auth="login"), self._sess(7, auth="login")
        kw = self._options_kw(s6)
        self.assertNotIn("apiKeyHelper", self._settings_of(kw), "the suppression could not apply: none written")
        self.assertTrue(s6._launched_keyed, "the launch records the side it actually took")
        self._options_kw(s6)   # a reconnect
        self._options_kw(s7)
        rows = [p["text"] for p in self.be.problems(20)
                if "billing pick 'login' cannot apply" in p["text"] and "billing the API key" in p["text"]]
        self.assertEqual(len(rows), 2, "said once per SESSION at launch, not per reconnect")
        self.assertIn("managed settings", rows[0])

    def test_a_key_pick_with_no_helper_anywhere_leaves_the_cli_to_decide(self):
        """An explicit key pick on a box with no helper AND no login launches plain and records that it MEANT
        the key (_launched_unkeyed_pick), so a login landing rings in the per-init check as the pick
        contradicted. (With a login present the pick falls to it instead: PickFallsToTheAvailableSide.)"""
        self._no_helper()
        self.be.login_ok = lambda: False
        s = self._sess(3, auth="key")
        kw = self._options_kw(s)
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"], "nothing injected, not an empty var either")
        self.assertFalse(s._launched_keyed)
        self.assertTrue(s._launched_unkeyed_pick)
        self.assertEqual(s._pick_fell_said, "", "nothing to fall to: no fall, no notice")


class UnpickedFollowsTheExplicitDefaultAtLaunch(_OptionsHarness):
    def test_the_launch_suppresses_the_helper_for_an_unpicked_session_once_the_default_is_login(self):
        """The round-2 review: the explicit default reached an unpicked session's STATUS but not its LAUNCH (the
        options read sess.auth, empty when unpicked, so the helper ran and the turn billed the key while the flyout
        said Login). The launch now resolves the side by the status's rule."""
        s = self._sess(7)                                        # minted before any default: no pick of its own
        kw0 = self._options_kw(s)
        self.assertNotIn("apiKeyHelper", self._settings_of(kw0), "no default: the launch stays plain, the CLI decides")
        self.assertTrue(s._launched_keyed, "…and on a helper box that means the key")
        self.assertTrue(self.be.set_auth_default("login"))
        kw = self._options_kw(s)
        self.assertEqual(self._settings_of(kw).get("apiKeyHelper"), "", "the explicit login default suppresses the helper for the next connect")
        self.assertFalse(s._launched_keyed, "the launch means the login, as the status says")
        self.assertEqual(s.effective_auth(), "login")
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])
        self.assertFalse([p for p in self.be.problems(10) if "cannot apply" in p["text"]], "following a default is no fall: no problem row")
        self.assertTrue(self.be.set_auth_default("key"))
        kw2 = self._options_kw(s)
        self.assertNotIn("apiKeyHelper", self._settings_of(kw2), "an explicit key default: the helper runs")
        self.assertTrue(s._launched_keyed)
        self.assertTrue(self.be.set_auth_default("auto"))
        kw3 = self._options_kw(s)
        self.assertNotIn("apiKeyHelper", self._settings_of(kw3), "automatic again: plain")
        # a session with its own pick is untouched by the default
        p = self._sess(8, auth="login")
        self.assertTrue(self.be.set_auth_default("key"))
        self.assertEqual(self._settings_of(self._options_kw(p)).get("apiKeyHelper"), "", "its own login pick still suppresses the helper")


class PickFallsToTheAvailableSide(_OptionsHarness):
    """The user 2026-09-08: no login on the box means everything bills the key, never a dead login — and
    the mirror. A pick this box cannot bill launches on the side it can, says so once per session in the
    problem ring, and stays the PICK in status with authPickUnavailable beside it. A box with both keeps
    every pre-existing launch shape (the pins in OptionsInjection run on that box)."""

    def test_a_stale_login_pick_on_a_box_with_no_login_launches_keyed_and_says_so_once(self):
        self.be.login_ok = lambda: False
        s = self._sess(1, auth="login")
        kw = self._options_kw(s)
        self.assertNotIn("apiKeyHelper", self._settings_of(kw), "no helper suppression: the helper runs, the key bills")
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])
        self.assertTrue(s._launched_keyed, "the per-init check expects the keyed landing: no false alarm")
        self.assertFalse(s._launched_unkeyed_pick)
        sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        kw = self._options_kw(s)   # the reconnect
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", kw["env"], "a keyed launch carries no login bearer")
        rows = [p["text"] for p in self.be.problems(20) if "billing pick 'login' cannot apply" in p["text"]]
        self.assertEqual(len(rows), 1, "once per session, not per reconnect")
        self.assertIn(sb._cred.WHY_NO_LOGIN, rows[0])
        self.assertIn("billing the API key", rows[0])
        self.assertEqual(s.effective_auth(), "login", "the PICK is kept: the menu check-marks it")
        self.assertEqual(self.be.pick_unavailable("login"), "login")
        self.assertEqual(self.be.pick_unavailable(""), "", "unpicked is never 'unavailable'")
        self.assertEqual(self.be.pick_unavailable("key"), "")

    def test_a_key_pick_on_a_helperless_box_with_a_login_launches_on_the_login(self):
        self._no_helper()
        sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        s = self._sess(2, auth="key")
        kw = self._options_kw(s)
        self.assertEqual(self._settings_of(kw).get("apiKeyHelper"), "", "a login launch, suppression and all")
        self.assertEqual(kw["env"].get("CLAUDE_CODE_OAUTH_TOKEN"), "synthetic-login-token", "the login's bearer rides")
        self.assertFalse(s._launched_keyed)
        self.assertFalse(s._launched_unkeyed_pick, "not 'the CLI decides': the launch chose the login, so a "
                                                   "login landing is quiet in the per-init check")
        self._options_kw(s)
        rows = [p["text"] for p in self.be.problems(20) if "billing pick 'key' cannot apply" in p["text"]]
        self.assertEqual(len(rows), 1)
        self.assertIn(sb._cred.WHY_NO_HELPER, rows[0])
        self.assertIn("billing the login", rows[0])
        self.assertEqual(s.effective_auth(), "key", "the PICK is kept")
        self.assertEqual(self.be.pick_unavailable("key"), "key")

    def test_a_box_with_both_changes_nothing(self):
        for n, a, keyed, helper in ((1, "login", False, ""), (2, "key", True, None), (3, "", True, None)):
            s = self._sess(n, auth=a) if a else self._sess(n)
            kw = self._options_kw(s)
            self.assertEqual(s._launched_keyed, keyed, a or "unpicked")
            self.assertEqual(self._settings_of(kw).get("apiKeyHelper"), helper, a or "unpicked")
            self.assertEqual(s._pick_fell_said, "")
        self.assertEqual([p for p in self.be.problems(20) if "cannot apply" in p["text"]], [])
        self.assertEqual(self.be.pick_unavailable("login"), "")
        self.assertEqual(self.be.pick_unavailable("key"), "")

    def test_the_status_rows_carry_the_unavailable_pick_live_and_dormant(self):
        self.be.login_ok = lambda: False
        sid = self.be.spawn("n", "/tmp", auth="login")   # an EXPLICIT picker pick still lands (spawn)
        s = sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))
        snap = s.snapshot()
        self.assertEqual((snap["auth"], snap["authPickUnavailable"]), ("login", "login"))
        row = self.be.live_sessions()[sid]
        self.assertEqual((row["auth"], row["authPickUnavailable"]), ("login", "login"), "the dormant twin agrees")
        self.be.login_ok = lambda: True
        self.assertEqual(s.snapshot()["authPickUnavailable"], "", "a login signed in later clears it on the next push")
        self.assertEqual(self.be.live_sessions()[sid]["authPickUnavailable"], "")


class CannotTell(_OptionsHarness):
    """A side whose availability cannot be read just now never moves a launch (review 2026-09-09): an unreadable
    or non-JSON operator settings file (Claude Code rewrites it) or an unreadable ~/.claude.json reads as
    cannot-tell on every path that used to raise or to read as the missing side; the launch keeps the pick as
    is and says so once per session."""

    def _break_settings(self):
        Path(self.cfg, "settings.json").write_text("{not json")     # mid-rewrite
        sb._cred.forget_helper_key()

    def test_an_unreadable_settings_file_is_cannot_tell_for_both_sides_and_raises_nowhere(self):
        self._break_settings()
        self.assertEqual(self.be.key_state(), "unknown")
        self.assertFalse(self.be.key_available, "the seed and the judges' default still read it as no helper")
        self.assertEqual(self.be.auth_unavailable_why("key"), "", "not 'no helper': cannot tell")
        self.assertEqual(self.be.auth_unavailable_why("login"), "", "not 'managed': cannot tell")
        self.assertEqual((self.be.pick_unavailable("login"), self.be.pick_unavailable("key")), ("", ""))
        self.assertEqual((self.be.pick_fall("login"), self.be.pick_fall("key")), ("", ""))
        snap = self._sess(1, auth="login").snapshot()                 # raised CredentialError out of helper_source before
        self.assertEqual((snap["authPickUnavailable"], snap["authPickFell"]), ("", ""))
        sid = self.be.spawn("n", "/tmp", auth="key")
        row = self.be.live_sessions()[sid]
        self.assertEqual((row["authPickUnavailable"], row["authPickFell"]), ("", ""))
        rows = [p["text"] for p in self.be.problems(20) if "cannot tell which side this box bills" in p["text"]]
        self.assertEqual(len(rows), 1, "the unreadable settings are said once per process")

    def test_a_key_pick_launches_as_picked_and_says_so_once_when_the_settings_cannot_be_read(self):
        self._break_settings()
        s = self._sess(2, auth="key")
        kw = self._options_kw(s)
        self.assertNotIn("apiKeyHelper", self._settings_of(kw), "no login suppression: the pick stands")
        self.assertEqual(s._pick_fell_said, "", "no fall")
        self._options_kw(s)                                           # a reconnect
        rows = [p["text"] for p in self.be.problems(20) if "launching with the pick as is" in p["text"]]
        self.assertEqual(len(rows), 1, "once per session, not per reconnect")
        self.assertIn("'key'", rows[0])

    def test_a_login_pick_launches_as_picked_when_the_settings_cannot_be_read(self):
        self._break_settings()
        s = self._sess(3, auth="login")
        kw = self._options_kw(s)
        self.assertEqual(self._settings_of(kw).get("apiKeyHelper"), "", "the login launch, suppression and all")
        self.assertFalse(s._launched_keyed)
        rows = [p["text"] for p in self.be.problems(20) if "launching with the pick as is" in p["text"]]
        self.assertEqual(len(rows), 1)
        self.assertIn("'login'", rows[0])

    def test_an_unreadable_account_file_never_falls_a_login_pick(self):
        self.be.login_ok = lambda: None                               # the kernel's probe: ~/.claude.json mid-rewrite
        self.assertEqual(self.be.auth_unavailable_why("login"), "")
        self.assertEqual(self.be.pick_fall("login"), "", "a helper is configured, but cannot-tell never falls")
        s = self._sess(4, auth="login")
        kw = self._options_kw(s)
        self.assertEqual(self._settings_of(kw).get("apiKeyHelper"), "", "launched on the login as picked")
        self.assertEqual(s._pick_fell_said, "")
        rows = [p["text"] for p in self.be.problems(20) if "launching with the pick as is" in p["text"]]
        self.assertEqual(len(rows), 1)
        self.assertIn("~/.claude.json", rows[0])
        self.be.login_ok = lambda: False                              # the read heals and says: no account
        self.assertEqual(self.be.pick_fall("login"), "key", "now the fall")

    def test_a_key_pick_never_falls_onto_a_login_that_cannot_be_read(self):
        self._no_helper()
        self.be.login_ok = lambda: None
        self.assertEqual(self.be.pick_unavailable("key"), "key", "the helper is known missing")
        self.assertEqual(self.be.pick_fall("key"), "", "but the login is cannot-tell: no fall")
        s = self._sess(5, auth="key")
        kw = self._options_kw(s)
        self.assertNotIn("apiKeyHelper", self._settings_of(kw))
        self.assertTrue(s._launched_unkeyed_pick, "as picked: the CLI decides, and the per-init check knows the pick")


class TheFallIsCarriedInStatus(_OptionsHarness):
    """authPickFell says which side a pick this box cannot bill actually fell to, "" when nothing did; the tab
    hover and the Billing sub-line read it instead of inferring a fall from authPickUnavailable alone (review
    2026-09-09: on a box with neither side the hover claimed a fall that never happened)."""

    def test_a_login_pick_with_a_helper_falls_and_says_key(self):
        self.be.login_ok = lambda: False
        snap = self._sess(1, auth="login").snapshot()
        self.assertEqual((snap["authPickUnavailable"], snap["authPickFell"]), ("login", "key"))

    def test_a_login_pick_on_a_box_with_neither_side_is_unavailable_but_fell_nowhere(self):
        self._no_helper()
        self.be.login_ok = lambda: False
        s = self._sess(2, auth="login")
        snap = s.snapshot()
        self.assertEqual((snap["authPickUnavailable"], snap["authPickFell"]), ("login", ""))
        kw = self._options_kw(s)
        self.assertEqual(self._settings_of(kw).get("apiKeyHelper"), "", "launched as picked: the CLI decides")
        self.assertEqual(s._pick_fell_said, "", "no fall, no fall notice")
        sid = self.be.spawn("n", "/tmp", auth="login")
        row = self.be.live_sessions()[sid]
        self.assertEqual((row["authPickUnavailable"], row["authPickFell"]), ("login", ""), "the dormant twin agrees")

    def test_a_key_pick_on_a_helperless_box_with_a_login_says_login(self):
        self._no_helper()
        self.be.login_ok = lambda: True
        self.assertEqual(self._sess(3, auth="key").snapshot()["authPickFell"], "login")

    def test_the_options_and_the_status_decide_the_same_way(self):
        # the launch's side IS pick_fall's answer: one decision, two readers
        self.be.login_ok = lambda: False
        s = self._sess(4, auth="login")
        self._options_kw(s)
        self.assertTrue(s._launched_keyed)
        self.assertEqual(self.be.pick_fall("login"), "key")

    def test_the_webview_reads_the_carried_fall_on_both_surfaces(self):
        src = (Path(HERE).parent / "ui" / "webview" / "render.ts").read_text()
        self.assertIn("authPickFell?: string;", src)
        self.assertIn("function authFellTo(st: Status): string", src)
        self.assertIn("authFellTo(s.status)", src, "the tab hover")
        self.assertIn("authFellTo(st)", src, "the Billing sub-line")


class AccountReadStates(unittest.TestCase):
    """~/.claude.json names a signed-in account ("ok"), names none ("none"), or cannot be read just now
    ("unreadable": a stat error, or bytes that are not JSON during Claude Code's non-atomic rewrite). A failed
    read is never cached against the mtime, and the backend's probe reads it as cannot-tell (None)."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self._saved = dict(km._ACCT_CACHE)
        km._ACCT_CACHE.update({"mtime": -1.0, "val": "", "label": "", "state": "none"})

    def tearDown(self):
        if self._home is not None:
            os.environ["HOME"] = self._home
        km._ACCT_CACHE.update(self._saved)

    def _write(self, text, t):
        p = Path(self.home, ".claude.json")
        p.write_text(text)
        os.utime(p, (t, t))                                           # a distinct mtime per write, whatever the clock does

    def test_the_three_states_and_no_caching_of_a_failed_read(self):
        self.assertEqual(km._claude_account_state(), "none", "no file")
        self._write(json.dumps({"oauthAccount": {"accountUuid": "11111111-2222-3333-4444-555555555555",
                                                 "emailAddress": "user@example.com"}}), 1_800_000_000)
        self.assertEqual(km._claude_account_state(), "ok")
        self.assertTrue(km._claude_account())
        self.assertEqual(km._claude_account_label(), "user@example.com")
        self._write('{"oauthAccount": {"accountUu', 1_800_000_001)    # mid-rewrite
        self.assertEqual(km._claude_account_state(), "unreadable")
        self.assertEqual((km._claude_account(), km._claude_account_label()), ("", ""))
        self.assertEqual(km._ACCT_CACHE["mtime"], -1.0, "a failed read is not cached against the mtime: the next read retries")
        self._write(json.dumps({"oauthAccount": {"accountUuid": "11111111-2222-3333-4444-555555555555"}}), 1_800_000_001)
        self.assertEqual(km._claude_account_state(), "ok", "the same mtime, re-read: the rewrite completed")
        self._write(json.dumps({}), 1_800_000_002)
        self.assertEqual(km._claude_account_state(), "none", "a file with no account")

    def test_the_backends_probe_is_tri_state(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('_sdk_backend.login_ok = lambda: (None if _claude_account_state() == "unreadable" else bool(_claude_account()))', src)

    def test_the_picker_treats_unreadable_as_available_and_keeps_a_remembered_login_pick(self):
        saved = (km._sdk, km._claude_account, km._claude_account_label, km._claude_account_state, km.jd._cred.helper_source)
        p = km.jd.STATE / "sdk-defaults.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            km._sdk = lambda: type("B", (), {"key_available": True})()
            km._claude_account = lambda: ""
            km._claude_account_label = lambda: ""
            km._claude_account_state = lambda: "unreadable"
            km.jd._cred.helper_source = lambda: "user"
            a = km._auth_avail()
            self.assertTrue(a["login"], "cannot tell is not 'no login'")
            self.assertNotIn("loginWhy", a)
            p.write_text(json.dumps({"auth": "login"}))
            self.assertEqual(km._auth_avail()["default"], "login", "a remembered login pick does not fall on a read failure")
            km._claude_account_state = lambda: "none"
            self.assertEqual(km._auth_avail()["default"], "key", "…and falls once the read says: no account")
            self.assertEqual(km._auth_avail()["loginWhy"], km.jd._cred.WHY_NO_LOGIN)
        finally:
            (km._sdk, km._claude_account, km._claude_account_label, km._claude_account_state, km.jd._cred.helper_source) = saved
            p.unlink(missing_ok=True)

    def test_an_unreadable_settings_file_does_not_read_as_managed_in_the_picker(self):
        saved = (km._sdk, km._claude_account, km._claude_account_label, km._claude_account_state, km.jd._cred.helper_source)
        try:
            km._sdk = lambda: type("B", (), {"key_available": False})()
            km._claude_account = lambda: "aaaaaaaaaaaa"
            km._claude_account_label = lambda: "user@example.com"
            km._claude_account_state = lambda: "ok"

            def boom():
                raise km.jd._cred.CredentialError("Claude Code settings file is not valid JSON: x")
            km.jd._cred.helper_source = boom
            a = km._auth_avail()                                      # used to raise out of the picker's reply
            self.assertTrue(a["login"])
            self.assertNotIn("loginWhy", a)
        finally:
            (km._sdk, km._claude_account, km._claude_account_label, km._claude_account_state, km.jd._cred.helper_source) = saved


class AvailabilityOncePerCycle(unittest.TestCase):
    """build_session asks for the availability half per session per push; inside a pusher cycle it is computed
    once (the cycle's _live_scope memo), outside one it is fresh (review 2026-09-09: each answer re-read
    sdk-defaults.json and both operator settings files)."""

    def test_one_compute_per_cycle_fresh_outside(self):
        calls, real = [], km._auth_avail
        km._auth_avail = lambda: (calls.append(1), {"login": True, "key": False, "acct": "", "default": "login",
                                                    "keyWhy": km.jd._cred.WHY_NO_HELPER})[1]
        try:
            km._live_scope.auth = {}                                  # a cycle opens
            a = km._auth_avail_status()
            b = km._auth_avail_status()
            self.assertEqual(len(calls), 1, "one compute for the cycle")
            self.assertEqual(a, b)
            self.assertEqual(a, {"login": True, "key": False, "keyWhy": km.jd._cred.WHY_NO_HELPER, "default": "login"}, "the status half, with the machine default since T380 (this stub of _auth_avail sends no explicit flag)")
            a["login"] = False
            self.assertTrue(km._auth_avail_status()["login"], "a caller's mutation does not leak into the memo")
            km._live_scope.auth = None                                # the cycle closes
            km._auth_avail_status()
            km._auth_avail_status()
            self.assertEqual(len(calls), 3, "outside a cycle: fresh every time")
        finally:
            km._auth_avail = real
            km._live_scope.auth = None

    def test_the_cycle_opens_and_closes_the_memo(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("_live_scope.auth = {}", src)
        i = src.index("_live_scope.auth = {}")
        self.assertIn("_live_scope.auth = None", src[i:], "reset in the cycle's finally")


class FallbackBothWays(_Keyed):
    """The default and a remembered pick fall to the side that exists, in both directions."""

    def test_default_auth_is_the_side_that_exists(self):
        self.assertEqual(self.be.default_auth({}), "key", "a helper: the key, whatever the login")
        self.be.login_ok = lambda: False
        self.assertEqual(self.be.default_auth({}), "key", "…and with no login, still the key")
        self.assertEqual(self.be.fallback_auth(), "key")
        self._no_helper()
        self.be.login_ok = lambda: True
        self.assertEqual(self.be.default_auth({}), "login", "no helper: the login")
        self.assertEqual(self.be.default_auth({"auth": "login"}), "login")
        self.be.login_ok = lambda: False
        self.assertEqual(self.be.default_auth({"auth": "login"}), "login",
                         "an explicit pick reads as picked: pick_unavailable says the launch fell")

    def test_a_remembered_login_default_with_no_login_seeds_nothing_and_says_so_once(self):
        sb.write_sdk_default(self.be.state_dir, auth="login")
        self.be.login_ok = lambda: False
        sid = self.be.spawn("n", "/tmp")
        self.assertNotIn("auth", sb.read_reg(self.be.state_dir, sid), "unpicked: bills the key by the box's fallback")
        self.assertEqual(self.be.default_auth(sb.read_reg(self.be.state_dir, sid)), "key")
        self.be.spawn("m", "/tmp")
        rows = [p["text"] for p in self.be.problems(20) if "remembered Billing pick is the login" in p["text"]]
        self.assertEqual(len(rows), 1, "once per process")
        self.assertIn(sb._cred.WHY_NO_LOGIN, rows[0])
        # the mirror (review find 2026-09-07) still holds beside it
        sb.write_sdk_default(self.be.state_dir, auth="key")
        self._no_helper()
        self.be.login_ok = lambda: True
        sid = self.be.spawn("k", "/tmp")
        self.assertNotIn("auth", sb.read_reg(self.be.state_dir, sid))
        rows = [p["text"] for p in self.be.problems(20) if "remembered Billing pick is the API key" in p["text"]]
        self.assertEqual(len(rows), 1)

    def test_availability_names_the_reason_for_each_missing_side(self):
        self.assertEqual(self.be.auth_avail(), {"login": True, "key": True})
        self.be.login_ok = lambda: False
        self.assertEqual(self.be.auth_avail(), {"login": False, "key": True, "loginWhy": sb._cred.WHY_NO_LOGIN})
        self.be.login_ok = lambda: True
        self._no_helper()
        self.assertEqual(self.be.auth_avail(), {"login": True, "key": False, "keyWhy": sb._cred.WHY_NO_HELPER})
        managed = os.path.join(self.cfg, "managed.json")
        Path(managed).write_text(json.dumps({"apiKeyHelper": os.path.join(self.cfg, "helper.sh")}))
        sb._cred.managed_settings_path = lambda: managed
        self.assertEqual(self.be.auth_avail(), {"login": False, "key": True, "loginWhy": sb._cred.WHY_MANAGED_HELPER})


class FastOrgPermissionFollowsBilling(_OptionsHarness):
    """Fast-mode permission follows BILLING (the user 2026-08-14): the CLI's availability probe asks
    the saved claude.ai login whenever one exists, even on a session whose inference bills the
    injected key — so _options asks the paying account itself (key_fast_org_env, fetch stubbed here)
    and hands the CLI the switch matching the answer. Both directions matter: an enabled key account
    skips the CLI's wrong-account probe, a disabled one forces fast mode off."""

    def _env(self, answer, n=1):
        self.asked = []
        sb._fetch_key_fast_org = lambda key: self.asked.append(key) or answer
        sb._cred.forget_helper_key()
        return self._options_kw(self._sess(n, auth="key"))["env"]

    def test_an_enabled_key_account_skips_the_clis_wrong_account_probe(self):
        env = self._env(True)
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1")
        self.assertNotIn("CLAUDE_CODE_DISABLE_FAST_MODE", env)
        self.assertEqual(self.asked, [FAKE_KEY], "the probe asks with the helper's own output, run in-process")
        self.assertNotIn("ANTHROPIC_API_KEY", env, "and the value never reaches the session's environment")

    def test_no_helper_means_no_probe_and_the_cli_default(self):
        self._no_helper()
        env = self._env(True)
        self.assertEqual(self.asked, [], "nothing to ask with")
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", env)
        self.assertNotIn("CLAUDE_CODE_DISABLE_FAST_MODE", env)

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

    def test_the_machine_default_is_set_explicitly_and_a_session_pick_then_moves_it_no_more(self):
        """T380 (the user 2026-09-12): the Billing flyout's Default group writes the seed every new session and every
        session with no pick of its own launches on; a session with its own pick keeps it; once set explicitly the
        per-session pick no longer seeds the default (before, the last pick did)."""
        picked = self.be.spawn("p", "/tmp", auth="login")
        self.assertTrue(self.be.set_auth_default("key"))
        d = sb.read_sdk_defaults(self.be.state_dir)
        self.assertEqual((d.get("auth"), d.get("authExplicit")), ("key", True))
        self.assertEqual(sb.read_reg(self.be.state_dir, picked)["auth"], "login", "a session with its own pick is untouched")
        self.assertEqual(sb.read_reg(self.be.state_dir, self.be.spawn("q", "/tmp")).get("auth"), "key", "a new session follows the default")
        # a per-session pick is about that session now: the default stays where the user set it
        sid = self.be.spawn("n", "/tmp")
        self.assertTrue(self.be.set_auth(sid, "login"))
        self.assertEqual(sb.read_sdk_defaults(self.be.state_dir).get("auth"), "key", "the explicit default is not moved by a session pick")
        self.assertEqual(sb.read_reg(self.be.state_dir, sid)["auth"], "login")
        self.assertTrue(self.be.set_auth_default("login"))
        self.assertEqual(sb.read_sdk_defaults(self.be.state_dir).get("auth"), "login")
        self.assertFalse(self.be.set_auth_default("credit-card"), "junk is not a side")
        # Automatic (review): the flag and the seed clear, the helper rule holds again, and a session pick seeds once more
        self.assertTrue(self.be.set_auth_default("auto"))
        d = sb.read_sdk_defaults(self.be.state_dir)
        self.assertEqual((d.get("auth"), d.get("authExplicit")), ("", False))
        self.assertEqual(self.be.fallback_auth(), "key", "the helper rule again")
        self.assertTrue(self.be.set_auth(sid, "key"))
        self.assertEqual(sb.read_sdk_defaults(self.be.state_dir).get("auth"), "key", "a session pick seeds the default again once it is automatic")

    def test_a_session_with_no_pick_of_its_own_follows_the_explicit_default_at_once(self):
        """The review of the first cut: the default reached only NEW sessions while the flyout's note promised that
        sessions with no pick follow it too. default_auth (a dormant reg) and effective_auth (a live session) read
        the explicit seed when this box can bill it; the launch applies it next time; the marks agree at once."""
        u = self.be.spawn("u", "/tmp")
        reg = sb.read_reg(self.be.state_dir, u)
        self.assertNotIn("auth", reg)
        self.assertEqual(self.be.default_auth(reg), "key", "the helper rule before any explicit default")
        live = sb.SdkSession(self.be, reg)
        self.assertEqual(live.effective_auth(), "key")
        self.assertTrue(self.be.set_auth_default("login"))
        self.assertEqual(self.be.default_auth(reg), "login", "a dormant unpicked session follows the explicit default")
        self.assertEqual(live.effective_auth(), "login", "…and a live one, in its status at once")
        self.assertEqual(self.be.explicit_default_auth(), "login")
        # the seed side this box cannot bill is not followed: the helper rule holds and the flyout greys that radio
        self._no_helper()
        self.assertTrue(self.be.set_auth_default("login"))
        sb.write_sdk_default(self.be.state_dir, auth="key", authExplicit=True)   # a stale explicit key on a box that lost its helper
        self.assertEqual(self.be.default_auth(reg), "login", "an explicit side this box cannot bill is not followed")
        self.assertEqual(live.effective_auth(), "login")
        # a session with its own pick is untouched by any of it
        p = self.be.spawn("p", "/tmp", auth="login")
        sb.write_sdk_default(self.be.state_dir, auth="key", authExplicit=True)
        self.assertEqual(self.be.default_auth(sb.read_reg(self.be.state_dir, p)), "login")

    def test_the_kernel_door_refuses_loudly_through_the_drive(self):
        """The scoped arm's toast (review): a backend without the writer (Codex) is refused by name, a refused side
        carries the backend's reason; neither raises inside the drive."""
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s))}
        saved = (km.Sessions.backend_for, km._kernel_knows, km._push_soon)
        try:
            km._kernel_knows = lambda sid: True
            km._push_soon = lambda: None
            class NoWriter:          # a backend that keeps no machine default
                pass
            km.Sessions.backend_for = staticmethod(lambda sid: NoWriter())
            self.assertTrue(km._drive({"type": "setAuth", "id": "11111111-2222-3333-4444-555555555555", "value": "login", "scope": "machine"}, client) is not False)
            self.assertEqual(sent[-1]["type"], "warn")
            self.assertIn("keeps no machine billing default", sent[-1]["text"])
            class Refuser:
                def set_auth_default(self, v): return False
                def auth_unavailable_why(self, v): return "no apiKeyHelper configured"
            km.Sessions.backend_for = staticmethod(lambda sid: Refuser())
            km._drive({"type": "setAuth", "id": "11111111-2222-3333-4444-555555555555", "value": "key", "scope": "machine"}, client)
            self.assertEqual(sent[-1]["text"], "Couldn't set this machine's default billing: no apiKeyHelper configured.")
            # a session-scoped "auto" (an older remote kernel's flyout would post it) is refused with a toast, never dropped (review)
            km._drive({"type": "setAuth", "id": "11111111-2222-3333-4444-555555555555", "value": "auto"}, client)
            self.assertEqual(sent[-1]["type"], "warn")
            self.assertIn("not for one session", sent[-1]["text"])
            # a scoped STORED login (T346 beside T380): the kernel takes no stored login as the machine default yet, so the
            # value is refused by name from an arm before the per-session one, and neither writer sees it: never the
            # machine default, and never read as this session's own pick either
            calls = []
            class Recorder:
                def set_auth_default(self, v): calls.append(("default", v)); return True
                def set_auth(self, sid, v): calls.append(("session", v)); return True
            km.Sessions.backend_for = staticmethod(lambda sid: Recorder())
            parked = []
            saved_park = km._set_auth_or_park
            km._set_auth_or_park = lambda be, sid, v: (parked.append(v), True)[1]
            try:
                km._drive({"type": "setAuth", "id": "11111111-2222-3333-4444-555555555555", "value": "login:0123456789ab", "scope": "machine"}, client)
            finally:
                km._set_auth_or_park = saved_park
            self.assertEqual(sent[-1]["type"], "warn")
            self.assertIn("a stored login can't be the machine's default yet", sent[-1]["text"])
            self.assertEqual(calls, [], "no writer is called for a scoped value the arm does not take")
            self.assertEqual(parked, [], "and the value never reaches the per-session pick path")
        finally:
            km.Sessions.backend_for, km._kernel_knows, km._push_soon = saved

    def test_the_explicit_default_probe_sees_a_same_size_rewrite_with_an_unchanged_mtime(self):
        """The review's forced probe: (mtime, size) alone kept login while the file said key."""
        self.assertTrue(self.be.set_auth_default("login"))
        self.assertEqual(self.be.explicit_default_auth(), "login")
        p = sb._defaults_path(self.be.state_dir)
        st = p.stat()
        raw = p.read_text()
        self.assertIn('"login"', raw)
        rewritten = raw.replace('"login"', '"key"')
        p.write_text(rewritten + " " * (len(raw) - len(rewritten)))   # the same byte length (trailing blanks are JSON's to ignore)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))       # the mtime held back on purpose
        self.assertEqual(p.stat().st_size, st.st_size, "same size")
        self.assertEqual(p.stat().st_mtime_ns, st.st_mtime_ns, "same mtime")
        self.assertEqual(self.be.explicit_default_auth(), "key", "ctime (or the inode) tells the rewrite apart")

    def test_the_machine_default_refuses_a_side_this_box_cannot_bill_with_the_reason(self):
        self._no_helper()
        self.assertFalse(self.be.set_auth_default("key"))
        self.assertEqual(self.be.last_auth_refusal, sb._cred.WHY_NO_HELPER)
        self.assertNotEqual(sb.read_sdk_defaults(self.be.state_dir).get("auth"), "key")

    def test_the_picker_pick_beats_the_remembered_default(self):
        sb.write_sdk_default(self.be.state_dir, auth="login")
        sid = self.be.spawn("n", "/tmp", auth="key")
        self.assertEqual(sb.read_reg(self.be.state_dir, sid)["auth"], "key")

    def test_refuses_junk_and_a_key_pick_on_a_helperless_box(self):
        sid = self.be.spawn("n", "/tmp")
        self.assertFalse(self.be.set_auth(sid, "credit-card"))
        self.assertEqual(self.be.last_auth_refusal, "", "junk is not a reason: the value is simply not a side")
        self._no_helper()
        self.assertFalse(self.be.set_auth(sid, "key"),
                         "no helper on this box: refuse rather than half-apply; the UI greys it")
        self.assertEqual(self.be.last_auth_refusal, sb._cred.WHY_NO_HELPER, "the refusal names its reason")
        rows = [p["text"] for p in self.be.problems(10) if sb._cred.WHY_NO_HELPER in p["text"]]
        self.assertEqual(len(rows), 1, "…in the problem ring too")
        self.assertNotIn("auth", sb.read_reg(self.be.state_dir, sid), "nothing half-applied")

    def test_refuses_a_login_pick_on_a_box_with_no_login_naming_the_reason(self):
        sid = self.be.spawn("n", "/tmp")
        self.be.login_ok = lambda: False
        self.assertFalse(self.be.set_auth(sid, "login"))
        self.assertEqual(self.be.last_auth_refusal, sb._cred.WHY_NO_LOGIN)
        self.assertEqual(self.be.auth_unavailable_why("login"), sb._cred.WHY_NO_LOGIN, "the toast reads the same sentence")
        self.assertEqual(self.be.auth_unavailable_why("key"), "")
        self.assertTrue(self.be.set_auth(sid, "key"), "the side that exists still takes")

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
        # the classification lives in _api_error_pass, the one-pass scanner both _api_error and the latched
        # _api_last_failed read through, so the pin reads that function
        self.assertIn('"authErr": _is_auth_error(text)', inspect.getsource(km._api_error_pass))
        self.assertIn('"apiAuthErr": bool(aerr and aerr.get("authErr"))', inspect.getsource(km.build_session))
        feed = inspect.getsource(km._feed_session_entry)
        self.assertIn('aerr.get("authErr") or aerr.get("refusal"))))', feed, "the card floors to needs-you")
        self.assertIn("sign-in or API key isn't working", feed, "the card names the real remedy")


class Availability(unittest.TestCase):
    """The selector exists only when BOTH choices are real — everywhere it could appear."""

    def setUp(self):
        self.real_sdk, self.real_acct, self.real_label = km._sdk, km._claude_account, km._claude_account_label
        self.real_state, self.real_source = km._claude_account_state, km.jd._cred.helper_source

    def tearDown(self):
        km._sdk, km._claude_account, km._claude_account_label = self.real_sdk, self.real_acct, self.real_label
        km._claude_account_state, km.jd._cred.helper_source = self.real_state, self.real_source

    def _world(self, key, acct, label="user@example.com", managed=False):
        km._sdk = lambda: type("B", (), {"key_available": bool(key)})()
        km._claude_account = lambda: acct
        km._claude_account_label = lambda: (label if acct else "")
        km._claude_account_state = lambda: ("ok" if acct else "none")   # the box's account file, never the runner's
        km.jd._cred.helper_source = lambda: ("managed" if managed else ("user" if key else None))

    def test_a_managed_helper_removes_the_login_choice(self):
        self._world(FAKE_KEY, "aaaaaaaaaaaa", managed=True)
        a = km._auth_avail()
        self.assertEqual((a["login"], a["key"]), (False, True), "no per-session layer can disable a managed helper")
        self.assertFalse(km._auth_both())

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
        # too); authBoth stays for older clients; authAvail (with reasons) and authPickUnavailable are
        # what the Billing menu renders from since 2026-09-08; authAcct names the login.
        self.assertIn('"auth": tm.get("auth", "")', src)
        self.assertIn('"authBoth": _auth_both()', src)
        self.assertIn('"authAvail": _auth_avail_status()', src)
        self.assertIn('"authPickUnavailable": tm.get("authPickUnavailable", "")', src)
        self.assertIn('"authAcct": _claude_account_label()', src)
        self.assertNotIn("authTail", src, "the status payload carries the choice, never key material")
        # …and the live map the push reads from carries the backend's per-session fields (authLive was
        # read off this map from the start and never merged into it — found 2026-09-08)
        src = inspect.getsource(km.Sessions.live)
        self.assertIn('"authLive": st.get("authLive", "")', src)
        self.assertIn('"authPickUnavailable": st.get("authPickUnavailable", "")', src)

    def test_availability_names_the_reason_for_each_missing_side(self):
        """The user 2026-09-08: the Billing menu lists both choices always and greys the one this box cannot
        bill, with the reason in its hover — so the reply and the status payload carry the reason."""
        self._world(FAKE_KEY, "aaaaaaaaaaaa")
        a = km._auth_avail()
        self.assertNotIn("loginWhy", a)
        self.assertNotIn("keyWhy", a)
        st = km._auth_avail_status()
        self.assertEqual({k: st[k] for k in ("login", "key", "default", "defaultExplicit")},
                         {"login": True, "key": True, "default": "key", "defaultExplicit": False},
                         "the status half carries the machine default since T380 (the Billing flyout's Default group marks it), and whether it is explicit")
        self.assertEqual(st["logins"][0]["machine"], True, "the login list rides beside (T346), the machine's own first")
        self._world(FAKE_KEY, "")
        a = km._auth_avail()
        self.assertEqual(a["loginWhy"], km.jd._cred.WHY_NO_LOGIN)
        self.assertEqual({k: v for k, v in km._auth_avail_status().items() if k != "logins"},
                         {"login": False, "key": True, "loginWhy": km.jd._cred.WHY_NO_LOGIN, "default": "key", "defaultExplicit": False})
        self._world("", "aaaaaaaaaaaa")
        self.assertEqual(km._auth_avail()["keyWhy"], km.jd._cred.WHY_NO_HELPER)
        self._world(FAKE_KEY, "aaaaaaaaaaaa", managed=True)
        self.assertEqual(km._auth_avail()["loginWhy"], km.jd._cred.WHY_MANAGED_HELPER)
        self.assertEqual({k: v for k, v in km._auth_avail_status().items() if k != "logins"},
                         {"login": False, "key": True, "loginWhy": km.jd._cred.WHY_MANAGED_HELPER, "default": "key", "defaultExplicit": False})
        self.assertNotIn(FAKE_KEY, json.dumps(km._auth_avail()), "the reasons carry no key material either")

    def test_the_default_falls_to_the_side_that_exists_both_ways(self):
        """A remembered login pick on a box with no login defaults to the key (the user 2026-09-08), exactly
        as a remembered key pick on a helper-less box already defaulted to the login."""
        p = km.jd.STATE / "sdk-defaults.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._world(FAKE_KEY, "")
            self.assertEqual(km._auth_avail()["default"], "key", "unpicked, no login: the key")
            p.write_text(json.dumps({"auth": "login"}))
            self.assertEqual(km._auth_avail()["default"], "key", "a remembered login pick with no login: the key")
            self._world(FAKE_KEY, "aaaaaaaaaaaa")
            self.assertEqual(km._auth_avail()["default"], "login", "…and with a login, the pick stands")
            self._world("", "aaaaaaaaaaaa")
            p.write_text(json.dumps({"auth": "key"}))
            self.assertEqual(km._auth_avail()["default"], "login", "the mirror: a remembered key pick with no helper")
            self._world("", "")
            self.assertEqual(km._auth_avail()["default"], "login", "neither: the login, the CLI's own resolution")
            p.write_text(json.dumps({"auth": "login"}))
            self.assertEqual(km._auth_avail()["default"], "login", "…a login pick with nothing to fall to stands")
        finally:
            p.unlink(missing_ok=True)

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

    def test_the_refusal_toast_names_the_backend_reason(self):
        # a refused setAuth tells the user WHY (no login signed in / no apiKeyHelper / a managed helper)
        # from the backend's own sentence, and keeps the generic text for the cases without one
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('why = str(getattr(be, "auth_unavailable_why", lambda v: "")(str(msg["value"])) or "")', src)
        self.assertIn('("Couldn\'t switch the account this session bills: %s." % why) if why', src)


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
        # rest: keyed (the box's helper), confirmed by an init
        self.be._note_auth_source(s, "apiKeyHelper")
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
        s._launched_keyed = True                              # the applying reconnect meant the key (_options)
        s._auth_pending = ""
        self.be._note_auth_source(s, "none")                  # picked key; the CLI reports login
        auth, live, pending = self._state(s)
        self.assertEqual((auth, pending), ("key", False))
        self.assertEqual(live, "login", "the wrong-side landing rides authLive → the row's ⚠ shape")
        self.assertTrue(any("billing the login" in p["text"] for p in self.be.problems(20)),
                        "…and the problems ring names it")

    def test_the_cycle_on_a_login_less_box(self):
        self.be.login_ok = lambda: False                     # a helper-only box with no login
        sid = "11111111-2222-3333-4444-00000000t125"
        sb.write_reg(Path(self.d), sid, {"sid": sid, "name": "misc2", "cwd": "/tmp"})
        s = self._sess(42, sid=sid)
        self.be.sessions[sid] = s
        self.be._note_auth_source(s, "apiKeyHelper")
        self.assertFalse(self.be.set_auth(sid, "login"),
                         "the pick the box cannot apply refuses AT PICK TIME — never accept-then-fail")
        self.assertEqual(self._state(s), ("key", "key", False),
                         "nothing moved: no pending window, no intent shown, the row keeps the truth")


class DrivePlumbing(unittest.TestCase):
    """setAuth rides the same drive/park path as the other per-session switches (source pins)."""

    def test_the_op_is_routed_parked_and_replayed(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"setAuth", "endSession"', src.replace("\n", " "), "an ID_OPS member")
        self.assertIn('elif t == "setAuth" and lg.parse_pick(msg.get("value"))[0]:', src)   # T346: 'login' | 'key' | 'login:<id>'
        self.assertIn("def _set_auth_or_park(be, sid, value):", src)
        # the machine's default (T380): the same op with scope "machine" writes the seed on THIS kernel and touches no session
        self.assertIn('elif t == "setAuth" and msg.get("scope") == "machine" and msg.get("value") in ("login", "key", "auto"):', src)
        self.assertIn('_set_def = getattr(be, "set_auth_default", None)', src)
        # the judges ask the same resolver the launch and the status use (round 3 of T380): the kernel wires it
        ksrc = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('jd._DEFAULT_AUTH_FN = getattr(_sdk_backend, "default_auth", None)', ksrc, "the one billing resolver, wired into the judges")
        self.assertIn("keeps no machine billing default", src, "a backend without the writer (Codex) is refused by name, never a raise inside the drive")
        plain = src.index('elif t == "setAuth" and lg.parse_pick(msg.get("value"))[0]:')
        self.assertLess(src.index('msg.get("scope") == "machine"'), plain,
                        "the scoped arm is tried first: the plain arm would otherwise swallow it as a per-session pick")
        # a scoped value the kernel does not take yet (a STORED login as the machine's default, T346 beside T380) is refused
        # by name from an arm BEFORE the plain one, so it is never read as a session's own pick either
        refuse = src.index('elif t == "setAuth" and msg.get("scope") == "machine":')
        self.assertLess(refuse, plain, "the scoped refusal sits before the per-session arm")
        self.assertIn("a stored login can't be the machine's default yet", src)
        self.assertIn('_gate_or_park(sid, ("auth", value))', src)   # parks on the gate, or hands over (2026-09-05)
        self.assertIn('elif op[0] == "auth":', src)
        self.assertIn("be.set_auth(sid, op[1])", src)

    def test_create_paths_pass_the_pick_through(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        # (parent + tags joined the signature with tab groups, 2026-09-04 — auth's slot is unchanged)
        self.assertIn("def _create_sdk_session(nm, cwd, auth=\"\", prefs=None, client=None, env=None, parent=\"\", tags=()):", src)
        self.assertEqual(src.count('auth=(a if lg.parse_pick(a)[0] else "")'), 2,
                         "the WS op and POST /new both pass it")

    def test_the_abc_names_the_control_and_the_default_refuses(self):
        sbc = open(os.path.join(os.path.dirname(HERE), "kernel", "session_backend.py")).read()
        self.assertIn("def set_auth(self, sid: str, value: str) -> bool:", sbc)
        self.assertIn("return False", sbc.split("def set_auth", 1)[1][:900])


if __name__ == "__main__":
    unittest.main()
