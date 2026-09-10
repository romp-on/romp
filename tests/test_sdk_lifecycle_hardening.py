#!/usr/bin/env python3
"""SDK-session lifecycle hardening (2026-07-05: a kernel death stranded every SDK session in
"purgatory" — cut turns never resumed, in-memory queues silently dropped, one orphaned CLI).

Covers the backend half:
  * queue persistence — SdkSession._pending mirrors to the registry on every mutation and is
    re-seeded from it, so a kernel death can DELAY queued turns but never lose them;
  * last_state_value — the cut-turn discriminator reads the last STATE record through the
    interleaved awaiting overlays (the boot heal itself appends one);
  * find_orphan_clis — matches only ORPHANED SDK-driven CLIs, i.e. whose parent is no live romp
    kernel (ppid 1 on macOS; the `systemd --user` subreaper on Linux, 2026-09-05) (--resume <ours> +
    stream-json), never a tmux session's interactive `claude --resume` and never a LIVE CLI still
    parented to a kernel (2026-07-06: a duplicate backend's reconcile reaped live sessions);
  * _boot_reconcile — resumes exactly the cut-turn / queued sessions (a user-interrupted or
    cleanly-finished session stays lazy), prepends the visible continuation nudge, reaps orphans;
  * drain — the SIGTERM path stops every running session, counts in-flight turns, and writes NO
    idle/waiting state (the trailing 'working' IS the next boot's resume marker).

All deterministic: no SDK import, no real claude processes (ps/os.kill are patched) — except PsArgv's
two real-ps tests, Linux-only, which run the machine's ps against a sleeper child they spawn and kill.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import signal
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))


def _backend(d=None):
    return sb.SdkBackend(d or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)


def _pid_max() -> int:
    """Fake pids above this can never be a live process on the box (T276c) — the reaper's liveness poll and
    os.getpgid then answer deterministically for them on every runner."""
    try:
        return int(open("/proc/sys/kernel/pid_max").read().strip())
    except (OSError, ValueError):
        return 4194304


_P = _pid_max()


def _reg(d, sid, **extra):
    r = {"sid": sid, "name": "s-" + sid[:4], "cwd": "/tmp", "alive": True, "lastSid": sid}
    r.update(extra)
    sb.write_reg(Path(d), sid, r)
    return r


class LastStateValue(unittest.TestCase):
    def test_reads_through_awaiting_overlays(self):
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-555555555555"
        sb.append_state(Path(d), sid, "working")
        sb.append_awaiting(Path(d), sid, False)      # the boot heal appends exactly this overlay
        self.assertEqual(sb.last_state_value(Path(d), sid), "working",
                         "an overlay after the state record must not hide the cut-turn marker")
        # last_state (the literal last line) would have returned the overlay — that's the trap
        self.assertNotIn("state", sb.last_state(Path(d), sid))

    def test_empty_and_missing(self):
        d = tempfile.mkdtemp()
        self.assertEqual(sb.last_state_value(Path(d), "nope"), "")


class FindOrphanClis(unittest.TestCase):
    SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    OWN = 31337   # this kernel's pid in the fixtures; no fixture below uses it as a parent

    def test_matches_only_sdk_clis_resuming_ours(self):
        lines = [
            # ours, SDK-driven (stream-json), re-parented to launchd → a true orphan, matched
            " 4242 1 /x/claude --output-format stream-json --resume %s --input-format stream-json" % self.SID,
            # a TMUX session's interactive resume (no stream-json mark) → never touched
            " 4243 1 claude --resume %s --name termsess" % self.SID,
            # SDK-driven but a sid we don't own → not ours to reap
            " 4244 1 /x/claude --resume ffffffff-0000-1111-2222-333333333333 --input-format stream-json",
            # junk / short lines are skipped, not crashed on
            "garbage", " 99", " 99 100", "",
        ]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [4242])

    def test_live_children_are_never_orphans(self):
        # Same command line as a true orphan, but still parented to a running kernel (the kernel's
        # own line is in the listing): a LIVE session's CLI. Reaping these was the 2026-07-06 kill
        # storm — a duplicate backend's reconcile SIGTERM'd freshly-resumed sessions mid-turn (exit 143).
        lines = [
            " 38438 901 /usr/bin/python3.12 /x/romp/bin/romp-kernel",
            " 4242 38438 /x/claude --output-format stream-json --resume %s --input-format stream-json" % self.SID,
            " 4245 1 /x/claude --output-format stream-json --resume %s --input-format stream-json" % self.SID,
        ]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [4245])

    # Orphaned = the parent is not a live romp kernel (2026-09-05). Until then the check was ppid 1,
    # which is what an orphan gets under launchd — and never under `systemd --user`, whose
    # PR_SET_CHILD_SUBREAPER re-parents an orphan to the user manager's pid, so the reap had matched
    # nothing on Linux under the service.
    def _cli(self, pid, ppid):
        return " %d %d /x/claude --output-format stream-json --resume=%s --input-format stream-json" % (pid, ppid, self.SID)

    def test_orphan_reparented_to_launchd(self):
        # macOS: pid 1 is launchd, present in the listing and not a kernel
        lines = [" 1 0 /sbin/launchd", self._cli(700, 1)]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [700])

    def test_orphan_reparented_to_the_systemd_user_manager(self):
        # Linux under the service: the orphan's ppid is the `systemd --user` pid, never 1
        lines = [" 1 0 /sbin/init", " 901 1 /usr/lib/systemd/systemd --user", self._cli(701, 901)]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [701])

    def test_orphan_whose_parent_is_absent_from_the_listing(self):
        # the parent died between the CLI's line and its own (ps is not atomic) — an orphan
        lines = [self._cli(702, 65000)]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [702])

    def test_a_live_cli_parented_to_a_romp_kernel_is_never_reaped(self):
        for kernel in (" 500 901 /usr/bin/python3.12 /x/romp/bin/romp-kernel",
                       " 500 901 python3 bin/romp-kernel",
                       " 500 901 python3 kernel/kernel.py",
                       " 500 901 /usr/bin/python3 /x/romp/kernel/kernel.py"):
            lines = [" 901 1 /usr/lib/systemd/systemd --user", kernel, self._cli(703, 500)]
            self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [], kernel)

    def test_a_cli_parented_to_a_different_live_kernel_is_that_kernels(self):
        # another kernel (an aux port, a second install) owns this CLI — not ours to reap, whatever
        # sid it carries; the orphan next to it, parented to the user manager, still is
        lines = [" 901 1 /usr/lib/systemd/systemd --user",
                 " 600 901 /usr/bin/python3.12 /elsewhere/romp/bin/romp-kernel",
                 self._cli(704, 600), self._cli(705, 901)]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [705])

    # This kernel's own children are live by PID, not by how `ps` spells the kernel (the #941 review).
    # _is_kernel_cmd knows the spellings romp-serve and a hand run produce; under any other (a `-c`
    # runner, a renamed launcher, `python3 ./kernel.py`) it read the caller's own children as orphans —
    # the unconditional protection the old `ppid != 1` test gave them, lost. A foreign kernel is still
    # judged by its text: the pid shortcut is for the caller only.
    def test_this_kernels_own_children_are_live_whatever_ps_calls_it(self):
        for kernel in ("python3 ./kernel.py",
                       "/usr/bin/python3 -c import runpy; runpy.run_path('kernel/kernel.py')",
                       "/x/venv/bin/python /x/romp/bin/romp-kernel-dev"):
            self.assertFalse(sb._is_kernel_cmd(kernel), kernel)     # so the pid path is what protects them
            lines = [" 901 1 /usr/lib/systemd/systemd --user", " %d 901 %s" % (self.OWN, kernel),
                     self._cli(707, self.OWN), self._cli(708, 901)]
            self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [708], kernel)

    def test_own_children_stay_live_when_the_kernels_own_line_is_absent(self):
        # unlike test_orphan_whose_parent_is_absent_from_the_listing (ppid 65000 is not us, so that one
        # IS an orphan): a listing that missed our own line still protects our children, by pid
        self.assertEqual(sb.find_orphan_clis([self._cli(709, self.OWN)], [self.SID], self.OWN), [])

    def test_another_kernels_children_are_still_judged_by_its_text(self):
        # the boundary #941 drew, unchanged: a kernel spelled in a way _is_kernel_cmd does not know
        # shields nothing unless it is THIS kernel
        lines = [" 901 1 /usr/lib/systemd/systemd --user", " 600 901 python3 ./kernel.py", self._cli(710, 600)]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [710])

    def test_kernel_match_is_on_argv_tokens_not_substrings(self):
        self.assertTrue(sb._is_kernel_cmd("/usr/bin/python3.12 /x/romp/bin/romp-kernel"))
        self.assertTrue(sb._is_kernel_cmd("python3 kernel/kernel.py"))
        self.assertTrue(sb._is_kernel_cmd("python3 kernel.py"))
        # a process merely mentioning the kernel is not one: its child would be an orphan
        self.assertFalse(sb._is_kernel_cmd("tail -f /x/state/romp/romp-kernel.log"))
        self.assertFalse(sb._is_kernel_cmd("node /x/romp/bin/romp-manager up"))
        self.assertFalse(sb._is_kernel_cmd("/usr/lib/systemd/systemd --user"))
        self.assertFalse(sb._is_kernel_cmd("python3 other/kernel.py"))
        lines = [" 800 1 tail -f /x/state/romp/romp-kernel.log", self._cli(706, 800)]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [706])

    def test_empty_sids_match_nothing(self):
        lines = [" 1 1 claude --resume  --input-format stream-json"]
        self.assertEqual(sb.find_orphan_clis(lines, [""], self.OWN), [])

    def test_equals_flag_spelling_matches(self):
        # The Agent SDK moved to `--resume=<sid>` (equals form); the space-only match was blind to
        # it, so every boot reconcile "reaped 0" while a real orphan kept working the repo for over
        # an hour (2026-07-25, the twin incident). Both spellings, and --session-id for a CLI that
        # was spawned fresh and never resumed, must match.
        lines = [
            " 5001 1 /x/claude --output-format stream-json --resume=%s --input-format stream-json" % self.SID,
            " 5002 1 /x/claude --output-format stream-json --session-id=%s --input-format stream-json" % self.SID,
            " 5003 1 /x/claude --output-format stream-json --session-id %s --input-format stream-json" % self.SID,
            # equals form but a foreign sid → still not ours
            " 5004 1 /x/claude --resume=ffffffff-0000-1111-2222-333333333333 --input-format stream-json",
        ]
        self.assertEqual(sb.find_orphan_clis(lines, [self.SID], self.OWN), [5001, 5002, 5003])


class LeaseRules(unittest.TestCase):
    """Ownership by LEASE (T305, stage 1 of sessions surviving a kernel restart): a CLI with a valid lease —
    fresh heartbeat, holder alive by pid and start time, CLI alive by pid and start time — is owned whatever
    its parent; a CLI with no valid lease and no live kernel parent is an orphan. Every anomaly is a problem
    row. Pure: lease_census takes the ps listing, the leases and a start-time reader, so no process, pid or
    second is real here. Synthetic ids throughout."""
    SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"     # the conversation id (lastSid) the CLI's argv carries
    RSID = "11111111-2222-3333-4444-555555555555"    # the romp sid the lease is filed under
    OWN = 31337                                      # this kernel's pid in the fixtures
    HOLDER = 777                                     # the lease holder's pid in the fixtures
    NOW = 1_800_000_000.0

    def _cli(self, pid, ppid, sid=None):
        return " %d %d /x/claude --output-format stream-json --resume=%s --input-format stream-json" % (pid, ppid, sid or self.SID)

    def _lease(self, pid, start="1000", holder=None, t=None, version="abc12345", sid=None):
        return {"sid": sid or self.RSID, "fsid": self.SID, "pid": pid, "start": start,
                "holder": holder if holder is not None else {"pid": self.HOLDER, "start": "50"},
                "version": version, "t": self.NOW if t is None else t}

    def _census(self, lines, leases, starts, version=""):
        return sb.lease_census(lines, [self.SID], self.OWN, leases, now=self.NOW, start=lambda p: starts.get(p), version=version)

    def test_a_reparented_cli_with_a_valid_lease_is_owned_whatever_its_parent(self):
        # launchd and the `systemd --user` manager: the two orphan shapes the parentage rule reaped, each owned here
        for parent in (" 1 0 /sbin/launchd", " 901 1 /usr/lib/systemd/systemd --user"):
            ppid = int(parent.split()[0])
            c = self._census([parent, self._cli(700, ppid)], [self._lease(700)], {700: "1000", self.HOLDER: "50"})
            self.assertEqual((c["orphans"], c["owned"], c["problems"], c["dead_leases"]), ([], {700: "lease"}, [], []), parent)
        # find_orphan_clis, handed the leases, gives the census's verdict
        self.assertEqual(sb.find_orphan_clis([" 1 0 /sbin/launchd", self._cli(700, 1)], [self.SID], self.OWN,
                                             [self._lease(700)], now=self.NOW, start={700: "1000", self.HOLDER: "50"}.get), [])
        # …and without them, the parentage rule alone, unchanged
        self.assertEqual(sb.find_orphan_clis([" 1 0 /sbin/launchd", self._cli(700, 1)], [self.SID], self.OWN), [700])

    def test_a_holder_that_is_gone_makes_the_cli_an_orphan_with_a_row(self):
        # the holder's pid has no start time (gone) — a crashed kernel's lease: reaped as before leases
        c = self._census([self._cli(701, 1)], [self._lease(701)], {701: "1000"})
        self.assertEqual(c["orphans"], [701])
        self.assertEqual([(p["kind"], p["sid"], p["cliPid"]) for p in c["problems"]], [("lease.holder-gone", self.RSID, 701)])
        # a holder pid worn by ANOTHER process now (start time differs) is gone too: pid alone is never identity
        c = self._census([self._cli(701, 1)], [self._lease(701)], {701: "1000", self.HOLDER: "51"})
        self.assertEqual((c["orphans"], [p["kind"] for p in c["problems"]]), ([701], ["lease.holder-gone"]))

    def test_a_stale_heartbeat_makes_the_cli_an_orphan_and_the_boundary_is_the_ttl(self):
        starts = {702: "1000", self.HOLDER: "50"}
        c = self._census([self._cli(702, 1)], [self._lease(702, t=self.NOW - sb.LEASE_TTL_S - 0.5)], starts)
        self.assertEqual((c["orphans"], [p["kind"] for p in c["problems"]]), ([702], ["lease.stale-heartbeat"]))
        c = self._census([self._cli(702, 1)], [self._lease(702, t=self.NOW - sb.LEASE_TTL_S)], starts)
        self.assertEqual((c["orphans"], c["owned"]), ([], {702: "lease"}), "a beat exactly TTL old still holds")

    def test_a_lease_whose_pid_now_names_another_process_is_no_live_process(self):
        # the lease's CLI died and its pid was reused — by an SDK CLI of ours here, the hardest case: the new
        # process has no valid lease (identity differs), so it is judged on its own and the row says why
        c = self._census([self._cli(703, 1)], [self._lease(703, start="1000")], {703: "2000", self.HOLDER: "50"})
        self.assertEqual((c["orphans"], [p["kind"] for p in c["problems"]]), ([703], ["lease.no-live-process"]))
        # …and by an unrelated process: no CLI to reap, the lease is dead and listed for removal
        c = self._census([" 1 0 /sbin/launchd", " 704 1 sleep 300"], [self._lease(704)], {704: "1000", self.HOLDER: "50"})
        self.assertEqual((c["orphans"], c["dead_leases"], [p["kind"] for p in c["problems"]]),
                         ([], [self.RSID], ["lease.no-live-process"]))

    def test_this_kernels_own_child_without_a_lease_is_owned_and_not_a_row(self):
        # the window between this kernel's spawn and its connect-time lease write is by design
        c = self._census([self._cli(705, self.OWN)], [], {})
        self.assertEqual((c["orphans"], c["owned"], c["problems"]), ([], {705: "own-child"}, []))

    def test_a_live_kernels_child_without_a_lease_is_kept_and_reported(self):
        # the previous code version's kernel wrote no leases: its sessions survive the upgrade boot, with a row
        lines = [" 901 1 /usr/lib/systemd/systemd --user", " 600 901 /usr/bin/python3.12 /x/romp/bin/romp-kernel", self._cli(706, 600)]
        c = self._census(lines, [], {})
        self.assertEqual((c["orphans"], c["owned"]), ([], {706: "kernel-child"}))
        self.assertEqual([(p["kind"], p["cliPid"], p["fsid"]) for p in c["problems"]], [("lease.cli-without-lease", 706, self.SID)])

    def test_no_lease_and_no_kernel_parent_is_todays_plain_orphan(self):
        c = self._census([" 901 1 /usr/lib/systemd/systemd --user", self._cli(707, 901)], [], {})
        self.assertEqual((c["orphans"], c["owned"], c["problems"]), ([707], {}, []))

    def test_a_lease_from_another_code_version_is_owned_and_reported(self):
        starts = {708: "1000", self.HOLDER: "50"}
        c = self._census([self._cli(708, 1)], [self._lease(708, version="old00000")], starts, version="new00000")
        self.assertEqual((c["orphans"], c["owned"], [p["kind"] for p in c["problems"]]), ([], {708: "lease"}, ["lease.version-skew"]))
        c = self._census([self._cli(708, 1)], [self._lease(708, version="new00000")], starts, version="new00000")
        self.assertEqual(c["problems"], [])
        c = self._census([self._cli(708, 1)], [self._lease(708, version="old00000")], starts)   # no version to compare: no row
        self.assertEqual(c["problems"], [])

    def test_two_clis_on_one_conversation_are_each_judged_alone_with_no_row_of_their_own(self):
        # the boot sweep files that event itself (reconcile.duplicate-cli, duplicate_clis); the census judges each
        c = self._census([self._cli(709, 1), self._cli(710, 1)], [self._lease(709)], {709: "1000", self.HOLDER: "50"})
        self.assertEqual((c["owned"], c["orphans"], c["problems"]), ({709: "lease"}, [710], []))
        self.assertEqual(sb.duplicate_clis([self._cli(709, 1), self._cli(710, 1)], [self.SID]), {self.SID: [709, 710]})

    def test_lease_state_names_the_first_failing_check(self):
        st = lambda starts: (lambda p: starts.get(p))
        L = self._lease(1)
        self.assertEqual(sb.lease_state(L, self.NOW, st({1: "1000", self.HOLDER: "50"})), "valid")
        self.assertEqual(sb.lease_state(L, self.NOW, st({self.HOLDER: "50"})), "no-live-process")
        self.assertEqual(sb.lease_state(L, self.NOW, st({1: "1000"})), "holder-gone")
        self.assertEqual(sb.lease_state(dict(L, t=self.NOW - sb.LEASE_TTL_S - 1), self.NOW, st({1: "1000", self.HOLDER: "50"})), "stale-heartbeat")
        self.assertEqual(sb.lease_state({"sid": "x"}, self.NOW, st({})), "no-live-process", "a missing field fails its check")
        self.assertEqual(sb.lease_state(dict(L, holder="junk"), self.NOW, st({1: "1000"})), "holder-gone")

    def test_find_session_cli_reaches_the_leased_cli_first_then_the_child_scan(self):
        lines = [" 1 0 /sbin/launchd", self._cli(711, 1), self._cli(712, self.OWN)]
        lease = self._lease(711)
        self.assertEqual(sb.find_session_cli(lines, [self.SID], self.OWN, lease=lease, start={711: "1000"}.get), 711,
                         "the re-parented leased CLI is the escalation's target")
        self.assertEqual(sb.find_session_cli(lines, [self.SID], self.OWN, lease=lease, start={711: "9999"}.get), 712,
                         "the lease's pid now names another process: the child scan stands")
        self.assertEqual(sb.find_session_cli(lines, [self.SID], self.OWN), 712)
        # a lease naming a pid that is not an SDK CLI of ours never wins
        self.assertEqual(sb.find_session_cli([" 713 1 sleep 300", self._cli(712, self.OWN)], [self.SID], self.OWN,
                                             lease=self._lease(713), start={713: "1000"}.get), 712)

    def test_lease_files_round_trip_list_and_skip_junk(self):
        d = tempfile.mkdtemp()
        self.assertEqual(sb.list_leases(d), [])
        sb.write_lease(d, self._lease(5))
        self.assertEqual(sb.read_lease(d, self.RSID)["pid"], 5)
        self.assertEqual([l["sid"] for l in sb.list_leases(d)], [self.RSID])
        (Path(d) / sb.LEASE_DIR / "junk.json").write_text("{not json")
        (Path(d) / sb.LEASE_DIR / "x.json.1.abcd.tmp").write_text("{}")
        self.assertEqual(len(sb.list_leases(d)), 1, "a corrupt lease and a temp file are nobody's claim")
        self.assertTrue(sb.remove_lease(d, self.RSID))
        self.assertFalse(sb.remove_lease(d, self.RSID))
        self.assertIsNone(sb.read_lease(d, self.RSID))

    def test_proc_start_names_a_live_process_and_not_a_fake_pid(self):
        me = sb.proc_start(os.getpid())
        self.assertTrue(me)
        self.assertEqual(sb.proc_start(os.getpid()), me, "a stable identity")
        self.assertIsNone(sb.proc_start(_P + 424242), "a pid above pid_max is nobody")
        # the no-procfs path (macOS): `ps -o lstart=` through the run seam
        ran = []
        def run(argv, **kw):
            ran.append(argv); return types.SimpleNamespace(stdout="Thu Sep 10 17:22:37 2026\n")
        with mock.patch.object(sb.os.path, "isdir", lambda p: False if p == "/proc" else os.path.isdir(p)):
            self.assertEqual(sb.proc_start(4242, run=run), "Thu Sep 10 17:22:37 2026")
            self.assertEqual(ran, [["ps", "-o", "lstart=", "-p", "4242"]])
            self.assertIsNone(sb.proc_start(4243, run=lambda *a, **k: types.SimpleNamespace(stdout="")))

    def test_problem_row_is_the_log_line_the_ring_prose_and_the_ledger_row(self):
        d = tempfile.mkdtemp(); logs = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=logs.append)
        del logs[:]                                     # the construction's own setup lines are not the subject
        base = len(be.problems())                       # …nor its own problem (the SDK is not importable here)
        prose = "CLI pid 5 of session 11111111 has a lease that does not hold (holder gone); reaped as an orphan"
        line = sb.problem_row(d, prose, "lease.holder-gone", sid=self.RSID, log=be._log, cliPid=5, t=1700000000, fsid=None)
        row = sb.parse_problem_row(line)
        self.assertEqual((row["kind"], row["t"], row["sid"], row["cliPid"], row["pid"], row["text"]),
                         ("lease.holder-gone", 1700000000, self.RSID, 5, os.getpid(), prose))
        self.assertNotIn("fsid", row, "a None field is dropped")
        self.assertTrue(line.startswith(prose + sb.PROBLEM_ROW_MARK), line)
        self.assertEqual(logs, [line], "the kernel log gets the whole line")
        self.assertEqual([r["text"] for r in be.problems()[base:]], [prose], "the ring gets the prose alone")
        ledger = (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines()
        self.assertEqual(json.loads(ledger[0]), row, "the ledger row is the same object")
        self.assertIsNone(sb.parse_problem_row("a plain line"))
        self.assertIsNone(sb.parse_problem_row("x" + sb.PROBLEM_ROW_MARK + "{not json"))
        # a summary (ring=False): the ledger and the log, no ring entry
        sb.problem_row(d, "boot summary", "reconcile.boot", log=be._log, ring=False)
        self.assertEqual((len(be.problems()) - base, len(logs), len((Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines())), (1, 2, 2))
        # an unwritable ledger never raises
        self.assertTrue(sb.problem_row(os.path.join(d, "no", "such", "dir"), "p", "k.x"))

    def test_the_backend_writes_beats_and_drops_the_lease_of_a_connected_session(self):
        d = tempfile.mkdtemp(); logs = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=logs.append, code_version="abc12345")
        del logs[:]                                     # the construction's own setup lines are not the subject
        base = len(be.problems())
        sess = types.SimpleNamespace(sid=self.RSID, name="web", resume_sid=self.SID)
        client = types.SimpleNamespace(_transport=types.SimpleNamespace(_process=types.SimpleNamespace(pid=os.getpid())))
        with mock.patch.object(sb.SdkBackend, "_lease_beat_loop", lambda self: None):   # the thread exits at once here
            be._lease_open(sess, client)
        lease = sb.read_lease(d, self.RSID)
        self.assertEqual((lease["pid"], lease["fsid"], lease["version"], lease["holder"]["pid"], lease["name"]),
                         (os.getpid(), self.SID, "abc12345", os.getpid(), "web"))
        self.assertEqual((lease["start"], lease["holder"]["start"]), (sb.proc_start(os.getpid()),) * 2)
        self.assertEqual(sb.lease_state(lease, time.time()), "valid")
        self.assertEqual(sb.lease_census([" %d %d /x/claude --resume=%s --input-format stream-json" % (os.getpid(), 1, self.SID)],
                                         [self.SID], self.OWN, [lease])["owned"], {os.getpid(): "lease"})
        # a beat refreshes the heartbeat and follows a conversation flip (a /clear, a fork)
        sess.resume_sid = "99999999-8888-7777-6666-555555555555"
        self.assertEqual(be._lease_beat_once(now=lease["t"] + 5), 1)
        again = sb.read_lease(d, self.RSID)
        self.assertEqual((again["t"], again["fsid"]), (lease["t"] + 5, sess.resume_sid))
        be._lease_close(sess)
        self.assertIsNone(sb.read_lease(d, self.RSID))
        self.assertEqual(be._lease_beat_once(), 0)
        be._lease_close(sess)                           # idempotent
        self.assertEqual(logs, [])
        # a transport with no pid: loud, and the session runs unleased
        be._lease_open(sess, types.SimpleNamespace())
        self.assertIsNone(sb.read_lease(d, self.RSID))
        self.assertEqual(len(be.problems()) - base, 1)
        self.assertIn("exposes no CLI pid", be.problems()[-1]["text"])

    def test_two_sessions_opening_at_once_start_one_heartbeat_thread_and_neither_crashes(self):
        # the T305 review: the beat thread used to be assigned under the lock and STARTED outside it; a second
        # opener in the first's write window read the unstarted thread as not alive, replaced it, and both then
        # started one Thread (RuntimeError out of the connect). Two opens held inside the write window at once.
        d = tempfile.mkdtemp(); be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        gate, started, errors = threading.Event(), [], []
        def loop(self_):
            started.append(threading.current_thread().name); gate.wait(10)
        barrier = threading.Barrier(2, timeout=10)
        real_write = sb.write_lease
        def write_inside_the_window(state_dir, lease):
            barrier.wait()                                  # both opens are between the lock and the write
            real_write(state_dir, lease)
        client = types.SimpleNamespace(_transport=types.SimpleNamespace(_process=types.SimpleNamespace(pid=os.getpid())))
        def open_(sid):
            try:
                be._lease_open(types.SimpleNamespace(sid=sid, name=sid[-2:], resume_sid=None), client)
            except Exception as e:
                errors.append(e)
        sids = ("11111111-2222-3333-4444-0000000000a1", "11111111-2222-3333-4444-0000000000a2")
        with mock.patch.object(sb.SdkBackend, "_lease_beat_loop", loop), mock.patch.object(sb, "write_lease", write_inside_the_window):
            ts = [threading.Thread(target=open_, args=(s,)) for s in sids]
            for t in ts: t.start()
            for t in ts: t.join(15)
        gate.set()
        self.assertEqual(errors, [], "no open may raise")
        self.assertEqual(len(started), 1, "one heartbeat thread per backend, started once")
        self.assertEqual(sorted(l["sid"] for l in sb.list_leases(d)), sorted(sids))

    def test_a_close_during_a_beat_leaves_no_lease_file_behind(self):
        # the T305 review: a beat snapshot is taken under the lock but each write ran without it, so a beat could
        # rewrite a lease that _lease_close had just popped and unlinked, and the next boot filed a false
        # no-live-process row for a session that ended cleanly. The close lands mid-beat, from another thread.
        d = tempfile.mkdtemp(); be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        client = types.SimpleNamespace(_transport=types.SimpleNamespace(_process=types.SimpleNamespace(pid=os.getpid())))
        a = types.SimpleNamespace(sid="11111111-2222-3333-4444-0000000000b1", name="a", resume_sid=None)
        b = types.SimpleNamespace(sid="11111111-2222-3333-4444-0000000000b2", name="b", resume_sid=None)
        with mock.patch.object(sb.SdkBackend, "_lease_beat_loop", lambda self: None):
            be._lease_open(a, client); be._lease_open(b, client)
        real_write = sb.write_lease; closer = []
        def write_then_close_b(state_dir, lease):
            real_write(state_dir, lease)
            if lease["sid"] == a.sid:                       # b ends cleanly while the beat is between a and b
                t = threading.Thread(target=be._lease_close, args=(b,)); t.start(); closer.append(t)
                time.sleep(0.2)
        with mock.patch.object(sb, "write_lease", write_then_close_b):
            be._lease_beat_once()
        for t in closer: t.join(5)
        self.assertIsNone(sb.read_lease(d, b.sid), "the beat must not rewrite a lease the close removed")
        self.assertIsNotNone(sb.read_lease(d, a.sid))
        self.assertEqual(be._lease_beat_once(), 1)

    def test_source_pins_the_connect_writes_the_close_drops_and_the_cadence_is_the_drain_holds(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
        self.assertIn("self.backend._lease_open(self, client)", src)
        self.assertIn("self.backend._lease_close(self)", src)
        self.assertIn("self._lease_close(s)", src, "the drain's reap drops the lease it ends")
        self.assertIn("code_version=_kernel_sha()", open(os.path.join(BIN, "romp-kernel")).read())
        self.assertEqual(sb.LEASE_TTL_S, sb.SdkBackend.DRAIN_HOLD_TTL, "the drain hold's TTL, exactly")
        self.assertEqual(sb.LEASE_TTL_S, 4 * sb.LEASE_HEARTBEAT_S, "four beats: outlives a missed beat, not a dead holder")


class QueuePersistence(unittest.TestCase):
    def test_enqueue_and_unqueue_mirror_to_registry(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-2222-3333-4444-666666666666"
        reg = _reg(d, sid)
        s = sb.SdkSession(be, reg)                   # never started: pure kernel-thread surface
        s.enqueue("first")
        s.enqueue("second")
        self.assertEqual(sb.read_reg(Path(d), sid).get("queue"), ["first", "second"])
        self.assertEqual(s.unqueue(0), "first")
        self.assertEqual(sb.read_reg(Path(d), sid).get("queue"), ["second"],
                         "a canceled turn leaves the persisted queue too")

    def test_init_seeds_pending_from_persisted_queue(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-2222-3333-4444-777777777777"
        reg = _reg(d, sid, queue=["held over", "", 42, "and this"])
        s = sb.SdkSession(be, reg)
        self.assertEqual(s.pending(), ["held over", "and this"],
                         "restores strings only — junk entries never wedge delivery")


class TodoIdsRideTheQueue(unittest.TestCase):
    """A queued message may ANSWER a user todo (SdkBackend.send's user_todo): the id travels WITH the
    message — on the in-memory entry (_TodoText), through the reg mirror and back through the boot
    seed — so whoever removes or loses the entry later reads the id off the entry itself, with no
    kernel-side table to lose across a restart. The mirror keeps reg['queue'] as bare strings and
    carries the id BESIDE the copy's identity, in reg['queueMeta'] (the T252c sidecar: one entry per
    position, text alone for an id-less copy), so every reader of the queue's texts is untouched and
    the mirror is byte-identical to the pre-todo shape for every other send; the sidecar's block
    alignment (queue_meta_from_reg) carries the id through every text-only rewrite of reg['queue']."""

    ANSWER = "Re: need the staging port — 8443."

    def _session(self, queue=None, queue_meta=None):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-2222-3333-4444-888888888888"
        extra = {}
        if queue is not None:
            extra["queue"] = queue
        if queue_meta is not None:
            extra["queueMeta"] = queue_meta
        reg = _reg(d, sid, **extra)
        return d, be, sid, sb.SdkSession(be, reg)

    def test_an_answer_entry_mirrors_its_id_beside_the_copy_and_the_queue_stays_bare(self):
        d, be, sid, s = self._session()
        s.enqueue("plain message")
        s.enqueue(self.ANSWER, todo="ut-9f2c1a34")
        reg = sb.read_reg(Path(d), sid)
        self.assertEqual(reg.get("queue"), ["plain message", self.ANSWER], "bare strings, both")
        self.assertEqual(reg.get("queueMeta"),
                         [{"text": "plain message"}, {"text": self.ANSWER, "todo": "ut-9f2c1a34"}])

    def test_a_send_minted_identity_and_the_id_share_one_sidecar_entry(self):
        d, be, sid, s = self._session()
        s.enqueue(self.ANSWER, qid="echo:a1", qts=1234, todo="ut-9f2c1a34")
        self.assertEqual(sb.read_reg(Path(d), sid).get("queueMeta"),
                         [{"text": self.ANSWER, "qid": "echo:a1", "qts": 1234, "todo": "ut-9f2c1a34"}])
        s2 = sb.SdkSession(be, sb.read_reg(Path(d), sid))    # "kernel restart"
        self.assertEqual([getattr(t, "todo", "") for t in s2.pending()], ["ut-9f2c1a34"])
        self.assertEqual([m["qid"] for m in s2.pending_meta()], ["echo:a1"], "the copy's identity too")

    def test_a_plain_queue_serializes_exactly_as_before(self):
        # byte-stability: with no answer queued, the mirror is the pre-todo shape — bare strings and the
        # T252c sidecar without a "todo" key anywhere
        d, be, sid, s = self._session()
        s.enqueue("first")
        s.enqueue("second")
        reg = sb.read_reg(Path(d), sid)
        self.assertEqual(json.dumps(reg.get("queue"), sort_keys=True),
                         json.dumps(["first", "second"], sort_keys=True))
        self.assertEqual(reg.get("queueMeta"), [{"text": "first"}, {"text": "second"}])

    def test_the_seed_restores_the_id_onto_the_entry(self):
        d, be, sid, s = self._session(queue=["held over", self.ANSWER, "", 42],
                                      queue_meta=[{"text": "held over"},
                                                  {"text": self.ANSWER, "todo": "ut-11112222"}])
        self.assertEqual(s.pending(), ["held over", self.ANSWER],
                         "the texts seed as before; junk is filtered exactly as before")
        self.assertEqual([getattr(t, "todo", "") for t in s.pending()], ["", "ut-11112222"])

    def test_an_older_mirror_without_the_sidecar_seeds_id_less_copies(self):
        d, be, sid, s = self._session(queue=["held over", self.ANSWER])
        self.assertEqual([getattr(t, "todo", "") for t in s.pending()], ["", ""])

    def test_unqueue_returns_the_id_bearing_text_and_cleans_the_mirror(self):
        d, be, sid, s = self._session()
        s.enqueue(self.ANSWER, todo="ut-9f2c1a34")
        got = s.unqueue(0)
        self.assertEqual(got, self.ANSWER, "the text contract is unchanged")
        self.assertEqual(getattr(got, "todo", ""), "ut-9f2c1a34",
                         "the id rides the returned entry — a recall's caller reads it here")
        reg = sb.read_reg(Path(d), sid)
        self.assertEqual(reg.get("queue"), [])
        self.assertEqual(reg.get("queueMeta"), [])

    def test_replace_queued_keeps_the_id_on_the_new_words(self):
        # an in-place edit of a queued answer (T306) swaps the words, not the ask they answer: the id
        # stays on the entry and in the mirror, and a plain entry's edit stays a bare string
        d, be, sid, s = self._session()
        s.enqueue("plain message")
        s.enqueue(self.ANSWER, todo="ut-9f2c1a34")
        self.assertEqual(s.replace_queued(1, "Re: the same ask, fuller answer", expect=self.ANSWER), self.ANSWER)
        self.assertEqual([getattr(t, "todo", "") for t in s.pending()], ["", "ut-9f2c1a34"])
        self.assertEqual(s.replace_queued(0, "plain, edited", expect="plain message"), "plain message")
        self.assertEqual(type(s.pending()[0]), str, "a plain entry's edit stays a bare str")
        reg = sb.read_reg(Path(d), sid)
        self.assertEqual(reg.get("queue"), ["plain, edited", "Re: the same ask, fuller answer"])
        self.assertEqual(reg.get("queueMeta"),
                         [{"text": "plain, edited"}, {"text": "Re: the same ask, fuller answer", "todo": "ut-9f2c1a34"}])

    def test_backend_unqueue_hands_the_id_through(self):
        d, be, sid, s = self._session()
        with be._lock:
            be.sessions[sid] = s
        s.enqueue(self.ANSWER, todo="ut-9f2c1a34")
        got = be.unqueue(sid, 0)
        self.assertEqual(got, self.ANSWER)
        self.assertEqual(getattr(got, "todo", ""), "ut-9f2c1a34")

    def _restored_todos(self, d, sid):
        return [getattr(t, "todo", "") for t in sb.SdkSession(_backend(d), sb.read_reg(Path(d), sid)).pending()]

    def test_boot_prepend_keeps_the_id_on_its_entry(self):
        # the cut-turn nudge prepend rewrites reg['queue'] by text — the sidecar's block alignment must
        # still put the id back on the answer, now one position down
        d = tempfile.mkdtemp()
        be = _backend(d)
        be._ensure = lambda sid, on_boot_settled=None: on_boot_settled and on_boot_settled()
        cut = "11111111-aaaa-0000-0000-0000000000f0"
        _reg(d, cut, queue=[self.ANSWER, "plain backlog"],
             queueMeta=[{"text": self.ANSWER, "todo": "ut-33334444"}, {"text": "plain backlog"}])
        sb.append_state(Path(d), cut, "working")
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), cut)])
        self.assertEqual(sb.read_reg(Path(d), cut).get("queue"),
                         [sb.BOOT_RESUME_NUDGE, self.ANSWER, "plain backlog"])
        self.assertEqual(self._restored_todos(d, cut), ["", "ut-33334444", ""])

    def test_crash_heal_prepend_keeps_the_id_on_its_entry(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        be._ensure = lambda sid, on_boot_settled=None: None
        sid = "11111111-aaaa-0000-0000-0000000000f1"
        _reg(d, sid, queue=[self.ANSWER], queueMeta=[{"text": self.ANSWER, "todo": "ut-55556666"}])
        s = sb.SdkSession(be, sb.read_reg(Path(d), sid))
        be._heal_cut_session(s)
        self.assertEqual(sb.read_reg(Path(d), sid).get("queue"), [sb.CRASH_RESUME_NUDGE, self.ANSWER])
        self.assertEqual(self._restored_todos(d, sid), ["", "ut-55556666"])

    def test_thread_wake_notice_keeps_the_id_on_its_entry(self):
        # a dormant comment thread woken with a killed question: _ensure rewrites reg['queue'] to put
        # the notice first — the id must still land on the answer, in the reg the SdkSession seed reads
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-0000000000f2"
        owner = "11111111-aaaa-0000-0000-0000000000f3"
        _reg(d, sid, threadOf=owner, pendingAsk=True, queue=[self.ANSWER, "plain reply"],
             queueMeta=[{"text": self.ANSWER, "todo": "ut-99990000"}, {"text": "plain reply"}])
        seeded = []

        class _Fake:
            def __init__(self, backend, reg):
                seeded.append(reg)
                self.thread = types.SimpleNamespace(is_alive=lambda: True)

            def start(self):
                pass

        with mock.patch.object(sb, "SdkSession", _Fake):
            be._ensure(sid)
        self.assertEqual(sb.read_reg(Path(d), sid).get("queue"), [sb.ASK_DIED_NOTICE, self.ANSWER, "plain reply"])
        self.assertEqual([getattr(t, "todo", "") for t in sb.SdkSession(be, seeded[0]).pending()],
                         ["", "ut-99990000", ""], "the seed reads THIS dict")

    def test_reconcile_strand_rehead_keeps_the_id(self):
        # the fed-turn twin (_inflight_texts) re-heads the queue when no conversation ever
        # materialized — the restored entry must still carry its id into the mirror
        d, be, sid, s = self._session()
        s.resume_sid = None                          # no init ever streamed: the re-head arm
        s.enqueue(self.ANSWER, todo="ut-77778888")
        with s._lock:
            fed = s._pop_for_feed_locked()[0]        # the input generator feeds the entry…
        s.inflight = 1
        s._inflight_texts.append(fed)                # …and its twin carries it, id and all
        s._reconcile_stranded()
        self.assertEqual([getattr(t, "todo", "") for t in s.pending()], ["ut-77778888"])
        self.assertEqual(sb.read_reg(Path(d), sid).get("queueMeta"),
                         [{"text": self.ANSWER, "todo": "ut-77778888"}])


def _procps() -> bool:
    """Whether this box's ps is procps (Linux; BSD ps has no --version). The truncation control below
    pins procps behaviour, so it runs only there."""
    try:
        return "procps" in subprocess.run(["ps", "--version"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False


class PsArgv(unittest.TestCase):
    """The `ps` both process scans run. `-ww` is the point: procps truncates every line to $COLUMNS
    when that variable is exported (BSD ps does by default), and an SDK CLI's sid sits ~2 KB into its
    argv behind --append-system-prompt, so a kernel started with COLUMNS exported reaped nothing and
    could not find its own child to signal, silently (2026-09-05; `COLUMNS=80 ps -axo` cut a 3200-char
    argv at 80 columns on procps-ng 4.0.4, `-axwwo` printed it whole). The first three tests pin the
    argv and its two call sites through mocks; the last two run this box's ps against a real long argv,
    on Linux only, so the width property itself has an executable check."""

    def test_the_argv_asks_for_unlimited_width(self):
        self.assertEqual(sb.PS_ARGV, ["ps", "-axwwo", "pid=,ppid=,command="])

    # GNU sleep rejects a non-numeric argument, so the sleeper with the >3000-character argv is this
    # interpreter, given the marker as an argument it ignores; it is killed on the way out. The marker is
    # minted per test so nothing else on the box (this process's own argv included) can carry it.
    def _sleeper_with_a_long_argv(self):
        marker = "romp-ps-ww-tail-" + uuid.uuid4().hex
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "x" * 3000 + marker],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.wait, timeout=10)
        self.addCleanup(child.kill)
        return child, marker

    def _ps_line_for(self, pid, argv):
        out = subprocess.run(argv, env={**os.environ, "COLUMNS": "80"}, capture_output=True, text=True,
                             timeout=10).stdout
        mine = [ln for ln in out.splitlines() if ln.split()[:1] == [str(pid)]]
        self.assertEqual(len(mine), 1, "one line for the sleeper's pid: %r" % (mine,))
        return mine[0]

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("ps"), "a real ps on Linux")
    def test_a_real_ps_under_columns_80_prints_a_3000_character_argv_whole(self):
        child, marker = self._sleeper_with_a_long_argv()
        line = self._ps_line_for(child.pid, sb.PS_ARGV)
        self.assertIn(marker, line, "the argv's tail survived COLUMNS=80 (line is %d chars)" % len(line))
        self.assertGreater(len(line), 3000)

    @unittest.skipUnless(sys.platform.startswith("linux") and _procps(), "procps ps on Linux")
    def test_without_ww_the_same_ps_cuts_the_argv_at_columns(self):
        # the control: the argv PS_ARGV replaced (-axo) loses the marker on procps, so the test above
        # passes because of -ww and not because this box's ps never truncates
        child, marker = self._sleeper_with_a_long_argv()
        line = self._ps_line_for(child.pid, ["ps", "-axo", "pid=,ppid=,command="])
        self.assertNotIn(marker, line)
        self.assertLessEqual(len(line), 80)

    def test_the_interrupt_escalation_reads_ps_with_it(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-00000000000f"
        _reg(d, sid)
        # our own child (ppid = this process), its sid 2 KB into the argv — what -ww keeps intact
        ps = "  4242 %d /x/claude --append-system-prompt %s --resume %s --input-format stream-json\n" % (
            os.getpid(), "p" * 2100, sid)
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout=ps)) as run:
            pid = be._session_cli_pid(types.SimpleNamespace(sid=sid, name="web"))
        self.assertEqual(pid, 4242)
        self.assertEqual(run.call_args_list[0][0][0], sb.PS_ARGV)

    def test_the_boot_reaper_reads_ps_with_it(self):
        # the reaper's own reap test (BootReconcile.test_reaps_orphans_but_never_tmux) pins the same
        # argv on its call; this one pins that the two sites share ONE constant, so neither can drift
        with open(sb.__file__) as f:
            src = f.read()
        self.assertNotIn('"-axo"', src, "every ps scan goes through PS_ARGV (-ww)")
        self.assertEqual(src.count("subprocess.run(PS_ARGV"), 2, "the reaper and the escalation")


class BootReconcile(unittest.TestCase):
    def _setup(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        be._ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (be._ensured.append(sid), on_boot_settled and on_boot_settled())
        return d, be

    def test_resumes_exactly_cut_and_queued_sessions(self):
        d, be = self._setup()
        cut = "11111111-aaaa-0000-0000-000000000001"       # tail 'working' → cut by the kernel death
        queued = "11111111-aaaa-0000-0000-000000000002"    # finished, but has a persisted queue
        interrupted = "11111111-aaaa-0000-0000-000000000003"  # user interrupt wrote 'idle'
        finished = "11111111-aaaa-0000-0000-000000000004"  # clean turn end wrote 'waiting'
        dead = "11111111-aaaa-0000-0000-000000000005"
        regs = [_reg(d, cut), _reg(d, queued, queue=["waiting msg"]),
                _reg(d, interrupted), _reg(d, finished), _reg(d, dead, alive=False)]
        sb.append_state(Path(d), cut, "working")
        sb.append_state(Path(d), queued, "waiting")
        sb.append_state(Path(d), interrupted, "working")
        sb.append_state(Path(d), interrupted, "idle")      # the user-interrupt marker
        sb.append_state(Path(d), finished, "waiting")
        sb.append_state(Path(d), dead, "working")          # dead: even a 'working' tail stays dead
        with mock.patch.object(sb.subprocess, "run",
                               return_value=mock.Mock(stdout="")):
            be._boot_reconcile(regs)
        self.assertEqual(sorted(be._ensured), sorted([cut, queued]),
                         "user-interrupted / finished / dead sessions stay lazy")

    def test_cut_turn_gets_the_nudge_prepended_before_its_queue(self):
        d, be = self._setup()
        cut = "11111111-aaaa-0000-0000-00000000000a"
        _reg(d, cut, queue=["sent during the outage"])
        sb.append_state(Path(d), cut, "working")
        sb.append_awaiting(Path(d), cut, False)            # the boot heal's overlay must not mask the cut
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), cut)])
        q = sb.read_reg(Path(d), cut).get("queue")
        self.assertEqual(q, [sb.BOOT_RESUME_NUDGE, "sent during the outage"],
                         "the visible continuation nudge is fed FIRST, then the restored backlog")
        self.assertEqual(be._ensured, [cut])

    def test_dead_bg_tasks_wake_the_session_with_a_named_notice(self):
        # bg tasks die with their CLI (the user 2026-07-11: nimbus's campaign watcher died with a
        # kernel restart and the session waited forever on a notification that could never arrive).
        # The reg's bgTasks mirror survives the death; the reconcile must tell the session what it
        # lost — by DESCRIPTION — and clear the mirror so the same deaths never re-notify.
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000c"
        _reg(d, sid, bgTasks=[{"desc": "20-minute timer for campaign-start check", "type": "local_bash",
                               "since": 1, "toolUseId": "tu1", "lastTool": ""}])
        sb.append_state(Path(d), sid, "waiting")           # idle — NOT cut; the notice alone wakes it
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        self.assertEqual(be._ensured, [sid], "an idle session with dead tasks is woken to hear it")
        reg = sb.read_reg(Path(d), sid)
        self.assertEqual(len(reg["queue"]), 1)
        self.assertIn("20-minute timer for campaign-start check", reg["queue"][0])
        self.assertIn("romp-system", reg["queue"][0], "a visible romp system notice, not silent")
        self.assertEqual(reg["bgTasks"], [], "reported — the same deaths never re-notify")

    def test_cut_turn_with_dead_tasks_orders_resume_nudge_then_notice(self):
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000d"
        _reg(d, sid, queue=["backlog msg"], bgTasks=[{"desc": "power watcher", "since": 1}])
        sb.append_state(Path(d), sid, "working")           # cut by the kernel death
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        q = sb.read_reg(Path(d), sid)["queue"]
        self.assertEqual(q[0], sb.BOOT_RESUME_NUDGE, "continuation context first")
        self.assertIn("power watcher", q[1])
        self.assertEqual(q[2], "backlog msg", "the restored backlog follows the notices")

    def test_stranded_pending_switch_flags_heal_at_boot_without_waking_the_session(self):
        # a /model or /effort switch mid-flight at the kernel's death strands its pending flags; the
        # dormant serving path shows them as switching-dots FOREVER (the user 2026-07-11, who reported the three
        # dots sitting there forever). The boot sweep heals the flags; an otherwise-idle session
        # stays lazy (no wake just for the heal).
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000e"
        _reg(d, sid, effortPending=True, modelPending=True)
        sb.append_state(Path(d), sid, "waiting")
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        reg = sb.read_reg(Path(d), sid)
        self.assertFalse(reg.get("effortPending"))
        self.assertFalse(reg.get("modelPending"))
        self.assertEqual(be._ensured, [], "the heal alone never wakes a session")

    def test_the_sweep_reads_each_registry_fresh_not_the_listing_it_was_handed(self):
        # __init__ lists the registries, then the echo reseed may re-queue a lost send into one of
        # them ON DISK, then the sweep walks that same listing: a row listed before the write still
        # showed an empty queue, so the session sat dormant with a message waiting in its registry.
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000f"
        regs = [_reg(d, sid, queue=[])]                    # the listing: nothing queued yet
        sb.write_reg(Path(d), sid, {**regs[0], "queue": ["re-queued after the listing"]})   # the reseed's write
        sb.append_state(Path(d), sid, "waiting")
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile(regs)
        self.assertEqual(be._ensured, [sid], "the sweep decides from the registry on disk, not the stale row")

    def test_reaps_orphans_but_never_tmux(self):
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000b"
        _reg(d, sid)
        sb.append_state(Path(d), sid, "working")
        # the orphan's fake pid sits above pid_max (T276): the tree kill polls procfs after its SIGTERM, and a live
        # process wearing a small fake pid on the box would earn a SIGKILL the pin below does not expect
        ps = ("  9999555 1 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              "  556 1 claude --resume %s --name termsess\n"
              "  90210 1 /usr/bin/python3 /x/romp/bin/romp-kernel\n"
              "  557 90210 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              ) % (sid, sid, sid)
        killed = []
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout=ps)) as run, \
             mock.patch.object(sb.os, "kill", side_effect=lambda p, s: killed.append((p, s))):
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        self.assertEqual(killed, [(9999555, sb.signal.SIGTERM)],
                         "the SDK orphan is reaped; the tmux CLI and the live (parented) CLI "
                         "on the same sid are untouched")
        self.assertEqual(run.call_args_list[0][0][0], sb.PS_ARGV, "the listing is read with PS_ARGV")

    def test_the_reaper_shields_this_kernels_own_children_by_pid(self):
        # The reconcile runs on a thread after the backend is up, concurrent with the kernel serving
        # requests, so a session started before the thread's `ps` is THIS kernel's child carrying one
        # of the lastsids. With the kernel spelled in a way _is_kernel_cmd does not know it was reaped
        # as an orphan (the #941 review); the caller hands its own pid down, and the child is live by it.
        d, be = self._setup()
        sid = "11111111-aaaa-0000-0000-00000000000d"
        _reg(d, sid)
        sb.append_state(Path(d), sid, "working")
        me = os.getpid()
        # fake pids above pid_max (T276c): a live runner process wearing a small fake pid made the tree kill
        # take a REAL process group (os.getpgid succeeded, killpg went unrecorded — an empty list on one
        # interpreter) or escalate to SIGKILL (the liveness poll saw it alive — an extra signal on another)
        MANAGER, OURS, ORPHAN = _P + 901, _P + 558, _P + 559
        ps = ("  %d 1 /usr/lib/systemd/systemd --user\n"
              "  %d %d python3 ./kernel.py\n"
              "  %d %d /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              "  %d %d /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              ) % (MANAGER, me, MANAGER, OURS, me, sid, ORPHAN, MANAGER, sid)
        killed = []
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout=ps)), \
             mock.patch.object(sb.os, "kill", side_effect=lambda p, s: killed.append((p, s))), \
             mock.patch.object(sb.os, "killpg", side_effect=lambda g, s: killed.append(("pg", g, s))), \
             mock.patch.object(sb.SdkBackend, "_pid_alive", lambda self, p: False):   # nothing is alive after its SIGTERM: no grace, no SIGKILL
            be._boot_reconcile([sb.read_reg(Path(d), sid)])
        self.assertEqual(killed, [(ORPHAN, sb.signal.SIGTERM)],
                         "a child this kernel already spawned is left alone whatever ps calls the kernel; "
                         "the orphan under the user manager is reaped with exactly one SIGTERM — no group kill, no escalation")

    def test_reconcile_is_opt_in(self):
        # Constructing the backend plain (tests, ad-hoc) must NOT spawn a reconcile thread; the
        # kernel opts in with reconcile=True. Pinned by patching the method and constructing both ways.
        d = tempfile.mkdtemp()
        sid = "11111111-aaaa-0000-0000-00000000000c"
        _reg(d, sid)
        sb.append_state(Path(d), sid, "working")
        with mock.patch.object(sb.SdkBackend, "_boot_reconcile") as br:
            sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
            self.assertEqual(br.call_count, 0)
            sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, reconcile=True)
            deadline = time.time() + 5
            while br.call_count == 0 and time.time() < deadline:
                time.sleep(0.01)                     # the reconcile runs on its own thread
            self.assertEqual(br.call_count, 1)


class WriteRegConcurrency(unittest.TestCase):
    def test_temp_names_are_writer_unique(self):
        """During a kernel restart the OUTGOING kernel and the incoming boot reconcile write the
        SAME sid's registry concurrently; a shared '<sid>.tmp' let one os.replace steal the other's
        temp mid-write (FileNotFoundError, live 2026-07-06). Pin: concurrent writers never collide
        and the final registry is one of the written values, with no stray temps left behind."""
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-888888888888"
        errs = []

        def hammer(tag):
            try:
                for i in range(50):
                    sb.write_reg(Path(d), sid, {"sid": sid, "writer": tag, "i": i})
            except Exception as e:
                errs.append(e)

        ts = [threading.Thread(target=hammer, args=(t,)) for t in ("a", "b", "c")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [], "no writer may crash on another's temp file")
        self.assertIn(sb.read_reg(Path(d), sid).get("writer"), ("a", "b", "c"))
        strays = [f for f in os.listdir(os.path.join(d, "sdk")) if f.endswith(".tmp")]
        self.assertEqual(strays, [], "failed/completed writes leave no temp litter")


class BootReconcileResilience(unittest.TestCase):
    def test_one_bad_session_does_not_strand_the_rest(self):
        """Live 2026-07-06: a write_reg race on the FIRST session aborted two whole reconcile
        passes, stranding every later session. One session's failure must log and continue."""
        d = tempfile.mkdtemp()
        be = _backend(d)
        be._ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (be._ensured.append(sid), on_boot_settled and on_boot_settled())
        logs = []
        be._log_cb = logs.append
        bad = "11111111-aaaa-0000-0000-0000000000e1"
        good = "11111111-aaaa-0000-0000-0000000000e2"
        regs = [_reg(d, bad), _reg(d, good)]
        for s in (bad, good):
            sb.append_state(Path(d), s, "working")
        real_write = sb.write_reg

        def exploding_write(state_dir, sid, reg):
            if sid == bad:
                raise FileNotFoundError("simulated temp-steal race")
            real_write(state_dir, sid, reg)

        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")), \
             mock.patch.object(sb, "write_reg", side_effect=exploding_write):
            be._boot_reconcile(regs)
        self.assertEqual(be._ensured, [good], "the sweep continued past the failing session")
        self.assertTrue(any("sweep continues" in m for m in logs), "the failure is loud, not silent")


