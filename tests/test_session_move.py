#!/usr/bin/env python3
"""Moving a live session's working directory (the user 2026-09-01, who wanted a session to follow a
subproject promoted to its own repo): SdkBackend.move as a thin wrapper over the CLI's `set_cwd`
control request, driven here through a SCRIPTED fake control channel — every response arm the CLI
has (ok / needs_trust→ok / rejected busy / rejected not_found / a control error / a missing sender),
the two-phase `cwdPending` reg write, the names-file rewrite, the prior-episode transcript relocation,
the boot-reconcile heal, and the guard that keeps the move's turn-less ResultMessage from settling a
turn that never was. All fixtures SYNTHETIC: placeholder uuids, temp dirs, hostname TESTHOST."""
import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_move", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"        # the session's stable romp uuid (= its first fsid)
EPISODE_FSID = "22222222-3333-4444-5555-666666666666"   # an earlier /clear episode of the same session
FORK_FSID = "33333333-4444-5555-6666-777777777777"      # a resume-fork transcript of the same session
THREAD = "99999999-8888-7777-6666-555555555555"


def _ok(cwd, changed=True):
    return {"status": "ok", "cwd": cwd, "changed": changed, "transcript_relocated": True}


class _Query:
    """The SDK client's private control-request sender, scripted: each call records the request and
    pops the next arm — a dict is the CLI's response, an exception is a control error."""
    def __init__(self, arms):
        self.arms = list(arms)
        self.requests = []

    async def _send_control_request(self, req):
        self.requests.append(dict(req))
        arm = self.arms.pop(0)
        if isinstance(arm, BaseException):
            raise arm
        return arm


class _Client:
    def __init__(self, query):
        self._query = query


class MoveBase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.claude = tempfile.mkdtemp()
        os.environ["CLAUDE_CONFIG_DIR"] = self.claude   # transcript_path resolves through this
        self.logs = []
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.be._log = lambda m, problem=None: self.logs.append((m, bool(problem)))
        self.old = os.path.realpath(tempfile.mkdtemp())
        self.new = os.path.realpath(tempfile.mkdtemp())
        self.be.spawn("web", self.old, bg="#123456", fg="#ffffff", sid=SID)
        self.loops = []

    def tearDown(self):
        for loop in self.loops:
            loop.call_soon_threadsafe(loop.stop)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        for d in (self.td, self.claude, self.old, self.new):
            shutil.rmtree(d, ignore_errors=True)

    # -- fixtures --
    def _reg(self):
        return json.loads((Path(self.td) / "sdk" / (SID + ".json")).read_text())

    def _names(self):
        return (Path(self.td) / "names" / SID).read_text().rstrip("\n").split("\t")

    def _wire(self, arms):
        """A session the backend believes is running: a real SdkSession (never started), its loop a live
        asyncio loop on a helper thread (so _call_on_loop's run_coroutine_threadsafe works), its client
        the scripted fake, its thread reporting alive so _ensure hands it back instead of spawning."""
        s = sb.SdkSession(self.be, dict(sb.read_reg(self.be.state_dir, SID) or {}, sid=SID))
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        self.loops.append(loop)
        s.loop = loop
        s.client = _Client(_Query(arms))
        s._connected.set()
        s.thread = types.SimpleNamespace(is_alive=lambda: True)
        self.be.sessions[SID] = s
        return s

    def _place(self, cwd, fsid, sidecar=False, text="x"):
        p = Path(sb.transcript_path(cwd, fsid))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"type": "user", "uuid": "u1", "cwd": cwd, "text": text}) + "\n")
        if sidecar:
            (p.parent / fsid / "subagents").mkdir(parents=True, exist_ok=True)
            (p.parent / fsid / "subagents" / "agent-1.jsonl").write_text("{}\n")
        return p

    def _record_history(self):
        """The session's earlier transcripts as romp knows them: an episode row (a /clear) and a
        resume-fork row (a cut turn resumed onto a fresh head), each with files under the OLD slug."""
        (Path(self.td) / "episodes").mkdir(exist_ok=True)
        (Path(self.td) / "episodes" / (SID + ".jsonl")).write_text(
            json.dumps({"head": "h1", "fsid": EPISODE_FSID, "t": 1}) + "\n"
            + json.dumps({"head": "h2", "fsid": SID, "t": 2}) + "\n")
        (Path(self.td) / "states").mkdir(exist_ok=True)
        with (Path(self.td) / "states" / (SID + ".jsonl")).open("a") as fh:
            fh.write(json.dumps({"t": 3, "resumeFork": {"from": FORK_FSID, "to": SID}}) + "\n")
        self._place(self.old, EPISODE_FSID, sidecar=True)
        self._place(self.old, FORK_FSID)


