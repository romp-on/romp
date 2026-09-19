#!/usr/bin/env python3
"""Per-session view flags (the user 2026-06-19): a persisted {sid: {flag: true}} dict under STATE, set
from the timeline lane gear. The only flag today is hideFromFeed — a session whose prompts shouldn't mint
feed cards (it stays on the timeline). These pin the storage helpers + the web boot hook. Synthetic only."""
import os
import pathlib
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_sf", os.path.join(BIN, "romp-kernel"))
# the kernel's helpers read/write jd.STATE; sandbox THAT module's STATE (the one the kernel actually uses)
# ...with _rebind_state, never STATE alone: GOALDIR and every derived dir stay at the import-bound root otherwise,
# and that module is ONE object shared by every test module in the process, so a goal store saved there outlived
# this module and reached every later module's feed for the placeholder sid (T281).
jd = km.jd


class SessionFlags(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd._rebind_state(Path(self.td.name))
        km._flags_cache.clear()

    def tearDown(self):
        jd._rebind_state(self.saved)
        self.td.cleanup()

    def test_default_is_empty(self):
        self.assertEqual(km._session_flags(), {})
        self.assertFalse(km._session_flag("sid1", "hideFromFeed"))

    def test_set_get_then_unset_drops_the_entry(self):
        km._set_session_flag("sid1", "hideFromFeed", True)
        self.assertTrue(km._session_flag("sid1", "hideFromFeed"))
        self.assertEqual(km._session_flags(), {"sid1": {"hideFromFeed": True}})
        km._set_session_flag("sid1", "hideFromFeed", False)
        self.assertFalse(km._session_flag("sid1", "hideFromFeed"))
        self.assertEqual(km._session_flags(), {}, "removing the last flag drops the whole session entry")

    def test_sessions_are_independent(self):
        km._set_session_flag("a", "hideFromFeed", True)
        km._set_session_flag("b", "hideFromFeed", False)
        self.assertTrue(km._session_flag("a", "hideFromFeed"))
        self.assertFalse(km._session_flag("b", "hideFromFeed"))
        self.assertNotIn("b", km._session_flags(), "a never-set / cleared flag isn't persisted")

    def test_cache_invalidates_on_write(self):
        self.assertEqual(km._session_flags(), {})           # primes the (empty) read path
        km._set_session_flag("a", "hideFromFeed", True)     # changes the file
        self.assertTrue(km._session_flag("a", "hideFromFeed"), "the (mtime_ns,size) cache key sees the write")

    def test_unknown_flag_value_is_false(self):
        km._set_session_flag("a", "hideFromFeed", True)
        self.assertFalse(km._session_flag("a", "someFutureFlag"), "an unset flag reads False")

    def test_web_boot_exposes_the_set_flag_hook(self):
        # the timeline web page posts setSessionFlag via this host hook (kernel _TIMELINE_BOOT)
        self.assertIn("__rompTimelineSetFlag", km._TIMELINE_BOOT)
        self.assertIn("setSessionFlag", km._TIMELINE_BOOT)


class AutoNudgeWiring(unittest.TestCase):
    """The Auto Nudge toggle is a SERVER-SIDE behavior, so the feed gear posts setAutoNudge to the kernel
    and the checkbox reflects the kernel's state via /version (not localStorage) — the user 2026-06-19."""

    def test_gear_has_the_autonudge_toggle_posting_to_the_kernel(self):
        self.assertIn("rs-autonudge", _gear_src(), "the gear panel has an Auto Nudge checkbox")
        self.assertIn("Auto Nudge", _gear_src())
        self.assertIn("setAutoNudge", _gear_src(), "toggling posts the server-side message")

    def test_version_reports_autonudge_state_for_the_checkbox(self):
        saved = jd.STATE
        td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(td.name))
        km._autonudge_cache.clear()
        try:
            self.assertTrue(km._version_info()["autoNudge"], "on by default (no state file)")
            km._set_auto_nudge(False)
            self.assertFalse(km._version_info()["autoNudge"], "an explicit off is respected")
            km._set_auto_nudge(True)
            self.assertTrue(km._version_info()["autoNudge"], "the gear reads the kernel's authoritative state")
        finally:
            jd._rebind_state(saved)
            td.cleanup()

    def test_default_on_even_when_state_file_lacks_the_key(self):
        saved = jd.STATE
        td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(td.name))
        km._autonudge_cache.clear()
        try:
            (Path(td.name) / "auto-nudge.json").write_text('{"nudged": {}}')  # present, no "enabled" key
            self.assertTrue(km._auto_nudge_on(), "a state file missing the enabled key still defaults on")
        finally:
            jd._rebind_state(saved)
            td.cleanup()


