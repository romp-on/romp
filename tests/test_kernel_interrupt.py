#!/usr/bin/env python3
"""Stop/interrupt → the chat chip should flip to 'ready' (the user 2026-06-20). The chip is driven by the
event-model open-turn signal (open turn AND no idle atom). A normal turn flips it via the transcript's
end_turn; an Esc INTERRUPT writes no end_turn and the Stop hook doesn't fire, so the kernel records a
state:"idle" transition itself (_record_idle) → an idle atom lands in the open turn → the chip reads ready.
Isolated from test_kernel.py (a peer is churning it). Synthetic fixtures only."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_intr", os.path.join(BIN, "romp-kernel")).load_module()

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, stop):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


class RecordIdle(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_appends_a_backdated_idle_state_record(self):
        km._record_idle(SID, NOW)
        rows = [json.loads(l) for l in (jd.STATE / "states" / (SID + ".jsonl")).read_text().splitlines() if l]
        self.assertEqual(rows, [{"t": NOW - 1, "state": "idle"}],
                         "one idle transition, backdated 1s so its [start,end] span is non-empty immediately")

    def test_no_sid_is_a_noop(self):
        km._record_idle("", NOW)
        self.assertFalse((jd.STATE / "states").exists(), "no sid → nothing written, no crash")


class IdleAtomFlipsTheOpenTurn(unittest.TestCase):
    """The mechanism: an idle state record (what _record_idle writes) becomes an idle atom inside the OPEN
    turn, so the chip's open_now (= not ended AND no idle atom) goes False → 'ready'."""

    def _open_session(self, states):
        # an OPEN turn: the last assistant stops on tool_use (no end_turn), so the turn never 'ends'
        recs = [uline(T0, "do the thing", "u1"),
                aline(T0 + 10, "working on it", "a1", "u1", "tool_use")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            return em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], states=states, now=NOW)

    def test_open_turn_is_working_without_an_idle_record(self):
        lt = self._open_session(states=[])["turns"][-1]
        self.assertFalse(lt["ended"], "a tool_use stop leaves the turn open")
        self.assertFalse(any(a["type"] == "idle" for a in lt["atoms"]), "no idle record → no idle atom → still working")

    def test_idle_record_lands_an_idle_atom_in_the_open_turn(self):
        # the interrupt's idle record (backdated 1s, like _record_idle)
        lt = self._open_session(states=[{"t": NOW - 1, "state": "idle"}])["turns"][-1]
        self.assertFalse(lt["ended"], "the turn still never got end_turn")
        self.assertTrue(any(a["type"] == "idle" for a in lt["atoms"]),
                        "the idle transition becomes an idle atom in the open turn → the chip flips to ready")


if __name__ == "__main__":
    unittest.main()