class MoveOk(MoveBase):
    def test_ok_moves_every_record_romp_keeps(self):
        self._record_history()
        s = self._wire([_ok(self.new)])
        res = self.be.move(SID, self.new + "/")   # trailing slash: canonicalised before the request
        self.assertEqual(res, "")
        self.assertEqual(s.client._query.requests, [{"subtype": "set_cwd", "path": self.new}])
        reg = self._reg()
        self.assertEqual(reg["cwd"], self.new)
        self.assertEqual(reg["movedFrom"]["cwd"], self.old)
        self.assertIsInstance(reg["movedFrom"]["t"], int)
        self.assertNotIn("cwdPending", reg, "the two-phase flag is SPENT, not left as a null")
        self.assertEqual(self._names(), ["web", self.new, "#123456", "#ffffff"],
                         "the names file follows, name and colours preserved")
        self.assertEqual(s.cwd, self.new, "the next reconnect resumes where the transcript now is")
        self.assertTrue(s._move_settle_expected, "the turn-less result the CLI emits next is expected")
        # the CLI moved the current transcript; romp moved the session's OLDER files the same way
        for fsid in (EPISODE_FSID, FORK_FSID):
            self.assertFalse(os.path.exists(sb.transcript_path(self.old, fsid)), fsid + " left the old slug")
            self.assertTrue(os.path.exists(sb.transcript_path(self.new, fsid)), fsid + " is under the new slug")
        side = Path(sb.transcript_path(self.new, EPISODE_FSID)).parent / EPISODE_FSID / "subagents" / "agent-1.jsonl"
        self.assertTrue(side.exists(), "the <fsid>/ sidecar folder followed its transcript")

    def test_needs_trust_is_answered_with_the_users_own_pick(self):
        s = self._wire([{"status": "needs_trust", "directory": self.new}, _ok(self.new)])
        self.assertEqual(self.be.move(SID, self.new), "")
        reqs = s.client._query.requests
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[1]["subtype"], "set_cwd")
        self.assertIs(reqs[1]["trust_accepted"], True)
        self.assertEqual(reqs[1]["trusted_directory"], self.new)
        self.assertEqual(self._reg()["cwd"], self.new)

    def test_the_clis_reported_cwd_is_the_stored_string(self):
        # the CLI answers with its own getcwd (true on-disk casing); that string is what every transcript
        # path derives from, so it wins over the one romp sent
        s = self._wire([_ok(self.new)])
        self.be.move(SID, self.new)
        self.assertEqual(self._reg()["cwd"], self.new)
        self.assertEqual(s.cwd, self.new)

    def test_same_folder_is_a_quiet_no_op(self):
        # never a request and never the flag: a cwdPending EQUAL to cwd is a state the boot heal cannot
        # read (both slugs are one slug → "under BOTH", a problem filed on every boot forever)
        s = self._wire([_ok(self.old, changed=False)])
        claims = []
        real = self.be._claim_cwd_pending
        self.be._claim_cwd_pending = lambda *a: (claims.append(a), real(*a))[1]
        self.assertEqual(self.be.move(SID, self.old), "")
        self.assertEqual(s.client._query.requests, [], "nothing to ask the CLI")
        self.assertEqual(claims, [], "the two-phase flag is never written for a same-folder move")
        reg = self._reg()
        self.assertEqual(reg["cwd"], self.old)
        self.assertNotIn("movedFrom", reg)
        self.assertNotIn("cwdPending", reg)
        self.assertFalse(s._move_settle_expected, "no request → nothing armed")

    def test_the_clis_changed_false_disarms_and_drops_the_flag(self):
        # the CLI resolved the target to where the session already is (a symlink, a case difference the
        # canonicaliser missed): no relocation → no turn-less result, and nothing to record
        s = self._wire([_ok(self.old, changed=False)])
        self.assertEqual(self.be.move(SID, self.new), "")
        reg = self._reg()
        self.assertEqual(reg["cwd"], self.old)
        self.assertNotIn("movedFrom", reg)
        self.assertNotIn("cwdPending", reg)
        self.assertFalse(s._move_settle_expected, "changed:false emits no turn-less result — disarmed")

    def test_two_concurrent_moves_send_one_request(self):
        # the second asker (a dashboard and `romp move`, or a double click) must not race the first's
        # rename and reg write: the two-phase flag is CLAIMED under the reg lock, and a claimed flag
        # refuses the second call before any request goes out
        gate = threading.Event()

        class _Gated(_Query):
            async def _send_control_request(self, req):
                self.requests.append(dict(req))
                await asyncio.get_running_loop().run_in_executor(None, gate.wait)
                return _ok(req["path"])
        s = self._wire([])
        s.client = _Client(_Gated([]))
        out = {}
        ta = threading.Thread(target=lambda: out.__setitem__("a", self.be.move(SID, self.new)))
        ta.start()
        for _ in range(500):                         # until the first call has claimed the flag
            if self._reg().get("cwdPending"):
                break
            time.sleep(0.01)
        out["b"] = self.be.move(SID, self.new)       # the second asker, while the first is in flight
        gate.set()
        ta.join(10)
        self.assertEqual(out.get("a"), "")
        self.assertIn("already pending", out["b"])
        self.assertEqual(len(s.client._query.requests), 1, "one set_cwd, not two")
        self.assertEqual(self._reg()["cwd"], self.new)
        self.assertNotIn("cwdPending", self._reg())

    def test_dormant_session_is_revived_first_then_moved(self):
        reg = self._reg()
        reg["alive"] = False
        (Path(self.td) / "sdk" / (SID + ".json")).write_text(json.dumps(reg))
        self._wire([_ok(self.new)])   # _ensure hands this back (alive thread) once resume flipped the reg
        self.assertEqual(self.be.move(SID, self.new), "")
        self.assertTrue(self._reg()["alive"], "resume() ran before the move")
        self.assertEqual(self._reg()["cwd"], self.new)


