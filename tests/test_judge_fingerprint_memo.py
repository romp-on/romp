#!/usr/bin/env python3
"""_discover_fingerprint() MEMOIZES each names/ entry, so a cache hit stats instead of re-reading.

The fingerprint is discover()'s cache-validity check, so it runs on EVERY discover() call — and every
call re-opened and re-read all ~226 names/ entries, then threw the contents away. Profiling a hot kernel
(the user 2026-07-22, whose laptop was running its fans) put `open (pathlib)` at ~110% of one core, the
whole of the pegged core; the fingerprint was the caller. Measured on that fleet: 7.85ms per call, and
0.76ms once each entry's parsed launch dir is memoized against its own mtime.

The memo keys on the entry's mtime — the same exact-change idiom _sdk_last_sid already uses two functions
up, NOT a time heuristic. What must stay LIVE is the project dir's mtime: that is the fork signal, so the
memo may cache the resolved PATH but must re-stat it every call. Synthetic only: placeholder UUIDs,
TESTHOST names, hermetic temp STATE.
"""
import json
import os
import pathlib
import shutil
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
FORK = "66666666-7777-8888-9999-aaaaaaaaaaaa"
OTHER = "99999999-0000-1111-2222-333333333333"
NAME = "TESTHOST-session"


class NamesReadCounter:
    """Counts read_text() calls against names/ entries only — the syscall the memo is there to avoid."""

    def __init__(self, names_dir):
        self.names = str(names_dir)
        self.n = 0
        self._orig = pathlib.Path.read_text

    def __enter__(self):
        orig, names, box = self._orig, self.names, self

        def counting(self, *a, **k):
            if str(self).startswith(names):
                box.n += 1
            return orig(self, *a, **k)

        pathlib.Path.read_text = counting
        return self

    def __exit__(self, *exc):
        pathlib.Path.read_text = self._orig
        return False


