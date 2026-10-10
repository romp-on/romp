#!/usr/bin/env python3
"""The person's own lines from the chat page go to the front of a session's queue (2026-10-06).

What happened: the chat page relays the person's words to a session with `romp send <session> "<text>"`, an ordinary
untagged send, and the session's queue feeds the CLI one text at a time, oldest first. A busy coordinating session
held 17 to 25 queued peer messages and machine sends at a time, so the person's lines waited up to 12 minutes behind
them.

The rule pinned here: `romp send --from-user` marks a text as the person's own (POST /send's `"fromUser": true`, the
only value the kernel accepts). A marked text is inserted at the front of the session's queue, behind any marked texts
already queued (the person's lines keep their own order) and behind every queued slash command (a /clear queued ahead
of where it would land would wipe it from the conversation). The text already handed to the CLI has left the queue and
is never passed. Unmarked texts never move. The marker rides the queued copy's identity, the registry mirror (a kernel
restart), the fed ledger (a re-headed copy), a send the kernel parked in its own queue and that queue's disk mirror,
and the remote forward to the kernel that owns the session.

SYNTHETIC fixtures only: invented text, placeholder uuids, TESTHOST message ids; no real session names or data.
"""
import json
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: both modules resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
sb = load_source("romp_sdk_backend_chat_jump", os.path.join(BIN, "romp_sdk_backend.py"))
km = load_source("romp_kernel_chat_jump", os.path.join(BIN, "romp-kernel"))
km._limit_hold = lambda sid: None      # the account gate is another axis; never read the machine's usage file
km._PROMPT_HOLD_S = 0.0

SID = "11111111-2222-3333-4444-0000000c4a71"     # this module's own synthetic sid
MID1 = "1700000000.111111.TESTHOST"
MID2 = "1700000001.222222.TESTHOST"

HIS1 = "hold the notes-api deploy until the web tests pass"
HIS2 = "and tell me when the api pod is back"
TAGGED = "timer: ten minutes passed, check the build\n\n<!-- romp-tag: timer -->"
UNTAGGED = "wake: the api queue drained"
UNTAGGED2 = "wake: the tests job finished"
WATCH = "[romp] The condition you asked romp to watch now HOLDS: build green.\n\n<!-- romp-injected --><!-- romp-system --><!-- romp-tag: watch -->"


def _banner(mid, body="the notes-api pod is up; please take the next run"):
    return "#### Mail from web\n\n%s\n\n<!-- romp-msg-id: %s -->" % (body, mid)


_QN = [0]


def _qid():
    _QN[0] += 1
    return "echo:%032x" % _QN[0]


