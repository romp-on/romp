#!/usr/bin/env python3
"""T289: a comment CREATE is idempotent on its own identity.

A create parked for transcript lag is retried by BOTH the kernel (the pusher's _retry_parked_creates) and
the client (its frame-keyed re-post), and a create whose ack was lost is sent again by the popover; before
this the second copy either collided on its explicit name and was refused with the create toast the user
saw, or, with the name now left to the kernel, would have minted a SECOND thread for one comment. The
kernel remembers each create it completed and answers a repeat with the SAME thread's ack; a lag-parked
create is parked once.

The identity is the client's createId when the frame carries one: the popover mints it at the send gesture
and every re-post of that gesture carries the same one, so a repeat is exactly a frame with an id the kernel
has seen. Keyed on the words instead (parent sid, anchor uuid, passage, text), the memo also swallowed a
DELIBERATE second comment in the same words on the same passage: one thread, two acks, the second name
nowhere on disk (review of the memo, 2026-09-09). A frame without a createId still keys on the words.
SYNTHETIC fixtures."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT = "aaaaaaaa-1111-2222-3333-444444444444"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


class FakeBackend:
    def __init__(self):
        self.calls = []

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="", model="", effort="", fast=""):
        self.calls.append(("fork", name)); return sid

    def connect(self, sid):
        return True

    def send(self, sid, text):
        return True

    def kill(self, sid):
        return True

    def rename(self, sid, name):
        self.calls.append(("rename", name)); return True

    def promote_thread(self, sid, name, bg="", fg=""):
        self.calls.append(("promote", name)); return True


class CreateIsIdempotent(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_msgs_cache.clear()
        # the create memos start empty as well (review, 2026-09-09): the tests reuse the same create ids
        # under one parent sid, so an id a previous test noted would name a thread id this test's fresh
        # store can hold too, and a fresh gesture here would be answered as a repeat; random thread ids
        # keep that from happening by chance today, and the clears make the order not matter. The noted
        # creates are the memo a passing test leaves behind; the in-flight set and the parked list are
        # empty at the end of every passing test, so their clears keep a test that failed part-way from
        # failing the next one too. Under the lock every writer of the three memos takes.
        with km._create_lock:
            km._recent_creates.clear(); km._inflight_creates.clear(); km._parked_creates.clear()
        self.now = int(time.time())
        cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / PARENT).write_text("web\t%s\t\t\n" % cdir)   # the names row: the refusal names the parent by it
        self.be = FakeBackend()
        self._saved_fns = (km._sdk, km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for,
                           km._push_session_now, km._live_map, km._kernel_knows)
        km._sdk = lambda: None
        km._kernel_knows = lambda sid: sid == PARENT     # the dispatcher's ownership guard is not under test
        self._saved_km_names = km.NAMES
        km.NAMES = jd.NAMES                              # the kernel's own binding of the names dir follows the rebind
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._sdk_ready = lambda: True
        km._live_map = lambda: {}
        t = self.now - 600
        p = self.proj / (PARENT + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in [
            uline(t, "how should the notes-api retry loop back off?", "u1"),
            aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1")]) + "\n")
        km._sessions = lambda now, window=None, forks=True: [
            {"sid": PARENT, "name": "web", "path": str(p), "mtime": self.now}]
        km._reveal_chat_for = lambda client, msg: None
        km._push_session_now = lambda sid: None
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s)), "app": "chat"}

    def tearDown(self):
        (km._sdk, km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for,
         km._push_session_now, km._live_map, km._kernel_knows) = self._saved_fns
        km.NAMES = self._saved_km_names
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _drive(self, msg):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            handled = km._drive(msg, self.client)
        return handled, buf.getvalue()

    def _warns(self):
        return [f["text"] for f in self.sent if f.get("type") == "warn"]

    def _create(self, text="Why jitter at all?", uuid="a1", name="", create_id=None):
        msg = {"type": "commentCreate", "id": PARENT, "uuid": uuid, "exact": "exponential backoff",
               "text": text, "name": name}
        if create_id is not None:
            msg["createId"] = create_id                 # the client's per-gesture stamp (absent from an older client)
        return self._drive(msg)

    def _fails(self):
        """The file viewer's refusals: each by its createId, never a bare warn that would fail every open box."""
        return [(f.get("createId"), f.get("text")) for f in self.sent if f.get("type") == "commentCreateFailed"]

    def _rows(self):
        return km._load_comments(PARENT).get("threads") or []

    def _acks(self):
        return [f for f in self.sent if f.get("type") == "commentCreated"]

    def _file_create(self, text="Which of these still matter?"):
        return self._drive({"type": "commentCreate", "id": PARENT, "uuid": "", "exact": "Kept gates:",
                            "text": text, "src": "~/notes/decisions.md", "createId": "c-file-1"})

    def test_a_file_passage_create_with_no_uuid_mints_a_thread_anchored_at_the_tip(self):
        """A FILE passage carries a src and no message uuid; its thread is anchored at the conversation
        tip, so the row is one the chat can open, and the opener names the file."""
        sends = []
        self.be.send = lambda sid, text: sends.append(text) or True
        self._file_create()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["anchorUuid"], "a1", "the tip, so the thread row is openable")
        self.assertEqual(rows[0]["cutUuid"], "", "still a tip fork: the whole conversation")
        self.assertEqual(rows[0]["src"], "~/notes/decisions.md")
        self.assertEqual([a["uuid"] for a in self._acks()], [""], "the ack keeps the file viewer's empty uuid")
        self.assertTrue(any(t.startswith("About this part of ~/notes/decisions.md:") for t in sends), sends)

    def test_a_file_passage_at_an_attachment_tip_anchors_at_the_last_chat_event(self):
        """The transcript's leaf is usually an attachment or system record, which no surface renders."""
        p = self.proj / (PARENT + ".jsonl")
        with p.open("a") as fh:
            fh.write(json.dumps({"type": "attachment", "timestamp": iso(self.now - 590), "uuid": "at1",
                                 "parentUuid": "a1", "attachment": {"type": "total_tokens_reminder"}}) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self._file_create()
        self.assertEqual(self._rows()[0]["anchorUuid"], "a1")

    def _boom_fork(self):
        def boom(*a, **k):
            raise RuntimeError("spawn refused")
        self.be.fork = boom

    def test_a_fork_that_dies_after_a_file_ack_is_rolled_back_and_recorded(self):
        """A file passage is acked on the durable row, ahead of the fork. A fork that then raises rolls
        the row back, re-sends the thread list without it, answers the create by its id, and records the
        file, the reason and the words; no bare warn goes out."""
        self._boom_fork()
        before = km._SDK_PROBLEM_SEQ[0]
        self._file_create()
        kinds = [f.get("type") for f in self.sent]
        self.assertEqual(kinds, ["commentCreated", "comments", "commentCreateFailed"])
        self.assertEqual(self._rows(), [], "the half-born row is rolled back")
        self.assertNotIn(self._acks()[0]["tid"], json.dumps(self.sent[1]), "the fresh frame drops the thread")
        self.assertEqual({f.get("createId") for f in self.sent if f["type"] != "comments"}, {"c-file-1"})
        self.assertEqual(km._SDK_PROBLEM_SEQ[0], before + 1)
        text = km._SDK_BOOT_PROBLEMS[-1]["text"]
        self.assertTrue(text.startswith("A comment on ~/notes/decisions.md was not started ("), text)
        self.assertIn("spawn refused", text)
        self.assertTrue(text.endswith("Your words: Which of these still matter?"), text)

    def test_a_failure_the_socket_cannot_take_is_still_recorded(self):
        self._boom_fork()

        def full(_s):
            raise OSError("client queue over its byte cap")
        self.client["send"] = full
        before = km._SDK_PROBLEM_SEQ[0]
        self._file_create()
        self.assertEqual(km._SDK_PROBLEM_SEQ[0], before + 1)

    def test_the_file_road_ack_carries_no_frame(self):
        """The thread has no registry entry yet at the early ack, so a frame would read it as unreadable."""
        self._file_create()
        self.assertEqual(self.sent[0]["type"], "commentCreated")

    def test_a_file_passage_with_no_chat_event_is_refused_before_the_row(self):
        (self.proj / (PARENT + ".jsonl")).write_text(json.dumps(
            {"type": "attachment", "timestamp": iso(self.now - 600), "uuid": "at1", "parentUuid": None,
             "attachment": {"type": "total_tokens_reminder"}}) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._built_chat.pop(PARENT, None)
        self._file_create()
        self.assertEqual(self._rows(), [])
        self.assertEqual(self._acks(), [])
        self.assertEqual(self._fails(), [("c-file-1", km.FILE_COMMENT_NO_ANCHOR)])
        self.assertEqual(self._warns(), [], "the refusal reaches its own box alone")

    def test_the_anchor_is_read_from_the_pushers_last_build(self):
        km._built_chat[PARENT] = ("sig", {"id": PARENT, "events": [{"uuid": "u1"}, {"uuid": "b7"}]}, "", None)
        self.addCleanup(km._built_chat.pop, PARENT, None)
        self._file_create()
        self.assertEqual(self._rows()[0]["anchorUuid"], "b7")

    def test_a_transcript_that_cannot_be_parsed_is_refused_in_its_own_words(self):
        km._built_chat.pop(PARENT, None)
        real = km.build_session

        def raising(*a, **k):
            raise ValueError("torn line")
        km.build_session = raising
        self.addCleanup(setattr, km, "build_session", real)
        _, log = self._file_create()
        self.assertEqual((self._rows(), self._acks()), ([], []))
        self.assertEqual(self._fails(), [("c-file-1", km.FILE_COMMENT_UNREAD)], "a parse fault is not an empty conversation")
        self.assertEqual(self._warns(), [])
        self.assertIn("file comment anchor for %s not read: ValueError: torn line" % PARENT[:8], log)

    def test_a_multi_line_comment_keeps_every_line_in_the_error_center(self):
        self._boom_fork()
        self._file_create(text="first line of the note\nsecond line in the middle\nthird line at the end")
        row = km._sdk_problem_rows()[-1]
        self.assertIn("first line of the note second line in the middle third line at the end", json.dumps(row))

    def test_a_multi_line_reason_stays_on_one_line(self):
        def boom(*a, **k):
            raise RuntimeError("spawn refused\nexit status 1")
        self.be.fork = boom
        self._file_create()
        self.assertIn("(thread not created: spawn refused exit status 1)", km._SDK_BOOT_PROBLEMS[-1]["text"])

    def test_the_recorded_words_are_capped(self):
        self._boom_fork()
        words = "w" * (km.COMMENT_FAILED_TEXT_CAP + 50)
        self._file_create(text=words)
        self.assertTrue(km._SDK_BOOT_PROBLEMS[-1]["text"].endswith("Your words: " + words[:km.COMMENT_FAILED_TEXT_CAP]))

    def test_the_parked_and_busy_replies_carry_the_create_id(self):
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="c-park"); self._create(uuid="a9", create_id="c-park")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual([n.get("createId") for n in nacks], ["c-park", "c-park"], "the lag nack and the busy nack")
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        with km._clients_lock:
            km._clients.append(self.client)
        try:
            km._retry_parked_creates()
        finally:
            with km._clients_lock:
                km._clients.remove(self.client)
        self.assertEqual([a.get("createId") for a in self._acks()], ["c-park"], "the parked create's ack")

    def test_every_problem_gets_its_own_seq_past_the_ring(self):
        seqs, counts = [], []
        for i in range(22):
            km._sdk_problem("problem %d" % i)
            seqs.append(km._SDK_BOOT_PROBLEMS[-1]["seq"])
            counts.append(km._sdk_problem_count())
        self.assertEqual(len(set(seqs)), 22)
        self.assertEqual([b - a for a, b in zip(counts, counts[1:])], [1] * 21, "the count moves by one per problem past the ring")

    def test_a_chat_comment_is_acked_only_after_its_fork(self):
        """The chat popover keeps its draft on a failure, so it must never see an ack the fork undoes."""
        self._boom_fork()
        self._create(create_id="c-chat-1")
        self.assertEqual(self._acks(), [])
        self.assertIn("commentCreateFailed", [f.get("type") for f in self.sent])

    def test_an_ack_the_socket_cannot_take_still_starts_the_thread(self):
        """A send that raises inside the early ack leaves the row to its fork, never orphaned."""
        def full(_s):
            raise OSError("client queue over its byte cap")
        self.client["send"] = full
        self._file_create()
        self.assertTrue(any(call[0] == "fork" for call in self.be.calls), "the fork still ran")
        self.assertEqual(len(self._rows()), 1)

    def test_the_same_words_on_two_files_are_two_threads(self):
        """A frame with no createId keys on its words and the file they came from."""
        for src in ("~/notes/a.md", "~/notes/b.md"):
            self._drive({"type": "commentCreate", "id": PARENT, "uuid": "", "exact": "Kept gates:",
                         "text": "Still true?", "src": src})
        self.assertEqual(sorted(r["src"] for r in self._rows()), ["~/notes/a.md", "~/notes/b.md"])

    def test_a_refusal_before_the_row_records_no_problem(self):
        """A refusal the user provoked is answered on the socket alone; nothing was acked, so nothing
        is recorded as a failed start."""
        before = km._SDK_PROBLEM_SEQ[0]
        self._create(name="no spaces!")
        self.assertEqual(self._acks(), [])
        self.assertEqual(km._SDK_PROBLEM_SEQ[0], before)

    def test_the_same_create_twice_yields_one_thread_and_two_acks_naming_it(self):
        self._create(); self._create()
        self.assertEqual(len(self._rows()), 1, "one comment, one thread")
        acks = self._acks()
        self.assertEqual(len(acks), 2, "the repeat is answered, so the popover adopts the thread")
        self.assertEqual(acks[0]["tid"], acks[1]["tid"])
        self.assertEqual(self._warns(), [])

    def test_a_different_comment_on_the_same_passage_is_a_second_thread(self):
        self._create("Why jitter at all?"); self._create("And the cap?")
        self.assertEqual(len(self._rows()), 2)
        self.assertEqual(len({a["tid"] for a in self._acks()}), 2)

    def test_a_lag_parked_create_is_parked_once(self):
        # the anchor is not in the transcript yet: the create parks, the client hears a transient nack and
        # re-posts on the next frame — the park must not double
        km._parked_creates.clear()
        self._create(uuid="a9"); self._create(uuid="a9")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 2)
        self.assertTrue(all(n["transient"] for n in nacks))
        self.assertEqual(len(km._parked_creates), 1, "one parked copy for one create")
        km._parked_creates.clear()

    def test_a_parked_create_that_landed_answers_the_clients_repost_with_the_same_thread(self):
        km._parked_creates.clear()
        self._create(uuid="a9")
        self.assertEqual(len(km._parked_creates), 1)
        # the transcript catches up: the pusher's retry lands the thread
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        # the client's own re-post of the same create arrives a beat later: the same thread, no second one
        self._create(uuid="a9")
        self.assertEqual(len(self._rows()), 1, "the repeat is the same comment")
        acks = self._acks()
        self.assertTrue(acks and acks[-1]["tid"] == rows[0]["tid"], acks)
        self.assertEqual(self._warns(), [])

    def test_a_repost_arriving_while_the_pushers_retry_creates_is_not_a_second_thread(self):
        # THE RACE (review, 2026-09-09): the pusher sends the chat frame before it retries parked creates,
        # the client re-posts the create on that frame, and the re-post lands on a WS thread while the
        # pusher's own create is in flight — neither copy is noted yet, so both minted. The identity must
        # be reserved before the create runs, on both doors, and the pusher must consult the memo.
        import threading
        km._parked_creates.clear()
        self._create(uuid="a9")                       # lag-parked (the anchor is not on disk yet)
        self.assertEqual(len(km._parked_creates), 1)
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        real = km._comment_create
        seen = {"n": 0, "nested": None}

        def racing(*a, **k):
            seen["n"] += 1
            if seen["n"] == 1:
                # the pusher's create is in flight: the client's re-post arrives on another thread NOW
                before = len(self.sent)
                t = threading.Thread(target=lambda: self._create(uuid="a9"))
                t.start(); t.join(10)
                seen["nested"] = self.sent[before:]
            return real(*a, **k)
        km._comment_create = racing
        try:
            km._retry_parked_creates()
        finally:
            km._comment_create = real
        self.assertEqual(seen["n"], 1, "the re-post never reached a second create: %r" % (seen["nested"],))
        self.assertEqual(len(self._rows()), 1, "one comment, one thread")
        kinds = [f.get("type") for f in (seen["nested"] or [])]
        self.assertTrue("commentCreateFailed" in kinds or "commentCreated" in kinds,
                        "the re-post is answered while the pusher's copy is in flight (a typed transient nack, or the ack): %r" % kinds)
        self.assertEqual(km._parked_creates, [])
        self.assertEqual(self._warns(), [])

    def test_a_repeat_after_the_thread_was_resolved_is_a_new_comment_not_a_dropped_one(self):
        # a resolved (or merged, or promoted) thread cannot take a message: answering a same-worded comment
        # with its ack would drop the user's words silently (review, 2026-09-09)
        self._create()
        tid = self._acks()[0]["tid"]
        self.assertIsNone(km._comment_resolve(PARENT, tid))
        self._create()
        rows = self._rows()
        self.assertEqual(len(rows), 2, "a second, open thread for the re-asked comment")
        self.assertEqual(self._warns(), [])

    def test_a_fresh_gesture_in_the_same_words_on_the_same_passage_is_a_second_thread(self):
        # the user posts a comment, takes the ack, and posts the SAME words on the same passage again under
        # another name: two gestures, two comments. Keyed on the words alone the memo answered the second
        # with the first thread's ack, and the second name was nowhere on disk (review, 2026-09-09).
        self._create(name="first-look", create_id="g-1111")
        first = self._acks()[-1]["tid"]
        self._create(name="second-look", create_id="g-2222")
        rows = self._rows()
        self.assertEqual(len(rows), 2, "two gestures are two comments, whatever their words")
        self.assertEqual(sorted(r["name"] for r in rows), ["first-look", "second-look"], "both names on disk")
        acks = self._acks()
        self.assertEqual(len(acks), 2)
        self.assertNotEqual(acks[1]["tid"], first, "the second ack names the second thread")
        self.assertEqual(self._warns(), [])

    def test_a_retried_frame_with_the_same_create_id_is_one_thread_answered_twice(self):
        # the re-post side of the same rule: a frame the client sends again (a lost ack, a lag retry) carries
        # the id it minted at the gesture, and the kernel answers it with the thread that gesture made
        self._create(create_id="g-1111"); self._create(create_id="g-1111")
        self.assertEqual(len(self._rows()), 1, "one gesture, one thread")
        acks = self._acks()
        self.assertEqual(len(acks), 2, "the repeat is answered, so the popover adopts the thread")
        self.assertEqual(acks[0]["tid"], acks[1]["tid"])
        self.assertEqual(self._warns(), [])

    def test_a_fresh_gesture_after_a_parked_create_landed_is_a_second_thread_and_its_repost_is_not(self):
        # the parked copy carries the gesture's id through the pusher's retry: the client's re-post of THAT
        # gesture is the same comment, and a new gesture in the same words a moment later is a new one
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="g-1111")
        self.assertEqual(len(km._parked_creates), 1)
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [])
        landed = self._rows()
        self.assertEqual(len(landed), 1)
        self._create(uuid="a9", create_id="g-1111")   # the client's own re-post of the parked gesture
        self.assertEqual(len(self._rows()), 1, "the re-post is the same comment")
        self.assertEqual(self._acks()[-1]["tid"], landed[0]["tid"])
        self._create(uuid="a9", create_id="g-2222")   # a new gesture, same words, same passage
        rows = self._rows()
        self.assertEqual(len(rows), 2, "a fresh gesture is a second thread")
        self.assertNotEqual(self._acks()[-1]["tid"], landed[0]["tid"])
        self.assertEqual(self._warns(), [])

    def test_two_gestures_in_the_same_words_are_both_parked_while_the_anchor_lags(self):
        # two comments posted before the anchor reached the transcript park separately and land as two
        # threads; the same gesture posted twice still parks once
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="g-1111"); self._create(uuid="a9", create_id="g-1111")
        self.assertEqual(len(km._parked_creates), 1, "one parked copy for one gesture")
        self._create(uuid="a9", create_id="g-2222")
        self.assertEqual(len(km._parked_creates), 2, "a second gesture is its own parked create")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 3)
        self.assertTrue(all(n["transient"] for n in nacks))
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [])
        self.assertEqual(len(self._rows()), 2, "both gestures landed as threads")
        self.assertEqual(self._warns(), [])

    def test_a_frame_without_a_create_id_still_keys_on_the_words(self):
        # an older client sends no id: the words identity stands for it, and a stamped frame in the same
        # words is not answered from the words memo (nor the other way round)
        self._create(); self._create()
        self.assertEqual(len(self._rows()), 1)
        self._create(create_id="g-1111")
        self.assertEqual(len(self._rows()), 2, "a stamped gesture is not a repeat of an unstamped one")
        self._create()
        self.assertEqual(len(self._rows()), 2, "and the unstamped repeat still answers from its own memo")
        self.assertEqual(self._warns(), [])

    def test_a_repost_of_a_parked_gesture_is_refused_at_the_door_before_the_create_runs(self):
        # the busy nack for a re-post of a parked gesture comes from the reservation, which finds the parked
        # copy by the gesture's id, so the create never runs a second time (compared by its words instead, a
        # stamped re-post would go free, re-run the create into the lag and rely on the park-once check)
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="g-1111")
        self.assertEqual(len(km._parked_creates), 1)
        real = km._comment_create
        calls = []

        def counting(*a, **k):
            calls.append(a)
            return real(*a, **k)
        km._comment_create = counting
        try:
            self._create(uuid="a9", create_id="g-1111")
        finally:
            km._comment_create = real
        self.assertEqual(calls, [], "the re-post is busy at the door; no second create")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 2)
        self.assertTrue(all(n["transient"] for n in nacks))
        self.assertEqual(len(km._parked_creates), 1)
        km._parked_creates.clear()

    def test_a_repost_landing_between_the_doors_release_and_its_park_is_parked_once(self):
        # the door releases its reservation before it parks, and the park-once check covers that window: a
        # re-post of the same gesture on another server thread lands in it, runs the create into the lag and
        # parks first; this door then finds that parked copy by the gesture's id and parks no twin (the other
        # door runs whole inside the window here, in place of a second thread, so the order is exact)
        km._parked_creates.clear()
        real = km._release_create
        raced = []

        def release_then_race(key):
            real(key)
            if not raced:
                raced.append(key)
                self._create(uuid="a9", create_id="g-1111")
        km._release_create = release_then_race
        try:
            self._create(uuid="a9", create_id="g-1111")
        finally:
            km._release_create = real
        self.assertEqual(len(raced), 1)
        self.assertEqual(len(km._parked_creates), 1, "one parked copy for one gesture, whichever door parked it")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 2)
        self.assertTrue(all(n["transient"] for n in nacks))
        km._parked_creates.clear()

    def test_an_answered_repeat_is_said_in_the_kernel_log(self):
        self._create()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            km._drive({"type": "commentCreate", "id": PARENT, "uuid": "a1", "exact": "exponential backoff",
                       "text": "Why jitter at all?", "name": ""}, self.client)
        self.assertIn("comment create repeated", buf.getvalue(), "a collapse is visible, never silent")


if __name__ == "__main__":
    unittest.main()
