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
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_rw", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
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
        self.assertIn('self._update_reg(sess.sid, rewindTo="", rewindLeaf="", rewindBare=False)', BACKEND_SRC)

    def test_input_gate_holds_the_edit_until_a_rewound_client_is_up(self):
        # feeding the edit turn to the un-rewound client is the wrong-branch delivery this kills
        self.assertIn("blocked = blocked or bool(self._rewind_to and not self._rewind_armed)", BACKEND_SRC)

    def test_reconnect_never_defers_on_rewind_held_turns(self):
        # rewind-held turns can't start until the reconnect arms them — deferring would deadlock
        self.assertIn("held = bool(self._rewind_to and not self._rewind_armed)", BACKEND_SRC)
        self.assertIn("if self.inflight == 0 and (held or not self._pending):", BACKEND_SRC)

    def test_result_message_consumes_the_flag(self):
        self.assertIn("# the rewind turn settled — the flag is CONSUMED", BACKEND_SRC)

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
        i_reg = BACKEND_SRC.index("self._update_reg(sid, rewindTo=target_uuid, rewindLeaf=leaf, rewindBare=bare)")
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
        self.assertIn("def rollback(self, sid: str, target_uuid: str)", BACKEND_SRC)
        self.assertIn("return self._arm_rewind(sid, target_uuid, None)", BACKEND_SRC)
        self.assertIn("return self._arm_rewind(sid, target_uuid, text)", BACKEND_SRC)
        self.assertIn("bare = text is None", BACKEND_SRC)
        # nothing is enqueued for a bare rollback — the next real message takes the branch
        self.assertIn("if not bare:\n            s.enqueue(text)", BACKEND_SRC)

    def test_refused_connect_never_pops_the_queue_for_a_bare_rollback(self):
        # the edit flow's queue head IS the held edit turn; a bare rollback enqueued nothing, so
        # the head (a held postal delivery, say) must survive the failure
        self.assertIn("bare = self._rewind_bare", BACKEND_SRC)
        i_bare = BACKEND_SRC.index("bare = self._rewind_bare")
        i_pop = BACKEND_SRC.index("dropped = self._pending.pop(0) if self._pending else None", i_bare)
        seg = BACKEND_SRC[i_bare:i_pop]
        self.assertIn("if not bare:", seg)
        self.assertIn("the rollback failed (the session's CLI refused it)", BACKEND_SRC)

    def test_settle_consume_clears_the_bare_flag_too(self):
        i = BACKEND_SRC.index("the flag is CONSUMED")
        seg = BACKEND_SRC[i:i + 600]
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
