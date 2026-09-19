#!/usr/bin/env python3
"""Conversation rewind — the chat's edit-message branch (SDK backend side).

Editing a past user message rewinds the conversation to just before it and sends the edited
text as the next turn, via the CLI's designed `--resume-session-at <record uuid>` flag riding
the SDK's extra_args passthrough (verified live 2026-07-16: the branch is written IN PLACE —
same fsid, new records with parentUuid=target — and a bad target exits 1 loudly, touching
nothing). These tests cover the backend's write-side machinery:

  * the pure helpers (transcript_path / last_record_uuid / rewind_disposition) EXECUTED, since
    the one-shot leaf guard is the crash-safety core: a heal/resume mid-rewind-turn must NOT
    re-apply the flag (that would truncate the very turn it delivered);
  * source pins on the SdkSession/SdkBackend wiring (the input gate that holds the edit turn
    until a rewound client is up, the reconnect that never defers on rewind-held turns, the
    ResultMessage consume, the refused-connect cleanup, and rewind()'s busy/queued refusals).
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_rw", os.path.join(BIN, "romp_sdk_backend.py"))
BACKEND_SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


class TranscriptPath(unittest.TestCase):
    def test_encodes_every_non_alphanumeric_as_dash(self):
        # matches the CLI exactly — underscores and spaces included (the underscore-dir lesson)
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "my_proj dir")
            os.makedirs(sub)
            p = sb.transcript_path(sub, "abc-123")
            base = os.path.basename(os.path.dirname(p))
            self.assertNotIn("_", base)
            self.assertNotIn(" ", base)
            self.assertTrue(p.endswith("abc-123.jsonl"))

    def test_realpaths_a_symlinked_launch_dir(self):
        # a symlinked cwd writes transcripts under the PHYSICAL path (the CLI realpaths)
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, "real")
            os.makedirs(real)
            link = os.path.join(d, "link")
            os.symlink(real, link)
            self.assertEqual(sb.transcript_path(link, "x"), sb.transcript_path(real, "x"))


class LastRecordUuid(unittest.TestCase):
    def _write(self, lines):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("\n".join(lines) + "\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_returns_the_last_uuid_bearing_record(self):
        p = self._write([
            json.dumps({"type": "user", "uuid": "u1"}),
            json.dumps({"type": "assistant", "uuid": "a1"}),
            json.dumps({"type": "last-prompt"}),          # uuid-less trailer — skipped
            json.dumps({"type": "queue-operation"}),
        ])
        self.assertEqual(sb.last_record_uuid(p), "a1")

    def test_missing_and_empty_files_return_empty(self):
        self.assertEqual(sb.last_record_uuid("/nonexistent/nope.jsonl"), "")
        self.assertEqual(sb.last_record_uuid(self._write([""])), "")

    def test_junk_tail_lines_are_skipped(self):
        p = self._write([
            json.dumps({"type": "assistant", "uuid": "a9"}),
            '{"uuid": "trunca',                            # a partial/corrupt line — skipped, not fatal
        ])
        self.assertEqual(sb.last_record_uuid(p), "a9")


class RewindDisposition(unittest.TestCase):
    def test_applies_only_while_the_leaf_is_unmoved(self):
        self.assertEqual(sb.rewind_disposition("t1", "leaf0", "leaf0"), "apply")

    def test_spent_once_the_conversation_moved(self):
        # the rewind turn's own records landed (or a crash-heal resumed mid-turn): re-applying
        # would truncate the delivered turn — the flag is spent, resume plainly
        self.assertEqual(sb.rewind_disposition("t1", "leaf0", "leaf1"), "spent")

    def test_spent_when_the_transcript_is_unreadable(self):
        self.assertEqual(sb.rewind_disposition("t1", "leaf0", ""), "spent")

    def test_none_without_a_pending_rewind(self):
        self.assertEqual(sb.rewind_disposition("", "leaf0", "leaf0"), "none")


class WiringPins(unittest.TestCase):
    def test_options_arms_via_extra_args_one_shot(self):
        # the SDK has no typed field for --resume-session-at → extra_args (designed passthrough),
        # applied only on rewind_disposition's say-so; merge-safe beside the fork's own extra_args
        self.assertIn('kw.setdefault("extra_args", {})["resume-session-at"] = sess._rewind_to', BACKEND_SRC)
        self.assertIn('disp = rewind_disposition(sess._rewind_to, sess._rewind_leaf,', BACKEND_SRC)
        self.assertIn('sess._rewind_armed = True', BACKEND_SRC)

    def test_spent_flag_is_cleared_from_session_and_reg(self):
        self.assertIn('elif disp == "spent":', BACKEND_SRC)
        self.assertIn('self._update_reg(sess.sid, rewindTo="", rewindLeaf="", rewindBare=False,', BACKEND_SRC)
        # …and the delete-while-busy wait dies with the flags (a kernel death mid-window lands
        # here: the "" leaf reads as spent, the cards restore loudly, never a wrong-branch cut)
        self.assertIn('sess._rewind_wait = False', BACKEND_SRC)

    def test_input_gate_holds_the_edit_until_a_rewound_client_is_up(self):
        # feeding the edit turn to the un-rewound client is the wrong-branch delivery this kills
        self.assertIn("blocked = blocked or bool(self._rewind_to and not self._rewind_armed)", BACKEND_SRC)

    def test_reconnect_never_defers_on_rewind_held_turns(self):
        # rewind-held turns can't start until the reconnect arms them — deferring would deadlock
        self.assertIn("held = bool(self._rewind_to and not self._rewind_armed)", BACKEND_SRC)
        # the quiet reading counts a fed text the CLI has not taken (_untaken) as in flight
        self.assertIn("quiet = self.inflight == 0 and self._untaken is None", BACKEND_SRC)
        self.assertIn("if quiet and (held or not self._pending):", BACKEND_SRC)

    def test_result_message_consumes_the_flag(self):
        self.assertIn("# the rewind turn settled — the flag is CONSUMED", BACKEND_SRC)
        # ARMED-only (delete-while-busy): the interrupted turn's own settle lands moments after
        # the Stop hook armed the flag — an unguarded consume read that arm as the branch-take
        self.assertIn("elif self._rewind_to and self._rewind_armed:", BACKEND_SRC)

    def test_refused_connect_fails_loudly_and_drops_the_held_edit(self):
        # a CLI exit-1 on the rewind connect: drop flag + queue head, toast, reconnect plainly —
        # never a crash-loop (the flag would re-apply forever: the leaf never moved)
        self.assertIn("if self._rewind_armed and not connected:", BACKEND_SRC)
        self.assertIn("def _rewind_failed(self, exc):", BACKEND_SRC)
        self.assertIn("your edited message was NOT sent", BACKEND_SRC)

    def test_rewind_refuses_busy_compacting_and_queued(self):
        self.assertIn("def rewind(self, sid: str, target_uuid: str, text: str)", BACKEND_SRC)
        self.assertIn("if self.busy(sid) or self.compacting(sid):", BACKEND_SRC)
        self.assertIn('return False, "messages are queued for this session', BACKEND_SRC)

    def test_rewind_persists_the_flag_before_ensuring_the_thread(self):
        # a fresh session thread seeds _rewind_to from the reg — writing after _ensure could race
        # the first connect and strand the held queue
        i_reg = BACKEND_SRC.index("self._update_reg(sid, rewindTo=target_uuid, rewindLeaf=leaf, rewindBare=bare, rewindWait=False)")
        i_ensure = BACKEND_SRC.index("s = self._ensure(sid)", i_reg - 2000)
        self.assertLess(i_reg, i_ensure)

    def test_session_seeds_rewind_state_from_the_reg(self):
        self.assertIn('self._rewind_to = reg.get("rewindTo") or ""', BACKEND_SRC)
        self.assertIn('self._rewind_leaf = reg.get("rewindLeaf") or ""', BACKEND_SRC)
        self.assertIn('self._rewind_bare = bool(reg.get("rewindBare"))', BACKEND_SRC)


SID = "11111111-2222-3333-4444-555555555555"


class _StubSession:
    """Just the attrs pending_cut reads off a live SdkSession."""
    def __init__(self, **kw):
        self.ended = False
        self.resume_sid = ""
        self.__dict__.update(kw)


class _StubBackend:
    """Just the attrs pending_cut reads off the backend (sessions map + reg dir)."""
    def __init__(self, state_dir):
        import pathlib
        self.state_dir = pathlib.Path(state_dir)
        self.sessions = {}


class RollbackAndPendingCut(unittest.TestCase):
    """The chat's DELETE rollback: the edit rewind armed with NO replacement turn (rewindBare).
    pending_cut is the read side — the kernel parse truncates at the cut while the flag is
    pending, because no record lands to move the leaf until the user's next message."""

    def test_rollback_is_the_bare_arm_and_rewind_the_texted_one(self):
        self.assertIn("def rollback(self, sid: str, target_uuid: str, revalidate=None)", BACKEND_SRC)
        self.assertIn("return self._arm_rewind(sid, target_uuid, None, revalidate=revalidate)", BACKEND_SRC)
        self.assertIn("return self._arm_rewind(sid, target_uuid, text)", BACKEND_SRC)
        self.assertIn("bare = text is None", BACKEND_SRC)
        # nothing is enqueued for a bare rollback — the next real message takes the branch
        self.assertIn("if not bare:\n            s.enqueue(text)", BACKEND_SRC)

    def test_refused_connect_never_pops_the_queue_for_a_bare_rollback(self):
        # the edit flow's queue head IS the held edit turn; a bare rollback enqueued nothing, so
        # the head (a held postal delivery, say) must survive the failure
        self.assertIn("bare = self._rewind_bare", BACKEND_SRC)
        i_bare = BACKEND_SRC.index("bare = self._rewind_bare")
        i_pop = BACKEND_SRC.index("dropped = self._q_pop(0)[0] if self._pending else None", i_bare)   # T252c: the queue pops through its helper
        seg = BACKEND_SRC[i_bare:i_pop]
        self.assertIn("if not bare:", seg)
        self.assertIn("the rollback failed (the session's CLI refused it)", BACKEND_SRC)

    def test_settle_consume_clears_the_bare_flag_too(self):
        i = BACKEND_SRC.index("the flag is CONSUMED")
        seg = BACKEND_SRC[i:i + 1200]
        self.assertIn('rewindTo="", rewindLeaf="", rewindBare=False', seg)

    def _fixture(self, tmp, bare=True, leaf_moved=False):
        """A stub backend + live stub session whose transcript's HOME-relative path really exists."""
        cwd = os.path.join(tmp, "proj")
        os.makedirs(cwd, exist_ok=True)
        tp = sb.transcript_path(cwd, SID)             # honors the patched HOME below
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        with open(tp, "w") as f:
            f.write(json.dumps({"type": "user", "uuid": "l1"}) + "\n")
            if leaf_moved:
                f.write(json.dumps({"type": "assistant", "uuid": "l2"}) + "\n")
        be = _StubBackend(os.path.join(tmp, "sdk"))
        be.sessions[SID] = _StubSession(sid=SID, cwd=cwd, _rewind_to="t1", _rewind_leaf="l1",
                                        _rewind_bare=bare)
        return be

    def test_pending_cut_applies_while_the_leaf_is_unmoved(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp)
            self.assertEqual(sb.SdkBackend.pending_cut(be, SID), "t1")

    def test_pending_cut_expires_the_instant_a_record_lands(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp, leaf_moved=True)
            self.assertEqual(sb.SdkBackend.pending_cut(be, SID), "")

    def test_pending_cut_ignores_an_edit_rewind(self):
        # the edit flow's window is covered by the client overlay + the enqueued turn landing in
        # seconds — only the BARE rollback needs the parse-side cut
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp, bare=False)
            self.assertEqual(sb.SdkBackend.pending_cut(be, SID), "")

    def test_pending_cut_reads_the_reg_for_a_dead_session(self):
        # kernel restart mid-pending: no live SdkSession, the reg carries the flag (crash-heal seam)
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp)
            reg = {"cwd": be.sessions[SID].cwd, "lastSid": "", "rewindTo": "t1",
                   "rewindLeaf": "l1", "rewindBare": True}
            del be.sessions[SID]
            sb.write_reg(be.state_dir, SID, reg)
            self.assertEqual(sb.SdkBackend.pending_cut(be, SID), "t1")

    def test_pending_cut_is_empty_with_nothing_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            be = _StubBackend(os.path.join(tmp, "sdk"))
            self.assertEqual(sb.SdkBackend.pending_cut(be, SID), "")

    # ── rewind_pending, EXECUTED (same stub world) ────────────────────────────────────────────
    # The boot pass keeps a hold latched only on this probe (D8: raw flag presence latched holds
    # forever after an out-of-band continuation), but the kernel-side tests stub the backend —
    # nothing ran the real leaf-verified join, so a regression here (a wrong fsid for a
    # resume-forked transcript, say) would silently re-open the forever-latched-hold hole with
    # every kernel test still green.

    def test_rewind_pending_while_the_leaf_is_unmoved(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp)
            self.assertTrue(sb.SdkBackend.rewind_pending(be, SID),
                            "an unconsumed rewind against an unmoved leaf is still applicable")

    def test_rewind_pending_goes_false_once_the_transcript_moves(self):
        # THE regression test for the D8 fix: raw flag presence would return True here — the flag
        # is still armed in the session — but the leaf moved, so the rewind is spent and a boot
        # hold keyed on it must resolve instead of latching forever
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp, leaf_moved=True)
            self.assertFalse(sb.SdkBackend.rewind_pending(be, SID))

    def test_rewind_pending_reads_the_reg_for_a_dead_session(self):
        # kernel restart mid-pending: no live SdkSession, the reg carries the flag + cwd/lastSid
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp)
            reg = {"cwd": be.sessions[SID].cwd, "lastSid": "", "rewindTo": "t1",
                   "rewindLeaf": "l1", "rewindBare": True}
            del be.sessions[SID]
            sb.write_reg(be.state_dir, SID, reg)
            self.assertTrue(sb.SdkBackend.rewind_pending(be, SID))

    def test_rewind_pending_follows_a_resume_fork(self):
        # pins the fsid = resume_sid-or-sid join: the forked session's CURRENT transcript is the
        # fork file, and it moved past the recorded leaf — reading the pre-fork file instead
        # would call the rewind still pending and re-latch a spent hold
        from unittest import mock
        fork = "22222222-3333-4444-5555-666666666666"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp)                    # SID's own transcript leaf is UNMOVED
            fp = sb.transcript_path(be.sessions[SID].cwd, fork)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w") as f:
                f.write(json.dumps({"type": "user", "uuid": "l1"}) + "\n")
                f.write(json.dumps({"type": "assistant", "uuid": "l2"}) + "\n")
            be.sessions[SID].resume_sid = fork
            self.assertFalse(sb.SdkBackend.rewind_pending(be, SID),
                             "the fork transcript moved past the leaf — spent, not pending")

    def test_rewind_pending_is_bare_agnostic(self):
        # unlike pending_cut (bare-only by design), the probe answers for EDIT rewinds too — a
        # boot hold from an edit gesture must stay latched while its rewind is genuinely pending
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            be = self._fixture(tmp, bare=False)
            self.assertTrue(sb.SdkBackend.rewind_pending(be, SID))