class InterruptingChip(unittest.TestCase):
    """The INTERRUPTING chip (the user 2026-07-02): a just-sent stop flips the chip AT ONCE — the stop can
    take seconds to reach a stream boundary and land on disk, and the UI used to sit on 'working' (button
    still pressable, timer counting) the whole time. Event-cleared the moment the turn settles."""

    def setUp(self):
        km._interrupt_clicked.clear()

    def tearDown(self):
        km._interrupt_clicked.clear()

    def test_stamp_reads_interrupting_before_the_stop_lands(self):
        # tmux path (no SDK flag in the snapshot): stop sent, no stop record yet → interrupting
        km._interrupt_clicked[SID] = NOW - 2
        self.assertTrue(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, None),
                        "stop sent, no stop record yet → interrupting")

    def test_a_closed_tail_without_a_stop_record_stays_interrupting(self):
        # THE FIX (the user 2026-07-07): the open/closed state of the turn is no longer consulted at all —
        # only the stop RECORD (or the cap) settles a tmux interrupt. A tail that momentarily reads closed
        # mid-settle must NOT drop the chip to 'working' (the flicker they reported).
        km._interrupt_clicked[SID] = NOW - 2
        self.assertTrue(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, None),
                        "no stop record → still in flight even though the tail reads closed")
        self.assertIn(SID, km._interrupt_clicked, "the stamp survives a closed-tail flicker")

    def test_the_stop_record_settles_it(self):
        km._interrupt_clicked[SID] = NOW - 2
        intr = {"type": "user", "t": NOW - 1,
                "message": {"role": "user", "content": "[Request interrupted by user]"}}
        self.assertFalse(km._interrupting(SID, {"turns": [{"atoms": [intr]}]}, NOW, None),
                         "the CLI's stop record at/after the click → settled")
        self.assertNotIn(SID, km._interrupt_clicked, "stamp consumed — never sticks")

    def test_wedged_turn_falls_back_after_the_safety_cap(self):
        km._interrupt_clicked[SID] = NOW - 121
        self.assertFalse(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, None),
                         "a wedged turn whose stop never lands falls back after the cap")
        self.assertNotIn(SID, km._interrupt_clicked)

    def test_the_ws_interrupt_handler_stamps_and_pushes(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertIn('_interrupt_clicked[str(sid)] = time.time()', src,
                      "the interrupt op stamps the optimistic state")
        self.assertIn('"interrupting" if _interrupting(sid, session, now, tm)', src,
                      "the chip formula reads the stamp, right under compacting")


class InterruptingSdkFlag(unittest.TestCase):
    """SDK sessions carry the interrupt state on their snapshot (backend _interrupted → tm['interrupting']):
    set at dispatch, cleared EXACTLY at the aborted turn's ResultMessage. The chip keys on THAT flag, not the
    transcript tail — the tail retires at the ResultMessage BEFORE the '[Request interrupted by user]' record
    lands, so keying on it flashed 'Interrupting…' then flipped to 'Working' (the user 2026-07-07)."""

    def setUp(self):
        km._interrupt_clicked.clear()

    def tearDown(self):
        km._interrupt_clicked.clear()

    def test_the_flag_holds_interrupting_regardless_of_the_tail(self):
        km._interrupt_clicked[SID] = NOW - 2
        # the live tail has already retired (empty/closed) — the OLD open_now clear dropped this to 'working'.
        # The SDK flag says the interrupt is still in flight → hold.
        self.assertTrue(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, {"interrupting": True}),
                        "the SDK in-flight flag holds the chip through the live-tail flicker")
        self.assertIn(SID, km._interrupt_clicked)

    def test_the_flag_going_false_settles_it_even_with_a_reopened_tail(self):
        km._interrupt_clicked[SID] = NOW - 2
        # ResultMessage settled → backend cleared _interrupted → snapshot False → settle, no stop-record scan
        # needed, and even though a queued turn has reopened the tail
        sess = {"turns": [{"atoms": [{"type": "assistant", "t": NOW}]}]}
        self.assertFalse(km._interrupting(SID, sess, NOW, {"interrupting": False}),
                         "the SDK flag going False is the settle event")
        self.assertNotIn(SID, km._interrupt_clicked)

    def test_the_false_flag_settles_without_any_stop_record(self):
        km._interrupt_clicked[SID] = NOW - 2
        self.assertFalse(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, {"interrupting": False}),
                         "SDK never needs the transcript scrape — the flag is authoritative")

    def test_a_true_flag_without_a_click_stamp_paints_nothing(self):
        # a stray in-flight flag (e.g. an internal reconnect interrupt) with no user click must not paint the
        # chip — only a user-initiated stop (which stamps _interrupt_clicked) reads as 'interrupting'
        self.assertFalse(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, {"interrupting": True}))

    def test_a_pre_click_snapshot_cannot_settle_the_click(self):
        # THE 2026-08-04 FLAP: the push loop snapshots every backend once, then builds for a while — a stop
        # clicked mid-loop reaches _interrupting with a snapshot taken BEFORE the click, whose
        # interrupting:False is pre-click evidence. It used to pop the stamp and eat the whole in-flight
        # window: the chip read Interrupting…, fell to Working until the real settle, then Ready (later
        # builds saw the flag True but no stamp, which deliberately paints nothing). A stale snapshot must
        # stand down to the transcript path instead — no stop record yet → still interrupting, stamp kept.
        km._interrupt_clicked[SID] = NOW - 2
        stale = {"interrupting": False, "snapT": NOW - 5}   # snapshotted 3s before the click
        self.assertTrue(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, stale),
                        "a snapshot older than the click carries no verdict on it")
        self.assertIn(SID, km._interrupt_clicked, "the stamp survives the stale snapshot")

    def test_a_fresh_snapshot_still_settles(self):
        # the guard is scoped to STALE snapshots only: one taken after the click keeps its authority
        km._interrupt_clicked[SID] = NOW - 2
        fresh = {"interrupting": False, "snapT": NOW - 1}
        self.assertFalse(km._interrupting(SID, {"turns": [{"atoms": []}]}, NOW, fresh),
                         "a post-click snapshot's False flag is the settle event, as designed")
        self.assertNotIn(SID, km._interrupt_clicked)

    def test_a_stale_snapshot_with_a_landed_stop_record_still_settles(self):
        # standing down means falling to the TRANSCRIPT path, not returning True unconditionally — the
        # CLI's stop record at/after the click settles it even while the snapshot lags
        km._interrupt_clicked[SID] = NOW - 2
        intr = {"type": "user", "t": NOW - 1,
                "message": {"role": "user", "content": "[Request interrupted by user]"}}
        stale = {"interrupting": False, "snapT": NOW - 5}
        self.assertFalse(km._interrupting(SID, {"turns": [{"atoms": [intr]}]}, NOW, stale))
        self.assertNotIn(SID, km._interrupt_clicked)

    def test_the_sdk_snapshot_carries_its_capture_time(self):
        # the guard needs snapT on every SDK snapshot — pin the field at source (kernel/sdk_backend.py)
        with open(os.path.join(os.path.dirname(BIN), "kernel", "sdk_backend.py")) as f:
            src = f.read()
        self.assertIn('"snapT": time.time()', src, "snapshot() stamps when it was taken")


