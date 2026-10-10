#!/usr/bin/env python3
"""Tagged machine lines and peer mail queued behind a running turn reach the session in ONE step.

The shape: a busy session's queue is fed to the CLI one text at a time, each held until the CLI takes it
(SdkSession._untaken), and the CLI takes a text sent mid-turn only at a tool boundary. Joining adjacent mail
banners (test_queued_mail_joined) left every TAGGED send (`romp send --tag`, a watcher's line, carrying the
`<!-- romp-tag: <label> -->` marker) going one per step, and a tagged line between two banners split the mail
too: a queue of a few dozen watcher lines and peer mail took a few dozen tool steps to drain, so an order queued
behind them waited most of an hour.

The rule: at feed time the head joins with every item directly behind it that is JOINABLE, a mail banner or a
tagged machine send, and the join stops at the first item that is not: the person's own messages (any untagged
send, a chat line marked fromUser), slash commands, romp's untagged notices, anything id-less and unmarked. A
head that is not joinable goes alone, nothing is reordered, and the joined text is bounded by
MAIL_JOIN_MAX_BYTES. Every joined send keeps its echo id: the joined text's landing names every part's id and
retires every part's echo; a teardown re-heads every part under its own id, or hands mail back to the bus by id.

SYNTHETIC fixtures only: invented text, placeholder uuids, the TESTHOST hostname in the message ids."""
import os
import sys
import tempfile
import threading
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
pm = load_source("romp_postal_taggedjoin", os.path.join(_q.BIN, "romp-postal-service"))
SID, _H = _q.SID, _q.OneFedTextAtATime
PEER = "22222222-3333-4444-5555-666666666666"
SEP = "\n\n"


def _mid(n):
    return "17000%05d.%06d.TESTHOST" % (n, n)


def _banner(mid, frm="api", body="the migration finished; the next step is yours"):
    return pm.format_push([{"from": frm, "from_id": PEER, "body": body + " (" + mid[:10] + ")", "id": mid, "date": ""}])


def _tagged(body, label="watch"):
    """A tagged machine send exactly as `romp send --tag` builds it: the text, a blank line, the marker."""
    return body + "\n\n<!-- romp-tag: " + label + " -->"


def _splice(text):
    return {"type": "attachment", "uuid": "11111111-2222-3333-4444-" + ("%012d" % (abs(hash(text)) % 10 ** 12)),
            "timestamp": _q._iso(time.time()), "attachment": {"type": "queued_command", "prompt": text}}