class FlagsStoreUnreadableRefuses(unittest.TestCase):
    """The state-readers audit (rank 5): the session-flags reader used to fold ANY read fault to {}
    and CACHE it, so _set_session_flag copied that empty, applied one edit, and atomically wrote it
    back — erasing every session's flags (including the postalServiceOff isolation boundaries) under
    a silent success. The setters now read PROVED: a read fault refuses the write loudly and the
    file is left exactly as it was. Synthetic sids only."""
    SID = "11111111-2222-3333-4444-555555555555"
    OTHER = "99999999-8888-7777-6666-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd._rebind_state(Path(self.td.name))
        km._flags_cache.clear()

    def tearDown(self):
        jd._rebind_state(self.saved)
        km._flags_cache.clear()
        self.td.cleanup()

    def _path(self):
        return jd.STATE / "session-flags.json"

    def _fault_reads_of(self, target):
        """Fail BOTH read_bytes (the fix's proved reader) and read_text (origin/main's reader) for one
        path, so the same test injects the fault on either tree — green on the fix, RED on main where
        the fold-to-{} erases the flags."""
        import errno
        real_rb, real_rt = Path.read_bytes, Path.read_text
        tgt = str(target)
        def rb(self, *a, **k):
            if str(self) == tgt:
                raise OSError(errno.EIO, "injected EIO")
            return real_rb(self, *a, **k)
        def rt(self, *a, **k):
            if str(self) == tgt:
                raise OSError(errno.EIO, "injected EIO")
            return real_rt(self, *a, **k)
        Path.read_bytes, Path.read_text = rb, rt
        return (real_rb, real_rt)

    def test_a_flag_toggle_is_refused_when_the_flags_store_cannot_be_read(self):
        # a populated store: the OTHER session is isolated from the postal bus — a safety boundary
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        before = self._path().read_bytes()
        saved = self._fault_reads_of(self._path())
        raised = None
        try:
            km._set_session_flag(self.SID, "hideFromFeed", True)   # a fresh edit on another sid
        except Exception as e:                                     # noqa: BLE001 — on main it never raises
            raised = e
        finally:
            Path.read_bytes, Path.read_text = saved
        km._flags_cache.clear()
        # THE erasure the audit is about: on origin/main the read folds to {}, the setter writes
        # {SID:{hideFromFeed}} and the OTHER session's isolation boundary is GONE — this assertion is
        # what turns RED there. The fix refuses the write, so the file is byte-for-byte unchanged.
        self.assertEqual(self._path().read_bytes(), before,
                         "the flags file must be unchanged when its file can't be read (main erases it here)")
        self.assertTrue(km._session_flag(self.OTHER, "postalServiceOff"),
                        "the pre-existing isolation flag survives the refused toggle")
        self.assertEqual(type(raised).__name__, "_StateUnreadable",
                         "the write is refused LOUDLY, not folded to a fabricated empty (got %r)" % raised)

    def test_a_torn_flags_file_is_quarantined_aside_not_overwritten(self):
        torn = b'{"sid": {"hideFromFeed": true'
        self._path().write_bytes(torn)
        km._flags_cache.clear()
        # 2026-09-14 (the lows PR's round two): the quarantine is not an empty store; with nothing known the proved
        # read REFUSES (the writers refuse with it), the bytes are still saved aside
        with self.assertRaises(km._StateUnreadable):
            km._session_flags_proved()
        q = list(jd.STATE.glob("session-flags.json.corrupt-*"))
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0].read_bytes(), torn, "the quarantine holds the ORIGINAL bytes")
        self.assertFalse(self._path().exists())

    def test_a_toggle_after_a_quarantine_starts_from_the_last_known_flags_and_keeps_every_other_boundary(self):
        # the manager's rule (2026-09-14): the first write after a quarantine must not rebuild the store from empty
        km._set_session_flag(self.OTHER, "postalServiceOff", True)     # an isolation boundary another session relies on
        km._set_session_flag("cccccccc-2222-4333-8444-0000000000c3", "hideFromFeed", True)
        self.assertEqual(km._session_flags().get(self.OTHER), {"postalServiceOff": True}, "read cleanly: known")
        self._path().write_bytes(b'{"torn": ')
        self.assertEqual(km._session_flags().get(self.OTHER), {"postalServiceOff": True},
                         "the display read quarantines the torn bytes and keeps the last cleanly read flags")
        self.assertFalse(self._path().exists(), "moved aside")
        self.assertEqual(km._mail_off_why_k(self.OTHER), "isolation", "the boundary holds across the quarantine")
        km._set_session_flag(self.SID, "hideFromFeed", True)           # the first toggle after the quarantine
        stored = json.loads(self._path().read_text())
        self.assertEqual(stored.get(self.OTHER), {"postalServiceOff": True}, "rebuilt from the last known flags, not from empty")
        self.assertEqual(stored.get("cccccccc-2222-4333-8444-0000000000c3"), {"hideFromFeed": True})
        self.assertEqual(stored.get(self.SID), {"hideFromFeed": True}, "plus the one flag")
        km._flags_cache.clear()
        self.assertEqual(km._mail_off_why_k(self.OTHER), "isolation", "and the door reads the rebuilt store cleanly")
        self.assertNotIn(str(self._path()), km._state_fault_seen, "the sidecar is history once the store is written")

    def test_a_cold_toggle_after_a_quarantine_is_refused_and_says_so(self):
        # nothing known (a restart after the quarantine): the write is refused with a notice, never a fresh store
        self._path().write_bytes(b'{"torn": ')
        km._flags_cache.clear()
        self.assertEqual(km._session_flags(), {}, "the display read quarantines and answers the unproved empty default")
        self.assertFalse(self._path().exists())
        self.assertEqual(km._mail_off_why_k(self.SID), "flags", "mail held: nothing is known")
        raised = None
        try:
            km._set_session_flag(self.SID, "hideFromFeed", True)
        except Exception as e:                                          # noqa: BLE001
            raised = e
        self.assertEqual(type(raised).__name__, "_StateUnreadable", "refused loudly (got %r)" % raised)
        self.assertIn("moved aside", str(raised))
        # round three: the refusal names the one in-product exit, the file to write and what that does; round four: the
        # sidecar is named as forensics only, never as an exit (its bytes are the ones that cannot be read back)
        self.assertIn("write {} to %s" % self._path(), str(raised), "the exit: the path to write")
        self.assertIn("mail isolation and feed mute is then off", str(raised), "and what writing it does")
        self.assertIn(list(jd.STATE.glob("session-flags.json.corrupt-*"))[0].name, str(raised), "and names the sidecar")
        self.assertIn("for forensics; they cannot be read back", str(raised), "the sidecar is kept, not offered as an exit")
        self.assertNotIn("restore the sidecar", str(raised))
        self.assertFalse(self._path().exists(), "no fresh store was written")
        self.assertEqual(len(list(jd.STATE.glob("session-flags.json.corrupt-*"))), 1, "the sidecar still stands")
        self.assertEqual(km._mail_off_why_k(self.SID), "flags", "the door stays closed")

    def test_every_exit_the_refusal_names_taken_as_written_opens_the_door(self):
        # round four's rule: a remedy text is tested by taking it as written. Every exit the refusal names is parsed from its
        # text and taken verbatim from a fresh hold, and each must open the door; round three's text named a second exit
        # (restore the sidecar's contents) whose bytes by construction cannot be read back, so taking it left the door held
        import re, shutil
        def hold():
            for old in jd.STATE.glob("session-flags.json.*"):
                old.unlink()
            if self._path().exists():
                self._path().unlink()
            self._path().write_bytes(b'{"torn": ')
            km._flags_cache.clear(); km._state_fault_seen.pop(str(self._path()), None)
            self.assertEqual(km._session_flags(), {}); self.assertEqual(km._mail_off_why_k(self.SID), "flags", "held")
            with self.assertRaises(km._StateUnreadable) as cm:
                km._set_session_flag(self.SID, "hideFromFeed", True)
            return str(cm.exception)
        text = hold()
        exits = []
        for m in re.finditer(r"write (\S+) to (\S+)", text):           # "write {} to <path> ..."
            exits.append(("write", m.group(1), m.group(2).rstrip(".,;)")))
        if re.search(r"restore the sidecar", text):
            exits.append(("restore-sidecar", None, None))
        self.assertTrue(exits, "the refusal names at least one exit: %r" % text)
        for kind, literal, path in exits:
            text = hold()
            if kind == "write":
                self.assertEqual((literal, path), ("{}", str(self._path())), "the write exit names the literal and the flags path")
                pathlib.Path(path).write_text(literal)
            else:
                side = list(jd.STATE.glob("session-flags.json.corrupt-*"))[0]
                shutil.copyfile(side, self._path())                      # "restore the sidecar's contents there", as written
            self.assertEqual(km._mail_off_why_k(self.SID), "", "the exit %r, taken as written, opens the door" % kind)
            km._set_session_flag(self.SID, "hideFromFeed", True)         # and the refused toggle now lands
            self.assertEqual(km._session_flags().get(self.SID), {"hideFromFeed": True})
            self.assertEqual(list(jd.STATE.glob("session-flags.json.corrupt-*")), [], "the mark retired after exit %r" % kind)
        self.assertIn("for forensics; they cannot be read back", text, "the sidecar is named as kept, not as an exit")
        kept = list(jd.STATE.glob("session-flags.json.retired-*"))
        self.assertEqual([k.read_bytes() for k in kept], [b'{"torn": '], "the sidecar's bytes kept for forensics, as the text says")

    def test_the_exit_writing_the_file_by_hand_opens_the_door_retires_the_mark_and_a_later_delete_is_a_fresh_install(self):
        # round three: a fail-closed state needs an in-product exit; taking it must end the hold, and a fresh file beside an
        # old sidecar must never re-enter it
        self._path().write_bytes(b'{"torn": ')
        km._flags_cache.clear()
        self.assertEqual(km._session_flags(), {}); self.assertEqual(km._mail_off_why_k(self.SID), "flags", "held")
        self.assertEqual(len(list(jd.STATE.glob("session-flags.json.corrupt-*"))), 1)
        self._path().write_text("{}")                                    # the remedy the refusal names
        self.assertEqual(km._mail_off_why_k(self.SID), "", "the door opens on the clean read")
        self.assertEqual(list(jd.STATE.glob("session-flags.json.corrupt-*")), [], "the mark is retired...")
        retired = list(jd.STATE.glob("session-flags.json.retired-*"))
        self.assertEqual(len(retired), 1); self.assertEqual(retired[0].read_bytes(), b'{"torn": ', "...and the bytes kept")
        km._set_session_flag(self.OTHER, "postalServiceOff", True)     # a write works again
        self._path().unlink()                                            # the user later removes the store beside the old sidecar
        km._flags_cache.clear()
        self.assertEqual(km._mail_off_why_k(self.SID), "", "a missing file beside a RETIRED sidecar is a fresh install, not a hold")
        self.assertFalse(km._flags_quarantined(self._path()))

    def test_a_clean_write_primes_the_cache_so_a_process_that_only_wrote_holds_the_last_known_flags(self):
        # round three, low (b): the proved reader's warm path needed a prior DISPLAY read; a process that just wrote the
        # flags had an empty cache and refused the next toggle cold after a quarantine
        km._flags_cache.clear()
        km._set_session_flag(self.OTHER, "postalServiceOff", True)     # a write, no display read
        self.assertIn(str(self._path()), km._flags_cache, "the write primed the cache")
        self._path().write_bytes(b'{"torn": ')
        km._set_session_flag(self.SID, "hideFromFeed", True)           # a toggle straight after the quarantine, still no display read
        stored = json.loads(self._path().read_text())
        self.assertEqual(stored.get(self.OTHER), {"postalServiceOff": True}, "rebuilt from the flags the write knew, not from empty")
        self.assertEqual(stored.get(self.SID), {"hideFromFeed": True})

    def test_enoent_flags_still_reads_empty_with_no_quarantine(self):
        self.assertEqual(km._session_flags(), {}, "a missing store is legitimately empty")
        self.assertEqual(km._session_flags_proved(), {})
        self.assertEqual(list(jd.STATE.glob("session-flags.json.corrupt-*")), [])


