#!/usr/bin/env python3
"""A text fed into a running turn is TAKEN when the CLI records its queue removal as absorbed_mid_turn, even with no
queued_command attachment; a removal for any other reason releases the hold but is never a delivery.

What happened (2026-10-09): romp feeds a running session one queued text at a time and holds the rest until it sees
the CLI take the current one (SdkSession._untaken, released by _untaken_taken). For a text fed mid-turn the take was
read only from the queued_command attachment the CLI writes when it splices the text into the turn. On one CLI
version some mid-turn takes left only the CLI's queue bookkeeping record, a queue-operation remove with reason
absorbed_mid_turn, and no attachment, so the hold never saw the take: busy sessions whose turns never end kept every
later text queued for over an hour.

The CLI writes a queue record's text only for a string-valued item: the enqueue of a text romp feeds carries the text,
its remove carries none. So the fix reads the removal by queue ORDER from a per-session ledger of the CLI's queue
records (sdk_backend._queue_ledger_fold: content-less removes resolve the oldest pending item that is not a CLI notice,
whose own removals keep their text), and by text where the removal names it (_queue_removal, through _text_take):
  * the previous text's late removal resolves the previous text, never the one now held, and a CLI notice queued ahead
    of the fed text does not take its content-less removal;
  * an absorbed_mid_turn removal naming the fed text is a take: the hold releases, the next text feeds into the same
    turn, the echoes it speaks for land by id (no record carries their text for the by-text retire), and it is
    counted (feed_take_counts);
  * a joined text (peer mail and tagged sends fed as one) matches its removal the same way, and every part's echo
    lands;
  * a removal for another reason releases the hold, since the text left the CLI's queue, and is never a delivery. A
    dropped_by_hook removal is not always a hook's decision: the CLI writes the same removal when its 540 s hook limit
    runs out or the session host reconnects before the kernel answers (2026-10-09: 78 drops in a day, 9 of 16 traced
    texts never came back). So a dropped_by_hook text romp's own prompt gate did not block goes back to the head of the
    queue and is fed again, at most three times per item (the fourth drop falls back), and never once its record
    landed; a text the gate blocked, or one whose re-sends are spent, is reported as removed unread: its echo is flagged
    never delivered (dropped and refused, like the prompt gate's refusal), it is not re-fed, its mail gets no read
    stamp, and one problem line names the session;
  * a remove with no reason (an older CLI's discard) or for another text releases nothing;
  * the queued_command attachment still releases the hold as before, and the boot re-delivery guard (_text_landed)
    reads an absorbed removal as a landing, so a restart cannot re-feed a text the turn already read.
The REAL _amain runs (its inputs() closure and _on_message) against the stand-in SDK client of
test_queued_sends_not_fused. SYNTHETIC fixtures only: invented text, placeholder uuids, the TESTHOST hostname."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from romp_load import load_source          # noqa: E402
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
import test_queued_sends_not_fused as _q   # noqa: E402  (borrowed harness: the stand-in SDK client and helpers)

sb = _q.sb
pm = load_source("romp_postal_absorbedtake", os.path.join(_q.BIN, "romp-postal-service"))
SID, FSID = _q.SID, _q.FSID
PEER = "22222222-3333-4444-5555-777777777777"
SEP = "\n\n"


def _mid(n):
    return "17100%05d.%06d.TESTHOST" % (n, n)


def _banner(mid, body="the index rebuild finished; your turn"):
    return pm.format_push([{"from": "api", "from_id": PEER, "body": body + " (" + mid[:10] + ")", "id": mid, "date": ""}])


def _tagged(body, label="watch"):
    return body + "\n\n<!-- romp-tag: " + label + " -->"


def _enqueue_op(text):
    """The CLI's queue bookkeeping when it receives a fed text: no uuid, the text as `content`."""
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": _q._iso(time.time()),
            "sessionId": FSID, "content": text}


def _remove_op(text, reason="absorbed_mid_turn"):
    """The CLI's queue bookkeeping when the text leaves its queue. reason None writes the older, reason-less shape."""
    rec = {"type": "queue-operation", "operation": "remove", "timestamp": _q._iso(time.time()), "sessionId": FSID}
    if text is not None:
        rec["content"] = text
    if reason is not None:
        rec["reason"] = reason
    return rec


def _splice(text):
    return {"type": "attachment", "uuid": "11111111-2222-3333-4444-" + ("%012d" % (abs(hash(text)) % 10 ** 12)),
            "timestamp": _q._iso(time.time()), "attachment": {"type": "queued_command", "prompt": text}}


