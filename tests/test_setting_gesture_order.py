#!/usr/bin/env python3
"""A kernel setting applies in GESTURE order, not arrival order (2026-08-29).

Federation queues KERNEL_SETTING sends per host while a socket is down and flushes them on
reconnect (ui/webview/federation.ts sendRemote/flushPending) — latest per TYPE, but only per TAB.
A dashboard tab frozen for hours (a backgrounded phone, a throttled browser tab) can therefore
re-dial and deliver a pick the user superseded from another device long ago. The kernel used to
apply whatever arrived, and the judge tiers RE-PROPAGATE what they apply, so one stale flush
walked every linked kernel back to the hours-old pick — and with the mesh then AGREEING, the
gear's mixed-marks disagreement surface showed nothing. The standing rule (CLAUDE.md, cards):
a writer whose evidence predates newer filed state stands down at the write moment.

The mechanism under test (kernel/kernel.py), enforced at the authoritative store so every path
is covered at once — live sends, queue flushes, racing dashboards, propagation legs:
- dashboards stamp every setting gesture with `gt` (epoch ms captured at the click; a queued
  flush carries the ORIGINAL stamp — pinned in ui/webview/federation-send-queue.test.ts),
- each setting's store persists the last-APPLIED stamp beside the value (auto-nudge.json /
  file-editing.json gain a "gt" field; the bare judge-tier stores gain a `<name>.gt` sidecar;
  a store without the field reads as gt 0),
- an arriving stamp older-or-equal to the stored one STANDS DOWN: no apply, no propagation,
  one loud stderr line naming both stamps; a message with NO stamp (older dashboard, direct
  HTTP caller) applies as it always did and records its arrival time.

Synthetic everything: hermetic XDG state, placeholder hosts/tokens, stubbed transport. The WS
handlers fan propagation out on a daemon thread; these tests swap the kernel's `threading`
binding for a delegating namespace whose Thread runs inline, so every fan-out is observed
synchronously and nothing sleeps.
"""
import contextlib
import errno
import io
import json
import os
import shutil
import tempfile
import threading as _real_threading
import time
import types
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_gestureorder", os.path.join(BIN, "romp-kernel"))

T_OLD, T_NEW = 1_700_000_000_000, 1_700_000_360_000   # two gestures six minutes apart


class _InlineThread:
    """Thread stand-in for the WS handlers' propagation fan-out: start() runs the target
    synchronously, so a test sees every propagation call without waiting on a real thread."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target, self._args, self._kwargs = target, args, (kwargs or {})

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


def _inline_threading():
    """A namespace that answers like the real threading module except Thread runs inline.
    Swapped onto km's MODULE GLOBAL only — the stdlib module itself is never touched, so
    nothing outside the kernel module can pick the fake up."""
    ns = types.SimpleNamespace(**{k: getattr(_real_threading, k)
                                  for k in dir(_real_threading) if not k.startswith("_")})
    ns.Thread = _InlineThread
    return ns


class _Base(unittest.TestCase):
    """Fresh STATE per test; the WS handlers' side effects stubbed (nudge tick, tmux, threads,
    the propagation fan-out recorded)."""

    def setUp(self):
        self._td = tempfile.mkdtemp()
        self._saved_state = km.jd.STATE
        km.jd.STATE = Path(self._td)
        km.jd._state_cache.clear()
        km._autonudge_cache.clear()
        vars(km).get("_ledger_write_failed", {}).clear()   # the writer's per-episode registry: no episode leaks in
        km._stale_seen.refused = None                      # a direct setter call's verdict never rides a later dispatch
        self._saved = {n: getattr(km, n) for n in
                       ("_propagate_judge_settings", "_auto_nudge_tick", "_tmux_sessions", "threading")}
        self.propagated = []
        km._propagate_judge_settings = self.propagated.append
        km._auto_nudge_tick = lambda *a, **k: None
        km._tmux_sessions = lambda: {}
        km.threading = _inline_threading()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km.jd.STATE = self._saved_state
        km.jd._state_cache.clear()
        km._autonudge_cache.clear()
        shutil.rmtree(self._td, ignore_errors=True)

    def dispatch(self, msg):
        """Drive the real WS handler; returns what it wrote to stderr."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, {})
        return err.getvalue()


class GestureOrderAtTheStore(_Base):
    """The setters themselves enforce the ordering, so every caller is covered."""

    def test_a_stale_judge_pick_stands_down_loudly(self):
        self.assertEqual(km._set_judge_model("opus", gt=T_NEW), T_NEW)
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "opus")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_judge_model("fable", gt=T_OLD), "older stamp must not apply")
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "opus", "the newer pick stands")
        for needle in ("judge-model", str(T_OLD), str(T_NEW), "stood down"):
            self.assertIn(needle, err.getvalue(), "the stand-down names the setting and both stamps")

    def test_equal_stamps_keep_the_stored_value(self):
        # determinism under identical clocks: equal gt → stand down, never a coin flip
        km._set_judge_model("opus", gt=T_NEW)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_judge_model("fable", gt=T_NEW))
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "opus")
        self.assertEqual(km._set_judge_model("fable", gt=T_NEW + 1), T_NEW + 1, "any newer stamp applies")
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "fable")

    def test_an_equal_stamp_with_the_same_value_is_a_silent_echo(self):
        # a judge pick reaches a remote kernel twice with one gt (dashboard broadcast + the origin
        # kernel's fan-out); the second copy is the gesture's own echo — no stand-down line, no
        # settingStale notice — while an equal stamp carrying a DIFFERENT value still stands down loudly
        km._set_judge_model("opus", gt=T_NEW)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_judge_model("opus", gt=T_NEW), "nothing to apply")
        self.assertEqual(err.getvalue(), "", "the echo is silent")
        self.assertIsNone(km._pop_stale_notice(), "…and leaves no settingStale notice for the socket")
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "opus")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_judge_model("fable", gt=T_NEW))
        self.assertIn("stale gesture stood down", err.getvalue(), "a differing equal-stamp gesture is still loud")
        self.assertIsNotNone(km._pop_stale_notice())
        # the same rule on a boolean setter
        self.assertEqual(km._set_auto_nudge(False, gt=T_NEW), T_NEW)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_auto_nudge(False, gt=T_NEW))
        self.assertEqual(err.getvalue(), "")
        self.assertIsNone(km._pop_stale_notice())

    def test_auto_nudge_and_file_editing_order_the_same_way(self):
        self.assertEqual(km._set_auto_nudge(False, gt=T_NEW), T_NEW)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_auto_nudge(True, gt=T_OLD))
        km._autonudge_cache.clear()
        self.assertFalse(km._auto_nudge_on(), "the newer OFF survives the stale ON")
        self.assertEqual(km._set_file_editing(True, gt=T_NEW), T_NEW)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_file_editing(False, gt=T_OLD))
        self.assertTrue(km._file_editing_on(), "the newer consent survives the stale revoke")

    def test_compact_suggest_orders_the_same_way_on_its_own_clock(self):
        # T208 (2026-09-01): it shares auto-nudge.json but wears its
        # OWN stamp key (compactSuggestGt) — ordering is per SETTING, so a compact-suggest gesture
        # never outranks an auto-nudge one, or vice versa, just because they share a file.
        self.assertEqual(km._set_compact_suggest(True, gt=T_NEW), T_NEW)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_compact_suggest(False, gt=T_OLD))
        km._autonudge_cache.clear()
        self.assertTrue(km._compact_suggest_on(), "the newer ON survives the stale OFF")
        # the sibling setting's clock is untouched: an older-stamped auto-nudge gesture still
        # applies, because the compact-suggest stamps above never landed on ITS key
        self.assertEqual(km._set_auto_nudge(True, gt=T_OLD), T_OLD)

    def test_an_unstamped_apply_still_arms_the_store_against_stale_flushes(self):
        # compat is one-way on purpose: no gt applies as today (arrival-stamped), and that arrival
        # stamp then outranks any hours-old stamped flush that shows up later
        before = int(time.time() * 1000)
        applied = km._set_judge_model("haiku")
        self.assertIsInstance(applied, int)
        self.assertGreaterEqual(applied, before, "an unstamped apply records its arrival time")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_judge_model("fable", gt=T_OLD))
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "haiku")

    def test_an_invalid_value_never_writes_a_stamp(self):
        self.assertIsNone(km._set_judge_model("gpt-99", gt=T_NEW), "garbage is refused, stamped or not")
        self.assertFalse((km.jd.STATE / "judge-model").exists())
        self.assertFalse((km.jd.STATE / "judge-model.gt").exists(),
                         "a refused value must not burn its stamp — the store never heard it")