class QueuedTaggedLinesAndMailSurfaceAtTheNextStep(unittest.TestCase):
    """End to end through the real inputs() closure and _on_message, with real sends (echo and id) for the tagged lines."""
    _Client, _Options = _H._Client, _H._Options
    setUp, tearDown = _H.setUp, _H.tearDown
    _wait, _settle, _push, _uid = _H._wait, _H._settle, _H._push, _H._uid
    _init, _assistant, _result_frame = _H._init, _H._assistant, _H._result_frame
    _user_record, _first_turn, _transcript, _append = _H._user_record, _H._first_turn, _H._transcript, _H._append

    def _take(self, c, text):
        self._append(_splice(text))
        self._assistant(c)

    def _reports(self):
        reports = []
        self.be.postal_taken = lambda sid, mids: reports.append((sid, list(mids)))
        return reports

    def _send(self, text, user=False):
        """A real send (SdkBackend.send): the queued copy and its echo share one minted id, which this returns."""
        self.assertTrue(self.be.send(SID, text, user=user))
        return [m["qid"] for m in self.be.pending_queued_meta(SID) if m.get("md") == text][-1]

    def _echo(self, qid):
        return (self.be._live.get(SID) or {}).get(qid)

    def _held(self, c):
        """Open a turn and hold the feed behind a mid-turn send, so everything queued after it waits."""
        self.s.enqueue("hold this")
        self._wait(lambda: len(c.writes) == 2, "the mid-turn send forwarded")

    def test_a_mixed_queue_of_tagged_lines_and_mail_drains_in_one_step(self):
        s, c = self.s, self._first_turn()
        reports = self._reports()
        self._held(c)
        texts, mids, qids = [], [], []
        mail_at = set(range(1, 32, 2))                      # 16 banners interleaved with 22 tagged lines
        for i in range(38):
            if i in mail_at:
                m = _mid(i)
                b = _banner(m, frm="tests" if i % 4 == 1 else "api")
                s.enqueue_postal(b, [m])
                texts.append(b); mids.append(m)
            else:
                t = _tagged("build %d finished green on TESTHOST" % i, label="ci" if i % 3 else "watch")
                qids.append(self._send(t))
                texts.append(t)
        self.assertEqual((len(mids), len(qids)), (16, 22))
        self._settle()
        self.assertEqual(len(c.writes), 2, "nothing more is fed while the send is untaken")
        self.assertEqual(len(s.pending()), 38)

        self._take(c, "hold this")
        joined = SEP.join(texts)
        self._wait(lambda: len(c.writes) == 3, "the next feed after the send's take")
        self.assertEqual(c.writes[2], (joined, "turn-1"),
                         "the 38 queued texts reach the CLI as ONE text at the next step; before, each tagged line went "
                         "alone and split the mail, so the queue took one tool step per item")
        self.assertEqual(s.pending(), [], "the whole queue drained")
        self.assertEqual(sb.postal_mids(joined), mids, "the joined text carries every mail id, in order")
        self.assertEqual(s.fed_texts()[-1], joined)
        for q in qids:
            self.assertEqual(self._echo(q).get("_joined_text"), joined, "each tagged line's echo knows the text it rides in")

        # while the CLI holds it, no part reads as overtaken (a later human turn is not a loss: the joined text is owed)
        self.be.settle_echoes(SID, time.time() + 100)
        self.assertEqual([q for q in qids if self._echo(q).get("dropped")], [], "no part of a fed joined text is flagged lost")

        self._take(c, joined)
        self._wait(lambda: len(reports) == 1, "the joined text's take report")
        self._settle()
        self.assertEqual(reports, [(SID, mids)], "every mail id reported taken exactly once, at the joined text's take")

        # the landing: one record, every tagged part's id once, and every part's echo retired by it
        rec = "11111111-2222-3333-4444-0000000000f1"
        self.assertEqual(s.qids_for_landing(rec, [joined], time.time()), [qids], "the record names all 22 ids")
        self.assertEqual(s.qids_for_landing("11111111-2222-3333-4444-0000000000f2", [joined], time.time()), [None],
                         "a second record of the same text pairs nothing: each id lands once")
        self.be.prune_live(SID, set(), {sb.echo_text_key(joined): time.time()})
        self.assertEqual([q for q in qids if self._echo(q) is not None], [], "every part's echo retired by the joined landing")

    def test_a_persons_message_in_the_middle_stops_the_join_and_goes_alone(self):
        s, c = self.s, self._first_turn()
        self._held(c)
        t1, t2 = _tagged("lint passed"), _tagged("tests passed", label="ci")
        b1, b2 = _banner(_mid(1)), _banner(_mid(2), frm="tests")
        mine = "please stop after this step and summarise"
        self._send(t1)
        s.enqueue_postal(b1, [_mid(1)])
        self._send(mine, user=True)                         # the person's own words: untagged
        self._send(t2)
        s.enqueue_postal(b2, [_mid(2)])
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "the first joined run")
        self.assertEqual(c.writes[2][0], t1 + SEP + b1, "joined up to the person's message")
        self.assertEqual(s.pending(), [mine, t2, b2], "the person's message keeps its place, unjoined")
        self._take(c, c.writes[2][0])
        self._wait(lambda: len(c.writes) == 4, "the person's message")
        self.assertEqual(c.writes[3][0], mine, "the person's message goes alone, byte for byte")
        self._take(c, mine)
        self._wait(lambda: len(c.writes) == 5, "the run behind it")
        self.assertEqual(c.writes[4][0], t2 + SEP + b2, "the rest joins after it")

    def test_a_chat_line_from_the_person_is_never_joined(self):
        s, c = self.s, self._first_turn()
        self._held(c)
        t1, t2, t3 = _tagged("deploy queued"), _tagged("deploy running"), _tagged("deploy done")
        line = "what is the status of the deploy?"
        marked_tagged = _tagged("a marked line that also wears a tag")   # refused by the kernel; never joined here either
        self._send(t1)
        with s._lock:                                        # queued as a marked chat line, in place (no reordering here)
            s._q_append(line, {"qid": "echo:11111111-2222-3333-4444-0000000000c1", "qts": 1, "fromUser": True})
            s._q_append(marked_tagged, {"qid": "echo:11111111-2222-3333-4444-0000000000c2", "qts": 2, "fromUser": True})
        self._send(t2)
        self._send(t3)
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "t1")
        self.assertEqual(c.writes[2][0], t1, "the tagged line stops before the chat line")
        for want in (line, marked_tagged):
            self._take(c, c.writes[-1][0])
            n = len(c.writes)
            self._wait(lambda: len(c.writes) == n + 1, "the next")
            self.assertEqual(c.writes[-1][0], want, "a line marked as the person's goes alone")
        self._take(c, c.writes[-1][0])
        self._wait(lambda: len(c.writes) == 6, "the tagged run behind")
        self.assertEqual(c.writes[5][0], t2 + SEP + t3)

    def test_a_slash_command_is_never_joined(self):
        s, c = self.s, self._first_turn()
        self._held(c)
        t1, t2, t3 = _tagged("index rebuilt"), _tagged("cache warm"), _tagged("ready")
        slash = "/review the search change"
        tagged_slash = _tagged("/review the search change")
        self._send(t1)
        s.enqueue(slash, qid="echo:11111111-2222-3333-4444-0000000000d1", qts=1)
        s.enqueue(tagged_slash, qid="echo:11111111-2222-3333-4444-0000000000d2", qts=2)
        self._send(t2)
        self._send(t3)
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "t1")
        self.assertEqual(c.writes[2][0], t1)
        for want in (slash, tagged_slash):
            self._take(c, c.writes[-1][0])
            n = len(c.writes)
            self._wait(lambda: len(c.writes) == n + 1, "the next")
            self.assertEqual(c.writes[-1][0], want, "a slash command goes alone, tagged or not")
        self._take(c, c.writes[-1][0])
        self._wait(lambda: len(c.writes) == 6, "the tagged run behind")
        self.assertEqual(c.writes[5][0], t2 + SEP + t3)

    def test_an_unjoinable_head_goes_alone_and_what_is_behind_it_joins(self):
        s, c = self.s, self._first_turn()
        self._held(c)
        mine = "and rename the helper while you are there"
        t1, b1 = _tagged("coverage 91 percent"), _banner(_mid(7))
        self._send(mine, user=True)
        self._send(t1)
        s.enqueue_postal(b1, [_mid(7)])
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "the head")
        self.assertEqual(c.writes[2][0], mine, "an untagged send at the head goes alone")
        self._take(c, mine)
        self._wait(lambda: len(c.writes) == 4, "the run")
        self.assertEqual(c.writes[3][0], t1 + SEP + b1)

    def test_two_untagged_sends_still_go_as_two_messages(self):
        s, c = self.s, self._first_turn()
        self._held(c)
        a, b = "first of the person's two", "second of the person's two"
        self._send(a, user=True)
        self._send(b, user=True)
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "a")
        self.assertEqual(c.writes[2][0], a)
        self.assertEqual(s.pending(), [b])

    def test_the_join_stops_short_of_the_size_cap(self):
        s, c = self.s, self._first_turn()
        self._held(c)
        t1, b1, t2 = _tagged("step one done"), _banner(_mid(3)), _tagged("step two done")
        old = sb.MAIL_JOIN_MAX_BYTES
        sb.MAIL_JOIN_MAX_BYTES = len((t1 + SEP + b1).encode("utf-8"))
        self.addCleanup(setattr, sb, "MAIL_JOIN_MAX_BYTES", old)
        self._send(t1)
        s.enqueue_postal(b1, [_mid(3)])
        q2 = self._send(t2)
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "the joined run")
        self.assertEqual(c.writes[2][0], t1 + SEP + b1, "joined up to the cap")
        self.assertEqual(s.pending(), [t2], "the line past the cap waits at the head")
        self.assertEqual([m.get("qid") for m in s.pending_meta()], [q2], "with its own id")


