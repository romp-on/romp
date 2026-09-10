#!/usr/bin/env python3
"""A composer send's identity exists from the PRESS: the client mints the copy's id (send-pending.ts newPending,
the kernel's own `echo:` form) and posts it with the send; the kernel parks it on the op, hands it to the backend
that identifies its copies, and a cancel that names the id removes exactly that copy.

Before this, the id was minted where the copy entered the backend's queue (SdkBackend.send): a send parked during
compaction, a usage-limit hold or behind a queue carried none until the drain, so the chat's bubble read the parked
copy by text, and a cancel posted no id at all (index and body only), so of two same-text copies a drifted index
removed the first by body while the client had dropped its own entry by id (the phantom "sending..." bubble, or a chip
for a cancelled send).

Exercised through the real kernel functions (_send_or_park, _deliver_send_batch, _apply_pending_ops, _cancel_parked,
_cancel_backend_queued and the ws handler _drive) against in-memory backend stand-ins, and against the real
TmuxBackend for the route whose send takes no id. The SdkBackend half (send(qid=), unqueue(qid=), the shape check
against a live queue) is in tests/test_queued_copy_identity.py.
SYNTHETIC fixtures only: a private synthetic sid, invented text, no hostnames."""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs
# conftest's floor. The manager port is poisoned too: a kernel that dialled a live manager would restart it.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_MANAGER_PORT"] = "1"
km = load_source("romp_kernel_pressid", os.path.join(BIN, "romp-kernel"))

# the account gate and the tmux prompt hold are separate axes (tests/test_kernel_limit_queue.py,
# tests/test_kernel_parked_ops_liveness.py): off here, so a park is a park for the reason under test
km._limit_hold = lambda sid: None
km._TMUX_PROMPT_HOLD_S = 0.0

SID = "7e8f9a0b-1c2d-4e3f-8a9b-0c1d2e3f4a5b"   # private synthetic sid
A = "echo:" + "a" * 32                        # ids in the kernel's own echo form, as the client mints them
B = "echo:" + "b" * 32
C = "echo:" + "c" * 32
WORDS = "same words"


class _IdBackend:
    """A backend that identifies its copies (pending_queued_meta, like SdkBackend): send takes the id, the queue
    keeps (text, id) pairs, unqueue locates by id under its own lock the way SdkSession.unqueue does."""

    def __init__(self, queue=()):
        self.q = [tuple(x) if isinstance(x, tuple) else (x, None) for x in queue]
        self.calls = []
        self.unqueued = []

    def forwards_sends(self):
        return True

    def send(self, sid, text, qid=None):
        self.calls.append((text, qid))
        self.q.append((text, qid))
        return True

    def pending_queued(self, sid):
        return [t for t, _ in self.q]

    def pending_queued_meta(self, sid):
        return [{"md": t, "qid": q, "qts": None} for t, q in self.q]

    def live_atoms(self, sid):
        return []

    def unqueue(self, sid, idx, expect=None, qid=None):
        if qid:
            idx = next((i for i, (_, q) in enumerate(self.q) if q == qid), -1)
        elif expect is not None and not (0 <= idx < len(self.q) and self.q[idx][0] == expect):
            idx = next((i for i, (t, _) in enumerate(self.q) if t == expect), -1)
        if not (0 <= idx < len(self.q)):
            return None
        t, q = self.q.pop(idx)
        self.unqueued.append((idx, t, q))
        return t


class _PlainBackend:
    """A backend whose copies carry no id, in the tmux shape: it lists its queue's copies with their stamps and no id
    (pending_queued_meta, which the chat reads for the stamps) and its send takes the text alone."""

    def __init__(self):
        self.calls = []

    def send(self, sid, text):
        self.calls.append(text)
        return True

    def pending_queued(self, sid):
        return []

    def pending_queued_meta(self, sid):
        return []

    def live_atoms(self, sid):
        return []


class _ParkFixture(unittest.TestCase):
    def setUp(self):
        self.echoes = []
        self._saved = (km._compacting_now, km._working_now, km.Sessions.backend_for, km._push_all,
                       km._optimistic_echo, km._mark_views_dirty)
        km._push_all = lambda: None
        km._mark_views_dirty = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": self.echoes.append((text, author))
        km._working_now = lambda sid: False
        km._compacting_now = lambda sid: False
        km._pending_ops.pop(SID, None)
        km._inflight_ops.pop(SID, None)

    def tearDown(self):
        (km._compacting_now, km._working_now, km.Sessions.backend_for, km._push_all,
         km._optimistic_echo, km._mark_views_dirty) = self._saved
        km._pending_ops.pop(SID, None)
        km._inflight_ops.pop(SID, None)
        km._save_pending_ops()


