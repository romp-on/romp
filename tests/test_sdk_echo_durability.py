#!/usr/bin/env python3
"""An SDK input echo is the ONLY visible record of a send the transcript hasn't caught up on — since
queued sends forward into the CLI mid-turn (2026-07-17) there is a window where a message is neither
queued nor landed, and the echo must own it (the user 2026-07-20: a reply sat invisible in the chat,
and one message was silently LOST across a kernel restart with no trace anywhere). Five durability
guarantees, each pinned here:
  1. the live-tail overflow cap never evicts an echo (work atoms are disposable; echoes aren't),
  2. NO floor retires an SDK echo (2026-09-06): an echo prunes only by its own text landing, and a
     dropped send's echo PERSISTS so the loss shows (the tmux echo's semantics). The genuine-human-turn
     floor once retired path-bearing echoes for the image-extraction case — the CLI's composer paste
     hook rewrites a pasted image path to "[Image #N]", so the text can never match — but that hook
     runs only in the terminal composer; an SDK session's input is stream-json and the CLI takes the
     text as typed, so on this backend the rule had only false positives (a `.png` echo retired with
     its message still in the CLI's queue). `_path_bearing` stays, narrowed to the hook's set, for the
     tmux settle that borrows it,
  3. unlanded echoes mirror to the registry (reg['echoes']) and reseed on backend construction, so a
     kernel restart cannot wipe the only evidence of an in-flight send,
  4. an echo that survives its holder is MARKED dropped at the event that orphaned it (boot reseed /
     fresh CLI spawn), so persistence reads as "never delivered", not as delivered history (the user
     2026-07-29: a two-day-old lost send kept resurfacing mid-chat, posing as an ordinary bubble) —
     unless the transcript already carries the text, as a native user record OR as the queued_command
     attachment a mid-turn splice leaves (the absorbed send's only record): that echo is neither
     re-delivered nor flagged — the verdict is recorded on it and prune_live retires it, by the text key
     the scan and the prune share (session_backend.echo_text_key) or on the verdict alone
     (AbsorbedSendsCountAsLandedAtBoot, FoundImpliesPrunable),
  5. that scan starts at the transcript's byte size at SEND time — the mark the echo carries — never at
     a fixed distance from EOF, so a landed send buried under megabytes of later output is still found
     and never re-run (LandingScanStartsAtTheSend).
SYNTHETIC fixtures only."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_echo", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"


def _echo(text, t=1000, key="echo:k1"):
    return key, {"type": "user", "uuid": key, "session_id": SID, "t": t, "parentUuid": None,
                 "author": "human", "_echo_text": text,
                 "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _work(i):
    return "w%d" % i, {"type": "assistant", "uuid": "w%d" % i, "session_id": SID, "t": 2000 + i,
                       "message": {"role": "assistant", "content": [{"type": "text", "text": "x"}]}}


class OverflowCapSparesEchoes(unittest.TestCase):
    def test_echo_survives_a_work_atom_flood(self):
        d = {}
        k, e = _echo("the in-flight reply")
        d[k] = e
        for i in range(sb.LIVE_TAIL_CAP + 60):
            wk, w = _work(i)
            d[wk] = w
            sb._evict_live_overflow(d)
        self.assertIn(k, d, "the echo must survive the cap — it is the send's only record")
        self.assertLessEqual(len(d), sb.LIVE_TAIL_CAP)

    def test_all_echo_pathology_still_bounds_memory(self):
        d = {}
        for i in range(sb.LIVE_TAIL_CAP + 20):
            k, e = _echo("msg %d" % i, t=1000 + i, key="echo:%d" % i)
            d[k] = e
        sb._evict_live_overflow(d)
        self.assertLessEqual(len(d), sb.LIVE_TAIL_CAP)


class NoFloorRetiresAnEcho(unittest.TestCase):
    """Guarantee 2. The genuine-human-turn floor once retired path-bearing echoes — the image-extraction
    case, where the CLI's composer paste hook rewrites a pasted image path to "[Image #N]" and the echo's
    text can never match. That hook runs only in the terminal composer: an SDK session's input is
    stream-json, and the CLI takes the text as typed (a count over one installation's SDK transcripts,
    2026-09-06: 0 image blocks in 8,569 user records across 71 sessions; every image-path text landed
    verbatim). So on this backend the rule had only false positives — a `.png` echo retired while its
    message sat in the CLI's queue — and no echo is floored any more: an echo retires by its own text
    landing or by the dropped marking. `_path_bearing` stays, narrowed to the hook's set, for the tmux
    settle that borrows it (kernel._tmux_echo_settle): there a send IS a paste into the composer."""

    def _backend(self):
        return sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)

    def test_plain_text_echo_survives_a_later_human_turn(self):
        be = self._backend()
        k, e = _echo("please refactor the parser")
        be._live[SID] = dict([(k, e)])
        be.prune_live(SID, tx_uuids=set(), tx_user_texts=set(), human_floor=e["t"] + 100)
        self.assertIn(k, be._live.get(SID, {}),
                      "a plain echo must not be floored away — its message may still be inside the CLI")

    def test_an_image_path_echo_survives_a_later_human_turn_too(self):
        # pre-change: retired here as the image-extraction case, with the message still inside the CLI
        be = self._backend()
        k, e = _echo("look at ~/Screenshots/shot.png please")
        be._live[SID] = dict([(k, e)])
        be.prune_live(SID, tx_uuids=set(), tx_user_texts=set(), human_floor=e["t"] + 100)
        self.assertIn(k, be._live.get(SID, {}), "no SDK echo is floored: the CLI takes the path as typed")

    def test_text_landing_still_prunes_any_echo(self):
        be = self._backend()
        k, e = _echo("please refactor the parser")
        be._live[SID] = dict([(k, e)])
        be.prune_live(SID, tx_uuids=set(), tx_user_texts={"please refactor the parser"}, human_floor=0)
        self.assertNotIn(SID, be._live)

    def test_an_image_path_echo_retires_when_its_text_lands(self):
        # the record carries the path verbatim (no rewrite on this route), so the by-text prune is the retire
        be = self._backend()
        text = "look at ~/Screenshots/shot.png please"
        k, e = _echo(text)
        be._live[SID] = dict([(k, e)])
        be.prune_live(SID, tx_uuids=set(), tx_user_texts={text: e["t"] + 5}, human_floor=0)
        self.assertNotIn(SID, be._live)

    def test_path_bearing_predicate(self):
        # the tmux settle's predicate: exactly the CLI paste hook's set, `/\.(png|jpe?g|gif|webp)$/i`
        # (absolute or ~-rooted paths; tests/test_kernel_fed_echo_absorbed.py pins the set itself)
        self.assertTrue(sb._path_bearing("see /tmp/x.png"))
        self.assertTrue(sb._path_bearing("look at ~/Screenshots/shot-2.PNG please"))
        self.assertTrue(sb._path_bearing("(/tmp/notes-api/docs/fig.jpeg)"))
        self.assertTrue(sb._path_bearing("and ~/Downloads/anim.GIF, then /srv/pics/x.webp"))
        self.assertFalse(sb._path_bearing("compare with /tmp/notes-api/docs/diagram.svg"),
                         "svg is not in the hook's set — the CLI never rewrites it")
        self.assertFalse(sb._path_bearing("see /tmp/old.bmp"), "nor bmp")
        self.assertFalse(sb._path_bearing("~/notes/todo.md is stale"),
                         "a non-image path is never rewritten out of the record — not extraction")
        self.assertFalse(sb._path_bearing("just a plain reply, and/or nothing else"))
        self.assertFalse(sb._path_bearing(""))

    def test_a_quote_chip_source_label_is_not_path_bearing(self):
        # the 2026-09-06 incident: a staged comment's quote chip leads with where the passage came from
        # (`path:line`), which the old any-path predicate called path-bearing — so the floor retired the
        # echo of a message the CLI still held. The text has no image in it; it must prune by text only.
        body = ("Replying to this highlighted code (/tmp/notes-api/notes/api.py:42):\n"
                "> def list_notes():\n\nrename this to fetch_notes")
        self.assertFalse(sb._path_bearing(body), "the tmux settle must not retire this as an extraction")
        be = self._backend()
        k, e = _echo(body, t=39)
        be._live[SID] = dict([(k, e)])
        be.prune_live(SID, tx_uuids=set(), tx_user_texts={"an earlier comment": 39}, human_floor=39)
        self.assertIn(k, be._live.get(SID, {}), "a quote-chip echo survives its sibling's landing")
        be.prune_live(SID, tx_uuids=set(), tx_user_texts={body: 69}, human_floor=69)
        self.assertNotIn(SID, be._live, "…and retires when its own text lands")


class FedEchoesOutliveTheFloor(unittest.TestCase):
    """An echo whose text the session has fed to the CLI (SdkSession._inflight_texts, the fed-turn twin
    of `inflight`) is in flight by construction — the CLI queues a mid-turn send behind the running turn
    and splices it at the next tool boundary — so no floor may retire it. It retires on its text landing
    (the absorbed atom's queued_command text) or on the CLI dying with it (the dropped marking). A
    fed-texts guard once shielded exactly these echoes from the floor; the floor itself is gone now
    (NoFloorRetiresAnEcho), so fed or unfed, dropped or live, an echo is never floored — these pin that
    the fed lifecycle and the CLI-death marking still behave. SYNTHETIC fixtures only; the session is
    registered without a thread."""

    IMG = "compare against /tmp/notes-api/docs/before.png please"

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w").close()
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd, "lastSid": SID}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = self.s

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def _fed_echo(self, text, t=38):
        self.s.inflight = 1
        self.s._inflight_texts.append(text)
        k, e = _echo(text, t=t)
        self.be._live[SID] = dict([(k, e)])
        return k, e

    def test_a_fed_image_echo_survives_the_floor(self):
        # a human record at t=39 (the sibling's landing) postdates the fed echo at t=38; the old rule
        # retired an image-bearing echo here — the CLI still has the message
        k, e = self._fed_echo(self.IMG, t=38)
        self.assertEqual(self.s.fed_texts(), [self.IMG])
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts={"the first comment": 39}, human_floor=39)
        self.assertIn(k, self.be._live.get(SID, {}), "a fed echo outranks the floor")

    def test_it_still_retires_when_its_text_lands(self):
        # the queued_command attachment lands the text (the kernel's _atom_user_texts covers that shape)
        k, e = self._fed_echo(self.IMG, t=38)
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts={self.IMG: 69}, human_floor=69)
        self.assertNotIn(SID, self.be._live, "landing retires a fed echo as before")

    def test_the_unfed_image_echo_survives_too(self):
        # nothing fed, nothing landed: an image-path echo used to floor away here; with no floor at all
        # it stays, like any echo of a message the transcript has not caught up on
        k, e = _echo(self.IMG, t=38)
        self.be._live[SID] = dict([(k, e)])
        self.assertEqual(self.s.fed_texts(), [])
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts=set(), human_floor=39)
        self.assertIn(k, self.be._live.get(SID, {}))

    def test_the_cli_dying_marks_the_loss_and_the_loss_stays(self):
        # the reconnect teardown (a resumable conversation): the fed list empties and the echo is flagged
        # dropped — the loss is visible, and it STAYS visible: a dropped echo retires by its text landing
        # or by the user's dismissal, never by a later sibling's turn (pre-change, a dropped image echo
        # floored away here and the loss record vanished)
        k, e = self._fed_echo(self.IMG, t=38)
        self.assertEqual(self.s.resume_sid, SID)
        self.s._reconcile_stranded()
        self.assertEqual(self.s.fed_texts(), [])
        self.assertTrue(e.get("dropped"), "the CLI died holding it → never delivered, shown as such")
        self.assertIn(k, self.be._live.get(SID, {}), "the flag path keeps the echo as the loss record")
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts=set(), human_floor=39)
        self.assertIn(k, self.be._live.get(SID, {}), "the loss record outlives the sibling's turn")
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts={self.IMG: 69}, human_floor=69)
        self.assertNotIn(SID, self.be._live, "…and retires when its own text lands (a resend)")

    def test_a_dropped_echo_is_dismissed_not_floored(self):
        # the only other retire for a dropped echo is the user's ✕ (dismiss_echo)
        k, e = self._fed_echo(self.IMG, t=38)
        e["dropped"] = True
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts=set(), human_floor=39)
        self.assertIn(k, self.be._live.get(SID, {}))
        self.assertEqual(self.be.dismiss_echo(SID, uuid=k), self.IMG)
        self.assertNotIn(SID, self.be._live)

    def test_fed_texts_is_a_snapshot(self):
        self.s._inflight_texts.append("one")
        snap = self.s.fed_texts()
        snap.append("two")
        self.assertEqual(self.s.fed_texts(), ["one"])


class EchoesSurviveARestart(unittest.TestCase):
    def test_persist_then_reseed_round_trip(self):
        state = tempfile.mkdtemp()
        be = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = _echo("message in flight across the restart")
        be._live[SID] = dict([(k, e)])
        be._persist_echoes(SID)
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual([x["text"] for x in reg.get("echoes") or []],
                         ["message in flight across the restart"])
        # "kernel restart": a fresh backend over the same state dir reseeds the echo into its live tail
        be2 = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
        atoms = be2.live_atoms(SID)
        self.assertEqual([a.get("_echo_text") for a in atoms],
                         ["message in flight across the restart"])
        self.assertEqual(atoms[0].get("author"), "human")

    def test_landing_empties_the_mirror(self):
        state = tempfile.mkdtemp()
        be = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = _echo("lands soon")
        be._live[SID] = dict([(k, e)])
        be._persist_echoes(SID)
        be.prune_live(SID, tx_uuids=set(), tx_user_texts={"lands soon"}, human_floor=0)
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("echoes"), [],
                         "a landed echo leaves the restart mirror too")

    def test_command_feedback_is_never_mirrored(self):
        state = tempfile.mkdtemp()
        be = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        be._live[SID] = {"cmd:1": {"type": "user", "uuid": "cmd:1", "t": 5, "command": "/model",
                                   "_echo_text": "/model opus", "author": "human",
                                   "message": {"role": "user", "content": [{"type": "text", "text": "/model opus"}]}}}
        be._persist_echoes(SID)
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("echoes"), [],
                         "a stale /model confirmation must not replay after a restart")


class AnswerEchoesKeepTheirId(unittest.TestCase):
    """A send that ANSWERS a user todo (SdkBackend.send's user_todo) stamps the id on its echo
    (`_todo`), mirrors it to the registry (`todo`) and gets it back at the boot reseed — the id
    rides the echo the way it rides the queue entry, so a loss detected later can be tied back to
    the ask. Every other echo mirrors exactly as before: the plain entry's keys are the pre-todo
    ones, byte for byte."""

    PLAIN_KEYS = ["t", "text", "author", "rompAuto", "dropped", "uuid"]

    def _backend(self, state):
        be = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        return be

    def test_send_stamps_the_id_on_the_echo_and_the_queue_entry(self):
        be = self._backend(tempfile.mkdtemp())
        fed = []
        be._ensure = lambda sid: type("S", (), {
            "enqueue": lambda self, t, qid=None, qts=None, todo="": fed.append((t, todo))})()
        self.assertTrue(be.send(SID, "Re: the staging port — 8443.", user_todo="ut-9f2c1a34"))
        self.assertTrue(be.send(SID, "plain words"))
        self.assertEqual(fed, [("Re: the staging port — 8443.", "ut-9f2c1a34"), ("plain words", "")],
                         "the id reaches the queue entry; a plain send hands an empty id")
        by_text = {a["_echo_text"]: a for a in be.live_atoms(SID) if a.get("_echo_text")}
        self.assertEqual(by_text["Re: the staging port — 8443."].get("_todo"), "ut-9f2c1a34")
        self.assertNotIn("_todo", by_text["plain words"], "a plain echo carries no id key at all")

    def test_the_mirror_carries_the_id_and_a_plain_entry_is_byte_identical(self):
        state = tempfile.mkdtemp()
        be = self._backend(state)
        k1, e1 = _echo("plain words", t=1000, key="echo:p")
        k2, e2 = _echo("Re: the staging port — 8443.", t=1001, key="echo:a")
        e2["_todo"] = "ut-9f2c1a34"
        be._live[SID] = {k1: e1, k2: e2}
        be._persist_echoes(SID)
        mirror = sb.read_reg(be.state_dir, SID).get("echoes")
        plain = next(x for x in mirror if x["text"] == "plain words")
        self.assertEqual(list(plain.keys()), self.PLAIN_KEYS, "the pre-todo shape, key for key, in order")
        self.assertEqual(plain, {"t": 1000, "text": "plain words", "author": "human",
                                 "rompAuto": False, "dropped": False, "uuid": "echo:p"})
        answer = next(x for x in mirror if x["text"] != "plain words")
        self.assertEqual(answer.get("todo"), "ut-9f2c1a34")
        # "kernel restart": the reseeded atom carries the id again; the plain one still has no key
        be2 = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
        atoms = {a["_echo_text"]: a for a in be2.live_atoms(SID) if a.get("_echo_text")}
        self.assertEqual(atoms["Re: the staging port — 8443."].get("_todo"), "ut-9f2c1a34")
        self.assertNotIn("_todo", atoms["plain words"])

    def test_boot_reseed_spares_an_answer_still_queued(self):
        # the persisted queue holds the answer (its id beside it in the sidecar): the reseed's "is it
        # still queued?" check must see it, or the answer's echo is flagged dropped (and re-delivered a
        # second time) while its message sits in the queue about to go out
        state = tempfile.mkdtemp()
        be = self._backend(state)
        meta = [{"text": "Re: the staging port — 8443.", "qid": "echo:k1", "qts": 1000000, "todo": "ut-9f2c1a34"}]
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True,
                                         "queue": ["Re: the staging port — 8443."], "queueMeta": meta})
        k, e = _echo("Re: the staging port — 8443.")
        e["_todo"] = "ut-9f2c1a34"
        be._live[SID] = dict([(k, e)])
        be._persist_echoes(SID)
        be2 = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
        atoms = be2.live_atoms(SID)
        self.assertTrue(atoms and not atoms[0].get("dropped"),
                        "a queued answer is in flight, not lost")
        reg = sb.read_reg(state, SID)
        self.assertEqual(reg.get("queue"), ["Re: the staging port — 8443."],
                         "and the reseed never rewrote the queue (no duplicate)")
        self.assertEqual(reg.get("queueMeta"), meta, "id intact")


class TodoAnswersAnnounceTheirLoss(unittest.TestCase):
    """The echo of a user-todo ANSWER carries the todo id (send(user_todo=...) — pinned above by
    AnswerEchoesKeepTheirId), and the drop-marking hands (sid, tid, text) to the constructor's
    todo_lost callback — the seam that returns a restart-lost answer to the asks the user still
    owes. Constructor-wired on purpose: the boot reseed fires drop marks from __init__, before any
    post-construction assignment could arm it. The rewind's dropped head takes the same seam."""

    ANSWER = "Re: need the staging port — 8443."
    TID = "ut-9f2c1a34"

    def _backend(self, state=None, lost=None):
        return sb.SdkBackend(state or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None,
                             todo_lost=lost)

    def _todo_echo(self, dropped=False):
        k, e = _echo(self.ANSWER)
        e["_todo"] = self.TID
        if dropped:
            e["dropped"] = True
        return k, e

    def test_the_drop_marking_hands_the_id_to_the_callback(self):
        calls = []
        be = self._backend(lost=lambda sid, tid, text: calls.append((sid, tid, text)))
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = self._todo_echo()
        be._live[SID] = dict([(k, e)])
        be._mark_dropped_echoes(SID, [])
        self.assertEqual(calls, [(SID, self.TID, self.ANSWER)])
        self.assertTrue(be._live[SID][k].get("dropped"), "the visible marking still happens")

    def test_an_echo_still_queued_fires_nothing(self):
        calls = []
        be = self._backend(lost=lambda *a: calls.append(a))
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = self._todo_echo()
        be._live[SID] = dict([(k, e)])
        be._mark_dropped_echoes(SID, [self.ANSWER])
        self.assertEqual(calls, [], "still in the surviving queue → in flight, not lost")

    def test_an_already_dropped_echo_never_refires(self):
        calls = []
        be = self._backend(lost=lambda *a: calls.append(a))
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = self._todo_echo(dropped=True)
        be._live[SID] = dict([(k, e)])
        be._mark_dropped_echoes(SID, [])
        self.assertEqual(calls, [], "the loss was already announced — marking is one-shot")

    def test_a_plain_dropped_echo_fires_nothing(self):
        calls = []
        be = self._backend(lost=lambda *a: calls.append(a))
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = _echo("an ordinary lost send")
        be._live[SID] = dict([(k, e)])
        be._mark_dropped_echoes(SID, [])
        self.assertEqual(calls, [], "no id on the echo → nothing to reopen")

    def test_a_backend_built_without_the_seam_keeps_marking(self):
        # tests and older kernels: no callback → the drop marking is unchanged, nothing raised
        be = self._backend()
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = self._todo_echo()
        be._live[SID] = dict([(k, e)])
        be._mark_dropped_echoes(SID, [])
        self.assertTrue(be._live[SID][k].get("dropped"))

    def test_the_id_reseeds_across_a_restart_and_the_boot_mark_fires(self):
        state = tempfile.mkdtemp()
        be = self._backend(state=state)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = self._todo_echo()
        be._live[SID] = dict([(k, e)])
        be._persist_echoes(SID)
        calls = []
        be2 = self._backend(state=state, lost=lambda sid, tid, text: calls.append((sid, tid)))
        self.assertEqual(calls, [(SID, self.TID)],
                         "the boot reseed hands the lost answer's id to the kernel")
        self.assertEqual(be2.live_atoms(SID)[0].get("_todo"), self.TID)

    def test_a_callback_error_never_breaks_the_marking(self):
        def boom(*a):
            raise RuntimeError("kernel side failed")

        be = self._backend(lost=boom)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = self._todo_echo()
        be._live[SID] = dict([(k, e)])
        be._mark_dropped_echoes(SID, [])
        self.assertTrue(be._live[SID][k].get("dropped"),
                        "the loss stays visible even when the reopen seam raises")

    def test_a_rewind_dropped_answer_head_is_handed_to_the_callback(self):
        # the other loss event: the CLI refuses a rewind connect and the held edit turn is pulled
        # off the queue — when that head is an answer, its ask must visibly return too
        calls = []
        be = self._backend(lost=lambda sid, tid, text: calls.append((sid, tid, text)))
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        s = sb.SdkSession(be, sb.read_reg(be.state_dir, SID))
        s.enqueue(self.ANSWER, todo=self.TID)
        s._rewind_bare = False
        s._rewind_failed(RuntimeError("refused"))
        self.assertEqual(calls, [(SID, self.TID, self.ANSWER)])
        self.assertEqual(s.pending(), [], "the head was dropped, as before")

    def test_a_bare_rollback_or_a_plain_head_hands_nothing_over(self):
        calls = []
        be = self._backend(lost=lambda *a: calls.append(a))
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        s = sb.SdkSession(be, sb.read_reg(be.state_dir, SID))
        s.enqueue("an unrelated held message")
        s._rewind_bare = True
        s._rewind_failed(RuntimeError("refused"))
        self.assertEqual(s.pending(), ["an unrelated held message"], "a bare rollback drops no head")
        s._rewind_bare = False
        s._rewind_failed(RuntimeError("refused"))
        self.assertEqual(s.pending(), [])
        self.assertEqual(calls, [], "a plain head carries no id → nothing to reopen")


class DroppedSendsAnnounceThemselves(unittest.TestCase):
    """Guarantee 4 (the user 2026-07-29): an echo that survives its holder is marked `dropped` at the
    exact event that orphaned it — the boot reseed, or a fresh CLI spawning — so the chat can render
    "never delivered" instead of a sent-looking bubble posing as history. A two-day-old lost send kept
    resurfacing mid-chat, hopping turns as new ones landed, its stale timestamp reading as a glitch:
    durable BY DESIGN, but illegible. The marking is self-correcting (a landed text still prunes the
    echo, flag and all) and rides the registry mirror across further restarts."""

    def _backend(self, state=None):
        return sb.SdkBackend(state or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)

    def test_boot_reseed_marks_an_unqueued_echo_dropped(self):
        state = tempfile.mkdtemp()
        be = self._backend(state)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = _echo("the send the dead CLI was holding")
        be._live[SID] = dict([(k, e)])
        be._persist_echoes(SID)
        be2 = self._backend(state)               # "kernel restart": nothing re-delivers this text
        atoms = be2.live_atoms(SID)
        self.assertTrue(atoms and atoms[0].get("dropped"),
                        "a reseeded echo with no queue entry has provably lost its message")
        self.assertTrue((sb.read_reg(state, SID).get("echoes") or [{}])[0].get("dropped"),
                        "the flag rides the mirror, so the NEXT restart keeps the verdict")

    def test_boot_reseed_spares_an_echo_still_in_the_queue(self):
        state = tempfile.mkdtemp()
        be = self._backend(state)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True,
                                         "queue": ["still queued across the restart"]})
        k, e = _echo("still queued across the restart")
        be._live[SID] = dict([(k, e)])
        be._persist_echoes(SID)
        be2 = self._backend(state)               # boot reconcile will re-deliver the queue → not lost
        atoms = be2.live_atoms(SID)
        self.assertTrue(atoms and not atoms[0].get("dropped"),
                        "a queued send is in flight, not lost — it must not read as never-delivered")

    def test_spawn_marking_spares_the_pending_queue(self):
        be = self._backend()
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k1, e1 = _echo("orphaned by the respawn", key="echo:a")
        k2, e2 = _echo("delivered to the fresh CLI", t=1001, key="echo:b")
        be._live[SID] = {k1: e1, k2: e2}
        be._mark_dropped_echoes(SID, ["delivered to the fresh CLI"])
        d = be._live[SID]
        self.assertTrue(d[k1].get("dropped"))
        self.assertFalse(d[k2].get("dropped"))

    def test_spawn_marking_ignores_command_feedback(self):
        be = self._backend()
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        be._live[SID] = {"cmd:1": {"type": "user", "uuid": "cmd:1", "t": 5, "command": "/model",
                                   "_echo_text": "/model opus", "author": "human",
                                   "message": {"role": "user", "content": [{"type": "text", "text": "/model opus"}]}}}
        be._mark_dropped_echoes(SID, [])
        self.assertFalse(be._live[SID]["cmd:1"].get("dropped"),
                         "command feedback is not a lost message; it retires via the command floor")

    def test_a_fresh_cli_spawn_runs_the_marking(self):
        # the spawn half is a call inside Session._run (spawning a real CLI in a unit test is not
        # practical) — pin it the way error-visibility's NoSilentSwallows pins handlers: read the source
        import ast
        import inspect
        run = next(n for n in ast.walk(ast.parse(inspect.getsource(sb)))
                   if isinstance(n, ast.FunctionDef) and n.name == "_run")
        calls = [n.func.attr for n in ast.walk(run)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertIn("_mark_dropped_echoes", calls,
                      "_run no longer marks orphaned echoes when a fresh CLI spawns")

    def test_landing_still_prunes_a_dropped_echo(self):
        # the self-correcting guarantee: a premature mark can never stick to a delivered message
        state = tempfile.mkdtemp()
        be = self._backend(state)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = _echo("actually landed after all")
        e["dropped"] = True
        be._live[SID] = dict([(k, e)])
        be._persist_echoes(SID)
        be.prune_live(SID, tx_uuids=set(), tx_user_texts={"actually landed after all"}, human_floor=0)
        self.assertNotIn(SID, be._live)
        self.assertEqual(sb.read_reg(state, SID).get("echoes"), [])

    def test_dismiss_retires_by_key_and_by_send_time(self):
        state = tempfile.mkdtemp()
        be = self._backend(state)
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = _echo("seen and dismissed")
        e["dropped"] = True
        be._live[SID] = dict([(k, e)])
        self.assertEqual(be.dismiss_echo(SID, uuid=k), "seen and dismissed")
        self.assertNotIn(SID, be._live)
        self.assertEqual(sb.read_reg(state, SID).get("echoes"), [], "dismissal empties the mirror")
        # by send time: the uuid regenerates at every boot reseed, but t survives it
        k2, e2 = _echo("dismissed after a restart", t=4242, key="echo:regen")
        e2["dropped"] = True
        be._live[SID] = dict([(k2, e2)])
        self.assertEqual(be.dismiss_echo(SID, uuid="echo:stale-painted-key", t=4242),
                         "dismissed after a restart")
        self.assertIsNone(be.dismiss_echo(SID, uuid="echo:stale-painted-key", t=4242),
                          "a second click is an idempotent miss, not an error")

    def test_dismiss_never_touches_a_pending_echo(self):
        be = self._backend()
        sb.write_reg(be.state_dir, SID, {"sid": SID, "alive": True})
        k, e = _echo("still in flight")
        be._live[SID] = dict([(k, e)])
        self.assertIsNone(be.dismiss_echo(SID, uuid=k, t=e["t"]),
                          "an undropped echo is the send's only record — undismissable")
        self.assertIn(k, be._live[SID])


class RedeliveryFeedsTheAuthoritativeQueue(unittest.TestCase):
    """The redeliver arm of _mark_dropped_echoes (2026-08-23: a proven-lost HUMAN send goes back
    into the queue instead of just flagging) must write the queue that is AUTHORITATIVE for its
    caller (found 2026-08-26): on the LIVE-session caller (a fresh spawn's _run; the resumable
    reconnect is flag-only — ReconnectStrandIsFlagOnly below) the in-memory _pending is
    authoritative — a reg-only write is clobbered by the very next _persist_queue snapshot,
    leaving the recovered send in limbo until a future kernel boot. SYNTHETIC fixtures only."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        # an EMPTY reg listing, not a MISSING one: list_regs treats an absent sdk/ dir as a scan
        # fault and serves every cached row — earlier tests' regs would reseed into this boot
        os.makedirs(os.path.join(self.state, "sdk"))
        # a real (empty) transcript, so _text_landed answers False (readable, nothing landed)
        # and the redeliver arm actually runs — unreadable fails safe toward the flag path
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w").close()
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def _reg(self, **extra):
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits",
               "alive": True, "cwd": self.cwd, "lastSid": SID}
        reg.update(extra)
        sb.write_reg(self.be.state_dir, SID, reg)
        return reg

    def _sess(self, reg):
        s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = s          # register WITHOUT starting the thread (no loop)
        return s

    def _stash_echo(self, text, t=100):
        e = {"type": "user", "uuid": "echo:" + text[:10], "session_id": SID, "t": t,
             "parentUuid": None, "author": "human", "_echo_text": text,
             "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        self.be._live.setdefault(SID, {})[e["uuid"]] = e
        return e

    def _queue(self):
        return (sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or []

    def test_live_session_redelivery_reaches_pending_and_survives_the_next_persist(self):
        reg = self._reg(queue=[])
        s = self._sess(reg)
        e = self._stash_echo("typed while the old client was dying", t=300)
        self.be._mark_dropped_echoes(SID, s.pending())     # the fresh-spawn (_run) call shape
        self.assertEqual(s.pending(), ["typed while the old client was dying"],
                         "a live session's recovered send must enter _pending — a reg-only write "
                         "is overwritten by the very next queue mirror")
        s._persist_queue()          # the mirror snapshot a reg-only write could not survive
        self.assertEqual(self._queue(), ["typed while the old client was dying"])
        self.assertFalse(e.get("dropped"), "re-delivered → renders as queued, not never-delivered")

    def test_live_session_redelivery_never_duplicates_a_pending_text(self):
        reg = self._reg(queue=[])
        s = self._sess(reg)
        s.enqueue("already back in the queue")
        self._stash_echo("already back in the queue")
        # a caller's queued snapshot can predate the enqueue — the write-moment check must dedupe
        self.be._mark_dropped_echoes(SID, [])
        self.assertEqual(s.pending(), ["already back in the queue"], "one copy, not two")


class ReconnectStrandIsFlagOnly(unittest.TestCase):
    """_reconcile_stranded's RESUMABLE branch is documented flag-only — the fed atom may genuinely
    have landed, so re-feeding risks a REAL duplicate the user sees twice. The redeliver arm of
    _mark_dropped_echoes silently flipped that policy (found 2026-08-26): a missed _text_landed
    scan (a tail-window miss, an unusual content shape) re-fed the message into the resumed
    conversation. On the resumable branch the loss is SURFACED (the dropped flag) and never
    re-fed; the re-feed belongs only where the conversation is NOT resumable (the no-init re-head,
    the boot/spawn dead paths). SYNTHETIC fixtures only."""

    TEXT = "typed while the reconnect tore the client down"

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))   # an empty listing, not a missing one (see above)
        # a real (empty) transcript: _text_landed answers False — the exact "missed scan" shape,
        # since the resumable branch must not trust that answer with a re-feed
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w").close()
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def _sess(self, **extra):
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits",
               "alive": True, "cwd": self.cwd, "lastSid": SID}
        reg.update(extra)
        sb.write_reg(self.be.state_dir, SID, reg)
        s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = s          # register WITHOUT starting the thread (no loop)
        return s

    def _stash_echo(self, text, t=100):
        e = {"type": "user", "uuid": "echo:" + text[:10], "session_id": SID, "t": t,
             "parentUuid": None, "author": "human", "_echo_text": text,
             "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        self.be._live.setdefault(SID, {})[e["uuid"]] = e
        return e

    def test_a_resumable_reconnect_flags_and_never_refeeds(self):
        s = self._sess()                                # lastSid set → the conversation is resumable
        self.assertEqual(s.resume_sid, SID)
        s.inflight = 1
        s._inflight_texts.append(self.TEXT)
        e = self._stash_echo(self.TEXT, t=300)
        s._reconcile_stranded()
        self.assertEqual(s.pending(), [],
                         "flag-only: a fed turn on a resumable conversation is never re-fed — "
                         "re-feeding risks a real duplicate")
        self.assertEqual((sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or [], [],
                         "…and no reg-queue back door either")
        self.assertTrue(e.get("dropped"), "the loss surfaces NOW as never-delivered")

    def test_a_fresh_conversation_reconnect_still_reheads(self):
        # the documented other branch: no init ever streamed → nothing the user can see exists,
        # so re-feeding cannot duplicate — the queue re-head is the loss-proof path here
        s = self._sess(lastSid="")                      # never resumed → resume_sid None
        self.assertIsNone(s.resume_sid)
        s.inflight = 1
        s._inflight_texts.append(self.TEXT)
        s._reconcile_stranded()
        self.assertEqual(s.pending(), [self.TEXT],
                         "the not-yet-materialized conversation re-heads the queue, as documented")



class _SpliceWorld(unittest.TestCase):
    """A registry + transcript for one SDK session, with the CLI's record shapes for a running turn and a
    mid-turn splice, and a backend constructor that runs the boot reseed. Shared by the scan classes
    below; no tests of its own. A PRIVATE synthetic sid (the shared placeholder is reserved for fixtures
    other modules journal against); the notes-api demo domain."""

    SID = "5a5a5a5a-6b6b-4c7c-8d8d-9e9e9e9e9e9e"
    SENT = ("Replying to this highlighted code (/tmp/notes-api/notes/api.py:42):\n"
            "> def list_notes():\n\nrename this to fetch_notes")
    T = 1_800_000_038          # the send (the echo's stamp)

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        self.tpath = sb.transcript_path(self.cwd, self.SID)
        os.makedirs(os.path.dirname(self.tpath), exist_ok=True)
        open(self.tpath, "w").close()

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    @staticmethod
    def _iso(t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _user(self, t, text, uuid, parent):
        return {"type": "user", "timestamp": self._iso(t), "uuid": uuid, "parentUuid": parent,
                "promptSource": "sdk", "userType": "external", "isSidechain": False,
                "message": {"role": "user", "content": text}}

    def _att(self, t, prompt, uuid, parent):
        # the real record's key shape (keys only — content invented): no '"user"' literal anywhere,
        # `userType` is the near-miss; the prompt is a string or a content-block list
        return {"type": "attachment", "timestamp": self._iso(t), "uuid": uuid, "parentUuid": parent,
                "isSidechain": False, "userType": "external",
                "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": "prompt",
                               "timestamp": int(t) * 1000}}

    def _qop(self, t, op, content=None):
        return {"type": "queue-operation", "timestamp": self._iso(t), "operation": op, "content": content}

    def _write(self, recs):
        with open(self.tpath, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def _append(self, recs):
        with open(self.tpath, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def _size(self):
        return os.path.getsize(self.tpath)

    def _pad(self, mb, t0):
        """`mb` megabytes of later output: tool_use / tool_result pairs of ~100 KB each, the shape a long
        autonomous run writes after a splice. Every tool_result line carries the literal '"user"', so the
        scan's prefilter lets each one through to the parser — the realistic cost, not a shortcut."""
        out, i, n = [], 0, int(mb * 10)
        for i in range(n):
            u = "pad_%d" % i
            out.append({"type": "assistant", "timestamp": self._iso(t0 + 2 * i), "uuid": "a_" + u, "parentUuid": None,
                        "message": {"role": "assistant", "content": [
                            {"type": "tool_use", "id": "tu_" + u, "name": "Bash", "input": {"command": "true"}}],
                            "stop_reason": "tool_use"}})
            out.append({"type": "user", "timestamp": self._iso(t0 + 2 * i + 1), "uuid": "tr_" + u, "parentUuid": "a_" + u,
                        "message": {"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": "tu_" + u, "content": "x" * 100_000}]}})
        return out

    def _running_turn(self):
        return [self._user(self.T - 38, "tighten the notes-api search", "u1", None),
                {"type": "assistant", "timestamp": self._iso(self.T - 28), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "tu_a1_0", "name": "Bash", "input": {"command": "true"}}],
                     "stop_reason": "tool_use"}}]

    def _splice(self, prompt=None, t=None):
        # the CLI's shape for a mid-turn splice: enqueue op (no uuid), the tool_result it waited for,
        # then the attachment stamped with the ENQUEUE time — and no user record for the text, ever
        return [self._qop(self.T, "enqueue", None),
                {"type": "user", "timestamp": self._iso(self.T + 12), "uuid": "tr1", "parentUuid": "a1",
                 "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "tu_a1_0", "content": "ok"}]}},
                self._qop(self.T + 12, "remove"),
                self._att(t if t is not None else self.T, prompt if prompt is not None else self.SENT,
                          "att1", "tr1")]

    def _backend(self, echoes, queue=()):
        sb.write_reg(self.state, self.SID, {"sid": self.SID, "name": "web", "mode": "acceptEdits",
                                            "alive": True, "cwd": self.cwd, "lastSid": self.SID,
                                            "queue": list(queue), "echoes": echoes})
        return sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)   # __init__ runs the reseed

    def _echo(self, text=None, t=None, **mark):
        e = {"t": t if t is not None else self.T, "text": text if text is not None else self.SENT,
             "author": "human", "rompAuto": False, "dropped": False}
        e.update(mark)                # off / fsid: the send-time transcript mark; landed: a recorded verdict
        return e

    def _queue(self):
        return (sb.read_reg(self.state, self.SID) or {}).get("queue") or []

    def _mirror(self):
        return (sb.read_reg(self.state, self.SID) or {}).get("echoes") or []

    def _live(self, be):
        return list((be._live.get(self.SID) or {}).values())