class MoveRefusals(MoveBase):
    def _unchanged(self, s=None):
        reg = self._reg()
        self.assertEqual(reg["cwd"], self.old)
        self.assertNotIn("cwdPending", reg)
        self.assertNotIn("movedFrom", reg)
        self.assertEqual(self._names()[1], self.old)
        if s is not None:
            self.assertEqual(s.cwd, self.old)
            self.assertFalse(s._move_settle_expected)

    def test_rejected_busy_is_the_parking_signal(self):
        s = self._wire([{"status": "rejected", "reason": "busy"}])
        self.assertEqual(self.be.move(SID, self.new), "busy")
        self._unchanged(s)

    def test_a_turn_in_flight_is_busy_before_any_request(self):
        s = self._wire([_ok(self.new)])
        s.inflight = 1
        self.assertEqual(self.be.move(SID, self.new), "busy")
        self.assertEqual(s.client._query.requests, [])
        self._unchanged(s)

    def test_a_text_queued_after_the_idle_reading_is_busy_before_any_request(self):
        # the busy reading and the arm are one step under the session lock (the feeder pops under that
        # lock and holds the head while the arm stands), so a text queued between move()'s first idle
        # reading and the arm is caught by the locked re-read: no request goes out, nothing is armed, and
        # the claim is returned
        s = self._wire([_ok(self.new)])
        real = self.be._claim_cwd_pending

        def claim_then_a_send_lands(*a):
            out = real(*a)
            s.enqueue("sent while the claim was being written")
            return out
        self.be._claim_cwd_pending = claim_then_a_send_lands
        self.assertEqual(self.be.move(SID, self.new), "busy")
        self.assertEqual(s.client._query.requests, [], "no set_cwd went out with a text in the queue")
        self.assertEqual(s.pending(), ["sent while the claim was being written"], "the text is untouched")
        self._unchanged(s)

    def test_rejected_carries_the_clis_own_words(self):
        s = self._wire([{"status": "rejected", "reason": "not_found",
                         "message": "Couldn't find a directory at /srv/notes-api/web."}])
        self.assertEqual(self.be.move(SID, self.new), "Couldn't find a directory at /srv/notes-api/web.")
        self._unchanged(s)

    def test_a_control_error_leaves_the_session_where_it_was(self):
        # the CLI rolls its chdir back on a relocation failure — the transcript is still under the OLD
        # slug, which is how romp knows nothing moved; it drops the flag and says why
        s = self._wire([RuntimeError("relocation failed: EXDEV")])
        self._place(self.old, SID)
        res = self.be.move(SID, self.new)
        self.assertTrue(res.startswith("the move failed:"), res)
        self.assertIn("EXDEV", res)
        self.assertIn(self.old, res)
        self._unchanged(s)

    def test_a_missing_sender_is_named_not_worked_around(self):
        s = self._wire([])
        s.client = types.SimpleNamespace(_query=object())   # an SDK without the private sender
        res = self.be.move(SID, self.new)
        self.assertIn("_send_control_request", res)
        self.assertIn("set_cwd", res)
        self._unchanged(s)

    def test_a_bad_target_is_refused_before_any_request(self):
        s = self._wire([_ok(self.new)])
        f = os.path.join(self.new, "notes.txt")
        Path(f).write_text("not a folder")
        self.assertTrue(self.be.move(SID, f).startswith("not a directory: "))
        self.assertTrue(self.be.move(SID, os.path.join(self.new, "nope")).startswith("directory not found: "))
        self.assertEqual(self.be.move(SID, "relative/path"), "the folder must be an absolute path: relative/path")
        self.assertEqual(self.be.move(SID, ""), "no folder given")
        self.assertEqual(s.client._query.requests, [])
        self._unchanged(s)

    def test_a_comment_thread_keeps_its_own_folder(self):
        self.be.fork("thread-x", SID, "a1", sid=THREAD, thread_of=SID)
        res = self.be.move(THREAD, self.new)
        self.assertIn("comment thread", res)

    def test_an_unknown_sid_is_refused(self):
        self.assertIn("no record", self.be.move("44444444-5555-6666-7777-888888888888", self.new))

    def test_an_existing_target_file_is_never_overwritten(self):
        self._record_history()
        self._place(self.new, EPISODE_FSID, text="already here")   # a collision only a person should resolve
        self._wire([_ok(self.new)])
        self.assertEqual(self.be.move(SID, self.new), "")
        self.assertTrue(os.path.exists(sb.transcript_path(self.old, EPISODE_FSID)), "left where it was")
        self.assertIn("already here", Path(sb.transcript_path(self.new, EPISODE_FSID)).read_text())
        self.assertTrue(any("already exists" in m and p for m, p in self.logs), "logged as a problem")
        self.assertFalse(os.path.exists(sb.transcript_path(self.old, FORK_FSID)), "the others still moved")


