#!/usr/bin/env python3
"""The notified snapshot survives a kernel restart (2026-09-10).

The bells diff each fresh feed build against the previous one, and _NOTIFY_PREV lived in memory
alone, so every kernel life began with an empty memory. The first build after a start was a silent
baseline, but the sessions come back one at a time after a restart (the SDK backend revives them),
so that baseline saw a partial board, and every card a later-revived session brought with it
"appeared" already in needs_input or completed and was announced again. Auto-update restarts the
kernel on every deploy; one evening's ~8 restarts turned 8 cards into 30 phone pushes, one card
pushed 12 times.

The snapshot now persists to STATE/notify-prev.json ({card id: {sid, column, announced,
announcedAt}}: the notified column the card was last seen in, and the column it was last announced
for), seeds _NOTIFY_PREV at the first build of a life, and a card is forgotten only when its own
session rendered without it: a session that did not render at all (not yet revived, dead) says
nothing about its cards. The compaction sweep after each judge pass bounds the store the way
session-order.json is bounded: a session neither alive, nor in the discover window, nor kept open is
gone for good and never renders again, so the sweep forgets its cards, mutes and all (the build alone
kept them forever). The same night's ledger showed the other half: one card on a busy session
re-entering needs_input at every turn end with nothing from the user in between, pushed twelve times
— so the same (card, column) pair is announced once, unless the other column was announced since or
the user acted on the card since (the override journal). Both the desktop notice and the phone push
read this one gate. Synthetic ids and names only (the notes-api demo sessions)."""
import io
import json
import os
import re
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_notify_prev", os.path.join(BIN, "romp-kernel"))
jd = km.jd

WEB = "11111111-2222-4333-8444-555555555501"
API = "11111111-2222-4333-8444-555555555502"


def _card(iid, sid, col, text="Fix the login flow", name="web", **kw):
    d = {"itemId": iid, "sid": sid, "name": name, "column": col, "text": text}
    d.update(kw)
    return d


def _feed(*cards, sessions=None, now=None):
    """A feed build. `sessions` is the board's live roster (build_feed's `sessions` rows); a
    session listed there rendered this build, whether or not it has a card. Omitted, the roster is
    whatever the cards themselves name. `now` is the build's moment (seconds), the clock an
    announcement is stamped with."""
    d = {"type": "feed", "asks": [dict(c) for c in cards]}
    if sessions is not None:
        d["sessions"] = [{"sid": s, "name": "s"} for s in sessions]
    if now is not None:
        d["now"] = now
    return d


def _entry(col, sid, announced="same", at=1_000):
    """A store entry as the last life wrote it: seen in `col`, announced for `announced` (the column
    itself by default — a card in a notified column was told when it got there) at `at`."""
    return {"sid": sid, "board": "feed", "column": col, "announced": col if announced == "same" else announced,   # board: phase three of the card boards (the entry is checked against its board's notify set)
            "announcedAt": at if (announced == "same" or announced is not None) else None}


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd._rebind_state(Path(self.td.name))         # the override journal lives beside the goals: rebind, not assign
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        km._NOTIFY_PREV[0] = None                    # a fresh kernel life
        km._set_notify_all(True)                     # the master bell: every card notifies

    def tearDown(self):
        jd._rebind_state(self.saved)
        km._NOTIFY_PREV[0] = None
        self.td.cleanup()

    def path(self):
        return jd.STATE / "notify-prev.json"

    def disk(self):
        return json.loads(self.path().read_text())

    def seed_disk(self, entries):
        self.path().write_text(json.dumps(entries))

    def boot(self, feed):
        """The first build of a kernel life, with its stderr trail."""
        km._NOTIFY_PREV[0] = None
        err = io.StringIO()
        with redirect_stderr(err):
            out = km._feed_notifications(feed)
        return out, err.getvalue()


