#!/usr/bin/env python3
"""Judges bill the account of the session they judge (the user 2026-08-12), and romp holds no API key
to do it with (the user 2026-09-08): the judges run on Claude Code's own credential resolution.

The incident behind the first rule: per-session billing claimed the manager's key out of os.environ so
no session inherited it ambiently, but the judges' subprocess env was a plain copy taken AFTER that
claim, and on a host whose only credential was that key every judge call refused "Not logged in" for 13
hours (~53k errors) while the cards sat parked in Working. The decision behind the second: a contributor
PR's test printed a key from a session's environment, so every path by which romp itself held a key was
removed (kernel/credentials.py); Claude Code's apiKeyHelper is the one key path left. The mechanics under
test, as they stand since 2026-09-08:

  * _judge_auth resolves a call's billing to the JUDGED SESSION's own pick (the registry's `auth`), with
    the session picker's exact fallback: an explicit pick wins; otherwise the key when Claude Code's
    settings for this process's cwd carry an apiKeyHelper (_key_available: read, never run), else login.
  * _judge_env strips ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN and the 1Password
    CLI's own names from EVERY child env. A key-billed call gets back exactly one thing since 2026-10-05: the
    kernel's held key (credentials.HeldKey, one helper run for every child; tests/test_judge_held_key.py pins
    the holder), never the ambient value. A login-billed call gets the claimed login tokens back
    (_LOGIN_AUTH_ENV_FN, wired by the kernel to sdk_backend.startup_auth_env; the environment standalone).
  * _judge_cmd appends `--settings {"apiKeyHelper": ""}` for a login-billed call (the helper outranks the
    login in the CLI's precedence) and for a key-billed call carrying the held key (the CLI would otherwise
    still run its helper once per call): either way the helper is off for that one process.
  * A credential-class error envelope LATCHES judge-auth-down for the session (STATE/judge-auth.json);
    the session's next successful call clears it. Both edges are events, no timers.
  * build_feed floors a latched session's focus card to needs-you wearing the "judgeAuth" story, whose
    key-mode copy now points at the helper, and the feed bundle carries the chip.

tests/test_credentials.py pins the credentials module itself and the basic judge shape; this module
extends those pins at the judge boundary and keeps the latch, classifier and kernel-wiring history.

Synthetic sids only; every credential-shaped value is an invented string used solely to assert that it
is stripped or carried; the staged helper is a fixture script that prints an invented value.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp())
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
jd = load_source("romp_judge_authbill", os.path.join(BIN, "romp-judge"))

AMBIENT = "synthetic-ambient-value"       # what a credential variable carries when a test stages one to
                                          # assert it is stripped; nothing under test validates the shape
HELD = "synthetic-held-value"             # what the staged apiKeyHelper fixture prints: the kernel's held key
_REAL_RUN = subprocess.run                # the fakes below replace subprocess.run module-wide; the helper still runs
CREDENTIAL_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
SID = "11111111-2222-3333-4444-555555555555"
NOT_LOGGED_IN = "Not logged in · Please run /login"   # the CLI's live refusal, verbatim shape
HELPER_OFF = ["--settings", '{"apiKeyHelper": ""}']   # the login-billed call's helper suppression, verbatim


def _op_names():
    """Every 1Password CLI name the judge boundary strips: the fixed names plus one under the prefix."""
    return tuple(jd._cred.OP_ENV_NAMES) + (jd._cred.OP_ENV_PREFIX + "acct",)


class TheJudgesFollowTheMachineDefault(unittest.TestCase):
    """T380 review: _judge_auth resolved an unpicked session as key-when-helper-else-login while the launch honours the
    machine's explicit default; the judges read the same seed (sdk-defaults.json) so they bill the session's account."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        jd._rebind_state(Path(self.tmp))
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        self.fsid = "11111111-2222-3333-4444-555555555555"
        (jd.SDKDIR / (self.fsid + ".json")).write_text(json.dumps({"sid": self.fsid, "name": "web"}))   # no pick of its own
        self._key = jd._key_available
        self._fn = jd._DEFAULT_AUTH_FN
        jd._DEFAULT_AUTH_FN = None
        self._lfn = getattr(jd, "_DEFAULT_LOGIN_FN", None)   # getattr: the red run at the base predates the name
        jd._DEFAULT_LOGIN_FN = None

    def tearDown(self):
        jd._key_available = self._key
        jd._DEFAULT_AUTH_FN = self._fn
        jd._DEFAULT_LOGIN_FN = self._lfn
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_judges_ask_the_one_resolver_the_kernel_wires_so_an_unbillable_default_moves_them_with_the_launch(self):
        """Round-3 review: a seed re-read here kept billing the login after the machine could no longer bill it (the login
        logged out, a managed helper appearing) while the launch, the status and the flyout had moved to the key; and an
        explicit key default with unreadable Claude Code settings made the picker keep the key while the judge said login.
        The judges now ask SdkBackend.default_auth over the reg, the resolver that applies the availability check."""
        calls = []
        # the resolver's verdicts, as the backend would give them for these regs: a logged-out login default → key
        jd._DEFAULT_AUTH_FN = lambda reg: (calls.append(dict(reg)), "key")[1]
        jd._key_available = lambda: True
        self.assertEqual(jd._judge_auth(self.fsid), "key", "the resolver's word, not a seed re-read")
        self.assertEqual(calls[-1].get("sid"), self.fsid, "asked over the session's own reg")
        # the fourth shape: an explicit key default with unreadable settings: the resolver keeps the key (cannot tell is
        # never a fall), while the standalone helper probe would have said no key
        jd._key_available = lambda: False
        self.assertEqual(jd._judge_auth(self.fsid), "key", "the resolver decides even when the judge's own helper probe says no")
        self.assertEqual(jd._judge_auth(""), "key", "a call with no session asks the resolver over an empty reg")
        self.assertEqual(calls[-1], {}, "…the empty reg")
        # a session's own pick: the resolver returns it (default_auth reads the reg first); the judge trusts the resolver
        (jd.SDKDIR / (self.fsid + ".json")).write_text(json.dumps({"sid": self.fsid, "auth": "login"}))
        jd._DEFAULT_AUTH_FN = lambda reg: reg.get("auth") or "key"
        self.assertEqual(jd._judge_auth(self.fsid), "login")
        # a resolver that fails or answers junk never raises inside a judge call: the standalone rule decides
        jd._DEFAULT_AUTH_FN = lambda reg: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertEqual(jd._judge_auth(self.fsid), "login", "the reg's own pick, standalone")
        jd._DEFAULT_AUTH_FN = lambda reg: "credit-card"
        (jd.SDKDIR / (self.fsid + ".json")).write_text(json.dumps({"sid": self.fsid}))
        jd._key_available = lambda: True
        self.assertEqual(jd._judge_auth(self.fsid), "key", "junk from the resolver: the helper rule")

    def test_an_unpicked_session_following_a_stored_login_default_bills_its_judges_to_that_login(self):
        """The user 2026-09-14: a stored login set as the machine's default. The resolver says the side (login); the second
        wired function, default_login, says WHICH; the judge carries "login:<id>" so _judge_cmd names that login's helper.
        An empty or junk id, or no second function, reads as the machine's own login; a failing one never raises."""
        jd._DEFAULT_AUTH_FN = lambda reg: "login"
        self.assertEqual(jd._judge_auth(self.fsid), "login", "no second function wired (an older kernel): the machine's own login")
        jd._DEFAULT_LOGIN_FN = lambda reg: "0123456789ab"
        self.assertEqual(jd._judge_auth(self.fsid), "login:0123456789ab")
        self.assertEqual(jd._judge_auth(""), "login:0123456789ab", "a call with no session follows the default too")
        jd._DEFAULT_LOGIN_FN = lambda reg: ""
        self.assertEqual(jd._judge_auth(self.fsid), "login", "the machine's own login")
        jd._DEFAULT_LOGIN_FN = lambda reg: "not-an-id"
        self.assertEqual(jd._judge_auth(self.fsid), "login", "junk reads as the machine's own login")
        jd._DEFAULT_AUTH_FN = lambda reg: "key"
        jd._DEFAULT_LOGIN_FN = lambda reg: "0123456789ab"
        self.assertEqual(jd._judge_auth(self.fsid), "key", "the id is read beside a login side only")
        jd._DEFAULT_AUTH_FN = lambda reg: "login"
        jd._DEFAULT_LOGIN_FN = lambda reg: (_ for _ in ()).throw(RuntimeError("boom"))
        jd._key_available = lambda: True
        self.assertEqual(jd._judge_auth(self.fsid), "key", "a failing second function: the standalone rule, never a raise")

    def test_standalone_the_registry_pick_and_the_helper_rule_stand_in(self):
        jd._key_available = lambda: True
        self.assertEqual(jd._judge_auth(self.fsid), "key", "no wiring, no pick: the helper rule")
        jd._key_available = lambda: False
        self.assertEqual(jd._judge_auth(self.fsid), "login")
        (jd.SDKDIR / (self.fsid + ".json")).write_text(json.dumps({"sid": self.fsid, "auth": "key"}))
        self.assertEqual(jd._judge_auth(self.fsid), "key", "a session's own pick")


