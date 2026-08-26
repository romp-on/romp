"""Judge model + effort selection (the user 2026-07-02). BOTH judge tiers — triage (planner/grouper/closer/
distiller/courier) and indexing (captioner/archiver) — get a model AND an effort chooser in the gear. The picks
are SERVER-SIDE (the judge runs kernel-side): each dropdown posts to the kernel (setJudgeModel/setIndexModel/
setJudgeEffort/setIndexEffort → STATE/{judge,index}-{model,effort}), and the judge reads them via jd._triage_model
/ _index_model / _triage_effort / _index_effort on its next pass (no restart).

Crucially the model vocabulary lives in ONE place: the kernel's MODEL_CHOICES / EFFORT_CHOICES, served at /models
and shared by the chat statusline picker, the timeline lane picker, AND these judge dropdowns. Defaults are
`claude --model` aliases (haiku / sonnet) that auto-track the latest of each family.
"""
import inspect
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate STATE so the test never touches the real picks
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

FILES = ("judge-model", "index-model", "judge-effort", "index-effort")


class JudgeSettings(unittest.TestCase):
    def setUp(self):
        # Sandbox STATE to a fresh temp dir each test — km and jd share the module object, so setting jd.STATE
        # steers both the setters (km) and the readers (jd). Fresh dir + cleared cache makes these tests immune
        # to whatever prior test files did to the shared jd.STATE / _state_cache in a full-suite run.
        import tempfile as _t
        from pathlib import Path as _P
        self._saved_state = jd.STATE
        self._td = _t.mkdtemp()
        jd.STATE = _P(self._td)
        jd._state_cache.clear()

    def tearDown(self):
        import shutil as _sh
        jd.STATE = self._saved_state
        jd._state_cache.clear()
        _sh.rmtree(self._td, ignore_errors=True)

    # ---- defaults are aliases (shared picker vocabulary) ----
    def test_defaults_are_family_aliases(self):
        self.assertEqual(jd.TRIAGE_MODEL, "sonnet", "triage default = sonnet alias (→ latest Sonnet)")
        self.assertEqual(jd.INDEX_MODEL, "haiku", "index default = haiku alias")
        self.assertEqual(jd._triage_model(), "sonnet")
        self.assertEqual(jd._index_model(), "haiku")
        self.assertEqual(jd._triage_effort(), "", "no effort by default (no --effort flag)")
        self.assertEqual(jd._index_effort(), "")

    # ---- ONE source: the kernel owns MODEL_CHOICES / EFFORT_CHOICES; no per-surface hardcoding ----
    def test_model_choices_are_the_single_source(self):
        self.assertEqual([m["value"] for m in km.MODEL_CHOICES], ["fable", "opus", "sonnet", "haiku"])
        self.assertEqual([e["value"] for e in km.EFFORT_CHOICES],
                         ["low", "medium", "high", "xhigh", "max", "ultracode"])   # ultracode tops the ladder (the user 2026-08-04)
        # the judge no longer keeps its own model list — it trusts the kernel-validated STATE value
        self.assertNotIn("JUDGE_MODELS", dir(jd), "the judge holds no model list (kernel is the single source)")

    def test_models_endpoint_serves_the_shared_lists(self):
        ksrc = inspect.getsource(km)
        self.assertIn('if p == "/models":', ksrc)
        # the shared lists, each choice carrying its colormap tint (the user 2026-08-17) — and,
        # since the version submenus (the user 2026-08-25), each family's versions + default too
        self.assertIn('{"models": [dict(c, color=_model_color(c["value"], _stops),', ksrc)
        self.assertIn('versions=[dict(v) for v in MODEL_VERSIONS.get(c["value"]) or []]', ksrc)

    # ---- per-tier overrides honored + validated ----
    def test_judge_tiers_accept_version_ids(self):
        # the settings pickers mirror the family+version submenus (the user 2026-08-25): a version
        # id is a valid judge model — it rides the SDK model param verbatim, like session picks.
        # The setter is effect-only (writes the state file or silently refuses) — assert the file.
        km._set_judge_model("claude-opus-4-8")
        jd._state_cache.clear()
        self.assertEqual(jd._triage_model(), "claude-opus-4-8")
        km._set_distill_model("claude-sonnet-4-6")
        self.assertEqual((jd.STATE / "distill-model").read_text(), "claude-sonnet-4-6")
        km._set_judge_model("claude-nonsense-9")
        jd._state_cache.clear()
        self.assertEqual(jd._triage_model(), "claude-opus-4-8", "unknown ids refused — the pick stands")
        km._set_judge_model("opus")   # restore a family value for the suites that follow
        jd._state_cache.clear()

    def test_overrides_are_honored(self):
        (jd.STATE / "judge-model").write_text("opus")
        (jd.STATE / "index-model").write_text("sonnet")
        (jd.STATE / "judge-effort").write_text("xhigh")
        jd._state_cache.clear()
        self.assertEqual(jd._triage_model(), "opus")
        self.assertEqual(jd._index_model(), "sonnet")
        self.assertEqual(jd._triage_effort(), "xhigh")

    def test_empty_override_uses_the_default(self):
        (jd.STATE / "judge-model").write_text("   ")   # whitespace/empty → default (the setter never writes this)
        jd._state_cache.clear()
        self.assertEqual(jd._triage_model(), jd.TRIAGE_MODEL, "empty file → default alias")

    def test_setters_validate_model_and_effort(self):
        km._set_judge_model("opus"); self.assertEqual((jd.STATE / "judge-model").read_text().strip(), "opus")
        km._set_judge_model("bogus"); self.assertEqual((jd.STATE / "judge-model").read_text().strip(), "opus")   # ignored
        km._set_index_model("haiku"); self.assertEqual((jd.STATE / "index-model").read_text().strip(), "haiku")
        km._set_judge_effort("max"); self.assertEqual((jd.STATE / "judge-effort").read_text().strip(), "max")
        km._set_judge_effort("bogus"); self.assertEqual((jd.STATE / "judge-effort").read_text().strip(), "max")   # ignored
        km._set_index_effort(""); self.assertEqual((jd.STATE / "index-effort").read_text().strip(), "")   # "" clears

    # ---- the judge applies the per-tier effort when the caller passes none ----
    def test_judge_run_applies_tier_effort(self):
        (jd.STATE / "judge-effort").write_text("high")
        (jd.STATE / "index-effort").write_text("low")
        jd._state_cache.clear()
        seen, saved_cmd = {}, jd._judge_cmd
        jd._judge_cmd = lambda model, sysp, effort=None: (seen.__setitem__(model, effort) or ["true"])
        saved_env, saved_paused = jd._judge_env, (jd.STATE / "retry-paused.json")
        try:
            jd._judge_run("sonnet", "SYS", "u", tier="triage")
            jd._judge_run("haiku", "SYS", "u", tier="index")
            jd._judge_run("opus", "SYS", "u", effort="max", tier="triage")   # explicit wins
        finally:
            jd._judge_cmd = saved_cmd
        self.assertEqual(seen.get("sonnet"), "high", "triage tier effort applied")
        self.assertEqual(seen.get("haiku"), "low", "index tier effort applied")
        self.assertEqual(seen.get("opus"), "max", "explicit caller effort wins over the tier default")

    # ---- /version + gear expose all four ----
    def test_version_reports_all_four(self):
        v = km._version_info()
        self.assertEqual((v["judgeModel"], v["indexModel"]), ("sonnet", "haiku"))
        self.assertEqual((v["judgeEffort"], v["indexEffort"]), ("", ""))

    def test_gear_has_four_dropdowns_with_plain_names(self):
        html = _gear_src()
        for sel in ("id=rs-judgemodel", "id=rs-judgeeffort", "id=rs-indexmodel", "id=rs-indexeffort"):
            self.assertIn(sel, html)
        # options come from GET /models at open (2026-07-13) — plain labels, one source
        # (ku() = kb() + the ?token= the kernel now requires on every request)
        self.assertIn("fetch(ku('/models')", html)
        self.assertIn({"value": "sonnet", "label": "Sonnet"}, km.MODEL_CHOICES)
        self.assertNotIn("balanced", html)   # descriptions dropped (the user, who wanted just the model names)

    def test_ws_handlers_exist(self):
        ksrc = inspect.getsource(km)
        for t in ("setJudgeModel", "setIndexModel", "setJudgeEffort", "setIndexEffort"):
            self.assertIn('msg.get("type") == "%s"' % t, ksrc)


if __name__ == "__main__":
    unittest.main()


# The gear moved from kernel-inline strings into the shared feed bundle
# (2026-07-13): ui/webview/gear.js is the single source both hosts render, so
# the gear pins read THAT file (and feed.css for its styling).
def _gear_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()


def _gear_css_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.css").read_text()
