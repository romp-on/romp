#!/usr/bin/env python3
"""Peer mail queued behind a running turn reaches the session in ONE step, not one message per step.

The shape: the bus pushes each new message to the kernel as its own banner (SdkBackend.deliver ->
SdkSession.enqueue_postal), and the input feeder hands the CLI one queued text at a time, holding the next until the
CLI has taken the current one (SdkSession._untaken). The CLI takes a text sent mid-turn only at a tool boundary, so
N banners queued during a long turn took N tool steps to arrive: a busy session read its mail many minutes late.

The fix: when the feeder is about to hand the CLI a mail banner, it joins it with every mail banner queued directly
behind it (adjacent at the head of the queue) into one text, a blank line between them, which carries every one of
their message ids. A bus banner already carries several messages when the bus has several ready (format_push), so
the joined text reads as mail has always read. Queued SENDS are still fed one each (test_queued_sends_not_fused), a
mail banner is never joined with anything that is not one, nothing else is reordered, and the text already handed to
the CLI is never touched. The id accounting follows from the joined text carrying the markers: the take report names
every id of the joined text once, when the CLI takes it, and a teardown hands back (or re-heads) every id of it.

The REAL _amain runs here against the stand-in SDK client of test_queued_sends_not_fused (its harness is borrowed by
method, so none of that module's own tests are collected twice). SYNTHETIC fixtures only: invented text, placeholder
uuids, the TESTHOST hostname in the message ids."""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from romp_load import load_source          # noqa: E402
# Hermetic state BEFORE any load (the borrowed module sets its own floor too; this one is this file's).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
import test_queued_sends_not_fused as _q   # noqa: E402  (borrowed harness: the stand-in SDK client and helpers)

sb = _q.sb
pm = load_source("romp_postal_queuejoin", os.path.join(_q.BIN, "romp-postal-service"))
SID, _H = _q.SID, _q.OneFedTextAtATime

PEER = "22222222-3333-4444-5555-666666666666"
M1, M2, M3, M4 = ("1700000001.111111.TESTHOST", "1700000002.222222.TESTHOST",
                  "1700000003.333333.TESTHOST", "1700000004.444444.TESTHOST")


def _banner(*mids, frm="api", body="the migration finished; the next step is yours"):
    """A bus banner exactly as the bus hands it to /deliver (its own formatter), one message per id."""
    return pm.format_push([{"from": frm, "from_id": PEER, "body": body + " (" + m[:10] + ")", "id": m, "date": ""}
                           for m in mids])


def _splice(text):
    """The CLI's record of a text it took mid-turn at a tool boundary: the queued_command attachment."""
    return {"type": "attachment", "uuid": "11111111-2222-3333-4444-" + ("%012d" % (abs(hash(text)) % 10 ** 12)),
            "timestamp": _q._iso(time.time()), "attachment": {"type": "queued_command", "prompt": text}}