class ParkedSendCarriesItsPressId(_ParkFixture):
    def test_two_same_text_parks_carry_their_ids_in_press_order_and_a_park_without_one_stays_three_slot(self):
        km._compacting_now = lambda sid: True
        be = _IdBackend()
        self.assertTrue(km._send_or_park(be, SID, WORDS, echo="human", qid=A))
        self.assertTrue(km._send_or_park(be, SID, WORDS, echo="human", qid=B))
        self.assertTrue(km._send_or_park(be, SID, "no id rode this one", echo="human"))
        self.assertTrue(km._send_or_park(be, SID, "/compact", echo="human", qid=C), "a typed slash command parks too")
        self.assertEqual(km._pending_ops[SID],
                         [("send", WORDS, "human", A), ("send", WORDS, "human", B),
                          ("send", "no id rode this one", "human"), ("command", "/compact", "human", C)],
                         "the id is the op's 4th slot, present only when the press minted one")
        self.assertEqual(be.calls, [], "parked, not handed over")
        self.assertEqual(self.echoes, [], "a parked send stamps no echo until it fires")

    def test_the_slot_survives_the_disk_mirror(self):
        km._compacting_now = lambda sid: True
        km._send_or_park(_IdBackend(), SID, WORDS, echo="human", qid=A)
        self.assertEqual(km._load_pending_ops().get(SID), [("send", WORDS, "human", A)],
                         "a kernel restart restores the parked send with its id")

    def test_handed_over_now_the_id_reaches_a_backend_that_identifies_its_copies_and_not_one_that_does_not(self):
        be = _IdBackend()
        # handed over, not parked: _send_or_park reports the arm it took ("parked", or the backend send's own
        # result, truthy on delivery), so the immediate path is told apart by the verdict, never by truthiness
        self.assertNotEqual(km._send_or_park(be, SID, "go", echo="human", qid=A), "parked")
        self.assertEqual(be.calls, [("go", A)], "the copy enters the queue under the id it was pressed with")
        plain = _PlainBackend()
        self.assertNotEqual(km._send_or_park(plain, SID, "go", echo="human", qid=A), "parked")
        self.assertEqual(plain.calls, ["go"], "a route whose copies carry no id takes the text alone")
        self.assertEqual(self.echoes, [("go", "human"), ("go", "human")])

    def test_the_drain_hands_each_parked_send_its_own_id(self):
        be = _IdBackend()
        km._deliver_send_batch(be, SID, [("send", WORDS, "human", A), ("send", "plain", "human")])
        self.assertEqual(be.calls, [(WORDS, A), ("plain", None)], "op[3] rides; a 3-slot op hands in none")
        plain = _PlainBackend()
        km._deliver_send_batch(plain, SID, [("send", WORDS, "human", A)])
        self.assertEqual(plain.calls, [WORDS], "no forwards_sends: the merged text, no id")

    def test_the_drain_fires_a_parked_command_under_its_id(self):
        be = _IdBackend()
        km.Sessions.backend_for = staticmethod(lambda sid: be)
        km._pending_ops[SID] = [("command", "/compact", "human", C)]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(be.calls, [("/compact", C)], "the command's echo will wear the id the press minted")
        self.assertEqual(self.echoes, [("/compact", "human")])

    def test_a_parked_command_fires_as_the_text_on_a_backend_whose_send_takes_no_id(self):
        plain = _PlainBackend()
        km.Sessions.backend_for = staticmethod(lambda sid: plain)
        km._pending_ops[SID] = [("command", "/compact", "human", C), ("send", "after it", "human", A)]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(plain.calls, ["/compact"], "the command fired; the id, which this send cannot take, was left off")
        self.assertEqual(km._pending_ops.get(SID), [("send", "after it", "human", A)],
                         "the op behind it waits for the command's turn to end: the queue was delivered, not dropped")
        self.assertEqual(self.echoes, [("/compact", "human")])


class _TmuxFixture(_ParkFixture):
    """The real TmuxBackend (km._TMUX) with the pane typing stubbed: _tmux_send records what reaches the pane, the
    transcript path resolves to none (no queue records, no parse) and the sid has a name. The ws handler's stubs
    are the ones TheWireCarriesTheId uses."""

    def setUp(self):
        super().setUp()
        self.typed = []
        self._tmux_saved = (km._tmux_send, km._path_of, km._name_of, km._sdk, km._push_soon,
                            km.jd.optimistic_followup, km._predict_working)
        km._tmux_send = lambda name, text, **kw: self.typed.append((name, text))
        km._path_of = lambda sid, now=None: None
        km._name_of = lambda sid: "web" if sid == SID else None
        km._sdk = lambda: None
        km._push_soon = lambda: None
        km.jd.optimistic_followup = lambda *a, **k: False
        km._predict_working = lambda *a, **k: None
        km.Sessions.backend_for = staticmethod(lambda sid: km._TMUX)

    def tearDown(self):
        (km._tmux_send, km._path_of, km._name_of, km._sdk, km._push_soon,
         km.jd.optimistic_followup, km._predict_working) = self._tmux_saved
        super().tearDown()