class PreexistingStoresReadAsZero(_Base):
    """File-format compat: every store written before this mechanism has no stamp, and must
    read as gt 0 so the FIRST stamped gesture wins over it."""

    def test_auto_nudge_json_without_the_field(self):
        (km.jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {"g1": {"count": 1}}}))
        km._autonudge_cache.clear()
        self.assertEqual(km._set_auto_nudge(False, gt=1), 1, "gt 1 beats the absent field's 0")
        d = json.loads((km.jd.STATE / "auto-nudge.json").read_text())
        self.assertFalse(d["enabled"])
        self.assertEqual(d["gt"], 1)
        self.assertEqual(d["nudged"], {"g1": {"count": 1}}, "the ledger the file also carries survives")

    def test_file_editing_json_without_the_field(self):
        (km.jd.STATE / "file-editing.json").write_text(json.dumps({"enabled": True}))
        self.assertEqual(km._set_file_editing(False, gt=1), 1)
        d = json.loads((km.jd.STATE / "file-editing.json").read_text())
        self.assertEqual((d["enabled"], d["gt"]), (False, 1))

    def test_judge_store_without_a_sidecar(self):
        (km.jd.STATE / "judge-model").write_text("opus")
        self.assertEqual(km._judge_state_gt("judge-model"), 0, "no sidecar reads as 0")
        self.assertEqual(km._set_judge_model("fable", gt=1), 1)
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "fable")


class TheIncidentReplay(_Base):
    """The failure that motivated this, driven through the real WS handler: host X was down when
    the pick queued; the tab froze; a newer pick from another device ran the mesh; hours later the
    old tab re-dialed and flushed the original pick."""

    def test_a_stale_flush_cannot_walk_the_pick_back(self):
        # the newer gesture (from the phone) landed first…
        self.dispatch({"type": "setJudgeModel", "model": "fable", "gt": T_NEW})
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "fable")
        self.assertEqual(self.propagated, [{"judgeModel": "fable", "gt": T_NEW}],
                         "an applied pick fans out carrying its own gesture stamp")
        # …then the frozen tab woke and flushed the superseded one
        err = self.dispatch({"type": "setJudgeModel", "model": "opus", "gt": T_OLD})
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "fable", "the store keeps the newer pick")
        self.assertEqual(len(self.propagated), 1,
                         "the stale flush propagates NOTHING — re-propagation is how one flush reverted a mesh")
        for needle in ("judge-model", str(T_OLD), str(T_NEW), "stood down"):
            self.assertIn(needle, err, "the stand-down is loud and names the stale and stored stamps")

    def test_reverse_arrival_order_newest_gesture_wins(self):
        # the race variant: same setting changed from two dashboards during one outage — both
        # flush on recovery and SOCKET order used to decide; gesture order must decide instead
        self.dispatch({"type": "setAutoNudge", "enabled": False, "gt": T_NEW})
        self.dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_OLD})
        km._autonudge_cache.clear()
        self.assertFalse(km._auto_nudge_on(), "the newest gesture wins regardless of arrival order")
        self.dispatch({"type": "setFileEditing", "enabled": True, "gt": T_NEW})
        self.dispatch({"type": "setFileEditing", "enabled": False, "gt": T_OLD})
        self.assertTrue(km._file_editing_on())

    def test_an_unstamped_message_applies_exactly_as_before(self):
        # full compat: an older dashboard, a peer kernel on older code, a direct HTTP caller
        before = int(time.time() * 1000)
        self.dispatch({"type": "setDistillModel", "model": "haiku"})
        self.assertEqual((km.jd.STATE / "distill-model").read_text(), "haiku")
        self.assertEqual(len(self.propagated), 1)
        self.assertEqual(self.propagated[0]["distillModel"], "haiku")
        self.assertGreaterEqual(self.propagated[0]["gt"], before,
                                "the fan-out carries the arrival stamp so receivers stay ordered too")
        # …and an unstamped message even beats a newer STORED stamp: old senders keep working
        self.dispatch({"type": "setDistillModel", "model": "triage"})
        self.assertEqual((km.jd.STATE / "distill-model").read_text(), "triage")


class PropagationCarriesTheStamp(_Base):
    """End to end: the origin's gt rides the /judge-settings leg, so a stale value can never win
    at any receiver by arriving via a second hop."""

    def setUp(self):
        super().setUp()
        km._propagate_judge_settings = self._saved["_propagate_judge_settings"]   # the real fan-out
        self._saved_rkc, self._saved_rem = km._remote_kernel_call, dict(km._remotes)
        km._remotes.clear()
        km._remotes.update({"boxup": {"host": "boxup", "status": "up", "local_port": 1, "token": "t1"}})
        self.calls = []
        km._remote_kernel_call = lambda r, m, p, payload=None, timeout=8: (
            self.calls.append((r["host"], m, p, payload)) or (200, {"ok": True}, None))

    def tearDown(self):
        km._remote_kernel_call = self._saved_rkc
        km._remotes.clear()
        km._remotes.update(self._saved_rem)
        super().tearDown()

    def test_the_forwarded_payload_carries_the_origin_gesture(self):
        self.dispatch({"type": "setJudgeModel", "model": "fable", "gt": T_NEW})
        self.assertEqual(self.calls, [("boxup", "POST", "/judge-settings",
                                       {"judgeModel": "fable", "gt": T_NEW})])

    def test_the_receiving_route_orders_by_the_forwarded_stamp(self):
        # play the RECEIVER: /judge-settings applies through _apply_judge_settings, which must
        # gate each field on the body-level gt — and never re-propagate (the one-hop contract,
        # pinned in test_judge_settings_sync.py)
        res = km._apply_judge_settings({"judgeModel": "fable", "gt": T_NEW})
        self.assertEqual(res["judgeModel"], "fable")
        with contextlib.redirect_stderr(io.StringIO()):
            res = km._apply_judge_settings({"judgeModel": "opus", "gt": T_OLD})
        self.assertEqual(res["judgeModel"], "fable", "the ack shows the newer pick still standing")
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "fable")
        self.assertEqual(self.calls, [], "a receiver applies without re-propagating")


class UpdateModeOrders(_Base):
    """setUpdateMode is in federation's queued KERNEL_SETTING class, so a frozen tab's flush can
    deliver it hours late — and it used to apply unconditionally: a stale flush silently reverted
    how this machine self-updates at boot (ask/auto/off). Gated exactly like _set_file_editing:
    the stamp persists in update-mode.json beside the mode, a file without the field reads as 0."""

    def test_a_stale_update_mode_flush_stands_down_loudly(self):
        self.assertEqual(km._set_update_mode("auto", gt=T_NEW), T_NEW)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_update_mode("off", gt=T_OLD), "older stamp must not apply")
        self.assertEqual(km._update_mode(), "auto", "the newer mode stands")
        for needle in ("update-mode", str(T_OLD), str(T_NEW), "stood down"):
            self.assertIn(needle, err.getvalue(), "the stand-down names the setting and both stamps")

    def test_the_dispatch_branch_hands_the_stamp_over(self):
        self.dispatch({"type": "setUpdateMode", "mode": "auto", "gt": T_NEW})
        err = self.dispatch({"type": "setUpdateMode", "mode": "off", "gt": T_OLD})
        self.assertEqual(km._update_mode(), "auto")
        self.assertIn("stood down", err)

    def test_the_stamp_persists_beside_the_mode(self):
        km._set_update_mode("auto", gt=T_NEW)
        d = json.loads((km.jd.STATE / "update-mode.json").read_text())
        self.assertEqual((d["mode"], d["gt"]), ("auto", T_NEW))

    def test_a_file_without_the_field_reads_as_zero(self):
        # file-format compat: a mode written before the mechanism yields to any stamped gesture
        (km.jd.STATE / "update-mode.json").write_text(json.dumps({"mode": "auto"}))
        self.assertEqual(km._set_update_mode("off", gt=1), 1, "gt 1 beats the absent field's 0")
        self.assertEqual(km._update_mode(), "off")

    def test_an_unstamped_set_applies_and_arms_the_store(self):
        before = int(time.time() * 1000)
        applied = km._set_update_mode("off")
        self.assertIsInstance(applied, int)
        self.assertGreaterEqual(applied, before, "an unstamped apply records its arrival time")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_update_mode("auto", gt=T_OLD))
        self.assertEqual(km._update_mode(), "off")

    def test_an_invalid_mode_never_writes_a_stamp(self):
        self.assertIsNone(km._set_update_mode("banana", gt=T_NEW))
        self.assertFalse((km.jd.STATE / "update-mode.json").exists(),
                         "a refused mode must not burn its stamp — the store never heard it")