if __name__ == "__main__":
    unittest.main()


class _BusySession:
    """The attrs the delete-while-busy pipeline reads/writes off a live SdkSession, with the
    interrupt and reconnect as call recorders. inflight=1 = a running turn."""
    def __init__(self, sid, cwd):
        import threading
        self.sid, self.cwd, self.name = sid, cwd, "busy"
        self.resume_sid = ""
        self.ended = False
        self.inflight = 1
        self._pending = []
        self._compacting = False
        self._lock = threading.Lock()
        self._rewind_to = self._rewind_leaf = ""
        self._rewind_bare = self._rewind_armed = self._rewind_wait = False
        self._rewind_revalidate = None
        self.interrupts, self.reconnects = 0, 0

    def interrupt(self):
        self.interrupts += 1

    def request_reconnect(self):
        self.reconnects += 1


class DeleteWhileBusy(unittest.TestCase):
    """Delete-while-busy (the user 2026-08-29), EXECUTED on the real backend methods: a bare
    rollback on an in-flight turn interrupts it, holds intake (the existing rewind gate), renders
    the cut immediately, and arms the rewind at the turn's actual END — where the interrupted
    turn's partial records become the abandoned branch tail. Failures restore loudly through
    _rewind_resolved("failed"); the idle path is byte-identical to before (the tests above)."""

    def setUp(self):
        import pathlib
        self._env = os.environ.get("HOME")
        self.tmp = tempfile.mkdtemp()
        os.environ["HOME"] = self.tmp
        self.cwd = os.path.join(self.tmp, "proj")
        os.makedirs(self.cwd)
        self.sid = "11111111-2222-3333-4444-000000000077"
        self.be = sb.SdkBackend(os.path.join(self.tmp, "sdk"), "/bin/true", lambda *a, **k: None,
                                log=lambda *a, **k: None)
        sb.write_reg(pathlib.Path(self.be.state_dir), self.sid,
                     {"sid": self.sid, "name": "busy", "cwd": self.cwd, "alive": True})
        tp = sb.transcript_path(self.cwd, self.sid)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        with open(tp, "w") as f:
            f.write(json.dumps({"type": "user", "uuid": "t1"}) + "\n")
            f.write(json.dumps({"type": "user", "uuid": "u2"}) + "\n")
        self.s = _BusySession(self.sid, self.cwd)
        self.be.sessions[self.sid] = self.s
        self.resolved = []
        self.be.rewind_resolved_cb = lambda sid, outcome: self.resolved.append((sid, outcome))

    def tearDown(self):
        if self._env is not None:
            os.environ["HOME"] = self._env

    def _partial_lands(self):
        """The interrupted turn's partial output — records landing AFTER the gesture."""
        with open(sb.transcript_path(self.cwd, self.sid), "a") as f:
            f.write(json.dumps({"type": "assistant", "uuid": "a-partial"}) + "\n")

    def test_gesture_interrupts_holds_and_renders_then_the_turn_end_arms(self):
        ok, err = self.be.rollback(self.sid, "t1", revalidate=lambda: None)
        self.assertTrue(ok, err)
        self.assertEqual(self.s.interrupts, 1, "the running turn is interrupted at the gesture")
        self.assertTrue(self.s._rewind_wait)
        self.assertEqual((self.s._rewind_to, self.s._rewind_bare), ("t1", True))
        # intake held: the inputs() gate's exact expression (rewind set, not armed)
        self.assertTrue(bool(self.s._rewind_to and not self.s._rewind_armed),
                        "the existing rewind gate holds message intake across the window")
        # the UI acknowledged at the click: pending_cut renders even as records keep landing
        self._partial_lands()
        self.assertEqual(self.be.pending_cut(self.sid), "t1")
        reg = sb.read_reg(self.be.state_dir, self.sid)
        self.assertTrue(reg.get("rewindWait"), "the window survives a kernel death VISIBLY (reg)")
        # the turn's actual end (the Stop hook / settle call this): the arm completes
        self.s.inflight = 0
        self.be._complete_rewind_wait(self.s)
        self.assertFalse(self.s._rewind_wait)
        self.assertEqual(self.s._rewind_leaf, "a-partial",
                         "the leaf records AFTER the dust settles — the partial output is the "
                         "abandoned branch tail, the rewind family's normal shape")
        self.assertEqual(self.s.reconnects, 1, "from here it is the idle rollback path exactly")
        reg = sb.read_reg(self.be.state_dir, self.sid)
        self.assertEqual((reg.get("rewindTo"), reg.get("rewindLeaf"), bool(reg.get("rewindWait"))),
                         ("t1", "a-partial", False))
        # idempotent: the settle observer firing after the Stop observer is a no-op
        self.be._complete_rewind_wait(self.s)
        self.assertEqual(self.s.reconnects, 1)
        self.assertEqual(self.resolved, [], "no failure — nothing resolved until the branch takes")

    def test_busy_answers_for_the_double_with_its_three_attributes(self):
        # busy() reads inflight, _pending and _untaken under _lock and calls no SdkSession method, so this
        # double (recorders only) answers; a method call on the session would break every test here that
        # reaches busy()
        self.assertTrue(self.be.busy(self.sid), "a running turn (inflight 1)")
        self.s.inflight = 0
        self.assertFalse(self.be.busy(self.sid))
        self.s._pending.append("queued behind the turn")
        self.assertTrue(self.be.busy(self.sid), "a queued text")

    def test_arm_time_revalidation_failure_restores_loudly(self):
        # a mid-window auto-compaction moved the boundary past the target: the closure refuses
        ok, _ = self.be.rollback(self.sid, "t1",
                                 revalidate=lambda: "the target fell behind a compaction")
        self.assertTrue(ok)
        self.s.inflight = 0
        self.be._complete_rewind_wait(self.s)
        self.assertEqual((self.s._rewind_to, self.s._rewind_wait), ("", False))
        self.assertEqual(self.resolved, [(self.sid, "failed")],
                         "the held cards restore through the existing failure event")
        self.assertEqual(self.s.reconnects, 0, "nothing to reconnect for — the delete is off")
        self.assertEqual(self.be.pending_cut(self.sid), "", "the cut render dies with the restore")

    def test_queued_strangers_still_refuse(self):
        self.s._pending.append("a queued message")
        ok, err = self.be.rollback(self.sid, "t1")
        self.assertFalse(ok)
        self.assertIn("queued", err)
        self.assertEqual(self.s.interrupts, 0, "refused at the gesture — nothing was interrupted")

    def test_compacting_refuses_with_its_own_honest_message(self):
        self.s._compacting = True
        ok, err = self.be.rollback(self.sid, "t1")
        self.assertFalse(ok)
        self.assertIn("compacting", err)
        self.assertEqual(self.s.interrupts, 0)

    def test_the_edit_rewind_keeps_the_busy_refusal(self):
        # only the DELETE inverts the hazard — an edit's replacement turn must not race a dying one
        ok, err = self.be.rewind(self.sid, "t1", "the edited text")
        self.assertFalse(ok)
        self.assertIn("busy", err)
        self.assertEqual(self.s.interrupts, 0)

    def test_a_turn_that_settled_under_the_gesture_completes_inline(self):
        # the third observer: busy() read true, but the turn settled before the fields landed —
        # with intake held, no later turn-end event would ever fire
        real_interrupt = self.s.interrupt
        def settle_and_interrupt():
            real_interrupt()
            self.s.inflight = 0
        self.s.interrupt = settle_and_interrupt
        ok, _ = self.be.rollback(self.sid, "t1", revalidate=lambda: None)
        self.assertTrue(ok)
        self.assertFalse(self.s._rewind_wait, "completed inline — nothing left to wait on")
        self.assertEqual(self.s.reconnects, 1)

    def test_wait_render_survives_a_kernel_restart_until_the_connect_rules(self):
        # restart mid-window: the reg carries the wait; pending_cut still renders the cut from the
        # reg (no live session), and the connect path's "" leaf reads as spent → loud restore
        ok, _ = self.be.rollback(self.sid, "t1", revalidate=lambda: None)
        self.assertTrue(ok)
        del self.be.sessions[self.sid]
        self.assertEqual(self.be.pending_cut(self.sid), "t1")
        self.assertEqual(sb.rewind_disposition("t1", "", "u2"), "spent",
                         "the mid-window '' leaf can never read as apply — a kernel death "
                         "degrades to the loud spent restore, never a wrong-branch cut")

    def test_stop_hook_and_settle_both_observe_the_turn_end(self):
        self.assertIn('if getattr(self, "_rewind_wait", False):\n            try:\n'
                      '                self.backend._complete_rewind_wait(self)', BACKEND_SRC)
        self.assertIn('if self._rewind_to and getattr(self, "_rewind_wait", False):', BACKEND_SRC)
