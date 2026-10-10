#!/usr/bin/env python3
"""Sessions went DEAF under host load (2026-09-11 21:00Z to 2026-09-12 00:00Z, a load of 100 to 300
on 64 cores). The SDK runs SdkSession._prompt_submit_hook for EVERY prompt a session receives, the
hook opened with a reg read on every prompt — before it could tell the prompt was not a recurring
cron's at all — and when that read stalled past the CLI's hook deadline the SDK REFUSED the prompt
instead of failing open: six sessions missed messages for 14 to 76 minutes each. The hook's own
docstring forbids exactly that ("every uncertain path fails OPEN").

Two guards, pinned here. (1) An ORDINARY prompt is answered from the per-session cache of
recurring-cron prompt heads and touches no file — no read, no stat — while a prompt an armed
recurring schedule carries still reads the reg and still blocks a replayed slot exactly as before.
The cache follows the armed set through its three refresh paths: construction, every sessionCrons
writer on the session object (the scheduling-tool hook's arm and delete, the Stop hook's record),
and a once-a-minute stat-then-read backstop for writers the object never saw. (2) The whole body runs
under a wall-time cap (ROMP_PROMPT_HOOK_TIMEOUT_S, default 8 s) with the reg read AND the
cronDelivered write off the loop thread, so a stall — even a blocking one — is answered {} inside the
cap: the prompt runs, and a stall that outlasts many prompts is one counted problem row per session,
not a row per prompt. Plus the SDK-side matcher deadline: raised to 120 s where no session host
raises it, left to the host's own 540 s bound where one does. Synthetic fixtures (hostname TESTHOST,
placeholder sid); PRIVATE sid."""
import asyncio
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the load — the module resolves its state root at import time, and only
# pytest runs conftest's floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_prompt_hook", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-000000000911"        # private synthetic sid — never the shared one
HOST = "TESTHOST"
PROMPT = "nightly sweep of the notes-api test corpus"
ORDINARY = "please rerun the failing web tests and summarize"
CRON = "0 0 1 1 *"          # Jan 1 00:00 — its most recent slot is FIXED for the whole test run
CORRUPT = b'{"sid": "trunca'


def _armed(prompt=PROMPT, cron=CRON):
    return {"id": "c1", "cron": cron, "prompt": prompt, "kind": "cron", "recurring": True,
            "armedAt": time.time() - 3600, "dueEpoch": None, "procGen": "gen-A"}


class _Gate(unittest.TestCase):
    """A real backend + session over a private reg on a temp state dir; no real CLI."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        self._path = sb._reg_path(Path(self.d), SID)
        self._env_before = os.environ.get("ROMP_PROMPT_HOOK_TIMEOUT_S")
        os.environ.pop("ROMP_PROMPT_HOOK_TIMEOUT_S", None)

    def tearDown(self):
        if self._env_before is None:
            os.environ.pop("ROMP_PROMPT_HOOK_TIMEOUT_S", None)
        else:
            os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = self._env_before

    def _write(self, crons):
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "sched", "cwd": "/tmp", "host": HOST,
                                         "alive": True, "sessionCrons": crons})

    def _session(self):
        return sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))

    def _fire(self, sess, prompt=PROMPT):
        return asyncio.run(sess._prompt_submit_hook({"prompt": prompt}, None, None))

    def _reg(self):
        """Read the reg file directly — never through the module readers a test may have wrapped."""
        try:
            return json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}

    def _count_reads(self):
        """Wrap the module's reg readers (looked up at call time) with counters; restored in tearDown."""
        counts = {"rmw": 0, "read": 0}
        real_rmw, real_read = sb.read_reg_for_rmw, sb.read_reg

        def rmw(state_dir, sid):
            counts["rmw"] += 1
            return real_rmw(state_dir, sid)

        def read(state_dir, sid):
            counts["read"] += 1
            return real_read(state_dir, sid)

        sb.read_reg_for_rmw, sb.read_reg = rmw, read
        self.addCleanup(setattr, sb, "read_reg_for_rmw", real_rmw)
        self.addCleanup(setattr, sb, "read_reg", real_read)
        return counts

    def _forbid_reads(self, sess):
        """Every file touch the hook could make RAISES (and is counted): the readers by module name, the
        stat by the session's own method. A hook that reaches any of them is caught by its except → {}
        — which is why the tests assert the COUNT, not just the answer."""
        counts = {"rmw": 0, "read": 0, "stat": 0}

        def boom(which):
            def f(*a, **k):
                counts[which] += 1
                raise AssertionError("the common path touched the reg (%s)" % which)
            return f

        real_rmw, real_read = sb.read_reg_for_rmw, sb.read_reg
        sb.read_reg_for_rmw, sb.read_reg = boom("rmw"), boom("read")
        self.addCleanup(setattr, sb, "read_reg_for_rmw", real_rmw)
        self.addCleanup(setattr, sb, "read_reg", real_read)
        sess._reg_mtime_ns = boom("stat")
        return counts


