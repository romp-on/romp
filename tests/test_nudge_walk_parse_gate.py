#!/usr/bin/env python3
"""T401 (2), 2026-09-13: the auto-nudge walk parsed every alive session on every pass, cold at boot (58.9 s of a 63 s first
cycle on the first boot with per-stage rows). Its parse is gated now: skipped while the nine files the memo keys on are
unchanged since the last completed look AND no clock leg that look declined on has come due; the skip repeats the recorded
verdict and does nothing else; only a road marked file-keyed, or the full walk run to its end, records a skippable memo. The
pass walks by recency and, with a client connected, yields after a look that paid a cold parse. Synthetic sessions only."""
import inspect
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_boot_parse_gating import km, _row, SID_OLD, SID_NEW   # noqa: E402  the hermetic kernel and the row builder

COMMON = dict(_session_flag=lambda sid, flag: False, _compacting_now=lambda *a, **k: False, _api_error=lambda path: False,
              _session_working=lambda turns: True, _auto_nudge_data=lambda: {}, _fire_debt_reminder=lambda sid, now, alive_ids: False)
STOPPED = [{"id": "t1", "t": 1000, "ended": True, "atoms": [{"t": 1000, "type": "user"}]}]


class NudgeWalkParseGate(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        km._TICK_SEEN.clear()
        for k, v in list(km._NUDGE_WALK_STATS.items()):
            km._NUDGE_WALK_STATS[k] = {} if isinstance(v, dict) else 0
        km._NUDGE_LOOK_STATS.clear()
        km._NUDGE_HORIZON.notes = None

    def _look(self, r, now, calls, **over):
        patches = dict(COMMON, **over)
        with mock.patch.multiple(km, **patches), \
             mock.patch.object(km.jd, "parsed_session", side_effect=lambda sid, paths, now: (calls.append(sid), {"turns": STOPPED})[1]), \
             mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None):
            return km._auto_nudge_session(r, now, {}, {}, {}, alive_ids={r["sid"]})

    def test_an_unchanged_session_with_no_clock_leg_skips_its_parse_and_repeats_its_verdict(self):
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); calls = []; now = time.time()
        self.assertEqual(self._look(r, now, calls), "working")
        self.assertEqual(self._look(r, now + 5, calls), "working", "the skip repeats the recorded verdict")
        self.assertEqual(calls, [SID_OLD], "one parse across two looks of an unchanged session with no clock leg")
        self.assertEqual((km._NUDGE_WALK_STATS["looks"], km._NUDGE_WALK_STATS["parses"], km._NUDGE_WALK_STATS["skippedParses"]), (2, 1, 1))
        Path(r["path"]).write_text(Path(r["path"]).read_text() + "\n")   # the transcript moves
        self.assertEqual(self._look(r, now + 10, calls), "working")
        self.assertEqual(calls, [SID_OLD, SID_OLD], "a moved file is evaluated")
        self.assertIn(("auto-nudge", SID_OLD), km._TICK_SEEN, "the memo persists with the tick memo")

    def test_a_look_that_never_parsed_records_nothing(self):
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); calls = []
        self.assertEqual(self._look(r, time.time(), calls, _session_flag=lambda sid, flag: True), "muted")
        self.assertNotIn(("auto-nudge", SID_OLD), km._TICK_SEEN)
        self.assertEqual(calls, [])

    def test_a_noted_clock_flip_evaluates_when_due_and_an_unbounded_note_never_skips(self):
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time()
        st = km._session_files_stat(r)
        km._nudge_look_done(r, st, [now + 100, now + 50], "working")
        self.assertEqual(km._nudge_look_check(r, now)[0::2], (True, "working"), "before the earliest flip: skip")
        self.assertEqual(km._nudge_look_check(r, now + 50)[0], False, "at the flip: evaluate")
        self.assertEqual(km._NUDGE_WALK_STATS["clockDue"], 1)
        km._nudge_look_done(r, st, [now + 100, None], "working")
        self.assertEqual(km._nudge_look_check(r, now)[0], False, "a leg released by something not in the files: never skipped")
        self.assertEqual(km._NUDGE_WALK_STATS["unbounded"], 1)
        km._nudge_look_done(r, st, [], None)
        self.assertEqual(km._nudge_look_check(r, now + 10 ** 6)[0::2], (True, None), "no clock leg: skippable while the files stand")
        os.utime(r["path"], (now - 1, now - 1))
        self.assertEqual(km._nudge_look_check(r, now)[0], False, "a moved file: evaluate")

    def test_the_clock_notes_collect_only_inside_a_look(self):
        km._NUDGE_HORIZON.notes = None
        km._nudge_clock(5.0)                                             # outside a look: a no-op
        km._NUDGE_HORIZON.notes = []
        km._nudge_clock(7.0); km._nudge_clock(None, "storeFault")
        self.assertEqual(km._NUDGE_HORIZON.notes, [7.0, None])
        km._NUDGE_HORIZON.notes = None

    def test_a_standing_deferral_notes_an_unbounded_release(self):
        km._NUDGE_HORIZON.notes = []
        with mock.patch.multiple(km, _auto_nudge_data=lambda: {"deferred": {}}, _write_auto_nudge=lambda d: True):
            self.assertFalse(km._nudge_deferred_ok("g1", "a reviver is pending", time.time(), SID_OLD))
        self.assertEqual(km._NUDGE_HORIZON.notes, [None], "a deferral is released by a judge pass watermark, not the session's files")
        km._NUDGE_HORIZON.notes = None

    def test_a_skipped_look_repeats_the_verdict_and_sends_nothing(self):
        """Round one, medium 1: the skip road ran the debt reminder with none of the session-level gates the full road applies
        before every send (idle, judged, unqueued), so the second look of a session the user stopped sent a reminder the full
        road refused. The skip repeats the recorded verdict and nothing else."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); calls = []; now = time.time(); asked = []
        self._look(r, now, calls)
        self.assertEqual(self._look(r, now + 1, calls, _fire_debt_reminder=lambda sid, now_, alive: (asked.append((sid, now_)), True)[1]), "working")
        self.assertEqual(asked, [], "the skip road sends nothing: every send sits behind the full road's gates")
        self.assertEqual(calls, [SID_OLD])

    def test_the_memo_survives_a_restart_through_the_loader(self):
        """Round one, medium 2: the loader kept rows of length six and the nudge's rows are eight long, so after a restart every
        cold parse was paid again, the case the gate exists for. The memo round-trips: done, persisted, cleared, loaded, skip."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time()
        saved_state = km.jd.STATE; km.jd.STATE = Path(tempfile.mkdtemp())
        try:
            km._nudge_look_done(r, km._session_files_stat(r), [now + 3600], "working")
            self.assertTrue(km._persist_tick_seen(force=True))
            km._TICK_SEEN.clear()
            self.assertGreaterEqual(km._load_tick_seen(), 1, "the persisted nudge row loads")
            self.assertEqual(km._nudge_look_check(r, now)[0::2], (True, "working"), "the next kernel skips on the loaded memo")
            self.assertEqual(km._nudge_look_check(r, now + 3600)[0], False, "and its flip still holds")
        finally:
            km.jd.STATE = saved_state

    def test_the_files_the_memo_keys_on_include_the_stores_identity_and_the_walks_logs(self):
        """Round one, low 2: the stat covered the store file alone, while the shared store's identity is three files (the store,
        its override journal, its archive) and the placement gate reads the episode and clears logs; a journal write whose store
        save did not land changed the world under a skip."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True)
        saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            st0 = km._session_files_stat(r)
            self.assertEqual(len(st0), 20, "ten files, (mtime, size) each")
            jp = km.jd._overrides_dir() / (SID_OLD + ".jsonl"); jp.parent.mkdir(parents=True, exist_ok=True); jp.write_text("{}\n")
            self.assertNotEqual(km._session_files_stat(r), st0, "the override journal moves the stat")
            st1 = km._session_files_stat(r)
            (km.jd.STATE / "cleared.jsonl").write_text("{}\n")
            self.assertNotEqual(km._session_files_stat(r), st1, "the clears log moves the stat")
        finally:
            km.jd._rebind_state(saved_state)

    def test_the_pass_walks_by_recency_and_yields_after_the_first_cold_parse_with_a_client(self):
        d = tempfile.mkdtemp()
        older, newer = _row(d, SID_OLD, old=True), _row(d, SID_NEW, old=False)
        seen = []; cold_for = {SID_NEW}                                  # only the newer session is cold, and it STAYS cold (a parse
        def look(s, now, live_map, nudged, waitfor, alive_ids=None, wake_only=False, cleared=None, reminders=None):   # that never caches)
            seen.append(s["sid"])
            if s["sid"] in cold_for:
                km._NUDGE_HORIZON.cold = getattr(km._NUDGE_HORIZON, "cold", 0) + 1; km._NUDGE_HORIZON.cold_last = True
            return None
        quiet = dict(_auto_nudge_data=lambda: {}, _auto_nudge_resume=lambda: None, _alive_sessions=lambda now, live_map: [older, newer],
                     _wait_for_graph=lambda now, ids: {}, _cleared_ids=lambda: set(), _auto_nudge_on=lambda: True,
                     _auto_nudge_session=look, _compact_suggest_tick=lambda sid, live, now: False, _relay_tick=lambda now, ids: None,
                     _debt_backstop_tick=lambda now: None, _dead_wait_sweep=lambda ids, nudged, now: None,
                     _awaiting_wake_outcomes=lambda now, ids: False, _push_soon=lambda: None, _pop_walk_gate=lambda k: None)
        with km._clients_lock:
            km._clients.append({"app": "feed", "wid": "lab", "send": lambda *a, **k: None, "alive": True, "dedup": {}})
        try:
            with mock.patch.multiple(km, **quiet):
                km._auto_nudge_pass(time.time(), {}, True)
        finally:
            with km._clients_lock:
                km._clients[:] = [c for c in km._clients if c.get("wid") != "lab"]
        self.assertEqual(seen, [SID_NEW], "the most recent session first; the rest deferred after a look that PAID a cold parse")
        self.assertEqual(km._NUDGE_WALK_STATS["deferredSessions"], 1)
        self.assertEqual(km._NUDGE_WALK_CURSOR[0], SID_OLD, "the first deferred session is where the next pass resumes")
        seen.clear()
        with km._clients_lock:
            km._clients.append({"app": "feed", "wid": "lab", "send": lambda *a, **k: None, "alive": True, "dedup": {}})
        try:
            with mock.patch.multiple(km, **quiet):
                km._auto_nudge_pass(time.time(), {}, True)               # the next pass resumes at the deferred session (round one,
        finally:                                                         #  medium 3): the older one is looked at BEFORE the cold
            with km._clients_lock:                                       #  head can yield again; a warm look never yields
                km._clients[:] = [c for c in km._clients if c.get("wid") != "lab"]
        self.assertEqual(seen, [SID_OLD, SID_NEW], "every session is reached within as many passes as there are cold parses")
        self.assertEqual(km._NUDGE_WALK_STATS["deferredSessions"], 1, "the rotated pass reached the end: nothing deferred")
        self.assertIsNone(km._NUDGE_WALK_CURSOR[0])
        seen.clear(); cold_for.update({SID_NEW, SID_OLD})
        with mock.patch.multiple(km, **quiet):
            km._auto_nudge_pass(time.time(), {}, True)                   # no client: the whole walk, cold or not
        self.assertEqual(seen, [SID_NEW, SID_OLD])
        self.assertEqual(km._NUDGE_WALK_STATS["deferredSessions"], 1, "no yield without a client")

    def test_only_a_file_keyed_road_records_a_skippable_memo(self):
        """Round three: the class fix. A look records a skippable memo only when its verdict came from a road marked file-keyed
        (the set names each road and the files it reads) or from the full walk run to its end; every other exit records an
        unbounded memo by default. The census: each file-keyed verdict driven and skippable; a verdict outside the set unbounded;
        a store fault (the round-three high) unbounded; the marked set reads only the files the memo keys on."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time()
        for verdict, over in (("working", {}),
                              ("progressing", {"_session_working": lambda turns: False, "_interrupt_suppresses_nudge": lambda turns, sid="", **k: False,
                                               "_pending_ops": {}, "_backend_queued": lambda sid: False, "_backend_rewind_pending": lambda sid: False,
                                               "_last_state": lambda sid: ("working", 10 ** 12)}),
                              ("user-interrupt", {"_session_working": lambda turns: False, "_interrupt_suppresses_nudge": lambda turns, sid="", **k: True})):
            with self.subTest(verdict=verdict):
                km._TICK_SEEN.clear(); calls = []
                self.assertEqual(self._look(r, now, calls, **over), verdict)
                memo = km._TICK_SEEN.get(("auto-nudge", SID_OLD))
                self.assertIsNotNone(memo); self.assertEqual(memo[-2], -1.0, "%s: a file-keyed road, skippable" % verdict)
                self.assertIn(verdict, km._NUDGE_FILE_KEYED_VERDICTS)
        for verdict, over in (("queued-input", {"_session_working": lambda turns: False, "_interrupt_suppresses_nudge": lambda turns, sid="", **k: False,
                                                "_pending_ops": {SID_OLD: [1]}}),
                              ("rewind-pending", {"_session_working": lambda turns: False, "_interrupt_suppresses_nudge": lambda turns, sid="", **k: False,
                                                  "_pending_ops": {}, "_backend_queued": lambda sid: False, "_backend_rewind_pending": lambda sid: True})):
            with self.subTest(verdict=verdict):
                km._TICK_SEEN.clear(); calls = []
                self.assertEqual(self._look(r, now, calls, **over), verdict)
                self.assertIsNone(km._TICK_SEEN[("auto-nudge", SID_OLD)][-2], "%s: not marked, so unbounded by default" % verdict)
                self.assertNotIn(verdict, km._NUDGE_FILE_KEYED_VERDICTS)
        with self.subTest(verdict="store-fault"):
            km._TICK_SEEN.clear(); calls = []
            quiet = dict(_session_working=lambda turns: False, _interrupt_suppresses_nudge=lambda turns, sid="", **k: False, _pending_ops={},
                         _backend_queued=lambda sid: False, _backend_rewind_pending=lambda sid: False, _last_state=lambda sid: ("", 0),
                         _session_awaiting=lambda *a, **k: False)
            with mock.patch.object(km.jd, "load_goals_shared_or_fault", side_effect=lambda sid: (None, OSError("EMFILE"))):
                self.assertIsNone(self._look(r, now, calls, **quiet))
            self.assertIsNone(km._TICK_SEEN[("auto-nudge", SID_OLD)][-2], "a store fault heals without a file write: unbounded")
            n_before = len(calls)
            self._look(r, now + 60, calls, **quiet)                          # the healed look (the store reads again)
            self.assertEqual(len(calls), n_before + 1, "the look after the fault parsed again: nothing was skipped")

    def _dead_asker_world(self, askers, now):
        """A debtor (SID_OLD) with one open ask from each of `askers`, none alive; the debt reminder runs its real ask set
        (the leg under test) and records what it would send; the registry directory is a fresh state root."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); calls = []; sent = []
        maps = ({}, {(a, SID_OLD): (now - 100 - i, "question", "are we done?") for i, a in enumerate(askers)}, {})
        def fire(sid, now_, alive_ids):
            asks = km._debt_asks(sid, alive_ids); sent.append([a[0] for a in asks]); return False
        quiet = dict(_session_working=lambda turns: False, _interrupt_suppresses_nudge=lambda turns, sid="", **k: False, _pending_ops={},
                     _backend_queued=lambda sid: False, _backend_rewind_pending=lambda sid: False, _last_state=lambda sid: ("", 0),
                     _session_awaiting=lambda *a, **k: False, _closer_settled=lambda *a: True, _nudge_placement_gate=lambda *a: False,
                     _postal_wait_maps=lambda: maps, _debt_reminder_outcomes=lambda sid, lt, now: None, _fire_debt_reminder=fire)
        km._TICK_SEEN.clear()
        return r, calls, sent, quiet

    def _dead_asker_look(self, r, now, calls, quiet, alive_ids=None):
        with mock.patch.multiple(km, **quiet), \
             mock.patch.object(km.jd, "load_goals_shared_or_fault", side_effect=lambda sid: ({"nodes": {}, "status": {}, "placements": {}}, None)), \
             mock.patch.object(km.jd, "parsed_session", side_effect=lambda sid, paths, now: (calls.append(sid), {"turns": STOPPED})[1]), \
             mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None):
            return km._auto_nudge_session(r, now, {}, {}, {}, alive_ids=alive_ids or {SID_OLD})   # the asker is not alive: no ask owed

    def test_an_ask_from_a_dead_peer_keys_the_askers_registry_row(self):
        """Round three, medium (T401 (2)): the debt reminder's ask set filters the postal wait maps by the ASKER's liveness,
        neither a session file, so a peer that asked and died left the debtor's memo unbounded (the deadAsker leg: about four in five of
        the unbounded notes on one boot, asks of long-gone peers, every debtor parsed every look). The dead asker's
        revival writes its registry row (STATE/sdk/<asker>.json), so that row joins the debtor's memo key (absent: a stable
        absent marker) and the leg notes nothing: the debtor skips at the second look."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            r, calls, sent, quiet = self._dead_asker_world([SID_NEW], now)
            before = dict(km._NUDGE_WALK_STATS.get("unboundedBy") or {})
            self.assertIs(self._dead_asker_look(r, now, calls, quiet), False)
            self.assertEqual(sent, [[]], "a dead asker's ask is owed to nobody now")
            memo = km._TICK_SEEN[("auto-nudge", SID_OLD)]
            self.assertIsNotNone(memo[-2], "the asker's row is keyed: the debtor's memo is bounded, not None")
            self.assertEqual(len(memo) - 2, 22, "ten files plus the asker's (mtime, size), zeros for an absent row: %r" % (memo,))
            self.assertEqual(km._NUDGE_WALK_STATS["unboundedBy"], before, "no leg notes anything for a keyed asker")
            self._dead_asker_look(r, now + 1, calls, quiet)                    # a skipped look answers from its memo
            self.assertEqual(calls, [SID_OLD], "the second look skipped: nothing of the debtor's or the asker's moved")
            self.assertEqual(len(km._session_files_stat(r)), 20, "the ten session files stand as they were")
            self.assertIsNone(km._NUDGE_HORIZON.keyed_askers, "the keyed set died with the look (round three, low 1)")
            self.assertIsNone(km._NUDGE_HORIZON.over_askers)
        finally:
            km.jd._rebind_state(saved_state)

    def test_a_dead_askers_revival_busts_the_debtors_memo_and_the_reminder_fires(self):
        """The revival writes the asker's registry row: the debtor's key moves, the next look parses, and the ask is owed."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            r, calls, sent, quiet = self._dead_asker_world([SID_NEW], now)
            self._dead_asker_look(r, now, calls, quiet); self._dead_asker_look(r, now + 1, calls, quiet)
            self.assertEqual(calls, [SID_OLD])
            (km.jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
            (km.jd.STATE / "sdk" / (SID_NEW + ".json")).write_text(json.dumps({"sid": SID_NEW, "pid": 1, "alive": True}))   # back
            self._dead_asker_look(r, now + 2, calls, quiet)                    # the alive set still names the debtor alone
            self.assertEqual(calls, [SID_OLD, SID_OLD], "the registry row moved the key: the look parsed again")
            self.assertEqual(sent[-1], [SID_NEW], "and the reminder found the ask owed to the revived peer: the row says alive")
        finally:
            km.jd._rebind_state(saved_state)

    def test_a_revival_landing_between_the_passs_alive_read_and_its_key_loop_still_owes_the_ask(self):
        """Round two, medium (the round-six rule): the pass read LIVENESS before the key. The alive set is taken over the
        cycle-top snapshot, then the key loop stats each session's files and asker rows; an asker that revived in that gap
        had its NEW row in the debtor's key while _debt_asks ran against the stale alive set, found it among the keyed
        askers and noted nothing, so the debtor recorded a skippable memo asserting it owed nothing. The keyed asker's
        aliveness is read from the SAME row the key stats, so the verdict and the key come from one file."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            r, calls, sent, quiet = self._dead_asker_world([SID_NEW], now)
            reg = km.jd.STATE / "sdk" / (SID_NEW + ".json")
            revived = []
            def alive_then_revival(now_, live_map):                   # the pass's alive read, with the revival landing right after it
                out = [r]                                             #  (once: the second pass reads the alive set and nothing lands)
                if not revived:
                    revived.append(1); reg.parent.mkdir(parents=True, exist_ok=True)
                    reg.write_text(json.dumps({"sid": SID_NEW, "pid": 1, "alive": True}))
                return out
            passq = dict(quiet, _alive_sessions=alive_then_revival, _auto_nudge_data=lambda: {}, _auto_nudge_resume=lambda: None,
                         _wait_for_graph=lambda now_, ids: {}, _cleared_ids=lambda: set(), _auto_nudge_on=lambda: True,
                         _compact_suggest_tick=lambda sid, live, now_: False, _relay_tick=lambda now_, ids: None,
                         _debt_backstop_tick=lambda now_: None, _dead_wait_sweep=lambda ids, nudged, now_: None,
                         _awaiting_wake_outcomes=lambda now_, ids: False, _push_soon=lambda: None, _pop_walk_gate=lambda k: None)
            with mock.patch.multiple(km, **passq), \
                 mock.patch.object(km.jd, "load_goals_shared_or_fault", side_effect=lambda sid: ({"nodes": {}, "status": {}, "placements": {}}, None)), \
                 mock.patch.object(km.jd, "parsed_session", side_effect=lambda sid, paths, now_: (calls.append(sid), {"turns": STOPPED})[1]), \
                 mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None):
                km._auto_nudge_pass(now, {}, True)
            self.assertEqual(calls, [SID_OLD], "the debtor was looked at once")
            self.assertEqual(sent, [[SID_NEW]], "the ask is owed: the asker's row, the file the key stats, says alive")
            memo = km._TICK_SEEN[("auto-nudge", SID_OLD)]
            self.assertIsNotNone(memo[-2], "and the memo is bounded by that row")
            reg.write_text(json.dumps({"sid": SID_NEW, "pid": 1, "alive": False}))   # the asker ends: the same row moves
            with mock.patch.multiple(km, **passq), \
                 mock.patch.object(km.jd, "load_goals_shared_or_fault", side_effect=lambda sid: ({"nodes": {}, "status": {}, "placements": {}}, None)), \
                 mock.patch.object(km.jd, "parsed_session", side_effect=lambda sid, paths, now_: (calls.append(sid), {"turns": STOPPED})[1]), \
                 mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None):
                km._auto_nudge_pass(now + 1, {}, True)
            self.assertEqual(calls, [SID_OLD, SID_OLD], "the row moved: the look parsed again")
            self.assertEqual(sent[-1], [], "and owes nothing to a peer whose row says it ended, whatever the alive set said")
        finally:
            km.jd._rebind_state(saved_state)

    def test_an_unreadable_or_gutted_asker_row_is_unproven_and_the_alive_set_decides_the_ask(self):
        """Round three, medium: an unreadable or gutted row read DEAD, while the backend's own reader serves the last good row
        over a read fault; one transient fault latched a skippable memo under an unmoved key. Round four, medium 1: keeping
        the ask UNCONDITIONALLY on an unproven row sent the debtor a reminder to answer a dead peer (a gutted reg whose driver
        is not running is dead to the backend). A row that cannot be read, or parses without an alive bit, is UNPROVEN: the
        look notes None under askerRowUnproved (the memo cannot skip on that evidence) and the ask is decided by the alive
        set, the backend's own answer. A missing row stays dead (the key carries the absent marker)."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            r, calls, sent, quiet = self._dead_asker_world([SID_NEW], now)
            reg = km.jd.STATE / "sdk" / (SID_NEW + ".json"); reg.parent.mkdir(parents=True, exist_ok=True)
            reg.write_text(json.dumps({"sid": SID_NEW, "field": 1}))          # the gutted shape: no alive bit
            before = dict(km._NUDGE_WALK_STATS.get("unboundedBy") or {})
            self._dead_asker_look(r, now, calls, quiet)                        # the asker is absent from the alive set
            self.assertEqual(sent, [[]], "no reminder to answer a peer the backend calls dead")
            memo = km._TICK_SEEN[("auto-nudge", SID_OLD)]
            self.assertIsNone(memo[-2], "the memo is unbounded on that evidence")
            by = km._NUDGE_WALK_STATS["unboundedBy"]
            self.assertEqual(by.get("askerRowUnproved", 0), before.get("askerRowUnproved", 0) + 1, by)
            self._dead_asker_look(r, now + 1, calls, quiet)
            self.assertEqual(calls, [SID_OLD, SID_OLD], "the second look parsed again")
            self.assertIsNone(km._asker_row_alive(SID_NEW), "gutted: unproven")
            reg.write_text("{not json")
            self.assertIsNone(km._asker_row_alive(SID_NEW), "unparseable: unproven")
            if os.geteuid() != 0:                                              # root reads through the mode bits
                reg.write_text(json.dumps({"sid": SID_NEW, "alive": True})); os.chmod(reg, 0)
                try:
                    self.assertIsNone(km._asker_row_alive(SID_NEW), "unreadable: unproven, never dead")
                    self._dead_asker_look(r, now + 2, calls, quiet, alive_ids={SID_OLD, SID_NEW})   # the backend says alive
                    self.assertEqual(sent[-1], [SID_NEW], "the ask is kept when the alive set has the asker (round two's case)")
                finally:
                    os.chmod(reg, 0o600)
            reg.write_text(json.dumps({"sid": SID_NEW, "alive": False}))
            self.assertIs(km._asker_row_alive(SID_NEW), False, "an explicit alive false is dead")
            reg.unlink()
            self.assertIs(km._asker_row_alive(SID_NEW), False, "a missing row is dead: the key carries the absent marker")
        finally:
            km.jd._rebind_state(saved_state)

    def test_an_undecodable_asker_row_does_not_abort_the_look(self):
        """Round four, medium 2: a row that is not UTF-8 raised UnicodeDecodeError (a ValueError, not an OSError) out of
        read_text, uncaught, through _debt_asks and the reminder, aborting the look: no memo ever recorded, a traceback per
        pass. It is unproven like any unreadable row: the look completes, notes the leg and decides the ask by the alive set."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            r, calls, sent, quiet = self._dead_asker_world([SID_NEW], now)
            reg = km.jd.STATE / "sdk" / (SID_NEW + ".json"); reg.parent.mkdir(parents=True, exist_ok=True)
            reg.write_bytes(b"\xff\xfe{")
            before = dict(km._NUDGE_WALK_STATS.get("unboundedBy") or {})
            self.assertIsNone(km._asker_row_alive(SID_NEW), "undecodable: unproven")
            self._dead_asker_look(r, now, calls, quiet)                        # must not raise
            self.assertEqual(calls, [SID_OLD], "the look completed over the undecodable row")
            self.assertEqual(sent, [[]], "and decided the ask by the alive set (the asker absent)")
            self.assertEqual(km._NUDGE_WALK_STATS["unboundedBy"].get("askerRowUnproved", 0), before.get("askerRowUnproved", 0) + 1)
            self.assertIsNone(km._TICK_SEEN[("auto-nudge", SID_OLD)][-2], "unbounded")
        finally:
            km.jd._rebind_state(saved_state)

    def test_an_alive_ninth_asker_notes_the_overflow_too(self):
        """Round three, low 3: the overflow note fired only for an asker absent from the alive set, so with the ninth asker
        alive nothing noted and the memo was skippable while that row sat outside the key; any overflow asker notes None
        under askerOverflow (its earlier name, deadAskerOverflow, said what it no longer meant)."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            askers = ["%08d-2222-3333-4444-0000000000%02d" % (i, i) for i in range(km._NUDGE_ASKER_ROWS_MAX + 1)]
            r, calls, sent, quiet = self._dead_asker_world(askers, now)
            before = dict(km._NUDGE_WALK_STATS.get("unboundedBy") or {})
            self._dead_asker_look(r, now, calls, quiet, alive_ids={SID_OLD, askers[0]})   # the overflowing (newest) asker is alive
            self.assertIsNone(km._TICK_SEEN[("auto-nudge", SID_OLD)][-2], "unbounded: its row is outside the key")
            self.assertEqual(km._NUDGE_WALK_STATS["unboundedBy"].get("askerOverflow", 0), before.get("askerOverflow", 0) + 1)
            self.assertEqual(sent, [[askers[0]]], "and the alive asker's ask is owed")
        finally:
            km.jd._rebind_state(saved_state)

    def test_the_pass_stats_the_postal_log_before_it_builds_the_asker_index(self):
        """Round three, low 5: the asker index was built from the postal wait maps before the key loop statted the log, so the
        key could claim a newer log than the selection read. The pass takes the log's stat first and hands it to the key."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True)
        st, keyed, over = km._nudge_look_stat(r, {}, (12.5, 77))
        self.assertEqual(st[km._NUDGE_POSTAL_KEY_AT:km._NUDGE_POSTAL_KEY_AT + 2], (12.5, 77), "the pre-taken stat sits at the log's position")
        self.assertEqual(len(st), 20)
        src = inspect.getsource(km._auto_nudge_pass)
        self.assertLess(src.index("messages.jsonl"), src.index("_nudge_asks_by_target()"), "the stat precedes the index")

    def test_an_ask_landing_between_the_index_build_and_the_key_ends_the_skip(self):
        """Round four, low 3 (the behavioural red for round three's low 5): an ask appended to the postal log AFTER the asker
        index read it and BEFORE the key loop statted the log gave the key a log newer than the selection, so the next pass
        skipped a debtor whose new asker's row was never keyed. The key carries the pre-index stat, so that pass parses."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            r, calls, sent, quiet = self._dead_asker_world([], now)
            log = km.jd.STATE / "timeline" / "messages.jsonl"; log.parent.mkdir(parents=True, exist_ok=True); log.write_text("")
            real_index = km._nudge_asks_by_target; landed = []
            def index_then_ask():
                out = real_index()
                if not landed:                                                 # the window: an ask lands after the index read the log
                    landed.append(1)
                    with open(log, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"kind": "question", "from_id": SID_NEW, "to_sid": SID_OLD, "t": time.time()}) + "\n")
                return out
            passq = dict(quiet, _alive_sessions=lambda now_, live_map: [r], _auto_nudge_data=lambda: {}, _auto_nudge_resume=lambda: None,
                         _wait_for_graph=lambda now_, ids: {}, _cleared_ids=lambda: set(), _auto_nudge_on=lambda: True,
                         _compact_suggest_tick=lambda sid, live, now_: False, _relay_tick=lambda now_, ids: None,
                         _debt_backstop_tick=lambda now_: None, _dead_wait_sweep=lambda ids, nudged, now_: None,
                         _awaiting_wake_outcomes=lambda now_, ids: False, _push_soon=lambda: None, _pop_walk_gate=lambda k: None,
                         _nudge_asks_by_target=index_then_ask)
            for i in range(2):
                with mock.patch.multiple(km, **passq), \
                     mock.patch.object(km.jd, "load_goals_shared_or_fault", side_effect=lambda sid: ({"nodes": {}, "status": {}, "placements": {}}, None)), \
                     mock.patch.object(km.jd, "parsed_session", side_effect=lambda sid, paths, now_: (calls.append(sid), {"turns": STOPPED})[1]), \
                     mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None):
                    km._auto_nudge_pass(now + i, {}, True)
            self.assertEqual(calls, [SID_OLD, SID_OLD], "the log moved after the key's stat of it: the second pass parsed")
        finally:
            km.jd._rebind_state(saved_state)

    def test_askers_beyond_the_eight_keyed_rows_note_an_unbounded_release_under_their_own_leg(self):
        """The key carries at most _NUDGE_ASKER_ROWS_MAX asker rows (oldest asks first); a debtor with more notes None for
        the rest under askerOverflow, so a session flooded with asks never grows a key without bound."""
        now = time.time(); saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            askers = ["%08d-2222-3333-4444-0000000000%02d" % (i, i) for i in range(km._NUDGE_ASKER_ROWS_MAX + 1)]
            r, calls, sent, quiet = self._dead_asker_world(askers, now)
            before = dict(km._NUDGE_WALK_STATS.get("unboundedBy") or {})
            self._dead_asker_look(r, now, calls, quiet)
            memo = km._TICK_SEEN[("auto-nudge", SID_OLD)]
            self.assertIsNone(memo[-2], "the ninth asker is not keyed: the memo is unbounded")
            by = km._NUDGE_WALK_STATS["unboundedBy"]
            self.assertEqual(by.get("askerOverflow", 0), before.get("askerOverflow", 0) + 1, "one note, under its own leg")
            self.assertNotIn("deadAsker", by, "no such leg: every dead asker is keyed or beyond the keyed rows")
            keyed, over = km._NUDGE_LOOK_ASKERS[SID_OLD]
            self.assertEqual((len(keyed), over), (km._NUDGE_ASKER_ROWS_MAX, [askers[0]]), "the NEWEST ask overflows: oldest first are keyed")
        finally:
            km.jd._rebind_state(saved_state)

    def test_a_host_suspension_ends_the_skip_of_a_working_verdict(self):
        """Round four, HIGH: `working` was marked file-keyed, but _session_working ends in _suspended_after, which reads the
        module-level _downtime list that _record_suspend appends to and STATE/kernel-downtime.jsonl refills; that file was not
        keyed, so a session whose open turn had no live working record kept its working verdict across a lid close and never
        fired what the full road fires after a suspension. The downtime log is the ninth keyed file: a suspension row ends the skip."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time(); calls = []
        saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp()))
        try:
            self.assertEqual(self._look(r, now, calls), "working")
            self.assertEqual(self._look(r, now + 1, calls), "working"); self.assertEqual(calls, [SID_OLD], "skipped while nothing moved")
            km._record_suspend((now - 30, now))                            # the lid closed and reopened: the downtime log gains a row
            self.assertEqual(self._look(r, now + 2, calls), "working")
            self.assertEqual(calls, [SID_OLD, SID_OLD], "the suspension row ends the skip: the look parsed again")
        finally:
            km.jd._rebind_state(saved_state)
            with km._TICK_SEEN_LOCK:
                pass

    def test_every_marked_road_reads_only_the_keyed_files(self):
        """Round five, medium 2: the census is a TRACE, not a denylist. Each marked road's functions are walked transitively with
        the ast module (the functions they call, the helpers those call, down to the keyed-file readers); every module-level
        name a road reads must be a function walked in turn, an immutable constant, or one of the allowlisted keyed-file readers,
        pure helpers and file-refilled states; any other module-level name, a brand-new list included, fails naming the road and
        the name. Module state refilled from a file (the _downtime list) is traced to the downtime log, which the stat function
        must name. Limits the census states rather than closes (round seven, low 1): a read through globals()[...] or getattr
        with a string attribute, a function-local import (locals_of treats every imported name as local), a read_text on a path
        built off the allowlisted jd.STATE or a literal Path, and a module-level lambda on a marked road (locals_of expects a def)
        are not traced; the roads are read by eye for those shapes."""
        import ast, inspect, types, builtins
        CLASS_ALLOW = {"Path": "pathlib's path type: a pure value"}   # the exception types are builtins, short-circuited a line earlier; a
        #                                               class with mutable state (Sessions, the door to the live backend table) is LIVE state
        ALLOW = {                                   # module-level names a marked road may read, each traced to its source
            "jd", "em", "os", "time", "json", "re", "sys", "math", "Path", "threading", "traceback", "zlib", "contextlib",   # stdlib (pure)
            "_downtime",                            # the host-suspension list, refilled from STATE/kernel-downtime.jsonl (keyed)
            "_intr_marks_memo",                     # a memo keyed on the parse identity plus the state log's machineCut pair
            "_intr_marks_memo_stats", "_INTR_MARKS_STATS_LOCK",   # that memo's hit/miss counters and their lock (no input)
            "_nudge_gate_memo", "_NUDGE_GATE_STATS",   # the placement gate's memo (the parse identity, the store view, the episode log's
            #                                         stat, the clears log's stat) and its counters
            "_last_state_cache", "_machine_cut_cache",   # _fold_records cursors over the state log (a keyed file), keyed by its path and stat
            "_stat_key",                            # a (mtime, size) reader
            "_NUDGE_HORIZON",                       # the look's thread-local horizon: its notes, and the keyed and overflow asker sets the
            #                                         gate derived from the postal log's maps (keyed) before the look; no input of its own
            "_NUDGE_WALK_STATS",                    # the walk's counters (no input)
            "_live_scope",                          # the jobs pass's thread-local scope: its files_stat slot holds the ten files' stats this
            #                                         key function itself took earlier in the SAME pass (plans/nudge-walk-events.md, the
            #                                         shared snapshot), a memo of the keyed files' own stats and never an input of its own
            "_FILES_STAT_STANDING",                 # the same stats standing across passes until a writer's mark or the floor (the dirty
            #                                         set is read through the scope's files_dirty slot): the key's own stats, no input
            "_NUDGE_ASKER_ROWS_MAX",                # a constant
            "_views_dirty", "_pusher_wake",         # the writers' dirty mark and the pusher's wake (_mark_views_dirty): outputs of a
            #                                         block filed or lifted, never inputs to the verdict
            "_INTR_MARKS_DISK", "_INTR_MARKS_DISK_LOCK", "_INTR_MARKS_DISK_DIRTY",   # the persisted interrupt-marks memo (T401 (3)
            #                                         target 3): rows keyed on the transcript's stat, the states log's cut pair (both
            #                                         keyed files) and the parse's own sdk-ownership bit (jd._sdk_owned, the input
            #                                         parsed_session reads as sdk_human), holding only the tally's own two maxima
        }
        DISPLAY_ONLY = {"_name_of": "the asker's display name for the reminder's TEXT (the names snapshot): never a verdict input"}
        ROAD_FORBIDDEN = {                          # a road whose KEY writes constants at some positions must never read those files (T401 (3)):
            "interrupt-block": {"names": {"_postal_wait_maps", "_nudge_asks_by_target", "_postal_index_memo"},
                                "jd": {"MESSAGES", "EPIDIR", "episode_floor"},
                                "text": ("messages.jsonl", "episodes")},   # the postal log and the episode log; the clears log stays a REAL
        }                                                             #  position (the store readers' override replay reads it)
        CONST_MODULES = {"sb"}                      # the SDK backend module: a marked road may read only a CONSTANT of it (a cause
        #                                             name, a marker string) or one of the pure text helpers below, never a live table
        SB_PURE = {"echo_text_key", "strip_echo_markers", "_strip_marker_tail"}   # pure functions of their text argument (an atom's
        #                                             user text folded to the echo key the interrupt marks compare against)
        REFILLED = {"_downtime": "kernel-downtime.jsonl"}
        LEAF_READERS = {"_fold_records": "the event model's fold cursor over the NAMED file (the state log here), keyed by that file's "
                                         "stat; its internals are the record cache and the checkpoint tables, which mirror the file",
                        "_postal_wait_maps": "the postal log's wait maps, cached on that file's (mtime_ns, size) and rebuilt from it alone "
                                             "(the eighth keyed file); its internals are that cache and the alias history, which mirror the log",
                        "_auto_nudge_data": "the nudge ledger (the tenth keyed file), read through _ledger_read's cache on its (mtime_ns, size); "
                                            "the interrupt tick's key carries this session's own row from it, so a row change busts the memo "
                                            "and another session's does not (T401 (3)); its internals are that cache and the fault latches"}
        JD_ALLOW = {"parsed_session", "_parse_entry", "_segs", "plan_units", "_placed_key", "_unit_key", "_closed_turns", "EPIDIR", "STATE",
                    "GOALDIR", "CLOSER_ON", "load_goals_shared_or_fault", "_seg_key", "_segment_id", "episode_floor", "_view_cleared",
                    "GOALARCHDIR", "_overrides_dir",   # the two keyed-file paths _session_files_stat itself names (the interrupt tick's key)
                    "load_goals_or_fault", "record_verdict", "append_block", "rollup_status", "save_goals", "INTERRUPT_BLOCK_WHY",
                    "_intr_paused_only",   # the interrupt arms' store readers and writers (T401 (3) round two): they load and write
                    "_pending_cut",    # the armed bare-rollback cut the judge parse reads live (no file): the marks memo takes NO key while it is armed
                    "_sdk_owned"}      # the parse's sdk-ownership bit (parsed_session hands it to the adapter as sdk_human): the
        #                                    marks memo's key carries the bit itself (round three, low 2)
        #                                    the goal store through its own API, the store, its journal and its archive being keyed
        #                                    files 3 to 5, and the override replay inside load_goals reads the clears log (keyed file 7,
        #                                    which is why the interrupt key keeps that position real); _intr_paused_only is a pure
        #                                    reader of the loaded store; INTERRUPT_BLOCK_WHY a constant. rollup_status is on the list
        #                                    because the arms call it; it reads the nudge ledger (stalled_facts) for a stall WARNING
        #                                    only, never a status input. _set_intr_blocked, the third arm writer with its own fresh
        #                                    ledger read, is OFF the interrupt-block road by design (the road's twelve members are
        #                                    pinned in test_boot_parse_gating) (1595 low 3)
        EM_ALLOW = {"hydrate", "atom_text", "_atom_text", "is_interrupt_record",   # pure readers of a record or an atom
                    "LazyAtoms"}   # the pre-cut turn's container: its user_facts reads the document's rows, the transcript's own records
        stat_src = inspect.getsource(km._session_files_stat)
        def module_name(n, g):
            return n in g
        def locals_of(fn_node):
            names = {a.arg for a in fn_node.args.args + fn_node.args.kwonlyargs + fn_node.args.posonlyargs}
            if fn_node.args.vararg: names.add(fn_node.args.vararg.arg)
            if fn_node.args.kwarg: names.add(fn_node.args.kwarg.arg)
            for n in ast.walk(fn_node):
                if isinstance(n, (ast.Name,)) and isinstance(n.ctx, ast.Store): names.add(n.id)
                if isinstance(n, ast.ExceptHandler) and n.name: names.add(n.name)
                if isinstance(n, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):   # a nested function's or lambda's parameters
                    names.update(a.arg for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs)   # are locals too
                    if n.args.vararg: names.add(n.args.vararg.arg)
                    if n.args.kwarg: names.add(n.args.kwarg.arg)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn_node: names.add(n.name)
                if isinstance(n, ast.Import) or isinstance(n, ast.ImportFrom):
                    for a in n.names: names.add((a.asname or a.name).split(".")[0])
            return names
        problems = []; self.maxDiff = None
        for verdict in sorted(set(km._NUDGE_FILE_KEYED_VERDICTS) | {"walk-completed", "interrupt-block"}):   # the debt leg's exit and
            #                                                                                              the interrupt tick's road too
            todo = list(km._NUDGE_FILE_KEYED_ROADS[verdict]); seen = set()
            self.assertTrue(todo, "%s: its road functions are named" % verdict)
            while todo:
                fn_name = todo.pop()
                if fn_name in seen: continue
                seen.add(fn_name)
                fn = getattr(km, fn_name, None)
                if not isinstance(fn, types.FunctionType):
                    problems.append((verdict, fn_name, "not a module-level function")); continue
                try:
                    tree = ast.parse(inspect.getsource(fn).lstrip())
                except (OSError, SyntaxError) as e:
                    problems.append((verdict, fn_name, "unreadable source: %r" % e)); continue
                fn_node = tree.body[0]; local = locals_of(fn_node); g = fn.__globals__   # the function's OWN globals (em's for em.fold_records)
                forbid = ROAD_FORBIDDEN.get(verdict)
                if fn_name in ("_session_files_stat", "_interrupt_block_key"):
                    forbid = None                         # the KEY builders stat those files (they name them) and read none
                if forbid:
                    src_text = inspect.getsource(fn)
                    for frag in forbid["text"]:
                        if frag in src_text:
                            problems.append((verdict, fn_name, "names %r, a file its key writes as a constant" % frag))
                for n in ast.walk(fn_node):
                    if forbid and isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in forbid["names"]:
                        problems.append((verdict, fn_name, "reads %s, a reader of a file its key writes as a constant" % n.id))
                    if forbid and isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "jd" and n.attr in forbid["jd"]:
                        problems.append((verdict, fn_name, "reads jd.%s, a file its key writes as a constant" % n.attr))
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("jd", "em"):
                        allow = JD_ALLOW if n.value.id == "jd" else EM_ALLOW
                        if n.attr not in allow:
                            problems.append((verdict, fn_name, "%s.%s is not a traced keyed-file reader" % (n.value.id, n.attr)))
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in CONST_MODULES:
                        mod = km.__dict__.get(n.value.id); val = getattr(mod, n.attr, None) if mod is not None else None
                        if not isinstance(val, (str, int, float, bool, tuple, frozenset)) and n.attr not in SB_PURE:
                            problems.append((verdict, fn_name, "%s.%s is not a constant or a pure text helper (%s)" % (n.value.id, n.attr, type(val).__name__)))
                    if not isinstance(n, ast.Name) or not isinstance(n.ctx, ast.Load): continue
                    name = n.id
                    if name in local or hasattr(builtins, name): continue   # the builtins MODULE (not the dict's attributes)
                    if not module_name(name, g):
                        problems.append((verdict, fn_name, "reads %s, bound nowhere the census can see" % name)); continue
                    val = g[name]
                    if isinstance(val, types.FunctionType):
                        if name in LEAF_READERS or name in DISPLAY_ONLY:
                            continue                                # a justified keyed-file reader (the trace stops at the file), or a
                        #                                             name read for display text alone, never for a verdict
                        if g is km.__dict__ and val.__globals__ is km.__dict__:
                            todo.append(name); continue             # a kernel helper: walked in turn, in its own globals
                        problems.append((verdict, fn_name, "calls %s from %s, a function the trace does not walk" % (name, getattr(val, "__module__", "?"))))
                        continue
                    if isinstance(val, (int, float, str, bytes, tuple, frozenset, bool, type(None))):
                        continue                                    # an immutable constant
                    if isinstance(val, type):
                        if name in CLASS_ALLOW:
                            continue                                # a justified class (a value type, an exception)
                        problems.append((verdict, fn_name, "reads class %s: an attribute chain off a class is live state unless justified" % name)); continue
                    if name in ALLOW or name in CONST_MODULES:
                        if name in REFILLED:
                            self.assertIn(REFILLED[name], stat_src, "%s: %s reads %s, refilled from %s, which the memo must key on" % (verdict, fn_name, name, REFILLED[name]))
                        continue
                    problems.append((verdict, fn_name, "reads module-level %s (%s), traced to no keyed file" % (name, type(val).__name__)))
            self.assertGreaterEqual(len(seen), len(km._NUDGE_FILE_KEYED_ROADS[verdict]), verdict)
        self.assertEqual(problems, [], "every marked road reads only the keyed files, through traced helpers")
        self.assertIn("kernel-downtime.jsonl", stat_src)

    def test_the_downtime_record_appends_before_it_writes_so_a_look_in_the_gap_cannot_record_a_stale_skippable_memo(self):
        """Round six, medium 1: with the write first, a look landing between the write and the append saw a moved stat but no
        suspension, computed working and recorded a skippable memo under the final stat; the append then moved nothing on disk
        and the memo stood forever (the round-four HIGH made permanent). The state moves first and its file last: a look in the
        gap sees the suspension (the verdict leaves working), and the write that follows busts whatever it recorded."""
        import builtins
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time(); calls = []
        open_turn = [{"id": "t1", "t": now - 100, "atoms": [{"t": now - 100, "type": "user"}]}]   # OPEN: no end, no idle
        saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp())); saved_down = list(km._downtime)
        quiet = dict(_session_flag=lambda sid, flag: False, _compacting_now=lambda *a, **k: False, _api_error=lambda path: False,
                     _interrupt_suppresses_nudge=lambda turns, sid="", **k: False, _pending_ops={}, _backend_queued=lambda sid: False,
                     _backend_rewind_pending=lambda sid: False, _last_state=lambda sid: ("", 0), _session_awaiting=lambda *a, **k: False,
                     _auto_nudge_data=lambda: {}, _fire_debt_reminder=lambda sid, now, alive_ids: False)
        gap = []
        def look(at):
            with mock.patch.multiple(km, **quiet), \
                 mock.patch.object(km.jd, "parsed_session", side_effect=lambda sid, paths, now: (calls.append(sid), {"turns": open_turn})[1]), \
                 mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None):
                return km._auto_nudge_session(r, at, {}, {}, {}, alive_ids={SID_OLD})
        real_open = builtins.open
        def open_and_look(path, *a, **kw):
            f = real_open(path, *a, **kw)
            if str(path).endswith("kernel-downtime.jsonl") and a and a[0] == "a":
                gap.append(look(now + 1))                          # a look lands while the file is open for the write
            return f
        try:
            km._downtime[:] = []
            self.assertEqual(look(now), "working", "an open turn, no suspension: working, a skippable memo")
            with mock.patch.object(builtins, "open", side_effect=open_and_look):
                km._record_suspend((now - 30, now))
            self.assertEqual(len(gap), 1)
            self.assertNotEqual(gap[0], "working", "the look in the gap saw the suspension (the state moved first): %r" % gap[0])
            n = len(calls)
            v = look(now + 2)
            self.assertEqual(len(calls), n + 1, "the write that followed busted the gap's memo: the next look parsed")
            self.assertNotEqual(v, "working")
        finally:
            km._downtime[:] = saved_down; km.jd._rebind_state(saved_state)

    def test_a_failed_downtime_write_forgets_every_nudge_memo(self):
        """Round five, low b: the list moved with no file to say so; every nudge memo is dropped so none may skip."""
        import builtins
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time(); calls = []
        saved_state = km.jd.STATE; km.jd._rebind_state(Path(tempfile.mkdtemp())); saved_down = list(km._downtime)
        try:
            self._look(r, now, calls); self.assertIn(("auto-nudge", SID_OLD), km._TICK_SEEN)
            km._TICK_SEEN[("interrupt-block", SID_OLD)] = (1,) * 18
            real_open = builtins.open
            def refuse(path, *a, **kw):
                if str(path).endswith("kernel-downtime.jsonl"): raise OSError("read-only")
                return real_open(path, *a, **kw)
            with mock.patch.object(builtins, "open", side_effect=refuse):
                km._record_suspend((now - 30, now))
            self.assertNotIn(("auto-nudge", SID_OLD), km._TICK_SEEN, "the nudge memos are gone")
            self.assertIn(("interrupt-block", SID_OLD), km._TICK_SEEN, "the other jobs' memos stand")
            self.assertEqual(km._downtime[-1], (now - 30, now), "the list moved regardless")
        finally:
            km._downtime[:] = saved_down; km.jd._rebind_state(saved_state)

    def test_a_closer_unsettled_memo_is_void_with_the_closer_toggle_off(self):
        """Round five, low c: CLOSER_ON is an import-time toggle, not a file; a memo recorded under it must not be served with it off."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time()
        km._nudge_look_done(r, km._session_files_stat(r), [], "closer-unsettled")
        self.assertEqual(km._nudge_look_check(r, now)[0::2], (True, "closer-unsettled"))
        with mock.patch.object(km.jd, "CLOSER_ON", False):
            self.assertEqual(km._nudge_look_check(r, now)[0], False, "void with the toggle off")
        km._nudge_look_done(r, km._session_files_stat(r), [], "working")
        with mock.patch.object(km.jd, "CLOSER_ON", False):
            self.assertEqual(km._nudge_look_check(r, now)[0], True, "another verdict is untouched by the toggle")

    def test_a_queued_lost_send_notes_an_unbounded_release(self):
        """Round five, low e: a send still in the backend's queue is a queue read, not a file; while it is queued the look must
        stay unbounded so a send that leaves the queue without landing stamps at once, not at the six-hour dead-man."""
        now = time.time()
        turns = [{"id": "t1", "t": now - 100, "end": now - 50, "ended": True, "atoms": [{"t": now - 100, "type": "user"}]}]
        rec = {"lastTurnId": "t1", "armAtoms": 0, "at": now - 60}    # the arm turn grew past its armed atoms: the response could be in
        km._NUDGE_HORIZON.notes = []
        try:
            with mock.patch.object(km, "_nudge_send_queued", lambda sid, gid: True), mock.patch.object(km.jd, "_segs", lambda tn, store: []):
                ready, resp = km._nudge_response_ready(turns, {}, rec, SID_OLD + ":g1", now)
            self.assertFalse(ready)
            self.assertIn(None, km._NUDGE_HORIZON.notes, "queued: unbounded")
            km._NUDGE_HORIZON.notes = []
            with mock.patch.object(km, "_nudge_send_queued", lambda sid, gid: False), mock.patch.object(km.jd, "_segs", lambda tn, store: []):
                ready, resp = km._nudge_response_ready(turns, {}, rec, SID_OLD + ":g1", now)
            self.assertTrue(ready, "the turn ended after the fire with no marker and nothing queued: the lost-send event")
        finally:
            km._NUDGE_HORIZON.notes = None

    def test_a_pending_rollback_cut_makes_the_look_unbounded_before_any_marked_verdict(self):
        """Round five, medium 1: the parse's cache key carries the pending bare-rollback cut (no file change), and the
        rewind-pending gate sat BELOW the marked verdicts, so a look under a pending cut recorded user-interrupt or working with
        nothing-can-flip; the CLI then refused the resume and the cut was dropped with no record landing, and every later look
        repeated the stale verdict. The gate sits above the marked roads now: a pending cut is an unbounded verdict."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time(); calls = []
        km._TICK_SEEN.clear()
        self.assertEqual(self._look(r, now, calls, _backend_rewind_pending=lambda sid: True), "rewind-pending")
        self.assertIsNone(km._TICK_SEEN[("auto-nudge", SID_OLD)][-2], "a pending cut: unbounded, whatever the marked roads would say")
        self.assertEqual(self._look(r, now + 1, calls, _backend_rewind_pending=lambda sid: False), "working", "the cut dropped: the look evaluates")
        self.assertEqual(calls, [SID_OLD, SID_OLD])

    def test_the_unbounded_notes_are_counted_per_leg(self):
        """Follow-up: a third of looks refused the memo as unbounded at the first gated boot and the counters could not say which
        road; every None note names its leg under memos.nudgeWalk.unboundedBy, the decorator's default under unmarked:<verdict>."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); now = time.time(); calls = []
        km._NUDGE_WALK_STATS["unboundedBy"] = {}
        quiet = dict(_session_working=lambda turns: False, _interrupt_suppresses_nudge=lambda turns, sid="", **k: False, _pending_ops={},
                     _backend_queued=lambda sid: False, _backend_rewind_pending=lambda sid: False, _last_state=lambda sid: ("", 0),
                     _session_awaiting=lambda *a, **k: False)
        with mock.patch.object(km.jd, "load_goals_shared_or_fault", side_effect=lambda sid: (None, OSError("EMFILE"))):
            self._look(r, now, calls, **quiet)
        self.assertEqual(km._NUDGE_WALK_STATS["unboundedBy"], {"storeFault": 1}, "one unbounded look books exactly one leg (round two: the "
                         "decorator's default fires only when no named leg noted the look)")
        km._TICK_SEEN.clear()
        self._look(r, now + 1, calls, _session_working=lambda turns: False, _interrupt_suppresses_nudge=lambda turns, sid="", **k: False,
                   _pending_ops={SID_OLD: [1]})
        self.assertEqual(km._NUDGE_WALK_STATS["unboundedBy"], {"storeFault": 1, "unmarked:queued-input": 1}, km._NUDGE_WALK_STATS["unboundedBy"])
        self.assertIn("unboundedBy", km._PERF_STATS.snapshot()["memos"]["nudgeWalk"])

    def test_every_unbounded_note_site_names_its_leg(self):
        """Round three, low 5: `leg or "unnamed"` absorbed a future None site with no name and nothing failed. A nameless None
        note raises, and a source census over every `_nudge_clock(...)` call in the kernel whose first argument MAY be None
        requires a second, literal argument. The census accepted only a Constant None or an IfExp (the five-lows read, low 4),
        so `_nudge_clock(flip)` with a None flip on one branch stayed green and raised inside the look at run time, where the
        tick's per-session guard drops that session's nudge for the pass; it now takes any first argument that is not a call
        or an arithmetic expression (a name, an attribute, a subscript, a conditional) as one that may be None."""
        import ast, inspect
        def census(src):
            sites, bad = 0, []
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_nudge_clock":
                    first = node.args[0] if node.args else None
                    if first is None or (isinstance(first, ast.Constant) and first.value is not None):
                        continue                                 # a literal instant
                    if isinstance(first, (ast.Call, ast.BinOp, ast.UnaryOp)):
                        continue                                 # an instant computed from one: `anchor + DEADMAN`, `float(x)`
                    sites += 1
                    if len(node.args) < 2 or not (isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                        bad.append(node.lineno)
            return sites, bad
        self.assertEqual(census("_nudge_clock(flip)\n_nudge_clock(rec.at)\n_nudge_clock(x if y else None, 'leg')"), (3, [1, 2]),
                         "a name or an attribute may be None and needs a leg; a conditional with one is fine")
        self.assertEqual(census("_nudge_clock(anchor + DEADMAN)\n_nudge_clock(float(t))\n_nudge_clock(5.0)"), (0, []), "instants")
        sites, bad = census(open(os.path.join(os.path.dirname(HERE), "bin", "romp-kernel"), encoding="utf-8").read())
        self.assertGreaterEqual(sites, 14, "the census found the None note sites: %d" % sites)
        self.assertEqual(bad, [], "every None note names its leg with a literal")
        km._NUDGE_HORIZON.notes = []
        try:
            with self.assertRaises(ValueError):
                km._nudge_clock(None)
        finally:
            km._NUDGE_HORIZON.notes = None

    def test_the_pass_keeps_its_stats_in_a_side_map_and_leaves_the_shared_rows_untouched(self):
        """Follow-up, low 1: the pass wrote _look_stat into the session rows _sessions memoises per cycle and hands out read-only;
        a later reader of a row would have read a stale key. The stats live in a side map keyed by sid, cleared per pass."""
        d = tempfile.mkdtemp(); older, newer = _row(d, SID_OLD, old=True), _row(d, SID_NEW, old=False)
        seen_map = {}
        def look(s, *a, **k):
            seen_map[s["sid"]] = dict(km._NUDGE_LOOK_STATS)                # what the look sees while the pass runs
            return None
        quiet = dict(_auto_nudge_data=lambda: {}, _auto_nudge_resume=lambda: None, _alive_sessions=lambda now, live_map: [older, newer],
                     _wait_for_graph=lambda now, ids: {}, _cleared_ids=lambda: set(), _auto_nudge_on=lambda: True,
                     _auto_nudge_session=look, _compact_suggest_tick=lambda sid, live, now: False,
                     _relay_tick=lambda now, ids: None, _debt_backstop_tick=lambda now: None, _dead_wait_sweep=lambda ids, nudged, now: None,
                     _awaiting_wake_outcomes=lambda now, ids: False, _push_soon=lambda: None, _pop_walk_gate=lambda k: None)
        with mock.patch.multiple(km, **quiet):
            km._auto_nudge_pass(time.time(), {}, True)
        self.assertNotIn("_look_stat", older); self.assertNotIn("_look_stat", newer)
        self.assertEqual(set(seen_map[SID_OLD]), {SID_OLD, SID_NEW}, "both sessions' stats in the side map during the pass")
        self.assertEqual(seen_map[SID_OLD][SID_OLD], km._session_files_stat(older))
        self.assertEqual(km._NUDGE_LOOK_STATS, {}, "the pass's stats are cleared when it ends: a look outside a pass stats for itself")

    def test_the_perf_memos_and_the_boot_row_carry_the_walk(self):
        self.assertIn("nudgeWalk", km._PERF_STATS.snapshot()["memos"])
        self.assertEqual(set(km._PERF_STATS.snapshot()["memos"]["nudgeWalk"]), set(km._NUDGE_WALK_STATS))
        saved = (km._append_restart_cut, km._BOOT_HEALTH_DONE[0], dict(km._NUDGE_WALK_FIRST), km._NUDGE_WALK_FIRST_OPEN[0]); rows = []
        km._append_restart_cut = lambda row: rows.append(row); km._BOOT_HEALTH_DONE[0] = False
        km._NUDGE_WALK_FIRST["skipped"][:] = [SID_OLD[:8]]; km._NUDGE_WALK_FIRST["parsed"][:] = [SID_NEW[:8]]; km._NUDGE_WALK_FIRST["deferred"] = 2
        km._NUDGE_WALK_FIRST_OPEN[0] = True
        try:
            km._boot_health_first_cycle(1.0)
        finally:
            km._append_restart_cut, km._BOOT_HEALTH_DONE[0] = saved[0], saved[1]
            km._NUDGE_WALK_FIRST.update(saved[2]); km._NUDGE_WALK_FIRST_OPEN[0] = saved[3]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["nudgeWalk"], {"skipped": [SID_OLD[:8]], "parsed": [SID_NEW[:8]], "deferred": 2}, rows[0])

    def test_the_row_carries_eight_of_the_sid_and_at_most_forty(self):
        """The attachUnsettled convention for session ids in the restart ledger."""
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); calls = []
        saved = (list(km._NUDGE_WALK_FIRST["parsed"]), km._NUDGE_WALK_FIRST_OPEN[0])
        km._NUDGE_WALK_FIRST["parsed"][:] = ["x"] * 40; km._NUDGE_WALK_FIRST_OPEN[0] = True
        try:
            self._look(r, time.time(), calls)
            self.assertEqual(len(km._NUDGE_WALK_FIRST["parsed"]), 40, "capped at forty")
            km._NUDGE_WALK_FIRST["parsed"][:] = []
            Path(r["path"]).write_text(Path(r["path"]).read_text() + "\n")
            self._look(r, time.time() + 1, calls)
            self.assertEqual(km._NUDGE_WALK_FIRST["parsed"], [SID_OLD[:8]], "eight characters of the sid")
        finally:
            km._NUDGE_WALK_FIRST["parsed"][:] = saved[0]; km._NUDGE_WALK_FIRST_OPEN[0] = saved[1]

    def test_a_wake_only_look_neither_skips_nor_records_and_is_counted(self):
        d = tempfile.mkdtemp(); r = _row(d, SID_OLD, old=True); calls = []; now = time.time()
        with mock.patch.multiple(km, **COMMON), \
             mock.patch.object(km.jd, "parsed_session", side_effect=lambda sid, paths, now: (calls.append(sid), {"turns": STOPPED})[1]), \
             mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None):
            for i in range(2):
                km._auto_nudge_session(r, now + i, {}, {}, {}, alive_ids={r["sid"]}, wake_only=True)
        self.assertEqual(calls, [SID_OLD, SID_OLD], "two parses: a wake-only look never skips")
        self.assertNotIn(("auto-nudge", SID_OLD), km._TICK_SEEN, "and records nothing: the toggle is not a file")
        self.assertEqual(km._NUDGE_WALK_STATS["wakeOnly"], 2)




class RedundancySkipKeepsItsDeadMan(unittest.TestCase):
    """Round one, HIGH: the redundancy skip in _judge_batch parked the goal (answeredAt written) and continued with no clock
    note, so the look recorded a flip of nothing-can-flip; a quiet session never moves its files, every later look skipped,
    and the 6 h parked dead-man never fired. The probe the read used: base fired fired-parked-backstop at now + 6 h + 60 s,
    the head answered None. Drives the real look over the quiet-session harness (its OWN kernel instance) with a REAL
    transcript file, since the gate stats the files."""

    def setUp(self):
        import test_nudge_memo_deadlock as M
        self.M = M; self.K = M.km
        self.h = M.MemoDeadlock("test_a_parked_goal_sleeps_silently"); self.h.setUp()
        # the full walk reaches the debt legs, which the borrowed harness neither patches nor restores (round four, low 3)
        self._debt_saved = {n: getattr(self.K, n) for n in ("_debt_reminder_outcomes", "_fire_debt_reminder", "_debt_asks", "_postal_wait_maps")}
        self.K._debt_reminder_outcomes = lambda sid, lt, now: None
        self.K._fire_debt_reminder = lambda sid, now, alive_ids: False
        self.K._TICK_SEEN.clear()
        for k, v in list(self.K._NUDGE_WALK_STATS.items()):
            self.K._NUDGE_WALK_STATS[k] = {} if isinstance(v, dict) else 0
        self.K._NUDGE_LOOK_STATS.clear()
        d = tempfile.mkdtemp(); self.path = os.path.join(d, M.SID + ".jsonl")
        Path(self.path).write_text("{}\n")

    def tearDown(self):
        for n, v in self._debt_saved.items():
            setattr(self.K, n, v)
        self.h.tearDown()

    def _tick(self, now):
        nudged = dict(self.K._auto_nudge_data().get("nudged", {}))
        return self.K._auto_nudge_session({"sid": self.M.SID, "path": self.path}, now, {}, nudged, {})

    def test_a_redundancy_skip_notes_the_parked_dead_man_and_the_backstop_fires_on_time(self):
        M, K = self.M, self.K
        self.h.judge_replies = [True]                                     # the judge rules the report redundant: a skip, a park
        self.assertIs(self._tick(M.NOW), False, "the full road: nothing fired")
        rows = self.h._rows()
        self.assertEqual([r.get("verdict") for r in rows], ["skipped-redundant"], rows)
        memo = K._TICK_SEEN.get(("auto-nudge", M.SID))
        self.assertIsNotNone(memo, "the look recorded its memo")
        flip = memo[-2]
        self.assertEqual(flip, M.NOW + K.AWAITING_DEADMAN_SECS, "the skip notes the parked dead-man it opened, not nothing-can-flip")
        self.assertIs(self._tick(M.NOW + 60), False, "the skip wrote the ledger, a keyed file: this look re-evaluates once")
        self.assertIsNone(self._tick(M.NOW + 120), "inside the window with nothing moved: the parse is skipped, the verdict repeated")
        self.assertEqual(K._NUDGE_WALK_STATS["skippedParses"], 1)
        late = M.NOW + K.AWAITING_DEADMAN_SECS + 60
        self.assertIs(self._tick(late), True, "past the dead-man the look evaluates and the backstop fires")
        self.assertEqual([r.get("verdict") for r in self.h._rows()][-1], "fired-parked-backstop")
        self.assertEqual(len(self.h.sent), 1)
        self.assertEqual(K._NUDGE_WALK_STATS["clockDue"], 1)




class PassSnapshotsAreNewerThanTheKey(unittest.TestCase):
    """Round seven, medium: the pass parsed the clear set once at its top and handed the same object to every look, while each
    look statted the files for itself; an undo landing between that snapshot and a look moved the clears log and the store, the
    look re-parsed, read the store fresh, but read the STALE clear set, skipped the un-cleared goal and recorded a skippable memo
    under the post-undo stat, silencing the goal. The pass now takes every session's stat before any snapshot and hands it to
    the look, so the memo's key is older than every input the verdict read; the next look sees the moved stat and fires."""

    def setUp(self):
        import test_nudge_memo_deadlock as M
        self.M = M; self.K = M.km
        self.h = M.MemoDeadlock("test_a_parked_goal_sleeps_silently"); self.h.setUp()
        self.K._TICK_SEEN.clear()
        for k, v in list(self.K._NUDGE_WALK_STATS.items()):
            self.K._NUDGE_WALK_STATS[k] = {} if isinstance(v, dict) else 0
        self.K._NUDGE_LOOK_STATS.clear()
        d = tempfile.mkdtemp(); self.path = os.path.join(d, M.SID + ".jsonl")
        Path(self.path).write_text("{}\n")
        self._debt_saved = {n: getattr(self.K, n) for n in ("_debt_reminder_outcomes", "_fire_debt_reminder", "_debt_asks", "_postal_wait_maps")}
        self.K._debt_reminder_outcomes = lambda sid, lt, now: None
        self.K._fire_debt_reminder = lambda sid, now, alive_ids: False

    def tearDown(self):
        for n, v in self._debt_saved.items():
            setattr(self.K, n, v)
        self.h.tearDown()

    def test_an_undo_between_the_pass_top_snapshot_and_the_look_cannot_silence_the_goal(self):
        M, K = self.M, self.K
        row = {"sid": M.SID, "path": self.path, "name": "web", "mtime": time.time()}
        self.h.judge_replies = [False]                                    # the judge rules the report NOT redundant: the goal is due
        cleared_log = K.jd.STATE / "cleared.jsonl"
        cleared_log.write_text("")
        reads = []
        def stale_cleared_then_undo():
            reads.append(1)
            if len(reads) == 1:                                           # the pass-top read happens; the user's undo lands right after it:
                cleared_log.write_text(json.dumps({"undo": M.G1}) + "\n")   # the clears log and the store both move, the snapshot is stale
                self.h.store["nodes"][M.G1]["cleared"] = False
                return {M.G1}
            return set()
        self.h.store["nodes"][M.G1]["cleared"] = True                     # cleared before the pass
        quiet = dict(_auto_nudge_data=lambda: {}, _auto_nudge_resume=lambda: None, _alive_sessions=lambda now, live_map: [row],
                     _wait_for_graph=lambda now, ids: {}, _cleared_ids=stale_cleared_then_undo, _auto_nudge_on=lambda: True,
                     _compact_suggest_tick=lambda sid, live, now: False, _relay_tick=lambda now, ids: None,
                     _debt_backstop_tick=lambda now: None, _dead_wait_sweep=lambda ids, nudged, now: None,
                     _awaiting_wake_outcomes=lambda now, ids: False, _push_soon=lambda: None, _pop_walk_gate=lambda k: None,
                     _put_walk_gate=lambda k, g, now: None)
        with mock.patch.multiple(K, **quiet):
            K._auto_nudge_pass(M.NOW, {}, True)                           # the pass whose snapshot went stale
            K._auto_nudge_pass(M.NOW + 5, {}, True)                       # the next pass: the truth
        self.assertEqual(len(self.h.sent), 1, "the un-cleared goal's nudge went out on the next pass: sent %r, rows %r, walk %r"
                         % (len(self.h.sent), [r.get("verdict") for r in self.h._rows()], K._NUDGE_WALK_STATS))


if __name__ == "__main__":
    unittest.main()