class TakeOnTheCLIsQueueRemoval(unittest.TestCase):
    """End to end through the real inputs() closure and _on_message, with real sends (echo and id)."""
    _H = _q.OneFedTextAtATime   # the borrowed harness, bound in the class body so it is not collected a second time here
    _Client, _Options = _H._Client, _H._Options
    setUp, tearDown = _H.setUp, _H.tearDown
    _wait, _settle, _push, _uid = _H._wait, _H._settle, _H._push, _H._uid
    _init, _assistant, _result_frame = _H._init, _H._assistant, _H._result_frame
    _user_record, _first_turn, _transcript, _append = _H._user_record, _H._first_turn, _H._transcript, _H._append

    def _send(self, text, user=False):
        """A real send (SdkBackend.send): the queued copy and its echo share one minted id, which this returns."""
        self.assertTrue(self.be.send(SID, text, user=user))
        hit = [k for k, a in (self.be._live.get(SID) or {}).items() if a.get("_echo_text") == text]
        self.assertTrue(hit, "the send's echo, stashed under its id")
        return hit[-1]

    def _echo(self, qid):
        return (self.be._live.get(SID) or {}).get(qid)

    def _reports(self):
        reports = []
        self.be.postal_taken = lambda sid, mids: reports.append((sid, list(mids)))
        return reports

    def _counts(self):
        return self.be.feed_take_counts()

    def test_an_absorbed_removal_with_no_attachment_releases_the_hold_and_the_next_text_feeds(self):
        """The incident: the CLI took the fed text into its running turn and wrote only its queue bookkeeping, the
        enqueue (carrying the text) and an absorbed_mid_turn remove carrying NONE (the CLI writes a remove's text only
        for a string-valued item, and a fed text is a content-block list). The hold releases on the next turn frame and
        the text behind it follows into the SAME turn."""
        s, c = self.s, self._first_turn()
        q1 = self._send("please also rerun the lint step", user=True)
        self._wait(lambda: len(c.writes) == 2, "the first mid-turn send forwarded")
        self._append(_enqueue_op("please also rerun the lint step"))
        q2 = self._send("and then summarise what changed", user=True)
        self._settle()
        self.assertEqual(len(c.writes), 2, "the second text is held while the first is untaken")
        self._assistant(c)                                   # a turn frame with only the enqueue on disk: no take
        self._settle()
        self.assertEqual(len(c.writes), 2, "an enqueue is not a take")
        self._append(_remove_op(None))                       # absorbed_mid_turn, no content: the live shape
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "the next text fed after the absorbed removal")
        self.assertEqual(c.writes[2], ("and then summarise what changed", "turn-1"), "into the same running turn")
        self.assertEqual(self._counts().get("absorbed_removal"), 1, "the take is counted")
        self._wait(lambda: (self._echo(q1) or {}).get("_landed"), "the taken text's echo landed by id")
        self.assertFalse((self._echo(q2) or {}).get("_landed"), "the text fed behind it is still owed its own take")
        self.assertFalse((self._echo(q1) or {}).get("dropped"), "a take is never a loss")
        self.be.prune_live(SID, set(), {})
        self.assertIsNone(self._echo(q1), "the landed echo retires with no record of its text (prune_live's _landed exit)")
        self.assertIsNotNone(self._echo(q2))

    def test_a_removal_that_names_the_text_releases_it_too(self):
        """A string-valued item's removal carries its text: matched by text, with no enqueue needed."""
        s, c = self.s, self._first_turn()
        s.enqueue("first note")
        self._wait(lambda: len(c.writes) == 2, "the first note forwarded")
        s.enqueue("second note")
        self._append(_remove_op("first note"))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "released by the removal naming it")
        self.assertEqual(self._counts().get("absorbed_removal"), 1)

    def test_joined_mail_and_tagged_sends_match_their_removal(self):
        """Mail and tagged sends queued back to back go in as one joined text; the enqueue carries the joined text and
        the CLI's content-less absorbed removal resolves it by queue order, so the hold releases, every part's echo
        lands, and every mail id is reported read."""
        s, c = self.s, self._first_turn()
        reports = self._reports()
        s.enqueue("hold this")
        self._wait(lambda: len(c.writes) == 2, "the holding send forwarded")
        self._append(_enqueue_op("hold this"))
        b1, b2 = _banner(_mid(1)), _banner(_mid(2), body="the cache is warm")
        t1, t2 = _tagged("build 7 finished green on TESTHOST"), _tagged("deploy queued", label="ci")
        s.enqueue_postal(b1, [_mid(1)])
        qa = self._send(t1)
        s.enqueue_postal(b2, [_mid(2)])
        qb = self._send(t2)
        s.enqueue("the person's own words")                 # not joinable: waits behind the joined text
        self._append(_splice("hold this"))                  # the holding send taken by its attachment, the old shape
        self._append(_remove_op(None))                      # ...and its own content-less removal right behind it
        self._assistant(c)
        joined = SEP.join([b1, t1, b2, t2])
        self._wait(lambda: len(c.writes) == 3, "the joined text fed")
        self.assertEqual(c.writes[2][0], joined)
        self._append(_enqueue_op(joined))
        self._assistant(c)
        self._settle()
        self.assertEqual(len(c.writes), 3, "the holding send's removal resolved the holding send, not the joined text")
        self._append(_remove_op(None))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 4, "the text behind the joined one fed after its absorbed removal")
        self.assertEqual(c.writes[3], ("the person's own words", "turn-1"))
        self._wait(lambda: len(reports) == 1, "the joined text's mail reported read")
        self.assertEqual(reports, [(SID, [_mid(1), _mid(2)])], "every mail id, once")
        for q in (qa, qb):
            self.assertTrue((self._echo(q) or {}).get("_landed"), "each tagged part's echo landed by id")
        self.assertEqual(self._counts().get("absorbed_removal"), 1, "one removal take: the holding send's attachment take is not one")

    def test_the_previous_texts_late_removal_does_not_release_the_current_one(self):
        """The previous text was taken by its attachment and the hold moved on; its content-less removal is written
        only AFTER the next text's enqueue. Queue order gives that removal to the previous text, so the current one
        stays held until its own removal."""
        s, c = self.s, self._first_turn()
        s.enqueue("note A")
        self._wait(lambda: len(c.writes) == 2, "A forwarded")
        self._append(_enqueue_op("note A"))
        s.enqueue("note B")
        s.enqueue("note C")
        self._append(_splice("note A"))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "B fed on A's attachment")
        self._append(_enqueue_op("note B"))
        self._append(_remove_op(None))                       # A's removal, late
        self._assistant(c)
        self._settle()
        self.assertEqual(len(c.writes), 3, "A's late removal is A's: C is not fed to fuse with B")
        self._append(_remove_op(None))                       # B's own
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 4, "C fed on B's removal")
        self.assertEqual(c.writes[3], ("note C", "turn-1"))

    def test_an_attach_to_a_surviving_cli_reads_the_queue_it_already_holds(self):
        """A kernel restart attaches to a CLI its session host kept alive, and that CLI still holds text A, enqueued
        before this client's first feed. A's late content-less removal must resolve A, not the text B fed now: the
        ledger of an attach folds the transcript from its start, so A is in it."""
        s, c = self.s, self._first_turn()
        self._append(_enqueue_op("note A"))                 # queued in the surviving CLI by the previous kernel
        s._qledger = None                                   # this client's ledger is new...
        s._host_is_attach = True                            # ...and the client attached to the CLI its host kept
        s.enqueue("note B")
        self._wait(lambda: len(c.writes) == 2, "B fed")
        self._append(_enqueue_op("note B"))
        s.enqueue("note C")
        self._append(_remove_op(None))                      # A's late removal
        self._assistant(c)
        self._settle()
        self.assertEqual(len(c.writes), 2, "A's removal is A's: C is not fed to fuse with B")
        self._append(_remove_op(None))                      # B's own
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "C fed on B's removal")

    def test_a_banner_taken_by_its_removal_is_not_handed_back_at_a_teardown(self):
        """A teardown mid-turn hands every stranded banner that did not land back to the bus. A banner the CLI took with
        only its content-less removal has no record to find, so the session's own memory of the take answers: the mail
        is not handed back and delivered a second time."""
        s, c = self.s, self._first_turn()
        handed = []
        self.be.postal_restore = lambda sid, mids: handed.append(list(mids)) or set(mids)
        b = _banner(_mid(4))
        s.enqueue_postal(b, [_mid(4)])
        self._wait(lambda: len(c.writes) == 2, "the mail forwarded")
        self._append(_enqueue_op(b))
        self._append(_remove_op(None))
        self._assistant(c)
        self._wait(lambda: s._untaken is None, "taken by its removal")
        s.loop.call_soon_threadsafe(lambda: (setattr(s, "_reconnect", True), s._wake_set()))
        self._wait(lambda: len(self._Client.instances) == 2 and s.client is self._Client.instances[1], "the forced reconnect")
        self._settle()
        self.assertEqual(handed, [], "the turn read it: not handed back to the bus")
        self.assertNotIn(b, s.pending(), "nor re-headed")

    def test_a_pending_notice_ahead_does_not_take_the_content_less_removal(self):
        """A CLI notice queued ahead of the fed text keeps its own text on its removal; a content-less removal is the
        fed text's, and the notice's later removal (with its text) changes nothing."""
        s, c = self.s, self._first_turn()
        self._append(_enqueue_op(_q.NOTIF))
        s.enqueue("note A")
        self._wait(lambda: len(c.writes) == 2, "A forwarded")
        self._append(_enqueue_op("note A"))
        s.enqueue("note B")
        self._append(_remove_op(None))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "B fed: the content-less removal was A's, not the notice's")
        self._append(_remove_op(_q.NOTIF, reason="delivered_to_agent"))
        self._assistant(c)
        self._settle()
        self.assertEqual(len(c.writes), 3)

    def test_a_drop_romp_did_not_decide_is_re_headed_and_fed_again(self):
        """(a) The CLI dropped the fed text at its prompt hook, and romp's own gate did not block it: the hook timed out
        or the session host reconnected before the answer came, which is no decision at all (2026-10-09: 78 such drops
        in a day, 9 of 16 traced texts never came back). The text goes back to the head of the queue under its own id
        and is fed again, once; its echo is neither dropped nor refused, its mail is not reported read at the drop, and
        one log line names the session."""
        s, c = self.s, self._first_turn()
        reports = self._reports()
        b = _banner(_mid(3))
        s.enqueue_postal(b, [_mid(3)])
        self._wait(lambda: len(c.writes) == 2, "the mail forwarded")
        q = self._send("a note the hook will time out on", user=True)
        self._wait(lambda: s.pending() == ["a note the hook will time out on"], "the note waits behind the mail")
        self._append(_enqueue_op(b))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "the dropped mail fed again")
        self.assertEqual(c.writes[2][0], b, "the dropped text itself, ahead of the note that queued behind it")
        self._settle()
        self.assertEqual(len(c.writes), 3, "fed once: the note waits for the re-sent mail's take")
        self.assertEqual(s.pending(), ["a note the hook will time out on"])
        self.assertEqual(reports, [], "a drop is not a read")
        self.assertEqual(self._counts().get("removed:dropped_by_hook:reheaded"), 1, "counted as a re-head")
        self.assertIsNone(self._counts().get("removed:dropped_by_hook"), "not counted as removed unread")
        self.assertEqual([p for p in self.be._problems if "removed a fed text" in p.get("text", "")], [],
                         "no never-delivered line")
        self.assertTrue(any("back at the head of the queue" in l and "web" in l for l in self.lines),
                        "one log line naming the session")

        # the person's note, dropped the same way, comes back too, and its echo is never flagged
        self._append(_enqueue_op(b))
        self._append(_remove_op(None))                       # the re-sent mail is taken this time
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 4, "the note fed after the re-sent mail's take")
        self.assertEqual(reports, [(SID, [_mid(3)])], "the re-sent mail is reported read once it is taken")
        self._append(_enqueue_op("a note the hook will time out on"))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 5, "the dropped note fed again")
        self.assertEqual(c.writes[4][0], "a note the hook will time out on")
        e = self._echo(q) or {}
        self.assertFalse(e.get("dropped"), "never flagged dropped")
        self.assertFalse(e.get("refused"), "never flagged refused")
        self.assertEqual(self._counts().get("removed:dropped_by_hook:reheaded"), 2)

    def test_a_drop_of_a_text_romps_gate_blocked_is_reported_never_delivered(self):
        """(b) romp's own prompt gate blocked the text (a replayed schedule slot, _prompt_submit_gate's one block), so
        the CLI's dropped_by_hook removal is a decision: the hold releases, since the text left the CLI's queue, but it
        is not re-fed. Its echo is flagged never delivered, its mail gets no read stamp, and one problem line names the
        session and the reason."""
        s, c = self.s, self._first_turn()
        reports = self._reports()
        b = _banner(_mid(3))
        s.enqueue_postal(b, [_mid(3)])
        self._wait(lambda: len(c.writes) == 2, "the mail forwarded")
        q = self._send("a note the gate will block", user=True)
        self._wait(lambda: len(c.writes) == 2 and s.pending() == ["a note the gate will block"], "the note waits")
        s._note_gate_blocked(b)                              # what the gate's block return records
        self._append(_enqueue_op(b))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "the note fed once the dropped mail left the CLI's queue")
        self._settle()
        self.assertEqual(c.writes[2][0], "a note the gate will block", "the blocked mail was not re-fed")
        self.assertEqual(reports, [], "a dropped banner's mail is never reported read")
        self.assertNotIn(b, s.pending(), "a drop romp's gate decided is not re-headed")
        self.assertEqual(self._counts().get("removed:dropped_by_hook"), 1)
        self.assertIsNone(self._counts().get("removed:dropped_by_hook:reheaded"))
        self.assertIsNone(self._counts().get("absorbed_removal"), "a drop is never counted as a take")
        lines = [p["text"] for p in self.be._problems if "removed a fed text" in p.get("text", "")]
        self.assertEqual(len(lines), 1, "one problem line")
        self.assertIn("web", lines[0], "naming the session")
        self.assertIn("dropped_by_hook", lines[0], "and the reason")
        self.assertIn(_mid(3), lines[0], "and the mail that stays unread")

        # the note the person sent is blocked too: its echo is flagged never delivered, and never re-fed
        s._note_gate_blocked("a note the gate will block")
        self._append(_enqueue_op("a note the gate will block"))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        self._assistant(c)
        self._wait(lambda: (self._echo(q) or {}).get("dropped"), "the dropped note's echo flagged")
        e = self._echo(q)
        self.assertTrue(e.get("refused"), "flagged refused like a prompt-gate refusal: dismissable, never re-delivered")
        self.assertIn("dropped_by_hook", e.get("refusedWhy", ""))
        self.assertFalse(e.get("_landed"), "a drop is never a landing")
        self.assertIsNone(s._untaken, "the hold released")
        self.assertEqual(s.pending(), [], "nothing re-headed")
        self.assertEqual(self._counts().get("removed:dropped_by_hook"), 2)
        self.assertEqual(len(c.writes), 3, "nothing fed again")
        self.be.prune_live(SID, set(), {})
        self.assertIsNotNone(self._echo(q), "a dropped echo stays visible until the person dismisses it")

    def test_the_fourth_drop_of_the_same_item_falls_back_to_never_delivered(self):
        """(c) A hook outside romp that really blocks a text drops it every time. The kernel re-sends one queued item at
        most HOOK_DROP_RESEND_MAX (3) times; the fourth drop takes the never-delivered road, so nothing loops."""
        s, c = self.s, self._first_turn()
        text = "a note some other hook always blocks"
        q = self._send(text, user=True)
        self._wait(lambda: len(c.writes) == 2, "forwarded")
        for i in range(3):
            self._append(_enqueue_op(text))
            self._append(_remove_op(None, reason="dropped_by_hook"))
            self._assistant(c)
            self._wait(lambda: len(c.writes) == 3 + i, "re-send %d fed" % (i + 1))
            self.assertEqual(c.writes[-1][0], text)
            self.assertFalse((self._echo(q) or {}).get("dropped"), "not flagged while re-sends remain")
        self.assertEqual(self._counts().get("removed:dropped_by_hook:reheaded"), 3)
        self._append(_enqueue_op(text))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        self._assistant(c)
        self._wait(lambda: (self._echo(q) or {}).get("dropped"), "the fourth drop flags the echo never delivered")
        self._settle()
        self.assertTrue((self._echo(q) or {}).get("refused"))
        self.assertEqual(len(c.writes), 5, "no fourth re-send")
        self.assertEqual(s.pending(), [], "nothing re-headed")
        self.assertIsNone(s._untaken)
        self.assertEqual(self._counts().get("removed:dropped_by_hook"), 1)
        self.assertEqual(self._counts().get("removed:dropped_by_hook:reheaded"), 3)
        self.assertEqual(sb.HOOK_DROP_RESEND_MAX, 3, "the cap this test walks")

    def test_a_text_that_landed_is_never_re_sent_on_a_drop(self):
        """(e) A drop removal for a text whose record landed (here a queued_command attachment written after the
        removal, so the first-match scan reads the drop) is a take: the landing check runs ahead of any re-head, so the
        turn never reads it twice. Live take and exit path alike."""
        s, c = self.s, self._first_turn()
        reports = self._reports()
        b = _banner(_mid(5))
        s.enqueue_postal(b, [_mid(5)])
        self._wait(lambda: len(c.writes) == 2, "the mail forwarded")
        s.enqueue("a note queued behind it")
        self._append(_enqueue_op(b))
        self._append(_remove_op(b, reason="dropped_by_hook"))
        self._append(_splice(b))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "the next text feeds")
        self.assertEqual(c.writes[2][0], "a note queued behind it", "the landed mail is not re-sent")
        self.assertNotIn(b, s.pending())
        self.assertEqual(reports, [(SID, [_mid(5)])], "a landed banner's mail is reported read")
        self.assertIsNone(self._counts().get("removed:dropped_by_hook:reheaded"))
        self.assertIsNone(self._counts().get("removed:dropped_by_hook"))

        # the exit path: the held note landed and was dropped in the same file; the CLI exits
        self._append(_enqueue_op("a note queued behind it"))
        self._append(_remove_op("a note queued behind it", reason="dropped_by_hook"))
        self._append(_splice("a note queued behind it"))
        s._release_hold_at_exit()
        self.assertIsNone(s._untaken)
        self.assertEqual(s.pending(), [], "not re-headed: the turn read it")
        self.assertTrue(any("after taking the last fed text" in l for l in self.lines))
        self.assertIsNone(self._counts().get("removed:dropped_by_hook:reheaded"))

    def test_a_reasonless_or_foreign_removal_releases_nothing(self):
        """A remove with no reason (an older CLI's discard: no verdict about delivery), and removals naming other texts,
        say nothing about the fed text: the hold stays until its own take, here its attachment."""
        s, c = self.s, self._first_turn()
        s.enqueue("first note")
        self._wait(lambda: len(c.writes) == 2, "the first note forwarded")
        s.enqueue("second note")
        self._append(_remove_op("some other text"))
        self._append(_remove_op("some other text", reason="dropped_by_hook"))
        self._append(_remove_op(None))                       # content-less, before the note's enqueue: not the note's
        self._assistant(c)
        self._settle()
        self.assertEqual(len(c.writes), 2, "none of these is the first note's take")
        self._append(_enqueue_op("first note"))
        self._append(_remove_op(None, reason=None))          # reason-less: resolves the entry with no verdict
        self._assistant(c)
        self._settle()
        self.assertEqual(len(c.writes), 2, "a reason-less removal is no take")
        self.assertEqual(self._counts(), {})
        self._append(_splice("first note"))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "its attachment still is")

    def test_the_queued_command_attachment_still_releases_the_hold_and_counts_no_removal(self):
        s, c = self.s, self._first_turn()
        q = self._send("check the release notes", user=True)
        self._wait(lambda: len(c.writes) == 2, "forwarded")
        self._append(_enqueue_op("check the release notes"))
        s.enqueue("then tag the release")
        self._append(_splice("check the release notes"))
        self._assistant(c)
        self._wait(lambda: len(c.writes) == 3, "the attachment is the take, as before")
        self.assertEqual(self._counts(), {}, "no removal take counted")
        self.assertFalse((self._echo(q) or {}).get("_landed"), "its echo retires by text on the record, as before")

    def test_the_cli_exiting_after_an_absorbed_removal_does_not_re_head_the_text(self):
        """The CLI took the text with only its removal and exited before any turn frame acted on it: the exit release
        reads the ledger and leaves it alone, where a text with no trace at all is re-headed."""
        s, c = self.s, self._first_turn()
        s.enqueue("a note the turn read")
        self._wait(lambda: len(c.writes) == 2, "forwarded")
        self._append(_enqueue_op("a note the turn read"))
        self._append(_remove_op(None))
        s._release_hold_at_exit()
        self.assertIsNone(s._untaken)
        self.assertEqual(s.pending(), [], "not re-headed: the turn read it")
        self.assertTrue(any("after taking the last fed text" in l for l in self.lines))

    def test_the_cli_exiting_after_a_drop_romp_did_not_decide_re_heads_the_text(self):
        """(d) The CLI dropped the text at its prompt hook and exited before any turn frame acted on it: the exit release
        reads the removal and, since romp's gate did not block the text, puts it back at the head for the next client.
        Its echo is not flagged."""
        s, c = self.s, self._first_turn()
        q = self._send("a note the hook will time out on", user=True)
        self._wait(lambda: len(c.writes) == 2, "forwarded")
        self._append(_enqueue_op("a note the hook will time out on"))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        s._release_hold_at_exit()
        self.assertIsNone(s._untaken)
        self.assertEqual(s.pending(), ["a note the hook will time out on"], "re-headed for the next client")
        self.assertEqual((s.pending_meta() or [{}])[0].get("qid"), q, "under its own id")
        self.assertFalse((self._echo(q) or {}).get("dropped"), "not flagged never delivered")
        self.assertEqual(self._counts().get("removed:dropped_by_hook:reheaded"), 1)
        self.assertIsNone(self._counts().get("removed:dropped_by_hook"))

    def test_the_cli_exiting_after_a_drop_romps_gate_blocked_does_not_re_head_the_text(self):
        """The same exit, for a text romp's own gate blocked: the take's road (flagged, not re-fed), as before."""
        s, c = self.s, self._first_turn()
        q = self._send("a note the gate will block", user=True)
        self._wait(lambda: len(c.writes) == 2, "forwarded")
        s._note_gate_blocked("a note the gate will block")
        self._append(_enqueue_op("a note the gate will block"))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        s._release_hold_at_exit()
        self.assertIsNone(s._untaken)
        self.assertEqual(s.pending(), [], "not re-headed")
        self.assertTrue((self._echo(q) or {}).get("dropped"), "flagged never delivered")
        self.assertEqual(self._counts().get("removed:dropped_by_hook"), 1)

    def test_a_gate_blocked_text_dropped_then_shut_down_is_not_re_headed(self):
        """The session ends (shutdown(): ended by hand, or hosts off) between the CLI's drop of a text romp's gate blocked
        and the next turn frame, so the exit release in _run's finally is what reads the drop. shutdown() runs first and
        must leave the gate-blocked map alone: emptied there, the drop read as undecided and the blocked text (a replayed
        schedule slot) went back to the head of the saved queue, to fire on the next revive (the review of c58720ac4)."""
        s, c = self.s, self._first_turn()
        q = self._send("a note the gate will block", user=True)
        self._wait(lambda: len(c.writes) == 2, "forwarded")
        s._note_gate_blocked("a note the gate will block")
        self._append(_enqueue_op("a note the gate will block"))
        self._append(_remove_op(None, reason="dropped_by_hook"))
        s.shutdown()
        self._wait(lambda: s._untaken is None, "the exit release ran in _run's finally")
        self._wait(lambda: (self._echo(q) or {}).get("dropped"), "the blocked note's echo flagged never delivered")
        self.assertEqual(s.pending(), [], "not re-headed after shutdown")
        self.assertIsNone(self._counts().get("removed:dropped_by_hook:reheaded"))
        self.assertEqual(self._counts().get("removed:dropped_by_hook"), 1)