class _JudgeAuthBase(unittest.TestCase):
    """Clean slate per test: no login wire, no latch file, no ambient credential, no session reg, and
    Claude Code settings that carry NO apiKeyHelper. The settings live in a fresh CLAUDE_CONFIG_DIR per
    test (conftest floors the variable to one dir shared by the whole run, so a helper one test stages
    would otherwise be read by the next), and the managed settings path points at a file that does not
    exist, so a box's own managed file cannot flip the default under the test."""

    def setUp(self):
        self._env_before = os.environ.pop("ANTHROPIC_API_KEY", None)
        self._login_before = jd._LOGIN_AUTH_ENV_FN
        jd._LOGIN_AUTH_ENV_FN = None
        jd._auth_cache[:] = [None, {}]
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self._managed_before = jd._cred.managed_settings_path
        jd._cred.managed_settings_path = lambda: os.path.join(self.cfg, "no-managed-settings.json")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        for p in (jd.JUDGE_AUTH, jd.SDKDIR / (SID + ".json"),
                  jd.STATE / "retry-paused.json", jd.STATE / "usage.json"):
            try:
                p.unlink()
            except OSError:
                pass
        jd._judge_ctx.fsid = None
        jd._judge_ctx.paused = False
        # a fresh holder per test: the key one test's helper printed must never ride the next test's children
        self._source_before = jd._KEY_SOURCE
        self.now = [1000.0]                           # the holder's clock: a test steps past its one-minute gap
        jd._KEY_SOURCE = jd._cred.HeldKey(jd._cred.child_key_helper, env_name="ANTHROPIC_API_KEY",
                                          label="apiKeyHelper", max_age_fn=jd._cred.helper_ttl_if_set,
                                          clock=lambda: self.now[0])

    def tearDown(self):
        jd._KEY_SOURCE = self._source_before
        jd._LOGIN_AUTH_ENV_FN = self._login_before
        jd._cred.managed_settings_path = self._managed_before
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if self._env_before is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._env_before
        jd._judge_ctx.fsid = None
        jd._judge_ctx.paused = False
        # leave NOTHING latched in the shared STATE: the suite runs every test file against one XDG state
        # home, and a leftover judge-auth.json row for the shared synthetic sid floors OTHER files'
        # build_feed cards to needs-you (25 stays-in-Working tests, found 2026-08-12)
        jd._auth_cache[:] = [None, {}]
        for p in (jd.JUDGE_AUTH, jd.SDKDIR / (SID + ".json")):
            try:
                p.unlink()
            except OSError:
                pass

    def _reg(self, auth):
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "auth": auth}))

    def _helper(self, cmd=None):
        """Stage "a key exists": Claude Code's user settings name an apiKeyHelper. By default a fixture script
        that prints HELD and counts its runs in helper.ran (billing resolution only reads the setting; the
        kernel's held key runs it once for the key-billed children)."""
        if cmd is None:
            script = Path(self.cfg, "helper.sh")
            script.write_text("#!/bin/sh\nprintf . >> '%s'\necho '%s'\n" % (Path(self.cfg, "helper.ran"), HELD))
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            cmd = str(script)
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": cmd}))

    def _helper_runs(self):
        try:
            return len(Path(self.cfg, "helper.ran").read_text())
        except OSError:
            return 0


