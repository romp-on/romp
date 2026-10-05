#!/usr/bin/env python3
"""Fast mode per judge tier (T300, the user 2026-09-10), the add-on over the judges' one Fast mode flag: a box
beside EACH tier's model picker, one flag per tier (STATE/judge-fast is the triage tier's, distill-fast and
index-fast the other two), read by the judges at call time for the tier the call runs in. The gear greys a
tier's box and says why when the tier's effective model cannot run fast; the value is kept, and the judges ask
nothing for that tier. Also here: the one-time carry-over from the single flag, and the four review concerns on
the first cut, each with a test:
  - a key-billed fast call carries the sessions' org-check env (permission follows billing), memoised, never a
    login-billed call;
  - a refused fast ask is loud: the CLI's readback records the refusal per tier (fast-refused.json, lifted by
    /version for the gear's hint) and writes ONE judge-errors row per reason change; an error envelope that
    carries the readback still writes a usage row (zero cost, marked err, skipped by the cost rollup);
  - the readback's field names are the CLI's own: two fixtures carry the result envelope's shape as claude
    2.1.257 prints it (captured 2026-09-10; synthetic values);
  - the cost rollup adds the CLI's own total_cost_usd, which the CLI already doubles for a fast call (measured:
    the same prompt cost twice as much fast), so a fast row is priced at the fast rate with no table of its own.
Hermetic state dir; synthetic values; no sockets; the CLI is a fake where a call runs.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading as _real_threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
FIX = os.path.join(HERE, "fixtures")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_judgefasttiers", os.path.join(BIN, "romp-kernel"))
jd = km.jd

FLAGS = ("judge-fast", "distill-fast", "index-fast")
MODELS = ("judge-model", "distill-model", "index-model")
HELPER_OFF = ["--settings", '{"apiKeyHelper": ""}']   # the login-billed call's helper suppression, verbatim
T_OLD, T_NEW = 1_700_000_000_000, 1_700_000_360_000


def _fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def _clear_state():
    for f in FLAGS + MODELS:
        for name in (f, f + ".gt"):
            try:
                (jd.STATE / name).unlink()
            except OSError:
                pass
    for name in ("fast-refused.json", "judge-fast-tiers.migrated"):
        try:
            (jd.STATE / name).unlink()
        except OSError:
            pass
    jd._state_cache.clear()
    jd._FAST_ORG_MEMO["env"] = None


class _InlineThread:
    """threading.Thread stand-in: the propagation fan-out runs inline, so a test sees every call at once."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target, self._args, self._kwargs = target, args, (kwargs or {})
    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class _Base(unittest.TestCase):
    def setUp(self):
        _clear_state()

    def tearDown(self):
        _clear_state()

    def _pick(self, field, model):
        res = km._apply_judge_settings({field: model})
        self.assertEqual(res[field], model, "the pick must be accepted for the scenario to mean anything")
        return res


