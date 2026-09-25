#!/usr/bin/env python3
"""Judge concurrency is a knob (T277). The judges' worker-pool width was a bare constant (6 concurrent
`claude -p` calls). It stays 6 by default and now reads from two places, the setting winning:

- `ROMP_JUDGE_CONCURRENCY`, read ONCE at judge module load: an integer clamped to 1..16; a value that is
  not an integer is ignored with one stderr line and the default stands.
- the kernel setting `judge-concurrency` (the gear's "Judge concurrency" select, alongside the triage
  model/effort picks): validated by the kernel before it is written, read fresh on every pass through
  the same mtime-cached STATE reader the tier picks use, so a change lands on the judges' NEXT pass with
  no restart. An empty setting clears back to the variable (else the default).

Every run_* entry point's `concurrency` argument defaults to None and resolves at CALL time (`_conc`),
because a def-time default (`=CONCURRENCY`) would have frozen the setting out of the long-lived kernel.
Hermetic: the judge module is loaded against a temp XDG state dir; no live state is touched.
"""
import contextlib
import inspect
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)   # a live kernel's export outranks the XDG floor
os.environ.pop("ROMP_JUDGE_CONCURRENCY", None)   # the default case below must see NO variable
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_conc", os.path.join(BIN, "romp-kernel"))
jd = km.jd


def _load_judge_with_env(value, name):
    """A FRESH judge module loaded with ROMP_JUDGE_CONCURRENCY=value (None = unset), plus what it
    wrote to stderr while loading — the module-load read is the thing under test."""
    saved = os.environ.get("ROMP_JUDGE_CONCURRENCY")
    if value is None:
        os.environ.pop("ROMP_JUDGE_CONCURRENCY", None)
    else:
        os.environ["ROMP_JUDGE_CONCURRENCY"] = value
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            mod = load_source(name, os.path.join(BIN, "romp-judge"))
    finally:
        if saved is None:
            os.environ.pop("ROMP_JUDGE_CONCURRENCY", None)
        else:
            os.environ["ROMP_JUDGE_CONCURRENCY"] = saved
    return mod, err.getvalue()


class _Sandbox(unittest.TestCase):
    """STATE sandboxed per test: km and jd share the module object, so steering jd.STATE steers both
    the kernel's setters and the judge's readers (the test_kernel_judge_model idiom)."""
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd.STATE = Path(self._td)
        jd._state_cache.clear()

    def tearDown(self):
        jd.STATE = self._saved_state
        jd._state_cache.clear()
        shutil.rmtree(self._td, ignore_errors=True)


class Default(_Sandbox):
    def test_default_is_six_with_no_variable_and_no_setting(self):
        self.assertEqual(jd.CONCURRENCY_DEFAULT, 6)
        self.assertEqual(jd.CONCURRENCY, 6, "no ROMP_JUDGE_CONCURRENCY in this process → the constant stays 6")
        self.assertEqual(jd._judge_concurrency(), 6, "no setting file → the module-load value")
        self.assertEqual(jd._conc(None), 6)
        self.assertEqual(jd._conc(2), 2, "an explicit argument always stands")

    def test_every_pass_entry_point_resolves_concurrency_at_call_time(self):
        # a def-time default (=CONCURRENCY) is exactly the bug this knob must not have: the kernel
        # imports the judge once and would run the setting-less value forever
        for fn in (jd.run_index, jd._run_index, jd.run_plan, jd.run_group, jd.run_consolidate, jd.run_close,
                   jd.run_unblock, jd.run_distill, jd.run_triage, jd.run_propagate, jd.run_courier,
                   jd._ab_classify):
            p = inspect.signature(fn).parameters["concurrency"]
            self.assertIsNone(p.default, "%s must default concurrency to None (resolved per call)" % fn.__name__)


def _recording_pool(seen):
    """A ThreadPoolExecutor stand-in that records max_workers and runs nothing: the tests below patch it in
    to read what width a real entry point asked for."""
    class Pool:
        def __init__(self, max_workers=None):
            seen.append(max_workers)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def submit(self, *a, **k):
            raise AssertionError("nothing to submit — the session list is empty")
        def map(self, fn, it):
            return [fn(x) for x in it]
    return Pool