class JudgeBillingResolution(_JudgeAuthBase):
    """The default follows the helper's presence; an explicit pick wins either way."""

    def test_defaults_to_the_key_when_a_helper_is_configured(self):
        self._helper()
        self.assertEqual(jd._judge_auth(SID), "key")      # no reg on disk
        self.assertEqual(jd._judge_auth(None), "key")     # a call with no session: the same default

    def test_defaults_to_login_when_no_helper_is_configured(self):
        self.assertEqual(jd._judge_auth(SID), "login")
        self.assertEqual(jd._judge_auth(None), "login")

    def test_an_explicit_login_pick_wins_over_a_configured_helper(self):
        self._helper()
        self._reg("login")
        self.assertEqual(jd._judge_auth(SID), "login")

    def test_a_key_pick_rides_the_key(self):
        self._helper()
        self._reg("key")
        self.assertEqual(jd._judge_auth(SID), "key")

    def test_a_key_pick_with_no_helper_keeps_its_billing_intent(self):
        # the pick is the session's billing intent; with no helper the child still launches keyless and
        # Claude Code's own resolution decides (KeylessKeyBilledCalls below), never a silent fall to login
        self._reg("key")
        self.assertEqual(jd._judge_auth(SID), "key")
        self.assertNotIn("ANTHROPIC_API_KEY", jd._judge_env("triage", "key"), "no helper: nothing for the kernel to hold")

    def test_a_helper_that_disables_itself_reads_as_no_helper(self):
        # the empty string is the CLI's disable value (a login launch's per-session layer writes it), so a
        # user settings file carrying it means "no key here", not "a helper named nothing"
        self._helper("")
        self.assertEqual(jd._judge_auth(SID), "login")

    def test_billing_resolution_reads_the_helper_and_only_the_held_key_runs_it_once(self):
        # romp decides billing from the SETTING, and building the argv runs nothing; since 2026-10-05 the
        # key-billed child's environment carries the kernel's held key, so the first env build runs the
        # helper and every later one reuses what it printed
        self._helper()
        self.assertEqual(jd._judge_auth(SID), "key")
        jd._judge_cmd("sonnet", "SYS", None, auth="key", key_held=True)
        self.assertEqual(self._helper_runs(), 0, "billing resolution and the argv run nothing")
        for _ in range(5):
            self.assertEqual(jd._judge_env("triage", "key").get("ANTHROPIC_API_KEY"), HELD)
        self.assertEqual(self._helper_runs(), 1, "one helper run for every key-billed child")
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ, "the held key never enters the kernel's own environment")

    def test_an_unreadable_settings_file_reads_as_no_helper_here(self):
        # the judge side answers the billing question and cannot raise out of it; the loud half (the SDK
        # backend's problem ring) is where an unparsable settings file is reported (judge._key_available)
        Path(self.cfg, "settings.json").write_text("{not json")
        self.assertFalse(jd._key_available())
        self.assertEqual(jd._judge_auth(SID), "login")


