#!/usr/bin/env python3
"""Retry upgrades after downgrades (the user 2026-09-17): a kernel-side switch (Settings, Automation, Model; STATE/retry-upgrade)
under which a session whose model fell to a lower tier without a pick asks for its pick again every RETRY_UPGRADE_S at a
quiet moment — a reconnect (want_switch_reconnect: now if nothing runs, else at the event that ends the last of it) whose connect re-asserts the pick —
until a parent turn is SERVED on the pick's tier again (the AssistantMessage learn, never the init's report or a context
refresh), which mints the "back on" card through the kernel-wired hook; a pick of the user's own, the session's end or the
switch going off end it too. A fallback that happens again while armed is logged, not carded again. The fallback's cause
is outside romp's view (a trigger in the task's context that ages out of the window), so the cadence is the designed read;
the boundary is the event. Hermetic state, synthetic sids, no CLI."""
import inspect
import os
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-000000000711"


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    return be


def _sess(be, **reg):
    r = {"sid": SID, "name": "web", "cwd": "/tmp", "model": "fable", "liveModel": "Fable 5.1", "liveModelId": "claude-fable-5-1"}
    r.update(reg)
    sb.write_reg(be.state_dir, r["sid"], dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    s.request_reconnect = lambda *a, **k: s._asks.append(a)
    s._asks = []
    be.sessions[r["sid"]] = s
    return s


class _Hooks:
    """The kernel's two class-level hooks, wired for a test and unwired after (the class is shared by every test)."""
    def __init__(self):
        self.fallbacks, self.restored = [], []

    def __enter__(self):
        sb.SdkBackend.on_model_fallback = staticmethod(lambda sid, frm, to: (self.fallbacks.append((frm, to)), "%s:g%d" % (sid, len(self.fallbacks)))[1])
        sb.SdkBackend.on_model_restored = staticmethod(lambda sid, frm, to: self.restored.append((frm, to)))
        return self

    def __exit__(self, *a):
        del sb.SdkBackend.on_model_fallback
        del sb.SdkBackend.on_model_restored


class Arming(unittest.TestCase):
    def test_a_downgrade_arms_the_retry_only_with_the_switch_on(self):
        be = _backend()
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            self.assertEqual(h.fallbacks, [("Fable 5.1", "Opus 5")], "the card, as before")
            self.assertIsNone(s._upgrade_retry, "switch off: nothing armed")
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            s = _sess(be)
            t0 = time.time()
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm)
            self.assertEqual((arm["from"], arm["to"], arm["pick"], arm["attempts"]), ("Fable 5.1", "Opus 5", "fable", 0))
            self.assertGreaterEqual(arm["next"], t0 + sb.RETRY_UPGRADE_S - 1, "the first attempt waits a full cadence")
            self.assertTrue(any(m.startswith("retry upgrade (web): Fable 5.1 fell back to Opus 5") for m in be._logs), be._logs)

    def test_a_fallback_while_armed_asks_the_store_again_and_is_logged_once_per_attempt(self):
        # review 2026-09-17: the card is the store's call (mint_fallback_card's existence-keyed dedupe mints nothing
        # while the swap's card stands, a fresh one once the user cleared it); the log says it once per attempt
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)        # the fallback: card + arm
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")               # a connect reports the pick (the init: not served)
            self.assertIsNotNone(s._upgrade_retry, "the CLI running on the pick is not the API serving it")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)        # …and the API falls back again, before any attempt
            self.assertEqual(len(h.fallbacks), 2, "the hook is asked each time; the store dedupes")
            self.assertEqual([m for m in be._logs if "fell back to Opus 5 again" in m], [], "no attempt yet: nothing to report")
            be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1)     # attempt 1
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            again = [m for m in be._logs if "fell back to Opus 5 again after attempt 1" in m]
            self.assertEqual(len(again), 1, "said once for attempt 1, with the wait to the next: %r" % be._logs)
            self.assertIn("next attempt in", again[0])
            self.assertIsNotNone(s._upgrade_retry, "still armed")