class TwoFlushRace(_Base):
    """Check-then-write must be ATOMIC per setting: the stores read the stamp, check
    _setting_stale, then write, on a ThreadingHTTPServer — so two near-simultaneous flushes
    (two dashboards flushing one setting on a host's recovery) could both pass the check against
    the same stored stamp and then land in SOCKET order, the exact inversion the mechanism
    promises away. Deterministic, events only: the OLDER gesture is held between its check and
    its write until the NEWER one has either fully landed (unserialized code — the bug) or
    provably blocked on the settings lock (serialized code); no schedule depends on timing."""

    def _race(self, older_call, newer_call):
        old_checked = _real_threading.Event()
        newer_progress = _real_threading.Event()
        orig_check = km._setting_stale

        def gated(name, gt, applied_gt):
            r = orig_check(name, gt, applied_gt)
            if _real_threading.current_thread().name == "older":
                old_checked.set()          # the older gesture has read + checked the stored stamp…
                newer_progress.wait(10)    # …and stalls before writing, until the newer one moves
            return r

        km._setting_stale = gated
        real_lock = getattr(km, "_SETTINGS_LOCK", None)
        if real_lock is not None:
            class _Probe:                  # detect the newer flush BLOCKING on the held lock —
                def __enter__(self):       # the event that releases the older one's stall
                    if not real_lock.acquire(blocking=False):
                        newer_progress.set()
                        real_lock.acquire()
                def __exit__(self, *a):
                    real_lock.release()
            km._SETTINGS_LOCK = _Probe()
        try:
            errs = []

            def guarded(fn):
                def run():
                    try:
                        fn()
                    except Exception as e:   # base-shape signature errors must not hang the schedule
                        errs.append(e)
                    finally:
                        old_checked.set()
                        newer_progress.set()
                return run

            told = _real_threading.Thread(target=guarded(older_call), name="older")
            told.start()
            self.assertTrue(old_checked.wait(10), "the older gesture reached its ordering check")
            tnew = _real_threading.Thread(
                target=guarded(lambda: (newer_call(), newer_progress.set())), name="newer")
            tnew.start()
            told.join(10)
            tnew.join(10)
            self.assertFalse(told.is_alive() or tnew.is_alive(), "both flushes finished")
        finally:
            km._setting_stale = orig_check
            if real_lock is not None:
                km._SETTINGS_LOCK = real_lock

    def test_judge_store_racing_flushes_land_in_gesture_order(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self._race(lambda: km._set_judge_model("opus", gt=T_OLD),
                       lambda: km._set_judge_model("fable", gt=T_NEW))
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "fable",
                         "socket order must never beat gesture order between two racing flushes")
        self.assertEqual(km._judge_state_gt("judge-model"), T_NEW)

    def test_file_editing_racing_flushes_land_in_gesture_order(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self._race(lambda: km._set_file_editing(False, gt=T_OLD),
                       lambda: km._set_file_editing(True, gt=T_NEW))
        d = json.loads((km.jd.STATE / "file-editing.json").read_text())
        self.assertEqual((d["enabled"], d["gt"]), (True, T_NEW))

    def test_update_mode_racing_flushes_land_in_gesture_order(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self._race(lambda: km._set_update_mode("off", gt=T_OLD),
                       lambda: km._set_update_mode("auto", gt=T_NEW))
        self.assertEqual(km._update_mode(), "auto")


class SidecarCrashSafety(_Base):
    """The judge value file and its .gt sidecar are two writes. They must publish atomically and
    SIDECAR FIRST: a crash between them then leaves the new stamp guarding the old value — a
    legitimate re-send stands down once and the next change heals — where value-first left the
    new value guarded by the OLD stamp, so a later stale gesture could invert the ordering. And
    any write failure must be LOUD: the old `except OSError: return None` was a mute third
    meaning of None (the callers' no-propagation gates silently skip the fan-out on it)."""

    def _crash_on_write(self, n):
        """Patch Path.write_text to raise OSError on call number `n` from now (both the raw
        write_text shape and _atomic_write's temp-file write funnel through it)."""
        import pathlib
        counter = {"n": 0}
        orig = pathlib.Path.write_text

        def failing(p, *a, **k):
            counter["n"] += 1
            if counter["n"] == n:
                raise OSError("simulated crash between the store's two writes")
            return orig(p, *a, **k)

        pathlib.Path.write_text = failing
        return lambda: setattr(pathlib.Path, "write_text", orig)

    def test_a_crash_between_the_writes_cannot_let_a_stale_gesture_invert(self):
        self.assertEqual(km._set_judge_model("opus", gt=T_OLD), T_OLD)
        restore = self._crash_on_write(2)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(km._set_judge_model("fable", gt=T_NEW),
                                  "the interrupted pick reports not-applied")
        finally:
            restore()
        # THE property: after the crash, a gesture older than the interrupted pick stands down
        t_mid = (T_OLD + T_NEW) // 2
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(km._set_judge_model("haiku", gt=t_mid),
                              "a stale gesture must never apply, whatever a crash left behind")
        self.assertNotEqual((km.jd.STATE / "judge-model").read_text(), "haiku")
        # …and the next legitimate change heals both files
        self.assertEqual(km._set_judge_model("fable", gt=T_NEW + 1), T_NEW + 1)
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "fable")
        self.assertEqual(km._judge_state_gt("judge-model"), T_NEW + 1)

    def test_the_crash_errs_toward_stand_down_never_inversion(self):
        # the state a crash may leave: the STAMP advanced, the VALUE kept — never the reverse
        self.assertEqual(km._set_judge_model("opus", gt=T_OLD), T_OLD)
        restore = self._crash_on_write(2)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km._set_judge_model("fable", gt=T_NEW)
        finally:
            restore()
        self.assertEqual((km.jd.STATE / "judge-model").read_text(), "opus",
                         "the value write never landed")
        self.assertEqual(km._judge_state_gt("judge-model"), T_NEW,
                         "the sidecar published FIRST — the safe direction")

    def test_the_half_applied_state_is_loud(self):
        km._set_judge_model("opus", gt=T_OLD)
        restore = self._crash_on_write(2)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._set_judge_model("fable", gt=T_NEW))
        finally:
            restore()
        for needle in ("judge-model", "half-applied"):
            self.assertIn(needle, err.getvalue().lower(),
                          "the half-apply names itself on stderr — rollback is impossible, so be loud")

    def test_a_failed_first_write_is_loud_and_applies_nothing(self):
        restore = self._crash_on_write(1)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._set_judge_model("fable", gt=T_NEW))
        finally:
            restore()
        self.assertIn("judge-model", err.getvalue(), "an OSError refusal is never silent")
        self.assertFalse((km.jd.STATE / "judge-model").exists())
        self.assertFalse((km.jd.STATE / "judge-model.gt").exists())

    def test_a_value_the_store_never_took_does_not_fan_out(self):
        # KEPT behavior, now documented in the docstring: consistency over delivery — the mesh
        # must never adopt a value the origin's own store refused.
        restore = self._crash_on_write(2)
        try:
            err_txt = self.dispatch({"type": "setJudgeModel", "model": "fable", "gt": T_NEW})
        finally:
            restore()
        self.assertEqual(self.propagated, [], "no propagation for a pick the store never took")
        self.assertIn("judge-model", err_txt, "…and the refusal says so on stderr")