class JudgeEnvBilling(_JudgeAuthBase):
    """The child env: every credential name stripped, the login tokens restored for a login-billed call
    only, and for a key-billed one the kernel's held key alone (2026-10-05; from 2026-09-08 a key-billed
    child got nothing and ran the helper itself, once per call)."""

    def test_every_credential_and_op_name_is_stripped_whatever_the_billing(self):
        """2026-09-08: the 1Password names are stripped unconditionally; until then a box with no key
        reference configured kept them in its judge children."""
        self._helper()
        ambient = {name: AMBIENT for name in CREDENTIAL_NAMES + _op_names()}
        login_tokens = ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
        for auth in ("key", "login", "codex"):
            with patch.dict(os.environ, ambient):
                env = jd._judge_env("triage", auth)
            for name in ambient:
                if auth == "login" and name in login_tokens:
                    # a login-billed child gets the LOGIN tokens back (standalone, the stash is the environment
                    # itself): they are the login, not key material, and the design keeps them
                    self.assertEqual(env.get(name), AMBIENT, "%s is the login-billed child's credential" % name)
                    continue
                if auth == "key" and name == "ANTHROPIC_API_KEY":
                    # the key-billed child carries the kernel's held key (2026-10-05), never the ambient value
                    self.assertEqual(env.get(name), HELD, "the held key, not the ambient one")
                    continue
                self.assertNotIn(name, env, "%s rode a %s-billed child" % (name, auth))
            self.assertEqual(env.get("ROMP_SUMMARIZING"), "1", "the rest of the env contract is untouched")

    def test_a_key_billed_child_gets_the_held_key_and_never_a_login_token(self):
        # before 2026-09-08 an unkeyed key-billed child was handed the login tokens as a fallback
        # credential; a login token would outrank the key, so it never rides a key-billed child
        self._helper()
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        env = jd._judge_env("triage", "key")
        self.assertEqual(env.get("ANTHROPIC_API_KEY"), HELD)
        for name in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.assertNotIn(name, env)

    def test_a_login_billed_child_gets_the_claimed_login_tokens_and_only_those(self):
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": AMBIENT, "ANTHROPIC_AUTH_TOKEN": AMBIENT}):
            env = jd._judge_env("triage", "login")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "synthetic-login-token")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env, "the ambient bearer stays stripped: only the wire's tokens ride")

    def test_standalone_a_login_billed_child_reads_the_login_tokens_from_the_environment(self):
        # no kernel wire (romp-judge --once, tests): the login tokens come straight from os.environ, and
        # exactly the names credentials.LOGIN_TOKEN_VARS lists
        self.assertIsNone(jd._LOGIN_AUTH_ENV_FN)
        staged = {name: "synthetic-%s" % name.lower() for name in jd._cred.LOGIN_TOKEN_VARS}
        with patch.dict(os.environ, dict(staged, ANTHROPIC_API_KEY=AMBIENT)):
            login_env = jd._judge_env("triage", "login")
            key_env = jd._judge_env("triage", "key")
        for name, value in staged.items():
            self.assertEqual(login_env.get(name), value)
            self.assertNotIn(name, key_env)
        self.assertNotIn("ANTHROPIC_API_KEY", login_env)

    def test_the_existing_env_contract_survives(self):
        os.environ["TMUX"] = "sock,1,0"
        try:
            env = jd._judge_env("index", "login", model="haiku")
            self.assertNotIn("TMUX", env)
            self.assertEqual(env.get("ROMP_SUMMARIZING"), "1")
            # the index tier's thinking-off var rides UNCONDITIONALLY (PR #880 review): the honored lever on
            # models that take thinking:disabled, a harmless no-op where the CLI drops it (Fable) and
            # `--effort` lands instead; the billing plumbing is the same either way
            self.assertEqual(env.get("MAX_THINKING_TOKENS"), "0")
            self.assertEqual(jd._judge_env("index", "login", model="fable").get("MAX_THINKING_TOKENS"), "0")
        finally:
            os.environ.pop("TMUX", None)


class JudgeArgvBilling(_JudgeAuthBase):
    """The argv: a login-billed call disables the helper for its one process; a key-billed call does only when
    it carries the kernel's held key (key_held), whose child must not run the helper again."""

    def test_a_key_billed_call_carrying_the_held_key_turns_the_helper_off(self):
        self.assertEqual(jd._judge_cmd("sonnet", "SYS", None, auth="key", key_held=True)[-2:], HELPER_OFF)
        self.assertEqual(jd._judge_cmd("sonnet", "SYS", "low", auth="key", key_held=True).count("--settings"), 1)

    def test_the_suppression_rides_after_the_call_flags_and_beside_effort(self):
        cmd = jd._judge_cmd("sonnet", "SYS", "low", auth="login")
        self.assertEqual(cmd[-2:], HELPER_OFF)
        self.assertIn("--effort", cmd, "the effort flag still lands beside it")
        self.assertEqual(json.loads(cmd[-1]), {"apiKeyHelper": ""}, "the empty string, not null: null falls through to the files")

    def test_a_key_billed_or_unpicked_argv_never_carries_it(self):
        for auth in ("key", "codex", None):
            self.assertNotIn("--settings", jd._judge_cmd("sonnet", "SYS", None, auth=auth), repr(auth))
        self.assertNotIn("--settings", jd._judge_cmd("sonnet", "SYS", None), "the default: no auth argument")

    def test_a_fast_login_billed_call_rides_one_overlay_with_both_keys(self):
        # `--settings` takes ONE value. With the triage tier's Fast mode on (STATE/judge-fast "on"), an Opus login-billed call
        # carries the fastMode opt-in and the helper suppression in one JSON overlay; a login-billed call on a
        # model that cannot run fast keeps HELPER_OFF byte for byte; a key-billed or unpicked Opus call carries
        # the opt-in alone, and the helper key never appears in it.
        (jd.STATE / "judge-fast").write_text("on")
        jd._state_cache.clear()
        try:
            cmd = jd._judge_cmd("opus", "SYS", None, auth="login")
            self.assertEqual(cmd.count("--settings"), 1, "one overlay, never two --settings")
            self.assertEqual(json.loads(cmd[-1]), {"fastMode": True, "apiKeyHelper": ""})
            self.assertEqual(jd._judge_cmd("sonnet", "SYS", None, auth="login")[-2:], HELPER_OFF)
            for auth in ("key", None):
                cmd = jd._judge_cmd("opus", "SYS", None, auth=auth)
                self.assertEqual(cmd.count("--settings"), 1, repr(auth))
                self.assertEqual(json.loads(cmd[-1]), {"fastMode": True}, repr(auth))
        finally:
            (jd.STATE / "judge-fast").unlink()
            jd._state_cache.clear()