import contextlib
import errno
import json
import shutil
import subprocess
from unittest import mock


@contextlib.contextmanager
def _stat_fault(target):
    """Fail every stat of ONE path with an EACCES for the duration of the block -- the arm the read-fault
    injector never reaches (review find, 2026-09-08): every reader stats BEFORE it reads (the display
    readers key their cache on it), and a state dir that cannot be searched faults exactly there.
    Everything else stats normally."""
    real_stat = Path.stat
    tgt = str(target)
    def st(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EACCES, "injected EACCES")
        return real_stat(self, *a, **k)
    Path.stat = st
    try:
        yield
    finally:
        Path.stat = real_stat


@contextlib.contextmanager
def _reads_fault(target):
    """Fail every byte read of ONE path with an EIO (the proved reader's read_bytes and the pre-fix
    reader's read_text alike) for the duration of the block; everything else reads normally."""
    real_rb, real_rt = Path.read_bytes, Path.read_text
    tgt = str(target)
    def rb(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rb(self, *a, **k)
    def rt(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rt(self, *a, **k)
    Path.read_bytes, Path.read_text = rb, rt
    try:
        yield
    finally:
        Path.read_bytes, Path.read_text = real_rb, real_rt


@contextlib.contextmanager
def _writes_fault(target):
    """Fail the PUBLISH of ONE state file with an ENOSPC for the duration of the block: _atomic_write
    writes `<name>.tmp.<pid>.<tid>.<n>` beside the file and renames it over, so failing every write_text
    of that shape is the disk refusing this file's publish while every other path, and every read, behaves."""
    real = Path.write_text
    prefix = Path(target).name + ".tmp."

    def wt(self, *a, **k):
        if self.name.startswith(prefix):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **k)
    Path.write_text = wt
    try:
        yield
    finally:
        Path.write_text = real


class StateUnreadableIsAPlainException(unittest.TestCase):
    """The WS receive loop re-raises (BrokenPipeError, ConnectionResetError, OSError) as a genuine
    socket failure and tears the connection down; every other exception falls to its logging arm
    and the next message still processes. A store-read fault must be the second kind: one handler
    branch that forgets to catch it costs a logged line, never the dashboard's socket."""

    def test_it_is_an_exception_but_never_an_oserror(self):
        self.assertTrue(issubclass(km._StateUnreadable, Exception))
        self.assertFalse(issubclass(km._StateUnreadable, OSError),
                         "as an OSError the WS loop would classify a read fault as a socket failure")

    def test_it_names_the_file_and_the_fault_in_plain_words(self):
        e = km._StateUnreadable(Path("/tmp/x/session-flags.json"), "read failed: [Errno 5] Input/output error")
        self.assertEqual(str(e), "session-flags.json could not be read (read failed: [Errno 5] Input/output error)")
        self.assertEqual((e.path.name, e.fault), ("session-flags.json", "read failed: [Errno 5] Input/output error"))


class FlagsDisplayReaderServesUnproved(unittest.TestCase):
    """The DISPLAY reader under a fault: the last value this kernel read if it holds one, else {}
    -- and NEITHER is cached under the file's stat key. A reader that cached what a fault produced
    would keep serving it after the disk recovered (same key, a cache HIT never reads), which is how
    the pre-fix reader turned a transient EIO into a permanent empty."""
    SID = "11111111-2222-3333-4444-555555555555"
    OTHER = "99999999-8888-7777-6666-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd._rebind_state(Path(self.td.name))
        km._flags_cache.clear()
        km._state_fault_seen.clear()
        self.notices = []
        self._notice = km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": self.notices.append((text, ok, kind))

    def tearDown(self):
        km._sync_notice = self._notice
        jd._rebind_state(self.saved)
        km._flags_cache.clear()
        km._state_fault_seen.clear()
        self.td.cleanup()

    def _path(self):
        return jd.STATE / "session-flags.json"

    def test_a_stat_fault_serves_the_last_read_value_and_refuses_the_writers(self):
        # the STAT arm (review find, 2026-09-08: every injector faulted the read, so the reader's stat
        # arm and the proved reader's were unpinned -- reverting either to a silent `return {}` passed)
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        self.assertEqual(km._session_flags(), {self.OTHER: {"postalServiceOff": True}})   # primes the cache
        key1 = km._flags_cache[str(self._path())][0]
        before = self._path().read_bytes()
        with _stat_fault(self._path()):
            self.assertEqual(km._session_flags(), {self.OTHER: {"postalServiceOff": True}},
                             "the stat arm serves the LAST value read, not {}")
            self.assertEqual(km._flags_cache[str(self._path())], (key1, {self.OTHER: {"postalServiceOff": True}}),
                             "and caches nothing new")
            with self.assertRaises(km._StateUnreadable) as cm:
                km._session_flags_proved()                              # the proved reader raises on the stat
            self.assertIn("stat failed: [Errno 13]", str(cm.exception))
            with self.assertRaises(km._StateUnreadable):
                km._set_session_flag(self.SID, "hideFromFeed", True)     # so a writer refuses
        self.assertEqual(self._path().read_bytes(), before, "the refused write left the file alone")
        bad = [t for t, ok, _k in self.notices if not ok]
        self.assertEqual(len(bad), 1, "one notice for the episode, from the display reader's stat arm")
        self.assertIn("stat failed: [Errno 13]", bad[0])
        km._flags_cache.clear()
        with _stat_fault(self._path()):
            self.assertEqual(km._session_flags(), {}, "a cold cache serves the empty default under a stat fault")
            self.assertNotIn(str(self._path()), km._flags_cache, "…uncached")

    def test_the_fault_notice_is_filed_under_the_refused_kind_not_sync(self):
        # review find, 2026-09-08: filed under the ring's default (sync) kind, the notice wore the
        # machine-sync label and a mute on that log silenced every disk-fault notice with it
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        with _reads_fault(self._path()):
            km._session_flags()
        self.assertEqual([(ok, k) for _t, ok, k in self.notices], [(False, "refused")])

    def test_a_quarantine_tells_the_dashboard_once_under_the_refused_kind(self):
        # review find, 2026-09-08: a quarantine reset the store to empty with only a stderr line, so
        # from the dashboard every flag (postal isolation included) simply reset itself
        torn = b'{"' + self.OTHER.encode() + b'": {"postalServiceOff": tr'
        self._path().write_bytes(torn)
        # 2026-09-14 (the lows PR's round two): the quarantine is not an empty store; the proved read cold RAISES (the
        # writers refuse with it) and the notice says what is held, never that the settings start over
        with self.assertRaises(km._StateUnreadable):
            km._session_flags_proved()
        self.assertEqual(len(self.notices), 1, "one notice for the move")
        text, ok, kind = self.notices[0]
        self.assertEqual((ok, kind), (False, "refused"))
        self.assertIn("session-flags.json could not be parsed and was moved aside to session-flags.json.corrupt-", text)
        self.assertIn("mail isolation included", text); self.assertIn("mail is held for every session", text)
        self.assertNotIn("start over empty", text, "the flags never start over empty")
        aside = list(jd.STATE.glob("session-flags.json.corrupt-*"))
        self.assertEqual(len(aside), 1)
        self.assertIn(aside[0].name, text, "the notice names the sidecar, so the bytes can be found")
        with self.assertRaises(km._StateUnreadable):
            km._session_flags_proved()                                  # the next read is an ENOENT beside the sidecar: still refused
        self.assertEqual(len(self.notices), 1, "… and the quarantine files nothing more: a corrupt file speaks exactly once")
        km._flags_cache.clear()
        self.assertEqual(km._session_flags(), {}, "the display reader answers the unproved empty default")
        self.assertEqual(len(self.notices), 2, "…and files the hold once (the fault notice)")
        self.assertIn("mail is held for every session", self.notices[1][0])
        km._session_flags()
        self.assertEqual(len(self.notices), 2, "one notice per episode")

    def test_the_stderr_quarantine_line_is_branched_for_the_flags_file_like_the_notice(self):
        # round three, low (a): the stderr line said the store reads as empty for every file; for the flags it says what is held
        import io
        err = io.StringIO()
        self._path().write_bytes(b'{"torn": ')
        km._flags_cache.clear()
        with contextlib.redirect_stderr(err):
            km._session_flags()
        self.assertIn("session-flags.json could not be parsed", err.getvalue())
        self.assertIn("the flags it held are unknown", err.getvalue()); self.assertNotIn("reads as empty", err.getvalue())
        other = jd.STATE / "notify-cards.json"; other.write_bytes(b'{"torn": ')
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            km._read_state_json(other, expect=dict)
        self.assertIn("the store reads as empty", err2.getvalue(), "the other state files keep their sentence")

    def test_valid_json_of_the_wrong_shape_is_quarantined_not_read_as_empty(self):
        # review find, 2026-09-08: a LIST where the flags dict belongs read as a proved empty store and
        # was overwritten by the next writer -- a file none of our writers produce, gone without a trace
        wrong = b'["' + self.OTHER.encode() + b'"]'
        self._path().write_bytes(wrong)
        with self.assertRaises(km._StateUnreadable):                     # 2026-09-14: quarantined, and cold the proved read refuses
            km._session_flags_proved()
        aside = list(jd.STATE.glob("session-flags.json.corrupt-*"))
        self.assertEqual(len(aside), 1, "quarantined like torn bytes")
        self.assertEqual(aside[0].read_bytes(), wrong)
        self.assertFalse(self._path().exists())
        self.assertEqual([k for _t, _ok, k in self.notices], ["refused"])
        self._path().write_bytes(wrong)
        km._flags_cache.clear()
        self.assertEqual(km._session_flags(), {}, "the display reader quarantines it the same way")
        self.assertEqual(len(list(jd.STATE.glob("session-flags.json.corrupt-*"))), 2)

    def test_a_fault_serves_the_last_read_value_and_caches_nothing_new(self):
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        first = km._session_flags()                                    # a clean read primes the cache
        key1 = km._flags_cache[str(self._path())][0]
        self.assertEqual(first, {self.OTHER: {"postalServiceOff": True}})
        # the file moves on (a second session's flag lands), so the stat key changes and the next
        # read is a MISS -- a primed cache under the same key would be a hit and never read at all
        km._set_session_flag(self.SID, "hideFromFeed", True)
        key2 = self._path().stat(); key2 = (key2.st_mtime_ns, key2.st_size)
        self.assertNotEqual(key1, key2, "the write must move the stat key, or this test reads nothing")
        with _reads_fault(self._path()):
            served = km._session_flags()
            # round three (low b): the write primed the cache with the value it wrote, so the last KNOWN value is the
            # written one (this kernel's own write, not the unread file and not {}); the fault caches nothing new
            self.assertEqual(served, {self.OTHER: {"postalServiceOff": True}, self.SID: {"hideFromFeed": True}},
                             "the fault serves the LAST value this kernel read or wrote, not {}")
            self.assertEqual(km._flags_cache[str(self._path())][0], key2,
                             "the cache holds the write's key; the fault produced no value worth keeping")
        # the disk recovers: the reader reads the real, newer file (a reader that had cached the
        # fault's value under key2 would serve the stale copy here forever)
        self.assertEqual(km._session_flags(), {self.OTHER: {"postalServiceOff": True}, self.SID: {"hideFromFeed": True}})

    def test_a_fault_on_a_cold_cache_serves_the_empty_default_uncached(self):
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        with _reads_fault(self._path()):
            self.assertEqual(km._session_flags(), {}, "no known-good yet: the empty default, unproved")
            self.assertNotIn(str(self._path()), km._flags_cache, "…and it is NOT cached")
        self.assertEqual(km._session_flags(), {self.OTHER: {"postalServiceOff": True}},
                         "the real flags read once the fault clears -- the empty was never latched")

    def test_the_fault_is_loud_once_per_episode_and_a_clean_read_rearms_it(self):
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        with _reads_fault(self._path()):
            km._session_flags(); km._session_flags(); km._session_flags()
        bad = [t for t, ok, _k in self.notices if not ok]
        self.assertEqual(len(bad), 1, "one notice per fault episode, however many builds read the store")
        self.assertIn("session-flags.json could not be read", bad[0])
        self.assertIn("[Errno 5]", bad[0])
        km._session_flags()                                            # the clean read ends the episode
        self.assertNotIn(str(self._path()), km._state_fault_seen)
        with _reads_fault(self._path()):
            km._flags_cache.clear()
            km._session_flags()
        self.assertEqual(len([t for t, ok, _k in self.notices if not ok]), 2, "a fresh episode speaks again")


class FlagsWsRefusal(unittest.TestCase):
    """The setSessionFlag WS arm under a store fault: the file is untouched, the poster gets a
    `settingRefused` frame addressed to its toggle (sid + flag) on its OWN socket, and nothing
    escapes _dispatch_ws. A `warn` frame did not reach the timeline page (no handler) so the lane
    gear kept showing the refused state until a reload."""
    SID = "11111111-2222-3333-4444-555555555555"
    OTHER = "99999999-8888-7777-6666-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty)
        jd._rebind_state(Path(self.td.name))
        km._flags_cache.clear()
        self.dirty = []
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        km._mark_views_dirty = self.saved[1]
        km._flags_cache.clear()
        self.td.cleanup()

    def _client(self):
        sent = []
        return {"app": "timeline", "wid": "w1", "alive": True,
                "send": lambda raw: sent.append(json.loads(raw))}, sent

    def test_a_refused_toggle_answers_the_poster_and_leaves_the_file_alone(self):
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        p = jd.STATE / "session-flags.json"
        before = p.read_bytes()
        for flag in ("hideFromFeed", "notify"):                       # both arms of the branch: the plain setter and the tri-state bell
            client, sent = self._client()
            with _reads_fault(p):
                km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": flag, "value": True}, client)
            self.assertEqual(p.read_bytes(), before, "%s: the flags file is byte-for-byte unchanged" % flag)
            self.assertEqual(len(sent), 1, "%s: exactly one frame, on the delivering socket" % flag)
            fr = sent[0]
            self.assertEqual((fr["type"], fr["sid"], fr["flag"], fr["itemId"]), ("settingRefused", self.SID, flag, ""))
            self.assertEqual(fr["gesture"], "flag", "the frame names its gesture; no pane infers it from empty fields")
            self.assertIs(fr["value"], False, "the value the kernel still paints for this flag rides along (here: unset -> off)")
            self.assertIn("couldn't save that setting", fr["text"])
            self.assertIn("session-flags.json could not be read", fr["text"])
            self.assertNotIn("warn", fr["type"])
        self.assertEqual(self.dirty, [], "a refused write marks nothing dirty -- there is nothing new to push")

    def test_a_clean_toggle_still_lands_and_sends_no_refusal(self):
        client, sent = self._client()
        km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": "hideFromFeed", "value": True}, client)
        self.assertEqual(sent, [], "no frame on success -- the next push carries the value")
        self.assertTrue(km._session_flag(self.SID, "hideFromFeed"))
        self.assertEqual(self.dirty, [1])

    def test_a_refusal_carries_the_painted_value_when_it_is_on(self):
        # `value` is what the display path still PAINTS, so the pane repaints a refused toggle to it. Every
        # other case here paints off (review find, 2026-09-08: a constant False survived), so this one
        # paints ON for both arms: the flag is set, and the master is on (the bell's default with no
        # override). The file then moves on (a new stat key) so the faulted read is a real miss served
        # from the primed cache, not a cache hit
        km._set_session_flag(self.SID, "hideFromFeed", True)
        km._set_notify_all(True)
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        km._session_flags(); km._notify_cards()                         # prime the display caches
        km._set_session_flag(self.OTHER, "postalServiceOff", True)       # bump the flags file's stat key
        p = jd.STATE / "session-flags.json"
        before = p.read_bytes()
        for flag in ("hideFromFeed", "notify"):
            client, sent = self._client()
            with _reads_fault(p):
                km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": flag, "value": False}, client)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["gesture"], "flag")
            self.assertIs(sent[0]["value"], True, "%s: the kernel still paints ON, and the frame says so" % flag)
        self.assertEqual(p.read_bytes(), before)


class SessionBellJudgedAgainstAProvedMaster(unittest.TestCase):
    """_set_notify_session judges the click against the MASTER bell, which lives in the OTHER store
    (notify-cards.json). Read through the display reader, a fault there folded the master to off, so a
    click matching that fabricated master popped the session's stored override and rewrote
    session-flags.json without it -- the user's mute erased under the success path. The master is read
    PROVED now: a fault on the bells file refuses the flags write exactly like a fault on the flags file."""
    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty)
        jd._rebind_state(Path(self.td.name))
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        self.dirty = []
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        km._mark_views_dirty = self.saved[1]
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        self.td.cleanup()

    def test_a_session_bell_click_is_refused_when_the_bells_store_cannot_be_read(self):
        km._set_notify_all(True)                                   # the master is ON …
        km._set_notify_session(self.SID, False)                    # … and this session is MUTED (a stored override)
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        flags_p, cards_p = jd.STATE / "session-flags.json", jd.STATE / "notify-cards.json"
        self.assertEqual(json.loads(flags_p.read_text()), {self.SID: {"notify": False}})
        before = flags_p.read_bytes()
        raised = None
        with _reads_fault(cards_p):                                # the OTHER store faults
            try:
                km._set_notify_session(self.SID, False)            # the user clicks mute again (or the pane re-sends it)
            except Exception as e:                                 # noqa: BLE001 -- before the fix it never raises
                raised = e
        # before the fix: the master folded to off, False == off, the override was POPPED and the flags file
        # rewritten as {} -- the mute gone. Now the write is refused and the file is byte-for-byte unchanged.
        self.assertEqual(flags_p.read_bytes(), before, "the flags file must be unchanged when the bells file can't be read")
        self.assertEqual(type(raised).__name__, "_StateUnreadable", "refused loudly (got %r)" % raised)
        self.assertIn("notify-cards.json", str(raised), "the refusal names the store that faulted")

    def test_the_ws_arm_refuses_a_session_bell_on_the_other_store_s_fault(self):
        km._set_notify_all(True); km._set_notify_session(self.SID, False)
        km._flags_cache.clear(); km._notify_cards_cache.clear()
        flags_p, cards_p = jd.STATE / "session-flags.json", jd.STATE / "notify-cards.json"
        before = flags_p.read_bytes()
        sent = []
        client = {"app": "timeline", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
        with _reads_fault(cards_p):
            km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": "notify", "value": False}, client)
        self.assertEqual(flags_p.read_bytes(), before)
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]["type"], sent[0]["gesture"], sent[0]["flag"]), ("settingRefused", "flag", "notify"))
        self.assertIn("notify-cards.json could not be read", sent[0]["text"])
        self.assertEqual(self.dirty, [])


