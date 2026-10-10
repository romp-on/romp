#!/usr/bin/env python3
"""The judge rate-limit gate (the user 2026-07-07): while the ACCOUNT is limit-exhausted (usage.json,
written by the SDK backend's /usage poll), every judge LLM call across every session is a doomed API
retry; the archiver postmortem counted ~1160 wasted calls in one 90-minute window. _judge_run skips the
call and rides the SAME `paused` flag as a retry-pause skip, so no give-up counter ever counts it as a
failure. `resets_at` makes the gate self-expiring (a stale "limited" stops gating the moment the window
resets: event-based, no age heuristics); a missing/unreadable usage.json never gates.

The gate scopes to LOGIN-billed calls (2026-08-28), and a call's billing follows the judged session's
pick, else Claude Code's settings (an apiKeyHelper in those settings means the key, read for the billing
decision). So "login billing" here is an empty CLAUDE_CONFIG_DIR and "key billing" a settings.json naming a
helper, each staged per test. Since 2026-10-05 the judges hold the key for their key-billed children and run
the helper to get it; these tests are about the gate, so the holder's runner is a stub that prints an invented
value (tests/test_judge_held_key.py pins the real run). Synthetic fixtures."""
import json
import shutil
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path
import os

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_rategate", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"


class RateLimitGate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._RATE_GATE_LOGGED.clear()
        self.calls = []
        self._saved_run = jd.subprocess.run
        # the gate scopes to LOGIN-billed calls (2026-08-28): pin the billing deterministically. These tests
        # always assumed login, which silently flipped to key on a machine with a key configured; since
        # 2026-09-08 the unpicked default reads Claude Code's settings, so a fresh, empty CLAUDE_CONFIG_DIR
        # per test (conftest's floor dir is shared by the run) and a managed settings path that does not
        # exist mean login here, whatever the box under the test has configured
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        self.cfg = tempfile.mkdtemp()
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self._managed_before = jd._cred.managed_settings_path
        jd._cred.managed_settings_path = lambda: os.path.join(self.cfg, "no-managed-settings.json")
        jd._judge_ctx.fsid = None
        self._source_before = jd._KEY_SOURCE          # the judges' held key, with a stub runner: no shell, no real helper
        jd._KEY_SOURCE = jd._cred.HeldKey(jd._cred.child_key_helper, runner=lambda cmd: "synthetic-held-value")

        class _FakeDone:
            stdout = '{"result": "the-model-reply"}'

        def fake_run(*a, **k):
            self.calls.append(a)
            return _FakeDone()
        jd.subprocess.run = fake_run

    def tearDown(self):
        jd.subprocess.run = self._saved_run
        jd._KEY_SOURCE = self._source_before
        jd._cred.managed_settings_path = self._managed_before
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before
        jd._judge_ctx.fsid = None
        shutil.rmtree(self.td, ignore_errors=True)
        shutil.rmtree(self.cfg, ignore_errors=True)

    def _usage(self, pct, resets_in, bucket="five_hour"):
        (jd.STATE / "usage.json").write_text(json.dumps(
            {"t": int(time.time()),
             bucket: {"pct": pct, "resets_at": int(time.time()) + resets_in}}))

    def test_exhausted_window_skips_the_call_as_a_pause_not_a_failure(self):
        self._usage(100, 3600)
        out = jd._judge_run("m", "sys", "user")
        self.assertEqual(out, "")
        self.assertEqual(self.calls, [], "no subprocess launched — the call never went out")
        self.assertTrue(jd._judge_ctx.paused, "skip rides the paused flag: give-ups don't count it")
        errs = (jd.STATE / "judge-errors.jsonl").read_text()
        self.assertIn("rate-limited", errs, "…and the window is announced once")
        jd._judge_run("m", "sys", "user")             # second skip in the same window: no second line
        self.assertEqual((jd.STATE / "judge-errors.jsonl").read_text().count("rate-limited"), 1)

    def test_high_but_not_exhausted_never_gates(self):
        self._usage(99, 3600)
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(jd._judge_ctx.paused)

    def test_expired_window_self_disables(self):
        self._usage(100, -60)                         # says limited, but resets_at already passed
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")
        self.assertEqual(len(self.calls), 1, "a stale 'limited' stops gating at reset — event-based")

    def test_missing_usage_never_gates(self):
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")

    def test_fable_bucket_gates_exactly_the_calls_that_bill_it(self):
        # The gated buckets FOLLOW THE CALL'S MODEL (2026-08-18): the old tuple ignored `fable`
        # ("judges run Sonnet") — stale once the judge-model pin read fable, and ~22,400 doomed
        # retries burned across 08-16/17 while the fable window sat at 100%.
        self._usage(100, 3600, bucket="fable")
        self.assertEqual(jd._judge_run("sonnet", "sys", "user"), "the-model-reply",
                         "a sonnet call does not bill the fable window — never gated by it")
        self.assertEqual(jd._judge_run("fable", "sys", "user"), "",
                         "a fable call IS gated by the fable window")
        self.assertTrue(jd._judge_ctx.paused)

    def test_the_gate_latches_the_loud_limit_banner(self):
        # a limit-down judge layer must SAY so (the user 2026-08-18), never fail quietly: the gate
        # writes the judge-limit latch, self-expiring at the window reset, cleared by a success
        self._usage(100, 3600, bucket="fable")
        jd._judge_run("fable", "sys", "user")
        row = jd._limit_down()
        self.assertEqual(row["bucket"], "fable")
        self.assertEqual(row["model"], "fable")
        # a successful call (model switched to sonnet, say) clears the latch — the deciding event
        self.assertEqual(jd._judge_run("sonnet", "sys", "user"), "the-model-reply")
        self.assertIsNone(jd._limit_down())

    def test_the_latch_self_expires_at_the_reset(self):
        jd._limit_mark("fable", 100, int(time.time()) - 5, "fable")   # already past its reset
        self.assertIsNone(jd._limit_down(), "the window reset IS the deciding event — no age heuristics")

    def test_limit_shaped_envelope_latches_and_pokes_a_usage_poll(self):
        # get_usage rides turn ends, so an idle fleet's usage.json goes stale; the FIRST doomed call
        # is the event that refreshes it — the envelope latches the banner and fires the wired poll
        class _FakeErr:
            stdout = '{"is_error": true, "result": "Claude AI usage limit reached — resets 12:00"}'
        jd.subprocess.run = lambda *a, **k: _FakeErr()
        poked = []
        saved = jd._USAGE_REFRESH_FN
        try:
            jd._USAGE_REFRESH_FN = lambda: poked.append(1)
            self.assertEqual(jd._judge_run("fable", "sys", "user"), "")
            self.assertEqual(poked, [1], "one exact usage poll fired")
            self.assertEqual((jd._limit_down() or {}).get("bucket"), "account")
        finally:
            jd._USAGE_REFRESH_FN = saved



