#!/usr/bin/env python3
"""A new session's identity colour is picked to stand out from what you're WORKING ON (the user 2026-07-16).

sdk_backend.pick_identity_color hashes the sid into the palette and never looks at what's in use, so a fresh
SDK session could wear a live session's colour while other colours sat free. _pick_identity_color ranks the
palette by: live holders (0 wins) → most-idle (oldest activity among holders) → palette order. Synthetic
fleet only: placeholder UUIDs, invented names.
"""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_cp", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-%012d"


class PickColor(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.names = td / "names"; self.names.mkdir()
        self.saved = (jd.NAMES, km.NAMES, jd.STATE, km.Sessions.live, km._sessions)
        jd.NAMES = km.NAMES = self.names
        jd.STATE = td
        self.bgs = km.pal.colors(km._palette_name())

    def tearDown(self):
        (jd.NAMES, km.NAMES, jd.STATE, km.Sessions.live, km._sessions) = self.saved
        self.td.cleanup()

    def _fleet(self, rows):
        """rows: [(sid, bg, mtime)] → a live fleet with those identities + transcript mtimes."""
        for sid, bg, _ in rows:
            (self.names / sid).write_text("sess-%s\t/tmp/x\t%s\twhite\n" % (sid[-2:], bg))
        km.Sessions.live = staticmethod(lambda: {sid: {} for sid, _, _ in rows})
        km._sessions = lambda now: [{"sid": s, "name": "n", "anchor": None, "path": "/tmp/p", "mtime": m}
                                    for s, _, m in rows]

    def test_an_untaken_color_always_wins_and_is_the_most_distinct_free_one(self):
        # palette order is colorblind-tuned most-distinct-first, so the first FREE slot is the best free one
        self._fleet([(SID % 1, self.bgs[0], NOW)])
        self.assertEqual(km._pick_identity_color(NOW)[0], self.bgs[1], "colour 0 is held → take the next free")
        self._fleet([(SID % 1, self.bgs[0], NOW), (SID % 2, self.bgs[1], NOW)])
        self.assertEqual(km._pick_identity_color(NOW)[0], self.bgs[2])

    def test_an_empty_fleet_takes_the_first_palette_color(self):
        self._fleet([])
        self.assertEqual(km._pick_identity_color(NOW)[0], self.bgs[0])

    def test_a_dead_session_frees_its_color_again(self):
        # only LIVE holders count — else after a week of work every colour looks taken and we'd never
        # hand out a free one. The names file still exists; the session just isn't running.
        for sid, bg in ((SID % 1, self.bgs[0]), (SID % 2, self.bgs[1])):
            (self.names / sid).write_text("sess\t/tmp/x\t%s\twhite\n" % bg)
        km.Sessions.live = staticmethod(lambda: {SID % 2: {}})       # only the SECOND is running
        km._sessions = lambda now: [{"sid": SID % 2, "name": "n", "anchor": None, "path": "/p", "mtime": NOW}]
        self.assertEqual(km._pick_identity_color(NOW)[0], self.bgs[0], "the dead session's colour is free")

    def test_with_every_color_taken_the_most_idle_one_is_reused(self):
        # one live session per palette colour; the one touched LEAST recently is the least confusable to reuse
        rows = [(SID % i, bg, NOW - 60) for i, bg in enumerate(self.bgs)]
        idle_i = 4
        rows[idle_i] = (SID % idle_i, self.bgs[idle_i], NOW - 99999)   # untouched for a day
        self._fleet(rows)
        self.assertEqual(km._pick_identity_color(NOW)[0], self.bgs[idle_i])

    def test_fewest_holders_outranks_idleness(self):
        # colour 0 has TWO holders (one ancient); colour 1 has one, more recent. Spreading out beats age:
        # a colour worn twice is already the most confusable thing on screen.
        rows = [(SID % 1, self.bgs[0], NOW - 99999), (SID % 2, self.bgs[0], NOW - 99999)]
        rows += [(SID % (10 + i), bg, NOW) for i, bg in enumerate(self.bgs[1:])]
        self._fleet(rows)
        self.assertEqual(km._pick_identity_color(NOW)[0], self.bgs[1], "1 holder beats 2, even much older ones")

    def test_create_hands_the_fleet_aware_color_to_spawn_rather_than_letting_it_hash(self):
        # the whole point: only the KERNEL sees both backends' live sessions, so it picks and passes the
        # colour down. sdk_backend.spawn keeps its sid-hash for a caller that supplies none.
        self._fleet([(SID % 1, self.bgs[0], NOW)])
        got = {}

        class _FakeSdk:
            def spawn(self, nm, cwd, bg="", fg="", sid=None, auth="", model="", effort=""):
                got.update(bg=bg, fg=fg)
                return SID % 9
            def connect(self, sid):
                return True

        saved = (km._sdk, km._reveal_chat, km._mark_views_dirty)
        km._sdk = lambda: _FakeSdk()
        km._reveal_chat = lambda *a: None
        km._mark_views_dirty = lambda: None
        try:
            km._create_sdk_session("newsess", "/tmp/x")
        finally:
            (km._sdk, km._reveal_chat, km._mark_views_dirty) = saved
        self.assertEqual(got.get("bg"), self.bgs[1], "colour 0 is live → spawn is handed the next free one")
        self.assertTrue(got.get("fg"), "its paired foreground rides along")

    def test_a_just_created_session_with_no_transcript_holds_its_color(self):
        # rapid-fire creation: the 2nd new session must not reuse the 1st's colour just because the 1st has
        # no transcript yet. No transcript = you JUST made it = active now.
        (self.names / (SID % 1)).write_text("fresh\t/tmp/x\t%s\twhite\n" % self.bgs[0])
        km.Sessions.live = staticmethod(lambda: {SID % 1: {}})
        km._sessions = lambda now: []                                  # nothing discovered — no transcript yet
        self.assertEqual(km._pick_identity_color(NOW)[0], self.bgs[1], "the brand-new session keeps colour 0")


if __name__ == "__main__":
    unittest.main()
