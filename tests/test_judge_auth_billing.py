#!/usr/bin/env python3
"""Judges bill the account of the session they judge (the user 2026-08-12).

The incident this pins: per-session billing (f48af49c) claims the manager env's
ANTHROPIC_API_KEY out of os.environ (sdk_backend.work_api_key) so no session CLI
inherits it ambiently — but the judges' subprocess env was still a plain copy of
os.environ, taken AFTER that claim. On a host whose only credential is the env key
(no login), every judge call refused "Not logged in · Please run /login" for 13
hours (~53k errors) while the cards sat parked in Working with nothing on screen
saying why. The mechanics under test:

  * _work_key READS the key, never claims it: through the kernel's wire
    (_WORK_KEY_FN → sdk_backend.work_api_key, the one claimer) once it lands, and
    straight from os.environ before it / standalone — no second pop to race.
  * _judge_auth resolves a call's billing to the JUDGED SESSION's own pick (the
    registry's `auth`), with the session picker's exact fallback: explicit
    'login' → login; anything else → key when one exists, else login.
  * _judge_env strips the ambient key from every child env and injects it back
    explicitly for a key-mode call only (removal, not blanking — the CLI treats
    an empty var as key-mode-without-a-key and refuses).
  * A credential-class error envelope LATCHES judge-auth-down for the session
    (STATE/judge-auth.json); the session's next successful call clears it. Both
    edges are events — no timers, no per-build re-derivation.
  * build_feed floors a latched session's focus card to needs-you wearing the
    "judgeAuth" story (source pins, the build_feed test pattern), and the feed
    bundle carries the chip.

Synthetic sids only; the fixture key is an invented string; no real key material.
"""
import json
import os
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp())
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
os.environ.pop("ROMP_API_KEY_REF", None)
jd = SourceFileLoader("romp_judge_authbill", os.path.join(BIN, "romp-judge")).load_module()

FAKE_KEY = "romp-test-fixture-key-not-real"   # nothing under test validates the shape, so no sk- prefix:
                                              # the maintainer's gitleaks pre-commit hook rightly refuses
                                              # anything that even looks like a real key in a commit
SID = "11111111-2222-3333-4444-555555555555"
NOT_LOGGED_IN = "Not logged in · Please run /login"   # the CLI's live refusal, verbatim shape


class _JudgeAuthBase(unittest.TestCase):
    """Clean slate per test: no wire, no latch file, no ambient key, no session reg."""

    def setUp(self):
        self._env_before = os.environ.pop("ANTHROPIC_API_KEY", None)
        self._fn_before = jd._WORK_KEY_FN
        self._configured_before = jd._WORK_KEY_CONFIGURED_FN
        self._login_before = jd._LOGIN_AUTH_ENV_FN
        jd._WORK_KEY_FN = None
        jd._WORK_KEY_CONFIGURED_FN = None
        jd._LOGIN_AUTH_ENV_FN = None
        jd._auth_cache[:] = [None, {}]
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        for p in (jd.JUDGE_AUTH, jd.SDKDIR / (SID + ".json"),
                  jd.STATE / "retry-paused.json", jd.STATE / "usage.json"):
            try:
                p.unlink()
            except OSError:
                pass

    def tearDown(self):
        jd._WORK_KEY_FN = self._fn_before
        jd._WORK_KEY_CONFIGURED_FN = self._configured_before
        jd._LOGIN_AUTH_ENV_FN = self._login_before
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if self._env_before is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._env_before
        jd._judge_ctx.fsid = None
        jd._judge_ctx.paused = False
        # leave NOTHING latched in the shared STATE: the suite runs every test file against one
        # XDG state home, and a leftover judge-auth.json row for the shared synthetic sid floors
        # OTHER files' build_feed cards to needs-you (25 stays-in-Working tests, found 2026-08-12)
        jd._auth_cache[:] = [None, {}]
        for p in (jd.JUDGE_AUTH, jd.SDKDIR / (SID + ".json")):
            try:
                p.unlink()
            except OSError:
                pass

    def _reg(self, auth):
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "auth": auth}))


class WorkKeyRead(_JudgeAuthBase):
    def test_reads_through_the_kernel_wire_once_it_lands(self):
        jd._WORK_KEY_FN = lambda: FAKE_KEY
        self.assertEqual(jd._work_key(), FAKE_KEY)

    def test_reads_the_environment_before_the_wire_lands_without_claiming_it(self):
        os.environ["ANTHROPIC_API_KEY"] = FAKE_KEY
        self.assertEqual(jd._work_key(), FAKE_KEY)
        # never a second claimer: the variable is still there for sdk_backend's own pop
        self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), FAKE_KEY)

    def test_a_broken_wire_propagates_instead_of_selecting_login(self):
        jd._WORK_KEY_FN = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            jd._work_key()