class StoreWriteFailureIsLoudAndContained(_Base):
    """A store write that fails (ENOSPC, a read-only STATE) must die INSIDE the setter — one loud
    stderr line naming the setting, None returned (stood-down semantics: no apply, no tick, no
    propagation) — and never escape it: the WS reader loop classifies a raised OSError as a
    presumed socket failure and re-raises it into an outer silent pass, so a full-disk gear
    toggle used to tear the dashboard's entire WebSocket down with zero log output.
    _set_judge_state and _set_update_mode already hold this contract; these tests pin
    _set_auto_nudge and _set_file_editing onto the same one."""

    def _fail_writes(self, exc=None):
        """Patch Path.write_text to raise OSError on every call from now (both stores publish
        via _atomic_write, whose temp-file write funnels through it). The raise carries the REAL
        shape (errno, strerror and the path being written, which for a publish is the temp file
        with its per-call sequence), so a fault text built from str(e) would differ on every call
        and the once-per-episode and never-the-temp-path assertions below would catch it (review
        find, 2026-09-08). Default: a read-only STATE dir."""
        import pathlib
        orig = pathlib.Path.write_text
        e = exc or OSError(errno.EROFS, "Read-only file system")

        def failing(p, *a, **k):
            raise OSError(e.errno, e.strerror, str(p))

        pathlib.Path.write_text = failing
        return lambda: setattr(pathlib.Path, "write_text", orig)

    def test_auto_nudge_write_failure_is_loud_none_and_applies_nothing(self):
        self.assertEqual(km._set_auto_nudge(True, gt=T_OLD), T_OLD)
        restore = self._fail_writes()
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._set_auto_nudge(False, gt=T_NEW),
                                  "a failed write reports not-applied — it never raises")
        finally:
            restore()
        self.assertIn("auto-nudge", err.getvalue(), "the refusal names the setting on stderr")
        km._autonudge_cache.clear()
        self.assertTrue(km._auto_nudge_on(), "nothing applied — the store keeps the old value")

    def test_file_editing_write_failure_is_loud_none_and_applies_nothing(self):
        self.assertEqual(km._set_file_editing(True, gt=T_OLD), T_OLD)
        restore = self._fail_writes()
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._set_file_editing(False, gt=T_NEW),
                                  "a failed write reports not-applied — it never raises")
        finally:
            restore()
        self.assertIn("file-editing", err.getvalue(), "the refusal names the setting on stderr")
        self.assertTrue(km._file_editing_on(), "the consent the store holds survives the failed revoke")

    def test_a_full_disk_gear_toggle_does_not_tear_the_ws_down(self):
        # the incident shape, through the REAL dispatch: an OSError escaping the setter reaches
        # the reader loop's `except (BrokenPipeError, ConnectionResetError, OSError): raise`,
        # which hands it to an outer silent `pass` — connection dead, nothing logged. The
        # setter's own stderr line must be the loudest thing that happens.
        ticks = []
        km._auto_nudge_tick = lambda *a, **k: ticks.append(a)
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        restore = self._fail_writes()
        try:
            for msg, name in (({"type": "setFileEditing", "enabled": True, "gt": T_NEW}, "file-editing"),
                              ({"type": "setAutoNudge", "enabled": False, "gt": T_NEW}, "auto-nudge")):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    try:
                        km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
                    except OSError:
                        self.fail("an OSError escaped _dispatch_ws (%s) — the reader loop "
                                  "re-raises it as a socket failure and silently tears the "
                                  "WebSocket down" % name)
                self.assertIn(name, err.getvalue(), "the failure is loud and names the setting")
        finally:
            restore()
        self.assertTrue(client["alive"], "the delivering client survives the failed write")
        self.assertEqual(ticks, [], "a stood-down toggle fires no nudge tick — no new information")
        # This used to pin "no settingStale frame" for both toggles: a write failure sent nothing. The
        # maintainer's fold on PR #1019 draws the line the other way — a user gesture's WRITE step is a
        # fault boundary too, and a refused write must be TOLD — so the ledger toggle now answers its socket
        # with the same settingStale+why frame an unproved-read refusal sends (the gear snaps the box back to
        # the stored value and says why). setFileEditing's store is not one of this PR's two ledgers and keeps
        # its silent arm for now: nothing escapes there, and nothing is told.
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual([(f["setting"], f["why"].split(":")[0]) for f in frames], [("auto-nudge", "write failed")],
                         "the ledger toggle's refused write is answered on the delivering socket, once")
        self.assertIsNone(frames[0]["kept"], "no write ever landed here: the frame does not present the default as kept")

    def test_a_refused_ledger_write_is_told_per_gesture_and_said_once_per_episode(self):
        """The maintainer's fold on PR #1019 (a user gesture's WRITE step is a fault boundary too), on the two
        ledger toggles through the REAL dispatch: every gesture whose publish raises is answered on the
        delivering socket — the settingStale+why frame, `kept` naming the value the gear re-fills to — while
        the fault itself is said ONCE per episode (one stderr line, one error-center row) however many gestures
        repeat it; a landed write ends the episode, and a fresh fault speaks again. Before: the OSError arm
        logged per call and sent nothing, so the gear kept the flipped box while the kernel held the old value."""
        self.assertEqual(km._set_auto_nudge(True, gt=T_OLD), T_OLD)
        del km._SDK_BOOT_PROBLEMS[:]
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        full = OSError(errno.ENOSPC, "No space left on device")
        restore, err = self._fail_writes(full), io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                for msg in ({"type": "setAutoNudge", "enabled": False, "gt": T_NEW},
                            {"type": "setAutoNudge", "enabled": False, "gt": T_NEW + 1},
                            {"type": "setCompactSuggest", "enabled": True, "gt": T_NEW}):
                    km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        finally:
            restore()
        self.assertTrue(client["alive"], "nothing escaped to the reader loop")
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual([(f["setting"], f["gt"]) for f in frames],
                         [("auto-nudge", T_NEW), ("auto-nudge", T_NEW + 1), ("compact-suggest", T_NEW)],
                         "every refused gesture is answered on its own socket")
        for f in frames:
            self.assertIn("No space left on device", f["why"], "the frame names the fault")
            self.assertNotIn(".tmp.", f["why"], "errno + strerror only: never the temp path")
        self.assertIs(frames[0]["kept"], True, "the stored value stands: the gear snaps the box back to it")
        self.assertEqual(err.getvalue().count("write failed"), 1, "said once per fault episode, not per gesture")
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), 1, "one error-center row per episode")
        self.assertIn("auto-nudge.json", km._SDK_BOOT_PROBLEMS[0]["text"])
        km._autonudge_cache.clear()
        self.assertTrue(km._auto_nudge_on(), "nothing applied — the store keeps the old value")
        self.assertFalse(km._compact_suggest_on())
        self.assertEqual(km._set_auto_nudge(False, gt=T_NEW + 2), T_NEW + 2, "the disk heals: the next gesture lands")
        restore, err = self._fail_writes(full), io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._set_auto_nudge(True, gt=T_NEW + 3))
        finally:
            restore()
        self.assertEqual(err.getvalue().count("write failed"), 1, "a landed write ended the episode: a new fault speaks again")
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), 2)

    def test_a_direct_setter_refusal_never_rides_an_unrelated_gesture(self):
        """A setter called OUTSIDE a WS arm (a direct call: nothing pops its verdict) leaves the refused-write
        verdict on the thread. The next gt-gated gesture on that thread clears it at its own stand-down check
        (_setting_stale clears `refused` alongside `last`), so an unrelated gesture that then reaches
        _tell_stale_gesture (its own write refused under the same fault, or applied) never answers its
        socket with a frame naming a setting it did not touch (review find, 2026-09-08)."""
        self.assertEqual(km._set_auto_nudge(True, gt=T_OLD), T_OLD)
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        restore = self._fail_writes()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(km._set_auto_nudge(False, gt=T_NEW), "refused: the write failed")
                self.assertEqual((km._stale_seen.refused or {}).get("setting"), "auto-nudge",
                                 "a direct call leaves its verdict on the thread: nothing popped it")
                # an UNRELATED gesture, its own write refused under the same fault: the frame it answers, if
                # any, must be about update-mode; a leaked auto-nudge verdict would ride out here
                km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "setUpdateMode", "mode": "auto", "gt": T_NEW}, client)
                self.assertEqual([m.get("setting") for m in sent if m.get("type") == "settingStale"], [],
                                 "update-mode's refused write records no notice of its own, and auto-nudge's never rides it")
                self.assertIsNone(km._stale_seen.refused, "the unrelated gesture's check cleared the stale verdict")
                self.assertIsNone(km._set_auto_nudge(False, gt=T_NEW + 1), "refused again: a fresh verdict on the thread")
                self.assertEqual((km._stale_seen.refused or {}).get("setting"), "auto-nudge")
        finally:
            restore()
        # the disk healed: an unrelated APPLYING gesture pops nothing (the setter returned a stamp), and the
        # stale verdict must not be waiting there for whichever refusal comes next
        km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "setUpdateMode", "mode": "off", "gt": T_NEW + 2}, client)
        self.assertEqual([m for m in sent if m.get("type") == "settingStale"], [], "an APPLYING gesture answers no frame")
        self.assertIsNone(km._stale_seen.refused, "…and its check cleared the verdict left by the direct call")
        self.assertEqual(km._update_mode(), "off", "…and applied")