class IdleInterruptNeverStrands(unittest.TestCase):
    """A stop pressed on an IDLE session must not paint (let alone strand) 'Interrupting…' (the user
    2026-08-14: it never went back to normal). The flag's only clear events are the aborted turn's
    ResultMessage or a fresh turn — an idle press produces NEITHER, so latching it pinned the snapshot
    at interrupting:True forever, and every later press bought another 120s of 'stopping…' off the
    eternal flag. Same guard _signal_cli has carried since the 2026-07-20 strand; and the kernel's op
    sites stamp _interrupt_clicked only when a turn is actually open (_working_now), so a tmux idle
    press doesn't ride the 120s wedge cap either."""

    def _session(self, inflight):
        sb = SourceFileLoader("romp_sdk_backend_intr", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        s = sb.SdkSession(be, {"sid": SID, "name": "web"})
        s.inflight = inflight
        return s

    class _Loop:
        def __init__(self):
            self.scheduled = 0
        def call_soon_threadsafe(self, fn):
            self.scheduled += 1              # record the dispatch; never run it (no real loop here)

    def test_an_idle_press_does_not_latch_the_flag_but_still_dispatches(self):
        s = self._session(inflight=0)
        s.loop, s.client = self._Loop(), object()          # channel up → the polite control rung
        s.interrupt()
        self.assertFalse(s._interrupted, "no turn to stop → no latch → nothing can strand")
        self.assertEqual(s.loop.scheduled, 1, "the control request itself still goes out (Esc semantics)")

    def test_a_busy_press_latches_exactly_as_before(self):
        s = self._session(inflight=1)
        s.loop, s.client = self._Loop(), object()
        s.interrupt()
        self.assertTrue(s._interrupted, "a running turn latches — its ResultMessage clears it")

    def test_do_interrupt_carries_the_same_guard(self):
        import asyncio
        class _Client:
            async def interrupt(self):
                return None
        s = self._session(inflight=0)
        s.client = _Client()
        asyncio.run(s._do_interrupt())
        self.assertFalse(s._interrupted, "the async leg must not re-latch what interrupt() declined to")
        s2 = self._session(inflight=2)
        s2.client = _Client()
        asyncio.run(s2._do_interrupt())
        self.assertTrue(s2._interrupted)

    def test_the_kernel_op_sites_gate_the_stamp_on_a_live_turn(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertEqual(src.count("_intr_live = _working_now(str(sid))"), 2,
                         "both interrupt entry points (WS op + HTTP route) gauge before dispatch")
        self.assertEqual(src.count("if _intr_live:"), 2,
                         "…and stamp only when a turn was actually open")


class FeedCardInterruptingBadge(unittest.TestCase):
    """The feed CARD wears a steady 'interrupting…' badge while a stop is in flight, then swaps to the
    past-tense 'interrupted' — never flickering between 'working' and 'interrupted' as the live-tail retires
    mid-settle (the user 2026-07-07). Same _interrupting derivation as the chip and the timeline lane."""

    def test_build_feed_computes_and_gates_the_interrupting_badge(self):
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertIn("sess_interrupting = _interrupting(fsid, ps or {}, now, tm)", src,
                      "the card reuses the chip's derivation — safe to call again in this push")
        self.assertIn('"interrupting": bool(sess_interrupting', src, "the card carries the in-flight flag")
        self.assertIn("sess_interrupted and not sess_interrupting", src,
                      "the past-tense badge yields to the in-flight one — never both at once")


class InterruptMarker(unittest.TestCase):
    """The CLI's '[Request interrupted by user]' stop record is an EVENT, not typed input (the user
    2026-07-02): build_session flags it so the chat renders a slim rail marker, never a blue bubble."""

    def test_build_session_flags_the_stop_record(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn('if prompt.strip().startswith("[Request interrupted by user"):', src)
        self.assertIn('ev["interruptMarker"] = True', src)


class InterruptRecordEndsTurn(unittest.TestCase):
    """The CLI's stop record ENDS its turn in the event model (the user 2026-07-05). An interrupt writes
    no end_turn, so the dangling user record read as an OPEN turn in any STATES-LESS parse — which is what
    the kernel's _parse is: the chip latched 'Interrupting…' to its 120s cap and _ops_gate parked a /model
    pick against an idle session, while auto-nudge (whose judge parse folds states) simultaneously read the
    same session as stopped and fired into it. The record is the interrupt event itself, so the turn ends
    on it — no states overlay required."""

    def setUp(self):
        km._downtime[:] = []

    def _turns(self, recs, states=None):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            return em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)],
                                    states=states or [], now=NOW)["turns"]

    def _interrupted(self, marker="[Request interrupted by user]"):
        return [uline(T0, "do the thing", "u1"),
                aline(T0 + 10, "working on it", "a1", "u1", "tool_use"),
                uline(T0 + 60, marker, "u2", "a1")]

    def test_interrupt_record_ends_the_turn_in_a_states_less_parse(self):
        turns = self._turns(self._interrupted())
        self.assertEqual(len(turns), 1, "the stop record folds into the turn it stopped")
        self.assertTrue(turns[-1]["ended"], "the CLI's stop record IS the turn end — no end_turn is coming")
        self.assertFalse(km._session_working(turns), "an interrupted session is not working")

    def test_tool_use_variant_ends_the_turn_too(self):
        turns = self._turns(self._interrupted("[Request interrupted by user for tool use]"))
        self.assertTrue(turns[-1]["ended"], "a permission-prompt dismissal is the same stop event")

    def test_next_prompt_opens_a_fresh_turn(self):
        recs = self._interrupted() + [uline(T0 + 120, "take another angle", "u3", "u2")]
        turns = self._turns(recs)
        self.assertEqual(len(turns), 2, "a prompt after the stop opens fresh, never absorbs into the dead turn")
        self.assertEqual(turns[0]["ended"], True)
        self.assertEqual((turns[1]["trigger"] or {}).get("uuid"), "u3")

    def test_plain_dangling_prompt_still_reads_open(self):
        # regression guard: a NORMAL not-yet-answered prompt (no stop record) is optimistic working
        turns = self._turns([uline(T0, "do the thing", "u1"),
                             aline(T0 + 10, "working on it", "a1", "u1", "tool_use")])
        self.assertFalse(turns[-1]["ended"])
        self.assertTrue(km._session_working(turns), "an open turn without a stop record still reads working")

    def test_suppression_holds_through_trailing_idle_atoms(self):
        # the judge-side parse folds a states idle atom AFTER the stop record — the scan must not care
        turns = self._turns(self._interrupted(), states=[{"t": T0 + 61, "state": "idle"}])
        self.assertTrue(km._interrupt_suppresses_nudge(turns),
                        "interrupt behind an idle span still reads as the user's last action")
        normal = self._turns([uline(T0, "ask", "u1"), aline(T0 + 10, "done", "a1", "u1", "end_turn")])
        self.assertFalse(km._interrupt_suppresses_nudge(normal), "a normally-ended turn is not user-stopped")

    def test_a_peer_message_does_not_lift_suppression(self):
        # requirement (the user 2026-07-05 via ui): suppressed until the USER's next message — a peer
        # postal turn (author {"peer": …} via the romp-msg-id marker) ending in between must not re-arm
        recs = self._interrupted() + [
            uline(T0 + 200, "QUESTION: which port?\nromp-msg-id: 1111.2_3.TESTHOST", "u3", "u2"),
            aline(T0 + 220, "answered the peer", "a2", "u3", "end_turn")]
        self.assertTrue(km._interrupt_suppresses_nudge(self._turns(recs)),
                        "a peer's postal message is not the user speaking — still suppressed")

    def test_the_users_next_message_lifts_suppression(self):
        recs = self._interrupted() + [uline(T0 + 200, "ok, take the other approach", "u3", "u2"),
                                      aline(T0 + 220, "on it", "a2", "u3", "end_turn")]
        self.assertFalse(km._interrupt_suppresses_nudge(self._turns(recs)),
                         "the user spoke after the interrupt → the user-message event re-arms the nudge")