class GateScopedToLoginBilling(RateLimitGate):
    """The correction round (the user 2026-08-28, who watched key-billed cards keep landing under a
    'paused' banner): usage.json's windows are the LOGIN account's, a judge call bills the JUDGED
    session's account, so the gate — and the latch's clear, and the envelope's mark — scope to
    login-billed calls. A key-billed call is pay-per-token: no windows, never gated, and its
    outcomes say nothing about the login window in either direction."""

    def _key_billed(self):
        # no per-session pick on file and an apiKeyHelper in Claude Code's settings: the key default. The
        # holder's runner is a stub (setUp), so the path need not exist.
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": "/synthetic/helper.sh"}))

    def _login_billed(self):
        Path(self.cfg, "settings.json").unlink()      # no helper anywhere: back to the login default

    def _reg(self, auth):
        """An explicit per-session pick, which outranks the default either way."""
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "auth": auth}))
        jd._judge_ctx.fsid = SID

    def test_key_billed_calls_pass_a_full_window(self):
        self._usage(100, 3600)
        self._key_billed()
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")
        self.assertEqual(len(self.calls), 1, "the call went out — pay-per-token has no window to burn")

    def test_key_success_never_clears_the_login_latch_login_success_does(self):
        jd._limit_mark("five_hour", 100, int(time.time()) + 3600, "m")
        self._key_billed()
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")
        self.assertEqual((jd._limit_down() or {}).get("bucket"), "five_hour",
                         "a key-billed success says nothing about the login window")
        self._login_billed()
        self.assertEqual(jd._judge_run("m", "sys", "user"), "the-model-reply")
        self.assertIsNone(jd._limit_down(), "a login-billed success IS the early-reset evidence")

    def test_key_billed_limit_envelope_mints_no_latch(self):
        # the manager's ruling: the account latch carries no resets_at (never self-expires), and with
        # key successes no longer clearing it, a key 429 minting it would stick a false banner
        class _FakeErr:
            stdout = '{"is_error": true, "result": "rate limit reached, try again shortly"}'
        jd.subprocess.run = lambda *a, **k: _FakeErr()
        self._key_billed()
        self.assertEqual(jd._judge_run("m", "sys", "user"), "")
        self.assertIsNone(jd._limit_down(), "no account-window latch off a pay-per-token 429")

class SegKeyUnified(unittest.TestCase):
    def test_kernel_delegates_to_the_judge_seg_key(self):
        # the two copies had to never drift; since 2026-07-07 the kernel's is a delegation, so they cannot.
        import inspect
        prior = {name: os.environ.get(name) for name in ("ROMP_KERNEL_NO_OPEN", "ROMP_SERVE_TOKEN")}

        def put_back():
            for name, value in prior.items():
                restore_env(name, value)
        self.addCleanup(put_back)      # the kernel's import needs both set; this process keeps neither
        os.environ.setdefault("ROMP_KERNEL_NO_OPEN", "1")
        os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
        km = load_source("romp_kernel_segkey", os.path.join(BIN, "romp-kernel"))
        self.assertIn("jd._seg_key(seg_id)", inspect.getsource(km._seg_key))
        for sid in ("u:123:h", "u:123:h#p", None, "", "plain", "a:b"):
            self.assertEqual(km._seg_key(sid), km.jd._seg_key(sid))


if __name__ == "__main__":
    unittest.main()