class JudgeBillingResolution(_JudgeAuthBase):
    def test_defaults_to_the_key_when_one_exists(self):
        jd._WORK_KEY_FN = lambda: FAKE_KEY
        self.assertEqual(jd._judge_auth(SID), "key")      # no reg on disk
        self.assertEqual(jd._judge_auth(None), "key")     # fleet-level call, same default

    def test_defaults_to_login_when_no_key_exists(self):
        self.assertEqual(jd._judge_auth(SID), "login")
        self.assertEqual(jd._judge_auth(None), "login")

    def test_an_explicit_login_pick_wins_over_an_available_key(self):
        jd._WORK_KEY_FN = lambda: FAKE_KEY
        self._reg("login")
        self.assertEqual(jd._judge_auth(SID), "login")

    def test_a_key_pick_rides_the_key(self):
        jd._WORK_KEY_FN = lambda: FAKE_KEY
        self._reg("key")
        self.assertEqual(jd._judge_auth(SID), "key")

    def test_a_key_pick_with_no_key_keeps_its_billing_intent(self):
        self._reg("key")
        self.assertEqual(jd._judge_auth(SID), "key")
        with self.assertRaises(jd._keysrc.KeySourceError):
            jd._judge_env("triage", "key")


class JudgeEnvBilling(_JudgeAuthBase):
    def test_key_mode_injects_the_key_explicitly(self):
        jd._WORK_KEY_FN = lambda: FAKE_KEY
        env = jd._judge_env("triage", "key")
        self.assertEqual(env.get("ANTHROPIC_API_KEY"), FAKE_KEY)

    def test_login_mode_carries_no_key_at_all(self):
        jd._WORK_KEY_FN = lambda: FAKE_KEY
        env = jd._judge_env("triage", "login")
        self.assertNotIn("ANTHROPIC_API_KEY", env)        # removal, not blanking

    def test_the_ambient_key_is_stripped_from_a_login_mode_child_standalone(self):
        # standalone (romp-judge --once): nobody claimed the env key, but a login-mode child
        # must still not inherit it — billing is an explicit choice per call, never ambient
        os.environ["ANTHROPIC_API_KEY"] = FAKE_KEY
        env = jd._judge_env("index", "login")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        env = jd._judge_env("index", "key")
        self.assertEqual(env.get("ANTHROPIC_API_KEY"), FAKE_KEY)

    def test_the_existing_env_contract_survives(self):
        os.environ["TMUX"] = "sock,1,0"
        try:
            env = jd._judge_env("index", "login", model="haiku")
            self.assertNotIn("TMUX", env)
            self.assertEqual(env.get("ROMP_SUMMARIZING"), "1")
            # the index tier's thinking-off var rides UNCONDITIONALLY (PR #880 review): the honored lever on
            # models that take thinking:disabled, a harmless no-op where the CLI drops it (Fable) and
            # `--effort` lands instead — the billing plumbing is the same either way
            self.assertEqual(env.get("MAX_THINKING_TOKENS"), "0")
            self.assertEqual(jd._judge_env("index", "login", model="fable").get("MAX_THINKING_TOKENS"), "0")
        finally:
            os.environ.pop("TMUX", None)


