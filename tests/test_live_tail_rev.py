#!/usr/bin/env python3
"""The live-tail revision. Both backends keep an in-memory tail the chat merges ahead of the transcript
(the SDK backend's `_live`, the Codex backend's per-session list), and the chat-build signature keys a tab on
that tail without hashing its atoms per cycle. A backend with a counter keeps a per-sid revision that
advances on every change to the tail and only then (the SDK backend's _touch_live). The tests here pin
the two halves of that contract: every writer bumps (an add, a
replace, a pop, a flag write, a reworded echo, including the flag writes _mark_dropped_echoes makes
outside the live-tail lock), and a call that changed nothing (a read, a prune that retired nothing, a
settle over echoes already marked, a queue miss) leaves the revision alone; plus the kernel's dispatcher,
Sessions.live_rev, and its fallback for a backend with no counter.

Synthetic fixtures only: private synthetic sids, invented text, a hermetic state root.
"""
import inspect
import json
import os
import tempfile
import types
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_livetailrev", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_livetailrev", os.path.join(BIN, "romp_sdk_backend.py"))
ek = km.sb.echo_text_key                          # the one text key both backends prune by

SID = "77777777-8888-9999-aaaa-bbbbbbbbbbb1"      # this module's own synthetic sids: nothing else writes under them
SID2 = "77777777-8888-9999-aaaa-bbbbbbbbbbb2"


def _backend():
    d = tempfile.mkdtemp()
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)


def _work(uuid, t):
    return {"type": "assistant", "uuid": uuid, "t": t,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_" + uuid,
                                                          "name": "Bash", "input": {}}]}}


