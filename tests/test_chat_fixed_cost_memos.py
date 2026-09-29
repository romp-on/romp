#!/usr/bin/env python3
"""The chat build's per-build fixed costs, memoized on the inputs they read, and the counters that say
where a chat rebuild came from.

Every chat build used to re-derive four things whose inputs had not moved: the live merge's
transcript-side sets (every atom of the parse, per build, for the chat, feed and timeline builds of
one cycle), the sealed postal cards (re-hydrated on every judge pass although a caption is the only
judge-written value a card embeds), the ledger's goal-tree walk (per build, over the same parse and
store), and, in its own commit, the task fold (every turn's atoms, per build). builds.chat in GET /perf
counted rebuilds without saying whether the watched tab or a background one paid, or which input moved.

The tests drive the real code paths: _PerfStats, the real _push over two tabs, _merge_live_atoms with a
recording backend, build_session over a synthetic session in a rebound state root, and _msg_summaries
over stubbed discovery rows. Synthetic fixtures only: private placeholder sids (these tests mint goals,
so never the shared placeholder), invented text, TESTHOST, the notes-api demo world."""
import json
import os
import re
import tempfile
import threading
import time
import unittest
from unittest import mock
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs
# conftest's floor (a bare unittest run would otherwise write real state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_chat_memos", os.path.join(BIN, "romp-kernel"))
jd = km.jd                                       # the kernel's own judge module

# PRIVATE synthetic sids: these tests mint goal stores, and the shared placeholder sid's override journal
# is replayed onto every store minted under it (CLAUDE.md, goal-store fixtures).
SID_A = "66666666-7777-8888-9999-aaaaaaaaaaa1"
SID_B = "66666666-7777-8888-9999-aaaaaaaaaaa2"
PEER = "66666666-7777-8888-9999-aaaaaaaaaaa3"
OTHER_A = "66666666-7777-8888-9999-aaaaaaaaaaa4"   # two sessions the built tab is party to no message with
OTHER_B = "66666666-7777-8888-9999-aaaaaaaaaaa5"
MID = "1788400000.100_1.TESTHOST"


def _clear_memos():
    """Every chat-build memo this module fills, emptied; a name absent on a tree without the memos is skipped."""
    for name in ("_chat_fold", "_ledger_memo", "_task_fold_memo", "_node_anchor_last", "_node_anchor_rev",
                 "_merge_sets_memo"):
        d = getattr(km, name, None)
        if isinstance(d, dict):
            d.clear()


# ── the signature's labels and the chat writer ───────────────────────────────────────────────────
class SigLabels(unittest.TestCase):
    """_chat_build_sig is a flat tuple with one labelled position per component (_CHAT_SIG_LABELS); a rebuild
    is attributed to the components that moved, by position. The row the push hands over is one slot
    either way (None without a row), so every signature has the same shape."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = td.name
        self.tx = Path(self.tmp) / (SID_A + ".jsonl")
        self.tx.write_text('{"type": "user"}\n')
        self.sess = {"sid": SID_A, "path": str(self.tx), "anchor": SID_A}
        self.saved = (km._sdk, km._live_map)
        km._sdk = lambda: None
        km._live_map = lambda: {}

    def tearDown(self):
        km._sdk, km._live_map = self.saved

    def test_a_real_signature_has_one_label_per_position_and_a_row_slot_either_way(self):
        sig = km._chat_build_sig(self.sess)
        self.assertEqual(len(sig), len(km._CHAT_SIG_LABELS))
        row_i = km._CHAT_SIG_LABELS.index("row")
        self.assertEqual(sig[row_i], (None, False), "no row handed over and an empty map: the slot says so")
        self.assertEqual(km._chat_build_sig(self.sess), sig, "stable while nothing moved")
        row = {"state": "idle", "model": "m", "context": 10, "effort": "e", "mode": None, "fast": False,
               "since": int(time.time()) - 5, "subagents": [], "bgTasks": [], "connected": True}   # since: recent, so the faded boolean holds
        sig2 = km._chat_build_sig(self.sess, row)
        self.assertEqual(len(sig2), len(sig))
        self.assertEqual(km._chat_sig_miss(sig, sig2), ("row",), "the row handed over is the row component")
        forked = dict(self.sess, anchor=SID_B)                # a forked lane stats two states files
        sig3 = km._chat_build_sig(forked)
        self.assertEqual(len(sig3), len(sig), "the states component holds both files; the shape never changes")
        self.assertEqual(km._chat_sig_miss(sig, sig3), ("states",))
        self.assertEqual(km._PerfStats.CHAT_MISS, km._CHAT_SIG_LABELS + ("cold", "nosig"))
        self.assertIsNone(km._chat_build_sig({"sid": SID_A, "path": ""}), "no path: no signature")

    def test_each_moved_component_is_named(self):
        base = tuple(range(len(km._CHAT_SIG_LABELS)))
        for i, lab in enumerate(km._CHAT_SIG_LABELS):
            new = list(base)
            new[i] = "changed"
            self.assertEqual(km._chat_sig_miss(base, tuple(new)), (lab,), lab)
        self.assertEqual(km._chat_sig_miss(base, base), (), "an equal signature is no miss")
        self.assertEqual(km._chat_sig_miss(None, base), ("cold",), "no cached build")
        self.assertEqual(km._chat_sig_miss(base, None), ("nosig",), "no signature could be taken")
        two = list(base)
        two[km._CHAT_SIG_LABELS.index("transcript")] = "x"
        two[km._CHAT_SIG_LABELS.index("store")] = "y"
        self.assertEqual(km._chat_sig_miss(base, tuple(two)), ("store", "transcript"),
                         "several moved components are each named, sorted")
        self.assertEqual(km._chat_sig_miss(base, base[:-1]), ("cold",), "a signature of another shape is no cached build")


class Collector(unittest.TestCase):
    """_PerfStats.build_chat: the chat kind's writer, splitting the watched tab's rebuilds from the
    background tabs' and naming what moved for each background rebuild."""

    def test_build_chat_splits_active_from_background_and_attributes_the_latter(self):
        st = km._PerfStats()
        st.build_chat(True)
        st.build_chat(False, 0.010, active=True)
        st.build_chat(False, 0.020, active=False, miss=("store",))
        st.build_chat(False, 0.030, active=False, miss=("states", "transcript"))
        st.build_chat(False, 0.005, active=False, miss=("cold",))
        st.build_chat_moved()
        c = st.snapshot()["builds"]["chat"]
        self.assertEqual((c["cached"], c["built"], c["active_built"], c["bg_built"], c["moved"]), (1, 4, 1, 3, 1))
        self.assertAlmostEqual(c["ms"], 65.0)
        self.assertEqual({k: v for k, v in c["bg_miss"].items() if v},
                         {"store": 1, "states": 1, "transcript": 1, "cold": 1},
                         "one count per moved component: the two-component miss counts under both")
        self.assertEqual(set(c["bg_miss"]), set(km._PerfStats.CHAT_MISS))
        st.build_chat(False, 0.001, miss=("cold",))
        self.assertEqual(c["bg_miss"]["cold"], 1, "the snapshot is a copy; a later write does not move it")
        s = st.snapshot()["builds"]
        self.assertEqual(set(s["timeline"]), {"cached", "built", "ms"}, "no split on the other kinds")
        json.dumps(s)
        st.build("chat", False, 0.001)                        # the plain writer still serves the kind, unattributed
        c2 = st.snapshot()["builds"]["chat"]
        self.assertEqual((c2["built"], c2["active_built"] + c2["bg_built"]), (6, 5))

    def test_the_four_memos_report_under_memos(self):
        snap = km._PerfStats().snapshot()["memos"]
        self.assertEqual(set(snap["chatMergeSets"]), {"hit", "miss", "entries", "floorAgeMaxS", "builtAboveFloor"})   # 5b's two counters
        self.assertEqual(set(snap["chatPostal"]), {"gate", "hit", "commit_new"})
        self.assertEqual(set(snap["chatLedger"]), {"hit", "miss", "bypass_live", "bypass_hold", "bypass_empty", "evict", "entries"})
        self.assertEqual(set(snap["chatFoldTasks"]), {"hit", "miss", "entries"})