class FingerprintMemoTest(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()                           # module-globals → reset between tests
        jd._namefp_memo.clear()
        jd._lastsid_memo.clear()
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, self.cdir))
        self._write_transcript(SID)

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        jd._namefp_memo.clear()
        shutil.rmtree(self._td, ignore_errors=True)

    def _write_transcript(self, sid, title=None):
        p = self.proj / (sid + ".jsonl")
        head = json.dumps({"type": "custom-title", "customTitle": title or NAME}) + "\n"
        p.write_text(head + json.dumps({"type": "user", "uuid": "u1"}) + "\n")

    def _touch(self, p, delta=10):
        """Move an mtime EXPLICITLY: a rewrite inside one filesystem timestamp tick would otherwise be
        indistinguishable, and this test is about the mtime signal, not about clock resolution."""
        st = os.stat(p)
        os.utime(p, (st.st_atime + delta, st.st_mtime + delta))

    # ── the fix ─────────────────────────────────────────────────────────────
    def test_an_unchanged_entry_is_not_reread(self):
        jd._discover_fingerprint()                            # first call populates the memo
        with NamesReadCounter(jd.NAMES) as c:
            jd._discover_fingerprint()
            jd._discover_fingerprint()
            jd._discover_fingerprint()
        self.assertEqual(c.n, 0, "an unchanged names/ entry is never re-read — that IS the fix")

    def test_the_first_call_does_read_every_entry(self):
        with NamesReadCounter(jd.NAMES) as c:
            jd._discover_fingerprint()
        self.assertEqual(c.n, 1, "a cold memo still reads each entry exactly once")

    def test_a_deleted_entry_is_dropped_from_the_memo(self):
        (jd.NAMES / OTHER).write_text("%s\t%s" % ("TESTHOST-two", self.cdir))
        jd._discover_fingerprint()
        self.assertIn(OTHER, jd._namefp_memo)
        (jd.NAMES / OTHER).unlink()
        jd._discover_fingerprint()
        self.assertNotIn(OTHER, jd._namefp_memo,
                         "a retired session's entry is evicted — the memo can't grow without bound")

    # ── what must NOT change ────────────────────────────────────────────────
    def test_the_memo_does_not_change_the_fingerprint_value(self):
        def reference():
            """The pre-memo computation, inline: read every entry, every call."""
            out = []
            for f in sorted(jd.NAMES.iterdir()):
                try:
                    mt = f.stat().st_mtime
                except OSError:
                    continue
                try:
                    parts = f.read_text().rstrip("\n").split("\t")
                    cdir = parts[1] if len(parts) > 1 else ""
                except Exception:
                    cdir = ""
                pm = 0
                if cdir:
                    try:
                        pm = os.stat(jd._proj_dir(cdir)).st_mtime
                    except OSError:
                        pm = 0
                rec = jd._sdk_transcript_path(f.name) or ""   # signed like lastSid, with its dir's mtime like pm
                rm = 0
                if rec:
                    try:
                        rm = os.stat(os.path.dirname(rec)).st_mtime
                    except OSError:
                        rm = 0
                last = jd._sdk_last_sid(f.name) or ""
                signed = jd._signed_mtime(f.name, rec, last, jd._proj_dir(cdir) if cdir else None)
                out.append((f.name, mt, pm, last, rec, rm, jd._in_window(signed, int(time.time()))))
            return tuple(out)

        (jd.NAMES / OTHER).write_text("%s\t%s" % ("TESTHOST-two", self.cdir))
        self.assertEqual(jd._discover_fingerprint(), reference(), "cold memo matches the old computation")
        self.assertEqual(jd._discover_fingerprint(), reference(), "warm memo matches it too")

    def test_a_rewritten_entry_is_reread_and_moves_the_fingerprint(self):
        a = jd._discover_fingerprint()
        cdir2 = str(Path(self._td) / "work2")
        jd._proj_dir(cdir2).mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, cdir2))     # session relaunched from a new dir
        self._touch(jd.NAMES / SID)
        with NamesReadCounter(jd.NAMES) as c:
            b = jd._discover_fingerprint()
        self.assertEqual(c.n, 1, "a moved mtime forces exactly one re-read")
        self.assertNotEqual(a, b, "...and the new launch dir reaches the fingerprint")

    def test_a_new_fork_still_moves_the_fingerprint_on_a_warm_memo(self):
        """The memo may cache the RESOLVED project dir, but it must re-stat it every call: a fork landing
        in that dir bumps the DIR mtime while the names/ entry never moves."""
        a = jd._discover_fingerprint()
        self._write_transcript(FORK, title=NAME)
        self._touch(self.proj)
        b = jd._discover_fingerprint()
        self.assertNotEqual(a, b, "a fork's dir-mtime bump is read LIVE, never served from the memo")

    def test_a_new_entry_appears_through_a_warm_memo(self):
        a = jd._discover_fingerprint()
        (jd.NAMES / OTHER).write_text("%s\t%s" % ("TESTHOST-two", self.cdir))
        b = jd._discover_fingerprint()
        self.assertNotEqual(a, b)
        self.assertIn(OTHER, [row[0] for row in b])

    def test_a_woken_dormant_session_re_enters_discover(self):
        """A session whose transcript aged out of WINDOW and is then touched again must reappear.

        Nothing structural changes when it wakes — no names/ entry moves, no dir entry is added — so the
        fingerprint held and discover() kept serving the cached list that had already dropped the row.
        Every surface keyed on it (a comment thread's session lookup among them) then denied the session
        existed while its transcript sat right there, appended seconds earlier."""
        anchor = self.proj / (SID + ".jsonl")
        dormant = time.time() - (jd.WINDOW + 3600)
        os.utime(anchor, (dormant, dormant))
        now = int(time.time())
        self.assertEqual(jd.discover(now), [], "the aged-out session is out of the window")
        cold = jd._discover_fingerprint()
        os.utime(anchor, None)                                  # the wake: an append, and nothing else
        self.assertNotEqual(jd._discover_fingerprint(), cold,
                            "crossing back into the window moves the fingerprint")
        self.assertEqual([row[0] for row in jd.discover(now)], [SID],
                         "...so discover() re-walks and the session is listed again")

    def test_a_woken_RELOCATED_session_re_enters_discover(self):
        """A session whose CLI recorded a transcript outside the launch dir is read from that record, so
        its window crossing is signed from the recorded file, not from the launch dir's (absent) one."""
        moved = Path(self._td) / "projects" / "relocated"
        moved.mkdir(parents=True, exist_ok=True)
        recorded = moved / (SID + ".jsonl")
        (self.proj / (SID + ".jsonl")).unlink()
        recorded.write_text(json.dumps({"type": "user", "uuid": "u1"}) + "\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"transcriptPath": str(recorded)}))
        dormant = time.time() - (jd.WINDOW + 3600)
        os.utime(recorded, (dormant, dormant))
        now = int(time.time())
        self.assertEqual(jd.discover(now), [], "the aged-out recorded transcript is out of the window")
        os.utime(recorded, None)
        self.assertEqual([row[0] for row in jd.discover(now)], [SID],
                         "the recorded file's wake moves the fingerprint, so discover() re-walks")

    def _age_out_and_wake(self, current, *aged):
        """Age `current` and every `aged` file out of WINDOW, check discover() drops the session, then
        touch only `current` and return what discover() lists."""
        dormant = time.time() - (jd.WINDOW + 3600)
        for path in (current,) + aged:
            os.utime(path, (dormant, dormant))
        now = int(time.time())
        self.assertEqual(jd.discover(now), [], "every transcript is out of the window")
        os.utime(current, None)
        return [row[0] for row in jd.discover(now)]

    def test_a_woken_CLEARED_session_re_enters_discover(self):
        """After a /clear the session reads its lastSid file, so that file's wake is what gets signed."""
        self._write_transcript(OTHER)
        cleared = self.proj / (OTHER + ".jsonl")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"lastSid": OTHER}))
        self.assertEqual(self._age_out_and_wake(cleared, self.proj / (SID + ".jsonl")), [SID])

    def test_a_woken_RELOCATED_and_CLEARED_session_re_enters_discover(self):
        """A relocated session after a /clear reads the lastSid sibling of its recorded file."""
        moved = Path(self._td) / "projects" / "relocated"
        moved.mkdir(parents=True, exist_ok=True)
        recorded, sibling = moved / (SID + ".jsonl"), moved / (OTHER + ".jsonl")
        for path in (recorded, sibling):
            path.write_text(json.dumps({"type": "user", "uuid": "u1"}) + "\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"transcriptPath": str(recorded), "lastSid": OTHER}))
        self.assertEqual(self._age_out_and_wake(sibling, recorded, self.proj / (SID + ".jsonl")), [SID])

    def test_a_woken_session_with_a_STALE_record_re_enters_discover(self):
        """A record naming a file that no longer exists falls back to the launch dir's file, as discover() does."""
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        gone = Path(self._td) / "projects" / "gone" / (SID + ".jsonl")
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"transcriptPath": str(gone)}))
        self.assertEqual(self._age_out_and_wake(self.proj / (SID + ".jsonl")), [SID])

    def test_a_NUL_in_a_recorded_value_signs_as_absent_and_the_session_stays_listed(self):
        """Only a corrupt or hand-edited registry carries a NUL; discover must still list the session."""
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        for record in ({"lastSid": OTHER + "\x00"},
                       {"transcriptPath": str(Path(self._td) / "pro\x00jects" / (SID + ".jsonl"))}):
            with self.subTest(record=record):
                (jd.SDKDIR / (SID + ".json")).write_text(json.dumps(record))
                jd._namefp_memo.clear()
                self.assertIn(SID, [row[0] for row in jd.discover(int(time.time()))])

    def test_an_append_inside_the_window_still_serves_the_cache(self):
        """The counterpart: a live session's append must NOT invalidate, or the cache buys nothing."""
        now = int(time.time())
        listed = jd.discover(now)
        os.utime(self.proj / (SID + ".jsonl"), None)
        self.assertIs(jd.discover(now), listed, "an append inside the window is still a cache hit")

    def test_discover_still_serves_its_cached_list(self):
        """End to end: the memo sits UNDER discover()'s cache, so an unchanged namespace still short-circuits."""
        now = int(time.time())
        a = jd.discover(now)
        self.assertIs(jd.discover(now), a)
        with NamesReadCounter(jd.NAMES) as c:
            jd.discover(now)
        self.assertEqual(c.n, 0, "a warm discover() reads no names/ entry at all")


if __name__ == "__main__":
    unittest.main()
