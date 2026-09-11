"""Diff-based delta-send for the chat (the user 2026-06-25, who wanted to stop re-sending what didn't change).

The chat pusher used to send the FULL events array on every change (~8MB for a big transcript). Now the
whole transcript stays resident in the browser (instant scrollback), but a caught-up client receives only
the CHANGED SUFFIX as {type:"chatTail", from, events}. The suffix is found by DIFFING the freshly-built
events against the previous build — robust to _hydrate_postal turning one event into several cards mid-array,
which a fixed window would mishandle. A fresh connect / fork / behind-the-change client still gets the full
{type:"session"} so it always renders from a correct base. Source-level + behavioural pins.
"""
import json
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _client():
    sent = []
    return {"send": sent.append, "sent": {}}, sent


def _last(sent):
    return json.loads(sent[-1])


class ChatDiffTest(unittest.TestCase):
    def test_diff_finds_the_exact_changed_suffix(self):
        a = [{"uuid": "1", "x": 1}, {"uuid": "2", "x": 2}]
        self.assertEqual(km._chat_diff([], a), 0, "no prior build → full (from 0)")
        self.assertEqual(km._chat_diff(a, a + [{"uuid": "3"}]), 2, "append → from = old length")
        # a tool output filling an EARLIER card changes that card in place → from = its index
        filled = [{"uuid": "1", "x": 1}, {"uuid": "2", "x": 2, "output": "done"}, {"uuid": "3"}]
        self.assertEqual(km._chat_diff(a + [{"uuid": "3"}], filled), 1, "in-place fill → from = that index")
        self.assertEqual(km._chat_diff(a, list(a)), len(a), "no change → from = length (empty suffix)")


