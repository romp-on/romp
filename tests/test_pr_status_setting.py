#!/usr/bin/env python3
"""The PR status switch: a per-install gear setting that turns the Outline's PR chips, and every git and gh read behind
them, on or off. The reader, the setter's gesture rules, the boot seed from ROMP_PR_STATUS, and the hand-off to gitpr.
Synthetic fixtures only."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_pr_status", os.path.join(BIN, "romp-kernel"))
jd, gp = km.jd, km.gp


class PrStatusSwitch(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state, self.saved_on = jd.STATE, gp.enabled()
        self.saved_env = os.environ.pop(gp.PR_STATUS_ENV, None)
        jd._rebind_state(Path(self.td.name))
        (Path(self.td.name) / "session-hosts").write_text("off\n")
        km._prs_read_fault_said.clear()
        self.store = jd.STATE / km.PR_STATUS_FILE

    def tearDown(self):
        jd._rebind_state(self.saved_state)
        gp.set_enabled(self.saved_on)
        if self.saved_env is None:
            os.environ.pop(gp.PR_STATUS_ENV, None)
        else:
            os.environ[gp.PR_STATUS_ENV] = self.saved_env
        self.td.cleanup()

    def test_on_by_default_and_the_setter_flips_gitpr_at_once(self):
        self.assertTrue(km._pr_status_on(), "no file: the shipped default")
        stamp = km._set_pr_status(False)
        self.assertIsNotNone(stamp)
        self.assertFalse(km._pr_status_on())
        self.assertFalse(gp.enabled(), "the flip reaches gitpr without waiting for a push")
        self.assertEqual(gp.repo_of(os.getcwd()), "", "off: no git read resolves a repo")
        self.assertIsNotNone(km._set_pr_status(True))
        self.assertTrue(km._pr_status_on() and gp.enabled())

    def test_an_echo_and_an_older_gesture_apply_nothing(self):
        stamp = km._set_pr_status(False)
        km._pop_stale_notice()
        self.assertIsNone(km._set_pr_status(False, gt=stamp), "the gesture's own echo")
        self.assertIsNone(km._pop_stale_notice(), "…applied quietly: no stand-down for the user's own pick")
        self.assertIsNone(km._set_pr_status(True, gt=stamp - 1), "an older gesture stands down")
        self.assertFalse(km._pr_status_on())
        self.assertEqual(km._version_info()["settingsGt"]["pr-status"], stamp)
        self.assertIs(km._version_info()["prStatus"], False, "the gear's row reads it")

    def test_a_non_boolean_store_reads_off_and_says_so_once_per_value(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for v in ("false", "false", 0, "true"):
                self.store.write_text(json.dumps({"enabled": v, "gt": 1}))
                self.assertFalse(km._pr_status_on(), repr(v))
            self.store.write_text(json.dumps({"enabled": None, "gt": 2}))
            self.assertTrue(km._pr_status_on(), "null is absent: the default")
            self.store.write_text("{torn")
            self.assertTrue(km._pr_status_on(), "unreadable: the default")
        self.assertEqual(err.getvalue().count("not a boolean"), 3, "one line per distinct value")

    def test_the_boot_seed_writes_off_once_and_never_over_a_standing_store(self):
        os.environ[gp.PR_STATUS_ENV] = "off"
        gp.set_enabled(True)
        self.assertTrue(km._seed_pr_status(), "env off, no store: seeded")
        self.assertFalse(km._pr_status_on() or gp.enabled())
        km._set_pr_status(True)
        self.assertFalse(km._seed_pr_status(), "the gear owns it after the first boot")
        self.assertTrue(km._pr_status_on() and gp.enabled())

    def test_the_boot_seed_without_the_env_only_hands_the_switch_to_gitpr(self):
        self.store.write_text(json.dumps({"enabled": False, "gt": 5}))
        gp.set_enabled(True)
        self.assertFalse(km._seed_pr_status())
        self.assertFalse(gp.enabled(), "a standing off store reaches gitpr before the first push")
        self.assertEqual(json.loads(self.store.read_text())["gt"], 5, "nothing rewritten")

    def test_main_seeds_it_and_the_push_reads_it_live(self):
        import inspect
        self.assertIn("_seed_pr_status()", inspect.getsource(km.main))
        self.assertIn("gp.set_enabled(_pr_status_on())", inspect.getsource(km._push))


if __name__ == "__main__":
    unittest.main()