def _echo(key, text, t, **flags):
    a = {"type": "user", "uuid": key, "session_id": SID, "t": t, "parentUuid": None, "author": "human",
         "_echo_text": text, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    a.update(flags)
    return a


# ---- message doubles for _forward: the SDK's shapes, by class name (msg_to_atom keys on them) ----
class _TextBlock:
    def __init__(self, text): self.text = text
class _AssistantMessage:
    def __init__(self, content, model="claude-x", uuid="a1", parent_tool_use_id=None):
        self.content, self.model, self.uuid = content, model, uuid
        self.parent_tool_use_id, self.stop_reason, self.error = parent_tool_use_id, "end_turn", None
_TextBlock.__name__ = "TextBlock"; _AssistantMessage.__name__ = "AssistantMessage"


class SdkLiveTailRevision(unittest.TestCase):
    def setUp(self):
        self.be = _backend()

    def rev(self, sid=SID):
        return self.be.live_rev(sid)

    def test_starts_at_zero_and_reads_do_not_move_it(self):
        self.assertEqual(self.rev(), 0)
        self.assertEqual(self.be.live_atoms(SID), [])
        self.assertEqual(self.be.live_atom_kinds(SID), [])
        self.assertEqual(self.rev(), 0, "a read is not a change")
        self.assertEqual(self.rev(SID2), 0, "per sid")

    def test_a_stash_bumps_once_per_add_and_once_per_replace(self):
        self.be._stash_live(SID, "e1", _echo("e1", "first message", 10))
        self.assertEqual(self.rev(), 1)
        self.be._stash_live(SID, "e2", _echo("e2", "second message", 11))
        self.assertEqual(self.rev(), 2)
        self.be._stash_live(SID, "e2", _echo("e2", "second message, edited", 12))   # the same key: a replace
        self.assertEqual(self.rev(), 3)
        self.assertEqual(self.rev(SID2), 0, "another sid's tail is untouched")
        self.be.live_atoms(SID)
        self.assertEqual(self.rev(), 3, "the pusher's read leaves it where it was")

    def test_forward_bumps_for_a_streamed_atom(self):
        s = sb.SdkSession(self.be, {"sid": SID, "name": "web", "cwd": "/tmp"})
        self.be._forward(s, _AssistantMessage([_TextBlock("a streamed reply")], uuid="m1"))
        self.assertEqual(self.rev(), 1)
        self.assertEqual([a["uuid"] for a in self.be.live_atoms(SID)], ["m1"])
        self.be._forward(s, _AssistantMessage([_TextBlock("more of it")], uuid="m2"))
        self.assertEqual(self.rev(), 2)

    def test_prune_bumps_once_per_call_that_retired_something(self):
        self.be._stash_live(SID, "w1", _work("w1", 5))
        self.be._stash_live(SID, "w2", _work("w2", 6))
        self.be._stash_live(SID, "e1", _echo("e1", "typed while it ran", 7))
        r0 = self.rev()
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts={}, human_floor=0)
        self.assertEqual(self.rev(), r0, "nothing landed: the tail did not change, so the revision holds")
        self.be.prune_live(SID, tx_uuids={"w1", "w2"}, tx_user_texts={}, human_floor=0)
        self.assertEqual(self.rev(), r0 + 1, "two work atoms landed by uuid in one call: one change, one bump")
        self.assertEqual([a["uuid"] for a in self.be.live_atoms(SID)], ["e1"])
        self.be.prune_live(SID, tx_uuids=set(), tx_user_texts={ek("typed while it ran"): 8}, human_floor=0)
        self.assertEqual(self.rev(), r0 + 2, "the echo landed by text")
        self.assertEqual(self.be.live_atoms(SID), [])
        self.be.prune_live(SID, tx_uuids={"w1"}, tx_user_texts={}, human_floor=0)
        self.assertEqual(self.rev(), r0 + 2, "an empty tail: no change")

    def test_retire_live_work_bumps_only_when_work_atoms_were_held(self):
        self.be._stash_live(SID, "e1", _echo("e1", "an echo, not work", 6))
        r0 = self.rev()
        self.be.retire_live_work(SID)
        self.assertEqual(self.rev(), r0, "an echo-only tail holds no work: nothing retired, no bump")
        self.be._stash_live(SID, "w1", _work("w1", 7))
        self.be._stash_live(SID, "w2", _work("w2", 8))
        r1 = self.rev()
        self.be.retire_live_work(SID)
        self.assertEqual(self.rev(), r1 + 1, "two work atoms retired in one settle: one bump")
        self.assertEqual([a["uuid"] for a in self.be.live_atoms(SID)], ["e1"], "the echo stays, the work went")

    def test_dismiss_echo_bumps_on_a_hit_and_not_on_a_miss(self):
        self.be._stash_live(SID, "e1", _echo("e1", "never delivered", 6, dropped=True))
        self.be._stash_live(SID, "e2", _echo("e2", "still in flight", 7))
        r0 = self.rev()
        self.assertIsNone(self.be.dismiss_echo(SID, uuid="e2"), "a live echo is not dismissable")
        self.assertEqual(self.rev(), r0, "a miss changes nothing")
        self.assertEqual(self.be.dismiss_echo(SID, uuid="e1"), "never delivered")
        self.assertEqual(self.rev(), r0 + 1)

    def test_unqueue_drops_the_canceled_echo_and_bumps(self):
        self.be._stash_live(SID, "e1", _echo("e1", "cancel me", 6))
        self.be.sessions[SID] = types.SimpleNamespace(unqueue=lambda idx, expect=None, qid=None: "cancel me")
        r0 = self.rev()
        self.assertEqual(self.be.unqueue(SID, 0), "cancel me")
        self.assertEqual(self.rev(), r0 + 1)
        self.assertEqual(self.be.live_atoms(SID), [])
        self.be.sessions[SID] = types.SimpleNamespace(unqueue=lambda idx, expect=None, qid=None: None)
        self.assertIsNone(self.be.unqueue(SID, 0))
        self.assertEqual(self.rev(), r0 + 1, "a queue miss pops no echo and bumps nothing")
        self.be.sessions[SID] = types.SimpleNamespace(unqueue=lambda idx, expect=None, qid=None: "already gone")
        self.assertEqual(self.be.unqueue(SID, 0), "already gone")
        self.assertEqual(self.rev(), r0 + 1, "a queue hit whose echo was retired ahead of it changes no atom")

    def test_edit_queued_rewords_the_echo_and_bumps(self):
        """The queue-edit route rewrites a queued message's echo in place (its text and its message block),
        so the tab that renders the echo changed with no file moving: a change to the tail, one bump."""
        self.be._stash_live(SID, "e1", _echo("e1", "the old words", 6))
        self.be.sessions[SID] = types.SimpleNamespace(replace_queued=lambda idx, text, expect=None: "the old words")
        r0 = self.rev()
        self.assertEqual(self.be.edit_queued(SID, 0, "the new words"), "the old words")
        self.assertEqual(self.rev(), r0 + 1, "the reworded echo is a change to the tail")
        self.assertEqual(self.be.live_atoms(SID)[0]["_echo_text"], "the new words")
        self.be.sessions[SID] = types.SimpleNamespace(replace_queued=lambda idx, text, expect=None: None)
        self.assertIsNone(self.be.edit_queued(SID, 0, "nothing to edit"))
        self.assertEqual(self.rev(), r0 + 1, "a queue miss rewords nothing and bumps nothing")
        self.be.sessions[SID] = types.SimpleNamespace(replace_queued=lambda idx, text, expect=None: "no echo wears this")
        self.assertEqual(self.be.edit_queued(SID, 0, "still nothing"), "no echo wears this")
        self.assertEqual(self.rev(), r0 + 1, "a queue hit with no matching echo changes no atom")

    def test_mark_dropped_echoes_bumps_for_its_flag_writes_outside_the_lock(self):
        """The two in-place flag writes (`dropped`, `_landed`) change atoms the pusher already holds by
        identity, without the live-tail lock; each is a change to the tail and advances the revision."""
        self.be._stash_live(SID, "e1", _echo("e1", "lost with its process", 6))
        r0 = self.rev()
        self.be._mark_dropped_echoes(SID, queued_texts=(), refeed=False)   # the flag path only
        self.assertTrue(self.be.live_atoms(SID)[0].get("dropped"))
        self.assertEqual(self.rev(), r0 + 1, "the `dropped` verdict is a change")
        self.be._mark_dropped_echoes(SID, queued_texts=(), refeed=False)
        self.assertEqual(self.rev(), r0 + 1, "already flagged: nothing newly dropped, no bump")
        self.be._stash_live(SID, "e2", _echo("e2", "landed, un-pruned", int(time.time()) - 5))   # recent: inside the re-delivery age line
        self.be._text_landed = lambda sid, text, t=None, off=None, fsid=None: True
        r1 = self.rev()
        self.be._mark_dropped_echoes(SID, queued_texts=())                  # the scan finds the record
        e2 = next(a for a in self.be.live_atoms(SID) if a["uuid"] == "e2")
        self.assertTrue(e2.get("_landed"))
        self.assertFalse(e2.get("dropped"))
        self.assertEqual(self.rev(), r1 + 1, "the `_landed` verdict is a change too, counted once")

    def test_every_writer_site_bumps_by_source(self):
        """The writer set _touch_live's docstring names, pinned by source beside the behavioural tests above
        (which carry the claim that each bump happens): each mutator's source calls _touch_live, the two
        flag-writing lines in _mark_dropped_echoes are each followed by one, and _stash_live and _forward are
        the only sites that stash into _live, so a new mutator fails here until it is classified."""
        for name in ("_stash_live", "_forward", "unqueue", "edit_queued", "dismiss_echo", "prune_live",
                     "retire_live_work", "_mark_dropped_echoes", "settle_echoes"):
            src = inspect.getsource(getattr(sb.SdkBackend, name))
            self.assertIn("self._touch_live(", src, "%s changes the tail without advancing its revision" % name)
        mde = inspect.getsource(sb.SdkBackend._mark_dropped_echoes)
        self.assertIn('a["_landed"] = True', mde)
        self.assertIn('a["dropped"] = True', mde)
        self.assertEqual(mde.count("self._touch_live(sid)"), 2, "one bump per flag-writing pass")
        for name in ("live_atoms", "live_atom_kinds", "_persist_echoes"):
            src = inspect.getsource(getattr(sb.SdkBackend, name))
            self.assertNotIn("_touch_live", src, "%s is a read" % name)
        whole = inspect.getsource(sb.SdkBackend)
        self.assertEqual(whole.count("self._live.setdefault("), 2, "_stash_live and _forward are the only stashes")
        bump = inspect.getsource(sb.SdkBackend._touch_live)
        self.assertIn("revs[sid] = revs.get(sid, 0) + 1", bump)
        self.assertNotIn("with self._live_lock", bump, "the caller holds the lock; the bump takes nothing")
        self.assertIn("with self._live_lock", inspect.getsource(sb.SdkBackend.live_rev),
                      "the read is under the lock, like the atoms it counts")

    def test_a_bare_double_that_borrows_the_mutator_and_the_bump_gets_a_counter_of_its_own(self):
        """tests/test_restart_redelivery.py binds _mark_dropped_echoes onto a plain class whose own
        _touch_live is a no-op; a double that borrows the real _touch_live too gets the revision map made
        in the instance on first use, so neither kind of double declares a counter."""
        class BE:
            _live = {}
            _live_lock = __import__("threading").RLock()
            def _log(self, msg, problem=False): pass
            def _persist_echoes(self, sid): pass
            def _wake_push(self): pass
        be = BE()
        be._live.setdefault(SID, {})["echo:x"] = {"_echo_text": "typed", "author": "human", "t": 3}
        be._mark_dropped_echoes = sb.SdkBackend._mark_dropped_echoes.__get__(be)
        be._touch_live = sb.SdkBackend._touch_live.__get__(be)
        be._mark_dropped_echoes(SID, queued_texts=(), refeed=False)
        self.assertEqual(be.__dict__["_live_rev"], {SID: 1})
        BE._live.clear()


