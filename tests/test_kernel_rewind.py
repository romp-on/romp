#!/usr/bin/env python3
"""Conversation rewind — the kernel's rewindSend drive op + its transcript validation.

_rewind_target picks WHERE an edit of user record U cuts the conversation: U's nearest
user/assistant ancestor (the record types the CLI addresses; attachments are spine nodes but not
targets). It refuses, with a human reason, everything the CLI would refuse loudly (and one thing
it wouldn't: a stale click on an already-abandoned branch): a missing record, a pre-compaction
record ("No message found" — verified live 2026-07-16), and the conversation's first message.
Exercised HERE against synthetic transcripts, so the backend's failure path stays reserved for
genuine races. Plus source pins on the drive-op arm (SDK-only gate, busy gate, warn toasts)."""
import inspect
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_rw", os.path.join(BIN, "romp-kernel")).load_module()

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

SID = "11111111-2222-3333-4444-555555555555"


def _rec(typ, uuid, parent, text="x", **extra):
    r = {"type": typ, "uuid": uuid, "parentUuid": parent, "sessionId": SID,
         "timestamp": "2026-07-16T10:00:00Z"}
    if typ in ("user", "assistant"):
        r["message"] = {"role": typ, "content": [{"type": "text", "text": text}]}
    r.update(extra)
    return r