class OrdinaryPromptsTouchNoFile(_Gate):
    """(a) The common path: a prompt no armed recurring schedule carries costs no read and no stat."""

    def test_an_ordinary_prompt_never_reads_the_reg(self):
        self._write([_armed()])
        s = self._session()
        counts = self._forbid_reads(s)
        self.assertEqual(self._fire(s, ORDINARY), {}, "the prompt runs")
        self.assertEqual(counts, {"rmw": 0, "read": 0, "stat": 0},
                         "no reg read, no stat: the answer came from the cache")
        self.assertNotIn("cronDelivered", self._reg(), "and nothing was written")
        self.assertFalse(any("prompt allowed" in m for m in self.logs),
                         "no fail-open log either — this was a plain miss, not an error path")

    def test_a_session_with_no_schedules_is_the_same_free_path(self):
        self._write([])
        s = self._session()
        counts = self._forbid_reads(s)
        for i in range(50):
            self.assertEqual(self._fire(s, "message %d for the web session" % i), {})
        self.assertEqual(counts, {"rmw": 0, "read": 0, "stat": 0},
                         "fifty prompts inside a minute: not one file touch")

    def test_an_empty_prompt_is_ignored_without_a_read(self):
        self._write([_armed()])
        s = self._session()
        counts = self._forbid_reads(s)
        self.assertEqual(self._fire(s, ""), {})
        self.assertEqual(asyncio.run(s._prompt_submit_hook({}, None, None)), {})
        self.assertEqual(counts, {"rmw": 0, "read": 0, "stat": 0})


