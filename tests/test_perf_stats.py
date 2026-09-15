#!/usr/bin/env python3
"""The kernel's always-on performance counters (`_PerfStats`, GET /perf, `romp perf`) and the runtime
switch for the romp-perf stderr log (POST /perf {"log": bool}).

Before this the only instrumentation was `_perf()`, gated on ROMP_PERF at process start, so turning it
on meant a kernel restart; and nothing reported rates, so an optimization was checked with a hand-run
profiler. The collector counts pusher cycles and wakes, cycle durations (a ring for percentiles) and
the pusher thread's CPU, per-stage time, build cache hits, bytes sent per slot kind, goal-store I/O,
judge passes with their threads' CPU, and HTTP requests per method and path, with a lock and dict
increments on the hot paths and no formatting until a read.

Drives the REAL Handler over HTTP and the REAL _push with stubbed builders (the test_color_route.py
and test_tab_meta_push.py patterns). Synthetic fixtures only: placeholder UUIDs, invented names."""
import concurrent.futures
import inspect
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_perf", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
# A PRIVATE synthetic sid for the goal-store tests: load_goals replays the per-sid override journal,
# and node ids collide across test modules under the shared placeholder (CLAUDE.md, goal-store fixtures).
GOAL_SID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
TOP_KEYS = {"now", "since", "uptime_s", "log", "process", "pusher", "jobs", "stages_ms", "builds", "sends",   # jobs: the jobs thread's passes
            "goals", "memos", "judge", "http", "parses",   # parses: cold event-model parses (T323 stage 1)
            "checkpoints",                                 # checkpoints: the folds' checkpoints (T323 stage 3)
            "asmCheckpoint",                               # asmCheckpoint: the assembly documents (T323 stage 4a)
            "asmIndex",                                    # asmIndex: the lazy index's built atoms, by caller (T323 stage 4c)
            "recordCache",                                 # recordCache: the shared reader's byte budget and evictions (2026-09-11)
            "chatPages",                                   # chatPages: the pre-floor history pages cache (T323 stage 4b)
            "skillLoadIndex",                              # skillLoadIndex: the judge's skill-load boot pass, its raw reads (T333)
            "fileSlice",                                   # fileSlice: the file preview popover's slice cache: hit / miss / bytes / warm (T351)
            "glossary",                                    # glossary: files parsed, frames / terms / bytes built per cycle, entries cut, files refused (T351 stage 2)
            "stacks"}                                      # stacks: every thread's last frames under ROMP_PERF_STACKS, else None (T358)


def _burn_cpu(seconds):
    """Spin this thread for `seconds` of its own CPU time (thread_time, so a descheduled thread still burns
    the asked amount rather than merely waiting it out)."""
    t = time.thread_time()
    while time.thread_time() - t < seconds:
        pass


class _HttpWatch:
    """Wait for the HTTP wrapper's record instead of racing it. _perf_http_timed counts a request in its
    `finally`, AFTER the handler put the response on the wire, so a test that reads the snapshot as soon
    as urlopen returns can see the count land a moment later (it did, about one run in five). Patching
    the collector's http_request on the instance (the class method stays) makes every record observable:
    the real method runs, then the key is appended and an Event set, and wait_for blocks on the event
    until the key has been recorded `n` times or the bound passes."""

    def __enter__(self):
        st = km._PERF_STATS
        real = km._PerfStats.http_request
        self.keys, self.ev = [], threading.Event()

        def wrapped(key, dt):
            real(st, key, dt)
            self.keys.append(key)           # append BEFORE set: an observed set implies a visible append
            self.ev.set()
        st.http_request = wrapped
        return self

    def __exit__(self, *a):
        del km._PERF_STATS.http_request     # the instance attribute goes; the class method shows again

    def wait_for(self, key, n, timeout=2.0):
        deadline = time.monotonic() + timeout
        while self.keys.count(key) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self.ev.wait(left)
            self.ev.clear()
        return True