class FlagsWsWriteFailure(unittest.TestCase):
    """The setSessionFlag WS arm when the store READS but its PUBLISH fails (ENOSPC, EROFS, EACCES): the
    maintainer's fold on PR #1019 -- a user gesture's WRITE step is a fault boundary too. Before, the arm
    caught only _StateUnreadable, so the OSError out of _atomic_write escaped _dispatch_ws to the receive
    loop, which re-raises any OSError as a socket failure: the dashboard was DROPPED without a word. Now
    the publish raises _StateUnwritable, the arm answers the same settingRefused frame ("could not be
    written"), the file is untouched, the fault is filed once per episode on the path's registry, and a
    landed write ends the episode."""
    SID = "11111111-2222-3333-4444-555555555555"
    OTHER = "99999999-8888-7777-6666-555555555555"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty, km._sync_notice)
        jd._rebind_state(Path(self.td.name))
        km._flags_cache.clear(); km._notify_cards_cache.clear(); km._state_fault_seen.clear()
        vars(km).get("_state_write_fault_seen", {}).clear()
        self.dirty, self.notices = [], []
        km._mark_views_dirty = lambda: self.dirty.append(1)
        km._sync_notice = lambda text, ok=True, kind="sync": self.notices.append((text, ok))

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        km._mark_views_dirty, km._sync_notice = self.saved[1:]
        km._flags_cache.clear(); km._notify_cards_cache.clear(); km._state_fault_seen.clear()
        vars(km).get("_state_write_fault_seen", {}).clear()
        self.td.cleanup()

    def _client(self):
        sent = []
        return {"app": "timeline", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}, sent

    def test_a_failed_publish_answers_the_poster_instead_of_dropping_the_socket(self):
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        p = jd.STATE / "session-flags.json"
        before = p.read_bytes()
        with _writes_fault(p):
            for flag in ("hideFromFeed", "notify"):                   # the plain setter and the tri-state bell
                client, sent = self._client()
                try:
                    km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": flag, "value": True}, client)
                except OSError:
                    self.fail("%s: the OSError escaped _dispatch_ws -- the receive loop re-raises it and drops the client" % flag)
                self.assertTrue(client["alive"])
                self.assertEqual(p.read_bytes(), before, "%s: the flags file is byte-for-byte unchanged" % flag)
                self.assertEqual(len(sent), 1, "%s: exactly one frame, on the delivering socket" % flag)
                fr = sent[0]
                self.assertEqual((fr["type"], fr["gesture"], fr["sid"], fr["flag"]), ("settingRefused", "flag", self.SID, flag))
                self.assertIn("couldn't save that setting", fr["text"])
                self.assertIn("session-flags.json could not be written", fr["text"])
                self.assertIn("No space left on device", fr["text"])
                self.assertNotIn(".tmp.", fr["text"], "errno + strerror only, never the temp path")
                self.assertIs(fr["value"], False, "the value the kernel still paints rides along")
        self.assertEqual(self.dirty, [], "a refused write marks nothing dirty")
        self.assertEqual(len([t for t, ok in self.notices if not ok]), 1, "the fault is filed ONCE per episode, not per click")
        self.assertIn("could not be written", self.notices[0][0])
        self.assertIn("not saved", self.notices[0][0])
        # the disk heals: the next click lands, ends the episode, and a fresh fault speaks again
        client, sent = self._client()
        km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": "hideFromFeed", "value": True}, client)
        self.assertEqual(sent, [])
        self.assertTrue(km._session_flag(self.SID, "hideFromFeed"))
        self.assertNotIn(str(p), km._state_write_fault_seen, "a landed write ends the episode")
        with _writes_fault(p):
            client, sent = self._client()
            km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": "hideFromFeed", "value": False}, client)
        self.assertEqual([m["type"] for m in sent], ["settingRefused"])
        self.assertEqual(len([t for t, ok in self.notices if not ok]), 2, "a new episode is filed again")
        self.assertTrue(km._session_flag(self.SID, "hideFromFeed"), "nothing applied: the store keeps the value")

    def test_a_failed_rename_refuses_the_same_way_and_leaves_no_temp_behind(self):
        # the publish's OTHER fault point (review find, 2026-09-08): the temp WROTE, and the rename over
        # the live file failed (EACCES on the directory, EROFS). The same refusal on the socket, and
        # _atomic_write's unlink means the failed publish leaves no `<name>.tmp.*` beside the store
        km._set_session_flag(self.OTHER, "postalServiceOff", True)
        km._flags_cache.clear()
        p = jd.STATE / "session-flags.json"
        before = p.read_bytes()
        client, sent = self._client()
        with mock.patch.object(km.os, "replace", side_effect=PermissionError(errno.EACCES, "Permission denied")):
            km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": "hideFromFeed", "value": True}, client)
        self.assertTrue(client["alive"])
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual([m["type"] for m in sent], ["settingRefused"])
        self.assertIn("session-flags.json could not be written (write failed: [Errno 13] Permission denied)", sent[0]["text"])
        self.assertEqual(list(jd.STATE.glob("session-flags.json.tmp.*")), [], "the failed publish left no temp behind")

    def test_the_setter_raises_the_plain_exception_never_the_os_error(self):
        p = jd.STATE / "session-flags.json"
        with _writes_fault(p):
            with self.assertRaises(km._StateUnwritable) as cm:
                km._set_session_flag(self.SID, "hideFromFeed", True)
        self.assertNotIsInstance(cm.exception, OSError, "an OSError is what the receive loop reads as a dead socket")
        self.assertEqual(str(cm.exception), "session-flags.json could not be written (write failed: [Errno 28] No space left on device)")
        self.assertEqual((cm.exception.path.name, cm.exception.fault), ("session-flags.json", "write failed: [Errno 28] No space left on device"))
        self.assertFalse(p.exists(), "nothing was published")