class TheTmuxRouteTakesTheTextAlone(_TmuxFixture):
    """TmuxBackend lists its queue's copies (pending_queued_meta, for the chat's stamps) and its send takes no id: a
    send that rode in with one reaches the pane as the text, handed over now or fired from the parked queue, and
    the client's id still names the copy while it is parked."""

    def test_a_composer_send_carrying_an_id_reaches_the_pane(self):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s))}
        self.assertTrue(km._drive({"type": "sendMessage", "id": SID, "text": "hello there", "qid": A}, client))
        self.assertEqual(self.typed, [("web", "hello there")], "the text reached the pane; the id stayed with the kernel")
        self.assertEqual(self.echoes, [("hello there", "human")], "the kernel's own echo, as for every tmux send")
        self.assertNotIn(SID, km._pending_ops, "handed over, not parked")
        self.assertEqual(sent, [], "nothing refused")

    def test_a_follow_up_carrying_an_id_reaches_the_pane(self):
        client = {"send": lambda s: None}
        self.assertTrue(km._drive({"type": "askFollowUp", "itemId": SID + ":g4", "text": "and the fix?", "qid": A}, client))
        self.assertEqual(len(self.typed), 1)
        self.assertEqual(self.typed[0][0], "web")
        self.assertIn("and the fix?", self.typed[0][1])

    def test_a_parked_command_fires_as_the_text_and_the_queue_behind_it_stays(self):
        km._pending_ops[SID] = [("command", "/compact", "human", C), ("send", "after it", "human", A)]
        km._save_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(self.typed, [("web", "/compact")], "the command reached the pane as the text")
        self.assertEqual(km._pending_ops.get(SID), [("send", "after it", "human", A)],
                         "the send behind it waits for the command's turn to end: nothing was dropped")
        self.assertEqual(self.echoes, [("/compact", "human")])

    def test_a_parked_send_keeps_the_id_for_its_chip_and_its_cancel_until_the_drain_hands_the_text_over(self):
        km._working_now = lambda sid: True                 # a turn is open: tmux holds the send
        client = {"send": lambda s: None}
        km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": A}, client)
        km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": B}, client)
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human", A), ("send", WORDS, "human", B)])
        self.assertIsNone(km._cancel_parked(SID, 0, WORDS, qid=B), "the ✕ by id is exact on a parked tmux send too")
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human", A)])
        km._working_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.typed, [("web", WORDS)], "the drain hands the pane the text")
        self.assertNotIn(SID, km._pending_ops)


class CancelParkedById(_ParkFixture):
    """The ✕ on a parked send that names its id: exact; an id no parked op carries is the miss and removes
    nothing; an id-less cancel keeps the index/body fallback."""

    OP1 = ("send", WORDS, "human", A)
    OP2 = ("send", WORDS, "human", B)

    def test_removes_exactly_the_op_carrying_the_id_of_two_wearing_the_same_words(self):
        km._pending_ops[SID] = [self.OP1, self.OP2]
        self.assertIsNone(km._cancel_parked(SID, 0, WORDS, qid=B), "a stale index and the shared words: the id wins")
        self.assertEqual(km._pending_ops[SID], [self.OP1], "the other send stays parked")

    def test_an_id_no_parked_op_carries_is_the_miss_and_removes_nothing(self):
        km._pending_ops[SID] = [self.OP2]
        miss = km._cancel_parked(SID, 0, WORDS, qid=A)
        self.assertIn("too late", miss, "the named send is gone: say so, never pop the neighbour wearing its words")
        self.assertEqual(km._pending_ops[SID], [self.OP2])
        self.assertIn("too late", km._cancel_parked(SID, -1, WORDS, qid=A), "the optimistic arm (no index yet): the same miss")
        self.assertEqual(km._pending_ops[SID], [self.OP2])

    def test_the_in_flight_head_is_never_the_ids_target(self):
        km._pending_ops[SID] = [self.OP1, self.OP2]
        km._inflight_ops[SID] = km._pending_ops[SID][0]
        self.assertIn("too late", km._cancel_parked(SID, 0, WORDS, qid=A), "the backend has it")
        self.assertEqual(km._pending_ops[SID], [self.OP1, self.OP2])
        self.assertIsNone(km._cancel_parked(SID, 1, WORDS, qid=B), "the second chip still cancels")
        self.assertEqual(km._pending_ops[SID], [self.OP1])

    def test_an_id_less_cancel_keeps_the_index_and_body_fallback(self):
        km._pending_ops[SID] = [("send", "first", "human"), ("send", "second", "human")]
        self.assertIsNone(km._cancel_parked(SID, 0, "second"), "an older client's ✕: the body relocates")
        self.assertEqual(km._pending_ops[SID], [("send", "first", "human")])
        km._pending_ops[SID] = [self.OP1, self.OP2]
        self.assertIsNone(km._cancel_parked(SID, 1, WORDS), "and it may name an id-carrying op by index and body")
        self.assertEqual(km._pending_ops[SID], [self.OP1])