class PerTierAsk(_Base):
    def test_fast_capable_is_the_opus_family(self):
        for m in ("opus", "claude-opus-4-6", "claude-opus-5", "Opus"):
            self.assertTrue(jd.fast_capable(m), repr(m))
        for m in ("sonnet", "haiku", "fable", "claude-sonnet-5", "claude-haiku-4-5-20251001", "", None):
            self.assertFalse(jd.fast_capable(m), repr(m))

    def test_each_tier_reads_its_own_flag_and_its_own_model(self):
        self.assertFalse(jd._tier_fast("triage", "opus"), "off by default")
        self.assertIsNotNone(km._set_judge_fast("on"))
        self.assertTrue(jd._tier_fast("triage", "opus"))
        self.assertTrue(jd._judge_fast(), "the first cut's reader is the triage tier's flag")
        self.assertFalse(jd._tier_fast("triage", "sonnet"), "a model that cannot run fast asks nothing; the flag is kept")
        self.assertEqual((jd.STATE / "judge-fast").read_text(), "on")
        self.assertFalse(jd._tier_fast("index", "opus"), "each tier has its own flag")
        self.assertFalse(jd._tier_fast("distill", "opus"))
        self.assertIsNotNone(km._set_index_fast("on"))
        self.assertIsNotNone(km._set_distill_fast("on"))
        self.assertTrue(jd._tier_fast("index", "opus"))
        self.assertTrue(jd._tier_fast("distill", "opus"))
        self.assertTrue(jd._tier_fast(None, "opus"), "no tier = the triage tier, the long-standing default")

    def test_the_argv_carries_the_opt_in_for_the_calls_tier_only(self):
        km._set_distill_fast("on")
        off = jd._judge_cmd("opus", "SYS")
        self.assertNotIn("--settings", off, "the triage tier's flag is off: nothing added")
        self.assertNotIn("--settings", jd._judge_cmd("opus", "SYS", tier="index"))
        cmd = jd._judge_cmd("opus", "SYS", tier="distill")
        self.assertEqual(cmd.count("--settings"), 1)
        self.assertEqual(json.loads(cmd[-1]), {"fastMode": True})
        self.assertEqual(cmd[:-2], off, "the opt-in is appended and nothing else moves")
        self.assertEqual(jd._judge_cmd("sonnet", "SYS", tier="distill"), jd._judge_cmd("sonnet", "SYS"), "a non-Opus distill model: the plain argv")
        # a login-billed call: ONE overlay with both keys; fast off keeps the helper pin byte for byte
        cmd = jd._judge_cmd("opus", "SYS", None, auth="login", tier="distill")
        self.assertEqual(cmd.count("--settings"), 1)
        self.assertEqual(json.loads(cmd[-1]), {"fastMode": True, "apiKeyHelper": ""})
        self.assertEqual(jd._judge_cmd("opus", "SYS", None, auth="login", tier="index")[-2:], HELPER_OFF)

    def test_the_run_passes_its_tier_and_resolved_model(self):
        # the call site is inline in _judge_run_impl: pinned at the source (the end-to-end run is exercised below)
        with open(os.path.join(ROOT, "kernel", "judge.py")) as f:
            src = f.read()
        self.assertIn("fast_asked = _tier_fast(tier, model)", src)
        self.assertIn("_judge_cmd(model, sys_prompt, effort, auth=auth, tier=tier, key_held=bool(held))", src)

    def test_only_on_and_off_are_storable(self):
        for bad in ("yes", "true", "1", "", "session", "ON"):
            self.assertIsNone(km._set_distill_fast(bad), repr(bad))
            self.assertIsNone(km._set_index_fast(bad), repr(bad))
        self.assertFalse((jd.STATE / "distill-fast").exists(), "a refused value never lands")
        self.assertFalse((jd.STATE / "index-fast").exists())


