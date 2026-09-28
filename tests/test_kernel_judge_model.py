"""Judge model + effort selection (the user 2026-07-02). BOTH judge tiers — triage (planner/grouper/closer/
distiller/courier) and indexing (captioner/archiver) — get a model AND an effort chooser in the gear. The picks
are SERVER-SIDE (the judge runs kernel-side): each dropdown posts to the kernel (setJudgeModel/setIndexModel/
setJudgeEffort/setIndexEffort → STATE/{judge,index}-{model,effort}), and the judge reads them via jd._triage_model
/ _index_model / _triage_effort / _index_effort on its next pass (no restart).

Crucially the model vocabulary lives in ONE place: the kernel's MODEL_CHOICES / EFFORT_CHOICES, served at /models
and shared by the chat statusline picker, the timeline lane picker, AND these judge dropdowns. Defaults are
`claude --model` aliases (haiku / sonnet) that auto-track the latest of each family.
"""
import contextlib
import inspect
import io
import json
import os
import tempfile
import threading as _real_threading
import types
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate STATE so the test never touches the real picks
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

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
        # since the version submenus (the user 2026-08-25), each family's versions + default too:
        # the default is the family's remembered pin, else its ALIAS — never the list's head, which
        # pinned every picker-set session to the head id while the CLI's alias moved on
        # (the payload leads with `rev`, the pick memory's revision, so a picker can drop a /models
        # response older than one it applied — the models frame's counter)
        self.assertIn('{"rev": _rev,', ksrc)
        self.assertIn('"models": [dict(c, color=_model_color(c["value"], _stops),', ksrc)
        self.assertIn('default=_picks.get(c["value"]) or c["value"],', ksrc)   # …beside `resolves`, the alias's resolution (2026-09-28)
        # …each version row stamped with any CLI minimum-version refusal (T222, 2026-09-01: the live
        # catalog can list ids newer than the installed binary, so the row says so before a pick)
        # …the versions from the CATALOG (the seed table as the Models API fetch grew it, T222) ∪ what
        # running sessions' CLIs report (_versions_catalog), deduped by id, newest first
        self.assertIn('versions=[_with_cli_block(dict(v), _blocks)', ksrc)
        self.assertIn('for v in _cat.get(c["value"]) or []],', ksrc)

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
        jd._judge_cmd = lambda model, sysp, effort=None, **kw: (seen.__setitem__(model, effort) or ["true"])
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


T_OLD, T_NEW = 1_700_000_000_000, 1_700_000_360_000
HELPER_OFF = ["--settings", '{"apiKeyHelper": ""}']   # the login-billed call's helper suppression, verbatim