class MemoBump(unittest.TestCase):
    """The memos' counters are bumped under the chat fold's lock: the pusher, the WS handlers and the
    backends all build, and a bare increment from two threads loses counts."""

    def test_concurrent_bumps_land_exactly(self):
        stats, n, per = {"hit": 0}, 8, 2000
        gate = threading.Barrier(n)

        def run():
            gate.wait()
            for _ in range(per):
                km._chat_memo_bump(stats, "hit")
        threads = [threading.Thread(target=run) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(stats["hit"], n * per)
        km._chat_memo_bump(stats, "new", 3)
        self.assertEqual(stats["new"], 3, "a missing key starts at zero; n adds n")

    def test_the_bump_waits_on_the_fold_caches_lock(self):
        stats, done = {"hit": 0}, threading.Event()
        with km._chat_fold_lock:
            t = threading.Thread(target=lambda: (km._chat_memo_bump(stats, "hit"), done.set()))
            t.start()
            t.join(0.2)
            self.assertEqual(stats["hit"], 0, "held: the bump has not landed")
        done.wait(5)
        self.assertEqual(stats["hit"], 1, "released: it lands")


# ── the real push over two tabs ──────────────────────────────────────────────────────────────────
class TwoTabAttribution(unittest.TestCase):
    """_push over two tabs, one watched: a rebuild counts under active_built or bg_built, and a background
    rebuild names the component that moved. The sweep at the end of the chat block drops the memos of
    tabs no longer shown (keeping this cycle's comment threads, like the fold prefixes)."""

    STUBS = ("NAMES", "_live_map", "_live_names", "_chat_tab_sessions", "build_session",
             "_cached_feed", "_cached_timeline", "build_timeline", "_fleet_view_sig", "_comments_frame",
             "_retry_parked_creates")

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = td.name
        names = Path(self.tmp) / "names"
        names.mkdir()
        for sid, nm in ((SID_A, "web"), (SID_B, "api")):
            (names / sid).write_text("%s\t/proj/TESTHOST/app\t#1EA1EB\twhite\n" % nm)
        self.tx = {sid: Path(self.tmp) / (sid + ".jsonl") for sid in (SID_A, SID_B)}
        for p in self.tx.values():
            p.write_text('{"type": "user"}\n')                 # exists: _chat_build_sig is a real signature
        self.saved = {nm: getattr(km, nm) for nm in self.STUBS}
        self.saved_state = (jd.STATE, dict(km._built_chat), dict(km._prev_chat_events),
                            dict(km._prev_chat_ledger), list(km._last_tab_order), km._judge_gen[0],
                            [set(km._thread_fold_keep[0]), set(km._thread_fold_keep[1])])
        jd._rebind_state(Path(self.tmp) / "state")
        for d in (jd.STATESDIR, jd.GOALDIR):
            d.mkdir(parents=True, exist_ok=True)
        km.NAMES = names
        self.live = {}
        km._live_map = lambda: dict(self.live)
        km._live_names = lambda tm: {"web": SID_A, "api": SID_B}
        km._chat_tab_sessions = lambda now, live_map: [{"sid": s, "name": n, "path": str(self.tx[s]), "anchor": s}
                                                  for s, n in ((SID_A, "web"), (SID_B, "api"))]
        km.build_session = self._build_session
        km._cached_feed = lambda now, live_map, sig, connect=False: {"working": [], "awaiting": [], "now": now}
        km._cached_timeline = lambda now, live_map, sig, connect=False: {"turns": {}, "judging": [], "messages": [], "now": now}
        km.build_timeline = lambda now, live_map, **kw: {"lanes": [], "now": now}
        km._fleet_view_sig = lambda now, live_map: {"probe": 1}
        km._comments_frame = lambda sid, live_map: None
        km._retry_parked_creates = lambda: None
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        self.built = []
        self.chat = {"app": "chat", "alive": True, "sent": {}, "active": SID_A, "send": lambda s: None}
        self.tl = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: None}

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        st, bc, pe, pl, lo, jg, keep = self.saved_state
        jd._rebind_state(st)
        km._built_chat.clear(); km._built_chat.update(bc)
        km._prev_chat_events.clear(); km._prev_chat_events.update(pe)
        km._prev_chat_ledger.clear(); km._prev_chat_ledger.update(pl)
        km._last_tab_order[:] = lo
        km._judge_gen[0] = jg
        km._thread_fold_keep[0], km._thread_fold_keep[1] = keep

    def _build_session(self, sid, now, live_map):
        self.built.append(sid)
        return {"type": "session", "id": sid, "name": "x", "events": [{"uuid": "e1", "type": "user"}],
                "ledger": None, "status": {"state": "waiting"}, "color": None}

    @staticmethod
    def _chat():
        return km._PERF_STATS.snapshot()["builds"]["chat"]

    @staticmethod
    def _delta(before, after):
        d = {k: after[k] - before[k] for k in ("cached", "built", "active_built", "bg_built")}
        d["bg_miss"] = {k: v - before["bg_miss"][k] for k, v in after["bg_miss"].items() if v - before["bg_miss"][k]}
        return d

    def test_active_background_and_the_named_cause_of_each_background_rebuild(self):
        c0 = self._chat()
        km._push([self.chat, self.tl])
        c1 = self._chat()
        self.assertEqual(sorted(self.built), sorted([SID_A, SID_B]))
        self.assertEqual(self._delta(c0, c1), {"cached": 0, "built": 2, "active_built": 1, "bg_built": 1,
                                               "bg_miss": {"cold": 1}}, "first cycle: the background tab is cold")
        km._push([self.chat, self.tl])
        c2 = self._chat()
        self.assertEqual(self._delta(c1, c2), {"cached": 2, "built": 0, "active_built": 0, "bg_built": 0, "bg_miss": {}},
                         "nothing moved: both tabs are served on the one key")
        with open(self.tx[SID_B], "a") as f:
            f.write('{"type": "assistant"}\n')
        km._push([self.chat, self.tl])
        c3 = self._chat()
        self.assertEqual(self._delta(c2, c3), {"cached": 1, "built": 1, "active_built": 0, "bg_built": 1,
                                               "bg_miss": {"transcript": 1}},
                         "the background tab's transcript grew; the watched one is served")
        (jd.GOALDIR / (SID_B + ".json")).write_text(json.dumps({"rompUuid": SID_B, "nodes": {}, "status": {}}))
        km._bump_judge_gen_if_changed()                    # the producer's own bump after a pass that moved a store
        km._push([self.chat, self.tl])
        c4 = self._chat()
        self.assertEqual(self._delta(c3, c4)["bg_miss"], {"store": 1},
                         "a judge pass that published this session's store rebuilds its tab under store")
        self.assertEqual(self._delta(c3, c4)["bg_built"], 1)
        km._judge_gen[0] += 1                              # a pass that moved nothing this world reads
        km._push([self.chat, self.tl])
        self.assertEqual(self._delta(c4, self._chat())["built"], 0, "the judge-pass counter alone rebuilds no tab")
        c4 = self._chat()
        with open(jd.STATESDIR / (SID_B + ".jsonl"), "a") as f:
            f.write('{"t": 1, "state": "idle"}\n')
        km._push([self.chat, self.tl])
        c5 = self._chat()
        self.assertEqual(self._delta(c4, c5), {"cached": 1, "built": 1, "active_built": 0, "bg_built": 1,
                                               "bg_miss": {"states": 1}}, "the background tab's states file grew")
        km._push([self.chat, self.tl])
        c6 = self._chat()
        self.assertEqual(self._delta(c5, c6), {"cached": 2, "built": 0, "active_built": 0, "bg_built": 0, "bg_miss": {}},
                         "both are served again once nothing moves")
        with open(self.tx[SID_A], "a") as f:
            f.write('{"type": "assistant"}\n')
        km._push([self.chat, self.tl])
        c7 = self._chat()
        self.assertEqual(self._delta(c6, c7), {"cached": 1, "built": 1, "active_built": 1, "bg_built": 0, "bg_miss": {}},
                         "the watched tab's own transcript grew: it rebuilds, under active_built, unattributed")

    def test_the_sweep_drops_the_merge_sets_of_a_sid_neither_shown_nor_alive(self):
        stray = "66666666-7777-8888-9999-aaaaaaaaaaa9"
        alive = "66666666-7777-8888-9999-aaaaaaaaaaa8"
        self.live = {alive: {"state": "idle"}}
        for sid in (stray, alive, SID_B):
            km._merge_sets_memo[sid] = ({"turns": []}, ())
        try:
            km._push([self.chat, self.tl])
            self.assertNotIn(stray, km._merge_sets_memo, "a sid that is neither a tab nor alive is evicted")
            self.assertIn(SID_B, km._merge_sets_memo, "a shown tab's entry stays")
            self.assertIn(alive, km._merge_sets_memo, "an alive session's entry stays: the feed and timeline merge it")
        finally:
            for sid in (stray, alive, SID_B):
                km._merge_sets_memo.pop(sid, None)

    def test_the_sweep_drops_the_ledger_memo_of_a_tab_no_longer_shown_and_keeps_this_cycles_threads(self):
        stray = "66666666-7777-8888-9999-aaaaaaaaaaa9"
        thread = "66666666-7777-8888-9999-aaaaaaaaaaa7"
        for sid in (stray, thread, SID_B):
            km._ledger_memo[sid] = (("seams", None, 0), {"turns": []}, {"nodes": {}}, [], [])
        km._thread_fold_keep[1].add(thread)                   # a comment thread built this cycle
        evict0 = km._ledger_memo_stats["evict"]
        try:
            km._push([self.chat, self.tl])
            self.assertNotIn(stray, km._ledger_memo, "the ledger memo of a tab no longer shown is evicted")
            self.assertIn(SID_B, km._ledger_memo, "a shown tab's entry stays")
            self.assertIn(thread, km._ledger_memo, "this cycle's thread stays, like its fold prefix")
            self.assertEqual(km._ledger_memo_stats["evict"], evict0 + 1, "one eviction counted")
            self.assertEqual(km._ledger_memo_report()["entries"], len(km._ledger_memo))
        finally:
            for sid in (stray, thread, SID_B):
                km._ledger_memo.pop(sid, None)

    def test_the_sweep_drops_the_task_fold_memo_on_the_same_keep_set(self):
        stray = "66666666-7777-8888-9999-aaaaaaaaaaa9"
        thread = "66666666-7777-8888-9999-aaaaaaaaaaa7"
        for sid in (stray, thread, SID_B):
            km._task_fold_memo[sid] = {}
        km._thread_fold_keep[1].add(thread)
        try:
            km._push([self.chat, self.tl])
            self.assertNotIn(stray, km._task_fold_memo, "the task fold memo of a tab no longer shown is evicted")
            self.assertIn(SID_B, km._task_fold_memo, "a shown tab's entry stays")
            self.assertIn(thread, km._task_fold_memo, "this cycle's thread stays")
            self.assertEqual(km._task_fold_report()["entries"], len(km._task_fold_memo))
        finally:
            for sid in (stray, thread, SID_B):
                km._task_fold_memo.pop(sid, None)


