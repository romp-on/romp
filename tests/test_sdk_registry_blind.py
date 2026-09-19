#!/usr/bin/env python3
"""The SDK registry's blindness stand-down (review of the tmux backend's removal, 2026-09-11).

sdk_backend.list_regs answers [] for a MISSING sdk/ directory (a fresh root before the first write) and
serves its cache on a listing fault; the kernel must not read either as "no session": an sdk/ renamed
aside, unmounted, or a state root moved under a running kernel would otherwise empty the live map in
one tick, and the death sweep would stamp every live SDK session dead and the dead-wait sweep file
blocks on every card. The kernel checks the directory ITSELF (_sdk_records_blind, the SDK module
untouched): the previous rows are served, liveReadFailures rises, one stderr line per episode, and the
three death writers stand down. A fresh root with no sdk/ and no names is genuine emptiness and boots
clean. Also here: a send to a sid no backend owns refuses before any park, and both send routes answer
that refusal as ok:false / a warn, never as a delivered message.

All fixtures synthetic (placeholder UUIDs, invented names)."""
import contextlib
import pathlib
import io
import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_sdkblind", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
SID2 = "11111111-2222-3333-4444-666666666666"


class _LiveThread:
    def is_alive(self):
        return True


class _Running:
    thread = _LiveThread()

    def __init__(self, state="working"):
        self.state = state

    def snapshot(self):
        return {"state": self.state, "since": "", "model": "", "effort": "", "mode": "", "connected": True,
                "spawning": False, "ctx": None, "subagents": [], "bgTasks": []}


class _FakeSdk:
    """The SDK backend's live_sessions as list_regs shapes it: the alive regs under sdk/, and {} when the
    directory is missing or cannot be listed (the module's own contract, which the kernel must see through).
    `sessions` is the backend's session table: a sid whose driver thread runs (the kernel reads it to tell a
    running session from a vanished reg)."""
    def __init__(self):
        self.sessions = {}

    def live_sessions(self):
        out = {}
        try:
            names = os.listdir(jd.SDKDIR)
        except OSError:
            return out
        for n in names:
            try:
                reg = json.loads((jd.SDKDIR / n).read_text())
            except Exception:
                continue
            if reg.get("alive"):
                out[reg["sid"]] = {"state": "waiting", "since": "", "model": "", "effort": "", "mode": "",
                                   "connected": True, "spawning": False, "ctx": None, "subagents": [], "bgTasks": []}
        return out

    def owns(self, sid):
        return (jd.SDKDIR / (sid + ".json")).exists()


def _reg(sid, alive=True):
    jd.SDKDIR.mkdir(parents=True, exist_ok=True)
    (jd.SDKDIR / (sid + ".json")).write_text(json.dumps({"sid": sid, "alive": alive, "name": "web"}))


def _name(sid, name="web"):
    jd.NAMES.mkdir(parents=True, exist_ok=True)
    (jd.NAMES / sid).write_text(name + "\t/tmp\t\t\n")


def _marker_write(sid, t=NOW - 50, by="other"):
    """A death marker another road or process wrote (a REAL file, never a stub of _death_stamp_due)."""
    gd = jd.STATE / "gone"; gd.mkdir(parents=True, exist_ok=True)
    (gd / (sid + ".json")).write_text(json.dumps({"t": t, "by": by}))


def _marker(sid):
    try:
        return json.loads((jd.STATE / "gone" / (sid + ".json")).read_text())
    except OSError:
        return None


class _Root(unittest.TestCase):
    """A private state root per test, the fake SDK backend on km._sdk, no Codex backend."""
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._state = jd.STATE
        jd._rebind_state(pathlib.Path(self.td))
        self.fake = _FakeSdk()
        self._saved = (km._sdk, km._codex, dict(km._LIVE_LAST_ROWS), dict(km._LIVE_READ_FAILS), km._prev_live_sids[0])
        km._sdk = lambda: self.fake
        km._codex = lambda: None
        km._LIVE_LAST_ROWS.clear(); km._VANISHED_SAID.clear(); km._DEAD_WAIT_LIFE_SAID[0] = set()
        km._LIVE_READ_FAILS["count"] = 0
        km._LIVE_READ_FAILS["last"] = {}
        km._prev_live_sids[0] = None
        self.err = io.StringIO()

    def tearDown(self):
        km._sdk, km._codex, rows, fails, km._prev_live_sids[0] = self._saved
        km._LIVE_LAST_ROWS.clear(); km._LIVE_LAST_ROWS.update(rows)
        km._LIVE_READ_FAILS.clear(); km._LIVE_READ_FAILS.update(fails)
        jd._rebind_state(self._state)
        for root, dirs, _files in os.walk(self.td):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
        shutil.rmtree(self.td, ignore_errors=True)

    def live(self):
        with contextlib.redirect_stderr(self.err):
            return km._live_map()



