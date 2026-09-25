#!/usr/bin/env python3
"""Re-delivery has an AGE LINE and a REFUSED flag (2026-09-12). The boot and dead-spawn re-delivery arm
(_mark_dropped_echoes) re-feeds a human send whose text the transcript scan cannot find. A landing the scan
cannot see is re-fed at EVERY restart, forever: a kernel whose scan read only native user records (never the
queued_command attachment a mid-turn feed lands as) and only the last 2 MB of the file re-fed the same texts,
some days old, at two restarts in one night, one text landing six times. So: a send older than
REDELIVER_MAX_AGE_S at the restart is not re-fed and not scanned — it takes the flag path (dropped, kept in
the chat as never-delivered) and ONE notice card per session per restart (dropped_sends_card, through the
backend's post_notice door, plans/notice-cards.md) offers each dropped message back with a Send again button;
nothing is injected into the session (the user 2026-09-15, who rejected the notice the card replaced). A send
the prompt gate REFUSED is flagged dropped + refused at the refusal, so it is never re-fed; both flags ride
the registry mirror across restarts. The queue proper (sends never fed) is not under the line. On the BOOT road the
card is posted once the kernel has wired the notice door (TheBootRoad): the constructor's reseed parks it, the
kernel posts it. SYNTHETIC."""
import inspect
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_stale0", os.path.join(BIN, "romp-event-model"))
sb = load_source("romp_sdk_backend_stale", os.path.join(HERE, "..", "kernel", "sdk_backend.py"))

SID = "11111111-2222-3333-4444-666666666666"
SID2 = "11111111-2222-3333-4444-666666666667"    # a second session on the boot road (TheBootRoad's two-card case)
NOW = FRESH_T = STALE_T = 0              # set per test by Fixture.setUp (_reset_clock), never at import


def _reset_clock():
    """Every stamp is relative to the clock the TEST runs on, read in setUp — never at import. pytest imports
    each module at collection and runs this one minutes later under the full suite, while the code under test
    reads time.time() at the call: an import-time NOW put the send planted 120 s INSIDE the line past it by the
    time the arm ran (CI 2026-09-12: both sends dropped, the standalone run green at every timezone)."""
    global NOW, FRESH_T, STALE_T
    NOW = int(time.time())
    FRESH_T = NOW - 60                       # a minute before the restart: inside any sane line
    STALE_T = NOW - 3 * 86400                # three days before it: the pile the field measured