class SyncNoticeRowsCarryAKind(unittest.TestCase):
    """The ring the shell's bell mirrors carries each row's KIND (review find, 2026-09-08): "sync" by
    default (a machine sync, the ring's original tenant), "refused" for a state-file fault or a
    quarantine, so the shell files the two apart and a mute on one never hides the other."""

    def setUp(self):
        self._ring, self._seq = list(km._SYNC_NOTICES), km._SYNC_SEQ

    def tearDown(self):
        km._SYNC_NOTICES[:] = self._ring
        km._SYNC_SEQ = self._seq

    def test_the_default_is_sync_and_a_fault_files_refused(self):
        km._sync_notice("pushed this machine's build to TESTHOST")
        self.assertEqual(km._sync_notice_rows()[-1]["kind"], "sync")
        km._sync_notice("a fault", ok=False, kind="refused")
        row = km._sync_notice_rows()[-1]
        self.assertEqual((row["kind"], row["ok"]), ("refused", False))
        km._state_fault_seen.clear(); km._state_write_fault_seen.clear()
        km._note_state_fault(km._StateUnreadable(Path("/x/session-flags.json"), "read failed: [Errno 5] Input/output error"))
        row = km._sync_notice_rows()[-1]
        self.assertEqual(row["kind"], "refused", "the display-read fault reaches the bell under its own kind")
        self.assertIn("session-flags.json could not be read", row["text"])
        km._note_state_fault(km._StateUnwritable(Path("/x/session-flags.json"), "write failed: [Errno 28] No space left on device"))
        km._state_fault_seen.clear(); km._state_write_fault_seen.clear()
        row = km._sync_notice_rows()[-1]
        self.assertEqual(row["kind"], "refused", "and so does a write fault")
        self.assertIn("session-flags.json could not be written", row["text"])