class ChatBuildRowsLeaveWithTheCertifiedDeath(_Root):
    """The per-session chat build rows (/perf, builds.chat.bySession) leave with the CERTIFIED death of their session and
    with nothing else: _record_death drops the row after the marker lands (every road: the sweep's tick, the kill gesture,
    the boot pass, the self-close), and the tick's own walk drops the row of a departure whose marker another road or
    process stamped only when the tick's own verdict is dead (reg present False, never None; no registry blind; no alive
    arm): ONE classifier. Rounds one to three (2026-09-15) computed a keep at the tick from a moving window and forgot what
    they kept a tick later; round four swept with a simpler predicate at the tick's top and dropped a live session's row
    four ways. So every unstamped arm is driven over THREE ticks, and the standing-marker cases write a REAL marker."""
    def _seed(self):
        _reg(SID); _reg(SID2); _name(SID); _name(SID2)
        rows = self.live()
        self.assertEqual(set(rows), {SID, SID2})
        return rows

    def _rows(self, *sids):
        self._saved_rows = dict(km._PERF_STATS.chat_by_session)
        self.addCleanup(self._restore_rows)
        km._PERF_STATS.chat_by_session.clear()
        for sid in sids:
            km._PERF_STATS.chat_by_session[sid] = {"first": 10.0, "last": 10.0, "max": 10.0, "n": 1, "cached": 0, "bytes": 5}

    def _restore_rows(self):
        km._PERF_STATS.chat_by_session.clear(); km._PERF_STATS.chat_by_session.update(self._saved_rows)

    def _tick(self, t, live):
        with contextlib.redirect_stderr(self.err):
            km._death_sweep_tick(t, live)

    def test_every_unstamped_arm_keeps_the_row_over_three_ticks(self):
        """SID2 leaves the live map and an arm of the tick certifies it alive or stands down without stamping; at the
        departure tick, the next (0.5 s later, SID2 in neither the live map nor the departures) and the one after, the row
        stays and no death is stamped. The seven arms: the SDK registry row standing (a dead session the user keeps open as
        a tab); the SDK registry directory unreadable; the Codex registry unreadable; the Codex registry alive after a
        live-map blink; an SDK reg gone under a running driver thread; recent life with no reg; a stamp not due."""
        class _Codex:
            def _session(self, sid):
                return {"sid": sid} if sid == SID2 else None
            def owns(self, sid):
                return sid == SID2
        def _life():
            d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
            (d / (SID2 + ".jsonl")).write_text(json.dumps({"t": NOW - 120, "state": "waiting"}) + "\n")
        arms = {
            "an SDK registry row standing": (False, lambda: None),
            "the SDK registry directory unreadable": (True, lambda: os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))),
            "the Codex registry unreadable": (True, lambda: setattr(km, "_codex_records_blind", lambda cx: True)),
            "the Codex registry alive after a live-map blink": (True, lambda: setattr(km, "_codex", lambda: _Codex())),
            "an SDK reg gone under a running driver thread": (True, lambda: setattr(km, "_sdk_thread_alive", lambda sid: sid == SID2)),
            "recent life with no reg": (True, _life),
            "a stamp not due (a real marker standing, the reg standing)": (False, lambda: _marker_write(SID2)),
        }
        names = ("_codex", "_codex_records_blind", "_sdk_thread_alive")
        saved = {nm: getattr(km, nm) for nm in names}
        kept = []                                                     # every arm's verdict, asserted OUTSIDE the subtests too
        for label, (unlink, arm) in arms.items():
            with self.subTest(arm=label):
                try:
                    rows = self._seed()
                    self._rows(SID, SID2)
                    self._tick(NOW, rows)                             # arms the set-diff trigger
                    if unlink:
                        os.unlink(jd.SDKDIR / (SID2 + ".json"))
                    arm()
                    m0 = _marker(SID2)                                # None, or the real marker the arm wrote: untouched below
                    self._tick(NOW + 1, {SID: rows[SID]})             # SID2 departed
                    self.assertEqual(_marker(SID2), m0, "%s: nothing stamped at the departure tick" % label)
                    self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID, SID2}, "%s: kept at the departure tick" % label)
                    self._tick(NOW + 2, {SID: rows[SID]})             # the next jobs pass: SID2 in neither the live map nor the departures
                    self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID, SID2}, "%s: kept at the second tick" % label)
                    self._tick(NOW + 3, {SID: rows[SID]})
                    self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID, SID2}, "%s: kept at the third tick" % label)
                    self.assertEqual(_marker(SID2), m0, "%s: nothing stamped over three ticks" % label)
                    kept.append(label)
                finally:
                    for nm, v in saved.items():
                        setattr(km, nm, v)
                    if jd.SDKDIR.with_name("sdk.aside").exists():
                        os.rename(jd.SDKDIR.with_name("sdk.aside"), jd.SDKDIR)
                    for f in jd.SDKDIR.glob("*.json"):
                        f.unlink()
                    for f in (jd.STATE / "states").glob("*.jsonl") if (jd.STATE / "states").exists() else []:
                        f.unlink()
                    for f in (jd.STATE / "gone").glob("*.json") if (jd.STATE / "gone").exists() else []:
                        f.unlink()
                    km._prev_live_sids[0] = None
                    self._restore_rows()
        self.assertEqual(len(kept), len(arms), "every unstamped arm kept the row over three ticks: %s" % kept)

    def test_the_already_stamped_arm_is_free_when_the_departure_has_no_row(self):
        """The 1677 read, low 2: the arm paid _sdk_thread_alive and a whole states-file read for a not-due departure that had no
        /perf row at all. Membership in the rows table is tested first, so with nothing to drop the arm reads nothing."""
        rows = self._seed()
        self._rows(SID)                                                   # SID2 has no row
        self._tick(NOW, rows)
        _marker_write(SID2, t=NOW - 50)                                   # a real marker standing: not due
        os.unlink(jd.SDKDIR / (SID2 + ".json"))
        calls = []
        saved = (km._sdk_thread_alive, km._recent_life)
        km._sdk_thread_alive = lambda sid: calls.append(("thread", sid)) or False
        km._recent_life = lambda sid, now: calls.append(("life", sid)) or False
        try:
            self._tick(NOW + 1, {SID: rows[SID]})
        finally:
            km._sdk_thread_alive, km._recent_life = saved
        self.assertEqual(calls, [], "no row to drop: neither the thread nor the states file was read")
        self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID})
        self._rows(SID, SID2)                                             # with a row the arm reads and drops as before
        km._prev_live_sids[0] = {SID, SID2}
        self._tick(NOW + 2, {SID: rows[SID]})
        self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID})

    def test_a_standing_marker_another_road_stamped_drops_the_row_only_on_the_ticks_own_dead_verdict(self):
        """Round five: a REAL marker standing (written by another road or process, no states row postdating it, so the stamp
        is not due) for a departure. The row goes only when the tick's own verdict is dead: reg present False (never None),
        neither registry blind, and no alive arm (not Codex-owned, no driver thread, no recent life). Four alive cases keep
        the row over three ticks; the dead case drops it and stamps nothing new."""
        class _Codex:
            def _session(self, sid):
                return {"sid": sid} if sid == SID2 else None
            def owns(self, sid):
                return sid == SID2
        def _life():
            d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
            (d / (SID2 + ".jsonl")).write_text(json.dumps({"t": NOW - 120, "state": "waiting"}) + "\n")
        cases = {
            "the Codex registry alive after a live-map blink": (True, lambda: setattr(km, "_codex", lambda: _Codex()), True),
            "a running driver thread": (True, lambda: setattr(km, "_sdk_thread_alive", lambda sid: sid == SID2), True),
            "recent life": (True, _life, True),
            "an unreadable sdk/ with the reg standing": (False, lambda: os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside")), True),
            "dead: the reg absent, no alive arm": (True, lambda: None, False),
        }
        names = ("_codex", "_sdk_thread_alive")
        saved = {nm: getattr(km, nm) for nm in names}
        verdicts = []
        for label, (unlink, arm, keep) in cases.items():
            with self.subTest(case=label):
                try:
                    rows = self._seed()
                    self._rows(SID, SID2)
                    self._tick(NOW, rows)
                    _marker_write(SID2, t=NOW - 50)                       # a real marker, standing: the stamp is not due
                    if unlink:
                        os.unlink(jd.SDKDIR / (SID2 + ".json"))
                    arm()
                    for k in (1, 2, 3):
                        self._tick(NOW + k, {SID: rows[SID]})
                        self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID, SID2} if keep else {SID},
                                         "%s: tick %d" % (label, k))
                    self.assertEqual(_marker(SID2), {"t": NOW - 50, "by": "other"}, "%s: the standing marker is untouched" % label)
                    verdicts.append(label)
                finally:
                    for nm, v in saved.items():
                        setattr(km, nm, v)
                    if jd.SDKDIR.with_name("sdk.aside").exists():
                        os.rename(jd.SDKDIR.with_name("sdk.aside"), jd.SDKDIR)
                    for f in jd.SDKDIR.glob("*.json"):
                        f.unlink()
                    for sub_ in ("states", "gone"):
                        for f in (jd.STATE / sub_).glob("*.json*") if (jd.STATE / sub_).exists() else []:
                            f.unlink()
                    km._prev_live_sids[0] = None
                    self._restore_rows()
        self.assertEqual(len(verdicts), len(cases), "every case ran to its verdict: %s" % verdicts)

    def test_a_death_the_tick_certifies_drops_the_row_and_an_empty_live_map_drops_nothing(self):
        rows = self._seed()
        self._rows(SID, SID2)
        self._tick(NOW, rows)
        self._tick(NOW + 1, {})                                       # every sid "departed" at once, every reg standing
        self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID, SID2}, "an empty live map with the regs standing drops nothing")
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self._tick(NOW + 2, rows)                                     # back
        os.unlink(jd.SDKDIR / (SID2 + ".json"))                       # the reg goes, no life rows: dead history
        self._tick(NOW + 3, {SID: rows[SID]})
        self.assertIsNotNone(_marker(SID2), "stamped by the sweep")
        self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID}, "the row goes with the certified death")
        self._tick(NOW + 4, {SID: rows[SID]})
        self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID}, "and stays gone; the live session's row stays")

    def test_the_self_close_road_drops_the_row_through_the_real_record_death(self):
        """One other _record_death road: the end-on-idle sweep (a session asked to close itself once its turn settles) kills
        the session and records the death; the row leaves with it."""
        rows = self._seed()
        self._rows(SID, SID2)
        class _Be:
            def kill(self, sid):
                pass
        stubs = {"_parse": lambda path, sid, now: {"turns": []}, "_session_working": lambda turns: False, "_path_of": lambda sid: "",
                 "_comment_kill_all": lambda sid, be: None, "_send_to_app": lambda app, msg: None, "_push_soon": lambda: None}
        saved = {nm: getattr(km, nm) for nm in stubs}
        saved_be = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: _Be())
        for nm, v in stubs.items():
            setattr(km, nm, v)
        try:
            km._end_on_idle_save({SID2})
            with contextlib.redirect_stderr(self.err):
                km._end_on_idle_sweep(NOW + 5, rows)
        finally:
            for nm, v in saved.items():
                setattr(km, nm, v)
            km.Sessions.backend_for = saved_be
        self.assertIsNotNone(_marker(SID2), "the self-close recorded the death")
        self.assertEqual(set(km._PERF_STATS.chat_by_session), {SID}, "the row left with it")