class PersistedSnapshotGatesTheBoot(_Base):
    def test_a_card_persisted_as_notified_is_silent_at_boot(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB)})
        out, err = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(out, [], "the card was announced in the last life; a restart is not the card entering")
        self.assertIn("[notify] seeded 1 cards from disk", err)

    def test_a_card_missing_from_the_snapshot_is_announced_once(self):
        # the card entered the column while no kernel was watching: real news, said once
        self.seed_disk({WEB + ":g1": _entry("completed", WEB)})
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"),
                                 _card(WEB + ":g2", WEB, "needs_input", text="Pick the auth library"),
                                 sessions=[WEB]))
        self.assertEqual([o[3] for o in out], [WEB + ":g2"])
        self.assertEqual(out[0][0], "Romp needs you: web")
        d = self.disk()
        self.assertEqual(d[WEB + ":g1"], _entry("completed", WEB))
        self.assertEqual((d[WEB + ":g2"]["column"], d[WEB + ":g2"]["announced"]), ("needs_input", "needs_input"),
                         "the new entry lands on disk in the same build that announces it, marked told")
        again = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "completed"),
                                             _card(WEB + ":g2", WEB, "needs_input"), sessions=[WEB]))
        self.assertEqual(again, [], "holding the column is not news")

    def test_a_column_change_across_a_restart_is_news(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB)})
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        self.assertEqual(len(out), 1, "completed at the last sight, blocked now: the card ENTERED needs_input")
        self.assertEqual(self.disk()[WEB + ":g1"]["announced"], "needs_input")

    def test_a_restart_does_not_re_announce_what_this_life_announced(self):
        # life 1: first boot, then a card completes → one announcement, written to disk
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        out = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(len(out), 1)
        self.assertEqual(self.disk()[WEB + ":g1"]["column"], "completed")
        # life 2: the same board — nothing entered anything
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(out, [], "a restart re-announced every card sitting in the notified columns")
        out = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(out, [])

    def test_sessions_reviving_one_at_a_time_after_a_restart_do_not_re_announce(self):
        """THE measured storm: the first build after a restart sees only the sessions revived so
        far; the rest bring their cards a build or two later, already in needs_input/completed.
        A session that did not render says nothing about its cards — they stay remembered."""
        self.seed_disk({WEB + ":g1": _entry("completed", WEB), API + ":g1": _entry("needs_input", API)})
        # build 1: only web is back
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(out, [])
        self.assertIn(API + ":g1", self.disk(), "api has not rendered yet — its card is not gone")
        # build 2: api revives with its blocked card
        out = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "completed"),
                                           _card(API + ":g1", API, "needs_input", name="api"),
                                           sessions=[WEB, API]))
        self.assertEqual(out, [], "the card was announced in the last life; reviving is not entering")
        # and a kernel that dies between those two builds leaves a snapshot the next life can trust
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"),
                                 _card(API + ":g1", API, "needs_input", name="api"), sessions=[WEB, API]))
        self.assertEqual(out, [])

    def test_a_card_never_told_that_re_blocks_is_announced(self):
        # remembered in needs_input but never announced (its bell was off when it got there)
        self.seed_disk({WEB + ":g1": _entry("needs_input", WEB, announced=None)})
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        self.assertEqual(self.disk(), {}, "a working card with nothing announced is nothing to remember")
        out = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        self.assertEqual(len(out), 1, "the first time the user hears of this (card, column)")