NODE = shutil.which("node")
VIEW_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "ui", "romp-timeline-view.js")

# The timeline's Obsidian-desktop flag writer, executed in node against a stubbed _kernelPost and a stubbed
# fs (the tests/test_timeline_touch.py harness shape). Synthetic sids; ROMP_STATE_DIR is a temp dir and fs
# is stubbed besides, so no real file is ever touched.
_WRITER_HARNESS = r"""
const { TimelinePanel } = require(process.argv[1]);
const fs = require('fs');
process.env.ROMP_STATE_DIR = process.argv[2];
const SID = '11111111-2222-3333-4444-555555555555';
const real = { writeFileSync: fs.writeFileSync, renameSync: fs.renameSync, readFileSync: fs.readFileSync };
function run(answer) {
  const writes = [], renames = [], reads = [], posts = [];
  fs.writeFileSync = function (p, data) { writes.push([String(p), String(data)]); };
  fs.renameSync = function (a, b) { renames.push([String(a), String(b)]); };
  fs.readFileSync = function (p) { reads.push(String(p)); return real.readFileSync.apply(fs, arguments); };
  const v = Object.create(TimelinePanel.prototype);
  v.draw = () => {};
  v.data = { sessions: [{ id: SID, hideFromFeed: true }] };            // the gear's optimistic click, already painted…
  v._pendingFlags = { [SID]: { hideFromFeed: true } };                  // …and held sticky
  v._laneRefusal = null; v._laneMenu = null; v._laneMenuBuild = null;
  v._kernelPost = (route, body) => { posts.push([route, body]); return Promise.resolve(answer); };
  try { v._setSessionFlag({ id: SID }, 'hideFromFeed', true); } finally { Object.assign(fs, real); }
  return new Promise((res) => setImmediate(() => res({ writes, renames, reads, posts, refusal: v._laneRefusal,
                                                          painted: v.data.sessions[0].hideFromFeed, pending: v._pendingFlags })));
}
(async () => {
  const r = {};
  process.versions.electron = '1.0.0-test';                   // the Electron guard: the writer runs at all
  r.ok = await run({ ok: true, body: { ok: true, id: SID, flag: 'hideFromFeed', value: true } });
  r.refused = await run({ ok: false, refusal: true, body: { ok: false, value: false },
                          error: "couldn't save that setting — session-flags.json could not be read (read failed: [Errno 5] injected EIO); try again" });
  r.down = await run({ ok: false, unreachable: true, error: 'the kernel is not running; start romp and try again' });
  delete process.versions.electron;
  r.noElectron = await run({ ok: true, body: { ok: true } });   // a bare-node run: nothing at all
  process.stdout.write(JSON.stringify(r));
})();
"""