class GenuineEmptiness(_Root):
    def test_a_fresh_root_with_no_registry_and_no_names_is_not_blind(self):
        self.assertFalse(jd.SDKDIR.exists())
        self.assertFalse(km._sdk_records_blind())
        self.assertEqual(self.live(), {})
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0, "an empty world counts no failure")
        self.assertEqual(self.err.getvalue(), "")

    def test_an_existing_empty_registry_directory_is_an_authoritative_empty_map(self):
        jd.SDKDIR.mkdir(parents=True)
        _name(SID)                                   # a names entry with no reg: dead history, not blindness
        self.assertFalse(km._sdk_records_blind())
        self.assertEqual(self.live(), {})
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0)


class RegistryDirectoryGone(_Root):
    def _seed(self):
        _reg(SID); _reg(SID2); _name(SID); _name(SID2)
        rows = self.live()
        self.assertEqual(set(rows), {SID, SID2})
        return rows

    def test_a_registry_renamed_aside_serves_the_previous_rows_counts_and_says_so_once(self):
        rows = self._seed()
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        self.assertTrue(km._sdk_records_blind())
        self.assertEqual(self.live(), rows, "the previous rows are served, not an empty map")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 1)
        self.assertEqual(self.err.getvalue().count("liveness: the sdk backend's read failed"), 1)
        self.assertEqual(self.live(), rows)
        self.assertEqual(km._LIVE_READ_FAILS["count"], 2, "every stood-down read counts")
        self.assertEqual(self.err.getvalue().count("liveness:"), 1, "said once per episode")
        os.rename(jd.SDKDIR.with_name("sdk.aside"), jd.SDKDIR)
        self.assertEqual(self.live(), rows, "the directory back: a fresh read, no stand-down")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 2)

    def test_the_death_sweep_stamps_nothing_while_the_registry_directory_is_gone(self):
        rows = self._seed()
        km._death_sweep_tick(NOW, rows)              # arms the set-diff trigger
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        with contextlib.redirect_stderr(self.err):
            km._death_sweep_tick(NOW + 1, {})        # every sid "departed" at once
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self.assertFalse((jd.STATE / "states" / (SID + ".jsonl")).exists(), "no idle row either")
        self.assertIn("death-sweep: the SDK registry directory cannot be read", self.err.getvalue())

    def test_an_unlistable_registry_directory_is_blindness_not_absence(self):
        """The real fault staged, not a stub (2026-09-14: the suite stayed green under CPython 3.14 because no test made sdk/
        unlistable): a mode-000 sdk/ makes the reg's stat raise EACCES; _sdk_reg_exists answers None (blindness) on every
        interpreter (3.14's Path.exists() answers False there, which read as every session absent), and the death sweep
        stamps nothing while it stands."""
        if os.geteuid() == 0:
            self.skipTest("root reads through chmod 000")
        rows = self._seed()
        km._death_sweep_tick(NOW, rows)              # arms the set-diff trigger
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "name": "web", "alive": True}))
        self.assertTrue(km._sdk_reg_exists(SID), "the reg is there while sdk/ can be listed")
        os.chmod(jd.SDKDIR, 0)
        try:                                          # restored here, not in a cleanup: tearDown removes the root before cleanups run
            self.assertIsNone(km._sdk_reg_exists(SID), "an unlistable sdk/: None, the writers' blindness, never False")
            with contextlib.redirect_stderr(self.err):
                km._death_sweep_tick(NOW + 1, {})    # every sid "departed" at once
        finally:
            os.chmod(jd.SDKDIR, 0o755)
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self.assertFalse((jd.STATE / "states" / (SID + ".jsonl")).exists(), "no idle row either")

    def test_dead_wait_corroboration_stands_down_and_is_tallied_once_per_pass(self):
        self._seed()
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        stats = {}
        self.assertIsNone(km._dead_wait_corroborated(SID, stats=stats))
        self.assertIsNone(km._dead_wait_corroborated(SID2, stats=stats))
        self.assertEqual(stats, {"sdk": 2})
        with contextlib.redirect_stderr(self.err):
            self.assertIsNone(km._dead_wait_corroborated(SID))
        self.assertIn("dead-wait: the SDK registry directory cannot be read for", self.err.getvalue())

    def test_a_boot_creates_the_registry_directory_and_names_only_sids_are_dead_history(self):
        # a root whose sessions were all terminal ones, or a fresh root before its first SDK write: names on
        # record, no sdk/ yet. The boot pass creates the directory (the kernel owns its existence from then
        # on, so a missing sdk/ after boot is a vanished one), and the names-only sids stamp as dead history
        # (decision f) with no stand-down and no failure counted.
        _name(SID); _name(SID2)
        km._LIVE_LAST_ROWS.clear()                   # a fresh process: no previous rows to go on
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
        self.assertTrue(jd.SDKDIR.is_dir(), "the boot created sdk/")
        self.assertIsNotNone(_marker(SID)); self.assertIsNotNone(_marker(SID2))
        self.assertEqual(self.live(), {})
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0, "a legitimate steady state counts no failure")
        self.assertNotIn("death-boot: the SDK registry directory cannot be read", self.err.getvalue())

    def test_a_directory_recreated_around_the_moved_one_keeps_the_running_session_and_stands_down_on_the_rest(self):
        # after sdk/ vanishes, the SDK backend's next routine reg write re-creates it with one gutted reg (no
        # alive field); the other sessions' regs went with the moved directory. The directory lists, so the
        # guard is not blind, and every vanished reg is judged PER SID: a session whose driver thread runs is
        # alive and keeps its row; a dormant one with recent life leaves the map but no writer stamps it.
        rows = self._seed()
        self.fake.sessions[SID] = _Running()         # SID runs in this kernel; SID2 is dormant
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID2 + ".jsonl")).write_text(json.dumps({"t": NOW - 120, "state": "waiting"}) + "\n")
        km._death_sweep_tick(NOW, rows)              # arms the set-diff trigger
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        jd.SDKDIR.mkdir()
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "lastSid": "abcd"}))   # the running session's next write
        self.assertFalse(km._sdk_records_blind(), "a directory that reads is never blind")
        fresh = self.live()
        self.assertEqual(set(fresh), {SID}, "the running session keeps its row past its gutted reg; the dormant one leaves the map")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0, "no failed read: the directory answered")
        self.assertIn("vanished while its session runs — kept alive", self.err.getvalue())
        with contextlib.redirect_stderr(self.err):
            km._death_sweep_tick(NOW + 1, fresh)
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self.assertIn("1 departed sid(s) hold no reg but show recent life", self.err.getvalue())
        self.assertIs(km._dead_wait_corroborated(SID, now=NOW), False, "a live thread is alive")
        stats = {}
        self.assertIsNone(km._dead_wait_corroborated(SID2, stats=stats, now=NOW))
        self.assertEqual((stats.get("life"), stats.get("lifeSids")), (1, {SID2}))

    def test_a_boot_sparing_six_holds_them_through_a_new_sessions_reg_and_the_next_boot(self):
        # the six spared at boot must stay spared after the user creates ONE session: its reg lands, the
        # registry is no longer empty, and the rule is per sid, never gated on the registry as a whole
        sids = ["11111111-2222-3333-4444-00000000000%d" % i for i in range(1, 7)]
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        for sid in sids:
            _name(sid); (d / (sid + ".jsonl")).write_text(json.dumps({"t": NOW - 300, "state": "working"}) + "\n")
        km._LIVE_LAST_ROWS.clear()
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
        self.assertEqual(km._LIVE_READ_FAILS["count"], 6)
        new = "11111111-2222-3333-4444-000000000099"
        _reg(new); _name(new)                        # one new session: the registry is no longer empty
        live = self.live()
        self.assertEqual(set(live), {new})
        with contextlib.redirect_stderr(self.err):
            for sid in sids:
                self.assertIsNone(km._dead_wait_corroborated(sid, now=NOW + 5), sid)
            km._death_sweep_tick(NOW + 5, live)
            km._death_sweep_tick(NOW + 6, live)
            km._death_boot_pass(NOW + 10)
        for sid in sids:
            self.assertIsNone(_marker(sid), "still stood down, not stamped")

    def test_an_already_stamped_sid_is_not_recent_life_at_the_next_boot(self):
        # the death writer's own idle row sits at now-1; a boot within the horizon must read the standing
        # marker as "already stamped", never as life that stands the pass down
        _name(SID)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        km._record_death(SID, NOW - 3600, "boot")
        km._LIVE_LAST_ROWS.clear()
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0)
        self.assertNotIn("moved aside", self.err.getvalue())

    def test_a_boot_with_the_registry_moved_aside_after_live_sessions_stamps_nothing(self):
        # sdk/ renamed for a backup while names/ stands, and the kernel restarts: the new process has no previous
        # rows, the boot creates an empty sdk/, and a names sid with no reg looks exactly like dead history —
        # except that it shows RECENT LIFE (states rows minutes old, a lease, a goal store just written).
        _name(SID); _name(SID2)
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(json.dumps({"t": NOW - 120, "state": "working"}) + "\n")
        (jd.STATE / "leases").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "leases" / (SID2 + ".json")).write_text(json.dumps({"sid": SID2}))
        os.utime(jd.STATE / "leases" / (SID2 + ".json"), (NOW - 60, NOW - 60))
        km._LIVE_LAST_ROWS.clear()
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
            stats = {}
            self.assertIsNone(km._dead_wait_corroborated(SID, stats=stats, now=NOW))
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self.assertEqual((stats.get("life"), stats.get("lifeSids")), (1, {SID}), "its own key: the directory reads, the life is what stands it down")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 2, "both stand-downs counted")
        self.assertEqual(self.err.getvalue().count("death-boot: 2 names sid(s) hold no reg but show recent life"), 1)

    def test_a_terminal_era_root_with_stale_states_still_stamps_at_boot(self):
        _name(SID)
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(json.dumps({"t": NOW - 30 * 86400, "state": "waiting"}) + "\n")
        km._LIVE_LAST_ROWS.clear()
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
        self.assertIsNotNone(_marker(SID), "no life within the horizon: dead history")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0)

    def test_a_stood_down_departure_stays_armed_and_is_stamped_the_tick_its_life_ages_out(self):
        rows = self._seed()
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID2 + ".jsonl")).write_text(json.dumps({"t": NOW - 120, "state": "waiting"}) + "\n")
        km._death_sweep_tick(NOW, rows)
        os.unlink(jd.SDKDIR / (SID2 + ".json"))       # the reg goes; the session shows life, no thread
        with contextlib.redirect_stderr(self.err):
            km._death_sweep_tick(NOW + 1, {SID: rows[SID]})
            self.assertIsNone(_marker(SID2), "recent life: stood down")
            self.assertIn(SID2, km._prev_live_sids[0], "…and still a departure the next tick re-asks")
            km._death_sweep_tick(NOW + 2, {SID: rows[SID]})
            self.assertIsNone(_marker(SID2))
            km._death_sweep_tick(NOW + jd.WINDOW + 10, {SID: rows[SID]})   # its life ages out of the horizon
        self.assertIsNotNone(_marker(SID2), "stamped by the sweep itself, no restart needed")

    def test_the_dead_wait_life_stand_down_line_is_said_once_per_episode(self):
        _name(SID); _name(SID2)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        for sid in (SID, SID2):
            (d / (sid + ".jsonl")).write_text(json.dumps({"t": NOW - 120, "state": "waiting"}) + "\n")
        saved = km._PREV_ALIVE
        try:
            km._PREV_ALIVE = {SID}
            with contextlib.redirect_stderr(self.err):
                km._dead_wait_sweep(set(), {}, NOW)
                km._dead_wait_sweep(set(), {}, NOW + 1)
                km._dead_wait_sweep(set(), {}, NOW + 2)
            self.assertEqual(self.err.getvalue().count("show recent life; stood down"), 1, "one line for the episode")
            km._PREV_ALIVE = {SID, SID2}
            with contextlib.redirect_stderr(self.err):
                km._dead_wait_sweep(set(), {}, NOW + 3)
            self.assertEqual(self.err.getvalue().count("show recent life; stood down"), 2, "the set changed: said again")
        finally:
            km._PREV_ALIVE = saved

    def test_dead_wait_reads_a_names_only_sid_with_stale_life_as_dead_history(self):
        _name(SID)
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(json.dumps({"t": NOW - 30 * 86400, "state": "waiting"}) + "\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)   # the registry exists and is empty: nothing recent, so history
        self.assertIs(km._dead_wait_corroborated(SID, now=NOW), True)

    def test_a_reg_deleted_by_hand_while_others_stand_ends_that_session_and_freezes_nothing(self):
        rows = self._seed()
        os.unlink(jd.SDKDIR / (SID2 + ".json"))       # one dormant session's reg (no thread, no recent life), removed out of band
        self.assertFalse(km._sdk_records_blind(), "a directory that reads is never blind")
        fresh = self.live()
        self.assertEqual(set(fresh), {SID}, "the fresh read is served, not the frozen previous rows")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0)
        self.assertEqual(self.err.getvalue().count("the session leaves the live map"), 1)
        self.live()
        self.assertEqual(self.err.getvalue().count("vanished"), 1, "said once per sid")
        self.assertIn(SID2, self.err.getvalue())
        self.assertIs(km._dead_wait_corroborated(SID2, now=NOW), True, "no thread, no recent life: ended")

    def test_a_reg_deleted_by_hand_under_a_running_session_keeps_it_alive(self):
        rows = self._seed()
        run = _Running(state="permission")
        self.fake.sessions[SID2] = run
        os.unlink(jd.SDKDIR / (SID2 + ".json"))
        fresh = self.live()
        self.assertEqual(set(fresh), {SID, SID2}, "its driver runs: the row stays")
        self.assertIn("kept alive", self.err.getvalue())
        self.assertEqual(fresh[SID2]["state"], "permission", "the row is the session's own snapshot, not the read before")
        run.state = "working"
        self.assertEqual(self.live()[SID2]["state"], "working", "…and it follows the snapshot every tick, never frozen")
        km._death_sweep_tick(NOW, rows)
        with contextlib.redirect_stderr(self.err):
            km._death_sweep_tick(NOW + 1, {SID: fresh[SID]})   # a map that dropped it anyway
        self.assertIsNone(_marker(SID2), "a live thread is never stamped")
        self.assertIs(km._dead_wait_corroborated(SID2, now=NOW), False)

    @unittest.skipIf(os.geteuid() == 0, "root reads an unreadable directory")
    def test_the_boot_pass_and_the_sweep_stand_down_on_an_unlistable_directory_without_raising(self):
        rows = self._seed()
        km._death_sweep_tick(NOW, rows)
        os.chmod(jd.SDKDIR, 0)
        try:
            with contextlib.redirect_stderr(self.err):
                km._death_boot_pass(NOW)             # no raise: the guard is consulted before any per-sid read
                km._death_sweep_tick(NOW + 1, {})
                stats = {}
                self.assertIsNone(km._dead_wait_corroborated(SID, stats=stats))
            self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
            self.assertEqual(stats, {"sdk": 1})
            self.assertIn("death-boot: the SDK registry directory cannot be read", self.err.getvalue())
            self.assertIn("death-sweep: the SDK registry directory cannot be read", self.err.getvalue())
        finally:
            os.chmod(jd.SDKDIR, stat.S_IRWXU)

    @unittest.skipIf(os.geteuid() == 0, "root reads an unreadable directory")
    def test_an_unlistable_registry_directory_is_blind_too(self):
        rows = self._seed()
        os.chmod(jd.SDKDIR, 0)
        try:
            self.assertTrue(km._sdk_records_blind())
            self.assertEqual(self.live(), rows)
            self.assertEqual(km._LIVE_READ_FAILS["count"], 1)
        finally:
            os.chmod(jd.SDKDIR, stat.S_IRWXU)