class InterruptStampHoldsUntilLanded(unittest.TestCase):
    """The 'Interrupting…' chip holds from the click until the stop LANDS (its CLI stop record), never
    flipping to 'working' in between (the user 2026-07-05: they hit stop, saw 'Interrupting…', then it
    flipped back to 'Working' before the stop had landed). An earlier `turn_t > t0` guard cleared the stamp
    the moment the open turn's start postdated the click — but the SDK's queued-turn release and live-tail
    flicker push that start past the click DURING the settle, so the chip flipped early. Keying on the stop
    RECORD holds it for the whole in-flight window and still covers a genuinely later turn."""

    def setUp(self):
        km._interrupt_clicked.clear()

    def tearDown(self):
        km._interrupt_clicked.clear()

    def _intr(self, t):
        return {"type": "user", "t": t, "message": {"role": "user", "content": "[Request interrupted by user]"}}

    def test_open_turn_no_record_yet_stays_interrupting_even_when_it_started_after_the_click(self):
        # the regression: a live-merged / queued-turn start postdates the click, but the stop hasn't landed —
        # the OLD guard cleared here (turn_t NOW-2 > t0 NOW-5) and the chip fell to 'working' prematurely.
        km._interrupt_clicked[SID] = NOW - 5
        sess = {"turns": [{"atoms": [{"type": "assistant", "t": NOW - 2}]}]}   # open, started after the click, NO stop record
        self.assertTrue(km._interrupting(SID, sess, NOW, None),
                        "no stop record yet → still in flight → Interrupting…, not a premature 'working'")
        self.assertIn(SID, km._interrupt_clicked, "stamp survives — the stop hasn't landed")

    def test_the_stop_record_landing_clears_the_stamp(self):
        # a message queued behind the stop reopened the turn (open_now True), but the interrupted turn's stop
        # record is on disk → the stop landed → the chip falls to honest 'working' for the genuine new work
        km._interrupt_clicked[SID] = NOW - 5
        sess = {"turns": [{"atoms": [self._intr(NOW - 3)]}, {"atoms": [{"type": "assistant", "t": NOW - 1}]}]}
        self.assertFalse(km._interrupting(SID, sess, NOW, None),
                         "the CLI's stop record landed → the interrupt is done, the new work is genuine 'working'")
        self.assertNotIn(SID, km._interrupt_clicked, "stamp consumed, never re-latches")

    def test_the_interrupted_turn_itself_keeps_the_stamp(self):
        km._interrupt_clicked[SID] = NOW - 30
        sess = {"turns": [{"atoms": [{"type": "assistant", "t": NOW - 60}]}]}   # the running turn, no stop record yet
        self.assertTrue(km._interrupting(SID, sess, NOW, None),
                        "the turn that was running at the click wears the stamp until its stop lands")

    def test_a_stale_record_from_a_prior_interrupt_does_not_clear(self):
        # an interrupt record from BEFORE this click (an earlier stop of an earlier turn) must not count as
        # THIS stop landing — only a record at/after the click does
        km._interrupt_clicked[SID] = NOW - 5
        sess = {"turns": [{"atoms": [self._intr(NOW - 600)]}, {"atoms": []}]}   # old record, current turn still open
        self.assertTrue(km._interrupting(SID, sess, NOW, None),
                        "only a stop record at/after the click marks THIS interrupt landed")


