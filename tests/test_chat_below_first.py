#!/usr/bin/env python3
"""A change ABOVE a proto-2 client's held tail run costs it a delta, not a full session frame (2026-09-30).

The two session-frame senders diff a build against the shared baseline once and hand every client the list's FIRST change.
A proto-2 client holds a tail run, [first, last] by uuid, that begins at a turn boundary (_tail_run_start, #1719), so for a
session in an hours-long turn the run is that whole turn; a change at an index below the run's first edge — the task card
of a long-lived background agent re-fired in place, hours above — read as `change_from <= pf`, and _send_chat_proto2 fell
to the full frame: the tail run the client already held, ~1.6 MB, re-sent at every such change (3,470 times in one day
for one session, two open connections, pusher.chatFullWhy changeBelowFirst). The full carried nothing of the change
either: the frame begins at the run's turn boundary, above which it holds nothing.

Now the sender re-diffs from the client's own first edge against the baseline the loop handed it (_chat_prev_seen,
_chat_diff_from) and cuts the delta there — a status-only tail when nothing at or after the edge changed, the changed
suffix otherwise — with the wire key of the change above riding as `changedBelow`, so a page holding that history in a
run re-asks the run's span (render.ts chatTail → loadTurns) and the fresh page replaces the card in place. The full frame
stays for what it is for: no baseline on the sending thread (a sender outside the two loops), a change AT the held first
event, a base the list no longer holds (a fork, a rewind: baseGone), a change at index 0 (changeAt0).

Synthetic only: invented events with placeholder uuids (11111111-2222-…), hostname-free, no transcript on disk (the full
path's turn mapping finds no session and serves the plain cut). The kernel module is loaded hermetically; nothing live."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_belowfirst", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-4333-8444-000000000930"


def U(i):
    return "11111111-2222-3333-4444-%012d" % i


def ev(i, kind=None, text=None):
    return {"uuid": U(i), "kind": kind or ("user" if i % 2 == 0 else "assistant"), "md": text if text is not None else "turn %d" % i}


def build(n):
    return [ev(i) for i in range(n)]


def client():
    sent = []
    return {"send": lambda s: sent.append(json.loads(s)), "sent": {}, "proto": 2, "echat": {},
            "cid": "c-test-0001", "kind": "relay", "wid": "w-test"}, sent


def frame(evs):
    return {"id": SID, "type": "session", "events": evs, "status": {"state": "working"}}


class ChangeAboveTheHeldRun(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        self._why = dict(km._PERF_STATS.chat_full_why_stats)
        self._n = km._PERF_STATS.chat_below_first_delta_n

    def tearDown(self):
        jd.STATE = self._state
        self.td.cleanup()

    def _held(self, c, evs, pf, last=None, served=None):
        """The client was served its frame from `served` (the list the baseline will hold; `evs` when None) and holds the tail
        run from index pf through the list's last durable event (or `last`, a key): a cycle's full with no base yet, then the
        run's first edge set to the turn boundary the tail run begins at (_tail_run_start; the cut is modelled, not parsed)."""
        src = evs if served is None else served
        km._send_chat_locked(c, frame(src), None, 0, False)          # noBase: the full, the view record naming `src`
        self.assertIs(c["echatView"][SID]["list"], src)
        c["echat"][SID] = {"first": km._event_key(evs[pf]), "last": last or km._last_anchor(evs)}
        c["sent"].clear()                                            # the dedup memory of that first frame is not this test's

    def _fulls(self, reason):
        return km._PERF_STATS.chat_full_why_stats.get(reason, 0) - self._why.get(reason, 0)

    def _deltas(self):
        return km._PERF_STATS.chat_below_first_delta_n - self._n

    def _rows(self):
        p = jd.STATE / "client-diag.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def _send(self, c, prev, cur):
        change_from = km._chat_diff(prev, cur)
        with km._chat_prev_seen(prev):
            km._send_chat_locked(c, frame(cur), None, change_from, False)
        return change_from

    def test_an_in_place_change_above_the_held_first_edge_is_a_status_tail_naming_the_key_not_a_full(self):
        prev = build(400)
        cur = list(prev)
        cur[120] = dict(prev[120], md="background agent: resumed")        # the task card re-fired in place, far above
        c, sent = client()
        self._held(c, cur, 300, served=prev)                              # the tail run: the current turn, from index 300
        del sent[:]
        change_from = self._send(c, prev, cur)
        self.assertEqual(change_from, 120, "the list's first change is above the held first edge")
        self.assertEqual([f["type"] for f in sent], ["chatTail"], "a tail, never the tail-run-sized full: %r" % [f["type"] for f in sent])
        t = sent[0]
        self.assertEqual(t["events"], [], "nothing at or after the held first edge changed: an empty suffix")
        self.assertEqual(t["afterUuid"], U(399), "anchored at the list's last, as a status-only tail is")
        self.assertEqual(t["changedBelow"], U(120), "the change above rides as its key, for the page's re-ask")
        self.assertEqual(t["status"], {"state": "working"})
        self.assertEqual(c["echat"][SID], {"first": U(300), "last": U(399)}, "the base stands")
        self.assertEqual(self._fulls("changeBelowFirst"), 0)
        self.assertEqual(self._deltas(), 1, "counted beside chatFullWhy as chatBelowFirstDelta")
        self.assertEqual([r for r in self._rows() if r.get("what") == "chatFull"], [], "no chatFull row: no full went")

    def test_a_change_above_and_an_append_below_sends_the_appended_suffix_only(self):
        prev = build(400)
        cur = list(prev)
        cur[120] = dict(prev[120], md="background agent: stopped")
        cur += [ev(400), ev(401)]                                         # …and the live turn grew by two events
        c, sent = client()
        self._held(c, cur, 300, last=U(399), served=prev)                 # the client holds through the OLD last
        del sent[:]
        self._send(c, prev, cur)
        self.assertEqual([f["type"] for f in sent], ["chatTail"])
        t = sent[0]
        self.assertEqual(t["afterUuid"], U(399))
        self.assertEqual([e["uuid"] for e in t["events"]], [U(400), U(401)], "the new events, from after what the client holds")
        self.assertEqual(t["changedBelow"], U(120))
        self.assertEqual(c["echat"][SID], {"first": U(300), "last": U(401)})

    def test_a_change_above_and_one_inside_the_held_run_sends_from_the_inner_change(self):
        prev = build(400)
        cur = list(prev)
        cur[120] = dict(prev[120], md="background agent: resumed")
        cur[350] = dict(prev[350], md="turn 350, its tool result filled in")   # a change the client DOES hold
        c, sent = client()
        self._held(c, cur, 300, served=prev)
        del sent[:]
        self._send(c, prev, cur)
        self.assertEqual([f["type"] for f in sent], ["chatTail"])
        t = sent[0]
        self.assertEqual(t["afterUuid"], U(349), "cut at the first change at or after the held first edge")
        self.assertEqual([e["uuid"] for e in t["events"]], [U(i) for i in range(350, 400)])
        self.assertEqual(t["changedBelow"], U(120))
        self.assertEqual(self._deltas(), 1)

    def test_an_empty_tail_that_carries_a_change_above_is_sent_even_when_the_view_is_held(self):
        # the empty-suffix dedup (2026-09-23) withholds a tail that would leave the page as it is; one carrying `changedBelow`
        # goes all the same, since the page may hold that history in a run and owes it a re-ask
        prev = build(400)
        c, sent = client()
        self._held(c, prev, 300)
        with km._chat_prev_seen(prev):
            km._send_chat_locked(c, frame(prev), None, len(prev), False)   # a no-change cycle: records the view the client holds
        n0 = len(sent)
        cur = list(prev)
        cur[120] = dict(prev[120], md="background agent: resumed")
        self._send(c, prev, cur)
        self.assertEqual(len(sent), n0 + 1, "sent despite the held view")
        self.assertEqual(sent[-1]["type"], "chatTail")
        self.assertEqual(sent[-1]["events"], [])
        self.assertEqual(sent[-1]["changedBelow"], U(120))

    def test_the_full_frame_stays_where_it_is_owed(self):
        prev = build(400)
        cur = list(prev)
        cur[120] = dict(prev[120], md="background agent: resumed")
        # (a) no baseline on the sending thread: a sender outside the two loops keeps today's full, counted and filed
        c, sent = client()
        self._held(c, cur, 300, served=prev)
        del sent[:]
        km._send_chat_locked(c, frame(cur), None, km._chat_diff(prev, cur), False)
        self.assertEqual([f["type"] for f in sent], ["session"])
        self.assertEqual(sent[0]["proto"], 2)
        self.assertEqual(self._fulls("changeBelowFirst"), 1)
        rows = [r for r in self._rows() if r.get("what") == "chatFull"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["data"]["reason"], rows[0]["data"]["changeFrom"], rows[0]["data"]["firstHeld"]), ("changeBelowFirst", 120, True))
        # (b) a change AT the held first event: the client holds it, and no delta can anchor before it
        c, sent = client()
        self._held(c, cur, 120, served=prev)
        del sent[:]
        self._send(c, prev, cur)
        self.assertEqual([f["type"] for f in sent], ["session"])
        self.assertEqual(self._fulls("changeBelowFirst"), 2)
        # (c) a base the list no longer holds (a fork, a rewind): the full, as baseGone
        c, sent = client()
        self._held(c, cur, 300, served=prev)
        c["echat"][SID] = {"first": "11111111-2222-3333-4444-999999999001", "last": "11111111-2222-3333-4444-999999999002"}
        del sent[:]
        self._send(c, prev, cur)
        self.assertEqual([f["type"] for f in sent], ["session"])
        self.assertEqual(self._fulls("baseGone"), 1)
        # (d) a change at index 0 against a held base: changeAt0, the full, as before
        c, sent = client()
        self._held(c, cur, 300, served=prev)
        del sent[:]
        cur0 = list(cur); cur0[0] = dict(cur[0], md="the first event changed")
        self._send(c, prev, cur0)
        self.assertEqual([f["type"] for f in sent], ["session"])
        self.assertEqual(self._fulls("changeAt0"), 1)
        self.assertEqual(self._deltas(), 0, "none of these is the above-the-run delta")

    def test_a_baseline_that_does_not_reach_the_held_edge_keeps_the_full(self):
        # the client's base came from a build newer than the baseline (a connect push served it; the baseline did not
        # advance): a re-diff from an edge the baseline never reached says nothing, and the full frame stands
        prev = build(200)
        cur = build(400)
        cur[120] = dict(cur[120], md="background agent: resumed")
        c, sent = client()
        self._held(c, cur, 300, served=prev)
        del sent[:]
        self._send(c, prev, cur)
        self.assertEqual([f["type"] for f in sent], ["session"])
        self.assertEqual(self._fulls("changeBelowFirst"), 1)

    def test_a_client_served_from_a_list_the_baseline_never_saw_keeps_the_full_until_the_cycle_lines_them_up(self):
        # the review of the first cut: a TARGETED push (a connect, a typed-input echo) serves one client from its own build
        # without advancing the shared baseline. Baseline: card A at 350. The client holds B at 350 from that push. The next
        # build reverts the card to A and changes a card at 120: re-diffed against the baseline, nothing at or after the held
        # edge differs, and an empty tail would leave the client on B for good. Its view record names a list that is not the
        # baseline, so it gets the full, which carries A — as before. The cycle then serves every client from its list and
        # advances the baseline to it, and the next change above the run is a delta again.
        base = build(400)                                                 # the shared baseline: A at 350
        pushed = list(base); pushed[350] = dict(base[350], md="card B")   # the targeted push's build, never the baseline
        c, sent = client()
        self._held(c, pushed, 300, served=pushed)
        del sent[:]
        cur = list(base); cur[120] = dict(base[120], md="background agent: resumed")   # A at 350 again, a change above
        self._send(c, base, cur)
        self.assertEqual([f["type"] for f in sent], ["session"], "the full: the client's run is not the baseline's content")
        self.assertEqual(sent[0]["events"][-50]["md"], "turn 350", "…and it carries A, the current card")
        self.assertEqual(self._fulls("changeBelowFirst"), 1)
        self.assertEqual(self._deltas(), 0)
        # the cycle advanced the baseline to `cur`, the list every client was served from: lined up again
        c["echat"][SID]["first"] = U(300)
        del sent[:]
        nxt = list(cur); nxt[121] = dict(cur[121], md="background agent: stopped")
        self._send(c, cur, nxt)
        self.assertEqual([f["type"] for f in sent], ["chatTail"])
        self.assertEqual(sent[0]["changedBelow"], U(121))
        self.assertEqual(self._deltas(), 1)

    def _history_reply_advanced_the_edge(self, c, new_first):
        """What the dispatcher does when a history reply's `_base` names a new first edge (kernel.py, the loadTurns / loadOlder /
        loadAround road): the echat entry's first moves, the last stands, and the view record's list provenance is dropped —
        the run now holds the reply's page, built on its own. Pinned against the source below."""
        old = c["echat"][SID]
        c["echat"][SID] = {"first": new_first, "last": old.get("last")}
        c["echatView"][SID]["list"] = None

    def test_a_history_reply_that_extended_the_run_keeps_the_full_until_the_cycle_serves_the_client_again(self):
        # the closing review: a loadTurns reply extends the client's run downward with a page built on its own (a card at 250 as
        # version B, say), and advances the first edge. The baseline list still holds version A at 250. The next build reverts
        # the card to A and changes a card at 120: re-diffed from the new first edge against the baseline, nothing differs, and
        # an empty tail would strand the client on B. The dispatcher drops the view record's provenance with the edge advance, so
        # the gate fails and the client gets the full — which carries A — until the cycle serves it from the list it advances
        # the baseline to; then the delta road is open again.
        base = build(400)
        c, sent = client()
        self._held(c, base, 300, served=base)                              # served from the baseline, run from 300
        del sent[:]
        self._history_reply_advanced_the_edge(c, U(250))                  # the page [250, 300) landed, its own build
        cur = list(base); cur[120] = dict(base[120], md="background agent: resumed")
        self._send(c, base, cur)
        self.assertEqual([f["type"] for f in sent], ["session"], "the full: the run's content is not all the baseline's")
        self.assertEqual(self._fulls("changeBelowFirst"), 1)
        self.assertEqual(self._deltas(), 0)
        self.assertIs(c["echatView"][SID]["list"], cur, "the full served the client from the cycle's list again")
        # the cycle advanced the baseline to `cur`: the next change above the run is a delta
        c["echat"][SID]["first"] = U(300)
        del sent[:]
        nxt = list(cur); nxt[121] = dict(cur[121], md="background agent: stopped")
        self._send(c, cur, nxt)
        self.assertEqual([f["type"] for f in sent], ["chatTail"])
        self.assertEqual(self._deltas(), 1)

    def test_the_dispatcher_drops_the_provenance_where_it_advances_the_edge(self):
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read_text()
        i = src.index('client.setdefault("echat", {})[sid] = {"first": base["first"], "last": old.get("last")}')
        after = src[i:i + 900]
        self.assertIn('_rec = (client.get("echatView") or {}).get(sid)', after, "the edge advance reads the view record…")
        self.assertIn('_rec["list"] = None', after, "…and drops its list provenance beside the echat rewrite")
        self.assertEqual(src.count('"last": old.get("last")}'), 1, "the one other writer of a client's echat entry (the history reply's edge advance)")

    def test_two_changes_to_one_card_are_two_tails_each_with_its_own_revision(self):
        # the review of the first cut: the same card changing twice with the tail, status and watermark unchanged built two
        # byte-identical empty tails, and the per-client dedup (_send_client) folded the second into the first within its
        # window, so the page never heard of the second change. The changed event's digest rides the tail as changedBelowRev.
        prev = build(400)
        c, sent = client()
        self._held(c, prev, 300)
        del sent[:]
        one = list(prev); one[120] = dict(prev[120], md="background agent: resumed")
        self._send(c, prev, one)                                          # the cycle then advances the baseline to `one`
        two = list(one); two[120] = dict(one[120], md="background agent: stopped")
        self._send(c, one, two)
        self.assertEqual([f["type"] for f in sent], ["chatTail", "chatTail"], "two frames: the dedup saw two different tails")
        self.assertEqual([f["changedBelow"] for f in sent], [U(120), U(120)])
        self.assertNotEqual(sent[0]["changedBelowRev"], sent[1]["changedBelowRev"], "the digest tells the two changes apart")
        self.assertEqual(len(sent[0]["changedBelowRev"]), 32, "blake2b-16 as hex")
        self.assertEqual(self._deltas(), 2, "counted per frame that left")
        # …and a cycle in which nothing moved (the baseline now `two`, the build `two` again) sends nothing: the empty tail
        # is held against the view the client holds, as before, and the counter follows the frames that left
        self._send(c, two, two)
        self.assertEqual(len(sent), 2, "no frame for a no-change cycle")
        self.assertEqual(self._deltas(), 2)

    def test_chat_diff_from(self):
        prev = build(10)
        same = list(prev)
        self.assertEqual(km._chat_diff_from(prev, same, 4), 10, "unchanged: the length")
        changed = list(prev); changed[6] = dict(prev[6], md="x")
        self.assertEqual(km._chat_diff_from(prev, changed, 4), 6)
        self.assertEqual(km._chat_diff_from(prev, changed, 7), 10, "a change before `lo` is not seen")
        self.assertEqual(km._chat_diff_from(prev, prev + [ev(10)], 4), 10, "an append: the shorter length")
        self.assertEqual(km._chat_diff_from(prev, prev[:8], 4), 8, "a cut: the shorter length")
        self.assertEqual(km._chat_diff_from(prev, same, 30), 30, "a `lo` past both lists is answered as is")
        self.assertEqual(km._chat_diff_from([], same, 0), 0)

    def test_the_baseline_rides_the_thread_and_nests(self):
        self.assertIsNone(getattr(km._CHAT_PREV, "events", None))
        outer = build(3)
        with km._chat_prev_seen(outer):
            self.assertIs(km._CHAT_PREV.events, outer)
            with km._chat_prev_seen(None):
                self.assertIsNone(km._CHAT_PREV.events, "a loop with no baseline hides the outer one")
            with km._chat_prev_seen("not a list"):
                self.assertIsNone(km._CHAT_PREV.events)
            self.assertIs(km._CHAT_PREV.events, outer, "restored")
        self.assertIsNone(getattr(km._CHAT_PREV, "events", None))

    def test_both_session_frame_senders_hand_the_baseline_to_their_loops(self):
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read_text()
        self.assertEqual(src.count("with _chat_delivery() as _handed, _chat_prev_seen(_seen):"), 2,
                         "the pusher cycle and the targeted one-session push, the two loops that diff against _seen")
        self.assertIn('tail["changedBelow"] = below_key', src)
        self.assertIn('tail["changedBelowRev"] = below_rev', src)
        self.assertIn('pusher["chatBelowFirstDelta"] = self.chat_below_first_delta_n', src)
        self.assertIn('rec.get("list") is prev', src, "the re-diff is gated on the client's last frame being cut from the baseline list")


if __name__ == "__main__":
    unittest.main()