class AJoinedTextHandsBackEveryPart(unittest.TestCase):
    """A teardown that strands a joined text re-heads every part under its own id, or hands its mail back by id."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                            # a self-minted state root pins per-session hosts off
        self._cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        self.tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(self.tp), exist_ok=True)
        open(self.tp, "w").close()
        self.logged = []
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lambda m, **k: self.logged.append(m))
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd, "lastSid": SID}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        self._park = threading.Event()                # _ensure hands a send only to a session whose thread is alive
        self.s.thread = threading.Thread(target=self._park.wait, daemon=True)
        self.s.thread.start()
        self.be.sessions[SID] = self.s
        self.t1, self.t2 = _tagged("migration 1 applied"), _tagged("migration 2 applied")
        self.m1 = _mid(11)
        self.b1 = _banner(self.m1)
        self.assertTrue(self.be.send(SID, self.t1))
        self.s.enqueue_postal(self.b1, [self.m1])
        self.assertTrue(self.be.send(SID, self.t2))
        self.q1, _, self.q2 = [m.get("qid") for m in self.s.pending_meta()]
        self.joined = SEP.join([self.t1, self.b1, self.t2])
        self.assertEqual(self._feed(), self.joined)

    def tearDown(self):
        self._park.set()
        if self._cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg

    def _feed(self):
        """What the feeder does with the head (inputs()): pop it, join what is behind it, then stamp the parts' echoes."""
        s = self.s
        with s._lock:
            item, meta = s._pop_for_feed_locked(0)
            item, joined_qids = s._join_queued_locked(item, meta)
            s.inflight += 1
            s._inflight_texts.append(item)
        if joined_qids:
            self.be._stamp_joined_echoes(SID, joined_qids, item)
        return item

    def _echo(self, qid):
        return (self.be._live.get(SID) or {}).get(qid)

    def test_no_conversation_re_heads_every_part_under_its_own_id(self):
        self.s.resume_sid = None
        self.s._reconcile_stranded()
        self.assertEqual(self.s.pending(), [self.t1, self.b1, self.t2], "every part back at the head, in order")
        self.assertEqual([m.get("qid") for m in self.s.pending_meta()], [self.q1, None, self.q2],
                         "each send under its own echo id, the banner id-less as mail always is")
        self.assertEqual(self._feed(), self.joined, "the next client is fed the same joined text")
        self.assertEqual(self.s.qids_for_landing("11111111-2222-3333-4444-0000000000e1", [self.joined], time.time()),
                         [[self.q1, self.q2]], "and its landing still names both ids")

    def test_a_resumable_conversation_hands_the_mail_back_and_flags_the_sends(self):
        handed = []
        self.be.postal_restore = lambda sid, mids: (handed.append(list(mids)), set(mids))[1]
        self.s.resume_sid = SID
        self.s._reconcile_stranded()
        self.assertEqual(handed, [[self.m1]], "the banner's id goes back to the bus")
        self.assertEqual(self.s.pending(), [], "nothing re-fed into a resumable conversation")
        self.assertTrue(self._echo(self.q1).get("dropped") and self._echo(self.q2).get("dropped"),
                        "each send is flagged never-delivered under its own id, as a lone stranded send is")

    def test_a_resumable_conversation_whose_bus_refuses_re_heads_the_banner_alone(self):
        def refuse(sid, mids):
            raise ConnectionRefusedError("no bus on the port")
        self.be.postal_restore = refuse
        self.s.resume_sid = SID
        self.s._reconcile_stranded()
        self.assertEqual(self.s.pending(), [self.b1], "the banner alone, not the sends beside it")
        self.assertIn(self.m1, self.s._postal_taken)
        self.assertTrue(self._echo(self.q1).get("dropped") and self._echo(self.q2).get("dropped"))

    def test_a_joined_text_that_landed_before_the_teardown_is_left_whole(self):
        handed, reports = [], []
        self.be.postal_restore = lambda sid, mids: (handed.append(list(mids)), set(mids))[1]
        self.be.postal_taken = lambda sid, mids: reports.append(list(mids))
        with open(self.tp, "a") as f:
            import json
            f.write(json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-0000000000e2",
                                "timestamp": _q._iso(time.time() + 1),
                                "message": {"role": "user", "content": self.joined}}) + "\n")
        self.s.resume_sid = SID
        self.s._reconcile_stranded()
        self.assertEqual(handed, [], "landed mail is not handed back")
        self.assertEqual(reports, [[self.m1]], "it is reported taken")
        for q in (self.q1, self.q2):
            e = self._echo(q)
            self.assertTrue(e is None or (e.get("_landed") and not e.get("dropped")), "the sends landed, never flagged")

    def test_the_cli_exiting_with_the_joined_text_untaken_re_heads_every_part(self):
        self.s.resume_sid = SID
        self.s._untaken = {"text": self.joined, "item": self.joined, "fresh": False, "settled": False, "qid": self.q1,
                           "t": int(time.time()), "off": 0, "fsid": SID}
        self.s._release_hold_at_exit()
        self.assertEqual(self.s.pending(), [self.t1, self.b1, self.t2])
        self.assertEqual([m.get("qid") for m in self.s.pending_meta()], [self.q1, None, self.q2])

    def test_the_parts_stamp_survives_a_kernel_restart(self):
        mirror = {e.get("uuid"): e for e in (sb.read_reg(self.be.state_dir, SID) or {}).get("echoes") or []}
        self.assertEqual(mirror[self.q1].get("joined"), self.joined, "the echo mirror carries the joined text")
        be2 = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lambda m, **k: None)
        be2._lease_survives = lambda sid: True        # the CLI lives on under its host: nothing flagged at the boot
        be2._reseed_echoes([sb.read_reg(self.be.state_dir, SID)])
        self.assertEqual(be2._live[SID][self.q2].get("_joined_text"), self.joined)
        be2.prune_live(SID, set(), {sb.echo_text_key(self.joined): time.time() + 1})
        self.assertNotIn(SID, be2._live, "the reseeded parts retire on the joined landing")


del _H   # borrowed by method above; unbound so the harness's own tests are not collected again from here


if __name__ == "__main__":
    unittest.main()
