#!/usr/bin/env python3
"""Tracker endings, two gaps from the working-column audit (the user 2026-08-26: audit that all
Working cards should be working — the non-real evidence isolated to sender-side delegation trackers
whose ending event could no longer fire):
(1) ARCHIVED-DONE RECIPIENT — run_propagate's back-link scanned recipient stores live-only, so a
    recipient goal that completed and was then archived (the user cleared the done card) vanished
    from the scan and the sender's tracker never checked off. The recipient-side scan now reads
    live+archive merged (read-only on that side; propagate writes SENDER stores only).
(2) CLEARED-UNDONE RECIPIENT — the user dismissed the recipient's card without completion, killing
    the back-link's event forever; the reply sweep now covers a linked local tracker whose
    ref-joined recipient goal is cleared-without-done — the recipient's reply at/after the send is
    the report-back event (the exact quiet/cross-host rule), why-stamped as the dismissal shape it
    is. A LIVE linked recipient still defers to the back-link: a reply alone never ends a
    delegation whose card is still being worked. SYNTHETIC fixtures only; private synthetic sids."""
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
jd = SourceFileLoader("romp_judge_trkend", os.path.join(BIN, "romp-judge")).load_module()

T = 1_787_300_000
SENDER = "a21e0001-1111-4222-8333-000000000001"   # private synthetic sids — never the shared placeholder
RECIP = "a21e0001-1111-4222-8333-000000000002"
MID = "1787299000.000001_1.TESTHOST"


def _node(nid, text, parent, t=T, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return base


class World(unittest.TestCase):
    def setUp(self):
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/dev/null", None, "web"), (RECIP, "/dev/null", None, "api")]
        self.td = tempfile.TemporaryDirectory()
        self._msgs = jd.MESSAGES
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        jd.MESSAGES.write_text("")

    def tearDown(self):
        jd.discover = self._saved
        jd.MESSAGES = self._msgs
        for sid in (SENDER, RECIP):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass
        self.td.cleanup()

    def _sender_with_tracker(self):
        st = {"rompUuid": SENDER, "seq": 2, "nodes":
              {SENDER + ":g1": _node(SENDER + ":g1", "Ship the staged verification", None),
               SENDER + ":t1": _node(SENDER + ":t1", "↪ delegated to api: verify refs", SENDER + ":g1",
                                     handoff={"peer": RECIP, "msgId": MID})},
              "placements": {}, "status": {}}
        jd.save_goals(SENDER, st)

    def _recipient(self, done, cleared, archive):
        rn = _node(RECIP + ":g5", "Verify the staged refs", None,
                   origin={"peer": SENDER, "goalId": SENDER + ":t1", "msgId": MID})
        st = {"rompUuid": RECIP, "nodes": {RECIP + ":g5": rn}, "placements": {}, "status": {}}
        if done:
            jd.record_verdict(st, st["nodes"][RECIP + ":g5"], "closer", "done", T + 100,
                              why="refs verified; drift zero")
        if cleared:
            st["nodes"][RECIP + ":g5"]["cleared"] = True
        (jd.save_goal_archive if archive else jd.save_goals)(RECIP, st)
        if archive:
            jd.save_goals(RECIP, {"rompUuid": RECIP, "nodes": {}, "placements": {}, "status": {}})

    def _reply(self, at):
        jd.MESSAGES.write_text(json.dumps(
            {"t": at, "ev": "sent", "id": "r1", "from_id": RECIP, "to_id": SENDER,
             "kind": "coordinate", "body": "verified; drift is zero"}) + "\n")

    def _tracker(self):
        return jd.load_goals(SENDER)["nodes"][SENDER + ":t1"]


class ArchivedDoneRecipient(World):
    def test_the_back_link_reaches_an_archived_completion(self):
        self._sender_with_tracker()
        self._recipient(done=True, cleared=True, archive=True)
        jd.run_propagate(now=T + 900)
        nd = self._tracker()
        self.assertTrue(nd.get("nodeComplete"),
                        "the completion event FIRED — moving files must not erase it")
        self.assertIn("refs verified", nd.get("doneWhy") or "",
                      "the recipient's own resolution still travels")

    def test_a_live_done_recipient_is_byte_identical(self):
        self._sender_with_tracker()
        self._recipient(done=True, cleared=False, archive=False)
        jd.run_propagate(now=T + 900)
        self.assertTrue(self._tracker().get("nodeComplete"))


class DismissedRecipient(World):
    def test_a_cleared_undone_recipient_ends_on_the_reply(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=False)
        self._reply(T + 500)
        jd.run_propagate(now=T + 900)
        nd = self._tracker()
        self.assertTrue(nd.get("nodeComplete"))
        self.assertIn("dismissed", nd.get("doneWhy") or "",
                      "the why says what happened — a dismissal-shaped ending, not a completion claim")

    def test_no_reply_stays_open(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=False)
        jd.run_propagate(now=T + 900)
        self.assertFalse(self._tracker().get("nodeComplete"),
                         "no report-back event yet — the standing wait machinery owns it")

    def test_a_reply_before_the_send_does_not_count(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=False)
        self._reply(T - 500)
        jd.run_propagate(now=T + 900)
        self.assertFalse(self._tracker().get("nodeComplete"))

    def test_a_live_linked_recipient_never_ends_on_a_reply(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=False, archive=False)
        self._reply(T + 500)
        jd.run_propagate(now=T + 900)
        self.assertFalse(self._tracker().get("nodeComplete"),
                         "the back-link owns a live linked delegation — a reply alone is not its end")

    def test_an_archived_cleared_undone_recipient_also_ends_on_the_reply(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=True)
        self._reply(T + 500)
        jd.run_propagate(now=T + 900)
        self.assertTrue(self._tracker().get("nodeComplete"),
                        "the dismissal shape resolves through the archive too")


if __name__ == "__main__":
    unittest.main()