class CronPromptsStillGate(_Gate):
    """(b) A prompt IN the cache consults the reg and blocks a replayed slot exactly as before."""

    def test_construction_seeds_the_cache_from_the_reg(self):
        self._write([_armed()])
        s = self._session()
        self.assertEqual(s._cron_prompts, frozenset({PROMPT}))
        self.assertIsNotNone(s._cron_prompts_mtime, "the reg's mtime is stamped beside it")

    def test_matching_prompt_reads_the_reg_and_blocks_the_restart_replay(self):
        self._write([_armed()])
        counts = self._count_reads()
        s1 = self._session()
        self.assertEqual(self._fire(s1), {}, "the first delivery of a slot goes through")
        self.assertEqual(counts["rmw"], 1, "…and it DID consult the reg (read_reg_for_rmw, once)")
        slot = sb.cron_prev_due(CRON, time.time())
        key = sb.cron_slot_key(CRON, PROMPT)
        self.assertEqual(self._reg().get("cronDelivered"), {key: slot},
                         "delivery — not arming — writes the durable slot record")
        s2 = self._session()                       # the kernel restarted; boot re-arm resumed it
        out = self._fire(s2)                       # the fresh CLI catch-up re-fires the slot
        self.assertEqual(counts["rmw"], 2, "the replay check is a reg read too")
        self.assertEqual(out.get("decision"), "block", "the replay is refused")
        self.assertIn("already ran", out.get("reason", ""))
        self.assertEqual(self._reg().get("cronDelivered"), {key: slot}, "a refused replay changes nothing")
        self.assertTrue(any("blocked a replayed schedule fire" in m for m in self.logs), "loudly")

    def test_a_blocked_replay_is_remembered_so_its_drop_is_not_re_sent(self):
        """The gate's block is the one dropped_by_hook removal romp decides: it is recorded (_note_gate_blocked) so the
        take path reads the CLI's removal as a decision and does not re-send it (_requeue_hook_drop). A prompt the gate
        lets through is not recorded."""
        self._write([_armed()])
        s1 = self._session()
        self.assertEqual(self._fire(s1), {})
        self.assertFalse(s1._take_gate_blocked(PROMPT), "an allowed prompt is not remembered")
        s2 = self._session()
        self.assertEqual(self._fire(s2).get("decision"), "block")
        self.assertEqual(self._fire(s2, ORDINARY), {})
        self.assertFalse(s2._take_gate_blocked(ORDINARY))
        self.assertTrue(s2._take_gate_blocked(PROMPT), "the blocked prompt is remembered for its drop")

    def test_long_prompts_match_on_the_recorded_500(self):
        long_prompt = "x" * 480 + PROMPT           # the reg stores prompt[:500]
        self._write([_armed(prompt=long_prompt[:500])])
        counts = self._count_reads()
        s1 = self._session()
        self.assertEqual(self._fire(s1, long_prompt), {})
        self.assertEqual(counts["rmw"], 1, "the cache keys on the same first 500 characters the reg holds")
        s2 = self._session()
        self.assertEqual(self._fire(s2, long_prompt).get("decision"), "block")

    def test_unreadable_reg_on_a_cron_prompt_fails_open_and_says_so(self):
        self._write([_armed()])
        s = self._session()
        self._path.write_bytes(CORRUPT)
        self.assertEqual(self._fire(s), {}, "fail OPEN")
        self.assertEqual(self._path.read_bytes(), CORRUPT, "…and never writes through the corruption")
        self.assertTrue(any("unreadable" in m for m in self.logs), "loudly")

    def test_one_shots_never_enter_the_cache(self):
        self._write([{"id": "w1", "cron": "", "prompt": PROMPT, "recurring": False, "armedAt": time.time(),
                      "dueEpoch": time.time() + 60, "procGen": "gen-A", "src": "toolhook"}])
        s = self._session()
        self.assertEqual(s._cron_prompts, frozenset(), "recurring_crons(reg) is the cache's predicate")
        counts = self._forbid_reads(s)
        self.assertEqual(self._fire(s), {})
        self.assertEqual(counts["rmw"], 0, "a one-shot's prompt is an ordinary prompt to the gate")


