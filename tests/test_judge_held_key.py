#!/usr/bin/env python3
"""The kernel holds the API key for its judge children (the user 2026-10-05).

Until then every key-billed judge child (`claude -p`, about 90 a minute on a busy box) ran Claude Code's
apiKeyHelper inside its own CLI, so every judge call was one read from the operator's secret manager, and
when the secret manager's hourly read quota refused most of them the cards went stale. Now
credentials.HeldKey runs the helper once and keeps the value in the judge process's memory; _judge_env
hands it to each key-billed child as ANTHROPIC_API_KEY and _judge_cmd turns that child's own helper off
(`--settings {"apiKeyHelper": ""}`: without it CLI 2.1.284 still runs the helper once per call even though
it then sends the environment's key, measured with a counting stand-in helper against a local stand-in API).

What this module pins:
  * the holder: N asks, one run, also from many threads at once; a refusal brings one fresh run, shared by
    every thread that saw it; never more than one run a minute, a failed run included; a changed command
    runs at once; an age limit, when set, refreshes on expiry;
  * the judges end to end, with a fake CLI and the REAL fixture helper: N judge spawns cause one helper run;
    a 401 envelope causes exactly one refresh and one retry of that child; a retry refused again stands the
    next calls down until the minute is up;
  * the key never reaches a log line, the service log (stderr), the error rows, the auth latch, the usage rows
    or any other file under the state root, even when the child echoes it;
  * a helper that is configured and fails surfaces as an error (the auth latch, an "auth" row, a stderr line)
    and spawns no child: never a fall onto the child's own helper run;
  * no helper the kernel may run for its children (none, or one in managed settings) keeps the old road.

Synthetic values throughout: the fixture helper prints invented strings no validator would take for a key,
and this module uses its own synthetic session id.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from romp_load import load_source
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads (they resolve their state root at import time; only pytest runs conftest's floor).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
os.environ.pop("CLAUDE_CODE_API_KEY_HELPER_TTL_MS", None)
jd = load_source("romp_judge_heldkey", os.path.join(BIN, "romp-judge"))
cred = jd._cred

SID = "7e1d0c4a-5b2f-4c3d-9e8f-0a1b2c3d4e5f"     # this module's own synthetic session id
HELD_1 = "synthetic-held-value-one"
HELD_2 = "synthetic-held-value-two"
HELPER_OFF = ["--settings", '{"apiKeyHelper": ""}']
REFUSED = "Invalid API key · Fix external API key"    # CLI 2.1.284's words for a 401 on a key, verbatim shape
SERVED = {"result": "ok", "usage": {}, "duration_ms": 3}
GAP = cred.HELD_KEY_REFRESH_GAP_S
_REAL_RUN = subprocess.run


def _vault(outputs):
    """A fixture secret store: a helper script that counts its runs and prints outputs[n-1] on its n-th run (the
    last entry repeats once they run out); an entry of None makes that run exit 1. Returns (dir, script)."""
    d = tempfile.mkdtemp()
    Path(d, "outputs").write_text("".join(("FAIL" if o is None else o) + "\n" for o in outputs))
    script = Path(d, "helper.sh")
    script.write_text(
        "#!/bin/sh\n"
        "d='%s'\n"
        "n=$(( $(cat \"$d/runs\" 2>/dev/null || echo 0) + 1 ))\n"
        "echo \"$n\" > \"$d/runs\"\n"
        "line=$(sed -n \"${n}p\" \"$d/outputs\")\n"
        "[ -n \"$line\" ] || line=$(tail -n 1 \"$d/outputs\")\n"
        "[ \"$line\" != FAIL ] || exit 1\n"
        "echo \"$line\"\n" % d)
    script.chmod(0o700)
    return d, str(script)


def _runs(d):
    try:
        return int(Path(d, "runs").read_text())
    except (OSError, ValueError):
        return 0


class HeldKeyHolder(unittest.TestCase):
    """credentials.HeldKey on its own: the real run_helper over the fixture helper, a stepped clock."""

    def _holder(self, outputs, max_age=None):
        self.vault, script = _vault(outputs)
        self.cmd = [script]
        self.now = [100.0]
        return cred.HeldKey(lambda: self.cmd[0], max_age_fn=lambda: max_age, clock=lambda: self.now[0])

    def tearDown(self):
        shutil.rmtree(getattr(self, "vault", ""), ignore_errors=True)

    def test_many_asks_run_the_command_once(self):
        h = self._holder([HELD_1])
        self.assertEqual({h.value() for _ in range(50)}, {HELD_1})
        self.now[0] += 3600
        self.assertEqual(h.value(), HELD_1, "no age limit: a static key is held until it is refused")
        self.assertEqual((_runs(self.vault), h.runs), (1, 1))

    def test_a_pool_asking_at_once_waits_for_one_run(self):
        h = self._holder([HELD_1, HELD_2])
        start, out = threading.Barrier(16), []

        def ask():
            start.wait()
            out.append(h.value())
        threads = [threading.Thread(target=ask) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(out, [HELD_1] * 16)
        self.assertEqual(_runs(self.vault), 1, "the lock holds the pool behind one run")

    def test_no_command_holds_nothing_and_runs_nothing(self):
        h = self._holder([HELD_1])
        self.cmd[0] = None
        self.assertEqual(h.value(), "")
        self.assertEqual(h.refused(HELD_1), "")
        self.assertEqual(_runs(self.vault), 0)

    def test_a_refusal_brings_one_fresh_run_and_a_sibling_reuses_it(self):
        h = self._holder([HELD_1, HELD_2])
        self.assertEqual(h.value(), HELD_1)
        self.now[0] += GAP + 1
        self.assertEqual(h.refused(HELD_1), HELD_2, "one fresh run, its value to retry with")
        self.assertEqual(h.refused(HELD_1), HELD_2, "a sibling that saw the old value refused gets the new one")
        self.assertEqual(h.value(), HELD_2)
        self.assertEqual(_runs(self.vault), 2)

    def test_concurrent_refusals_share_one_refresh(self):
        h = self._holder([HELD_1, HELD_2])
        h.value()
        self.now[0] += GAP + 1
        start, out = threading.Barrier(12), []

        def refuse():
            start.wait()
            out.append(h.refused(HELD_1))
        threads = [threading.Thread(target=refuse) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(out, [HELD_2] * 12)
        self.assertEqual(_runs(self.vault), 2, "one refresh for every child that saw the 401")

    def test_inside_the_minute_a_refusal_runs_nothing_and_the_holder_stands_down(self):
        h = self._holder([HELD_1, HELD_2])
        h.value()
        self.now[0] += 10
        self.assertEqual(h.refused(HELD_1), "", "the helper ran ten seconds ago: no run, no retry")
        with self.assertRaises(cred.CredentialError) as cm:
            h.value()
        self.assertIn("at most once every 60 s", str(cm.exception))
        self.assertNotIn(HELD_1, str(cm.exception), "static words, never the value")
        self.assertEqual(_runs(self.vault), 1)
        self.now[0] += GAP
        self.assertEqual(h.value(), HELD_2, "the minute is up: the next ask runs the helper")
        self.assertEqual(_runs(self.vault), 2)

    def test_a_refused_retry_marks_the_key_without_a_run(self):
        h = self._holder([HELD_1, HELD_2])
        h.value()
        self.now[0] += GAP + 1
        self.assertEqual(h.refused(HELD_1), HELD_2)
        self.assertEqual(h.refused(HELD_2, allow_run=False), "")
        with self.assertRaises(cred.CredentialError):
            h.value()
        self.assertEqual(_runs(self.vault), 2)

    def test_a_failed_run_is_remembered_for_the_minute(self):
        h = self._holder([None, HELD_1])
        for _ in range(20):
            with self.assertRaises(cred.CredentialError) as cm:
                h.value()
            self.assertEqual(str(cm.exception), "apiKeyHelper failed (non-zero exit)")
        self.assertEqual(_runs(self.vault), 1, "a refusing secret manager is not asked again at the call rate")
        self.now[0] += GAP
        self.assertEqual(h.value(), HELD_1)
        self.assertEqual(_runs(self.vault), 2)

    def test_a_runner_that_breaks_any_other_way_is_held_to_the_gap_too(self):
        calls = []

        def boom(cmd):
            calls.append(cmd)
            raise TypeError("a value that must not be quoted")
        h = cred.HeldKey(lambda: "/synthetic/cmd", runner=boom, clock=lambda: 0.0)
        for _ in range(3):
            with self.assertRaises(cred.CredentialError) as cm:
                h.value()
            self.assertEqual(str(cm.exception), "apiKeyHelper could not be run")
        self.assertEqual(len(calls), 1)

    def test_a_changed_command_runs_at_once(self):
        h = self._holder([HELD_1])
        h.value()
        other, script2 = _vault([HELD_2])
        self.addCleanup(shutil.rmtree, other, True)
        self.cmd[0] = script2
        self.assertEqual(h.value(), HELD_2, "the operator edited the helper: nothing held for the old one counts")
        self.assertEqual((_runs(self.vault), _runs(other)), (1, 1))

    def test_an_age_limit_refreshes_on_expiry_but_never_inside_the_minute(self):
        h = self._holder([HELD_1, HELD_2, "synthetic-held-value-three"], max_age=300)
        self.assertEqual(h.value(), HELD_1)
        self.now[0] += 299
        self.assertEqual(h.value(), HELD_1)
        self.now[0] += 1
        self.assertEqual(h.value(), HELD_2, "a short-lived token's lifetime ends: one run")
        short = self._holder([HELD_1, HELD_2], max_age=10)
        short.value()
        self.now[0] += 11
        self.assertEqual(short.value(), HELD_1, "past its age inside the minute: the gap is the floor between runs")
        self.now[0] += GAP
        self.assertEqual(short.value(), HELD_2)

    def test_scrub_blanks_every_value_held(self):
        h = self._holder([HELD_1, HELD_2])
        self.assertEqual(h.scrub("nothing held yet " + HELD_1), "nothing held yet " + HELD_1)
        h.value()
        self.now[0] += GAP + 1
        h.refused(HELD_1)
        text = h.scrub("old %s new %s json %s" % (HELD_1, HELD_2, json.dumps(HELD_2)))
        self.assertNotIn(HELD_1, text)
        self.assertNotIn(HELD_2, text)
        self.assertEqual(text.count("[key withheld]"), 3)


class TheSourceTheJudgesUse(unittest.TestCase):
    """Which command the judges' holder runs, and its age limit."""

    def setUp(self):
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self._managed_before = cred.managed_settings_path
        cred.managed_settings_path = lambda: os.path.join(self.cfg, "managed-settings.json")

    def tearDown(self):
        cred.managed_settings_path = self._managed_before
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before
        shutil.rmtree(self.cfg, ignore_errors=True)

    def test_the_users_helper_is_run_for_the_children_and_a_managed_one_is_not(self):
        self.assertIsNone(cred.child_key_helper(), "no helper anywhere")
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": "/synthetic/user-helper"}))
        self.assertEqual(cred.child_key_helper(), "/synthetic/user-helper")
        Path(self.cfg, "managed-settings.json").write_text(json.dumps({"apiKeyHelper": "/synthetic/managed-helper"}))
        self.assertIsNone(cred.child_key_helper(),
                          "a managed helper outranks the per-call layer: the children keep running it themselves")
        Path(self.cfg, "managed-settings.json").unlink()
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": ""}))
        self.assertIsNone(cred.child_key_helper(), "the disable value is no helper")

    def test_the_age_limit_is_the_operators_ttl_only_when_set(self):
        with patch.dict(os.environ, {}):
            os.environ.pop(cred.HELPER_TTL_VAR, None)
            self.assertIsNone(cred.helper_ttl_if_set(), "unset: held until refused, not twelve reads an hour")
            os.environ[cred.HELPER_TTL_VAR] = "900000"
            self.assertEqual(cred.helper_ttl_if_set(), 900.0)

    def test_the_judges_holder_reads_the_users_helper_into_the_key_variable(self):
        # by name, not identity: another test module may re-execute the credentials module into the same object
        self.assertEqual(jd._KEY_SOURCE.env_name, "ANTHROPIC_API_KEY")
        self.assertEqual(jd._KEY_SOURCE.command_fn.__name__, "child_key_helper")
        self.assertEqual(jd._KEY_SOURCE.max_age_fn.__name__, "helper_ttl_if_set")


