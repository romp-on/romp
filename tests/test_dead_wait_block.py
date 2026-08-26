#!/usr/bin/env python3
"""A DORMANT session's stamped-awaiting Working card converts to a procedural block (the user
2026-08-22): the CLI died while a judged wait still stood, so nothing that could answer it is
running — yet a live awaiting stamp exempted the card from the whole ladder (wake, nudge, staller)
and it sat "paused" in Working forever (two live cards measured at 79 hours). The conversion is
event-triggered (the death transition; a boot catch-up sweep), once per stamp episode, stands down
for restart cuts (the resume machinery owns those), and its why is a recognized procedural block.
SYNTHETIC fixtures only (placeholder UUIDs, invented text)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

STAMP_T = 1781100000
_N = [0]


def _fresh_sid():
    """A distinct sid per test: the goals-store cache is mtime-keyed, and same-second reseeds of one
    sid would hand a later test the previous test's mutated store object."""
    _N[0] += 1
    return "11111111-2222-3333-4444-5555555555%02d" % _N[0]


SID = ""
GID = ""


def _register_name(sid):
    """A names-registry entry — the launch record BOTH backends write at creation. It is what marks
    a reg-less sid as one tmux could have run; a sid with no reg and no names entry exists only as
    a transcript (the file fallback's world), and no liveness owner here can answer for it."""
    jd.NAMES.mkdir(parents=True, exist_ok=True)
    (jd.NAMES / sid).write_text("web\t~/notes-api\t#3355aa\t#ffffff\n")


def _seed_store(awaiting=True, named=True):
    # named=True: the fixture models a romp-LAUNCHED session (the usual world), so the owner scan
    # is entitled to settle it; named=False models a transcript-derived one (no launch record).
    if named:
        _register_name(SID)
    store = jd.load_goals(SID)
    nd = {"id": GID, "text": "delegate the batch and report", "parentId": None,
          "nodeComplete": False, "blocked": False, "cleared": False, "t": STAMP_T - 100,
          "mt": STAMP_T, "trail": [], "doneWhy": "",
          "log": [{"ev_t": STAMP_T, "src": "closer", "kind": "awaiting",
                   "why": "both workers' report-backs", "at": STAMP_T}]}
    if awaiting:
        nd["awaitingWhy"] = "both workers' report-backs"
        nd["awaitingAt"] = STAMP_T
        nd["awaitingKind"] = "peer"
    store["nodes"][GID] = jd.GuardedNode(nd)
    store["status"] = {GID: "working"}
    jd.save_goals(SID, store)
    return store


def _write_state(state, t):
    d = jd.STATE / "states"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / (SID + ".jsonl"), "w") as f:
        f.write(json.dumps({"state": state, "t": t}) + "\n")


class _HermeticDeadWait(unittest.TestCase):
    def setUp(self):
        global SID, GID
        SID = _fresh_sid()
        GID = SID + ":g1"
        km._PREV_ALIVE = None
        self.nudged = {}
        # hermetic liveness: never read whether THIS box has tmux (the corroboration the sweep now
        # does before converting would otherwise shell out); an authoritative empty owner scan is
        # the corroborated-dead world these tests were written in
        km._TMUX.available = lambda: True
        km._TMUX.alive_sids = lambda t=3: set()

    def tearDown(self):
        for nm in ("available", "alive_sids"):
            km._TMUX.__dict__.pop(nm, None)   # instance attrs shadow the class methods; drop them
        for d in (jd.GOALDIR, jd.STATE / "states", jd.SDKDIR, jd.STATE / "gone", jd.NAMES):
            if d.is_dir():
                for f in d.glob("*"):
                    f.unlink()
        p = jd.STATE / "auto-nudge.json"
        if p.exists():
            p.unlink()