class AutoNudgeInterruptGate(unittest.TestCase):
    """Auto-nudge NEVER fires off a turn the user interrupted (the user 2026-07-05): the stop record means
    the human is at the controls — a nudge then steals the session (their case: a 2-minute turn on the old
    model) and holds parked drive ops behind it. Likewise a session with PARKED ops (a queued send / model
    pick) is being driven — a nudge would jump the user's queue. Both gates are event-based: the CLI's stop
    record, and the _pending_ops FIFO itself."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, jd.CLOSER_ON)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        jd.CLOSER_ON = False              # closer-verdict gate idles → the new gates are what's exercised
        jd.GOALDIR.mkdir(parents=True)
        km._downtime[:] = []
        km._parse_cache.clear()
        km._autonudge_cache.clear()
        km._pending_ops.clear()
        self.tmux = {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None}}
        km._set_auto_nudge(True)

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, jd.CLOSER_ON) = self.saved
        km._pending_ops.clear()
        km._parse_cache.clear()
        self.td.cleanup()

    def _transcript(self, interrupted):
        recs = [uline(T0, "first ask", "u1"),
                aline(T0 + 20, "Done.", "a1", "u1", "end_turn"),
                uline(T0 + 100, "second ask", "u2", "a1"),
                aline(T0 + 120, "digging in", "a2", "u2", "tool_use")]
        if interrupted:
            recs.append(uline(T0 + 130, "[Request interrupted by user]", "u3", "a2"))
        else:
            recs.append(aline(T0 + 130, "Finished the second ask.", "a3", "a2", "end_turn"))
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _goal(self):
        g = SID + ":gw"
        store = {"rompUuid": SID, "seq": 1, "lastNode": g, "closedTurns": [],
            "nodes": {g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                          "blocked": False, "cleared": False, "trail": [], "t": T0}},
            "placements": {}, "status": {g: "working"}}
        # mirror a caught-up planner (the 2026-07-15 placement gate): the fire path requires every
        # due unit placed, and these fixtures mean "the judges ruled and left the goal working"
        try:
            turns = jd.parsed_session(SID, [str(self.tpath)], NOW)["turns"]
            for u in jd.plan_units({"turns": turns}, store):
                store["placements"][jd._unit_key(u[0], u[1])] = None
        except Exception:
            pass
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        return g

    def _stub(self):
        sent = []
        saved = km._tmux_send, jd.optimistic_followup
        km._tmux_send = lambda name, body, **kw: sent.append((name, body))
        jd.optimistic_followup = lambda sid, gid: True

        def restore():
            km._tmux_send, jd.optimistic_followup = saved
        return sent, restore

    def test_control_a_normally_ended_stall_still_nudges(self):
        # fixture validity: without a stop record or parked ops, this exact setup DOES nudge — so the
        # no-fire asserts below test the gates, not a broken fixture.
        self._transcript(interrupted=False)
        g = self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(len(sent), 1, "the orphaned working goal is nudged")
            self.assertIn("romp-goal-id: " + g, sent[0][1])
        finally:
            restore()

    def test_no_nudge_after_a_user_interrupt(self):
        self._transcript(interrupted=True)
        self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(sent, [], "the user stopped this turn themselves — they're driving, not stalled")
        finally:
            restore()

    def test_no_nudge_while_drive_ops_are_parked(self):
        self._transcript(interrupted=False)
        self._goal()
        km._pending_ops[SID] = [("model", "fable")]
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(sent, [], "queued user intent outranks a nudge — never jump the user's queue")
        finally:
            restore()

    def _append(self, recs):
        with open(self.tpath, "a") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

    def test_a_peer_turn_after_the_interrupt_stays_suppressed(self):
        # requirement (the user 2026-07-05 via ui): suppression lifts on the USER-message event only — a
        # peer postal exchange ending after the interrupt used to make the latest turn read 'genuine' and
        # re-arm the nudge.
        self._transcript(interrupted=True)
        self._append([uline(T0 + 200, "COORDINATE: heads-up\nromp-msg-id: 1111.2_3.TESTHOST", "u4", "u3"),
                      aline(T0 + 220, "acknowledged", "a4", "u4", "end_turn")])
        self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(sent, [], "a peer spoke, the user didn't — still their pause, still suppressed")
        finally:
            restore()

    def test_the_users_message_after_the_interrupt_rearms_the_nudge(self):
        self._transcript(interrupted=True)
        self._append([uline(T0 + 200, "keep going with plan B", "u4", "u3"),
                      aline(T0 + 220, "resuming with plan B", "a4", "u4", "end_turn")])
        self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(len(sent), 1, "the user re-engaged and the goal re-stalled → nudging resumes")
        finally:
            restore()

    def test_feed_card_wears_the_interrupted_badge(self):
        # the floated badge (the user 2026-07-05): a working card whose session the user stopped says
        # "interrupted" instead of sitting silent like an orphaned goal. Cache-only like the working dot.
        self._transcript(interrupted=True)
        self._goal()
        km._parse(str(self.tpath), SID, NOW)                       # warm the cache (stands in for _warm_fleet_bg)
        card = next(a for a in km.build_feed(NOW, self.tmux)["asks"] if a["itemId"] == SID + ":gw")
        self.assertTrue(card.get("interrupted"), "user-stopped + no message since → interrupted badge")

    def test_feed_badge_clears_once_the_user_re_engages(self):
        self._transcript(interrupted=True)
        self._append([uline(T0 + 200, "keep going with plan B", "u4", "u3")])   # user spoke; turn back open
        self._goal()
        km._parse(str(self.tpath), SID, NOW)
        card = next(a for a in km.build_feed(NOW, self.tmux)["asks"] if a["itemId"] == SID + ":gw")
        self.assertFalse(card.get("interrupted"), "the user's next message retires the badge")


class AutoNudgeLoopGate(AutoNudgeInterruptGate):
    """A session that schedules its own next move is not stalled (the user 2026-08-15, whose looping
    workers were nudged four times in an hour — every iteration's ended turn re-armed the nudge, the
    goal never completes because it's a loop, and each status-ask burned the next beat and could
    supersede the pending wakeup). While the newest genuine ended turn carries a ScheduleWakeup or
    CronCreate tool_use, auto-nudge stands down; the first genuine turn that ends WITHOUT scheduling
    lifts the gate — an event, never a timer."""

    def _sched_line(self, uuid, parent, tool):
        return {"type": "assistant", "timestamp": iso(T0 + 220), "uuid": uuid, "parentUuid": parent,
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": "quiet hold; next pass on the timer"},
                                        {"type": "tool_use", "name": tool, "input": {"delaySeconds": 1200}}],
                            "stop_reason": "end_turn"}}

    def _loop_transcript(self, tool="ScheduleWakeup"):
        self._transcript(interrupted=False)
        self._append([uline(T0 + 200, "keep the batches coming on your own cadence", "u5", "a3"),
                      self._sched_line("a5", "u5", tool)])

    def test_a_self_pacing_session_is_never_nudged(self):
        for tool in ("ScheduleWakeup", "CronCreate"):
            km._parse_cache.clear()
            km._autonudge_cache.clear()
            self._loop_transcript(tool)
            self._goal()
            sent, restore = self._stub()
            try:
                km._auto_nudge_tick(NOW, self.tmux)
                self.assertEqual(sent, [], "%s = the session paces itself — let it loop" % tool)
            finally:
                restore()

    def test_a_nudge_response_cannot_strip_the_loops_protection(self):
        # one derailment already happened (a nudge's own romp-injected response turn ended, without
        # scheduling) — the arm scan skips injected turns, so the loop's own last word still rules
        self._loop_transcript()
        self._append([uline(T0 + 300, "<!-- romp-injected -->[romp] where does this stand?", "u6", "a5"),
                      aline(T0 + 320, "still iterating on the timer.", "a6", "u6", "end_turn")])
        self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(sent, [], "a romp-injected turn neither re-arms nor lifts the loop gate")
        finally:
            restore()

    def test_a_genuine_unscheduled_end_lifts_the_gate(self):
        # the loop finished (or the user redirected it): its last genuine turn ends with no
        # scheduling tool → normal stall rules resume and the orphaned working goal is nudged
        self._loop_transcript()
        self._append([uline(T0 + 300, "wrap it up and summarize", "u6", "a5"),
                      aline(T0 + 320, "stopping the loop; summary next.", "a6", "u6", "end_turn")])
        self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(len(sent), 1, "no pending self-schedule → the stall is real again")
        finally:
            restore()


class WakeupScheduledUnit(unittest.TestCase):
    """_wakeup_scheduled in isolation: the newest genuine ended turn's tool_use blocks decide."""

    def _turn(self, ended=True, tools=(), injected=False):
        atoms = []
        if injected:
            atoms.append({"type": "user", "t": T0,
                          "message": {"role": "user", "content": "<!-- romp-injected -->[romp] hi"}})
        else:
            atoms.append({"type": "user", "t": T0, "message": {"role": "user", "content": "go"}})
        content = [{"type": "text", "text": "ok"}] + [{"type": "tool_use", "name": t, "input": {}} for t in tools]
        atoms.append({"type": "assistant", "t": T0 + 1, "message": {"role": "assistant", "content": content}})
        return {"id": "t", "t": T0, "ended": ended, "atoms": atoms,
                "trigger": None if injected else {"uuid": "u"}}

    def test_schedule_and_cron_both_count_nothing_else_does(self):
        self.assertTrue(km._wakeup_scheduled([self._turn(tools=("ScheduleWakeup",))]))
        self.assertTrue(km._wakeup_scheduled([self._turn(tools=("Bash", "CronCreate"))]))
        self.assertFalse(km._wakeup_scheduled([self._turn(tools=("Bash", "TaskCreate"))]))
        self.assertFalse(km._wakeup_scheduled([self._turn(tools=())]))
        self.assertFalse(km._wakeup_scheduled([]))

    def test_only_the_newest_genuine_ended_turn_rules(self):
        older = self._turn(tools=("ScheduleWakeup",))
        newer = self._turn(tools=())
        self.assertFalse(km._wakeup_scheduled([older, newer]),
                         "a later genuine turn without scheduling lifts the gate")
        open_turn = self._turn(ended=False, tools=())
        self.assertTrue(km._wakeup_scheduled([older, open_turn]),
                        "an OPEN turn has not ruled yet — the loop's last ended word stands")


