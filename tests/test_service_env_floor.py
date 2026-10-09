#!/usr/bin/env python3
"""The suite-wide service-settings floor (tests/__init__.py, scrub_service_env, 2026-10-09): no test runs
under the live service's ROMP_ settings. Every shell a romp-managed session opens inherits the manager's
environment, the service's own knobs among it, so a suite started from such a shell loaded the kernel
with production values (a tail-cache benchmark ran with its rule off because the service had it off).
The tests package clears every inherited ROMP_ name at import, before conftest.py and before any test
module, except an allowlist of test opt-ins.

Three things are held here: the floor observed from a module loaded under pytest and under a bare
unittest run whose shell carries synthetic service values; the clearing rule itself (default clear,
allowlist keep); and the allowlist against every ROMP_ name a test module reads from the environment
without setting it, so a new opt-in that nobody listed fails here instead of being cleared silently."""
import ast
import glob
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:      # a direct `python3 tests/test_service_env_floor.py` run starts with tests/ on the path
    sys.path.insert(0, ROOT)
import tests  # noqa: E402  the package whose import is the floor under test

# Synthetic stand-ins for what a manager's env file hands every session shell: the three the incident
# carried, plus a knob no list anywhere names (the default-clear case), plus a session-identity name.
SERVICE_VALUES = {
    "ROMP_RECORD_CACHE_TAIL_MB": "0",
    "ROMP_GC_FREEZE_FULL_MS": "off",
    "ROMP_EXPECTED_AUTH": "login",
    "ROMP_SYNTHETIC_KNOB_NO_LIST_NAMES": "7",
    "ROMP_SESSION_NAME": "web",
}
# Opt-ins the run is handed on purpose, which the floor keeps.
KEPT_VALUES = {
    "ROMP_LAB_SHOT": "/nonexistent/synthetic-shot.png",
    "ROMP_TEST_SERVICE_FLOOR_CHILD": "1",
}
CHILD = "ROMP_TEST_SERVICE_FLOOR_CHILD"

# What this module saw when it loaded: the floor must already have run by then.
AT_IMPORT = {name: os.environ.get(name) for name in list(SERVICE_VALUES) + list(KEPT_VALUES)}

# Production knobs a test module names inside a source-pin string, never as an opt-in; the scan below
# reads code, not strings, so none is expected today. A name added here is cleared like any other knob.
READ_BUT_CLEARED = ()


def _is_environ(node):
    return isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"


def environ_reads_and_sets(source):
    """(names read from os.environ, names written into os.environ) in one module's code, by AST. A name
    held in a module-level constant (MARKER_ENV = "ROMP_X"; os.environ.get(MARKER_ENV)) is resolved:
    tests/test_tempdir_hygiene.py hands its child run a path that way, and a scan of literals alone
    missed it, so the first cut of the floor cleared it in the child."""
    tree = ast.parse(source)
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value

    def _romp_name(node):
        value = node.value if isinstance(node, ast.Constant) else consts.get(node.id) if isinstance(node, ast.Name) else None
        return value if isinstance(value, str) and value.startswith("ROMP_") else None

    reads, sets = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_environ(node.value) and _romp_name(node.slice):
            (sets if isinstance(node.ctx, ast.Store) else reads).add(_romp_name(node.slice))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            fn, first = node.func, (_romp_name(node.args[0]) if node.args else None)
            if fn.attr == "get" and _is_environ(fn.value) and first:
                reads.add(first)
            elif fn.attr == "getenv" and first:
                reads.add(first)
            elif fn.attr in ("setdefault",) and _is_environ(fn.value) and first:
                sets.add(first)
            elif fn.attr == "setenv" and first:
                sets.add(first)
            elif fn.attr in ("dict", "update"):
                target = node.args[0] if fn.attr == "dict" and node.args else fn.value
                mapping = (node.args[1] if len(node.args) > 1 else None) if fn.attr == "dict" else (node.args[0] if node.args else None)
                if _is_environ(target):
                    if isinstance(mapping, ast.Dict):
                        sets.update(n for n in map(_romp_name, mapping.keys) if n)
                    sets.update(kw.arg for kw in node.keywords if kw.arg and kw.arg.startswith("ROMP_"))
    return reads, sets