class DeadWaitBlock(_HermeticDeadWait):
    def test_dormant_stamped_card_converts_to_a_recognized_procedural_block(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        fired = km._dead_wait_block(SID, GID, STAMP_T, "both workers' report-backs", self.nudged, STAMP_T + 900)
        self.assertTrue(fired)
        store = jd.load_goals(SID)
        nd = store["nodes"][GID]
        self.assertTrue(nd.get("blocked"), "the card lands in the terminal the ladder promises: blocked")
        self.assertTrue(str(nd.get("blockWhy") or "").startswith(jd.DEAD_WAIT_WHY_PREFIX))
        self.assertIn("both workers' report-backs", nd.get("blockWhy") or "",
                      "the brief names WHAT died with the session")
        self.assertTrue(jd.procedural_block_why(nd.get("blockWhy")),
                        "a dead wait is romp bookkeeping — the briefer must not invent a decision")
        # the evidence time is the newest recorded event (the settle), never wall-clock now
        blk = [e for e in nd.get("log", []) if e.get("kind") == "block"][-1]
        self.assertEqual(blk.get("ev_t"), STAMP_T + 50)

    def test_an_open_turn_last_state_stands_down_for_the_resume_machinery(self):
        _seed_store()
        _write_state("working", STAMP_T + 50)   # a restart CUT — the resume nudge owns this card
        self.assertFalse(km._dead_wait_block(SID, GID, STAMP_T, "w", self.nudged, STAMP_T + 900))
        self.assertFalse(jd.load_goals(SID)["nodes"][GID].get("blocked"))

    def test_once_per_stamp_episode_and_a_new_anchor_rearms(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        self.assertTrue(km._dead_wait_block(SID, GID, STAMP_T, "w", self.nudged, STAMP_T + 900))
        self.assertFalse(km._dead_wait_block(SID, GID, STAMP_T, "w", self.nudged, STAMP_T + 950),
                         "same episode never converts twice")
        # a genuinely NEW stamp episode (newer anchor) re-arms — but the fresh-store guard still
        # refuses while the card sits blocked, so no double-block either
        self.assertFalse(km._dead_wait_block(SID, GID, STAMP_T + 100, "w", self.nudged, STAMP_T + 990))

    def test_a_lifted_stamp_or_resolved_card_stands_down(self):
        _seed_store(awaiting=False)             # no live stamp on the fresh read
        _write_state("idle", STAMP_T + 50)
        self.assertFalse(km._dead_wait_block(SID, GID, STAMP_T, "w", self.nudged, STAMP_T + 900))

    def test_boot_catchup_sweep_converts_dormant_stores_and_spares_alive_ones(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = None                   # first tick after boot
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        self.assertTrue(jd.load_goals(SID)["nodes"][GID].get("blocked"), "boot catch-up found the dead wait")
        # …and an ALIVE session is never swept: reseed and list it as alive
        self.tearDown(); self.setUp()
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = None
        km._dead_wait_sweep({SID}, self.nudged, STAMP_T + 900)
        self.assertFalse(jd.load_goals(SID)["nodes"][GID].get("blocked"))

    def test_a_post_stamp_peer_ack_does_not_hide_the_wait_from_the_sweep(self):
        # the 100-hour survivors (2026-08-23): a worker's "starting now" mail seconds after the stamp
        # made the peer-answered supersede read the wait as met, so the sweep stood down forever while
        # the chip kept showing awaiting. The sweep reads the RAW stamp: a dormant owner can't process
        # an answer anyway, so a recorded wait on a Working card converts regardless.
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        saved = km._peer_answered_at
        km._peer_answered_at = lambda sid: STAMP_T + 110   # an ack landed just after the stamp
        try:
            km._PREV_ALIVE = None
            km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        finally:
            km._peer_answered_at = saved
        self.assertTrue(jd.load_goals(SID)["nodes"][GID].get("blocked"),
                        "the supersede must not hide a dormant owner's wait from the sweep")

    def test_death_transition_triggers_between_ticks(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = {SID}                  # was alive last tick…
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)   # …gone this tick: the death event
        self.assertTrue(jd.load_goals(SID)["nodes"][GID].get("blocked"))

    def test_the_block_writer_settles_the_brief_inline(self):
        # "Stuck on Distilling" (the user 2026-08-23): a dead store falls out of discover's 48h window,
        # so no distill pass ever writes its brief — the card asked for one forever. The procedural why
        # IS the decision; the writer settles blockSummary/briefedMt itself.
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = None
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        nd = jd.load_goals(SID)["nodes"][GID]
        self.assertTrue(nd.get("blocked"))
        self.assertEqual(nd.get("blockSummary"), nd.get("blockWhy"),
                         "the brief settles at the writer — never left for a pass that will not come")
        self.assertIsNotNone(nd.get("briefedMt"))

    def test_the_sweep_heals_a_pre_existing_briefless_procedural_block(self):
        # Blocks written before the writers settled briefs inline: blocked, procedural why, no brief.
        _seed_store()
        st = jd.load_goals(SID)
        nd = st["nodes"][GID]
        jd.record_verdict(st, nd, "nudge", "block", STAMP_T + 100,
                          why=jd.dead_wait_block_why("the full test suite it kicked off"))
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        self.assertIsNone(jd.load_goals(SID)["nodes"][GID].get("blockSummary"))
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = None
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        nd = jd.load_goals(SID)["nodes"][GID]
        self.assertTrue((nd.get("blockSummary") or "").startswith(jd.DEAD_WAIT_WHY_PREFIX),
                        "the repair settles the stuck card's brief from its own why")

    def test_a_genuine_block_why_is_never_repaired_over(self):
        # The repair takes PROCEDURAL whys only: a genuine decision brief stays the briefer's job.
        _seed_store()
        st = jd.load_goals(SID)
        nd = st["nodes"][GID]
        jd.record_verdict(st, nd, "closer", "block", STAMP_T + 100,
                          why="pick a database: sqlite or postgres?")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = None
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        self.assertIsNone(jd.load_goals(SID)["nodes"][GID].get("blockSummary"),
                          "a substantive ask keeps waiting for the real briefer")

    def test_wake_goal_routes_its_dormant_branch_here(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("return _dead_wait_block(sid, gid, at, why, nudged, now)", src)
        self.assertIn("_dead_wait_sweep(alive_ids, nudged, now)", src)


class DeadWaitCorroboration(_HermeticDeadWait):
    """The sweep's trigger — absence from a RAW liveness listing — inherits every collapse that
    listing has (tmux list error/timeout empties the map for a cycle; a swallowed SDK live-merge
    exception does the same to the merged half), and the block it files is irreversible bookkeeping
    on the user's board with nothing to lift it when the listing returns. So absence alone NEVER
    files: the death is corroborated with the liveness OWNER first (the SDK reg's alive bit / a
    standing death record / the owner scan), and an unconfirmable candidate stands down for the
    cycle with its transition kept armed — the doctrine _death_sweep_tick and _death_boot_pass follow."""

    def _blocked(self):
        return bool(jd.load_goals(SID)["nodes"][GID].get("blocked"))

    def test_a_raw_listing_collapse_alone_never_files(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._TMUX.alive_sids = lambda t=3: {SID}   # the OWNER answers alive — the raw listing blinked
        km._PREV_ALIVE = {SID}
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)   # empty alive set: the collapse shape
        self.assertFalse(self._blocked(), "an owner-corroborated ALIVE session must never convert")
        self.assertIn(SID, km._PREV_ALIVE, "the death transition stays armed for a genuine later death")

    def test_probe_failure_stands_down_and_the_next_tick_retries(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._TMUX.alive_sids = lambda t=3: None    # a REAL probe failure — cannot confirm either way
        km._PREV_ALIVE = {SID}
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        self.assertFalse(self._blocked(), "unconfirmed is never dead — nothing files")
        self.assertIn(SID, km._PREV_ALIVE, "the candidate is kept, not spent")
        km._TMUX.alive_sids = lambda t=3: set()   # the probe recovers and corroborates the death…
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 950)
        self.assertTrue(self._blocked(), "…and the retried tick converts")

    def test_sdk_reg_alive_bit_outranks_the_merged_maps_absence(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"alive": True}))
        km._PREV_ALIVE = {SID}
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)   # the swallowed SDK-merge shape
        self.assertFalse(self._blocked(),
                         "alive:True is live/revivable/crash-looped — the resume contract owns it")
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"alive": False}))
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 950)
        self.assertTrue(self._blocked(), "alive:False is the owner's durable answer — it converts")

    def test_a_standing_death_record_corroborates_without_a_probe(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        gone = jd.STATE / "gone"
        gone.mkdir(parents=True, exist_ok=True)
        (gone / (SID + ".json")).write_text(json.dumps({"t": STAMP_T + 60, "by": "gone"}))
        km._TMUX.alive_sids = lambda t=3: None    # even with the probe down…
        km._PREV_ALIVE = {SID}
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        self.assertTrue(self._blocked(), "…a death a corroborated writer already stamped answers")

    def test_wake_goal_headless_branch_stands_down_for_file_derived_sessions(self):
        # a no-tmux box's _alive_sessions falls back to FILE-derived sessions, which reach
        # _wake_goal absent from the merged map while genuinely ALIVE — no owner here can answer
        # for a reg-less one, so nothing may file
        _seed_store(named=False)                  # transcript-derived: no launch record
        _write_state("idle", STAMP_T + 50)
        km._TMUX.available = lambda: False
        store = jd.load_goals(SID)
        fired = km._wake_goal(SID, GID, (STAMP_T, "w"), self.nudged, [], store,
                              STAMP_T + 900, {}, {})
        self.assertFalse(fired)
        self.assertFalse(self._blocked(), "a reg-less file-derived session has no owner to ask")
        # …but a genuinely ENDED SDK session still converts on the same box: the reg answers
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"alive": False}))
        fired = km._wake_goal(SID, GID, (STAMP_T, "w"), self.nudged, [], store,
                              STAMP_T + 950, {}, {})
        self.assertTrue(fired)
        self.assertTrue(self._blocked())

    def test_tmux_appearing_mid_flight_cannot_settle_a_sid_it_never_owned(self):
        # AUTHORITY FOLLOWS OWNERSHIP: a headless box's file fallback lists a LIVE
        # transcript-derived session (no reg, no names entry — launched by neither backend); it
        # drops out of the file-derived alive set, arming its death transition… and THEN tmux is
        # installed. The availability probe is live (shutil.which), so keying the stand-down on
        # the BOX's tmux availability would let the fresh, EMPTY server — which never ran this
        # sid — answer as its liveness owner: a false conversion of a live session's card. An
        # owner scan settles only sids the owner could have run; this one stands down REGARDLESS
        # of tmux availability.
        _seed_store(named=False)                  # transcript-derived: no launch record
        _write_state("idle", STAMP_T + 50)
        km._TMUX.available = lambda: True         # tmux just appeared mid-flight…
        km._TMUX.alive_sids = lambda t=3: set()   # …and its fresh server owns nothing
        self.assertIsNone(km._dead_wait_corroborated(SID),
                          "an owner scan settles only sids the owner could have run")
        km._PREV_ALIVE = {SID}
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 900)
        self.assertFalse(self._blocked(), "a sid tmux never ran must not convert on tmux's word")
        self.assertIn(SID, km._PREV_ALIVE, "stood down and kept armed, never spent")
        # …while the SAME sid WITH a launch record is the owner's to settle: it converts
        _register_name(SID)
        km._dead_wait_sweep(set(), self.nudged, STAMP_T + 950)
        self.assertTrue(self._blocked())