class RuntimeJudgeBilling(_JudgeAuthBase):
    """_judge_run's pre-launch path: the usage gate and the codex engine touch no Anthropic credential."""

    def test_exhausted_login_window_pauses_without_launching(self):
        self._reg("login")
        jd._judge_ctx.fsid = SID
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "usage.json").write_text(json.dumps({
                "five_hour": {"pct": 100, "resets_at": int(time.time()) + 3600}}))
            # Keep the usage fixture, limit latch, log, and in-memory gate state local to this test.
            with patch.multiple(jd, STATE=state, JUDGE_LIMIT=state / "judge-limit.json",
                                _limit_cache=[None, {}], _RATE_GATE_LOGGED={}), \
                    patch.object(jd, "_judge_engine", return_value="claude"), \
                    patch.object(jd.subprocess, "run") as run:
                self.assertEqual(jd._judge_run("sonnet", "SYS", "input", judge="planner"), "")
                run.assert_not_called()
                self.assertTrue(jd._judge_ctx.paused)
                self.assertEqual(jd._limit_down()["bucket"], "five_hour")
                self.assertEqual(jd._auth_down_map(), {}, "a usage pause is not an auth failure")

    def test_codex_call_never_resolves_or_carries_an_anthropic_credential(self):
        self._helper()
        jd._judge_ctx.fsid = SID

        def codex_reply(command, **kwargs):
            with open(command[command.index("-o") + 1], "w") as output:
                output.write("ok")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(jd, "_judge_engine", return_value="codex"), \
                patch.object(jd, "_judge_auth", side_effect=AssertionError("a codex call resolves no billing")), \
                patch.object(jd, "_key_available", side_effect=AssertionError("nor reads the helper")), \
                patch.dict(os.environ, {name: AMBIENT for name in CREDENTIAL_NAMES}), \
                patch.object(jd.subprocess, "run", side_effect=codex_reply) as run:
            self.assertEqual(jd._judge_run("synthetic-model", "SYS", "input", judge="planner"), "ok")
        run.assert_called_once()
        for name in CREDENTIAL_NAMES:
            self.assertNotIn(name, run.call_args.kwargs["env"])
        self.assertNotIn("--settings", run.call_args.args[0])


class CredentialErrorNote(_JudgeAuthBase):
    """A settings file romp cannot read is the one credential failure left; its note names the file and
    never any other program's output."""

    def test_a_credential_error_speaks_in_its_own_words_and_anything_else_is_generic(self):
        exc = jd._cred.CredentialError("Claude Code settings file is not valid JSON: /synthetic/settings.json")
        self.assertEqual(jd._credential_error_note(exc), str(exc))
        self.assertEqual(jd._credential_error_note(RuntimeError("synthetic-sensitive-output")),
                         "API credential source failed")

    def test_unreadable_settings_bill_the_login_and_say_so_once(self):
        """_judge_auth raises nothing for a settings fault: an unreadable Claude Code settings file reads as
        no helper, so unpicked calls bill the login, and the judge says so once per process on stderr (the
        SDK backend says the same once in its problem ring); never a silent fall to the other account
        (review 2026-09-08)."""
        import io
        from contextlib import redirect_stderr
        jd._SETTINGS_UNREADABLE_SAID.clear()
        exc = jd._cred.CredentialError("Claude Code settings file cannot be read: /synthetic/settings.json")
        with patch.object(jd._cred, "key_available", side_effect=exc):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(jd._judge_auth(SID), "login")
                self.assertEqual(jd._judge_auth(SID), "login")
        self.assertIn("cannot be read: /synthetic/settings.json", err.getvalue())
        self.assertIn("bill the login until it reads", err.getvalue())
        self.assertEqual(err.getvalue().count("romp-judge:"), 1, "said once per process")
        jd._SETTINGS_UNREADABLE_SAID.clear()

    def test_an_unexpected_env_failure_never_quotes_its_cause_and_never_falls_back_to_ambient_auth(self):
        private_output = "synthetic-sensitive-provider-output"
        self._helper()
        jd._judge_ctx.fsid = SID
        with patch.object(jd, "_judge_engine", return_value="claude"), \
                patch.object(jd, "_judge_env", side_effect=RuntimeError(private_output)), \
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": AMBIENT, "ANTHROPIC_AUTH_TOKEN": AMBIENT}), \
                patch.object(jd.subprocess, "run") as run, \
                patch.object(jd, "_log_judge_error") as log:
            self.assertEqual(jd._judge_run("sonnet", "SYS", "input", judge="planner"), "")
        run.assert_not_called()
        self.assertTrue(jd._judge_ctx.paused)
        row = jd._auth_down_map()[SID]
        self.assertEqual(row["mode"], "key", "the mode is the call's resolved billing")
        self.assertEqual(row["note"], "API credential source failed")
        self.assertNotIn(private_output, str(log.call_args))
        self.assertNotIn(private_output, json.dumps(row))