class QueuedMailSurfacesAtTheNextStep(unittest.TestCase):
    """End to end through the real inputs() closure and _on_message."""
    _Client, _Options = _H._Client, _H._Options
    setUp, tearDown = _H.setUp, _H.tearDown
    _wait, _settle, _push, _uid = _H._wait, _H._settle, _H._push, _H._uid
    _init, _assistant, _result_frame = _H._init, _H._assistant, _H._result_frame
    _user_record, _first_turn, _transcript, _append = _H._user_record, _H._first_turn, _H._transcript, _H._append

    def _take(self, c, text):
        """The CLI takes the fed `text` at a tool boundary: its splice record lands and the next frame reads it."""
        self._append(_splice(text))
        self._assistant(c)

    def _reports(self):
        reports = []
        self.be.postal_taken = lambda sid, mids: reports.append((sid, list(mids)))
        return reports

    def test_adjacent_mail_is_fed_as_one_text_and_a_typed_note_between_stays_in_place(self):
        s, c = self.s, self._first_turn()
        reports = self._reports()
        s.enqueue("please also update the changelog")              # a mid-turn send: fed at once, now held
        self._wait(lambda: len(c.writes) == 2, "the mid-turn send forwarded")
        b1, b2, b3, b4 = _banner(M1), _banner(M2, frm="tests"), _banner(M3), _banner(M4, frm="tests")
        s.enqueue_postal(b1, [M1])
        s.enqueue_postal(b2, [M2])
        s.enqueue("a typed note in between", qid="echo:11111111-2222-3333-4444-000000000a01", qts=1)
        s.enqueue_postal(b3, [M3])
        s.enqueue_postal(b4, [M4])
        self._settle()
        self.assertEqual(len(c.writes), 2, "nothing more is fed while the send is untaken")
        self.assertEqual(s.pending(), [b1, b2, "a typed note in between", b3, b4])

        self._take(c, "please also update the changelog")
        joined12 = b1 + "\n\n" + b2
        self._wait(lambda: len(c.writes) == 3, "the next feed after the send's take")
        self.assertEqual(c.writes[2], (joined12, "turn-1"),
                         "the two banners queued back to back reach the CLI as ONE text at the next step; before "
                         "the fix the first went alone and the second waited for another tool step")
        self.assertEqual(sb.postal_mids(c.writes[2][0]), [M1, M2], "the joined text carries both ids, in order")
        self.assertEqual(s.pending(), ["a typed note in between", b3, b4],
                         "the typed note is not joined with mail and keeps its place; the mail behind it waits")
        self.assertEqual((sb.read_reg(self.state, SID) or {}).get("queue"), s.pending(),
                         "the persisted queue lost both fed banners and nothing else")
        self.assertEqual([m.get("qid") for m in (sb.read_reg(self.state, SID) or {}).get("queueMeta")],
                         ["echo:11111111-2222-3333-4444-000000000a01", None, None],
                         "the queue's identity list stays aligned with its texts")
        self.assertEqual(s.fed_texts()[-1], joined12, "the fed-turn twin holds the joined text, whole")
        self.assertEqual(reports, [], "nothing is reported taken before the CLI takes it")

        self._take(c, joined12)
        self._wait(lambda: len(c.writes) == 4, "the typed note fed after the joined mail's take")
        self.assertEqual(reports, [(SID, [M1, M2])], "both ids are reported taken once, at the joined text's take")
        self.assertEqual(c.writes[3], ("a typed note in between", "turn-1"), "the note alone, in its turn")

        self._take(c, "a typed note in between")
        joined34 = b3 + "\n\n" + b4
        self._wait(lambda: len(c.writes) == 5, "the mail behind the note")
        self.assertEqual(c.writes[4], (joined34, "turn-1"))
        self._take(c, joined34)
        self._wait(lambda: len(reports) == 2, "the second joined text's take report")
        self._settle()
        self.assertEqual(reports, [(SID, [M1, M2]), (SID, [M3, M4])], "every id reported taken exactly once")
        self.assertEqual(s.pending(), [])
        self.assertEqual(s._postal_taken[-4:], [M1, M2, M3, M4], "the taken-id list holds every id")

    def test_a_banner_alone_is_fed_unchanged(self):
        s, c = self.s, self._first_turn()
        b = _banner(M1, M2)                                          # one banner carrying two messages, as the bus sends it
        s.enqueue_postal(b, [M1, M2])
        self._wait(lambda: len(c.writes) == 2, "the banner forwarded")
        self.assertEqual(c.writes[1], (b, "turn-1"), "a lone banner reaches the CLI byte for byte")

    def test_a_typed_send_quoting_a_marker_is_never_joined_with_mail(self):
        """A send the person typed carries its echo id; even if its words quote a mail marker it is not mail."""
        s, c = self.s, self._first_turn()
        s.enqueue("hold this")
        self._wait(lambda: len(c.writes) == 2, "the hold")
        quoting = "what does <!-- romp-msg-id: 1700000009.999999.TESTHOST --> mean?"
        b = _banner(M1)
        s.enqueue(quoting, qid="echo:11111111-2222-3333-4444-000000000a02", qts=1)
        s.enqueue_postal(b, [M1])
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "the typed send")
        self.assertEqual(c.writes[2], (quoting, "turn-1"))
        self.assertEqual(s.pending(), [b])

    def test_the_join_stops_short_of_the_size_cap(self):
        s, c = self.s, self._first_turn()
        s.enqueue("hold this")
        self._wait(lambda: len(c.writes) == 2, "the hold")
        b1, b2, b3 = _banner(M1), _banner(M2), _banner(M3)
        cap = len((b1 + "\n\n" + b2).encode("utf-8"))               # room for two banners, not three
        old = sb.MAIL_JOIN_MAX_BYTES
        sb.MAIL_JOIN_MAX_BYTES = cap
        self.addCleanup(setattr, sb, "MAIL_JOIN_MAX_BYTES", old)
        for b, m in ((b1, M1), (b2, M2), (b3, M3)):
            s.enqueue_postal(b, [m])
        self._take(c, "hold this")
        self._wait(lambda: len(c.writes) == 3, "the joined mail")
        self.assertEqual(c.writes[2][0], b1 + "\n\n" + b2, "joined up to the cap")
        self.assertEqual(s.pending(), [b3], "the banner past the cap waits at the head for the next step")


class AJoinedBannerHandsBackEveryId(unittest.TestCase):
    """A teardown that strands a joined banner hands every one of its ids back to the bus, or re-heads it whole."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                            # a self-minted state root pins per-session hosts off
        self._cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w").close()
        self.logged = []
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lambda m, **k: self.logged.append(m))
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd, "lastSid": SID}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = self.s
        self.joined = _banner(M1) + "\n\n" + _banner(M2, frm="tests")
        with self.s._lock:                            # as the feeder leaves it: both banners' ids taken
            self.s._take_postal_locked([M1])
            self.s._take_postal_locked([M2])

    def tearDown(self):
        if self._cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg

    def _strand(self):
        self.s.inflight = 1
        self.s._inflight_texts.append(self.joined)
        self.s._reconcile_stranded()

    def test_every_id_of_a_joined_banner_goes_back_to_the_bus(self):
        handed = []
        self.be.postal_restore = lambda sid, mids: (handed.append(list(mids)), set(mids))[1]
        self._strand()
        self.assertEqual(handed, [[M1, M2]], "one hand-back naming both ids of the joined text")
        self.assertEqual(self.s.pending(), [], "the bus re-delivers them; nothing re-fed here")
        self.assertFalse({M1, M2} & set(self.s._postal_taken), "both ids forgotten, so the re-delivery is queued")

    def test_a_joined_banner_the_bus_cannot_take_back_is_re_headed_whole_under_both_ids(self):
        def refuse(sid, mids):
            raise ConnectionRefusedError("no bus on the port")
        self.be.postal_restore = refuse
        self._strand()
        self.assertEqual(self.s.pending(), [self.joined], "re-headed whole")
        self.assertEqual((sb.read_reg(self.be.state_dir, SID) or {}).get("queue"), [self.joined])
        self.assertEqual(self.s._postal_taken[-2:], [M1, M2], "both ids taken again with it")
        self.assertEqual(self.s._postal_inhand, {}, "the hand-back is over for both")
        # a re-post of either one now is a repeat, not new mail
        self.assertEqual(self.s.enqueue_postal(_banner(M2, frm="tests"), [M2]), [M2])
        self.assertEqual(self.s.pending(), [self.joined])


del _H   # borrowed by method above; unbound so the harness's own tests are not collected again from here


if __name__ == "__main__":
    unittest.main()