class StaleGestureAnswersTheDeliveringSocket(_Base):
    """A stood-down gesture used to be invisible to the dashboard that made it: one kernel stderr
    line, while the open gear kept displaying the refused pick as applied — and with the mesh
    AGREEING on the kept value, the mixed marks showed nothing. The WS branch that stands a
    gesture down now answers the DELIVERING socket with a settingStale frame (the _reply idiom
    the saveFile acks use); the gear toasts it and re-fills if open (ui/webview
    setting-stale.test.ts pins that side). Event-keyed: the frame is the deciding event — no
    polling, no timer."""

    def dispatch_rec(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        return sent

    def test_a_stood_down_judge_pick_answers_settingStale(self):
        self.dispatch_rec({"type": "setJudgeModel", "model": "fable", "gt": T_NEW})
        sent = self.dispatch_rec({"type": "setJudgeModel", "model": "opus", "gt": T_OLD})
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual(len(frames), 1, "the delivering socket hears exactly one stand-down frame")
        self.assertEqual(frames[0]["setting"], "judge-model")
        self.assertEqual(frames[0]["storedGt"], T_NEW)
        self.assertEqual(frames[0]["kept"], "fable", "the kept value rides along (cheap: one store read)")
        self.assertEqual(frames[0]["gt"], T_OLD, "the refused gesture's own stamp rides the frame — the "
                         "dashboard folds N kernels' refusals of one flush by it")

    def test_the_frame_echoes_the_refused_gesture_without_its_stamp(self):
        # the toast's Apply anyway re-issues exactly this echo with a FRESH stamp (PR #879 follow-up):
        # the refused message rides back minus its gt, so a re-issue can never reuse the stale one
        self.dispatch_rec({"type": "setJudgeModel", "model": "fable", "gt": T_NEW})
        sent = self.dispatch_rec({"type": "setJudgeModel", "model": "opus", "gt": T_OLD})
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual(frames[0]["gesture"], {"type": "setJudgeModel", "model": "opus"})
        self.assertNotIn("gt", frames[0]["gesture"], "the stale stamp is dropped on purpose")
        # a boolean toggle echoes the same way
        self.dispatch_rec({"type": "setAutoNudge", "enabled": False, "gt": T_NEW})
        sent = self.dispatch_rec({"type": "setAutoNudge", "enabled": True, "gt": T_OLD})
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual(frames[0]["gesture"], {"type": "setAutoNudge", "enabled": True})

    def test_every_gt_gated_setting_answers(self):
        cases = [({"type": "setAutoNudge", "enabled": False}, {"type": "setAutoNudge", "enabled": True},
                  "auto-nudge", False),
                 # T208 rides the same door beside Auto Nudge
                 ({"type": "setCompactSuggest", "enabled": True}, {"type": "setCompactSuggest", "enabled": False},
                  "compact-suggest", True),
                 ({"type": "setFileEditing", "enabled": True}, {"type": "setFileEditing", "enabled": False},
                  "file-editing", True),
                 ({"type": "setUpdateMode", "mode": "auto"}, {"type": "setUpdateMode", "mode": "off"},
                  "update-mode", "auto"),
                 # per-install (never propagated), but two dashboards on ONE kernel still race, so it
                 # gt-gates and answers like the rest (2026-09-01)
                 ({"type": "setThinkingSummaries", "enabled": True}, {"type": "setThinkingSummaries", "enabled": False},
                  "thinking-summaries", True),
                 # the user-todos switch (2026-09-03) is the same per-install class
                 ({"type": "setUserTodos", "enabled": True}, {"type": "setUserTodos", "enabled": False},
                  "user-todos", True),
                 ({"type": "setIndexEffort", "effort": "high"}, {"type": "setIndexEffort", "effort": "low"},
                  "index-effort", "high"),
                 ({"type": "setDistillModel", "model": "haiku"}, {"type": "setDistillModel", "model": "triage"},
                  "distill-model", "haiku"),
                 # the default-comment trio (2026-08-29) rides the same queued-class door, so it
                 # gt-gates and answers the same way
                 ({"type": "setCommentModel", "model": "haiku"}, {"type": "setCommentModel", "model": "session"},
                  "comment-model", "haiku"),
                 ({"type": "setCommentEffort", "effort": "high"}, {"type": "setCommentEffort", "effort": "session"},
                  "comment-effort", "high"),
                 ({"type": "setCommentFast", "fast": "on"}, {"type": "setCommentFast", "fast": "session"},
                  "comment-fast", "on")]
        for newer, older, store, kept in cases:
            sent = self.dispatch_rec(dict(newer, gt=T_NEW))
            self.assertEqual([m for m in sent if m.get("type") == "settingStale"], [],
                             "an APPLIED gesture sends no frame (%s)" % store)
            sent = self.dispatch_rec(dict(older, gt=T_OLD))
            frames = [m for m in sent if m.get("type") == "settingStale"]
            self.assertEqual(len(frames), 1, store)
            self.assertEqual(frames[0]["setting"], store)
            self.assertEqual(frames[0]["storedGt"], T_NEW)
            self.assertEqual(frames[0]["gt"], T_OLD, store)
            self.assertEqual(frames[0]["kept"], kept, store)
            self.assertEqual(frames[0]["gesture"], older, "the refused message echoes back minus its gt (%s)" % store)

    def test_no_frame_for_invalid_or_unstamped(self):
        sent = self.dispatch_rec({"type": "setJudgeModel", "model": "gpt-99", "gt": T_NEW})
        self.assertEqual(sent, [], "a refused VALUE is not a stale gesture — no frame")
        sent = self.dispatch_rec({"type": "setJudgeModel", "model": "haiku"})
        self.assertEqual(sent, [], "the unstamped compat path applies — no frame")


class VersionReportsEveryStoredStamp(_Base):
    """/version carries `settingsGt`: every gt-gated store's last-applied stamp, keyed by the store
    name the settingStale frame uses (PR #879 follow-up). The gear reads /version on every open and
    stamps its next gesture at max(Date.now(), gt + 1) — the maintainer's proposed shape — instead of
    trusting the device's wall clock. Integers only: the route is auth-exempt."""

    def test_a_fresh_install_reports_every_store_at_zero(self):
        gts = km._version_info()["settingsGt"]
        self.assertEqual(set(gts), set(km._GT_STORES), "one key per gt-gated store, no more, no less")
        self.assertEqual(len(km._GT_STORES), 20, "six toggles/modes + fourteen kernel-side stores (judge-concurrency since T277, tmux-backend since T288, judge-fast with the judges' fast mode, distill-fast and index-fast with T300's box per tier)")
        self.assertEqual(set(gts.values()), {0}, "nothing applied yet reads 0 — nothing to outrank")
        self.assertEqual(json.loads(json.dumps(gts)), gts, "plain JSON — ints, no paths, nothing to redact")

    def test_each_setter_s_stamp_is_reported_under_its_store_and_nothing_else_moves(self):
        self.assertEqual(km._set_judge_model("fable", gt=T_NEW), T_NEW)
        self.assertEqual(km._set_auto_nudge(False, gt=T_OLD), T_OLD)
        self.assertEqual(km._set_compact_suggest(True, gt=T_NEW + 5), T_NEW + 5)
        self.assertEqual(km._set_file_editing(True, gt=T_NEW + 6), T_NEW + 6)
        self.assertEqual(km._set_update_mode("auto", gt=T_NEW + 7), T_NEW + 7)
        self.assertEqual(km._set_thinking_summaries(True, gt=T_NEW + 8), T_NEW + 8)
        self.assertEqual(km._set_comment_fast("on", gt=T_NEW + 9), T_NEW + 9)
        self.assertEqual(km._set_judge_fast("on", gt=T_NEW + 10), T_NEW + 10)
        self.assertEqual(km._set_distill_fast("on", gt=T_NEW + 11), T_NEW + 11)
        self.assertEqual(km._set_index_fast("on", gt=T_NEW + 12), T_NEW + 12)
        self.assertEqual(km._set_user_todos(True, gt=T_NEW + 13), T_NEW + 13)
        gts = km._version_info()["settingsGt"]
        want = {"judge-model": T_NEW, "auto-nudge": T_OLD, "compact-suggest": T_NEW + 5,
                "file-editing": T_NEW + 6, "update-mode": T_NEW + 7, "thinking-summaries": T_NEW + 8,
                "comment-fast": T_NEW + 9, "judge-fast": T_NEW + 10, "distill-fast": T_NEW + 11, "index-fast": T_NEW + 12,
                "user-todos": T_NEW + 13}
        for store, gt in want.items():
            self.assertEqual(gts[store], gt, store)
        for store in set(km._GT_STORES) - set(want):
            self.assertEqual(gts[store], 0, "%s was never set and stays 0" % store)
        # the two checkboxes that share auto-nudge.json keep separate clocks in the report too
        self.assertNotEqual(gts["auto-nudge"], gts["compact-suggest"])

    def test_an_unstamped_apply_reports_its_arrival_time(self):
        before = int(time.time() * 1000)
        km._set_index_effort("high")
        got = km._version_info()["settingsGt"]["index-effort"]
        self.assertGreaterEqual(got, before, "the compat path's arrival stamp is what the store holds")

    def test_the_frames_setting_names_are_exactly_the_report_s_keys(self):
        # the completeness cross-check: every store a settingStale frame can name is reported, so the
        # gear's clock (which learns from BOTH) has one vocabulary
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        newer = [{"type": "setAutoNudge", "enabled": False}, {"type": "setCompactSuggest", "enabled": True},
                 {"type": "setFileEditing", "enabled": True}, {"type": "setUpdateMode", "mode": "auto"},
                 {"type": "setThinkingSummaries", "enabled": True}, {"type": "setUserTodos", "enabled": True},
                 {"type": "setJudgeModel", "model": "fable"},
                 {"type": "setIndexModel", "model": "fable"}, {"type": "setJudgeEffort", "effort": "high"},
                 {"type": "setIndexEffort", "effort": "high"}, {"type": "setJudgeConcurrency", "value": "4"},
                 {"type": "setDistillModel", "model": "haiku"},
                 {"type": "setDistillEffort", "effort": "high"}, {"type": "setCommentModel", "model": "haiku"},
                 {"type": "setCommentEffort", "effort": "high"}, {"type": "setCommentFast", "fast": "on"},
                 {"type": "setTmuxBackend", "enabled": True}, {"type": "setJudgeFast", "enabled": True},
                 {"type": "setDistillFast", "enabled": True}, {"type": "setIndexFast", "enabled": True}]
        older = [{"type": "setAutoNudge", "enabled": True}, {"type": "setCompactSuggest", "enabled": False},
                 {"type": "setFileEditing", "enabled": False}, {"type": "setUpdateMode", "mode": "off"},
                 {"type": "setThinkingSummaries", "enabled": False}, {"type": "setUserTodos", "enabled": False},
                 {"type": "setJudgeModel", "model": "opus"},
                 {"type": "setIndexModel", "model": "opus"}, {"type": "setJudgeEffort", "effort": "low"},
                 {"type": "setIndexEffort", "effort": "low"}, {"type": "setJudgeConcurrency", "value": "2"},
                 {"type": "setDistillModel", "model": "triage"},
                 {"type": "setDistillEffort", "effort": "low"}, {"type": "setCommentModel", "model": "session"},
                 {"type": "setCommentEffort", "effort": "session"}, {"type": "setCommentFast", "fast": "session"},
                 {"type": "setTmuxBackend", "enabled": False}, {"type": "setJudgeFast", "enabled": False},
                 {"type": "setDistillFast", "enabled": False}, {"type": "setIndexFast", "enabled": False}]
        with contextlib.redirect_stderr(io.StringIO()):
            for n, o in zip(newer, older):
                km.Handler._dispatch_ws(types.SimpleNamespace(), dict(n, gt=T_NEW), client)
                km.Handler._dispatch_ws(types.SimpleNamespace(), dict(o, gt=T_OLD), client)
        named = {m["setting"] for m in sent if m.get("type") == "settingStale"}
        self.assertEqual(named, set(km._version_info()["settingsGt"]), "frames and the report share one vocabulary")
        self.assertEqual(len(named), 20)   # six toggles/modes + fourteen kernel-side stores since T300's box per judge tier


class ASkewedClockCannotLockTheStore(_Base):
    """The maintainer's scenario on #879: a device whose clock runs ahead stamps a store into the
    future, and every correctly-clocked device is refused until the skew elapses — no gesture from
    them can win. With the frame echoing the refused gesture and reporting the stamp it lost to, the
    dashboard re-issues the same pick stamped max(now, storedGt + 1): applied, propagated with that
    stamp, no frame. The re-issue is a new user gesture — new information, not a clock heuristic."""

    def dispatch_rec(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        return sent

    def test_the_echoed_gesture_re_issued_above_the_stored_stamp_wins(self):
        self.dispatch_rec({"type": "setJudgeModel", "model": "fable", "gt": T_NEW})   # the fast clock's pick
        self.propagated.clear()
        sent = self.dispatch_rec({"type": "setJudgeModel", "model": "opus", "gt": T_OLD})   # the honest clock's, refused
        frame = [m for m in sent if m.get("type") == "settingStale"][0]
        self.assertEqual(frame["storedGt"], T_NEW)
        self.assertEqual(km.jd._state_str("judge-model", ""), "fable", "refused: the store still holds the future stamp's pick")
        self.assertEqual(self.propagated, [], "a refusal fans nothing out")
        # what the toast's Apply anyway sends: the echo plus max(Date.now(), storedGt + 1) — the
        # device's clock says T_OLD, so the learned stamp wins
        retry = dict(frame["gesture"], gt=max(T_OLD, frame["storedGt"] + 1))
        sent = self.dispatch_rec(retry)
        self.assertEqual([m for m in sent if m.get("type") == "settingStale"], [], "the re-issue is not refused")
        self.assertEqual(km.jd._state_str("judge-model", ""), "opus", "the correctly-clocked device's pick applied")
        self.assertEqual(km._judge_state_gt("judge-model"), T_NEW + 1, "the store holds the re-issue's stamp")
        self.assertEqual(self.propagated[-1], {"judgeModel": "opus", "gt": T_NEW + 1},
                         "…and the fan-out carries it, so every receiver orders the same way")
        self.assertEqual(km._version_info()["settingsGt"]["judge-model"], T_NEW + 1)

    def test_a_toggle_climbs_the_same_way(self):
        self.dispatch_rec({"type": "setFileEditing", "enabled": False, "gt": T_NEW})
        sent = self.dispatch_rec({"type": "setFileEditing", "enabled": True, "gt": T_OLD})
        frame = [m for m in sent if m.get("type") == "settingStale"][0]
        self.assertFalse(km._file_editing_on())
        sent = self.dispatch_rec(dict(frame["gesture"], gt=max(T_OLD, frame["storedGt"] + 1)))
        self.assertEqual(sent, [])
        self.assertTrue(km._file_editing_on(), "the re-issued consent applied")
        self.assertEqual(km._setting_stored_gt("file-editing"), T_NEW + 1)


class WiringPins(unittest.TestCase):
    """Source pins on the non-judge WS branches (the judge branches' propagation args are
    pinned in test_judge_settings_sync.py): each must hand the message's stamp to its setter —
    a branch that drops it reopens the unconditional-apply hole for that setting."""

    def setUp(self):
        import inspect
        self.src = inspect.getsource(km.Handler._dispatch_ws)

    def test_auto_nudge_branch_gates_on_the_stamp_and_skips_the_tick_on_stand_down(self):
        self.assertIn('_set_auto_nudge(enabled, gt=_gesture_ms(msg)) is not None', self.src,
                      "a stood-down toggle must not fire the nudge tick either — no new information")

    def test_file_editing_branch_passes_the_stamp(self):
        self.assertIn('_set_file_editing(enabled, gt=_gesture_ms(msg))', self.src)

    def test_compact_suggest_branch_gates_on_the_stamp_and_skips_the_tick_on_stand_down(self):
        # T208's WS branch mirrors setAutoNudge's: gt-gated, immediate tick only on a real apply
        self.assertIn('_set_compact_suggest(enabled, gt=_gesture_ms(msg)) is not None', self.src)

    def test_update_mode_branch_passes_the_stamp(self):
        self.assertIn('_set_update_mode(str(msg["mode"]), gt=_gesture_ms(msg))', self.src)

    def test_every_stood_down_branch_tells_the_delivering_socket(self):
        self.assertGreaterEqual(self.src.count("_tell_stale_gesture(client, msg)"), 14,
                                "all fourteen gt-gated branches (five toggles + nine judge tiers) "
                                "answer the delivering socket on a stand-down, handing over the "
                                "refused message so the frame can echo it")
        self.assertNotIn("_tell_stale_gesture(client)\n", self.src,
                         "no branch still calls the echo-less form — the toast's Apply anyway "
                         "re-issues what the frame echoes")

    def test_the_docstring_names_all_three_none_causes(self):
        doc = km._set_judge_state.__doc__ or ""
        for needle in ("invalid", "stale", "OSError"):
            self.assertIn(needle, doc, "None has exactly three named causes")
        self.assertIn("propagat", doc, "the no-propagate-on-OSError behavior is documented, with its why")

    def test_the_toggle_docstrings_name_the_write_failure_stand_down(self):
        # the same contract _set_judge_state documents: None's write-failure cause is named, so a
        # caller reading the docstring knows a full disk stands the gesture down rather than raising
        for fn in (km._set_auto_nudge, km._set_compact_suggest, km._set_file_editing,
                   km._set_thinking_summaries):
            self.assertIn("OSError", fn.__doc__ or "",
                          "%s documents the write-failure cause of None" % fn.__name__)

    def test_both_judge_files_publish_atomically_sidecar_first(self):
        import inspect
        src = inspect.getsource(km._set_judge_state)
        self.assertIn('_atomic_write(jd.STATE / (fname + ".gt")', src, "the sidecar publishes via _atomic_write")
        self.assertIn("_atomic_write(jd.STATE / fname,", src, "the value publishes via _atomic_write")
        self.assertNotIn(".write_text(", src, "no raw write_text — a torn write must not tear the store")
        self.assertLess(src.index('fname + ".gt"'), src.index("_atomic_write(jd.STATE / fname,"),
                        "the sidecar publishes FIRST — a crash between the two errs toward stand-down")


class AutoNudgeTurnOnActsNow(_Base):
    """Turning Auto Nudge on acts in the same handler call instead of waiting for the pusher's
    next pass — and ONLY a real apply does: a stale stamp and the gesture's own echo carry no
    new information, so neither fires a tick (T208's setCompactSuggest arm has the same shape).
    #846 inserted that arm between the auto-nudge setter and its act-now tick, which moved the
    tick onto the new arm without changing the tick line — a source pin alone would not have
    noticed, so the handler is driven here."""

    def setUp(self):
        super().setUp()
        self.ticks = []
        km._auto_nudge_tick = lambda *a, **k: self.ticks.append((a, k))   # restored by _Base.tearDown

    def test_a_real_turn_on_ticks_once_without_the_dead_wait_sweep(self):
        self.dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_NEW})
        self.assertEqual(len(self.ticks), 1, "a real apply acts now")
        (now, tmux), kw = self.ticks[0]
        self.assertIsInstance(now, int)
        self.assertEqual(kw, {"run_dead_wait": False}, "the WS tick never runs the one-observer sweep")
        self.assertEqual(tmux, {}, "the tick takes the listing the handler fetched (_tmux_sessions)")

    def test_a_stale_stamp_and_an_echo_fire_no_tick(self):
        self.dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_NEW})
        self.dispatch({"type": "setAutoNudge", "enabled": False, "gt": T_OLD})   # stale: stands down
        self.dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_NEW})    # echo: the same pick again
        self.assertEqual(len(self.ticks), 1, "only the applied gesture ticked")

    def test_an_unstamped_toggle_applies_and_ticks(self):
        self.dispatch({"type": "setAutoNudge", "enabled": True})                 # older dashboard, no gt
        self.assertEqual(len(self.ticks), 1)

    def test_a_real_turn_off_applies_and_fires_no_tick(self):
        # turning OFF is a real apply too, with nothing to act on: the tick is a no-op when off, so
        # firing it would only fork the session listing on the WS thread for nothing
        self.dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_OLD})
        self.dispatch({"type": "setAutoNudge", "enabled": False, "gt": T_NEW})
        self.assertFalse(km._auto_nudge_on(), "the turn-off applied")
        self.assertEqual(len(self.ticks), 1, "only the turn-on ticked")