class TheGateBlockedMapIsBounded(unittest.TestCase):
    """The prompts romp's gate blocked are kept per session, bounded by count and by age, consumed by the drop they
    answer, and kept through shutdown, since the exit release reads them after it (SdkSession._note_gate_blocked,
    _take_gate_blocked)."""

    def _bare(self):
        s = sb.SdkSession.__new__(sb.SdkSession)     # the map needs none of __init__
        s.loop, s.client, s.detached = None, None, False
        return s

    def test_count_bound_drops_the_oldest(self):
        s = self._bare()
        for i in range(sb.GATE_BLOCKED_KEEP + 5):
            s._note_gate_blocked("blocked prompt %d" % i)
        self.assertEqual(len(s._gate_blocked), sb.GATE_BLOCKED_KEEP)
        self.assertFalse(s._take_gate_blocked("blocked prompt 0"), "the oldest went first")
        self.assertTrue(s._take_gate_blocked("blocked prompt %d" % (sb.GATE_BLOCKED_KEEP + 4)))
        self.assertFalse(s._take_gate_blocked("blocked prompt %d" % (sb.GATE_BLOCKED_KEEP + 4)),
                         "consumed: one block answers one drop")

    def test_age_bound(self):
        s = self._bare()
        s._note_gate_blocked("an old block")
        s._gate_blocked[sb._gate_prompt_key("an old block")] -= sb.GATE_BLOCKED_TTL_S + 1
        self.assertFalse(s._take_gate_blocked("an old block"), "too old to answer a drop")
        s._note_gate_blocked("another old block")
        s._gate_blocked[sb._gate_prompt_key("another old block")] -= sb.GATE_BLOCKED_TTL_S + 1
        s._note_gate_blocked("a fresh block")
        self.assertEqual(list(s._gate_blocked), [sb._gate_prompt_key("a fresh block")], "old entries pruned on write")

    def test_keyed_on_the_first_500_characters(self):
        s = self._bare()
        s._note_gate_blocked("y" * 500 + " tail one")
        self.assertTrue(s._take_gate_blocked("y" * 500 + " a different tail"))

    def test_kept_through_shutdown(self):
        """shutdown() runs before _run's finally reads the map (_release_hold_at_exit), so it must not empty it; the
        session object is never reused, and both maps bound themselves."""
        s = self._bare()
        s._note_gate_blocked("a block")
        s._hook_drop_resends = {"q:x": 2}
        s.shutdown()
        self.assertEqual(s._hook_drop_resends, {"q:x": 2})
        self.assertTrue(s._take_gate_blocked("a block"), "the exit release still finds the block")


