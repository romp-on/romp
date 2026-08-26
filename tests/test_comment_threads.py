#!/usr/bin/env python3
"""Comment threads (the user 2026-08-13): highlight a passage in a session's chat, comment on it,
and a side conversation opens there — a popover backed by a FORK of the session cut at the anchored
message, kept OFF the board (reg threadOf, no names/ entry) until "Break out" promotes it.

Covered here: the inclusive cut-target resolution, the thread-fork's invisibility contract (no
names/, skipped by live_sessions, skipped by discover), the opening message's frame + its strip,
the transcript→popover projection, the create/reply/resolve/promote ops, and promotion's seeding
order. All fixtures SYNTHETIC: invented text, placeholder UUIDs.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_TMUX_AVAILABLE"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
em = SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()
sb = SourceFileLoader("romp_sdk_backend_ct", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

km._limit_hold = lambda sid: None

PARENT = "11111111-2222-3333-4444-555555555555"
THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, meta=False):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "promptSource": "typed", "message": {"role": "user", "content": text}}
    if meta:
        r["isMeta"] = True
    return r


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def tline(t, uuid, parent):
    """A tool_use-only assistant record — a spine node that is not prose."""
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": "tu_" + uuid, "name": "Read", "input": {}}]}}


def boundary(t, uuid, parent):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t),
            "uuid": uuid, "parentUuid": parent}


class CommentBase(unittest.TestCase):
    """Temp state root shared by kernel + judge (km.jd IS the loaded jd module), synthetic
    transcripts under a temp projects/ dir — the test_sdk_clear_fork conventions."""

    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()
        jd._PARSE_CACHE.clear()
        km._thread_msgs_cache.clear()
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        # never build the real SDK backend in here — the frame reads state "" from a None backend
        self._saved_sdk = km._sdk
        km._sdk = lambda: None

    def tearDown(self):
        km._sdk = self._saved_sdk
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _write(self, stem, records):
        p = self.proj / (stem + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return p

    def _parent_records(self):
        t = self.now - 600
        return [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(t + 60, "and the cap?", "u2", parent="a1"),
                aline(t + 65, "Cap the delay at two minutes.", "a2", parent="u2")]


# ── the cut target: inclusive, guarded ────────────────────────────────────────────────────────────

class CutTarget(CommentBase):
    def test_an_assistant_anchor_cuts_at_itself_not_its_ancestor(self):
        p = self._write(PARENT, self._parent_records())
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a1")
        self.assertIsNone(err)
        self.assertEqual(cut, "a1", "the thread must HOLD the highlighted answer — inclusive cut")
        self.assertGreater(cut_t, 0, "the cut record's own time rides along for the timeline square")

    def test_a_tool_row_anchor_falls_to_its_nearest_prose_ancestor(self):
        # a tool_use record is type "assistant" but NOT a clean cut — including it would leave the
        # fork's history ending on a dangling tool call; the nearest prose record carries the anchor
        t = self.now - 300
        recs = self._parent_records() + [tline(t, "tu9", "a2")]
        p = self._write(PARENT, recs)
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "tu9")
        self.assertIsNone(err)
        self.assertEqual(cut, "a2")

    def test_a_pre_compaction_anchor_is_refused_loudly(self):
        t = self.now - 600
        recs = [uline(t, "old ask", "u1"),
                aline(t + 5, "old answer", "a1", parent="u1"),
                boundary(t + 100, "b1", "a1"),
                uline(t + 200, "fresh ask", "u2", parent="b1")]
        p = self._write(PARENT, recs)
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a1")
        self.assertIsNone(cut)
        self.assertIn("compaction", err)

    def test_an_unknown_anchor_is_refused(self):
        p = self._write(PARENT, self._parent_records())
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "nope")
        self.assertIsNone(cut)
        self.assertTrue(err)

    def _seamed_session(self):
        """A machine-cut resume that forked fresh-headed: old records in the resumed-from file,
        new ones in the current file, joined only by the states resumeFork lineage row — the shape
        that read every pre-seam message as 'not in the transcript' (the user 2026-08-15)."""
        old_fsid, new_fsid = PARENT, "cccccccc-dddd-eeee-ffff-000000000000"
        t = self.now - 900
        self._write(old_fsid, [uline(t, "the pre-seam ask", "u1"),
                               aline(t + 5, "the pre-seam answer, the one worth a comment", "a1", parent="u1")])
        p = self._write(new_fsid, [uline(t + 300, "the post-seam ask", "u9", parent=None),
                                   aline(t + 305, "the post-seam answer", "a9", parent="u9")])
        sdir = jd.STATE / "states"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / (PARENT + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": old_fsid, "to": new_fsid}, "t": t + 250}) + "\n")
        return p

    def test_a_pre_seam_anchor_is_found_and_falls_back_to_a_tip_fork(self):
        p = self._seamed_session()
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a1")
        self.assertIsNone(err, "the stitched chain must FIND the message the chat shows")
        self.assertEqual(cut, "", "behind the seam the CLI can't address it — tip fork instead")
        self.assertGreater(cut_t, 0, "the anchor's own time still stamps the row")

    def test_a_post_seam_anchor_still_cuts_at_itself(self):
        p = self._seamed_session()
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a9")
        self.assertIsNone(err)
        self.assertEqual(cut, "a9")

    def test_rewind_names_the_seam_instead_of_denying_the_message_exists(self):
        p = self._seamed_session()
        cut, err = km._rewind_target(str(p), PARENT, "u1")
        self.assertIsNone(cut)
        self.assertIn("restart seam", err)


# ── the thread fork's invisibility contract ───────────────────────────────────────────────────────

class ThreadForkInvisibility(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.be.spawn("parent", self.td, sid=PARENT)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_a_thread_fork_writes_no_names_entry_and_carries_threadOf(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertFalse((Path(self.td) / "names" / THREAD).exists(),
                         "names/ is the discoverability trigger — a thread must never write it")
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertEqual(reg.get("threadOf"), PARENT)
        self.assertEqual(reg.get("forkOf"), PARENT)
        self.assertEqual(reg.get("forkAt"), "a1")

    def test_fast_mode_rides_the_fork_like_model_and_effort(self):
        # the user 2026-08-25: a comment made from an Opus-high-FAST session came up slow — the fork
        # reg seeded mode/effort/model from the parent but never fast; fast_opt reads reg["fast"] at
        # connect, so inheriting it here makes the thread fast from its first frame
        preg_path = Path(self.td) / "sdk" / (PARENT + ".json")
        preg = json.loads(preg_path.read_text())
        preg["fast"] = True
        preg_path.write_text(json.dumps(preg))
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertTrue(reg.get("fast"), "a fast parent's new thread is fast")

    def test_a_slow_parent_stays_slow(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertNotIn("fast", reg, "no inherited fast key when the parent never asked for it")

    def test_a_plain_fork_still_writes_its_names_entry(self):
        self.be.fork("fork-x", PARENT, "a1", sid=THREAD)
        self.assertTrue((Path(self.td) / "names" / THREAD).exists())

    def test_live_sessions_skips_threads_so_no_tab_is_born(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertIn(PARENT, self.be.live_sessions())
        self.assertNotIn(THREAD, self.be.live_sessions(),
                         "a thread's only surface is the parent chat's comment UI")

    def test_promote_writes_names_and_clears_threadOf(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertTrue(self.be.promote_thread(THREAD, "sidework", "#123456", "#ffffff"))
        self.assertTrue((Path(self.td) / "names" / THREAD).exists())
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertNotIn("threadOf", reg)
        self.assertEqual(reg.get("name"), "sidework")
        self.assertIn(THREAD, self.be.live_sessions(), "a promoted thread is an ordinary session")

    def test_promote_refuses_a_non_thread(self):
        self.assertFalse(self.be.promote_thread(PARENT, "nope"))

    def test_session_state_reads_a_dormant_thread_as_empty(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertEqual(self.be.session_state(THREAD), "")


class ThreadDiscoverBlindness(CommentBase):
    def test_discover_never_lists_a_thread(self):
        self._write(PARENT, self._parent_records())
        (jd.NAMES / PARENT).write_text("parent\t%s" % self.cdir)
        self._write(THREAD, self._parent_records())     # the fork's transcript exists on disk
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir,
             "lastSid": THREAD, "alive": True, "threadOf": PARENT}))
        sids = [r[0] for r in jd.discover(self.now)]
        self.assertIn(PARENT, sids)
        self.assertNotIn(THREAD, sids, "no names/ entry → no judge pass, no cards, no lane")


# ── the opening message: frame + strip ────────────────────────────────────────────────────────────

class OpeningMessage(unittest.TestCase):
    def test_frame_quotes_the_passage_and_carries_the_comment(self):
        body = km._comment_first_message("Cap the delay at two minutes.", "Why two minutes and not five?")
        self.assertIn("> Cap the delay at two minutes.", body)
        self.assertTrue(body.endswith("Why two minutes and not five?"))
        self.assertTrue(body.startswith(km._COMMENT_FRAME_HEAD))

    def test_strip_returns_exactly_the_comment(self):
        body = km._comment_first_message("line one\nline two", "The comment.\n\nWith two paragraphs.")
        self.assertEqual(km._comment_strip_frame(body), "The comment.\n\nWith two paragraphs.")

    def test_strip_leaves_an_unframed_message_alone(self):
        self.assertEqual(km._comment_strip_frame("plain reply"), "plain reply")


# ── the transcript → popover projection ───────────────────────────────────────────────────────────

class ThreadProjection(CommentBase):
    def _thread_records(self):
        """The fork copy (u1..a1, verbatim uuids) + the side conversation after the cut."""
        t = self.now - 500
        return [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(t + 100, km._comment_first_message(
                    "exponential backoff", "Why jitter at all?"), "cu1", parent="a1"),
                aline(t + 110, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                aline(t + 111, "It also spreads retries across the window.", "ca2", parent="ca1")]

    def _seed_thread(self, records=None, seen=None):
        self._write(THREAD, records or self._thread_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir,
             "lastSid": THREAD, "alive": True, "threadOf": PARENT}))
        km._save_comments(PARENT, {"threads": [
            {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "a1",
             "exact": "exponential backoff", "status": "open",
             "createdT": self.now - 400, "lastSeenT": seen if seen is not None else self.now}]})

    def test_projection_starts_after_the_cut_and_strips_the_frame(self):
        self._seed_thread()
        msgs = km._thread_messages(THREAD, "a1")
        self.assertEqual([m["who"] for m in msgs], ["you", "agent"])
        self.assertEqual(msgs[0]["text"], "Why jitter at all?",
                         "the popover shows the comment, not its quoting frame")
        self.assertNotIn("thundering", msgs[0]["text"])

    def test_consecutive_agent_records_merge_into_one_reply(self):
        self._seed_thread()
        msgs = km._thread_messages(THREAD, "a1")
        self.assertIn("thundering herds", msgs[1]["text"])
        self.assertIn("spreads retries", msgs[1]["text"])

    def test_frame_reports_unread_from_the_watermark(self):
        self._seed_thread(seen=self.now - 450)      # replies landed after the last look
        fr = km._comments_frame(PARENT)
        self.assertEqual(fr["type"], "comments")
        self.assertEqual(fr["id"], PARENT)
        self.assertTrue(fr["threads"][0]["unread"])
        km._comment_seen(PARENT, THREAD)
        km._thread_msgs_cache.clear()
        fr = km._comments_frame(PARENT)
        self.assertFalse(fr["threads"][0]["unread"])

    def test_no_store_no_frame(self):
        self.assertIsNone(km._comments_frame(PARENT))

    def test_a_promoted_thread_whose_session_ended_drops_off_the_frame(self):
        # the user 2026-08-13: broke a thread out, closed the resulting session, and the highlight
        # kept claiming "now its own session" — a dead promotion is done, not a stale pointer
        self._write(THREAD, self._thread_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "sidework", "cwd": self.cdir, "lastSid": THREAD, "alive": True}))
        km._save_comments(PARENT, {"threads": [
            {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "a1",
             "exact": "exponential backoff", "status": "promoted", "promotedName": "sidework",
             "createdT": self.now - 400, "lastSeenT": self.now}]})
        fr = km._comments_frame(PARENT)
        self.assertEqual(len(fr["threads"]), 1, "still alive — the mark and chip stay")
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "sidework", "cwd": self.cdir, "lastSid": THREAD, "alive": False}))
        fr = km._comments_frame(PARENT)
        self.assertEqual(fr["threads"], [], "ended — no highlight, no badge, nothing on the message")
        # the row survives in the store: reviving the session from the Fleet should bring it back
        self.assertEqual(len(km._load_comments(PARENT)["threads"]), 1)

    def test_pre_fork_thread_reads_nothing_not_the_parent(self):
        # Until the CLI init spends forkOf, the thread reg's lastSid points at the PARENT
        # transcript — reading it would present the parent's post-anchor turns as the thread's own.
        self._write(PARENT, self._parent_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": PARENT,
             "alive": True, "threadOf": PARENT, "forkOf": PARENT, "forkAt": "a1"}))
        self.assertEqual(km._thread_messages(THREAD, "a1"), [])

    def test_an_injected_marker_message_is_not_the_users(self):
        recs = self._thread_records()
        recs.append({"type": "user", "timestamp": iso(self.now - 80), "uuid": "inj1",
                     "parentUuid": "ca2", "promptSource": "typed",
                     "message": {"role": "user",
                                 "content": "<!-- romp-injected -->Where does this stand?"}})
        self._seed_thread(records=recs)
        msgs = km._thread_messages(THREAD, "a1")
        self.assertNotIn("Where does this stand", json.dumps(msgs))

    def test_frame_surfaces_a_thread_launch_error(self):
        self._seed_thread()
        class _Stub:
            def session_state(self, sid):
                return ""
            def launch_error(self, sid):
                return {"text": "its process couldn't start (a synthetic reason)", "at": 0, "limit": False}
        km._sdk = lambda: _Stub()
        fr = km._comments_frame(PARENT)
        self.assertIn("couldn't start", fr["threads"][0]["error"])

    def test_a_compaction_summary_record_is_not_a_message(self):
        recs = self._thread_records()
        recs.append({"type": "user", "timestamp": iso(self.now - 90), "uuid": "cs1",
                     "parentUuid": "ca2", "isCompactSummary": True,
                     "message": {"role": "user", "content": "summary of the earlier exchange"}})
        self._seed_thread(records=recs)
        msgs = km._thread_messages(THREAD, "a1")
        self.assertNotIn("summary of the earlier exchange", json.dumps(msgs))

    def test_a_large_transcript_reads_from_the_tail_window(self):
        # the copied history can be huge; the side conversation sits at the END, so the projection
        # must come out of the tail window without a full reparse — and be IDENTICAL to it
        filler = [aline(self.now - 550 + i, "filler %d " % i + "x" * 4000, "f%d" % i,
                        parent=("u1" if i == 0 else "f%d" % (i - 1))) for i in range(120)]
        t = self.now - 500
        recs = ([uline(self.now - 600, "how should the retry loop back off?", "u1")] + filler +
                [aline(t + 5, "Use exponential backoff.", "a1", parent="f119"),
                 uline(t + 100, km._comment_first_message("exponential backoff", "Why jitter?"),
                       "cu1", parent="a1"),
                 aline(t + 110, "Jitter prevents thundering herds.", "ca1", parent="cu1")])
        self._seed_thread(records=recs)
        p = self.proj / (THREAD + ".jsonl")
        self.assertGreater(p.stat().st_size, km._THREAD_TAIL_BYTES,
                           "the fixture must actually overflow the tail window")
        msgs = km._thread_messages(THREAD, "a1")
        self.assertEqual([m["who"] for m in msgs], ["you", "agent"])
        self.assertEqual(msgs[0]["text"], "Why jitter?")
        self.assertIn("thundering herds", msgs[1]["text"])


# ── the ops: create / reply / resolve / promote ───────────────────────────────────────────────────

class FakeBackend:
    """Records calls; shaped like SdkBackend where the ops touch it."""

    def __init__(self):
        self.calls = []
        self.sent = []

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="",
             model="", effort=""):
        self.calls.append(("fork", name, parent_sid, cut_uuid, sid, thread_of))
        self.forked_meta = (model, effort)
        self.forked_bg = bg
        return sid

    def connect(self, sid):
        self.calls.append(("connect", sid))
        return True

    def send(self, sid, text):
        self.calls.append(("send", sid))
        self.sent.append((sid, text))
        return True

    def resume(self, name, sid, cwd=None):
        self.calls.append(("resume", sid))
        return True

    def interrupt(self, sid):
        self.calls.append(("interrupt", sid))
        return True

    def kill(self, sid):
        self.calls.append(("kill", sid))
        return True

    def promote_thread(self, sid, name, bg="", fg=""):
        self.calls.append(("promote", sid, name))
        self.promoted_color = (bg, fg)
        return True


class CommentOps(CommentBase):
    def setUp(self):
        super().setUp()
        self.be = FakeBackend()
        self._saved_backend_for = km.Sessions.backend_for
        self._saved_ready = km._sdk_ready
        self._saved_sessions = km._sessions
        self._saved_reveal = km._reveal_chat_for
        self._saved_push_now = km._push_session_now
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._sdk_ready = lambda: True
        p = self._write(PARENT, self._parent_records())
        km._sessions = lambda now, window=None, forks=True: [
            {"sid": PARENT, "name": "parent", "path": str(p), "mtime": self.now}]
        km._reveal_chat_for = lambda client, msg: None
        km._push_session_now = lambda sid: None

    def tearDown(self):
        km.Sessions.backend_for = self._saved_backend_for
        km._sdk_ready = self._saved_ready
        km._sessions = self._saved_sessions
        km._reveal_chat_for = self._saved_reveal
        km._push_session_now = self._saved_push_now
        super().tearDown()

    def test_create_forks_a_thread_and_sends_the_framed_opener(self):
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why jitter at all?")
        self.assertIsNone(err)
        self.assertTrue(tid)
        kinds = [c[0] for c in self.be.calls]
        self.assertEqual(kinds, ["fork", "connect", "send"])
        fork = self.be.calls[0]
        self.assertEqual(fork[3], "a1", "inclusive cut — the thread holds the highlighted answer")
        self.assertEqual(fork[5], PARENT, "born as a threadOf fork, never a board session")
        self.assertTrue(self.be.sent[0][1].startswith(km._COMMENT_FRAME_HEAD))
        row = km._comment_thread(PARENT, tid)
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["anchorUuid"], "a1")

    def test_threads_autoname_by_count_and_accept_an_edited_name(self):
        _, tid1 = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        _, tid2 = km._comment_create(PARENT, "a1", "the cap", "And this?")
        self.assertEqual(km._comment_thread(PARENT, tid1)["name"], "parent-comment-1")
        self.assertEqual(km._comment_thread(PARENT, tid2)["name"], "parent-comment-2")
        self.assertEqual(self.be.calls[0][1], "parent-comment-1",
                         "the thread's reg wears the name — a break-out inherits it")
        _, tid3 = km._comment_create(PARENT, "a1", "jitter", "Named.", name="my.question")
        self.assertEqual(km._comment_thread(PARENT, tid3)["name"], "my.question")
        err, _ = km._comment_create(PARENT, "a1", "jitter", "Bad.", name="no spaces!")
        self.assertIn("letters, digits", err)
        fr = km._comments_frame(PARENT)
        self.assertEqual(fr["threads"][0]["name"], "parent-comment-1",
                         "the popover titles threads by name off the frame")

    def test_model_and_effort_picks_ride_the_fork_untouched_by_default(self):
        km._comment_create(PARENT, "a1", "exponential backoff", "Why?", model="haiku", effort="low")
        self.assertEqual(self.be.forked_meta, ("haiku", "low"))
        km._comment_create(PARENT, "a1", "the cap", "Plain.")
        self.assertEqual(self.be.forked_meta, ("", ""), "no pick = inherit; the parent is never touched")

    def test_the_comments_identity_color_rides_create_fork_row_and_frame(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?", color="#a3be8c")
        self.assertEqual(self.be.forked_bg, "#a3be8c")
        self.assertEqual(km._comment_thread(PARENT, tid)["color"], "#a3be8c")
        self.assertEqual(km._comments_frame(PARENT)["threads"][0]["color"], "#a3be8c")
        _, tid2 = km._comment_create(PARENT, "a1", "the cap", "Junk color.", color="not-a-hex")
        self.assertEqual(self.be.forked_bg, "", "a non-hex color falls to the backend's own pick")
        self.assertNotIn("color", km._comment_thread(PARENT, tid2))

    def test_a_harness_task_notification_never_renders_as_the_users_words(self):
        recs = self._parent_records()
        t = self.now - 200
        recs += [uline(t, km._comment_first_message("exponential backoff", "Why?"), "cu1", parent="a2"),
                 uline(t + 5, "<task-notification>\n<task-id>b1</task-id>\n<status>stopped</status>"
                       "\n</task-notification>", "tn1", parent="cu1"),
                 aline(t + 10, "Because herds.", "ca1", parent="tn1")]
        self._write(THREAD, recs)
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "t", "cwd": self.cdir, "lastSid": THREAD,
             "alive": True, "threadOf": PARENT}))
        msgs = km._thread_messages(THREAD, "a2")
        self.assertEqual([m["who"] for m in msgs], ["you", "agent"],
                         "the harness notice is for the AGENT, not a popover bubble")
        self.assertNotIn("task-notification", json.dumps(msgs))

    def test_a_refused_cut_leaves_no_thread_row_behind(self):
        err, tid = km._comment_create(PARENT, "missing-uuid", "text", "comment")
        self.assertTrue(err)
        self.assertIsNone(tid)
        self.assertEqual(km._load_comments(PARENT).get("threads", []), [])
        self.assertEqual(self.be.calls, [])

    def test_reply_reaches_the_thread_and_reopens_a_resolved_one(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_resolve(PARENT, tid)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "resolved")
        self.assertIn(("kill", tid), self.be.calls)
        err = km._comment_reply(PARENT, tid, "one more question")
        self.assertIsNone(err)
        self.assertIn(("resume", tid), self.be.calls, "replying IS the reopen gesture")
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "open")
        self.assertEqual(self.be.sent[-1], (tid, "one more question"))

    def test_delete_interrupts_the_inflight_reply_before_the_kill(self):
        # deleting a thread mid-generation must STOP the work, not just its cue (the user 2026-08-17)
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_delete(PARENT, tid)
        kinds = [c[0] for c in self.be.calls if c[0] in ("interrupt", "kill")]
        self.assertEqual(kinds, ["interrupt", "kill"], "cut the turn first, then shut the CLI down")

    def test_delete_removes_the_row(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_resolve(PARENT, tid)
        km._comment_delete(PARENT, tid)
        self.assertIsNone(km._comment_thread(PARENT, tid))

    def test_promote_seeds_before_names_and_floors_past_the_exchange(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        t = self.now - 200
        self._write(tid, [uline(self.now - 600, "how should the retry loop back off?", "u1"),
                          aline(self.now - 595, "Use exponential backoff.", "a1", parent="u1"),
                          uline(t, "opener", "cu1", parent="a1"),
                          aline(t + 10, "reply", "ca1", parent="cu1")])
        (jd.SDKDIR / (tid + ".json")).write_text(json.dumps(
            {"sid": tid, "name": "thread-x", "cwd": self.cdir,
             "lastSid": tid, "alive": True, "threadOf": PARENT}))
        order = []
        saved_seed = km._seed_fork_stores
        km._seed_fork_stores = lambda *a, **k: order.append("seed") or saved_seed(*a, **k)
        real_promote = self.be.promote_thread
        self.be.promote_thread = lambda *a, **k: order.append("names") or real_promote(*a, **k)
        try:
            err = km._comment_promote(PARENT, tid, "sidework")
        finally:
            km._seed_fork_stores = saved_seed
        self.assertIsNone(err)
        self.assertEqual(order, ["seed", "names"],
                         "judge seeds must land before the names/ write — the fork() contract")
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "promoted")
        self.assertEqual(km._comment_thread(PARENT, tid)["promotedName"], "sidework")
        floor = jd.episode_floor(tid)
        self.assertIsNotNone(floor)
        self.assertGreaterEqual(floor, t + 10,
                                "the floor sits at the thread's leaf — the popover exchange is settled history")

    def _promotable(self, tid):
        """The transcript + reg a thread needs before _comment_promote will touch it."""
        t = self.now - 200
        self._write(tid, [uline(t, "opener", "cu1"), aline(t + 10, "reply", "ca1", parent="cu1")])
        (jd.SDKDIR / (tid + ".json")).write_text(json.dumps(
            {"sid": tid, "name": "thread-x", "cwd": self.cdir,
             "lastSid": tid, "alive": True, "threadOf": PARENT}))

    def test_promote_keeps_the_threads_own_color(self):
        # the color the dialog suggested rides create → row → PROMOTE (the user 2026-08-19: it used
        # to be re-picked at break-out, so the session never matched the color the thread had worn)
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?", color="#F9D849")
        self._promotable(tid)
        self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"))
        self.assertEqual(self.be.promoted_color, ("#F9D849", "black"),
                         "the row's color, with the palette's readable fg — never a fresh pick")

    def test_promote_picks_fresh_only_for_a_colorless_row(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self._promotable(tid)
        self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"))
        bg, fg = self.be.promoted_color
        self.assertTrue(bg.startswith("#") and fg in ("white", "black"),
                        "a pre-color row still gets a real identity")

    def test_promote_refuses_a_bad_name(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        err = km._comment_promote(PARENT, tid, "bad name!")
        self.assertIn("letters, digits", err)

    def test_the_promoting_latch_refuses_resolve_delete_and_reply(self):
        # promote seeds for seconds on a big transcript; ops landing in that window must refuse
        # THROUGH the CAS, or a racing resolve kills the just-promoted board session
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_update(PARENT, tid, status="promoting")
        calls_before = list(self.be.calls)
        for op in (km._comment_resolve, km._comment_delete):
            err = op(PARENT, tid)
            self.assertIn("becoming its own session", err)
        err = km._comment_reply(PARENT, tid, "hello?")
        self.assertIn("becoming its own session", err)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "promoting")
        self.assertEqual(self.be.calls, calls_before, "no kill, no send — the latch holds")

    def test_a_failed_promote_reverts_the_latch(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        t = self.now - 200
        self._write(tid, [uline(t, "opener", "cu1"), aline(t + 10, "reply", "ca1", parent="cu1")])
        (jd.SDKDIR / (tid + ".json")).write_text(json.dumps(
            {"sid": tid, "name": "thread-x", "cwd": self.cdir,
             "lastSid": tid, "alive": True, "threadOf": PARENT}))
        saved = km._seed_fork_stores
        km._seed_fork_stores = lambda *a, **k: "seeding failed on purpose"
        try:
            err = km._comment_promote(PARENT, tid, "sidework")
        finally:
            km._seed_fork_stores = saved
        self.assertIn("seeding failed", err)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "open",
                         "the latch must never stick on a failed promote")

    def test_seed_fork_stores_refuses_a_vanished_cut(self):
        p = self.proj / (PARENT + ".jsonl")
        err = km._seed_fork_stores(PARENT, THREAD, str(p), "gone-uuid")
        self.assertIn("isn't in the conversation anymore", err)

    def test_resolve_refuses_a_promoted_thread_so_delete_can_never_kill_its_session(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_update(PARENT, tid, status="promoted", promotedName="sidework")
        err = km._comment_resolve(PARENT, tid)
        self.assertIn("its own session", err)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "promoted",
                         "resolve must never overwrite promoted — that hands delete a session to kill")
        km._comment_delete(PARENT, tid)                 # removing the highlight row is fine…
        self.assertNotIn(("kill", tid), self.be.calls)  # …but the promoted session is never killed
        self.assertIsNone(km._comment_thread(PARENT, tid))

    def test_a_missing_cut_shows_nothing_never_the_copied_history(self):
        self._write(THREAD, self._parent_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir,
             "lastSid": THREAD, "alive": True, "threadOf": PARENT}))
        self.assertEqual(km._thread_messages(THREAD, "not-in-transcript"), [])

    def test_ending_the_parent_sweeps_its_threads_clis(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_update(PARENT, tid, status="promoted", promotedName="kept")
        _, tid2 = km._comment_create(PARENT, "a1", "exponential backoff", "And the cap?")
        km._comment_kill_all(PARENT, self.be)
        self.assertIn(("kill", tid2), self.be.calls, "open threads die with their only surface")
        self.assertNotIn(("kill", tid), self.be.calls, "a promoted thread is a board session — untouched")

    def test_a_failed_create_kills_the_half_born_reg(self):
        self.be.send = lambda sid, text: (_ for _ in ()).throw(RuntimeError("boom"))
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self.assertIn("boom", err)
        self.assertIsNone(tid)
        self.assertEqual(km._load_comments(PARENT).get("threads", []), [])
        self.assertIn(("kill",), {c[:1] for c in self.be.calls},
                      "the forked reg/CLI must not outlive the removed row")

    def test_drive_ops_are_registered(self):
        src = (Path(BIN) / "romp-kernel").resolve().read_text()
        for op in ("commentCreate", "commentReply", "commentResolve", "commentDelete",
                   "commentSeen", "commentPromote"):
            self.assertIn('"%s"' % op, src)


class ExchangeLatchReplacedThePushCount(unittest.TestCase):
    """T102 (the user 2026-08-26): the push-count settle (settledPushes / _comment_settle_step) is
    RETIRED — it was a proxy for the real ending event, and it broke both ends: the fork-birth
    frames read all-quiet so the create-window pulse died until the CLI booted, and any stall in
    the 0→1→2 stepping parked the pulse green forever. The client's pulse is exchange-scoped now —
    latched at the send gesture, cleared by the agent's reply RECORD arriving in msgs — so the
    frame carries the exchange's records (msgs) and no per-push counter."""

    def test_the_push_count_is_gone_root_and_branch(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertNotIn("settledPushes", src.replace("settledPushes — is RETIRED", ""),
                         "no counter rides the frame (the tombstone comment is the one mention)")
        self.assertNotIn("_comment_settle_step", src)
        ui = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "comments.ts")).read()
        self.assertNotIn("settledPushes", ui)
        self.assertNotIn("SETTLE_CONFIRM_PUSHES", ui)

    def test_the_frame_still_carries_the_exchange_records_and_epoch(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('"sinceEpoch": since_ms,', src)
        self.assertIn('"msgs": msgs, "events": events', src)


if __name__ == "__main__":
    unittest.main()