class RuntimeJudgeBilling(_JudgeAuthBase):
    def test_runtime_reference_resolves_once_per_key_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "service.env")
            with open(path, "w") as config:
                config.write("ROMP_API_KEY_REF=op://test-vault/test-item/credential\n")
            with patch.dict(os.environ, {"ROMP_SERVICE_ENV_FILE": path}), \
                    patch.object(jd._keysrc.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0, stdout=FAKE_KEY.encode())) as provider:
                for count in (1, 2):
                    auth = jd._judge_auth(SID)
                    self.assertEqual(provider.call_count, count - 1, "billing metadata never resolves")
                    env = jd._judge_env("triage", auth)
                    self.assertEqual(env["ANTHROPIC_API_KEY"], FAKE_KEY)
                    self.assertEqual(provider.call_count, count)

    def test_successful_key_call_resolves_the_provider_wire_once(self):
        jd._WORK_KEY_FN = Mock(return_value=FAKE_KEY)
        jd._WORK_KEY_CONFIGURED_FN = Mock(return_value=True)
        jd._judge_ctx.fsid = SID
        with patch.object(jd, "_judge_engine", return_value="claude"), \
                patch.object(jd.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stderr="", stdout=json.dumps({"result": "ok"}))) as run:
            self.assertEqual(jd._judge_run("sonnet", "SYS", "input", judge="planner"), "ok")
        jd._WORK_KEY_FN.assert_called_once_with()
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["env"]["ANTHROPIC_API_KEY"], FAKE_KEY)

    def test_login_env_does_not_resolve_and_restores_claimed_login_tokens(self):
        jd._WORK_KEY_FN = Mock(side_effect=RuntimeError("must not resolve"))
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic-ambient-key",
                                     "ANTHROPIC_AUTH_TOKEN": "synthetic-ambient-bearer"}):
            env = jd._judge_env("triage", "login")
        jd._WORK_KEY_FN.assert_not_called()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "synthetic-login-token")

    def test_exhausted_login_window_pauses_without_inspecting_or_resolving_a_key(self):
        self._reg("login")
        jd._WORK_KEY_FN = Mock(side_effect=RuntimeError("must not resolve"))
        jd._WORK_KEY_CONFIGURED_FN = Mock(side_effect=RuntimeError("must not inspect"))
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
                jd._WORK_KEY_FN.assert_not_called()
                jd._WORK_KEY_CONFIGURED_FN.assert_not_called()
                self.assertTrue(jd._judge_ctx.paused)
                self.assertEqual(jd._limit_down()["bucket"], "five_hour")
                self.assertEqual(jd._auth_down_map(), {}, "a usage pause is not an auth failure")

    def test_codex_call_never_selects_or_retrieves_an_anthropic_credential(self):
        jd._WORK_KEY_FN = Mock(side_effect=RuntimeError("must not resolve"))
        jd._WORK_KEY_CONFIGURED_FN = Mock(side_effect=RuntimeError("must not inspect"))
        jd._judge_ctx.fsid = SID

        def codex_reply(command, **kwargs):
            with open(command[command.index("-o") + 1], "w") as output:
                output.write("ok")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(jd, "_judge_engine", return_value="codex"), \
                patch.dict(os.environ, {name: "synthetic-ambient-credential" for name in
                           ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")}), \
                patch.object(jd.subprocess, "run", side_effect=codex_reply) as run:
            self.assertEqual(jd._judge_run("synthetic-model", "SYS", "input", judge="planner"), "ok")
        jd._WORK_KEY_FN.assert_not_called()
        jd._WORK_KEY_CONFIGURED_FN.assert_not_called()
        run.assert_called_once()
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.assertNotIn(name, run.call_args.kwargs["env"])

    def test_provider_failure_pauses_judging_and_never_falls_back_to_ambient_auth(self):
        private_output = "synthetic-sensitive-provider-output"
        jd._WORK_KEY_FN = Mock(side_effect=RuntimeError(private_output))
        jd._WORK_KEY_CONFIGURED_FN = lambda: True
        jd._judge_ctx.fsid = SID
        with patch.object(jd, "_judge_engine", return_value="claude"), \
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic-ambient-key",
                                       "ANTHROPIC_AUTH_TOKEN": "synthetic-ambient-bearer"}), \
                patch.object(jd.subprocess, "run") as run, \
                patch.object(jd, "_log_judge_error") as log:
            self.assertEqual(jd._judge_run("sonnet", "SYS", "input", judge="planner"), "")
        run.assert_not_called()
        jd._WORK_KEY_FN.assert_called_once_with()
        self.assertTrue(jd._judge_ctx.paused, "configuration outages must not consume summary give-up attempts")
        row = jd._auth_down_map()[SID]
        self.assertEqual(row["mode"], "key")
        self.assertEqual(row["note"], "API credential source failed")
        self.assertNotIn(private_output, str(log.call_args))
        self.assertNotIn(private_output, json.dumps(row))

    def test_missing_explicit_key_pauses_the_call_and_reports_auth_down(self):
        self._reg("key")
        jd._WORK_KEY_FN = lambda: ""
        jd._judge_ctx.fsid = SID
        with patch.object(jd, "_judge_engine", return_value="claude"), \
                patch.object(jd.subprocess, "run") as run:
            self.assertEqual(jd._judge_run("sonnet", "SYS", "input", judge="planner"), "")
        run.assert_not_called()
        self.assertTrue(jd._judge_ctx.paused)
        self.assertEqual(jd._auth_down_map()[SID]["mode"], "key")


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