@unittest.skipUnless(NODE, "node not available")
class TimelineFlagWriterPostsThroughTheKernel(unittest.TestCase):
    """The timeline's Obsidian-desktop flag writer (2026-09-08). Until this change it read-modify-wrote
    session-flags.json itself (PR #1020 had made that write honest: the Electron guard, the kernel's
    state root, tmp + rename) -- a SECOND WRITER of the flags with no lock against the kernel's own
    setter. It now posts the kernel's /flag, which lands through _set_session_flag with the socket op's
    validation, and a refusal takes the settingRefused door the lane gear already renders from: the
    optimistic state ends, the toggle repaints to what the kernel still paints, the reason shows. With
    no kernel answering the gesture is refused and says so; the panel never writes the file."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        out = subprocess.run([NODE, "-e", _WRITER_HARNESS, VIEW_JS, cls.td.name],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        cls.r = json.loads(out.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_the_toggle_is_one_post_through_the_kernel_and_never_a_file(self):
        for case in ("ok", "refused", "down"):
            self.assertEqual(self.r[case]["posts"], [["/flag", {"id": "11111111-2222-3333-4444-555555555555",
                                                                 "flag": "hideFromFeed", "value": True}]], case)
            self.assertEqual((self.r[case]["writes"], self.r[case]["renames"]), ([], []),
                             "%s: the panel writes no state file of its own, kernel up or down" % case)
            self.assertFalse(any(p.endswith("session-flags.json") for p in self.r[case]["reads"]),
                             "%s: nor does it read one to modify" % case)

    def test_an_accepted_write_holds_its_optimistic_state_until_the_poll_confirms_it(self):
        ok = self.r["ok"]
        self.assertEqual((ok["painted"], ok["pending"], ok["refusal"]),
                         (True, {"11111111-2222-3333-4444-555555555555": {"hideFromFeed": True}}, None))

    def test_the_kernels_refusal_ends_the_optimistic_state_in_the_kernels_words(self):
        rf = self.r["refused"]
        self.assertEqual(rf["pending"], {}, "the sticky copy is released")
        self.assertIs(rf["painted"], False, "the toggle repaints to what the kernel still paints (the value it sent)")
        self.assertEqual(rf["refusal"], {"sid": "11111111-2222-3333-4444-555555555555", "flag": "hideFromFeed",
                                         "text": "couldn't save that setting — session-flags.json could not be read (read failed: [Errno 5] injected EIO); try again"})

    def test_no_kernel_is_a_refusal_that_says_so_with_the_toggle_back_where_the_click_found_it(self):
        dn = self.r["down"]
        self.assertEqual(dn["pending"], {})
        self.assertIs(dn["painted"], False)
        self.assertEqual(dn["refusal"]["text"], "couldn't save that setting — the kernel is not running; start romp and try again")

    def test_without_electron_nothing_is_posted_or_touched(self):
        ne = self.r["noElectron"]
        self.assertEqual((ne["posts"], ne["writes"], ne["renames"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()


# The gear moved from kernel-inline strings into the shared feed bundle
# (2026-07-13): ui/webview/gear.js is the single source both hosts render, so
# the gear pins read THAT file (and feed.css for its styling).
def _gear_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()


def _gear_css_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.css").read_text()


class WsFlagsMustBeBooleans(unittest.TestCase):
    """Every WS flag the dashboards send takes JSON true/false and nothing else. Each handler used to
    coerce with bool(), so the STRING "false" enabled auto-nudge, file editing, compact suggestions,
    thinking summaries and conserve-memory, paused API retries across every session, set a lane flag,
    armed a card bell, forwarded a tag DELETE to its home kernel, made a directory and enabled an MCP
    server. Now a non-boolean is refused on the delivering socket -- a `warn` frame naming the field;
    setSessionFlag and cardNotify on the settingRefused frame their pages (timeline, feed) render and
    repaint from, a `warn` never reaching either; editTag on its own tagEditFailed frame; mcpAction in
    its mcpResult error -- and the setting is untouched. The shipped panes send real booleans
    (render.ts, feed.ts, timeline-boot.ts), so their frames are unchanged."""
    SID = "11111111-2222-3333-4444-555555555555"
    BAD = ("false", "true", "no", 1, 0)

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._mark_views_dirty, km._push_soon, km._ws_act_now_tick)
        jd._rebind_state(Path(self.td.name))
        km._flags_cache.clear()
        km._mark_views_dirty = lambda: None
        km._push_soon = lambda: None
        km._ws_act_now_tick = lambda: None           # turn-on acts at once in service; not under test here

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        km._mark_views_dirty, km._push_soon, km._ws_act_now_tick = self.saved[1:]
        km._flags_cache.clear()
        self.td.cleanup()

    def _client(self):
        sent = []
        return {"app": "chat", "wid": "w1", "alive": True,
                "send": lambda raw: sent.append(json.loads(raw))}, sent

    def _refused(self, frame, op, field):
        client, sent = self._client()
        km.Handler._dispatch_ws(None, frame, client)
        self.assertEqual(len(sent), 1, (op, frame, sent))
        self.assertEqual(sent[0]["type"], "warn", (op, sent))
        self.assertEqual(sent[0]["text"], "%s: '%s' must be true or false, got %s"
                         % (op, field, json.dumps(frame[field])))

    def _refused_setting(self, frame, gesture, field, sid, flag="", item_id=""):
        """The two page-handled flags refuse on settingRefused, addressed to the toggle, carrying the
        value the kernel still paints (nothing was written, so: the default, off)."""
        client, sent = self._client()
        km.Handler._dispatch_ws(None, frame, client)
        self.assertEqual(len(sent), 1, (gesture, frame, sent))
        fr = sent[0]
        self.assertEqual((fr["type"], fr["gesture"], fr["sid"], fr["flag"], fr["itemId"]),
                         ("settingRefused", gesture, sid, flag, item_id), fr)
        self.assertIs(fr["value"], False, "the painted value rides along: nothing was written")
        self.assertIn("'%s' must be true or false, got %s" % (field, json.dumps(frame[field])), fr["text"])
        self.assertTrue(fr["text"].startswith("couldn't save"), fr["text"])

    def test_kernel_settings_refuse_strings_and_apply_real_booleans(self):
        warn = lambda frame, op, field: self._refused(frame, op, field)
        lane = lambda frame, op, field: self._refused_setting(frame, "flag", field, self.SID, flag="hideFromFeed")
        cases = (
            ("setAutoNudge", "enabled", {}, km._auto_nudge_on, warn),
            ("setCompactSuggest", "enabled", {}, km._compact_suggest_on, warn),
            ("setFileEditing", "enabled", {}, km._file_editing_on, warn),
            ("setThinkingSummaries", "enabled", {}, km._thinking_summaries_on, warn),
            ("setWholeChatFrames", "enabled", {}, km._whole_chat_frames_on, warn),   # the Whole chat frames switch (2026-09-15)
            ("setConserve", "enabled", {}, km._conserve_on, warn),
            ("setJudgeFast", "enabled", {}, lambda: km.jd._state_str("judge-fast", "off") == "on", warn),   # Fast mode, the triage tier's box
            ("setDistillFast", "enabled", {}, lambda: km.jd._state_str("distill-fast", "off") == "on", warn),   # T300: a box per tier
            ("setIndexFast", "enabled", {}, lambda: km.jd._state_str("index-fast", "off") == "on", warn),
            ("setAlwaysFast", "enabled", {}, lambda: km.jd._state_str("always-fast", "off") == "on", warn),   # the model switches (Settings, Automation, Model; 2026-09-17)
            ("setRetryUpgrade", "enabled", {}, lambda: km.jd._state_str("retry-upgrade", "off") == "on", warn),
            ("setGlobalRetryPaused", "value", {}, km._retry_paused_on, warn),
            ("setSessionFlag", "value", {"id": self.SID, "flag": "hideFromFeed"},
             lambda: km._session_flag(self.SID, "hideFromFeed"), lane),
        )
        for op, field, extra, reader, refused in cases:
            before = reader()                        # each setting's own default (auto-nudge ships ON)
            for bad in self.BAD:
                refused(dict({"type": op, field: bad}, **extra), op, field)
                km._flags_cache.clear()
                self.assertEqual(reader(), before, "%s: %r left the setting untouched" % (op, bad))
            for want in (not before, before):        # a real boolean flips it, and flips it back
                client, sent = self._client()
                km.Handler._dispatch_ws(None, dict({"type": op, field: want}, **extra), client)
                km._flags_cache.clear()
                self.assertEqual(reader(), want, "%s: a real %r applies" % (op, want))
                self.assertEqual(sent, [], "%s: no frame on success" % op)

    def test_the_session_bell_and_the_card_bell_refuse_strings(self):
        card = self.SID + ":g1"
        for bad in self.BAD:
            self._refused_setting({"type": "setSessionFlag", "id": self.SID, "flag": "notify", "value": bad},
                                  "flag", "value", self.SID, flag="notify")
            self._refused_setting({"type": "cardNotify", "itemId": card, "sid": self.SID, "value": bad},
                                  "bell", "value", self.SID, item_id=card)
        km._flags_cache.clear()
        self.assertEqual(km._session_flags(), {}, "no bell override was written")
        self.assertEqual(km._notify_cards(), {}, "no card override was written")
        client, sent = self._client()
        km.Handler._dispatch_ws(None, {"type": "cardNotify", "itemId": card, "sid": self.SID, "value": True}, client)
        self.assertEqual(sent, [])
        self.assertIs(km._notify_cards().get(card), True, "a real true arms the card (the master is off)")
        km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": "notify", "value": True},
                                self._client()[0])
        km._flags_cache.clear()
        self.assertEqual(km._session_flags().get(self.SID), {"notify": True})

    def test_the_log_names_the_field_and_its_type_never_the_value(self):
        # review find, 2026-09-08: the stderr line carried up to 60 characters of whatever a client put
        # in the field; the echo belongs in the frame the sender gets, the log names the field and type
        import io
        leak = "SECRET-VALUE-TESTHOST"
        frames = (
            ({"type": "setAutoNudge", "enabled": leak}, "'enabled' is a string, not a boolean", "warn"),
            ({"type": "setGlobalRetryPaused", "value": [leak]}, "'value' is an array, not a boolean", "warn"),
            ({"type": "setSessionFlag", "id": self.SID, "flag": "hideFromFeed", "value": leak},
             "'value' is a string, not a boolean", "settingRefused"),
            ({"type": "cardNotify", "itemId": self.SID + ":g1", "sid": self.SID, "value": {"k": leak}},
             "'value' is an object, not a boolean", "settingRefused"),
            ({"type": "createSession", "name": "fresh", "dir": "/nonexistent/TESTHOST", "mkdir": 7},
             "'mkdir' is a number, not a boolean", "warn"),
        )
        for frame, note, kind in frames:
            client, sent = self._client()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                km.Handler._dispatch_ws(None, frame, client)
            line = err.getvalue()
            self.assertIn("romp-kernel: refused %s: %s" % (frame["type"], note), line, (frame, line))
            self.assertNotIn(leak, line, "the client's value stays out of the kernel's log")
            self.assertEqual(len(sent), 1, (frame, sent))
            self.assertEqual(sent[0]["type"], kind)
            field = [k for k in ("enabled", "value", "mkdir") if k in frame][0]
            self.assertIn("'%s' must be true or false, got %s" % (field, json.dumps(frame[field])), sent[0]["text"],
                          "the sender still sees what it sent")

    def test_an_explicit_null_value_reads_as_absent(self):
        # the rule _as_bool states (review find, 2026-09-08): null takes the field's default (off), where a
        # string or a number is refused; no frame, since nothing was refused
        km._set_session_flag(self.SID, "hideFromFeed", True)
        km._flags_cache.clear()
        self.assertTrue(km._session_flag(self.SID, "hideFromFeed"))
        client, sent = self._client()
        km.Handler._dispatch_ws(None, {"type": "setSessionFlag", "id": self.SID, "flag": "hideFromFeed", "value": None}, client)
        km._flags_cache.clear()
        self.assertFalse(km._session_flag(self.SID, "hideFromFeed"))
        self.assertEqual(sent, [])

    def test_no_ws_handler_coerces_a_gated_flag_with_bool(self):
        # moved here from ui/timeline-flags.test.ts (review find, 2026-09-08): a kernel source pin
        # belongs in the kernel's own lane, where a kernel change runs it. The behaviour itself is
        # test_kernel_settings_refuse_strings_and_apply_real_booleans above; this catches a coercion
        # slipped back in beside the gate
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        for field in ('msg.get("value")', 'msg.get("enabled")', 'msg.get("mkdir")', 'e.get("delete")',
                      'b.get("delete")', 'b.get("on")', 'b.get("mkdir")', 'body.get("on")', 'msg["enabled"]'):
            self.assertNotIn("bool(%s)" % field, src, "%s is checked by _as_bool, never coerced" % field)
        self.assertNotIn('if msg.get("enabled") else', src, "a truthiness ternary is a coercion too (the T288 review's find)")

    def test_create_session_refuses_a_string_mkdir_before_touching_the_disk(self):
        calls = []
        saved = km._resolve_create_dir
        km._resolve_create_dir = (lambda raw, create=False:
                                  calls.append((raw, create)) or ("", "stubbed: no directory here"))
        try:
            for bad in self.BAD:
                self._refused({"type": "createSession", "name": "fresh", "dir": "/nonexistent/TESTHOST",
                               "mkdir": bad}, "createSession", "mkdir")
            self.assertEqual(calls, [], "no directory is resolved, let alone created, on a malformed flag")
        finally:
            km._resolve_create_dir = saved

    def test_edit_tag_refuses_a_string_delete_on_its_own_failure_frame(self):
        fwd, views = [], []
        saved = (km._forward_tag_edit, km._send_to_view)
        km._forward_tag_edit = lambda host, body: fwd.append((host, body)) or ({"ok": True, "tag": {}}, None)
        km._send_to_view = lambda app, msg, wid: views.append((app, msg, wid))
        try:
            for bad in self.BAD:
                client, sent = self._client()
                km.Handler._dispatch_ws(None, {"type": "editTag",
                                               "edit": {"host": "TESTHOST", "name": "pool", "delete": bad}}, client)
                self.assertEqual(sent, [], "the refusal rides tagEditFailed, where a refused edit lands")
                app, msg, wid = views.pop()
                self.assertEqual((app, msg["type"], msg["host"], msg["name"], msg["queued"], wid),
                                 ("timeline", "tagEditFailed", "TESTHOST", "pool", False, "w1"))
                self.assertEqual(msg["error"], "editTag: 'delete' must be true or false, got %s" % json.dumps(bad))
            self.assertEqual(fwd, [], "nothing crossed to the home kernel (a string used to forward a DELETE)")
            km.Handler._dispatch_ws(None, {"type": "editTag", "edit": {"host": "TESTHOST", "name": "pool", "delete": True}},
                                    self._client()[0])
            self.assertEqual(fwd, [("TESTHOST", {"name": "pool", "delete": True})])
            km.Handler._dispatch_ws(None, {"type": "editTag", "edit": {"host": "TESTHOST", "name": "pool", "delete": False}},
                                    self._client()[0])
            self.assertEqual(fwd[-1], ("TESTHOST", {"name": "pool"}), "a real false forwards no delete key, as before")
            self.assertEqual(views, [], "no failure frame for a real boolean")
        finally:
            km._forward_tag_edit, km._send_to_view = saved

    def test_mcp_action_refuses_a_string_enabled_in_its_result_frame(self):
        calls = []

        class BE:
            def mcp_action(self, sid, name, action, enabled=True):
                calls.append((sid, name, action, enabled))
                return ""
        saved = (km.Sessions.backend_for, km._name_of, km._sdk)
        km.Sessions.backend_for = staticmethod(lambda sid: BE())
        km._name_of = lambda sid: "web" if sid == self.SID else None    # _kernel_knows: ours
        km._sdk = lambda: None
        try:
            for bad in self.BAD:
                client, sent = self._client()
                self.assertTrue(km._drive({"type": "mcpAction", "id": self.SID, "server": "postal",
                                           "action": "enable", "enabled": bad}, client), "consumed: refused, not passed on")
                self.assertEqual(len(sent), 1, sent)
                self.assertEqual((sent[0]["type"], sent[0]["server"]), ("mcpResult", "postal"))
                self.assertEqual(sent[0]["error"], "'enabled' must be true or false, got %s" % json.dumps(bad))
            self.assertEqual(calls, [], "no control request reaches the session on a malformed flag")
            client, sent = self._client()
            km._drive({"type": "mcpAction", "id": self.SID, "server": "postal", "action": "enable", "enabled": False}, client)
            self.assertEqual(calls, [(self.SID, "postal", "enable", False)], "a real false rides through as itself")
            self.assertEqual(sent[0]["error"], "")
            km._drive({"type": "mcpAction", "id": self.SID, "server": "postal", "action": "reconnect"}, client)
            self.assertEqual(calls[-1], (self.SID, "postal", "reconnect", True), "absent keeps its old default")
        finally:
            km.Sessions.backend_for = staticmethod(saved[0])
            km._name_of, km._sdk = saved[1], saved[2]
