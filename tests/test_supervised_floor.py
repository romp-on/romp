#!/usr/bin/env python3
"""The suite-wide ROMP_SUPERVISED floor (tests/conftest.py, 2026-09-05): every shell under a romp-managed
session inherits ROMP_SUPERVISED=1 from the kernel (the service unit exports it), and kernel/keysource.py
gives that variable authority — a supervised manager reads the env file only and ignores a startup key.
Twenty-five tests that stage a startup key went red when the suite ran from inside a romp session while
CI stayed green. conftest pops the variable at import and before every test, and resets keysource's
per-process memory of which path selected which source so modules cannot leak selection state into each
other. Source pins, in the style of test_model_catalog_floor.py, plus the floor observed from inside a test."""
import os
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))


class SupervisedFloor(unittest.TestCase):
    def test_conftest_pops_the_variable_at_import_and_per_test(self):
        src = open(os.path.join(HERE, "conftest.py")).read()
        head, _, body = src.partition("@pytest.fixture(autouse=True)\ndef _no_real_service_env")
        self.assertTrue(body, "the fixture header moved — re-anchor this pin")
        self.assertIn('os.environ.pop("ROMP_SUPERVISED", None)', head, "the import-time floor")
        self.assertIn('os.environ.pop("ROMP_SUPERVISED", None)', body, "the per-test re-assert")
        self.assertIn("_reset_keysource_state()", body, "keysource memory is per test")
        import re
        self.assertRegex(src, r"_AUTHORITATIVE_PATHS\.clear\(\)")
        self.assertRegex(src, r"_ENV_PROVIDER_PATHS.*\.clear\(\)")
        self.assertRegex(src, r'_CACHE = \(\(\), ""\)')

    def test_the_floor_holds_for_a_bare_run_under_a_supervised_shell(self):
        """Independent of the sibling modules whose preambles also pop the variable: a subprocess with the
        variable set runs only this module's behavioural check, which passes only if conftest's floor did."""
        import subprocess, sys
        env = dict(os.environ, ROMP_SUPERVISED="1")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            os.path.join(HERE, "test_supervised_floor.py") + "::SupervisedFloor::test_the_floor_holds_inside_a_test"],
                           env=env, capture_output=True, text=True, timeout=120, cwd=os.path.dirname(HERE))
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-400:])

    def test_the_floor_holds_inside_a_test(self):
        self.assertNotIn("ROMP_SUPERVISED", os.environ, "a romp-managed shell's export must not reach a test")