class JudgeSpawnsShareTheHeldKey(unittest.TestCase):
    """_judge_run end to end: the fake CLI answers by the key it was given; the fixture helper runs for real."""

    def setUp(self):
        self.root = tempfile.mkdtemp()                # every file this test's judge writes lives under here
        self.state = os.path.join(self.root, "state")
        self.cfg = os.path.join(self.root, "claude-config")
        os.makedirs(self.state)
        os.makedirs(self.cfg)
        self._state_before = jd.STATE
        jd._rebind_state(Path(self.state))
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "auth": "key"}))
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self._managed_before = cred.managed_settings_path
        cred.managed_settings_path = lambda: os.path.join(self.cfg, "managed-settings.json")
        self.now = [5000.0]
        self._source_before = jd._KEY_SOURCE
        jd._KEY_SOURCE = cred.HeldKey(cred.child_key_helper, env_name="ANTHROPIC_API_KEY", label="apiKeyHelper",
                                      max_age_fn=cred.helper_ttl_if_set, clock=lambda: self.now[0])
        self._wires = (jd._DEFAULT_AUTH_FN, jd._DEFAULT_LOGIN_FN, jd._LOGIN_AUTH_ENV_FN)
        jd._DEFAULT_AUTH_FN = jd._DEFAULT_LOGIN_FN = jd._LOGIN_AUTH_ENV_FN = None
        jd._auth_cache[:] = [None, {}]
        jd._HELD_FAIL_SAID.clear()
        jd._judge_ctx.fsid = SID
        jd._judge_ctx.paused = False
        self.spawns = []                              # (argv, the key the child was given), one per child
        self.refused_keys = set()                     # keys the fake API answers with a 401
        self.echo = False                             # the fake CLI quotes the key it was given back
        self.dead = False                             # the fake CLI dies with no stdout (its stderr is logged)
        self._lock = threading.Lock()
        for p in (patch.object(jd, "_judge_engine", return_value="claude"),
                  patch.object(jd.subprocess, "run", side_effect=self._fake_cli)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        jd._KEY_SOURCE = self._source_before
        jd._DEFAULT_AUTH_FN, jd._DEFAULT_LOGIN_FN, jd._LOGIN_AUTH_ENV_FN = self._wires
        cred.managed_settings_path = self._managed_before
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before
        jd._rebind_state(self._state_before)
        jd._auth_cache[:] = [None, {}]
        jd._judge_ctx.fsid = None
        jd._judge_ctx.paused = False
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(getattr(self, "vault", ""), ignore_errors=True)

    # ── fixtures ─────────────────────────────────────────────────────────────────────────────────────────
    def _helper(self, outputs):
        self.vault, script = _vault(outputs)
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": script}))

    def _fake_cli(self, cmd, input=None, env=None, **kw):
        if isinstance(cmd, str):                      # the fixture helper, run by the holder: for real
            return _REAL_RUN(cmd, input=input, env=env, **kw)
        key = (env or {}).get("ANTHROPIC_API_KEY")
        with self._lock:
            self.spawns.append((list(cmd), key))
        said = (" (sent %s)" % key) if self.echo else ""
        if self.dead:
            return SimpleNamespace(stdout="", stderr="x-api-key: %s" % key, returncode=1)
        if key in self.refused_keys:
            return SimpleNamespace(stdout=json.dumps({"is_error": True, "result": REFUSED + said}),
                                   stderr=("rejected key %s" % key) if self.echo else "", returncode=1)
        return SimpleNamespace(stdout=json.dumps(SERVED), stderr="", returncode=0)

    def _judge(self, judge="planner"):
        return jd._judge_run("sonnet", "SYS", "u", judge=judge, tier="triage")

    def _errors(self):
        try:
            return [json.loads(ln) for ln in jd.ERRORS.read_text().splitlines() if ln.strip()]
        except OSError:
            return []

    # ── the tests ────────────────────────────────────────────────────────────────────────────────────────
    def test_n_judge_spawns_cause_one_helper_run(self):
        self._helper([HELD_1, HELD_2])
        outs = [self._judge() for _ in range(30)]

        def pool_call(out):
            jd._judge_ctx.fsid = SID
            out.append(self._judge("closer"))
        pooled = []
        threads = [threading.Thread(target=pool_call, args=(pooled,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(outs + pooled, ["ok"] * 40)
        self.assertEqual(_runs(self.vault), 1, "forty judge spawns, one helper run")
        self.assertEqual(len(self.spawns), 40)
        for argv, key in self.spawns:
            self.assertEqual(key, HELD_1, "every key-billed child carries the held key")
            self.assertEqual(argv[-2:], HELPER_OFF, "and runs no helper of its own")
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ, "the held key never enters the judge process's environment")
        self.assertEqual(jd._auth_down_map(), {})

    def test_a_401_causes_exactly_one_refresh_and_one_retry(self):
        self._helper([HELD_1, HELD_2])
        self.assertEqual(self._judge(), "ok")
        self.refused_keys = {HELD_1}                  # the key was rotated: the API refuses the held one
        self.now[0] += GAP + 1
        self.assertEqual(self._judge(), "ok", "refused, refreshed, retried once, served")
        self.assertEqual([k for _, k in self.spawns], [HELD_1, HELD_1, HELD_2])
        self.assertEqual(_runs(self.vault), 2, "exactly one refresh")
        self.assertEqual(jd._auth_down_map(), {}, "a refusal the retry recovered latches nothing")
        for _ in range(10):
            self.assertEqual(self._judge(), "ok")
        self.assertEqual({k for _, k in self.spawns[3:]}, {HELD_2})
        self.assertEqual(_runs(self.vault), 2)

    def test_concurrent_401s_share_one_refresh(self):
        self._helper([HELD_1, HELD_2])
        self._judge()
        self.refused_keys = {HELD_1}
        self.now[0] += GAP + 1
        start, outs = threading.Barrier(8), []

        def call():
            jd._judge_ctx.fsid = SID
            start.wait()
            outs.append(self._judge())
        threads = [threading.Thread(target=call) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(outs, ["ok"] * 8)
        self.assertEqual(_runs(self.vault), 2, "eight children saw the 401; the helper ran once more")
        self.assertLessEqual({k for _, k in self.spawns}, {HELD_1, HELD_2})

    def test_a_retry_refused_again_stands_the_next_calls_down_until_the_minute_is_up(self):
        self._helper([HELD_1, HELD_2])
        self._judge()
        self.refused_keys = {HELD_1, HELD_2}          # the secret store still holds a revoked key
        self.now[0] += GAP + 1
        self.assertEqual(self._judge(), "")
        self.assertEqual([k for _, k in self.spawns], [HELD_1, HELD_1, HELD_2], "one refresh, one retry, no more")
        self.assertIn(REFUSED, jd._auth_down_map()[SID]["note"], "the API's refusal latches the session loudly")
        before = len(self.spawns)
        self.assertEqual(self._judge(), "")
        self.assertTrue(jd._judge_ctx.paused, "inside the minute the call stands down: a skip, not a failure")
        self.assertEqual(len(self.spawns), before, "and spends no API call on a key already refused")
        self.assertIn("at most once every 60 s", self._errors()[-1]["note"])
        self.assertEqual(_runs(self.vault), 2)
        self.now[0] += GAP
        self._judge()
        self.assertEqual(_runs(self.vault), 3, "the minute is up: the helper runs again")

    def test_a_missing_helper_surfaces_an_error_and_spawns_nothing(self):
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": os.path.join(self.cfg, "no-such-helper")}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._judge(), "")
            self.assertEqual(self._judge(), "")
        note = "apiKeyHelper is not on the manager's PATH (exit 127)"
        self.assertEqual(self.spawns, [], "no child at all: never a fall onto the child's own helper run")
        self.assertTrue(jd._judge_ctx.paused)
        self.assertEqual(jd._auth_down_map()[SID]["note"], note, "the session's card floors with the helper's words")
        self.assertEqual([(r["err"], r["note"]) for r in self._errors()], [("auth", note)] * 2)
        self.assertEqual(err.getvalue().count(note), 1, "the service log says it once")
        self.assertEqual(jd._KEY_SOURCE.runs, 1, "the second call inside the minute ran nothing")

    def test_a_failing_helper_surfaces_its_own_words(self):
        self._helper([None])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self._judge(), "")
        self.assertEqual(jd._auth_down_map()[SID]["note"], "apiKeyHelper failed (non-zero exit)")
        self.assertEqual(self.spawns, [])

    def test_a_refresh_that_fails_names_the_helper_on_the_card(self):
        self._helper([HELD_1, None])
        self._judge()
        self.refused_keys = {HELD_1}
        self.now[0] += GAP + 1
        self.assertEqual(self._judge(), "")
        self.assertEqual(len(self.spawns), 2, "no retry without a fresh key")
        self.assertEqual(jd._auth_down_map()[SID]["note"], "apiKeyHelper failed (non-zero exit)")
        self.assertIn(("auth", "apiKeyHelper failed (non-zero exit)"), [(r["err"], r["note"]) for r in self._errors()])

    def test_the_key_never_reaches_a_log_line_or_any_written_file(self):
        self._helper([HELD_1, HELD_2])
        (jd.STATE / "debug-mode.json").write_text(json.dumps({"on": True}))   # error rows carry input and reply
        self.echo = True                              # a child that quotes the key it was given, everywhere it can
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self._judge(), "ok")
            self.refused_keys = {HELD_1}
            self.now[0] += GAP + 1
            self.assertEqual(self._judge(), "ok", "refused with the key quoted, refreshed, retried")
            self.refused_keys = {HELD_1, HELD_2}
            self.now[0] += GAP + 1
            self.assertEqual(self._judge(), "", "refused again after the refresh: the latch carries the quote")
            self.now[0] += GAP + 1
            self.refused_keys = set()
            self.dead = True                          # a dead child whose stderr tail is logged
            self.assertEqual(self._judge(), "")
        self.assertGreaterEqual(len({k for _, k in self.spawns}), 2, "both keys rode children")
        rows = jd.ERRORS.read_text()
        self.assertIn("[key withheld]", rows, "the quotes were written, blanked (the walk below is not vacuous)")
        for value in (HELD_1, HELD_2):
            self.assertNotIn(value, err.getvalue(), "the service log")
            self.assertNotIn(value, json.dumps(dict(os.environ)), "the judge process's own environment")
        leaked = []
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                data = Path(dirpath, name).read_bytes()
                leaked += [os.path.join(dirpath, name) for v in (HELD_1, HELD_2) if v.encode() in data]
        self.assertEqual(leaked, [], "no file under the state root or the config dir holds the key")

    def test_no_helper_keeps_the_old_road(self):
        self.assertEqual(self._judge(), "ok")
        argv, key = self.spawns[0]
        self.assertIsNone(key, "nothing to hold: the child resolves its own credential")
        self.assertNotIn("--settings", argv)
        self.assertEqual(jd._KEY_SOURCE.runs, 0)

    def test_a_managed_helper_keeps_the_old_road(self):
        vault, script = _vault([HELD_1])
        self.addCleanup(shutil.rmtree, vault, True)
        Path(self.cfg, "managed-settings.json").write_text(json.dumps({"apiKeyHelper": script}))
        self.assertEqual(self._judge(), "ok")
        argv, key = self.spawns[0]
        self.assertIsNone(key, "the per-call layer cannot turn a managed helper off, so the kernel does not run it")
        self.assertNotIn("--settings", argv)
        self.assertEqual(_runs(vault), 0)

    def test_a_login_billed_call_never_touches_the_held_key(self):
        self._helper([HELD_1])
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "auth": "login"}))
        jd._auth_cache[:] = [None, {}]
        self.assertEqual(self._judge(), "ok")
        self.assertIsNone(self.spawns[0][1])
        self.assertEqual(self.spawns[0][0][-2:], HELPER_OFF, "the login road's own suppression, unchanged")
        self.assertEqual(_runs(self.vault), 0)


class TheCLIsRefusalWordsAreCredentialErrors(unittest.TestCase):
    def test_the_401_wording_latches(self):
        for s in (REFUSED, "Invalid auth token · Fix external auth token"):
            self.assertTrue(jd._is_auth_error(s), s)
        self.assertFalse(jd._is_auth_error("Overloaded"))


if __name__ == "__main__":
    unittest.main()