class CancelBackendQueuedById(unittest.TestCase):
    def test_the_id_wins_over_a_stale_index_and_the_shared_words(self):
        be = _IdBackend([(WORDS, A), (WORDS, B)])
        self.assertIsNone(km._cancel_backend_queued(be, SID, 0, WORDS, qid=B))
        self.assertEqual(be.unqueued, [(1, WORDS, B)], "the backend was told the id and popped the copy wearing it")
        self.assertEqual(be.q, [(WORDS, A)], "the other entry stays")

    def test_an_id_the_queue_does_not_hold_misses_and_touches_nothing(self):
        be = _IdBackend([(WORDS, B)])
        self.assertIn("too late", km._cancel_backend_queued(be, SID, 0, WORDS, qid=A))
        self.assertIn("too late", km._cancel_backend_queued(be, SID, -1, WORDS, qid=A), "the optimistic arm: the same miss")
        self.assertEqual(be.unqueued, [], "no pop")
        self.assertEqual(be.q, [(WORDS, B)], "the twin survives")

    def test_an_id_less_cancel_keeps_the_body_relocation_and_the_raw_index(self):
        be = _IdBackend(["alpha", "beta"])
        self.assertIsNone(km._cancel_backend_queued(be, SID, 2, "beta"), "a stale index relocates by body")
        self.assertEqual(be.unqueued, [(1, "beta", None)])
        be = _IdBackend([(WORDS, A), (WORDS, B)])
        self.assertIsNone(km._cancel_backend_queued(be, SID, 1, WORDS), "an older client names an id-carrying entry by index and body")
        self.assertEqual(be.q, [(WORDS, A)])

    def test_a_backend_whose_unqueue_takes_no_id_takes_the_index_and_body_path_never_a_refusal(self):
        class _Plain:
            """It lists its copies with stamps and no id, and its unqueue has the older signature."""

            def __init__(self):
                self.q = ["alpha", WORDS]
                self.unqueued = []

            def pending_queued(self, sid):
                return list(self.q)

            def pending_queued_meta(self, sid):
                return [{"md": t, "qid": None, "qts": None} for t in self.q]

            def unqueue(self, sid, idx, expect=None):
                if not (0 <= idx < len(self.q)) or self.q[idx] != expect:
                    return None
                self.unqueued.append((idx, self.q.pop(idx)))
                return expect
        be = _Plain()
        self.assertIsNone(km._cancel_backend_queued(be, SID, 1, WORDS, qid=A), "the id cannot be checked here: the click's index and body decide, as before")
        self.assertEqual(be.unqueued, [(1, WORDS)])


