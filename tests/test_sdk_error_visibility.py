"""SDK-backend problems reach the user (the user 2026-07-28).

Everything the SDK backend logs used to go to the kernel's stderr and nowhere else: a session thread
that died, a stream that dropped, a model switch the CLI refused. The dashboard showed none of it, so a
session that misbehaved left the user with nothing to look at. Now every line logged WHILE AN EXCEPTION
IS BEING HANDLED is recorded in a bounded ring, the feed payload carries it, and the feed mirrors it
into the shell's error center as a 'sdk' entry.

The classification is the exact event, not a keyword sniff: sys.exc_info() is live for the whole dynamic
extent of an except block, so a line emitted from inside one (helpers included) IS that exception's
report.
"""
import ast
import inspect
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_sdkerr", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_sdkerr", os.path.join(BIN, "romp_sdk_backend.py"))
SB_SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


class _Ring(sb.SdkBackend):
    """The ring + _log alone — SdkBackend.__init__ touches the real state dir, which these don't need."""

    def __init__(self):
        self._problems = []
        self._problem_seq = 0
        import threading
        self._problem_lock = threading.Lock()
        self.lines = []
        self._log_cb = self.lines.append


class ProblemRing(unittest.TestCase):
    def test_a_line_logged_while_handling_an_exception_is_a_problem(self):
        r = _Ring()
        try:
            raise ValueError("the stream dropped")
        except ValueError as e:
            r._log("sdk session web crashed: %s" % e)
        self.assertEqual(len(r.problems()), 1)
        self.assertIn("the stream dropped", r.problems()[0]["text"])
        self.assertEqual(r.lines, ["sdk session web crashed: the stream dropped"],
                         "the kernel log still gets every line, unchanged")

    def test_an_ordinary_line_is_not_a_problem(self):
        r = _Ring()
        r._log("boot reconcile: resumed 2 cut turn(s)")
        self.assertEqual(r.problems(), [])
        self.assertEqual(len(r.lines), 1)

    def test_a_helper_called_from_inside_an_except_block_still_counts(self):
        # the dynamic extent is the point: the report is usually written by a helper, not at the raise
        r = _Ring()

        def report():
            r._log("rewind (web): the CLI refused --resume-session-at")

        try:
            raise RuntimeError("refused")
        except RuntimeError:
            report()
        self.assertEqual(len(r.problems()), 1)

    def test_problem_kwarg_overrides_in_both_directions(self):
        r = _Ring()
        r._log("claude_agent_sdk is NOT importable", problem=True)     # no live exception, still a problem
        try:
            raise OSError("expected")
        except OSError:
            r._log("this one is routine", problem=False)
        texts = [p["text"] for p in r.problems()]
        self.assertEqual(texts, ["claude_agent_sdk is NOT importable"])

    def test_the_ring_is_bounded_and_keeps_the_newest(self):
        r = _Ring()
        for i in range(sb.SdkBackend.PROBLEM_RING + 25):
            r._log("failure %d" % i, problem=True)
        rows = r.problems()
        self.assertEqual(len(rows), sb.SdkBackend.PROBLEM_RING)
        self.assertEqual(rows[-1]["text"], "failure %d" % (sb.SdkBackend.PROBLEM_RING + 24))
        self.assertEqual(r.problem_seq(), sb.SdkBackend.PROBLEM_RING + 25,
                         "the sequence keeps counting past the ring — it is the occurrence id")

    def test_problems_returns_a_copy_and_honours_limit(self):
        r = _Ring()
        for i in range(5):
            r._log("failure %d" % i, problem=True)
        rows = r.problems(2)
        self.assertEqual([x["text"] for x in rows], ["failure 3", "failure 4"])
        rows.clear()
        self.assertEqual(len(r.problems()), 5, "the caller cannot mutate the ring")


class NoSilentSwallows(unittest.TestCase):
    """The failures that used to be `except Exception: pass` now log — which is what puts them in the
    ring. Checked per-function so the test says what regressed, not just that a count moved."""

    LOUD = ("_do_set_model", "_do_set_mode", "_do_refresh_context", "_learn_model",
            "_resolve_model_pending", "_heal_stale_awaiting", "_persist_echoes", "_on_session_gone",
            "_fire_boot_settled", "_poke", "_wake_push", "_options")

    def test_named_handlers_report_instead_of_passing(self):
        tree = ast.parse(SB_SRC)
        fns = {n.name: n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in self.LOUD}
        self.assertEqual(sorted(fns), sorted(self.LOUD), "a checked function was renamed or removed")
        for name, fn in fns.items():
            for h in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]:
                bare = len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
                self.assertFalse(bare, "%s swallows an exception silently again" % name)