class DeadWaitStandDownLogging(_HermeticDeadWait):
    """Wedge-time log ergonomics: a stand-down is LOUD (the fail-loudly rule — a silent one wedges
    a candidate forever with no trace to act on) but collapsed to ONE line per sweep pass
    (_death_sweep_tick's idiom) — the per-candidate line multiplies by the candidate count under
    exactly the wedge it reports (a 20-session listing collapse would log every candidate every
    tick at the pusher cadence)."""

    def _blocked(self):
        return bool(jd.load_goals(SID)["nodes"][GID].get("blocked"))

    def test_probe_failure_logs_one_line_per_pass_not_per_candidate(self):
        sid2 = _fresh_sid()
        _register_name(SID)
        _register_name(sid2)
        km._TMUX.alive_sids = lambda t=3: None    # a REAL probe failure, shared by the whole pass
        km._PREV_ALIVE = {SID, sid2}
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._dead_wait_sweep(set(), {}, STAMP_T + 900)
        lines = [ln for ln in buf.getvalue().splitlines() if "dead-wait" in ln and "probe" in ln]
        self.assertEqual(len(lines), 1, "one line per pass, not per candidate: %r" % lines)
        self.assertIn("2 candidate(s) stood down this pass", lines[0])
        self.assertEqual({SID, sid2} & km._PREV_ALIVE, {SID, sid2}, "both kept armed")

    def test_unreadable_reg_stand_down_is_loud_and_per_pass_deduped(self):
        sid2 = _fresh_sid()
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text("{not json")     # an unreadable owner reg
        (jd.SDKDIR / (sid2 + ".json")).write_text("{not json")
        km._PREV_ALIVE = {SID, sid2}
        buf = io.StringIO()
        with redirect_stderr(buf):
            km._dead_wait_sweep(set(), {}, STAMP_T + 900)
        lines = [ln for ln in buf.getvalue().splitlines()
                 if "dead-wait" in ln and "unreadable" in ln]
        self.assertEqual(len(lines), 1,
                         "silent forever is a wedge with no trace; per-candidate is a flood: %r" % lines)
        self.assertIn("2 candidate(s) stood down this pass", lines[0])
        self.assertEqual({SID, sid2} & km._PREV_ALIVE, {SID, sid2}, "both kept armed")

    def test_single_probe_callers_still_name_the_sid(self):
        # _wake_goal's dormant branch corroborates ONE sid per call — there its line IS the pass,
        # and naming the sid is what makes the trace actionable
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text("{not json")
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertIsNone(km._dead_wait_corroborated(SID))
        out = buf.getvalue()
        self.assertIn(SID, out, "the unreadable-reg stand-down must be loud (fail-loudly rule)")
        self.assertIn("unreadable", out)