class TheSamePairIsAnnouncedOnce(_Base):
    """A card that leaves a column and comes back is not news unless the other column was announced
    since or the user acted on the card since (the override journal)."""

    def _journal(self, sid, node, op, t, **kw):
        d = jd._overrides_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / (sid + ".jsonl")).open("a") as f:
            f.write(json.dumps({"node": node, "op": op, "t": t, **kw}) + "\n")

    def _flap(self, iid, sid, col, name="web", now=None):
        """One round trip: the card leaves `col` for working and comes back (the return at `now`).
        Returns the announcements the return produced."""
        km._feed_notifications(_feed(_card(iid, sid, "working", name=name), sessions=[sid], now=now))
        return km._feed_notifications(_feed(_card(iid, sid, col, name=name), sessions=[sid], now=now))

    def test_the_flap_announces_once(self):
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        first = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        self.assertEqual(len(first), 1)
        for _ in range(12):
            self.assertEqual(self._flap(WEB + ":g1", WEB, "needs_input"), [],
                             "the judges re-filing the same block at every turn end: told once, not twelve times")
        self.assertEqual(self.disk()[WEB + ":g1"]["announced"], "needs_input")

    def test_each_column_change_announces(self):
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        n = 0
        for col in ("needs_input", "completed", "needs_input"):
            n += len(km._feed_notifications(_feed(_card(WEB + ":g1", WEB, col), sessions=[WEB])))
        self.assertEqual(n, 3, "needs_input → completed → needs_input is three distinct announcements")

    def test_a_working_card_keeps_its_mark_across_a_restart(self):
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        self.assertEqual(self.disk()[WEB + ":g1"], {"sid": WEB, "board": "feed", "column": None, "announced": "needs_input",
                                                    "announcedAt": self.disk()[WEB + ":g1"]["announcedAt"]})
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        self.assertEqual(out, [])
        out = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        self.assertEqual(out, [], "the mark survived the restart: the same pair stays silent")

    def test_a_user_gesture_on_the_card_makes_the_re_entry_news(self):
        T = 1_700_000_000                                              # an explicit clock: the build's `now`
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB], now=T))
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB], now=T + 10))
        self.assertEqual(self.disk()[WEB + ":g1"]["announcedAt"], T + 10, "stamped with the build's moment")
        self._journal(WEB, WEB + ":g1", "followup", T + 15)          # the user's targeted reply on the card
        out = self._flap(WEB + ":g1", WEB, "needs_input", now=T + 20)
        self.assertEqual(len(out), 1, "the user answered; a new block after that is news")
        self.assertEqual(self._flap(WEB + ":g1", WEB, "needs_input", now=T + 30), [],
                         "…and told once again, not per turn end")
        clock = T + 30
        for op in ("resolve", "unclear", "redistill"):
            self._journal(WEB, WEB + ":g1", op, clock + 5)
            clock += 10
            self.assertEqual(len(self._flap(WEB + ":g1", WEB, "needs_input", now=clock)), 1, op)

    def test_the_kernels_own_journal_rows_are_not_the_users(self):
        T = 1_700_000_000
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB], now=T))
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB], now=T + 10))
        self._journal(WEB, WEB + ":g1", "block", T + 15, src="nudge", why="stalled")
        self._journal(WEB, WEB + ":g1", "clear", T + 16, src="romp", why="episode boundary")
        self._journal(WEB, WEB + ":g2", "resolve", T + 17)          # a sibling card's gesture
        self._journal(WEB, WEB + ":g1", "resolve", T + 5)           # a gesture from BEFORE the announcement
        self._journal(WEB, WEB + ":g1", "resolve", T + 10)          # …and one in the announcement's own second
        self.assertEqual(self._flap(WEB + ":g1", WEB, "needs_input", now=T + 20), [])
        self._journal(WEB, WEB + ":g1", "clear", T + 25, src="user", why="cleared from the feed")
        self.assertEqual(len(self._flap(WEB + ":g1", WEB, "needs_input", now=T + 30)), 1,
                         "the user's own cross-off counts")

    def test_the_seed_counts_as_told(self):
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        self.assertEqual(out, [])
        self.assertEqual(self._flap(WEB + ":g1", WEB, "needs_input"), [], "the user has the board: not news")

    def test_a_muted_entry_leaves_no_mark(self):
        km._set_notify_card(WEB + ":g1", False, WEB)
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        self.assertEqual(km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB])), [])
        self.assertIsNone(self.disk()[WEB + ":g1"]["announced"], "nothing was told")
        km._set_notify_card(WEB + ":g1", True, WEB)
        self.assertEqual(len(self._flap(WEB + ":g1", WEB, "needs_input")), 1, "armed now: the first telling")

    def test_an_unreadable_journal_reads_as_not_acted_on(self):
        self.boot(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        real = jd._journal_read

        def eio(sid):
            raise OSError(5, "Input/output error")
        jd._journal_read = eio
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                out = self._flap(WEB + ":g1", WEB, "needs_input")
        finally:
            jd._journal_read = real
        self.assertEqual(out, [], "the silent side is the safe one")
        self.assertIn("[notify]", err.getvalue())


class FirstBootSeedsSilently(_Base):
    def test_no_file_means_seed_from_the_board_and_announce_nothing(self):
        out, err = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"),
                                   _card(API + ":g1", API, "needs_input", name="api"),
                                   _card(API + ":g2", API, "working", name="api"), sessions=[WEB, API]))
        self.assertEqual(out, [], "an existing install's history is status, not news: no storm on the first boot")
        d = self.disk()
        self.assertEqual(sorted(d), [WEB + ":g1", API + ":g1"])
        self.assertEqual((d[WEB + ":g1"]["column"], d[WEB + ":g1"]["announced"]), ("completed", "completed"),
                         "the seed counts as told: the user has the board")
        self.assertIn("[notify]", err)
        self.assertNotIn("from disk", err)
        # the next life is fully event-true: a fresh entry is announced, the seeded ones stay quiet
        out, _ = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"),
                                 _card(API + ":g1", API, "needs_input", name="api"),
                                 _card(API + ":g2", API, "completed", name="api"), sessions=[WEB, API]))
        self.assertEqual([o[3] for o in out], [API + ":g2"])

    def test_an_empty_first_board_still_marks_the_install_seeded(self):
        out, _ = self.boot(_feed(sessions=[]))
        self.assertEqual(out, [])
        self.assertEqual(self.disk(), {}, "the file's existence is what says 'seeded'")
        out = km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "needs_input"), sessions=[WEB]))
        self.assertEqual(len(out), 1, "work can SURFACE blocked — appearing there is entering there")