class CacheFollowsTheArmedSet(_Gate):
    """The refresh paths: the scheduling-tool hook's arm and delete, the Stop hook's record, and the
    once-a-minute mtime backstop for a writer this object never saw."""

    def _tool(self, s, tool, **tool_input):
        return asyncio.run(s._sched_tool_hook({"tool_name": tool, "tool_input": tool_input}, None, None))

    def test_cron_create_via_the_tool_hook_arms_the_gate(self):
        self._write([])
        s = self._session()
        counts = self._count_reads()
        self.assertEqual(self._fire(s), {})
        self.assertEqual(counts["rmw"], 0, "before the arm, the prompt is ordinary: no read")
        self._tool(s, "CronCreate", name="c1", schedule=CRON, prompt=PROMPT)
        self.assertIn(PROMPT, s._cron_prompts, "the arm refreshed the cache")
        reads = counts["rmw"]
        self.assertEqual(self._fire(s), {}, "the first delivery goes through…")
        self.assertEqual(counts["rmw"], reads + 1, "…via a reg read")
        self.assertTrue(self._reg().get("cronDelivered"), "…and records the slot")
        self.assertEqual(self._fire(s).get("decision"), "block", "the same slot again is a replay")

    def test_cron_delete_via_the_tool_hook_disarms_it(self):
        self._write([_armed()])
        s = self._session()
        self._tool(s, "CronDelete", name="c1")
        self.assertEqual(s._cron_prompts, frozenset(), "the delete refreshed the cache")
        counts = self._forbid_reads(s)
        self.assertEqual(self._fire(s), {})
        self.assertEqual(counts["rmw"], 0, "the prompt is ordinary again: no read")

    def test_stop_hook_record_refreshes_the_cache(self):
        self._write([])
        s = self._session()
        self.assertEqual(s._cron_prompts, frozenset())
        asyncio.run(s._stop_hook({"session_crons": [{"id": "c1", "schedule": CRON, "prompt": PROMPT,
                                                     "kind": "cron", "recurring": True}]}, None, None))
        self.assertIn(PROMPT, s._cron_prompts, "the Stop hook's sessionCrons record is a writer too")

    def test_backstop_rereads_when_the_reg_mtime_moved(self):
        self._write([])
        s = self._session()
        # another writer (the boot reconcile, a kernel-side prune) arms a schedule behind this object
        self._write([_armed()])
        st = self._path.stat()
        os.utime(self._path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # a moved mtime, for certain
        counts = self._count_reads()
        s._cron_prompts_at = time.monotonic() - sb.CRON_PROMPTS_REFRESH_S - 1      # the minute is up
        self.assertEqual(self._fire(s), {}, "the backstop found the arm: this is the slot's first delivery")
        self.assertIn(PROMPT, s._cron_prompts)
        self.assertGreaterEqual(counts["read"], 1, "the moved mtime cost a read")
        self.assertTrue(self._reg().get("cronDelivered"), "…and the gate engaged")

    def test_backstop_costs_one_stat_and_no_read_when_the_mtime_is_unchanged(self):
        self._write([])
        s = self._session()
        counts = self._count_reads()
        stats = {"n": 0}
        real_stat = s._reg_mtime_ns

        def counted():
            stats["n"] += 1
            return real_stat()
        s._reg_mtime_ns = counted
        s._cron_prompts_at = time.monotonic() - sb.CRON_PROMPTS_REFRESH_S - 1
        self.assertEqual(self._fire(s, ORDINARY), {})
        self.assertEqual((stats["n"], counts["read"], counts["rmw"]), (1, 0, 0),
                         "one stat, no read: the mtime had not moved")
        self.assertEqual(self._fire(s, ORDINARY), {})
        self.assertEqual(stats["n"], 1, "the slot was claimed: the next prompt inside the minute skips the stat")

    def test_backstop_is_not_reached_inside_the_minute(self):
        self._write([])
        s = self._session()
        counts = self._forbid_reads(s)
        self.assertEqual(self._fire(s, ORDINARY), {})
        self.assertEqual(counts["stat"], 0, "a fresh cache is trusted for the whole minute")


class TimeoutFailsOpen(_Gate):
    """(c) The cap: a body that runs long is answered {} — and logged as a problem — inside the cap."""

    def _timed_fire(self, s, prompt=PROMPT):
        async def run():
            t0 = time.monotonic()
            out = await s._prompt_submit_hook({"prompt": prompt}, None, None)
            return out, time.monotonic() - t0
        return asyncio.run(run())

    def test_a_slow_body_is_cut_and_the_prompt_runs(self):
        self._write([_armed()])
        s = self._session()
        os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = "0.05"

        async def slow(inp):
            await asyncio.sleep(0.6)
            return {"decision": "block", "reason": "too late to matter"}
        s._prompt_submit_gate = slow
        out, took = self._timed_fire(s)
        self.assertEqual(out, {}, "fail OPEN: the prompt runs")
        self.assertLess(took, 0.5, "…answered inside the cap, not after the body finished")
        self.assertTrue(any("ran past its" in m for m in self.logs), "loudly")
        self.assertTrue(any("ran past its" in str(p.get("text")) for p in self.be._problems),
                        "…as a PROBLEM (the dashboard's error ring), not a plain log line")

    def test_a_stalled_blocking_reg_read_is_cut_too(self):
        """The 2026-09-11 shape exactly: the reg read itself hangs. wait_for can only interrupt a body
        that yields, so the read runs off the loop thread — pinned here by a BLOCKING sleep."""
        self._write([_armed()])
        s = self._session()
        os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = "0.05"
        real = sb.read_reg_for_rmw

        def stalled(state_dir, sid):
            time.sleep(0.6)
            return real(state_dir, sid)
        sb.read_reg_for_rmw = stalled
        self.addCleanup(setattr, sb, "read_reg_for_rmw", real)
        out, took = self._timed_fire(s)
        self.assertEqual(out, {}, "the cron prompt is allowed rather than refused at the SDK's deadline")
        self.assertLess(took, 0.5, "the hook did not wait for the stalled read")
        self.assertTrue(any("ran past its" in m for m in self.logs))

    def test_a_stalled_blocking_cron_delivered_write_is_cut_too(self):
        """The cron-prompt path's OTHER file touch: recording the slot is _update_reg, a lock wait plus a
        read plus a write, and the cap can only interrupt at an await, so the write runs off the loop
        thread as the read does. Pinned by a BLOCKING sleep in write_reg (which _update_reg reaches
        by module name); the cut write still lands, so the slot the prompt ran is on file."""
        self._write([_armed()])
        s = self._session()
        os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = "0.05"
        real = sb.write_reg

        def stalled(state_dir, sid, reg):
            time.sleep(0.6)
            return real(state_dir, sid, reg)
        sb.write_reg = stalled
        self.addCleanup(setattr, sb, "write_reg", real)
        out, took = self._timed_fire(s)
        self.assertEqual(out, {}, "the cron prompt is allowed rather than refused at the SDK's deadline")
        self.assertLess(took, 0.5, "the hook did not wait for the stalled write")
        self.assertTrue(any("ran past its" in m for m in self.logs))
        self.assertTrue(self._reg().get("cronDelivered"),
                        "the worker finished the write the cap cut: the delivered slot is recorded")

    def test_repeated_timeouts_are_one_counted_problem_row(self):
        """A stall lasts an EPISODE, not a prompt. The kernel log gets every timed-out hook's line, but
        the ring row is keyed per session, so the dashboard sees one counted row rather than a fresh
        entry per prompt evicting every other problem (the 2026-09-06 class)."""
        self._write([_armed()])
        s = self._session()
        os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = "0.05"
        real = sb.read_reg_for_rmw

        def stalled(state_dir, sid):
            time.sleep(0.6)
            return real(state_dir, sid)
        sb.read_reg_for_rmw = stalled
        self.addCleanup(setattr, sb, "read_reg_for_rmw", real)
        self.assertEqual(self._timed_fire(s)[0], {})
        self.assertEqual(self._timed_fire(s)[0], {})
        self.assertEqual(len([m for m in self.logs if "ran past its" in m]), 2,
                         "the kernel log still gets every line")
        rows = [p for p in self.be._problems if "ran past its" in str(p.get("text"))]
        self.assertEqual(len(rows), 1, "…but the ring holds ONE row for the episode")
        self.assertEqual(rows[0].get("count"), 2, "…that counts the repeat")
        self.assertIn("1 repeat", rows[0]["text"])
        self.assertEqual(rows[0].get("key"), ("prompt-hook-cap", SID), "keyed per session")

    def test_the_cap_reads_the_env_at_call_time(self):
        self.assertEqual(sb.prompt_hook_timeout_s(), sb.PROMPT_HOOK_TIMEOUT_S_DEFAULT)
        self.assertEqual(sb.PROMPT_HOOK_TIMEOUT_S_DEFAULT, 8.0)
        os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = "2.5"
        self.assertEqual(sb.prompt_hook_timeout_s(), 2.5)
        for bad in ("abc", "0", "-3", " "):
            os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = bad
            self.assertEqual(sb.prompt_hook_timeout_s(), 8.0, "unusable value → the default: %r" % bad)

    def test_a_fast_body_is_untouched_by_the_cap(self):
        self._write([_armed()])
        s = self._session()
        os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = "5"
        self.assertEqual(self._fire(s), {})
        self.assertEqual(self._fire(s).get("decision"), "block", "the gate's verdicts pass through the cap intact")
        self.assertFalse(any("ran past its" in m for m in self.logs))


class MatcherDeadline(_Gate):
    """The SDK-side deadline on the UserPromptSubmit matcher."""

    def _hosts(self, on):
        Path(self.d, "session-hosts").write_text("on" if on else "off")

    def test_hosts_off_raises_the_sdk_deadline_to_120(self):
        self._write([])
        self._hosts(False)
        fake = lambda **kw: types.SimpleNamespace(**kw)
        s = self._session()
        m = self.be._prompt_hook_matcher(fake, s)
        self.assertEqual(m.timeout, sb.PROMPT_HOOK_SDK_TIMEOUT_S)
        self.assertEqual(m.timeout, 120.0)
        self.assertGreater(m.timeout, sb.PROMPT_HOOK_TIMEOUT_S_DEFAULT, "the hook's own cap always fires first")
        self.assertEqual(m.hooks, [s._prompt_submit_hook])
        self.assertIsNone(m.matcher)

    def test_hosts_on_leaves_the_deadline_to_the_host_bound(self):
        self._write([])
        self._hosts(True)
        fake = lambda **kw: types.SimpleNamespace(**kw)
        s = self._session()
        m = self.be._prompt_hook_matcher(fake, s)
        self.assertFalse(hasattr(m, "timeout"), "None for _options's host loop to fill with HOOK_TIMEOUT_S")
        self.assertEqual(m.hooks, [s._prompt_submit_hook])

    def test_an_older_sdk_without_timeout_still_gets_its_matcher_loudly(self):
        self._write([])
        self._hosts(False)

        def old(matcher=None, hooks=None):
            return types.SimpleNamespace(matcher=matcher, hooks=hooks)
        s = self._session()
        m = self.be._prompt_hook_matcher(old, s)
        self.assertEqual(m.hooks, [s._prompt_submit_hook])
        self.assertFalse(hasattr(m, "timeout"))
        self.assertTrue(any("takes no timeout" in x for x in self.logs), "the degraded deadline is visible")
        self.assertTrue(any("takes no timeout" in str(p.get("text")) for p in self.be._problems))

    def test_options_wires_the_builder_into_the_hooks_dict(self):
        import sys
        self._write([])
        self._hosts(False)
        fake_sdk = "claude_agent_sdk" not in sys.modules and not sb.sdk_importable()
        if fake_sdk:
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
            sys.modules["claude_agent_sdk"] = fake
            self.addCleanup(sys.modules.pop, "claude_agent_sdk", None)
        fetch_before = sb._fetch_key_fast_org
        sb._fetch_key_fast_org = lambda key: None     # a real HTTPS GET otherwise — never from a test
        self.addCleanup(setattr, sb, "_fetch_key_fast_org", fetch_before)
        s = self._session()
        kw = self.be._options(s, dict)
        m = kw["hooks"]["UserPromptSubmit"][0]
        self.assertEqual(getattr(m, "timeout", None), 120.0)
        self.assertEqual(m.hooks, [s._prompt_submit_hook])


if __name__ == "__main__":
    unittest.main()