class LostReply(MoveBase):
    """set_cwd's reply can be lost (a control error, a timeout) AFTER the CLI relocated — it moves first
    and replies after. The transcript's location decides, exactly as the boot heal decides a kernel death
    mid-move; dropping the flag blindly would leave romp deriving paths from the old folder."""

    def test_a_lost_reply_after_the_cli_moved_stands(self):
        self._record_history()
        s = self._wire([RuntimeError("control request timed out")])
        self._place(self.new, SID)                  # the CLI's half happened; only its reply was lost
        self.assertEqual(self.be.move(SID, self.new), "")
        reg = self._reg()
        self.assertEqual(reg["cwd"], self.new)
        self.assertEqual(reg["movedFrom"]["cwd"], self.old)
        self.assertNotIn("cwdPending", reg)
        self.assertEqual(self._names()[1], self.new)
        self.assertEqual(s.cwd, self.new)
        self.assertTrue(s._move_settle_expected, "the CLI's turn-less result is still coming — the arm stands")
        self.assertTrue(os.path.exists(sb.transcript_path(self.new, EPISODE_FSID)), "prior episodes follow")
        self.assertTrue(any("reply was lost" in m and p for m, p in self.logs),
                        "on the problem ring: the arm outlives move() and holds the queue")

    def test_a_lost_reply_with_the_transcript_nowhere_is_uncertain_and_keeps_the_flag(self):
        s = self._wire([RuntimeError("control request timed out")])
        res = self.be.move(SID, self.new)
        self.assertIn("uncertain", res)
        self.assertIn("timed out", res)
        reg = self._reg()
        self.assertEqual(reg["cwd"], self.old, "nothing changed")
        self.assertEqual(reg["cwdPending"], self.new, "the flag stays for the next boot's heal")
        self.assertNotIn("movedFrom", reg)
        self.assertFalse(s._move_settle_expected)
        self.assertTrue(any("NEITHER" in m and p for m, p in self.logs), "said loudly")
        # …and a second move is refused until it is settled, rather than racing a state nobody can read
        s2 = self._wire([_ok(self.new)])
        self.assertIn("already pending", self.be.move(SID, self.new))
        self.assertEqual(s2.client._query.requests, [])