class _World:
    """A real SdkBackend and SdkSession, registered without a thread (no loop, no CLI), as the postal tests build it."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                            # a self-minted state root pins per-session hosts off
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        self.sess = self._new_session()
        self.be._ensure = lambda sid, on_boot_settled=None: self.be.sessions.get(sid)

    def _new_session(self):
        reg = sb.read_reg(self.be.state_dir, SID) or {"sid": SID, "name": "api", "mode": "acceptEdits",
                                                      "alive": True, "cwd": self.cwd, "lastSid": SID}
        sb.write_reg(self.be.state_dir, SID, reg)
        s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = s
        return s

    def send(self, text):
        """An ordinary send with an id (the composer's, a peer's `romp send`, a script's)."""
        self.sess.enqueue(text, qid=_qid(), qts=1)

    def his(self, text):
        """A `romp send --from-user` text, as SdkBackend.send queues it."""
        self.sess.enqueue(text, qid=_qid(), qts=1, from_user=True)

    def mail(self, mid):
        self.assertEqual(self.sess.enqueue_postal(_banner(mid), [mid]), [])

    def fed_order(self, sess=None):
        """Every queued text in the order the feeder hands it to the CLI: its own pop, one text at a time."""
        s = sess or self.sess
        out = []
        while True:
            with s._lock:
                if not s._pending:
                    return out
                text, _meta = s._pop_for_feed_locked(0)
            out.append(text)


class HisLinesGoFirst(_World, unittest.TestCase):

    def test_a_marked_text_is_fed_before_mail_tagged_and_untagged_sends(self):
        self.mail(MID1)
        self.send(TAGGED)
        self.send(UNTAGGED)
        self.sess.enqueue(WATCH)                             # a watch notice, queued by the kernel with no id
        self.mail(MID2)
        before = self.sess.pending()                         # whatever order the mail rules gave the rest
        self.assertEqual(len(before), 5)
        self.his(HIS1)
        self.assertEqual(self.fed_order(), [HIS1] + before, "first, with the rest unmoved behind it")

    def test_two_marked_texts_keep_their_order(self):
        self.send(UNTAGGED)
        self.his(HIS1)
        self.mail(MID1)
        self.his(HIS2)
        self.assertEqual(self.fed_order(), [HIS1, HIS2, UNTAGGED, _banner(MID1)],
                         "the second line stops behind the first; everything else keeps its order behind them")

    def test_the_text_handed_to_the_cli_is_never_passed(self):
        self.send(UNTAGGED)
        self.send(UNTAGGED2)
        with self.sess._lock:
            fed, _ = self.sess._pop_for_feed_locked(0)       # handed to the CLI, not yet taken
        self.assertEqual(fed, UNTAGGED)
        self.his(HIS1)
        self.assertEqual(self.sess.pending(), [HIS1, UNTAGGED2],
                         "the handed text left the queue; the marked line goes ahead of what is still queued")

    def test_a_queued_clear_is_never_passed(self):
        self.send(UNTAGGED)
        self.send("/clear")
        self.send(UNTAGGED2)
        self.his(HIS1)
        self.assertEqual(self.fed_order(), [UNTAGGED, "/clear", HIS1, UNTAGGED2],
                         "a marked line ahead of a /clear would be read and then wiped")

    def test_it_stops_behind_every_queued_slash_command(self):
        self.send(UNTAGGED)
        self.send("/clear")
        self.send(TAGGED)
        self.send("/compact keep the deploy notes")
        self.send(UNTAGGED2)
        self.his(HIS1)
        self.assertEqual(self.fed_order(), [UNTAGGED, "/clear", TAGGED, "/compact keep the deploy notes", HIS1, UNTAGGED2])

    def test_a_path_is_not_a_slash_command(self):
        self.send("/tmp/notes-api.log has the trace")        # starts with a slash, but names a path: passable
        self.his(HIS1)
        self.assertEqual(self.fed_order(), [HIS1, "/tmp/notes-api.log has the trace"])

    def test_a_marked_slash_command_stops_a_later_marked_text_behind_it(self):
        self.send(UNTAGGED)
        self.his("/clear")
        self.his(HIS1)
        self.assertEqual(self.fed_order(), ["/clear", HIS1, UNTAGGED])

    def test_unmarked_texts_never_move(self):
        self.his(HIS1)
        self.send(UNTAGGED)
        self.mail(MID1)
        self.send(TAGGED)
        self.send(HIS2)                                      # the same words, unmarked: an ordinary send
        self.assertEqual(self.fed_order(), [HIS1, UNTAGGED, _banner(MID1), TAGGED, HIS2],
                         "with no marker every text joins the back, as before")

    def test_an_empty_queue_takes_the_marked_text_at_the_head(self):
        self.his(HIS1)
        self.assertEqual(self.fed_order(), [HIS1])

    def test_the_identities_stay_aligned_with_the_texts(self):
        self.send(UNTAGGED)
        self.mail(MID1)
        self.his(HIS1)
        meta = self.sess.pending_meta()
        self.assertIsNotNone(meta, "the two lists agree after the insert")
        self.assertEqual([m["md"] for m in meta], [HIS1, UNTAGGED, _banner(MID1)])
        self.assertIsNotNone(meta[0]["qid"], "the marked copy keeps its id")
        self.assertIsNone(meta[2]["qid"], "a banner wears no id")

    def test_the_mirror_writes_the_marker_and_a_restart_keeps_it(self):
        self.send(UNTAGGED)
        self.his(HIS1)
        self.sess._persist_queue()
        reg = sb.read_reg(self.be.state_dir, SID)
        self.assertEqual([m.get("fromUser") for m in reg["queueMeta"]], [True, None])
        again = self._new_session()                          # seeded from the registry mirror, as a restart seeds it
        self.sess = again
        self.his(HIS2)
        self.assertEqual(self.fed_order(again), [HIS1, HIS2, UNTAGGED],
                         "the restored line is still marked, so the new one stops behind it")

    def test_the_mirror_reader_takes_only_true(self):
        reg = {"queue": [UNTAGGED, HIS1],
               "queueMeta": [{"text": UNTAGGED, "qid": "echo:" + "a" * 32, "qts": 1, "fromUser": "yes"},
                             {"text": HIS1, "qid": "echo:" + "b" * 32, "qts": 1, "fromUser": True}]}
        got = sb.queue_meta_from_reg(reg)
        self.assertNotIn("fromUser", got[0])
        self.assertIs(got[1]["fromUser"], True)

    def test_a_re_headed_copy_keeps_its_marker(self):
        self.send(UNTAGGED)
        self.his(HIS1)
        with self.sess._lock:
            fed, _ = self.sess._pop_for_feed_locked(0)       # HIS1 handed to a client that then died untaken
            self.assertEqual(fed, HIS1)
            self.sess._q_prepend([fed], self.sess._unfeed_locked([fed]))   # the stranded re-head
        self.his(HIS2)
        self.assertEqual(self.fed_order(), [HIS1, HIS2, UNTAGGED])

    def test_backend_send_marks_the_copy(self):
        self.assertTrue(self.be.send(SID, UNTAGGED, user=True))
        self.assertTrue(self.be.send(SID, TAGGED))
        self.assertTrue(self.be.send(SID, HIS1, user=True, from_user=True))
        self.assertTrue(self.be.send(SID, UNTAGGED2, user=True))
        self.assertEqual(self.sess.pending(), [HIS1, UNTAGGED, TAGGED, UNTAGGED2])


class _FakeBackend:
    """A backend whose send takes the marker, the way SdkBackend.send does."""

    def __init__(self):
        self.calls = []

    def forwards_sends(self):
        return True                       # the SDK's regime: a drained pile is handed over one send each

    def send(self, sid, text, qid=None, user=False, paths=None, from_user=False):
        self.calls.append((text, user, from_user))
        return True


class _OldBackend:
    """A send with no marker keyword (Codex, a stand-in): called as before."""

    def __init__(self):
        self.calls = []

    def forwards_sends(self):
        return True

    def send(self, sid, text, qid=None, user=False):
        self.calls.append((text, user))
        return True


class KernelCarriesTheMarker(unittest.TestCase):

    def setUp(self):
        self.be = _FakeBackend()
        self._saved = (km._compacting_now, km._working_now, km._push_all, km.Sessions.backend_for,
                       km._host_for_sid, km._remote_forward)
        km._push_all = lambda: None
        km._working_now = lambda sid: False
        km._compacting_now = lambda sid: False
        km.Sessions.backend_for = lambda sid: self.be
        km._host_for_sid = lambda sid: None
        km._pending_ops.clear()

    def tearDown(self):
        (km._compacting_now, km._working_now, km._push_all, km.Sessions.backend_for,
         km._host_for_sid, km._remote_forward) = self._saved
        km._pending_ops.clear()

    def test_the_send_body_takes_true_and_refuses_anything_else(self):
        ok = km._parse_send_body(json.dumps({"name": "api", "text": HIS1, "fromUser": True}).encode())
        self.assertEqual(ok, {"who": "api", "text": HIS1, "fromUser": True})
        self.assertEqual(km._parse_send_body(json.dumps({"name": "api", "text": UNTAGGED}).encode()),
                         {"who": "api", "text": UNTAGGED}, "no marker: the body reads as before")
        for bad in (False, "true", 1, None):
            self.assertIsNone(km._parse_send_body(json.dumps({"name": "api", "text": HIS1, "fromUser": bad}).encode()),
                              "a marker the kernel cannot honour (%r) fails the whole send, as a malformed tag does" % (bad,))

    def test_the_send_body_refuses_a_tagged_marked_text(self):
        self.assertIsNone(km._parse_send_body(json.dumps({"name": "api", "text": HIS1, "tag": "timer",
                                                          "fromUser": True}).encode()),
                          "a tag says machine-sent; the marker says the person's own words; both cannot hold")

    def test_a_handed_over_send_carries_the_marker(self):
        km._send_or_park(self.be, SID, HIS1, user=True, from_user=True)
        km._send_or_park(self.be, SID, UNTAGGED, user=True)
        self.assertEqual(self.be.calls, [(HIS1, True, True), (UNTAGGED, True, False)])

    def test_a_backend_without_the_keyword_is_called_as_before(self):
        old = _OldBackend()
        km._send_or_park(old, SID, HIS1, user=True, from_user=True)
        self.assertEqual(old.calls, [(HIS1, True)])

    def test_a_parked_send_keeps_the_marker_through_the_drain(self):
        km._compacting_now = lambda sid: True
        km._send_or_park(self.be, SID, HIS1, user=True, from_user=True)
        km._send_or_park(self.be, SID, UNTAGGED, user=True)
        self.assertEqual(self.be.calls, [])
        km._compacting_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [(HIS1, True, True), (UNTAGGED, True, False)])

    def test_the_parked_queue_mirror_keeps_the_marker_across_a_restart(self):
        km._compacting_now = lambda sid: True
        km._send_or_park(self.be, SID, HIS1, user=True, from_user=True, qid="echo:" + "c" * 32)
        km._send_or_park(self.be, SID, UNTAGGED, user=True)
        km._save_pending_ops()
        restored = km._load_pending_ops()                     # what the next kernel reads at boot
        ops = restored[SID]
        self.assertEqual([km._op_from_user(op) for op in ops], [True, False])
        self.assertEqual(km._op_qid(ops[0]), "echo:" + "c" * 32, "the id slot is untouched")
        self.assertTrue(km._op_user(ops[0]))

    def test_deliver_text_passes_the_marker_down(self):
        ok, err, _queued = km._deliver_text(SID, HIS1, from_user=True)
        self.assertTrue(ok, err)
        ok, err, _queued = km._deliver_text(SID, UNTAGGED)
        self.assertTrue(ok, err)
        self.assertEqual(self.be.calls, [(HIS1, True, True), (UNTAGGED, True, False)])

    def test_the_remote_forward_carries_the_marker(self):
        bodies = []
        km._host_for_sid = lambda sid: {"host": "TESTHOST"}
        km._remote_forward = lambda r, path, body: bodies.append((path, body)) or {"ok": True}
        km._deliver_text(SID, HIS1, from_user=True)
        km._deliver_text(SID, UNTAGGED)
        self.assertEqual(bodies, [("/send", {"id": SID, "text": HIS1, "fromUser": True}),
                                  ("/send", {"id": SID, "text": UNTAGGED})])
        self.assertEqual(self.be.calls, [], "a remote session's text goes to its own kernel, not to a local backend")

    def test_the_route_reads_the_marker_from_the_body(self):
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        route = src.split('u.path == "/send"')[1][:3000]
        self.assertIn('_deliver_text(sid, body["text"], from_user=bool(body.get("fromUser")))', route)


class CliMarksHisLines(unittest.TestCase):
    """`romp send --from-user` names `"fromUser": true` in its POST /send body; without the flag it names nothing."""

    def test_the_payload_line_carries_the_marker_only_when_asked(self):
        src = open(os.path.join(BIN, "romp"), encoding="utf-8").read()
        body = src.split('if [[ "$_verb" == "send" ]]; then', 1)[1].split("elif [[", 1)[0]
        line = [ln for ln in body.splitlines() if "_payload=" in ln][0].strip()
        for flag, want in (("1", {"name": "api", "text": HIS1, "fromUser": True}),
                           ("", {"name": "api", "text": HIS1})):
            out = subprocess.run(["bash", "-c", '_who=api; _text="$1"; _from_user="$2"; %s; printf %%s "$_payload"' % line,
                                  "x", HIS1, flag], capture_output=True, text=True, check=True).stdout
            self.assertEqual(json.loads(out), want)


if __name__ == "__main__":
    unittest.main()