class Collector(unittest.TestCase):
    """_PerfStats on its own: every writer lands where the docstring says, and the read-time work
    (percentiles, the goals and judge reads, the process reads) produces the documented shape."""

    def setUp(self):
        self.st = km._PerfStats()

    def test_snapshot_has_the_documented_shape_and_starts_at_zero(self):
        snap = self.st.snapshot()
        self.assertEqual(set(snap), TOP_KEYS)
        p = snap["pusher"]
        self.assertEqual(p["cycles"], 0)
        for k in ("cycle_ms_p50", "cycle_ms_p90", "cycle_ms_ring_max", "cycle_ms_max", "cycle_cpu_ms_sum"):
            self.assertEqual(p[k], 0.0, k)
        self.assertEqual(p["ring_n"], 0)
        self.assertEqual(set(snap["stages_ms"]), set(km._PerfStats.STAGES))
        self.assertEqual(set(snap["builds"]), {"chat", "feed", "timeline", "feedJson", "thread"})   # thread: the comment popover's build (2026-09-08)
        self.assertEqual(set(snap["sends"]), {"full", "delta", "deduped"})
        self.assertEqual(snap["judge"]["ms_mean"], 0.0, "no passes: the mean is 0, not a division error")
        self.assertIn("cpu_ms_sum", snap["judge"])
        self.assertIn("cpu_ms_workers", snap["judge"])
        self.assertEqual(set(snap["goals"]), {"loads", "saves", "writes"}, "read through jd.goal_io_stats")
        # the three identity memos' readers land here (review find, 2026-09-08: they had no consumer)
        self.assertEqual(set(snap["memos"]), {"pass", "shared", "chain", "nudgeGate", "nudgeWalk", "convergeDeclined", "sessionsListing", "cleared", "courierSkip", "backref", "captions", "goalArchive", "plannerSkip", "ghostDropped",
                                              "bgTops", "liftGate", "intrMarks", "deadWait", "tickSeen", "statesOverlay", "lanes", "spendTree", "summaryAnchor",
                                              "chatMergeSets", "chatPostal", "chatLedger", "chatFoldTasks",   # the chat build's fixed-cost memos (2026-09-09)
                                              "outlineProvisional"})   # the Outline's provisional-row ledger memo, parse-free (plans/outline-pane-provisional-row.md, 2026-09-15)
        self.assertEqual(set(snap["memos"]["outlineProvisional"]), {"hit", "miss", "bypass_hold", "bypass_empty", "entries"},
                         "the provisional ledger memo: hits and misses on the store object's identity, the two bypasses (a rewind hold, an empty store), and the occupancy")
        self.assertEqual(set(snap["memos"]["spendTree"]), {"entries", "bytes", "bound", "served", "dirStats", "fileStats", "entryStats", "listings", "loaded", "loadFailed", "written", "swept", "dropped", "dumpSkipped", "evicted", "writeFailed"}, "the spend guard's tree memos against their bound")
        self.assertEqual(snap["memos"]["spendTree"]["bound"], km.SPEND_GUARD_TREE_MEMO_BYTES)
        self.assertEqual(set(snap["memos"]["summaryAnchor"]), {"entries", "bytes", "bound", "hit", "miss", "evict", "fault"},
                         "the brief line's text-atom landings (T388): occupancy and counters against their bound")
        self.assertEqual(snap["memos"]["summaryAnchor"]["bound"], km.SUMMARY_ANCHOR_MEMO_BYTES)
        self.assertEqual(set(snap["memos"]["bgTops"]), {"hit", "miss", "resolve", "walk", "walk_neg", "idx_build", "entries"},
                         "the placed-launch memo (_bg_placed_tops): counters plus its occupancy")
        for k, v in snap["memos"]["bgTops"].items():
            self.assertIsInstance(v, int, k)
        self.assertEqual(set(snap["memos"]["liftGate"]), {"skip", "load", "shared", "writer", "noop", "entries"},
                         "the awaiting-lift gate: session-cycles skipped vs read, the probes the shared cache "
                         "answered, the writer loads and the ones that filed nothing, plus its occupancy")
        for k, v in snap["memos"]["liftGate"].items():
            self.assertIsInstance(v, int, k)
        # the two memos the interrupt tick trims to its alive set: the interrupt-marks memo and the awaiting
        # overlay's states-log fold, each with its counters and its occupancy
        self.assertEqual(set(snap["memos"]["intrMarks"]), {"hit", "miss", "evict", "entries", "restored", "refused", "computeMs", "persisted"},
                         "the identity memo's counters and the persisted memo's (T401 (3) target 3)")
        self.assertEqual(snap["memos"]["intrMarks"], km._intr_marks_memo_report())
        self.assertEqual(set(snap["memos"]["statesOverlay"]), {"hit", "append", "refold", "fail", "evict", "entries"})
        self.assertEqual(snap["memos"]["statesOverlay"], km._states_overlay_report())
        for blk in ("intrMarks", "statesOverlay"):
            for k, v in snap["memos"][blk].items():
                self.assertIsInstance(v, int, "%s.%s" % (blk, k))
        self.assertEqual(set(snap["memos"]["lanes"]), {"hit", "miss", "live_tail", "complain_skip", "unshared_skip", "evict", "entries",
                                                     "segs_hit", "segs_miss", "prefix_hit", "prefix_segs", "dead_serve", "dead_miss", "dead_failed_serve"},
                         "the timeline's per-lane segment memo: one outcome per live lane per bars build, the dead lanes beside")
        self.assertTrue(all(type(v) is int for v in snap["memos"]["lanes"].values()))
        self.assertEqual(set(snap["memos"]["chatMergeSets"]), {"hit", "miss", "entries", "floorAgeMaxS", "builtAboveFloor"})   # 5b's two
        self.assertEqual(set(snap["memos"]["chatPostal"]), {"gate", "hit", "commit_new"})
        self.assertEqual(set(snap["memos"]["chatLedger"]), {"hit", "miss", "bypass_live", "bypass_hold", "bypass_empty", "evict", "entries"})
        self.assertEqual(set(snap["memos"]["chatFoldTasks"]), {"hit", "miss", "entries"})
        self.assertEqual(set(snap["memos"]["plannerSkip"]), {"skipped", "planned", "recorded", "restored", "refused", "persisted", "mismatchByTerm"})   # T401 (5c)
        self.assertEqual(set(snap["memos"]["captions"]), {"served", "parsed", "unstatable"})
        self.assertEqual(set(snap["memos"]["goalArchive"]), {"served", "loaded"})
        self.assertEqual(set(snap["memos"]["backref"]), {"served", "built"},
                         "the sender-board walk behind the courier link repair: built once per input state (2026-09-09)")
        self.assertEqual(snap["memos"]["nudgeGate"], {"served": 0, "derived": 0, "failed": 0},
                         "the nudge walk's placement gate: served vs re-derived (2026-09-09)")
        self.assertEqual(set(snap["memos"]["cleared"]), {"served", "derived"},
                         "the clear set: parsed once per file state, served while it stands (2026-09-09)")
        self.assertEqual(snap["memos"]["courierSkip"], km.jd.courier_skip_stats(),
                         "the courier gate: sessions skipped, scanned, recorded (2026-09-09)")
        self.assertEqual(snap["memos"]["pass"], km._goals_memo_report())
        self.assertEqual(snap["memos"]["shared"], km.jd.shared_store_stats())
        self.assertEqual(snap["memos"]["chain"], km.jd.chain_memo_stats())
        for k in ("rss_kb", "threads", "cpu_s", "pid"):
            self.assertIn(k, snap["process"])
        self.assertGreater(snap["process"]["threads"], 0)
        self.assertGreaterEqual(snap["process"]["rss_kb"], 0)
        self.assertGreaterEqual(snap["uptime_s"], 0)
        json.dumps(snap)                                     # the whole thing serializes as-is

    def test_the_asm_checkpoint_block_names_the_restore_parts(self):
        """1606 low 4: nothing pinned restoreMs on /perf. The block's restoreMs sub-keys: the four named parts and the total."""
        st = km.em.asm_checkpoint_stats()
        self.assertEqual(set(st["restoreMs"]), {"load", "verify", "index", "seed", "total"})
        self.assertTrue(all(isinstance(v, float) for v in st["restoreMs"].values()), st["restoreMs"])

    def test_the_asm_index_block_carries_the_documented_keys(self):
        """The lazy index's block (asmIndex): its keys pinned, the light-facts gauge among them (T401 (3) target 3, round three:
        the gauge was documented on /perf but never exposed)."""
        st = km.em.asm_index_stats()
        self.assertEqual(set(st), {"cap", "evictions", "materialized", "materializedBy", "materializedByStage", "resident", "restoredTurns",
                                   "rowDecodes", "userFacts"})
        self.assertIsInstance(st["userFacts"], int); self.assertGreaterEqual(st["userFacts"], 0)

    def test_the_feed_build_block_carries_the_per_session_card_memo(self):
        """builds.feed gained `memo` (T368): the feed's per-session card memo beside the build counters, its hits and
        misses per session per build, the misses attributed to the key component that moved (plus `cold`), the
        evictions, and the resident set against its bound (a fraction of the machine's memory, or ROMP_FEED_MEMO_BYTES).
        The map is the memo's own report, copied per read; tests/test_feed_session_memo.py drives the values."""
        snap = self.st.snapshot()
        self.assertEqual(set(snap["builds"]["feed"]), {"cached", "built", "ms", "memo"})
        memo = snap["builds"]["feed"]["memo"]
        self.assertEqual(set(memo), {"hit", "miss", "evict", "entries", "bytes", "bound", "derived", "miss_by"})
        self.assertEqual(set(memo["miss_by"]), set(km._FEED_MEMO_LABELS) | {"cold"})
        self.assertEqual(memo["bound"], km.FEED_MEMO_BYTES)
        self.assertEqual(memo, km._feed_memo_report())
        for k, v in memo.items():
            self.assertIsInstance(v, (int, dict), k)
        for k, v in memo["miss_by"].items():
            self.assertIsInstance(v, int, k)
        self.assertIsNot(memo, km._FEED_MEMO_STATS, "a copy per read, never the live counters")
        for kind in ("chat", "timeline", "feedJson", "thread"):
            self.assertEqual(set(snap["builds"][kind]) & {"memo"}, set(), "%s: only the feed carries the memo" % kind)

    def test_pusher_counters(self):
        self.st.wake(); self.st.wake(); self.st.wake()
        self.st.wake_kind(True); self.st.wake_kind(False); self.st.wake_kind(False)
        self.st.cycle(0.010, 0.004); self.st.cycle(0.030, 0.006); self.st.cycle(0.020)
        p = self.st.snapshot()["pusher"]
        self.assertEqual(p["wakes"], 3)
        self.assertEqual((p["wakes_event"], p["wakes_backstop"]), (1, 2))
        self.assertEqual(p["cycles"], 3)
        self.assertAlmostEqual(p["cycle_ms_sum"], 60.0)
        self.assertAlmostEqual(p["cycle_cpu_ms_sum"], 10.0, msg="the thread's own CPU rides beside the wall")
        self.assertAlmostEqual(p["cycle_ms_max"], 30.0)
        self.assertAlmostEqual(p["cycle_ms_last"], 20.0)
        self.assertEqual(p["ring_n"], 3)

    def test_ring_percentiles_and_max_come_from_the_last_256_cycles(self):
        self.st.cycle(5.0)                                   # one slow boot cycle: 5000 ms
        for i in range(300):                                 # 0..299 ms; the ring keeps 44..299
            self.st.cycle(i / 1000.0)
        p = self.st.snapshot()["pusher"]
        self.assertEqual(p["ring_n"], 256)
        self.assertEqual(p["cycles"], 301, "the count is lifetime; only the percentile window is bounded")
        self.assertAlmostEqual(p["cycle_ms_p50"], 44 + 128)  # sorted ring[int(0.5 * 256)]
        self.assertAlmostEqual(p["cycle_ms_p90"], 44 + 230)  # sorted ring[int(0.9 * 256)]
        self.assertAlmostEqual(p["cycle_ms_ring_max"], 299.0, msg="the window's max: the ring's largest")
        self.assertAlmostEqual(p["cycle_ms_max"], 5000.0, msg="the lifetime max keeps the boot cycle")

    def test_the_per_session_chat_build_timer_keeps_first_last_and_max_and_leaves_with_its_sessions_certified_death(self):
        """The process split's measure (2026-09-14): beside the aggregate, a row per session with the FIRST build after the
        boot (set once per process life), the last, the max, the counts and the leaf's bytes; sorted by max under
        builds.chat.bySession; a row leaves with its session's CERTIFIED death (_record_death drops it, whichever road
        recorded the death) and with nothing else; the aggregate is unchanged."""
        A, B, C = "aaaaaaaa-2222-4333-8444-0000000000a1", "bbbbbbbb-2222-4333-8444-0000000000b2", "cccccccc-2222-4333-8444-0000000000c3"
        self.st.build_chat(False, 0.100, active=True, sid=A, nbytes=1000)    # A: first 100 ms
        self.st.build_chat(False, 0.050, sid=A, nbytes=1200)                  # A: last 50, max stays 100
        self.st.build_chat(False, 0.020, sid=B, nbytes=50)                    # B: first 20
        self.st.build_chat(False, 0.300, sid=B, nbytes=60)                    # B: last 300, max 300
        self.st.build_chat(True, sid=C)                                       # C: cached only, never built
        self.st.build_chat(True, sid=A)
        self.st.build_chat(False, 0.010)                                      # no sid: the aggregate alone
        snap = self.st.snapshot()
        chat = snap["builds"]["chat"]
        self.assertEqual((chat["built"], chat["cached"]), (5, 2), "the aggregate counts every build as before")
        rows = {r["sid"]: r for r in chat["bySession"]}
        self.assertEqual([r["sid"] for r in chat["bySession"]], [B, A, C], "sorted by max, the largest first")
        self.assertEqual(rows[A], {"sid": A, "first": 100.0, "last": 50.0, "max": 100.0, "n": 2, "cached": 1, "bytes": 1200})
        self.assertEqual(rows[B], {"sid": B, "first": 20.0, "last": 300.0, "max": 300.0, "n": 2, "cached": 0, "bytes": 60})
        self.assertEqual(rows[C], {"sid": C, "first": None, "last": None, "max": 0.0, "n": 0, "cached": 1, "bytes": None})
        self.st.build_chat(False, 0.400, sid=A)
        rows = {r["sid"]: r for r in self.st.snapshot()["builds"]["chat"]["bySession"]}
        self.assertEqual((rows[A]["first"], rows[A]["last"], rows[A]["max"]), (100.0, 400.0, 400.0), "first is set once; last and max move")
        self.st.chat_row_drop(B)                                              # B's death was certified (_record_death calls this)
        self.assertEqual(sorted(r["sid"] for r in self.st.snapshot()["builds"]["chat"]["bySession"]), sorted([A, C]))
        self.st.chat_row_drop("no-such-sid")                                  # a death of a session never built: nothing to drop
        self.assertEqual(len(self.st.snapshot()["builds"]["chat"]["bySession"]), 2)
        # the certified death drives the drop through the real _record_death and the real death sweep's tick over three ticks
        # (tests/test_sdk_registry_blind.py, ChatBuildRowsLeaveWithTheCertifiedDeath); the call sites are executed, not read: PushStages below
        # drives the real _push and the real _push_session_now and reads the rows from the snapshot

    def test_stages_builds_judge(self):
        self.st.stage("push.chat", 0.5); self.st.stage("push.chat", 0.25); self.st.stage("jobs", 0.1)
        self.st.build("chat", True); self.st.build("chat", False, 0.040); self.st.build("feed", False, 1.0)
        self.st.judge_pass(2.0); self.st.judge_pass(4.0); self.st.judge_cpu(0.25)
        snap = self.st.snapshot()
        self.assertAlmostEqual(snap["stages_ms"]["push.chat"], 750.0)
        self.assertAlmostEqual(snap["stages_ms"]["jobs"], 100.0)
        # chat also carries the watched/background split and the per-component attribution (2026-09-09); the
        # plain writer counts the build and attributes nothing
        self.assertEqual(snap["builds"]["chat"], {"cached": 1, "built": 1, "ms": 40.0, "active_built": 0, "bg_built": 0,
                                                  "moved": 0, "coldSkipped": 0, "bg_miss": {k: 0 for k in km._PerfStats.CHAT_MISS},
                                                  "bySession": []})                   # the per-session timer (2026-09-14): no sid handed in, no row
        self.assertEqual(snap["builds"]["feed"]["built"], 1)
        self.assertEqual(snap["builds"]["timeline"], {"cached": 0, "built": 0, "ms": 0.0})
        self.assertEqual(snap["judge"]["passes"], 2)
        self.assertAlmostEqual(snap["judge"]["ms_last"], 4000.0)
        self.assertAlmostEqual(snap["judge"]["ms_mean"], 3000.0)
        self.assertAlmostEqual(snap["judge"]["cpu_ms_sum"] - snap["judge"]["cpu_ms_workers"], 250.0,
                               msg="the tier threads' CPU, apart from the pool workers' share")

    def test_sends_classify_by_kind_and_slot_name(self):
        self.st.send(("chat", SID), "full", 1000)            # a tuple dedup key: the slot is its first element
        self.st.send(("chat", SID), "full", 500)
        self.st.send(("chat", SID), "deduped", 1000)
        self.st.send("feed", "delta", 20)                    # a bare string key (the feed's own delta path)
        s = self.st.snapshot()["sends"]
        self.assertEqual(s["full"], {"chat": {"count": 2, "bytes": 1500}})
        self.assertEqual(s["deduped"], {"chat": {"count": 1, "bytes": 1000}})
        self.assertEqual(s["delta"], {"feed": {"count": 1, "bytes": 20}})

    def test_send_slots_are_capped(self):
        for i in range(40):
            self.st.send(("slot%d" % i,), "full", 1)
        d = self.st.snapshot()["sends"]["full"]
        self.assertEqual(len(d), km._PerfStats.SLOTS + 1)
        self.assertEqual(d["other"]["count"], 40 - km._PerfStats.SLOTS)

    def test_http_keys_are_capped_and_ws_adds_no_time(self):
        cap = km._PerfStats.HTTP_PATHS
        for i in range(cap + 36):
            self.st.http_request("GET /scan/%d" % i, 0.001)
        self.st.http_request("GET /ws", None)
        h = self.st.snapshot()["http"]
        self.assertEqual(len(h), cap + 1, "the cap's keys plus other")
        self.assertEqual(h["other"]["count"], 36 + 1,
                         "the 36 keys past the cap and /ws, which arrived after it")
        st2 = km._PerfStats()
        st2.http_request("GET /ws", None); st2.http_request("POST /tick", 0.002)
        h = st2.snapshot()["http"]
        self.assertEqual(h["GET /ws"], {"count": 1, "ms": 0.0}, "a socket's lifetime is not a request time")
        self.assertEqual(h["POST /tick"]["count"], 1)
        self.assertAlmostEqual(h["POST /tick"]["ms"], 2.0)

    def test_the_http_cap_clears_the_kernels_own_route_table(self):
        """HTTP_PATHS bounds the distinct keys for the kernel's LIFETIME (a scanner must not grow the dict), so it
        has to sit comfortably above the kernel's own fixed routes, or a real route that first arrives after
        the cap lands in "other" for good. The first cap, 64, was below the route table itself (88 literals
        on 2026-09-07). The count comes from the do_* dispatch source (`p == "/x"`, `u.path == "/x"` and the
        `in ("/x", "/y")` tuples; inspect.getsource unwraps the timing decorator), so this trips when routes
        outgrow the headroom: 1.5x the literal count, room for the collapsed /dist/*, /media/* and /remote/*/…
        families and an OPTIONS preflight per cross-origin POST route."""
        lit = re.compile(r'(?:\bp|u\.path) (?:==|in) (?:"(/[^"]*)"|\(((?:"/[^"]*"(?:, )?)+)\))')
        n = 0
        for meth in ("do_GET", "do_HEAD", "do_OPTIONS", "do_POST"):
            src = inspect.getsource(getattr(km.Handler, meth))
            paths = set()
            for m in lit.finditer(src):
                paths.update([m.group(1)] if m.group(1) is not None else re.findall(r'"(/[^"]*)"', m.group(2)))
            n += len(paths)
        self.assertGreaterEqual(n, 80, "the derivation lost the route table (did the dispatch shape change?)")
        self.assertGreaterEqual(km._PerfStats.HTTP_PATHS, int(n * 1.5),
                                "%d fixed routes: raise HTTP_PATHS, or routes land in other for the kernel's lifetime" % n)

    def test_http_key_is_method_plus_normalized_path(self):
        key = km._perf_http_key
        self.assertEqual(key("GET", "/sessions"), "GET /sessions")
        self.assertEqual(key("POST", "/perf"), "POST /perf", "GET /perf and POST /perf are separate rows")
        self.assertEqual(key("GET", "/dist/render.js"), "GET /dist/*")
        self.assertEqual(key("GET", "/dist/fonts/a-b-c.woff2"), "GET /dist/*", "sixty font files: one key")
        self.assertEqual(key("GET", "/media/romp-app-192.png"), "GET /media/*")
        self.assertEqual(key("GET", "/remote/TESTHOST/ws"), "GET /remote/*/ws", "no host name in a key")
        self.assertEqual(key("HEAD", "/remote/TESTHOST/file"), "HEAD /remote/*/file")
        self.assertEqual(key("GET", "/remote/TESTHOST"), "GET /remote/*")
        self.assertEqual(key("", "/version"), "/version", "a handler without a method: the path alone")

    def test_reset_starts_over_and_moves_since(self):
        self.st.cycle(0.1); self.st.http_request("GET /x", 0.1)
        before = self.st.snapshot()["since"]
        time.sleep(0.01)
        self.st.reset()
        snap = self.st.snapshot()
        self.assertEqual(snap["pusher"]["cycles"], 0)
        self.assertEqual(snap["http"], {})
        self.assertGreater(snap["since"], before)

    def test_writers_are_thread_safe(self):
        def hammer():
            for _ in range(2000):
                self.st.wake(); self.st.send(("chat", SID), "full", 1); self.st.http_request("GET /p", 0.0)
        ts = [threading.Thread(target=hammer) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        snap = self.st.snapshot()
        self.assertEqual(snap["pusher"]["wakes"], 16000)
        self.assertEqual(snap["sends"]["full"]["chat"]["count"], 16000)
        self.assertEqual(snap["http"]["GET /p"]["count"], 16000)


class ProcessStatsFallback(unittest.TestCase):
    """_process_stats reads VmRSS from /proc/self/status; a platform without /proc (macOS) gets
    ru_maxrss, which the kernel there reports in bytes and Linux in KB, so only the darwin branch
    scales. Both branches are driven here: /proc is made to fail on every platform, and the
    platform name and the rusage read are patched so the figure is exact."""

    class _Usage:
        ru_maxrss = 2048 * 1024                              # bytes on darwin, KB on linux

    def _stats(self, platform):
        real_open = open

        def no_proc(path, *a, **kw):
            if path == "/proc/self/status":
                raise FileNotFoundError(path)
            return real_open(path, *a, **kw)
        import resource
        with mock.patch("builtins.open", side_effect=no_proc), \
                mock.patch.object(resource, "getrusage", return_value=self._Usage()), \
                mock.patch.object(sys, "platform", platform):
            return km._process_stats()

    def test_without_proc_rss_comes_from_ru_maxrss(self):
        st = self._stats("linux")
        self.assertIsInstance(st["rss_kb"], int)
        self.assertEqual(st["rss_kb"], 2048 * 1024, "linux reports ru_maxrss in KB: taken as is")
        for k in ("threads", "cpu_s", "pid"):
            self.assertIn(k, st)
        self.assertEqual(st["pid"], os.getpid())

    def test_on_darwin_ru_maxrss_is_bytes_and_is_scaled_to_kb(self):
        st = self._stats("darwin")
        self.assertEqual(st["rss_kb"], 2048, "ru_maxrss // 1024")

    def test_with_proc_present_the_fallback_is_not_used(self):
        import resource
        with mock.patch.object(resource, "getrusage", side_effect=AssertionError("fallback taken")):
            try:
                with open("/proc/self/status"):
                    pass
            except OSError:
                self.skipTest("no /proc on this platform")
            self.assertGreater(km._process_stats()["rss_kb"], 0, "VmRSS read from /proc")


class WakeCounting(unittest.TestCase):
    """_pusher_wake is a threading.Event whose set() counts: every existing call site — including the
    bound-method callbacks the backends hold — counts a wake without being touched."""

    def test_the_pusher_wake_counts_sets(self):
        self.assertIsInstance(km._pusher_wake, threading.Event)
        self.assertIsInstance(km._pusher_wake, km._CountedEvent)
        was_set = km._pusher_wake.is_set()
        before = km._PERF_STATS.snapshot()["pusher"]["wakes"]
        km._pusher_wake.set()
        cb = km._pusher_wake.set                             # the shape the backends are handed (push=_pusher_wake.set)
        cb()
        self.assertTrue(km._pusher_wake.is_set())
        self.assertEqual(km._PERF_STATS.snapshot()["pusher"]["wakes"], before + 2)
        if not was_set:
            km._pusher_wake.clear()

    def test_wake_helpers_still_set_the_event(self):
        km._pusher_wake.clear()
        km._push_soon()
        self.assertTrue(km._pusher_wake.is_set())
        km._pusher_wake.clear()


class PerfLogToggle(unittest.TestCase):
    """_perf() writes only when the switch is on, and the switch flips at runtime."""

    def setUp(self):
        self.saved = km._PERF
        km._set_perf_log(False)

    def tearDown(self):
        km._set_perf_log(self.saved)

    def test_off_writes_nothing_on_writes_one_line(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._perf("probe", b=2, a=1)
        self.assertEqual(buf.getvalue(), "")
        self.assertTrue(km._set_perf_log(True))
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._perf("probe", b=2, a=1)
        self.assertEqual(buf.getvalue(), "romp-perf probe a=1 b=2\n")
        km._set_perf_log(False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._perf("probe", b=2, a=1)
        self.assertEqual(buf.getvalue(), "", "off again: nothing")

    def test_the_off_path_reads_one_module_global(self):
        src = inspect.getsource(km._perf)
        self.assertIn("if not _PERF:\n        return", src, "the hot path stays a name lookup and a return")


class GoalIoCounters(unittest.TestCase):
    """judge.load_goals / save_goals count calls and disk writes; the kernel reads them via goal_io_stats."""

    def setUp(self):
        self.jd = km.jd
        self.jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._clean)

    def _clean(self):
        for p in (self.jd.GOALDIR / (GOAL_SID + ".json"), self.jd.STATE / "overrides" / (GOAL_SID + ".jsonl")):
            try:
                p.unlink()
            except OSError:
                pass

    def test_loads_saves_and_writes(self):
        before = self.jd.goal_io_stats()
        store = self.jd.load_goals(GOAL_SID)                 # no file yet: a fresh store, still one load
        after = self.jd.goal_io_stats()
        self.assertEqual(after["loads"], before["loads"] + 1)
        self.jd.save_goals(GOAL_SID, store)                  # the first publish writes the file
        after = self.jd.goal_io_stats()
        self.assertEqual(after["saves"], before["saves"] + 1)
        self.assertEqual(after["writes"], before["writes"] + 1)
        store = self.jd.load_goals(GOAL_SID)
        self.jd.save_goals(GOAL_SID, store)                  # byte-identical: a save, not a write
        after2 = self.jd.goal_io_stats()
        self.assertEqual(after2["saves"], after["saves"] + 1)
        self.assertEqual(after2["writes"], after["writes"], "the no-op republish skip is visible as saves without writes")
        self.assertEqual(km._PERF_STATS.snapshot()["goals"], after2, "the kernel's snapshot carries the judge counters")

    def test_the_getter_returns_a_copy(self):
        d = self.jd.goal_io_stats()
        d["loads"] = -1
        self.assertNotEqual(self.jd.goal_io_stats()["loads"], -1)

    def test_the_reference_doc_describes_memos_and_routes_the_pushers_loads_there(self):
        # docs/reference.md read `goals.loads` as every store read; the pusher's loads moved to the shared cache
        # with this PR, so the doc names the memos section and sends the reader there (review find, 2026-09-08)
        doc = Path(HERE).parent.joinpath("docs", "reference.md").read_text()
        self.assertIn("- `memos`:", doc)
        for k in ("`pass`", "`shared`", "`chain`", "`intrMarks`", "`statesOverlay`", "`deadWait`"):
            self.assertIn(k, doc)
        self.assertIn("`memos.shared`", doc)

    def test_the_pushers_shared_loads_count_under_memos_shared_not_under_goals_loads(self):
        # `goals.loads` is the writer's loader alone; the pusher's read-only loads ride load_goals_shared and
        # show under memos.shared (review find, 2026-09-08: nine pusher sites left `loads` with the move to the
        # shared cache, and the doc still read it as every store read). A fill is a miss, a re-read a hit.
        self.jd.save_goals(GOAL_SID, self.jd.load_goals(GOAL_SID))
        before, snap0 = self.jd.goal_io_stats(), km._PERF_STATS.snapshot()["memos"]["shared"]
        self.jd.load_goals_shared(GOAL_SID)
        self.jd.load_goals_shared(GOAL_SID)
        after, snap = self.jd.goal_io_stats(), km._PERF_STATS.snapshot()["memos"]["shared"]
        self.assertEqual(after["loads"], before["loads"], "two shared loads: no writer-side load counted")
        self.assertEqual((snap["miss"] - snap0["miss"], snap["hit"] - snap0["hit"]), (1, 1),
                         "...the fill and the hit are the shared cache's, on the snapshot")


class JudgeCpu(unittest.TestCase):
    """The judge's CPU is attributed from two places: the tier threads (_run_tier) and every future the
    tiers submit to judge.py's pools (_TimedPool, bound to the module's ThreadPoolExecutor name)."""

    def test_pool_workers_account_their_cpu(self):
        jd = km.jd
        self.assertTrue(issubclass(jd.ThreadPoolExecutor, concurrent.futures.ThreadPoolExecutor),
                        "every pool in judge.py is a real executor that also accounts")
        before = jd.judge_worker_cpu_ms()
        with jd.ThreadPoolExecutor(max_workers=2) as ex:
            self.assertEqual(ex.submit(lambda a, b=1: a + b, 2, b=3).result(), 5, "args and kwargs pass through")
            ex.submit(_burn_cpu, 0.005).result()
        grew = jd.judge_worker_cpu_ms() - before
        self.assertGreaterEqual(grew, 4.0, "about 5 ms of a worker's CPU landed")
        self.assertLess(grew, 500.0)
        self.assertEqual(km._PERF_STATS.snapshot()["judge"]["cpu_ms_workers"], jd.judge_worker_cpu_ms())

    def test_run_tier_accounts_the_tier_threads_cpu(self):
        """The shared tier runner (judge.py _run_tier, stage three round two) lands the thread's own CPU in the pass's
        accounting record under its lock, a raising tier included (the finally); the producer feeds the record's total to
        /perf's judge.cpu_ms_sum (a source pin on the call)."""
        jd = km.jd
        acc = jd._pass_acc()
        jd._run_tier(lambda: _burn_cpu(0.005), "index", acc)
        self.assertGreaterEqual(acc["cpuS"] * 1000.0, 4.0); self.assertEqual(acc["failures"], [])
        before = acc["cpuS"]
        with redirect_stderr(io.StringIO()):
            jd._run_tier(lambda: (_burn_cpu(0.005), (_ for _ in ()).throw(RuntimeError("tier died"))), "triage", acc)
        self.assertGreaterEqual((acc["cpuS"] - before) * 1000.0, 4.0, "a raising tier still accounts (the finally)")
        self.assertEqual(len(acc["failures"]), 1); self.assertIn("RuntimeError: tier died", acc["failures"][0])
        import inspect
        self.assertIn('_PERF_STATS.judge_cpu(res["tierCpuS"])', inspect.getsource(km._producer), "the producer feeds the total")
        self.assertIn('with acc["lock"]:', inspect.getsource(jd._run_tier), "the accumulation takes the lock")
        before = km._PERF_STATS.snapshot()["judge"]["cpu_ms_sum"]
        km._PERF_STATS.judge_cpu(0.005)
        self.assertAlmostEqual(km._PERF_STATS.snapshot()["judge"]["cpu_ms_sum"] - before, 5.0, places=3)


class PusherRecords(unittest.TestCase):
    """The pusher's seams record into _PERF_STATS: a cycle lands in the ring with its thread's CPU, the
    cycle jobs split into push and jobs, the cached and rebuilt feed/timeline paths count, and the
    send paths classify full / delta / deduped for whole-frame, delta-capable and chat-tail clients."""

    JOBS = ("_apply_pending_ops", "_turn_notify_tick", "_lift_spent_awaiting", "_death_sweep_tick",
            "_end_on_idle_sweep", "_deferral_sweep_tick", "_auto_nudge_tick", "_interrupt_block_tick",
            "_auto_pause_on_limit", "_usage_poll_tick", "_auto_pause_on_spend_limit", "_auto_resume_retry",
            "_auto_resume_session_retry", "_auto_retry_tick", "_idle_queue_drive_tick",
            "_clear_done_working_notes", "_spend_guard_tick", "_push_all",
            "_api_health_frame", "_api_health_push")   # the bottom bar's API cell; the spend guard (T350, "_converge_checkpoints")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        names = Path(self.td.name) / "names"
        names.mkdir()
        self.saved = (km.NAMES, km._live_map, km._pusher_cycle_jobs, list(km._built_feed),
                      list(km._built_timeline), km.build_feed, km.build_timeline, km._needs_you_count,
                      km._feed_notifications, km._badge_push, km._views_dirty[0])
        self.saved_jobs = {nm: getattr(km, nm) for nm in self.JOBS}
        km.NAMES = names
        km._live_map = lambda: {}
        self.addCleanup(self._restore)
        self.addCleanup(self.td.cleanup)

    def _restore(self):
        (km.NAMES, km._live_map, km._pusher_cycle_jobs, bf, bt, km.build_feed, km.build_timeline,
         km._needs_you_count, km._feed_notifications, km._badge_push, vd) = self.saved
        km._built_feed[:] = bf
        km._built_timeline[:] = bt
        km._views_dirty[0] = vd
        for nm, fn in self.saved_jobs.items():
            setattr(km, nm, fn)

    def _pusher(self):
        return km._PERF_STATS.snapshot()["pusher"]

    def test_an_idle_cycle_is_one_that_set_no_wake_sent_nothing_and_saved_nothing(self):
        # IDLE CYCLES (2026-09-09): the loop re-enters after a fixed 0.5 s backstop whether or not anything
        # changed; the idle share is what a cadence change is judged on. A cycle whose jobs set no wake,
        # sent no client payload and saved no goal store counts as idle, with its wall and CPU.
        km._pusher_cycle_jobs = lambda now, live_map, any_client: time.sleep(0.003)
        before = self._pusher()
        km._pusher_cycle()
        after = self._pusher()
        self.assertEqual(after["idle_cycles"], before["idle_cycles"] + 1)
        self.assertGreaterEqual(after["idle_ms_sum"] - before["idle_ms_sum"], 3.0)
        self.assertGreaterEqual(after["idle_cpu_ms_sum"], before["idle_cpu_ms_sum"])
        self.assertEqual(after["cycles"], before["cycles"] + 1, "an idle cycle is still a cycle")

    def test_a_cycle_that_sends_a_payload_is_not_idle(self):
        km._pusher_cycle_jobs = lambda now, live_map, any_client: km._PERF_STATS.send(("chat", "s1"), "full", 10)
        before = self._pusher()
        km._pusher_cycle()
        after = self._pusher()
        self.assertEqual(after["idle_cycles"], before["idle_cycles"])
        self.assertEqual(after["sends"], before["sends"] + 1)

    def test_a_deduped_frame_does_not_break_an_idle_cycle(self):
        # with a dashboard connected the push builds and compares the per-cycle chat frames every cycle; a
        # frame the client already holds is reported as "deduped" and is not a payload that went out
        km._pusher_cycle_jobs = lambda now, live_map, any_client: km._PERF_STATS.send(("chat", "taborder"), "deduped", 10)
        before = self._pusher()
        km._pusher_cycle()
        after = self._pusher()
        self.assertEqual(after["idle_cycles"], before["idle_cycles"] + 1, "still idle")
        self.assertEqual(after["sends"], before["sends"], "a deduped frame is not a send")

    def test_a_cycle_that_sets_the_wake_is_not_idle(self):
        km._pusher_cycle_jobs = lambda now, live_map, any_client: km._pusher_wake.set()
        before = self._pusher()
        km._pusher_cycle()
        km._pusher_wake.clear()
        self.assertEqual(self._pusher()["idle_cycles"], before["idle_cycles"])

    def test_a_cycle_that_saves_a_goal_store_is_not_idle(self):
        km._pusher_cycle_jobs = lambda now, live_map, any_client: km.jd._goal_io_bump("saves")
        before = self._pusher()
        km._pusher_cycle()
        self.assertEqual(self._pusher()["idle_cycles"], before["idle_cycles"])
        km._pusher_cycle_jobs = lambda now, live_map, any_client: km.jd._goal_io_bump("writes")
        km._pusher_cycle()
        self.assertEqual(self._pusher()["idle_cycles"], before["idle_cycles"])

    def test_a_cycle_is_counted_and_timed(self):
        km._pusher_cycle_jobs = lambda now, live_map, any_client: time.sleep(0.005)
        before = self._pusher()
        km._pusher_cycle()
        after = self._pusher()
        self.assertEqual(after["cycles"], before["cycles"] + 1)
        self.assertGreaterEqual(after["cycle_ms_last"], 5.0)
        self.assertEqual(after["ring_n"], min(before["ring_n"] + 1, km._PerfStats.RING))

    def test_a_cycle_records_its_threads_cpu_not_its_waits(self):
        km._pusher_cycle_jobs = lambda now, live_map, any_client: time.sleep(0.020)   # a wait, no CPU
        before = self._pusher()
        km._pusher_cycle()
        after = self._pusher()
        self.assertGreaterEqual(after["cycle_ms_last"], 20.0)
        self.assertLess(after["cycle_cpu_ms_sum"] - before["cycle_cpu_ms_sum"], 15.0,
                        "a sleeping cycle adds far less CPU than wall")
        km._pusher_cycle_jobs = lambda now, live_map, any_client: _burn_cpu(0.005)     # CPU, no wait
        before = self._pusher()
        km._pusher_cycle()
        after = self._pusher()
        self.assertGreaterEqual(after["cycle_cpu_ms_sum"] - before["cycle_cpu_ms_sum"], 4.0,
                                "a spinning cycle's CPU lands in cycle_cpu_ms_sum")

    def test_a_raising_cycle_is_still_counted(self):
        km._pusher_cycle_jobs = lambda now, live_map, any_client: (_ for _ in ()).throw(RuntimeError("job died"))
        before = self._pusher()["cycles"]
        with self.assertRaises(RuntimeError):
            km._pusher_cycle()
        self.assertEqual(self._pusher()["cycles"], before + 1)

    def test_cycle_jobs_split_into_push_and_jobs(self):
        # the REAL _pusher_cycle_jobs with every tick job a no-op and _push_all a stub that reads the clock ONCE,
        # under a stubbed time.monotonic that steps 1 ms per read: `push` is the _push_all call (the read inside
        # the stub plus the read that closes it: 2 ms exactly), `jobs` the rest of the function (the reads outside
        # the push: a count of clock reads, never negative). A wall-clock ratio here (push >= a 5 ms sleep, jobs
        # below two pushes) was green alone and a coin toss under the suite (2026-09-12, 2026-09-14); a stubbed
        # clock makes both stages counts
        for nm in self.JOBS:
            setattr(km, nm, lambda *a, **k: None)
        km._push_all = lambda live_map=None: time.monotonic()
        reads = [0]
        def _clock():
            reads[0] += 1
            return reads[0] * 0.001
        with mock.patch.object(km.time, "monotonic", _clock):
            before = km._PERF_STATS.snapshot()["stages_ms"]
            km._pusher_cycle_jobs(int(time.time()), {}, True)
            after = km._PERF_STATS.snapshot()["stages_ms"]
            push, jobs = after["push"] - before["push"], after["jobs"] - before["jobs"]
            n_first = reads[0]
            self.assertAlmostEqual(push, 2.0, places=6, msg="the push stage spans the stub's read and the closing read")
            self.assertGreaterEqual(jobs, 0.0, "jobs is the function minus the push, never negative")
            self.assertAlmostEqual(push + jobs, (n_first - 1) * 1.0, places=6, msg="push plus jobs is the function's whole span: every read but the first")
            before = km._PERF_STATS.snapshot()["stages_ms"]
            km._pusher_cycle_jobs(int(time.time()), {}, False)   # no client: no push, the jobs still run
            after = km._PERF_STATS.snapshot()["stages_ms"]
            self.assertEqual(after["push"], before["push"])
            self.assertAlmostEqual(after["jobs"] - before["jobs"], (reads[0] - n_first - 1) * 1.0, places=6, msg="the no-client cycle's jobs span every read but its first")

    def test_a_connect_serves_the_build_it_tested_when_the_cache_is_replaced_between_its_reads(self):
        # _cached_timeline tested the cached payload and returned it as two reads of the shared list while the
        # pusher thread assigns _built_timeline[:] on a rebuild; a connect on the handler thread whose two reads
        # straddled that assignment returned the replacement build, not the one its freshness test saw. One read.
        class Swapped(list):
            """_built_timeline with the pusher's `_built_timeline[:] = [...]` landing between two reads of the
            payload slot: the first read answers the build, every later one the replacement."""
            def __init__(self, entry, later):
                super().__init__(entry)
                self.reads, self.later = 0, later

            def __getitem__(self, i):
                if i == 1:
                    self.reads += 1
                    if self.reads > 1:
                        return self.later
                return list.__getitem__(self, i)
        built = {"type": "timeline", "now": 1.0, "turns": {}, "judging": {}, "messages": []}
        replacement = {"type": "timeline", "now": 2.0, "turns": {}, "judging": {}, "messages": []}
        real = km._built_timeline
        km._built_timeline = Swapped(["sig-a", built, 5.0, 4.0], replacement)   # the pusher replaced the build between the two reads
        self.addCleanup(setattr, km, "_built_timeline", real)
        km.build_timeline = lambda *a, **k: (_ for _ in ()).throw(AssertionError("a connect never rebuilds"))
        served = km._VIEW_STATS["tlServe"]
        self.assertIs(km._cached_timeline(int(time.time()), {}, "sig-b", connect=True), built,
                      "the connect gets the build its freshness test saw, not the replacement that landed under it")
        self.assertEqual(km._VIEW_STATS["tlServe"], served + 1)

    def test_feed_and_timeline_builds_count_cached_and_rebuilt(self):
        km.build_feed = lambda now, live_map: {"working": [], "items": []}
        km.build_timeline = lambda now, live_map, **kw: {"turns": [], "judging": [], "messages": [], "now": now}
        km._needs_you_count = lambda feed: 0
        km._feed_notifications = lambda feed: []
        km._badge_push = lambda n: None
        km._views_dirty[0] = 0.0
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        b0 = km._PERF_STATS.snapshot()["builds"]
        now = int(time.time())
        km._cached_feed(now, {}, "sig-a")                    # nothing warmed: a rebuild
        km._cached_feed(now, {}, "sig-a")                    # unchanged sig: served from the cache
        km._cached_timeline(now, {}, "sig-a")
        km._cached_timeline(now, {}, "sig-a", connect=True)  # a connecting page never rebuilds
        b1 = km._PERF_STATS.snapshot()["builds"]
        self.assertEqual(b1["feed"]["built"] - b0["feed"]["built"], 1)
        self.assertEqual(b1["feed"]["cached"] - b0["feed"]["cached"], 1)
        self.assertGreaterEqual(b1["feed"]["ms"], b0["feed"]["ms"])
        self.assertEqual(b1["timeline"]["built"] - b0["timeline"]["built"], 1)
        self.assertEqual(b1["timeline"]["cached"] - b0["timeline"]["cached"], 1)

    @staticmethod
    def _sends():
        s = km._PERF_STATS.snapshot()["sends"]
        return {(kind, slot): e["count"] for kind, d in s.items() for slot, e in d.items()}

    def test_a_whole_frame_client_counts_full_then_deduped(self):
        sent = []
        c = {"app": "chat", "alive": True, "send": sent.append, "sent": {}}
        s0 = self._sends()
        km._send_client(c, ("working", SID), {"type": "working", "names": ["web"]})
        km._send_client(c, ("working", SID), {"type": "working", "names": ["web"]})
        s1 = self._sends()
        self.assertEqual(len(sent), 1, "the second identical frame was deduped")
        self.assertEqual(s1[("full", "working")] - s0.get(("full", "working"), 0), 1)
        self.assertEqual(s1[("deduped", "working")] - s0.get(("deduped", "working"), 0), 1)
        bytes_full = km._PERF_STATS.snapshot()["sends"]["full"]["working"]["bytes"]
        self.assertGreaterEqual(bytes_full, len(sent[0]))

    def test_a_delta_client_counts_the_suppressed_bars_frame_as_deduped(self):
        # every browser pane connects with delta=1, so the bars slot's dedup happens in _send_slot_delta's
        # unchanged path, not in _send_client — it must count there too, or deduped.timelinebars stays 0
        sent = []
        c = {"app": "timeline", "alive": True, "send": sent.append, "sent": {}, "delta": True}
        bars = {"type": "bars", "turns": {"lane": [{"id": "t1", "a": 1}]}, "judging": [], "messages": [],
                "now": 1, "warming": False}
        pre = json.dumps(bars)
        sig = km._dedup_sig(bars, pre)
        s0 = self._sends()
        km._send_slot(c, "bars", bars, pre, sig)
        km._send_slot(c, "bars", bars, pre, sig)
        s1 = self._sends()
        self.assertEqual(len(sent), 1, "the keyed full went once; the unchanged repeat sent nothing")
        self.assertEqual(s1[("full", "timelinebars")] - s0.get(("full", "timelinebars"), 0), 1)
        self.assertEqual(s1[("deduped", "timelinebars")] - s0.get(("deduped", "timelinebars"), 0), 1)
        self.assertEqual(s1.get(("delta", "timelinebars"), 0) - s0.get(("delta", "timelinebars"), 0), 0)

    def test_a_caught_up_chat_client_counts_its_tail_as_delta(self):
        sent = []
        c = {"app": "chat", "alive": True, "send": sent.append, "sent": {}}
        m1 = {"type": "session", "id": SID, "name": "web", "events": [{"uuid": "e1", "type": "user"}],
              "status": {"state": "working"}}                # the shape build_session returns: a {type: session} frame
        m2 = {"type": "session", "id": SID, "name": "web", "events": m1["events"] + [{"uuid": "e2", "type": "assistant"}],
              "status": {"state": "waiting"}}
        s0 = self._sends()
        km._send_chat(c, m1, None, 0, False)                # nothing held: the whole session
        km._send_chat(c, m2, None, 1, False)                # caught up through e1: the suffix from 1
        s1 = self._sends()
        self.assertEqual([json.loads(x)["type"] for x in sent], ["session", "chatTail"])
        self.assertEqual(s1[("full", "chat")] - s0.get(("full", "chat"), 0), 1)
        self.assertEqual(s1[("delta", "chat")] - s0.get(("delta", "chat"), 0), 1, "the tail is the chat's delta")


class PushStages(unittest.TestCase):
    """_push driven for real (the test_tab_meta_push.py pattern) with builders stubbed to sleep 5 ms
    each: every push.* stage grows by at least its builder's sleep, the chat build counts as built on
    the first push and as cached on the second (same transcript, background tab), and the timeline
    client's bars go out in the send stage."""

    STUBS = ("NAMES", "_live_map", "_live_names", "_chat_tab_sessions", "build_session",
             "_cached_feed", "_cached_timeline", "build_timeline", "_fleet_view_sig", "_comments_frame",
             "_retry_parked_creates")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        names = Path(self.tmp) / "names"
        names.mkdir()
        (names / SID).write_text("web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        self.transcript = Path(self.tmp) / (SID + ".jsonl")
        self.transcript.write_text('{"type": "user"}\n')       # exists → _chat_build_sig is a real signature
        self.saved = {nm: getattr(km, nm) for nm in self.STUBS}
        self.saved_state = (km.jd.STATE, dict(km._built_chat), dict(km._prev_chat_events),
                            dict(km._prev_chat_ledger), list(km._last_tab_order))
        km.NAMES = names
        km.jd.STATE = Path(self.tmp) / "state"
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._live_map = lambda: {}
        km._live_names = lambda tm: {"web": SID}
        km._chat_tab_sessions = lambda now, live_map: [{"sid": SID, "name": "web", "path": str(self.transcript),
                                                    "anchor": SID}]
        km.build_session = self._build_session
        km._cached_feed = lambda now, live_map, sig, connect=False: self._slow({"working": [], "awaiting": [], "now": now})
        km._cached_timeline = lambda now, live_map, sig, connect=False: self._slow(
            {"turns": {}, "judging": [], "messages": [], "now": now})
        km.build_timeline = lambda now, live_map, **kw: {"lanes": [], "now": now}
        km._fleet_view_sig = lambda now, live_map: {"probe": 1}
        km._comments_frame = lambda sid, live_map: None
        km._retry_parked_creates = lambda: None
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        self.builds = 0
        self.chat_frames, self.tl_frames = [], []
        self.chat = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: self.chat_frames.append(json.loads(s))}
        self.tl = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: self.tl_frames.append(json.loads(s))}

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        st, bc, pe, pl, lo = self.saved_state
        km.jd.STATE = st
        km._built_chat.clear(); km._built_chat.update(bc)
        km._prev_chat_events.clear(); km._prev_chat_events.update(pe)
        km._prev_chat_ledger.clear(); km._prev_chat_ledger.update(pl)
        km._last_tab_order[:] = lo

    @staticmethod
    def _slow(value):
        time.sleep(0.005)
        return value

    def _build_session(self, sid, now, live_map):
        self.builds += 1
        return self._slow({"type": "session", "id": sid, "name": "web", "events": [{"uuid": "e1", "type": "user"}],
                           "ledger": None, "status": {"state": "waiting"}, "color": None})

    def test_stages_and_chat_builds_are_recorded_by_the_real_push(self):
        snap0 = km._PERF_STATS.snapshot()
        km._push([self.chat, self.tl])
        snap1 = km._PERF_STATS.snapshot()
        self.assertEqual(self.builds, 1)
        d = {k: snap1["stages_ms"][k] - snap0["stages_ms"][k] for k in snap0["stages_ms"]}
        self.assertGreaterEqual(d["push.chat"], 5.0, "the build_session sleep lands in the chat stage")
        self.assertGreaterEqual(d["push.feed"], 5.0, "the _cached_feed sleep lands in the feed stage")
        self.assertGreaterEqual(d["push.timeline"], 5.0, "the _cached_timeline sleep lands in the timeline stage")
        self.assertGreater(d["push.send"], 0.0, "the bars serialization and send took time")
        self.assertEqual(d["jobs"], 0.0, "a bare _push is not a cycle: jobs and push stay")
        self.assertEqual(d["push"], 0.0)
        self.assertEqual(snap1["builds"]["chat"]["built"] - snap0["builds"]["chat"]["built"], 1)
        self.assertEqual(snap1["builds"]["chat"]["cached"] - snap0["builds"]["chat"]["cached"], 0)
        self.assertGreaterEqual(snap1["builds"]["chat"]["ms"] - snap0["builds"]["chat"]["ms"], 5.0)
        self.assertIn("session", [f["type"] for f in self.chat_frames])
        self.assertIn("bars", [f["type"] for f in self.tl_frames])
        # the same transcript again: the background tab's build is served from the cache
        km._push([self.chat, self.tl])
        snap2 = km._PERF_STATS.snapshot()
        self.assertEqual(self.builds, 1, "no rebuild")
        self.assertEqual(snap2["builds"]["chat"]["cached"] - snap1["builds"]["chat"]["cached"], 1)
        self.assertEqual(snap2["builds"]["chat"]["built"] - snap1["builds"]["chat"]["built"], 0)
        self.assertLess(snap2["stages_ms"]["push.chat"] - snap1["stages_ms"]["push.chat"], 5.0,
                        "a cached tab costs the chat stage no build")

    def test_the_per_session_row_is_fed_by_the_real_push_with_the_leafs_bytes_and_a_cached_second_push(self):
        # round three, low 2: the wiring executed instead of a regex over the kernel source: the built site hands the sid and
        # the leaf's byte size, the cached site hands the sid; a second push over the same transcript raises `cached` and leaves n
        km._PERF_STATS.chat_by_session.pop(SID, None)
        km._push([self.chat, self.tl])
        rows = {r["sid"]: r for r in km._PERF_STATS.snapshot()["builds"]["chat"]["bySession"]}
        self.assertIn(SID, rows, "the built site hands the sid: %s" % sorted(rows))
        row = rows[SID]
        self.assertEqual((row["n"], row["cached"], row["bytes"]), (1, 0, os.path.getsize(self.transcript)), row)
        self.assertGreaterEqual(row["first"], 5.0, "the build's sleep is the first build's ms"); self.assertEqual((row["last"], row["max"]), (row["first"], row["first"]))
        km._push([self.chat, self.tl])
        row2 = {r["sid"]: r for r in km._PERF_STATS.snapshot()["builds"]["chat"]["bySession"]}[SID]
        self.assertEqual((row2["n"], row2["cached"], row2["first"]), (1, 1, row["first"]), "the cached site hands the sid; n and first stand")

    def test_the_targeted_push_records_its_build_in_the_row_and_the_aggregate(self):
        # round three, low 1: _push_session_now built through build_session and recorded nothing, so the row's first (the number
        # the timer exists to read) could be a build over a cache an unrecorded handshake push had warmed; it records under the
        # label `targeted` now and still caches nothing (no dependency record: a stored entry would have no signature)
        km._PERF_STATS.chat_by_session.pop(SID, None)
        saved = (list(km._clients), km._PERF_STATS.snapshot()["builds"]["chat"])
        with km._clients_lock:
            km._clients[:] = [self.chat]
        try:
            km._push_session_now(SID)
        finally:
            with km._clients_lock:
                km._clients[:] = saved[0]
        self.assertEqual(self.builds, 1, "the targeted push built the session")
        snap = km._PERF_STATS.snapshot()["builds"]["chat"]
        row = {r["sid"]: r for r in snap["bySession"]}.get(SID)
        self.assertIsNotNone(row, "the targeted push feeds the per-session row")
        self.assertEqual((row["n"], row["cached"], row["bytes"]), (1, 0, os.path.getsize(self.transcript)), row)
        self.assertGreaterEqual(row["first"], 5.0)
        self.assertEqual(snap["built"] - saved[1]["built"], 1, "and the aggregate")
        self.assertEqual(snap["bg_miss"].get("targeted", 0) - saved[1]["bg_miss"].get("targeted", 0), 1, "attributed to the push, not a signature component")
        self.assertNotIn(SID, km._built_chat, "still not cached: the build ran with no dependency record")
        self.assertIn("session", [f["type"] for f in self.chat_frames])
        # round four, low b: the watched tab's handshake build lands under active_built, read from the target client's active sid
        self.chat["active"] = SID
        km._PERF_STATS.chat_by_session.pop(SID, None)
        before = km._PERF_STATS.snapshot()["builds"]["chat"]
        with km._clients_lock:
            km._clients[:] = [self.chat]
        try:
            km._push_session_now(SID)
        finally:
            with km._clients_lock:
                km._clients[:] = saved[0]
        after = km._PERF_STATS.snapshot()["builds"]["chat"]
        self.assertEqual((after["active_built"] - before["active_built"], after["bg_built"] - before["bg_built"]), (1, 0),
                         "the watched tab's build counts as active, not background")
        self.assertEqual(after["bg_miss"].get("targeted", 0), before["bg_miss"].get("targeted", 0), "no background attribution for the watched tab")

    def test_the_seams_stay_where_the_stages_are_defined(self):
        # the order of the four stage records in _push is the definition of the split; pinned beside the
        # behavioural test above so a re-ordering is caught even when every stage still grows
        push = inspect.getsource(km._push)
        idx = [push.index('_PERF_STATS.stage("%s"' % st) for st in ("push.chat", "push.feed", "push.timeline", "push.send")]
        self.assertEqual(idx, sorted(idx))
        self.assertIn("_PERF_STATS.judge_pass(", inspect.getsource(km._producer))
        loop = inspect.getsource(km._pusher)
        self.assertIn("_woke = _pusher_wake.wait(0.5)", loop)
        self.assertIn("_PERF_STATS.wake_kind(_woke)", loop)


class PerfRoutes(unittest.TestCase):
    """GET /perf and POST /perf through the real Handler: token-gated, the documented shape, the toggle,
    and every request method counted under METHOD /path."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.saved_log = km._PERF
        km._set_perf_log(False)

    def tearDown(self):
        km._set_perf_log(self.saved_log)

    def _req(self, method, path, body=None, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            # km.TOKEN, not os.environ: under xdist every worker imports every test module at
            # collection, and a later module's import-time ROMP_SERVE_TOKEN write changes the env
            # after this module's kernel captured its token (the test_kernel_attach_on_behalf pattern)
            headers["X-Romp-Token"] = km.TOKEN
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw.startswith(("{", "[")) else raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            return e.code, (json.loads(raw) if raw.startswith(("{", "[")) else raw)

    @staticmethod
    def _http(key):
        return km._PERF_STATS.snapshot()["http"].get(key, {"count": 0, "ms": 0.0})

    def test_get_perf_serves_the_snapshot(self):
        with _HttpWatch() as w:
            st, snap = self._req("GET", "/perf")
            self.assertEqual(st, 200)
            self.assertEqual(set(snap), TOP_KEYS)
            self.assertIs(snap["log"], False)
            self.assertEqual(snap["process"]["pid"], os.getpid())
            self.assertTrue(w.wait_for("GET /perf", 1), "the request's own record lands after the response")
            st, snap2 = self._req("GET", "/perf?x=1")
        self.assertEqual(st, 200)
        self.assertGreaterEqual(snap2["http"]["GET /perf"]["count"], 1, "counted under METHOD /path, query stripped")
        self.assertFalse([k for k in snap2["http"] if "?" in k])

    def test_get_perf_requires_the_token(self):
        st, body = self._req("GET", "/perf", token=False)
        self.assertEqual(st, 403)
        self.assertNotIn("cycles", str(body))
        st, body = self._req("GET", "/perf?stacks=1", token=False)
        self.assertEqual(st, 403, "the stack sample is token-gated like the snapshot")
        self.assertNotIn("frames", str(body))

    def test_get_perf_stacks_carries_one_frame_list_per_thread_with_its_stage_mark(self):
        """T401 (2)'s proof instrument: `?stacks=1` adds one row per live thread (name, ident, self, stage, frames innermost
        last); a thread inside a tick job shows `jobs.<job>` and the frame it waits in; the plain snapshot carries no `stacks`;
        the registry row is gone once the job returns."""
        ev = threading.Event(); inside = threading.Event()
        def probe():
            inside.set(); ev.wait(10)
        th = threading.Thread(target=lambda: km._job_stage("probe", probe), name="probe-thread", daemon=True)
        th.start(); self.assertTrue(inside.wait(5))
        try:
            st, snap = self._req("GET", "/perf?stacks=1")
        finally:
            ev.set(); th.join(5)
        self.assertEqual(st, 200)
        rows = snap["stacks"]
        self.assertIsInstance(rows, dict, "?stacks=1 fills the slot the plain snapshot leaves null")
        self.assertTrue(rows and all(set(r) == {"self", "stage", "frames"} for r in rows.values()), list(rows.items())[:1])
        key = "%d probe-thread" % th.ident
        self.assertIn(key, rows, sorted(rows))                              # keyed "<ident> <kind>" (T358's duplicate-worker case)
        mine = rows[key]
        self.assertEqual(mine["stage"], "jobs.probe", mine)
        self.assertTrue(any(f.startswith("wait (threading.py:") for f in mine["frames"]), mine["frames"])
        self.assertTrue(any(f.startswith("_job_stage (") for f in mine["frames"]), mine["frames"])   # the kernel's file name is
        #                                                                                                the launcher's here
        self.assertEqual(mine["frames"][-1].split(" ")[0], "wait", "innermost last")
        self.assertEqual(sum(1 for r in rows.values() if r["self"]), 1, "the answering handler thread is marked once")
        self.assertTrue(all(len(r["frames"]) <= 40 for r in rows.values()))
        self.assertTrue(any(k.endswith(" handler") and rows[k]["self"] for k in rows), "the answering thread's kind is handler: %s" % sorted(rows))
        self.assertNotIn(th.ident, km._STAGE_BY_TID, "the registry row is gone once the job returns")
        st, plain = self._req("GET", "/perf")
        self.assertIsNone(plain["stacks"], "the plain snapshot carries the slot empty, as before")

    def test_the_sample_keys_threads_by_kind_never_by_a_session_name(self):
        """Round one, medium 1: an SDK session thread is named "sdk:<session name>", and the sample's key carried it where
        the reference promised no session content. Keys are "<ident> <kind>", the kind _thread_kind's (the name before the
        convention's separator, a default name's target function, a pool worker's prefix)."""
        gate = threading.Event()
        th = threading.Thread(target=gate.wait, name="sdk:notes-api-web", daemon=True); th.start()
        try:
            rows = km._thread_stacks()
        finally:
            gate.set(); th.join(5)
        self.assertIn("%d sdk" % th.ident, rows, sorted(rows))
        self.assertNotIn("notes-api-web", json.dumps(rows), "no session name anywhere in the sample")
        self.assertEqual((km._thread_kind("sdk-intr:web"), km._thread_kind("Thread-12 (process_request_thread)"), km._thread_kind("pusher"),
                          km._thread_kind("MainThread"), km._thread_kind(None)), ("sdk-intr", "handler", "pusher", "main", "?"))
        # round two: every identity-bearing worker follows kind:payload, and a default name keeps its target function
        self.assertEqual((km._thread_kind("codex:notes-api-web"), km._thread_kind("end-host:11111111"), km._thread_kind("peer:TESTHOST")),
                         ("codex", "end-host", "peer"))
        self.assertEqual((km._thread_kind("Thread-7 (_ask_poll)"), km._thread_kind("Thread-9 (serve_forever)"), km._thread_kind("Thread-3")),
                         ("_ask_poll", "serve_forever", "thread"), "a default name keeps the target function, the identity a slow-boot read needs")
        self.assertEqual((km._thread_kind("judge-index_2"), km._thread_kind("ThreadPoolExecutor-0_4")), ("judge-index", "pool"))
        gate = threading.Event()
        th = threading.Thread(target=gate.wait, daemon=True); th.start()   # unnamed: Python's "Thread-N (wait)"
        try:
            rows = km._thread_stacks()
        finally:
            gate.set(); th.join(5)
        self.assertIn("%d wait" % th.ident, rows, sorted(rows))

    def test_every_named_thread_site_maps_to_a_kind_without_an_identity(self):
        """Round two, medium 1, and round three's medium 1: a census of every thread and pool construction site in the kernel,
        every module the kernel loads in-process (the backends, the judge, the credentials helper) and the postal service,
        walked with the ast module (a regex could not cross a newline and missed five named sites, the Codex worker's among
        them). A constant name is a kind already; a name with a dynamic part (a session name, a sid, a host) must carry it after
        the convention's separator so _thread_kind drops it; a name built any other way fails, and so does a name the census
        cannot see: a Thread's positional name (its third positional argument), a Timer with a positional beyond its interval
        and function, keywords passed through **kwargs, or an aliased constructor (an assignment whose value is one of the
        constructors; ctor_of resolves Name and Attribute spellings only, so an alias would hide every site built through it).
        Every kind family the census derives must appear in the reference's kind list, so a new kind cannot ship undocumented."""
        import ast, re
        root = os.path.dirname(BIN)
        files = [os.path.join(root, "kernel", f) for f in ("kernel.py", "sdk_backend.py", "codex_backend.py", "session_host.py",
                                                            "judge.py", "credentials.py")] + \
                [os.path.join(root, "postal", "postal_service.py")]
        CTORS = {"Thread", "Timer", "ThreadPoolExecutor", "_TimedPool"}
        def ctor_of(call):
            f = call.func
            n = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            return n if n in CTORS else None
        def static_prefix(v):
            """(the constant text before any dynamic part, whether the name has a dynamic part), or None for an unreadable expression."""
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                return v.value, False
            if isinstance(v, ast.JoinedStr):
                first = v.values[0] if v.values else None
                return (first.value if isinstance(first, ast.Constant) else ""), any(isinstance(p, ast.FormattedValue) for p in v.values)
            if isinstance(v, ast.BinOp) and isinstance(v.op, (ast.Mod, ast.Add)) and isinstance(v.left, ast.Constant) and isinstance(v.left.value, str):
                return v.left.value.split("%")[0], True
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "format" and isinstance(v.func.value, ast.Constant):
                return v.func.value.value.split("{")[0], True
            return None, None
        sites, named, bad, dyn_kinds, per_file = 0, [], [], set(), {}
        for f in files:
            src = open(f, encoding="utf-8").read()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "threading" and any(a.name in CTORS and a.asname for a in node.names):
                    bad.append(("%s:%d" % (os.path.basename(f), node.lineno), "a constructor imported under an alias the census cannot follow", ""))
                if isinstance(node, ast.Import) and any(a.name == "threading" and a.asname for a in node.names):
                    bad.append(("%s:%d" % (os.path.basename(f), node.lineno), "the threading module imported under an alias the census cannot follow", ""))
                if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)) and ctor_of(ast.Call(func=node.value, args=[], keywords=[])) \
                        and not all(isinstance(tg, ast.Name) and tg.id in CTORS for tg in node.targets):   # judge.py rebinds ThreadPoolExecutor
                    bad.append(("%s:%d" % (os.path.basename(f), node.lineno), "a constructor aliased into a name the census cannot follow", ast.dump(node.value)[:60]))   # to its timed subclass: both names are constructors, so every site stays visible
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or ctor_of(node) is None:
                    continue
                sites += 1; per_file[os.path.basename(f)] = per_file.get(os.path.basename(f), 0) + 1
                label = "%s:%d" % (os.path.basename(f), node.lineno)
                if ctor_of(node) == "Thread" and len(node.args) >= 3:
                    bad.append((label, "a positional name the census cannot read: pass name= as a keyword", "")); continue
                if ctor_of(node) == "Timer" and len(node.args) > 2:
                    bad.append((label, "a Timer with a positional beyond its interval and function: spell args, kwargs and any name as keywords", "")); continue
                if any(k.arg is None for k in node.keywords):
                    bad.append((label, "keywords through **kwargs may carry a name the census cannot read: spell them", "")); continue
                kw = next((k for k in node.keywords if k.arg in ("name", "thread_name_prefix")), None)
                if kw is None:
                    continue                                     # a default name: the rule keeps the target function
                static, dynamic = static_prefix(kw.value)
                named.append(label)
                if static is None:
                    bad.append((label, "a name built from an expression the census cannot read", ast.dump(kw.value)[:80])); continue
                if dynamic:
                    if not static.endswith(km._THREAD_NAME_SEP):
                        bad.append((label, "a dynamic part without the kind:payload separator", static)); continue
                    kind = km._thread_kind(static + "notes-api-web")
                    if kind != static[:-1] or "notes" in kind:
                        bad.append((label, "the payload survives", kind))
                    dyn_kinds.add(kind)                          # a kind with a payload is a documented family (sdk, codex, ...)
                else:
                    kind = km._thread_kind(static if kw.arg == "name" else static + "_0")   # a pool prefix names its workers <prefix>_N
                    if kind != static or re.search(r"[/\\]|[0-9a-f]{8}-", static):
                        bad.append((label, "a constant name that is not a plain kind", kind))
        self.assertGreaterEqual(sites, 60, "the census walked the construction sites: %d" % sites)
        self.assertGreaterEqual(len(named), 23, "the census found every named site, the multi-line ones included: %r" % named)
        self.assertTrue(any(l.startswith("codex_backend.py:") for l in named), "the Codex worker's site is walked: %r" % named)
        self.assertGreaterEqual(per_file.get("credentials.py", 0), 1, "the credentials helper's Timer is a construction site the census walked: %r" % per_file)
        self.assertGreaterEqual(per_file.get("judge.py", 0), 7, "the judge tiers' pools are construction sites the census walked: %r" % per_file)
        self.assertEqual(bad, [], "every named thread maps to a kind with no identity in it")
        ref = open(os.path.join(root, "docs", "reference.md"), encoding="utf-8").read()
        para = ref[ref.index("- `stacks`: every live thread's stack"):]
        para = para[:para.index("\n- ", 10)]
        undocumented = sorted(k for k in dyn_kinds if "`%s`" % k not in para)
        self.assertEqual(undocumented, [], "every kind family with a payload (sdk, codex, end-host, peer, ...) is in the reference's kind list; a constant name is its own kind")
        self.assertGreaterEqual(len(dyn_kinds), 5, sorted(dyn_kinds))

    def test_the_judge_pools_workers_carry_their_tier(self):
        """Round three, low 2: the pin on the pool prefix was a substring check on the source; the behaviour is pinned instead:
        a _TimedPool built on a thread named like a tier gives its workers names whose kind is judge-<tier>."""
        jd = km.jd
        out = []
        def tier():
            with jd._TimedPool(max_workers=1) as ex:
                out.append(ex.submit(lambda: threading.current_thread().name).result(5))
        th = threading.Thread(target=tier, name="index"); th.start(); th.join(10)
        self.assertEqual(len(out), 1, out)
        self.assertEqual(km._thread_kind(out[0]), "judge-index", out[0])

    def test_the_sample_never_reads_source_through_linecache(self):
        """Round one, low 1: extract_stack read and cached every source file in every stack (4 MB of kernel) for line text
        the sample never prints; the frame walk touches no file."""
        import linecache
        linecache.clearcache()
        km._thread_stacks()
        self.assertEqual([k for k in linecache.cache if k.endswith(("romp-kernel", "kernel.py", "threading.py"))], [],
                         "the sample loaded source it does not print")

    def test_the_stage_registry_follows_the_marks(self):
        """`_set_stage` writes the thread-local the readers consult and the by-ident row the sample reads, and clears the row at
        None; the push decorator and the job thunk both go through it."""
        tid = threading.get_ident()
        km._set_stage(None)
        self.assertNotIn(tid, km._STAGE_BY_TID)
        km._job_stage("probe", lambda: self.assertEqual((km._current_read_stage(), km._STAGE_BY_TID.get(tid)), ("jobs.probe", "jobs.probe")))
        self.assertEqual((km._current_read_stage(), km._STAGE_BY_TID.get(tid)), (None, None))
        km._stage_marked("marked")(lambda: self.assertEqual(km._STAGE_BY_TID.get(tid), "marked"))()
        self.assertNotIn(tid, km._STAGE_BY_TID)

    def test_post_perf_requires_the_token(self):
        st, body = self._req("POST", "/perf", {"log": True}, token=False)
        self.assertEqual(st, 403)
        self.assertFalse(km._PERF, "a refused POST flips nothing")

    def test_post_perf_flips_the_log_and_perf_emits_only_after(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._perf("probe", n=1)
        self.assertEqual(buf.getvalue(), "", "off before the POST")
        with redirect_stderr(io.StringIO()):                 # the route's own "log on" notice goes to stderr
            st, r = self._req("POST", "/perf", {"log": True})
        self.assertEqual((st, r), (200, {"ok": True, "log": True}))
        self.assertTrue(km._PERF)
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._perf("probe", n=1)
        self.assertEqual(buf.getvalue(), "romp-perf probe n=1\n", "on after the POST, no restart")
        st, snap = self._req("GET", "/perf")
        self.assertIs(snap["log"], True, "the snapshot reports the switch")
        with redirect_stderr(io.StringIO()):
            st, r = self._req("POST", "/perf", {"log": False})
        self.assertEqual((st, r), (200, {"ok": True, "log": False}))
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._perf("probe", n=1)
        self.assertEqual(buf.getvalue(), "")

    def test_post_perf_refuses_a_malformed_body(self):
        for body in ({}, {"log": "yes"}, {"log": 1}, [], "log"):
            st, r = self._req("POST", "/perf", body)
            self.assertEqual(st, 400, body)
            self.assertFalse(r["ok"])
        self.assertFalse(km._PERF)

    def test_the_routes_sit_after_the_gate_in_the_source(self):
        get = inspect.getsource(km.Handler.do_GET)
        gate = "ok, self._set_cookie, why = self._authorize(q)"
        self.assertEqual(get.count(gate), 1)
        self.assertGreater(get.index('p == "/perf"'), get.index(gate))
        post = inspect.getsource(km.Handler.do_POST)
        self.assertEqual(post.count(gate), 1)
        self.assertGreater(post.index('u.path == "/perf"'), post.index(gate))

    def test_every_request_is_timed_per_method_and_path(self):
        before = self._http("GET /version")
        with _HttpWatch() as w:
            self._req("GET", "/version?probe=1", token=False)   # an exempt route counts too
            self._req("GET", "/version", token=False)
            self.assertTrue(w.wait_for("GET /version", 2), "both records landed (waited on, not raced)")
        after = self._http("GET /version")
        self.assertEqual(after["count"], before["count"] + 2)
        self.assertGreater(after["ms"], before["ms"])

    def test_head_and_options_are_counted_too(self):
        head0, opt0 = self._http("HEAD /version"), self._http("OPTIONS /perf")
        with _HttpWatch() as w:
            self._req("HEAD", "/version", token=False)
            self._req("OPTIONS", "/perf")
            self.assertTrue(w.wait_for("HEAD /version", 1))
            self.assertTrue(w.wait_for("OPTIONS /perf", 1))
        self.assertEqual(self._http("HEAD /version")["count"], head0["count"] + 1, "a /file probe storm is visible")
        self.assertEqual(self._http("OPTIONS /perf")["count"], opt0["count"] + 1, "a preflight burst is visible")

    def test_a_ws_upgrade_is_counted_when_it_arrives(self):
        # the wrapper on a stand-in handler: for a /ws path the count is taken BEFORE the handler runs,
        # since do_GET returns only when the socket closes, and no time is added after
        seen = {}

        class H:
            path = "/ws?token=x"
            command = "GET"

            @km._perf_http_timed
            def do_GET(self):
                seen["during"] = PerfRoutes._http("GET /ws")["count"]
        before = self._http("GET /ws")
        H().do_GET()
        after = self._http("GET /ws")
        self.assertEqual(seen["during"], before["count"] + 1, "counted at arrival, not at socket close")
        self.assertEqual(after["count"], before["count"] + 1, "and not a second time in the finally")
        self.assertEqual(after["ms"], before["ms"], "a socket's lifetime is not a request time")



class StacksField(unittest.TestCase):
    """The perf route's `stacks` (T358, a debugging aid behind ROMP_PERF_STACKS; T401's sample): every thread's frames, keyed by
    the thread's ident WITH its kind, so two workers sharing a kind stay two entries (the duplicate-worker case the aid is for);
    None without the switch."""
    def test_two_threads_sharing_a_name_are_two_entries(self):
        import threading
        from unittest import mock
        gate = threading.Event()
        ths = [threading.Thread(target=gate.wait, name="same-name-worker", daemon=True) for _ in range(2)]
        for t in ths:
            t.start()
        try:
            with mock.patch.dict(os.environ, {"ROMP_PERF_STACKS": "1"}):
                snap = km._PerfStats().snapshot()
            keys = [k for k in (snap.get("stacks") or {}) if k.endswith(" same-name-worker")]
            self.assertEqual(len(keys), 2, "one entry per thread, the name carried: %s" % sorted(snap.get("stacks") or {}))
            self.assertEqual(len(set(keys)), 2, "keyed by ident: distinct")
            self.assertTrue(all(str(t.ident) in k for t, k in zip(sorted(ths, key=lambda t: t.ident), sorted(keys, key=lambda k: int(k.split()[0])))))
            for k in keys:                                                   # the value shape (T401): the row the served
                row = snap["stacks"][k]                                      #  boot diagnostic and romp perf stacks read
                self.assertEqual(set(row), {"self", "stage", "frames"}, row)
                self.assertIs(row["self"], False); self.assertIsNone(row["stage"])
                self.assertTrue(row["frames"] and all(" (" in f and f.endswith(")") for f in row["frames"]), row["frames"])
                self.assertTrue(row["frames"][-1].startswith("wait ("), "innermost last: the worker waits on its gate")
        finally:
            gate.set()
            for t in ths:
                t.join(timeout=5)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROMP_PERF_STACKS", None)
            self.assertIsNone(km._PerfStats().snapshot()["stacks"], "None without the switch")

if __name__ == "__main__":
    unittest.main()