class WsActNowTickIsWrapped(_Base):
    """The act-now tick runs on the WS handler thread, and the reader loop (Handler._ws) re-raises
    OSError as a socket failure whose outer handler tears the connection down silently — so an
    OSError out of the pass head (the session-listing fork, a store read) closed the dashboard's
    socket with no log line: since #846 for setCompactSuggest, for setAutoNudge once #943 restored
    its tick. Both arms now tick through one wrap (_ws_act_now_tick) that logs the traceback the way
    the pusher's own wrap does. The tick never writes to the delivering socket (its only push is
    _push_soon(), a wake flag), which is what makes catching everything there safe: nothing caught
    is that socket's own failure. Driven through the real dispatch, the incident's shape."""

    ARMS = ({"type": "setAutoNudge", "enabled": True, "gt": T_NEW},
            {"type": "setCompactSuggest", "enabled": True, "gt": T_NEW})

    def _dispatch_with_failing_tick(self, exc):
        def boom(*a, **k):
            raise exc
        km._auto_nudge_tick = boom                                # restored by _Base.tearDown
        out = []
        for msg in self.ARMS:
            client = {"send": lambda s: None, "alive": True}
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                try:
                    km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
                except OSError:
                    self.fail("an OSError escaped _dispatch_ws (%s) — the reader loop re-raises it as a "
                              "socket failure and silently tears the WebSocket down" % msg["type"])
            self.assertTrue(client["alive"], "%s: the delivering client survives the failed tick" % msg["type"])
            out.append(err.getvalue())
        return out

    def test_a_failing_act_now_tick_is_logged_not_a_socket_failure(self):
        errs = self._dispatch_with_failing_tick(OSError(28, "No space left on device"))
        for msg, err in zip(self.ARMS, errs):
            self.assertIn("auto-nudge", err, "%s: the failure is loud and names the tick" % msg["type"])
            self.assertIn("OSError", err, "%s: the traceback is logged, as the pusher's wrap logs it" % msg["type"])
            self.assertIn("No space left on device", err)
        km._autonudge_cache.clear()
        self.assertTrue(km._auto_nudge_on(), "the flag write preceded the tick and stands")
        self.assertTrue(km._compact_suggest_on(), "…for both arms")

    def test_any_failure_of_the_tick_is_caught_the_same_way(self):
        # the pusher's wrap catches Exception; so does this one — a TypeError in the pass head is no
        # more a socket failure than an OSError is
        errs = self._dispatch_with_failing_tick(RuntimeError("a bad store record"))
        for err in errs:
            self.assertIn("auto-nudge", err)
            self.assertIn("RuntimeError: a bad store record", err)


