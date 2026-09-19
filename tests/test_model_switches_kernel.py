#!/usr/bin/env python3
"""The kernel side of the Automation pane's two Model switches (the user 2026-09-17): Always fast (STATE/always-fast) and Retry
upgrades after downgrades (STATE/retry-upgrade), bare "on"/"off" stores on the judge-knob machinery — validated, stamped,
propagated to every linked kernel, reported RAW by /version at the top level (this gear's fill) and in the cross-machine
settings dict (the mixed marks), members of _GT_STORES and of the /judge-settings field table — plus the "back on" card the
retry's happy end mints (jd.mint_restored_card, mint_fallback_card's twin with the same dedupe). Hermetic state, synthetic
sids, inline threads, no sockets."""
import contextlib
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
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_modelswitches", os.path.join(BIN, "romp-kernel"))
jd = km.jd
STORES = ("always-fast", "retry-upgrade")
T_OLD, T_NEW = 1_700_000_000_000, 1_700_000_360_000


def _clear_state():
    for f in STORES:
        for name in (f, f + ".gt"):
            try:
                (jd.STATE / name).unlink()
            except OSError:
                pass
    jd._state_cache.clear()


class _InlineThread:
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


class TheStores(_Base):
    def test_off_by_default_and_only_on_or_off_are_storable(self):
        res = km._apply_judge_settings({})
        self.assertEqual((res["alwaysFast"], res["retryUpgrade"]), ("off", "off"), "the ack names both, off with no file")
        self.assertIsNone(km._set_always_fast("maybe"), "an invalid value is refused")
        self.assertFalse((jd.STATE / "always-fast").exists(), "…and writes nothing")
        self.assertIsNotNone(km._set_always_fast("on")); self.assertEqual((jd.STATE / "always-fast").read_text(), "on")
        self.assertIsNotNone(km._set_retry_upgrade("on")); self.assertEqual((jd.STATE / "retry-upgrade").read_text(), "on")
        jd._state_cache.clear()
        res = km._apply_judge_settings({"alwaysFast": "off"})
        self.assertEqual((res["alwaysFast"], res["retryUpgrade"]), ("off", "on"), "the settings door applies one and reports both")

    def test_membership_version_and_the_stamps(self):
        self.assertTrue(set(STORES) <= set(km._GT_STORES))
        self.assertTrue({"alwaysFast", "retryUpgrade"} <= dict(km._JUDGE_SETTING_FIELDS).keys(), "/judge-settings applies them on a peer")
        v = km._version_info()
        self.assertEqual((v["alwaysFast"], v["retryUpgrade"]), ("off", "off"), "/version top level, RAW: this gear's fill")
        self.assertEqual((v["settings"]["alwaysFast"], v["settings"]["retryUpgrade"]), ("off", "off"), "the cross-machine dict: the mixed marks")
        for store in STORES:
            self.assertEqual(v["settingsGt"][store], 0, "never set: 0")
        self.assertEqual(km._set_retry_upgrade("on", gt=T_NEW), T_NEW)
        jd._state_cache.clear()
        v = km._version_info()
        self.assertEqual((v["retryUpgrade"], v["settings"]["retryUpgrade"], v["settingsGt"]["retry-upgrade"]), ("on", "on", T_NEW))

    def _ws(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        jd._state_cache.clear()
        return sent

    def test_the_socket_ops_store_propagate_and_order_by_gesture(self):
        propagated = []
        saved_threading, saved_prop = km.threading, km._propagate_judge_settings
        ns = {k: getattr(_real_threading, k) for k in dir(_real_threading) if not k.startswith("__")}
        ns["Thread"] = _InlineThread
        km.threading = types.SimpleNamespace(**ns)
        km._propagate_judge_settings = lambda body: propagated.append(body)
        try:
            self.assertEqual(self._ws({"type": "setAlwaysFast", "enabled": True, "gt": T_NEW}), [])
            self.assertEqual(jd._state_str("always-fast", "off"), "on")
            self.assertEqual(jd._state_str("retry-upgrade", "off"), "off", "the other switch is another store")
            self.assertEqual(propagated, [{"alwaysFast": "on", "gt": T_NEW}], "the applied pick fans out under its stamp")
            self.assertEqual(self._ws({"type": "setRetryUpgrade", "enabled": True, "gt": T_NEW}), [])
            self.assertEqual(jd._state_str("retry-upgrade", "off"), "on")
            self.assertEqual(propagated[-1], {"retryUpgrade": "on", "gt": T_NEW})
            # an OLDER gesture stands down: nothing applied, nothing propagated, the delivering socket hears it
            sent = self._ws({"type": "setRetryUpgrade", "enabled": False, "gt": T_OLD})
            self.assertEqual(jd._state_str("retry-upgrade", "off"), "on")
            self.assertEqual(len(propagated), 2)
            self.assertEqual([m["setting"] for m in sent if m.get("type") == "settingStale"], ["retry-upgrade"])
            # a flag that is not a boolean is refused unwritten, with a warn on the delivering socket
            sent = self._ws({"type": "setAlwaysFast", "enabled": "off", "gt": T_NEW + 2})
            self.assertEqual(jd._state_str("always-fast", "off"), "on")
            self.assertEqual([m["type"] for m in sent], ["warn"])
            self.assertEqual(len(propagated), 2)
        finally:
            km.threading, km._propagate_judge_settings = saved_threading, saved_prop


class TheBackOnCard(unittest.TestCase):
    """OWN sid (the goal-store fixture rule, CLAUDE.md 2026-08-24): other modules journal user gestures against the shared
    placeholder sid and node ids collide across fresh stores."""
    RSID = "6b6b6b6b-7c7c-8d8d-9e9e-0f0f0f0f0f0f"

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        try:
            (jd._overrides_dir() / (self.RSID + ".jsonl")).unlink()
        except OSError:
            pass

    def test_mints_a_completed_card_saying_the_session_is_back_and_dedupes_while_it_stands(self):
        gid = jd.mint_restored_card(self.RSID, "Opus 5", "Fable 5.1", ev_t=1_787_500_000)
        self.assertTrue(gid)
        store = jd.load_goals(self.RSID)
        nd = store["nodes"][gid]
        self.assertEqual(nd["text"], "Model back on Fable 5.1 (after the automatic change to Opus 5)")
        self.assertEqual(nd["why"], jd.RESTORED_WHY)
        self.assertEqual(nd["swap"], {"from": "Opus 5", "to": "Fable 5.1"})
        self.assertTrue(nd.get("nodeComplete"), "minted done: kernel bookkeeping, never a question")
        self.assertIn("Retry upgrades after downgrades", nd.get("doneWhy") or "", "the why names the switch and what happened")
        self.assertIsNone(jd.mint_restored_card(self.RSID, "Opus 5", "Fable 5.1"), "the same fact while the card stands mints nothing")
        self.assertTrue(jd.mint_restored_card(self.RSID, "Sonnet 5", "Fable 5.1"), "a different swap is new information")
        self.assertEqual(jd.RESTORED_WHY, "kernel-observed model restored")
        self.assertNotEqual(jd.RESTORED_WHY, jd.CAPACITY_FALLBACK_WHY, "its own why key: the fallback's dedupe never folds it")


class TheTick(_Base):
    """_retry_upgrade_tick is the one time-shaped piece: a 30 s look between attempts ten minutes apart. Executed with an
    injected backend and explicit clocks (review 2026-09-17: it had only source pins)."""

    def setUp(self):
        super().setUp()
        self._sdk = km._sdk
        km._retry_upgrade_last[0] = 0.0

    def tearDown(self):
        km._sdk = self._sdk
        km._retry_upgrade_last[0] = 0.0
        super().tearDown()

    def test_it_looks_every_thirty_seconds_and_hands_the_clock_to_the_backend(self):
        calls = []
        fake = types.SimpleNamespace(retry_model_upgrades=lambda now: calls.append(now))
        km._sdk = lambda: fake
        km._retry_upgrade_tick(1000.0); self.assertEqual(calls, [1000.0])
        km._retry_upgrade_tick(1010.0); self.assertEqual(calls, [1000.0], "inside the look: nothing")
        km._retry_upgrade_tick(1000.0 + km.RETRY_UPGRADE_TICK_S + 1); self.assertEqual(len(calls), 2)
        self.assertEqual(km.RETRY_UPGRADE_TICK_S, 30)

    def test_no_backend_or_an_older_one_is_a_quiet_no_op(self):
        km._sdk = lambda: None
        km._retry_upgrade_tick(2000.0)   # no raise
        km._sdk = lambda: types.SimpleNamespace()   # a backend without the method
        km._retry_upgrade_tick(2000.0 + km.RETRY_UPGRADE_TICK_S + 1)

    def test_an_applied_flip_reaches_the_running_sessions_and_a_stale_one_does_not(self):
        calls = []
        km._sdk = lambda: types.SimpleNamespace(apply_model_switches=lambda: calls.append(1))
        saved_threading, saved_prop = km.threading, km._propagate_judge_settings
        ns = {k: getattr(_real_threading, k) for k in dir(_real_threading) if not k.startswith("__")}
        ns["Thread"] = _InlineThread
        km.threading = types.SimpleNamespace(**ns)
        km._propagate_judge_settings = lambda body: None
        try:
            sent = []
            client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "setAlwaysFast", "enabled": True, "gt": T_NEW}, client)
            self.assertEqual(calls, [1], "the gear's own flip: the sessions follow")
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "setAlwaysFast", "enabled": False, "gt": T_OLD}, client)
            self.assertEqual(calls, [1], "a stale gesture applied nothing, so nothing follows")
            km._apply_judge_settings({"retryUpgrade": "on", "gt": T_NEW + 5})
            self.assertEqual(calls, [1, 1], "a peer's propagated pick lands here too")
            km._apply_judge_settings({"judgeFast": "on", "gt": T_NEW + 6})
            self.assertEqual(calls, [1, 1], "another field: not these switches' business")
        finally:
            km.threading, km._propagate_judge_settings = saved_threading, saved_prop
            jd._state_cache.clear()
            for f in ("judge-fast", "judge-fast.gt"):
                try:
                    (jd.STATE / f).unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()