class RewindTarget(unittest.TestCase):
    def _transcript(self, recs):
        d = tempfile.mkdtemp()
        p = os.path.join(d, SID + ".jsonl")
        with open(p, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return p

    def _base(self):
        return [
            _rec("user", "u1", None, "first ask"),
            _rec("assistant", "a1", "u1", "first reply"),
            _rec("user", "u2", "a1", "second ask"),
            _rec("assistant", "a2", "u2", "second reply"),
        ]

    def test_editing_a_message_cuts_at_the_previous_assistant(self):
        p = self._transcript(self._base())
        self.assertEqual(km._rewind_target(p, SID, "u2"), ("a1", None))

    def test_attachment_spine_nodes_are_skipped_not_targeted(self):
        # the CLI parent-chains attachments between the user record and the reply — they're on the
        # spine but aren't addressable messages, so the walk crosses them to the real ancestor
        recs = self._base() + [
            _rec("attachment", "att1", "a2", attachment={"type": "other"}),
            _rec("user", "u3", "att1", "third ask"),
        ]
        p = self._transcript(recs)
        self.assertEqual(km._rewind_target(p, SID, "u3"), ("a2", None))

    def test_the_first_message_has_nothing_to_rewind_to(self):
        p = self._transcript(self._base())
        target, err = km._rewind_target(p, SID, "u1")
        self.assertIsNone(target)
        self.assertIn("first message", err)

    def test_a_record_off_the_active_chain_is_refused(self):
        # u2/a2 were already rewound away (u3 branches from a1) — a stale window's click on the
        # old bubble must not truncate the NEW branch
        recs = self._base() + [_rec("user", "u3", "a1", "second ask, edited"),
                               _rec("assistant", "a3", "u3", "branch reply")]
        p = self._transcript(recs)
        target, err = km._rewind_target(p, SID, "u2")
        self.assertIsNone(target)
        self.assertIn("already rewound", err)

    def test_a_pre_compaction_record_is_refused(self):
        # the CLI only loads post-boundary records; a pre-boundary target exits 1 "No message found"
        recs = self._base() + [
            _rec("system", "cb1", None, subtype="compact_boundary", logicalParentUuid="a2",
                 compactMetadata={"preservedSegment": {"tailUuid": "a2"}}),
            _rec("user", "cs1", "cb1", "summary", isCompactSummary=True),
            _rec("user", "u3", "cs1", "post-compaction ask"),
            _rec("assistant", "a3", "u3", "post-compaction reply"),
        ]
        p = self._transcript(recs)
        target, err = km._rewind_target(p, SID, "u2")
        self.assertIsNone(target)
        self.assertIn("compaction", err)
        # while the FIRST post-compaction message cuts at the replayed summary record — a user-type
        # record the CLI addresses fine (verified live: user-record uuids are valid targets)
        self.assertEqual(km._rewind_target(p, SID, "u3"), ("cs1", None))

    def test_a_missing_record_is_refused(self):
        p = self._transcript(self._base())
        target, err = km._rewind_target(p, SID, "77777777-8888-9999-aaaa-bbbbbbbbbbbb")
        self.assertIsNone(target)
        self.assertIn("isn't in the transcript", err)


class DriveOpPins(unittest.TestCase):
    def test_rewind_send_is_a_drive_op(self):
        src = inspect.getsource(km._drive)
        self.assertIn('"rewindSend"', src)   # in ID_OPS → routed by session id
        self.assertIn('elif t == "rewindSend" and msg.get("uuid") and msg.get("text"):', src)

    def test_refusals_warn_toast_and_never_send(self):
        src = inspect.getsource(km._drive)
        self.assertIn('err = _rewind_send(sid, str(msg["uuid"]), str(msg["text"]))', src)
        self.assertIn('client["send"](json.dumps({"type": "warn", "text": err}))', src)

    def test_rewind_send_gates_on_backend_and_busy(self):
        src = inspect.getsource(km._rewind_send)
        self.assertIn('if not hasattr(be, "rewind"):', src)          # SDK-only (tmux has Esc Esc natively)
        self.assertIn("if _ops_gate(sid):", src)                     # busy/compacting/parked-queue → refuse
        self.assertIn("target, err = _rewind_target(", src)          # transcript validation before the backend

    def test_no_optimistic_kernel_echo_for_a_rewind(self):
        # the edit lands MID-chat at the branch point, not at the tail — the client overlay owns the gap
        src = inspect.getsource(km._drive)
        arm = src[src.index('elif t == "rewindSend"'):src.index('elif t == "interrupt"')]
        self.assertNotIn("_send_or_park", arm)


class DeleteRollback(unittest.TestCase):
    """The chat's DELETE button: rewindDelete rolls the conversation back to just before the
    deleted message — the edit rewind's validation and cut point, with nothing sent."""

    def test_rewind_delete_is_a_drive_op_that_sends_nothing(self):
        src = inspect.getsource(km._drive)
        self.assertIn('"rewindDelete"', src)          # in ID_OPS → routed by session id
        self.assertIn('elif t == "rewindDelete" and msg.get("uuid"):', src)
        arm = src[src.index('elif t == "rewindDelete"'):src.index('elif t == "interrupt"')]
        self.assertIn('err = _rewind_rollback(sid, str(msg["uuid"]))', arm)
        self.assertIn('client["send"](json.dumps({"type": "warn", "text": err}))', arm)
        self.assertNotIn("_send_or_park", arm)

    def test_rollback_gates_match_the_edit_rewind(self):
        src = inspect.getsource(km._rewind_rollback)
        self.assertIn('if not hasattr(be, "rollback"):', src)    # SDK-only (tmux has Esc Esc natively)
        self.assertIn("if _ops_gate(sid):", src)                 # busy/compacting/parked-queue → refuse
        self.assertIn("target, err = _rewind_target(", src)      # the SAME cut point as an edit
        self.assertIn("be.rollback(sid, target)", src)


class ParseCut(unittest.TestCase):
    """While a bare rollback is pending, the kernel parse starts its walk at the cut — every
    surface renders the rolled-back conversation immediately, instead of showing the doomed
    tail until the user's next message finally lands past the recorded leaf."""

    def _transcript(self, recs):
        d = tempfile.mkdtemp()
        p = os.path.join(d, SID + ".jsonl")
        with open(p, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return p

    def _base(self):
        return [
            _rec("user", "u1", None, "first ask"),
            _rec("assistant", "a1", "u1", "first reply"),
            _rec("user", "u2", "a1", "second ask"),
            _rec("assistant", "a2", "u2", "second reply"),
        ]

    def test_leaf_override_truncates_the_active_chain(self):
        p = self._transcript(self._base())
        ad = km.em.FileAdapter([p], p, leaf_override="a1")
        self.assertEqual(ad.active_path(), {"u1", "a1"})

    def test_a_stale_override_falls_back_to_the_true_leaf(self):
        # a raced clear / wrong file: an override absent from the graph must NEVER empty the parse
        p = self._transcript(self._base())
        ad = km.em.FileAdapter([p], p, leaf_override="99999999-aaaa-bbbb-cccc-dddddddddddd")
        self.assertEqual(ad.active_path(), {"u1", "a1", "u2", "a2"})

    def test_parse_session_threads_the_override(self):
        p = self._transcript(self._base())
        cut = km.em.parse_session(p, leaf_override="a1")
        full = km.em.parse_session(p)
        texts = lambda sess: json.dumps([a.get("message") for t in sess["turns"] for a in t["atoms"]])
        self.assertNotIn("second ask", texts(cut))
        self.assertIn("second ask", texts(full))
        self.assertIn("first reply", texts(cut))   # everything up to the cut point survives

    def test_a_salvaged_reply_in_the_abandoned_tail_drops_with_the_cut(self):
        """Deleting a message must take its REPLY with it even when that reply survives only as an
        orphanReply marker. A salvaged reply has no position in the transcript graph — that absence
        is exactly what the marker papers over — so the leaf_override walk cannot drop it the way it
        drops the abandoned chain. Unfiltered, the prompt vanished (it was on the chain) while the
        answer stayed standing alone (it was re-synthesized from states/), which reads as a delete
        that only half worked (the user 2026-08-01)."""
        recs = [
            _rec("user", "u1", None, "first ask", timestamp="2026-07-16T10:00:00Z"),
            _rec("assistant", "a1", "u1", "first reply", timestamp="2026-07-16T10:00:10Z"),
            _rec("user", "u2", "a1", "second ask", timestamp="2026-07-16T10:00:20Z"),
        ]
        p = self._transcript(recs)
        ep = lambda z: km.em.parse_z(z)
        markers = [   # one on each side of the cut point (a1, 10:00:10)
            {"t": ep("2026-07-16T10:00:05Z"), "orphanReply": {"uuid": "o-early", "text": "kept salvage"}},
            {"t": ep("2026-07-16T10:00:30Z"), "orphanReply": {"uuid": "o-late", "text": "doomed salvage"}},
        ]
        texts = lambda s: json.dumps([a.get("message") for t in s["turns"] for a in t["atoms"]])
        full = km.em.parse_session(p, states=markers)
        self.assertIn("doomed salvage", texts(full))     # with no delete pending, the salvage shows
        self.assertIn("kept salvage", texts(full))
        cut = km.em.parse_session(p, states=markers, leaf_override="a1")
        self.assertNotIn("second ask", texts(cut))       # the deleted prompt goes, as before
        self.assertNotIn("doomed salvage", texts(cut))   # and now its salvaged reply goes WITH it
        self.assertIn("kept salvage", texts(cut))        # a salvage from BEFORE the cut is untouched
        self.assertIn("first reply", texts(cut))

    def test_the_cut_never_filters_idle_spans(self):
        """Idle atoms describe the session's working state NOW (an open span runs to `now`), not
        conversation content — filtering them on the cut's timestamp would blank the live working
        indicator for any session sitting on a pending delete."""
        src = inspect.getsource(km.em.parse_session)
        cut_block = src[src.index("if leaf_override and leaf_override in adapter.by_uuid"):]
        self.assertNotIn("synthesize_idle", cut_block.split("atoms += orphans")[0])
        self.assertIn("orphans = [a for a in orphans if a[\"t\"] <= cut_t]", src)

    def test_kernel_parse_keys_the_cache_on_the_cut(self):
        # arming and clearing both change the parse with NO file change — the cut must ride the key
        src = inspect.getsource(km._parse)
        self.assertIn("cut = _be.pending_cut(sid) if _be else \"\"", src)
        self.assertIn("key = (st.st_mtime, st.st_size, cut)", src)
        self.assertIn("leaf_override=cut or None", src)
        # the never-parsing feed reader compares the file identity prefix only
        self.assertIn("tuple(hit[0][:2]) == key", inspect.getsource(km._parse_cached))

    def test_the_built_chat_cache_sig_carries_the_cut_too(self):
        # same lesson one level up: the BUILT payload cache would otherwise keep pushing a
        # background tab's uncut payload until the transcript next changes (verified live 07-17:
        # active tabs rebuild and cut correctly; the sig closes the background-tab hole)
        src = inspect.getsource(km._chat_build_sig)
        self.assertIn('sig.append(_be.pending_cut(sess.get("sid") or "") if _be else "")', src)


class RevertOnDelete(unittest.TestCase):
    """Deleting a message rolls the GOAL STORE back too (jd.revert_to), keyed on the deleted message's time
    (the user 2026-07-22). _atom_epoch resolves that time from the transcript BEFORE the backend arms its
    cut; the rollback arm then reverts the goal actions the abandoned turn(s) drove."""

    def _transcript(self, recs):
        d = tempfile.mkdtemp()
        p = os.path.join(d, SID + ".jsonl")
        with open(p, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return p

    def test_atom_epoch_resolves_a_message_time(self):
        from datetime import datetime, timezone
        now = int(datetime(2026, 7, 16, 10, 1, 0, tzinfo=timezone.utc).timestamp())
        p = self._transcript([_rec("user", "u1", None, "first ask"),
                              _rec("assistant", "a1", "u1", "first reply"),
                              _rec("user", "u2", "a1", "second ask")])
        want = next(a["t"] for t in km.em.parse_session(p, rompuuid=SID, now=now)["turns"]
                    for a in t["atoms"] if a.get("uuid") == "u2")
        self.assertEqual(km._atom_epoch(p, SID, "u2", now), want, "resolves the atom's epoch time")
        self.assertIsNone(km._atom_epoch(p, SID, "no-such-uuid", now), "a uuid not in the transcript → None")

    def test_delete_hides_born_in_range_goals_at_the_gesture(self):
        # the deleted message's time is read BEFORE be.rollback arms the pending_cut (which would hide it
        # from _parse); the gesture then latches the card HOLD — the archive waits for the branch-take
        # (two-phase timing: archiving at ARM both missed late mints and archived goals for rewinds that
        # never happened).
        src = inspect.getsource(km._rewind_rollback)
        self.assertIn("cut_t = _atom_epoch(", src)                   # resolved before be.rollback
        self.assertIn("ok, berr = be.rollback(sid, target)", src)
        self.assertIn("_arm_rewind_hold(be, sid, cut_t)", src)       # hide on success; archive at the take
        self.assertLess(src.index("cut_t = _atom_epoch("), src.index("be.rollback(sid, target)"),
                        "the deleted message's time is read BEFORE the cut is armed")

    def test_edit_hides_born_in_range_goals_too(self):
        # an edit abandons the old tail the same way a delete does → same two-phase cleanup
        src = inspect.getsource(km._rewind_send)
        self.assertIn("cut_t = _atom_epoch(", src)
        self.assertIn("_arm_rewind_hold(be, sid, cut_t)", src)
        self.assertLess(src.index("cut_t = _atom_epoch("), src.index("be.rewind(sid, target"),
                        "the edited message's time is read BEFORE the cut is armed")

    def test_drop_goals_after_is_best_effort(self):
        # a cleanup failure must never undo the cut the user already got
        src = inspect.getsource(km._drop_goals_after)
        self.assertIn("jd.drop_goals_after(sid, cut_t, kept=kept)", src)   # kept-chain exemption threads through
        self.assertIn("except Exception", src)                       # swallow-and-log, never raise past the delete


class TwoPhaseRewindTiming(unittest.TestCase):
    """Items 4 + 5 of the rewind-cleanup plan: the gesture HIDES the affected cards (latched hold),
    the ARCHIVE lands only at the branch-take, a failed/refused/dissolved rewind RESTORES loudly,
    and an unresolvable cut time is an error row — never a silent no-cleanup. Pre-fix the archive
    fired at ARM time: a CLI refusal or a spent flag left the conversation intact with its goals
    already archived (the inverse bug), and every mint landing after the arm escaped forever."""

    T0 = 1781100000
    CUT = T0 + 50

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self._saved_state = km.jd.STATE
        (self.td / "state").mkdir()
        km.jd._rebind_state(self.td / "state")
        km._rewind_holds[0] = None                     # drop the cached map from any earlier test
        self._saved_sessions = km._sessions
        jd = km.jd
        s = {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {},
             "placementsV": jd.PLACEMENTS_V}
        jd.apply_plan(s, "s1", self.T0, [{"do": "mint", "why": "x", "text": "Pre-cut survivor"}], [])
        jd.apply_plan(s, "s2", self.T0 + 100, [{"do": "mint", "why": "x", "text": "Doomed ask"}],
                      jd.open_menu(s))
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        self.survivor, self.doomed = "%s:g1" % SID, "%s:g2" % SID

    def tearDown(self):
        km._sessions = self._saved_sessions
        km._rewind_holds[0] = None
        km.jd._rebind_state(self._saved_state)

    def _transcript(self, recs):
        p = self.td / (SID + ".jsonl")
        with open(p, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        km._sessions = lambda now: [{"sid": SID, "path": str(p)}]
        return str(p)

    def test_the_gesture_hides_and_the_take_archives(self):
        km._rewind_hold_set(SID, self.CUT, "leaf-at-arm")
        feed = km._feed_goals(SID)
        self.assertNotIn(self.doomed, feed["nodes"], "the gesture hid the doomed card at once")
        self.assertIn(self.survivor, feed["nodes"], "…and only the doomed card")
        self.assertIn(self.doomed, km.jd.load_goals(SID)["nodes"],
                      "the STORE is untouched while the rewind is pending (hide, not archive)")
        km._on_rewind_resolved(SID, "taken")           # the branch-take event
        self.assertNotIn(self.doomed, km.jd.load_goals(SID)["nodes"], "the take archives")
        self.assertIn(self.doomed, km.jd.load_goal_archive(SID)["nodes"])
        self.assertIsNone(km._rewind_hold_get(SID), "the hold is spent — latched until this event only")

    def test_a_refused_rewind_restores_the_hidden_cards_loudly(self):
        km._rewind_hold_set(SID, self.CUT, "leaf-at-arm")
        km._on_rewind_resolved(SID, "failed")          # the CLI refused; conversation unchanged
        self.assertIn(self.doomed, km.jd.load_goals(SID)["nodes"], "nothing was archived")
        self.assertIn(self.doomed, km._feed_goals(SID)["nodes"], "the card is back on the feed")
        self.assertIsNone(km._rewind_hold_get(SID))
        self.assertIn("rewind-restore", km.jd.ERRORS.read_text(), "the restore is loud")

    def test_a_kept_chain_card_born_after_the_cut_survives_the_hide_and_the_take(self):
        # The replacement ask's card is minted DURING the open rewind turn (the judge's prompt-run,
        # by design) with t > cut_t — a bare t-keyed sweep hid it all turn and archived it at the
        # take. The sweep threads the kept-chain exemption: identity, not time, decides its fate.
        self._transcript([_rec("user", "u1", None, "first ask"),
                          _rec("assistant", "a1", "u1", "first reply"),
                          _rec("user", "u2", "a1", "second ask"),
                          _rec("assistant", "a2", "u2", "second reply"),
                          _rec("user", "u3", "a1", "second ask, rewritten"),
                          _rec("assistant", "a3", "u3", "new-branch reply")])
        jd = km.jd
        s = jd.load_goals(SID)
        jd.apply_plan(s, "s3", self.CUT + 30, [{"do": "mint", "why": "x", "text": "Fresh ask"}],
                      jd.open_menu(s), prompt_uuid="u3")   # kept-chain anchor, born INSIDE the window
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        fresh = "%s:g3" % SID
        km._rewind_hold_set(SID, self.CUT, "a2")
        feed = km._feed_goals(SID)
        self.assertIn(fresh, feed["nodes"], "the live-branch card stays visible during the hold")
        self.assertNotIn(self.doomed, feed["nodes"], "…while the doomed card still hides")
        km._on_rewind_resolved(SID, "taken")
        live = jd.load_goals(SID)
        self.assertIn(fresh, live["nodes"], "the take spares the kept-chain card")
        self.assertNotIn(fresh, jd.load_goal_archive(SID)["nodes"])
        self.assertNotIn(fresh, live.get("rewindSwept", {}), "no bogus permanent tombstone")
        self.assertNotIn(self.doomed, live["nodes"], "the doomed card still archives")
        self.assertIn(self.doomed, jd.load_goal_archive(SID)["nodes"])

    def test_a_user_restored_card_survives_the_hold_hide_and_the_take(self):
        # A card restored out of an EARLIER rewind's sweep carries a durable rewindRestored stamp;
        # a LATER rewind whose cut range merely time-overlaps it used to hide it at the gesture
        # (kernel.py's hold view) and re-archive it at the take (drop_goals_after) — popping the
        # stamp, so even the reconciler's shield was gone. Both surfaces route through
        # jd.swept_ids, so one exemption gives hide==take parity: the restored card never hides,
        # never re-archives, and keeps its stamp. Only the user's own gesture re-kills it.
        jd = km.jd
        s = jd.load_goals(SID)
        jd.apply_plan(s, "s3", self.T0 + 90, [{"do": "mint", "why": "x", "text": "Restored earlier"}],
                      jd.open_menu(s))
        restored_nid = "%s:g3" % SID
        s["rewindRestored"] = {restored_nid: self.T0 + 95}   # the earlier restore's durable stamp
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        km._rewind_hold_set(SID, self.CUT, "leaf-at-arm")
        feed = km._feed_goals(SID)
        self.assertIn(restored_nid, feed["nodes"], "the restored card never hides at the gesture")
        self.assertNotIn(self.doomed, feed["nodes"], "…while the unrestored in-range card does")
        km._on_rewind_resolved(SID, "taken")
        live = jd.load_goals(SID)
        self.assertIn(restored_nid, live["nodes"], "the take spares it too — hide==take parity")
        self.assertEqual(live["rewindRestored"][restored_nid], self.T0 + 95,
                         "…with the durable stamp intact")
        self.assertNotIn(restored_nid, jd.load_goal_archive(SID)["nodes"])
        self.assertNotIn(self.doomed, live["nodes"], "the unrestored card still archives")

    def test_a_spent_flag_discriminates_through_recorded_resume_lineage(self):
        # crash-heal shape: a recorded fresh-head resume fork (states resumeFork row) means the
        # armed leaf is reachable only through the STITCHED walk — a lineage-blind walk read it as
        # off-chain and archived live cards on a guess. The exported predicate must restore here.
        frm = SID
        fork = "22222222-3333-4444-5555-666666666666"
        anchor = self.td / (frm + ".jsonl")
        with open(anchor, "w") as f:
            for r in [_rec("user", "u1", None, "first ask"),
                      _rec("assistant", "a1", "u1", "first reply"),
                      _rec("user", "u2", "a1", "second ask"),
                      _rec("assistant", "a2", "u2", "second reply")]:
                f.write(json.dumps(r) + "\n")
        fp = self.td / (fork + ".jsonl")
        with open(fp, "w") as f:
            for r in [_rec("user", "u3", None, "continues after the machine cut"),
                      _rec("assistant", "a3", "u3", "stitched reply")]:
                f.write(json.dumps(r) + "\n")
        km.jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.STATESDIR / (SID + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": frm, "to": fork}, "t": self.T0 + 40}) + "\n")
        km._sessions = lambda now: [{"sid": SID, "path": str(fp)}]
        km._rewind_hold_set(SID, self.CUT, "a2")       # armed pre-fork; the rollback then dissolved
        km._on_rewind_resolved(SID, "spent")
        self.assertIn(self.doomed, km.jd.load_goals(SID)["nodes"],
                      "the stitched walk keeps a2 → restored, never archived on a guess")
        self.assertNotIn(self.doomed, km.jd.load_goal_archive(SID)["nodes"])

    def test_a_spent_flag_with_the_old_branch_still_active_restores(self):
        # the rollback dissolved: a record landed on the OLD branch, so the recorded leaf is still
        # on the active chain — archiving here would archive cards for turns that still exist
        self._transcript([_rec("user", "u1", None, "first ask"),
                          _rec("assistant", "a1", "u1", "first reply"),
                          _rec("user", "u2", "a1", "second ask"),
                          _rec("assistant", "a2", "u2", "second reply")])
        km._rewind_hold_set(SID, self.CUT, "a2")       # the leaf recorded at arm — still the leaf
        km._on_rewind_resolved(SID, "spent")
        self.assertIn(self.doomed, km.jd.load_goals(SID)["nodes"], "restored, not archived")
        self.assertIsNone(km._rewind_hold_get(SID))

    def test_a_spent_flag_whose_branch_took_archives(self):
        # crash-heal shape: the take landed (u3 branches from a1) before the flag could be tidied —
        # the recorded leaf a2 is off the active chain, so the rewind DID happen
        self._transcript([_rec("user", "u1", None, "first ask"),
                          _rec("assistant", "a1", "u1", "first reply"),
                          _rec("user", "u2", "a1", "second ask"),
                          _rec("assistant", "a2", "u2", "second reply"),
                          _rec("user", "u3", "a1", "second ask, rewritten"),
                          _rec("assistant", "a3", "u3", "new-branch reply")])
        km._rewind_hold_set(SID, self.CUT, "a2")
        km._on_rewind_resolved(SID, "spent")
        self.assertNotIn(self.doomed, km.jd.load_goals(SID)["nodes"], "the take archives")
        self.assertIn(self.doomed, km.jd.load_goal_archive(SID)["nodes"])

    def test_the_hold_view_re_points_a_hidden_focus_so_needs_you_floors_still_land(self):
        # build_feed's perm/api-error/judge-auth floors walk from lastNode and require it to land
        # in nodes — a focus hidden by the hold made every floor silently no-op for the whole
        # window (the frozen-board shape the jauth floor exists to prevent). The view re-points at
        # the newest survivor, the same move the take itself makes.
        self.assertEqual(km.jd.load_goals(SID).get("lastNode"), self.doomed,
                         "premise: the latest placement's top is the doomed card")
        km._rewind_hold_set(SID, self.CUT, "leaf-at-arm")
        view = km._feed_goals(SID)
        self.assertEqual(view.get("lastNode"), self.survivor, "the view's focus re-points")
        self.assertIn(view["lastNode"], view["nodes"], "…so a floor walk lands on a visible card")
        self.assertEqual(km.jd.load_goals(SID).get("lastNode"), self.doomed,
                         "the LIVE store's focus is untouched while the rewind is pending")

    def test_the_hold_view_re_rolls_a_parents_column_when_its_blocker_hides(self):
        # a pre-cut top whose ONLY blocker is a post-cut sub must not sit in needs-you — presenting
        # an ask the user just deleted — for the whole pending window (unbounded on a bare delete).
        # The take re-rolls for exactly this reason (archive_goal_nodes); the view must serve the
        # same columns. Conversely a top blocked by a PRE-cut sub keeps its column.
        jd = km.jd
        s = jd.load_goals(SID)
        jd.apply_plan(s, "s3", self.T0 + 5, [{"do": "mint", "why": "x", "text": "Second survivor"}],
                      jd.open_menu(s))                                    # g3, pre-cut top
        jd.apply_plan(s, "s4", self.T0 + 8, [{"do": "sub", "why": "x", "under": 2,
                                              "text": "early sub"}], jd.open_menu(s))   # g4 under g3
        jd.apply_plan(s, "s5", self.T0 + 120, [{"do": "sub", "why": "x", "under": 1,
                                                "text": "late sub"}], jd.open_menu(s))  # g5 under g1
        menu = jd.open_menu(s)                          # tree order: g1, g5(sub), g3, g4(sub), g2
        jd.apply_plan(s, "s6", self.T0 + 130, [{"do": "block", "why": "owed", "goal": 2},
                                               {"do": "block", "why": "owed", "goal": 4}], menu)
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        second = "%s:g3" % SID
        self.assertEqual(jd.load_goals(SID)["status"][self.survivor], "blocked", "premise")
        self.assertEqual(jd.load_goals(SID)["status"][second], "blocked", "premise")
        km._rewind_hold_set(SID, self.CUT, "leaf-at-arm")
        view = km._feed_goals(SID)
        self.assertNotIn("%s:g5" % SID, view["nodes"], "the post-cut blocker hides")
        self.assertEqual(view["status"].get(self.survivor), "working",
                         "…and its parent's column re-rolls to what the take will produce")
        self.assertEqual(view["status"].get(second), "blocked",
                         "a top blocked by a PRE-cut sub keeps its column")
        self.assertEqual(jd.load_goals(SID)["status"][self.survivor], "blocked",
                         "the LIVE store is untouched while the rewind is pending")

    def test_build_session_serves_the_hold_filtered_store(self):
        # the feed was NOT the only goal-store surface: the session pane's ledger tree (and the
        # tab-hover recents derived from it) read jd.load_goals raw and kept showing the doomed
        # asks for the whole armed window — unbounded on a bare delete
        src = inspect.getsource(km.build_session)
        self.assertIn("gstore = _apply_rewind_hold(sid, jd.load_goals(sid))", src)

    def test_the_boot_pass_resolves_a_hold_the_transcript_moved_past_out_of_band(self):
        # bare rollback armed, kernel dies, the user continues the session CLI-natively: the OLD
        # branch grows past the recorded leaf and nothing ever consumes the reg flag. Raw flag
        # presence kept the hold latched forever — cards hidden with NO future resolving event
        # while the leaf-verified pending_cut let the chat render the full tail. The boot pass
        # keys on the backend's leaf-verified probe and resolves through the spent discriminator.
        self._transcript([_rec("user", "u1", None, "first ask"),
                          _rec("assistant", "a1", "u1", "first reply"),
                          _rec("user", "u2", "a1", "second ask"),
                          _rec("assistant", "a2", "u2", "second reply"),
                          _rec("user", "u3", "a2", "continues in a terminal"),
                          _rec("assistant", "a3", "u3", "out-of-band reply")])
        km._rewind_hold_set(SID, self.CUT, "a2")
        class ArmedButSpent:                            # the reg still carries the flag…
            def rewind_flags(self, sid):
                return ("a1", "a2", True)
            def rewind_pending(self, sid):              # …but the transcript moved past the leaf
                return False
        saved_sdk = km._sdk
        km._sdk = lambda: ArmedButSpent()
        try:
            km._rewind_holds_boot()
        finally:
            km._sdk = saved_sdk
        self.assertIn(self.doomed, km.jd.load_goals(SID)["nodes"],
                      "the old branch grew past the arm — restored, never latched forever")
        self.assertIsNone(km._rewind_hold_get(SID), "the hold resolved at boot")
        # while a GENUINELY pending rewind (leaf unchanged — disposition "apply") stays latched
        km._rewind_hold_set(SID, self.CUT, "a2")
        class StillPending:
            def rewind_pending(self, sid):
                return True
        km._sdk = lambda: StillPending()
        try:
            km._rewind_holds_boot()
        finally:
            km._sdk = saved_sdk
        self.assertIsNotNone(km._rewind_hold_get(SID), "a verified-pending hold stays latched")
        km._rewind_hold_clear(SID)

    def test_an_unresolvable_cut_time_is_loud_never_a_silent_no_sweep(self):
        # item 5: cut_t=None used to skip the sweep with NO log — contra the fail-loudly rule
        km._arm_rewind_hold(object(), SID, None)
        self.assertIn("revert-skipped", km.jd.ERRORS.read_text(), "an error row names the skip")
        self.assertIsNone(km._rewind_hold_get(SID), "no hold is latched on an unknowable cut")

    def test_the_one_time_migration_runs_once_and_marks_itself_done(self):
        # the boot migration cleans the pre-fix residue (dead-branch orphans on dormant sessions the
        # cadence-riding reconciliation never revisits) exactly once, marker-gated in state
        calls = []
        saved = km.jd.run_rewound_reconcile
        km.jd.run_rewound_reconcile = lambda **kw: calls.append(kw) or (3, 0)
        try:
            km._rewind_migration_bg()
            km._rewind_migration_bg()
        finally:
            km.jd.run_rewound_reconcile = saved
        self.assertEqual(len(calls), 1, "the second boot found the marker and did nothing")
        self.assertGreater(calls[0].get("window") or 0, 86400 * 365,
                           "migration discovery reaches far past the 48h caption horizon")
        marker = km.jd.STATE / "rewind-reconcile-migration-v2.done"
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(marker.read_text()).get("archived"), 3)

    def test_the_migration_marker_waits_for_a_zero_failure_pass(self):
        # "returned" is not "succeeded": per-session errors are swallowed loudly inside the pass,
        # and a marker written over a failed DORMANT session skips its orphans forever (steady-state
        # discovery never reaches it, and the marker blocks the wide re-run). A dirty pass leaves
        # the marker unwritten; the clean retry writes it.
        saved = km.jd.run_rewound_reconcile
        marker = km.jd.STATE / "rewind-reconcile-migration-v2.done"
        try:
            km.jd.run_rewound_reconcile = lambda **kw: (2, 1)      # one session failed this boot
            km._rewind_migration_bg()
            self.assertFalse(marker.exists(), "a dirty pass writes no marker — retry next boot")
            km.jd.run_rewound_reconcile = lambda **kw: (1, 0)      # the retry comes back clean
            km._rewind_migration_bg()
            self.assertTrue(marker.exists(), "the clean pass marks itself done")
        finally:
            km.jd.run_rewound_reconcile = saved

    def test_the_boot_pass_resolves_a_hold_whose_event_fired_while_down(self):
        # kernel restart mid-window: the take landed, no kernel was up to hear the event — boot
        # resolves through the same discriminator instead of leaving the hold latched forever
        self._transcript([_rec("user", "u1", None, "first ask"),
                          _rec("assistant", "a1", "u1", "first reply"),
                          _rec("user", "u2", "a1", "second ask"),
                          _rec("assistant", "a2", "u2", "second reply"),
                          _rec("user", "u3", "a1", "second ask, rewritten"),
                          _rec("assistant", "a3", "u3", "new-branch reply")])
        km._rewind_hold_set(SID, self.CUT, "a2")
        saved_sdk = km._sdk
        km._sdk = lambda: None                         # no backend → nothing reports the rewind pending
        try:
            km._rewind_holds_boot()
        finally:
            km._sdk = saved_sdk
        self.assertNotIn(self.doomed, km.jd.load_goals(SID)["nodes"])
        self.assertIsNone(km._rewind_hold_get(SID))


class RewindKeptLookupEconomy(unittest.TestCase):
    """_rewind_kept_uuids runs on EVERY feed/chat build of a held session, for the whole armed
    window — unbounded on a bare delete. Three properties pinned here: the kept walk memoizes on
    the exact inputs that can change its answer (candidate-file stats, states stat, pending cut)
    and re-walks the moment any of them moves; a still-LIVE session older than the 48h caption
    window resolves through discover's cached wide walk instead of failing every build (a bare
    delete freezes the transcript mtime at arm time, so a long-armed hold guarantees the 48h
    miss); and a lookup that does fail is loud once per armed hold, never once per build (the
    pre-fix shape appended one undeduplicated judge-errors row per ~5s rebuild, ~17k rows/day,
    while the view silently widened to the bare-t hide)."""

    T0 = 1781100000
    CUT = T0 + 50

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self._saved_state = km.jd.STATE
        (self.td / "state").mkdir()
        km.jd._rebind_state(self.td / "state")
        km._rewind_holds[0] = None
        self._saved_sessions = km._sessions
        self._saved_discover = km.jd.discover
        self._saved_cm = km.em.chain_membership
        self._saved_sdk = km._sdk
        km._rewind_kept_memo.clear()
        km._rewind_kept_err.clear()
        jd = km.jd
        s = {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {},
             "placementsV": jd.PLACEMENTS_V}
        jd.apply_plan(s, "s1", self.T0, [{"do": "mint", "why": "x", "text": "Pre-cut survivor"}], [])
        jd.apply_plan(s, "s2", self.T0 + 100, [{"do": "mint", "why": "x", "text": "Doomed ask"}],
                      jd.open_menu(s))
        jd.apply_plan(s, "s3", self.CUT + 30, [{"do": "mint", "why": "x", "text": "Fresh ask"}],
                      jd.open_menu(s), prompt_uuid="u3")   # kept-chain anchor, born in range
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        self.survivor, self.doomed, self.fresh = ("%s:g1" % SID, "%s:g2" % SID, "%s:g3" % SID)

    def tearDown(self):
        km._sdk = self._saved_sdk
        km.em.chain_membership = self._saved_cm
        km.jd.discover = self._saved_discover
        km._sessions = self._saved_sessions
        km._rewind_holds[0] = None
        km._rewind_kept_memo.clear()
        km._rewind_kept_err.clear()
        km.jd._rebind_state(self._saved_state)

    def _transcript(self, register=True):
        """u2/a2 rewound away (u3 branches from a1): kept = u1,a1,u3,a3."""
        p = self.td / (SID + ".jsonl")
        with open(p, "w") as f:
            for r in [_rec("user", "u1", None, "first ask"),
                      _rec("assistant", "a1", "u1", "first reply"),
                      _rec("user", "u2", "a1", "second ask"),
                      _rec("assistant", "a2", "u2", "second reply"),
                      _rec("user", "u3", "a1", "second ask, rewritten"),
                      _rec("assistant", "a3", "u3", "new-branch reply")]:
                f.write(json.dumps(r) + "\n")
        if register:
            km._sessions = lambda now: [{"sid": SID, "path": str(p)}]
        return str(p)

    def _count_walks(self):
        calls, real = [], self._saved_cm
        def counting(*a, **k):
            calls.append(1)
            return real(*a, **k)
        km.em.chain_membership = counting
        return calls

    def test_the_kept_walk_memoizes_on_the_fileset_and_busts_on_change(self):
        p = self._transcript()
        km._rewind_hold_set(SID, self.CUT, "a2")
        calls = self._count_walks()
        feed = km._feed_goals(SID)
        self.assertIn(self.fresh, feed["nodes"], "premise: the kept exemption is live")
        self.assertNotIn(self.doomed, feed["nodes"])
        km._feed_goals(SID)
        self.assertEqual(len(calls), 1, "an unchanged transcript is walked once, not once per build")
        with open(p, "a") as f:                        # a record lands → the stat moves
            f.write(json.dumps(_rec("user", "u4", "a3", "more work")) + "\n")
        km._feed_goals(SID)
        self.assertEqual(len(calls), 2, "a transcript change is a new world — re-walk")
        km._feed_goals(SID)
        self.assertEqual(len(calls), 2, "…and the new answer memoizes in turn")
        km._rewind_hold_clear(SID)
        self.assertNotIn(str(SID), km._rewind_kept_memo, "the memo dies with the hold")

    def test_a_changed_pending_cut_busts_the_memo(self):
        self._transcript()
        km._rewind_hold_set(SID, self.CUT, "a2")
        cut = [""]
        class Cutter:
            def pending_cut(self, sid):
                return cut[0]
        km._sdk = lambda: Cutter()
        calls = self._count_walks()
        km._feed_goals(SID)
        km._feed_goals(SID)
        self.assertEqual(len(calls), 1)
        cut[0] = "a1"                                  # the cut changes the parse with NO file change
        km._feed_goals(SID)
        self.assertEqual(len(calls), 2, "arming/clearing the cut must bust the memo (the _parse lesson)")

    def test_a_failing_lookup_is_loud_once_per_hold_and_never_cached(self):
        km._sessions = lambda now: []                  # no transcript anywhere:
        km.jd.discover = lambda now, window=None, forks=True: []   # 48h set AND wide walk miss
        km._rewind_hold_set(SID, self.CUT, "a2")
        feed = km._feed_goals(SID)
        self.assertNotIn(self.doomed, feed["nodes"], "the hide degrades to t-keyed, never to nothing")
        km._feed_goals(SID)
        km._feed_goals(SID)
        self.assertEqual(km.jd.ERRORS.read_text().count("rewind-kept"), 1,
                         "three builds, one row — loud once per armed hold, not per build")
        self.assertNotIn(str(SID), km._rewind_kept_memo, "a failure is never memoized")
        km._rewind_hold_clear(SID)
        km._rewind_hold_set(SID, self.CUT, "a2")       # a NEW hold is a fresh complaint
        km._feed_goals(SID)
        self.assertEqual(km.jd.ERRORS.read_text().count("rewind-kept"), 2)

    def test_a_live_session_older_than_the_caption_window_keeps_its_kept_exemption(self):
        # the 48h set misses the sid while it is still live on every surface (DEATH_BACKFILL wide
        # walk keeps it visible there) — pre-fix the kept lookup failed on EVERY build of such a
        # session: a deterministic silent widening to the bare-t hide (the fresh kept-chain card
        # vanished) plus unbounded log growth. Liveness owns visibility; age owns nothing.
        p = self._transcript(register=False)
        km._sessions = lambda now: []                  # idle past the caption window
        km.jd.discover = (lambda now, window=None, forks=True:
                          [(SID, Path(p), SID, "web")] if window else [])
        km._rewind_hold_set(SID, self.CUT, "a2")
        feed = km._feed_goals(SID)
        self.assertIn(self.fresh, feed["nodes"], "the kept-chain card stays visible past 48h")
        self.assertNotIn(self.doomed, feed["nodes"], "…while the doomed card still hides")
        errs = km.jd.ERRORS.read_text() if km.jd.ERRORS.exists() else ""
        self.assertNotIn("rewind-kept", errs, "no degrade row — the lookup simply succeeds")


if __name__ == "__main__":
    unittest.main()
