#!/usr/bin/env python3
"""Per-session env vars, spawn-time slice (the user 2026-08-17): two SDK sessions in the SAME
directory can run with different environments. Before this, env came only from directory-scoped
.claude/settings*.json — every session in the repo got it, and it outlived the session.

The mechanics under test:
  * `env_request_error` is the ONE validator both doors share (the /new handler mirrors it): a
    payload is a dict of NAME→string-value pairs, names matching [A-Za-z_][A-Za-z0-9_]*; anything
    else is named loudly, never skipped.
  * spawn() persists the dict in the session's reg (`env`) — the same home model/effort live in —
    and refuses a bad payload outright rather than writing a poisoned reg.
  * flag_settings_path folds a non-empty env into the per-sid settings payload beside ultracode /
    fastMode, and the return-""-when-no-keys contract stands.
  * _options threads the session's env into that file at EVERY connect — the file is rewritten on
    each use, so reconnects re-assert the reg's env by construction (pinned by tampering the file
    between two _options calls).
  * set_env mirrors set_effort's shape (persist + reconnect to apply; env is connect-time), minus
    the badge/chip machinery that belongs to the not-yet-built UI slice; an UNCHANGED re-assert
    (the `romp new --env` re-brief on a standing session, or the fresh-spawn echo) skips the
    reconnect — the asked-for env is already in force or already queued.
  * fork() inherits the parent's env like model/auth — it is that conversation, continued elsewhere.

Synthetic fixtures only: FEATURE_FLAG=1 shapes, placeholder sids — never credential-shaped values
(the gitleaks scanner reads this repo too).
"""
import json
import os
import tempfile
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
sb = SourceFileLoader("romp_sdk_backend_env", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

PARENT = "11111111-2222-3333-4444-555555555555"
CHILD = "66666666-7777-8888-9999-aaaaaaaaaaaa"
ENV = {"FEATURE_FLAG": "1", "UI_THEME": "dark"}


class _Backend(unittest.TestCase):
    """Base: a backend on a temp state dir, no real CLI, no real key claim."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._stash_before = sb._WORK_KEY
        sb._WORK_KEY = ""                          # never claim a real key from this process's env
        self._fetch_before = sb._fetch_key_fast_org
        sb._fetch_key_fast_org = lambda key: None  # the fast-org probe is a real HTTPS GET — never from a test
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        sb._WORK_KEY = self._stash_before
        sb._fetch_key_fast_org = self._fetch_before

    def _reg(self, sid):
        return sb.read_reg(self.be.state_dir, sid)

    def _sess(self, sid):
        return sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))


class EnvRequestError(unittest.TestCase):
    """The shared validator: loud and specific, never a silent skip."""

    def test_valid_payloads_pass(self):
        self.assertEqual(sb.env_request_error({"FEATURE_FLAG": "1"}), "")
        self.assertEqual(sb.env_request_error({"_UNDER": "x", "A9": ""}), "",
                         "an empty VALUE is meaningful — explicitly setting empty")
        self.assertEqual(sb.env_request_error({}), "", "an empty dict is a valid (vacuous) payload")

    def test_a_non_dict_is_named(self):
        for bad in ("FEATURE_FLAG=1", ["FEATURE_FLAG"], 7, None):
            err = sb.env_request_error(bad)
            self.assertIn("env", err)
            self.assertTrue(err, "a non-object payload must be refused, not coerced")

    def test_a_bad_name_is_named(self):
        for bad in ("9BAD", "", "BAD-NAME", "BAD NAME", "über"):
            err = sb.env_request_error({bad: "1"})
            self.assertIn("[A-Za-z_][A-Za-z0-9_]*", err,
                          "the error must teach the alphabet, not just refuse: %r" % bad)

    def test_a_non_string_value_is_named(self):
        for bad in (1, None, True, {"nested": "no"}):
            err = sb.env_request_error({"FEATURE_FLAG": bad})
            self.assertIn("FEATURE_FLAG", err,
                          "the offending NAME must be in the error (fail loudly): %r" % (bad,))

    def test_a_nul_byte_in_a_value_is_named(self):
        # an execve envp entry is a NUL-terminated C string, so a NUL value is unfulfillable by
        # definition — accepted, it bakes an env the CLI can only truncate or throw on into the reg
        err = sb.env_request_error({"FEATURE_FLAG": "1\x00x"})
        self.assertIn("FEATURE_FLAG", err, "the offending NAME must be in the error")
        self.assertIn("NUL", err, "the error names the actual problem")

    def test_other_control_bytes_stay_legitimate(self):
        self.assertEqual(sb.env_request_error({"FEATURE_FLAG": "line1\nline2\ttabbed"}), "",
                         "NUL only — newlines and tabs are legitimate env content")

    def test_the_identity_names_are_refused(self):
        # options.env owns ROMP_SID / ROMP_SESSION_NAME (the identity overlay below): a user var of
        # either name would silently shadow or be shadowed by the identity, breaking `romp end self`
        # with nothing pointing at the cause — refused at the door instead, like every bad payload
        for name in ("ROMP_SID", "ROMP_SESSION_NAME"):
            err = sb.env_request_error({name: "x"})
            self.assertIn(name, err, "the reserved NAME must be in the error")
            self.assertIn("romp sets", err, "the error teaches WHO owns the name, not just 'no'")
        self.assertTrue(sb.env_request_error({"FEATURE_FLAG": "1", "ROMP_SID": "x"}),
                        "a reserved name refuses the WHOLE payload, never a silent skip")


class FlagSettingsEnv(unittest.TestCase):
    """flag_settings_path folds env in beside ultracode/fastMode; the ""-when-empty contract stands."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _read(self, p):
        return json.loads(Path(p).read_text())

    def test_env_alone_writes_the_file(self):
        p = sb.flag_settings_path(self.d, PARENT, env=ENV)
        self.assertTrue(p)
        self.assertEqual(self._read(p), {"env": ENV})

    def test_env_rides_beside_the_boolean_keys(self):
        p = sb.flag_settings_path(self.d, PARENT, ultracode=True, fast=True, env=ENV)
        got = self._read(p)
        self.assertEqual(got["env"], ENV)
        self.assertTrue(got["ultracode"] and got["fastMode"],
                        "env must merge INTO the payload, not replace the keys already riding it")

    def test_no_keys_still_returns_empty(self):
        self.assertEqual(sb.flag_settings_path(self.d, PARENT), "")
        self.assertEqual(sb.flag_settings_path(self.d, PARENT, env=None), "")
        self.assertEqual(sb.flag_settings_path(self.d, PARENT, env={}), "",
                         "an empty env adds no key — the no-keys contract is the common case")

    def test_an_unwritable_dir_degrades_loudly(self):
        # a plain FILE where the flag-settings dir goes forces the OSError (os.makedirs raises)
        Path(self.d, sb.FLAG_SETTINGS_DIR).write_text("not a directory")
        logged = []
        p = sb.flag_settings_path(self.d, PARENT, env=ENV,
                                  log=lambda msg, problem=False: logged.append((msg, problem)))
        self.assertEqual(p, "", "degrade to launch — a session without its env still beats none")
        self.assertEqual(len(logged), 1)
        msg, problem = logged[0]
        self.assertTrue(problem, "the drop is a problem row, not a quiet info line")
        self.assertIn("env", msg, "the log names the dropped keys")
        self.assertIn(PARENT, msg, "the log names whose launch went without them")

    def test_the_oserror_path_stays_quiet_without_a_logger(self):
        Path(self.d, sb.FLAG_SETTINGS_DIR).write_text("not a directory")
        self.assertEqual(sb.flag_settings_path(self.d, PARENT, env=ENV), "",
                         "log=None (direct callers) must neither raise nor change the '' contract")

    def test_no_keys_asked_means_no_log_even_on_a_bad_dir(self):
        Path(self.d, sb.FLAG_SETTINGS_DIR).write_text("not a directory")
        logged = []
        self.assertEqual(
            sb.flag_settings_path(self.d, PARENT, log=lambda *a, **k: logged.append(a)), "")
        self.assertEqual(logged, [], "nothing requested, nothing dropped — nothing to report")


class SpawnEnv(_Backend):
    def test_spawn_persists_the_env_in_the_reg(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        self.assertEqual(self._reg(sid).get("env"), ENV)

    def test_spawn_without_env_writes_no_key(self):
        sid = self.be.spawn("web", "/tmp")
        self.assertNotIn("env", self._reg(sid))

    def test_spawn_refuses_a_bad_payload_loudly(self):
        with self.assertRaises(ValueError):
            self.be.spawn("web", "/tmp", env={"9BAD": "1"})
        with self.assertRaises(ValueError):
            self.be.spawn("web", "/tmp", env={"FEATURE_FLAG": 1})

    def test_spawn_refuses_the_identity_names(self):
        # a reg born with ROMP_SID in its user env would shadow-race the identity at every connect
        with self.assertRaises(ValueError):
            self.be.spawn("web", "/tmp", env={"ROMP_SID": PARENT})
        with self.assertRaises(ValueError):
            self.be.spawn("web", "/tmp", env={"ROMP_SESSION_NAME": "impostor"})


class _OptionsBackend(_Backend):
    """_Backend plus the _options seam: ClaudeAgentOptions is a parameter (a dict stands in) and
    the in-function import only needs HookMatcher — stub the module when the real dependency is
    absent (CI without the venv)."""

    def setUp(self):
        super().setUp()
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


class OptionsThreadsEnv(_OptionsBackend):
    """_options → flag_settings_path(env=…): the file the CLI launches with carries the reg's env."""

    def test_the_settings_file_carries_the_regs_env(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        kw = self._options_kw(self._sess(sid))
        self.assertIn("settings", kw)
        self.assertEqual(json.loads(Path(kw["settings"]).read_text())["env"], ENV)

    def test_no_env_and_no_flags_means_no_settings_file(self):
        sid = self.be.spawn("web", "/tmp")
        kw = self._options_kw(self._sess(sid))
        self.assertNotIn("settings", kw,
                         "the return-\"\"-when-no-keys contract: a plain session launches without "
                         "a flag-settings file at all")

    def test_every_connect_rewrites_the_file_so_reconnects_reassert(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        s = self._sess(sid)
        p = self._options_kw(s)["settings"]
        Path(p).write_text('{"env": {"TAMPERED": "yes"}}')   # drift the file behind romp's back
        p2 = self._options_kw(s)["settings"]
        self.assertEqual(p2, p)
        self.assertEqual(json.loads(Path(p2).read_text())["env"], ENV,
                         "the file is rewritten from the session on EVERY use — a reconnect "
                         "re-asserts the env by construction, never trusts what's on disk")

    def test_a_failed_flag_write_degrades_loudly_through_options(self):
        # the connect-time seam: /new already echoed the env as applied, so a write failure here
        # must reach the Log as a problem — the session launching without its env is otherwise
        # invisible to every surface (no readback channel)
        sid = self.be.spawn("web", "/tmp", env=ENV)
        Path(self.be.state_dir, sb.FLAG_SETTINGS_DIR).write_text("not a directory")
        logged = []
        self.be._log = lambda msg, problem=False: logged.append((msg, problem))
        kw = self._options_kw(self._sess(sid))
        self.assertNotIn("settings", kw, "degrade to launch, never abort the connect")
        self.assertTrue(any(problem and "env" in msg for msg, problem in logged),
                        "the drop must land in the Log as a problem naming env: %r" % (logged,))


class SetEnv(_Backend):
    """set_env: set_effort's persist+reconnect shape, minus the UI slice's badge/chip machinery."""

    def _live(self, sid):
        s = self._sess(sid)
        s.request_reconnect = lambda: self.reconnects.append(1)
        self.reconnects = []
        self.be.sessions[sid] = s
        return s

    def test_a_change_persists_and_reconnects(self):
        sid = self.be.spawn("web", "/tmp")
        s = self._live(sid)
        self.assertTrue(self.be.set_env(sid, ENV))
        self.assertEqual(self._reg(sid)["env"], ENV)
        self.assertEqual(s.env_vars, ENV)
        self.assertTrue(self.reconnects, "env is connect-time — the reconnect is what applies it")

    def test_an_unchanged_reassert_skips_the_reconnect(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        self._live(sid)
        self.assertTrue(self.be.set_env(sid, dict(ENV)))
        self.assertFalse(self.reconnects,
                         "same env = nothing to apply: the fresh-spawn echo and the nightly "
                         "re-brief must not churn the CLI process")

    def test_replace_not_merge(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        self._live(sid)
        self.assertTrue(self.be.set_env(sid, {"FEATURE_FLAG": "0"}))
        self.assertEqual(self._reg(sid)["env"], {"FEATURE_FLAG": "0"},
                         "the payload IS the session's per-session env — names not re-asserted drop")

    def test_refuses_junk_and_unknown_sids(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        self.assertFalse(self.be.set_env(sid, {"9BAD": "1"}))
        self.assertEqual(self._reg(sid)["env"], ENV, "a refused payload must not half-apply")
        self.assertFalse(self.be.set_env(CHILD, ENV), "no reg, no session — refuse, don't mint")

    def test_refuses_a_nul_value(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        self.assertFalse(self.be.set_env(sid, {"FEATURE_FLAG": "1\x00x"}),
                         "a NUL value is unfulfillable — refuse, never persist it into the reg")
        self.assertEqual(self._reg(sid)["env"], ENV, "the poisoned payload must not half-apply")

    def test_refuses_the_identity_names(self):
        sid = self.be.spawn("web", "/tmp", env=ENV)
        self.assertFalse(self.be.set_env(sid, {"ROMP_SESSION_NAME": "impostor"}),
                         "the identity env is romp's own — never a per-session override")
        self.assertEqual(self._reg(sid)["env"], ENV, "the refused payload must not half-apply")

    def test_an_explicit_empty_dict_clears_and_reconnects(self):
        # the replace-not-merge contract's limiting case: {} DECLARES "no per-session env" —
        # the only way to remove a spawn-time debugging var from a running session
        sid = self.be.spawn("web", "/tmp", env=ENV)
        s = self._live(sid)
        self.assertTrue(self.be.set_env(sid, {}))
        self.assertEqual(self._reg(sid)["env"], {}, "the empty declaration replaces the whole set")
        self.assertEqual(s.env_vars, {})
        self.assertTrue(self.reconnects, "clearing is a CHANGE — it applies by reconnecting")
        self.assertEqual(sb.flag_settings_path(self.be.state_dir, sid, env=s.env_vars), "",
                         "cleared env adds no key — the next connect launches without a flag file")
        self.reconnects.clear()
        self.assertTrue(self.be.set_env(sid, {}), "re-clearing is the unchanged re-assert")
        self.assertFalse(self.reconnects, "already clear = nothing to apply, no CLI churn")

    def test_a_dormant_session_persists_without_a_live_object(self):
        sid = self.be.spawn("web", "/tmp")
        self.assertTrue(self.be.set_env(sid, ENV))
        self.assertEqual(self._reg(sid)["env"], ENV,
                         "the next connect reads the reg — persistence alone is a full apply "
                         "for a session with no live client")


class ForkInheritsEnv(_Backend):
    def test_a_fork_carries_the_parents_env(self):
        os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()   # transcript_path resolves through this
        try:
            self.be.spawn("parent", self.d, sid=PARENT, env=ENV)
            self.be.fork("child", PARENT, "a1", sid=CHILD)
            self.assertEqual(self._reg(CHILD).get("env"), ENV,
                             "it is that conversation, continued elsewhere — env inherits like "
                             "model/auth do")
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def test_a_fork_of_an_env_less_parent_stays_env_less(self):
        os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
        try:
            self.be.spawn("parent", self.d, sid=PARENT)
            self.be.fork("child", PARENT, "a1", sid=CHILD)
            self.assertNotIn("env", self._reg(CHILD))
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)


class LegacyReservedEnv(_OptionsBackend):
    """Regs written before ENV_RESERVED_NAMES existed can carry ROMP_SID / ROMP_SESSION_NAME in
    their stored env. Spawn and set_env refuse them at the door now — but a standing reg is
    replayed verbatim at every connect, and fork() copies the parent's. Three obligations at the
    apply seam: the session still LAUNCHES (a reconnect refusal would brick a long-running session
    over a var accepted under older rules), the reserved name never reaches the applied env (it
    would shadow-race the options.env identity — `romp end self` resolving to a forged sid), and
    the skip is LOUD, naming the session and the ignored var (never silently)."""

    def _poisoned(self, env):
        """A reg whose stored env predates the reserved-name rule — written behind the validator,
        the way those regs actually exist on disk."""
        sid = self.be.spawn("web", "/tmp")
        reg = self._reg(sid)
        reg["env"] = dict(env)
        sb.write_reg(self.be.state_dir, sid, reg)
        return sid

    def test_a_pre_rule_reg_launches_with_the_reserved_name_skipped(self):
        sid = self._poisoned({"FEATURE_FLAG": "1", "ROMP_SID": PARENT})
        logged = []
        self.be._log = lambda msg, problem=False: logged.append((msg, problem))
        kw = self._options_kw(self._sess(sid))
        applied = json.loads(Path(kw["settings"]).read_text())["env"]
        self.assertEqual(applied, {"FEATURE_FLAG": "1"},
                         "the rest of the stored env still applies — skip the var, not the session")
        self.assertEqual(kw["env"]["ROMP_SID"], sid,
                         "the identity overlay stands untouched — the forged sid never shadows it")
        self.assertTrue(any(problem and "ROMP_SID" in msg and "web" in msg
                            for msg, problem in logged),
                        "the skip must be loud, naming the session and the ignored var: %r"
                        % (logged,))

    def test_a_reg_carrying_only_reserved_names_still_launches(self):
        sid = self._poisoned({"ROMP_SESSION_NAME": "impostor"})
        logged = []
        self.be._log = lambda msg, problem=False: logged.append((msg, problem))
        kw = self._options_kw(self._sess(sid))
        self.assertNotIn("settings", kw,
                         "nothing left after the skip = the no-keys contract, not an empty env")
        self.assertEqual(kw["env"]["ROMP_SESSION_NAME"], "web")
        self.assertTrue(any(problem and "ROMP_SESSION_NAME" in msg for msg, problem in logged))

    def test_a_fork_drops_the_reserved_names_from_the_inherited_env(self):
        os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()   # transcript_path resolves through this
        try:
            self.be.spawn("parent", self.d, sid=PARENT)
            reg = self._reg(PARENT)
            reg["env"] = {"FEATURE_FLAG": "1", "ROMP_SID": PARENT}
            sb.write_reg(self.be.state_dir, PARENT, reg)
            logged = []
            self.be._log = lambda msg, problem=False: logged.append((msg, problem))
            self.be.fork("child", PARENT, "a1", sid=CHILD)
            self.assertEqual(self._reg(CHILD).get("env"), {"FEATURE_FLAG": "1"},
                             "the copy is where a legacy reg's poison stops propagating")
            self.assertTrue(any(problem and "ROMP_SID" in msg and "child" in msg
                                for msg, problem in logged),
                            "the drop must be loud, naming the session and the var: %r" % (logged,))
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)


class ValidatorLockstep(unittest.TestCase):
    """_env_error (the /new door's kernel-side mirror) and env_request_error are hand-kept copies.
    Spawn backs the door with a loud ValueError, but set_env refuses with a silent False its callers
    discard — so drift between the copies on the existing:true path would be a 200 with an env echo
    and NOTHING applied. This pin is the backstop the door docstring cites: identical verdicts
    (messages included) across the whole good/bad payload table, so loosening one copy fails here
    instead of going silent."""

    PAYLOADS = (
        # valid: plain, underscore/empty-value, the vacuous empty declaration
        {"FEATURE_FLAG": "1"}, {"_UNDER": "x", "A9": ""}, {},
        # non-dicts, truthy and falsy alike (the door 400s all of them)
        "FEATURE_FLAG=1", ["FEATURE_FLAG"], 7, None, False, 0, "", [],
        # bad names
        {"9BAD": "1"}, {"": "1"}, {"BAD-NAME": "1"}, {"BAD NAME": "1"}, {"über": "1"},
        # the reserved identity names (options.env owns them), alone and riding a valid payload
        {"ROMP_SID": "x"}, {"ROMP_SESSION_NAME": "web"}, {"FEATURE_FLAG": "1", "ROMP_SID": "x"},
        # bad values, the NUL hole included
        {"FEATURE_FLAG": 1}, {"FEATURE_FLAG": None}, {"FEATURE_FLAG": True},
        {"FEATURE_FLAG": {"nested": "no"}}, {"FEATURE_FLAG": "1\x00x"},
    )

    @staticmethod
    def _kernel():
        import sys
        if "romp_kernel" in sys.modules:
            return sys.modules["romp_kernel"]
        # the kernel imports its deps by these exact module names (the test_new_route_prefs pattern)
        for name, fn in (("romp_event_model", "romp-event-model"), ("romp_judge", "romp-judge")):
            if name not in sys.modules:
                SourceFileLoader(name, os.path.join(BIN, fn)).load_module()
        return SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

    def test_the_two_validator_copies_agree_verdict_for_verdict(self):
        km = self._kernel()
        for payload in self.PAYLOADS:
            self.assertEqual(km._env_error(payload), sb.env_request_error(payload),
                             "the copies must stay in lockstep — payload %r" % (payload,))

    CREDENTIALS = ({"ANTHROPIC_API_KEY": "x"}, {"ANTHROPIC_AUTH_TOKEN": "x"}, {"CLAUDE_CODE_OAUTH_TOKEN": "x"})

    def test_the_copies_agree_on_credentials_with_and_without_runtime_retrieval(self):
        """Under runtime API-key retrieval a per-session API key is reserved (it competes with the source); a
        login session's token override is not (review find, 2026-09-05). Both copies, both worlds."""
        import tempfile
        km = self._kernel()
        for payload in self.CREDENTIALS:                       # no source configured: all three pass
            self.assertEqual(km._env_error(payload), "")
            self.assertEqual(sb.env_request_error(payload), "")
        d = tempfile.mkdtemp()
        path = os.path.join(d, "service.env")
        with open(path, "w") as fh:
            fh.write("ROMP_API_KEY_REF=op://test-vault/test-item/credential\n")
        saved = {k: os.environ.get(k) for k in ("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV")}
        try:
            os.environ["ROMP_SERVICE_ENV_FILE"] = path; os.environ["ROMP_SERVICE_ENV"] = path
            for m in (km.jd._keysrc, sb._keysrc):
                m._CACHE = ((), ""); m._AUTHORITATIVE_PATHS.clear()
            for auth in ("", "key", "login"):
                verdicts = [(km._env_error(p, auth), sb.env_request_error(p, auth)) for p in self.CREDENTIALS]
                for a, b in verdicts:
                    self.assertEqual(a, b, "the copies must stay in lockstep under runtime retrieval (auth=%r)" % auth)
                self.assertIn("reserved while runtime API key retrieval", verdicts[0][0], "the API key always competes")
                if auth == "login":
                    self.assertEqual(verdicts[1][0], ""); self.assertEqual(verdicts[2][0], "")   # its own token stays
                else:
                    self.assertIn("reserved", verdicts[1][0]); self.assertIn("reserved", verdicts[2][0])   # keyed: no competing token
        finally:
            for k, v in saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
            for m in (km.jd._keysrc, sb._keysrc):
                m._CACHE = ((), ""); m._AUTHORITATIVE_PATHS.clear()


class DrivePlumbing(unittest.TestCase):
    """The /new door rides the same park/drain path as the other per-session switches (source pins,
    the test_session_auth.DrivePlumbing pattern)."""

    def test_the_op_is_routed_parked_and_replayed(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("def _set_env_or_park(be, sid, value):", src)
        self.assertIn('_park_op(sid, ("env", value))', src)
        self.assertIn('elif op[0] == "env":', src)
        self.assertIn("be.set_env(sid, op[1])", src)
        self.assertIn('("model", "effort", "fast", "env", "cwd")', src,
                      "a repeat env pick REPLACES the earlier parked one in place, like model/effort")

    def test_the_create_path_passes_env_through(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('def _create_sdk_session(nm, cwd, auth="", prefs=None, client=None, env=None):', src)
        self.assertIn("sid = _sdk().spawn(nm, cwd, bg, fg, auth=auth, env=env)", src,
                      "env rides the SPAWN — the reg is born with it, ahead of the prefs pass")


# ── The session-identity environment surface (the user 2026-08-15 sid, 2026-08-16 name).
# Every SDK session's CLI process — and every Bash it runs — carries its romp identity in env:
# ROMP_SID (the stable uuid; what `romp end self` resolves through) and ROMP_SESSION_NAME (the
# human name at spawn). The name is a GENERIC capability for child processes that need to know
# which session they belong to (attribution, logging), deliberately coupled to no consumer.
# Env is spawn-frozen, so a post-spawn rename is not reflected — the sid is the address, the
# name a label. Source pins over _options' env line (the SDK merges options.env OVER the
# inherited environment, so both ride the same additive overlay as the bin PATH). Distinct layer
# from the per-session user env above: identity rides options.env (the transport), user env the
# per-sid flag-settings file — neither writes the other's layer.
SDK = Path(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read_text()


class SessionIdentityEnv(unittest.TestCase):
    def test_sid_and_name_ride_the_spawn_env(self):
        self.assertIn('"ROMP_SID": str(sess.sid),', SDK,
                      "the stable identity — addressing (romp end self)")
        self.assertIn('"ROMP_SESSION_NAME": str(sess.name)', SDK,
                      "the human name at spawn — attribution/logging for child processes")

    def test_the_name_is_documented_as_spawn_frozen(self):
        # the caveat is the contract: a rename after spawn is NOT reflected in a live session's
        # env, so nothing may treat the name as an address — the comment must keep saying so
        self.assertIn("a rename after spawn is NOT reflected", SDK)

    def test_the_terminal_launcher_exports_the_same_identity(self):
        # both backends: the tmux launch line carries ROMP_SID + ROMP_SESSION_NAME into the CLI's
        # environment (the user 2026-08-16 — external tools attribute env-first, never via tmux)
        launcher = Path(os.path.join(os.path.dirname(HERE), "bin", "romp")).read_text()
        self.assertIn('claude_cmd="ROMP_SID=$sid ROMP_SESSION_NAME=\\"$display\\" $claude_cmd"', launcher)

    def test_one_env_overlay_only(self):
        # both vars ride _options' single env= overlay (additive over os.environ via
        # _bin_on_path_env) — a second env assembly would fork the truth
        self.assertEqual(SDK.count('"ROMP_SESSION_NAME":'), 1)
        self.assertEqual(SDK.count('env={**_bin_on_path_env(os.environ)'), 1)


if __name__ == "__main__":
    unittest.main()


class EnvSecretsStayPrivate(unittest.TestCase):
    """Env values can be secrets (PR #889 review): the per-sid flag-settings file is created 0600
    like the serve token, and the parked chat chip renders NAMES only — never a value."""

    def test_the_flag_settings_file_is_private(self):
        import stat
        d = tempfile.mkdtemp()
        p = sb.flag_settings_path(d, "11111111-2222-3333-4444-555555555555", env={"TOKEN": "s3cret"})
        self.assertTrue(p, "an env writes the file")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, "0600, the serve-token treatment")
        self.assertIn("s3cret", open(p).read(), "…and the value is in it (the CLI reads it), private")
        p2 = sb.flag_settings_path(d, "11111111-2222-3333-4444-555555555555", env={"TOKEN": "other"})
        self.assertEqual(stat.S_IMODE(os.stat(p2).st_mode), 0o600, "a rewrite keeps it private")

    def test_the_parked_chip_names_the_vars_but_never_their_values(self):
        km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()
        md = km._parked_md(("env", {"B_TOKEN": "s3cret", "A_FLAG": "1"}))
        self.assertEqual(md, "/env A_FLAG B_TOKEN", "sorted names, no values")
        self.assertNotIn("s3cret", md)
        self.assertEqual(km._parked_md(("env", {})), "/env (cleared)")

