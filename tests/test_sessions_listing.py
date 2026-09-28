#!/usr/bin/env python3
"""GET /sessions is served from the cycle's snapshot (plans/sessions-route-from-the-cycle.md): the pusher's cycle builds the
listing once when its exact key moved (the live rows, the names snapshot, the working-notes store, the registry revision, the
compacting bits, the launch errors) and every request serves the kept JSON; a request before the first cycle builds once and is served from the
kept listing by the next; ?threads=1 rides its own key. The postal bus reads this route for its roster (list_agents, the
send's liveness check), so a session's start, rename and death reach the roster within one cycle, and the rows keep the
fields the bus reads. Hermetic: a temp state root, two synthetic sessions on disk, the live map stubbed."""
import http.client
import inspect
from unittest import mock
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from romp_load import load_source
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_sessions_listing", os.path.join(BIN, "romp-kernel"))
jd = km.jd
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))   # the module name the kernel reads the revision through
NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
SID2 = "11111111-2222-3333-4444-565656565656"
FSID = "11111111-2222-3333-4444-888888888888"       # a transcript id a /clear minted under SID
BUS_FIELDS = {"id", "name", "state", "dir", "bg", "fg", "lastSid", "compacting", "working", "backend"}   # what the postal bus's
#                                                                                                          roster and the picker read


class _Listing(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, km.WORKING_DIR, km.Sessions.live, km._sdk)
        names = td / "names"; names.mkdir()
        proj = td / "projects"; proj.mkdir()
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"; jd.GOALDIR.mkdir()
        jd.STATE = td
        km.NAMES = names
        km.WORKING_DIR = td / "working"; km.WORKING_DIR.mkdir()
        km._sdk = lambda: None
        cdir = td / "work"; cdir.mkdir()
        self.cdir = cdir
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.pdir, self.names = pdir, names
        rec = {"type": "user", "timestamp": "2026-06-11T00:00:00.000Z", "uuid": "u1", "parentUuid": None, "promptSource": "typed",
               "message": {"role": "user", "content": "hello there"}}
        for sid, name in ((SID, "web"), (SID2, "api")):
            (pdir / (sid + ".jsonl")).write_text(json.dumps(rec) + "\n")
            (names / sid).write_text("%s\t%s\t#abcdef\n" % (name, str(cdir)))
        meta = {"state": "waiting", "since": NOW - 5, "model": "", "effort": "", "context": None, "compactPct": None,
                "color": None, "mode": "", "backend": "sdk"}
        self.row = {SID: dict(meta), SID2: dict(meta)}
        km.Sessions.live = lambda: dict(self.row)
        self._reset()
        with km._clients_lock:
            self.saved_clients = list(km._clients); km._clients[:] = []

    def _reset(self):
        reset = getattr(km, "_sessions_listing_reset", None)
        if reset is not None:
            reset()
        else:                                                          # absent at the base
            getattr(km, "_SESSIONS_LISTING", {}).update({"key": None, "rows": None, "json": None, "threads": None, "threadsKey": None,
                                                         "built": 0, "served": 0, "requestBuilt": 0, "missBy": {}})

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, km.WORKING_DIR, km.Sessions.live, km._sdk) = self.saved
        with km._clients_lock:
            km._clients[:] = self.saved_clients
        self._reset()
        for slot in ("snapshot", "sessions", "paths", "names", "files_stat", "files_dirty"):
            setattr(km._live_scope, slot, None)
        self.td.cleanup()

    def _cycle(self):
        km._pusher_cycle()

    def _body(self, threads=False):
        serve = getattr(km, "_sessions_listing_serve", None)
        if serve is None:                                            # the base: the route builds per request
            return km._session_rows() + (km._thread_rows() if threads else [])
        return json.loads(serve(threads=threads))

    def _stats(self):
        L = getattr(km, "_SESSIONS_LISTING", {})
        return {k: L.get(k, 0) for k in ("built", "served", "requestBuilt")}, dict(L.get("missBy", {}))

    def _count_builds(self, builds):
        """A seam on the row builder: the cycle's (_session_rows_from) on the head, the request's (_session_rows) at the base."""
        name = "_session_rows_from" if hasattr(km, "_session_rows_from") else "_session_rows"
        real = getattr(km, name)
        setattr(km, name, (lambda live_map: (builds.append(1), real(live_map))[1]) if name == "_session_rows_from"
                else (lambda: (builds.append(1), real())[1]))
        return name, real