class UnownedSendRefuses(_Root):
    def test_send_or_park_refuses_an_unowned_sid_before_any_park_even_with_an_open_turn(self):
        saved = (km._working_now, km._compacting_now, dict(km._pending_ops))
        km._working_now = lambda sid: True             # a dead session whose transcript ends on an open turn
        km._compacting_now = lambda sid: False
        try:
            with contextlib.redirect_stderr(self.err):
                self.assertIsNone(km._send_or_park(km._UNOWNED, SID, "hello"))
            self.assertNotIn(SID, km._pending_ops, "nothing parked for a session nobody runs")
            self.assertIn("no backend owns this session", self.err.getvalue())
        finally:
            km._working_now, km._compacting_now, ops = saved
            km._pending_ops.clear(); km._pending_ops.update(ops)

    def test_the_ws_send_op_warns_on_a_refused_handover(self):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "wid": "", "cid": "c1"}
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon, km._name_of)
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._push_soon = lambda *a, **k: None
        km._name_of = lambda sid: "web"
        try:
            with contextlib.redirect_stderr(self.err):
                km._drive({"type": "sendMessage", "id": SID, "text": "hello"}, client)
        finally:
            km.Sessions.backend_for, km._push_soon, km._name_of = saved
        warns = [m for m in sent if m.get("type") == "warn"]
        self.assertTrue(warns, sent)
        self.assertIn("not delivered", warns[0]["text"])