class _InlineThread:
    """threading.Thread stand-in: the propagation fan-out runs inline, so a test sees every call at once."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target, self._args, self._kwargs = target, args, (kwargs or {})
    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class FastJudging(unittest.TestCase):
    """Fast mode for the judges: STATE/judge-fast ("on" | "off", off by default) adds the CLI's fastMode opt-in to
    judge calls whose model is Opus. The CLI refuses fast mode to a non-interactive client unless the
    flag-settings layer carries that exact key (sdk_backend.flag_settings_path is the sessions' twin),
    and fast mode is an Opus-only preview, so the key rides only a call whose model reads as the opus
    family through _model_family_version (the bare alias or a pinned version id); every other model's
    argv is the toggle-off argv, byte for byte. `--settings` takes ONE value, so when a login-billed
    call also needs the helper suppression both keys share one JSON overlay, and the login-only overlay
    keeps the exact string the auth-billing tests pin. The setting is a kernel setting in the shape of
    the other judge knobs: validated, gesture-stamped, in /version raw (top level and the cross-machine
    settings dict), applied and acked by /judge-settings, propagated from the socket op, and the
    judge-usage row keeps the CLI's own word on whether fast engaged (fast_mode_state)."""

    def setUp(self):
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

    def _ws(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        jd._state_cache.clear()   # the getters cache by mtime: read the file the op wrote, not the cache
        return sent

    def test_off_by_default_and_the_argv_is_untouched(self):
        self.assertFalse(jd._judge_fast())
        for m in ("opus", "claude-opus-4-6", "sonnet"):
            self.assertNotIn("--settings", jd._judge_cmd(m, "SYS"), m)
        v = km._version_info()
        self.assertEqual(v["judgeFast"], "off", "/version top level, RAW")
        self.assertEqual(v["settings"]["judgeFast"], "off", "the cross-machine settings dict (the gear's mixed marks)")
        self.assertIn("judge-fast", v["settingsGt"], "its stamp rides with the other stores'")

    def test_fast_adds_the_opt_in_for_opus_alias_and_version_ids(self):
        models = ("opus", "claude-opus-4-6", "claude-opus-5", "sonnet", "haiku", "fable")
        off = {m: jd._judge_cmd(m, "SYS") for m in models}
        (jd.STATE / "judge-fast").write_text("on")
        jd._state_cache.clear()
        self.assertTrue(jd._judge_fast())
        for m in ("opus", "claude-opus-4-6", "claude-opus-5"):
            cmd = jd._judge_cmd(m, "SYS")
            self.assertEqual(cmd.count("--settings"), 1, m)
            i = cmd.index("--settings")
            self.assertEqual(json.loads(cmd[i + 1]), {"fastMode": True}, m)
            self.assertEqual(cmd[:i], off[m], "%s: the opt-in is appended and nothing else moves" % m)
        for m in ("sonnet", "haiku", "fable"):
            self.assertEqual(jd._judge_cmd(m, "SYS"), off[m], "%s cannot run fast: the toggle-off argv" % m)
        cmd = jd._judge_cmd("opus", "SYS", "high")
        self.assertIn("--effort", cmd, "the effort flag still lands beside the overlay")
        self.assertEqual(json.loads(cmd[-1]), {"fastMode": True})
        # a login-billed Opus call: ONE overlay with both keys (--settings takes one value)
        cmd = jd._judge_cmd("opus", "SYS", None, auth="login")
        self.assertEqual(cmd.count("--settings"), 1)
        self.assertEqual(json.loads(cmd[-1]), {"fastMode": True, "apiKeyHelper": ""})
        # ...and a login-billed call on a model that cannot run fast keeps the login-only string verbatim
        self.assertEqual(jd._judge_cmd("sonnet", "SYS", None, auth="login")[-2:], HELPER_OFF)

    def test_the_setting_is_a_kernel_setting_like_the_other_judge_knobs(self):
        self.assertIn("judge-fast", km._GT_STORES)
        self.assertIn("judgeFast", dict(km._JUDGE_SETTING_FIELDS))
        self.assertIsNotNone(km._set_judge_fast("on"))
        self.assertEqual((jd.STATE / "judge-fast").read_text(), "on")
        self.assertTrue(jd._judge_fast())
        for bad in ("yes", "true", "1", "", "ON"):
            self.assertIsNone(km._set_judge_fast(bad), "%r is refused unwritten" % bad)
        self.assertEqual((jd.STATE / "judge-fast").read_text(), "on", "the refused values wrote nothing")
        v = km._version_info()
        self.assertEqual((v["judgeFast"], v["settings"]["judgeFast"]), ("on", "on"))
        self.assertIsNotNone(km._set_judge_fast("off"))
        self.assertFalse(jd._judge_fast())
        self.assertEqual(km._version_info()["judgeFast"], "off")

    def test_the_socket_op_stores_on_off_propagates_and_orders_by_gesture(self):
        propagated = []
        saved_threading, saved_prop = km.threading, km._propagate_judge_settings
        ns = {k: getattr(_real_threading, k) for k in dir(_real_threading) if not k.startswith("__")}
        ns["Thread"] = _InlineThread
        km.threading = types.SimpleNamespace(**ns)
        km._propagate_judge_settings = lambda body: propagated.append(body)
        try:
            # the gear's checkbox posts a boolean; the kernel stores on/off and fans the applied value out
            self.assertEqual(self._ws({"type": "setJudgeFast", "enabled": True, "gt": T_NEW}), [])
            self.assertTrue(jd._judge_fast())
            self.assertEqual(propagated, [{"judgeFast": "on", "gt": T_NEW}])
            # an OLDER gesture stands down: nothing applied, nothing propagated, the delivering socket hears it
            sent = self._ws({"type": "setJudgeFast", "enabled": False, "gt": T_OLD})
            self.assertTrue(jd._judge_fast())
            self.assertEqual(len(propagated), 1)
            self.assertEqual([m["setting"] for m in sent if m.get("type") == "settingStale"], ["judge-fast"])
            # a newer one applies and turns it off again
            self._ws({"type": "setJudgeFast", "enabled": False, "gt": T_NEW + 1})
            self.assertFalse(jd._judge_fast())
            self.assertEqual(propagated[-1], {"judgeFast": "off", "gt": T_NEW + 1})
            # a flag that is not a boolean is refused unwritten, with a warn on the delivering socket
            sent = self._ws({"type": "setJudgeFast", "enabled": "on", "gt": T_NEW + 2})
            self.assertFalse(jd._judge_fast())
            self.assertEqual([m["type"] for m in sent], ["warn"])
            self.assertEqual(len(propagated), 2)
        finally:
            km.threading, km._propagate_judge_settings = saved_threading, saved_prop

    def test_the_usage_row_keeps_the_cli_s_fast_readback(self):
        # whether fast engaged is the CLI's call, per account: the result envelope's fast_mode_state is the
        # one readback, so each usage row keeps it (None when the envelope carries none)
        saved_usage = jd.USAGE
        jd.USAGE = jd.STATE / "judge-usage.jsonl"
        try:
            jd._log_judge_usage("planner", "triage", "opus", None,
                                {"usage": {"input_tokens": 1}, "duration_ms": 5, "fast_mode_state": "on"}, 1.0, 2.0)
            jd._log_judge_usage("captioner", "index", "haiku", None, {"usage": {}, "duration_ms": 5}, 3.0, 4.0)
            rows = [json.loads(ln) for ln in jd.USAGE.read_text().splitlines()]
        finally:
            jd.USAGE = saved_usage
        self.assertEqual([r["fast"] for r in rows], ["on", None])
        self.assertEqual(rows[0]["model"], "opus")


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