class SendChatTest(unittest.TestCase):
    def _m(self, sid, events):
        return {"type": "session", "id": sid, "name": sid, "events": events,
                "status": {"state": "working"}, "ledger": None, "color": None}

    def test_new_client_gets_the_full_session_then_appends_arrive_as_a_tail(self):
        a = [{"uuid": "1"}, {"uuid": "2"}]
        c, sent = _client()
        km._send_chat(c, self._m("S", a), None, 0, False)             # first send → full
        self.assertEqual(_last(sent)["type"], "session")
        b = a + [{"uuid": "3"}]
        km._send_chat(c, self._m("S", b), None, 2, False)             # caught up, appended → tail from 2
        tail = _last(sent)
        self.assertEqual(tail["type"], "chatTail")
        self.assertEqual(tail["from"], 2)
        self.assertEqual([e["uuid"] for e in tail["events"]], ["3"])
        self.assertEqual(tail["total"], 3)
        self.assertIn("status", tail)                          # the chip rides along on the delta

    def test_a_tool_fill_re_sends_from_that_cards_index(self):
        b = [{"uuid": "1"}, {"uuid": "2"}, {"uuid": "3"}]
        c, sent = _client()
        km._send_chat(c, self._m("S", b), None, 0, False)             # full
        filled = [{"uuid": "1"}, {"uuid": "2", "output": "done"}, {"uuid": "3"}]
        km._send_chat(c, self._m("S", filled), None, 1, False)        # card #2 filled → tail FROM 1
        tail = _last(sent)
        self.assertEqual(tail["type"], "chatTail")
        self.assertEqual(tail["from"], 1)
        self.assertEqual([e.get("output") for e in tail["events"]], ["done", None])

    def test_a_flag_only_change_rides_an_empty_tail_carrying_the_flags(self):
        # the user 2026-09-11: a bell flipped in one split column reached the other only with its next FULL frame.
        # Unchanged events diff to len(prev) → an empty suffix; the flags ride it as the status does
        c, sent = _client()
        a = [{"uuid": "1"}, {"uuid": "2"}]
        km._send_chat(c, self._m("S", a), None, 0, False)             # full
        m2 = dict(self._m("S", a), notify=True, hideFromFeed=False, postalServiceOff=True)
        km._send_chat(c, m2, None, km._chat_diff(a, a), False)
        t = _last(sent)
        self.assertEqual((t["type"], t["from"], t["events"]), ("chatTail", 2, []), "an empty suffix: nothing in the events changed")
        self.assertEqual((t["notify"], t["hideFromFeed"], t["postalServiceOff"]), (True, False, True), "the flags ride the tail")
        n = len(sent)
        km._send_chat(c, dict(m2, notify=False), None, km._chat_diff(a, a), False)
        self.assertEqual((len(sent), _last(sent)["notify"]), (n + 1, False), "the flip alone is a new frame: the dedup signature reads the flags")

    def test_a_fork_new_head_uuid_forces_a_full_resend(self):
        c, sent = _client()
        km._send_chat(c, self._m("S", [{"uuid": "1"}, {"uuid": "2"}]), None, 0, False)   # full
        # the tab re-pointed onto a NEW transcript (a /clear-style fork) → first event uuid changes
        km._send_chat(c, self._m("S", [{"uuid": "9"}, {"uuid": "10"}]), None, 0, False)
        self.assertEqual(_last(sent)["type"], "session", "a fork must full-resend, never a tail onto a wrong base")

    def test_a_change_below_the_clients_loaded_tail_forces_a_full(self):
        # the client holds a TAIL starting at headFrom=2 (echat = (tail_head_uuid, headFrom)). A change at
        # index 1 is BELOW its loaded tail → it lacks [1,2) → must full-resend, not tail.
        c, sent = _client()
        evs = [{"uuid": "1"}, {"uuid": "2"}, {"uuid": "3"}, {"uuid": "4"}]
        c["echat"] = {"S": ("3", 2)}                           # holds the tail [2,4): head '3' at index 2
        km._send_chat(c, self._m("S", evs), None, 1, False)           # change_from 1 < headFrom 2 → full
        self.assertEqual(_last(sent)["type"], "session")

    def test_a_big_session_full_send_is_trimmed_to_the_tail_with_an_offset(self):
        evs = [{"uuid": str(i)} for i in range(km.WIRE_TAIL + 50)]    # bigger than the wire tail
        c, sent = _client()
        km._send_chat(c, self._m("S", evs), None, 0, False)
        full = _last(sent)
        self.assertEqual(full["type"], "session")
        self.assertEqual(len(full["events"]), km.WIRE_TAIL, "ship only the last WIRE_TAIL events")
        self.assertEqual(full["headFrom"], 50, "offset = total - WIRE_TAIL (older history lives before it)")
        self.assertEqual(full["headTotal"], km.WIRE_TAIL + 50)
        self.assertEqual(full["events"][0]["uuid"], "50", "the tail starts at headFrom")
        # echat now tracks (tail_head_uuid, headFrom) → a later append delta uses the GLOBAL index
        evs2 = evs + [{"uuid": "NEW"}]
        km._send_chat(c, self._m("S", evs2), None, len(evs), False)   # appended at global index = old total
        tail = _last(sent)
        self.assertEqual(tail["type"], "chatTail")
        self.assertEqual(tail["from"], len(evs), "the tail's `from` is the GLOBAL index, mapped by the browser")
        self.assertEqual([e["uuid"] for e in tail["events"]], ["NEW"])

    def test_the_top_level_git_branch_survives_the_tail_trim(self):
        # Regression (the user 2026-06-30): the status-bar branch + tab tooltip read a TOP-LEVEL gitBranch field,
        # never the head system event. The system event lives at events[0]; a >WIRE_TAIL session ships only the
        # last WIRE_TAIL events, so that head event (and its branch) fell off the wire → the branch vanished on
        # every long session. A top-level field is not part of the windowed events, so it must always ride along.
        evs = [{"uuid": str(i)} for i in range(km.WIRE_TAIL + 50)]    # bigger than the wire tail
        m = self._m("S", evs); m["gitBranch"] = "main"
        c, sent = _client()
        km._send_chat(c, m, None, 0, False)
        full = _last(sent)
        self.assertEqual(len(full["events"]), km.WIRE_TAIL, "events are still trimmed to the tail")
        self.assertEqual(full.get("gitBranch"), "main", "the top-level branch rides along even when trimmed")

    def test_a_small_session_under_the_tail_is_sent_whole(self):
        evs = [{"uuid": "1"}, {"uuid": "2"}]
        c, sent = _client()
        km._send_chat(c, self._m("S", evs), None, 0, False)
        full = _last(sent)
        self.assertEqual(full["type"], "session")
        self.assertNotIn("headFrom", full, "a session that fits under WIRE_TAIL is sent whole, no offset")

    def test_the_ledger_rides_the_tail_only_when_it_changed(self):
        # the ledger (goal tree, tens of KB) only changes on a judge pass, so it must NOT ride every 0.5s delta
        a = [{"uuid": "1"}, {"uuid": "2"}]
        c, sent = _client()
        km._send_chat(c, self._m("S", a), None, 0, False)                  # full → carries the ledger
        km._send_chat(c, self._m("S", a + [{"uuid": "3"}]), None, 2, False)   # only an event appended
        self.assertEqual(_last(sent)["type"], "chatTail")
        self.assertNotIn("ledger", _last(sent), "an unchanged ledger does NOT ride every delta")
        km._send_chat(c, self._m("S", a + [{"uuid": "3"}, {"uuid": "4"}]), None, 3, True)   # judge pass
        self.assertIn("ledger", _last(sent), "a changed ledger DOES ride the delta")