class MetaCommandRefused(_Root):
    """A /model, /effort or /fast to a sid no backend owns is refused before any stamp or park (review find):
    the meta-command arm runs ahead of the send refusal on both routes. The client hears the refusal on the
    settingRefused frame (gesture command, the sid, the command head's word as the flag), never a bare warn:
    the timeline page renders no warn, and the chat reads one arriving during a create as that create's
    verdict."""

    def setUp(self):
        super().setUp()
        self._saved2 = (km.Sessions.__dict__["backend_for"], km._push_soon, km._name_of, dict(km._pending_ops),
                       dict(km._model_switch_pending))
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._push_soon = lambda *a, **k: None
        km._name_of = lambda sid: "web" if sid == SID else None
        km._pending_ops.clear(); km._model_switch_pending.clear()

    def tearDown(self):
        km.Sessions.backend_for, km._push_soon, km._name_of, ops, pend = self._saved2
        km._pending_ops.clear(); km._pending_ops.update(ops)
        km._model_switch_pending.clear(); km._model_switch_pending.update(pend)
        super().tearDown()

    def _drive(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "wid": "", "cid": "c1"}
        with contextlib.redirect_stderr(self.err):
            km._drive(msg, client)
        self.assertEqual([m for m in sent if m.get("type") == "warn"], [], "no bare warn: the frame has its own type")
        return [m for m in sent if m.get("type") == "settingRefused"]

    def test_the_ws_send_op_refuses_each_meta_command_without_a_stamp_or_a_park(self):
        for text in ("/model opus", "/effort high", "/fast on"):
            refusals = self._drive({"type": "sendMessage", "id": SID, "text": text})
            self.assertEqual([(m["gesture"], m["sid"], m["flag"]) for m in refusals],
                             [("command", SID, text.split()[0][1:])], (text, refusals))
            self.assertIn("not delivered", refusals[0]["text"])
        self.assertEqual(km._pending_ops, {}, "nothing parked")
        self.assertNotIn(SID, km._model_switch_pending, "no switching dots on a dead lane")

    def test_the_lane_menus_command_op_refuses_the_same_way(self):
        saved = km._sid_of
        km._sid_of = lambda who: SID if who == "web" else who      # the lane menu keys its ops by session NAME
        try:
            refusals = self._drive({"type": "sendCommand", "name": "web", "cmd": "/effort high"})
        finally:
            km._sid_of = saved
        self.assertEqual([(m["gesture"], m["sid"], m["flag"]) for m in refusals], [("command", SID, "effort")], refusals)
        self.assertIn("not delivered", refusals[0]["text"])
        self.assertEqual(km._pending_ops, {})