class Variable(_Sandbox):
    def test_variable_is_read_at_module_load(self):
        mod, err = _load_judge_with_env("10", "romp_judge_conc_ten")
        mod.STATE = jd.STATE   # the fresh module resolved STATE from the process env at load; under a full run that
        mod._state_cache.clear()   # is another module's store, which a peer test may have written a setting into
        self.assertEqual(mod.CONCURRENCY, 10)
        self.assertEqual(mod._judge_concurrency(), 10, "no setting → the variable's value")
        self.assertEqual(err, "", "a valid integer says nothing")

    def test_parser_defaults_clamps_and_ignores_garbage_with_one_line(self):
        parse = jd._concurrency_from_env
        self.assertEqual(parse(None), 6)
        self.assertEqual(parse(""), 6)
        self.assertEqual(parse("  "), 6)
        self.assertEqual(parse(" 4 "), 4)
        self.assertEqual(parse("0"), 1, "clamped to the floor")
        self.assertEqual(parse("-3"), 1)
        self.assertEqual(parse("99"), 16, "clamped to the ceiling")
        self.assertEqual(parse("16"), 16)
        for bad in ("six", "6.5", "1e1", "0x6"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(parse(bad), 6, "%r is ignored" % bad)
            lines = err.getvalue().splitlines()
            self.assertEqual(len(lines), 1, "exactly one stderr line for %r: %r" % (bad, lines))
            self.assertIn("ROMP_JUDGE_CONCURRENCY", lines[0])
            self.assertIn(bad, lines[0])

    def test_clamp_applies_at_module_load_too(self):
        mod, err = _load_judge_with_env("99", "romp_judge_conc_clamp")
        self.assertEqual(mod.CONCURRENCY, 16)
        self.assertEqual(err, "", "a clamped integer says nothing — it is a value, applied at the bound")
        mod, err = _load_judge_with_env("lots", "romp_judge_conc_bad")
        self.assertEqual(mod.CONCURRENCY, 6, "garbage → the default")
        self.assertEqual(len(err.splitlines()), 1, "…and one stderr line: %r" % err)

    def test_the_reference_says_an_edit_to_the_variable_waits_for_a_manager_restart(self):
        # The variable is read at judge module load, which happens inside the kernel, from the environment the manager
        # hands every kernel it spawns; the manager takes service.env only when it starts. The reference said to set
        # it in service.env "then a restart", which reads as if `romp refresh` (a kernel restart) applied the edit.
        with open(os.path.join(os.path.dirname(HERE), "docs", "reference.md"), encoding="utf-8") as f:
            text = f.read()
        at = text.index("- `ROMP_JUDGE_CONCURRENCY=<1..16>`")
        doc = " ".join(text[at:text.index("\n\n", at)].split())
        self.assertIn("from the environment the manager hands the kernel", doc)
        self.assertIn("restart the manager (`romp down`, then `romp up`)", doc)
        self.assertNotIn("then a restart", doc, "a kernel restart does not reread service.env")


class Setting(_Sandbox):
    def test_setting_is_validated_written_and_read_fresh(self):
        self.assertIsNotNone(km._set_judge_concurrency("3"))
        self.assertEqual((jd.STATE / "judge-concurrency").read_text(), "3")
        self.assertEqual(jd._judge_concurrency(), 3)
        self.assertEqual(jd._conc(None), 3, "the run_* default resolves to the setting")
        # a later change lands on the next read (mtime-cached, like the tier picks); the mtime bump
        # stands in for the seconds between two real gestures
        self.assertIsNotNone(km._set_judge_concurrency("5"))
        f = jd.STATE / "judge-concurrency"
        st = f.stat()
        os.utime(f, (st.st_atime + 2, st.st_mtime + 2))
        self.assertEqual(jd._judge_concurrency(), 5)

    def test_out_of_range_and_garbage_are_refused_not_clamped(self):
        # the kernel validates before writing (the judge trusts the file): a select can only offer
        # 1..16, so anything else is a bad client, refused with nothing written
        self.assertIsNotNone(km._set_judge_concurrency("2"))
        for bad in ("0", "17", "40", "-1", "six", "6.0", " 6"):
            self.assertIsNone(km._set_judge_concurrency(bad), "%r must be refused" % bad)
        self.assertEqual((jd.STATE / "judge-concurrency").read_text(), "2", "the refused values wrote nothing")
        self.assertEqual(jd._judge_concurrency(), 2)

    def test_empty_setting_clears_back_to_the_variable_or_default(self):
        self.assertIsNotNone(km._set_judge_concurrency("9"))
        self.assertEqual(jd._judge_concurrency(), 9)
        self.assertIsNotNone(km._set_judge_concurrency(""), "the empty pick is the gear's Default option")
        f = jd.STATE / "judge-concurrency"
        st = f.stat()
        os.utime(f, (st.st_atime + 2, st.st_mtime + 2))
        self.assertEqual(jd._judge_concurrency(), jd.CONCURRENCY, "cleared → the module-load value")

    def test_setting_wins_over_the_variable(self):
        mod, _ = _load_judge_with_env("12", "romp_judge_conc_twelve")
        saved = mod.STATE
        mod.STATE = jd.STATE   # the sandboxed store the kernel writes
        mod._state_cache.clear()
        try:
            self.assertEqual(mod._judge_concurrency(), 12, "no setting → the variable")
            self.assertIsNotNone(km._set_judge_concurrency("2"))
            self.assertEqual(mod._judge_concurrency(), 2, "a setting outranks the variable")
        finally:
            mod.STATE = saved
            mod._state_cache.clear()

    def test_a_pass_sizes_its_pool_from_the_live_setting(self):
        # end to end through a real entry point: run_distill with nothing to distill still builds its
        # pool, and the pool's width is the setting at call time
        seen = []
        with unittest.mock.patch.object(jd, "ThreadPoolExecutor", _recording_pool(seen)), \
             unittest.mock.patch.object(jd, "discover", lambda now, **k: []), \
             unittest.mock.patch.object(jd, "_drain_undiscovered", lambda *a, **k: 0):
            jd.run_distill(now=1)
            self.assertEqual(seen, [6], "no setting → the default pool")
            self.assertIsNotNone(km._set_judge_concurrency("4"))
            jd.run_distill(now=1)
            self.assertEqual(seen, [6, 4], "the next pass reads the setting — no restart")
            jd.run_distill(now=1, concurrency=2)
            self.assertEqual(seen, [6, 4, 2], "an explicit argument still stands")

    def test_the_eyeballing_cli_pool_follows_the_setting_too(self):
        # `romp-judge --test <transcript>` (jd._test) opens its own caption pool: the one pool outside the
        # pass entry points, and the review found it still on the load-time constant. Nothing to caption
        # here (a synthetic path, no tasks), so only the requested width is observed.
        seen = []
        self.assertIsNotNone(km._set_judge_concurrency("2"))
        with unittest.mock.patch.object(jd, "ThreadPoolExecutor", _recording_pool(seen)), \
             unittest.mock.patch.object(jd, "tasks_for", lambda *a, **k: []), \
             unittest.mock.patch.object(jd, "caption_llm", lambda text: ""), \
             contextlib.redirect_stdout(io.StringIO()):
            jd._test("/tmp/11111111-2222-4333-8444-555555555555.jsonl")
        self.assertEqual(seen, [2])

    def test_an_undecodable_setting_file_reads_as_the_default(self):
        # a hand edit saved in another encoding: the STATE reader caught OSError only, and read_text's
        # UnicodeDecodeError (a ValueError) escaped through _conc BEFORE any pool opened, failing the whole
        # tier every cycle (the review's find). It reads as the default now, like a missing file.
        (jd.STATE / "judge-concurrency").write_bytes(b"\xff\xfe6")
        self.assertEqual(jd._state_str("judge-concurrency", ""), "")
        self.assertEqual(jd._judge_concurrency(), jd.CONCURRENCY)
        self.assertEqual(jd._conc(None), jd.CONCURRENCY, "the pass opens its pool at the default width")

    def test_kernel_surfaces_carry_the_field(self):
        # /judge-settings applies it through the same validated door and answers RAW ("" = following
        # the variable/default), like the distill and comment fields; the stamp store list and the
        # settings-field table both name it, so it propagates and orders like every other judge knob
        self.assertIn("judgeConcurrency", dict(km._JUDGE_SETTING_FIELDS))
        self.assertIn("judge-concurrency", km._GT_STORES)
        res = km._apply_judge_settings({})
        self.assertEqual(res["judgeConcurrency"], "")
        res = km._apply_judge_settings({"judgeConcurrency": "7"})
        self.assertEqual(res["judgeConcurrency"], "7")
        self.assertEqual(jd._judge_concurrency(), 7)
        res = km._apply_judge_settings({"judgeConcurrency": "70"})
        self.assertEqual(res["judgeConcurrency"], "7", "an invalid value is ignored and the ack shows what stands")
        # /version reports it beside the other judge knobs, top level AND in the cross-machine `settings`
        # dict (the mixed marks compare that one), RAW like the distill and comment fields
        v = km._version_info()
        self.assertEqual(v["judgeConcurrency"], "7")
        self.assertEqual(v["settings"]["judgeConcurrency"], "7")
        self.assertIn("judge-concurrency", v["settingsGt"], "its gesture stamp rides with the other stores'")


import unittest.mock  # noqa: E402  (after the module loads above, like the other kernel tests)

if __name__ == "__main__":
    unittest.main()