class OneListingPerChange(_Listing):
    def test_two_cycles_with_nothing_moved_build_once_and_serve_equal_bodies(self):
        builds = []
        name, real = self._count_builds(builds)
        try:
            self._cycle(); self._cycle(); self._cycle()
            a, b = self._body(), self._body()
        finally:
            setattr(km, name, real)
        self.assertEqual(len(builds), 1, "one build across three quiet cycles (the base built per request, never per cycle)")
        self.assertEqual(a, b)
        self.assertEqual({r["id"] for r in a}, {SID, SID2})
        st, miss = self._stats()
        self.assertEqual((st["built"], st["served"], st["requestBuilt"]), (1, 2, 0))
        self.assertEqual(miss, {"first": 1})
        for r in a:
            self.assertTrue(BUS_FIELDS <= set(r), "the fields the bus reads stand: %r" % sorted(r))

    def test_each_key_input_moving_rebuilds_once_and_the_served_row_carries_the_change(self):
        self._cycle()
        self.row[SID]["state"] = "working"                                           # a state change (the live row)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["state"], "working")
        (self.names / SID).write_text("web-two\t%s\t#abcdef\n" % str(self.cdir))     # a rename (the names snapshot)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["name"], "web-two")
        (km.WORKING_DIR / SID2).write_text("editing the listing")                    # a working note (the notes store)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID2)["working"], "editing the listing")
        sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": SID2})   # a registry write (lastSid rides it)
        self._cycle()
        st, miss = self._stats()
        self.assertEqual(st["built"], 5, "one build per moved input: %r" % (miss,))
        self.assertEqual(set(miss), {"first", "rows", "names", "notes", "registry"}, "each miss names its input: %r" % miss)
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], 5, "and a quiet cycle builds nothing")

    def test_a_rows_launch_error_rides_the_listing_and_its_change_rebuilds_once(self):
        # `romp compact --wait` reads the row's launch error beside its compacting bit (the second review of the native
        # compaction, 2026-09-21): a compaction that ended loudly drops the compacting bit exactly as a clean end does and
        # leaves the notice on the backend's launch error, so the row carries that record, and it is a key input, or the
        # kept listing would serve the pre-failure row until some other input moved.
        errs, reads = {}, []
        saved = km._launch_error
        km._launch_error = lambda sid: (reads.append(str(sid)), errs.get(str(sid)))[1]
        self.addCleanup(setattr, km, "_launch_error", saved)
        self._cycle()
        self.assertIsNone(next(r for r in self._body() if r["id"] == SID)["launchError"], "no failure: the field is there and empty")
        # The shared read is the refresh's pair memo (listing_pairs), which bounds the listing to one read per session:
        # the cycle's launch-error memo is filled by the key's read and hit by no reader after it, kept as a backstop for a
        # second cycle reader outside the pair (the post-merge review of the listing pairing, 2026-09-22).
        self.assertEqual(reads.count(SID), 1, "one backend read per session per cycle: the key and the row share the refresh's pair memo, listing_pairs (%r)" % reads)
        errs[SID] = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                     "at": NOW + 1.5, "limit": False, "noRetry": True}
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["launchError"], errs[SID], "the backend's record, whole")
        self.assertEqual(self._stats()[0]["built"], 2, "the notice is a key input: one rebuild")
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], 2, "and a quiet cycle builds nothing")
        # a second compaction failing the same way leaves the same words at a new stamp, and `romp compact --wait`
        # tells that end from the standing notice by the stamp; so the stamp is half of the key, or the kept listing
        # would serve the old record over the fresh loud end and the wait would print done (the post-merge review of
        # the exit clause, 2026-09-21: a text-only key was pinned by nothing)
        errs[SID] = dict(errs[SID], at=NOW + 7.5)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["launchError"], errs[SID], "the new stamp is served")
        self.assertEqual(self._stats()[0]["built"], 3, "the same words at a new stamp are a key input: one rebuild")
        errs.pop(SID)                                                                  # the next accepted turn clears it
        self._cycle()
        self.assertIsNone(next(r for r in self._body() if r["id"] == SID)["launchError"])
        self.assertEqual(self._stats()[0]["built"], 4)

    def _compaction_seams(self, comp, errs, reads=None, land=None):
        """The listing's two per-row backend reads stubbed as the world's compacting bit (`comp`, sid to bool) and launch
        error (`errs`, sid to record), the seams the launch-error test above uses; `land`, when given, runs after each
        read for SID (the hook a loud end lands through, between two reads)."""
        saved = (km._launch_error, km._compacting_now)

        def launch_error(sid):
            v = errs.get(str(sid))
            if reads is not None:
                reads.append(str(sid))
            if land is not None and str(sid) == SID:
                land()
            return v

        def compacting(sid, tm=None, path=None):
            v = comp.get(str(sid), False)
            if land is not None and str(sid) == SID:
                land()
            return v
        km._launch_error, km._compacting_now = launch_error, compacting
        self.addCleanup(lambda: (setattr(km, "_launch_error", saved[0]), setattr(km, "_compacting_now", saved[1])))

    def test_a_loud_end_between_the_keys_read_and_the_rows_build_is_never_served_as_a_clean_end(self):
        """The row read compacting fresh while its launch error came from the cycle memo filled at the key's read, so a
        loud end landing between the two reads (the bit falls, the notice lands) built a row reading compacting False with
        no notice, a clean end's shape, whenever another key input moved in the same cycle or on the first cycle (a quiet
        cycle serves the prior rows and never showed it), and `romp compact --wait` printed done over an uncompacted thread (the post-merge review
        of the native compaction, 2026-09-21). The key now reads the pair once per sid per cycle, compacting then notice,
        and the row takes that read: the served row carries the pair as the key read it, and the next cycle's key reads
        the end."""
        comp, errs, reads = {SID: True, SID2: False}, {}, []
        self._compaction_seams(comp, errs, reads)
        notice = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                  "at": NOW + 1.5, "limit": False, "noRetry": True}
        self._cycle()
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (True, None), "mid-compaction, no notice")
        real = km._session_rows_from

        def build_after_the_end(live_map):                  # the loud end lands after the key's read, before the rows' build
            comp[SID] = False; errs[SID] = notice
            return real(live_map)
        km._session_rows_from = build_after_the_end
        try:
            self.row[SID2]["state"] = "working"             # another key input moves: this cycle rebuilds the listing
            reads.clear()
            self._cycle()
        finally:
            km._session_rows_from = real
        self.assertEqual(self._stats()[0]["built"], 2, "the moved input rebuilt the listing this cycle")
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (True, None),
                         "the row carries the pair as the key read it, never (False, None), the clean end's shape the base built")
        self.assertEqual(reads.count(SID), 1, "still one backend read per session per cycle: %r" % reads)
        self._cycle()                                       # the next cycle's key reads the end: the bit down, the notice up
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (False, notice), "the loud end, one cycle on")
        st, miss = self._stats()
        self.assertEqual((st["built"], miss.get("rows")), (3, 2), "one rebuild per cycle the rows moved in: %r" % miss)

    def test_a_loud_end_between_the_pairs_two_reads_yields_compacting_with_the_notice(self):
        """The pair is read compacting then notice, so a loud end landing between its two reads yields (True, notice),
        which the wait already judges as a loud end; read notice then compacting it would yield (False, None), the clean
        end's shape (2026-09-21)."""
        comp, errs, landed = {SID: True, SID2: False}, {}, []
        notice = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                  "at": NOW + 1.5, "limit": False, "noRetry": True}

        def land():                                         # the loud end lands right after the first of SID's two reads
            if landed == ["armed"]:
                landed[:] = ["landed"]; comp[SID] = False; errs[SID] = notice
        self._compaction_seams(comp, errs, land=land)
        real_key = km._sessions_listing_key

        def key_arming_the_hook(live_map, names):           # armed inside the listing's key alone: a read of the same seams
            if arm[0]:                                      #  from elsewhere in the cycle cannot land the end
                landed.append("armed")
            try:
                return real_key(live_map, names)
            finally:
                if landed and landed[-1] == "armed":
                    landed.pop()
        arm = [False]
        km._sessions_listing_key = key_arming_the_hook
        self.addCleanup(setattr, km, "_sessions_listing_key", real_key)
        self._cycle()
        self.row[SID2]["state"] = "working"                 # another key input moves: this cycle rebuilds the listing
        arm[0] = True
        self._cycle()
        self.assertEqual(landed, ["landed"], "the end landed between the pair's reads, inside the listing's key")
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (True, notice),
                         "compacting read first: the end is read with the bit still up, a loud end to the wait")
        self.assertEqual(self._stats()[0]["built"], 2)

    def _loud_end_landing_on_the_first_read(self):
        """The two seams stubbed as the order case above stubs them: SID mid-compaction with no notice, and the Codex loud
        end (the compacting bit down and the launch-error notice up, written together) landing on SID's first seam read
        after the returned list is armed, once, since the seams call the hook after every read for SID and a second landing
        would say nothing about the order (2026-09-22). Returns (landed, notice, memos): append "armed" to `landed` right
        before the read under test, and it reads ["landed"] once the end has landed; `memos` records the thread's pair memo
        as it stood at that read, where the memo-less branch is decided (a memo checked absent after the build alone says
        only that none was LEFT open: a build that opened one around itself and closed it in a finally would pass that
        check while the branch this pin is about is no longer the one taken, the review of this pin, 2026-09-22)."""
        comp, errs, landed, memos = {SID: True, SID2: False}, {}, [], []
        notice = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                  "at": NOW + 1.5, "limit": False, "noRetry": True}

        def land():
            if landed == ["armed"]:
                memo = getattr(km._live_scope, "listing_pairs", None)
                memos.append(None if memo is None else dict(memo))    # as it stood at this read, not as it is filled later
                landed[:] = ["landed"]; comp[SID] = False; errs[SID] = notice
        self._compaction_seams(comp, errs, land=land)
        return landed, notice, memos

    def test_a_requests_own_row_build_with_no_refresh_open_reads_the_pair_compacting_then_notice(self):
        """The request path: a request thread's own build (the serve before the first cycle, the fault build after a
        cycle's build raised, GET /sessions/by-fsid) runs with no pair memo open on its thread, so the pair reader takes
        its memo-less branch and reads the two seams fresh, compacting then notice. The order case above arms its hook
        inside the listing's key, the refresh path, and pinned nothing here: with the memo-less branch rewritten to read
        the launch error before the compacting bit, this module and every sibling that stubs the two seams stayed green
        (the post-merge review of the listing pairing, 2026-09-22). Under that rewrite a loud end landing between the two
        reads gives a request's row (False, None), the clean end's shape, and `romp compact --wait` polling the route
        before the first cycle or while the kept listing is stale from a fault prints done over an uncompacted thread.
        The pin: the memo asserted absent before one direct row build on this thread and at the read the end lands on,
        the row's name and dir checked as the head's before its pair is read (the builder's catch-all fallback row, which
        any other field's raise produces, carries (False, None) too, so a red on the pair alone could not tell the order
        from a raise elsewhere), then (True, notice), and no memo left open after."""
        landed, notice, memos = self._loud_end_landing_on_the_first_read()
        self.assertIsNone(getattr(km._live_scope, "listing_pairs", None), "no refresh is open on this thread: the memo-less branch")
        landed.append("armed")
        row = km._session_listing_row(SID, self.row[SID], {}, None)
        self.assertEqual(landed, ["landed"], "the end landed between the pair's two reads, inside the one row build")
        self.assertEqual((row["name"], row["dir"]), ("web", str(self.cdir)),
                         "the head's row, not the builder's catch-all fallback (the sid's first eight characters, no dir), whose "
                         "pair is (False, None) as well: a red on the pair below is the read order, not a raise elsewhere")
        self.assertEqual((row["compacting"], row["launchError"]), (True, notice),
                         "compacting read first on the request path too: the end is read with the bit still up, a loud end to the wait")
        self.assertEqual(memos, [None], "no pair memo at the read the end landed on: the memo-less branch, not one the build opened around itself")
        self.assertIsNone(getattr(km._live_scope, "listing_pairs", None), "and none left open after the build")

    def test_the_by_fsid_route_reaches_the_same_memo_less_branch_and_reads_the_pair_in_the_same_order(self):
        """GET /sessions/by-fsid builds the one row it serves through the same row builder on the request thread, with no
        refresh open: the same memo-less branch, pinned on its own route since the route's other reads (the live map, the
        registry's lastSid, the transcript path) come before the row build and touch neither seam; the same guards as the
        direct build's, the memo recorded at the landing read and the row checked as the head's before its pair
        (2026-09-22)."""
        landed, notice, memos = self._loud_end_landing_on_the_first_read()
        self.assertIsNone(getattr(km._live_scope, "listing_pairs", None), "no refresh is open on this thread")
        landed.append("armed")
        code, row = km._session_by_fsid(SID)
        self.assertEqual(code, 200, row)
        self.assertEqual(row["id"], SID)
        self.assertEqual(landed, ["landed"], "the end landed between the pair's two reads, inside the route's one row build")
        self.assertEqual((row["name"], row["dir"]), ("web", str(self.cdir)),
                         "the head's row, not the builder's catch-all fallback: a red on the pair below is the read order")
        self.assertEqual((row["compacting"], row["launchError"]), (True, notice),
                         "the by-fsid row reads the pair compacting then notice, as the listing's rows do")
        self.assertEqual(memos, [None], "no pair memo at the read the end landed on: the route reached the memo-less branch")
        self.assertIsNone(getattr(km._live_scope, "listing_pairs", None), "and none left open after the route's build")

    def test_the_compacting_bit_alone_moving_rebuilds_once_each_way_and_the_row_carries_it(self):
        """The compacting bit is a key input in its own right (the design's compacting edge): a compaction starting and
        clearing with nothing else moving rebuilds the listing once each way and the served row carries the bit, so the
        pair's key half cannot be the notice's identity alone (2026-09-21)."""
        comp, errs = {SID: False, SID2: False}, {}
        self._compaction_seams(comp, errs)
        self._cycle()
        n0 = self._stats()[0]["built"]
        comp[SID] = True                                    # the compaction starts: the bit alone moves
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], n0 + 1, "the bit rising rebuilt once")
        self.assertTrue(next(r for r in self._body() if r["id"] == SID)["compacting"], "and the served row reads compacting")
        comp[SID] = False                                   # it clears: the bit alone moves back
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], n0 + 2, "the bit falling rebuilt once")
        self.assertFalse(next(r for r in self._body() if r["id"] == SID)["compacting"], "and the served row reads not compacting")
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], n0 + 2, "a quiet cycle builds nothing")

    def test_a_rows_compaction_end_record_rides_the_listing_and_moves_the_key_on_its_own(self):
        # The record `romp compact --wait` judges (the post-merge review of the wait, 2026-09-21): the backend's
        # bracket-end counter with the last end's kind, words and stamp, which the next accepted turn does not erase (it
        # clears launchError, within milliseconds when a message parked behind the compaction drains at a loud end's
        # poke). The row carries it whole, and it is a key input ON ITS OWN: in that race every other input stands still
        # (the state back to waiting, compacting False, launchError None again), so a record outside the key would leave
        # the row built before the end served until something else moved, and a wait reading it would never see the end.
        recs, reads = {}, []
        saved = km._compact_end
        km._compact_end = lambda sid: (reads.append(str(sid)), recs.get(str(sid)))[1]
        self.addCleanup(setattr, km, "_compact_end", saved)
        self._cycle()
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertIn("compactEnd", row, "the field is there")
        self.assertIsNone(row["compactEnd"], "a backend that keeps no record: empty, never absent")
        self.assertEqual(reads.count(SID), 1, "one backend read per session per cycle: the key and the row share the memo (%r)" % reads)
        recs[SID] = {"ends": 0, "kind": "", "text": "", "at": None}                       # a Codex row before any end
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["compactEnd"], recs[SID], "the record, whole")
        self.assertEqual(self._stats()[0]["built"], 2, "the record is a key input: one rebuild")
        loud = {"ends": 1, "kind": "loud", "at": NOW + 1.5,
                "text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was"}
        recs[SID] = loud                     # the loud end with its notice already erased: launchError None, compacting False, state waiting
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["compactEnd"], loud)
        self.assertEqual(self._stats()[0]["built"], 3, "the end alone moved the key, nothing else did")
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], 3, "and a quiet cycle builds nothing")
        recs[SID] = {"ends": 2, "kind": "clean", "text": "", "at": NOW + 9.0}
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["compactEnd"]["kind"], "clean")
        self.assertEqual(self._stats()[0]["built"], 4)
        self.assertEqual(reads.count(SID), 5, "still one read per cycle (%r)" % reads)

    def test_the_rows_compaction_end_record_is_read_from_the_owning_backend(self):
        # The live path (the post-merge review, 2026-09-21): the kernel's own read of the backend's compact_end, not a
        # stub of it. SID's backend exposes the method; SID2's has none (the SDK backend's shape, which inherits the
        # contract's None default; a backend from before the method reads the same way); and a record that is not a
        # dict reads as None, never as a row that raises.
        rec = {"ends": 1, "kind": "loud", "at": NOW,
               "text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was"}
        holder = {"rec": rec}

        class WithRecord:
            def compact_end(self, sid):
                return holder["rec"] if str(sid) == SID else None
        saved = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: WithRecord() if str(sid) == SID else object())
        self.addCleanup(setattr, km.Sessions, "backend_for", saved)
        self._cycle()
        rows = {r["id"]: r for r in self._body()}
        self.assertEqual(rows[SID]["compactEnd"], rec, "the backend's record, read live and served whole")
        self.assertIsNone(rows[SID2]["compactEnd"], "a backend without the method: None, and the row is still built")
        self.assertEqual(rows[SID2]["name"], "api", "the row is the full row, not the minimal one a raise leaves")
        holder["rec"] = ["not", "a", "record"]
        self._cycle()
        rows = {r["id"]: r for r in self._body()}
        self.assertIsNone(rows[SID]["compactEnd"], "a record that is not a dict reads as none")
        self.assertEqual(rows[SID]["name"], "web")

    def test_a_start_a_rename_and_a_death_reach_the_roster_within_one_cycle(self):
        """The postal bus's roster (list_agents, the send's liveness check) reads this route: a session that started is
        listed after one cycle, a renamed one carries its name, a dead one is gone (absence reads as death downstream)."""
        del self.row[SID2]
        self._cycle()
        self.assertEqual({r["id"] for r in self._body()}, {SID})
        self.row[SID2] = dict(self.row[SID])                                          # the start
        self._cycle()
        self.assertEqual({r["id"] for r in self._body()}, {SID, SID2})
        del self.row[SID]                                                             # the death
        self._cycle()
        self.assertEqual({r["id"] for r in self._body()}, {SID2})

    def test_a_request_before_the_first_cycle_builds_once_and_is_served_from_the_kept_listing_by_the_next(self):
        builds = []
        name, real = self._count_builds(builds)
        try:
            a = self._body(); b = self._body()
            self.assertEqual(len(builds), 1, "one build, from the registry (no cycle yet), served to both requests (the base built twice)")
            self._cycle()
            self.assertEqual(len(builds), 2, "the first cycle rebuilds under its key from its snapshot")
            self.assertEqual(self._body(), a)
        finally:
            setattr(km, name, real)
        st, _ = self._stats()
        self.assertEqual((st["requestBuilt"], st["built"]), (1, 1))

    def test_a_fault_in_the_listing_never_skips_the_parked_ops_and_requests_build_for_themselves_until_a_build_lands(self):
        """1752 round two, the medium: the listing job sat inside the pending ops' try, so a raise in the key or the build skipped
        _apply_pending_ops for the cycle and the stderr line blamed pending operations; now the job has its own try and line,
        and the served listing falls back to a per-request build while the kept one is stale from a fault."""
        import io
        self._cycle()
        ops = []
        real_ops = km._apply_pending_ops
        real_build = getattr(km, "_session_rows_from", km._session_rows)
        name = "_session_rows_from" if hasattr(km, "_session_rows_from") else "_session_rows"
        km._apply_pending_ops = lambda: ops.append(1)
        setattr(km, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the build failed")))
        err = io.StringIO()
        try:
            self.row[SID]["state"] = "working"                       # the key moves: the cycle rebuilds and the build raises
            with mock.patch.object(sys, "stderr", err):
                self._cycle()
            self.assertEqual(ops, [1], "the parked ops still ran this cycle (the base skipped them)")
            self.assertIn("sessions-listing:", err.getvalue(), "the line names the listing, not pending operations: %r" % err.getvalue()[:200])
            setattr(km, name, real_build)
            body = self._body()
            self.assertEqual(next(r for r in body if r["id"] == SID)["state"], "working", "a request builds for itself while the kept listing is stale")
            st, _ = self._stats()
            self.assertEqual(getattr(km, "_SESSIONS_LISTING", {}).get("faultBuilt"), 1, "counted as a fault build")
            self._cycle()                                              # the next cycle's build lands: served from memory again
            self.assertIsNone(getattr(km, "_SESSIONS_LISTING", {}).get("fault"))
            self._body()
            self.assertEqual(getattr(km, "_SESSIONS_LISTING", {}).get("faultBuilt"), 1, "no fault build once a build landed")
        finally:
            km._apply_pending_ops = real_ops
            setattr(km, name, real_build)

    def test_a_plain_rebuild_keeps_the_thread_rows_under_their_own_key(self):
        self._cycle()
        self._body(threads=True)
        L = getattr(km, "_SESSIONS_LISTING", {})
        tkey = L.get("threadsKey")
        self.assertIsNotNone(tkey, "the thread rows were built and keyed")
        self.row[SID]["state"] = "working"                           # a plain input moves: the listing rebuilds
        self._cycle()
        self.assertEqual(L.get("threadsKey"), tkey, "the thread rows keep their key")
        self.assertIsNotNone(L.get("threads"), "and their rows: no thread build for a plain rebuild")
        (jd.STATE / "session-flags.json").write_text(json.dumps({SID: {"postalServiceOff": True}}))   # the mailbox fields' store
        self._body(threads=True)
        self.assertNotEqual(L.get("threadsKey"), tkey, "the flags store's stat is in the threads key")

    def test_the_notes_key_carries_the_inode_as_the_notes_memo_does(self):
        key = getattr(km, "_sessions_listing_key", None)
        self.assertIsNotNone(key, "the listing key exists")
        (km.WORKING_DIR / SID).write_text("a note")
        k = key(self.row, {})
        notes = k[2]
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(notes[0]), 4, "(name, mtime_ns, size, ino): %r" % (notes[0],))

    def test_the_route_serves_the_kept_json_and_threads_ride_their_own_key(self):
        self._cycle()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def get(path):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                c.request("GET", path, headers={"X-Romp-Token": km.TOKEN})
                resp = c.getresponse(); body = resp.read().decode(); c.close()
                return resp.status, body
            status, body = get("/sessions")
            self.assertEqual(status, 200)
            self.assertEqual(body, getattr(km, "_SESSIONS_LISTING", {}).get("json"), "the kept JSON, byte for byte (the base keeps none)")
            self.assertEqual({r["id"] for r in json.loads(body)}, {SID, SID2})
            status, body = get("/sessions?threads=1")
            self.assertEqual(status, 200)
            rows = json.loads(body)
            self.assertIsInstance(rows, list)
            self.assertEqual({r["id"] for r in rows}, {SID, SID2}, "no thread sessions: the same rows")
        finally:
            srv.shutdown(); srv.server_close()

    def test_a_registry_write_of_a_field_no_row_reads_keeps_the_listing_across_five_cycles_and_a_read_field_rebuilds_once(self):
        """The design's rule 1 (one listing per change) on the registry input: on the deploy boot of 2026-09-15 the listing was
        rebuilt every cycle (built 778 in 776 s, missBy registry 767) because the key carried REG_REV, which counts every
        write, and the backend writes fields no row reads each cycle. The key's registry component is the rows revision,
        moved only by a change to lastSid, threadOf or alive."""
        builds = []
        name, real = self._count_builds(builds)
        sdkdir_saved = jd.SDKDIR
        jd.SDKDIR = Path(str(jd.STATE)) / "sdk"                  # the row's lastSid read (jd._sdk_last_sid) over this test's registry
        try:
            sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": SID})
            self._cycle()
            n0 = len(builds)
            miss0 = self._stats()[1].get("registry", 0)
            for i in range(5):
                sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": SID, "lastActivity": NOW + i, "ctxTokens": 100 + i})
                self._cycle()
            self.assertEqual(len(builds), n0, "five writes of fields no row reads: the listing is kept (the base rebuilt on each)")
            self.assertEqual(self._stats()[1].get("registry", 0), miss0, "and none counted as a registry miss")
            sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": FSID, "lastActivity": NOW + 9})
            self._cycle()
            self.assertEqual(len(builds), n0 + 1, "a change to a field a row reads rebuilds once")
            self.assertEqual(self._stats()[1].get("registry", 0), miss0 + 1, "counted as the registry miss it is")
            row = [r for r in self._body() if r["id"] == SID][0]
            self.assertEqual(row["lastSid"], FSID, "and the served row carries the new transcript id")
            self._cycle()
            self.assertEqual(len(builds), n0 + 1, "the next cycle keeps it")
            sb.write_reg(str(jd.STATE), SID2, {"sid": SID2, "alive": True, "threadOf": SID})
            self._cycle()
            self.assertEqual(len(builds), n0 + 2, "a registration becoming a comment thread rebuilds once (threadOf is read)")
        finally:
            setattr(km, name, real)
            jd.SDKDIR = sdkdir_saved

    def test_the_registry_revision_moves_on_the_one_write_path_and_every_writer_goes_through_it(self):
        """Every write, replace or removal of a file under the SDK registry directory (STATE/sdk/<sid>.json), across kernel/,
        cli/, postal/ and bin/, goes through write_reg: the census walks each write site and reads the path expression
        behind it for the SDK registry's marks (`_reg_path(state_dir`, `/ "sdk" /`, `"sdk"` beside `.json`); the Codex
        backend's registry.json is its own table, which no listing field reads."""
        src = inspect.getsource(sb)
        self.assertIn("REG_REV[0] += 1", inspect.getsource(sb.write_reg), "the write path bumps the revision (the base has none)")
        rev = getattr(sb, "reg_rev", lambda: 0)
        rev0 = rev()
        sb.write_reg(str(jd.STATE), SID2, {"sid": SID2, "alive": True})
        self.assertEqual(rev(), rev0 + 1)
        writers = [m.start() for m in re.finditer(r"\bwrite_reg\(", src)]
        self.assertGreaterEqual(len(writers), 10, "the registry's writers all call write_reg")
        root = Path(os.path.dirname(HERE))
        marks = re.compile(r"_reg_path\((?:self\.)?state_dir|/ \"sdk\" /|\"sdk\"[^\n]*\.json|\.json[^\n]*\"sdk\"")   # the SDK registry's
        #                                                              marks alone: the Codex backend's registry.json is its own table, read by no listing field
        offenders = []
        for sub in ("kernel", "cli", "postal", "bin"):
            for f in sorted((root / sub).glob("*")):
                if not f.is_file() or f.suffix not in (".py", "") or f.name.endswith((".bats", ".sh", ".md")):
                    continue
                try:
                    text = f.read_text()
                except (OSError, UnicodeDecodeError):
                    continue
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if not re.search(r"\.write_text\(|os\.replace\(|\.unlink\(|os\.unlink\(|os\.remove\(", line):
                        continue
                    window = "\n".join(lines[max(0, i - 6):i + 1])
                    if marks.search(window) and "def write_reg" not in "\n".join(lines[max(0, i - 12):i + 1]):
                        offenders.append("%s:%d" % (f.relative_to(root), i + 1))
        self.assertEqual(offenders, [], "registry files written or removed outside write_reg")