class KernelSetting(_Base):
    def test_the_settings_door_applies_and_reports_the_raw_flags(self):
        res = km._apply_judge_settings({})
        self.assertEqual((res["judgeFast"], res["distillFast"], res["indexFast"]), ("off", "off", "off"))
        res = km._apply_judge_settings({"distillFast": "on", "indexFast": "on"})
        self.assertEqual((jd.STATE / "distill-fast").read_text(), "on")
        self.assertEqual((jd.STATE / "index-fast").read_text(), "on")
        self.assertFalse((jd.STATE / "judge-fast").exists(), "fields not in the body are untouched")
        self.assertEqual((res["judgeFast"], res["distillFast"], res["indexFast"]), ("off", "on", "on"))
        res = km._apply_judge_settings({"distillFast": "maybe"})
        self.assertEqual(res["distillFast"], "on", "an invalid value is ignored and the ack shows what holds")

    def test_a_model_pick_that_cannot_run_fast_keeps_the_flag(self):
        # the gear greys the box and says why; the kernel clears nothing (the user's session's rule, 2026-09-10)
        self._pick("judgeModel", "opus")
        km._set_judge_fast("on"); km._set_distill_fast("on")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = self._pick("judgeModel", "sonnet")
        self.assertEqual((res["judgeFast"], res["distillFast"]), ("on", "on"), "the values stand")
        self.assertEqual(err.getvalue(), "")
        self.assertFalse(jd._tier_fast("triage", jd._triage_model()), "...and the tier asks nothing on Sonnet")
        self.assertFalse(jd._tier_fast("distill", jd._distill_model()), "a Distilling pick of Follow triage follows the triage model")

    def test_the_flags_are_stamp_stores_and_ride_version(self):
        self.assertTrue({"judge-fast", "distill-fast", "index-fast"} <= set(km._GT_STORES))
        self.assertTrue({"judge-fast", "distill-fast", "index-fast"} <= set(km._settings_gt()))
        self.assertTrue({"judgeFast", "distillFast", "indexFast"} <= dict(km._JUDGE_SETTING_FIELDS).keys())
        km._set_index_fast("on")
        v = km._version_info()
        self.assertEqual((v["judgeFast"], v["distillFast"], v["indexFast"]), ("off", "off", "on"), "/version top level, RAW")
        self.assertEqual((v["settings"]["judgeFast"], v["settings"]["distillFast"], v["settings"]["indexFast"]), ("off", "off", "on"),
                         "the cross-machine settings dict (the gear's mixed marks)")
        self.assertEqual(v["fastRefused"], {}, "no refusal recorded: an empty record")
        for store in ("judge-fast", "distill-fast", "index-fast"):
            self.assertIn(store, v["settingsGt"])

    def _ws(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        jd._state_cache.clear()
        return sent

    def test_the_two_new_socket_ops_store_propagate_and_order_by_gesture(self):
        propagated = []
        saved_threading, saved_prop = km.threading, km._propagate_judge_settings
        ns = {k: getattr(_real_threading, k) for k in dir(_real_threading) if not k.startswith("__")}
        ns["Thread"] = _InlineThread
        km.threading = types.SimpleNamespace(**ns)
        km._propagate_judge_settings = lambda body: propagated.append(body)
        try:
            self.assertEqual(self._ws({"type": "setDistillFast", "enabled": True, "gt": T_NEW}), [])
            self.assertTrue(jd._distill_fast())
            self.assertFalse(jd._judge_fast(), "the triage flag is another store")
            self.assertEqual(propagated, [{"distillFast": "on", "gt": T_NEW}])
            self.assertEqual(self._ws({"type": "setIndexFast", "enabled": True, "gt": T_NEW}), [])
            self.assertTrue(jd._index_fast())
            self.assertEqual(propagated[-1], {"indexFast": "on", "gt": T_NEW})
            # an OLDER gesture stands down: nothing applied, nothing propagated, the delivering socket hears it
            sent = self._ws({"type": "setIndexFast", "enabled": False, "gt": T_OLD})
            self.assertTrue(jd._index_fast())
            self.assertEqual(len(propagated), 2)
            self.assertEqual([m["setting"] for m in sent if m.get("type") == "settingStale"], ["index-fast"])
            # a flag that is not a boolean is refused unwritten, with a warn on the delivering socket
            sent = self._ws({"type": "setDistillFast", "enabled": "on", "gt": T_NEW + 2})
            self.assertTrue(jd._distill_fast())
            self.assertEqual([m["type"] for m in sent], ["warn"])
            self.assertEqual(len(propagated), 2)
        finally:
            km.threading, km._propagate_judge_settings = saved_threading, saved_prop


class CarryOver(_Base):
    def test_an_existing_on_is_carried_to_the_tiers_that_can_run_fast(self):
        # the first cut's one flag ran every Opus call fast, whichever tier: a triage flag on with distilling on
        # Opus (following triage) and indexing on Haiku becomes triage on, distilling on, indexing off
        self._pick("judgeModel", "opus"); self._pick("indexModel", "haiku")
        km._set_judge_fast("on")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._migrate_judge_fast_tiers(), 2)
        self.assertEqual((jd.STATE / "distill-fast").read_text(), "on")
        self.assertEqual((jd.STATE / "index-fast").read_text(), "off")
        self.assertEqual((jd.STATE / "judge-fast").read_text(), "on", "the triage flag is the old flag, untouched")
        self.assertEqual(err.getvalue().count("fast mode carried over"), 2)
        self.assertTrue((jd.STATE / "judge-fast-tiers.migrated").exists(), "the marker, written last")
        self.assertEqual(km._migrate_judge_fast_tiers(), 0, "done once: the marker says so")
        self.assertEqual(km._setting_stored_gt("distill-fast"), 1, "a carried value is older than any gesture")
        self.assertEqual(km._set_distill_fast("off", gt=T_OLD), T_OLD, "a peer's earlier explicit off is not stood down by it")

    def test_a_fresh_install_writes_both_off_and_a_later_triage_tick_never_spreads(self):
        # the review finding on the first cut: with the carried value as the marker, a Triage box ticked on a fresh
        # install spread to the distilling tier at the next restart. The marker is the files' existence now.
        self._pick("judgeModel", "opus")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._migrate_judge_fast_tiers(), 2, "both files written, as off")
        self.assertEqual(err.getvalue(), "", "nothing carried: nothing said")
        self.assertEqual(((jd.STATE / "distill-fast").read_text(), (jd.STATE / "index-fast").read_text()), ("off", "off"))
        self.assertEqual((km._setting_stored_gt("distill-fast"), km._setting_stored_gt("index-fast")), (1, 1), "older than any gesture")
        km._set_judge_fast("on")           # the user ticks only the Triage box...
        self.assertEqual(km._migrate_judge_fast_tiers(), 0, "...and a restart carries nothing: the marker stands")
        self.assertEqual((jd.STATE / "distill-fast").read_text(), "off")
        self.assertFalse(jd._tier_fast("distill", jd._distill_model()))
        # a real gesture from any machine, even one stamped before this boot, outranks the migration default
        self.assertEqual(km._set_distill_fast("on", gt=T_OLD), T_OLD)
        self.assertTrue(jd._distill_fast())

    def test_an_off_flag_writes_both_off_too(self):
        km._set_judge_fast("off")
        self.assertEqual(km._migrate_judge_fast_tiers(), 2)
        self.assertEqual(((jd.STATE / "distill-fast").read_text(), (jd.STATE / "index-fast").read_text()), ("off", "off"))

    def test_a_half_applied_carry_over_completes_on_the_next_boot(self):
        # the first boot wrote distill-fast and died before index-fast and the marker (an OSError on the second
        # write, a crash): the next boot leaves the written file alone, writes the missing one, then the marker
        self._pick("judgeModel", "opus"); self._pick("indexModel", "opus")
        km._set_judge_fast("on")
        km._set_distill_fast("off", gt=1)          # what the failed boot left: written, no marker
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._migrate_judge_fast_tiers(), 1, "only the missing tier is written")
        self.assertEqual((jd.STATE / "distill-fast").read_text(), "off", "the written file is left as it was")
        self.assertEqual((jd.STATE / "index-fast").read_text(), "on", "the missing tier gets its carry")
        self.assertTrue((jd.STATE / "judge-fast-tiers.migrated").exists())
        self.assertEqual(km._migrate_judge_fast_tiers(), 0)

    def test_a_failed_write_leaves_no_marker_so_the_next_boot_retries(self):
        km._set_judge_fast("on")
        with patch.object(km, "_set_index_fast", return_value=None), contextlib.redirect_stderr(io.StringIO()):
            # the tier table binds the setters at import: patch the table's entry, not the module name
            tiers = tuple((f, n, w, (km._set_distill_fast if n == "distill-fast" else (lambda v, gt=None: None)), m) for f, n, w, _s, m in km._JUDGE_FAST_TIERS)
            with patch.object(km, "_JUDGE_FAST_TIERS", tiers):
                self.assertEqual(km._migrate_judge_fast_tiers(), 1, "distill landed, index failed")
        self.assertFalse((jd.STATE / "judge-fast-tiers.migrated").exists(), "no marker: not done")
        self.assertFalse((jd.STATE / "index-fast").exists())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._migrate_judge_fast_tiers(), 1, "the next boot completes the rest")
        self.assertTrue((jd.STATE / "judge-fast-tiers.migrated").exists())

    def test_the_kernel_boots_through_it(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        i = src.index("_n = jd.migrate_all_stores()")
        self.assertIn("_migrate_judge_fast_tiers()", src[i:i + 600], "right after the store migration, before any judge pass")


class Readback(_Base):
    """The CLI's answer to a fast ask, in the shape claude 2.1.257 prints (two fixtures captured 2026-09-10 with
    synthetic values): fast_mode_state and fast_mode_disabled_reason on the RESULT envelope, usage.speed beside."""

    def setUp(self):
        super().setUp()
        self._saved_usage, self._saved_errors = jd.USAGE, jd.ERRORS
        jd.USAGE = jd.STATE / ("judge-usage-%s.jsonl" % os.getpid())
        jd.ERRORS = jd.STATE / ("judge-errors-%s.jsonl" % os.getpid())

    def tearDown(self):
        for p in (jd.USAGE, jd.ERRORS):
            try:
                p.unlink()
            except OSError:
                pass
        jd.USAGE, jd.ERRORS = self._saved_usage, self._saved_errors
        super().tearDown()

    def _rows(self, p):
        try:
            return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
        except OSError:
            return []

    def test_the_fixtures_carry_the_fields_the_readback_reads(self):
        fast, std = _fixture("claude-p-result-envelope-fast.json"), _fixture("claude-p-result-envelope-standard.json")
        for env in (fast, std):
            self.assertEqual(env["type"], "result")
            for k in ("fast_mode_state", "fast_mode_disabled_reason", "total_cost_usd", "usage", "is_error"):
                self.assertIn(k, env, k)
        self.assertEqual((fast["fast_mode_state"], fast["fast_mode_disabled_reason"], fast["usage"]["speed"]), ("on", None, "fast"))
        self.assertEqual((std["fast_mode_state"], std["fast_mode_disabled_reason"], std["usage"]["speed"]), ("off", "sdk_opt_in_required", "standard"))
        # the usage row keeps the state and the reason from those very names
        jd._log_judge_usage("planner", "triage", "opus", None, fast, 1.0, 2.0)
        jd._log_judge_usage("planner", "triage", "opus", None, std, 3.0, 4.0)
        rows = self._rows(jd.USAGE)
        self.assertEqual([(r["fast"], r["fastReason"]) for r in rows], [("on", None), ("off", "sdk_opt_in_required")])
        self.assertNotIn("err", rows[0])

    def test_the_cost_rollup_adds_the_cli_s_own_figure_which_is_fast_priced(self):
        # the same one-word prompt cost 0.270 fast and 0.135 standard on 2026-09-10: the CLI's total_cost_usd already
        # carries the fast premium, so the band that sums it prices a fast row right with no table of its own
        fast, std = _fixture("claude-p-result-envelope-fast.json"), _fixture("claude-p-result-envelope-standard.json")
        self.assertAlmostEqual(fast["total_cost_usd"] / std["total_cost_usd"], 2.0, places=6)
        saved = dict(km._JUDGE_USAGE_CACHE) if hasattr(km, "_JUDGE_USAGE_CACHE") else None
        jd._log_judge_usage("planner", "triage", "opus", None, dict(fast, duration_ms=5), 1.0, 2.0)
        jd._log_judge_usage("captioner", "index", "opus", None, dict(std, duration_ms=5), 3.0, 4.0)
        jd._log_judge_usage("planner", "triage", "opus", None, dict(std, total_cost_usd=0, duration_ms=5), 5.0, 6.0, err=True)
        with patch.object(jd, "STATE", jd.STATE), patch.object(km, "_judge_usage_rows", lambda: self._rows(jd.USAGE)):
            band = km._judge_usage(0)
        self.assertEqual(band["total"]["calls"], 2, "the error envelope's row is not a call")
        self.assertAlmostEqual(band["total"]["cost"], fast["total_cost_usd"] + std["total_cost_usd"], places=9)
        self.assertAlmostEqual(band["byTier"]["triage"]["cost"], fast["total_cost_usd"], places=9)
        if saved is not None:
            km._JUDGE_USAGE_CACHE.update(saved)

    def test_a_refusal_is_recorded_once_per_reason_and_cleared_by_an_on(self):
        std = _fixture("claude-p-result-envelope-standard.json")
        jd._note_fast_readback("distill", "opus", std, "distiller", None)
        rec = jd._fast_refused()
        self.assertEqual(rec["distill"]["reason"], "sdk_opt_in_required")
        self.assertEqual(rec["distill"]["model"], "opus")
        self.assertEqual(km._version_info()["fastRefused"]["distill"]["reason"], "sdk_opt_in_required", "/version lifts it for the gear")
        errs = self._rows(jd.ERRORS)
        self.assertEqual([(r["err"], r["judge"]) for r in errs], [("fast-refused", "distiller")])
        self.assertIn("distill tier asked for fast mode on opus", errs[0]["note"])
        jd._note_fast_readback("distill", "opus", std, "briefer", None)
        self.assertEqual(len(self._rows(jd.ERRORS)), 1, "the same refusal standing: said once")
        other = dict(std, fast_mode_disabled_reason="org_disabled")
        jd._FAST_ORG_MEMO["env"] = {"X": "1"}
        jd._note_fast_readback("distill", "opus", other, "distiller", None)
        self.assertEqual(len(self._rows(jd.ERRORS)), 2, "a new reason is a new row")
        self.assertIsNone(jd._FAST_ORG_MEMO["env"], "an org-shaped refusal drops the org-check memo")
        jd._note_fast_readback("distill", "opus", _fixture("claude-p-result-envelope-fast.json"), "distiller", None)
        self.assertEqual(jd._fast_refused(), {}, "an on clears the tier's record")
        self.assertEqual(len(self._rows(jd.ERRORS)), 2)


class ReadbackEdges(Readback):
    def test_cooldown_is_not_a_refusal_and_clears_nothing(self):
        std = _fixture("claude-p-result-envelope-standard.json")
        jd._note_fast_readback("index", "opus", std, "captioner", None)
        cool = dict(std, fast_mode_state="cooldown", fast_mode_disabled_reason=None)
        jd._note_fast_readback("index", "opus", cool, "captioner", None)
        self.assertEqual(jd._fast_refused()["index"]["reason"], "sdk_opt_in_required", "the standing record is untouched")
        self.assertEqual(len(self._rows(jd.ERRORS)), 1, "no row for a cooldown")
        jd._note_fast_readback("distill", "opus", cool, "distiller", None)
        self.assertNotIn("distill", jd._fast_refused(), "a cooldown on a clean tier records nothing")
        # on/cooldown alternation at caption volume: no flapping rows
        for _ in range(3):
            jd._note_fast_readback("index", "opus", _fixture("claude-p-result-envelope-fast.json"), "captioner", None)
            jd._note_fast_readback("index", "opus", cool, "captioner", None)
        self.assertEqual(len(self._rows(jd.ERRORS)), 1)

    def test_an_envelope_without_the_field_says_nothing(self):
        bare = {k: v for k, v in _fixture("claude-p-result-envelope-fast.json").items() if not k.startswith("fast_mode")}
        jd._note_fast_readback("triage", "opus", bare, "planner", None)
        self.assertEqual(jd._fast_refused(), {})
        self.assertEqual(self._rows(jd.ERRORS), [])
        std = _fixture("claude-p-result-envelope-standard.json")
        jd._note_fast_readback("triage", "opus", std, "planner", None)
        jd._note_fast_readback("triage", "opus", bare, "planner", None)
        self.assertIn("triage", jd._fast_refused(), "...and clears nothing either: no answer is no information")

    def test_any_refusal_drops_the_org_check_memo(self):
        jd._FAST_ORG_MEMO["env"] = {}
        std = dict(_fixture("claude-p-result-envelope-standard.json"), fast_mode_disabled_reason="extra_usage_disabled")
        jd._note_fast_readback("triage", "opus", std, "planner", None)
        self.assertIsNone(jd._FAST_ORG_MEMO["env"], "the paying account is asked again after any refusal")
        jd._FAST_ORG_MEMO["env"] = {"X": "1"}
        jd._note_fast_readback("triage", "opus", std, "planner", None)
        self.assertIsNone(jd._FAST_ORG_MEMO["env"], "a standing same-reason refusal drops it too (no row, but the ask stands)")

    def test_concurrent_refusals_of_two_tiers_both_stand(self):
        import threading
        std = _fixture("claude-p-result-envelope-standard.json")
        gate = threading.Barrier(2)
        def refuse(tier, judge):
            gate.wait()
            jd._note_fast_readback(tier, "opus", std, judge, None)
        ts = [threading.Thread(target=refuse, args=("distill", "distiller")), threading.Thread(target=refuse, args=("index", "captioner"))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(set(jd._fast_refused()), {"distill", "index"}, "neither record lost to the other's write")
        self.assertEqual(sorted(r["judge"] for r in self._rows(jd.ERRORS)), ["captioner", "distiller"], "exactly one row each")
        self.assertEqual([p.name for p in jd.STATE.glob("fast-refused.json.*")], [], "no temp file left behind")


class RunEndToEnd(_Base):
    """_judge_run with a fake CLI: the env a key-billed fast call carries, and what an error envelope leaves."""

    def setUp(self):
        super().setUp()
        self._saved_usage, self._saved_errors = jd.USAGE, jd.ERRORS
        jd.USAGE = jd.STATE / ("judge-usage-e2e-%s.jsonl" % os.getpid())
        jd.ERRORS = jd.STATE / ("judge-errors-e2e-%s.jsonl" % os.getpid())
        self._saved_sb = sys.modules.get("romp_sdk_backend")
        self.asked = []
        fake_sb = types.SimpleNamespace(helper_fast_org_env=lambda log, cwd: (self.asked.append(cwd) or {"CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK": "1"}))
        sys.modules["romp_sdk_backend"] = fake_sb

    def tearDown(self):
        if self._saved_sb is not None:
            sys.modules["romp_sdk_backend"] = self._saved_sb
        else:
            sys.modules.pop("romp_sdk_backend", None)
        for p in (jd.USAGE, jd.ERRORS):
            try:
                p.unlink()
            except OSError:
                pass
        jd.USAGE, jd.ERRORS = self._saved_usage, self._saved_errors
        super().tearDown()

    def _run(self, envelope, auth, model="opus", tier="triage"):
        seen = {}
        def fake_run(cmd, input=None, capture_output=None, text=None, cwd=None, env=None, timeout=None):
            seen["cmd"], seen["env"] = list(cmd), dict(env or {})
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")
        saved = jd.subprocess.run
        jd.subprocess.run = fake_run
        try:
            with patch.object(jd, "_judge_engine", return_value="claude"), patch.object(jd, "_judge_auth", return_value=auth), \
                 patch.object(jd, "_login_auth_env", return_value={}), contextlib.redirect_stderr(io.StringIO()):
                out = jd._judge_run(model, "SYS", "u", judge="planner", tier=tier)
        finally:
            jd.subprocess.run = saved
        return out, seen

    def _rows(self, p):
        try:
            return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
        except OSError:
            return []

    def test_a_key_billed_fast_call_carries_the_org_check_env_once_and_a_login_call_never(self):
        km._set_judge_fast("on")
        fast = _fixture("claude-p-result-envelope-fast.json")
        out, seen = self._run(fast, auth="key")
        self.assertEqual(out, "ok")
        self.assertEqual(seen["env"].get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1", "permission follows billing")
        self.assertEqual(json.loads(seen["cmd"][seen["cmd"].index("--settings") + 1]), {"fastMode": True})
        self.assertEqual(self.asked, [None], "asked once, with no project cwd (the judges bill the operator's helper)")
        self._run(fast, auth="key")
        self.assertEqual(self.asked, [None], "memoised: a judge call is not a connect")
        out, seen = self._run(fast, auth="login")
        # ...and never from this process's own environment either: a session's skip is not the judge's verdict. The
        # operator's kill switch (CLAUDE_CODE_DISABLE_FAST_MODE in service.env) is another matter: it reaches every
        # judge child, as it did before the per-tier boxes and as it reaches every session
        with patch.dict(os.environ, {"CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK": "1", "CLAUDE_CODE_DISABLE_FAST_MODE": "1"}):
            out, seen = self._run(fast, auth="login")
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", seen["env"], "a login-billed call: the CLI's probe already asks the paying account")
        self.assertEqual(seen["env"].get("CLAUDE_CODE_DISABLE_FAST_MODE"), "1", "the operator's kill switch reaches the judge child")
        self.assertEqual(json.loads(seen["cmd"][seen["cmd"].index("--settings") + 1]), {"fastMode": True, "apiKeyHelper": ""})
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_FAST_MODE": "1"}):
            out, seen = self._run(fast, auth="key")
        self.assertEqual(seen["env"].get("CLAUDE_CODE_DISABLE_FAST_MODE"), "1", "...on a key-billed call too, beside the org-check switch")
        self.assertEqual(seen["env"].get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1")
        # fast off for the tier, or a model that cannot run it: no probe, no opt-in
        out, seen = self._run(fast, auth="key", model="sonnet")
        self.assertNotIn("--settings", seen["cmd"])
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", seen["env"])
        km._set_judge_fast("off")
        out, seen = self._run(fast, auth="key")
        self.assertNotIn("--settings", seen["cmd"])

    def test_an_error_envelope_keeps_its_readback_as_a_zero_cost_row_and_the_refusal_is_recorded(self):
        km._set_judge_fast("on")
        env = dict(_fixture("claude-p-result-envelope-standard.json"), is_error=True, subtype="error_during_execution",
                   result="Fast mode is not available for this organization")
        out, seen = self._run(env, auth="key")
        self.assertEqual(out, "", "an error envelope is a failed call to the callers")
        rows = self._rows(jd.USAGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["fast"], rows[0]["fastReason"], rows[0]["cost"], rows[0].get("err")), ("off", "sdk_opt_in_required", 0, True))
        errs = self._rows(jd.ERRORS)
        self.assertEqual([r["err"] for r in errs], ["call", "fast-refused"], "the call failure and the refusal, each once")
        self.assertEqual(jd._fast_refused()["triage"]["reason"], "sdk_opt_in_required")
        # the same envelope again: no second refusal row; a success with fast on clears the record
        self._run(env, auth="key")
        self.assertEqual([r["err"] for r in self._rows(jd.ERRORS)], ["call", "fast-refused", "call"], "no second refusal row")
        self._run(_fixture("claude-p-result-envelope-fast.json"), auth="key")
        self.assertEqual(jd._fast_refused(), {})
        rows = self._rows(jd.USAGE)
        self.assertEqual([r.get("err") for r in rows], [True, True, None])

    def test_the_org_check_is_asked_once_across_concurrent_callers(self):
        import threading
        gate = threading.Barrier(4)
        def ask():
            gate.wait()
            jd._fast_org_env()
        ts = [threading.Thread(target=ask) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(self.asked, [None], "one helper run for the pool: the others wait for its answer")
        self.assertEqual(jd._fast_org_env(), {"CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK": "1"})

    def test_a_probe_with_nothing_to_say_is_kept_until_a_refusal(self):
        sys.modules["romp_sdk_backend"] = types.SimpleNamespace(helper_fast_org_env=lambda log, cwd: (self.asked.append(cwd) or {}))
        self.assertEqual(jd._fast_org_env(), {})
        self.assertEqual(jd._fast_org_env(), {})
        self.assertEqual(self.asked, [None], "a dead network does not cost a probe per call")
        km._set_judge_fast("on")
        out, seen = self._run(_fixture("claude-p-result-envelope-standard.json"), auth="key")   # the CLI refuses: re-ask
        self.assertIsNone(jd._FAST_ORG_MEMO["env"])
        jd._fast_org_env()
        self.assertEqual(self.asked, [None, None])

    def test_a_success_without_the_field_is_not_a_refusal(self):
        km._set_judge_fast("on")
        bare = {k: v for k, v in _fixture("claude-p-result-envelope-fast.json").items() if not k.startswith("fast_mode")}
        out, seen = self._run(bare, auth="key")
        self.assertEqual(out, "ok")
        self.assertEqual(jd._fast_refused(), {})
        self.assertEqual(self._rows(jd.ERRORS), [])
        rows = self._rows(jd.USAGE)
        self.assertEqual([(r["fast"], r["fastReason"]) for r in rows], [(None, None)], "the row says the CLI said nothing")

    def test_a_call_that_asked_nothing_records_no_readback(self):
        # fast off: the standard envelope's "off" is not a refusal of anything
        out, seen = self._run(_fixture("claude-p-result-envelope-standard.json"), auth="key")
        self.assertEqual(out, "ok")
        self.assertEqual(jd._fast_refused(), {})
        self.assertEqual(self._rows(jd.ERRORS), [])


class WiredInline(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            self.src = f.read()

    def test_the_three_ops_share_one_arm_and_propagate_their_own_field(self):
        self.assertIn('msg.get("type") in ("setJudgeFast", "setDistillFast", "setIndexFast") and msg.get("enabled") is not None', self.src)
        self.assertIn('args=({_ffield: _jfv, "gt": _jgt},)', self.src)

    def test_the_cost_rollup_skips_error_rows(self):
        self.assertIn('if (o.get("t") or 0) < t0 or o.get("err"):', self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