class CrashHeal(unittest.TestCase):
    """_on_session_gone on an ABNORMAL mid-turn death (CLI killed/crashed; not user-interrupted,
    not our shutdown) must NOT settle 'waiting' — that masked the cut and stranded the session
    until the next kernel restart (2026-07-06: reaped sessions wrote triple 'waiting' and stalled).
    Instead it keeps the trailing 'working' and resumes ONCE via _heal_cut_session; the budget
    re-arms only when a turn completes."""

    SID = "11111111-2222-3333-4444-777777777777"

    def _dead_session(self, be, d, inflight=1, interrupted=False):
        reg = sb.read_reg(Path(d), self.SID) or _reg(d, self.SID)   # keep a prior heal's queue intact
        s = sb.SdkSession(be, reg)          # never started: pure object surface
        sb.append_state(Path(d), self.SID, "working")
        s.inflight = inflight
        s._interrupted = interrupted
        return s

    def test_midturn_death_keeps_cut_marker_and_resumes(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        s = self._dead_session(be, d)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(s)
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "working",
                         "no 'waiting' settle — the trailing 'working' IS the cut marker")
        q = sb.read_reg(Path(d), self.SID).get("queue")
        self.assertEqual(q, [sb.CRASH_RESUME_NUDGE],
                         "the visible crash nudge is queued so the resume is never silent")
        ens.assert_called_once_with(self.SID)

    def test_second_death_without_completed_turn_is_a_crash_loop(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        logs = []
        be._log_cb = logs.append
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(self._dead_session(be, d))
            be._on_session_gone(self._dead_session(be, d))   # died again before any ResultMessage
        self.assertEqual(ens.call_count, 1, "one resume per cut — no respawn loop")
        self.assertEqual(sb.read_reg(Path(d), self.SID).get("queue"), [sb.CRASH_RESUME_NUDGE],
                         "the nudge is not stacked by the refused second heal")
        self.assertTrue(any("crash loop" in m for m in logs), "the give-up is loud")
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "working",
                         "still cut — the next kernel restart's reconcile picks it up")

    def test_completed_turn_rearms_the_heal_budget(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(self._dead_session(be, d))
            be._turn_completed(self.SID)                     # a ResultMessage landed in between
            be._on_session_gone(self._dead_session(be, d))
        self.assertEqual(ens.call_count, 2, "a completed turn re-arms one resume for the next cut")

    def test_user_interrupted_death_still_settles_waiting(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        s = self._dead_session(be, d, inflight=1, interrupted=True)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(s)
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "waiting",
                         "a user-interrupted turn's death is not a cut — settle as before")
        ens.assert_not_called()

    def test_idle_death_still_settles_waiting(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        s = self._dead_session(be, d, inflight=0)
        with mock.patch.object(be, "_ensure") as ens:
            be._on_session_gone(s)
        self.assertEqual(sb.last_state_value(Path(d), self.SID), "waiting")
        ens.assert_not_called()


class Drain(unittest.TestCase):
    def test_drain_stops_sessions_and_writes_no_state(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-00000000000d"
        reg = _reg(d, sid)
        sb.append_state(Path(d), sid, "working")     # an in-flight turn's stamp
        s = sb.SdkSession(be, reg)
        s.inflight = 1
        s.thread = threading.Thread(target=lambda: time.sleep(0.01), daemon=True)
        s.thread.start()
        be.sessions[sid] = s
        r = be.drain(1.0)
        self.assertEqual((r["stopped"], r["inflight"]), (1, 1))
        self.assertEqual(r["cutTurns"], [{"sid": sid, "name": reg.get("name", sid)}],
                         "the drain names what it cuts — the restart-cut ledger's rows (T121)")
        self.assertTrue(s.ended, "shutdown was requested on the session")
        self.assertEqual(sb.last_state_value(Path(d), sid), "working",
                         "drain writes no idle/waiting — the trailing 'working' IS the boot "
                         "reconcile's resume marker")

    def test_drain_counts_mid_shutdown_cuts_and_survives_threadless_sessions(self):
        # T143's two ledger undercounts, executed: an `ended` session with a live in-flight turn IS
        # a cut (10 transcript-verified cuts vs 7 rows — the old filter dropped mid-shutdown ones),
        # and a constructed-but-never-started session (thread None) crashed the WHOLE drain
        # recordless on 2 of 18 restarts.
        d = tempfile.mkdtemp()
        be = _backend(d)
        s1 = sb.SdkSession(be, _reg(d, "11111111-aaaa-0000-0000-0000000000c1"))
        s1.inflight = 1
        s1.ended = True                                   # mid-shutdown, turn still live
        s1.thread = threading.Thread(target=lambda: None, daemon=True)
        s1.thread.start()
        s2 = sb.SdkSession(be, _reg(d, "11111111-aaaa-0000-0000-0000000000c2"))
        s2.inflight = 1                                   # constructed, never started: thread is None
        s2.thread = None
        be.sessions[s1.sid] = s1
        be.sessions[s2.sid] = s2
        r = be.drain(0.2)
        cut_sids = sorted(c["sid"] for c in r["cutTurns"])
        self.assertEqual(cut_sids, [s1.sid, s2.sid],
                         "both cuts recorded — ended included, threadless included, no crash")

    def test_drain_with_nothing_running_is_a_quiet_noop(self):
        be = _backend()
        self.assertEqual(be.drain(0.1), {"stopped": 0, "inflight": 0, "unjoined": 0, "reaped": 0,
                                         "cutTurns": []})

    def test_drain_reaps_the_cli_of_a_session_that_wont_close(self):
        # The 2026-07-25 twin incident: the drain's bound expired on a busy session ("still
        # closing: ..."), the kernel exited, and the orphaned CLI kept executing its turn for over
        # an hour while the next boot resumed the same conversation into a second process. The
        # drain must never exit leaving a live child: SIGTERM the unjoined session's CLI (then
        # SIGKILL if it lingers).
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-00000000000e"
        s = sb.SdkSession(be, _reg(d, sid))
        s.inflight = 1
        s.thread = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        s.thread.start()                                  # outlives the drain bound → unjoined
        be.sessions[sid] = s
        be._session_cli_pid = lambda sess: 4242
        calls = []
        def fake_kill(pid, sig):
            calls.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError                  # the TERM landed; existence poll sees it gone
        r = be.drain(0.1, kill=fake_kill)
        self.assertEqual((r["unjoined"], r["reaped"]), (1, 1))
        self.assertIn((4242, signal.SIGTERM), calls)
        self.assertNotIn((4242, signal.SIGKILL), calls, "a TERM that lands never escalates")

    def test_drain_sigkills_a_cli_that_ignores_term(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-aaaa-0000-0000-00000000000f"
        s = sb.SdkSession(be, _reg(d, sid))
        s.thread = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        s.thread.start()
        be.sessions[sid] = s
        be._session_cli_pid = lambda sess: 4243
        calls = []
        def stubborn_kill(pid, sig):
            calls.append((pid, sig))                      # sig 0 never raises → still alive
        r = be.drain(0.1, kill=stubborn_kill)
        self.assertEqual(r["reaped"], 1)
        self.assertIn((4243, signal.SIGKILL), calls, "a wedged CLI still never outlives the kernel")


if __name__ == "__main__":
    unittest.main()