class JudgeRunLatchAndInjection(_JudgeAuthBase):
    """_judge_run end to end with a fake CLI: the envelope drives the latch, the env carries the billing."""

    def _run(self, envelope, auth_reg=None, key=FAKE_KEY):
        if key:
            jd._WORK_KEY_FN = lambda: key
        if auth_reg:
            self._reg(auth_reg)
        jd._judge_ctx.fsid = SID
        seen = {}

        def fake_run(cmd, input=None, capture_output=None, text=None, cwd=None, env=None, timeout=None):
            seen["env"] = env
            return SimpleNamespace(stdout=json.dumps(envelope), stderr="", returncode=0)

        saved = jd.subprocess.run
        jd.subprocess.run = fake_run
        try:
            out = jd._judge_run("sonnet", "SYS", "u", judge="planner", tier="triage")
        finally:
            jd.subprocess.run = saved
        return out, seen

    def test_a_not_logged_in_envelope_latches_and_the_call_reports_failure(self):
        out, seen = self._run({"is_error": True, "result": NOT_LOGGED_IN})
        self.assertEqual(out, "")
        row = jd._auth_down_map().get(SID)
        self.assertTrue(row, "credential refusal must latch judge-auth-down")
        self.assertEqual(row["mode"], "key")
        self.assertIn("Not logged in", row["note"])
        self.assertEqual(seen["env"].get("ANTHROPIC_API_KEY"), FAKE_KEY)   # key-mode call carried the key

    def test_a_transient_error_envelope_does_not_latch(self):
        out, _ = self._run({"is_error": True, "result": "Overloaded, please retry"})
        self.assertEqual(out, "")
        self.assertEqual(jd._auth_down_map(), {})

    def test_the_next_success_clears_the_latch(self):
        self._run({"is_error": True, "result": NOT_LOGGED_IN})
        self.assertIn(SID, jd._auth_down_map())
        out, _ = self._run({"result": "ok", "usage": {}, "duration_ms": 3})
        self.assertEqual(out, "ok")
        self.assertNotIn(SID, jd._auth_down_map())

    def test_a_login_pick_launches_the_judge_with_a_clean_env(self):
        _, seen = self._run({"result": "ok", "usage": {}, "duration_ms": 3}, auth_reg="login")
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])


class KernelWiringAndFloorPins(unittest.TestCase):
    """The kernel side, pinned the way every build_feed behavior is (inspect.getsource)."""

    @classmethod
    def setUpClass(cls):
        cls.km = SourceFileLoader("romp_kernel_authbill", os.path.join(BIN, "romp-kernel")).load_module()

    def test_the_kernel_wires_judges_to_the_one_key_claimer(self):
        import inspect
        self.assertIn("jd._WORK_KEY_FN = sbmod.work_api_key", inspect.getsource(self.km._sdk_locked))

    def test_build_feed_floors_a_latched_session_yielding_to_the_live_floors(self):
        import inspect
        src = inspect.getsource(self.km.build_feed)
        self.assertIn("_jauth_map = jd._auth_down_map()", src)
        self.assertIn("jerr and api_top is None and perm_top is None", src)
        self.assertIn('column = ("needs_input" if (api_block or nid == jauth_top or nid == perm_top', src)

    def test_the_floored_card_carries_the_judgeAuth_story(self):
        import inspect
        src = inspect.getsource(self.km.build_feed)
        self.assertIn('"state": "judgeAuth"', src)
        self.assertIn("the API key its judges bill is being refused. Fix the key (service.env)", src)
        self.assertIn("the login its judges bill is being refused. Sign in again (claude /login)", src)

    def test_the_judge_auth_classifier_mirrors_the_kernels(self):
        # judge.py loads standalone, so the classifier is a copy, not an import — the two must agree
        # on the strings that matter (each side may only ever grow strictly looser together).
        for s in ("Not logged in", "API key is invalid", "invalid x-api-key",
                  "failed to authenticate", "OAuth token has expired", "oauth token revoked",
                  "authentication_error", "overloaded", "rate_limit_error", ""):
            self.assertEqual(jd._is_auth_error(s), self.km._is_auth_error(s), s)

    def test_the_feed_bundle_carries_the_chip(self):
        ts = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "feed.ts")).read()
        css = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "feed.css")).read()
        self.assertIn('it.blocked?.state === "judgeAuth"', ts)
        self.assertIn("fask-jauth", ts)
        self.assertIn(".fask-jauth", css)