class AbsorbedSendsCountAsLandedAtBoot(_SpliceWorld):
    """The boot/dead-spawn duplicate guard (_text_landed) must know the shape an ABSORBED send leaves. A
    message fed into a running turn is spliced in at the CLI's next tool boundary as a queued_command
    ATTACHMENT record — no native user line is ever written for it — and until 2026-09-06 the scan
    accepted user records alone (its raw-line prefilter even required the literal '"user"', which an
    attachment line lacks; it carries `userType`). So a kernel restart in the window between the splice
    and the next merged build reseeded the echo, read the send as never landed, re-queued the text, and
    the resumed CLI ran the same instruction a second time — the chat showed it twice.

    Now a found text is neither re-delivered nor flagged: it landed and is merely un-pruned, and the next
    build's prune_live (the absorbed atom's text reaches _atom_user_texts) retires it. Two more scan
    rules ride along, both mirrored from prune_live's by-text rule: a record stamped BEFORE the send is
    not this send's landing ("ok" repeats), and the match is the whole text under the shared key
    (echo_text_key), never a substring."""

    def test_the_attachment_only_transcript_counts_as_landed(self):
        self._write(self._running_turn() + self._splice())
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T), True,
                      "the queued_command attachment IS the absorbed send's landing")

    def test_a_content_block_prompt_counts_too(self):
        # the SDK injection path writes the prompt as a content-block list, not a string
        self._write(self._running_turn() + self._splice(prompt=[{"type": "text", "text": self.SENT}]))
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T), True)

    def test_boot_reseed_neither_refeeds_nor_flags_an_absorbed_send(self):
        # the incident's shape: the CLI spliced the message (attachment written), the kernel died before
        # the next merged build pruned the echo. Pre-fix: the echo was re-queued and the CLI ran it twice.
        self._write(self._running_turn() + self._splice())
        be = self._backend([self._echo()])
        self.assertEqual(self._queue(), [], "a landed send is never re-queued — the CLI would run it twice")
        live = self._live(be)
        self.assertEqual([a["_echo_text"] for a in live], [self.SENT], "the echo is reseeded, awaiting its prune")
        self.assertFalse(live[0].get("dropped"), "…and not flagged: it landed, it is merely un-pruned")
        self.assertFalse((sb.read_reg(self.state, self.SID).get("echoes") or [{}])[0].get("dropped"))
        # the next merged build lands the absorbed atom's text at the attachment's (enqueue) stamp
        be.prune_live(self.SID, tx_uuids=set(), tx_user_texts={self.SENT: self.T}, human_floor=self.T - 38)
        self.assertNotIn(self.SID, be._live, "the by-text prune retires it, exactly once")

    def test_a_multi_line_send_is_found_through_the_json_escaping(self):
        # the old raw-line prefilter looked for the collapsed text's first 60 chars in the raw JSON line,
        # where every newline is the two characters backslash-n — a quote chip's reply never matched
        self._write(self._running_turn() + [self._user(self.T + 3, self.SENT, "u2", "a1")])
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T), True)

    def test_an_earlier_twin_of_the_text_is_not_this_sends_landing(self):
        # prune_live's T237b rule, mirrored: "ok" from an hour ago is not the landing of the "ok" the
        # dead CLI was holding — that send IS lost, and it goes back into the queue
        self._write([self._user(self.T - 3600, "ok", "u1", None)])
        be = self._backend([self._echo(text="ok")])
        self.assertIs(be._text_landed(self.SID, "ok", self.T), False)
        self.assertEqual(self._queue(), ["ok"], "re-delivered: the only record of the text predates the send")
        self.assertFalse(self._live(be)[0].get("dropped"))

    def test_a_record_at_the_send_second_counts(self):
        # the echo's stamp is minted before the CLI can see the text, so its record is at or after it —
        # the same second included (both stamps are whole seconds)
        self._write([self._user(self.T, "ok", "u1", None)])
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, "ok", self.T), True)

    def test_a_superstring_record_is_not_a_match(self):
        # the whole text, never a substring: a found echo is handed to the by-text prune, which matches
        # exactly — a substring "find" would leave an echo nothing ever prunes
        self._write([self._user(self.T + 2, "ok go ahead with the rename", "u1", None)])
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, "ok", self.T), False)

    def test_a_lost_send_with_only_its_enqueue_op_is_still_re_delivered(self):
        # the control: the CLI died holding the message — the enqueue op is on disk, no attachment, no
        # user record. Provably lost → back into the queue, as before.
        self._write(self._running_turn() + [self._qop(self.T, "enqueue", None)])
        be = self._backend([self._echo()])
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T), False)
        self.assertEqual(self._queue(), [self.SENT])
        self.assertFalse(self._live(be)[0].get("dropped"), "re-queued renders as queued, never as lost")

    def test_an_unreadable_transcript_still_takes_the_flag_path(self):
        os.remove(self.tpath)
        be = self._backend([self._echo()])
        self.assertIsNone(be._text_landed(self.SID, self.SENT, self.T), "cannot read → no verdict")
        self.assertEqual(self._queue(), [], "never re-deliver on doubt")
        self.assertTrue(self._live(be)[0].get("dropped"), "…but the possible loss is shown, for a human call")

    def test_the_match_set_reads_both_record_shapes(self):
        # the helper _text_landed matches against: user text (string or blocks, joined AND per block —
        # romp bundles injected messages), and the queued_command prompt (string or blocks); nothing else.
        # Keyed by echo_text_key — outer whitespace only, the kernel's rule — never collapsed: the CLI
        # stores user text verbatim, and a wider match here is one the prune could never retire
        self.assertEqual(sb._landed_texts({"type": "user", "message": {"content": " a  b "}}), {"a  b"})
        self.assertEqual(sb._landed_texts({"type": "user", "message": {"content": [
            {"type": "text", "text": "one"}, {"type": "text", "text": "two"}]}}), {"one two", "one", "two"})
        self.assertEqual(sb._landed_texts({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "x\ny"}}), {"x\ny"})
        self.assertEqual(sb._landed_texts({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": [{"type": "text", "text": "p"}]}}), {"p"})
        self.assertEqual(sb._landed_texts({"type": "attachment", "attachment": {"type": "total_tokens_reminder"}}), set())
        self.assertEqual(sb._landed_texts({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}), set())
        self.assertEqual(sb._landed_texts({"type": "assistant", "message": {"content": [{"type": "text", "text": "z"}]}}), set())


class LandingScanStartsAtTheSend(_SpliceWorld):
    """The scan's bound is the SEND EVENT, not a byte count. _text_landed once read the last 2 MB of the
    transcript: fine for the LOST verdict (nothing to find anyway), wrong for the LANDED one — a send
    spliced into a running turn, its echo left un-pruned because no client was connected to build, then
    2 MB of tool output, then a kernel restart: the attachment sat above the window, the scan answered
    False, and the resumed CLI ran the instruction a second time. Now send() records the transcript's
    byte size (and the fsid it measured) on the echo, the mirror carries it across restarts, and the scan
    reads from that mark to EOF: every record that can land the send is at or after it, however much
    follows. An echo with no mark (persisted before marks existed), or whose mark belongs to another
    file or lies past the end, reads the whole file — the side that never duplicates."""

    def _spliced_then_padded(self, mb=3):
        """running turn → the mark (the send happens here) → the splice → `mb` MB of later output."""
        self._write(self._running_turn())
        off = self._size()
        self._append(self._splice() + self._pad(mb, self.T + 20))
        return off

    def test_an_attachment_three_megabytes_before_eof_is_found(self):
        off = self._spliced_then_padded(3)
        self.assertGreater(self._size() - off, 3_000_000, "the landing sits well outside any 2 MB tail")
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T, off, self.SID), True)

    def test_the_reseed_neither_refeeds_nor_flags_a_marked_send_buried_under_later_output(self):
        off = self._spliced_then_padded(3)
        be = self._backend([self._echo(off=off, fsid=self.SID)])
        self.assertEqual(self._queue(), [], "found from the mark: never re-queued, the CLI would run it twice")
        live = self._live(be)
        self.assertEqual([a["_echo_text"] for a in live], [self.SENT])
        self.assertFalse(live[0].get("dropped"))
        self.assertTrue(live[0].get("_landed"), "the verdict is recorded for prune_live")
        self.assertEqual((live[0].get("_echo_off"), live[0].get("_echo_fsid")), (off, self.SID),
                         "the mark rides the reseeded atom")

    def test_an_echo_without_a_mark_reads_the_whole_file(self):
        # an echo persisted before marks existed: no tail window either — the whole file, and found
        self._spliced_then_padded(3)
        be = self._backend([self._echo()])
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T), True)
        self.assertEqual(self._queue(), [])
        self.assertFalse(self._live(be)[0].get("dropped"))

    def test_a_mark_from_another_file_or_past_the_end_reads_from_the_start(self):
        off = self._spliced_then_padded(3)
        be = self._backend([])
        # the fsid changed since the send (a /clear, a fork): the mark means nothing in this file
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T, off, "0f0f0f0f-1e1e-4d2d-8c3c-9b4b9b4b9b4b"), True)
        # a mark past EOF (the file is not the one it was measured on) → from the start
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T, self._size() + 4096, self.SID), True)
        # a mark with no fsid, or a non-integer, is no mark
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T, off, None), True)
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T, "%d" % off, self.SID), True)

    def test_the_scan_starts_at_the_mark(self):
        # a twin of the text BEFORE the mark with no readable stamp — which the send-time floor cannot
        # exclude ("a record with no readable stamp still counts") — is excluded by position: it was on
        # disk before the send, so it cannot be this send's landing
        self._write([{"type": "user", "uuid": "u0", "parentUuid": None, "message": {"role": "user", "content": "ok"}}])
        off = self._size()
        self._append(self._running_turn())
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, "ok", self.T), True, "unmarked: the stampless twin counts")
        self.assertIs(be._text_landed(self.SID, "ok", self.T, off, self.SID), False, "marked: nothing after the send")

    def test_a_mark_inside_a_record_skips_the_fragment(self):
        # the mark was taken while the CLI was mid-write: the scan's first line is a fragment of that
        # record — skipped like any non-record line, never an error (which would read as "cannot scan")
        self._write(self._running_turn())
        first_line_end = open(self.tpath, "rb").read().index(b"\n") + 1
        off = first_line_end + 10
        self._append(self._splice())
        be = self._backend([])
        self.assertIs(be._text_landed(self.SID, self.SENT, self.T, off, self.SID), True)

    def test_send_marks_the_echo_and_the_mirror_and_the_mark_survives_the_restart(self):
        self._write(self._running_turn())
        be = self._backend([])
        reg = sb.read_reg(self.state, self.SID)
        s = sb.SdkSession(be, dict(reg))
        be._ensure = lambda sid: s                     # no real session thread
        size_at_send = self._size()
        self.assertTrue(be.send(self.SID, self.SENT))
        atom = self._live(be)[0]
        self.assertEqual((atom.get("_echo_off"), atom.get("_echo_fsid")), (size_at_send, self.SID),
                         "the echo carries the transcript's size at the send, on the file it measured")
        m = self._mirror()
        self.assertEqual((m[0].get("off"), m[0].get("fsid")), (size_at_send, self.SID), "…and so does the mirror")
        # the CLI took the text (the queue empties), spliced it, then wrote 3 MB; the kernel restarts
        reg = sb.read_reg(self.state, self.SID); reg["queue"] = []; sb.write_reg(self.state, self.SID, reg)
        self._append(self._splice() + self._pad(3, self.T + 20))
        be2 = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)
        self.assertEqual(self._queue(), [], "the restart never re-runs a send the mark proves landed")
        live = self._live(be2)
        self.assertEqual(len(live), 1)
        self.assertFalse(live[0].get("dropped"))
        self.assertTrue(live[0].get("_landed"))

    def test_a_send_before_the_transcript_exists_marks_zero(self):
        os.remove(self.tpath)
        be = self._backend([])
        s = sb.SdkSession(be, dict(sb.read_reg(self.state, self.SID)))
        be._ensure = lambda sid: s
        self.assertTrue(be.send(self.SID, self.SENT))
        self.assertEqual(self._live(be)[0].get("_echo_off"), 0, "everything the file will ever hold is after the send")

    def test_an_unmarked_mirror_entry_reseeds_without_a_mark(self):
        # byte-compat with entries persisted before marks existed: no off → no _echo_off, and the
        # mirror written back carries none either
        self._write(self._running_turn())
        be = self._backend([self._echo()])
        atom = self._live(be)[0]
        self.assertNotIn("_echo_off", atom)
        self.assertNotIn("off", self._mirror()[0])