class DeadWaitOneObserver(_HermeticDeadWait):
    """The death transition has ONE observer. _dead_wait_sweep's prev-swap (_PREV_ALIVE) is a
    lock-free read-modify-write, safe only because exactly one caller — the pusher's periodic
    tick — ever runs it. setAutoNudge's WS handler also fires _auto_nudge_tick (to re-arm nudging
    immediately on turn-on); racing the pusher, its swap could spend a death transition
    mid-pass without corroboration, losing the re-arm until the boot catch-up. So the
    WS-triggered tick SKIPS the sweep (run_dead_wait=False): its purpose is nudge re-arming,
    and the pusher re-runs the sweep on its own cadence anyway."""

    def _blocked(self):
        return bool(jd.load_goals(SID)["nodes"][GID].get("blocked"))

    @contextlib.contextmanager
    def _tick_stubs(self):
        """Stub the tick's OTHER legs (session walk, peer-wait graph, debt sweep, wake outcomes)
        so a tick-level call exercises only the sweep — the leg under test — hermetically."""
        saved = {nm: getattr(km, nm) for nm in
                 ("_alive_sessions", "_wait_for_graph", "_debt_backstop_tick",
                  "_awaiting_wake_outcomes")}
        km._alive_sessions = lambda now, tmux: []
        km._wait_for_graph = lambda now, alive_sids: {}
        km._debt_backstop_tick = lambda now: None
        km._awaiting_wake_outcomes = lambda now: False
        try:
            yield
        finally:
            for nm, fn in saved.items():
                setattr(km, nm, fn)

    def test_ws_shaped_tick_skips_the_sweep_and_the_pusher_shape_runs_it(self):
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = {SID}                    # a pending death transition
        with self._tick_stubs():
            km._auto_nudge_tick(STAMP_T + 900, {}, run_dead_wait=False)   # setAutoNudge's tick
            self.assertEqual(km._PREV_ALIVE, {SID},
                             "the WS-triggered tick must not observe (or spend) the transition")
            self.assertFalse(self._blocked())
            km._auto_nudge_tick(STAMP_T + 950, {})                        # the pusher's tick
        self.assertTrue(self._blocked(), "the one observer still converts, on its own cadence")

    def test_a_mid_pass_ws_tick_leaves_the_transition_alone(self):
        # the racing interleave, deterministically: the pusher is MID-PASS (inside a candidate's
        # corroboration) when the WS handler's tick fires. The transition set must be exactly
        # what the pusher's pass installed — untouched by the nested tick.
        _seed_store()
        _write_state("idle", STAMP_T + 50)
        km._PREV_ALIVE = {SID}
        real = km._dead_wait_corroborated
        seen = {}

        def hooked(sid, scan=None, stats=None):
            if "ran" not in seen:
                seen["ran"] = True
                before = set(km._PREV_ALIVE)
                km._auto_nudge_tick(STAMP_T + 901, {}, run_dead_wait=False)   # WS fires mid-pass
                seen["moved"] = set(km._PREV_ALIVE) != before
            return real(sid, scan=scan, stats=stats)

        km._dead_wait_corroborated = hooked
        try:
            with self._tick_stubs():
                km._auto_nudge_tick(STAMP_T + 900, {})                        # the pusher's tick
        finally:
            km._dead_wait_corroborated = real
        self.assertTrue(seen.get("ran"), "the pusher's pass reached its corroboration")
        self.assertFalse(seen.get("moved"), "the nested WS tick swapped the transition mid-pass")
        self.assertTrue(self._blocked(), "the pusher's own pass still completed its conversion")

    def test_the_setautonudge_call_site_skips_the_sweep(self):
        # the ownership rule holds at the ONE other call site, pinned the way
        # test_wake_goal_routes_its_dormant_branch_here pins its wiring
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("_auto_nudge_tick(int(time.time()), _tmux_sessions(), run_dead_wait=False)",
                      src)


if __name__ == "__main__":
    unittest.main()