class TheTick(unittest.TestCase):
    def test_a_due_session_is_asked_once_per_cadence_by_a_deferred_reconnect(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            now = time.time()
            self.assertEqual(be.retry_model_upgrades(now), 0, "not due yet")
            self.assertEqual(s._asks, [])
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "due: one ask")
            self.assertEqual(len(s._asks), 1, "the session is quiet: one immediate reconnect (defer=False)")
            self.assertEqual(s._asks[0], (), "…through request_reconnect(defer=False), a keyword the stub drops")
            self.assertEqual(s._upgrade_retry["attempts"], 1)
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 2), 0, "…and not again until the next cadence")
            self.assertEqual(be.retry_model_upgrades(now + 2 * sb.RETRY_UPGRADE_S + 3), 1)
            self.assertEqual(s._upgrade_retry["attempts"], 2)
            self.assertTrue(any(m.startswith("retry upgrade (web): attempt 2") for m in be._logs), be._logs)

    def test_the_switch_going_off_ends_a_standing_retry(self):
        # review 2026-09-17: an arm that outlived the switch would have minted a "back on" card crediting a retry that never ran
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0)
            self.assertEqual(s._asks, []); self.assertIsNone(s._upgrade_retry, "the tick clears it")
            self.assertTrue(any("standing down" in m for m in be._logs), be._logs)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)
            self.assertEqual(h.restored, [], "no card credits a retry that never ran")
            # a reader that meets the switch off first clears it too
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True); self.assertIsNotNone(s._upgrade_retry)
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)
            self.assertIsNone(s._upgrade_retry); self.assertEqual(h.restored, [])

    def test_turning_the_switch_on_takes_up_a_session_that_already_sits_below_its_pick(self):
        be = _backend()
        with _Hooks():
            s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")   # pick fable, running on Opus already
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            be.apply_model_switches()
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "the very card the user was looking at")
            self.assertEqual(arm["to"], "Opus 5"); self.assertTrue(arm["from"]); self.assertEqual(arm["pick"], "fable")
            t = _sess(be, sid="11111111-2222-4333-8444-000000000712", model="", liveModel="Opus 5", liveModelId="claude-opus-5")
            be.apply_model_switches()
            self.assertIsNone(t._upgrade_retry, "no pick, no tier to sit below")

    def test_an_ended_session_is_skipped(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s.ended = True
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0)


class TheWayBack(unittest.TestCase):
    def test_a_served_turn_on_the_picks_tier_ends_the_retry_and_mints_the_card(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")                # the init after the reconnect: configured, not served
            self.assertIsNotNone(s._upgrade_retry); self.assertEqual(h.restored, [])
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)   # a parent turn served on Fable
            self.assertIsNone(s._upgrade_retry, "done")
            self.assertEqual(h.restored, [("Opus 5", "Fable 5.1")], "the hook: (the fallback it sat on, the model it is back on)")
            self.assertTrue(any(m.startswith("retry upgrade (web): back on Fable 5.1") for m in be._logs), be._logs)

    def test_a_higher_tier_than_the_one_it_fell_from_counts_too_and_a_lower_one_does_not(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be, model="opus", liveModel="Opus 5", liveModelId="claude-opus-5")
            s._learn_model("Sonnet 5", raw="claude-sonnet-5", served=True)     # Opus → Sonnet
            self.assertIsNotNone(s._upgrade_retry)
            s._learn_model("Haiku 4.5", raw="claude-haiku-4-5", served=True)   # lower still: a second fallback, same arm
            self.assertIsNotNone(s._upgrade_retry)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)   # above the tier it fell from
            self.assertIsNone(s._upgrade_retry)
            self.assertEqual(h.restored[-1], ("Sonnet 5", "Fable 5.1"))

    def test_a_pick_of_the_users_own_ends_the_retry(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            self.assertIsNotNone(s._upgrade_retry)
            try:
                be.set_model(SID, "opus")
            except Exception:
                pass   # the acknowledging chip needs a chat this lab has none of; the arm clears under the lock before it
            self.assertIsNone(s._upgrade_retry, "the user chose: nothing to retry")


class KernelWiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            self.src = f.read()

    def test_the_hook_the_tick_and_the_stage_are_wired(self):
        self.assertIn("type(_sdk_backend).on_model_restored = staticmethod(", self.src)
        self.assertIn("jd.mint_restored_card(sid, frm, to), _push_soon()", self.src)
        self.assertIn("_job_stage('retryUpgrade', lambda: _retry_upgrade_tick(now))", self.src)
        self.assertIn("def _retry_upgrade_tick(now):", self.src)
        self.assertIn("be.retry_model_upgrades(now)", self.src)
        src = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn('self._learn_model(pretty_model(m), raw=str(m), served=True)', src, "the parent AssistantMessage is the served evidence")
        self.assertEqual(src.count("served=True"), 1, "…and the ONLY served learn: the init's report of the configured model passes no flag")
        self.assertIn('self._learn_model(pretty_model(d.get("model")), raw=str(d.get("model") or ""))', src, "the init learn, flagless")


class ABusySessionIsNotCut(unittest.TestCase):
    def test_a_due_attempt_on_a_busy_session_waits_for_quiet_and_no_second_attempt_stacks_on_it(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s.inflight = 1
            now = time.time()
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "due: the attempt is asked for")
            self.assertEqual(s._asks, [], "…but nothing reconnects mid-turn, and never through the deferred road")
            self.assertFalse(s._reconnect_when_idle)
            self.assertEqual(s._switch_wanted, "retry upgrade")
            self.assertTrue(any("once the session is quiet" in m for m in be._logs if m.startswith("retry upgrade (web): attempt 1")), be._logs)
            self.assertEqual(be.retry_model_upgrades(now + 2 * sb.RETRY_UPGRADE_S + 2), 0, "a cadence later, still busy: no attempt stacks on the standing ask")
            self.assertEqual(s._upgrade_retry["attempts"], 1)
            s.inflight = 0
            self.assertTrue(s._try_switch_reconnect(), "the settle's try: quiet now, carried")
            self.assertEqual(len(s._asks), 1)
            self.assertGreaterEqual(s._upgrade_retry["next"], time.time() + sb.RETRY_UPGRADE_S - 5, "the next attempt counts from the carried reconnect")


if __name__ == "__main__":
    unittest.main()