class FoundImpliesPrunable(_SpliceWorld):
    """A text the scan reports landed must be one prune_live can retire — else the echo has no exit: a
    found echo is neither re-fed nor flagged, and dismiss_echo refuses a non-dropped echo. Until
    2026-09-06 the scan matched whitespace-COLLAPSED text while the prune compared the RAW echo text
    against the kernel's stripped keys, so `romp send <sid> $'text\n'` (the route passes its argument
    verbatim) left an echo the chat hid, the prune never retired, and every boot re-scanned. Two fixes,
    both pinned: one key rule on every side (session_backend.echo_text_key), and the found verdict
    recorded on the echo so prune_live retires it even when no key matches."""

    TEXT = "hello from a script\n"          # a trailing newline: the record stores it verbatim

    def test_the_scan_and_the_prune_share_one_key(self):
        self.assertEqual(sb.echo_text_key(" q\n"), "q")
        self.assertEqual(sb.echo_text_key("a  b\nc"), "a  b\nc", "outer whitespace only, never collapsed")
        self.assertEqual(sb.echo_text_key(None), "")
        self._write(self._running_turn() + [self._user(self.T + 3, self.TEXT, "u2", "a1")])
        be = self._backend([self._echo(text=self.TEXT)])
        self.assertIs(be._text_landed(self.SID, self.TEXT, self.T), True)
        self.assertEqual(self._queue(), [])
        live = self._live(be)
        self.assertFalse(live[0].get("dropped"))
        # the kernel's key for that record (_atom_user_texts strips) retires the echo whose raw text differs
        be.prune_live(self.SID, tx_uuids=set(), tx_user_texts={sb.echo_text_key(self.TEXT): self.T + 3}, human_floor=0)
        self.assertNotIn(self.SID, be._live, "found ⇒ prunable")
        self.assertEqual(self._mirror(), [])

    def test_the_recorded_verdict_is_the_exit_when_no_key_matches(self):
        self._write(self._running_turn() + [self._user(self.T + 3, self.TEXT, "u2", "a1")])
        be = self._backend([self._echo(text=self.TEXT)])
        live = self._live(be)
        self.assertTrue(live[0].get("_landed"), "the scan's verdict is recorded on the echo")
        self.assertTrue(self._mirror()[0].get("landed"), "…and mirrored, so a restart keeps it")
        be.prune_live(self.SID, tx_uuids=set(), tx_user_texts={}, human_floor=0)
        self.assertNotIn(self.SID, be._live, "prune_live honours the verdict without a text match")
        self.assertEqual(self._mirror(), [])

    def test_a_landed_echo_is_never_rescanned_or_flagged(self):
        # the transcript is unreadable at this boot — the path that flags an unadjudicated echo
        # (AbsorbedSendsCountAsLandedAtBoot's unreadable case) — but this one was already found
        os.remove(self.tpath)
        be = self._backend([self._echo(text=self.TEXT, landed=True)])
        live = self._live(be)
        self.assertTrue(live[0].get("_landed"))
        self.assertFalse(live[0].get("dropped"), "a recorded landing is final: never re-scanned, never flagged")
        self.assertEqual(self._queue(), [])

    def test_an_unlanded_echo_is_still_unaffected_by_the_verdict_path(self):
        # the control: no record, no verdict → the redeliver arm as before
        self._write(self._running_turn())
        be = self._backend([self._echo(text=self.TEXT)])
        self.assertEqual(self._queue(), [self.TEXT])
        self.assertFalse(self._live(be)[0].get("_landed"))


if __name__ == "__main__":
    unittest.main()