class PruneAndWriteDiscipline(_Base):
    def test_a_card_gone_from_its_rendered_session_is_pruned(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB), WEB + ":g2": _entry("completed", WEB)})
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(self.disk(), {WEB + ":g1": _entry("completed", WEB)},
                         "web rendered without g2: cleared/archived — the id never comes back")

    def test_a_sessions_last_card_leaving_prunes_too(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB)})
        self.boot(_feed(sessions=[WEB]))                 # web is on the board with no card left
        self.assertEqual(self.disk(), {})

    def test_a_session_not_on_the_board_keeps_its_cards(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB), API + ":g1": _entry("needs_input", API)})
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(self.disk(), {WEB + ":g1": _entry("completed", WEB),
                                       API + ":g1": _entry("needs_input", API)})

    def test_the_compaction_sweep_forgets_the_cards_of_a_session_gone_for_good(self):
        # The build forgets a card only when its session renders without it, and a session gone for good
        # (worktree deleted, never revived, its card cleared while it was dead) never renders again: the
        # file grew by its cards forever. The compaction sweep bounds the snapshot the way _gc_session_order
        # bounds session-order.json: alive, or a transcript still in the discover window, or a dead tab
        # kept open, else forgotten, mutes and all. A dead session still in the window keeps its cards
        # remembered, so its revival stays silent.
        GONE = "11111111-2222-4333-8444-555555555503"
        KEPT = "11111111-2222-4333-8444-555555555504"
        km._set_notify_card(GONE + ":g1", False, GONE)            # a mute the user set on the gone session's card
        km._set_notify_card(API + ":g1", False, API)              # ...and one on api's, which stays
        self.seed_disk({WEB + ":g1": _entry("completed", WEB), API + ":g1": _entry("needs_input", API),
                        GONE + ":g1": _entry("needs_input", GONE), KEPT + ":g1": _entry("completed", KEPT)})
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        saved = jd.discover, km._live_map
        jd.discover = lambda now, window=None, forks=True: [(API, "/dev/null", None, "api")]   # api: dead, in the window
        km._live_map = lambda: {WEB: {}}                     # web: alive
        km._kept_open.add(KEPT)                                   # kept: a dead tab the user kept open
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                km._compact_goal_stores()
        finally:
            jd.discover, km._live_map = saved
            km._kept_open.discard(KEPT)
        expect = {WEB + ":g1": _entry("completed", WEB), API + ":g1": _entry("needs_input", API),
                  KEPT + ":g1": _entry("completed", KEPT)}
        self.assertEqual(km._NOTIFY_PREV[0], expect, "gone from memory; alive, in-window and kept-open stay")
        self.assertEqual(self.disk(), expect, "...and from the file")
        self.assertIn("[notify] forgot 1 card of 1 session gone for good", err.getvalue())
        self.assertNotIn(GONE + ":g1", km._notify_cards(), "its mute went with it")
        self.assertEqual(km._notify_cards().get(API + ":g1"), False, "api has not rendered: the mute stays")

    def test_the_roster_falls_back_to_the_cards_own_sessions(self):
        # a payload with no `sessions` rows (the older fixtures): a session with a card rendered
        self.seed_disk({WEB + ":g1": _entry("completed", WEB), WEB + ":g2": _entry("completed", WEB)})
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed")))
        self.assertEqual(self.disk(), {WEB + ":g1": _entry("completed", WEB)})

    def test_an_unchanged_snapshot_is_not_rewritten(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB)})
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        p = self.path()
        before = p.stat().st_mtime_ns
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "completed"),
                                     _card(WEB + ":g2", WEB, "working"), sessions=[WEB]))
        self.assertEqual(p.stat().st_mtime_ns, before, "a build that changes nothing writes nothing")

    def test_the_file_is_private_and_published_atomically(self):
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        mode = stat.S_IMODE(self.path().stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual([p.name for p in Path(self.td.name).glob("notify-prev.json.tmp*")], [],
                         "no temp file left beside the published one")

    def test_provisional_placeholders_never_enter_the_snapshot(self):
        self.boot(_feed(_card("provisional:" + WEB, WEB, "needs_input", provisional=True), sessions=[WEB]))
        self.assertEqual(self.disk(), {})

    def test_a_remembered_cards_bell_override_survives_its_sessions_absence(self):
        # the bell store's prune rides the same event: a card we still remember is not gone
        km._set_notify_card(API + ":g1", False, API)          # a mute the user set on api's blocked card
        self.seed_disk({API + ":g1": _entry("needs_input", API)})
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(km._notify_cards().get(API + ":g1"), False, "api has not rendered: the mute stays")
        self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB, API]))
        self.assertNotIn(API + ":g1", km._notify_cards(), "api rendered without it: pruned as before")