class SessionAttributes(unittest.TestCase):
    def test_a_session_never_reads_a_state_dir_of_its_own(self):
        # the AttributeError that killed a session thread on an /effort switch applied at reconnect:
        # state_dir lives on the BACKEND. This is the class of bug the ring made visible.
        tree = ast.parse(SB_SRC)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SdkSession")
        reads = [n for n in ast.walk(cls)
                 if isinstance(n, ast.Attribute) and n.attr == "state_dir"
                 and isinstance(n.value, ast.Name) and n.value.id == "self"]
        self.assertEqual(reads, [], "SdkSession has no state_dir — use self.backend.state_dir")

    def test_a_crashed_session_logs_its_traceback(self):
        self.assertIn("crashed: {type(e).__name__}: {e}", SB_SRC)
        i = SB_SRC.index("crashed: {type(e).__name__}: {e}")
        self.assertIn("traceback.format_exc()", SB_SRC[i:i + 300],
                      "a bare type+message names no line to fix")


class KernelSide(unittest.TestCase):
    def test_a_traceback_folds_to_cause_and_effect(self):
        txt = km._sdk_problem_text("boot reconcile failed: Traceback (most recent call last):\n"
                                   "  File \"kernel/sdk_backend.py\", line 1, in _boot_reconcile\n"
                                   "    raise KeyError('x')\n"
                                   "KeyError: 'x'\n")
        self.assertEqual(txt, "boot reconcile failed … KeyError: 'x'")

    def test_a_one_line_problem_is_left_alone_and_long_ones_are_capped(self):
        self.assertEqual(km._sdk_problem_text("set_model (web -> opus) refused"),
                         "set_model (web -> opus) refused")
        self.assertEqual(km._sdk_problem_text(""), "")
        long = km._sdk_problem_text("x" * 900, cap=40)
        self.assertEqual(len(long), 40)
        self.assertTrue(long.endswith("…"))

    def test_rows_sign_each_occurrence_and_skip_blanks(self):
        km._SDK_BOOT_PROBLEMS[:] = [{"seq": 1, "t": 100.0, "text": "the SDK backend could not be built"},
                                    {"seq": 2, "t": 101.0, "text": "   "}]
        try:
            rows = km._sdk_problem_rows()
            self.assertEqual([r["text"] for r in rows], ["the SDK backend could not be built"])
            self.assertEqual(len({r["sig"] for r in rows}), len(rows))
            self.assertTrue(rows[0]["sig"].startswith("sdk|"))
            self.assertEqual(rows[0]["t"], 100.0)
            again = km._sdk_problem_rows()
            self.assertEqual(again[0]["sig"], rows[0]["sig"],
                             "a rebuild re-sends the SAME signature, so the bell never re-logs it")
        finally:
            km._SDK_BOOT_PROBLEMS.clear()

    def test_a_boot_problem_and_a_backend_problem_cannot_collide(self):
        # both rings number from 1; the source tag is what keeps their signatures apart
        class _Be:
            def problems(self, limit=0):
                return [{"seq": 1, "t": 5.0, "text": "sdk session web crashed"}]

            def problem_seq(self):
                return 1

        km._SDK_BOOT_PROBLEMS[:] = [{"seq": 1, "t": 4.0, "text": "the SDK backend could not be built"}]
        prev, prev_seq = km._sdk_backend, km._SDK_PROBLEM_SEQ[0]
        km._sdk_backend = _Be()
        km._SDK_PROBLEM_SEQ[0] = 1                        # the planted ring's one problem, as _sdk_problem would count it
        try:
            rows = km._sdk_problem_rows()
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({r["sig"] for r in rows}), 2)
            self.assertEqual(km._sdk_problem_count(), 2)
        finally:
            km._sdk_backend = prev
            km._SDK_PROBLEM_SEQ[0] = prev_seq
            km._SDK_BOOT_PROBLEMS.clear()

    def test_the_feed_payload_and_its_cache_key_carry_the_problems(self):
        self.assertIn('"sdkNotices": _sdk_problem_rows(),', inspect.getsource(km.build_feed))
        # a fresh failure is its own event: the view cache busts on it instead of riding the 5s bucket
        self.assertIn('sig["__sdkp__"] = _sdk_problem_count()', inspect.getsource(km._fleet_view_sig))

    def test_the_error_center_knows_the_kind(self):
        js = km._LANDING_ERRS_JS
        self.assertIn("'apierror','sdk'", js)
        self.assertIn("sdk:'sdk'", js)
        self.assertIn("sdk:\"romp's SDK backend", js)


if __name__ == "__main__":
    unittest.main()