class RequestBuildsAreShared(_Listing):
    """The two paths where a REQUEST builds the listing itself (no cycle has built it yet, which is the whole of a restart's
    reload window, or the cycle's last build failed) share one build across every caller, reuse it briefly, and hold a
    bounded number of callers waiting for it. Before this each request built for itself: a build that took about 16 s while
    a restarted kernel reloaded its transcripts piled up about 1,360 abandoned connections on about 1,500 threads (the
    postal bus gives up after 6 s and retries; the kernel kept computing every abandoned request), and the bus's liveness
    checks failed (2026-09-28). The cycle's keyed listing is untouched: it never expires on a clock."""

    def _gated_builder(self):
        """A row builder that blocks until the test opens the gate: builds counted, `entered` set on the first."""
        builds, entered, gate = [], threading.Event(), threading.Event()
        real = km._session_rows_from

        def slow(live_map):
            builds.append(1)
            entered.set()
            gate.wait(20)
            return real(live_map)
        km._session_rows_from = slow
        self.addCleanup(setattr, km, "_session_rows_from", real)
        self.addCleanup(gate.set)                    # never leave a build parked past the test
        return builds, entered, gate

    @staticmethod
    def _waiting():
        sh = getattr(km, "_SESSIONS_REQUEST_BUILD", None)
        return sh.waiting if sh is not None else 0

    @staticmethod
    def _until(pred, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not pred():
            time.sleep(0.01)
        return pred()

    def test_concurrent_requests_before_the_first_cycle_share_one_build(self):
        builds, entered, gate = self._gated_builder()
        bodies, errors = [], []

        def call():
            try:
                bodies.append(km._sessions_listing_serve())
            except Exception as e:                   # noqa: BLE001 (the assertion names it)
                errors.append(e)
        ts = [threading.Thread(target=call, daemon=True) for _ in range(8)]
        for t in ts:
            t.start()
        self._until(lambda: len(builds) + self._waiting() >= 8, 5)   # every caller is building or waiting on a build
        gate.set()
        for t in ts:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(bodies), 8, "every caller was answered")
        self.assertEqual(len(builds), 1, "eight concurrent callers, one build (the base built once per caller)")
        self.assertEqual(len(set(bodies)), 1, "and all eight were served the same bytes")
        self.assertEqual({r["id"] for r in json.loads(bodies[0])}, {SID, SID2})

    def test_a_request_built_listing_older_than_the_reuse_window_is_rebuilt(self):
        builds = []
        name, real = self._count_builds(builds)
        self.addCleanup(setattr, km, name, real)
        now = [1000.0]
        with mock.patch.object(km, "_sessions_request_clock", lambda: now[0], create=True):
            self._body()
            self.row[SID]["state"] = "working"
            now[0] += 0.5
            inside = self._body()
            self.assertEqual(len(builds), 1, "inside the reuse window the kept request build serves")
            self.assertEqual(next(r for r in inside if r["id"] == SID)["state"], "waiting")
            now[0] += 5.0
            past = self._body()
        self.assertEqual(len(builds), 2, "past the window, with still no cycle, a request rebuilds (the base served its first "
                                         "build until a cycle ran, however long that took)")
        self.assertEqual(next(r for r in past if r["id"] == SID)["state"], "working", "and the rebuilt rows carry the change")

    def test_the_cycle_listing_is_never_rebuilt_on_the_clock(self):
        """The plan's rule (plans/sessions-route-from-the-cycle.md): where the inputs are known, the key decides, not a clock."""
        self._cycle()
        builds = []
        name, real = self._count_builds(builds)
        self.addCleanup(setattr, km, name, real)
        now = [1000.0]
        with mock.patch.object(km, "_sessions_request_clock", lambda: now[0], create=True):
            a = self._body()
            now[0] += 3600.0
            b = self._body()
        self.assertEqual(builds, [], "the cycle's keyed listing serves from memory at any age")
        self.assertEqual(a, b)

    def test_waiters_past_the_cap_are_refused_at_once_and_waiters_give_up_at_their_deadline(self):
        builds, entered, gate = self._gated_builder()
        outcomes = []

        def call():
            t0 = time.monotonic()
            try:
                km._sessions_listing_serve()
                outcomes.append(("ok", time.monotonic() - t0))
            except Exception as e:                   # noqa: BLE001
                outcomes.append((getattr(e, "reason", type(e).__name__), time.monotonic() - t0))
        with mock.patch.object(km, "_SESSIONS_REQUEST_WAITERS_MAX", 3, create=True), \
                mock.patch.object(km, "_SESSIONS_REQUEST_WAIT_S", 0.5, create=True):
            leader = threading.Thread(target=km._sessions_listing_serve, daemon=True)
            leader.start()
            self.assertTrue(entered.wait(5), "the leader's build started")
            ts = [threading.Thread(target=call, daemon=True) for _ in range(12)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(5)
            stuck = [t for t in ts if t.is_alive()]
            gate.set()
            leader.join(10)
        self.assertEqual(stuck, [], "every request returned while the build still ran (the base held each in a build of its own)")
        kinds = [k for k, _ in outcomes]
        self.assertEqual(kinds.count("busy"), 9, "past the cap of 3 waiters, refused: %r" % outcomes)
        self.assertEqual(kinds.count("timeout"), 3, "the 3 waiters gave up at their deadline: %r" % outcomes)
        self.assertTrue(all(s < 0.4 for k, s in outcomes if k == "busy"), "a refusal is immediate: %r" % outcomes)
        self.assertEqual(len(builds), 1, "one build for all thirteen callers")
        self.assertLessEqual(km._SESSIONS_REQUEST_BUILD.stats["peakWaiting"], 3)

    def test_many_simultaneous_requests_are_all_answered_with_a_bounded_number_waiting(self):
        """Through the real Handler: forty simultaneous GET /sessions while one build runs. Every one is answered inside the
        waiter deadline, never held for the build's length, and those the cap turns away get a 503 with Retry-After (the
        postal bus reads any non-200 as 'the kernel did not answer', the same as its own 6 s timeout, only sooner)."""
        builds, entered, gate = self._gated_builder()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        results = []

        def get():
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=4)
            try:
                c.request("GET", "/sessions", headers={"X-Romp-Token": km.TOKEN})
                resp = c.getresponse()
                results.append((resp.status, resp.getheader("Retry-After"), resp.read().decode()))
            except Exception as e:                   # noqa: BLE001 (a client timeout: the request was held)
                results.append((None, None, type(e).__name__))
            finally:
                c.close()
        with mock.patch.object(km, "_SESSIONS_REQUEST_WAITERS_MAX", 4, create=True), \
                mock.patch.object(km, "_SESSIONS_REQUEST_WAIT_S", 0.5, create=True):
            leader = threading.Thread(target=km._sessions_listing_serve, daemon=True)
            leader.start()
            self.assertTrue(entered.wait(5), "the leader's build started")
            ts = [threading.Thread(target=get, daemon=True) for _ in range(40)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(8)
            gate.set()
            leader.join(10)
        self.assertEqual(len(results), 40)
        self.assertEqual([r for r in results if r[0] != 503], [], "every request was answered 503 while the build ran, "
                         "none held for the build (the base held all forty): %r" % results[:3])
        self.assertTrue(all(r[1] == "1" for r in results), "each 503 carries Retry-After")
        body = json.loads(results[0][2])
        self.assertEqual((body["ok"], body["retryable"]), (False, True))
        self.assertLessEqual(km._SESSIONS_REQUEST_BUILD.stats["peakWaiting"], 4, "never more than the cap waiting at once")
        self.assertEqual(len(builds), 1)


if __name__ == "__main__":
    unittest.main()