# ── the live merge's transcript-side sets ────────────────────────────────────────────────────────
class MergeSets(unittest.TestCase):
    """_merge_tx_sets: the sets _merge_live_atoms derives from the parsed session, memoized per sid on the
    session object's identity; the same object hits, a fresh parse misses, and the memoized sets are never
    written by a merge or by the backend's prune."""

    T = 1781100000

    def setUp(self):
        self._saved = (km.Sessions.__dict__["backend_for"], dict(km._merge_sets_memo), dict(km._merge_sets_stats))
        km._merge_sets_memo.clear()
        for k in km._merge_sets_stats:
            km._merge_sets_stats[k] = 0
        self.calls, self.live = [], []
        test = self

        class Fake:
            def live_atoms(self, sid):
                return list(test.live)

            def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
                test.calls.append((tx_uuids, tx_user_texts, human_floor))
        km.Sessions.backend_for = staticmethod(lambda sid: Fake())

    def tearDown(self):
        km.Sessions.backend_for = self._saved[0]
        km._merge_sets_memo.clear(); km._merge_sets_memo.update(self._saved[1])
        km._merge_sets_stats.clear(); km._merge_sets_stats.update(self._saved[2])

    @staticmethod
    def _user(text, uid, t, author="human"):
        return {"type": "user", "uuid": uid, "t": t, "author": author,
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}}

    @staticmethod
    def _assistant(uid, t, text=None):
        content = ([{"type": "text", "text": text}] if text is not None
                   else [{"type": "thinking", "thinking": "", "signature": "sig"}])   # a textless twin
        return {"type": "assistant", "uuid": uid, "t": t, "message": {"role": "assistant", "content": content}}

    def _session(self):
        T = self.T
        return {"turns": [
            {"id": "t1", "trigger": "u1", "t": T, "end": T + 10, "ended": True,
             "atoms": [self._user("tighten the notes-api search", "u1", T), self._assistant("a1", T + 10, "Done.")]},
            {"id": "t2", "trigger": "u2", "t": T + 20, "end": T + 30, "ended": True,
             "atoms": [self._user("ok", "u2", T + 20), self._user("ok", "u2b", T + 25),
                       self._assistant("a2", T + 30)]}]}

    def _unmemoized(self, session):
        """The derivation as _merge_live_atoms wrote it before the memo, over the same helpers."""
        tx_uuids = {a.get("uuid") for turn in session["turns"] for a in turn["atoms"] if a.get("uuid")}
        tx_text_uuids = {a.get("uuid") for turn in session["turns"] for a in turn["atoms"]
                         if a.get("uuid") and km._atom_prose_chars(a) > 0}
        tx_texts = {t for turn in session["turns"] for a in turn["atoms"] for t in km._atom_user_texts(a)}
        tx_text_t = {}
        for turn in session["turns"]:
            for a in turn["atoms"]:
                for t in km._atom_user_texts(a):
                    tx_text_t[t] = max(tx_text_t.get(t, 0), float(a.get("t") or 0))
        return (tx_uuids, tx_text_uuids, tx_texts, tx_text_t, km._human_turn_floor(session))

    def test_the_same_object_hits_a_fresh_parse_misses_and_sids_do_not_share(self):
        sess = self._session()
        s1 = km._merge_tx_sets(sess, SID_A)
        self.assertEqual((km._merge_sets_stats["hit"], km._merge_sets_stats["miss"]), (0, 1))
        self.assertIs(km._merge_tx_sets(sess, SID_A), s1, "the same parsed object: served, not derived")
        self.assertEqual((km._merge_sets_stats["hit"], km._merge_sets_stats["miss"]), (1, 1))
        again = self._session()                                     # equal content, a new parse object
        s2 = km._merge_tx_sets(again, SID_A)
        self.assertIsNot(s2, s1)
        self.assertEqual(s2, s1, "a re-parse of the same transcript derives the same sets")
        self.assertEqual(km._merge_sets_stats["miss"], 2)
        km._merge_tx_sets(again, SID_B)                             # another sid, the same object: its own entry
        self.assertEqual(km._merge_sets_stats["miss"], 3)
        self.assertEqual(km._merge_sets_report(), {"hit": 1, "miss": 3, "entries": 2, "floorAgeMaxS": 0, "builtAboveFloor": 0})   # no floor here:
        #                                                                                                        the two 5b counters stay at zero

    def test_the_sets_equal_the_unmemoized_derivation_and_the_three_sets_are_frozen(self):
        sess = self._session()
        got = km._merge_tx_sets(sess, SID_A)
        self.assertEqual(tuple(got), self._unmemoized(sess))
        self.assertEqual(got[0], {"u1", "a1", "u2", "u2b", "a2"})
        self.assertNotIn("a2", got[1], "the textless twin is not a text uuid")
        self.assertEqual(got[3]["ok"], self.T + 25, "a repeated text keeps its newest record time")
        self.assertEqual(got[4], self.T + 25)
        for i in range(3):
            self.assertIsInstance(got[i], frozenset, "set %d is frozen: a write raises instead of corrupting later hits" % i)
        self.assertIsInstance(got[3], dict, "tx_text_t stays a dict: the SDK backend's prune dispatches on isinstance(dict)")

    def test_a_merge_hands_prune_live_the_memoized_sets_and_a_withheld_uuid_makes_a_new_set(self):
        sess = self._session()
        sets = km._merge_tx_sets(sess, SID_A)
        # a live reply streaming under the textless twin's uuid: withheld from the prune, so the text stays
        self.live = [{"type": "assistant", "uuid": "a2", "t": self.T + 30,
                      "message": {"role": "assistant", "content": [{"type": "text", "text": "the explanation"}]}}]
        merged = km._merge_live_atoms(sess, SID_A)
        tx_uuids, tx_text_t, floor = self.calls[-1]
        self.assertEqual(tx_uuids, sets[0] - {"a2"})
        self.assertIsNot(tx_uuids, sets[0])
        self.assertIn("a2", sets[0], "the memoized set is unchanged by the withholding")
        self.assertIs(tx_text_t, sets[3])
        self.assertEqual(floor, sets[4])
        self.assertIn("the explanation", json.dumps(merged["turns"][-1]["atoms"]), "the live text is shown")
        self.assertIs(km._merge_tx_sets(sess, SID_A), sets, "and the entry still serves")
        # a live atom whose disk twin carries text: nothing withheld, the memoized set itself is handed over
        self.live = [{"type": "assistant", "uuid": "a1", "t": self.T + 10,
                      "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}}]
        km._merge_live_atoms(sess, SID_A)
        self.assertIs(self.calls[-1][0], sets[0])
        self.assertEqual(sets, km._merge_tx_sets(sess, SID_A), "prune_live wrote into nothing")
        self.assertEqual(km._merge_sets_stats["miss"], 1)

    def test_no_live_atoms_skips_the_sets(self):
        sess = self._session()
        self.assertIs(km._merge_live_atoms(sess, SID_A), sess)
        self.assertEqual(km._merge_sets_stats, {"hit": 0, "miss": 0, "floorAgeMaxS": 0, "builtAboveFloor": 0})

    def test_the_memo_is_bounded_one_eviction_at_a_time(self):
        sess = self._session()
        for i in range(km._MERGE_SETS_MAX + 3):
            km._merge_tx_sets(sess, "sid-%d" % i)
        self.assertEqual(len(km._merge_sets_memo), km._MERGE_SETS_MAX)
        self.assertNotIn("sid-0", km._merge_sets_memo, "the oldest entry went first")
        self.assertIn("sid-%d" % (km._MERGE_SETS_MAX + 2), km._merge_sets_memo)


# ── the sealed postal cards' values and the caption map ──────────────────────────────────────────
class PostalCardDeps(unittest.TestCase):
    """_postal_card_deps: per sealed card, exactly the values it embeds from outside the transcript."""

    def setUp(self):
        self._names = getattr(km._live_scope, "names", None)
        km._live_scope.names = {PEER: ["api", "/tmp/notes-api", "#abcdef"]}

    def tearDown(self):
        km._live_scope.names = self._names

    def test_each_embedded_value_is_a_component_and_nothing_else_is(self):
        index = {MID: {"id": MID, "from": "api", "fromId": PEER, "toId": SID_A, "body": "b",
                       "kind": "coordinate", "t": 1, "park": False}}
        colour = {"bg": "#abcdef", "fg": "#ffffff"}
        cards = [{"kind": "postal-service", "direction": "in", "peer": "api", "mid": MID, "summary": "cap", "body": "b"},
                 {"kind": "postal-service", "direction": "out", "peer": "api", "mid": MID, "body": "b"},
                 {"kind": "postal-service", "direction": "out", "peer": "tests", "body": "no row joined"},
                 {"kind": "tool", "name": "Bash", "output": "a raw event that did not hydrate"}]
        calls = []

        def caps():
            calls.append(1)
            return {MID: "cap"}
        deps = km._postal_card_deps(cards, index, caps)
        self.assertEqual(deps, ((MID, "cap", "api", colour), (MID, "cap", colour), (None, None, None), None))
        self.assertEqual(len(calls), 1, "the caption map is fetched once, and only because a card carries a mid")
        km._live_scope.names = {PEER: ["api", "/tmp/notes-api", "#000000"]}      # the sender's colour
        self.assertNotEqual(km._postal_card_deps(cards, index, caps), deps)
        km._live_scope.names = {PEER: ["renamed", "/tmp/notes-api", "#abcdef"]}  # the sender's name
        self.assertNotEqual(km._postal_card_deps(cards, index, caps), deps)
        km._live_scope.names = {PEER: ["api", "/tmp/notes-api", "#abcdef"]}
        self.assertNotEqual(km._postal_card_deps(cards, index, lambda: {MID: "other"}), deps, "the caption")
        self.assertEqual(km._postal_card_deps(cards, index, caps), deps, "the same inputs, the same tuple")
        self.assertEqual(km._postal_card_deps([cards[2], cards[3]], index, lambda: self.fail("no mid, no map")),
                         ((None, None, None), None))


class MsgSummariesKey(unittest.TestCase):
    """_msg_summaries' per-session submap is keyed on every input its scan reads: the parse's key (the
    transcript's stat, the pending cut, the states file), the captions file and the goal store (the
    store, its override journal, its archive)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))
        self.saved = (km._sessions, km._msg_sum_scan_session, dict(km._msg_sum_cache), km._sdk)
        km._msg_sum_cache.clear()
        km._sdk = lambda: None
        self.scanned = []
        km._msg_sum_scan_session = lambda sid, path, now: (self.scanned.append(sid) or {sid + ":m": "cap"})
        self.rows = [{"sid": SID_A, "name": "web", "path": "/x/a", "mtime": 100},
                     {"sid": SID_B, "name": "api", "path": "/x/b", "mtime": 100}]
        km._sessions = lambda now: list(self.rows)
        for d in (jd.CAPDIR, jd.GOALDIR, jd.GOALARCHDIR, jd._overrides_dir(), jd.STATESDIR):
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        km._sessions, km._msg_sum_scan_session = self.saved[:2]
        km._msg_sum_cache.clear(); km._msg_sum_cache.update(self.saved[2])
        km._sdk = self.saved[3]
        jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def _scan(self):
        self.scanned.clear()
        km._msg_summaries()
        return list(self.scanned)

    def test_each_input_change_rescans_that_session_only_and_unchanged_inputs_rescan_nothing(self):
        self.assertEqual(sorted(self._scan()), sorted([SID_A, SID_B]), "the first call scans everything")
        self.assertEqual(self._scan(), [], "unchanged inputs: no rescan")
        (jd.CAPDIR / (SID_A + ".jsonl")).write_text(json.dumps({"id": "seg", "caption": "c"}) + "\n")
        self.assertEqual(self._scan(), [SID_A], "a caption written with no transcript change rescans its session")
        self.assertEqual(self._scan(), [])
        (jd.GOALDIR / (SID_B + ".json")).write_text(json.dumps({"nodes": {}, "status": {}, "seams": [1]}))
        self.assertEqual(self._scan(), [SID_B], "a store publish (its seams) rescans its session")
        with open(jd._overrides_dir() / (SID_B + ".jsonl"), "a") as f:
            f.write('{"op": "reopen"}\n')
        self.assertEqual(self._scan(), [SID_B], "a journaled user gesture rescans its session")
        (jd.GOALARCHDIR / (SID_A + ".json")).write_text("{}")
        self.assertEqual(self._scan(), [SID_A], "an archive write rescans its session")
        self.rows[0] = {**self.rows[0], "mtime": 200}
        self.assertEqual(self._scan(), [SID_A], "the row's mtime still keys when the transcript cannot be stat'd")
        (jd.STATESDIR / (SID_B + ".jsonl")).write_text('{"state": "idle", "t": 1}\n')
        self.assertEqual(self._scan(), [SID_B], "a states row (an idle atom in the parse) rescans its session")
        self.assertEqual(self._scan(), [])
        self.assertEqual(km._msg_summaries(), {SID_A + ":m": "cap", SID_B + ":m": "cap"})

    def test_the_transcripts_size_and_the_pending_cut_key_too(self):
        tx = Path(self.td.name) / "a.jsonl"
        tx.write_text('{"type": "user"}\n')
        self.rows[0] = {**self.rows[0], "path": str(tx)}
        cut = {"value": ""}

        class Fake:
            def pending_cut(self, sid):
                return cut["value"] if sid == SID_A else ""
        km._sdk = lambda: Fake()
        self._scan()
        self.assertEqual(self._scan(), [])
        with open(tx, "a") as f:
            f.write('{"type": "assistant"}\n')                 # the size moves even where the mtime's clock does not
        self.assertEqual(self._scan(), [SID_A], "the transcript's size keys")
        cut["value"] = "uuid-of-the-cut"                     # a pending chat delete changes the parse with no file change
        self.assertEqual(self._scan(), [SID_A], "the backend's pending cut keys")
        self.assertEqual(self._scan(), [])

    def test_the_key_is_taken_before_the_scan(self):
        # a caption appended while the scan runs pairs the OLD key with the new content: one more rescan on
        # the next call, never a stale hit. Proven by writing the caption from inside the scan.
        def scanning(sid, path, now):
            self.scanned.append(sid)
            if sid == SID_A and not (jd.CAPDIR / (SID_A + ".jsonl")).exists():
                (jd.CAPDIR / (SID_A + ".jsonl")).write_text(json.dumps({"id": "seg", "caption": "c"}) + "\n")
            return {sid + ":m": "cap"}
        km._msg_sum_scan_session = scanning
        self._scan()
        self.assertEqual(self._scan(), [SID_A], "the write that landed mid-scan is seen on the next call")
        self.assertEqual(self._scan(), [])


class CycleCaptionSlot(unittest.TestCase):
    """The caption map is fetched once per pusher cycle: _pusher_cycle opens a slot on the cycle's scope,
    the first build that needs the map fills it, later builds of the cycle read it, the timeline's postal
    connectors read the same slot, and the finally closes it. A thread with no cycle scope (a connect
    push) reads the map directly."""

    def setUp(self):
        self.saved = (km._live_map, km._pusher_cycle_jobs, km._msg_summaries, jd.STATE,
                      dict(km._postal_log_cache))
        self.fetched = []
        km._live_map = lambda: {}
        km._msg_summaries = lambda: (self.fetched.append(1) or {MID: "cap"})
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        jd._rebind_state(Path(td.name))                      # the timeline's postal log lives under the state root
        km._postal_log_cache.clear()

    def tearDown(self):
        km._live_map, km._pusher_cycle_jobs, km._msg_summaries, state, log = self.saved
        jd._rebind_state(state)
        km._postal_log_cache.clear(); km._postal_log_cache.update(log)
        km._live_scope.msgsum = None

    def _connectors(self, now):
        return km._postal_messages(now, {SID_A, PEER}, {SID_A: "web", PEER: "api"})

    def test_reads_within_one_cycle_fetch_once_and_the_slot_closes_with_the_cycle(self):
        seen = []

        def jobs(now, live_map, any_client):
            seen.append(getattr(km._live_scope, "msgsum", None))
            first = km._msg_summaries_scoped()
            for _ in range(4):
                self.assertIs(km._msg_summaries_scoped(), first, "the cycle's map, not a fresh fetch")
        km._pusher_cycle_jobs = jobs
        km._pusher_cycle()
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0], "the slot was open during the jobs")
        self.assertEqual(len(self.fetched), 1, "one fetch for the whole cycle")
        self.assertIsNone(getattr(km._live_scope, "msgsum", None), "closed with the cycle")
        for _ in range(3):
            km._msg_summaries_scoped()                          # no scope: the direct path
        self.assertEqual(len(self.fetched), 4, "three direct reads fetch three times")

    def test_the_slot_closes_when_the_jobs_raise(self):
        seen = []

        def jobs(now, live_map, any_client):
            seen.append(getattr(km._live_scope, "msgsum", None))
            raise RuntimeError("a job failed")
        km._pusher_cycle_jobs = jobs
        with self.assertRaises(RuntimeError):
            km._pusher_cycle()
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0], "the slot was open during the jobs")
        self.assertIsNone(getattr(km._live_scope, "msgsum", None), "closed by the finally")

    def test_the_timelines_postal_connectors_read_the_cycles_map(self):
        now = int(time.time())
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd.MESSAGES.write_text(json.dumps({"ev": "sent", "id": MID, "from": "api", "from_id": PEER, "to_id": SID_A,
                                           "body": "the api tests are green now", "kind": "coordinate",
                                           "t": now - 60}) + "\n")
        rows = []

        def jobs(now_, live_map, any_client):
            km._msg_summaries_scoped()                          # a chat build fetched the cycle's map
            rows.append(self._connectors(now))                  # the timeline build's connectors, twice
            rows.append(self._connectors(now))
        km._pusher_cycle_jobs = jobs
        km._pusher_cycle()
        self.assertEqual(len(self.fetched), 1, "the connectors read the cycle's map, not a fresh fetch")
        self.assertEqual([r[0]["summary"] for r in rows], ["cap", "cap"], "each connector carries the caption")
        self._connectors(now)
        self.assertEqual(len(self.fetched), 2, "no scope: the direct path")


# ── build_session over a synthetic session ───────────────────────────────────────────────────────
def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}, "cwd": "/tmp/notes-api", "version": "2.1.0", "gitBranch": "main"}


def _aline(t, text, uuid, parent, tool=None, stop="end_turn"):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "id": "tu_%s" % uuid, "name": tool, "input": {"command": "uv run pytest -q"}})
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "model": "claude-sonnet-4", "content": content, "stop_reason": stop},
            "cwd": "/tmp/notes-api", "version": "2.1.0", "gitBranch": "main"}


def _trline(t, tool_use_id, uuid, parent, content="ok\n"):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}]}}


class World:
    """A synthetic session build_session can build: names/ + projects/<cdir>/<sid>.jsonl under a state root
    the kernel's judge module is rebound to (tests/test_chat_fold.py's Sess, reduced), plus a goal-store
    writer and a postal log. The live tail is the owning backend's (nobody's here: an unowned sid has none)."""

    def __init__(self, sid):
        self.sid = sid
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.cdir = td / "launchdir"
        self.cdir.mkdir()
        proj = td / "projects"
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(self.cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (sid + ".jsonl")
        self.tpath.write_text("")
        self.saved_state = jd.STATE
        self.saved = (jd.PROJECTS, km.NAMES, km._live_map, km._GLOBAL_CLAUDE_MD, km._msg_summaries, km._sdk,
                      os.environ.get("CLAUDE_CONFIG_DIR"))
        jd._rebind_state(td)                              # names, goals, captions, archive, states, messages: all under td
        jd.PROJECTS = proj
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / sid).write_text("web\t%s\t#abcdef\n" % str(self.cdir))
        km.NAMES = jd.NAMES
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        self.now = int(time.time())                       # discovery keys on the real clock
        self.t = self.now - 3 * 86400
        self.tm = {sid: {"state": "working", "since": self.now - 100, "model": "", "effort": "",
                         "context": None, "compactPct": None, "color": None}}
        km._live_map = lambda: self.tm
        km._msg_summaries = lambda: {}
        km._sdk = lambda: None
        os.environ["CLAUDE_CONFIG_DIR"] = str(td / "claude")   # no real task store is read
        _clear_memos()
        km._parse_cache.clear(); km._PATH_LINK_CACHE.clear(); km._SPACE_PATH_CACHE.clear()
        km._postal_index_memo[0] = None
        if isinstance(jd._discover_cache, dict):
            jd._discover_cache.clear()
        self.n = 0
        self.last = None

    def close(self):
        km._rewind_hold_clear(self.sid)
        (jd.PROJECTS, km.NAMES, km._live_map, km._GLOBAL_CLAUDE_MD, km._msg_summaries, km._sdk, cfg) = self.saved
        if cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = cfg
        jd._rebind_state(self.saved_state)
        _clear_memos()
        km._parse_cache.clear()
        km._postal_index_memo[0] = None
        self.td.cleanup()

    def uid(self):
        self.n += 1
        return "bbbbbbbb-0000-0000-0000-%012d" % self.n

    def tick(self, dt=5):
        self.t += dt
        return self.t

    def turn(self, i):
        """One complete turn: prompt, one Bash round (use + result), a closing reply."""
        u = self.uid()
        recs = [_uline(self.tick(), "step %d: tighten the notes-api search" % i, u, self.last)]
        a = self.uid()
        recs.append(_aline(self.tick(), "Round %d: adjusting `search.py`." % i, a, u, tool="Bash", stop="tool_use"))
        r = self.uid()
        recs.append(_trline(self.tick(), "tu_%s" % a, r, a))
        b = self.uid()
        recs.append(_aline(self.tick(), "Step %d done." % i, b, r))
        self.last = b
        return recs

    def append(self, recs):
        with open(self.tpath, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        os.utime(self.tpath, None)

    def store(self, nodes, seams=None, last=None):
        """Publish goals/<sid>.json by rename, as save_goals does."""
        p = jd.GOALDIR / (self.sid + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"nodes": nodes, "status": {}, "seams": seams or [], "lastNode": last}))
        os.replace(tmp, p)

    def nodes(self, n, trail=(), parent=None):
        return {"g%d" % i: {"id": "g%d" % i, "text": "goal %d" % i, "t": self.t + i, "mt": self.t + i,
                            "parentId": parent, "trail": list(trail)} for i in range(1, n + 1)}

    def postal_log(self, rows):
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        with open(jd.MESSAGES, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def build(self, scoped=True):
        """build_session under the pusher's names scope (as _pusher_cycle sets it); scoped=False is a
        handler-thread build, which reads the registry per card."""
        if scoped:
            km._live_scope.names = km._names_snapshot()
        try:
            return km.build_session(self.sid, self.now, self.tm)
        finally:
            km._live_scope.names = None


def _dump(m):
    return json.dumps(m, sort_keys=True, default=str)


class PostalGate(unittest.TestCase):
    """The fold gate keys a tab's sealed postal cards on the values they embed (this session's postal revision
    and, per card, its caption and its peer's name and colour), not on the judge generation or the log's
    identity: a judge pass that moved none of them re-hydrates nothing, mail between two other sessions
    re-hydrates nothing, a caption change re-hydrates exactly the sealed cards, and the commit hydrates only
    the raw events new since the seal."""

    def setUp(self):
        self.w = World(SID_A)
        self.caps = {}
        km._msg_summaries = lambda: dict(self.caps)
        self.w.postal_log([{"ev": "sent", "id": MID, "from": "api", "from_id": PEER, "to_id": SID_A,
                            "body": "the api tests are green now", "kind": "coordinate", "t": self.w.t}])
        self.judge_gen = km._judge_gen[0]

    def tearDown(self):
        km._judge_gen[0] = self.judge_gen
        self.w.close()

    def _incoming(self):
        """One sealed incoming card: a delivered peer message, the reply, then a complete turn after it."""
        w = self.w
        u = w.uid()
        w.append([_uline(w.tick(), "<!-- romp-msg-id: %s -->\nthe api tests are green now" % MID, u, w.last)])
        a = w.uid()
        w.append([_aline(w.tick(), "Noted, thanks.", a, u)])
        w.last = a
        w.append(w.turn(2))

    def _spy(self):
        """Every _hydrate_postal call build_session makes from here on, as the event lists it was handed."""
        calls = []
        orig = km._hydrate_postal

        def counting(events, *a, **kw):
            calls.append(list(events))
            return orig(events, *a, **kw)
        km._hydrate_postal = counting
        self.addCleanup(setattr, km, "_hydrate_postal", orig)
        return calls

    @staticmethod
    def _card(m):
        return next(ev for ev in m["events"] if ev.get("kind") == "postal-service")

    @staticmethod
    def _stats():
        # zeros on a tree without the counters, so the behavioural assertions run first
        return dict(getattr(km, "_chat_postal_stats", None) or {"gate": 0, "hit": 0, "commit_new": 0})

    def test_a_judge_pass_that_moved_no_caption_leaves_the_sealed_card_alone(self):
        w = self.w
        self.caps[MID] = "api: tests green"
        w.append(w.turn(0))
        self._incoming()
        m = w.build()
        self.assertEqual(self._card(m)["summary"], "api: tests green")
        w.build()                                                  # warm: the card is sealed in the prefix
        raw = km._chat_fold_get(SID_A)["postal_raw"]
        self.assertEqual(len(raw), 1, "the marker event rides the entry raw")
        calls = self._spy()
        s0 = self._stats()
        km._judge_gen[0] += 1                                      # a judge pass: some store moved
        m2 = w.build()
        self.assertEqual([any(e is raw[0] for e in c) for c in calls], [False],
                         "one hydration, the tail pass: the sealed card was neither re-hydrated by the gate "
                         "nor hydrated again at the commit")
        self.assertEqual(self._card(m2)["summary"], "api: tests green")
        s1 = self._stats()
        self.assertEqual((s1["gate"] - s0["gate"], s1["hit"] - s0["hit"], s1["commit_new"] - s0["commit_new"]), (0, 1, 0))
        # equivalence with a cold build stands
        km._chat_fold.clear()
        self.assertEqual(_dump(w.build()), _dump(m2))

    def test_a_caption_change_re_hydrates_exactly_the_sealed_card_and_refreshes_it(self):
        w = self.w
        self.caps[MID] = "api: tests green (live)"
        w.append(w.turn(0))
        self._incoming()
        w.build()
        w.build()
        entry = km._chat_fold_get(SID_A)
        self.assertEqual(entry["postal_deps"], ((MID, "api: tests green (live)", None, None),),
                         "the entry records the values its card embeds: mid, caption, the sender's name and colour")
        raw = entry["postal_raw"]
        calls = self._spy()
        s0 = self._stats()
        n0 = km._CHAT_FOLD_STATS.get("g:postal", 0)
        self.caps[MID] = "api: tests green"                       # the final caption lands
        m = w.build()
        self.assertEqual(self._card(m)["summary"], "api: tests green")
        self.assertEqual([any(e is raw[0] for e in c) for c in calls][0], True, "the gate re-hydrated the sealed card")
        self.assertEqual(km._chat_postal_stats["gate"] - s0["gate"], 1)
        self.assertGreater(km._CHAT_FOLD_STATS.get("g:postal", 0), n0, "a different card demotes the prefix")
        self.assertEqual(km._chat_fold_get(SID_A)["postal_deps"], ((MID, "api: tests green", None, None),))
        km._chat_fold.clear()
        self.assertEqual(_dump(w.build()), _dump(m), "equal to a cold build")

    def test_a_peers_colour_refreshes_the_sealed_card_and_a_caption_for_another_message_does_not(self):
        w = self.w
        self.caps[MID] = "api: tests green"
        w.append(w.turn(0))
        self._incoming()
        w.build()
        m = w.build()
        self.assertIsNone(self._card(m).get("color"), "the sender has no registry entry yet")
        s0 = self._stats()
        self.caps["1788400000.200_2.TESTHOST"] = "api: something else"
        w.build()
        self.assertEqual((km._chat_postal_stats["gate"] - s0["gate"], km._chat_postal_stats["hit"] - s0["hit"]), (0, 1),
                         "a caption for another message moves nothing this card embeds")
        (jd.NAMES / PEER).write_text("api\t%s\t#123456\n" % str(w.cdir))
        m2 = w.build()
        self.assertEqual(self._card(m2).get("color"), {"bg": "#123456", "fg": "#ffffff"})
        self.assertEqual(km._chat_postal_stats["gate"] - s0["gate"], 1, "the sender's colour is a value the card embeds")
        self.assertEqual(km._chat_fold_get(SID_A)["postal_deps"],
                         ((MID, "api: tests green", "api", {"bg": "#123456", "fg": "#ffffff"}),))

    def test_a_build_outside_the_names_scope_seals_unverified_and_the_next_scoped_build_verifies_once(self):
        # a handler-thread build (a connect push) reads the registry per card, so the values it embeds and
        # the values it would record are two reads: it records None, and the pusher's next build, which reads
        # one snapshot, re-hydrates once and records what it saw
        w = self.w
        self.caps[MID] = "api: tests green"
        w.append(w.turn(0))
        self._incoming()
        w.build(scoped=False)
        w.build(scoped=False)
        self.assertIsNone(km._chat_fold_get(SID_A)["postal_deps"], "sealed unverified")
        s0 = self._stats()
        w.build()
        self.assertEqual((km._chat_postal_stats["gate"] - s0["gate"], km._chat_postal_stats["hit"] - s0["hit"]), (1, 0),
                         "the scoped build re-hydrated once")
        self.assertIsNotNone(km._chat_fold_get(SID_A)["postal_deps"])
        w.build()
        self.assertEqual((km._chat_postal_stats["gate"] - s0["gate"], km._chat_postal_stats["hit"] - s0["hit"]), (1, 1),
                         "and from then on the recorded values verify it")

    def test_the_commit_hydrates_only_the_raw_events_new_since_the_seal(self):
        w = self.w
        self.caps[MID] = "api: tests green"
        w.append(w.turn(0))
        self._incoming()
        w.build()
        w.build()
        s0 = self._stats()
        calls = self._spy()
        # a second delivery of the same message id, then a turn to seal it behind
        u = w.uid()
        w.append([_uline(w.tick(), "<!-- romp-msg-id: %s -->\nthe api tests are green now" % MID, u, w.last)])
        a = w.uid()
        w.append([_aline(w.tick(), "Seen.", a, u)])
        w.last = a
        w.append(w.turn(4))
        m = w.build()
        self.assertEqual(len([e for e in m["events"] if e.get("kind") == "postal-service"]), 2)
        self.assertEqual(km._chat_postal_stats["commit_new"] - s0["commit_new"], 1,
                         "one raw event was new since the seal, and one was hydrated at the commit")
        raw = km._chat_fold_get(SID_A)["postal_raw"]
        self.assertEqual(len(raw), 2)
        commit_calls = [c for c in calls if c and any(e is raw[1] for e in c) and len(c) == 1]
        self.assertEqual(len(commit_calls), 1, "the commit's hydration carried the new raw event alone")
        km._chat_fold.clear()
        self.assertEqual(_dump(w.build()), _dump(m), "equal to a cold build")

    def test_mail_between_two_other_sessions_verifies_the_sealed_card_without_re_hydrating(self):
        """The gate keys the sealed cards on this session's postal revision (2026-09-18), not the log's
        identity: a row between two other sessions moves the log and nothing the card embeds, so the recorded
        values verify it (a hit); a row addressed to this session re-hydrates once."""
        w = self.w
        self.caps[MID] = "api: tests green"
        w.append(w.turn(0))
        self._incoming()
        w.build()
        w.build()                                                  # the card is sealed and verified
        raw = km._chat_fold_get(SID_A)["postal_raw"]
        s0 = self._stats()
        calls = self._spy()
        w.postal_log([{"ev": "sent", "id": "1788400000.300_3.TESTHOST", "from_id": OTHER_A, "to_id": OTHER_B,
                       "body": "unrelated", "kind": "coordinate", "t": w.t}])
        m = w.build()
        self.assertEqual((km._chat_postal_stats["gate"] - s0["gate"], km._chat_postal_stats["hit"] - s0["hit"]), (0, 1),
                         "mail between two other sessions: the recorded values verify the sealed card")
        self.assertEqual([any(e is raw[0] for e in c) for c in calls], [False],
                         "the tail pass only: the sealed card was not re-hydrated")
        self.assertEqual(self._card(m)["summary"], "api: tests green")
        w.postal_log([{"ev": "sent", "id": "1788400000.400_4.TESTHOST", "from_id": PEER, "to_id": SID_A,
                       "body": "and the docs", "kind": "coordinate", "t": w.t}])
        w.build()
        self.assertEqual((km._chat_postal_stats["gate"] - s0["gate"], km._chat_postal_stats["hit"] - s0["hit"]), (1, 1),
                         "a record addressed to this session re-hydrates the sealed card once")


class PostalSidRevs(unittest.TestCase):
    """_postal_sid_revs: per session, (n, last_mid, outcomes) over the records addressed to or from it, the
    outcomes folded by VALUE per record; the records with no recipient under ""; _postal_sid_revs_of serving
    the memoized index's table by identity; and _chat_postal_rev reading a session's pair (2026-09-18)."""

    @staticmethod
    def _rec(mid, frm, to, t, **outs):
        r = {"id": mid, "from": "api", "fromId": frm, "toId": to, "body": "b", "kind": "coordinate", "t": t, "park": False}
        r.update(outs)
        return r

    def test_each_key_folds_its_records_and_their_outcome_values(self):
        idx = {"m1": self._rec("m1", PEER, SID_A, 1),
               "m2": self._rec("m2", SID_A, PEER, 2),
               "m3": self._rec("m3", OTHER_A, OTHER_B, 3, read=30),
               "m4": self._rec("m4", "", "", 4),
               "m5": self._rec("m5", "", SID_B, 5),             # the bus's own return note: no sender, one recipient
               "m6": self._rec("m6", SID_B, SID_B, 6)}          # self-addressed: counted once
        revs = km._postal_sid_revs(idx)
        self.assertEqual(revs[SID_A], (2, "m2", ()))
        self.assertEqual(revs[PEER], (2, "m2", ()))
        self.assertEqual(revs[OTHER_A], (1, "m3", (("m3", 30, None, None, None, None),)))
        self.assertEqual(revs[OTHER_B], revs[OTHER_A])
        self.assertEqual(revs[""], (1, "m4", ()), "the bucket holds the records with no recipient alone")
        self.assertEqual(revs[SID_B], (2, "m6", ()), "a note with no sender keys its recipient; a self-addressed row counts once")
        self.assertEqual(set(revs), {SID_A, PEER, OTHER_A, OTHER_B, "", SID_B})
        idx["m2"]["read"] = 20                                    # an outcome landing on this session's own message
        r2 = km._postal_sid_revs(idx)
        self.assertEqual(r2[SID_A], (2, "m2", (("m2", 20, None, None, None, None),)))
        self.assertEqual(r2[PEER], r2[SID_A])
        self.assertEqual(r2[OTHER_A], revs[OTHER_A], "a third party's entry is untouched")
        del idx["m2"]["read"]                                     # read then un-read with no build between: the receipt
        self.assertEqual(km._postal_sid_revs(idx), revs)         # is back where it was, and so is the revision
        idx["m2"]["read"] = 25                                    # read again at another time: the card renders the time
        self.assertNotEqual(km._postal_sid_revs(idx)[SID_A], r2[SID_A])
        idx["m1"].update(bounced=11, bouncedWhy="no such mailbox")
        r3 = km._postal_sid_revs(idx)
        self.assertEqual(r3[SID_A][2], (("m1", None, None, 11, None, "no such mailbox"), ("m2", 25, None, None, None, None)),
                         "every outcome and the bounce's why are values, in the log's order")
        idx["m1"]["bouncedWhy"] = "mailbox closed"
        self.assertNotEqual(km._postal_sid_revs(idx)[SID_A], r3[SID_A], "the why is rendered, so it is folded")

    def test_opposite_outcomes_on_two_messages_do_not_net_to_no_change(self):
        idx = {"m10": self._rec("m10", SID_A, PEER, 1, read=10), "m12": self._rec("m12", SID_A, PEER, 2)}
        a = km._postal_sid_revs(idx)[SID_A]
        del idx["m10"]["read"]
        idx["m12"]["read"] = 12                                   # an unexec on one, an exec on the other
        b = km._postal_sid_revs(idx)[SID_A]
        self.assertEqual((a[0], b[0]), (2, 2))
        self.assertNotEqual(a, b, "a count of outcomes would read 1 both times; the values differ")

    def test_the_memoized_index_carries_one_table_per_version_and_a_callers_dict_gets_its_own(self):
        w = World(SID_A)
        try:
            w.postal_log([{"ev": "sent", "id": MID, "from": "api", "from_id": PEER, "to_id": SID_A, "body": "b",
                           "kind": "coordinate", "t": w.t}])
            idx = km._postal_index()
            revs = km._postal_sid_revs_of(idx)
            self.assertIs(revs, km._postal_index_memo[0][3], "the memo entry carries the table")
            self.assertIs(km._postal_sid_revs_of(km._postal_index()), revs, "one table per index version, not per call")
            self.assertEqual(revs, km._postal_sid_revs(idx))
            self.assertEqual(km._chat_postal_rev(SID_A, idx), ((1, MID, ()), None))
            self.assertEqual(km._chat_postal_rev(OTHER_A, idx), (None, None), "a session party to no message: no revision")
            own = dict(idx)
            self.assertIsNot(km._postal_sid_revs_of(own), revs, "a caller's own dict never borrows the memo's table")
            self.assertEqual(km._postal_sid_revs_of(own), revs)
            w.postal_log([{"ev": "exec", "id": MID, "t": w.t + 1}])
            idx2 = km._postal_index()
            self.assertIsNot(idx2, idx, "the log grew: a new index version")
            revs2 = km._postal_sid_revs_of(idx2)
            self.assertIs(revs2, km._postal_index_memo[0][3])
            self.assertEqual(km._chat_postal_rev(SID_A, idx2), ((1, MID, ((MID, w.t + 1, None, None, None, None),)), None))
            self.assertEqual(km._chat_postal_rev(OTHER_A, idx2), (None, None))
        finally:
            w.close()


class LedgerMemo(unittest.TestCase):
    """The goal-tree walk and the live roots, memoized per session on every input of the walk: the parsed
    transcript (by identity, with no live atoms merged), the store (by identity) and its seams,
    cleared.jsonl (its stat, taken before the read) and the warm-anchor table's per-session revision."""

    def setUp(self):
        self.w = World(SID_A)
        self.w.append(self.w.turn(0))
        self.w.append(self.w.turn(1))

    def tearDown(self):
        self.w.close()

    @staticmethod
    def _stats():
        return dict(km._ledger_memo_stats)

    @staticmethod
    def _delta(before):
        now = km._ledger_memo_stats
        return {k: now[k] - before[k] for k in now if now[k] != before[k]}

    def test_the_session_s_repo_is_in_the_key_and_filters_each_row_s_prs(self):
        """A goal's PR chips show only refs in the session's own repo, so a session whose repo changes must
        re-walk rather than serve rows filtered for the old one."""
        w = self.w
        nodes = w.nodes(2)
        nodes["g1"]["prRefs"] = [["notes-api-org/notes-api", 12], ["someone-else/other", 9]]
        w.store(nodes)
        repo = ["notes-api-org/notes-api"]
        with mock.patch.object(km.gp, "repo_of", lambda cwd: repo[0]):
            row = next(r for r in w.build()["ledger"]["tree"] if r["id"] == "g1")
            self.assertEqual(row["prNums"], [12], "the walk stamps the session repo's refs on the row")
            s = self._stats()
            w.build()
            self.assertEqual(self._delta(s), {"hit": 1})
            repo[0] = "someone-else/other"
            s = self._stats()
            row = next(r for r in w.build()["ledger"]["tree"] if r["id"] == "g1")
            self.assertEqual(self._delta(s), {"miss": 1}, "a new repo is a new key")
            self.assertEqual(row["prNums"], [9])

    def test_unchanged_inputs_hit_and_each_input_change_misses_once(self):
        w = self.w
        s = self._stats()
        self.assertEqual(w.build()["ledger"]["tree"], [])
        self.assertEqual(self._delta(s), {"bypass_empty": 1}, "no store: nothing to walk, nothing to keep")
        w.store(w.nodes(3))
        s = self._stats()
        m = w.build()
        self.assertEqual([r["id"] for r in m["ledger"]["tree"]], ["g3", "g2", "g1"], "freshest first")
        self.assertEqual(self._delta(s), {"miss": 1})
        s = self._stats()
        m2 = w.build()
        self.assertEqual(self._delta(s), {"hit": 1})
        self.assertEqual(_dump(m2["ledger"]), _dump(m["ledger"]))
        km._ledger_memo.clear()
        self.assertEqual(_dump(w.build()["ledger"]), _dump(m["ledger"]), "the served ledger equals a fresh walk")
        # a store publish
        w.store(w.nodes(4))
        s = self._stats()
        self.assertEqual(len(w.build()["ledger"]["tree"]), 4)
        self.assertEqual(self._delta(s), {"miss": 1})
        # a journaled user gesture (the shared store's identity carries its override journal)
        jd._overrides_dir().mkdir(parents=True, exist_ok=True)
        with open(jd._overrides_dir() / (SID_A + ".jsonl"), "a") as f:
            f.write(json.dumps({"op": "resolve", "node": "absent", "t": w.now}) + "\n")
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"miss": 1})
        # a clear
        with open(jd.STATE / "cleared.jsonl", "a") as f:
            f.write(json.dumps({"id": "g1", "t": w.now}) + "\n")
        s = self._stats()
        m = w.build()
        self.assertEqual(self._delta(s), {"miss": 1})
        self.assertTrue(next(r for r in m["ledger"]["tree"] if r["id"] == "g1")["cleared"])
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1})
        # a transcript append: a new parse
        w.append(w.turn(2))
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"miss": 1})
        # a seam change (the seg ids of every turn after it)
        w.store(w.nodes(4), seams=[{"segs": ["no-such-seg"], "t": w.t - 30, "top": "g1", "text": "seam"}])
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"miss": 1})
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1})
        self.assertEqual(km._ledger_memo_report()["entries"], 1)

    def test_a_rewind_hold_and_a_live_merge_bypass(self):
        w = self.w
        w.store(w.nodes(2))
        w.build()
        km._rewind_hold_set(SID_A, w.t + 3, "")
        try:
            s = self._stats()
            self.assertEqual(len(w.build()["ledger"]["tree"]), 2)
            self.assertEqual(self._delta(s), {"bypass_hold": 1})
        finally:
            km._rewind_hold_clear(SID_A)
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1}, "the hold gone, the earlier entry serves")
        # a live atom merged into the last turn: the owning backend's live tail (the SDK backend's input echo
        # shape); the kernel keeps no echo store of its own since the tmux backend's removal (2026-09-11)
        class _LiveTail(km._UnownedBackend):
            def live_atoms(self, sid):
                return [{"type": "user", "uuid": "echo-11111111-2222-3333-4444-555555555555", "session_id": sid,
                         "t": w.now, "parentUuid": None, "author": "human", "_echo_text": "one more thing",
                         "message": {"role": "user", "content": [{"type": "text", "text": "one more thing"}]}}]

            def prune_live(self, sid, tx_uuids, tx_text_t, human_floor):
                pass
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: _LiveTail())):
            s = self._stats()
            w.build()
        self.assertEqual(self._delta(s), {"bypass_live": 1})

    def test_a_warm_anchor_learned_for_this_sessions_node_misses_once_and_another_sessions_does_not(self):
        w = self.w
        w.store(w.nodes(2))
        w.build()
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1})
        km._node_anchor_uuids({"id": "g9", "trail": ["s1"]}, {"s1": "u-1"}, {"s1": "a-1"}, sid=SID_B)
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1}, "another session's revision is not this key")
        km._node_anchor_uuids({"id": "g1", "trail": ["s1"]}, {"s1": "u-1"}, {"s1": "a-1"}, sid=SID_A)
        s = self._stats()
        m = w.build()
        self.assertEqual(self._delta(s), {"miss": 1})
        self.assertEqual(next(r for r in m["ledger"]["tree"] if r["id"] == "g1")["anchorUuid"], "a-1",
                         "the cold node now reads the warm anchor the table holds")
        km._node_anchor_uuids({"id": "g1", "trail": ["s1"]}, {"s1": "u-1"}, {"s1": "a-1"}, sid=SID_A)
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1}, "the same resolve again changes no entry: no bump")
        km._node_anchor_uuids({"id": "g1", "trail": ["s1"]}, {"s1": "u-1"}, {"s1": "a-1"})
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1}, "a resolve with no session names no revision")

    def test_a_resolve_landing_during_the_walk_misses_next_build_never_a_stale_hit(self):
        w = self.w
        w.store(w.nodes(2))
        w.build()
        w.store(w.nodes(3))                                 # the next build walks
        real, fired = km._node_anchor_uuids, []

        def racing(nd, trig, work, sid=None):
            if not fired:                                    # a peer build's resolve lands after this build read the rev
                fired.append(1)
                km._node_anchor_rev[SID_A] = km._node_anchor_rev.get(SID_A, 0) + 1
            return real(nd, trig, work, sid=sid)
        km._node_anchor_uuids = racing
        try:
            s = self._stats()
            w.build()
            self.assertEqual(self._delta(s), {"miss": 1})
        finally:
            km._node_anchor_uuids = real
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"miss": 1}, "the stored revision predates the racing bump: a miss")
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1})

    def test_a_warm_resolve_writes_the_table_entry_before_it_bumps_the_revision(self):
        # A build reads the revision before its walk and stores it. Were the bump to land first, a build
        # reading the revision between the bump and the entry write would store the new revision against a
        # walk over the old entry and serve it next build; so the entry is written first.
        revs_at_write = []

        class Recording(dict):
            def __setitem__(self, k, v):
                revs_at_write.append(km._node_anchor_rev.get(SID_A, 0))
                dict.__setitem__(self, k, v)
        real = km._node_anchor_last
        km._node_anchor_last = Recording()
        self.addCleanup(setattr, km, "_node_anchor_last", real)
        before = km._node_anchor_rev.get(SID_A, 0)
        km._node_anchor_uuids({"id": "g7", "trail": ["s1"]}, {"s1": "u-1"}, {"s1": "a-1"}, sid=SID_A)
        self.assertEqual(revs_at_write, [before], "the entry landed while the revision still read the old value")
        self.assertEqual(km._node_anchor_rev.get(SID_A, 0), before + 1)
        self.assertEqual(km._node_anchor_last["g7"], ("u-1", "a-1"))

    def test_a_store_published_between_the_builds_two_loads_misses_next_build(self):
        # build_session loads the store twice: at the top, for the seams its seg maps are cut with, and at
        # the ledger, for the nodes it walks. A publish landing between the two pairs the old seams' maps
        # with the new store object; the seams in the key make the next build, whose maps come from the new
        # seams, miss instead of serving that tree on the new store's identity.
        w = self.w
        w.store(w.nodes(2))
        w.build()
        real, calls = jd.load_goals_shared_or_fault, []

        def racing(sid):
            calls.append(sid)
            if len(calls) == 2:                                  # between the two loads: a publish with new seams
                w.store(w.nodes(3), seams=[{"segs": ["no-such-seg"], "t": w.t - 30, "top": "g1", "text": "seam"}])
            return real(sid)
        jd.load_goals_shared_or_fault = racing
        try:
            s = self._stats()
            m = w.build()
        finally:
            jd.load_goals_shared_or_fault = real
        self.assertEqual(calls, [SID_A, SID_A], "the seg maps' load, then the ledger's")
        self.assertEqual(self._delta(s), {"miss": 1})
        self.assertEqual(len(m["ledger"]["tree"]), 3, "the walk read the new store's nodes")
        s = self._stats()
        m2 = w.build()
        self.assertEqual(self._delta(s), {"miss": 1}, "the stored walk was cut with the old seams: not this build's key")
        km._ledger_memo.clear()
        self.assertEqual(_dump(w.build()["ledger"]), _dump(m2["ledger"]), "the served ledger equals a fresh walk")
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1})

    def test_a_clear_landing_during_the_walk_misses_next_build(self):
        # cleared.jsonl is stat'd BEFORE it is read: a row appended between the two pairs the old key with
        # the new set, so the next build misses instead of serving a set the file has moved past. The hook sits on
        # _cleared_ids_read, the one read every reader of the set shares (PR 2032: the walk reads through the display
        # reader, which takes the set and its fault from that read; a hook on the set-only wrapper was off the path,
        # so the row never landed during the walk and the next build served the memo)
        w = self.w
        w.store(w.nodes(2))
        real = km._cleared_ids_read

        def racing():
            with open(jd.STATE / "cleared.jsonl", "a") as f:
                f.write(json.dumps({"id": "g2", "t": w.now}) + "\n")
            km._cleared_ids_read = real
            return real()
        km._cleared_ids_read = racing
        try:
            s = self._stats()
            w.build()
            self.assertEqual(self._delta(s), {"miss": 1})
        finally:
            km._cleared_ids_read = real
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"miss": 1}, "the key taken before the read predates the row")
        s = self._stats()
        w.build()
        self.assertEqual(self._delta(s), {"hit": 1})

    def test_a_muted_tab_still_ships_no_tree(self):
        w = self.w
        w.store(w.nodes(3))
        w.build()
        (jd.STATE / "session-flags.json").write_text(json.dumps({SID_A: {"hideFromFeed": True}}))
        s = self._stats()
        m = w.build()
        self.assertEqual((m["ledger"]["tree"], m["ledger"]["recent"], m["ledger"]["current"]), ([], [], None))
        self.assertEqual(self._delta(s), {"hit": 1}, "the mute is applied after the memo, live")
        self.assertEqual(len(km._ledger_memo[SID_A][3]), 3, "the memo keeps the walk for an unmute")