class OpCredentialAndRetrievalGate(_JudgeAuthBase):
    """Review finds of 2026-09-05, at the judge boundary."""

    def test_a_judge_child_never_inherits_the_op_credential_when_romp_runs_op(self):
        with patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-op-token", "OP_SESSION_acct": "s",
                                     "ROMP_API_KEY_REF": "op://test-vault/test-item/credential"}):
            env = jd._judge_env("triage", auth="login")
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", env)
        self.assertNotIn("OP_SESSION_acct", env)

    def test_a_helper_box_keeps_op_in_its_judge_children(self):
        """No reference configured: the sessions (and judges) may authenticate through a helper that runs op
        itself, so romp leaves op's environment alone."""
        with patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-op-token"}), \
             patch.object(jd._keysrc, "_OP_ENV", {}):
            env = jd._judge_env("triage", auth="login")
        self.assertEqual(env.get("OP_SERVICE_ACCOUNT_TOKEN"), "synthetic-op-token")

    def test_a_failed_retrieval_is_not_retried_until_the_next_pass_or_a_new_source(self):
        calls = []
        def failing():
            calls.append(1)
            raise jd._keysrc.KeySourceError("1Password credential retrieval timed out; check op authentication")
        jd._WORK_KEY_FN = failing
        jd._WORK_KEY_CONFIGURED_FN = lambda: True
        saved = dict(jd._KEY_GATE); saved_gen = jd._PASS_GEN[0]
        try:
            jd._KEY_GATE.update(fp=None, gen=None, note="")
            jd.begin_pass_frame()                                  # a pass is running
            with self.assertRaisesRegex(jd._keysrc.KeySourceError, "timed out"):
                jd._judge_env("triage", auth="key")
            with self.assertRaisesRegex(jd._keysrc.KeySourceError, "timed out"):
                jd._judge_env("triage", auth="key")                # same pass, same source: fails at once
            self.assertEqual(len(calls), 1, "one retrieval per pass, not one per call")
            self.assertFalse(jd.begin_pass_frame(), "a tier joining the open pass…")
            with self.assertRaises(jd._keysrc.KeySourceError):
                jd._judge_env("triage", auth="key")
            self.assertEqual(len(calls), 1, "…shares its gate: no retry mid-pass")
            jd.end_pass_frame(); jd.begin_pass_frame()             # the NEXT pass is the deciding event
            with self.assertRaises(jd._keysrc.KeySourceError):
                jd._judge_env("triage", auth="key")
            self.assertEqual(len(calls), 2)
            jd._WORK_KEY_FN = lambda: FAKE_KEY                     # …and a retrieval that works clears nothing it need not
            jd.end_pass_frame(); jd.begin_pass_frame()
            self.assertEqual(jd._judge_env("triage", auth="key")["ANTHROPIC_API_KEY"], FAKE_KEY)
        finally:
            jd._KEY_GATE.update(saved); jd._PASS_GEN[0] = saved_gen
            jd.end_pass_frame()

    def test_the_first_retrieval_of_a_pass_gates_the_concurrent_wave(self):
        """Six judge threads reach a pass's first key call together; the first retrieves, the rest wait for
        its verdict instead of each spawning op and waiting out a timeout (review find, 2026-09-05)."""
        import threading
        calls, gate = [], threading.Event()
        def slow_failing():
            calls.append(1); gate.wait(2.0)
            raise jd._keysrc.KeySourceError("1Password credential retrieval timed out; check op authentication")
        jd._WORK_KEY_FN = slow_failing
        saved = dict(jd._KEY_GATE); saved_gen = jd._PASS_GEN[0]
        results = []
        def worker():
            try:
                jd._judge_env("triage", auth="key"); results.append("ok")
            except jd._keysrc.KeySourceError as e:
                results.append(str(e))
        try:
            jd._KEY_GATE.update(fp=None, gen=None, note=""); jd.begin_pass_frame()
            threads = [threading.Thread(target=worker) for _ in range(6)]
            for th in threads: th.start()
            time.sleep(0.3); gate.set()
            for th in threads: th.join(5.0)
            self.assertEqual(len(calls), 1, "one retrieval for the whole wave")
            self.assertEqual(len(results), 6)
            self.assertTrue(all("timed out" in r for r in results))
        finally:
            jd._KEY_GATE.update(saved); jd._PASS_GEN[0] = saved_gen; jd.end_pass_frame()

    def test_standalone_callers_without_a_pass_frame_retry_every_call(self):
        calls = []
        def failing():
            calls.append(1)
            raise jd._keysrc.KeySourceError("1Password credential retrieval failed")
        jd._WORK_KEY_FN = failing
        saved = dict(jd._KEY_GATE); saved_gen = jd._PASS_GEN[0]
        try:
            jd._KEY_GATE.update(fp=None, gen=None, note=""); jd._PASS_GEN[0] = 0
            for _ in range(2):
                with self.assertRaises(jd._keysrc.KeySourceError):
                    jd._judge_env("triage", auth="key")
            self.assertEqual(len(calls), 2)
        finally:
            jd._KEY_GATE.update(saved); jd._PASS_GEN[0] = saved_gen


if __name__ == "__main__":
    unittest.main()