class BootHeal(MoveBase):
    """A kernel that died between the CLI's `ok` and romp's reg write leaves cwdPending; the transcript's
    location settles it."""

    def test_a_pending_move_to_the_same_folder_is_simply_cleared(self):
        # a flag from before move() short-circuited the same-folder case: nothing moved, nothing to
        # settle, and the location test would otherwise read "under BOTH" on every boot forever
        reg = self._reg()
        reg["cwdPending"] = self.old
        reg["lastSid"] = SID
        (Path(self.td) / "sdk" / (SID + ".json")).write_text(json.dumps(reg))
        self._place(self.old, SID)
        self.be._heal_cwd_pending(reg)
        got = self._reg()
        self.assertNotIn("cwdPending", got)
        self.assertEqual(got["cwd"], self.old)
        self.assertFalse(any(p for m, p in self.logs), "no problem filed — there was nothing to settle")
        self.assertTrue(any("own folder" in m for m, p in self.logs))

    def _pending_reg(self):
        reg = self._reg()
        reg["cwdPending"] = self.new
        reg["lastSid"] = SID
        (Path(self.td) / "sdk" / (SID + ".json")).write_text(json.dumps(reg))
        return reg

    def test_transcript_under_the_new_slug_finishes_romps_half(self):
        self._record_history()
        reg = self._pending_reg()
        self._place(self.new, SID)          # the CLI's move happened
        self.be._boot_reconcile([reg])     # the wiring: the heal runs for EVERY reg, dormant included
        got = self._reg()
        self.assertEqual(got["cwd"], self.new)
        self.assertEqual(got["movedFrom"]["cwd"], self.old)
        self.assertNotIn("cwdPending", got)
        self.assertEqual(self._names()[1], self.new)
        self.assertTrue(os.path.exists(sb.transcript_path(self.new, EPISODE_FSID)), "prior episodes follow too")

    def test_transcript_still_under_the_old_slug_clears_the_flag(self):
        reg = self._pending_reg()
        self._place(self.old, SID)          # the move never happened
        self.be._heal_cwd_pending(reg)
        got = self._reg()
        self.assertEqual(got["cwd"], self.old)
        self.assertNotIn("cwdPending", got)
        self.assertNotIn("movedFrom", got)

    def test_both_or_neither_is_left_for_a_person_loudly(self):
        reg = self._pending_reg()
        self._place(self.old, SID)
        self._place(self.new, SID)
        self.be._heal_cwd_pending(reg)
        self.assertEqual(self._reg()["cwdPending"], self.new, "ambiguous → the flag stays")
        self.assertTrue(any("BOTH" in m and p for m, p in self.logs))
        os.remove(sb.transcript_path(self.old, SID))
        os.remove(sb.transcript_path(self.new, SID))
        self.be._heal_cwd_pending(reg)
        self.assertEqual(self._reg()["cwdPending"], self.new)
        self.assertTrue(any("NEITHER" in m and p for m, p in self.logs))