class FollowUpRefused(_Root):
    def test_a_refused_follow_up_moves_nothing(self):
        sent, predicted, reopened = [], [], []
        client = {"send": lambda s: sent.append(json.loads(s)), "wid": "", "cid": "c1"}
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon, km._name_of, km._predict_working,
                 jd.optimistic_followup, km._mark_views_dirty)
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._push_soon = lambda *a, **k: None
        km._name_of = lambda sid: "web" if sid == SID else None
        km._predict_working = lambda *a, **k: predicted.append(a)
        jd.optimistic_followup = lambda *a, **k: reopened.append(a) or True
        km._mark_views_dirty = lambda *a, **k: None
        try:
            with contextlib.redirect_stderr(self.err):
                km._drive({"type": "askFollowUp", "itemId": SID + ":g1", "text": "go on"}, client)
        finally:
            (km.Sessions.backend_for, km._push_soon, km._name_of, km._predict_working,
             jd.optimistic_followup, km._mark_views_dirty) = saved
        errs = [m for m in sent if m.get("type") == "err"]
        self.assertTrue(errs, sent)
        self.assertEqual((errs[0]["op"], errs[0]["itemId"]), ("askFollowUp", SID + ":g1"), "the shape the feed reverts on")
        self.assertIn("No running backend owns this session", errs[0]["text"])
        self.assertEqual(predicted, [], "no card moves to Working on a refusal")
        self.assertEqual(reopened, [], "no reopen event is written on a refusal")