class AuthErrorClass(_JudgeAuthBase):
    def test_credential_failures_classify(self):
        for s in (NOT_LOGGED_IN, "API key is invalid · Please run /login",
                  "invalid x-api-key", "Failed to authenticate",
                  "OAuth token has expired", '{"type":"authentication_error"}'):
            self.assertTrue(jd._is_auth_error(s), s)

    def test_transient_failures_do_not(self):
        for s in ("overloaded_error", "prompt is too long", "rate_limit_error",
                  "Internal server error", "", None):
            self.assertFalse(jd._is_auth_error(s), repr(s))


class AuthLatch(_JudgeAuthBase):
    def test_mark_then_clear_round_trip(self):
        jd._auth_down_mark(SID, "key", NOT_LOGGED_IN)
        row = jd._auth_down_map().get(SID)
        self.assertTrue(row and row["mode"] == "key" and row["note"] == NOT_LOGGED_IN)
        self.assertGreater(row["t"], 0)
        jd._auth_down_clear(SID)
        self.assertNotIn(SID, jd._auth_down_map())

    def test_repeat_marks_keep_the_first_failure_time_and_skip_identical_writes(self):
        jd._auth_down_mark(SID, "key", NOT_LOGGED_IN)
        t0 = jd._auth_down_map()[SID]["t"]
        m0 = jd.JUDGE_AUTH.stat().st_mtime_ns
        jd._auth_down_mark(SID, "key", NOT_LOGGED_IN)     # same evidence: no write, no mtime churn
        self.assertEqual(jd.JUDGE_AUTH.stat().st_mtime_ns, m0)
        jd._auth_down_mark(SID, "key", "API key is invalid")   # new evidence: note moves, t holds
        row = jd._auth_down_map()[SID]
        self.assertEqual(row["t"], t0)
        self.assertEqual(row["note"], "API key is invalid")

    def test_no_session_no_row(self):
        jd._auth_down_mark(None, "key", NOT_LOGGED_IN)
        jd._auth_down_mark("", "key", NOT_LOGGED_IN)
        self.assertEqual(jd._auth_down_map(), {})

    def test_clear_without_a_row_writes_nothing(self):
        jd._auth_down_clear(SID)
        self.assertFalse(jd.JUDGE_AUTH.exists())