SF_SID = "11111111-2222-4333-8444-555555555555"   # the single-flight tests' one fake alive session


class AutoNudgeTickIsSingleFlight(_Base):
    """_auto_nudge_tick has two concurrent entry points — the pusher's periodic pass (0.5 s
    backstop) and the setAutoNudge arm's act-now pass on the WS handler thread — and the nudge
    send has no claim-before-send (_compact_suggest_tick's latch covers only its own seam), so
    two passes that overlap each derive the same due goal and inject the same nudge twice into
    one session. The tick is single-flight (_AUTO_NUDGE_TICK_LOCK): a pass that finds another in
    flight stands down whole, and loses nothing — the flag write precedes the turn-on's tick, so
    a pass found in flight already reads the turned-on world, and the pusher re-runs on its own
    cadence. Driven with the REAL tick; the per-session walk (which owns the send) is a recorder
    that holds the first pass mid-send until the overlapping entry has been and gone, so the
    interleave is deterministic, not a sleep."""

    def setUp(self):
        super().setUp()
        km._auto_nudge_tick = self._saved["_auto_nudge_tick"]      # the real tick is the subject here
        self._also = {n: getattr(km, n) for n in
                      ("_alive_sessions", "_wait_for_graph", "_auto_nudge_session", "_compact_suggest_tick",
                       "_debt_backstop_tick", "_dead_wait_sweep", "_awaiting_wake_outcomes", "_push_soon")}
        self.sends = []                                            # the `now` of every pass that reached the send
        self.entered, self.release = _real_threading.Event(), _real_threading.Event()
        km._alive_sessions = lambda now, tmux: [{"sid": SF_SID, "path": "/nonexistent.jsonl"}]
        km._wait_for_graph = lambda now, alive_ids: {}
        km._auto_nudge_session = self._walk
        km._compact_suggest_tick = lambda sid, tm, now: False
        km._debt_backstop_tick = lambda now: None
        km._dead_wait_sweep = lambda alive_ids, nudged, now: None
        km._awaiting_wake_outcomes = lambda now, alive_ids: False
        km._push_soon = lambda: None

    def tearDown(self):
        self.release.set()                                         # never leave a held pass behind
        for n, v in self._also.items():
            setattr(km, n, v)
        super().tearDown()

    def _walk(self, s, now, tmux, nudged, waitfor, alive_ids=None, wake_only=False, cleared=None):   # #936 adds the kwarg (toggle off → wake-only walk); T267d hands the pass's clear set
        """The per-session walk standing in for the SEND: the first pass to reach it holds here,
        mid-send, until the test releases it; every later pass records and returns at once."""
        self.sends.append(now)
        if len(self.sends) == 1:
            self.entered.set()
            self.release.wait(5)
        return True

    def _pass_in_flight(self, now, **kw):
        t = _real_threading.Thread(target=km._auto_nudge_tick, args=(now, {}), kwargs=kw, daemon=True)
        t.start()
        self.assertTrue(self.entered.wait(5), "the first pass reached its send")
        return t

    def test_the_ws_arm_meeting_the_pusher_mid_pass_sends_nothing_more(self):
        t = self._pass_in_flight(1000)                                          # the pusher's shape
        self.dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_NEW})   # a real apply: its act-now tick
        self.release.set()
        t.join(5)
        self.assertEqual(self.sends, [1000], "one nudge, from the pass already in flight")

    def test_the_pusher_meeting_the_ws_pass_mid_pass_stands_down_and_runs_next_cycle(self):
        t = self._pass_in_flight(1000, run_dead_wait=False)                     # the WS arm's shape
        km._auto_nudge_tick(1001, {})                                           # the pusher's pass: stands down
        self.release.set()
        t.join(5)
        self.assertEqual(self.sends, [1000], "the overlapping pass sent nothing")
        km._auto_nudge_tick(1002, {})                                           # its next cycle
        self.assertEqual(self.sends, [1000, 1002], "a stood-down pass is retried next cycle, never lost")

    def test_a_pass_that_raises_releases_the_guard(self):
        self.release.set()                                                      # nothing holds in this test
        km._alive_sessions = lambda now, tmux: 1 / 0
        with self.assertRaises(ZeroDivisionError):                              # propagates as before (the
            km._auto_nudge_tick(1000, {})                                       # pusher loop catches it)
        km._alive_sessions = lambda now, tmux: [{"sid": SF_SID, "path": "/nonexistent.jsonl"}]
        km._auto_nudge_tick(1001, {})
        self.assertEqual(self.sends, [1001], "the guard is released on every exit, a raise included")