class SessionsLiveRevDispatch(unittest.TestCase):
    def setUp(self):
        self._saved = (km._sdk, km._codex)

    def tearDown(self):
        km._sdk, km._codex = self._saved

    def test_an_unowned_sid_answers_with_its_empty_tail(self):
        # no backend owns the sid → the unowned route, which keeps no counter and no tail: the dispatcher
        # answers with the serialized empty tail, stable across reads
        km._sdk = lambda: None
        km._codex = lambda: None
        self.assertIs(km.Sessions.backend_for(SID), km._UNOWNED)
        self.assertEqual(km.Sessions.live_rev(SID), json.dumps([]))
        self.assertEqual(km.Sessions.live_rev(SID), json.dumps([]), "stable: nothing can move an empty tail")

    def test_an_sdk_sid_reads_the_backend_counter(self):
        be = _backend()
        be.owns = lambda sid: sid == SID
        km._sdk = lambda: be
        self.assertEqual(km.Sessions.live_rev(SID), 0)
        be._stash_live(SID, "e1", _echo("e1", "typed", 1))
        self.assertEqual(km.Sessions.live_rev(SID), 1, "dispatch by the owning backend")

    def test_a_backend_without_a_counter_answers_with_the_tail_itself(self):
        atoms = [{"uuid": "c1", "t": 1, "message": {"content": [{"type": "text", "text": "hi"}]}}]
        fake = types.SimpleNamespace(owns=lambda sid: sid == SID, live_atoms=lambda sid: list(atoms))
        km._sdk = lambda: fake
        v0 = km.Sessions.live_rev(SID)
        self.assertEqual(v0, json.dumps(atoms, sort_keys=True, default=str), "exact: the serialized tail")
        self.assertEqual(km.Sessions.live_rev(SID), v0, "stable while the tail is")
        atoms[0]["t"] = 2
        self.assertNotEqual(km.Sessions.live_rev(SID), v0, "an in-place change to an atom moves it")
        atoms.append({"uuid": "c2", "t": 3})
        self.assertNotEqual(km.Sessions.live_rev(SID), v0)


if __name__ == "__main__":
    unittest.main()