class JudgeRunBilling(_JudgeAuthBase):
    """_judge_run end to end with a fake CLI: the envelope drives the latch, the env and argv carry the
    billing. A helper is staged unless a test says otherwise, so the unpicked default is the key."""

    def _run(self, envelope, auth_reg=None, helper=True, model="sonnet"):
        if helper:
            self._helper()
        if auth_reg:
            self._reg(auth_reg)
        jd._judge_ctx.fsid = SID
        seen = {"n": 0}

        def fake_run(cmd, input=None, capture_output=None, text=None, cwd=None, env=None, timeout=None, **kw):
            if isinstance(cmd, str):                  # the staged helper, run by the kernel's held key: for real
                return _REAL_RUN(cmd, input=input, capture_output=capture_output, text=text, cwd=cwd, env=env,
                                 timeout=timeout, **kw)
            seen["n"] += 1
            seen["cmd"] = list(cmd)
            seen["env"] = env
            return SimpleNamespace(stdout=json.dumps(envelope), stderr="", returncode=0)

        saved = jd.subprocess.run
        jd.subprocess.run = fake_run
        try:
            with patch.object(jd, "_judge_engine", return_value="claude"):
                out = jd._judge_run(model, "SYS", "u", judge="planner", tier="triage")
        finally:
            jd.subprocess.run = saved
        return out, seen

    def test_a_not_logged_in_envelope_latches_and_the_call_reports_failure(self):
        out, seen = self._run({"is_error": True, "result": NOT_LOGGED_IN})
        self.assertEqual(out, "")
        row = jd._auth_down_map().get(SID)
        self.assertTrue(row, "credential refusal must latch judge-auth-down")
        self.assertEqual(row["mode"], "key", "the unpicked default on a helper box")
        self.assertIn("Not logged in", row["note"])
        self.assertEqual(seen["env"].get("ANTHROPIC_API_KEY"), HELD, "the key-billed child carried the held key")
        self.assertEqual(seen["n"], 1, "the helper ran a moment ago, inside the minute: no refresh, so no retry")
        self.assertEqual(self._helper_runs(), 1)

    def test_a_refusal_on_a_login_pick_latches_with_the_login_mode(self):
        # the card copy branches on the mode (KernelWiringAndFloorPins): sign in again vs fix the helper
        out, _ = self._run({"is_error": True, "result": NOT_LOGGED_IN}, auth_reg="login")
        self.assertEqual(out, "")
        self.assertEqual(jd._auth_down_map()[SID]["mode"], "login")

    def test_a_transient_error_envelope_does_not_latch(self):
        out, _ = self._run({"is_error": True, "result": "Overloaded, please retry"})
        self.assertEqual(out, "")
        self.assertEqual(jd._auth_down_map(), {})

    def test_the_next_success_clears_the_latch(self):
        self._run({"is_error": True, "result": NOT_LOGGED_IN})
        self.assertIn(SID, jd._auth_down_map())
        # inside the minute after the refusal the key-billed calls stand down (tests/test_judge_held_key.py);
        # past it the helper runs again and the next served call is the deciding event
        self.now[0] += jd._cred.HELD_KEY_REFRESH_GAP_S + 1
        out, _ = self._run({"result": "ok", "usage": {}, "duration_ms": 3})
        self.assertEqual(out, "ok")
        self.assertNotIn(SID, jd._auth_down_map())

    def test_a_key_billed_call_carries_the_held_key_alone_and_turns_the_childs_helper_off(self):
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        with patch.dict(os.environ, {name: AMBIENT for name in CREDENTIAL_NAMES + _op_names()}):
            out, seen = self._run({"result": "ok", "usage": {}, "duration_ms": 3})
        self.assertEqual(out, "ok")
        for name in CREDENTIAL_NAMES + _op_names():
            if name == "ANTHROPIC_API_KEY":
                self.assertEqual(seen["env"].get(name), HELD, "the kernel's held key, never the ambient value")
                continue
            self.assertNotIn(name, seen["env"], name)
        i = seen["cmd"].index("--settings")
        self.assertEqual(seen["cmd"][i:i + 2], HELPER_OFF, "the child must not run the helper again")

    def test_a_login_pick_launches_with_the_login_tokens_and_the_helper_disabled(self):
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": AMBIENT}):
            out, seen = self._run({"result": "ok", "usage": {}, "duration_ms": 3}, auth_reg="login")
        self.assertEqual(out, "ok")
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertEqual(seen["env"].get("CLAUDE_CODE_OAUTH_TOKEN"), "synthetic-login-token")
        i = seen["cmd"].index("--settings")
        self.assertEqual(seen["cmd"][i:i + 2], HELPER_OFF)

    def test_a_fast_login_pick_on_opus_launches_with_one_overlay_and_logs_the_readback(self):
        # end to end through _judge_run: the triage tier's Fast mode on, a login pick, an Opus model. The child gets ONE
        # --settings overlay carrying both keys, the login tokens, no key; the usage row keeps the envelope's
        # fast_mode_state, the CLI's own word on whether fast engaged.
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        (jd.STATE / "judge-fast").write_text("on")
        jd._state_cache.clear()
        saved_usage = jd.USAGE
        jd.USAGE = jd.STATE / ("judge-usage-%s.jsonl" % os.getpid())
        try:
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": AMBIENT}):
                out, seen = self._run({"result": "ok", "usage": {}, "duration_ms": 3, "fast_mode_state": "on"},
                                      auth_reg="login", model="opus")
            rows = [json.loads(ln) for ln in jd.USAGE.read_text().splitlines()]
        finally:
            jd.USAGE = saved_usage
            (jd.STATE / "judge-fast").unlink()
            jd._state_cache.clear()
        self.assertEqual(out, "ok")
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertEqual(seen["env"].get("CLAUDE_CODE_OAUTH_TOKEN"), "synthetic-login-token")
        self.assertEqual(seen["cmd"].count("--settings"), 1)
        self.assertEqual(json.loads(seen["cmd"][seen["cmd"].index("--settings") + 1]),
                         {"fastMode": True, "apiKeyHelper": ""})
        self.assertEqual([(r["model"], r["fast"]) for r in rows], [("opus", "on")])