class TheLandingScanReadsRemovals(unittest.TestCase):
    """_text_landed / _text_take straight against a synthetic transcript: the boot re-delivery guard counts an absorbed
    removal as a landing (a restart must not re-feed a text the turn read), and a drop as not landed."""
    _H = _q.OneFedTextAtATime
    _Client, _Options = _H._Client, _H._Options
    setUp, tearDown = _H.setUp, _H.tearDown

    def _write(self, recs):
        sb.write_reg(self.state, SID, {**sb.read_reg(self.state, SID), "lastSid": FSID})
        p = sb.transcript_path(self.cwd, FSID)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        import json
        with open(p, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def test_verdicts(self):
        self._write([_enqueue_op("alpha"), _remove_op("alpha"),
                     _enqueue_op("beta"), _remove_op("beta", reason="dropped_by_hook"),
                     _enqueue_op("gamma"), _remove_op("gamma", reason=None),
                     _splice("delta")])
        self.assertEqual(self.be._text_take(SID, "alpha", 0, 0, FSID), "absorbed")
        self.assertEqual(self.be._text_take(SID, "beta", 0, 0, FSID), ("removed", "dropped_by_hook"))
        self.assertIs(self.be._text_take(SID, "gamma", 0, 0, FSID), False, "a reason-less remove says nothing")
        self.assertEqual(self.be._text_take(SID, "delta", 0, 0, FSID), "record")
        self.assertIs(self.be._text_landed(SID, "alpha", 0, 0, FSID), True, "absorbed: landed, never re-fed at boot")
        self.assertIs(self.be._text_landed(SID, "beta", 0, 0, FSID), False, "dropped: not landed")
        cur = {}
        self.assertIs(self.be._text_landed(SID, "beta", 0, 0, FSID, cursor=cur), False)
        self.assertEqual(cur.get("take"), ("removed", "dropped_by_hook"), "the verdict rides back in the cursor")
        future = int(time.time()) + 3600
        self.assertIs(self.be._text_take(SID, "alpha", future, 0, FSID), False,
                      "a removal stamped before the send is an older copy's, like any record")

    def test_the_ledger_resolves_removals_by_queue_order(self):
        self._write([{"type": "user", "uuid": "11111111-2222-3333-4444-000000000001", "message": {"content": "x"}}])
        led = {"fsid": FSID, "scan_off": 0, "entries": [], "n": 0}
        self._write([_enqueue_op(_q.NOTIF), _enqueue_op("alpha"), _enqueue_op("beta"), _enqueue_op("gamma"),
                     _remove_op(None), _remove_op(_q.NOTIF), _remove_op(None, reason="dropped_by_hook"),
                     {"type": "queue-operation", "operation": "dequeue", "timestamp": _q._iso(time.time())}])
        sb._queue_ledger_fold(self.state, SID, led)
        res = [(sorted(e["keys"])[0][:6] if e["keys"] else None, e["res"]) for e in led["entries"]]
        self.assertEqual([r[1] for r in res], ["absorbed_mid_turn", "absorbed_mid_turn", "dropped_by_hook", "dequeue"],
                         "content-less removes skip the pending notice; the notice's own removal names it; dequeue takes the oldest")
        self.assertEqual(sb._queue_ledger_verdict(led, {"text": "alpha", "off": 0, "fsid": FSID}), "absorbed")
        self.assertEqual(sb._queue_ledger_verdict(led, {"text": "beta", "off": 0, "fsid": FSID}), ("removed", "dropped_by_hook"))
        self.assertEqual(sb._queue_ledger_verdict(led, {"text": "gamma", "off": 0, "fsid": FSID}), "record")
        self.assertIsNone(sb._queue_ledger_verdict(led, {"text": "delta", "off": 0, "fsid": FSID}), "never enqueued")
        before = led["scan_off"]
        sb._queue_ledger_fold(self.state, SID, led)
        self.assertEqual(led["scan_off"], before, "resumes where it stopped")

    def test_the_notice_rule_is_the_event_models(self):
        self.assertEqual(sb._CLI_NOTICE_RE.pattern, sb._em.SYSTEM_WRAPPER_RE.pattern,
                         "the ledger's notice test is a twin of the event model's (backend threads never read it)")

    def test_a_content_block_list_removal_matches_too(self):
        rec = _remove_op(None)
        rec["content"] = [{"type": "text", "text": "epsilon"}]
        self._write([rec])
        self.assertEqual(self.be._text_take(SID, "epsilon", 0, 0, FSID), "absorbed")


if __name__ == "__main__":
    unittest.main()