class TheWireCarriesTheId(unittest.TestCase):
    """The ws handler: sendMessage and askFollowUp post the id the client minted; a cancelQueued names it. The
    kernel accepts only an id in its own echo form that the session does not already hold, and mints as before
    otherwise (a forged or colliding id could name another send's copy)."""

    def setUp(self):
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s))}
        self.be = _IdBackend()
        self._saved = (km._name_of, km._sdk, km.Sessions.backend_for, km._compacting_now, km._working_now,
                       km._push_soon, km._push_all, km._mark_views_dirty, km._optimistic_echo,
                       km.jd.optimistic_followup, km._predict_working)
        km.jd.optimistic_followup = lambda *a, **k: False   # the card reopen is the goal store's, not this test's
        km._predict_working = lambda *a, **k: None
        km._name_of = lambda sid: "web" if sid == SID else None
        km._sdk = lambda: None
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._compacting_now = lambda sid: True
        km._working_now = lambda sid: False
        km._push_soon = lambda: None
        km._push_all = lambda: None
        km._mark_views_dirty = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": None
        km._pending_ops.pop(SID, None)
        km._inflight_ops.pop(SID, None)

    def tearDown(self):
        (km._name_of, km._sdk, km.Sessions.backend_for, km._compacting_now, km._working_now,
         km._push_soon, km._push_all, km._mark_views_dirty, km._optimistic_echo,
         km.jd.optimistic_followup, km._predict_working) = self._saved
        km._pending_ops.pop(SID, None)
        km._inflight_ops.pop(SID, None)
        km._save_pending_ops()

    def test_a_send_parks_under_the_id_it_was_posted_with(self):
        self.assertTrue(km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": A}, self.client))
        self.assertTrue(km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": B}, self.client))
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human", A), ("send", WORDS, "human", B)])
        self.assertEqual(self.sent, [], "nothing refused")

    def test_a_follow_up_parks_its_wrapped_body_under_the_id(self):
        self.assertTrue(km._drive({"type": "askFollowUp", "itemId": SID + ":g4", "text": "and the fix?", "qid": A}, self.client))
        [op] = km._pending_ops[SID]
        self.assertEqual((op[0], op[3]), ("send", A))
        self.assertIn("and the fix?", op[1])

    def test_an_id_in_another_form_or_one_the_session_holds_is_not_taken_and_the_kernel_mints_as_before(self):
        for bad in ("s-1", "echo:", "echo:zz", "echo:" + "A" * 32, "echo:" + "a" * 8, "echo:" + "a" * 70, 7, None):
            km._drive({"type": "sendMessage", "id": SID, "text": "words %r" % (bad,), "qid": bad}, self.client)
        self.assertTrue(all(len(op) == 3 for op in km._pending_ops[SID]), "no slot for an id in another form: %r" % (km._pending_ops[SID],))
        km._pending_ops.pop(SID, None)
        # held by a parked op
        km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": A}, self.client)
        km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": A}, self.client)
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human", A), ("send", WORDS, "human")],
                         "the second press of a held id parks without it")
        km._pending_ops.pop(SID, None)
        # held by the backend's queue
        self.be.q.append(("older copy", B))
        km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": B}, self.client)
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human")])
        km._pending_ops.pop(SID, None)
        # held by a live echo (a fed copy between the queue and its landing)
        self.be.live_atoms = lambda sid: [{"uuid": C, "_echo_text": "fed copy"}]
        km._drive({"type": "sendMessage", "id": SID, "text": WORDS, "qid": C}, self.client)
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human")])

    def test_a_cancel_that_names_the_id_removes_that_copy_whatever_index_the_click_carried(self):
        km._pending_ops[SID] = [("send", WORDS, "human", A), ("send", WORDS, "human", B)]
        km._save_pending_ops()
        km._drive({"type": "cancelQueued", "id": SID, "park": 0, "md": WORDS, "qid": B}, self.client)
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human", A)])
        self.assertEqual(self.sent[-1]["type"], "cancelResult")
        self.assertTrue(self.sent[-1]["ok"])
        # the optimistic arm (no park index has round-tripped): the id finds the parked copy, then the backend's
        km._drive({"type": "cancelQueued", "id": SID, "md": WORDS, "qid": A}, self.client)
        self.assertNotIn(SID, km._pending_ops)
        self.assertTrue(self.sent[-1]["ok"])
        self.be.q[:] = [(WORDS, A), (WORDS, B)]
        km._drive({"type": "cancelQueued", "id": SID, "md": WORDS, "qid": B}, self.client)
        self.assertEqual(self.be.q, [(WORDS, A)])
        self.assertTrue(self.sent[-1]["ok"])
        # the backend-queue arm with a stale index: the index names the first copy, the id the second; the id wins
        self.be.q[:] = [(WORDS, A), (WORDS, B)]
        km._drive({"type": "cancelQueued", "id": SID, "idx": 0, "md": WORDS, "qid": B}, self.client)
        self.assertEqual(self.be.q, [(WORDS, A)], "the copy the click named by id left; the one at the index stays")
        self.assertTrue(self.sent[-1]["ok"])
        # an id neither queue holds: the honest miss, and the same-words neighbours in both queues stay
        km._pending_ops[SID] = [("send", WORDS, "human", B)]
        self.assertEqual(self.be.q, [(WORDS, A)])
        km._drive({"type": "cancelQueued", "id": SID, "md": WORDS, "qid": C}, self.client)
        self.assertFalse(self.sent[-1]["ok"])
        self.assertIn("too late", self.sent[-1]["text"])
        self.assertEqual(km._pending_ops[SID], [("send", WORDS, "human", B)])
        self.assertEqual(self.be.q, [(WORDS, A)])


if __name__ == "__main__":
    unittest.main()