class KeylessKeyBilledCalls(_JudgeAuthBase):
    """A session whose pick says `key` on a box whose Claude Code settings carry no apiKeyHelper. The
    2026-09-07 shape ran the child keyless on Claude Code's own credential with the login tokens as a
    fallback and a once-per-process stderr line; since 2026-09-08 there is nothing to fall back from: the
    child launches keyless like every other key-billed child, gets no login token (it would outrank the
    helper), and nothing is said, because nothing is missing."""

    def _stderr(self, fn):
        import io
        from contextlib import redirect_stderr
        out = io.StringIO()
        with redirect_stderr(out):
            r = fn()
        return r, out.getvalue()

    def test_a_keyless_key_billed_call_runs_end_to_end_and_latches_nothing(self):
        self._reg("key")
        self.assertFalse(jd._key_available())
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        jd._judge_ctx.fsid = SID
        seen = {}
        # the fake envelope carries the modelUsage map a current CLI reports, naming the version the bare
        # alias resolved to (the one the alias table assumes, so nothing drifts); without the map the judge
        # says once per process that the served version cannot be checked, and whether this test saw that
        # line depended on which class in the module had already spent the notice
        served_id = "claude-sonnet-%d-%d" % jd._ALIAS_HEAD["sonnet"]
        prior_served = jd._ALIAS_SERVED.get("sonnet")     # the answered call records the served version per process

        def put_back():
            if prior_served is None:
                jd._ALIAS_SERVED.pop("sonnet", None)
            else:
                jd._ALIAS_SERVED["sonnet"] = prior_served
        self.addCleanup(put_back)

        def fake_run(cmd, input=None, env=None, **kw):
            seen["cmd"] = list(cmd)
            seen["env"] = env
            return SimpleNamespace(stdout=json.dumps({"result": "ok", "usage": {}, "duration_ms": 3,
                                                      "modelUsage": {served_id: {"outputTokens": 4}}}),
                                   stderr="", returncode=0)
        with patch.object(jd, "_judge_engine", return_value="claude"), \
                patch.object(jd.subprocess, "run", side_effect=fake_run):
            out, err = self._stderr(lambda: jd._judge_run("sonnet", "SYS", "u", judge="planner", tier="triage"))
        self.assertEqual(out, "ok")
        for name in CREDENTIAL_NAMES:
            self.assertNotIn(name, seen["env"], name)
        self.assertNotIn("--settings", seen["cmd"])
        self.assertEqual(err, "", "nothing is missing, so nothing is announced")
        self.assertEqual(jd._auth_down_map(), {}, "a call that ran is not an auth failure")
        self.assertFalse(jd._judge_ctx.paused)


class RetiredKeyPlumbing(unittest.TestCase):
    """The names the 2026-09-08 retirement removed stay gone: any of them coming back is a key path."""

    def test_the_judge_module_carries_none_of_the_retired_names(self):
        for name in ("_keysrc", "_WORK_KEY_FN", "_WORK_KEY_CONFIGURED_FN", "_work_key", "_work_key_configured",
                     "_key_source_unconfigured", "_UNKEYED_SAID", "_KEY_GATE", "_PASS_GEN", "_KEY_GATE_CV",
                     "_KEY_INFLIGHT", "_resolve_work_key_gated", "KeySourceError"):
            self.assertFalse(hasattr(jd, name), name)

    def test_the_judge_module_reads_credentials_through_the_one_module(self):
        self.assertEqual(jd._cred.__name__, "romp_credentials")
        for name in ("CredentialError", "api_key_helper", "key_available", "OP_ENV_NAMES", "OP_ENV_PREFIX",
                     "LOGIN_TOKEN_VARS"):
            self.assertTrue(hasattr(jd._cred, name), name)


class KernelWiringAndFloorPins(unittest.TestCase):
    """The kernel side, pinned the way every build_feed behavior is (inspect.getsource)."""

    @classmethod
    def setUpClass(cls):
        cls.km = load_source("romp_kernel_authbill", os.path.join(BIN, "romp-kernel"))

    def test_the_kernel_wires_the_login_tokens_and_no_key(self):
        import inspect
        src = inspect.getsource(self.km._sdk_locked)
        self.assertIn("jd._LOGIN_AUTH_ENV_FN = sbmod.startup_auth_env", src)
        for retired in ("_WORK_KEY_FN", "_WORK_KEY_CONFIGURED_FN", "work_api_key"):
            self.assertNotIn(retired, src, retired)

    def test_build_feed_floors_a_latched_session_yielding_to_the_live_floors(self):
        import inspect
        src = inspect.getsource(self.km.build_feed) + inspect.getsource(self.km._feed_session_entry)   # T368: the loop body
        self.assertIn("_jauth_map = jd._auth_down_map()", src)
        self.assertIn("jerr and api_top is None and perm_top is None", src)
        self.assertIn('column = ("needs_input" if (api_block or nid == jauth_top or nid == perm_top', src)

    def test_the_floored_card_carries_the_judgeAuth_story(self):
        import inspect
        src = inspect.getsource(self.km._feed_session_entry)   # T368: build_feed's per-session loop body
        self.assertIn('"state": "judgeAuth"', src)
        # the key-mode copy points at the one key path left (2026-09-08); the login copy is unchanged
        self.assertIn("the API key its judges bill is being refused. Fix the key behind Claude Code's apiKeyHelper "
                      "(rotate the vault item) or switch which account this session bills", src)
        self.assertIn("the login its judges bill is being refused. Sign in again (claude /login)", src)
        self.assertNotIn("Fix the key (service.env)", src, "the retired env-file copy")

    def test_the_judge_auth_classifier_mirrors_the_kernels(self):
        # judge.py loads standalone, so the classifier is a copy, not an import: the two must agree
        # on the strings that matter (each side may only ever grow strictly looser together).
        for s in ("Not logged in", "API key is invalid", "invalid x-api-key",
                  "failed to authenticate", "OAuth token has expired", "oauth token revoked",
                  "authentication_error", "Invalid API key · Fix external API key",
                  "Invalid auth token · Fix external auth token", "overloaded", "rate_limit_error", ""):
            self.assertEqual(jd._is_auth_error(s), self.km._is_auth_error(s), s)

    def test_the_feed_bundle_carries_the_chip(self):
        ts = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "feed.ts")).read()
        css = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "feed.css")).read()
        self.assertIn('it.blocked?.state === "judgeAuth"', ts)
        self.assertIn("fask-jauth", ts)
        self.assertIn(".fask-jauth", css)


if __name__ == "__main__":
    unittest.main()