class Fixture(unittest.TestCase):
    def setUp(self):
        _reset_clock()
        self.td = tempfile.mkdtemp()
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.td, "claude")
        self.cwd = os.path.join(self.td, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        self.tpath = tp
        open(tp, "w").close()                       # an empty transcript: nothing has landed

        class BE:
            state_dir = None
            _live = {}
            _reg_lock = __import__("threading").RLock()
            _live_lock = __import__("threading").RLock()
            _persisted = []
            _logs = []
            _problems = []
            _forgotten = []
            _notices = []                            # every post_notice call, as the kernel's door would see it

            @staticmethod
            def on_notice(sid, key, title, body="", **kw):
                """The kernel's door (type(backend).on_notice = staticmethod(post_notice)), captured: the row the
                kernel would append, with the rev counting the posts under the key."""
                rev = 1 + sum(1 for n in BE._notices if n["sid"] == sid and n["key"] == key)
                BE._notices.append(dict(sid=sid, key=key, title=title, body=body, **kw))
                return {"op": "post", "sid": sid, "key": key, "rev": rev, "title": title}, None

            def _log(self, msg, problem=False, **kw):
                self._logs.append(msg)
                if problem:
                    self._problems.append(msg)

            def _persist_echoes(self, sid):
                self._persisted.append(sid)

            def _wake_push(self):
                pass

            def _touch_live(self, sid):
                pass

            def forget_fed(self, sid, uuid_):
                self._forgotten.append(uuid_)
        self.be = BE()
        import pathlib
        self.be.state_dir = pathlib.Path(self.td)
        for name in ("_mark_dropped_echoes", "_text_landed", "mark_echo_refused", "post_notice"):
            setattr(self.be, name, getattr(sb.SdkBackend, name).__get__(self.be))
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "alive": True, "cwd": self.cwd,
                                              "lastSid": SID, "queue": []})
        self._line = sb.REDELIVER_MAX_AGE_S

    def tearDown(self):
        self.be._live.clear()
        sb.REDELIVER_MAX_AGE_S = self._line
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def _echo(self, text, t, author="human"):
        d = self.be._live.setdefault(SID, {})
        key = "echo:%d" % (len(d) + 1)               # one key per stash: two echoes may wear one text
        d[key] = {"_echo_text": text, "author": author, "t": t, "uuid": key}
        return d[key]

    def _reg_queue(self):
        return (sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or []

    def _the_card(self):
        """The ONE notice the arm posted, with the kernel's own rules on its shape checked (the door the stand-in
        captures validates nothing): the key grammar, the producer, the caps on actions and labels, a /send body
        that names only text, never a target and never a command."""
        self.assertEqual(len(self.be._notices), 1, "one card per session per restart: %r" % ([n["title"] for n in self.be._notices],))
        n = self.be._notices[0]
        self.assertEqual((n["sid"], n["key"], n["producer"]), (SID, "dropped-sends", "dropped-sends"))
        self.assertTrue(n["needs_you"], "re-sending the user's own words is their call: needsYou")
        self.assertTrue(n["dismiss_on_action"], "a Send again spends the card")
        self.assertIsInstance(n["t"], int); self.assertLessEqual(abs(n["t"] - NOW), 5, "t is the restart, not the send")
        self.assertLessEqual(len(n["title"]), 200); self.assertLessEqual(len(n["actions"]), 4)
        for a in n["actions"]:
            self.assertEqual(set(a), {"label", "route", "body"}); self.assertEqual(a["route"], "/send")
            self.assertTrue(0 < len(a["label"]) <= 60, a["label"])
            self.assertEqual(set(a["body"]), {"text"}, "the notice's own session is the target: no id, no name")
            self.assertTrue(a["body"]["text"].strip() and not a["body"]["text"].lstrip().startswith("/"))
        for text in [n["title"], n["body"]] + [a["label"] for a in n["actions"]]:
            for word in ("[romp]", "romp-", "card", "board", "goal", "column", "nudge"):
                self.assertNotIn(word, text, "the user's terms, no romp vocabulary: %r" % text)
        return n

    def _no_session_notice(self, *queues):
        """Nothing romp-authored reached any queue: the session is never told about the drop (the user 2026-09-15)."""
        for q in queues:
            for t in q:
                self.assertNotIn("[romp]", t); self.assertNotIn("romp-injected", t); self.assertNotIn("stale", t)


class TheAgeLine(Fixture):
    def test_a_send_past_the_line_is_not_refed_and_one_card_offers_it_back(self):
        fresh = self._echo("fresh typed words", FRESH_T)
        stale = self._echo("three day old words", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        q = self._reg_queue()
        self.assertEqual(q, ["fresh typed words"], "the fresh send is re-delivered exactly as before, and NOTHING else is queued")
        self._no_session_notice(q)
        n = self._the_card()
        self.assertEqual(n["title"], "1 message you typed before the restart was not re-sent", "no name on the registry row: none in the title")
        self.assertIn(time.strftime("%Y-%m-%d %H:%M", time.localtime(STALE_T)), n["body"], "the body names the dropped send's stamp")
        self.assertIn("three day old words", n["body"]); self.assertIn("older than 30 min", n["body"], "…and the age line")
        self.assertNotIn("fresh typed words", n["body"], "a re-delivered send is not on the card")
        self.assertEqual(n["actions"], [{"label": "Send again", "route": "/send", "body": {"text": "three day old words"}}])
        self.assertNotIn("three day old words", q, "the stale send never re-enters the queue")
        self.assertTrue(stale.get("dropped") and stale.get("stale"), "the stale send takes the flag path and says why")
        self.assertNotIn("dropped", fresh, "the fresh send is queued, not lost")
        rows = [p for p in self.be._problems if "age line" in p]
        self.assertEqual(len(rows), 1, "one problem row for the whole drop, not one per send")
        self.assertIn("offered back on a card (key=dropped-sends rev=1)", rows[0], "the post's row is logged")

    def test_two_or_three_stale_sends_get_a_button_each_and_send_all_again(self):
        self._echo("newer stale words", STALE_T)
        self._echo("older stale words", STALE_T - 3600)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), [], "no re-delivery, no notice: the queue is untouched")
        n = self._the_card()
        self.assertEqual(n["title"], "2 messages you typed before the restart were not re-sent")
        body = n["body"]
        self.assertLess(body.index("older stale words"), body.index("newer stale words"), "listed in send order")
        self.assertIn(time.strftime("%Y-%m-%d %H:%M", time.localtime(STALE_T - 3600)), body)
        self.assertIn(time.strftime("%Y-%m-%d %H:%M", time.localtime(STALE_T)), body)
        self.assertEqual([a["label"] for a in n["actions"]], ["Send again: older stale words", "Send again: newer stale words", "Send all again"])
        self.assertEqual([a["body"]["text"] for a in n["actions"]],
                         ["older stale words", "newer stale words", "older stale words\n\nnewer stale words"],
                         "Send all again carries them as one message, in send order")
        self.assertIn("Send all again re-sends them as one message, in order", body)

    def test_four_or_more_stale_sends_get_send_all_again_alone(self):
        texts = ["stale words %d" % i for i in range(4)]
        for i, t in enumerate(texts):
            self._echo(t, STALE_T + i)
        self.be._mark_dropped_echoes(SID, [])
        n = self._the_card()
        self.assertEqual(n["title"], "4 messages you typed before the restart were not re-sent")
        self.assertEqual([a["label"] for a in n["actions"]], ["Send all again"], "five buttons would be refused; one for all of them")
        self.assertEqual(n["actions"][0]["body"]["text"], "\n\n".join(texts))
        self.assertIn("restore it from the chat", n["body"], "the way to re-send one alone is named")
        for t in texts:
            self.assertIn(t, n["body"])

    def test_a_typed_command_gets_no_button_and_the_body_says_to_type_it_again(self):
        self._echo("/clear", STALE_T)
        self._echo("real typed words", STALE_T + 60)
        self.be._mark_dropped_echoes(SID, [])
        n = self._the_card()
        self.assertEqual(n["actions"], [{"label": "Send again", "route": "/send", "body": {"text": "real typed words"}}],
                         "a stored action may never begin with a slash: the command gets no button")
        self.assertIn("/clear", n["body"]); self.assertIn("type it again", n["body"])
        self.be._notices.clear(); self.be._live.clear()
        self._echo("  /model opus", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        n = self._the_card()
        self.assertEqual(n["actions"], [], "a lone command: the card stands with no button")

    def test_the_card_names_the_session_from_its_registry_row(self):
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "name": "web", "alive": True, "cwd": self.cwd, "lastSid": SID, "queue": []})
        self._echo("three day old words", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._the_card()["title"], "1 message you typed to web before the restart was not re-sent")

    def test_the_builder_bounds_the_body_and_the_labels(self):
        long = "a very long typed message " * 20
        stale = [{"_echo_text": long, "t": STALE_T}, {"_echo_text": "short one", "t": STALE_T + 1}]
        title, body, acts = sb.dropped_sends_card("api", stale, NOW, 1800)
        self.assertEqual(title, "2 messages you typed to api before the restart were not re-sent")
        self.assertEqual(len(acts[0]["label"]), 60); self.assertTrue(acts[0]["label"].startswith("Send again: a very long"))
        self.assertTrue(acts[0]["label"].endswith("\u2026")); self.assertEqual(acts[0]["body"]["text"], long, "the label is cut, the text never is")
        line = next(l for l in body.splitlines() if "a very long typed message" in l)
        prefix = "- %s: " % time.strftime("%Y-%m-%d %H:%M", time.localtime(STALE_T))
        self.assertTrue(line.startswith(prefix), line[:40])
        self.assertEqual(len(line), len(prefix) + sb.DROPPED_SENDS_LINE_CHARS, "each listed message is one bounded line")
        self.assertTrue(line.endswith("\u2026"))
        many = [{"_echo_text": "m%d" % i, "t": STALE_T + i} for i in range(sb.DROPPED_SENDS_LIST_MAX + 3)]
        _, body, acts = sb.dropped_sends_card("", many, NOW, 1800)
        self.assertIn("- and 3 more, in the chat", body); self.assertEqual(body.count("\n- "), sb.DROPPED_SENDS_LIST_MAX + 1)
        self.assertEqual([a["label"] for a in acts], ["Send all again"])
        self.assertIn(time.strftime("%Y-%m-%d %H:%M", time.localtime(NOW)), body, "the restart time opens the body")

    def test_a_stale_send_is_not_scanned(self):
        # the scan is the expensive step (a mark-less echo streams the whole transcript); the line runs first
        self._echo("three day old words", STALE_T)
        calls = []
        self.be._text_landed = lambda *a, **k: calls.append(a) or False
        with mock.patch.object(sb, "_input_landed_after", side_effect=AssertionError("scanned a stale send")):
            self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(calls, [], "no transcript scan for a send past the line")

    def test_the_line_is_measured_from_the_send_stamp_not_the_text(self):
        # a send just inside the line is re-fed; one just outside is dropped — the stamp decides
        inside = self._echo("inside the line", NOW - int(sb.REDELIVER_MAX_AGE_S) + 120)
        outside = self._echo("outside the line", NOW - int(sb.REDELIVER_MAX_AGE_S) - 120)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), ["inside the line"])
        self.assertTrue(outside.get("stale"))
        self.assertNotIn("dropped", inside)
        self.assertEqual([a["body"]["text"] for a in self._the_card()["actions"]], ["outside the line"])

    def test_the_line_can_be_switched_off(self):
        sb.REDELIVER_MAX_AGE_S = 0
        self._echo("three day old words", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), ["three day old words"], "0 restores the unbounded re-feed")
        self.assertEqual(self.be._notices, [], "nothing dropped, no card")

    def test_the_line_never_touches_the_queue_proper(self):
        # a send still in reg['queue'] (never fed) is the person's words waiting their turn: it stays, whatever its age
        self._echo("waiting its turn", STALE_T)
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "alive": True, "cwd": self.cwd,
                                              "lastSid": SID, "queue": ["waiting its turn"]})
        self.be._mark_dropped_echoes(SID, ["waiting its turn"])
        self.assertEqual(self._reg_queue(), ["waiting its turn"])
        self.assertFalse(any(a.get("dropped") for a in self.be._live[SID].values()))
        self.assertEqual(self.be._notices, [])

    def test_a_stale_romp_authored_echo_makes_no_card(self):
        self._echo("an old nudge body", STALE_T, author="romp")
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), [], "a nudge was never re-fed; nothing to announce")
        self.assertTrue(all(a.get("dropped") for a in self.be._live[SID].values()))
        self.assertEqual(self.be._notices, [], "regenerable machinery, not the user's words: no card")

    def test_a_second_call_in_the_same_boot_posts_nothing(self):
        # the boot reseed and the fresh spawn both run the arm for one session; the flags the first call wrote take the
        # same sends out of the second call's selection, so the card is posted once (plans/notice-cards.md: dedupe within
        # one boot); a later restart that drops MORE is a new revision, new information
        self._echo("three day old words", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        self.be._mark_dropped_echoes(SID, [])
        self._the_card()
        self._echo("other old words", STALE_T + 5)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual([n["title"] for n in self.be._notices][1], "1 message you typed before the restart was not re-sent")
        self.assertEqual(self.be._problems[-1].count("rev=2"), 1, "the second post is the key's second revision")

    def test_a_live_session_is_told_nothing_and_the_card_wears_its_name(self):
        class S:
            name = "api"
            def __init__(self):
                self.q = []
            def pending(self):
                return list(self.q)
            def enqueue(self, text, qid=None, qts=None):
                self.q.append(text)
        s = S()
        self.be.sessions = {SID: s}
        self.be._lock = __import__("threading").RLock()
        self._echo("fresh typed words", FRESH_T)
        self._echo("three day old words", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(s.q, ["fresh typed words"], "the re-delivered send, and NO notice behind it")
        self._no_session_notice(s.q, self._reg_queue())
        self.assertEqual(self._reg_queue(), [], "the live session's queue is authoritative; the reg is not written")
        self.assertEqual(self._the_card()["title"], "1 message you typed to api before the restart was not re-sent")

    def test_a_refused_post_is_said_in_the_problem_row_and_never_becomes_a_session_notice(self):
        # the kernel's door answers (None, why) for a refusal (an unknown session, a bad key): loud in the error centre,
        # nothing queued to the session, the drop itself unchanged
        type(self.be).on_notice = staticmethod(lambda sid, key, title, body="", **kw: (None, 'no session answers to "%s"' % sid))
        stale = self._echo("three day old words", STALE_T)
        self._echo("fresh typed words", FRESH_T)
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), ["fresh typed words"])
        self._no_session_notice(self._reg_queue())
        self.assertTrue(stale.get("dropped") and stale.get("stale"), "the drop stands whatever the card did")
        rows = [p for p in self.be._problems if "age line" in p]
        self.assertEqual(len(rows), 1); self.assertIn("could not be posted: no session answers to", rows[0])
        # a door that RAISES is caught by the backend's post_notice, never the arm's death
        self.be._live.clear(); self.be._problems.clear()
        def boom(*a, **k):
            raise RuntimeError("the store is read-only")
        type(self.be).on_notice = staticmethod(boom)
        self._echo("three day old words", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        self.assertIn("could not be posted: the notice could not be posted (the store is read-only)", self.be._problems[-1])
        # a stand-in with no door at all: the same loud row
        self.be._live.clear(); self.be._problems.clear()
        del self.be.post_notice
        self._echo("three day old words", STALE_T)
        self.be._mark_dropped_echoes(SID, [])
        self.assertIn("could not be posted: no notice door on this backend", self.be._problems[-1])

    def test_the_session_notice_is_gone_from_the_source(self):
        # the pin: no builder for an injected line remains, and the arm enqueues nothing but re-delivered sends
        self.assertFalse(hasattr(sb, "stale_redelivery_notice"))
        src = inspect.getsource(sb.SdkBackend._mark_dropped_echoes)
        self.assertNotIn("s.enqueue(notice", src); self.assertNotIn("add.append(notice", src)
        self.assertIn("post(sid, DROPPED_SENDS_KEY, title, body, producer=DROPPED_SENDS_KEY, needs_you=True", src)
        self.assertNotIn("[romp]", inspect.getsource(sb.dropped_sends_card))


class TheReference(unittest.TestCase):
    def test_the_reference_says_an_edit_to_the_line_waits_for_a_manager_restart(self):
        # REDELIVER_MAX_AGE_S is read at import, inside the kernel, from the environment the manager hands every kernel
        # it spawns; the manager takes service.env only when it starts. The reference said to set it in service.env
        # "then a restart", which reads as if `romp refresh` (a kernel restart) applied the edit.
        with open(os.path.join(os.path.dirname(HERE), "docs", "reference.md"), encoding="utf-8") as f:
            text = f.read()
        at = text.index("- `ROMP_REDELIVER_MAX_AGE_S=<seconds>`")
        doc = " ".join(text[at:text.index("\n- ", at + 1)].split())
        self.assertIn("from the environment the manager hands it", doc)
        self.assertIn("restart the manager (`romp down`, then `romp up`)", doc)
        self.assertNotIn("then a restart", doc, "a kernel restart does not reread service.env")


class TheRefusedFlag(Fixture):
    def test_a_refused_send_is_flagged_and_never_refed(self):
        a = self._echo("a prompt the gate refused", FRESH_T)
        self.assertEqual(self.be.mark_echo_refused(SID, "a prompt the gate refused", "replayed schedule slot"), 1)
        self.assertTrue(a.get("dropped") and a.get("refused"))
        self.assertEqual(self.be._persisted, [SID], "the flags ride the mirror at once")
        self.assertEqual(self.be._forgotten, [a["uuid"]], "its landing will never come")
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(self._reg_queue(), [], "a refused send is not a lost send: nothing re-fed, no notice")

    def test_the_mark_matches_under_the_echo_key_and_skips_landed_and_other_texts(self):
        a = self._echo("  a prompt the gate refused \n", FRESH_T)
        b = self._echo("another message entirely", FRESH_T)
        c = self._echo("a prompt the gate refused", FRESH_T - 5)
        c["_landed"] = True
        self.assertEqual(self.be.mark_echo_refused(SID, "a prompt the gate refused"), 1)
        self.assertTrue(a.get("refused"))
        self.assertNotIn("refused", b)
        self.assertNotIn("refused", c, "a landed echo is not refused: its record exists")
        self.assertEqual(self.be.mark_echo_refused(SID, ""), 0)
        self.assertEqual(self.be.mark_echo_refused(SID, "nothing wears this"), 0)

    def _replay(self, logs=None):
        """The real gate over a real backend (tests/test_cron_replay_dedupe.py's shape): the first fire records the
        slot, and a FRESH session's second fire of the same prompt is the restart replay. An echo wearing the prompt
        is stashed live before that fire. Returns (backend, the fresh session, the prompt, the state dir)."""
        import asyncio
        from pathlib import Path
        d = tempfile.mkdtemp()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=(logs.append if logs is not None else
                                                                      lambda *a, **k: None))
        cron, prompt = "* * * * *", "ping me every minute"
        sb.write_reg(Path(d), SID, {
            "sid": SID, "name": "web", "cwd": "/tmp", "alive": True,
            "sessionCrons": [{"id": "c1", "cron": cron, "prompt": prompt, "kind": "cron", "recurring": True,
                              "armedAt": time.time() - 3600, "dueEpoch": None, "procGen": "gen-A"}]})
        first = sb.SdkSession(be, sb.read_reg(Path(d), SID))
        self.assertEqual(asyncio.run(first._prompt_submit_hook({"prompt": prompt}, None, None)), {},
                         "the first fire of the slot runs and is recorded")
        be._stash_live(SID, "echo:refused1", {"type": "user", "uuid": "echo:refused1", "session_id": SID,
                                              "t": FRESH_T, "author": "human", "_echo_text": prompt,
                                              "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
        return be, sb.SdkSession(be, sb.read_reg(Path(d), SID)), prompt, Path(d)

    @staticmethod
    def _mark_waits(be, stall_s=0.0):
        """Wrap the backend's mark so a test can WAIT for it (and stall it): the gate issues the mark beside its
        verdict, on a thread of its own, so the verdict comes back before the flag is on. Returns the Event the
        wrapped mark sets when it has run."""
        real, done = be.mark_echo_refused, threading.Event()

        def wrapped(sid, text, reason=""):
            try:
                if stall_s:
                    time.sleep(stall_s)
                return real(sid, text, reason)
            finally:
                done.set()
        be.mark_echo_refused = wrapped
        return done

    def _timed_fire(self, sess, prompt):
        import asyncio

        async def run():
            t0 = time.monotonic()
            out = await sess._prompt_submit_hook({"prompt": prompt}, None, None)
            return out, time.monotonic() - t0
        return asyncio.run(run())

    def test_the_gates_block_marks_the_echo(self):
        # The block flags the echo wearing that prompt refused, so the next boot's re-delivery never treats it as a
        # lost send; the flag rides the mirror at once.
        be, again, prompt, d = self._replay()
        done = self._mark_waits(be)
        out, _ = self._timed_fire(again, prompt)
        self.assertEqual(out.get("decision"), "block", "the replayed slot is refused")
        self.assertTrue(done.wait(5), "the mark ran")
        a = be._live[SID]["echo:refused1"]
        self.assertTrue(a.get("dropped") and a.get("refused"), "…and the echo wearing it is flagged refused")
        mirror = (sb.read_reg(d, SID) or {}).get("echoes") or []
        self.assertTrue(any(e.get("refused") for e in mirror), "the flag is on the mirror already")

    def test_a_stalled_mark_never_turns_the_block_into_an_allow(self):
        """The mark is a reg write. Awaited INSIDE _prompt_submit_hook's cap (asyncio.wait_for, {} = allowed on a
        timeout), a stalled write ran the cap out AFTER the verdict was decided: the block came back as an allow and
        the replayed schedule fired anyway (the pull request review, 2026-09-14). So the verdict is returned first
        and the mark runs beside it, outside the cap: with the mark stalled far past a 50 ms cap, the block still
        arrives inside the cap, the cap never trips, and the flag lands once the mark is through."""
        logs = []
        be, again, prompt, d = self._replay(logs)
        done = self._mark_waits(be, stall_s=0.6)
        before = os.environ.get("ROMP_PROMPT_HOOK_TIMEOUT_S")
        os.environ["ROMP_PROMPT_HOOK_TIMEOUT_S"] = "0.05"
        self.addCleanup(lambda: os.environ.pop("ROMP_PROMPT_HOOK_TIMEOUT_S", None) if before is None
                        else os.environ.__setitem__("ROMP_PROMPT_HOOK_TIMEOUT_S", before))
        out, took = self._timed_fire(again, prompt)
        self.assertEqual(out.get("decision"), "block", "the verdict does not wait on the mark")
        self.assertLess(took, 0.5, "…and arrives inside the cap, not after the stalled write")
        self.assertFalse(any("ran past its" in str(m) for m in logs), "the cap never tripped: nothing was under it")
        self.assertTrue(done.wait(5), "the mark still ran, outside the cap")
        a = be._live[SID]["echo:refused1"]
        self.assertTrue(a.get("dropped") and a.get("refused"), "…and flagged the echo once it was through")
        self.assertTrue(any(e.get("refused") for e in ((sb.read_reg(d, SID) or {}).get("echoes") or [])))


class TheMirror(Fixture):
    def test_stale_and_refused_ride_the_mirror_both_ways(self):
        writes = []
        self.be._update_reg = lambda sid, **kw: writes.append(kw)
        self.be._persist_echoes = sb.SdkBackend._persist_echoes.__get__(self.be)
        a = self._echo("three day old words", STALE_T)
        a["dropped"] = a["stale"] = True
        b = self._echo("a prompt the gate refused", FRESH_T)
        b["dropped"] = b["refused"] = True
        self.be._persist_echoes(SID)
        snap = {e["text"]: e for e in writes[-1]["echoes"]}
        self.assertTrue(snap["three day old words"].get("stale") and snap["three day old words"].get("dropped"))
        self.assertTrue(snap["a prompt the gate refused"].get("refused"))
        self.assertNotIn("refused", snap["three day old words"])
        stashed = []
        self.be._stash_live = lambda sid, key, atom: stashed.append(atom)
        self.be._reseed_echoes = sb.SdkBackend._reseed_echoes.__get__(self.be)
        self.be._live.clear()                         # nothing live → the reseed stashes and stops
        self.be._reseed_echoes([{"sid": SID, "alive": True, "echoes": writes[-1]["echoes"], "queue": []}])
        by = {x["_echo_text"]: x for x in stashed}
        self.assertTrue(by["three day old words"].get("dropped") and by["three day old words"].get("stale"))
        self.assertTrue(by["a prompt the gate refused"].get("dropped") and by["a prompt the gate refused"].get("refused"))


class TheBootRoad(unittest.TestCase):
    """The card on the BOOT road, over a REAL backend wired the kernel's way. The kernel wires the notice door on the
    backend's class AFTER the constructor returns, and the constructor's echo reseed runs the age line synchronously: a
    held human send older than the line at the restart was flagged dropped and stale (the flags on the mirror) with NO
    card, the flags kept it out of every later boot's selection, and the card promised for the first restart after the
    line was never posted. The reseed PARKS the card (the flag writes, the queue re-add and the mirror write stay
    synchronous, as before) and the kernel posts it once the door is wired, through post_boot_notices, on a thread of its
    own: the kernel calls it while it still holds its construction lock, which the door's session check re-enters
    (Sessions.live() through _sdk()), so the thread waits the lock out where a synchronous post would deadlock the boot.
    The door here is the kernel's own class-level assignment, captured; the backend is the real class over its own state
    root, which wears the hosts floor (a root with no session-hosts file is on, and a hosted connect starts a real host).
    The constructor is built with reconcile off, its default: no thread starts, no session connects. Two of the claims
    are read at the moment they are about: the flags are on the mirror when the constructor RETURNS, before any door is
    wired (a mirror write parked with the post would pass a read after the join), and a parked post that raises is one
    problem row with the next parked post still run (a drainer without its guard dies at the first raise)."""

    def setUp(self):
        _reset_clock()
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        open(os.path.join(self.td, "session-hosts"), "w").write("off")     # the hosts floor: a real backend over its own root
        before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.td, "claude")   # hermetic: no read of a real account file
        self.addCleanup(lambda: os.environ.pop("CLAUDE_CONFIG_DIR", None) if before is None
                        else os.environ.__setitem__("CLAUDE_CONFIG_DIR", before))
        self.posted, self.logs = [], []
        self.assertNotIn("on_notice", vars(sb.SdkBackend), "precondition: no door on the class before the kernel wires one")

    def tearDown(self):
        if "on_notice" in vars(sb.SdkBackend):
            del sb.SdkBackend.on_notice                     # the class is shared with every other test in this module

    def _stale_reg(self, sid=SID):
        sb.write_reg(self.td, sid, {"sid": sid, "alive": True, "cwd": self.td, "lastSid": sid, "queue": [],
                                    "echoes": [{"t": STALE_T, "text": "three day old words", "author": "human",
                                                "uuid": "echo:boot1", "rompAuto": False, "dropped": False}]})

    def _door(self, sid, key, title, body="", **kw):
        """The kernel's door (post_notice), captured: one row per post, the rev counting the posts under the key."""
        rev = 1 + sum(1 for n in self.posted if n["sid"] == sid and n["key"] == key)
        self.posted.append(dict(sid=sid, key=key, title=title, body=body, **kw))
        return {"op": "post", "sid": sid, "key": key, "rev": rev, "title": title}, None

    def _boot(self, door=None):
        """One kernel boot in the kernel's order: construct (the reseed runs the age line), wire the door on the CLASS, post
        the parked cards; the posting thread is joined so the assertions read a finished boot. Returns (backend, thread).
        self.at_return is what the constructor's RETURN left, read before the door exists: (the live atom, the mirror
        row) for SID, copied, so a case can hold the constructor to the synchronous half it promises."""
        before = len(self.posted)
        be = sb.SdkBackend(self.td, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        self.assertEqual(len(self.posted), before, "no post from inside the constructor: the door is wired after it")
        self.at_return = (dict((be._live.get(SID) or {}).get("echo:boot1") or {}), sb.read_reg(self.td, SID) or {})
        type(be).on_notice = staticmethod(door or self._door)   # the kernel's line, after the constructor has returned
        th = be.post_boot_notices()
        if th is not None:
            th.join(10)
            self.assertFalse(th.is_alive(), "the posting thread finished")
        return be, th

    def test_the_first_boot_posts_the_card_once_and_the_next_boot_posts_nothing(self):
        self._stale_reg()
        be, th = self._boot()
        self.assertIsNotNone(th, "the boot had a card to post")
        # the synchronous half stands as before, and it is the CONSTRUCTOR's: the flags were live and on the mirror when
        # it returned, before the door was wired or the post ran (a mirror write parked with the post would fail here and
        # pass the reads below, which follow the join)
        atom0, reg0 = self.at_return
        self.assertTrue(atom0.get("dropped") and atom0.get("stale"), atom0)
        self.assertTrue(reg0.get("echoes") and reg0["echoes"][0].get("dropped") and reg0["echoes"][0].get("stale"),
                        "the flags reach the mirror inside the constructor: %r" % (reg0.get("echoes"),))
        a = be._live[SID]["echo:boot1"]
        self.assertTrue(a.get("dropped") and a.get("stale"), "the stale send takes the flag path at the reseed")
        reg = sb.read_reg(be.state_dir, SID) or {}
        self.assertTrue(reg.get("echoes") and reg["echoes"][0].get("dropped") and reg["echoes"][0].get("stale"), reg.get("echoes"))
        self.assertEqual(reg.get("queue") or [], [], "nothing re-fed, nothing injected")
        # exactly one post carrying the dropped key, and exactly one card: revision 1 under it
        self.assertEqual(len(self.posted), 1, [n["title"] for n in self.posted])
        n = self.posted[0]
        self.assertEqual((n["sid"], n["key"], n["producer"], n["needs_you"], n["dismiss_on_action"]),
                         (SID, "dropped-sends", "dropped-sends", True, True))
        self.assertEqual(n["title"], "1 message you typed before the restart was not re-sent")
        self.assertEqual(n["actions"], [{"label": "Send again", "route": "/send", "body": {"text": "three day old words"}}])
        self.assertIn("three day old words", n["body"])
        rows = [str(m) for m in self.logs if "age line" in str(m)]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("offered back on a card (key=dropped-sends rev=1)", rows[0], "the post's outcome rides the problem row")
        self.assertIsNone(be.post_boot_notices(), "a second call posts nothing: the parked cards were taken whole")
        self.assertEqual(len(self.posted), 1)
        # the NEXT boot: the flags keep the send out of the selection, so it posts nothing (the first boot was the one
        # chance, which is why the card could never appear while the post ran inside the constructor)
        be2, th2 = self._boot()
        self.assertIsNone(th2, "nothing parked: the send is already flagged")
        self.assertEqual(len(self.posted), 1, "no second card for a send already flagged")
        self.assertTrue(be2._live[SID]["echo:boot1"].get("stale"))

    def test_the_parked_post_runs_on_its_own_thread_never_the_constructing_one(self):
        # the kernel calls post_boot_notices under its construction lock, and the door's session check re-enters that
        # lock (Sessions.live() through _sdk()): the post must run on the thread the call starts, never on the caller's
        self._stale_reg()
        seen = []

        def door(sid, key, title, body="", **kw):
            seen.append(threading.current_thread())
            return self._door(sid, key, title, body, **kw)
        be, th = self._boot(door)
        self.assertIsNotNone(th)
        self.assertEqual(seen, [th], "the door ran on the posting thread alone")
        self.assertNotEqual(th, threading.current_thread())

    def test_a_refused_boot_post_is_said_in_the_problem_row_and_the_flags_stand(self):
        self._stale_reg()
        be, th = self._boot(lambda sid, key, title, body="", **kw: (None, 'no session answers to "%s"' % sid))
        self.assertIsNotNone(th)
        self.assertTrue(be._live[SID]["echo:boot1"].get("stale"), "the drop stands whatever the card did")
        rows = [str(m) for m in self.logs if "age line" in str(m)]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("could not be posted: no session answers to", rows[0])

    def test_a_raising_parked_post_is_one_problem_row_and_the_next_parked_post_still_runs(self):
        # two sessions park a card each at the boot. The door's FIRST answer is a row that is no mapping, so that card's
        # parked post raises at its row read (the one raise inside a parked post that the door cannot cause otherwise:
        # post_notice absorbs a door that raises into a refusal); the raise is one problem row and the second card is
        # still posted. Without the guard around each parked post the thread dies at the first raise and the second
        # session's card is never posted.
        self._stale_reg()
        self._stale_reg(SID2)
        calls = []

        def door(sid, key, title, body="", **kw):
            calls.append(sid)
            if len(calls) == 1:
                return ["not", "a", "row"], None            # truthy and no .get: the parked post raises AttributeError
            return self._door(sid, key, title, body, **kw)
        be, th = self._boot(door)
        self.assertIsNotNone(th)
        self.assertEqual(sorted(calls), sorted([SID, SID2]), "both parked posts reached the door: the raise stopped none")
        self.assertEqual([n["sid"] for n in self.posted], [calls[1]], "the second card was posted after the first raised")
        failed = [str(m) for m in self.logs if "the boot post failed" in str(m)]
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("AttributeError", failed[0], "the row names the raise")
        rows = [str(m) for m in self.logs if "age line" in str(m)]
        self.assertEqual(len(rows), 1, rows)                 # the posted card's outcome; the raising post's row never formed
        self.assertIn("offered back on a card (key=dropped-sends rev=1)", rows[0])
        self.assertTrue(rows[0].startswith(calls[1][:8]), rows[0])
        for sid in (SID, SID2):
            self.assertTrue(be._live[sid]["echo:boot1"].get("dropped") and be._live[sid]["echo:boot1"].get("stale"),
                            "the flags stand on both sessions' sends: written at the reseed, before either post")


if __name__ == "__main__":
    unittest.main()