class ThinkingSummariesSetting(_Base):
    """The thinking-summaries toggle (2026-09-01): a PER-INSTALL kernel setting — its own json under
    STATE, off until this install says yes, never propagated (not in federation's KERNEL_SETTING
    set) — that still rides the gesture-ordered door: two dashboards on one kernel can race, so a
    stale stamp stands down loudly and the delivering socket hears settingStale, exactly like
    _set_file_editing. The SDK backend reads the same file at every connect (_options), so the
    store here IS the switch the sessions see."""

    FILE = "thinking-summaries.json"

    def test_absent_file_reads_off_and_never_writes(self):
        self.assertFalse(km._thinking_summaries_on(), "a fresh install is OFF — shipping never turns it on")
        self.assertFalse((km.jd.STATE / self.FILE).exists(), "reading must not create the file")

    def test_set_persists_value_and_stamp_in_its_own_file(self):
        self.assertEqual(km._set_thinking_summaries(True, gt=T_NEW), T_NEW)
        d = json.loads((km.jd.STATE / self.FILE).read_text())
        self.assertEqual((d["enabled"], d["gt"]), (True, T_NEW))
        self.assertTrue(km._thinking_summaries_on())
        self.assertEqual(km._set_thinking_summaries(False, gt=T_NEW + 1), T_NEW + 1)
        self.assertFalse(km._thinking_summaries_on(), "OFF is a real value, not the absent default")

    def test_a_stale_gesture_stands_down_loudly(self):
        self.assertEqual(km._set_thinking_summaries(True, gt=T_NEW), T_NEW)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_thinking_summaries(False, gt=T_OLD), "older stamp must not apply")
        self.assertTrue(km._thinking_summaries_on(), "the newer ON survives the stale OFF")
        for needle in ("thinking-summaries", str(T_OLD), str(T_NEW), "stood down"):
            self.assertIn(needle, err.getvalue(), "the stand-down names the setting and both stamps")

    def test_an_equal_stamp_with_the_same_value_is_a_silent_echo(self):
        # the same rule the other gt-gated setters follow: one gesture delivered twice with one stamp
        # (a re-dialed socket flushing what already landed) applies nothing and says nothing — no
        # stand-down line, no settingStale notice — while an equal stamp carrying a DIFFERENT value
        # still stands down loudly
        self.assertEqual(km._set_thinking_summaries(True, gt=T_NEW), T_NEW)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_thinking_summaries(True, gt=T_NEW), "nothing to apply")
        self.assertEqual(err.getvalue(), "", "the echo is silent")
        self.assertIsNone(km._pop_stale_notice(), "…and leaves no settingStale notice for the socket")
        self.assertTrue(km._thinking_summaries_on())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._set_thinking_summaries(False, gt=T_NEW))
        self.assertIn("stale gesture stood down", err.getvalue(), "a differing equal-stamp gesture is still loud")
        self.assertIsNotNone(km._pop_stale_notice())
        self.assertTrue(km._thinking_summaries_on(), "the stored ON survives the equal-stamp OFF")

    def test_a_file_without_the_field_reads_as_zero(self):
        (km.jd.STATE / self.FILE).write_text(json.dumps({"enabled": True}))
        self.assertEqual(km._set_thinking_summaries(False, gt=1), 1, "gt 1 beats the absent field's 0")
        self.assertFalse(km._thinking_summaries_on())

    def test_the_dispatch_branch_hands_the_stamp_over_and_answers_the_socket(self):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "setThinkingSummaries", "enabled": True,
                                                              "gt": T_NEW}, client)
            self.assertEqual([m for m in sent if m.get("type") == "settingStale"], [], "an applied gesture: no frame")
            km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "setThinkingSummaries", "enabled": False,
                                                              "gt": T_OLD}, client)
        self.assertTrue(km._thinking_summaries_on(), "the store keeps the newer pick")
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual(len(frames), 1)
        self.assertEqual((frames[0]["setting"], frames[0]["storedGt"], frames[0]["kept"]),
                         ("thinking-summaries", T_NEW, True))
        self.assertEqual(self.propagated, [], "per-install: a thinking-summaries pick never fans out")

    def test_write_failure_is_loud_none_and_applies_nothing(self):
        self.assertEqual(km._set_thinking_summaries(True, gt=T_OLD), T_OLD)
        import pathlib
        orig = pathlib.Path.write_text

        def failing(p, *a, **k):
            raise OSError("simulated full disk")

        pathlib.Path.write_text = failing
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._set_thinking_summaries(False, gt=T_NEW), "a failed write never raises")
        finally:
            pathlib.Path.write_text = orig
        self.assertIn("thinking-summaries", err.getvalue(), "the refusal names the setting on stderr")
        self.assertTrue(km._thinking_summaries_on(), "nothing applied — the store keeps the old value")
        self.assertIn("OSError", km._set_thinking_summaries.__doc__ or "",
                      "the docstring names the write-failure cause of None, like its siblings")

    def test_the_setting_is_reported_but_not_broadcast(self):
        import inspect
        src = inspect.getsource(km.Handler._dispatch_ws)
        self.assertIn('_set_thinking_summaries(enabled, gt=_gesture_ms(msg))', src,
                      "the WS branch hands the gesture stamp to the store")
        fed = Path(BIN).parent / "ui" / "webview" / "federation.ts"
        self.assertNotIn("setThinkingSummaries", fed.read_text(),
                         "per-install: NOT a KERNEL_SETTING — it must never queue for or reach another kernel")
        km._set_thinking_summaries(True, gt=T_NEW)
        self.assertIs(km._version_info()["thinkingSummaries"], True, "/version reports it so the gear fills")


class RefusedToggleTellsTheDeliveringSocket(_Base):
    """A toggle whose ledger read was UNPROVED (auto-nudge.json unreadable; 2026-09-07) is refused at the
    writer rather than persisting a copy the reader could not vouch for — and the refusal must reach the
    socket that made the gesture. Silent, it was the defect's other face: gear.js flips the checkbox
    locally and re-fills only on reopen, so the box read OFF while the kernel kept ON, and when the file
    healed ON is what ran. The setter records a `refused` notice (a sibling of _setting_stale's stand-down,
    claiming nothing about ordering) and the WS branch answers with the settingStale frame plus `why`: the
    gear's copy — not applied, keeping the stored value — is exactly true, and its re-fill snaps the box
    back; the fault itself goes to the error center."""

    def setUp(self):
        super().setUp()
        for reg in ("_ledger_fault_warned", "_ledger_refusal_warned", "_ledger_write_failed"):
            vars(km).get(reg, {}).clear()                  # absent on a kernel before the fix: fail on the defect
        self.ledger = km.jd.STATE / "auto-nudge.json"
        self.problems = len(km._SDK_BOOT_PROBLEMS)
        self.ticks = []
        km._auto_nudge_tick = lambda *a, **k: self.ticks.append(a)
        self._real_read = Path.read_text

    def tearDown(self):
        Path.read_text = self._real_read
        super().tearDown()

    def _fault(self):
        real, target = Path.read_text, str(self.ledger)

        def failing(p, *a, **k):
            if str(p) == target:
                raise OSError(errno.EIO, "Input/output error")
            return real(p, *a, **k)
        Path.read_text = failing

    def _dispatch(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        return sent, client

    def test_a_toggle_under_a_read_fault_is_refused_and_the_socket_hears_it(self):
        self.assertEqual(km._set_auto_nudge(False, gt=T_OLD), T_OLD)   # OFF on disk
        km._autonudge_cache.clear()
        km._auto_nudge_data()                                          # a proved read: OFF is the last proved snapshot
        self.ledger.write_text(json.dumps(json.loads(self.ledger.read_bytes()), indent=1))   # the file moves on…
        before = self.ledger.read_bytes()
        self._fault()                                                  # …and the new bytes cannot be read
        sent, client = self._dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_NEW})
        Path.read_text = self._real_read
        self.assertEqual(self.ledger.read_bytes(), before, "the file keeps its bytes: nothing rewritten from a copy the reader could not vouch for")
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual(len(frames), 1, "the delivering socket hears the refusal: the gear re-fills and says not applied")
        f = frames[0]
        self.assertEqual(f["setting"], "auto-nudge")
        self.assertIs(f["kept"], False, "the kept value is the last snapshot this process proved — OFF — never the fabricated ON")
        self.assertIn("Input/output error", f["why"], "…and the frame names the fault")
        self.assertEqual(f["gt"], T_NEW)
        self.assertEqual(f["storedGt"], T_OLD)
        self.assertEqual(f["gesture"], {"type": "setAutoNudge", "enabled": True})
        self.assertEqual(self.ticks, [], "a refused toggle fires no act-now tick")
        self.assertTrue(client["alive"])
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), self.problems + 1, "the fault is on record in the error center")
        self.assertIn("on was not applied", km._SDK_BOOT_PROBLEMS[-1]["text"])
        # healed, the same gesture applies and the socket hears nothing
        sent, _c = self._dispatch({"type": "setAutoNudge", "enabled": True, "gt": T_NEW})
        self.assertEqual([m for m in sent if m.get("type") == "settingStale"], [])
        km._autonudge_cache.clear()
        self.assertTrue(km._auto_nudge_on())
        self.assertEqual(len(self.ticks), 1, "…and the turn-on acts at once, as ever")

    def test_the_kept_value_includes_this_kernel_s_own_last_write(self):
        # a proved WRITE is a proved state too: after it, and before any read, a refusal must name the
        # written value — not the value of the last proved read (the writer publishes and refreshes the
        # cache with what it read back under the file's stat key)
        self.assertEqual(km._set_auto_nudge(False, gt=T_OLD), T_OLD)
        km._autonudge_cache.clear()
        self.assertFalse(km._auto_nudge_data()["enabled"])            # the last proved READ says OFF
        self.assertEqual(km._set_auto_nudge(True, gt=T_OLD + 1), T_OLD + 1)   # this kernel writes ON; no read since
        self.ledger.write_text(json.dumps(json.loads(self.ledger.read_bytes()), indent=1))   # the file moves on…
        self._fault()                                                  # …and the new bytes cannot be read
        sent, _c = self._dispatch({"type": "setAutoNudge", "enabled": False, "gt": T_NEW})
        Path.read_text = self._real_read
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual(len(frames), 1)
        self.assertIs(frames[0]["kept"], True, "the kept value is what this kernel last proved — its own write, ON")
        self.assertIn("off was not applied", km._SDK_BOOT_PROBLEMS[-1]["text"])

    def test_with_no_proved_snapshot_the_frame_names_no_kept_value(self):
        # a fault before this kernel ever proved a read (boot): the copy readers get is the DEFAULT, so
        # the frame must not present it as the kept value — the gear then drops its "Keeping …" clause
        self.assertEqual(km._set_compact_suggest(True, gt=T_OLD), T_OLD)
        km._autonudge_cache.clear()
        before = self.ledger.read_bytes()
        self._fault()
        sent, _c = self._dispatch({"type": "setCompactSuggest", "enabled": False, "gt": T_NEW})
        Path.read_text = self._real_read
        self.assertEqual(self.ledger.read_bytes(), before)
        frames = [m for m in sent if m.get("type") == "settingStale"]
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["setting"], "compact-suggest")
        self.assertIsNone(frames[0]["kept"], "unknown is unknown: no fabricated kept value")
        self.assertIn("Input/output error", frames[0]["why"])
        self.assertEqual(self.ticks, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