class CorruptOrUnreadableSnapshot(_Base):
    def test_garbage_is_treated_as_absent_with_a_loud_line(self):
        self.path().write_text("{not json")
        out, err = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(out, [], "treated as a first boot: a silent seed, never a crash")
        self.assertIn("[notify]", err)
        d = self.disk()
        self.assertEqual((sorted(d), d[WEB + ":g1"]["column"]), ([WEB + ":g1"], "completed"), "a valid file replaces it")
        self.assertTrue([p for p in Path(self.td.name).glob("notify-prev.json.corrupt-*")],
                        "the bad bytes are moved aside, never deleted")

    def test_a_wrong_shape_is_treated_as_absent(self):
        self.path().write_text(json.dumps([WEB + ":g1"]))
        out, err = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        self.assertEqual(out, [])
        self.assertIn("[notify]", err)

    def test_malformed_entries_are_skipped_not_fatal(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB), WEB + ":g2": "completed", WEB + ":g3": 7,
                        WEB + ":g4": {"column": "working", "sid": WEB},
                        WEB + ":g5": {"board": "feed", "column": None, "announced": None, "sid": WEB}})
        out, err = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"),
                                   _card(WEB + ":g2", WEB, "completed"), sessions=[WEB]))
        self.assertEqual([o[3] for o in out], [WEB + ":g2"], "an entry we could not read is no memory of the card")
        self.assertIn("[notify] seeded 1 cards from disk", err)

    def test_an_unreadable_file_is_treated_as_absent_with_a_loud_line(self):
        self.seed_disk({WEB + ":g1": _entry("completed", WEB)})
        real = Path.read_bytes
        target = str(self.path())

        def eio(p, *a, **k):
            if str(p) == target:
                raise OSError(5, "Input/output error")
            return real(p, *a, **k)
        Path.read_bytes = eio
        try:
            out, err = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        finally:
            Path.read_bytes = real
        self.assertEqual(out, [])
        self.assertIn("[notify]", err)
        self.assertIn("notify-prev.json", err)

    def test_a_failed_write_is_a_log_line_not_a_crash(self):
        saved = km._atomic_write

        def boom(path, text, mode=None):
            raise OSError(28, "No space left on device")
        km._atomic_write = boom
        try:
            out, err = self.boot(_feed(_card(WEB + ":g1", WEB, "completed"), sessions=[WEB]))
        finally:
            km._atomic_write = saved
        self.assertEqual(out, [])
        self.assertIn("[notify]", err)
        self.assertIn("No space left", err)


class BothLegsReadTheOneGate(unittest.TestCase):
    def test_desktop_and_phone_legs_iterate_the_same_fired_list(self):
        src = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()
        self.assertIn("_fired = _feed_notifications(feed)", src)
        self.assertIn("for _t, _b, _sid, _iid in _fired:", src)
        self.assertIn("_system_notify(_t, _b)", src)
        self.assertIn('_push_notify(_t, _b, _sid, _badge, kind="card", card_id=_iid', src)
        calls = [ln for ln in src.splitlines()
                 if re.search(r"_feed_notifications\(", ln) and not ln.lstrip().startswith(("#", "def "))]
        self.assertEqual(len(calls), 1, "one diff, one gate — never a second: %r" % calls)


if __name__ == "__main__":
    unittest.main()