class AutoNudgeArming(AutoNudgeInterruptGate):
    """Arming keys on the newest GENUINE ended turn (arm_id), not the latest turn (the user 2026-07-06,
    business): a kernel-restart resume banner (romp-injected) opened a session's last turn and the old
    `_turn_romp_injected(latest)` gate then suppressed every FIRST nudge until some genuine turn ended —
    an idle session with a working card that could never be nudged. A romp-injected turn must neither
    RE-ARM a nudge (the 2026-07-01 runaway stays fixed) nor BLOCK a first one."""

    def _write(self, recs):
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _genuine_then_romp_banner(self):
        self._write([
            uline(T0, "please wire the thing", "u1"),
            aline(T0 + 20, "on it — paused here", "a1", "u1", "end_turn"),
            uline(T0 + 100, "<!-- romp-injected -->[romp] The romp kernel restarted and cut this "
                            "session's in-flight turn; resumed with history intact.", "u2", "a1"),
            aline(T0 + 120, "resumed.", "a2", "u2", "end_turn")])

    def test_a_restart_banner_last_turn_does_not_block_the_first_nudge(self):
        self._genuine_then_romp_banner()
        g = self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(len(sent), 1, "the working goal takes its FIRST nudge even though the "
                                           "latest turn is romp-injected (the restart banner)")
            self.assertIn("romp-goal-id: " + g, sent[0][1])
        finally:
            restore()

    def test_a_nudge_response_turn_never_refires(self):
        # the 2026-07-01 runaway guard, restated on arm_id: after a nudge, the agent's romp-triggered
        # response turn ends — arm_id hasn't moved, so the second tick marks nudge-failed, never re-sends.
        self._genuine_then_romp_banner()
        self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(len(sent), 1)
            self._write([
                uline(T0, "please wire the thing", "u1"),
                aline(T0 + 20, "on it — paused here", "a1", "u1", "end_turn"),
                uline(T0 + 100, "<!-- romp-injected -->[romp] status check follow-up", "u2", "a1"),
                aline(T0 + 120, "still where I left it.", "a2", "u2", "end_turn")])
            km._parse_cache.clear()
            km._auto_nudge_tick(NOW + 10, self.tmux)
            self.assertEqual(len(sent), 1, "a romp-triggered response turn does not move arm_id → no re-fire")
        finally:
            restore()

    def test_romp_only_history_never_fires(self):
        self._write([
            uline(T0 + 100, "<!-- romp-injected -->[romp] The romp kernel restarted.", "u1"),
            aline(T0 + 120, "resumed.", "a1", "u1", "end_turn")])
        self._goal()
        sent, restore = self._stub()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(sent, [], "no genuine ended turn to arm off → never fires")
        finally:
            restore()