class SendRouteRefuses(_Root):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_post_send_answers_a_refusal_as_ok_false_with_the_reason(self):
        _name(SID)                                   # a names-only sid: no backend owns it
        saved = km.Sessions.__dict__["backend_for"]
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        try:
            with contextlib.redirect_stderr(self.err):
                code, resp = self._post("/send", {"id": SID, "text": "hello"})
        finally:
            km.Sessions.backend_for = saved
        self.assertEqual(code, 200)
        self.assertIs(resp.get("ok"), False, resp)
        self.assertIn("not delivered", resp.get("error", ""))
        self.assertNotIn("queued", resp)

    def test_post_send_refuses_each_meta_command_by_sid_and_by_name(self):
        _name(SID)
        saved = (km.Sessions.__dict__["backend_for"], dict(km._pending_ops), dict(km._model_switch_pending))
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._pending_ops.clear(); km._model_switch_pending.clear()
        try:
            with contextlib.redirect_stderr(self.err):
                for text in ("/model opus", "/effort high", "/fast on"):
                    code, resp = self._post("/send", {"id": SID, "text": text})
                    self.assertEqual((code, resp.get("ok")), (200, False), (text, resp))
                    self.assertIn("the command was not delivered", resp["error"])
                code, resp = self._post("/send", {"name": "ghost", "text": "/model opus"})   # a name no live session answers to
            self.assertEqual((code, resp.get("ok")), (200, False), resp)
            self.assertIn("ghost", resp["error"])
            self.assertEqual(km._pending_ops, {}, "nothing parked for the dead lane")
            self.assertEqual(km._model_switch_pending, {}, "no switching dots stamped")
        finally:
            km.Sessions.backend_for, ops, pend = saved
            km._pending_ops.clear(); km._pending_ops.update(ops)
            km._model_switch_pending.clear(); km._model_switch_pending.update(pend)


if __name__ == "__main__":
    unittest.main()