class TaskFold(unittest.TestCase):
    """_fold_tasks' per-turn partials, memoized per sid on each turn's atoms list identity and fingerprint:
    a build over the parse cache's object rescans only the turn the live merge replaced."""

    T = 1781100000

    def setUp(self):
        self._saved = (dict(km._task_fold_memo), dict(km._task_fold_stats), os.environ.get("CLAUDE_CONFIG_DIR"))
        km._task_fold_memo.clear()
        for k in km._task_fold_stats:
            km._task_fold_stats[k] = 0
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["CLAUDE_CONFIG_DIR"] = td.name              # no real task store is read

    def tearDown(self):
        km._task_fold_memo.clear(); km._task_fold_memo.update(self._saved[0])
        km._task_fold_stats.clear(); km._task_fold_stats.update(self._saved[1])
        if self._saved[2] is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._saved[2]

    def _turn(self, i, create=None, update=None, rejected=False):
        T = self.T + 100 * i
        atoms = [{"type": "user", "uuid": "u%d" % i, "t": T, "author": "human",
                  "message": {"role": "user", "content": [{"type": "text", "text": "step %d" % i}]}}]
        content, results = [], []
        if create:
            content.append({"type": "tool_use", "id": "tu_c%d" % i, "name": "TaskCreate",
                            "input": {"subject": create[0], "activeForm": create[1]}})
            results.append({"type": "tool_result", "tool_use_id": "tu_c%d" % i,
                            "content": "InputValidationError: subject is required" if rejected else "Task #%d created" % (i + 1),
                            **({"is_error": True} if rejected else {})})
        if update:
            content.append({"type": "tool_use", "id": "tu_u%d" % i, "name": "TaskUpdate",
                            "input": {"taskId": update[0], "status": update[1]}})
        content.append({"type": "tool_use", "id": "tu_b%d" % i, "name": "Bash", "input": {"command": "uv run pytest -q"}})
        results.append({"type": "tool_result", "tool_use_id": "tu_b%d" % i, "content": "ok"})
        atoms.append({"type": "assistant", "uuid": "a%d" % i, "t": T + 10, "message": {"role": "assistant", "content": content}})
        atoms.append({"type": "user", "uuid": "r%d" % i, "t": T + 20, "message": {"role": "user", "content": results}})
        return {"id": "t%d" % i, "trigger": "u%d" % i, "t": T, "end": T + 20, "ended": True, "atoms": atoms}

    def _session(self):
        return {"turns": [self._turn(0, create=("write the tests", "Writing the tests")),
                          self._turn(1, create=("run the suite", "Running the suite")),
                          self._turn(2, update=("1", "completed"))]}

    EXPECTED = [{"id": "1", "subject": "write the tests", "activeForm": "Writing the tests", "status": "completed"},
                {"id": "2", "subject": "run the suite", "activeForm": "Running the suite", "status": "pending"}]

    def test_identity_hits_a_new_parse_misses_and_a_live_merge_misses_the_last_turn_only(self):
        sess = self._session()
        self.assertEqual(km._fold_tasks(sess), self.EXPECTED, "no sid: the unmemoized fold")
        self.assertEqual(km._task_fold_stats, {"hit": 0, "miss": 3}, "a direct call scans and memoizes nothing")
        self.assertEqual(km._task_fold_report()["entries"], 0)
        got = km._fold_tasks(sess, SID_A)
        self.assertEqual(got, self.EXPECTED)
        self.assertEqual(km._task_fold_stats, {"hit": 0, "miss": 6})
        got2 = km._fold_tasks(sess, SID_A)
        self.assertEqual(got2, self.EXPECTED)
        self.assertIsNot(got2, got, "a fresh list per call")
        self.assertEqual(km._task_fold_stats, {"hit": 3, "miss": 6}, "the same turns: every turn served")
        merged = dict(sess, turns=[dict(t) for t in sess["turns"]])       # _merge_live_atoms' shape
        merged["turns"][-1]["atoms"] = list(merged["turns"][-1]["atoms"]) + [
            {"type": "assistant", "uuid": "live", "t": self.T + 999,
             "message": {"role": "assistant", "content": [{"type": "text", "text": "streaming"}]}}]
        self.assertEqual(km._fold_tasks(merged, SID_A), self.EXPECTED)
        self.assertEqual(km._task_fold_stats, {"hit": 5, "miss": 7}, "a live merge: the last turn scanned, the rest served")
        fresh = json.loads(json.dumps(sess))                              # a re-parse: new atom lists
        self.assertEqual(km._fold_tasks(fresh, SID_A), self.EXPECTED)
        self.assertEqual(km._task_fold_stats, {"hit": 5, "miss": 10})
        self.assertEqual(km._task_fold_report(), {"hit": 5, "miss": 10, "entries": 1})
        self.assertIsNone(km._fold_tasks({"turns": []}, SID_A), "no turns: no checklist")

    def test_a_turn_that_grew_in_place_is_rescanned(self):
        sess = self._session()
        km._fold_tasks(sess, SID_A)
        sess["turns"][1]["atoms"].append({"type": "assistant", "uuid": "a1b", "t": self.T + 150, "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": "tu_u1b", "name": "TaskUpdate",
                                              "input": {"taskId": "2", "status": "in_progress"}}]}})
        got = km._fold_tasks(sess, SID_A)
        self.assertEqual(got[1]["status"], "in_progress", "the appended update is folded")
        self.assertEqual(km._task_fold_stats, {"hit": 2, "miss": 4}, "the grown turn's fingerprint moved")

    def test_a_rejected_create_and_a_result_in_a_later_turn_fold_as_before(self):
        # the combine runs over every turn's partial in order, so a result that lands in a later turn than its
        # call still names the task, and a rejected create is still no item (the unmemoized rules)
        sess = {"turns": [self._turn(0, create=("write the tests", "Writing the tests")),
                          self._turn(1, create=("a malformed create", None), rejected=True),
                          self._turn(2, update=("1", "in_progress"))]}
        self.assertEqual(km._fold_tasks(sess, SID_A), km._fold_tasks(sess),
                         "memoized and direct folds agree")
        self.assertEqual([t["id"] for t in km._fold_tasks(sess, SID_A)], ["1"], "the rejected create is no item")
        self.assertEqual(km._fold_tasks(sess, SID_A)[0]["status"], "in_progress")

    def test_the_result_is_not_the_memo_and_the_store_reader_leaves_it_unchanged(self):
        sess = self._session()
        got = km._fold_tasks(sess, SID_A)
        before = json.dumps(got)
        self.assertIsNone(km._read_task_store("no-such-fsid-" + SID_A[:8], got), "no store dir: the loud None")
        self.assertEqual(json.dumps(got), before, "the reader alters nothing")
        got[0]["status"] = "cancelled"                                    # a caller altering its copy
        self.assertEqual(km._fold_tasks(sess, SID_A)[0]["status"], "completed", "the memo is untouched")

    def test_build_session_hands_its_sid_to_the_fold(self):
        w = World(SID_A)
        seen = []
        real = km._fold_tasks
        km._fold_tasks = lambda session, sid=None: (seen.append(sid) or None)
        try:
            w.append(w.turn(0))
            w.build()
        finally:
            km._fold_tasks = real
            w.close()
        self.assertEqual(seen, [SID_A], "the memo is keyed on the session the build is for")


if __name__ == "__main__":
    unittest.main()