class KernelDisplayParseReadsStates(unittest.TestCase):
    """The DISPLAY parse (kernel _parse — feeds _session_working on the chip, feed card, timeline lane) must
    read states/<sid>.jsonl exactly like jd.parsed_session does. The interrupt/settle idle transition lands
    in states/, NOT the transcript: an SDK stop need not write any transcript record. So a states-only change
    must both (a) synthesize an idle atom in the display parse and (b) BUST the parse cache (keyed on the
    transcript alone before, so the fresh idle was invisible). Before this, an SDK interrupt cleared 'working'
    for the judge/nudge (which read states) but the chip/card/lane stayed yellow (the user 2026-07-22:
    "pressing interrupt doesn't clear working"). Synthetic fixtures only."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._parse_cache.clear()
        self.tpath = Path(self.td.name) / (SID + ".jsonl")
        # an OPEN turn: the assistant stops on tool_use (no end_turn) and no interrupt record → reads working
        recs = [uline(T0, "do the thing", "u1"),
                aline(T0 + 10, "working on it", "a1", "u1", "tool_use")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def tearDown(self):
        jd.STATE = self.saved
        km._parse_cache.clear()
        self.td.cleanup()

    def _write_idle(self):
        # exactly what SdkBackend.interrupt / _record_idle append: a backdated idle transition, states/ only
        p = jd.STATE / "states" / (SID + ".jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"t": NOW - 1, "state": "idle"}) + "\n")

    def test_open_turn_reads_working_before_any_idle(self):
        ps = km._parse(str(self.tpath), SID, NOW)
        self.assertTrue(km._session_working(ps["turns"]),
                        "an open tool_use turn with no idle transition → working")

    def test_a_states_only_idle_clears_working_and_busts_the_cache(self):
        # first parse: no states file yet → working, and it caches keyed on the transcript
        self.assertTrue(km._session_working(km._parse(str(self.tpath), SID, NOW)["turns"]),
                        "premise: the open turn reads working and is now cached")
        self._write_idle()                              # the interrupt writes idle to states/ ONLY (transcript untouched)
        ps = km._parse(str(self.tpath), SID, NOW)
        self.assertTrue(any(a.get("type") == "idle" for turn in ps["turns"] for a in turn["atoms"]),
                        "the states idle transition became an idle atom in the display parse")
        self.assertFalse(km._session_working(ps["turns"]),
                         "a states-only idle transition clears 'working' on the display parse (cache busted)")