class Helpers(MoveBase):
    def test_known_fsids_reads_every_lineage_record(self):
        self._record_history()
        reg = self._reg()
        reg["lastSid"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        got = sb.known_fsids(Path(self.td), SID, reg)
        self.assertEqual(got, {SID, EPISODE_FSID, FORK_FSID, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})

    def test_canon_dir_realpaths_and_requires_an_existing_directory(self):
        link = os.path.join(self.td, "link")
        os.symlink(self.new, link)
        self.assertEqual(sb.canon_dir(link + "/"), (self.new, ""))
        self.assertEqual(sb.canon_dir("~")[0], os.path.realpath(os.path.expanduser("~")))
        self.assertEqual(sb.canon_dir("rel")[1], "the folder must be an absolute path: rel")
        self.assertEqual(sb.canon_dir("  ")[1], "no folder given")
        self.assertTrue(sb.canon_dir(os.path.join(self.new, "gone"))[1].startswith("directory not found"))

    def test_relocate_skips_absent_sources_and_a_same_slug_move(self):
        self.assertEqual(sb.relocate_transcripts(self.old, self.new, {SID}), [])
        self._place(self.old, SID)
        self.assertEqual(sb.relocate_transcripts(self.old, self.old + "/", {SID}), [], "same slug → nothing to do")
        self.assertTrue(os.path.exists(sb.transcript_path(self.old, SID)))


class FakeResultMessage:
    def __init__(self, num_turns):
        self.num_turns = num_turns
        self.result = ""


class FakeAssistantMessage:
    pass


class FakeSystemMessage:
    pass


class SpuriousSettle(unittest.TestCase):
    """The accepted move's turn-less ResultMessage(num_turns=0) must not run the turn-settle side effects."""

    def _session(self, be, armed):
        s = object.__new__(sb.SdkSession)
        s.backend = be
        s.sid = SID
        s.name = "web"
        s.resume_sid = None
        s._skill_tool_ids = set()
        s._move_settle_expected = armed
        s.marks = []
        s._mark = lambda st: s.marks.append(st)
        return s

    def test_armed_and_zero_turns_is_consumed_without_settling(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        completed = []
        be._turn_completed = lambda sid: completed.append(sid)
        s = self._session(be, armed=True)
        s._on_message(FakeResultMessage(0), FakeAssistantMessage, FakeResultMessage, FakeSystemMessage)
        self.assertEqual(completed, [], "no turn ended — nothing re-arms the crash-resume budget")
        self.assertEqual(s.marks, [], "no 'waiting' write for a turn that never was")
        self.assertFalse(s._move_settle_expected, "spent on the match")

    def test_the_guard_only_fires_on_an_armed_zero_turn_result(self):
        # A real turn's result while the arm stands proves the arm stale (the move's result would have
        # preceded it) and DROPS it, logged, so a zero-turn result after that goes to the ordinary settle.
        logs = []
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=lambda m: logs.append(str(m)))
        s = self._session(be, armed=False)
        self.assertFalse(s._consume_move_settle(FakeResultMessage(0)), "unarmed: a zero-turn result settles as before")
        s._move_settle_expected = True
        self.assertFalse(s._consume_move_settle(FakeResultMessage(1)), "a real turn reports its round trips")
        self.assertFalse(s._move_settle_expected, "and drops the arm: the move's own result would have come first")
        self.assertTrue(any("stale arm is dropped" in m for m in logs), logs)
        self.assertFalse(s._consume_move_settle(FakeResultMessage(0)),
                         "after the drop a zero-turn result is nobody's move: the ordinary settle takes it")
        s._move_settle_expected = True
        self.assertTrue(s._consume_move_settle(FakeResultMessage(0)), "armed and zero turns: the move's own result")
        self.assertFalse(s._move_settle_expected, "spent on the match")

    def test_the_guard_precedes_the_settle_branch(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py"), encoding="utf-8").read()
        i = src.index("def _on_message(self, msg, AssistantMessage, ResultMessage, SystemMessage):")
        g = src.index("elif isinstance(msg, ResultMessage) and self._consume_move_settle(msg):", i)
        b = src.index("elif isinstance(msg, ResultMessage):", i)
        self.assertLess(g, b, "the move guard is checked before the ordinary ResultMessage settle")
        # armed BEFORE the request goes out — the response and the result race on two threads
        m = src.index("def move(self, sid: str, new_cwd: str) -> str:")
        arm = src.index("s._move_settle_expected = True", m)
        req = src.index("r, err = self._set_cwd_request(s, target)", m)
        self.assertLess(arm, req)

    def test_the_private_sender_is_spoken_in_one_place(self):
        # the SDK has no public set_cwd wrapper yet; the private call is isolated so the swap is one edit
        src = open(os.path.join(BIN, "romp_sdk_backend.py"), encoding="utf-8").read()
        body = src[src.index("def _set_cwd_request(s, path: str, trust: str | None = None):"):]
        body = body[:body.index("\n    def _finish_move(")]
        self.assertIn('"_send_control_request"', body)
        elsewhere = src.replace(body, "")
        m = elsewhere.index("def move(self, sid: str, new_cwd: str) -> str:")
        self.assertNotIn("_send_control_request", elsewhere[m:m + 6000],
                         "move() speaks set_cwd only through _set_cwd_request")


if __name__ == "__main__":
    unittest.main()