class ServiceEnvFloor(unittest.TestCase):
    def test_the_floor_holds_inside_a_test(self):
        """Run by the subprocess legs below, and as a plain test in every run: a module loaded under the suite
        sees none of the service's names, whatever the shell carried."""
        for name in SERVICE_VALUES:
            self.assertIsNone(AT_IMPORT[name], "%s reached a test module at import" % name)
        if AT_IMPORT[CHILD] == "1":    # the configured-shell legs: nothing came back since, and the opt-ins survive
            for name in SERVICE_VALUES:
                self.assertNotIn(name, os.environ, "%s reached a test" % name)
            for name, value in KEPT_VALUES.items():
                self.assertEqual(AT_IMPORT[name], value, "the floor removed the opt-in %s" % name)
            self.assertTrue(set(SERVICE_VALUES) <= set(tests.SERVICE_ENV_SCRUBBED), tests.SERVICE_ENV_SCRUBBED)

    def _child_env(self):
        env = dict(os.environ)
        env.update(SERVICE_VALUES)
        env.update(KEPT_VALUES)
        return env

    def test_the_floor_holds_for_a_pytest_run_under_a_configured_shell(self):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            os.path.join(HERE, "test_service_env_floor.py") + "::ServiceEnvFloor::test_the_floor_holds_inside_a_test"],
                           env=self._child_env(), capture_output=True, text=True, timeout=180, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-500:])
        self.assertIn("1 passed", r.stdout)

    def test_the_floor_holds_for_a_bare_unittest_run_under_a_configured_shell(self):
        """conftest.py never loads here; the package is the only floor, which is why the floor lives there."""
        r = subprocess.run([sys.executable, "-m", "unittest", "-q",
                            "tests.test_service_env_floor.ServiceEnvFloor.test_the_floor_holds_inside_a_test"],
                           env=self._child_env(), capture_output=True, text=True, timeout=180, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-500:])
        self.assertIn("OK", r.stderr)

    def test_clears_by_default_and_keeps_by_allowlist(self):
        env = dict(SERVICE_VALUES, PATH="/usr/bin", ANTHROPIC_BASE_URL="http://example.invalid",
                   ROMP_TESTS_SYSTEM_TMPDIR="/tmp", ROMP_TEST_PROBE="x", ROMP_SERVED_TESTS_REQUIRE="1",
                   ROMP_HYGIENE_READY="/nonexistent/ready")
        removed = tests.scrub_service_env(env)
        self.assertEqual(removed, sorted(SERVICE_VALUES))
        self.assertEqual(sorted(env), sorted(["PATH", "ANTHROPIC_BASE_URL", "ROMP_TESTS_SYSTEM_TMPDIR", "ROMP_TEST_PROBE",
                                              "ROMP_SERVED_TESTS_REQUIRE", "ROMP_HYGIENE_READY"]),
                         "only ROMP_ names are touched, and the allowlisted ones stay")

    def test_every_opt_in_a_test_reads_is_kept(self):
        """A ROMP_ name some test module or the harness reads from os.environ, and that no test writes into
        os.environ, is something the run must be HANDED: an opt-in, a CI switch, a parent test's handoff. Each
        must be on the allowlist, or the floor clears it and the leg it switches on silently never runs."""
        reads, sets = set(), set()
        files = sorted(glob.glob(os.path.join(HERE, "test_*.py")) + glob.glob(os.path.join(HERE, "smoke_*.py"))
                       + [os.path.join(HERE, "conftest.py")])
        for path in files:
            r, s = environ_reads_and_sets(open(path, encoding="utf-8").read())
            reads |= r
            sets |= s
        handed = reads - sets - set(READ_BUT_CLEARED)
        missing = sorted(n for n in handed if not tests.service_env_kept(n))
        self.assertEqual(missing, [], "add each to SERVICE_ENV_KEEP_NAMES in tests/__init__.py (or, for a production "
                                      "knob a test only names, to READ_BUT_CLEARED here)")
        self.assertIn("ROMP_SERVED_TESTS_REQUIRE", handed, "the scan still sees CI's switch: re-anchor it if not")

    def test_the_scan_reads_code_not_strings(self):
        src = ('import os\nx = os.environ.get("ROMP_A")\ny = os.environ["ROMP_B"]\nos.environ["ROMP_C"] = "1"\n'
               'pin = \'os.environ.get("ROMP_D")\'\nMARK = "ROMP_E"\nz = os.environ.get(MARK)\n')
        self.assertEqual(environ_reads_and_sets(src), ({"ROMP_A", "ROMP_B", "ROMP_E"}, {"ROMP_C"}))


if __name__ == "__main__":
    unittest.main()