class RenderHandlesTheTail(unittest.TestCase):
    def _render(self):
        import pathlib
        return (pathlib.Path(BIN).parent / "ui" / "webview" / "render.ts").read_text()

    def test_render_truncates_to_from_appends_and_repaints_from_the_changed_point(self):
        r = self._render()
        self.assertIn('else if (m.type === "chatTail") chatTail(m);', r)       # dispatched
        self.assertIn("const from = (msg.from | 0) - (s.headFrom || 0);", r)   # GLOBAL index → resident-tail local
        # The two rejection cases split on 2026-07-28. Below the loaded head → still a quiet return (the
        # resident tail is fine). A GAP (from past what we hold) → ask for a full session: "wait for the
        # next full" was a promise nothing kept, and the tab froze there until its socket dropped.
        self.assertIn("if (from < 0) return;", r)
        # the gap check runs in KERNEL coordinates — the client's injected optimistic tail is not part
        # of the kernel's index space, and counting it masked genuine gaps (the user 2026-08-09)
        self.assertIn("if (from > kernelLen) {", r)
        self.assertIn('requestFullSession(msg.id, "gap");', r)   # 2026-09-07: the ask names its reason (skeleton tabs)
        self.assertIn("s.events.length = from;", r)                            # truncate the superseded tail
        self.assertIn("for (const e of (msg.events || [])) s.events.push(e);", r)  # append the suffix
        self.assertIn("v.rendered = Math.min(v.rendered, from);", r)           # repaint from the exact change

    def test_render_handles_a_partial_session_and_streams_older_in(self):
        r = self._render()
        # upsert records the wire offset → s.events is the tail [headFrom, headTotal); an empty frame for a held
        # transcript keeps the resident window instead (T249b, frame-merge.ts)
        self.assertIn("headFrom: kept && prev ? prev.headFrom : (msg.headFrom ?? 0),", r)
        # scroll to the top of the resident tail with older on the server → request the previous chunk
        self.assertIn('vscodeApi?.postMessage({ type: "loadOlder", id: sid, before: s.headFrom });', r)
        self.assertIn("if (moreOnServer && (v.winStart ?? 0) === 0 && st < topH + edgePx) { requestOlder(", r)
        # chatHead PREPENDS the chunk + lowers headFrom + re-anchors
        self.assertIn('else if (m.type === "chatHead") chatHead(m);', r)
        self.assertIn("if (older.length) s.events = older.concat(s.events);", r)
        self.assertIn("s.headFrom = from;", r)


if __name__ == "__main__":
    unittest.main()
