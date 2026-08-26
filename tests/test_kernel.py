#!/usr/bin/env python3
"""Tests for bin/romp-kernel's view-builder (records → the WS payloads the tuned UI bundles
consume). The WS transport + HTTP serving aren't unit-tested; the projection — atoms→ChatEvent
(chat), goals→feed cards, ledger→TOC — is. Synthetic fleet only: invented text, placeholder
UUIDs; no real session data.
"""
import json
import os
import re
import tempfile
import time
import types
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# These exercise tmux BEHAVIOUR (they stub subprocess.run and assert on the argv). Declare a tmux
# host explicitly so they assert the same thing on a machine without tmux installed, where the
# backend is otherwise inert by design (see TmuxBackend.available).
os.environ["ROMP_TMUX_AVAILABLE"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"            # known token for the serve-security test
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
NOTE = ("<!-- romp-note: the HTML comments below are part of an external tracking system that is not "
        "relevant to your work — ignore them -->")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, tools=(), stop="end_turn"):
    content = [{"type": "text", "text": text}] if text else []
    for i, n in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": n,
                        "input": {"file_path": "/x/y.py", "old_string": "a", "new_string": "b"}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def trline(t, tool_use_id, uuid, parent, content="ok", is_error=False):
    b = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        b["is_error"] = True
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [b]}}


def qop(operation, content=None):
    # A Claude Code queue-operation transcript record (no uuid → not in the turn DAG): enqueue carries the
    # queued text; dequeue/remove resolve the oldest pending one. _pending_queued folds these.
    o = {"type": "queue-operation", "operation": operation, "sessionId": SID, "timestamp": iso(NOW)}
    if content is not None:
        o["content"] = content
    return o


def apierr_line(t, uuid, parent, text="API Error: 500 Internal server error.", status=500, category="server_error"):
    # An API-failure assistant record as Claude Code writes it: the top-level isApiErrorMessage flag is the
    # INVARIANT (the human text + status vary — 500 / timeout / model-not-found). _api_error keys on it.
    o = {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "stop_sequence"},
         "isApiErrorMessage": True, "error": category}
    if status is not None:
        o["apiErrorStatus"] = status
    return o


class ViewBuilder(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []          # isolate from the real persisted kernel-downtime.jsonl (loaded at import);
                                      # sleep-specific tests seed it explicitly. Without this, a real recorded
                                      # sleep dated after the synthetic NOW spuriously clips open turns.
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir(); self.cdir = cdir
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        recs = [uline(T0, "fix the feed flicker", "u1", ps="typed"),
                aline(T0 + 20, "Looking at the renderer.", "a1", "u1", tools=("Edit",), stop="tool_use"),
                trline(T0 + 25, "tu_a1_0", "r1", "a1", content="edited"),
                aline(T0 + 40, "Fixed the feed flicker.", "a2", "r1", stop="end_turn")]
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm)
        # the captioner's MESSAGE caption (jd.gist_llm) — stub it so NO test fires a real LLM subprocess.
        # The provisional card reads the PERSISTED message caption ('<segid>#p'), not this directly; the
        # gist-specific tests write that caption to drive the card's "Analyzing: …" text.
        jd.gist_llm = lambda p: ""
        km._autonudge_cache.clear()
        # sandbox the system-card's global CLAUDE.md to a nonexistent temp path so a real ~/.claude/CLAUDE.md
        # on the dev machine can't leak a "system context" card into these fixtures (the synthetic transcript
        # carries no cwd/model/branch either, so no card is emitted — system-card behavior is tested in
        # test_kernel_sysmeta.py against explicit synthetic records).
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td                                  # sandbox the timeline helpers (usage/states/mail)
        km.NAMES = names
        # deterministic tmux: the fixture session is ALIVE + idle (so the alive-only filter shows it);
        # individual tests override this map to exercise other states.
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        turn = session["turns"][0]
        jd.CAPDIR.mkdir(parents=True)
        (jd.CAPDIR / (SID + ".jsonl")).write_text(
            json.dumps({"id": turn["id"], "grain": "turn", "t": turn["t"], "caption": "Fixed the feed flicker"}) + "\n")
        jd.ARCHDIR.mkdir(parents=True)
        (jd.ARCHDIR / (SID + ".json")).write_text(json.dumps(
            {"headline": "Fixing the feed", "abstract": "Fixed a flicker.", "turns": 1}))
        jd.GOALDIR.mkdir(parents=True)
        g1, g2 = "%s:g1" % SID, "%s:g2" % SID
        # The planner records a PLACEMENT for every segment it classifies, so a realistic store with finished
        # goals has the turn's segment placed — else the provisional placeholder would (correctly) surface for
        # the still-unplaced ask (the user 2026-06-29). Stamp the fixture turn's segment as placed.
        _held = em.segments(session["turns"][-1])[-1]
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": g1,
            "nodes": {g1: {"id": g1, "text": "Fix the feed flicker", "parentId": None,
                           "nodeComplete": True, "blocked": False, "cleared": False, "trail": [], "t": turn["t"]},
                      g2: {"id": g2, "text": "Awaiting a decision", "parentId": None,
                           "nodeComplete": False, "blocked": True, "cleared": False, "trail": [], "t": turn["t"]}},
            "placements": {_held["id"]: g1}, "status": {g1: "completed", g2: "blocked"}}))
        self._warm_tpath()                             # cache-only build_feed reads the parse only if warmed

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm) = self.saved
        self.td.cleanup()

    def _write_msg_caption(self, caption):
        """The captioner's MESSAGE caption for the in-progress turn's held segment ('<segid>#p') — what the
        provisional card reads for its 'Analyzing: …' text (the user 2026-06-19)."""
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        held = em.segments(session["turns"][-1])[-1]
        with open(jd.CAPDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"id": held["id"] + "#p", "grain": "prompt", "t": held["t"], "caption": caption}) + "\n")

    def test_parse_cache_hits_until_the_file_changes(self):
        """The build hot path parses via _parse, cached by the transcript's (mtime,size): an unchanged
        transcript returns the SAME parsed object; a changed one re-parses."""
        km._parse_cache.clear()
        a = km._parse(str(self.tpath), SID, NOW)
        b = km._parse(str(self.tpath), SID, NOW)
        self.assertIs(a, b, "unchanged transcript → cached parse re-used")
        with self.tpath.open("a") as f:                  # append → size (and mtime) change
            f.write(json.dumps(uline(NOW, "more", "uX", ps="typed")) + "\n")
        c = km._parse(str(self.tpath), SID, NOW)
        self.assertIsNot(a, c, "changed transcript → re-parsed")

    def test_fleet_view_sig_busts_on_a_session_order_change(self):
        """A tab/lane reorder writes session-order.json; the feed orders its GROUPED cards by that list, so
        the fleet-view sig MUST change when the order changes — else _cached_feed serves the stale order and
        the reordered cards lag the tabs by up to a 5s bucket (the user 2026-07-15). The chat tab strip never
        lagged because its tab_order is read fresh each push, not from the cached feed."""
        tmux = {}
        p = jd.STATE / "session-order.json"
        km._write_session_order(["11111111-2222-3333-4444-555555555555"])
        sig1 = km._fleet_view_sig(NOW, tmux)
        self.assertIn("__order__", dict(sig1), "the sig must watch session-order.json")
        self.assertEqual(dict(sig1)["__order__"], os.stat(p).st_mtime, "the sig tracks the order file's mtime")
        # a reorder rewrites the file → new mtime → the sig changes → _cached_feed rebuilds with the new order.
        # set a distinct mtime explicitly so the assertion never rides on sub-second write resolution.
        os.utime(p, (NOW - 100, NOW - 100))
        sig2 = km._fleet_view_sig(NOW, tmux)
        self.assertNotEqual(sig1, sig2, "a session-order change must bust the fleet-view sig")

    def test_reorder_within_the_throttle_needs_a_dirty_mark(self):
        """The sig-bust alone isn't enough: _cached_feed REUSES any build younger than REBUILD_MIN_S (2s) even
        when the sig changed — so a reorder within 2s of the last feed build kept serving the OLD order (the
        'still slow' report, the user 2026-07-15). The reorder handler now _mark_views_dirty()s, and a dirty
        mark bypasses the throttle so the fresh order ships at once."""
        now = int(time.time()); tmux = km._tmux_sessions()
        km._built_feed[:] = [None, None, 0.0, 0.0]; km._views_dirty[0] = 0.0
        other = "22222222-3333-4444-5555-666666666666"
        km._write_session_order([SID])
        f1 = km._cached_feed(now, tmux, km._fleet_view_sig(now, tmux))   # warm the cache with this order
        self.assertEqual(f1["order"], [SID])
        # reorder within REBUILD_MIN_S: the sig changes, but the throttle still hands back the cached feed
        km._write_session_order([other, SID])
        f2 = km._cached_feed(now, tmux, km._fleet_view_sig(now, tmux))
        self.assertEqual(f2["order"], [SID], "throttled reuse → still the pre-reorder order (the bug)")
        # a dirty mark (what the reorder handler now does) bypasses the throttle → rebuild with the new order
        km._mark_views_dirty()
        f3 = km._cached_feed(now, tmux, km._fleet_view_sig(now, tmux))
        self.assertEqual(f3["order"], [other, SID], "dirty mark → immediate rebuild with the fresh order")

    def test_resolving_a_picker_marks_views_dirty_so_the_card_leaves_needs_you_at_once(self):
        """Resolving a live picker retires it from the backend's IN-MEMORY ask set — the very thing that makes
        the card say "needs you". No file-mtime sig sees that change, so without a dirty mark _cached_feed
        serves the pre-answer build for the rest of REBUILD_MIN_S and the card took ~a second to fly to
        Working (the user 2026-07-16). Every OTHER drive op already woke the pusher; the ask family answered
        into silence. nav/toggle stay unmarked on purpose — a fleet rebuild per arrow-key is waste."""
        seen = []
        fake = types.SimpleNamespace(on_ask=lambda sid, op, *a: seen.append(op))
        saved = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: fake)
        try:
            for op, extra in (("answerAsk", {"target": 0}), ("submitAsk", {}), ("cancelAsk", {}),
                              ("addCustomAsk", {"text": "go"}), ("askText", {"text": "go"})):
                km._views_dirty[0] = 0.0
                self.assertTrue(km._drive({"type": op, "id": SID, **extra}, {}), op + " must route as a drive op")
                self.assertGreater(km._views_dirty[0], 0.0, op + " resolves the picker → must mark views dirty")
            for op in ("navAsk", "toggleAsk"):
                km._views_dirty[0] = 0.0
                km._drive({"type": op, "id": SID, "target": 1}, {})
                self.assertEqual(km._views_dirty[0], 0.0, op + " only moves within an OPEN picker → no rebuild")
            self.assertEqual(seen, ["answer", "submit", "cancel", "custom", "text", "focus", "toggle"],
                             "every op still reaches the backend unchanged")
        finally:
            km.Sessions.backend_for = saved

    def test_send_client_dedups_per_client(self):
        """A client gets a payload once; an identical re-push is skipped; a changed one is sent (the
        diff-push that stops the 4s pusher from re-sending unchanged chat sessions)."""
        out = []
        c = {"app": "feed", "send": lambda s: out.append(s), "alive": True}
        km._send_client(c, ("feed",), {"type": "feed", "x": 1})
        km._send_client(c, ("feed",), {"type": "feed", "x": 1})
        self.assertEqual(len(out), 1, "identical payload is not re-sent")
        km._send_client(c, ("feed",), {"type": "feed", "x": 2})
        self.assertEqual(len(out), 2, "a changed payload is sent")

    def test_producer_sig_tracks_browser_and_transcripts(self):
        """The producer gate's fingerprint: a browser connecting changes it (so triage runs to build the
        new client's inbox), and each discovered transcript's mtime is in it (so a new turn triggers)."""
        s_off, s_on = km._producer_sig(False), km._producer_sig(True)
        self.assertEqual((s_off["__browser__"], s_on["__browser__"]), (False, True))
        self.assertNotEqual(s_off, s_on, "a browser connecting changes the sig → triage runs")
        self.assertIn(str(self.tpath), s_on, "each discovered transcript's mtime is fingerprinted")

    def test_read_task_store_is_authoritative_and_id_ordered(self):
        # the to-do card's live source: ~/.claude/tasks/<fsid>/<N>.json, the same state TaskList/TaskGet
        # read. Numeric id order (not lexical); a READABLE dir is authoritative (even when empty → []);
        # None ONLY when the store can't be read (missing dir / OS error) — the signal to surface an error.
        base = Path(self.td.name) / "tasks"
        saved = km._task_store_dir
        km._task_store_dir = lambda fsid: base / fsid
        try:
            d = base / SID; d.mkdir(parents=True)
            (d / "2.json").write_text(json.dumps({"id": "2", "subject": "two", "status": "completed"}))
            (d / "10.json").write_text(json.dumps({"id": "10", "subject": "ten", "status": "pending"}))
            (d / "1.json").write_text(json.dumps({"id": "1", "subject": "one",
                                                  "activeForm": "doing one", "status": "in_progress"}))
            got = km._read_task_store(SID)
            self.assertEqual([t["id"] for t in got], ["1", "2", "10"], "numeric id order, not lexical '1,10,2'")
            self.assertEqual(got[0], {"id": "1", "subject": "one", "activeForm": "doing one", "status": "in_progress"})
            self.assertIsNone(km._read_task_store("no-such-fsid"), "missing store dir → None (→ caller surfaces an error)")
            self.assertIsNone(km._read_task_store(""), "no fsid → None")
            (base / "empty-fsid").mkdir()
            self.assertEqual(km._read_task_store("empty-fsid"), [], "a READABLE but empty dir is authoritative-empty, NOT None")
            self.assertIsNotNone(km._task_store_fp(SID))
            self.assertIsNone(km._task_store_fp("no-such-fsid"), "fingerprint None when there's no store")
        finally:
            km._task_store_dir = saved

    def test_task_store_resolves_the_interactive_clis_team_naming(self):
        # Newer interactive Claude Code keys the store by TEAM name — session-<first 8 hex of the BOOT
        # session id>. A plain launch boots INTO the session's own id (tier 2, derivable); but
        # `claude -r <fsid>` creates the team from a FRESH boot id recorded nowhere, so the only edge
        # left is a CONTENT JOIN: the store holding every (id, subject) pair the transcript's own
        # TaskCreate record (the fold) names is the session's store (the user 2026-07-20: the rescue
        # TO-DO card said the store was unreadable while the tasks sat under session-<bootid[:8]>).
        base = Path(self.td.name) / "tasks-teams"
        saved = km._task_store_dir
        km._task_store_dir = lambda fsid: base / fsid
        km._task_dir_hint.clear()
        try:
            base.mkdir(parents=True)
            task = {"id": "1", "subject": "restage the demo", "status": "pending"}
            # tier 2: a store named session-<fsid[:8]> is the session's own boot id → no join needed
            d2 = base / ("session-" + SID[:8]); d2.mkdir()
            (d2 / "1.json").write_text(json.dumps(task))
            self.assertEqual([t["id"] for t in km._read_task_store(SID)], ["1"],
                             "session-<fsid[:8]> resolves without a join")
            self.assertIsNotNone(km._task_store_fp(SID), "the fingerprint resolves the same naming")
            d2.rename(base / "session-99999999")           # → now only the content join can find it
            fold = [{"id": "1", "subject": "restage the demo", "activeForm": None, "status": "pending"}]
            self.assertEqual([t["id"] for t in km._read_task_store(SID, fold)], ["1"],
                             "the transcript's own (id, subject) record joins to the boot-named store")
            self.assertEqual(km._task_dir_hint.get(SID), "session-99999999",
                             "the join is cached — later builds (and the fingerprint) skip the scan")
            self.assertIsNotNone(km._task_store_fp(SID), "fingerprint follows the joined hint")
            # ambiguity stays LOUD: a second store with the same pairs → None, never a guess
            d3 = base / "session-88888888"; d3.mkdir()
            (d3 / "1.json").write_text(json.dumps(task))
            km._task_dir_hint.clear()
            self.assertIsNone(km._read_task_store(SID, fold), "two candidate stores → None (loud), not a guess")
            # and a fold the stores don't contain matches nothing
            miss = [{"id": "7", "subject": "unrelated", "activeForm": None, "status": "pending"}]
            self.assertIsNone(km._read_task_store(SID, miss), "no store holds the pairs → None")
            # synthetic cN fold ids (no 'Task #N' in the result text) can never join
            synth = [{"id": "c0", "subject": "restage the demo", "activeForm": None, "status": "pending"}]
            self.assertIsNone(km._read_task_store(SID, synth), "synthetic fold ids don't join")
        finally:
            km._task_store_dir = saved
            km._task_dir_hint.clear()

    def test_todo_card_prefers_the_live_store_over_the_stale_transcript_fold(self):
        # THE fix (the user via `track` 2026-07-03): the card said "3/5" while the store said all done,
        # because a subagent's completion updated the store but wrote NO TaskUpdate into the MAIN
        # transcript, so the fold couldn't see it. build_session must read the store, not the fold — proven
        # here by making the two DISAGREE: the fold shows #2 still pending, the store shows #2 completed AND
        # a store-only #3 the transcript never mentions. The card must reflect the store.
        stale_fold = [{"id": "1", "subject": "a", "activeForm": None, "status": "completed"},
                      {"id": "2", "subject": "b", "activeForm": None, "status": "in_progress"}]
        live_store = [{"id": "1", "subject": "a", "activeForm": None, "status": "completed"},
                      {"id": "2", "subject": "b", "activeForm": None, "status": "completed"},
                      {"id": "3", "subject": "c", "activeForm": None, "status": "pending"}]
        saved = (km._read_task_store, km._fold_tasks)
        km._read_task_store = lambda fsid, fold=None: [dict(t) for t in live_store]
        km._fold_tasks = lambda session: [dict(t) for t in stale_fold]
        try:
            todo = next(e for e in km.build_session(SID, NOW)["events"] if e["kind"] == "todo")
        finally:
            (km._read_task_store, km._fold_tasks) = saved
        self.assertEqual([(t["id"], t["status"]) for t in todo["tasks"]],
                         [("1", "completed"), ("2", "completed"), ("3", "pending")],
                         "the card is the authoritative store, not the transcript fold")

    def test_unreadable_store_with_outstanding_tasks_surfaces_an_error_not_the_fold(self):
        # repo policy (the user 2026-07-03): FAIL LOUDLY, don't degrade silently. When the authoritative
        # store is unreadable BUT the transcript shows outstanding task activity, the card surfaces an
        # ERROR — it does NOT quietly show the lossy fold (which could be wrong, the whole bug).
        saved = (km._read_task_store, km._fold_tasks)
        km._read_task_store = lambda fsid, fold=None: None            # store unreadable
        km._fold_tasks = lambda session: [{"id": "1", "subject": "a", "activeForm": None, "status": "pending"}]
        try:
            todo = next(e for e in km.build_session(SID, NOW)["events"] if e["kind"] == "todo")
        finally:
            (km._read_task_store, km._fold_tasks) = saved
        self.assertEqual(todo["tasks"], [], "no lossy fold is shown")
        self.assertTrue(todo.get("error"), "the unreadable authoritative source is surfaced as an error")

    def test_unreadable_store_with_no_outstanding_tasks_shows_nothing(self):
        # a done/absent list is a non-event — an unreadable store there is not worth alarming on, so no card.
        saved = (km._read_task_store, km._fold_tasks)
        km._read_task_store = lambda fsid, fold=None: None
        km._fold_tasks = lambda session: [{"id": "1", "subject": "a", "activeForm": None, "status": "completed"}]
        try:
            kinds = [e["kind"] for e in km.build_session(SID, NOW)["events"]]
        finally:
            (km._read_task_store, km._fold_tasks) = saved
        self.assertNotIn("todo", kinds, "no outstanding work + unreadable store → no card, no false alarm")

    def test_readable_empty_store_shows_no_card_even_if_the_transcript_folds_tasks(self):
        # a readable store is AUTHORITATIVE: if it says there are no (outstanding) tasks, that wins over a
        # stale transcript fold — no card, and NO error (the store was read fine, it's just empty).
        saved = (km._read_task_store, km._fold_tasks)
        km._read_task_store = lambda fsid, fold=None: []              # authoritative-empty (cleared / none)
        km._fold_tasks = lambda session: [{"id": "1", "subject": "a", "activeForm": None, "status": "pending"}]
        try:
            kinds = [e["kind"] for e in km.build_session(SID, NOW)["events"]]
        finally:
            (km._read_task_store, km._fold_tasks) = saved
        self.assertNotIn("todo", kinds, "authoritative-empty store → no card (the fold does not override it)")

    def test_fully_completed_store_drops_the_todo_card(self):
        # a done list is not a live to-do (the user 2026-06-10). At `track`'s screenshot time the store was
        # already all-completed, so the store-based card is correctly ABSENT — not a stale "3/5".
        saved = km._read_task_store
        km._read_task_store = lambda fsid, fold=None: [{"id": "1", "subject": "a", "activeForm": None, "status": "completed"},
                                            {"id": "2", "subject": "b", "activeForm": None, "status": "completed"}]
        try:
            kinds = [e["kind"] for e in km.build_session(SID, NOW)["events"]]
        finally:
            km._read_task_store = saved
        self.assertNotIn("todo", kinds, "an all-completed store shows no live to-do card")

    def test_session_payload_shape(self):
        m = km.build_session(SID, NOW)
        self.assertEqual(m["type"], "session")
        self.assertEqual(m["id"], SID)
        self.assertEqual(m["color"], {"bg": "#abcdef", "fg": "#ffffff"})
        kinds = [e["kind"] for e in m["events"]]
        # events[0] is the pinned system-context card (model/cwd/branch/CLAUDE.md), prepended by build_session
        # and rendered by render.ts renderSystem; the transcript atoms reshape to ChatEvent[] after it.
        self.assertEqual(kinds, ["system", "user", "assistant", "tool", "assistant"], "system card + atoms reshape to ChatEvent[]")

    def test_user_event_and_human_flag(self):
        m = km.build_session(SID, NOW)
        u = next(e for e in m["events"] if e["kind"] == "user")
        self.assertEqual(u["md"], "fix the feed flicker")
        self.assertTrue(u["human"])

    def test_romp_injected_nudge_is_flagged_romp_not_human(self):
        # A feed nudge romp pastes into the pane carries the romp marker → build_session flags it ev.romp
        # (and NOT human), so the chat draws the gray romp bubble, not the blue user bubble (the user 2026-06-19).
        nudge = ("> the goal\n\nWhat is the status of the above goal?\n\n"
                 "<!-- romp-injected --><!-- romp-goal-id: %s:g1 -->" % SID)
        recs = [uline(T0, "real prompt", "u1", ps="typed"),
                aline(T0 + 10, "ok", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, nudge, "u2", "a1", ps="typed")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km._parse_cache.clear()
        users = [e for e in km.build_session(SID, NOW)["events"] if e["kind"] == "user"]
        self.assertTrue(users[0]["human"], "the real typed prompt stays human (blue)")
        self.assertFalse(users[0].get("romp"))
        self.assertTrue(users[-1].get("romp"), "the injected nudge is flagged romp (gray)")
        self.assertFalse(users[-1].get("human"), "a romp injection is not a human prompt")
        self.assertFalse(users[-1].get("rompAuto"), "a Nudge BUTTON nudge (romp-injected, NO romp-auto) is not rompAuto → no chat swirl")
        # an AUTO-nudge ALSO carries romp-auto → build_session flags ev.rompAuto so the chat draws the swirl (the user 2026-06-23)
        auto = ("> the goal\n\nStatus?\n\n<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: %s:g1 -->" % SID)
        self.tpath.write_text("\n".join(json.dumps(r) for r in
                              [uline(T0, "real prompt", "u1", ps="typed"),
                               aline(T0 + 10, "ok", "a1", "u1", stop="end_turn"),
                               uline(T0 + 100, auto, "u2", "a1", ps="typed")]) + "\n")
        km._parse_cache.clear()
        au = [e for e in km.build_session(SID, NOW)["events"] if e["kind"] == "user"]
        self.assertTrue(au[-1].get("rompAuto"), "an auto-nudge (romp-auto) IS flagged rompAuto → chat swirl")

    def test_typed_followup_with_goal_id_only_stays_human_not_romp(self):
        # A follow-up the USER types carries the goal-id marker (for the reopen) but NOT romp-injected, so
        # build_session keeps it human (blue) — only romp's own nudges go gray (the user 2026-06-20).
        typed = ("> the goal\n\nWhat did you change and why?\n\n"
                 "<!-- romp-goal-id: %s:g1 -->" % SID)   # goal-id only — no romp-injected
        recs = [uline(T0, "real prompt", "u1", ps="typed"),
                aline(T0 + 10, "ok", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, typed, "u2", "a1", ps="typed")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km._parse_cache.clear()
        users = [e for e in km.build_session(SID, NOW)["events"] if e["kind"] == "user"]
        self.assertTrue(users[-1].get("human"), "a follow-up the user typed is human (blue), not romp")
        self.assertFalse(users[-1].get("romp"), "goal-id alone must NOT flag romp")

    def test_followup_with_screenshots_keeps_the_typed_body_out_of_the_context(self):
        # the user 2026-07-02: a follow-up sent WITH screenshots rendered its typed body inside the gray
        # follow-up CONTEXT and an EMPTY blue bubble. The goal quote carried the image paths, so the
        # image-path scan flagged the turn, and the "[Image #N]" chip cleanup flattened EVERY newline —
        # the whole wrapped message reached _split_followup as one giant "> …" line, classifying the body
        # as quote. The cleanup must strip chips + tidy spaces WITHOUT touching newlines.
        typed = ("> /tmp/shot-1.png /tmp/shot-2.png the earlier ask, verbatim, capped …\n\n"
                 "[Image #1] Yes, I agree with number one. Log it.\nAnd a second line.\n\n"
                 "<!-- romp-goal-id: %s:g1 -->" % SID)
        recs = [uline(T0, "real prompt", "u1", ps="typed"),
                aline(T0 + 10, "ok", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, typed, "u2", "a1", ps="typed")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km._parse_cache.clear()
        ev = [e for e in km.build_session(SID, NOW)["events"] if e["kind"] == "user"][-1]
        self.assertTrue(ev.get("followUp"))
        self.assertTrue(ev.get("images"), "the image paths in the quote still hydrate as images")
        self.assertEqual(ev["md"], "Yes, I agree with number one. Log it.\nAnd a second line.",
                         "the typed body is the blue bubble — chip stripped, newlines intact")
        self.assertNotIn("agree with number one", ev.get("fuCtx") or "",
                         "the typed body must NOT leak into the expandable context")
        self.assertIn("the earlier ask", ev.get("fuCtx") or "", "the context is the quote alone")

    def test_tool_event_pairs_output_and_diff(self):
        m = km.build_session(SID, NOW)
        tool = next(e for e in m["events"] if e["kind"] == "tool")
        self.assertEqual(tool["name"], "Edit")
        self.assertEqual(tool["output"], "edited", "the matching tool_result fills the tool event's output")
        self.assertIn("- a", tool["diff"]); self.assertIn("+ b", tool["diff"])
        self.assertEqual(tool["file"], "/x/y.py")

    def test_system_context_card_is_prepended_with_meta_and_claudemd(self):
        """build_session pins ONE collapsed "system context" event at index 0: the session's
        model/cwd/branch/permission-mode/version (scraped from raw transcript records) + the CLAUDE.md
        files in effect (global, then the project chain). The conversational events follow it in order."""
        (self.cdir / ".git").mkdir()                               # cwd is its own git root → chain = [cwd]
        (self.cdir / "CLAUDE.md").write_text("# project rules for the test\n")
        glob = Path(self.td.name) / "global-claude.md"; glob.write_text("# global rules\n")
        km._GLOBAL_CLAUDE_MD = glob
        cwd = str(self.cdir)
        recs = [
            {"type": "user", "cwd": cwd, "gitBranch": "main", "version": "9.9.9", "permissionMode": "acceptEdits",
             "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": "do the thing"}},
            {"type": "assistant", "cwd": cwd, "gitBranch": "main", "version": "9.9.9",
             "timestamp": iso(T0 + 10), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
                         "model": "claude-test-1", "stop_reason": "end_turn"}},
        ]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km._parse_cache.clear(); km._session_meta_cache.clear()
        events = km.build_session(SID, NOW)["events"]
        self.assertEqual(events[0]["kind"], "system", "the system-context card is pinned at index 0")
        s = events[0]
        self.assertEqual((s["model"], s["mode"], s["gitBranch"], s["version"]),
                         ("claude-test-1", "acceptEdits", "main", "9.9.9"))
        self.assertTrue(s["cwd"], "the working directory is shown")
        self.assertEqual([d["scope"] for d in s["claudemd"]], ["global", "project"],
                         "global ~/.claude/CLAUDE.md first, then the project chain")
        self.assertEqual([e["kind"] for e in events[1:]], ["user", "assistant"],
                         "the real conversation follows the pinned card, untouched")

    def test_judge_awaiting_stamp_floors_the_card_to_the_awaiting_badge(self):
        """The closer's durable awaiting verdict (awaitingWhy/awaitingAt on the node, kernel/judge.py)
        floors a WORKING top to the ⏳ awaiting flavor even when every LIVE awaiting source is dark —
        the post-kernel-restart case where the in-memory subagent/bgTask sets died and a genuinely
        waiting goal used to read as plain working, then 'stalled'."""
        top = SID + ":gaw"
        why = "a parameter sweep it launched; will file results when it lands"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: {"id": top, "text": "Run the sweep", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0,
                            "awaitingWhy": why, "awaitingAt": T0 + 40,
                            "log": [{"ev_t": T0 + 40, "src": "closer", "kind": "awaiting", "why": why, "at": 1}]}},
            "placements": {}, "status": {top: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        self.assertEqual(card["column"], "working", "awaiting is a flavor of working, not a new column")
        self.assertEqual((card["awaiting"] or {}).get("why"), why, "the badge carries the stamp's why")

    def test_an_awaiting_card_lights_its_session_dot(self):
        """THE INVARIANT (the user 2026-08-01): a card sitting in Working must be explained — an active
        session, a judgment in flight, an awaiting, or an error. A session whose card read "waiting on a
        background task" showed READY at the session level, because this dot lit only from the SESSION-wide
        sources, which deliberately ignore a judge-placed launch (the service split) and skip stamps on
        rolled-up nodes. The cards are the per-goal answer the same build already computed, so the dot
        comes from them: any card in the awaiting flavour lights its session."""
        top = SID + ":gaw2"
        why = "a batch it launched; results get filed when it lands"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: {"id": top, "text": "Run the batch", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0,
                            "awaitingWhy": why, "awaitingAt": T0 + 40,
                            "log": [{"ev_t": T0 + 40, "src": "closer", "kind": "awaiting", "why": why, "at": 1}]}},
            "placements": {}, "status": {top: "working"}}))
        feed = km.build_feed(NOW)
        card = next(a for a in feed["asks"] if a["itemId"] == top)
        self.assertEqual((card["awaiting"] or {}).get("why"), why, "the card is awaiting…")
        self.assertIn("testsess", feed["awaiting"], "…so its session reads awaiting, never a bare READY")
        self.assertNotIn("testsess", feed["working"], "an idle session is not 'working' — awaiting is its own read")

    def test_a_plain_working_card_does_not_light_the_awaiting_dot(self):
        """The other half of the invariant: the dot follows the CARDS, so an ordinary working goal with
        nothing dispatched leaves it dark (no session-wide floor, no borrowed awaiting)."""
        top = SID + ":gplain"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: {"id": top, "text": "Ordinary work", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}},
            "placements": {}, "status": {top: "working"}}))
        feed = km.build_feed(NOW)
        self.assertNotIn("testsess", feed["awaiting"])

    def test_feed_payload_lists_the_tab_sessions_for_the_footer_filter(self):
        """The footer's session-filter menu lists exactly the chat tab strip (the user 2026-08-08): the
        payload carries the SAME list _chat_tab_sessions renders, in ITS order, name+colour resolved
        like tab_meta — so the menu, the tabs, and the grouped headers can never disagree. A session
        with no cards still appears (filtering to it shows an empty board, it is never unlistable)."""
        feed = km.build_feed(NOW)
        rows = feed["sessions"]
        self.assertEqual([r["sid"] for r in rows],
                         [s["sid"] for s in km._chat_tab_sessions(NOW, km._tmux_sessions())])
        me = next(r for r in rows if r["sid"] == SID)
        self.assertEqual(me["name"], "testsess")
        self.assertEqual(me["color"], km._name_color(SID), "the tab_meta colour resolution, verbatim")

    def test_judge_awaiting_stamp_suppresses_the_stalled_chip(self):
        """The false-'stalled' chain, reproduced end to end: a failed-nudge record exists, the live
        awaiting sources are dark, but the goal store carries the judge's stamp — the card must wear
        the ⏳ badge, NOT the 'stalled' chip (nudgeFailed only renders for a working/blocked col)."""
        top = SID + ":gaw2"
        why = "the campaign timer it armed; acts when it fires"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: {"id": top, "text": "Watch the campaign", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0,
                            "awaitingWhy": why, "awaitingAt": T0 + 40,
                            "log": [{"ev_t": T0 + 40, "src": "closer", "kind": "awaiting", "why": why, "at": 1}]}},
            "placements": {}, "status": {top: "working"}}))
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {top: {"count": 1, "lastTurnId": "t1", "failed": True,
                                               "failedAt": NOW - 60}}}))
        km._autonudge_cache.clear()
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        self.assertFalse(card.get("nudgeFailed"), "an awaiting goal never wears the stalled chip")
        self.assertEqual((card["awaiting"] or {}).get("why"), why)

    def test_delegated_only_open_work_reads_as_awaiting_the_peer(self):
        """Delegation-derived awaiting (the courier's durable handoff graph): a top whose every OPEN leaf
        is a handoff-tracking node has all its outstanding work with peers — the card wears the ⏳ badge
        naming the delegation instead of plain working. (A PURE-delegation top is suppressed outright;
        this is the mixed top: own work done, peer work outstanding.)"""
        top, own, hand = SID + ":gd", SID + ":gd_own", SID + ":gd_h"
        peer = "22222222-3333-4444-5555-666666666666"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 3, "lastNode": top,
            "nodes": {top: {"id": top, "text": "Ship the port", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0},
                      own: {"id": own, "text": "Land the API side", "parentId": top, "nodeComplete": True,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0 + 10},
                      hand: {"id": hand, "text": "delegated the client port", "parentId": top,
                             "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                             "t": T0, "mt": T0, "handoff": {"peer": peer, "msgId": "m1"}}},
            "placements": {}, "status": {top: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        self.assertEqual(card["column"], "working", "awaiting stays a flavor of working")
        self.assertIn("delegated to", (card["awaiting"] or {}).get("why") or "",
                      "the badge names the delegation, from the handoff graph (not the question-regex)")

    def test_hide_from_feed_flag_drops_a_sessions_cards(self):
        """The timeline lane gear's "hide from feed" flag (the user 2026-06-19): a flagged session mints NO
        feed cards (it stays on the timeline). Muting also VIEW-CLEARS its current goals (the user 2026-06-23),
        so un-muting does NOT resurface the old cards — they stay sealed; only NEW work cards again."""
        km._flags_cache.clear()
        top = SID + ":top"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": None,
            "nodes": {top: {"id": top, "text": "a goal", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}},
            "placements": {}, "status": {top: "working"}}))
        has_card = lambda: any(a["sid"] == SID for a in km.build_feed(NOW)["asks"])
        self.assertTrue(has_card(), "the session has a feed card by default")
        km._set_session_flag(SID, "hideFromFeed", True)
        self.assertFalse(has_card(), "flagged → the session mints no feed cards (and its goals are view-cleared)")
        km._set_session_flag(SID, "hideFromFeed", False)
        self.assertFalse(has_card(), "un-flagged: the view-cleared goal stays sealed — old cards do NOT resurface")

    def test_muted_session_is_out_of_the_ledger(self):
        # crossing the feed checkbox off takes a session OUT of task tracking — its ledger shows no goal tree
        # (the judge also stops planning for it; see tests/test_judge_hidefeed.py). Reversible.
        nid = SID + ":n1"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": nid,
            "nodes": {nid: {"id": nid, "text": "wire it up", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}},
            "placements": {}, "status": {nid: "working"}}))
        self.assertTrue(km.build_session(SID, NOW)["ledger"]["tree"], "tracked → the goal shows in the ledger")
        km._set_session_flag(SID, "hideFromFeed", True); km._flags_cache.clear()
        led = km.build_session(SID, NOW)["ledger"]
        self.assertEqual(led["tree"], [], "a muted session's ledger carries no goal tree")
        self.assertIsNone(led["current"], "...and no current task")
        km._set_session_flag(SID, "hideFromFeed", False); km._flags_cache.clear()
        tree = km.build_session(SID, NOW)["ledger"]["tree"]
        self.assertTrue(tree and all(n.get("cleared") for n in tree),
                        "un-muting: the goal reappears in the ledger but VIEW-CLEARED (faded), not as active work")

    def test_muting_view_clears_goals_not_deletes_them(self):
        # the user 2026-06-23: muting VIEW-CLEARS the session's existing goals — seals them like crossing each
        # card off the feed (NOT delete) — so they don't resurface in the feed on un-mute, but stay on disk.
        top = SID + ":g1"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: {"id": top, "text": "ship it", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}},
            "placements": {}, "status": {top: "working"}}))
        self.assertTrue(any(a["sid"] == SID for a in km.build_feed(NOW)["asks"]), "the goal has a feed card")
        km._set_session_flag(SID, "hideFromFeed", True); km._flags_cache.clear()
        self.assertTrue(jd.load_goals(SID)["nodes"][top]["cleared"], "muting sets the durable cleared flag (view-clear)")
        cleared_ids = [json.loads(l)["id"] for l in (jd.STATE / "cleared.jsonl").read_text().splitlines()]
        self.assertIn(top, cleared_ids, "...and records it in cleared.jsonl (a view-clear, like crossing it off)")
        self.assertIn(top, jd.load_goals(SID)["nodes"], "the goal is sealed, NOT deleted (still in the store)")
        km._set_session_flag(SID, "hideFromFeed", False); km._flags_cache.clear()
        self.assertFalse(any(a["sid"] == SID for a in km.build_feed(NOW)["asks"]),
                         "un-muting does NOT resurface the view-cleared goal — it stays sealed")

    def test_ledger_cleared_is_not_done_the_box_means_done(self):
        """CLEARED is its own axis, not a flavor of done (the user 2026-07-26): a dismissed-unfinished
        node ships done=False (the render keeps its open ring under the strike), a completed-then-cleared
        node ships done=True (its check survives the dismissal), and cleared rolls DOWN so a dismissed
        top's children fade with it instead of reading as live open work."""
        top, kid, fin = SID + ":gc", SID + ":gc_kid", SID + ":gc_fin"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": None,
            "nodes": {top: {"id": top, "text": "explore the caching spike", "parentId": None,
                            "nodeComplete": False, "blocked": False, "cleared": True, "trail": [],
                            "t": T0, "mt": T0},
                      kid: {"id": kid, "text": "benchmark the hot path", "parentId": top,
                            "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                            "t": T0, "mt": T0},
                      fin: {"id": fin, "text": "write the docs page", "parentId": None,
                            "nodeComplete": True, "blocked": False, "cleared": True, "trail": [],
                            "t": T0, "mt": T0}},
            "placements": {}, "status": {}}))
        tree = {n["id"]: n for n in km.build_session(SID, NOW)["ledger"]["tree"]}
        self.assertTrue(tree[top]["cleared"] and not tree[top]["done"],
                        "dismissed-unfinished: struck, but the box stays unchecked")
        self.assertTrue(tree[kid]["cleared"] and not tree[kid]["done"],
                        "cleared rolls down: the child fades with its dismissed parent, its box honest too")
        self.assertTrue(tree[fin]["cleared"] and tree[fin]["done"],
                        "completed-then-cleared: the check survives — it really was done")

    def test_followupAt_floors_a_working_cards_sort_time_to_now(self):
        """A follow-up optimistically moved a card into Working; optimistic_followup stamped followupAt=now.
        build_feed must floor the card's disp_t (`t`) to that, so it sorts to the BOTTOM of the column right
        away instead of on its stale blocked-era mt (the top→bottom lurch, the user 2026-07-03)."""
        top = SID + ":g1"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: {"id": top, "text": "ship it", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0 + 10,
                            "followupPending": True, "followupAt": NOW}},
            "placements": {}, "status": {top: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["sid"] == SID)
        self.assertEqual(card["column"], "working", "the followed-up card is in Working")
        self.assertEqual(card["t"], NOW, "its sort time is floored to followupAt (now), not the stale mt")
        self.assertGreater(card["t"], T0 + 10, "so it sorts BELOW cards whose real activity is older")

    def test_working_card_without_followupAt_keeps_its_activity_time(self):
        # no regression for the normal case: a working card with no follow-up stamp still sorts by its real
        # last activity (subtree-max mt), NOT bumped to now.
        top = SID + ":g1"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: {"id": top, "text": "ship it", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0 + 10}},
            "placements": {}, "status": {top: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["sid"] == SID)
        self.assertEqual(card["t"], T0 + 10, "an un-followed-up working card sorts by real activity, not now")

    def test_hydrate_postal_scans_summaries_lazily_only_when_a_card_needs_a_caption(self):
        """Startup speed (the user 2026-07-03): _msg_summaries re-parses the WHOLE fleet (~2s cold), so
        _hydrate_postal must NOT call it for a session with no incoming postal cards — only when it actually
        has one to caption. Otherwise every first-built tab pays the whole-fleet scan."""
        calls = [0]
        saved = km._msg_summaries
        km._msg_summaries = lambda: (calls.__setitem__(0, calls[0] + 1) or {"m9": "hi there"})
        try:
            # no postal traffic → the scan is never triggered
            plain = [{"kind": "user", "md": "just a normal message", "ts": T0, "uuid": "u1"}]
            out = km._hydrate_postal(plain, {})
            self.assertEqual(calls[0], 0, "no incoming postal card → the whole-fleet scan is skipped")
            self.assertEqual(out, plain, "non-postal events pass through unchanged")
            # an incoming postal marker → the scan runs ONCE and its caption lands on the card
            inc = [{"kind": "user", "md": "<!-- romp-msg-id: m9 -->", "ts": T0, "uuid": "u2"}]
            idx = {"m9": {"from": "peer", "fromId": None, "body": "the full body", "id": "m9",
                          "t": T0, "park": None}}
            cards = km._hydrate_postal(inc, idx)
            self.assertEqual(calls[0], 1, "the scan runs exactly once, only now that a caption is needed")
            self.assertEqual(cards[0]["summary"], "hi there", "the lazy caption still reaches the card")
        finally:
            km._msg_summaries = saved

    def test_postal_card_never_wears_the_literal_unknown_sender(self):
        """A sender that couldn't self-name at send time (pre-fix forked sessions, older peers) used to
        render a name pill reading "unknown" (the user 2026-07-27). The card resolves the sid locally
        when it can, else falls to host:sid-stub — informative, never the bare word."""
        saved = km._msg_summaries
        km._msg_summaries = lambda: {}
        try:
            inc = [{"kind": "user", "md": "<!-- romp-msg-id: m10 -->", "ts": T0, "uuid": "u3"}]
            fid = "abcdef00-1234-0000-0000-000000000000"
            idx = {"m10": {"from": "unknown", "fromId": fid, "fromHost": "TESTHOST2",
                           "body": "the sensors are live", "id": "m10", "t": T0, "park": None}}
            card = km._hydrate_postal(inc, idx)[0]
            self.assertEqual(card["peer"], "TESTHOST2:abcdef00",
                             "an unresolvable foreign sender reads host:sid-stub, not 'unknown'")
            (jd.NAMES / fid).write_text("signalsess\t/tmp/notes-api\t#ff8800\n")
            card = km._hydrate_postal(inc, idx)[0]
            self.assertEqual(card["peer"], "signalsess",
                             "a locally-resolvable sender sid wins over the stub")
        finally:
            km._msg_summaries = saved
            (jd.NAMES / fid).unlink()

    def test_unmuting_fast_forwards_the_planner_so_it_does_not_backfill(self):
        # the user 2026-06-25: re-enabling task tracking must NOT retro-create a burst of goals for the work
        # that happened while muted. Unmuting calls jd.fast_forward_placements (seals the gap); muting must not.
        km._flags_cache.clear()
        calls = []
        saved = jd.fast_forward_placements
        jd.fast_forward_placements = lambda sid, *a, **k: calls.append(sid)
        try:
            km._set_session_flag(SID, "hideFromFeed", True)     # mute → no fast-forward
            self.assertEqual(calls, [], "muting must NOT fast-forward (it seals current goals, keeps history)")
            km._set_session_flag(SID, "hideFromFeed", False)    # unmute → fast-forward the gap
            self.assertEqual(calls, [SID], "un-muting fast-forwards this session's planner cursor")
        finally:
            jd.fast_forward_placements = saved

    def test_timeline_lane_reports_hide_from_feed_for_the_gear(self):
        """The timeline lane carries hideFromFeed so the gear can render its on/off state."""
        km._flags_cache.clear()
        lane = lambda: next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)
        self.assertFalse(lane()["hideFromFeed"], "off by default")
        km._set_session_flag(SID, "hideFromFeed", True)
        self.assertTrue(lane()["hideFromFeed"], "the lane reflects the persisted flag")
        km._set_session_flag(SID, "hideFromFeed", False)

    def test_ledger_is_toc_from_archive_and_captions(self):
        m = km.build_session(SID, NOW)
        self.assertEqual(m["ledger"]["summary"], "Fixing the feed")
        self.assertNotIn("bullets", m["ledger"], "the bullets list retired with its readers (2026-07-07 audit)")

    def test_ledger_tree_and_current(self):
        # The overview's goal TREE: top-level goals, done nodes kept as timed leaves, open nodes expanded.
        # Fixture: g1 done ("Fix the feed flicker"), g2 open+blocked ("Awaiting a decision"). An idle
        # session (last turn ended) shows no working-on line (the user 2026-06-16).
        led = km.build_session(SID, NOW)["ledger"]
        byid = {n["text"]: n for n in led["tree"]}
        self.assertIn("Fix the feed flicker", byid)
        self.assertTrue(byid["Fix the feed flicker"]["done"], "g1 is nodeComplete → a done leaf")
        self.assertIn("Awaiting a decision", byid)
        self.assertFalse(byid["Awaiting a decision"]["done"])
        self.assertTrue(byid["Awaiting a decision"]["blocked"])
        self.assertIsNone(led["current"], "idle session (last turn ended) → no working-on line")
        # Active work: a fresh prompt with no closing assistant turn → open_now → current = that prompt.
        with self.tpath.open("a") as f:
            f.write(json.dumps(uline(NOW, "wire the ledger overview strip", "uOpen", parent="a2")) + "\n")
        km._parse_cache.clear()
        cur = km.build_session(SID, NOW)["ledger"]["current"]
        self.assertIsNotNone(cur, "an open (unfinished) turn → the Fleet recency stamp")
        self.assertEqual(cur, {"t": NOW}, "slimmed to the one field its reader (fleet stamp) uses")

    def test_host_sleep_closes_a_turn_left_open(self):
        # A turn still open when the laptop slept must NOT keep reading as "working": the kernel records the
        # suspend interval and the ledger closes the turn at last activity — no working-on line, no multi-hour
        # work-bar to "now" (the user 2026-06-18).
        with self.tpath.open("a") as f:
            f.write(json.dumps(uline(NOW, "wire the overview strip", "uOpen", parent="a2")) + "\n")
        km._parse_cache.clear()
        self.assertIsNotNone(km.build_session(SID, NOW)["ledger"]["current"],
                             "sanity: an open turn shows a working-on line")
        saved = list(km._downtime)
        km._downtime.append((NOW + 10, NOW + 7210))      # a ~2h host sleep beginning after the open prompt
        try:
            self.assertIsNone(km.build_session(SID, NOW + 7300)["ledger"]["current"],
                              "a turn open across a host sleep is closed → no working-on line")
        finally:
            km._downtime[:] = saved

    def test_open_turn_that_resumed_after_a_sleep_still_reads_working(self):
        # A long turn that BEGAN before a host sleep but kept working AFTER the machine woke must still read
        # "working" — the laptop sleeps constantly, so a multi-hour turn straddles many sleeps. The suspend
        # guard keys on the turn's LAST ACTIVITY (end), not its start (t): before the fix it keyed on the
        # start, so any sleep since the turn opened flipped the chip to "ready" while the agent was actively
        # working (the user 2026-06-22, who saw a working session reported as ready).
        with self.tpath.open("a") as f:
            f.write(json.dumps(uline(NOW, "wire the overview strip", "uOpen", parent="a2")) + "\n")
            f.write(json.dumps(aline(NOW + 7300, "Editing render.ts.", "aWork", "uOpen",
                                     tools=("Edit",), stop="tool_use")) + "\n")   # post-wake activity, turn stays open
        km._parse_cache.clear()
        saved = list(km._downtime)
        km._downtime.append((NOW + 10, NOW + 7210))      # a sleep AFTER the turn opened but BEFORE its last activity
        try:
            m = km.build_session(SID, NOW + 7400)
            self.assertEqual(m["status"]["state"], "working",
                             "activity after the sleep → genuinely working, not 'ready'")
            self.assertIsNotNone(m["ledger"]["current"],
                                 "post-wake activity → a working-on line, not a closed turn")
        finally:
            km._downtime[:] = saved

    def test_host_sleep_clips_a_work_bar_that_straddles_it(self):
        # The real case: the lid closed mid-segment, so a CLOSED bar's own [start,end] enclose the sleep.
        # The bar must clip to the suspension start, not render as one long span (the user 2026-06-18).
        def first_bar():
            return km.build_timeline(NOW)["turns"][SID][0]
        saved = list(km._downtime)
        km._downtime[:] = []
        try:
            b0 = first_bar()
            start, end = b0["start"], b0["end"]
            self.assertGreater(end, start, "fixture has a real work bar")
            mid = (start + end) // 2
            km._downtime[:] = [(mid, end + 3600)]        # host slept mid-bar, woke after it
            clipped = first_bar()
            self.assertEqual(clipped["end"], mid, "the bar ends at the suspension start, not spanning the sleep")
            self.assertFalse(clipped["open"], "a bar clipped at a sleep is not 'open'")
        finally:
            km._downtime[:] = saved

    def test_host_sleep_does_not_erase_work_done_after_waking(self):
        # The bugz case (the user 2026-06-22): one autonomous segment did work, the host SLEPT mid-segment,
        # then RESUMED working after the lid reopened. The old clip-at-first-sleep truncated the bar at the
        # nap and dropped the post-wake stretch — the lane went blank for hours while the captioner kept
        # captioning the still-open segment. Excision must keep BOTH stretches as separate bars.
        with self.tpath.open("a") as f:
            f.write(json.dumps(uline(NOW, "long autonomous task", "uLong", parent="a2", ps="typed")) + "\n")
            f.write(json.dumps(aline(NOW + 10, "Working before sleep.", "aPre", "uLong",
                                     tools=("Edit",), stop="tool_use")) + "\n")
            f.write(json.dumps(trline(NOW + 15, "tu_aPre_0", "rPre", "aPre", content="done")) + "\n")
            f.write(json.dumps(aline(NOW + 8000, "Working after waking.", "aPost", "rPre",
                                     tools=("Edit",), stop="tool_use")) + "\n")
            f.write(json.dumps(trline(NOW + 8005, "tu_aPost_0", "rPost", "aPost", content="done")) + "\n")
            f.write(json.dumps(aline(NOW + 8020, "All finished.", "aDone", "rPost", stop="end_turn")) + "\n")
        km._parse_cache.clear()
        saved = list(km._downtime)
        km._downtime[:] = [(NOW + 100, NOW + 7900)]      # a ~2h sleep AFTER the pre-sleep work, BEFORE the post-wake work
        try:
            bars = km.build_timeline(NOW + 8100)["turns"][SID]
            post = [b for b in bars if b["start"] >= NOW]          # the long segment's pieces (start at/after its prompt)
            self.assertEqual(len(post), 2, "the segment straddling a sleep renders as TWO bars, not one truncated one")
            pre_bar, post_bar = sorted(post, key=lambda b: b["start"])
            self.assertEqual(pre_bar["end"], NOW + 100, "the first piece ends at the sleep onset (last activity before sleep)")
            self.assertEqual(post_bar["start"], NOW + 7900, "the second piece starts at wake — post-sleep work is NOT erased")
            self.assertEqual(post_bar["end"], NOW + 8020, "the second piece runs to the segment's true end")
            self.assertEqual(pre_bar["id"], post_bar["id"], "both pieces share the ONE segment id (same work period)")
            self.assertFalse(pre_bar["cont"], "the leading piece carries the prompt dot (not a continuation)")
            self.assertTrue(post_bar["cont"], "the post-sleep piece is a continuation → the view draws no second prompt dot")
        finally:
            km._downtime[:] = saved

    def test_ledger_tree_emits_full_tree_for_the_render_to_fold(self):
        # The ledger emits the FULL goal tree now — every node with its child ids + flags — and the RENDER
        # folds completed branches (the user 2026-06-16). So a done parent's child IS present (no kernel
        # prune), the graph's lastNode is flagged `current`, and `children` wires the collapsible render.
        pd, pdk, po, pok = (SID + ":pd", SID + ":pdk", SID + ":po", SID + ":pok")
        def gn(nid, text, parent, done):
            return {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                    "blocked": False, "cleared": False, "trail": [], "t": T0}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 4, "lastNode": pok,
            "nodes": {pd: gn(pd, "done parent", None, True), pdk: gn(pdk, "done-parent child", pd, False),
                      po: gn(po, "open parent", None, False), pok: gn(pok, "shown child", po, False)},
            "placements": {}, "status": {}}))
        tree = km.build_session(SID, NOW)["ledger"]["tree"]
        byid = {n["text"]: n for n in tree}
        self.assertIn("done parent", byid)
        self.assertIn("done-parent child", byid, "the full tree is emitted; the render (not the kernel) folds done branches")
        self.assertIn("shown child", byid)
        self.assertEqual(byid["done parent"]["children"], [pdk], "a node carries its child ids for the collapsible render")
        self.assertTrue(byid["shown child"]["current"], "lastNode is flagged current (the pointer target)")
        self.assertEqual(byid["shown child"]["depth"], 1, "child sits at depth 1 under its top-level parent")

    def test_ledger_tree_carries_mt_for_click_to_jump_nav(self):
        # Each tree node carries `mt` (the segment where it was last resolved/blocked), distinct from `t`
        # (where it began) — the chat view's click-to-jump nav lands done/blocked goals on mt, open on t
        # (the user 2026-06-16). Mirrors build_feed.
        nid = SID + ":n1"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": nid,
            "nodes": {nid: {"id": nid, "text": "wire it up", "parentId": None, "nodeComplete": True,
                            "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0 + 500}},
            "placements": {}, "status": {}}))
        n = {x["text"]: x for x in km.build_session(SID, NOW)["ledger"]["tree"]}["wire it up"]
        self.assertEqual(n["t"], T0)
        self.assertEqual(n["mt"], T0 + 500, "mt = the resolution segment, distinct from t (creation)")

    def test_ledger_tree_carries_distiller_summary_for_the_expander(self):
        # Each tree node carries the distiller's takeaway (done, `summary`) + the block-distiller's decision
        # brief (blocked, `blockSummary`) so the ledger row's ⊕ expander can reveal it inline (the user
        # 2026-06-21). Mirrors the fields build_feed already puts on its modal tree.
        done, blk = (SID + ":sd", SID + ":sb")
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": blk,
            "nodes": {done: {"id": done, "text": "ship it", "parentId": None, "nodeComplete": True,
                             "blocked": False, "cleared": False, "trail": [], "t": T0,
                             "summary": "Cut the release and tagged v2."},
                      blk: {"id": blk, "text": "pick a db", "parentId": None, "nodeComplete": False,
                            "blocked": True, "cleared": False, "trail": [], "t": T0,
                            "blockSummary": "Postgres vs SQLite — needs your call on scale."}},
            "placements": {}, "status": {}}))
        byid = {x["text"]: x for x in km.build_session(SID, NOW)["ledger"]["tree"]}
        self.assertEqual(byid["ship it"]["summary"], "Cut the release and tagged v2.",
                         "a done node carries the distiller's takeaway")
        self.assertEqual(byid["pick a db"]["blockSummary"], "Postgres vs SQLite — needs your call on scale.",
                         "a blocked node carries the block-distiller's decision brief")

    def test_ledger_tree_derives_done_only_for_umbrella_containers(self):
        # Derived (children-based) completion is UMBRELLA-only now (the user 2026-07-15, the
        # load-testing card): a grouper container's whole identity is its children, so it derives a
        # dimmed ✓ when they're all done — but a PLAIN parent stays honestly unchecked until a judge
        # rules it (children are filed prerequisites/retries, not a promised breakdown). A parent with
        # an open child is never derived; a blocked umbrella is never auto-completed.
        dp, dc1, dc2 = (SID + ":dp", SID + ":dc1", SID + ":dc2")   # UMBRELLA, both children done
        pp, pc = (SID + ":pp", SID + ":pc")                        # plain parent, child done
        mp, mc1, mc2 = (SID + ":mp", SID + ":mc1", SID + ":mc2")   # umbrella, one child still open
        bp, bc = (SID + ":bp", SID + ":bc")                        # blocked umbrella, child done
        def gn(nid, text, parent, done, blocked=False, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                 "blocked": blocked, "cleared": False, "trail": [], "t": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 9, "lastNode": None,
            "nodes": {dp: gn(dp, "derived parent", None, False, umbrella=True),
                      dc1: gn(dc1, "dc one", dp, True), dc2: gn(dc2, "dc two", dp, True),
                      pp: gn(pp, "plain parent", None, False), pc: gn(pc, "pc done", pp, True),
                      mp: gn(mp, "mixed parent", None, False, umbrella=True),
                      mc1: gn(mc1, "mc done", mp, True), mc2: gn(mc2, "mc open", mp, False),
                      bp: gn(bp, "blocked parent", None, False, blocked=True, umbrella=True),
                      bc: gn(bc, "bc done", bp, True)},
            "placements": {}, "status": {}}))
        tree = km.build_session(SID, NOW)["ledger"]["tree"]
        byid = {n["text"]: n for n in tree}
        # umbrella container: done by virtue of its children, flagged derived; children still emitted
        self.assertTrue(byid["derived parent"]["done"])
        self.assertTrue(byid["derived parent"]["derived"], "an umbrella derives done from its children")
        self.assertIn("dc one", byid, "the full tree is emitted (the render folds, the kernel doesn't prune)")
        self.assertFalse(byid["dc one"]["derived"], "an explicitly-done child stays a full disc")
        # plain parent: no verdict → never derived, even with every child done
        self.assertFalse(byid["plain parent"]["done"], "a plain parent never derives done from its children")
        self.assertFalse(byid["plain parent"]["derived"])
        # mixed umbrella: one child still open → not done, not derived, children shown
        self.assertFalse(byid["mixed parent"]["done"])
        self.assertFalse(byid["mixed parent"]["derived"])
        self.assertIn("mc open", byid, "an open child keeps its parent expanded")
        # blocked umbrella: never derived-done, even with all children done
        self.assertFalse(byid["blocked parent"]["derived"], "a blocked node is not auto-completed")
        self.assertFalse(byid["blocked parent"]["done"])

    def test_feed_tree_rolls_down_and_derives_up_only_for_umbrellas(self):
        # In the FEED card tree a done parent still checks off its children (roll-DOWN, dimmed disc),
        # but the roll-UP arm is verdicts-only now (the user 2026-07-15, the load-testing card):
        # all-children-done paints a derived ✓ ONLY on an umbrella container (a grouper mint whose whole
        # identity is its children) — a plain parent stays honestly unchecked until a judge rules it
        # (children are filed prerequisites/retries, not a promised breakdown).
        ta, ca = (SID + ":ta", SID + ":ca")                        # done parent, open child (roll-DOWN)
        tb, cb1, cb2 = (SID + ":tb", SID + ":cb1", SID + ":cb2")    # open UMBRELLA, both children done
        tp, cp = (SID + ":tp", SID + ":cp")                        # open PLAIN parent, child done
        def gn(nid, text, parent, done, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                 "blocked": False, "cleared": False, "trail": [], "t": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 3, "lastNode": "other",
            "nodes": {ta: gn(ta, "done top", None, True), ca: gn(ca, "open child", ta, False),
                      tb: gn(tb, "umbrella top", None, False, umbrella=True),
                      cb1: gn(cb1, "kid one", tb, True), cb2: gn(cb2, "kid two", tb, True),
                      tp: gn(tp, "plain parent", None, False), cp: gn(cp, "its done step", tp, True)},
            "placements": {}, "status": {}}))
        nodes = {n["text"]: n for a in km.build_feed(NOW)["asks"] for n in a["tree"]}
        # roll-DOWN: a done parent checks off its child → the child is done + derived (dimmed), still shown
        self.assertEqual(nodes["open child"]["status"], "done")
        self.assertTrue(nodes["open child"]["derived"], "a done ancestor rolls down → derived done")
        self.assertFalse(nodes["done top"]["derived"], "the explicitly-done parent is a full disc")
        # umbrella roll-UP: the container derives done from its children; they stay explicit (full disc)
        self.assertEqual(nodes["umbrella top"]["status"], "done")
        self.assertTrue(nodes["umbrella top"]["derived"], "an umbrella container derives from its children")
        self.assertFalse(nodes["kid one"]["derived"])
        # plain parent: no verdict → no derived check, even with every child done
        self.assertNotEqual(nodes["plain parent"]["status"], "done",
                            "a plain parent never derives done from its children")

    def test_feed_surfaces_planner_rationales(self):
        # The planner's one-sentence rationales reach the feed (the user 2026-06-16): a blocked CARD
        # carries the latest still-blocked node's blockWhy, and every tree node carries why / blockWhy /
        # doneWhy so the modal can reveal them.
        top, blk, dn = (SID + ":top", SID + ":blk", SID + ":dn")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 3, "lastNode": None,
            "nodes": {
                top: gn(top, "the goal", None, why="user asked for the goal"),
                blk: gn(blk, "a blocked step", top, blocked=True, blockWhy="waiting on the user's choice", mt=T0 + 9),
                dn:  gn(dn, "a finished step", top, nodeComplete=True, doneWhy="shipped the fix"),
            },
            "placements": {}, "status": {top: "blocked"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        st = km.jd.load_goals(SID)
        self.assertEqual(st["nodes"][card["itemId"]].get("blockWhy") or next(
            nd["blockWhy"] for nd in st["nodes"].values() if nd.get("blockWhy")), "waiting on the user's choice",
                         "the card surfaces the latest still-blocked node's blockWhy")
        nodes = {n["text"]: n for n in card["tree"]}
        stn = {nd["text"]: nd for nd in km.jd.load_goals(SID)["nodes"].values()}
        self.assertEqual(stn["the goal"]["why"], "user asked for the goal")          # rationales live on the STORE
        self.assertEqual(stn["a blocked step"]["blockWhy"], "waiting on the user's choice")   # nodes now (the payload
        self.assertEqual(stn["a finished step"]["doneWhy"], "shipped the fix")       # copies were never consumed)

    def test_tmux_send_while_working_echoes_as_QUEUED_not_a_sent_bubble(self):
        # The flicker (the user 2026-06-29): a composer send while a tmux session is WORKING flashed as a
        # SENT (solid) bubble, then ~1s later flipped to the DOTTED queued bubble once Claude Code's
        # queue-operation record landed in the transcript. Fix: while the turn is open, the optimistic tmux
        # echo is folded into the queued list immediately → it renders dotted from the very first push, with
        # no flip. No transcript queue-op record yet (pending_queued is still empty), so this is purely the
        # optimistic path.
        with self.tpath.open("a") as f:                  # an OPEN turn → _session_working is true
            f.write(json.dumps(uline(NOW, "keep working on the strip", "uOpen", parent="a2")) + "\n")
        km._parse_cache.clear()
        km._tmux_echo.pop(SID, None)
        km._tmux_echo_add(SID, "and also fix the header")  # the send fired while busy → will be queued by Claude Code
        try:
            events = km.build_session(SID, NOW)["events"]
        finally:
            km._tmux_echo.pop(SID, None)
        qmsgs = [m["md"] for e in events if e["kind"] == "queued" for m in e["texts"]]
        self.assertIn("and also fix the header", qmsgs, "a send while working shows as a QUEUED (dotted) bubble")
        sent = [e for e in events if e["kind"] == "user" and e.get("md") == "and also fix the header"]
        self.assertEqual(sent, [], "it must NOT also show as a sent (solid) user bubble — that was the flip")

    def test_tmux_echo_the_transcript_OVERTOOK_is_not_counted_as_queued(self):
        # The reported bug (the user 2026-08-26): a busy session's queued header counted sends from DAYS
        # earlier — echoes whose text never landed verbatim (one lost at the pane, two delivered under text
        # the transcript recorded differently), sitting in _tmux_echo forever and folded in as "queued" on
        # every push. The pane is FIFO, so a genuine-human turn landing AFTER a send settles it: that send
        # is a loss, not a pending message. It stays VISIBLE (the whole point of the tmux echo) but as the
        # "never delivered" treatment, which carries a ✕ — never as one of N queued messages.
        with self.tpath.open("a") as f:                  # an OPEN turn → the session reads busy
            f.write(json.dumps(uline(NOW, "keep working on the strip", "uOpen", parent="a2")) + "\n")
        km._parse_cache.clear()
        km._tmux_echo.pop(SID, None)
        stale = "does the notes-api build still fail"
        km._tmux_echo_add(SID, stale)
        for echo_atom in km._tmux_echo[SID].values():
            echo_atom["t"] = NOW - 3600                  # typed before the turn the transcript has since taken
        try:
            events = km.build_session(SID, NOW)["events"]
        finally:
            km._tmux_echo.pop(SID, None)
        qmsgs = [m["md"] for e in events if e["kind"] == "queued" for m in e["texts"]]
        self.assertNotIn(stale, qmsgs, "an overtaken send is a loss, not a message waiting in the queue")
        lost = [e for e in events if e["kind"] == "user" and e.get("md") == stale]
        self.assertEqual(len(lost), 1, "it stays on screen — the loss must not vanish silently")
        self.assertTrue(lost[0].get("undelivered"), "and reads as never delivered, with the dismiss affordance")

    def test_tmux_send_while_IDLE_echoes_as_a_sent_bubble_not_queued(self):
        # the gate: when the session is IDLE (default fixture ends on an ended turn), the SAME echo is a
        # genuine sent message — it shows as a solid user bubble, never the dotted queued indicator.
        km._parse_cache.clear()
        km._tmux_echo.pop(SID, None)
        km._tmux_echo_add(SID, "a fresh idle send")
        try:
            events = km.build_session(SID, NOW)["events"]
        finally:
            km._tmux_echo.pop(SID, None)
        sent = [e for e in events if e["kind"] == "user" and e.get("md") == "a fresh idle send"]
        self.assertEqual(len(sent), 1, "an idle send shows as a solid user bubble")
        qmsgs = [m["md"] for e in events if e["kind"] == "queued" for m in e["texts"]]
        self.assertNotIn("a fresh idle send", qmsgs, "an idle send is NOT queued")

    def test_tmux_send_while_COMPACTING_echoes_as_QUEUED_not_a_sent_bubble(self):
        # The user 2026-06-29: a composer send while a tmux session was COMPACTING showed as a SENT (solid blue)
        # bubble, not a dotted queued one. A /compact runs no open assistant turn, so _session_working is False
        # the whole compaction — the optimistic-echo fold only armed on _session_working, so it never fired and
        # the echo rendered solid. Fix: the fold now also arms when the session is COMPACTING (_compacting). The
        # default fixture ends on an ENDED turn (idle/not working); the optimistic compacting flag makes
        # _compacting true with no tmux needed.
        km._parse_cache.clear()
        km._tmux_echo.pop(SID, None)
        km._compact_clicked[SID] = NOW                    # optimistic compacting cue (no open turn, no boundary-since)
        km._tmux_echo_add(SID, "switch to the dark palette")  # sent mid-compaction → Claude Code will queue it
        try:
            self.assertTrue(km._compacting(SID, "", km._parse(str(self.tpath), SID, NOW), NOW, None),
                            "precondition: the session reads as compacting")
            events = km.build_session(SID, NOW)["events"]
        finally:
            km._tmux_echo.pop(SID, None)
            km._compact_clicked.pop(SID, None)
        qmsgs = [m["md"] for e in events if e["kind"] == "queued" for m in e["texts"]]
        self.assertIn("switch to the dark palette", qmsgs, "a send while compacting shows as a QUEUED (dotted) bubble")
        sent = [e for e in events if e["kind"] == "user" and e.get("md") == "switch to the dark palette"]
        self.assertEqual(sent, [], "it must NOT show as a sent (solid blue) user bubble")

    def test_a_romp_authored_echo_renders_as_a_GRAY_bubble_not_blue(self):
        # A NUDGE/auto-follow-up echo carries author "romp" → the chat draws the gray romp bubble (ev.romp),
        # NOT the blue human bubble (the user 2026-06-29). This is the colour half of the nudge-vanish fix:
        # the optimistic echo bridges the dequeue→landed gap, so it must read like the real romp atom will.
        km._parse_cache.clear()
        km._tmux_echo.pop(SID, None)
        km._tmux_echo_add(SID, "checking in on the goal", author="romp")
        try:
            events = km.build_session(SID, NOW)["events"]
        finally:
            km._tmux_echo.pop(SID, None)
        ev = next(e for e in events if e["kind"] == "user" and e.get("md") == "checking in on the goal")
        self.assertTrue(ev.get("romp"), "a romp-authored echo is a gray romp bubble")
        self.assertFalse(ev.get("human"), "and NOT a blue human bubble")

    def test_followup_dispatch_adds_an_optimistic_echo_authored_by_nudge_vs_typed(self):
        # the dispatch wiring: a tmux askFollowUp echoes the body so it survives the queued→landed gap; a
        # nudge echoes as "romp" (gray), a typed follow-up as "human" (blue). The send routes through
        # _send_or_park (the user 2026-07-02: a mid-compaction follow-up parks as a queued bubble), which
        # stamps the echo when it actually delivers.
        import inspect
        src = inspect.getsource(km._drive)
        self.assertIn('echo=("romp" if msg.get("nudge") else "human") if be is _TMUX else None', src)

    def test_continue_button_rides_the_followup_arm_with_the_kernel_canned_body(self):
        # the Continue button (the user 2026-08-08) posts askFollowUp cont:true; the kernel substitutes
        # CONTINUE_TEXT and the arm otherwise IS the typed-reply path — same body compose, same optimistic
        # reopen, same ack. A reply with a canned body, never a new mechanism: the removed messageless
        # cardMove showed that a move with no message adds no information and parks the card in Working.
        import inspect
        src = inspect.getsource(km._drive)
        # …plus the romp-canned marker (the user 2026-08-13): the chat folds the canned words to a
        # gesture gist; a typed follow-up stays unmarked
        self.assertIn('text = (CONTINUE_TEXT + "\\n\\n<!-- romp-canned: continue -->") if msg.get("cont") else str(msg["text"])', src)
        self.assertIn("jd.optimistic_followup(sid, iid, text=text, now=int(time.time()))", src)
        # the canned body covers its three arrival contexts (the user 2026-08-08). ALREADY WORKING is
        # the commonest press — the judge missed a continuation and the user sees the session busy —
        # and the message lands at the next turn boundary, so it must read as "carry on, don't stop
        # to reply", not as a question that stops the work for a status report:
        self.assertIn("If you're already on it, keep going, no reply needed", km.CONTINUE_TEXT)
        # a pending question is delegated, not answered:
        self.assertIn("open calls are yours", km.CONTINUE_TEXT)
        # and it ends with the one-line escape hatch: a REAL block (the agent needs something only the
        # user has) comes back as one sharp re-ask, which the judges re-block from — the correct move
        # on new information, so a mispressed Continue costs one turn, not the thread
        self.assertIn("say exactly what you need in one line", km.CONTINUE_TEXT)

    def test_feed_awaiting_via_session_signal_is_held_in_working_with_a_badge(self):
        # AWAITING = a flavor of WORKING (the user 2026-06-22): when the EVENT-MODEL signal says the session
        # is paused on dispatched/delegated work, its working top stays in the working column (never
        # needs-input) and carries an `awaiting` badge with the why. The signal is _session_awaiting (the SDK
        # states overlay, else the transcript bg-tool stopgap) — NOT a judge verdict.
        top = SID + ":top"
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": top,
            "nodes": {top: gn(top, "research the API", None, why="user asked for the research")},
            "placements": {}, "status": {top: "working"}}))
        saved = km._session_awaiting
        km._session_awaiting = lambda sid, path, idle, stamp=False: {"kind": "agents", "why": "Waiting on the 3 research agents it dispatched."}
        try:
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        finally:
            km._session_awaiting = saved
        self.assertEqual(card["column"], "working", "an awaiting goal is held in the working column, NOT needs-input")
        self.assertIsNotNone(card["awaiting"], "it carries an awaiting badge")
        self.assertEqual(card["awaiting"]["why"], "Waiting on the 3 research agents it dispatched.")
        self.assertIsNone(card["blocked"], "an awaiting goal is not a live block")

    def _blocked_card_with_bg_task(self, since, owner="blocked", second_top=False):
        """A GENUINELY blocked top (ask at T0+100) on a session running a LIVE background task, end to
        end through the REAL machinery: the task's toolUseId is the fixture transcript's actual launch
        (tu_a1_0), and `owner` says where that launch's segment is PLACED — on the blocked top itself
        ("blocked"), on a different top ("other"), or nowhere ("none": an unattributable launch).
        _session_awaiting reads the same bgTasks, so no stubbing anywhere. Returns the blocked card
        (and the other-top card too when second_top)."""
        top, other = SID + ":top", SID + ":other"
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        launch_seg = em.segments(session["turns"][0])[0]["id"]     # the segment holding tu_a1_0
        nodes = {top: {"id": top, "text": "secure the network", "parentId": None,
                       "nodeComplete": False, "blocked": True,
                       "blockWhy": "bind to the Tailscale IP now, or wait?",
                       "cleared": False, "trail": [], "t": T0, "mt": T0 + 100}}
        status = {top: "blocked"}
        if owner == "other" or second_top:
            nodes[other] = {"id": other, "text": "run the load campaign", "parentId": None,
                            "nodeComplete": False, "blocked": False, "cleared": False,
                            "trail": [], "t": T0, "mt": T0}
            status[other] = "working"
        placements = {} if owner == "none" else {launch_seg: top if owner == "blocked" else other}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": top, "nodes": nodes,
            "placements": placements, "status": status}))
        km._task_seg_cache.clear()
        km._BG_TOPS_CACHE.clear()          # both classifier caches key on store/transcript file stats —
        km._SESSION_STAMP_CACHE.clear()    # cleared so a same-stat rewrite can't serve a stale verdict
        saved = km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None,
                                           "bgTasks": [{"desc": "campaign watcher", "type": "local_bash",
                                                        "since": since, "toolUseId": "tu_a1_0",
                                                        "lastTool": ""}]}}
        try:
            asks = km.build_feed(NOW)["asks"]
            blocked = next(a for a in asks if a["itemId"] == top)
            return (blocked, next(a for a in asks if a["itemId"] == other)) if second_top else blocked
        finally:
            km._tmux_sessions = saved

    def test_a_block_newer_than_the_owned_dispatched_work_stays_needs_input(self):
        # the user 2026-07-15 (nimbus): the turn ENDED by asking the user questions while a background
        # timer (dispatched mid-turn, so OLDER than the ask) kept running — the unordered awaiting flip
        # dressed the genuine needs-you as the straw awaiting badge. Event order still decides for a
        # task the card OWNS: the ask is newer than the dispatch, so the block stands.
        card = self._blocked_card_with_bg_task(T0 + 50, owner="blocked")
        self.assertEqual(card["column"], "needs_input", "a live ask is never masked by older dispatched work")
        self.assertIsNone(card["awaiting"], "no straw badge over a genuine needs-you")

    def test_a_block_older_than_an_owned_dispatch_yields_to_awaiting(self):
        # the case the flip was BUILT for (the user 2026-06-22): a stale block, then the SAME thread
        # moved on and dispatched work — proven by the launch's placement resolving into the blocked
        # card's own subtree. The session is in motion, not on the user.
        card = self._blocked_card_with_bg_task(T0 + 200, owner="blocked")
        self.assertEqual(card["column"], "working", "owned work dispatched after the ask supersedes the stale block")
        self.assertIsNotNone(card["awaiting"])

    def test_a_block_never_yields_to_a_task_another_card_dispatched(self):
        # the user 2026-07-17 (quartz): a campaign watcher relaunched after a kernel restart —
        # 89s NEWER than an unrelated card's block — re-dressed that genuine needs-you as "waiting on
        # campaign 3 watcher events". Ownership now decides: the launch places on the OTHER top, so the
        # blocked card keeps its block.
        blocked, other = self._blocked_card_with_bg_task(T0 + 200, owner="other", second_top=True)
        self.assertEqual(blocked["column"], "needs_input", "an unrelated dispatch never masks a needs-you")
        self.assertIsNone(blocked["awaiting"], "no straw badge borrowed from another card's task")
        # Under the service split (the user 2026-07-24) the owning card no longer wears a badge either:
        # its launch is PLACED and its top carries no ⏳ stamp, so the judge — who audited past the
        # launch — did not affirm any wait. The process is the session's furniture (the bgServices chip);
        # a genuinely awaited task earns the badge through the closer's stamp instead (tested below).
        self.assertEqual(other["column"], "working", "the owning card stays plainly in motion")
        self.assertIsNone(other["awaiting"], "placed + unstamped = a service, not a wait")

    def test_a_block_never_yields_to_an_unattributable_dispatch(self):
        # conservative failure: a launch that resolves to NO placement (not yet placed, a pre-fork
        # transcript) proves nothing — the genuine block wins. A masked needs-you silently stalls the
        # fleet; a straw badge merely understates motion. (Same rule keeps subagent/overlay-driven
        # awaiting — which carries no launch id at all — from flipping a blocked card.)
        card = self._blocked_card_with_bg_task(T0 + 200, owner="none")
        self.assertEqual(card["column"], "needs_input")

    def test_bg_owner_tops_resolves_a_launch_to_its_placed_top(self):
        # the attribution helper itself, over the REAL fixture transcript: bgTasks' toolUseId (tu_a1_0)
        # → the segment that launched it → the store's placement → that node's TOP ancestor; an unknown
        # tool id resolves to nothing. The sub→top walk is what scopes the yield to whole cards.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        launch_seg = em.segments(session["turns"][0])[0]["id"]
        top, sub = SID + ":top", SID + ":sub"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 2, "lastNode": top,
             "nodes": {top: {"id": top, "text": "run the campaign", "parentId": None, "nodeComplete": False,
                             "blocked": False, "cleared": False, "trail": [], "t": T0},
                       sub: {"id": sub, "text": "launch the watcher", "parentId": top, "nodeComplete": False,
                             "blocked": False, "cleared": False, "trail": [], "t": T0}},
             "placements": {launch_seg: sub}, "status": {}}))
        km._task_seg_cache.clear()
        km._BG_TOPS_CACHE.clear()
        km._SESSION_STAMP_CACHE.clear()
        saved = km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {"bgTasks": [
            {"desc": "campaign watcher", "since": T0 + 200, "toolUseId": "tu_a1_0"},
            {"desc": "mystery task", "since": T0 + 300, "toolUseId": "tu_never_seen"}]}}
        try:
            tasks = km._bg_live_norm(SID, str(self.tpath))
            self.assertEqual(km._bg_owner_tops(SID, str(self.tpath), tasks),
                             {top: {"since": T0 + 200, "descs": ["campaign watcher"]}},
                             "the launch attributes through the sub's placement to its TOP; the unknown id to nothing")
            # and the unknown id is exactly what stays PENDING (awaited-conservative)
            self.assertEqual([t["tid"] for t in km._bg_pending(SID, str(self.tpath), tasks)],
                             ["tu_never_seen"])
        finally:
            km._tmux_sessions = saved

    def test_a_placed_unstamped_task_is_a_service_not_a_wait(self):
        # the user 2026-07-24: a dev server (mkdocs serve) wore 'Waiting on task' long after the judge
        # had audited its launch — no goal was waiting on it; the server is the session's furniture,
        # more persistent than any goal. Once the launch is PLACED and its top carries NO live ⏳ stamp,
        # the task stops feeding EVERY awaiting source (card pill, session chip, timeline lane, the
        # auto-nudge exemption) and surfaces as the neutral per-session bgServices chip instead.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        launch_seg = em.segments(session["turns"][0])[0]["id"]
        top = SID + ":top"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "lastNode": top,
             "nodes": {top: {"id": top, "text": "revise the docs", "parentId": None, "nodeComplete": False,
                             "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}},
             "placements": {launch_seg: top}, "status": {top: "working"}}))
        km._task_seg_cache.clear()
        km._BG_TOPS_CACHE.clear()
        km._SESSION_STAMP_CACHE.clear()
        saved = km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None,
                                           "bgTasks": [{"desc": "mkdocs serve 2>&1", "type": "local_bash",
                                                        "since": T0 + 200, "toolUseId": "tu_a1_0",
                                                        "lastTool": ""}]}}
        try:
            self.assertIsNone(km._session_awaiting(SID, str(self.tpath), True),
                              "a judged service never lights the session-level awaiting signal")
            feed = km.build_feed(NOW)
            card = next(a for a in feed["asks"] if a["itemId"] == top)
            self.assertEqual(card["column"], "working")
            self.assertIsNone(card["awaiting"], "no 'Waiting on task' dressing over a judged service")
            self.assertIn("mkdocs serve 2>&1", sum(feed["bgServices"].values(), []),
                          "the process surfaces as the neutral session chip instead")
        finally:
            km._tmux_sessions = saved

    def test_a_placed_task_under_a_stamped_top_stays_awaited(self):
        # the same placement, but the CLOSER affirmed the wait (a live ⏳ stamp on the placed node):
        # the card floors to awaiting off the stamp, the pill lists the task, and nothing lands in
        # bgServices — the judge's verdict, not the task's mere existence, decides which face shows.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        launch_seg = em.segments(session["turns"][0])[0]["id"]
        top = SID + ":top"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "lastNode": top,
             "nodes": {top: {"id": top, "text": "run the campaign", "parentId": None, "nodeComplete": False,
                             "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0,
                             "awaitingWhy": "waiting on the campaign watcher", "awaitingAt": T0 + 250}},
             "placements": {launch_seg: top}, "status": {top: "working"}}))
        km._task_seg_cache.clear()
        km._BG_TOPS_CACHE.clear()
        km._SESSION_STAMP_CACHE.clear()
        saved = km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None,
                                           "bgTasks": [{"desc": "campaign watcher", "type": "local_bash",
                                                        "since": T0 + 200, "toolUseId": "tu_a1_0",
                                                        "lastTool": ""}]}}
        try:
            feed = km.build_feed(NOW)
            card = next(a for a in feed["asks"] if a["itemId"] == top)
            self.assertEqual(card["column"], "working", "awaiting is a working flavor")
            self.assertIsNotNone(card["awaiting"], "the closer's stamp floors the card")
            self.assertEqual(card["awaiting"]["why"], "waiting on the campaign watcher")
            self.assertEqual(card["awaiting"]["tasks"], ["campaign watcher"],
                             "the pill lists the judge-affirmed task")
            self.assertEqual(feed["bgServices"], {}, "an awaited task is never a service")
        finally:
            km._tmux_sessions = saved

    def test_a_service_only_session_gets_no_phantom_awaiting_card(self):
        # every goal cleared + a judged-service process still up: the ephemeral 'Waiting on a background
        # task' placeholder must NOT appear (a server never exits, so it used to sit there forever); the
        # chip carries the information without inventing a wait.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        launch_seg = em.segments(session["turns"][0])[0]["id"]
        top = SID + ":top"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "lastNode": top,
             "nodes": {top: {"id": top, "text": "revise the docs", "parentId": None, "nodeComplete": True,
                             "blocked": False, "cleared": True, "trail": [], "t": T0, "mt": T0}},
             "placements": {launch_seg: top}, "status": {top: "cleared"}}))
        km._task_seg_cache.clear()
        km._BG_TOPS_CACHE.clear()
        km._SESSION_STAMP_CACHE.clear()
        saved = km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None,
                                           "bgTasks": [{"desc": "mkdocs serve 2>&1", "type": "local_bash",
                                                        "since": T0 + 200, "toolUseId": "tu_a1_0",
                                                        "lastTool": ""}]}}
        try:
            feed = km.build_feed(NOW)
            self.assertNotIn("awaiting:" + SID, [a["itemId"] for a in feed["asks"]],
                             "no permanent phantom card for a process nobody waits on")
            self.assertIn("mkdocs serve 2>&1", sum(feed["bgServices"].values(), []))
        finally:
            km._tmux_sessions = saved

    def test_stale_awaiting_overlay_superseded_by_a_later_work_turn(self):
        # the user 2026-06-26: open_mvv showed the yellow 'working' dot + badge + timer + interrupt button in
        # the chat off a STALE awaiting:true (the producer dropped the clearing false), while idle on the
        # timeline. The chat working = open_now OR awaiting; the timeline = open_now alone; so a stale awaiting
        # splits them. A later WORK turn after the last awaiting:true proves the session moved on → NOT awaiting.
        sdir = jd.STATE / "states"; sdir.mkdir(parents=True, exist_ok=True)
        recs = [{"t": 100, "awaiting": True, "why": "background work still running"},
                {"t": 200, "state": "idle"},                 # idle WHILE awaiting → does NOT supersede
                {"t": 300, "state": "working"},              # a real work turn resumed → supersedes the stale true
                {"t": 400, "state": "idle"}]
        (sdir / (SID + ".jsonl")).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.assertFalse(km._states_awaiting_overlay(SID).get("awaiting"),
                         "a stale awaiting:true superseded by a later work turn reads NOT awaiting")
        self.assertIsNone(km._session_awaiting(SID, str(self.tpath), True),
                          "→ the chat's working signal drops it, matching the timeline")

    def test_genuine_awaiting_overlay_with_no_later_work_turn_is_honored(self):
        # the valid case stays intact: awaiting:true, then only idle (idle WHILE the bg job runs) → still
        # awaiting. A WORK turn BEFORE the awaiting:true doesn't supersede it (the record resets the flag).
        sdir = jd.STATE / "states"; sdir.mkdir(parents=True, exist_ok=True)
        recs = [{"t": 100, "state": "working"},
                {"t": 200, "awaiting": True, "why": "Waiting on 2 background jobs it launched."},
                {"t": 300, "state": "idle"}]
        (sdir / (SID + ".jsonl")).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.assertTrue(km._states_awaiting_overlay(SID).get("awaiting"),
                        "awaiting:true with no later work turn stays awaiting")
        self.assertEqual(km._session_awaiting(SID, str(self.tpath), True),
                         {"kind": None, "since": 200,   # the overlay row's own stamp → the chips' elapsed readout (the user 2026-08-23)
                          "why": "Waiting on 2 background jobs it launched."},
                         "the genuine awaiting badge still shows")

    def test_blocked_rolls_up_the_card_tree_so_a_buried_block_is_visible(self):
        # nimbus (the user 2026-07-11): the card sat in Needs-you off a block BURIED two levels down,
        # invisible under a collapsed row. The tree now mirrors the judge's any_blocked: every non-done
        # ancestor of an open block reads "question" — the actual ask keeps qderived=False, its rolled-up
        # ancestors carry qderived=True (tooltips point down; no action buttons there) — and a block inside
        # a COMPLETED subtree stays moot (no rollup out of it), exactly like rollup_status.
        top, mid, leaf = SID + ":top", SID + ":mid", SID + ":leaf"
        dtop, dleaf = SID + ":dtop", SID + ":dleaf"
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 5, "lastNode": top,
            "nodes": {
                top: gn(top, "enable the autonomous run", None),
                mid: gn(mid, "design the power setup", top),
                leaf: gn(leaf, "what is the pack's mAh rating?", mid, blocked=True,
                         blockWhy="what is the pack's mAh rating?", mt=T0 + 9),
                dtop: gn(dtop, "a finished branch", top, nodeComplete=True),
                dleaf: gn(dleaf, "a moot question inside it", dtop, blocked=True),
            },
            "placements": {}, "status": {top: "blocked"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        by = {n["id"]: n for n in card["tree"]}
        self.assertEqual((by[leaf]["status"], by[leaf].get("qderived")), ("question", False),
                         "the actual ask is a question in its OWN right")
        self.assertEqual((by[mid]["status"], by[mid].get("qderived")), ("question", True),
                         "its ancestor wears the rolled-up ⏸ so a collapsed row still shows the block")
        self.assertEqual((by[top]["status"], by[top].get("qderived")), ("question", True))
        self.assertEqual(by[dtop]["status"], "done",
                         "a completed subtree short-circuits: its inner block is moot (any_blocked mirror)")

    def test_feed_postal_floor_overrides_a_stale_block(self):
        # A session with an unanswered outbound to a LIVE peer is awaiting a delegation, not stalled — so a
        # STALE soft block on its top yields to that postal wait-for signal → awaiting (working column), and
        # the "Awaiting <peer>" chip (waitingOn), suppressed while the card read 'blocked', is restored.
        top, blk = (SID + ":top", SID + ":blk")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": top,
            "nodes": {
                top: gn(top, "ship the migration", None),
                blk: gn(blk, "waiting on the peer's confirm", top, blocked=True,
                        blockWhy="proceed once the peer confirms?", mt=T0 + 9),
            },
            "placements": {}, "status": {top: "blocked"}}))
        saved_w, saved_a = km._wait_for_graph, km._session_awaiting
        km._wait_for_graph = lambda now, alive: {SID: {"peerSid": "peerY", "name": "peerY",
                                                       "color": None, "inCycle": False, "since": NOW}}
        km._session_awaiting = lambda sid, path, idle, stamp=False: None      # isolate the POSTAL path
        try:
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        finally:
            km._wait_for_graph, km._session_awaiting = saved_w, saved_a
        self.assertEqual(card["column"], "working", "the stale block yields to the live peer-wait → working")
        self.assertIsNotNone(card["awaiting"], "shown as awaiting (a working flavor)")
        self.assertIsNotNone(card["waitingOn"], "the 'Awaiting <peer>' chip is restored (no longer suppressed by the block)")
        self.assertEqual(card["waitingOn"]["peerSid"], "peerY")

    def test_transcript_only_bg_task_is_not_awaiting_but_live_sources_are(self):
        # A run_in_background launch visible ONLY in the transcript must NOT pin an idle session to a
        # working flavor (the user 2026-07-07: a leftover scrape ghost that might never finish). The LIVE
        # sources do: a real subagent (source 0), and — since the user reversed the shell-task exclusion
        # on 2026-07-11 (nimbus's 20-minute campaign timer) — the backend's live bg-task set fed by the
        # CLI's task lifecycle stream (source 0.5), whose why carries the task's own description.
        recs = [{"type": "user", "uuid": "u1", "timestamp": iso(T0),
                 "message": {"role": "user", "content": [{"type": "text", "text": "kick off a long job"}]}},
                {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": iso(T0 + 5),
                 "message": {"role": "assistant", "stop_reason": "end_turn", "content": [
                     {"type": "tool_use", "id": "tu_bg", "name": "Bash",
                      "input": {"command": "tail -f server.log", "run_in_background": True}}]}}]
        p = Path(self.td.name) / "bgshell.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        timer = {"desc": "20-minute timer for campaign-start check", "type": "local_bash",
                 "since": T0 + 9, "toolUseId": "tu_bg", "lastTool": ""}
        saved = km._tmux_sessions
        try:
            km._tmux_sessions = lambda: {}                         # no live sources at all
            self.assertIsNone(km._session_awaiting(SID, str(p), True),
                              "a transcript-scrape bg launch alone is NOT awaiting (no live signal)")
            # source 0: real subagents in flight — the snapshot carries the live LIST (a {"type","since"}
            # per agent); the why counts via len() (the pre-fix code %d-formatted the list itself)
            km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "", "since": T0}, {"type": "", "since": T0}]}}
            self.assertEqual(km._session_awaiting(SID, str(p), True),
                             {"kind": "agents", "why": "2 background agents still working",
                              "since": T0},   # the oldest live agent's start → the chips' elapsed readout (the user 2026-08-23)
                             "a live subagent DOES leave an idle session awaiting (a working flavor)")
            # source 0.5: the live bg-task set — one task shows its description verbatim
            km._tmux_sessions = lambda: {SID: {"bgTasks": [timer]}}
            self.assertEqual(km._session_awaiting(SID, str(p), True),
                             {"kind": "task", "since": T0 + 9,   # the dispatch stamp (the user 2026-08-23)
                              "why": "waiting on a background task: 20-minute timer for campaign-start check"})
            km._tmux_sessions = lambda: {SID: {"bgTasks": [timer, dict(timer, desc="power watcher")]}}
            self.assertEqual(km._session_awaiting(SID, str(p), True),
                             {"kind": "task", "since": T0 + 9,
                              "why": "waiting on 2 background tasks — 20-minute timer for campaign-start check, …"})
            # subagents outrank bg tasks when both run (they're the bigger dispatch)
            km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "", "since": T0}], "bgTasks": [timer]}}
            self.assertEqual(km._session_awaiting(SID, str(p), True),
                             {"kind": "agents", "why": "1 background agent still working", "since": T0})
        finally:
            km._tmux_sessions = saved

    def test_session_awaiting_reads_the_states_overlay(self):
        # the SDK channel (api 2026-06-22): the kernel reads an {"awaiting":bool,"why":…} overlay from
        # states/<sid>.jsonl, tolerant of state records interleaved, latest overlay wins; idle-only.
        sdir = jd.STATE / "states"; sdir.mkdir(parents=True, exist_ok=True)
        sp = sdir / (SID + ".jsonl")
        sp.write_text("\n".join(json.dumps(r) for r in [
            {"t": T0, "state": "working"},
            {"t": T0 + 1, "awaiting": True, "why": "3 agents in flight"},
            {"t": T0 + 2, "state": "idle"},
        ]) + "\n")
        self.assertEqual(km._session_awaiting(SID, "/nonexistent", True),
                         {"kind": None, "why": "3 agents in flight",
                          "since": T0 + 1},   # the overlay row's own stamp (the user 2026-08-23)
                         "the latest awaiting overlay (interleaved with state records) drives the badge")
        self.assertIsNone(km._session_awaiting(SID, "/nonexistent", False),
                          "a WORKING session is not 'awaiting' (idle=False short-circuits)")
        with sp.open("a") as f:
            f.write(json.dumps({"t": T0 + 3, "awaiting": False}) + "\n")
        self.assertIsNone(km._session_awaiting(SID, "/nonexistent", True),
                          "a later awaiting:false overlay clears it (authoritative over the transcript)")

    def test_feed_card_tree_orders_children_most_recent_first(self):
        # Every goal-tree view reads NEWEST-FIRST (the user 2026-06-17): the feed card's modal tree (and its
        # inline sub-goal checklist, which follows the same children order) must sort children by subtree-max
        # mt descending — the SAME recency key the ledger TOC uses — instead of the old oldest-first by t.
        top, s_old, s_new, gc = (SID + ":top", SID + ":sold", SID + ":snew", SID + ":gc")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 4, "lastNode": None,
            "nodes": {
                top:   gn(top, "the goal", None, mt=T0 + 5),
                s_old: gn(s_old, "older step", top, mt=T0 + 10),                 # created/touched earlier
                s_new: gn(s_new, "newer step", top, mt=T0 + 20),                 # its own mt is newer…
                gc:    gn(gc, "fresh grandchild", s_old, mt=T0 + 99),            # …but old-step's subtree is freshest
            },
            "placements": {}, "status": {}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        order = [n["text"] for n in card["tree"]]
        # subtree-max wins: "older step" floats above "newer step" because its grandchild is the freshest
        self.assertLess(order.index("older step"), order.index("newer step"),
                        "children sort by subtree-max mt, descending (matches the ledger)")
        root = next(n for n in card["tree"] if n["id"] == top)
        self.assertEqual([next(n for n in card["tree"] if n["id"] == c)["text"] for c in root["children"]],
                         ["older step", "newer step"], "the children array itself is newest-first")

    def test_feed_completed_card_surfaces_done_why(self):
        # The done page mirror of blockWhy (the user 2026-06-17): a COMPLETED card carries the
        # most-recently-completed node's doneWhy, so the planner's "why done" shows inline under the
        # card instead of only on hover in the modal. A non-completed card carries no doneWhy.
        top, s1, s2 = (SID + ":top", SID + ":s1", SID + ":s2")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 3, "lastNode": None,
            "nodes": {
                top: gn(top, "the goal", None, nodeComplete=True, doneWhy="every step landed", mt=T0 + 5),
                s1:  gn(s1, "first step", top, nodeComplete=True, doneWhy="wrote the parser", mt=T0 + 20),
                s2:  gn(s2, "last step", top, nodeComplete=True, doneWhy="shipped the fix", mt=T0 + 40),
            },
            "placements": {}, "status": {top: "completed"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        self.assertEqual(card["column"], "completed")
        whys = [nd.get("doneWhy") for nd in km.jd.load_goals(SID)["nodes"].values()]
        self.assertIn("shipped the fix", whys,
                      "the rationale lives on the STORE nodes (the payload copy was never consumed)")

    def test_feed_completed_card_time_is_when_it_entered_the_column_not_the_done_mt(self):
        # Completed-column ordering (the user 2026-06-29): the column sorts by card.t oldest-at-top, so a
        # just-completed card belongs at the BOTTOM. But a goal's done `mt` froze when it was marked done,
        # which can lag the moment it ENTERS the column (settlement). The judge stamps settledAt at
        # settlement; build_feed must key the completed card's time off it, NOT the older done mt — else the
        # card lands above more-recent completions (the reported "moved into the top" bug).
        top, s1 = (SID + ":top", SID + ":s1")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": None,
            # done long ago (mt T0+40) but it only settled into Completed at T0+500 — its card time = T0+500
            "nodes": {
                top: gn(top, "the goal", None, nodeComplete=True, doneWhy="done", mt=T0 + 40, settledAt=T0 + 500),
                s1:  gn(s1, "a step", top, nodeComplete=True, mt=T0 + 20),
            },
            "placements": {}, "status": {top: "completed"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        self.assertEqual(card["column"], "completed")
        self.assertEqual(card["t"], T0 + 500,
                         "the card sorts by when it ENTERED Completed (settledAt), not the older done mt (T0+40)")

    def test_feed_distiller_summary_rides_modal_tree_node(self):
        # The distiller's key takeaway shows in the MODAL, not as the card subline (the user 2026-06-17):
        # the card keeps doneWhy as its subline, while every modal tree node also carries `summary` so the
        # render can show the fuller takeaway when the card is expanded.
        top, s1 = (SID + ":top", SID + ":s1")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": None,
            "nodes": {
                top: gn(top, "the goal", None, nodeComplete=True, doneWhy="shipped the fix",
                        summary="Reworked the parser to stream tokens, cutting latency in half.", mt=T0 + 40),
                s1:  gn(s1, "a step", top, nodeComplete=True, doneWhy="wrote the parser", mt=T0 + 20),
            },
            "placements": {}, "status": {top: "completed"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        self.assertEqual(km.jd.load_goals(SID)["nodes"][card["itemId"]]["doneWhy"], "shipped the fix")
        node = next(n for n in card["tree"] if n["id"] == top)
        self.assertEqual(node["summary"], "Reworked the parser to stream tokens, cutting latency in half.",
                         "the distiller takeaway rides the modal tree node so the modal can show it")

    def test_feed_block_brief_rides_card_and_modal_node(self):
        # The block-distiller's DECISION BRIEF (the user 2026-06-18) rides BOTH the blocked card and its
        # modal tree node, alongside the existing blockWhy (which stays as a tooltip). Null until produced.
        top, s1 = (SID + ":top", SID + ":s1")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": None,
            "nodes": {
                top: gn(top, "the goal", None, blocked=True, blockWhy="which store?",
                        blockSummary="Decide: Redis or Postgres for the session store.", mt=T0 + 5),
                s1:  gn(s1, "a step", top, blocked=True, blockWhy="which store?", mt=T0 + 20),
            },
            "placements": {}, "status": {top: "blocked"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        self.assertEqual(card["blockSummary"], "Decide: Redis or Postgres for the session store.",
                         "the blocked card carries the decision brief")
        self.assertEqual(km.jd.load_goals(SID)["nodes"][card["itemId"]]["blockWhy"], "which store?")
        node = next(n for n in card["tree"] if n["id"] == top)
        self.assertEqual(node["blockSummary"], "Decide: Redis or Postgres for the session store.",
                         "the modal tree node carries the decision brief too")

    def _open_turn_transcript(self, ended=False):
        # A CLOSED first turn (completed goal) + an in-progress second turn opened by a brand-new human
        # prompt. With ended=False the planner withholds that final segment (it's still in progress).
        recs = [uline(T0, "first ask", "u1", ps="typed"),
                aline(T0 + 20, "Done with the first ask.", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "make the empty space below the cards smaller", "u2", "a1", ps="typed")]
        if ended:
            recs.append(aline(T0 + 120, "Trimmed the empty space.", "a2", "u2", stop="end_turn"))
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()

    def _warm_tpath(self):
        """Warm the kernel parse cache for the fixture transcript. build_feed is CACHE-ONLY (the user
        2026-06-26): its working-dot + deep-link anchors + API-error/awaiting badges + provisional card read
        the parse ONLY if it's already cached, so the cold start paints cards at once; the background warmer
        (_warm_fleet_bg) populates the cache in production. Tests stand in for that warmer here."""
        km._parse_cache.pop(str(self.tpath), None)
        km._parse(str(self.tpath), SID, NOW)

    def _goal_store(self, nodes, status, last=None, closed=None, planned=True):
        store = {"rompUuid": SID, "seq": len(nodes), "lastNode": last, "closedTurns": closed or [],
                 "nodes": nodes, "placements": {}, "status": status}
        if planned:
            # mirror a caught-up planner: every currently-due unit is recorded (a skip records one too) —
            # the nudge fire path requires the planner queue empty (the 2026-07-15 placement gate), so a
            # fixture that means "the judges have ruled and left this store" must say so in placements.
            # planned=False = the planner hasn't processed the transcript yet.
            try:
                turns = jd.parsed_session(SID, [str(self.tpath)], NOW)["turns"]
                for u in jd.plan_units({"turns": turns}, store):
                    store["placements"][jd._unit_key(u[0], u[1])] = None
            except Exception:
                pass                                     # no transcript on disk yet → nothing due
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))

    def _working_tmux(self):
        km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 10, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}

    def test_provisional_card_surfaces_for_an_in_progress_prompt_with_no_card(self):
        # The user 2026-06-18: a session actively working a brand-new ask shows NO card, because the planner
        # withholds the final segment of an OPEN turn until it ends. Surface a live-prompt placeholder so the
        # working session isn't invisible — a working card, gist from the prompt, no goal node (empty tree).
        self._open_turn_transcript(ended=False)
        g1 = SID + ":g1"
        self._goal_store(
            {g1: {"id": g1, "text": "first ask", "parentId": None, "nodeComplete": True,
                  "blocked": False, "cleared": False, "trail": [], "t": T0}},
            {g1: "completed"}, last=g1, planned=False)   # unplaced by premise: the provisional exists because the planner hasn't placed
        self._working_tmux()
        asks = km.build_feed(NOW)["asks"]
        prov = [a for a in asks if a.get("provisional")]
        self.assertEqual(len(prov), 1, "the in-progress prompt surfaces exactly one provisional card")
        p = prov[0]
        self.assertEqual(p["itemId"], "provisional:" + SID)
        self.assertEqual(p["column"], "working")
        self.assertIn("empty space", p["text"], "before the message caption lands, the card shows the raw prompt (never blank)")
        self.assertEqual(p["tree"], [], "a placeholder carries no goal node")
        self.assertTrue(any(a["itemId"] == g1 for a in asks), "the real completed card is untouched")

    def test_no_provisional_card_for_a_raw_text_compact_command_window(self):
        # the user 2026-07-22: a session with no OPEN goal, currently /compact-ing, showed a spurious
        # "Compact conversation context" provisional card. CLI 2.1.215+ writes a typed /compact as a
        # raw-text human atom ~90s BEFORE its <command-name> wrapper lands (past the compact_boundary), so
        # _seg_command is still False during the window; the planner DEFERS placement for a slash-shaped
        # segment (never places it), so the placeholder would surface for the whole window. _provisional_card
        # must mirror plan_units' _seg_slash_shaped guard (judge.py:3207).
        recs = [uline(T0, "first ask", "u1", ps="typed"),
                aline(T0 + 20, "Done with the first ask.", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "/compact", "u2", "a1", ps="typed")]   # raw-text /compact, wrapper not yet landed
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        g1 = SID + ":g1"
        self._goal_store(
            {g1: {"id": g1, "text": "first ask", "parentId": None, "nodeComplete": True,
                  "blocked": False, "cleared": False, "trail": [], "t": T0}},
            {g1: "completed"}, last=g1, planned=False)   # every goal done; only the open /compact turn remains
        self._working_tmux()
        asks = km.build_feed(NOW)["asks"]
        self.assertFalse([a for a in asks if a.get("provisional")],
                         "a raw-text /compact turn gets no provisional card (mirrors the landed-command guard)")
        self.assertTrue(any(a["itemId"] == g1 for a in asks), "the real completed card is untouched")

    def test_provisional_card_resurrects_when_the_card_is_cleared_mid_turn(self):
        # the user 2026-07-05: clearing a card whose segment was STILL WORKING left the session on a blank
        # board until the turn ended — the placement tombstone suppressed the placeholder while the placed
        # node itself was gone. The placed-gate now sees through that: placed-but-cleared-out-from-under
        # RESURRECTS the placeholder (until the judge's one-shot live re-plan lands a fresh card).
        self._open_turn_transcript(ended=False)
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        held = em.segments(session["turns"][-1])[-1]
        g1 = SID + ":g1"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g1,
            "nodes": {g1: {"id": g1, "text": "a mis-titled card", "parentId": None, "nodeComplete": False,
                           "blocked": False, "cleared": True, "trail": [held["id"]], "t": T0}},
            "placements": {held["id"] + "#p": g1}, "status": {g1: "cleared"}}))
        self._working_tmux()
        asks = km.build_feed(NOW)["asks"]
        prov = [a for a in asks if a.get("provisional")]
        self.assertEqual(len(prov), 1, "the placeholder resurrects: a working session never shows a blank board")
        self.assertNotIn(g1, {a["itemId"] for a in asks}, "the cleared card itself stays off the board")

    def test_placeholder_drops_once_the_live_replan_lands_and_stays_out_after_a_second_clear(self):
        self._open_turn_transcript(ended=False)
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        held = em.segments(session["turns"][-1])[-1]
        g1, g3 = SID + ":g1", SID + ":g3"
        def write(g3_cleared):
            (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
                "rompUuid": SID, "seq": 3, "lastNode": g3,
                "nodes": {g1: {"id": g1, "text": "a mis-titled card", "parentId": None, "nodeComplete": False,
                               "blocked": False, "cleared": True, "trail": [], "t": T0},
                          g3: {"id": g3, "text": "Continuing: the real work", "parentId": None,
                               "nodeComplete": False, "blocked": False, "cleared": g3_cleared,
                               "trail": [held["id"]], "t": T0 + 100}},
                "placements": {held["id"] + "#p": g1, held["id"] + "#live": g3},
                "status": {g1: "cleared", g3: "cleared" if g3_cleared else "working"}}))
        self._working_tmux()
        write(g3_cleared=False)                        # the live re-plan landed its fresh card
        asks = km.build_feed(NOW)["asks"]
        self.assertFalse([a for a in asks if a.get("provisional")],
                         "the fresh live card replaces the placeholder")
        self.assertIn(g3, {a["itemId"] for a in asks})
        write(g3_cleared=True)                         # …and the user clears the fresh card TOO
        asks = km.build_feed(NOW)["asks"]
        self.assertFalse([a for a in asks if a.get("provisional")],
                         "a second clear of the same in-flight work is FINAL — no phantom placeholder forever")

    def test_live_cleared_under_helper_truth_table(self):
        nodes = {"s:g1": {"id": "s:g1", "parentId": None, "cleared": True},
                 "s:g2": {"id": "s:g2", "parentId": "s:g1", "cleared": False},
                 "s:g4": {"id": "s:g4", "parentId": None, "cleared": False}}
        self.assertTrue(km._card_gone(nodes, "s:g2"), "cleared ancestor → gone (the cross-off flags the TOP)")
        self.assertTrue(km._card_gone(nodes, "s:g9"), "absent from the live store (archived) → gone")
        self.assertFalse(km._card_gone(nodes, "s:g4"))
        self.assertTrue(km._live_cleared_under({"seg1#p": "s:g1"}, nodes, "seg1"),
                        "placed onto a cleared card → the clear-mid-work window is open")
        self.assertFalse(km._live_cleared_under({"seg1#p": "s:g4"}, nodes, "seg1"), "alive target → normal gate")
        self.assertFalse(km._live_cleared_under({"seg1#p": None}, nodes, "seg1"), "a None placement is a ruling")
        self.assertFalse(km._live_cleared_under({}, nodes, "seg1"), "unplaced → the normal gates apply")
        self.assertFalse(km._live_cleared_under({"seg1#p": "s:g1", "seg1#live": "s:g3"}, nodes, "seg1"),
                         "a recorded #live key closes the window, whatever became of its card")
        self.assertTrue(km._live_replanned({"seg1#live": None}, "seg1"))
        self.assertFalse(km._live_replanned({"seg1#p": "s:g1"}, "seg1"))

    def test_text_less_seams_get_distinct_stable_ids_not_one_shared_hash(self):
        # A settle-seam tail has no trigger text, so _segment_id can't hash content. It USED to hash sha1('')
        # — the SAME for every text-less seam — so under the timestamp-invariant _seg_key every empty seam in a
        # session collapsed to ONE key, and a fresh working seam inherited a long-done seam's placement (the
        # feed read it "already placed" and blanked the Working column, the user 2026-07-22). _segment_id now
        # keys a text-less segment on its ANCHOR ATOM's uuid: unique per seam, stable across parses, no window.
        import hashlib
        u = "11111111-2222-3333-4444-555555555555"
        def seam_atom(uuid):   # a text-LESS (tool-only) opener atom
            return {"uuid": uuid, "type": "assistant", "session_id": u,
                    "message": {"role": "assistant",
                                "content": [{"type": "tool_use", "id": "t_" + uuid, "name": "Bash", "input": {}}]}}
        a1, a2 = seam_atom("aaaa1111"), seam_atom("bbbb2222")
        id1 = em._segment_id(u, 1000000000, [a1], None)
        id2 = em._segment_id(u, 2000000000, [a2], None)
        self.assertTrue(id1.endswith(hashlib.sha1(b"aaaa1111").hexdigest()[:8]),
                        "a text-less seam is keyed by its anchor atom's uuid, not sha1('')")
        self.assertNotIn("da39a3ee", id1, "…so the shared empty-text hash never appears")
        self.assertNotEqual(km._seg_key(id1), km._seg_key(id2), "two distinct seams get DISTINCT keys")
        # STABLE across t-drift: same anchor atom, different seg_t → SAME key (the hash rides the uuid, not t)
        self.assertEqual(km._seg_key(id1), km._seg_key(em._segment_id(u, 1000000050, [a1], None)),
                         "the same seam re-parsed at a drifted t keeps its key")
        # TEXT-BEARING is unchanged — content hash, drift-invariant across the SDK echo (same text, diff uuid)
        def text_atom(uuid, txt):
            return {"uuid": uuid, "type": "user", "session_id": u,
                    "message": {"role": "user", "content": [{"type": "text", "text": txt}]}}
        tb1 = em._segment_id(u, 1000000000, [text_atom("cccc", "run the tests")], "cccc")
        tb2 = em._segment_id(u, 2000000000, [text_atom("dddd", "run the tests")], "dddd")
        self.assertEqual(km._seg_key(tb1), km._seg_key(tb2),
                         "a text-bearing seg keys on CONTENT, so its echo and real atom still match")
        # and the placement lookup no longer aliases — no window needed, distinct keys never collide
        self.assertTrue(km._seg_placed({id1 + "#p": u + ":g1"}, id1), "a seam matches its OWN placement")
        self.assertFalse(km._seg_placed({id1 + "#p": u + ":g1"}, id2), "…but NOT a distinct seam's (no alias)")

    def test_feed_api_error_floor_yields_to_awaiting_background_agents(self):
        # the user 2026-07-05 (the jld_audit inconsistency): the main thread hit content-filter API errors
        # while two background agents kept working. The feed card wore a red "API error" + "stalled" chip
        # while the chat chip said Working — build_feed's _api_error read was the ONE without the awaiting
        # gate (_session_chip and build_session both have it), and the api_top floor then suppressed the
        # very "Awaiting background agents" flip that told the truth, which also kept col=="working" so the
        # stalled chip showed. One formula: awaiting wins; the floor applies only when truly dead in the water.
        recs = [uline(T0, "audit the essay structure", "u1", ps="typed"),
                aline(T0 + 20, "Dispatching two reviewers.", "a1", "u1", tools=("Task",), stop="tool_use"),
                apierr_line(NOW - 60, "e1", "a1",
                            text="API Error: 400 Output blocked by content filtering policy", status=400,
                            category="invalid_request")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        g1 = SID + ":g1"
        self._goal_store({g1: {"id": g1, "text": "Audit the essay structure", "parentId": None,
                               "nodeComplete": False, "blocked": False, "cleared": False,
                               "trail": [], "t": T0}}, {g1: "working"}, last=g1)
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {g1: {"count": 1, "failed": True}}}))
        km._autonudge_cache.clear(); km._nudge_times_cache.clear()
        # the LIVE SubagentStart/Stop count rides the backend snapshot (the designed signal) — 2 agents running
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None,
                                           "subagents": [{"type": "", "since": 1}, {"type": "", "since": 2}]}}
        c = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[g1]
        self.assertIn("2 background agents", (c.get("awaiting") or {}).get("why") or "",
                      "the card says the agents are still working — the session is in motion")
        self.assertIsNone(c.get("blocked"), "no red apiError floor while agents run")
        self.assertFalse(c.get("nudgeFailed"), "no stalled chip while agents run")
        # control: the SAME transcript with no live agents is genuinely dead in the water → the floor applies
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        c = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[g1]
        self.assertEqual((c.get("blocked") or {}).get("state"), "apiError",
                         "without agents in flight the red badge + Retry still surface")
        self.assertFalse(c.get("awaiting"))

    def test_awaiting_survives_an_interleaved_turn_via_live_subagent_count(self):
        # the supersede hole itself (the user 2026-07-05): the overlay's awaiting:true is treated as stale
        # once ANY later 'working' state row lands — but a mid-wait turn (the auto-nudge status check)
        # writes exactly that row while the agents still run. The live snapshot count outranks the file.
        sdir = jd.STATE / "states"; sdir.mkdir(parents=True, exist_ok=True)
        (sdir / (SID + ".jsonl")).write_text(
            json.dumps({"t": NOW - 300, "awaiting": True, "why": "2 background task(s) running"}) + "\n"
            + json.dumps({"t": NOW - 60, "state": "working"}) + "\n"     # the nudge turn interleaving
            + json.dumps({"t": NOW - 30, "state": "waiting"}) + "\n")
        ov = km._states_awaiting_overlay(SID)
        self.assertFalse(ov and ov.get("awaiting"), "the overlay alone still reads superseded (the hole)")
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None,
                                           "subagents": [{"type": "", "since": 1}, {"type": "", "since": 2}]}}
        self.assertIn("2 background agents", (km._session_awaiting(SID, str(self.tpath), True) or {}).get("why", ""),
                      "the live SubagentStart/Stop count restores the truth over the superseded overlay")

    def test_card_carries_the_auto_nudge_history(self):
        # the stalled chip's EVIDENCE (the user 2026-07-02): a card whose goal the auto-nudge ledger has
        # fired on carries nudged {count, times} — the chip tooltip + modal line say romp DID follow up,
        # and when. The bare "stalled" label read like a state romp observed, not a nudge outcome
        # (the SSH-thread confusion: two fires, invisible from the card).
        g1, g2 = SID + ":g1", SID + ":g2"
        self._goal_store(
            {g1: {"id": g1, "text": "run the end-to-end test", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "trail": [], "t": T0},
             g2: {"id": g2, "text": "an unrelated card", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "trail": [], "t": T0}},
            {g1: "working", g2: "working"}, last=g1)
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {g1: {"count": 2, "lastTurnId": SID + ":1:aa", "failed": True}}}))
        (jd.STATE / "nudge-events.jsonl").write_text(
            json.dumps({"sid": SID, "gid": g1, "t": NOW - 600, "count": 1}) + "\n"
            + json.dumps({"sid": SID, "gid": g1, "t": NOW - 300, "count": 2}) + "\n")
        km._autonudge_cache.clear(); km._nudge_times_cache.clear()
        cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
        self.assertEqual(cards[g1]["nudged"], {"count": 2, "times": [NOW - 600, NOW - 300]},
                         "the fires and their times ride the card")
        self.assertTrue(cards[g1]["nudgeFailed"], "the failed stamp still drives the chip itself")
        self.assertIsNone(cards[g2]["nudged"], "a never-nudged goal ships null")

    def test_stalled_chip_yields_once_the_story_moved_on(self):
        # the user 2026-07-09 (g143): a failed nudge blocked the card, the user replied, the CLOSER ruled
        # it done, and a later user follow-up put it back to working — yet the chip resurfaced, because
        # `failed` only resets on the next nudge fire and the working arm never asked whether the stall
        # was already answered. Any real actor's diary event AFTER the nudge's own block retires the chip.
        g1 = SID + ":g1"
        self._goal_store(
            {g1: {"id": g1, "text": "audit the pipeline", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "trail": [], "t": T0,
                  "log": [{"ev_t": NOW - 500, "src": "nudge", "kind": "block", "at": NOW - 500},
                          {"ev_t": NOW - 400, "src": "user", "kind": "reopen", "at": NOW - 400},
                          {"ev_t": NOW - 390, "src": "closer", "kind": "done", "at": NOW - 390},
                          {"ev_t": NOW - 100, "src": "user", "kind": "reopen", "at": NOW - 100}]}},
            {g1: "working"}, last=g1)
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {g1: {"count": 1, "lastTurnId": SID + ":1:aa", "failed": True}}}))
        km._autonudge_cache.clear()
        cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
        self.assertFalse(cards[g1]["nudgeFailed"],
                         "a closer verdict + user reopen after the failed nudge is the story moving on")

    def test_stalled_chip_retires_on_the_unblockers_ruling(self):
        # the user 2026-08-14: the unblocker ruled a nudge's block answered in passing (a fresh request
        # arrived and the session resumed the thread) and the card moved back to Working — but the chip,
        # whose claim IS that block, survived for hours because "unblocker" was missing from the
        # story-moved actor set: a red "waiting on you" on a card the judges had just un-waited.
        g1 = SID + ":g1"
        self._goal_store(
            {g1: {"id": g1, "text": "audit the pipeline", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "trail": [], "t": T0,
                  "log": [{"ev_t": NOW - 500, "src": "nudge", "kind": "block", "at": NOW - 500},
                          {"ev_t": NOW - 100, "src": "unblocker", "kind": "unblock", "at": NOW - 100,
                           "why": "answered in passing: a new request arrived and the session resumed"}]}},
            {g1: "working"}, last=g1)
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {g1: {"count": 1, "lastTurnId": SID + ":1:aa", "failed": True}}}))
        km._autonudge_cache.clear()
        cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
        self.assertFalse(cards[g1]["nudgeFailed"],
                         "the unblocker's ruling IS the story moving on — the chip's claim was just overruled")

    def test_debug_mode_joins_warn_rows_onto_the_card(self):
        # the user 2026-07-09: with `romp --debug on`, every judge failure touching a card rides it to the
        # modal's Warnings section — goal-linked rows land on that card only, other sessions' rows never.
        g1, g2 = SID + ":g1", SID + ":g2"
        self._goal_store(
            {g1: {"id": g1, "text": "build the exporter", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "trail": [], "t": T0},
             g2: {"id": g2, "text": "an unrelated card", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "trail": [], "t": T0}},
            {g1: "working", g2: "working"}, last=g1)
        saved_errors = jd.ERRORS
        jd.ERRORS = Path(self.td.name) / "judge-errors.jsonl"
        try:
            rows = [{"t": NOW - 60, "judge": "distiller", "fsid": SID, "err": "cite-miss", "note": "n1", "goal": g1},
                    {"t": NOW - 50, "judge": "closer", "fsid": SID, "err": "parse", "note": "n2", "goal": [g1],
                     "debug": {"input": "in", "reply": "out"}},
                    {"t": NOW - 40, "judge": "closer", "fsid": "99999999-0000-0000-0000-000000000000",
                     "err": "parse", "note": "other session", "goal": [g1]}]
            jd.ERRORS.write_text("".join(json.dumps(r) + "\n" for r in rows))
            (jd.STATE / "debug-mode.json").write_text('{"on": true}')
            km._jerr_cache.clear()
            cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
            got = cards[g1]["warnRows"]
            self.assertEqual([r["note"] for r in got], ["n1", "n2"], "this card's rows only, newest last")
            self.assertEqual(got[1]["debug"], {"input": "in", "reply": "out"}, "the capture rides through")
            self.assertIsNone(cards[g2]["warnRows"], "no rows → no section")
            (jd.STATE / "debug-mode.json").write_text('{"on": false}')
            cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
            self.assertIsNone(cards[g1]["warnRows"], "debug off → nothing read, nothing emitted")
        finally:
            jd.ERRORS = saved_errors

    def test_card_warn_rows_join_shapes(self):
        # the pure join: goal as one id, goal as the closer's id list, seg resolved through placements
        # (any filing phase suffix); other sessions and unrelated goals never land; capped newest-last.
        sub = {"S:g1", "S:g1a"}
        pl = {"S:9:aa": "S:g1a", "S:9:bb#p": "S:g1"}
        rows = [{"fsid": "S", "goal": "S:g1", "note": "a"},
                {"fsid": "S", "goal": ["S:g9", "S:g1a"], "note": "b"},
                {"fsid": "S", "seg": "S:9:aa", "note": "c"},
                {"fsid": "S", "seg": "S:9:bb", "note": "d"},
                {"fsid": "S", "goal": "S:g9", "note": "nope"},
                {"fsid": "T", "goal": "S:g1", "note": "nope"}]
        got = km._card_warn_rows(rows, "S", sub, pl)
        self.assertEqual([r["note"] for r in got], ["a", "b", "c", "d"])
        many = [{"fsid": "S", "goal": "S:g1", "note": str(i)} for i in range(30)]
        self.assertEqual(len(km._card_warn_rows(many, "S", sub, {}, cap=20)), 20)
        self.assertEqual(km._card_warn_rows(many, "S", sub, {}, cap=20)[-1]["note"], "29",
                         "the cap keeps the newest rows")

    def test_cardmove_op_is_retired(self):
        # The messageless "Move to Working" was REMOVED (the user 2026-07-25: zero recorded uses; a
        # reply reopens with context). The op must no longer route as a drive op, and the judge no
        # longer exports its producer — only the journal REPLAY of historical "move" events survives.
        saved_bf = km.Sessions.backend_for
        km.Sessions.backend_for = lambda sid: None
        try:
            handled = km._drive({"type": "cardMove", "itemId": SID + ":g1", "sid": SID, "to": "working"}, None)
        finally:
            km.Sessions.backend_for = saved_bf
        self.assertFalse(handled, "cardMove is not a drive op any more")
        self.assertFalse(hasattr(jd, "user_move"), "the producer is gone; _replay_overrides keeps the reader")

    def test_provisional_card_surfaces_for_a_seam_tail(self):
        # plans/segment-regrowth.md: a top settles while its placed segment keeps growing with real
        # work → the tail splits into a fresh unplaced segment, and the feed shows a Working placeholder
        # NEXT TO the completed card (previously the pivot work was invisible: the segment was placed, so
        # the placeholder's drop-gate suppressed it by design). Placed tail → the placeholder yields.
        recs = [uline(T0, "fix A, B and C", "u1", ps="typed"),
                aline(T0 + 20, "Working through the three items.", "a1", "u1", tools=("Edit",), stop="tool_use"),
                trline(T0 + 25, "tu_a1_0", "r1", "a1", content="edited"),
                aline(T0 + 40, "All three merged and pushed.", "a2", "r1", stop="tool_use"),
                aline(T0 + 200, "", "a3", "a2", tools=("Bash",), stop="tool_use")]   # the post-settle pivot (turn OPEN)
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        seg_id = em.segments(km._parse(str(self.tpath), SID, NOW)["turns"][0])[0]["id"]
        g1 = SID + ":g1"
        store = {"rompUuid": SID, "seq": 1, "lastNode": g1,
                 "nodes": {g1: {"id": g1, "text": "fix A, B and C", "parentId": None, "nodeComplete": True,
                                "blocked": False, "cleared": False, "settledDone": True,
                                "trail": [seg_id], "t": T0, "mt": T0 + 40}},
                 "placements": {seg_id: g1}, "status": {g1: "completed"},
                 "seams": [{"t": T0 + 100, "top": g1, "text": "fix A, B and C",
                            "segs": [jd._seg_key(seg_id)]}]}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        self._working_tmux()
        asks = km.build_feed(NOW)["asks"]
        prov = [a for a in asks if a.get("provisional")]
        self.assertEqual(len(prov), 1, "the unplaced seam tail surfaces a Working placeholder")
        self.assertIn("fix A, B and C", prov[0]["text"], "…that names the completed goal it grew past")
        self.assertEqual(prov[0]["column"], "working")
        self.assertTrue(any(a["itemId"] == g1 and a["column"] == "completed" for a in asks),
                        "the completed card stays completed alongside it")
        # the planner places the tail (even as a SKIP) → the placeholder yields, same drop-gate as ever
        tail_id = jd.apply_seams([em.segments(km._parse(str(self.tpath), SID, NOW)["turns"][0])[0]], store)[1]["id"]
        store["placements"][tail_id] = None
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        self.assertFalse([a for a in km.build_feed(NOW)["asks"] if a.get("provisional")],
                         "a placed tail drops the placeholder")

    def test_a_legacy_empty_hash_placement_does_not_suppress_a_fresh_seam(self):
        # TRANSITION guard for the uuid-anchored seam identity (the user 2026-07-22): stores written before the
        # change carry seam placements keyed by the shared sha1('') hash (da39a3ee). A fresh seam now keys on
        # its atom uuid, so it CANNOT collide with a legacy empty-hash placement — a session working past a
        # completed goal surfaces its placeholder instead of reading a stale da39a3ee row as "already placed"
        # (the blank-board bug). PLACEMENTS_V's seal handles the flip; this pins that the keys simply don't alias.
        recs = [uline(T0, "fix A, B and C", "u1", ps="typed"),
                aline(T0 + 20, "Working through the three items.", "a1", "u1", tools=("Edit",), stop="tool_use"),
                trline(T0 + 25, "tu_a1_0", "r1", "a1", content="edited"),
                aline(T0 + 40, "All three merged and pushed.", "a2", "r1", stop="tool_use"),
                aline(T0 + 200, "", "a3", "a2", tools=("Bash",), stop="tool_use")]   # the seam tail (turn OPEN)
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        seg_id = em.segments(km._parse(str(self.tpath), SID, NOW)["turns"][0])[0]["id"]
        g1 = SID + ":g1"
        store = {"rompUuid": SID, "seq": 1, "lastNode": g1,
                 "nodes": {g1: {"id": g1, "text": "fix A, B and C", "parentId": None, "nodeComplete": True,
                                "blocked": False, "cleared": False, "settledDone": True,
                                "trail": [seg_id], "t": T0, "mt": T0 + 40}},
                 "placements": {seg_id: g1}, "status": {g1: "completed"},
                 "seams": [{"t": T0 + 100, "top": g1, "text": "fix A, B and C",
                            "segs": [jd._seg_key(seg_id)]}]}
        tail = jd.apply_seams([em.segments(km._parse(str(self.tpath), SID, NOW)["turns"][0])[0]], store)[1]
        self.assertNotIn("da39a3ee", tail["id"], "the seam tail is uuid-anchored now, not sha1('')")
        # a LEGACY empty-hash placement (pre-change) for some long-done seam — a gone target + a None ruling
        legacy = SID + ":1000000000:da39a3ee"
        store["placements"].update({legacy + "#p": SID + ":gGONE", legacy: None})
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        self._working_tmux()
        prov = [a for a in km.build_feed(NOW)["asks"] if a.get("provisional")]
        self.assertEqual(len(prov), 1,
                         "the fresh uuid-keyed seam surfaces its placeholder — a legacy da39a3ee placement can't alias it")
        self.assertEqual(prov[0]["column"], "working")
        self.assertTrue(any(a["itemId"] == g1 and a["column"] == "completed" for a in km.build_feed(NOW)["asks"]),
                        "the completed card stays completed alongside it")

    def test_session_working_reads_the_event_model_not_tmux(self):
        # the user 2026-06-22: WORKING is derived from the TRANSCRIPT (an open, un-ended final turn), never the
        # tmux pane state — tmux is one backend, so the signal must be backend-agnostic.
        saved = km._downtime; km._downtime = []          # no host-sleep windows interfering
        try:
            self.assertTrue(km._session_working([{"ended": False, "atoms": [{"type": "user"}], "t": NOW, "end": NOW}]),
                            "an open final turn = working")
            self.assertFalse(km._session_working([{"ended": True, "atoms": [{"type": "assistant"}], "t": NOW, "end": NOW}]),
                             "an ended turn = not working")
            self.assertFalse(km._session_working([{"ended": False, "atoms": [{"type": "idle"}], "t": NOW, "end": NOW}]),
                             "an idle-terminated turn = not working")
            self.assertFalse(km._session_working([]), "no turns = not working")
            # IDLE-LED-THEN-RESUMED (the user 2026-06-30, judge_audit): a session stalled (an idle span), then got
            # re-engaged and is ACTIVELY WORKING again — the earlier idle folds into the SAME turn, so the idle
            # atom sits in the MIDDLE with real work after it. Keying on ANY idle atom wrongly read this as
            # not-working mid-work, and the auto-nudge fired a spurious "status?" on it. Only a TAIL idle stops.
            self.assertTrue(km._session_working([{"ended": False, "t": NOW - 90, "end": NOW,
                             "atoms": [{"type": "idle"}, {"type": "assistant"}, {"type": "user"}]}]),
                            "idle in the MIDDLE but work resumed after it = working")
            self.assertFalse(km._session_working([{"ended": False, "t": NOW - 90, "end": NOW,
                             "atoms": [{"type": "assistant"}, {"type": "user"}, {"type": "idle"}]}]),
                             "worked then went idle AT THE TAIL = not working")
        finally:
            km._downtime = saved

    def test_compacting_corroborated_against_the_event_model(self):
        # a STUCK tmux @claude-state=compacting (a missed PostCompact — restart-storm / interrupted compaction)
        # must NOT read as compacting once the event model shows the session moved on: an OPEN working turn OR
        # a compact_boundary atom since the compaction start (the user 2026-06-24). A genuine compaction still
        # reads compacting. This is the chip/feed desync the user saw (compacting badge over a working session).
        km._compact_clicked.clear()
        saved = km._downtime; km._downtime = []
        SINCE = NOW - 100
        try:
            working = {"turns": [{"ended": False, "atoms": [{"type": "user"}], "t": NOW, "end": NOW}]}
            self.assertFalse(km._compacting(SID, "compacting", working, NOW, SINCE),
                             "stuck compacting + an OPEN working turn → not compacting (the desync bug)")
            settled = {"turns": [{"ended": True, "atoms": [
                {"type": "system", "subtype": "compact_boundary", "t": SINCE + 10}], "t": NOW, "end": NOW}]}
            self.assertFalse(km._compacting(SID, "compacting", settled, NOW, SINCE),
                             "stuck compacting + a compact_boundary since the start → compaction is done")
            genuine = {"turns": [{"ended": True, "atoms": [{"type": "assistant"}], "t": NOW, "end": NOW}]}
            self.assertTrue(km._compacting(SID, "compacting", genuine, NOW, SINCE),
                            "compacting + no open turn + no boundary-since → genuinely compacting")
            self.assertFalse(km._compacting(SID, "working", genuine, NOW, SINCE),
                             "tmux not compacting + no optimistic flag → not compacting")
        finally:
            km._downtime = saved

    def test_feed_working_list_follows_the_open_turn_not_tmux(self):
        # The working DOT (feed["working"], read by every surface) must follow the event model, NOT tmux: an
        # open turn is working even when tmux reads idle, and an ended turn is NOT working even when tmux reads
        # working (the user 2026-06-22 — moving off the tmux backend).
        name = km._name_of(SID)
        self._open_turn_transcript(ended=False)        # OPEN turn; setUp's tmux says "idle" (_open_turn_transcript warms the parse)
        self.assertIn(name, km.build_feed(NOW)["working"], "open turn → working even though tmux reads idle")
        self._open_turn_transcript(ended=True)         # ENDED turn
        self._working_tmux()                                               # tmux now reads "working"
        self.assertNotIn(name, km.build_feed(NOW)["working"], "ended turn → NOT working even though tmux reads working")

    def test_feed_carries_the_shared_session_order(self):
        # grouped mode (the user 2026-07-13) sorts each column's session runs by the SAME order the chat
        # tabs + timeline lanes hold (session-order.json) — the feed payload carries it on every push
        (km.jd.STATE / "session-order.json").write_text(json.dumps([SID, "22222222-0000-0000-0000-000000000000"]))
        self.assertEqual(km.build_feed(NOW)["order"], [SID, "22222222-0000-0000-0000-000000000000"])

    def test_awaiting_task_descs_read_the_live_snapshot(self):
        # The feed's "Waiting on task" pill (the user 2026-07-13) expands the live bg-task DESCRIPTIONS —
        # straight from the backend snapshot's bgTasks (the CLI task-lifecycle set); a desc-less task gets
        # a generic label; tmux sessions / unknown sids read []. Tasks with no launch id can't be
        # classified (2026-07-24: the service split) → pending → still AWAITED, listed as before.
        saved = km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {"bgTasks": [{"task_id": "t1", "desc": "Watch for round3 copy"},
                                                       {"task_id": "t2", "desc": ""}]}}
        try:
            self.assertEqual(km._awaiting_task_descs(SID, "/nonexistent"),
                             ["Watch for round3 copy", "background task"])
        finally:
            km._tmux_sessions = saved
        self.assertEqual(km._awaiting_task_descs("00000000-0000-0000-0000-000000000000", "/nonexistent"), [])
        # build_feed attaches the list on awaiting cards, beside the why (source pin)
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('"awaiting": ({"why": await_why, "kind": await_kind,', src)
        self.assertIn('"tasks": _awaiting_task_descs(fsid, s["path"])} if col == "awaiting" else None)', src)

    def test_provisional_card_shows_the_message_caption_once_it_lands(self):
        # The user 2026-06-19: the card reads the captioner's persisted MESSAGE caption ('<segid>#p') — the
        # SAME gist the timeline dot uses, no separate 'gist' judge call. Until it lands, the raw prompt;
        # once the captioner writes it, the phase prefix + the caption. An OPEN turn wears the honest
        # "Working:" — nothing is being analyzed while the session is still running (the user 2026-07-12).
        self._open_turn_transcript(ended=False)
        g1 = SID + ":g1"
        self._goal_store({g1: {"id": g1, "text": "first ask", "parentId": None, "nodeComplete": True,
                               "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g1: "completed"}, last=g1, planned=False)   # unplaced by premise: the provisional exists because the planner hasn't placed
        self._working_tmux()
        first = next(a for a in km.build_feed(NOW)["asks"] if a.get("provisional"))
        self.assertIn("empty space", first["text"], "no message caption yet → the raw prompt")
        self.assertNotIn("Analyzing", first["text"], "no stuck 'Analyzing…' placeholder — just the raw prompt")
        self._write_msg_caption("trimming the empty space below the cards")
        p = next(a for a in km.build_feed(NOW)["asks"] if a.get("provisional"))
        self.assertEqual(p["text"], "Working: trimming the empty space below the cards")
        self.assertFalse(p["judging"], "open turn → the swirl chip stays Working…, not Analyzing…")

    def test_provisional_card_says_analyzing_only_once_the_turn_settles(self):
        # The phase prefix tells the truth (the user 2026-07-12, who asked whether it was actually analyzing, or just
        # working and hadn't received the segment to analyze yet): the turn has ENDED but the planner
        # hasn't placed the segment — its classify pass is due/in flight — and only NOW does the card say
        # "Analyzing:" (and `judging` flips the swirl chip to Analyzing…).
        self._open_turn_transcript(ended=True)
        g1 = SID + ":g1"
        self._goal_store({g1: {"id": g1, "text": "first ask", "parentId": None, "nodeComplete": True,
                               "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g1: "completed"}, last=g1, planned=False)   # unplaced by premise: the provisional exists because the planner hasn't placed
        self._write_msg_caption("trimming the empty space below the cards")
        p = next(a for a in km.build_feed(NOW)["asks"] if a.get("provisional"))
        self.assertEqual(p["text"], "Analyzing: trimming the empty space below the cards")
        self.assertTrue(p["judging"], "settled turn awaiting placement → the chip may say Analyzing…")

    def test_working_card_wears_judging_through_the_settle_gap(self):
        # The user 2026-07-13: a finished session's card sat inertly in Working for a beat before moving to
        # Completed — the gap between the turn settling and the closer's verdict landing. A REAL working
        # card now carries `judging` through that gap (feed.ts shows the Analyzing… swirl), keyed on the
        # SAME event auto-nudge waits for: _closer_settled over the judge's own parse.
        g1 = SID + ":g1"
        self._goal_store({g1: {"id": g1, "text": "fix the flicker", "parentId": None, "nodeComplete": False,
                               "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g1: "working"}, last=g1)
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g1)
        self.assertEqual(card["column"], "working")
        self.assertTrue(card["judging"], "turn settled + no closer verdict yet → the Analyzing… gap")
        # the closer stamps its verdict for the turn AT ITS CURRENT SIZE → the swirl drops the same push
        lt = jd.parsed_session(SID, [str(self.tpath)], NOW)["turns"][-1]
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g1, "closedTurns": [lt["id"]],
            "closedSig": {lt["id"]: len(lt["atoms"])},
            "nodes": {g1: {"id": g1, "text": "fix the flicker", "parentId": None, "nodeComplete": False,
                           "blocked": False, "cleared": False, "trail": [], "t": T0}},
            "placements": {}, "status": {g1: "working"}}))
        card2 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g1)
        self.assertFalse(card2["judging"], "verdict recorded → no more Analyzing…; the card is plainly Working")

    def test_judging_never_shows_while_the_turn_is_still_open(self):
        # An OPEN turn is honest Working — nothing is being analyzed while the session still runs; the
        # swirl only covers the settled-but-unjudged beat (mirrors the provisional card's phase truth).
        g1 = SID + ":g1"
        self._goal_store({g1: {"id": g1, "text": "fix the flicker", "parentId": None, "nodeComplete": False,
                               "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g1: "working"}, last=g1)
        saved = km._session_working
        km._session_working = lambda turns: True
        try:
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g1)
        finally:
            km._session_working = saved
        self.assertFalse(card["judging"], "open turn → plain Working, never Analyzing…")

    # ── Auto Nudge (the user 2026-06-19): follow up ONCE on an orphaned working goal ──
    def _stub_nudge(self):
        # capture nudges instead of pasting into tmux; returns (sent_list, restore_fn)
        sent = []
        saved_send, saved_fu = km._tmux_send, jd.optimistic_followup
        km._tmux_send = lambda name, body, **kw: sent.append((name, body))
        jd.optimistic_followup = lambda sid, gid: True

        def restore():
            km._tmux_send, jd.optimistic_followup = saved_send, saved_fu
        return sent, restore

    def test_working_top_goal_picks_only_a_working_top(self):
        g1, g2, g3, sub = SID + ":g1", SID + ":g2", SID + ":g3", SID + ":s1"
        def n(nid, parent, done, blocked, cleared):
            return {"id": nid, "text": nid, "parentId": parent, "nodeComplete": done,
                    "blocked": blocked, "cleared": cleared, "trail": [], "t": T0}
        self._goal_store({g1: n(g1, None, True, False, False), g2: n(g2, None, False, True, False),
                          g3: n(g3, None, False, False, False), sub: n(sub, g3, False, False, False)},
                         {g1: "completed", g2: "blocked", g3: "working"})
        self.assertEqual(km._working_top_goal(SID), g3, "only a working TOP goal (not done/blocked/sub) qualifies")

    def test_working_top_goal_none_when_cleared(self):
        g = SID + ":g1"
        self._goal_store({g: {"id": g, "text": "x", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": True, "trail": [], "t": T0}}, {g: "working"})
        self.assertIsNone(km._working_top_goal(SID), "a cleared goal is not nudge-worthy")

    # ── working-note auto-expire (the user 2026-06-24): lift a stale set_working claim once a session goes
    #    idle with no working top goal, so peers stop coordinating against a finished session ──
    def _stub_expire(self, notes, working, top_goal):
        # stub the deps of _clear_done_working_notes; returns (cleared_calls, restore_fn). cleared_calls
        # records every _set_working_note(sid, text) the pass makes.
        cleared = []
        saved = (km._working_notes, km._alive_sessions, km._set_working_note,
                 km._session_working, km._working_top_goal, jd.parsed_session)
        km._working_notes = lambda: dict(notes)
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": str(self.tpath)}]
        km._set_working_note = lambda sid, text: cleared.append((sid, text))
        km._session_working = lambda turns: working
        km._working_top_goal = lambda sid: top_goal
        jd.parsed_session = lambda sid, paths, now: {"turns": [{"atoms": [], "ended": True}]}

        def restore():
            (km._working_notes, km._alive_sessions, km._set_working_note,
             km._session_working, km._working_top_goal, jd.parsed_session) = saved
        return cleared, restore

    def test_working_note_cleared_when_idle_and_done(self):
        cleared, restore = self._stub_expire({SID: "feed.ts"}, working=False, top_goal=None)
        try:
            km._clear_done_working_notes(NOW, {SID: {"state": "idle"}})
            self.assertEqual(cleared, [(SID, "")], "idle + no working top goal → the stale claim is lifted")
        finally:
            restore()

    def test_working_note_kept_while_still_working(self):
        cleared, restore = self._stub_expire({SID: "feed.ts"}, working=True, top_goal=None)
        try:
            km._clear_done_working_notes(NOW, {SID: {"state": "working"}})
            self.assertEqual(cleared, [], "a session still working (event model) keeps its claim")
        finally:
            restore()

    def test_working_note_kept_when_a_working_top_goal_remains(self):
        cleared, restore = self._stub_expire({SID: "feed.ts"}, working=False, top_goal=SID + ":gw")
        try:
            km._clear_done_working_notes(NOW, {SID: {"state": "idle"}})
            self.assertEqual(cleared, [], "idle but a working top goal remains (orphaned/stalled) → still its work")
        finally:
            restore()

    def test_working_note_tmux_working_short_circuits_before_parse(self):
        cleared, restore = self._stub_expire({SID: "feed.ts"}, working=False, top_goal=None)
        parsed = []
        jd.parsed_session = lambda *a, **k: (parsed.append(1), {"turns": []})[1]
        try:
            km._clear_done_working_notes(NOW, {SID: {"state": "working"}})   # tmux says working
            self.assertEqual(cleared, [], "tmux 'working' → keep the claim")
            self.assertEqual(parsed, [], "and short-circuit BEFORE parsing (cheap pre-gate)")
        finally:
            restore()

    def test_working_note_noop_when_none_published(self):
        cleared, restore = self._stub_expire({}, working=False, top_goal=None)
        try:
            km._clear_done_working_notes(NOW, {SID: {"state": "idle"}})
            self.assertEqual(cleared, [], "no published notes → nothing to do")
        finally:
            restore()

    def test_push_streams_the_active_tab_first(self):
        # the user 2026-06-24 (chat slow-load): _push builds + FLUSHES the tab the client is looking at FIRST
        # (its client["active"], from the ?active= connect hint), then streams the rest — so first paint is the
        # active transcript, not a wait on every tab building. The tab strip is sent before any heavy build.
        sent = []   # (key, msg) in SEND order
        saved = (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
                 km.build_timeline, km._send_client)
        km._tmux_sessions = lambda: {}
        p = str(self.tpath)   # a transcript ON DISK — a pathless fake would rank as just-created (top tier)
        km._chat_tab_sessions = lambda now, tmux: [{"sid": "A", "path": p}, {"sid": "B", "path": p},
                                                   {"sid": "C", "path": p}]
        km.build_session = lambda sid, now, tmux: {"id": sid, "name": sid, "color": None,
                                                   "status": None, "ledger": None}
        km.build_feed = lambda now, tmux: {"working": [], "asks": []}
        km.build_timeline = lambda now, tmux: None
        km._send_client = lambda c, key, msg, pre=None: sent.append((key, msg))
        client = {"app": "chat", "active": "B", "alive": True}
        try:
            km._push([client])
        finally:
            (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
             km.build_timeline, km._send_client) = saved
        chat_order = [key[1] for (key, _) in sent if key[0] == "chat"]
        self.assertEqual(chat_order[0], "B", "the ACTIVE tab is built + streamed first")
        self.assertEqual(set(chat_order), {"A", "B", "C"}, "every tab still streams")
        first_chat = next(i for i, (k, _) in enumerate(sent) if k[0] == "chat")
        taborder = next(i for i, (k, _) in enumerate(sent) if k[0] == "taborder")
        self.assertLess(taborder, first_chat, "the tab strip paints before the first heavy build")

    def test_push_no_active_hint_streams_in_tab_order(self):
        # no client["active"] (e.g. nothing persisted yet) → graceful fallback: stream in tab order, still
        # incrementally (no regression, just no prioritization).
        sent = []
        saved = (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
                 km.build_timeline, km._send_client)
        km._tmux_sessions = lambda: {}
        p = str(self.tpath)
        km._chat_tab_sessions = lambda now, tmux: [{"sid": "A", "path": p}, {"sid": "B", "path": p},
                                                   {"sid": "C", "path": p}]
        km.build_session = lambda sid, now, tmux: {"id": sid, "name": sid, "color": None,
                                                   "status": None, "ledger": None}
        km.build_feed = lambda now, tmux: {"working": [], "asks": []}
        km.build_timeline = lambda now, tmux: None
        km._send_client = lambda c, key, msg, pre=None: sent.append((key, msg))
        try:
            km._push([{"app": "chat", "alive": True}])
        finally:
            (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
             km.build_timeline, km._send_client) = saved
        self.assertEqual([key[1] for (key, _) in sent if key[0] == "chat"], ["A", "B", "C"],
                         "no active hint → tab order, all tabs still delivered")

    def test_push_builds_a_transcript_less_session_at_active_priority(self):
        """A JUST-CREATED session has no transcript, and its creator cannot declare it active — a
        client can't post activeTab for a tab whose first payload hasn't arrived — so ranked last it
        waited out the whole fleet's builds (~22s measured live), leaving "Opening session" dots on a
        session that had been ready the whole time (the user 2026-08-08). Its build is near-free, so
        it rides the ACTIVE tier and its payload streams at the top of the cycle."""
        sent = []
        saved = (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
                 km.build_timeline, km._send_client)
        km._tmux_sessions = lambda: {}
        p = str(self.tpath)
        km._chat_tab_sessions = lambda now, tmux: [
            {"sid": "A", "path": p},
            {"sid": "NEW", "path": p + ".does-not-exist"},   # just created: nothing on disk yet
            {"sid": "C", "path": p}]
        km.build_session = lambda sid, now, tmux: {"id": sid, "name": sid, "color": None,
                                                   "status": None, "ledger": None}
        km.build_feed = lambda now, tmux: {"working": [], "asks": []}
        km.build_timeline = lambda now, tmux: None
        km._send_client = lambda c, key, msg, pre=None: sent.append((key, msg))
        try:
            km._push([{"app": "chat", "active": "C", "alive": True}])
        finally:
            (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
             km.build_timeline, km._send_client) = saved
        chat_order = [key[1] for (key, _) in sent if key[0] == "chat"]
        self.assertEqual(chat_order, ["NEW", "C", "A"],
                         "the transcript-less session shares the active tier (stable within it)")

    def test_push_caches_unchanged_background_tabs_but_always_rebuilds_active(self):
        # the user 2026-06-24 (sluggish UI): the 0.5s pusher rebuilt EVERY open tab — a full transcript reshape
        # into ChatEvent[] AND a json.dumps of the whole chat, per tab, even when nothing changed — which pegged
        # the kernel on multi-MB transcripts and starved the webview. A BACKGROUND tab whose transcript+states
        # are unchanged now reuses its built payload (one stat() instead of a reshape+serialize); the ACTIVE
        # tab always rebuilds so what the user is watching stays live (incl. SDK live-tail atoms).
        import tempfile
        d = tempfile.mkdtemp()
        pa, pb = os.path.join(d, "A.jsonl"), os.path.join(d, "B.jsonl")
        for p in (pa, pb):
            with open(p, "w") as f:
                f.write("{}\n")
        calls = []
        saved = (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
                 km.build_timeline, km._send_client)
        km._tmux_sessions = lambda: {}
        km._chat_tab_sessions = lambda now, tmux: [{"sid": "A", "path": pa}, {"sid": "B", "path": pb}]
        km.build_session = lambda sid, now, tmux: (calls.append(sid) or
                                                   {"id": sid, "name": sid, "color": None, "status": None, "ledger": None})
        km.build_feed = lambda now, tmux: {"working": [], "asks": []}
        km.build_timeline = lambda now, tmux: None
        km._send_client = lambda c, key, msg, pre=None: None
        km._built_chat.clear()
        client = {"app": "chat", "active": "A", "alive": True}
        try:
            km._push([client])                       # 1st: builds A + B
            km._push([client])                       # 2nd: A rebuilt (active); B reused (unchanged)
            after_two = list(calls)
            with open(pb, "a") as f:                 # B's transcript grows → its signature busts
                f.write("{}\n")
            os.utime(pb, None)
            km._push([client])                       # 3rd: A rebuilt; B rebuilt (changed)
        finally:
            (km._tmux_sessions, km._chat_tab_sessions, km.build_session, km.build_feed,
             km.build_timeline, km._send_client) = saved
            km._built_chat.clear()
        self.assertEqual(calls.count("A"), 3, "the ACTIVE tab rebuilds on every push (stays live)")
        self.assertEqual(after_two.count("B"), 1, "an unchanged BACKGROUND tab is NOT rebuilt on the 2nd push")
        self.assertEqual(calls.count("B"), 2, "the background tab rebuilds once its transcript actually changes")

    def _orphaned_goal(self, idle=True, closer_done=True, planned=True):
        # an idle (or still-open) session whose top goal still shows "working". closer_done puts the latest
        # turn's id in closedTurns, so the closer-verdict gate lets the nudge through (the realistic case: the
        # closer ran and left the goal working). closer_done=False = the closer hasn't classified it yet.
        # planned=False = the planner hasn't placed the turn's units yet (the 2026-07-15 placement gate holds).
        self._open_turn_transcript(ended=idle); km._parse_cache.clear()
        g = SID + ":gw"
        closed = []
        if closer_done:
            try:
                closed = [km._parse(str(self.tpath), SID, NOW)["turns"][-1]["id"]]
            except Exception:
                closed = []
        self._goal_store({g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": T0}}, {g: "working"},
                         last=g, closed=closed, planned=planned)
        return g

    def test_auto_nudge_fires_once_per_turn(self):
        # the user 2026-06-26: ONE nudge per stalled turn. A stop that persists across pusher ticks must NOT
        # re-fire each tick — the old two-per-turn cap let a second nudge fire ~6s after the first, before the
        # agent had consumed it (and the 2nd landed as a type:attachment as the session resumed, so it showed
        # without the romp logo). A NEW turn re-arms (see the re-arm tests); the SAME turn never fires twice.
        g = self._orphaned_goal(idle=True)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "first nudge on the stall turn")
            self.assertIn("romp-goal-id: " + g, sent[0][1], "the follow-up targets that goal")
            self.assertIn(km.AUTO_NUDGE_TEXT, sent[0][1])         # carries the nudge prompt verbatim
            km._auto_nudge_tick(NOW, km._tmux_sessions())          # SAME turn → NO re-fire
            self.assertEqual(len(sent), 1, "no second nudge on the same turn — one per turn")
            km._auto_nudge_tick(NOW, km._tmux_sessions())          # SAME turn again → still capped
            self.assertEqual(len(sent), 1, "a persistent stop does not re-fire each tick")
        finally:
            restore()

    def test_auto_nudge_waits_for_the_planner_to_place_the_turn(self):
        # THE 11:35/11:40 RESTATEMENT NUDGES (the user 2026-07-15): closer-settled alone is not "the judges
        # have ruled" — an unplanned turn no-op-closes on an empty menu (_turn_menu derives from placements),
        # so the old gate passed minutes before the planner's block verdict landed and the nudge fired on the
        # opener's provisional 'working' card (bug g78: nudged 11:35:20, block landed 11:35:32; romp_docs g82:
        # nudged 11:40:37, block 11:40:51 — each agent restated its own unanswered ask). The fire path must
        # wait for the planner's own "processed" event: every due unit PLACED. Event-based — the placement's
        # landing (not a timer) opens the gate on the next tick.
        g = self._orphaned_goal(idle=True, planned=False)   # closer no-op-settled, planner still in queue
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 0, "no nudge while the planner hasn't placed the turn's units")
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 0, "the hold persists across ticks, not a one-shot skip")
            self._orphaned_goal(idle=True, planned=True)    # the planner catches up (placements recorded)
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the placement landing opens the gate — the nudge fires")
            self.assertIn("romp-goal-id: " + g, sent[0][1])
        finally:
            restore()

    def test_auto_nudge_fires_for_every_working_top(self):
        # all of a session's WORKING top goals get nudged each stop — not just the first (the user
        # 2026-06-28) — and same-tick fires COALESCE into ONE bundled message (the user 2026-07-24)
        # instead of N separate status checks: both goal ids ride the one send.
        self._open_turn_transcript(ended=True); km._parse_cache.clear()
        lt = km._parse(str(self.tpath), SID, NOW)["turns"][-1]["id"]
        g1, g2 = SID + ":g1", SID + ":g2"
        def n(nid):
            return {"id": nid, "text": nid, "parentId": None, "nodeComplete": False,
                    "blocked": False, "cleared": False, "trail": [], "t": T0}
        self._goal_store({g1: n(g1), g2: n(g2)}, {g1: "working", g2: "working"}, last=g2, closed=[lt])
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            bodies = [b for (_n, b) in sent]
            self.assertEqual(len(sent), 1, "same-tick nudges bundle into ONE message, never N sends")
            self.assertTrue(any("romp-goal-id: " + g1 in b for b in bodies), "g1 nudged")
            self.assertTrue(any("romp-goal-id: " + g2 in b for b in bodies), "g2 nudged")
        finally:
            restore()

    def test_auto_nudge_does_not_paint_the_followed_up_chip(self):
        # an auto-nudge must NOT optimistic-followup the goal — the "Followed up"/re-checking chip is reserved
        # for the user's OWN follow-ups, not romp's auto-nudge (the user 2026-06-28).
        g = self._orphaned_goal(idle=True)
        km._set_auto_nudge(True)
        sent, fu_calls = [], []
        saved_send, saved_fu = km._tmux_send, jd.optimistic_followup
        km._tmux_send = lambda name, body, **kw: sent.append(body)
        jd.optimistic_followup = lambda sid, gid, **kw: fu_calls.append(gid)
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the nudge fired")
            self.assertEqual(fu_calls, [], "auto-nudge does NOT optimistic-followup → no Followed-up chip")
        finally:
            km._tmux_send, jd.optimistic_followup = saved_send, saved_fu

    def _drive_nudge_over(self, turn, last_state):
        # Drive one _auto_nudge_tick over a single controlled TURN + _last_state, exercising the REAL
        # _session_working / genuine-stop gate. Returns the captured sends. One orphaned working top goal.
        g = SID + ":gw"
        self._goal_store({g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g: "working"}, last=g, closed=[turn["id"]])
        km._set_auto_nudge(True)
        saved = (jd.parsed_session, km._last_state, km._alive_sessions, km._session_awaiting,
                 km._api_error, km._wait_for_graph, km._session_flag, list(km._downtime))
        km._downtime[:] = []                                         # no host-sleep interfering with _session_working
        jd.parsed_session = lambda sid, paths, now: {"turns": [turn]}
        km._last_state = lambda sid: last_state
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": str(self.tpath)}]
        km._session_awaiting = lambda *a, **k: None
        km._api_error = lambda p: None
        km._wait_for_graph = lambda now, sids: {}
        km._session_flag = lambda sid, flag: False
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, {SID: {"state": "working"}})
            return list(sent)
        finally:
            restore()
            (jd.parsed_session, km._last_state, km._alive_sessions, km._session_awaiting,
             km._api_error, km._wait_for_graph, km._session_flag, dt) = saved
            km._downtime[:] = dt

    def test_auto_nudge_skips_an_idle_led_but_resumed_turn(self):
        # THE judge_audit BUG (the user 2026-06-30): a session stalled (an idle span), then the user re-engaged
        # and it's ACTIVELY WORKING again — the earlier idle folds into the SAME turn (an idle-led turn that
        # RESUMED), so the idle atom sits in the MIDDLE with real work after it. The old _session_working keyed
        # on ANY idle atom → read it as not-working mid-work; the genuine-stop gate then raced on _last_state
        # (working stamped once at turn-START, so its time is BEFORE the turn's latest atom) and let the nudge
        # through — a spurious "status?" fired 31s into real work. An actively-working session must NOT be nudged.
        turn = {"id": SID + ":t1", "ended": False, "t": T0, "end": NOW,
                "atoms": [{"type": "idle", "t": T0, "end": T0 + 40},   # the stall, at the HEAD of the turn
                          {"type": "user", "t": T0 + 40},              # re-engaged
                          {"type": "assistant", "t": NOW}]}            # resumed work — the TAIL is live work
        sent = self._drive_nudge_over(turn, last_state=("working", T0 + 40))   # working stamped at turn-start (racy)
        self.assertEqual(sent, [], "idle-led-but-resumed (actively working) must NOT be nudged")

    def test_auto_nudge_still_fires_on_a_stale_stuck_working_state(self):
        # bugsdk2 (the user 2026-06-25) — the case the idle-led fix must NOT reopen: a turn genuinely ENDED, but
        # the state log stuck at 'working' (a kernel restart lost the post-turn 'waiting' write). That stale
        # working record sits BEFORE the turn end, so the genuine-stop gate must still let the nudge through.
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW,
                "atoms": [{"type": "user", "t": T0}, {"type": "assistant", "t": NOW}]}
        sent = self._drive_nudge_over(turn, last_state=("working", T0 - 60))   # stale 'working', BEFORE the turn end
        self.assertEqual(len(sent), 1, "a genuinely-ended turn with a stale-stuck 'working' state STILL gets nudged")
        self.assertIn("romp-goal-id: " + SID + ":gw", sent[0][1])

    def test_auto_nudge_does_not_re_arm_on_its_own_nudge_response(self):
        # THE runaway (the user 2026-07-01, track: count climbed to 82 at ~5s intervals, burning tokens). A
        # nudge injects a message; the agent's RESPONSE turn is opened by that romp-authored trigger. Re-arming
        # off it (the 2026-06-25 "keep nudging til resolved" rule) tight-looped. A romp-opened turn must NEVER
        # re-arm — a persistent stall is surfaced as blocked + a nudge-failed chip instead, not nudged forever.
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW, "trigger": "u1",
                "atoms": [{"type": "user", "uuid": "u1", "author": "romp", "t": T0},   # the nudge opened this turn
                          {"type": "assistant", "t": NOW}]}                            # response, still working
        sent = self._drive_nudge_over(turn, last_state=("waiting", NOW))
        self.assertEqual(sent, [], "a turn opened by our own nudge must NOT re-arm (kills the runaway loop)")

    def test_auto_nudge_sends_the_fork_text_for_open_agent_todos(self):
        # plans/stalled-open-todos-nudge.md: a stalled goal whose subtree holds an item the agent's OWN
        # to-do list still marks open gets the FORK nudge — "continue these, or tell me which are blocked
        # and what you need" — instead of the plain status check. Claude Code's to-do system has no
        # "blocked" state, so this is how the blocker gets said out loud (the planner's nudge-mode note
        # then applies it as a block). The record carries `stalled` so a failure floors the card.
        self._open_turn_transcript(ended=True); km._parse_cache.clear()
        lt = km._parse(str(self.tpath), SID, NOW)["turns"][-1]["id"]
        g, c = SID + ":gw", SID + ":gt"
        self._goal_store(
            {g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0},
             c: {"id": c, "text": "hook up the adapter", "parentId": g, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0,
                 "agentTask": {"key": "1", "status": "open", "raw": "pending"}, "agentBornOpen": True}},
            {g: "working"}, last=g, closed=[lt])
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the stalled goal is nudged")
            # the fork ask (both the flat and the hierarchical-enumeration form carry these sentences):
            # the continue-or-name-the-blocker fork (the user 2026-07-02), leading with permission to
            # continue instead of a per-item report (the user 2026-08-11)
            self.assertIn("Keep going on anything you can", sent[0][1],
                          "the FORK text, not the plain status check")
            self.assertIn("you don't need my go-ahead", sent[0][1],
                          "licenses continuing without checking in first")
            self.assertIn("tell me which one and exactly what you need from me", sent[0][1])
            self.assertIn("hook up the adapter", sent[0][1], "the open item is named in the quote")
            self.assertNotIn(km.AUTO_NUDGE_TEXT, sent[0][1])
            self.assertNotIn("stalled", km._auto_nudge_data()["nudged"][g],
                             "the record flavor flag was retired 2026-07-07 (the block verdict supersedes it)")
        finally:
            restore()

    def test_fork_nudge_enumerates_open_todos_under_a_flat_done_top(self):
        # track g9 (the user 2026-07-02): the closer flat-DONE'd + settled the umbrella while the agent's
        # own to-do list still holds OPEN items under it. _open_leaves used to prune the whole walk at the
        # nodeComplete top, so the fork nudge quoted only the goal title and never named the items — the
        # authoritative-open set now pierces that marker and the items are enumerated.
        top, c1, c2, d1 = SID + ":gw", SID + ":gt1", SID + ":gt2", SID + ":gd"
        self._goal_store(
            {top: {"id": top, "text": "land the plugin system", "parentId": None, "nodeComplete": True,
                   "settledDone": True, "blocked": False, "cleared": False, "trail": [], "t": T0},
             c1: {"id": c1, "text": "rewire the plugin to the new engine", "parentId": top,
                  "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": T0,
                  "agentTask": {"key": "1", "status": "open", "raw": "pending"}, "agentBornOpen": True},
             c2: {"id": c2, "text": "migrate stores and land the branch", "parentId": top,
                  "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": T0,
                  "agentTask": {"key": "2", "status": "open", "raw": "pending"}, "agentBornOpen": True},
             d1: {"id": d1, "text": "a genuinely done step", "parentId": top, "nodeComplete": True,
                  "blocked": False, "cleared": False, "trail": [], "t": T0}},
            {top: "working"}, last=top)
        body = km._followup_body(top, None, km.AUTO_NUDGE_STALLED_TEXT, injected=True, auto=True, stalled=True)
        self.assertIn("rewire the plugin to the new engine", body, "open to-do #1 is named")
        self.assertIn("migrate stores and land the branch", body, "open to-do #2 is named")
        self.assertNotIn("a genuinely done step", body, "a done step is not")
        self.assertIn("still open on your own to-do list", body, "the fork body rides the enumeration")
        self.assertIn("you don't need my go-ahead", body,
                      "and licenses continuing without a per-item report first (the user 2026-08-11)")

    def test_auto_nudge_stamps_failed_when_its_response_leaves_the_goal_stalled(self):
        # plans/stalled-open-todos-nudge.md: after the ONE nudge, the agent's response turn ends (judged —
        # the closer-settled gate has passed, and the closer runs last among the judge tiers) with the goal
        # STILL working → never re-nudged; the record is stamped `failed` so the feed shows the "nudge
        # failed" chip (and floors a fork-flavored one to needs-you). Event-based: the trigger is the
        # response turn's END, not a timer.
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW, "trigger": "u1",
                "atoms": [{"type": "user", "uuid": "u1", "author": "romp", "t": T0},   # our nudge opened it
                          {"type": "assistant", "t": NOW}]}
        km._write_auto_nudge({"enabled": True, "nudged": {
            SID + ":gw": {"count": 1, "lastTurnId": SID + ":t0"}}})
        sent = self._drive_nudge_over(turn, last_state=("waiting", NOW))
        self.assertEqual(sent, [], "no re-nudge off our own response")
        rec = km._auto_nudge_data()["nudged"][SID + ":gw"]
        self.assertTrue(rec.get("failed"), "the unresolved response stamps `failed`")
        self.assertNotIn("stalled", rec, "the flavor flag is retired (2026-07-07)")

    def test_auto_nudge_stamps_failed_on_a_folded_response_too(self):
        # the SDK folds the nudge-response into the SAME turn id (no new turn) — the lastTurnId==lt_id arm
        # of the re-arm gate must stamp `failed` exactly like the romp-opened-turn arm.
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW,
                "atoms": [{"type": "user", "t": T0}, {"type": "assistant", "t": NOW}]}
        km._write_auto_nudge({"enabled": True, "nudged": {
            SID + ":gw": {"count": 1, "lastTurnId": SID + ":t1"}}})
        sent = self._drive_nudge_over(turn, last_state=("waiting", NOW))
        self.assertEqual(sent, [], "same turn id → no re-fire")
        self.assertTrue(km._auto_nudge_data()["nudged"][SID + ":gw"].get("failed"))

    def test_auto_nudge_failed_stamp_waits_for_the_response_to_reach_the_parse(self):
        # the network g1 manufactured block (the user 2026-07-19): the fire and the failed-stamp are
        # separate ticks, and 16s after the fire the parse still ENDED AT THE ARMING TURN — the injected
        # nudge (let alone the reply "All done — nothing is blocked on you") hadn't reached it — yet the
        # no-visible-segment fallback stamped "the response didn't resolve this" against a response that
        # resolved everything, manufacturing a block on a finished card. The response's ARRIVAL in the
        # parse is the event to wait for: armAtoms (the arming turn's atom count at fire time) unchanged
        # AND no newer turn = nothing has happened since the fire → skip, re-check next tick.
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW,
                "atoms": [{"type": "user", "t": T0}, {"type": "assistant", "t": T0 + 10}]}
        km._write_auto_nudge({"enabled": True, "nudged": {
            SID + ":gw": {"count": 1, "lastTurnId": SID + ":t1", "armAtoms": 2}}})
        sent = self._drive_nudge_over(turn, last_state=("waiting", NOW))
        self.assertEqual(sent, [], "no re-nudge while armed on the same turn")
        self.assertFalse(km._auto_nudge_data()["nudged"][SID + ":gw"].get("failed"),
                         "an unchanged arming turn means the response is NOT in the parse — no failed stamp")

    def test_auto_nudge_failed_stamp_lands_once_the_folded_response_grows_the_turn(self):
        # the same record once the fold DOES land: the arming turn grown past armAtoms is the arrival
        # event, and the stamp proceeds — the designed fold path keeps working with the new gate.
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW,
                "atoms": [{"type": "user", "t": T0}, {"type": "assistant", "t": T0 + 10},
                          {"type": "user", "t": T0 + 20}, {"type": "assistant", "t": NOW}]}
        km._write_auto_nudge({"enabled": True, "nudged": {
            SID + ":gw": {"count": 1, "lastTurnId": SID + ":t1", "armAtoms": 2}}})
        sent = self._drive_nudge_over(turn, last_state=("waiting", NOW))
        self.assertEqual(sent, [], "same turn id → no re-fire")
        self.assertTrue(km._auto_nudge_data()["nudged"][SID + ":gw"].get("failed"),
                        "atom growth past armAtoms = the response arrived — the stamp lands")

    def test_auto_nudge_fire_records_the_arming_turns_atom_count(self):
        # the arrival gate compares against fire-time state, so the fire must record it
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW,
                "atoms": [{"type": "user", "t": T0}, {"type": "assistant", "t": NOW}]}
        sent = self._drive_nudge_over(turn, last_state=("waiting", NOW))
        self.assertEqual(len(sent), 1, "fresh goal on a genuine ended turn fires")
        self.assertEqual(km._auto_nudge_data()["nudged"][SID + ":gw"].get("armAtoms"), 2,
                         "the fire stamps the arming turn's atom count")

    def test_auto_nudge_skips_a_session_with_an_armed_bare_rollback(self):
        """The network g14 resurrection (the user 2026-07-20): a bare-rollback delete writes NOTHING to
        the transcript, so the parse still shows the deleted turn and the goals minted from it; a nudge
        fired in that window quotes the rolled-back content back into the thread AND spends the branch
        cut as the new branch's first turn. While the backend holds an armed, unconsumed cut
        (pending_cut), the tick must skip the session; once the cut is spent, nudging resumes."""
        turn = {"id": SID + ":t1", "ended": True, "t": T0, "end": NOW,
                "atoms": [{"type": "user", "t": T0}, {"type": "assistant", "t": NOW}]}
        cut = {"v": "11111111-2222-3333-4444-555555555555"}
        fired = []                 # backend-owned sids fire via backend.send, not _tmux_send
        fake = type("B", (), {"pending_cut": staticmethod(lambda sid: cut["v"]),
                              "pending_queued": staticmethod(lambda sid: []),
                              "send": staticmethod(lambda sid, body: fired.append(body))})()
        saved_bf = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: fake)
        try:
            sent = self._drive_nudge_over(turn, last_state=("waiting", NOW))
            self.assertEqual((sent, fired), ([], []),
                             "an armed, unconsumed bare rollback holds the nudge — "
                             "the parse still shows the deleted turn")
            cut["v"] = ""          # the cut was spent: a record landed on the new branch
            self._drive_nudge_over(turn, last_state=("waiting", NOW))
            self.assertEqual(len(fired), 1, "cut consumed → the nudge flows again")
        finally:
            km.Sessions.backend_for = saved_bf

    def test_a_fresh_fire_resets_the_failed_flag(self):
        # a NEW genuine stall re-arms and fires; the fresh record must drop the previous episode's `failed`
        # (and its fork flavor) so the chip/floor reflect THIS episode, not a stale one.
        base = [uline(T0, "a1", "u1", ps="typed"), aline(T0 + 10, "d1", "a1", "u1", stop="end_turn")]
        g = self._stall_transcript(base)
        km._write_auto_nudge({"enabled": True, "nudged": {
            g: {"count": 3, "lastTurnId": SID + ":told", "failed": True, "stalled": True}}})
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the new genuine stall fires")
            rec = km._auto_nudge_data()["nudged"][g]
            self.assertNotIn("failed", rec, "a fresh fire opens a fresh episode — failed resets")
            self.assertNotIn("stalled", rec, "no open to-dos this time → regular flavor")
            self.assertEqual(rec["count"], 4, "the escalation count still climbs across episodes")
        finally:
            restore()

    def test_nudge_failed_flag_rides_the_working_card(self):
        # A REGULAR-flavor failed nudge: the card stays in Working (no floor without the fork flavor),
        # carrying nudgeFailed for the chip — glanceable, not an interrupt.
        g = SID + ":gw"
        self._goal_store({g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g: "working"}, last=g)
        km._write_auto_nudge({"enabled": True, "nudged": {g: {"count": 1, "lastTurnId": "x", "failed": True}}})
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertTrue(card["nudgeFailed"], "the card carries the nudge-failed chip flag")
        self.assertEqual(card["column"], "working", "a regular-flavor failure does NOT floor to needs-you")
        self.assertIsNone(card["blocked"], "no stalled badge without the fork flavor")

    def test_failed_fork_nudge_floors_the_card_to_needs_input(self):
        # plans/stalled-open-todos-nudge.md: fork flavor + to-dos STILL open + the response didn't resolve
        # it → the human is the bottleneck; the card floors to needs-you with the "stalled" badge (and is
        # never re-nudged — escalation instead of the loop).
        g, c = SID + ":gw", SID + ":gt"
        self._goal_store(
            {g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0},
             c: {"id": c, "text": "hook up the adapter", "parentId": g, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0,
                 "agentTask": {"key": "1", "status": "open", "raw": "pending"}, "agentBornOpen": True}},
            {g: "working"}, last=g)
        km._write_auto_nudge({"enabled": True, "nudged": {
            g: {"count": 1, "lastTurnId": "x"}}})
        km._mark_nudge_failed(g)   # 2026-07-07: the failure records a real BLOCK verdict (no read-side floor)
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["column"], "needs_input", "the failed nudge blocks the card via the normal ladder")
        self.assertTrue(card["nudgeFailed"])
        ev = [e for e in km.jd.load_goals(SID)["nodes"][g]["log"] if e["kind"] == "block"]
        self.assertEqual([e["src"] for e in ev], ["nudge"], "the block is a diary verdict, src nudge")

    def test_stalled_floor_self_heals_when_the_todos_are_crossed_off(self):
        # the floor requires open to-dos AT DISPLAY TIME: the instant the agent crosses the items off, the
        # card returns to Working on its own (event-based — the sync flips agentTask to done; no timer).
        g, c = SID + ":gw", SID + ":gt"
        self._goal_store(
            {g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0},
             c: {"id": c, "text": "hook up the adapter", "parentId": g, "nodeComplete": True,
                 "blocked": False, "cleared": False, "trail": [], "t": T0, "agentDone": True,
                 "agentTask": {"key": "1", "status": "done", "raw": "completed"}, "agentBornOpen": True}},
            {g: "working"}, last=g)
        km._write_auto_nudge({"enabled": True, "nudged": {
            g: {"count": 1, "lastTurnId": "x"}}})
        km._mark_nudge_failed(g)   # records the block verdict (ev = wall clock, its real stamp)...
        st = km.jd.load_goals(SID)
        # ...then the agent finishes AFTER it — the done's evidence must be newer than the block's
        # wall-clock stamp, as it is live (a block as new as the completion survives instead — the
        # 2026-07-15 fresh-block rule; NOW+5 here would invert the fixture's timeline)
        km.jd.record_verdict(st, st["nodes"][g], "agent", "done", int(time.time()) + 5,
                             why="the agent crossed off the last item")
        km.jd.rollup_status(st, True)
        km.jd.save_goals(SID, st)
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["column"], "completed", "the agent finishing outranks the stalled block")
        self.assertIsNone(card["blocked"])

    def test_nudge_failed_is_suppressed_once_the_goal_resolves(self):
        # the planner later blocks the goal off the fork response (or a follow-up) → the block story takes
        # over: the stale failed flag must not keep painting the chip on a card that has a real blockWhy.
        g = SID + ":gw"
        self._goal_store({g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                              "blocked": True, "blockWhy": "needs the staging credentials",
                              "cleared": False, "trail": [], "t": T0, "mt": T0 + 10}},
                         {g: "blocked"}, last=g)
        km._write_auto_nudge({"enabled": True, "nudged": {
            g: {"count": 1, "lastTurnId": "x", "failed": True, "stalled": True}}})
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertFalse(card["nudgeFailed"], "a resolved (blocked) goal drops the nudge-failed chip")
        self.assertEqual(card["column"], "needs_input", "the ordinary soft-block path files it")
        self.assertEqual(km.jd.load_goals(SID)["nodes"][g]["blockWhy"], "needs the staging credentials")

    def test_nudge_failed_chip_retires_on_user_action_even_without_its_block_row(self):
        # The g52 shape (2026-07-16): _mark_nudge_failed stamped `failed` in auto-nudge.json but its
        # paired diary block row was erased by a judge pass's stale save (the pass held the store
        # across its model call). The retire path keyed exclusively on that row, so the chip said
        # "waiting on you" straight through the user's own follow-up. A legacy record carrying
        # neither the row nor failedAt retires on any USER diary event at all — of the two failure
        # modes, a false "waiting on you" is the one that breaks flow.
        g = SID + ":gw"
        self._goal_store({g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": T0,
                              "log": [{"ev_t": T0 + 60, "src": "user", "kind": "reopen",
                                       "why": "reopened (optimistic)", "at": T0 + 60}]}},
                         {g: "working"}, last=g)
        km._write_auto_nudge({"enabled": True, "nudged": {g: {"count": 1, "lastTurnId": "x", "failed": True}}})
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertFalse(card["nudgeFailed"], "the user's follow-up ended 'waiting on you' — row or no row")

    def test_nudge_failed_chip_keys_on_the_failure_stamp_when_the_row_is_missing(self):
        # A NEW-format record carries failedAt (stamped with `failed`): the chip shows while nothing
        # real happened after the failure, and retires on the first real-actor event after it — the
        # same event rule the block row anchors, riding the one write the failure path is actually
        # guaranteed to leave.
        g = SID + ":gw"
        mint = {"ev_t": T0 + 10, "src": "planner", "kind": "reopen", "why": "reopened (nudge)", "at": T0 + 10}
        self._goal_store({g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": T0, "log": [mint]}},
                         {g: "working"}, last=g)
        km._write_auto_nudge({"enabled": True, "nudged": {
            g: {"count": 1, "lastTurnId": "x", "failed": True, "failedAt": T0 + 50}}})
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertTrue(card["nudgeFailed"], "only pre-failure history → the failure is still the story")
        st = km.jd.load_goals(SID)
        km.jd.record_verdict(st, st["nodes"][g], "user", "reopen", T0 + 100, why="followed up")
        km.jd.save_goals(SID, st)
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertFalse(card["nudgeFailed"], "a real-actor event after failedAt retires the chip")

    def test_a_failing_session_does_not_abort_the_nudge_tick_for_the_fleet(self):
        # 2026-07-16: a TypeError in one session's _session_awaiting aborted the WHOLE tick — 1333
        # consecutive ticks over two days — so every session after the bad one in the iteration
        # silently lost its nudges. The tick isolates per session now (the failure still logs).
        km._write_auto_nudge({"enabled": True, "nudged": {}})
        seen = []

        def boom(s, now, tmux, nudged, waitfor, alive_ids=None):
            if s["sid"] == "bad-session":
                raise TypeError("%d format: a real number is required, not list")
            seen.append(s["sid"])
            return False
        orig_one, orig_alive = km._auto_nudge_session, km._alive_sessions
        km._auto_nudge_session = boom
        km._alive_sessions = lambda now, tmux: [{"sid": "bad-session", "path": "x"},
                                                {"sid": "good-session", "path": "y"}]
        try:
            km._auto_nudge_tick(NOW, {})
        finally:
            km._auto_nudge_session, km._alive_sessions = orig_one, orig_alive
        self.assertEqual(seen, ["good-session"], "the session after the failing one still gets its pass")

    def _stall_transcript(self, recs):
        # write a transcript, clear the parse cache, and (re)create the working goal with ALL its turn ids
        # marked closer-classified (so the closer-gate always passes for the latest). Returns the goal id.
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n"); km._parse_cache.clear()
        ids = [t["id"] for t in km._parse(str(self.tpath), SID, NOW)["turns"]]
        g = SID + ":gw"
        self._goal_store({g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g: "working"}, last=g, closed=ids)
        return g

    def test_auto_nudge_re_arms_on_a_new_genuine_stall_turn(self):
        # a NEW genuine ended turn that leaves the goal working re-arms the nudge, the total count climbing each
        # fire (the user 2026-06-22, 2026-06-26).
        base = [uline(T0, "a1", "u1", ps="typed"), aline(T0 + 10, "d1", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "a2", "u2", "a1", ps="typed"), aline(T0 + 110, "d2", "a2", "u2", stop="end_turn")]
        g = self._stall_transcript(base)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "first stall → nudged")
            self.assertEqual(km._auto_nudge_data()["nudged"][g]["count"], 1)
            self._stall_transcript(base + [uline(T0 + 200, "a3", "u3", "a2", ps="typed"),
                                           aline(T0 + 210, "d3", "a3", "u3", stop="end_turn")])   # NEW genuine turn
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 2, "a new genuine stall turn re-arms the nudge")
            self.assertEqual(km._auto_nudge_data()["nudged"][g]["count"], 2, "total count climbs each fire")
        finally:
            restore()

    def test_auto_nudge_skips_a_mid_turn_lull_per_the_state_log(self):
        # the user 2026-06-25 (obsidian): during a long tool lull the event model can momentarily read "not
        # working" (a STALE parse showing an old turn ended) while the AUTHORITATIVE state log still says
        # 'working' for a NEWER turn the parse hasn't caught up to. A nudge fired then is MID-TURN and poisons
        # the once-per-turn re-arm so the real post-stop stall never gets nudged. Gate: skip while the latest
        # progressing state record is AT/AFTER the parsed turn's end (T0+120) — it's newer than the parse, so
        # the session really is still going. (the user 2026-06-29: the discriminator is the record's TIME.)
        g = self._orphaned_goal(idle=True)                       # parsed turn ends at T0+120
        km._set_auto_nudge(True)
        sp = jd.STATE / "states" / (SID + ".jsonl")
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"t": T0 + 300, "state": "working"}) + "\n")   # working AFTER the parsed end → still going
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 0, "no nudge: the 'working' record is newer than the parsed turn end")
            sp.write_text(json.dumps({"t": T0 + 301, "state": "waiting"}) + "\n")   # genuine stop now
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "once genuinely stopped (state log 'waiting'), the nudge fires")
        finally:
            restore()
            try:
                sp.unlink()
            except OSError:
                pass

    def test_auto_nudge_fires_when_a_finished_turn_left_a_stale_working_record(self):
        # the user 2026-06-29 (bugsdk2): a turn that genuinely ENDED (transcript end_turn at T0+120) but whose
        # post-turn 'waiting' write was LOST — e.g. a kernel restart killed the SDK ResultMessage handler — left
        # the state log stuck at 'working' from BEFORE the turn end (T0+50). The session is really stopped with a
        # working card, so it MUST be nudged; the stale 'working' must not block it forever. (The old value-only
        # gate skipped it permanently — the regression from dropping the restart-heal.)
        g = self._orphaned_goal(idle=True)                       # parsed turn ends at T0+120
        km._set_auto_nudge(True)
        sp = jd.STATE / "states" / (SID + ".jsonl")
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"t": T0 + 50, "state": "working"}) + "\n")   # stale: BEFORE the turn end, no later 'waiting'
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "a finished turn with a stale pre-end 'working' record still gets nudged")
            self.assertIn("romp-goal-id: " + g, sent[0][1], "the nudge targets the orphaned working goal")
        finally:
            restore()
            try:
                sp.unlink()
            except OSError:
                pass

    def test_auto_nudge_does_not_re_arm_on_a_nudge_response_that_stays_working(self):
        # the user 2026-07-01 — REVERSES the earlier 2026-06-25 keep-nudging-til-resolved rule, which tight-looped
        # (track: count 82 at ~5s intervals, burning tokens). The agent's OWN nudge-response turn, even if it
        # ends still-working, must NOT re-arm: its trigger is romp-authored (_turn_romp_injected). A stall that
        # persists WITHOUT genuine new work is surfaced as blocked + a nudge-failed chip, not nudged forever.
        base = [uline(T0, "a1", "u1", ps="typed"), aline(T0 + 10, "d1", "a1", "u1", stop="end_turn")]
        g = self._stall_transcript(base)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the genuine stall is nudged once")
            nudge = "Status?\n\n<!-- romp-injected --><!-- romp-goal-id: %s -->" % g   # romp-authored turn
            self._stall_transcript(base + [uline(T0 + 100, nudge, "u2", "a1", ps="typed"),
                                           aline(T0 + 110, "still working", "a2", "u2", stop="end_turn")])
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the nudge-RESPONSE turn does NOT re-arm — kills the runaway (the user 2026-07-01)")
        finally:
            restore()

    def test_auto_nudge_skips_a_session_waiting_on_a_live_peer(self):
        # the human's philosophy (2026-06-22): waiting on a live peer isn't a stall → don't nudge. The gate
        # reads _wait_for_graph; any entry for the sid (peer-wait or deadlock cycle) suppresses the nudge.
        self._orphaned_goal(idle=True)
        km._set_auto_nudge(True)
        saved = km._wait_for_graph
        km._wait_for_graph = lambda now, alive: {SID: {"peerSid": "peerY", "name": "peerY",
                                                       "color": None, "inCycle": False, "since": NOW}}
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "a session waiting on a live peer is held, not nudged")
            km._wait_for_graph = lambda now, alive: {}            # no longer waiting → the genuine stall is nudged
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "once the wait clears, the genuine stall is nudged")
        finally:
            restore(); km._wait_for_graph = saved

    def test_auto_nudge_skips_a_session_muted_from_the_feed(self):
        # the user: hideFromFeed (the lane feed checkbox OFF) means "I don't want this agent's feed features" —
        # and a nudge IS a feed feature, so a feed-muted session must not be auto-nudged.
        self._orphaned_goal(idle=True)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._set_session_flag(SID, "hideFromFeed", True); km._flags_cache.clear()
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "a session muted from the feed is not auto-nudged")
            km._set_session_flag(SID, "hideFromFeed", False); km._flags_cache.clear()
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "un-mute does NOT re-nudge: muting VIEW-CLEARED the goal, so it stays sealed")
        finally:
            restore()

    def test_auto_nudge_skips_a_session_awaiting_dispatched_work(self):
        # the user 2026-06-22: a session paused on work it dispatched/delegated is in flight, not stalled →
        # don't nudge. The gate reads _session_awaiting (the SDK states overlay / transcript bg-tool stopgap).
        self._orphaned_goal(idle=True)
        km._set_auto_nudge(True)
        saved = km._session_awaiting
        km._session_awaiting = lambda sid, path, idle, stamp=False: {"kind": "agents", "why": "Waiting on its background agents."}
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "an awaiting session is held, not nudged")
            km._session_awaiting = lambda sid, path, idle, stamp=False: None   # no longer awaiting → the genuine stall is nudged
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "once the wait clears, the genuine stall is nudged")
        finally:
            restore(); km._session_awaiting = saved

    def test_auto_nudge_logs_an_event_for_the_timeline(self):
        # each fire appends {sid,gid,t,count} to STATE/nudge-events.jsonl for business's ⚡ timeline marker.
        g = self._orphaned_goal(idle=True)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            ev = [json.loads(l) for l in (jd.STATE / "nudge-events.jsonl").read_text().splitlines()]
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["gid"], g)
            self.assertEqual(ev[0]["count"], 1)
            self.assertEqual(ev[0]["sid"], SID)
            self.assertIn("t", ev[0])
        finally:
            restore()

    def test_auto_nudge_is_a_noop_when_off(self):
        self._orphaned_goal(idle=True)
        km._set_auto_nudge(False)                                  # explicitly turned off
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "explicitly off → no nudges")
        finally:
            restore()

    def test_auto_nudge_skips_a_session_still_working(self):
        self._orphaned_goal(idle=False)                           # turn still OPEN → actively working
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "an actively-working session isn't orphaned")
        finally:
            restore()

    def test_auto_nudge_waits_for_the_closer_verdict(self):
        # the user 2026-06-21: don't nudge until the CLOSER has classified the latest turn. A turn that ENDED
        # by asking you a question is momentarily "working" before the closer marks its goal blocked; nudging
        # the agent there is pointless (it's waiting on YOU). closer_done=False → the latest turn is NOT in
        # closedTurns → the gate holds the nudge until the closer has had its say.
        self._orphaned_goal(idle=True, closer_done=False)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "no nudge until the closer has processed the latest turn")
        finally:
            restore()

    def test_auto_nudge_skips_an_awaiting_session(self):
        self._orphaned_goal(idle=True)
        km._set_auto_nudge(True)
        km._tmux_sessions = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "a session awaiting your approval is not orphaned")
        finally:
            restore()

    def test_auto_nudge_storage_round_trip(self):
        self.assertTrue(km._auto_nudge_on(), "on by default")
        km._set_auto_nudge(True); self.assertTrue(km._auto_nudge_on())
        km._mark_auto_nudged(SID + ":g1", "turn-A", 1)
        self.assertEqual(km._auto_nudge_data()["nudged"][SID + ":g1"],
                         {"count": 1, "lastTurnId": "turn-A"})
        km._set_auto_nudge(False)
        self.assertFalse(km._auto_nudge_on())
        self.assertEqual(km._auto_nudge_data()["nudged"][SID + ":g1"]["lastTurnId"], "turn-A",
                         "toggling off keeps the per-goal re-arm record")
        km._mark_auto_nudged(SID + ":g1", "turn-B", 2)   # NEW turn → count climbs, turn advances
        self.assertEqual(km._auto_nudge_data()["nudged"][SID + ":g1"],
                         {"count": 2, "lastTurnId": "turn-B"})

    def test_auto_nudge_turn_id_comes_from_the_closer_states_aware_parse(self):
        # the user 2026-06-22 (obsidian): the closer parses WITH states (idle atoms) and writes THAT turn id to
        # closedTurns. A states-LESS parse gives an idle-LED turn a different id (a synthesized leading idle
        # opens it, vs the human prompt), so the tick's closer-gate never matched and the nudge was blocked
        # forever. The tick must take its turn id from jd.parsed_session — the SAME (states-aware) source.
        g = self._orphaned_goal(idle=True)
        real = jd.parsed_session
        jd.parsed_session = lambda fsid, files, now: {"turns": [
            {"id": "CLOSER-STATES-AWARE-ID", "ended": True, "trigger": None, "t": T0, "end": T0,
             "atoms": [{"type": "user", "author": "human"}]}]}
        self._goal_store({g: {"id": g, "text": "x", "parentId": None, "nodeComplete": False, "blocked": False,
                              "cleared": False, "trail": [], "t": T0}}, {g: "working"}, last=g,
                         closed=["CLOSER-STATES-AWARE-ID"])   # what the closer wrote (states-aware id)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the tick takes its turn id from the closer's parse → matches closedTurns → fires")
        finally:
            restore(); jd.parsed_session = real

    def test_auto_nudge_data_drops_the_vestigial_done_list(self):
        # the old one-shot 'done' list (pre-re-arm) is no longer read or written; drop it on load so it's
        # cleaned from the file on the next write — no stale divergence from goal state (via business 2026-06-22).
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "done": [SID + ":gOld"],
             "nudged": {SID + ":g1": {"count": 1, "lastTurnId": "t"}}}))
        km._autonudge_cache.clear()
        self.assertNotIn("done", km._auto_nudge_data(), "the vestigial done list is dropped on load")
        self.assertIn(SID + ":g1", km._auto_nudge_data()["nudged"], "the nudged dict is preserved")
        km._mark_auto_nudged(SID + ":g2", "t2", 1)                    # any write
        on_disk = json.loads((jd.STATE / "auto-nudge.json").read_text())
        self.assertNotIn("done", on_disk, "the next write cleans 'done' from the file")

    def test_provisional_persists_past_turn_end_until_the_planner_places_it(self):
        # The user 2026-06-29: the placeholder must NOT vanish at turn-end. The planner classifies the held
        # segment a pass or two LATER (often an LLM call), so dropping it at turn-end left the feed showing
        # NOTHING in the gap ("serious delay between the provisional disappearing and the real cards
        # appearing"). It now persists until the planner PLACES the segment, so placeholder → real card swap
        # in one build. Keyed on the placement EVENT, not the open turn.
        self._open_turn_transcript(ended=True)         # ENDED turn; its 2nd-turn ask isn't placed yet (placements: {})
        g1 = SID + ":g1"
        self._goal_store(
            {g1: {"id": g1, "text": "first ask", "parentId": None, "nodeComplete": True,
                  "blocked": False, "cleared": False, "trail": [], "t": T0}},
            {g1: "completed"}, last=g1, planned=False)   # unplaced by premise: the provisional exists because the planner hasn't placed
        self._working_tmux()
        prov = [a for a in km.build_feed(NOW)["asks"] if a.get("provisional")]
        self.assertEqual(len(prov), 1, "an ENDED but unplaced ask still shows the placeholder — no gap before the real card")
        # Once the planner PLACES that segment (its key lands in placements — set even for a skip), the
        # placeholder drops: the real card / skip is on the board.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        held = em.segments(session["turns"][-1])[-1]
        store = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        store["placements"][held["id"]] = None
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        self.assertFalse([a for a in km.build_feed(NOW)["asks"] if a.get("provisional")],
                         "once the planner places the held segment, the placeholder is gone (replaced by the real card)")

    def test_no_provisional_card_when_a_working_card_already_covers_the_session(self):
        # The placeholder fills the gap only when NOTHING already shows the session working. An open top
        # goal (status working) means a real working card exists → no duplicate placeholder.
        self._open_turn_transcript(ended=False)
        g1, g2 = SID + ":g1", SID + ":g2"
        self._goal_store(
            {g1: {"id": g1, "text": "first ask", "parentId": None, "nodeComplete": True,
                  "blocked": False, "cleared": False, "trail": [], "t": T0},
             g2: {"id": g2, "text": "ongoing work", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "trail": [], "t": T0 + 50}},
            {g1: "completed", g2: "working"}, last=g2)
        self._working_tmux()
        asks = km.build_feed(NOW)["asks"]
        self.assertFalse([a for a in asks if a.get("provisional")],
                         "an existing working card suppresses the placeholder")
        self.assertTrue(any(a["itemId"] == g2 and a["column"] == "working" for a in asks))

    def test_feed_tree_node_carries_anchor_uuid_for_id_deeplink(self):
        # anchorUuid = the EXACT turn uuid for a node's anchor segment (where it resolved / was minted),
        # so a card click deep-links BY ID, not by nearest-time-heuristic. (the user 2026-06-17.)
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = next(s for t in session["turns"] for s in em.segments(t))
        w, r = km._seg_anchors(seg["atoms"])
        want = r or w                                  # reply uuid preferred (readable assistant text), else work
        self.assertTrue(want, "fixture has assistant atoms → a real anchor uuid")
        nid = SID + ":g1"                              # a DONE node → anchors on its (last) trail segment
        store = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        store["nodes"][nid]["trail"] = [seg["id"]]
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        node = next(n for a in km.build_feed(NOW)["asks"] for n in a["tree"] if n["id"] == nid)
        self.assertEqual(node["anchorUuid"], want, "node deep-links to the exact segment uuid, not a timestamp")

    def test_seg_anchors_skips_api_error_reply(self):
        # The user's bug (2026-06-18): a done goal's deep-link jumped to an 'API Error: …' line.
        # Claude Code records a failed turn as an assistant TEXT block (isApiErrorMessage → em tags
        # isApiError), so it carries text and WOULD win the reply anchor over the real reply.
        # _seg_anchors must skip it. End-to-end: transcript → em.segments → _seg_anchors.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in [
                uline(T0, "do the thing", "u1", ps="typed"),
                aline(T0 + 20, "here is the real reply", "a1", "u1", stop="end_turn"),
                apierr_line(T0 + 40, "aerr", "a1"),               # trailing API error = last text-carrying atom
            ]) + "\n")
            session = em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], now=NOW)
        seg = next(s for t in session["turns"] for s in em.segments(t))
        work, reply = km._seg_anchors(seg["atoms"])
        self.assertEqual(reply, "a1", "reply anchor is the real reply, NOT the trailing API-error line")
        self.assertEqual(work, "a1", "work anchor skips the error too → the real first assistant atom")

    def test_seg_anchors_none_when_turn_is_only_an_api_error(self):
        # A turn that produced ONLY an API error has no reply to jump to → (None, None); the feed/
        # timeline then fall back honestly rather than deep-linking to the error line. (2026-06-18.)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in [
                uline(T0, "do the thing", "u1", ps="typed"),
                apierr_line(T0 + 20, "aerr", "u1"),
            ]) + "\n")
            session = em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], now=NOW)
        seg = next(s for t in session["turns"] for s in em.segments(t))
        self.assertEqual(km._seg_anchors(seg["atoms"]), (None, None))

    def test_feed_tree_node_carries_mt_for_deeplink(self):
        # Every tree node carries mt = last-modified (the user 2026-06-16): a blocked/done node
        # deep-links to WHERE IT RESOLVED (the segment the planner applied the block/done op), not
        # where it was minted, so the feed-card click lands on that assistant action. Never-modified
        # nodes (open work, derived done) fall back to t.
        top, blk, dn, op = (SID + ":top", SID + ":blk", SID + ":dn", SID + ":op")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 4, "lastNode": None,
            "nodes": {
                top: gn(top, "the goal", None, mt=T0),
                blk: gn(blk, "a blocked step", top, blocked=True, mt=T0 + 30),    # resolved 30s after mint
                dn:  gn(dn, "a finished step", top, nodeComplete=True, mt=T0 + 60),
                op:  gn(op, "an open step", top),                                 # never modified → no mt key
            },
            "placements": {}, "status": {top: "blocked"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        nodes = {n["text"]: n for n in card["tree"]}
        self.assertEqual(nodes["a blocked step"]["mt"], T0 + 30, "blocked node deep-links to its block segment")
        self.assertEqual(nodes["a finished step"]["mt"], T0 + 60, "done node deep-links to its done segment")
        self.assertEqual(nodes["an open step"]["mt"], T0, "a never-modified node falls back to t")

    def test_ledger_highlight_and_arrow_mark_the_same_node_the_cursor(self):
        # ONE "here" marker (the user 2026-06-17): the highlight (current) and the → arrow (recent) now mark
        # the SAME node — the working cursor (lastNode) — NOT a separately-computed freshest-mt node. The two
        # used to diverge (highlight = the lastNode pointer; arrow = max mt). The tree ORDERING is still by mt
        # (freshest branch first); only the marker is unified onto the cursor.
        told, tnew, cnew = (SID + ":told", SID + ":tnew", SID + ":cnew")
        tpr, cpr = (SID + ":tpr", SID + ":cpr")
        def gn(nid, text, parent, done, mt):
            return {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                    "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": mt}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            # the cursor sits on the OLDER top, NOT the freshest-mt leaf — so the two would diverge if unfixed
            "rompUuid": SID, "seq": 5, "lastNode": told,
            "nodes": {told: gn(told, "older top", None, False, 100),
                      tnew: gn(tnew, "fresher top", None, True, 200),
                      cnew: gn(cnew, "freshly done leaf", tnew, True, 300),   # the freshest-mt node
                      tpr: gn(tpr, "pruned top", None, True, 50), cpr: gn(cpr, "pruned child", tpr, True, 60)},
            "placements": {}, "status": {}}))
        tree = km.build_session(SID, NOW)["ledger"]["tree"]
        texts = [n["text"] for n in tree]
        self.assertLess(texts.index("fresher top"), texts.index("older top"), "freshest top goal still sorts first (ordering unchanged)")
        byid = {n["text"]: n for n in tree}
        # ONE "here" marker: the highlight (current) marks the cursor; the separate `recent` arrow flag
        # was collapsed onto it long ago and the redundant key stopped shipping (2026-07-07 payload audit)
        self.assertTrue(byid["older top"]["current"], "the cursor carries the highlight")
        self.assertNotIn("recent", byid["older top"], "the redundant arrow flag no longer ships")
        self.assertFalse(byid["freshly done leaf"]["current"], "the freshest-mt node is NOT the marker")
        # onpath follows the cursor; an off-cursor branch is off-path (render folds it) but still EMITTED
        self.assertTrue(byid["older top"]["onpath"])
        self.assertFalse(byid["freshly done leaf"]["onpath"], "the freshest leaf is off the cursor's expand path now")
        self.assertIn("pruned child", byid, "the full tree is still emitted")

    def test_ledger_tree_shows_cleared_nodes_faded(self):
        # A cleared (dismissed) node is emitted and flagged `cleared` so the render fades + strikes it
        # (the user 2026-06-16: shown, not hidden). Since 2026-07-26 it is NOT counted done — the box
        # means done, and only done; this one was dismissed unfinished.
        top, clr = (SID + ":top", SID + ":clr")
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": None,
            "nodes": {top: {"id": top, "text": "live top", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": T0},
                      clr: {"id": clr, "text": "dismissed step", "parentId": top, "nodeComplete": False,
                            "blocked": False, "cleared": True, "trail": [], "t": T0}},
            "placements": {}, "status": {}}))
        byid = {n["text"]: n for n in km.build_session(SID, NOW)["ledger"]["tree"]}
        self.assertIn("dismissed step", byid, "a cleared node is shown now (not hidden)")
        self.assertTrue(byid["dismissed step"]["cleared"])
        self.assertFalse(byid["dismissed step"]["done"], "dismissed-unfinished: the box stays unchecked")
        self.assertFalse(byid["dismissed step"]["derived"], "cleared is its own flag, not derived")

    def test_ledger_tree_rolls_down_derived_done(self):
        # done rolls DOWN as well as up (the user 2026-06-16): a child under a done parent is derived-done
        # (a dimmed ✓), not ○ — matching the feed's ask-tree flatten() so the two views agree.
        top, child, gc = (SID + ":top", SID + ":child", SID + ":gc")
        def gn(nid, text, parent, done=False):
            return {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                    "blocked": False, "cleared": False, "trail": [], "t": T0}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 3, "lastNode": None,
            "nodes": {top: gn(top, "done top", None, done=True),
                      child: gn(child, "open child", top),               # itself not complete
                      gc: gn(gc, "open grandchild", child)},             # nor is its child
            "placements": {}, "status": {}}))
        byid = {n["text"]: n for n in km.build_session(SID, NOW)["ledger"]["tree"]}
        self.assertTrue(byid["done top"]["done"] and not byid["done top"]["derived"], "explicit done top")
        self.assertTrue(byid["open child"]["done"], "a child under a done top is done (roll-down)")
        self.assertTrue(byid["open child"]["derived"], "and it's DERIVED (dimmed ✓), not explicit")
        self.assertTrue(byid["open grandchild"]["derived"], "roll-down reaches the whole subtree")

    def test_ledger_tree_rolls_down_through_a_cleared_top(self):
        # It is CLEARED that rolls down through a dismissed top now, not done (the user 2026-07-26: the
        # box means done, and only done). The children still fade + strike with the dismissed parent —
        # the 2026-06-16 intent — but their boxes stay honest: unfinished stays unchecked.
        top, child = (SID + ":top", SID + ":child")
        def gn(nid, text, parent, **kw):
            d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": T0}
            d.update(kw); return d
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": None,
            "nodes": {top: gn(top, "dismissed top", None, cleared=True),
                      child: gn(child, "open child", top)},
            "placements": {}, "status": {}}))
        byid = {n["text"]: n for n in km.build_session(SID, NOW)["ledger"]["tree"]}
        self.assertTrue(byid["dismissed top"]["cleared"] and not byid["dismissed top"]["done"],
                        "a dismissed-unfinished top is struck, its box unchecked")
        self.assertTrue(byid["open child"]["cleared"], "a child under a CLEARED top fades with it (roll-down through clear)")
        self.assertFalse(byid["open child"]["done"], "...but its box stays honest — nothing finished it")

    def test_followup_body_quotes_context(self):
        # A feed follow-up quotes the ask it answers ('> <ask>') so the recipient session has context;
        # an explicit group title wins over the node lookup; unknown/none → bare text (the user 2026-06-16).
        # Every follow-up ALSO ends with the hidden goal marker (see the dedicated test below); fold it in.
        iid = SID + ":g2"                                   # fixture g2 = "Awaiting a decision", blocked, a top
        # default (the user TYPED this follow-up) ends with the goal-id only (→ reopen); NO romp-injected,
        # because it's the user's words → blue bubble. The romp-injected split is the dedicated test below.
        def mk(s, i=iid): return s + "\n\n" + NOTE + "<!-- romp-goal-id: " + i + " -->"
        # no title → node path: the node text + its status (g2 is blocked; it's a top so no "under")
        self.assertEqual(km._followup_body(iid, None, "go with option A"),
                         mk("> Awaiting a decision (blocked)\n\ngo with option A"))
        # explicit title (group modal) → verbatim, no node enrichment
        self.assertEqual(km._followup_body(iid, "Pick a database", "postgres"),
                         mk("> Pick a database\n\npostgres"))
        self.assertEqual(km._followup_body(SID + ":nope", None, "hi"),
                         mk("hi", SID + ":nope"), "no context → no empty quote (marker still appended)")

    def test_followup_body_prefers_the_verbatim_mint_quote(self):
        # g13 (the user 2026-07-01): a node carrying the minting message's VERBATIM head (node["quote"],
        # judge _mint_quote) is quoted back in the user's OWN WORDS — the way a person re-raises a thread —
        # with NO "(under X, done)" status tags and NO planner why. The paraphrased-title form stays only
        # as the legacy fallback (the test below).
        top, sub = SID + ":g1", SID + ":g3"
        store = {"rompUuid": SID, "seq": 3, "nodes": {
            top: {"id": top, "text": "Ship the release", "parentId": None,
                  "nodeComplete": True, "doneWhy": "All parts landed.", "blocked": False, "t": T0},
            sub: {"id": sub, "text": "Decide the version bump", "parentId": top, "nodeComplete": False,
                  "blocked": True, "blockWhy": "Need you to choose major vs minor.",
                  "quote": "can you figure out what version bump this release needs?", "t": T0}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        out = km._followup_body(sub, None, "go minor")
        self.assertIn("> can you figure out what version bump this release needs?", out,
                      "the user's own words, quoted back")
        self.assertNotIn("Decide the version bump", out, "the planner's paraphrase is not used")
        self.assertNotIn("(under", out, "no status/place tags — a person wouldn't annotate their own quote")
        self.assertNotIn("Need you to choose", out, "no planner why line")
        self.assertTrue(out.endswith("<!-- romp-goal-id: " + sub + " -->"), "the reopen marker still rides")

    def test_typed_followup_on_a_summary_carries_the_still_open_pieces(self):
        # The distilled summary is a LOSSY headline of the card (the user 2026-07-24): reply to it and the
        # session used to see that ONE line, so a takeaway that got the emphasis wrong propagated
        # unchallenged and nothing pointed the session at the rest — a BLOCKED sub-goal in particular went
        # unmentioned, which is the piece the reply is most likely to be about. The open pieces now ride
        # under the summary, the same bullets the nudge enumerates.
        top, done, open_, blk = SID + ":g1", SID + ":g3", SID + ":g4", SID + ":g5"
        store = {"rompUuid": SID, "seq": 5, "nodes": {
            top: {"id": top, "text": "Ship the notes API", "parentId": None, "nodeComplete": False,
                  "blocked": False, "summary": "Endpoints are live and the client is wired up.", "t": T0},
            done: {"id": done, "text": "Write the handlers", "parentId": top, "nodeComplete": True,
                   "blocked": False, "t": T0},
            open_: {"id": open_, "text": "Backfill the fixtures", "parentId": top, "nodeComplete": False,
                    "blocked": False, "why": "Needed before the load test.", "t": T0},
            blk: {"id": blk, "text": "Pick the rate-limit ceiling", "parentId": top, "nodeComplete": False,
                  "blocked": True, "blockWhy": "Need you to choose a number.", "t": T0}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        out = km._followup_body(top, None, "ship it")
        self.assertIn("> Endpoints are live and the client is wired up.", out, "the summary still leads")
        self.assertIn("Also still open on this:", out)
        self.assertIn("• Backfill the fixtures — Needed before the load test.", out)
        self.assertIn("• Pick the rate-limit ceiling (blocked) — Need you to choose a number.", out,
                      "the blocked piece is exactly what a reply to a wrong summary must not miss")
        self.assertNotIn("Write the handlers", out, "finished pieces are not still-open work")

    def test_a_flat_card_keeps_the_bare_summary_quote(self):
        # nothing open under it → nothing to add; the quote stays the one line it always was
        top = SID + ":g1"
        store = {"rompUuid": SID, "seq": 1, "nodes": {
            top: {"id": top, "text": "Ship the notes API", "parentId": None, "nodeComplete": True,
                  "blocked": False, "summary": "Shipped and verified.", "t": T0}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        out = km._followup_body(top, None, "nice")
        self.assertIn("> Shipped and verified.", out)
        self.assertNotIn("Also still open", out)

    def test_hierarchical_nudge_enumeration_wins_over_the_quote(self):
        # a NUDGE on a hierarchical top still enumerates the unfinished pieces (the user 2026-06-24) even
        # when the top carries a verbatim quote — the checklist is the more useful nudge form (g13 scope:
        # the multi-sub-goal case stays as-is).
        top = self._hier_goal_store()
        st = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        st["nodes"][top]["quote"] = "please ship the auth refactor end to end"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(st))
        out = km._followup_body(top, None, km.AUTO_NUDGE_TEXT, injected=True)
        self.assertIn("Still open on this:", out)
        self.assertNotIn("please ship the auth refactor", out)

    def test_followup_body_enriches_with_path_status_and_why(self):
        # the user 2026-06-17: the follow-up must carry more than a one-line title so the recipient session
        # understands WHAT it's following up on — the node's place (top goal), status, and the planner's why.
        # Since g13 this is the LEGACY fallback, used only by nodes minted before verbatim quotes were cached.
        top, sub = SID + ":g1", SID + ":g3"
        store = {"rompUuid": SID, "seq": 3, "nodes": {
            top: {"id": top, "text": "Ship the release", "parentId": None,
                  "nodeComplete": False, "blocked": False, "t": T0},
            sub: {"id": sub, "text": "Decide the version bump", "parentId": top, "nodeComplete": False,
                  "blocked": True, "blockWhy": "Need you to choose major vs minor.", "t": T0}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        out = km._followup_body(sub, None, "go minor")   # default: the user typed this follow-up
        self.assertIn('> Decide the version bump (under "Ship the release", blocked)', out)
        self.assertIn("> Need you to choose major vs minor.", out)   # the planner's why = the real question
        self.assertNotIn("<!-- romp-injected -->", out, "a TYPED follow-up is the user's → no romp-injected (blue bubble)")
        self.assertTrue(out.endswith("<!-- romp-goal-id: " + sub + " -->"))

    def _hier_goal_store(self):
        # a HIERARCHICAL top goal: two open own-work leaves (one blocked w/ a why), one DONE leaf, and one
        # DELEGATED leaf (a peer handoff). Used by the nudge-enumeration tests below.
        top = SID + ":g1"
        a, b, done, deleg = SID + ":a", SID + ":b", SID + ":d", SID + ":x"
        def n(nid, text, parent, **kw):
            return {"id": nid, "text": text, "parentId": parent, "nodeComplete": kw.get("done", False),
                    "blocked": kw.get("blocked", False), "blockWhy": kw.get("why", ""),
                    "handoff": kw.get("handoff"), "t": T0}
        store = {"rompUuid": SID, "seq": 1, "nodes": {
            top: n(top, "Ship the auth refactor", None),
            a: n(a, "Migrate the session store to Redis", top),
            b: n(b, "Add CSRF tokens", top, blocked=True, why="Need you to pick the token TTL."),
            done: n(done, "Update the login tests", top, done=True),
            deleg: n(deleg, "Peer is porting the client", top, handoff={"to": "peer"})},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        return top

    def test_nudge_enumerates_unfinished_subgoals(self):
        # the user 2026-06-24: a NUDGE on a hierarchical top goal must list its UNFINISHED lower-level nodes
        # (with the top as context) and ask for a status PER PIECE — so the session reports on each sub-goal,
        # not just the umbrella. Both the Nudge button and the auto-nudge land here with iid = the top goal.
        top = self._hier_goal_store()
        out = km._followup_body(top, None, km.AUTO_NUDGE_TEXT, injected=True)   # the Nudge button
        self.assertIn("> Ship the auth refactor", out, "the high-level goal is the heading/context")
        self.assertIn("Still open on this:", out)
        self.assertIn("• Migrate the session store to Redis", out, "an open own-work leaf is listed")
        self.assertIn("• Add CSRF tokens (blocked) — Need you to pick the token TTL.", out,
                      "a blocked leaf carries its tag + the planner's why")
        self.assertNotIn("Update the login tests", out, "a DONE leaf is not an unfinished piece")
        self.assertNotIn("Peer is porting the client", out, "a DELEGATED leaf is peer work, not this session's")
        self.assertIn("Where does each of those stand?", out,
                      "the body asks for a status on each piece, not the whole goal")
        self.assertNotIn(km.AUTO_NUDGE_TEXT, out, "the single-line 'status on the goal above' body is replaced")
        # still a proper nudge: gray-bubble marker + the reopen goal-id, targeting the TOP goal
        self.assertTrue(out.endswith("<!-- romp-injected --><!-- romp-goal-id: " + top + " -->"))

    def test_auto_nudge_gets_the_same_hierarchical_enumeration(self):
        # the auto-nudge (injected + auto) refines IDENTICALLY to the manual button (the user 2026-06-24).
        top = self._hier_goal_store()
        auto = km._followup_body(top, None, km.AUTO_NUDGE_TEXT, injected=True, auto=True)
        self.assertIn("Still open on this:", auto)
        self.assertIn("• Migrate the session store to Redis", auto)
        self.assertTrue(auto.endswith("<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: " + top + " -->"))

    def test_nudge_on_a_flat_goal_keeps_the_single_line_form(self):
        # a FLAT top (no sub-nodes) has no lower-level pieces to enumerate → the nudge keeps its existing
        # single-line "status on the goal above?" body verbatim (the auto-nudge tick relies on this).
        top = SID + ":g1"
        store = {"rompUuid": SID, "seq": 1, "nodes": {
            top: {"id": top, "text": "Wire up the thing", "parentId": None, "nodeComplete": False,
                  "blocked": False, "t": T0}}, "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        out = km._followup_body(top, None, km.AUTO_NUDGE_TEXT, injected=True)
        self.assertIn("> Wire up the thing", out)
        self.assertNotIn("Still open on this:", out, "nothing to enumerate on a flat goal")
        self.assertIn(km.AUTO_NUDGE_TEXT, out, "the single-line nudge body is preserved verbatim")

    def test_typed_followup_on_a_hierarchical_top_is_not_enumerated(self):
        # the enumeration is a NUDGE refinement (injected) only — a follow-up the USER TYPES on the top card
        # keeps the existing single-node quote, so we don't expand their reply into a per-sub status request.
        top = self._hier_goal_store()
        out = km._followup_body(top, None, "use Redis, TTL 1h")   # injected defaults False (the user typed it)
        self.assertNotIn("Still open on this:", out)
        self.assertIn("> Ship the auth refactor", out)
        self.assertIn("use Redis, TTL 1h", out)

    def test_feed_node_carries_prompt_anchor_uuid(self):
        # the user 2026-06-17: a card TITLE deep-links to the user's MINTING message BY ID — the minting
        # segment's trigger uuid (a user turn the chat tags), so prompt-intent resolves by id with no
        # kind-restricted nearest-time landing. promptAnchorUuid = trigger of the node's FIRST trail seg.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = em.segments(session["turns"][0])[0]
        nid = SID + ":g9"
        store = {"rompUuid": SID, "seq": 9, "nodes": {
            nid: {"id": nid, "text": "Awaiting a call", "parentId": None, "nodeComplete": False,
                  "blocked": True, "trail": [seg["id"]], "t": NOW}}, "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        asks = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
        self.assertIn(nid, asks, "the blocked top node is an ask card")
        # feed.ts reads the anchors off the card's `tree` node (it.tree.find(n => n.id === itemId)), so the
        # prompt anchor rides there alongside anchorUuid — not on the top-level ask dict.
        tnode = next(n for n in asks[nid]["tree"] if n["id"] == nid)
        self.assertEqual(tnode["promptAnchorUuid"], seg.get("trigger"),
                         "title prompt anchor = the minting segment's trigger (the user's message) uuid")

    def test_working_card_time_freshens_to_last_activity_not_mint(self):
        # A reply/nudge re-files under a WORKING goal and advances its mt; the card's age must FRESHEN to that
        # last activity, not stay pinned to the mint t (the user 2026-07-01, who replied but it still said 15m
        # ago when it should be 0m). completed/blocked cards already show their resolution mt; this fixes working.
        g = SID + ":gw"
        self._goal_store({g: {"id": g, "text": "confirm go-ahead", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": NOW - 900, "mt": NOW - 120}},
                         {g: "working"}, last=g)
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[g]
        self.assertEqual(card["t"], NOW - 120, "working card time = last activity (mt), so a reply freshens it")
        root = next(n for n in card["tree"] if n["id"] == g)
        self.assertEqual(root["last"], NOW - 120, "the modal root row age freshens to last activity too")

    def test_node_anchor_prefers_stored_prompt_uuid_over_seg_key_derivation(self):
        # The judge stamps promptUuid (the trigger atom uuid) on every node at mint (2026-07-01, via bugs).
        # The kernel must PREFER it over re-deriving the prompt anchor from trail[0]'s segment key — that
        # derivation drifts on trigger-TEXT mismatch (optimistic echo vs the real atom), which _seg_key can't
        # reconcile (it only fixes the timestamp axis), and the goal-modal title click then silently no-ops.
        # With the stored uuid there is nothing to re-match. Here seg_trig is EMPTY, so the derivation would
        # return None — the stored uuid must still win.
        nd = {"trail": ["sid:1000:deadbeef"], "promptUuid": "11111111-2222-3333-4444-555555555555"}
        prompt, _work = km._node_anchor_uuids(nd, {}, {})
        self.assertEqual(prompt, "11111111-2222-3333-4444-555555555555",
                         "the stored promptUuid wins even when the seg-key derivation misses (text drift)")

    def test_node_anchor_falls_back_to_derivation_when_no_stored_prompt_uuid(self):
        # A node minted BEFORE the promptUuid field existed has none → derive from trail[0]'s segment key,
        # exactly as before (additive: order of landing the judge write vs this kernel read doesn't matter).
        seg = "sid:1000:deadbeef"
        seg_trig = {km._seg_key(seg): "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        prompt, _work = km._node_anchor_uuids({"trail": [seg]}, seg_trig, {})
        self.assertEqual(prompt, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                         "no stored uuid → fall back to the seg_trig derivation (unchanged legacy path)")

    def test_card_carries_summary_anchor_for_the_summary_deep_link(self):
        # the distilled summary LINE deep-links: with no distiller citation stored on the node, the
        # fallback anchor is the trail's most CURRENT prose (the wrap-up), via _seg_last_text — not the
        # old biggest-text-block pick (the user 2026-07-01).
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = em.segments(session["turns"][0])[0]
        expect, _sub = km._seg_last_text(seg["atoms"])
        self.assertTrue(expect, "the fixture segment has assistant prose to anchor on")
        nid = SID + ":g42"
        store = {"rompUuid": SID, "seq": 42, "nodes": {
            nid: {"id": nid, "text": "Ship it", "parentId": None, "nodeComplete": True,
                  "blocked": False, "trail": [seg["id"]], "t": NOW}}, "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], expect,
                         "no citation stored → the card links its summary to the trail's latest prose")

    def test_summary_anchor_last_resort_is_the_trail_work_anchor(self):
        # a completed card whose citation doesn't resolve AND whose trail segments have no substantive
        # prose (tool-only work) previously shipped NO anchor at all — the summary rendered as plain,
        # unclickable text (the user 2026-07-02). Last resort now: the newest trail segment's WORK anchor
        # (seg_uuid — the same target the modal's node rows nav to), so the summary still deep-links.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = em.segments(session["turns"][0])[0]
        w, r = km._seg_anchors(seg["atoms"])
        expect = r or w
        self.assertTrue(expect, "the fixture segment has a work anchor")
        saved = km._seg_last_text
        km._seg_last_text = lambda atoms: (None, False)   # no prose anywhere → the latest-prose fallback yields nothing
        try:
            nid = SID + ":g47"
            store = {"rompUuid": SID, "seq": 47, "nodes": {
                nid: {"id": nid, "text": "Ship it", "parentId": None, "nodeComplete": True, "blocked": False,
                      "trail": [seg["id"]], "t": NOW, "summary": "Shipped.",
                      "summaryAnchor": "00000000-dead-dead-dead-000000000000"}},   # citation never resolves
                "placements": {}, "status": {}}
            (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
            card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
            self.assertEqual(card["summaryAnchorUuid"], expect,
                             "no citation + no prose → the trail's work anchor keeps the summary clickable")
        finally:
            km._seg_last_text = saved

    def test_card_carries_the_judge_stamped_warns(self):
        # judge _node_warn stamps anomalies (e.g. a distiller cite-miss) on the node; the card must carry
        # them verbatim so the feed can show the yellow "warning" chip and the click-through detail
        # (the user 2026-07-02). A node with no warns ships null, not a stray empty list.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = em.segments(session["turns"][0])[0]
        warn = {"kind": "cite-miss", "t": NOW - 60,
                "msg": "the distiller's source citation didn't come back",
                "detail": "What happened: …\n\nWhy it's unexpected: …"}
        nid, plain = SID + ":g45", SID + ":g46"
        store = {"rompUuid": SID, "seq": 45, "nodes": {
            nid: {"id": nid, "text": "Ship it", "parentId": None, "nodeComplete": True, "blocked": False,
                  "trail": [seg["id"]], "t": NOW, "summary": "Shipped.", "warns": [warn]},
            plain: {"id": plain, "text": "Other thing", "parentId": None, "nodeComplete": True,
                    "blocked": False, "trail": [seg["id"]], "t": NOW, "summary": "Done."}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
        self.assertEqual(cards[nid]["warns"], [warn], "the stamped warn reaches the card unchanged")
        self.assertIsNone(cards[plain]["warns"], "no warns → null on the card")

    def test_summary_anchor_prefers_the_distillers_cited_source(self):
        # the distiller CITES the message its takeaway is grounded in (node["summaryAnchor"], written by
        # the judge from the reply's SOURCE line): the kernel honors that over the deterministic fallback
        # whenever the uuid resolves in the live parse AND is substantive (the user 2026-07-01/07-14).
        # Scope: this is the primary tier for a goal that is NOT completed (status {} here → working);
        # a COMPLETED goal pins to the completion turn's wrap-up first (see the completed-pin tests).
        # Here the fallback would pick the LAST prose atom (a2); the citation names the earlier
        # (substantive) a1 and must win.
        early = "Grounding detail: the fix landed in the renderer's diff path, with a regression pin. " + "e " * 20
        late = "And the docs were refreshed to match the new behavior across both surfaces. " + "l " * 20
        recs = [uline(T0, "fix the feed flicker", "u1", ps="typed"),
                aline(T0 + 20, early, "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "continue", "u2", "a1", ps="typed"),
                aline(T0 + 120, late, "a2", "u2", stop="end_turn")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        segs = [sg for turn in session["turns"] for sg in em.segments(turn)]
        nid = SID + ":g43"
        store = {"rompUuid": SID, "seq": 43, "nodes": {
            nid: {"id": nid, "text": "Ship it", "parentId": None, "nodeComplete": True, "blocked": False,
                  "trail": [sg["id"] for sg in segs], "t": NOW, "summary": "Shipped.", "summaryAnchor": "a1"}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], "a1", "the distiller's cited source wins over the fallback")

    def test_summary_anchor_rejects_a_cited_connective_stub(self):
        # the incident shape (the user 2026-07-14): the distiller cited a short lead-in that merely NAMED
        # the goal ("Next the small one:") instead of the wrap-up where the outcome lives — topic match beat
        # substance. A citation is honored only when the cited atom is substantive (≥ jd.CITE_MIN_CHARS of
        # prose); a stub cite falls through to the deterministic latest-prose fallback, which lands on the
        # wrap-up. This heals bad anchors ALREADY stored, with no re-distill needed.
        stub = "Next the small one:"                                            # < 80 chars, names the goal
        wrap = ("All items are implemented, tested, and committed; the prompts now honor the tracking "
                "flag end to end and the suite is green.")
        recs = [uline(T0, "work the list", "u1", ps="typed"),
                aline(T0 + 20, stub, "aStub", "u1", stop="end_turn"),
                uline(T0 + 100, "carry on", "u2", "aStub", ps="typed"),
                aline(T0 + 120, wrap, "aWrap", "u2", stop="end_turn")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        segs = [sg for turn in session["turns"] for sg in em.segments(turn)]
        nid = SID + ":g48"
        store = {"rompUuid": SID, "seq": 48, "nodes": {
            nid: {"id": nid, "text": "The small one", "parentId": None, "nodeComplete": True, "blocked": False,
                  "trail": [sg["id"] for sg in segs], "t": NOW, "summary": "Done.", "summaryAnchor": "aStub"}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], "aWrap",
                         "a cited stub is rejected → the fallback lands on the substantive wrap-up")

    def test_summary_anchor_ignores_a_cited_source_that_does_not_resolve(self):
        # a citation whose uuid isn't in this parse (stale store, model copy error) must NOT ship a dead
        # link: the kernel falls back to the deterministic latest-prose anchor.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = em.segments(session["turns"][0])[0]
        expect, _sub = km._seg_last_text(seg["atoms"])
        nid = SID + ":g44"
        store = {"rompUuid": SID, "seq": 44, "nodes": {
            nid: {"id": nid, "text": "Ship it", "parentId": None, "nodeComplete": True, "blocked": False,
                  "trail": [seg["id"]], "t": NOW, "summary": "Shipped.",
                  "summaryAnchor": "99999999-dead-beef-0000-111111111111"}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], expect,
                         "an unresolvable citation falls back to the latest-prose anchor, never a dead link")

    def test_summary_anchor_fallback_prefers_the_latest_substantive_segment(self):
        # cross-segment recency (the user 2026-07-01): a goal whose trail spans an EARLY segment with a long
        # analysis and a LATER segment with a shorter (but substantive) wrap-up must anchor on the wrap-up.
        # Under the old max-length rule the early analysis held the anchor forever.
        early = "Deep analysis of the options. " + "detail " * 60          # long early prose (~450 chars)
        wrap = "Shipped: merged, tests pass, worktree cleaned up. " + "done " * 32   # substantive, shorter (~210)
        recs = [uline(T0, "build the feature", "u1", ps="typed"),
                aline(T0 + 20, early, "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "continue", "u2", "a1", ps="typed"),
                aline(T0 + 120, wrap, "a2", "u2", stop="end_turn")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        segs = [sg for turn in session["turns"] for sg in em.segments(turn)]
        nid = SID + ":g45"
        store = {"rompUuid": SID, "seq": 45, "nodes": {
            nid: {"id": nid, "text": "Build the feature", "parentId": None, "nodeComplete": True,
                  "blocked": False, "trail": [sg["id"] for sg in segs], "t": NOW}},
            "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], "a2",
                         "the latest substantive message (the wrap-up) beats a longer early analysis")

    def test_completed_summary_anchor_pins_to_the_completion_turns_wrapup(self):
        # the incident, round two (the user 2026-07-14): the distiller cited a SUBSTANTIVE mid-turn
        # status note (it passed the prose floor that filters connective stubs), so the summary click
        # landed mid-turn instead of on the giant turn-end recap. For a COMPLETED goal the anchor is
        # now EVENT-DERIVED, not a guess: the closer's DONE-ANCHOR appended the completing turn's final
        # segment as the trail tail (judge _close_turn), and the summary pins to that segment's last
        # substantive assistant block — the wrap-up — outranking the citation.
        note = ("Next the small one — item seven: the prompts now follow the tracking flag; "
                "moving on to the rename-healing work next after this lands cleanly.")
        wrap = ("Done: all nine items are implemented and committed; prompts honor the tracking flag, "
                "the rename heal landed with tests, and both suites are green.")
        recs = [uline(T0, "work the list", "u1", ps="typed"),
                aline(T0 + 20, note, "aNote", "u1", stop="end_turn"),
                uline(T0 + 100, "carry on", "u2", "aNote", ps="typed"),
                aline(T0 + 120, wrap, "aWrap", "u2", stop="end_turn")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        segs = [sg for turn in session["turns"] for sg in em.segments(turn)]
        nid = SID + ":g49"
        store = {"rompUuid": SID, "seq": 49, "nodes": {
            nid: {"id": nid, "text": "The list", "parentId": None, "nodeComplete": True, "blocked": False,
                  "trail": [sg["id"] for sg in segs], "t": NOW, "summary": "Done.",
                  "summaryAnchor": "aNote"}},   # substantive mid-turn citation — passes the prose gate
            "placements": {}, "status": {nid: "completed"}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], "aWrap",
                         "a completed goal pins its summary to the completion turn's wrap-up, over the citation")

    def test_completed_pin_reads_the_subtrees_newest_tail_for_an_umbrella_top(self):
        # the g91 click (the user 2026-07-15): a BOTTOM-UP-completed umbrella (all children done) has no
        # done verdict of its own, so the DONE-ANCHOR never appended a completing segment to ITS trail —
        # trail[-1] was still the MINT segment, and the pin sent the summary click to the goal's oldest
        # prose (the diagnosis opener) instead of the wrap-up the distiller correctly cited. The pin must
        # read the newest done-anchored tail across the SUBTREE: a child's tail IS its completing turn.
        mint = ("Opening analysis of the whole thread: the nudges are firing because the verdicts race "
                "the tick, and the details span two goals with several moving parts each.")
        wrap = ("Both fixes are landed, tested, and merged; the race is gated on placements and the "
                "moot heal now spares fresh blocks — full suites green on both sides.")
        recs = [uline(T0, "investigate and fix", "u1", ps="typed"),
                aline(T0 + 20, mint, "aMint", "u1", stop="end_turn"),
                uline(T0 + 100, "proceed", "u2", "aMint", ps="typed"),
                aline(T0 + 120, wrap, "aWrap", "u2", stop="end_turn")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        segs = [sg for turn in session["turns"] for sg in em.segments(turn)]
        top, kid = SID + ":g51", SID + ":g52"
        store = {"rompUuid": SID, "seq": 52, "nodes": {
            top: {"id": top, "text": "The umbrella", "parentId": None, "nodeComplete": False, "blocked": False,
                  "trail": [segs[0]["id"]], "t": NOW, "summary": "All done.",   # mint-time trail only
                  "summaryAnchor": "aMint"},               # citation resolves too — the pin must still win with aWrap
            kid: {"id": kid, "text": "The step that finished it", "parentId": top, "nodeComplete": True,
                  "blocked": False, "trail": [sg["id"] for sg in segs], "t": NOW}},   # done-anchored tail = the wrap turn
            "placements": {}, "status": {top: "completed"}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[top]
        self.assertEqual(card["summaryAnchorUuid"], "aWrap",
                         "an umbrella pins to the subtree's newest completing segment, not its own stale mint tail")

    def test_completed_pin_defers_to_the_citation_when_the_recap_has_no_prose(self):
        # a completed goal whose completion segment closed tool-only / one-liner (no substantive prose)
        # cannot pin — the distiller's citation stays the anchor (its purpose: name what informed the
        # summary), so the pin never trades a good link for a worse one.
        early = "Grounding detail: the fix landed in the renderer diff path, with a regression pin. " + "e " * 20
        recs = [uline(T0, "fix it", "u1", ps="typed"),
                aline(T0 + 20, early, "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "continue", "u2", "a1", ps="typed"),
                aline(T0 + 120, "Done.", "a2", "u2", stop="end_turn")]   # short close — below the prose floor
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self._warm_tpath()
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        segs = [sg for turn in session["turns"] for sg in em.segments(turn)]
        nid = SID + ":g50"
        store = {"rompUuid": SID, "seq": 50, "nodes": {
            nid: {"id": nid, "text": "Fix it", "parentId": None, "nodeComplete": True, "blocked": False,
                  "trail": [sg["id"] for sg in segs], "t": NOW, "summary": "Fixed.",
                  "summaryAnchor": "a1"}},
            "placements": {}, "status": {nid: "completed"}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], "a1",
                         "no substantive recap to pin on → the citation keeps the anchor")

    def test_followup_body_appends_goal_marker(self):
        # The follow-up judge reopens the tagged goal: every follow-up ends with a hidden
        # `<!-- romp-goal-id: <itemId> -->` marker (itemId = the card's top-goal node id), matched by the
        # judge's `romp-goal-id:\s*([^\s>]+)` (coordinated w/ the `judges` session, 2026-06-17). The
        # romp-injected author marker (→ gray bubble) rides ONLY a nudge (injected=True), NOT a follow-up
        # the user types — that's the user's words → blue bubble (the user 2026-06-20).
        iid = SID + ":g2"
        typed = km._followup_body(iid, "ctx", "do the thing")                  # default: the user typed it
        self.assertTrue(typed.endswith("\n\n" + NOTE + "<!-- romp-goal-id: " + iid + " -->"),
                        "a typed follow-up ends with the ignore-note + goal-id — no romp-injected (blue bubble)")
        self.assertNotIn("<!-- romp-injected -->", typed)
        self.assertEqual(re.search(r"romp-goal-id:\s*([^\s>]+)", typed).group(1), iid)   # the judge's parser
        nudge = km._followup_body(iid, "ctx", "do the thing", injected=True)   # romp's OWN nudge (the BUTTON)
        self.assertTrue(nudge.endswith("\n\n" + NOTE + "<!-- romp-injected --><!-- romp-goal-id: " + iid + " -->"),
                        "a nudge adds romp-injected (gray bubble) after the ignore-note, ahead of the goal-id")
        self.assertNotIn("<!-- romp-auto -->", nudge, "a Nudge BUTTON click is NOT auto → no romp-auto marker")
        # an AUTO-nudge (the kernel's background _auto_nudge_tick) ALSO carries romp-auto → the romp-logo marker
        auto = km._followup_body(iid, "ctx", "status?", injected=True, auto=True)
        self.assertTrue(auto.endswith("\n\n" + NOTE + "<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: " + iid + " -->"),
                        "an auto-nudge carries BOTH romp-injected and romp-auto, after the ignore-note, then the goal-id")

    def test_session_list_for_picker(self):
        # the + picker's payload (requestSessions → sessionList). Was always empty: bin/romp-kernel had
        # no requestSessions handler, so the kernel never replied. Running sessions first; archive headline
        # as the summary; the names-registry color.
        items = km._session_list(NOW, km._tmux_sessions())
        self.assertTrue(items, "picker must list the live session")
        it = next(i for i in items if i["id"] == SID)
        self.assertEqual(it["name"], "testsess")
        self.assertTrue(it["running"], "SID is alive in tmux → running")
        self.assertEqual(it["time"], "running")
        self.assertEqual(it["summary"], "Fixing the feed")
        self.assertEqual(it["color"], {"bg": "#abcdef", "fg": "#ffffff"})

    def _stale_session(self, sid, name, days):
        """Register a session whose transcript was last touched `days` ago (past jd.WINDOW, inside the
        picker's 30). Returns its launch dir."""
        d = Path(self.td.name) / ("dir-" + sid[:8]); d.mkdir()
        pdir = Path(self.td.name) / "projects" / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(d)))
        pdir.mkdir(parents=True)
        tp = pdir / (sid + ".jsonl")
        tp.write_text(json.dumps(uline(NOW - int((days + 1) * 86400), "draft the reply", "u1", ps="typed")) + "\n")
        t = NOW - int(days * 86400)
        os.utime(tp, (t, t))
        (jd.NAMES / sid).write_text("%s\t%s\t#9cd2ff\n" % (name, str(d)))
        return d

    def test_picker_reaches_past_the_caption_window(self):
        # The user 2026-07-24: typed a 3-day-old session's name into the + picker, got nothing back, and had
        # to start a fresh session instead. The picker's payload was built from discover()'s 48h CAPTION
        # horizon, which it had inherited by accident — so every session idle longer than two days was
        # invisible to it, and reviving one you couldn't reach by tab was impossible. The picker now gets
        # PICKER_WINDOW (30 days) in ONE reply when it opens; no lazy second fetch, because with forks off
        # the wider walk measured ~78ms cold, well under noticing.
        old_sid = "99999999-8888-7777-6666-555555555555"
        self._stale_session(old_sid, "roof", 3.5)

        items = km._session_list(NOW, {})
        it = next((i for i in items if i["id"] == old_sid), None)
        self.assertIsNotNone(it, "the picker reaches a session idle 3.5 days — the bug this fixes")
        self.assertEqual(it["name"], "roof")
        self.assertFalse(it["running"], "it is dead — the row is a revive target, not a reopen")
        self.assertEqual(it["time"], "3d ago")
        self.assertIn(SID, [i["id"] for i in items], "the recent session is still listed too")
        # the judge/feed horizon is UNCHANGED — only the picker reaches wider
        self.assertNotIn(old_sid, [s["sid"] for s in km._sessions(NOW)],
                         "discover()'s default 48h window still governs every other surface")

    def test_picker_lists_one_row_per_session_never_a_fork_lane(self):
        # A fork lane (an SDK /clear mints a new fsid under the same customTitle) is the SAME romp session
        # listed a second time, under an fsid that is NOT a romp sid — so its row pointed openSession at
        # something that isn't a session. Skipping fork detection is also what makes the 30-day window
        # affordable: reading each candidate's head cost 553ms of a 640ms walk (measured 2026-07-24).
        old_sid = "99999999-8888-7777-6666-555555555555"
        d = self._stale_session(old_sid, "roof", 3.5)
        pdir = Path(self.td.name) / "projects" / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(d)))
        fork_sid = "77777777-6666-5555-4444-333333333333"
        fork = pdir / (fork_sid + ".jsonl")                           # same customTitle → a fork lane
        fork.write_text("\n".join(json.dumps(r) for r in [
            {"type": "custom-title", "customTitle": "roof"},
            uline(NOW - 3 * 86400, "carry on", "u1", ps="typed")]) + "\n")
        t = NOW - int(3.4 * 86400)
        os.utime(fork, (t, t))

        # with forks ON (what the judge/feed use) it IS a lane of its own — so the fixture really does
        # exercise the fork path, and the picker's omission is the forks=False switch doing its job
        self.assertIn(fork_sid, [s["sid"] for s in km._sessions(NOW, km.PICKER_WINDOW)],
                      "fixture sanity: this is a real fork lane at the wider window")
        ids = [i["id"] for i in km._session_list(NOW, {})]
        self.assertEqual(ids.count(old_sid), 1, "the session appears exactly once")
        self.assertNotIn(fork_sid, ids, "its fork lane is not a row of its own in the picker")

    def test_alive_filter_empty_tmux_with_tmux_present_shows_nothing(self):
        # The user 2026-06-16: after killing every session and reloading, the surfaces wrongly
        # reopened tabs for the dead ones. Cause: an EMPTY tmux result fell back to file-derived
        # sessions. With a tmux binary present (the host case), an empty result is a GENUINE zero —
        # show nothing, not the dead session in the discover() window.
        saved = km._has_tmux
        km._has_tmux = lambda: True
        try:
            self.assertEqual(km._alive_sessions(NOW, {}), [], "tmux present + empty → no sessions")
            feed = km.build_feed(NOW, tmux={})
            self.assertEqual(feed.get("cards", []), [], "feed shows no card for a dead session")
            self.assertEqual(km._ordered_alive(NOW, {}), [], "no chat tabs / timeline lanes either")
        finally:
            km._has_tmux = saved

    def test_alive_filter_headless_no_tmux_falls_back_to_discover(self):
        # The ONLY case that still falls back: a genuinely headless run with no tmux binary at all
        # (a test box / CI), so a review-only surface isn't blank. Keyed on tmux PRESENCE, not a count.
        saved = km._has_tmux
        km._has_tmux = lambda: False
        try:
            sids = [s["sid"] for s in km._alive_sessions(NOW, {})]
            self.assertIn(SID, sids, "no tmux at all → fall back to the discovered session")
        finally:
            km._has_tmux = saved

    def test_producer_sig_tracks_renames(self):
        # The user 2026-06-16: tmux/tab renames didn't propagate to the chat. A rename touches only the
        # names file (no transcript), so the push fingerprint must include it or the producer never
        # re-pushes the new name.
        s1 = km._producer_sig(browser=True)
        self.assertIn("n:" + SID, s1, "the session's names-file mtime is part of the signature")
        t = os.stat(km.NAMES / SID).st_mtime
        os.utime(km.NAMES / SID, (t + 10, t + 10))     # a rename rewrites the file → newer mtime
        s2 = km._producer_sig(browser=True)
        self.assertNotEqual(s1, s2, "a names-file change must change the producer signature")

    def test_producer_sig_tracks_state_transitions(self):
        # The user 2026-06-17 (release-session settled-gate bug): a session going IDLE writes
        # states/<sid>.jsonl but NOT the transcript, so the push fingerprint must include it — else
        # run_plan/run_close never re-run on a pure idle and a now-done focus goal stays stuck "working".
        sd = jd.STATE / "states"; sd.mkdir(parents=True, exist_ok=True)
        sf = sd / (SID + ".jsonl")
        sf.write_text(json.dumps({"t": NOW - 50, "state": "working"}) + "\n")
        os.utime(sf, (NOW - 50, NOW - 50))
        s1 = km._producer_sig(browser=True)
        self.assertIn("s:" + SID, s1, "the session's states-file mtime is part of the signature")
        # a new idle record (a state transition) rewrites the states file → newer mtime
        sf.write_text(sf.read_text() + json.dumps({"t": NOW - 10, "state": "idle"}) + "\n")
        os.utime(sf, (NOW - 10, NOW - 10))
        s2 = km._producer_sig(browser=True)
        self.assertNotEqual(s1, s2, "a states-file transition (e.g. going idle) must change the signature")

    def test_resolve_node_crosses_off_a_blocked_goal(self):
        # the user 2026-06-17: clicking a blocked node's mark in the modal CROSSES IT OFF (nodeOverride
        # op:resolve). g2 is the fixture's blocked top goal. After resolve it must be nodeComplete, no
        # longer blocked, and its rolled-up status must leave "blocked" (a complete node can't block).
        g2 = "%s:g2" % SID
        self.assertTrue(jd.load_goals(SID)["nodes"][g2].get("blocked"), "fixture: g2 starts blocked")
        self.assertTrue(km._resolve_node(SID, g2), "resolve applies")
        after = jd.load_goals(SID)
        self.assertTrue(after["nodes"][g2].get("nodeComplete"), "resolve sets nodeComplete")
        self.assertFalse(after["nodes"][g2].get("blocked"), "resolve clears the block flag")
        self.assertNotEqual(after.get("status", {}).get(g2), "blocked", "rolled-up status leaves blocked")
        self.assertFalse(km._resolve_node(SID, g2), "resolve on an already-complete node is a no-op")

    def test_rename_session_live_renames_tmux(self):
        # A LIVE session renames via tmux; the after-rename-session hook then syncs the names file + pill.
        saved_name, saved_run = km._tmux_name_of, km.subprocess.run
        calls = []

        class _R:
            returncode = 0; stdout = ""; stderr = ""

        km._tmux_name_of = lambda s: "testsess"
        km.subprocess.run = lambda cmd, *a, **k: (calls.append(cmd), _R())[1]
        try:
            out = km._rename_session(SID, "newname")
            self.assertEqual(out, "newname")
            self.assertTrue(any(c[:2] == ["tmux", "rename-session"] and "newname" in c for c in calls),
                            "live rename must call `tmux rename-session ... newname`")
        finally:
            km._tmux_name_of, km.subprocess.run = saved_name, saved_run

    def test_rename_session_dead_writes_names_file_preserving_color(self):
        # A DEAD (read-only) tab has no tmux session, so the rename writes the names file directly,
        # keeping the recorded dir + identity color.
        saved_name = km._tmux_name_of
        km._tmux_name_of = lambda s: None
        try:
            out = km._rename_session(SID, "archived_name")
            self.assertEqual(out, "archived_name")
            self.assertEqual(km._name_of(SID), "archived_name", "dead-tab rename writes the names file")
            self.assertEqual(km._name_color(SID), {"bg": "#abcdef", "fg": "#ffffff"}, "color preserved")
        finally:
            km._tmux_name_of = saved_name

    def test_rename_session_rejects_invalid_name(self):
        self.assertIsNone(km._rename_session(SID, "has spaces!"), "invalid chars → rejected, no rename")

    def test_dead_session_is_not_auto_kept_as_a_tab(self):
        # the user 2026-06-17 REVERSED the earlier keep-a-tab-when-it-dies rule: a session shown alive then dead is now
        # TIMELINE-ONLY — it does NOT linger as a chat tab (reopen from the timeline instead). It still
        # reports 'closed' (so wherever it IS shown — a read-only tab — it renders struck-through).
        saved_seen, saved_has, saved_kept = set(km._seen_live), km._has_tmux, set(km._kept_open)
        km._has_tmux = lambda: True
        try:
            km._seen_live.clear(); km._seen_live.add(SID); km._kept_open.discard(SID)
            tabs = [s["sid"] for s in km._chat_tab_sessions(NOW, {})]
            self.assertNotIn(SID, tabs, "a dead session no longer auto-keeps a tab (timeline-only)")
            self.assertEqual(km.build_session(SID, NOW, {})["status"]["state"], "closed")
        finally:
            km._seen_live.clear(); km._seen_live.update(saved_seen); km._has_tmux = saved_has
            km._kept_open.clear(); km._kept_open.update(saved_kept)

    def test_dead_session_not_kept_when_never_seen_live(self):
        # A fresh kernel start (_seen_live empty) must NOT resurrect a dead session's tab (the Part-A rule).
        saved_seen, saved_has = set(km._seen_live), km._has_tmux
        km._has_tmux = lambda: True
        try:
            km._seen_live.clear()
            tabs = [s["sid"] for s in km._chat_tab_sessions(NOW, {})]
            self.assertNotIn(SID, tabs, "never-seen dead session is not shown on a fresh start")
        finally:
            km._seen_live.clear(); km._seen_live.update(saved_seen); km._has_tmux = saved_has

    def test_dead_kept_tab_excluded_once_forgotten(self):
        # ×-closing a dead read-only tab discards it from _kept_open — dead is timeline-only again
        # (the closeTab route's one remaining duty; hidden-tabs is gone, the user 2026-08-11).
        saved_seen, saved_has, saved_kept = set(km._seen_live), km._has_tmux, set(km._kept_open)
        km._has_tmux = lambda: True
        try:
            km._seen_live.clear(); km._seen_live.add(SID)
            km._kept_open.discard(SID)
            tabs = [s["sid"] for s in km._chat_tab_sessions(NOW, {})]
            self.assertNotIn(SID, tabs, "a forgotten dead tab is not shown")
        finally:
            km._seen_live.clear(); km._seen_live.update(saved_seen); km._has_tmux = saved_has
            km._kept_open.clear(); km._kept_open.update(saved_kept)

    def test_rel_ago_buckets(self):
        self.assertEqual(km._rel_ago(1000, 1000), "just now")
        self.assertEqual(km._rel_ago(1000, 1000 - 120), "2m ago")
        self.assertEqual(km._rel_ago(1000 + 7200, 1000), "2h ago")
        self.assertEqual(km._rel_ago(3 * 86400, 0), "3d ago")

    def test_fold_tasks_reconstructs_the_checklist_and_hides_raw_calls(self):
        # _fold_tasks reconstructs the checklist from the transcript's TaskCreate/TaskUpdate (id from
        # TaskCreate's "Task #N" result). It is now the DETECTOR of outstanding tasks + the fallback source
        # only when there's no authoritative store (see _read_task_store); the raw Task* tool calls are
        # never shown as tool cards (the webview hides them via ACK_TOOLS, so the kernel skips them).
        def asst(t, uuid, parent, blocks, stop="end_turn"):
            return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
                    "message": {"role": "assistant", "content": blocks, "stop_reason": stop}}
        tc = {"type": "tool_use", "id": "tc1", "name": "TaskCreate",
              "input": {"subject": "Wire the picker", "activeForm": "Wiring the picker"}}
        tu = {"type": "tool_use", "id": "tu1", "name": "TaskUpdate", "input": {"taskId": "1", "status": "in_progress"}}
        with self.tpath.open("a") as f:
            f.write(json.dumps(uline(T0 + 100, "make a plan", "u2", parent="a2", ps="typed")) + "\n")
            f.write(json.dumps(asst(T0 + 110, "a3", "u2", [{"type": "text", "text": "Planning."}, tc], stop="tool_use")) + "\n")
            f.write(json.dumps(trline(T0 + 112, "tc1", "r2", "a3", content="Task #1 created successfully: Wire the picker")) + "\n")
            f.write(json.dumps(asst(T0 + 120, "a4", "r2", [tu])) + "\n")
        session = km._parse(str(self.tpath), SID, NOW)
        tasks = km._fold_tasks(session)
        self.assertEqual([t["id"] for t in tasks], ["1"])
        self.assertEqual(tasks[0]["subject"], "Wire the picker")
        self.assertEqual(tasks[0]["activeForm"], "Wiring the picker")
        self.assertEqual(tasks[0]["status"], "in_progress", "TaskUpdate moved it to in_progress")
        m = km.build_session(SID, NOW)
        self.assertFalse(any(e["kind"] == "tool" and e["name"] in ("TaskCreate", "TaskUpdate") for e in m["events"]),
                         "raw Task* tool calls are folded away, not shown as tool cards")

    def test_queued_card_from_transcript_queue_ops(self):
        # Messages queued in the TUI while busy/compacting are written to the transcript as queue-operation
        # records; _pending_queued folds them so they surface as a {kind:"queued"} card at the bottom (the
        # "vanished during compaction" fix). EVENT-BASED (was pane-scraped) — and BOTH of two queued messages
        # show: the pane scrape dropped the 2nd and lost both (the user 2026-06-16).
        km._queued_parse_cache.clear()
        with self.tpath.open("a") as f:
            f.write(json.dumps(qop("enqueue", "fix the flaky test")) + "\n")
            f.write(json.dumps(qop("enqueue", "then bump the version")) + "\n")
        m = km.build_session(SID, NOW)
        q = [e for e in m["events"] if e["kind"] == "queued"]
        self.assertEqual(len(q), 1, "one queued card")
        self.assertEqual([t["md"] for t in q[0]["texts"]], ["fix the flaky test", "then bump the version"],
                         "BOTH queued messages, in submission order (the 2-message regression)")
        self.assertTrue(all("followUp" not in t for t in q[0]["texts"]), "plain queued messages aren't follow-ups")
        self.assertEqual(m["events"][-1]["kind"], "queued", "queued sits at the bottom, by the composer")

    def test_queued_card_absent_when_all_dequeued(self):
        # once Claude Code consumes the queue (dequeue records), nothing is still pending → no card.
        km._queued_parse_cache.clear()
        with self.tpath.open("a") as f:
            f.write(json.dumps(qop("enqueue", "fix the flaky test")) + "\n")
            f.write(json.dumps(qop("enqueue", "then bump the version")) + "\n")
            f.write(json.dumps(qop("dequeue")) + "\n")
            f.write(json.dumps(qop("dequeue")) + "\n")
        m = km.build_session(SID, NOW)
        self.assertFalse([e for e in m["events"] if e["kind"] == "queued"], "fully-drained queue → no card")

    def test_optimistic_compacting_until_boundary(self):
        # clicking compact marks the session 'compacting' AT ONCE on chat + timeline (no waiting for the
        # hook→tmux→4s-poll round-trip, which a reload can swallow); a compact_boundary at/after the click
        # clears it event-based (the user 2026-06-16).
        km._compact_clicked.clear()
        km._compact_clicked[SID] = NOW - 5                      # we just sent /compact
        m = km.build_session(SID, NOW)
        self.assertEqual(m["status"]["state"], "compacting", "optimistic cue shows immediately on the chip")
        tl = km.build_timeline(NOW)
        lane = next(s for s in tl["sessions"] if s["id"] == SID)
        self.assertEqual(lane["state"], "compacting", "and on the timeline lane, same instant")
        # the compaction completes → a compact_boundary lands after the click → the cue clears
        boundary = {"type": "system", "subtype": "compact_boundary", "timestamp": iso(NOW + 1),
                    "uuid": "cb1", "parentUuid": "a2"}
        with self.tpath.open("a") as f:
            f.write(json.dumps(boundary) + "\n")
        m2 = km.build_session(SID, NOW + 2)
        self.assertNotEqual(m2["status"]["state"], "compacting", "a compact_boundary clears the optimistic cue")
        self.assertNotIn(SID, km._compact_clicked, "the flag is popped once the boundary lands")

    def test_compacting_beats_blocked_when_user_compacts_an_api_errored_session(self):
        # The reported bug (the user 2026-06-29): a session whose context filled died on an API error, the
        # user clicked Compact to recover, and the chip stayed "blocked" the whole compaction — no sign the
        # compact was happening (worst on SDK sessions, which have no tmux 'compacting' state, so the chip
        # rides entirely on the optimistic /compact flag). The in-flight compaction MUST win over blocked.
        km._api_err_cache.clear()
        km._compact_clicked.clear()
        with self.tpath.open("a") as f:
            f.write(json.dumps(apierr_line(T0 + 60, "e1", "a2")) + "\n")
        self.assertEqual(km.build_session(SID, NOW)["status"]["state"], "blocked",
                         "fixture sanity: the API error alone files the chip under blocked")
        km._compact_clicked[SID] = NOW - 5                    # user clicks Compact on the blocked session
        self.assertEqual(km.build_session(SID, NOW)["status"]["state"], "compacting",
                         "the compaction the user just kicked off surfaces, not the stale blocked chip")
        # and once the compaction lands a boundary, the optimistic cue clears (back to blocked until retried)
        with self.tpath.open("a") as f:
            f.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                                "timestamp": iso(NOW + 1), "uuid": "cb1", "parentUuid": "e1"}) + "\n")
        self.assertNotEqual(km.build_session(SID, NOW + 2)["status"]["state"], "compacting",
                            "a compact_boundary clears the optimistic cue even on an API-errored session")

    def test_api_error_chat_card_and_blocked_chip(self):
        # an API error as the last record → a {kind:"apiError"} card at the bottom + the chip flips to
        # "blocked" (red) so a stalled session stands out (the user 2026-06-16).
        km._api_err_cache.clear()
        with self.tpath.open("a") as f:
            f.write(json.dumps(apierr_line(T0 + 60, "e1", "a2")) + "\n")
        m = km.build_session(SID, NOW)
        self.assertEqual(m["status"]["state"], "blocked", "API error → blocked chip")
        cards = [e for e in m["events"] if e["kind"] == "apiError"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["status"], 500)
        self.assertEqual(m["events"][-1]["kind"], "apiError", "the API-error card sits at the bottom")

    def test_api_error_clears_after_retry(self):
        # the user (or the Retry button) sends "retry" → a genuine user record after the error → no longer
        # blocked: no card, chip back to a normal state.
        km._api_err_cache.clear()
        with self.tpath.open("a") as f:
            f.write(json.dumps(apierr_line(T0 + 60, "e1", "a2")) + "\n")
            f.write(json.dumps(uline(T0 + 65, "retry", "u2", parent="e1")) + "\n")
        m = km.build_session(SID, NOW)
        self.assertNotEqual(m["status"]["state"], "blocked")
        self.assertFalse(any(e["kind"] == "apiError" for e in m["events"]))

    def test_api_error_floors_feed_goal(self):
        # the session's focus top-goal files under BLOCKED with an apiError reason → the card carries the
        # red badge + Retry (blocked.state == "apiError", column "needs_input").
        km._api_err_cache.clear()
        store = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        store["lastNode"] = "%s:g2" % SID          # focus the OPEN top-goal (g1 is complete → never floored)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        with self.tpath.open("a") as f:
            f.write(json.dumps(apierr_line(T0 + 60, "e1", "a2")) + "\n")
        self._warm_tpath()                             # cache-only build_feed reads the API-error floor only if the parse is warm
        d = km.build_feed(NOW)
        card = next(a for a in d["asks"] if a["text"] == "Awaiting a decision")
        self.assertEqual(card["blocked"]["state"], "apiError")
        self.assertEqual(card["blocked"]["status"], 500)
        self.assertEqual(card["column"], "needs_input", "an API-error card files under BLOCKED")

    def test_injected_img_paths_for_the_send_wait(self):
        # _tmux_send waits for these to resolve to "[Image #N]" before pressing Enter, so a text+image
        # message doesn't race the async image read and drop the text (the user 2026-06-17).
        self.assertEqual(km._injected_img_paths("look at /srv/a.png and ~/pics/b.jpg please"),
                         ["/srv/a.png", "~/pics/b.jpg"])
        self.assertEqual(km._injected_img_paths("no images here"), [])
        self.assertEqual(km._injected_img_paths(""), [])

    def test_user_images_extracts_pasted_path_and_blocks(self):
        # the user 2026-06-17: path-pasted images stopped rendering after the Python rebuild dropped the
        # extraction. _user_images mirrors the old transcript.ts: base64 block → data URL; path source →
        # path:<abs>; and — the reported case — a bare image PATH typed into the composer (plain text).
        b64 = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}]
        self.assertEqual(km._user_images(b64, "", True)[0]["src"], "data:image/png;base64,QUJD")
        psrc = [{"type": "image", "source": {"path": "/srv/img/shot.png"}}]
        self.assertEqual(km._user_images(psrc, "", True)[0], {"src": "path:/srv/img/shot.png", "path": "/srv/img/shot.png"})
        # the common case: a bare image path typed/dragged into the composer arrives as plain text
        self.assertEqual(km._user_images([], "look at ~/pics/diagram.png please", True),
                         [{"src": "path:~/pics/diagram.png", "path": "~/pics/diagram.png"}])
        # a non-human (injected) line is NOT scanned for bare paths
        self.assertEqual(km._user_images([], "/srv/img/x.png", False), [])
        # capped at 4
        many = " ".join("/srv/a%d.png" % i for i in range(8))
        self.assertEqual(len(km._user_images([], many, True)), 4)

    def test_gear_has_card_display_toggles_and_button_chrome(self):
        # the ⛭ settings gear (web dashboard) reads as a rounded-rect BUTTON (the user 2026-06-17). The old
        # Explanations + Sub-goals toggles are GONE from the gear (the user 2026-06-18): cards show the
        # distiller summary (no Explanations), and Sub-goals moved to the feed FOOTER.
        self.assertNotIn("id=rs-explanations", _gear_src())
        self.assertNotIn("id=rs-subgoals", _gear_src())
        self.assertNotIn("explanations", _gear_src())                               # every trace of the pref is gone
        self.assertIn("dispatchEvent(new Event('romp:settings'))", _gear_src())     # same-doc re-render signal (compact toggle etc.)
        # the ↻ refresh + ⛭ gear BUTTONS moved to the shell's far-left rail (the user 2026-06-25); only the
        # settings MODAL stays in the feed, opened by the rail gear via a {romp:'openSettings'} postMessage.
        self.assertNotIn("id=rrefresh", _gear_src())                              # refresh is on the rail now
        self.assertIn("e.data.romp === 'openSettings'", _gear_src())                  # the modal opens on the rail's request
        landing = km._landing()
        self.assertIn("id=rail-gear", landing)
        self.assertIn("id=rail-refresh", landing)
        self.assertIn("fetch('/restart',{method:'POST'})", landing)                 # the rail ↻ POSTs /restart

    def test_gear_polish_tooltips_colormap_bar_no_emoji(self):
        # the user 2026-06-23: descriptions become HOVER tooltips (decluttered), and the analytics button drops
        # its 📊 emoji.
        self.assertIn("#rsettings .rs-sub { display: none; }", _gear_css_src())               # descriptions hidden by default
        self.assertRegex(_gear_css_src(), r"#rsettings \.rs-row:hover \.rs-sub \{ display: block; position: absolute")  # float on hover
        self.assertNotIn("\U0001F4CA", _gear_src())                                 # the 📊 emoji is gone
        self.assertIn("Token usage analytics", _gear_src())                          # the label itself stays

    def test_gear_colormap_dropdown_options_are_bars_not_names(self):
        # the user 2026-06-23: the feed-colormap selector's OPTIONS are the gradient bars themselves — no map
        # NAMES. A custom widget (native <select> can't render gradient options): a button shows the picked
        # map's bar, and the list is one bar per map; clicking a bar selects it + posts setColormap.
        self.assertNotIn("<select id=rs-colormap", _gear_src())                      # the native name-list select is gone
        self.assertNotIn(">Hawaii<", _gear_src())                                    # no map names listed
        self.assertIn("id=rs-cmap-btn", _gear_src())                                 # the button shows the picked bar
        self.assertIn("id=rs-cmap-list", _gear_src())                                # the bar list
        self.assertIn(".rs-cmap-opt {", _gear_css_src())                                   # each option is a styled bar
        self.assertIn("function cmGrad(name)", _gear_src())                            # builds a bar gradient per map
        self.assertIn("linear-gradient(to right,", _gear_src())
        self.assertIn("{ type: 'setColormap', name: name }", _gear_src())                     # picking a bar persists + posts
        self.assertNotIn("renderCmapBar", _gear_src())                                 # the old preview-bar fn is gone

    def test_gear_has_show_git_branch_toggle(self):
        # the user 2026-06-23: a "Show git branch" checkbox controls whether the chat bottom-bar shows the
        # session's git branch beside the dir. OFF by default since 2026-08-10 (the user, trimming the
        # statusline for narrow panes): an explicit stored true opts in. It mirrors render.ts'
        # loadSettings().showBranch read, persisted in romp:settings.
        self.assertIn("id=rs-branch", _gear_src())
        self.assertIn("Show git branch", _gear_src())
        self.assertIn("s.showBranch = gb.checked", _gear_src())        # change → persist
        self.assertIn("gb.checked = s.showBranch === true", _gear_src())  # open → reflect (default OFF)
        self.assertIn("showBranch: false", _gear_src())               # load() default OFF, both branches
        self.assertNotIn("showBranch: true", _gear_src())             # the old default must not linger

    def test_chat_body_has_an_explicit_send_button(self):
        # The web-dashboard composer (kernel _chat_body, a SECOND copy of vscode-extension/src/page-skeleton.chatBody)
        # carries an explicit send button beside 📎, so ⏎ isn't the only way to send (the user 2026-06-17).
        body = km._chat_body()
        self.assertIn('id="composer-send"', body)
        self.assertLess(body.index("composer-attach"), body.index("composer-send"),
                        "send sits to the RIGHT of the 📎 attach button")

    def test_chat_body_attach_is_a_monochrome_svg_icon_not_an_emoji(self):
        # The 📎 attach glyph was replaced with a monochrome line-icon (currentColor SVG) in the romp style,
        # to match the gear/network chrome (the user 2026-07-15). The kernel _chat_body is the SECOND copy of
        # page-skeleton.chatBody the browser loads, so the icon must live HERE too, not only in page-skeleton.
        body = km._chat_body()
        attach = body[body.index('id="composer-attach"'):body.index('id="composer-send"')]
        self.assertIn("<svg", attach, "the attach button renders an inline SVG icon")
        self.assertIn('stroke="currentColor"', attach, "monochrome — inherits the button tint")
        self.assertNotIn("\U0001F4CE", attach)   # the 📎 paperclip emoji is gone

    def test_chat_body_has_the_composer_resize_handle(self):
        # The web dashboard's HTML is a SECOND copy of page-skeleton.chatBody (this is the copy the browser
        # actually loads) — so the drag-to-resize handle must live HERE too, not only in page-skeleton, or the
        # render.ts wiring finds no #composer-resize element and the handle never appears (the user 2026-07-07).
        body = km._chat_body()
        self.assertIn('id="composer-resize"', body)
        self.assertLess(body.index('id="footer"'), body.index('id="composer-resize"'), "inside the footer")
        self.assertLess(body.index('id="composer-resize"'), body.index('id="statusline"'), "on the top-edge divider, above the statusline")

    def test_feed_cards_are_top_level_goals_only(self):
        # The feed's cards are top-level GOALS only (read-side.md, the user 2026-06-16). A completed
        # goal → its own COMPLETED card; the blocked goal → a BLOCKED card. Turn captions are NOT cards:
        # despite a "Fixed the feed flicker" caption in the fixture, the stream is empty — emitting
        # captions as standalone DETAILS cards is the bug that flooded the columns.
        d = km.build_feed(NOW)
        self.assertEqual(d["type"], "feed")
        self.assertNotIn("items", d, "the standalone-items channel is gone (2026-07-07) — goal cards only")
        comp = [a for a in d["asks"] if a["column"] == "completed"]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0]["text"], "Fix the feed flicker")
        self.assertEqual(comp[0]["tree"][0]["status"], "done")
        self.assertTrue(any(a["column"] == "needs_input" for a in d["asks"]), "the blocked goal is a BLOCKED card")
        # card tint is the recency colormap (age → hawaii ramp), not a flat session color
        self.assertEqual(comp[0]["trgb"], list(km.cm.age_rgb(NOW - comp[0]["t"])))
        self.assertNotEqual(comp[0]["trgb"], km._rgb(comp[0]["color"]), "not the flat session color")

    def test_cards_for_segments_resolves_segment_to_owning_top_card(self):
        # reverse-hover: a hovered timeline bar's segment id → the TOP goal card that owns it (inverse
        # of _goal_segments), so the kernel can light that feed card.
        g1, g1c, g2 = "%s:g1" % SID, "%s:g1c" % SID, "%s:g2" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "nodes": {
                g1: {"id": g1, "text": "top", "parentId": None, "trail": ["segA"]},
                g1c: {"id": g1c, "text": "child", "parentId": g1, "trail": ["segB"]},
                g2: {"id": g2, "text": "other", "parentId": None, "trail": ["segC"]}}}))
        self.assertEqual(km._cards_for_segments(SID, ["segB"]), [g1])   # a CHILD's segment → its top card
        self.assertEqual(km._cards_for_segments(SID, ["segC"]), [g2])
        self.assertEqual(set(km._cards_for_segments(SID, ["segA", "segC"])), {g1, g2})
        self.assertEqual(km._cards_for_segments(SID, ["nope"]), [])
        self.assertEqual(km._cards_for_segments(SID, []), [])

    def test_feed_non_handoff_card_has_no_origin(self):
        comp = next(a for a in km.build_feed(NOW)["asks"] if a["column"] == "completed")
        self.assertIsNone(comp["origin"], "a normal (non-courier) card carries no handoff origin")

    def test_feed_live_permission_floors_focus_card_to_blocked(self):
        """A session stopped on a LIVE permission prompt floors its active-focus card under BLOCKED — the hard
        floor, beats the goal's planner status. The kernel reports column=needs_input DIRECTLY (it.column is
        authoritative; no working/blocked split routed by it.blocked), and the ⏸ approval badge rides it."""
        g = "%s:g5" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 5, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Work in progress", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": NOW - 50}},
            "placements": {}, "status": {g: "working"}}))
        km._tmux_sessions = lambda: {SID: {"state": "permission", "since": NOW - 30, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["blocked"]["state"], "permission", "a live permission prompt floors the focus card")
        self.assertEqual(card["column"], "needs_input", "the kernel files the floored card under BLOCKED directly")

    def test_feed_live_picker_floors_focus_card_to_blocked(self):
        """An SDK AskUserQuestion reports live state "picker" (it IS a picker, not a permission Allow/Deny;
        tmux's Notification hook calls the same prompt "permission"). The hard blocked floor must honor
        "picker" too, else an SDK session stopped on a question never registers as blocked the way a tmux
        one does (the user 2026-06-27). The card text says "awaiting your input" (vs "approval")."""
        g = "%s:g8" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 8, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Work in progress", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": NOW - 50}},
            "placements": {}, "status": {g: "working"}}))
        km._tmux_sessions = lambda: {SID: {"state": "picker", "since": NOW - 30, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["blocked"]["state"], "picker", "a live picker floors the focus card to blocked")
        self.assertEqual(card["column"], "needs_input", "a picker-floored card files under BLOCKED directly")
        self.assertIn("input", card["blocked"]["what"], "picker wording reflects a question, not an approval")
        # the session chip (build_session payload) also reads "awaiting" on a picker, like a permission
        self.assertEqual(km.build_session(SID, NOW)["status"]["state"], "needsInput",
                         "the session chip reads awaiting on a live picker")

    def test_feed_permission_does_not_floor_a_completed_focus(self):
        """The floor applies only to an OPEN focus goal — a live prompt while the focus is already
        completed (the block is on not-yet-placed new work) leaves the completed card alone."""
        # default store: lastNode = g1 (completed). A permission state must NOT floor g1.
        km._tmux_sessions = lambda: {SID: {"state": "permission", "since": NOW - 30, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        comp = next(a for a in km.build_feed(NOW)["asks"] if a["column"] == "completed")
        self.assertIsNone(comp["blocked"], "a completed focus card is not floored by a live prompt")

    def test_feed_live_permission_floors_a_nodecomplete_but_working_focus(self):
        """A focus goal that is nodeComplete but the settled gate still holds at "working" (the session kept
        working under it, then live-blocked) MUST floor to BLOCKED — the floor gates on the ROLLED-UP status,
        not the raw nodeComplete flag (the user 2026-06-18: system_prompt was red-tabbed but absent from the
        BLOCKED column because its nodeComplete focus rolled up to "working", not "completed")."""
        g = "%s:g7" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 7, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Read the system prompt", "parentId": None,
                          "nodeComplete": True, "blocked": False, "cleared": False, "trail": [], "t": NOW - 50}},
            "placements": {}, "status": {g: "working"}}))
        km._tmux_sessions = lambda: {SID: {"state": "permission", "since": NOW - 30, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["blocked"]["state"], "permission",
                         "a nodeComplete-but-WORKING focus IS floored by a live permission prompt")
        self.assertEqual(card["column"], "needs_input",
                         "the kernel reports the floored card as needs_input directly — no working/blocked split")

    def test_feed_no_permission_no_hard_block(self):
        comp = next(a for a in km.build_feed(NOW)["asks"] if a["column"] == "completed")
        self.assertIsNone(comp["blocked"], "no live permission (idle) → no hard block floor")

    def test_last_plain_user_turn_t_excludes_followups_and_romp(self):
        # a PLAIN human prompt counts; a romp-injected nudge (author romp) and a typed card-reply (carries
        # the romp-goal-id marker) do NOT — only untargeted replies set the re-check sweep (the user 2026-06-27).
        def turn(uuid, author, text, t):
            return {"trigger": uuid, "t": t,
                    "atoms": [{"uuid": uuid, "type": "user", "author": author, "t": t,
                               "message": {"role": "user", "content": [{"type": "text", "text": text}]}}]}
        turns = [
            turn("u1", "human", "hello there", 100),
            turn("u2", "romp", "nudge", 400),                                # romp-injected → excluded
            turn("u3", "human", "answer <!-- romp-goal-id: x:g1 -->", 500),  # targeted card-reply → excluded
            turn("u4", "human", "just chatting", 250),                       # plain → counts
        ]
        self.assertEqual(km._last_plain_user_turn_t(turns), 250)
        self.assertEqual(km._last_plain_user_turn_t([]), 0)

    def _blocked_store(self, **nodes_extra):
        g = "%s:gR" % SID
        nd = {"id": g, "text": "blocked goal", "parentId": None, "nodeComplete": False,
              "blocked": True, "blockWhy": "need your call", "cleared": False,
              "trail": [], "t": NOW - 100, "mt": NOW - 100}
        nd.update(nodes_extra)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g,
            "nodes": {g: nd}, "placements": {}, "status": {g: "blocked"}}))
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        return g

    def test_feed_plain_reply_moves_block_to_working_while_in_flight(self):
        # the user 2026-07-02 (who wanted it immediate) + 2026-07-29 (who wanted it to STOP strobing): a
        # PLAIN thread reply after a soft block moves the card to WORKING, and it HOLDS there across turn
        # boundaries until the UNBLOCKER has re-examined the block with evidence covering the reply
        # (blockCheckT reaches it). The old bound — the open turn — made the card round-trip
        # working↔needs-you at EVERY turn boundary of an active session (seven flips in six minutes on the
        # audited card); the watermark is the event the turn-bound was approximating, so the card now
        # returns exactly once, when the judge has actually re-considered and kept the block.
        g = self._blocked_store()
        saved_p, saved_w = km._last_plain_user_turn_t, km._session_working
        try:
            km._last_plain_user_turn_t = lambda turns: NOW - 10      # a plain reply AFTER the block (mt NOW-100)
            km._session_working = lambda turns: True                 # a turn is in flight
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card["column"], "working", "the reply moves the block to Working at once")
            self.assertFalse(card["recheck"], "a plain reply is not a TARGETED follow-up → recheck stays False")
            self.assertTrue(card["rejudging"], "the 'Re-judging…' swirl rides along in Working")
            km._session_working = lambda turns: False                # turn ended — but the judge hasn't looked yet
            card_idle = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card_idle["column"], "working",
                             "a turn boundary is not new information — the card HOLDS until the judge re-examines")
            self.assertTrue(card_idle["rejudging"], "still pending the judge → the swirl stays")
            g = self._blocked_store(blockCheckT=NOW - 5)             # the unblocker examined evidence PAST the reply
            card_ruled = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card_ruled["column"], "needs_input",
                             "the judge re-examined and kept the block → back to Needs-You, exactly once")
            self.assertFalse(card_ruled["rejudging"], "ruled → no spinner (the latch cannot stick past a judge look)")
            g = self._blocked_store()
            km._last_plain_user_turn_t = lambda turns: NOW - 300     # a reply that PRE-dates the block
            km._session_working = lambda turns: True
            card_pre = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertFalse(card_pre["rejudging"], "no reply since the block → nothing to re-judge")
            self.assertEqual(card_pre["column"], "needs_input")
        finally:
            km._last_plain_user_turn_t, km._session_working = saved_p, saved_w

    def test_feed_rejudging_ignores_a_stranded_echo_and_cannot_stick_past_a_judge_look(self):
        # REGRESSION (the user 2026-07-22): rejudging used to ALSO ride the backend send-echo, moving a card
        # to Working the instant you hit send (before the turn opened). But a composer slash-command echo
        # NEVER retires — its expanded transcript form ("<command-name>/jld…") doesn't text-match the raw
        # echo, and the parser skips it from the human floor — so a stranded echo pinned the card in Working,
        # idle, FOREVER, invisible to the nudge (which reads the still-blocked store). The arm reads ONLY the
        # PARSE's plain-reply floor (a real transcript atom, never the echo), and since 2026-07-29 the flag
        # clears on the unblocker's watermark (blockCheckT) instead of the turn boundary — so the two
        # stranding properties to pin are: the echo alone arms nothing, and the watermark always releases.
        g = self._blocked_store()
        km._tmux_echo.pop(SID, None)
        saved_p, saved_w = km._last_plain_user_turn_t, km._session_working
        try:
            # NO plain reply since the block in the parse — only a stranded echo in the live tail (the
            # slash-command case that never prunes). It must not arm the flip, working or idle.
            km._last_plain_user_turn_t = lambda turns: NOW - 300
            km._session_working = lambda turns: False
            km._tmux_echo_add(SID, "/jld go ahead, do option B", author="human")
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertFalse(card["rejudging"], "a stranded echo can never arm rejudging — only a parsed reply can")
            self.assertEqual(card["column"], "needs_input",
                             "so the blocked card stays in Needs-You where the nudge sees it — never stuck in Working")
            # a REAL parsed reply after the block arms the latch even while idle (pending the judge)…
            km._last_plain_user_turn_t = lambda turns: NOW - 10
            card2 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertTrue(card2["rejudging"], "a parsed plain reply arms the latch — idle or not, it's the judge's move")
            self.assertEqual(card2["column"], "working")
            # …and the unblocker's watermark ALWAYS releases it — advanced on every examine and on the
            # parse give-up path, so the 2026-07-22 stuck-in-Working failure has no revival route.
            g = self._blocked_store(blockCheckT=NOW - 5)
            card3 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertFalse(card3["rejudging"], "the watermark passed the reply → released")
            self.assertEqual(card3["column"], "needs_input")
        finally:
            km._tmux_echo.pop(SID, None)
            km._last_plain_user_turn_t, km._session_working = saved_p, saved_w

    def test_feed_recheck_targeted_followup_does_not_sweep_siblings(self):
        # two blocked tops; a TARGETED follow-up (followupPending) on g1 only. No plain reply. g1 re-checks,
        # g2 stays urgent — a direct card-reply doesn't move everything (the user 2026-06-27).
        g1, g2 = "%s:gA" % SID, "%s:gB" % SID
        def nd(gid, **kw):
            n = {"id": gid, "text": gid, "parentId": None, "nodeComplete": False, "blocked": True,
                 "blockWhy": "?", "cleared": False, "trail": [], "t": NOW - 100, "mt": NOW - 100}
            n.update(kw); return n
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": g1,
            "nodes": {g1: nd(g1, followupPending=True), g2: nd(g2)},
            "placements": {}, "status": {g1: "blocked", g2: "blocked"}}))
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        saved = km._last_plain_user_turn_t
        try:
            km._last_plain_user_turn_t = lambda turns: 0            # NO plain reply
            cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
            self.assertTrue(cards[g1]["recheck"], "targeted followup → that card re-checks")
            self.assertFalse(cards[g2]["recheck"], "sibling without a follow-up stays urgent-blocked")
        finally:
            km._last_plain_user_turn_t = saved

    def _child_blocked_store(self, **child_extra):
        # a top umbrella whose BLOCK lives on a DESCENDANT: blocked rolls up (the card reads blocked)
        # but the unblocker examines — and stamps blockCheckT on — the child, never the top.
        top, sub = "%s:gT" % SID, "%s:gS" % SID
        tn = {"id": top, "text": "umbrella", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": NOW - 200, "mt": NOW - 200}
        sn = {"id": sub, "text": "the actual ask", "parentId": top, "nodeComplete": False,
              "blocked": True, "blockWhy": "need your call", "cleared": False,
              "trail": [], "t": NOW - 100, "mt": NOW - 100}
        sn.update(child_extra)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": sub,
            "nodes": {top: tn, sub: sn}, "placements": {}, "status": {top: "blocked"}}))
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        return top

    def test_feed_rejudging_watermark_covers_a_descendant_block(self):
        # REGRESSION (the user 2026-07-31): the latch's clear bound read the TOP node's blockCheckT,
        # but when the block lives on a child (blocked rolls up), the unblocker stamps the CHILD's
        # watermark — the top's stays None forever, so the latch armed on every plain reply and no
        # judge look could ever release it: the card strobed Working↔Needs-You at every turn of an
        # active conversation (the audited card flipped in seconds-apart pairs for half an hour).
        # The bound is now the OLDEST watermark among the subtree's OPEN blocked nodes.
        top = self._child_blocked_store()
        saved_p, saved_w = km._last_plain_user_turn_t, km._session_working
        try:
            km._last_plain_user_turn_t = lambda turns: NOW - 10     # a plain reply AFTER the child's block
            km._session_working = lambda turns: False
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
            self.assertTrue(card["rejudging"], "reply not yet examined → the latch arms off the CHILD's block")
            self.assertEqual(card["column"], "working")
            # the unblocker examines THE CHILD with evidence covering the reply → released, exactly once
            top = self._child_blocked_store(blockCheckT=NOW - 5)
            card_ruled = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
            self.assertFalse(card_ruled["rejudging"],
                             "the CHILD's watermark passed the reply → released (the top's own None must not pin the latch)")
            self.assertEqual(card_ruled["column"], "needs_input")
            # an examine that PRE-dates the reply keeps the latch armed — the judge hasn't seen the reply
            top = self._child_blocked_store(blockCheckT=NOW - 20)
            card_stale = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
            self.assertTrue(card_stale["rejudging"], "examined before the reply → still the judge's move")
            self.assertEqual(card_stale["column"], "working")
        finally:
            km._last_plain_user_turn_t, km._session_working = saved_p, saved_w

    def _store_with_status(self, st):
        # one top goal in a given rolled-up status — used to simulate the judge rewriting the store mid-pass
        g = "%s:gP" % SID
        nd = {"id": g, "text": "the goal", "parentId": None,
              "nodeComplete": (st == "completed"), "blocked": (st == "blocked"),
              "blockWhy": ("need your call" if st == "blocked" else None),
              "cleared": False, "trail": [], "t": NOW - 100, "mt": NOW - 50}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g,
            "nodes": {g: nd}, "placements": {}, "status": {g: st}}))
        return g

    def test_feed_freezes_goal_reads_during_a_judge_pass_no_intermediate_flicker(self):
        # the user 2026-06-30: within ONE producer pass the planner writes a transient "blocked" that the closer
        # overrules to "completed"; a feed rebuild fired mid-pass (the 5s time bucket) used to read that
        # half-applied store and flicker the card working -> blocked -> completed. The PRE-pass snapshot makes
        # the feed serve a pass-boundary-consistent view: the card holds its pre-pass state for the whole pass,
        # then jumps straight to the post-pass state. (Without the snapshot, the first assert reads live "blocked"
        # and fails — this is the regression guard.)
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        g = self._store_with_status("working")             # PRE-pass state: working
        km._begin_goals_pass()                             # judge pass starts → snapshot the pre-pass stores
        try:
            self._store_with_status("blocked")             # planner's transient mid-pass write lands on disk
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card["column"], "working",
                             "mid-pass the feed serves the PRE-pass snapshot, NOT the transient blocked")
            self._store_with_status("completed")           # closer overrules to completed, still mid-pass
            card2 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card2["column"], "working", "still the pre-pass snapshot until the pass ends")
        finally:
            km._end_goals_pass()                           # pass over → live reads resume
        card3 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card3["column"], "completed", "after the pass the feed shows the fully-applied state")

    def test_feed_reads_live_outside_a_judge_pass(self):
        # the snapshot only applies DURING a pass — with no pass active a write shows immediately, so user
        # actions (clear/follow-up) aren't delayed (the user 2026-06-30).
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        g = self._store_with_status("working")
        km._end_goals_pass()                               # ensure no pass snapshot is active
        self._store_with_status("blocked")
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["column"], "needs_input", "no pass active → live read shows the block at once")

    def _settled_store(self, *suffixes):
        # top goal(s) already SETTLED into Completed, each with the diary its flags were materialized from —
        # the state a card is in when the user replies to it
        suffixes = suffixes or ("gP",)
        nodes, status = {}, {}
        for sfx in suffixes:
            g = "%s:%s" % (SID, sfx)
            nodes[g] = {"id": g, "text": "the goal " + sfx, "parentId": None,
                        "nodeComplete": True, "blocked": False, "cleared": False, "trail": [],
                        "t": NOW - 100, "mt": NOW - 50, "doneWhy": "finished",
                        "settledAt": NOW - 50, "settledDone": True,
                        "log": [{"ev_t": NOW - 50, "src": "closer", "kind": "done", "why": "finished", "at": NOW - 50},
                                {"ev_t": NOW - 50, "src": "romp", "kind": "settle", "at": NOW - 50}]}
            status[g] = "completed"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": len(nodes), "lastNode": list(nodes)[-1],
            "nodes": nodes, "placements": {}, "status": status}))
        return list(nodes) if len(nodes) > 1 else list(nodes)[0]

    def test_a_user_reply_mid_pass_shows_working_at_once_not_when_the_pass_ends(self):
        # THE BUG (the user 2026-07-21): the pre-pass snapshot above was also freezing out the USER's own
        # writes. optimistic_followup writes the LIVE store while the feed reads the frozen copy, so a reply
        # landing mid-pass stayed invisible for the whole pass — 30-80s in practice — and the client's revert
        # window expired first and toasted "that follow-up didn't move the card to Working" while the session
        # was already working the reply. A user gesture must never wait out a judge pass: the override journal
        # it records is replayed onto the snapshot, so the card flies to Working on the very next build.
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        g = self._settled_store()
        km._begin_goals_pass()                             # a judge pass is already in flight when the user replies
        try:
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card["column"], "completed", "pre-pass state: the card is settled in Completed")
            self.assertTrue(jd.optimistic_followup(SID, g, text="also handle the empty case", now=NOW))
            km._note_user_goal_write(SID)                  # what the askFollowUp route does on a real reply
            card2 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card2["column"], "working",
                             "the reply punches through the snapshot — no waiting out the pass")
            self.assertTrue(card2["followupPending"], "…wearing the Followed up chip, as after the pass")
            card3 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card3["column"], "working", "and it STAYS working on later builds in the same pass")
        finally:
            km._end_goals_pass()

    def test_a_second_reply_in_the_same_pass_punches_through_too(self):
        # The re-punch is keyed on the user-write MARK, not a once-per-snapshot flag: replying to one card
        # and then another, both inside a single (long) judge pass, must move BOTH. A plain done-flag would
        # have served the first reply and silently swallowed every reply after it for the rest of the pass.
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        ga, gb = self._settled_store("gA", "gB")
        km._begin_goals_pass()
        try:
            self.assertTrue(jd.optimistic_followup(SID, ga, text="first reply", now=NOW))
            km._note_user_goal_write(SID)
            cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
            self.assertEqual((cards[ga]["column"], cards[gb]["column"]), ("working", "completed"))
            self.assertTrue(jd.optimistic_followup(SID, gb, text="second reply", now=NOW + 1))
            km._note_user_goal_write(SID)
            cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
            self.assertEqual((cards[ga]["column"], cards[gb]["column"]), ("working", "working"),
                             "the second reply lands too, without waiting out the pass")
        finally:
            km._end_goals_pass()

    def test_the_user_punch_through_still_hides_the_judges_mid_pass_writes(self):
        # The punch-through is scoped to the USER's gesture, replayed onto the PRE-pass snapshot — it does not
        # re-open the store to the judges' half-applied writes, which is the whole reason the snapshot exists.
        # Three states are distinguishable here and only one is right: completed (frozen, the bug), needs_input
        # (the planner's transient, the flicker), working (the user's reply on the pre-pass card).
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        g = self._settled_store()
        km._begin_goals_pass()
        try:
            self.assertTrue(jd.optimistic_followup(SID, g, text="one more thing", now=NOW))
            km._note_user_goal_write(SID)
            self._store_with_status("blocked")             # the planner's transient mid-pass write lands on disk
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertEqual(card["column"], "working",
                             "the user's reply shows; the judge's mid-pass block does NOT")
        finally:
            km._end_goals_pass()

    def test_a_reply_that_predates_the_pass_is_never_double_applied(self):
        # The mark is compared against WHEN THE SNAPSHOT WAS READ, and a TIE resolves toward replaying — a
        # write racing the read loop must never be the one that gets lost. That is only safe because the
        # replay is idempotent, so pin the property the tie-break leans on: however many builds run, the
        # user's reopen appears in the card's diary exactly ONCE.
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        g = self._settled_store()
        km._end_goals_pass()
        self.assertTrue(jd.optimistic_followup(SID, g, text="before the pass", now=NOW))
        km._note_user_goal_write(SID)
        km._begin_goals_pass()                             # the pass snapshots a store that ALREADY has the reply
        try:
            for _ in range(3):
                card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
                self.assertEqual(card["column"], "working")
            log = km._feed_goals(SID)["nodes"][g].get("log") or []
            self.assertEqual(len([e for e in log if e.get("src") == "user" and e.get("kind") == "reopen"]), 1,
                             "the reopen is applied once, no matter how many builds replay the journal")
        finally:
            km._end_goals_pass()

    def test_the_feed_payload_carries_a_build_id_that_advances_per_build(self):
        # buildId is what lets a client tell "this payload predates my click" from "this is the kernel's
        # answer to it" (see _next_feed_build_id / cardMoveAck), so it must advance on every real build and
        # hold steady on a cache hit — otherwise an acked prediction clears against a stale payload.
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        first = km._cached_feed(NOW, km._tmux_sessions(), ("sig", 1))
        self.assertIsInstance(first.get("buildId"), int)
        again = km._cached_feed(NOW, km._tmux_sessions(), ("sig", 1))
        self.assertEqual(again["buildId"], first["buildId"], "a cache hit re-sends the SAME build, same id")
        km._views_dirty[0] = time.time()                   # a user write forces a rebuild past the cache
        third = km._cached_feed(NOW, km._tmux_sessions(), ("sig", 1))
        self.assertGreater(third["buildId"], first["buildId"], "a real rebuild advances the id")

    def test_seg_key_strips_the_volatile_timestamp(self):
        self.assertEqual(km._seg_key("11111111-2222:1782627917:19cee1e8"), "11111111-2222:19cee1e8")
        self.assertEqual(km._seg_key("11111111-2222:1782627951:19cee1e8"), "11111111-2222:19cee1e8",
                         "a different middle timestamp maps to the SAME key")
        self.assertIsNone(km._seg_key(None))
        self.assertEqual(km._seg_key("nocolons"), "nocolons", "a non-conforming id passes through")

    def test_drifted_trail_seg_id_still_resolves_the_summary_anchor(self):
        # the bug (the user 2026-06-27): a goal's trail seg id carried the SDK echo's send-time timestamp,
        # but the live parse's seg id for the same segment uses the real atom's process-time — same session,
        # same trigger-text hash, different t. The summary then showed no hover link / "couldn't locate".
        # build_feed must resolve it via the timestamp-invariant key.
        recs = [uline(NOW - 100, "wire the overview strip", "uTrig"),
                aline(NOW - 95, "Done — wired the overview strip.", "aReply", "uTrig")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km._parse_cache.clear()
        ps = km._parse(str(self.tpath), SID, NOW)              # warm the cache (build_feed reads cache-only)
        real_id = em.segments(ps["turns"][-1])[0]["id"]
        p = real_id.split(":")
        drifted = "%s:%d:%s" % (p[0], int(p[1]) - 30, p[2])    # same session + hash, send-time-shifted
        self.assertNotEqual(drifted, real_id)
        g = "%s:gD" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g,
            "nodes": {g: {"id": g, "text": "wire the strip", "parentId": None, "nodeComplete": True,
                          "blocked": False, "cleared": False, "trail": [drifted], "t": NOW - 100,
                          "mt": NOW - 95, "summary": "Wired the overview strip."}},
            "placements": {}, "status": {g: "completed"}}))
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["summaryAnchorUuid"], "aReply",
                         "summary anchor resolves despite the trail seg id's drifted timestamp")
        self.assertEqual(card["turnId"], g)   # sanity: it's the card we built

    def test_peer_wait_only_decorates_goals_minted_before_the_question(self):
        # the user 2026-06-28: a stale wait (an unanswered QUESTION to a live peer, hours old) was decorating
        # a brand-new unrelated goal with "Awaiting <peer>". A wait can only apply to goals that EXISTED when
        # the question was sent — a goal minted after it can't be awaiting that answer.
        old, new = "%s:gOld" % SID, "%s:gNew" % SID
        def nd(gid, t):
            return {"id": gid, "text": gid, "parentId": None, "nodeComplete": False, "blocked": False,
                    "cleared": False, "trail": [], "t": t, "mt": t}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 2, "lastNode": new,
            "nodes": {old: nd(old, NOW - 1000), new: nd(new, NOW - 10)},
            "placements": {}, "status": {old: "working", new: "working"}}))
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 50, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        saved = km._wait_for_graph
        try:                                              # the question was sent at NOW-500 (between gOld and gNew)
            km._wait_for_graph = lambda now, alive: {SID: {"peerSid": "peerz", "name": "peerz",
                "color": {"bg": "#fff", "fg": "#000"}, "inCycle": False, "since": NOW - 500}}
            cards = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}
            self.assertIsNotNone(cards[old].get("waitingOn"), "a goal predating the question shows Awaiting")
            self.assertIsNone(cards[new].get("waitingOn"),
                              "a goal minted AFTER the question is NOT decorated by the stale wait")
        finally:
            km._wait_for_graph = saved

    def _sender_goal(self, sender, gid, **kw):
        """Write a sender's goal store with one linked goal (open by default) — the cross-agent end of a
        courier handoff that build_feed checks to decide whether the '↪ from <peer>' badge is still live."""
        nd = {"id": gid, "text": "the sender's linked goal", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": NOW - 60}
        nd.update(kw)
        (jd.GOALDIR / (sender + ".json")).write_text(json.dumps({
            "rompUuid": sender, "seq": 1, "lastNode": None, "nodes": {gid: nd},
            "placements": {}, "status": {}}))

    def test_feed_courier_handoff_resolves_origin_sender(self):
        """A goal planted by the courier carries origin:{peer:<senderSid>,goalId,...}; while the sender's
        linked goal is OPEN, build_feed resolves the sender's rompUuid to a name + color for the badge."""
        sender = "99999999-8888-7777-6666-555555555555"
        (jd.NAMES / sender).write_text("sendersess\t/elsewhere\t#ff8800\n")
        self._sender_goal(sender, sender + ":g1")                 # sender's linked goal is OPEN → live handoff
        g = "%s:g7" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 7, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Do the handed-off work", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": NOW - 50,
                          "origin": {"peer": sender, "goalId": sender + ":g1", "msgId": "m-abc.123"}}},
            "placements": {}, "status": {g: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["origin"], {"peer": "sendersess", "peerHost": "", "peerSid": sender,
                                          "color": {"bg": "#ff8800", "fg": "#ffffff"}, "live": True},
                         "origin.peer (a sid) resolves to the sender's name + color; the link is live")

    def test_feed_handoff_origin_falls_back_to_short_sid_when_unnamed(self):
        """If the sender isn't in the names registry, fall back to a short sid (never crash / show blank)."""
        sender = "abcdef00-0000-0000-0000-000000000000"
        self._sender_goal(sender, sender + ":g1")                 # OPEN linked goal → badge shows
        g = "%s:g8" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 8, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Handoff from an unnamed peer", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": NOW - 50,
                          "origin": {"peer": sender, "goalId": sender + ":g1", "msgId": "m-x.1"}}},
            "placements": {}, "status": {g: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["origin"]["peer"], sender[:8])
        self.assertIsNone(card["origin"]["color"])

    def test_feed_handoff_origin_uses_couriers_name_host_snapshot_for_federated_sender(self):
        """A FEDERATED sender's sid resolves to nothing locally; the courier's plant-time snapshot
        (peerName + peerHost) carries the chip instead — host:name, never a bare sid stub (the user
        2026-07-26, after a delegation chip read as an 8-char sid prefix)."""
        sender = "abcdef00-1111-0000-0000-000000000000"
        self._sender_goal(sender, sender + ":g1")                 # OPEN linked goal → badge shows
        g = "%s:g21" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 21, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Handoff from a federated peer", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": NOW - 50,
                          "origin": {"peer": sender, "goalId": sender + ":g1", "msgId": "m-x.2",
                                     "peerName": "api", "peerHost": "TESTHOST2"}}},
            "placements": {}, "status": {g: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["origin"]["peer"], "api")
        self.assertEqual(card["origin"]["peerHost"], "TESTHOST2")

    def test_feed_handoff_origin_live_local_name_beats_stale_snapshot(self):
        """A LOCAL sender resolves through the live names registry (which tracks renames), and a local
        resolve means no host qualifier — even if the origin carries an old peerName/peerHost snapshot."""
        sender = "abcdef00-2222-0000-0000-000000000000"
        (jd.NAMES / sender).write_text("renamedsess\t/elsewhere\t#ff8800\n")
        self._sender_goal(sender, sender + ":g1")
        g = "%s:g22" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 22, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Handoff with a stale snapshot", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": NOW - 50,
                          "origin": {"peer": sender, "goalId": sender + ":g1", "msgId": "m-x.3",
                                     "peerName": "oldname", "peerHost": "elsewhere"}}},
            "placements": {}, "status": {g: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["origin"]["peer"], "renamedsess")
        self.assertEqual(card["origin"]["peerHost"], "")

    def test_feed_handoff_origin_persists_absorbed_with_live_false(self):
        """PROVENANCE IS HISTORY (the user 2026-08-16): the "↪ from <peer>" badge used to vanish the
        moment the sender's linked goal closed — which run_propagate makes the exact moment THIS card
        completes — so a completed card never showed where its work came from, and a propagated clear
        read as one card mysteriously taking another. The badge now stays for the card's life with
        live=False once absorbed; only the AFFORDANCE changes (dimmed, historical)."""
        sender = "11112222-3333-4444-5555-666677778888"
        (jd.NAMES / sender).write_text("sendersess\t/elsewhere\t#ff8800\n")
        self._sender_goal(sender, sender + ":g1", nodeComplete=True)   # sender finished its piece
        g = "%s:g9" % SID
        def write_origin(origin, mid):
            (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
                "rompUuid": SID, "seq": 9, "lastNode": g,
                "nodes": {g: {"id": g, "text": "Absorbed handoff", "parentId": None, "nodeComplete": False,
                              "blocked": False, "cleared": False, "trail": [], "t": NOW - 50,
                              "origin": {"peer": sender, "goalId": origin, "msgId": mid}}},
                "placements": {}, "status": {g: "working"}}))
        write_origin(sender + ":g1", "m-y.2")
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertEqual(card["origin"]["peer"], "sendersess", "the badge survives absorption")
        self.assertFalse(card["origin"]["live"], "sender's linked goal is done → absorbed → live False")
        write_origin(None, "m-y.3")                              # no link at all
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertFalse(card["origin"]["live"], "no link (goalId null) → absorbed")
        write_origin(sender + ":gGONE", "m-y.4")                 # link to a goal that no longer exists
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertFalse(card["origin"]["live"], "link to a missing goal → absorbed")

    def test_feed_tree_ships_the_handoff_kind_with_the_recipients_identity(self):
        """The sender's "↪ delegated to <peer>" tracking node ships kind "handoff" + the RECIPIENT's
        identity (from the courier-recorded handoff.peer — exact, never inferred), so the feed's
        long-dormant delegations section finally populates and the checklist stops showing a bare
        text row with no visible cross-card link (the user 2026-08-16)."""
        recip = "99998888-7777-6666-5555-444433332222"
        (jd.NAMES / recip).write_text("recipsess\t/elsewhere\t#22cc88\n")
        top, leaf, own = "%s:g20" % SID, "%s:g21" % SID, "%s:g22" % SID
        # the top keeps ONE ordinary leaf of its own — a pure-delegation top (every leaf a handoff)
        # is suppressed from the feed entirely, by design
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 22, "lastNode": None,
            "nodes": {top: {"id": top, "text": "Broader goal", "parentId": None, "nodeComplete": False,
                            "blocked": False, "cleared": False, "trail": [], "t": NOW - 80},
                      leaf: {"id": leaf, "text": "↪ delegated to recipsess: run the sweep", "parentId": top,
                             "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                             "t": NOW - 70, "handoff": {"peer": recip, "msgId": "m-h.1"}},
                      own: {"id": own, "text": "Own remaining step", "parentId": top,
                            "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                            "t": NOW - 65}},
            "placements": {}, "status": {top: "working"}}))
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        row = next(n for n in card["tree"] if n["id"] == leaf)
        self.assertEqual(row["kind"], "handoff")
        self.assertEqual(row["who"], "recipsess", "the row wears the RECIPIENT's identity")
        self.assertEqual(row["whoSid"], recip)
        plain = next(n for n in card["tree"] if n["id"] == top)
        self.assertEqual(plain["kind"], "ask", "ordinary nodes are untouched")

    def test_consolidator_never_absorbs_an_origin_top(self):
        """Umbrella absorption makes a top a non-top, and build_feed reads origin from the TOP only —
        so consolidating an origin-carrying completed card would erase the "↪ from <peer>" provenance
        from the board entirely (the user 2026-08-16). Excluded from the candidate forest."""
        sender = "11112222-3333-4444-5555-666677778888"
        g1, g2 = "%s:g30" % SID, "%s:g31" % SID
        store = {"rompUuid": SID, "seq": 31, "lastNode": None,
                 "nodes": {g1: {"id": g1, "text": "Native done goal", "parentId": None, "nodeComplete": True,
                                "blocked": False, "cleared": False, "trail": [], "t": NOW - 60},
                           g2: {"id": g2, "text": "Delegated done goal", "parentId": None, "nodeComplete": True,
                                "blocked": False, "cleared": False, "trail": [], "t": NOW - 50,
                                "origin": {"peer": sender, "goalId": sender + ":g1", "msgId": "m-c.1"}}},
                 "placements": {}, "status": {g1: "completed", g2: "completed"}}
        cands = [nd["id"] for nd in jd._consolidate_tops(store)]
        self.assertIn(g1, cands)
        self.assertNotIn(g2, cands, "provenance stays on the board — its card keeps its own face")

    def test_feed_clear_and_undo(self):
        g1 = "%s:g1" % SID
        self.assertTrue(any(a["itemId"] == g1 for a in km.build_feed(NOW)["asks"]))
        km._clear_ask(g1)
        d = km.build_feed(NOW)
        self.assertFalse(any(a["itemId"] == g1 for a in d["asks"]), "a cleared ask is hidden")
        self.assertTrue(d["canUndoClear"]); self.assertEqual(d["dismissedCount"], 1)
        km._undo_clear()
        d2 = km.build_feed(NOW)
        self.assertTrue(any(a["itemId"] == g1 for a in d2["asks"]), "undo restores it")
        self.assertFalse(d2["canUndoClear"])

    def test_clear_sets_durable_node_flag_and_undo_unsets_it(self):
        # The reappearance fix (the user 2026-06-18): a Clear is no longer only a view-level hide — it
        # stamps the DURABLE node-level `cleared` flag (rolled-up status → "cleared"), so the grouper
        # (which keys on nd['cleared']) can't re-wrap the top under a fresh umbrella id, and the settled
        # gate can't bounce it back to working/completed. Undo un-stamps it and the node rejoins its
        # real status.
        g1 = "%s:g1" % SID
        km._clear_ask(g1)
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][g1]["cleared"], "Clear sets the durable node flag")
        self.assertEqual(store["status"][g1], "cleared", "rolled-up status is 'cleared'")
        km._undo_clear()
        store2 = jd.load_goals(SID)
        self.assertFalse(store2["nodes"][g1]["cleared"], "Undo un-sets the durable flag")
        self.assertNotEqual(store2["status"][g1], "cleared", "the node rejoins its real status")

    def test_feed_clear_all_then_undo_restores_the_batch(self):
        d0 = km.build_feed(NOW)
        ids = [a["itemId"] for a in d0["asks"]]
        self.assertTrue(ids, "fixture has cards to clear")
        km._clear_all(ids)
        d1 = km.build_feed(NOW)
        self.assertEqual(len(d1["asks"]), 0, "clear-all empties the feed")
        self.assertTrue(d1["canUndoClear"])
        km._undo_clear()
        d2 = km.build_feed(NOW)
        self.assertEqual(len(d2["asks"]), len(d0["asks"]), "one undo restores the whole batch")
        self.assertFalse(d2["canUndoClear"])

    def test_alive_filter_drops_dead_sessions(self):
        # the hard filter: only sessions alive in tmux appear anywhere (feed/timeline/chat tabs)
        self.assertEqual(km._alive_sessions(NOW, {"other-sid": {}}), [], "dead session dropped")
        alive = km._alive_sessions(NOW, {SID: {"state": "working"}})
        self.assertEqual([s["sid"] for s in alive], [SID])

    def test_clear_all_clears_blocked_too(self):
        d0 = km.build_feed(NOW)
        self.assertTrue([a for a in d0["asks"] if a["column"] == "needs_input"], "fixture has a blocked ask")
        km._clear_all([a["itemId"] for a in d0["asks"]])
        self.assertEqual([a for a in km.build_feed(NOW)["asks"] if a["column"] == "needs_input"], [],
                         "clear-all clears the blocked column too")

    def test_chat_chip_working_is_event_model_not_tmux(self):
        # @claude-state says "working" but the fixture's turn ENDED -> chip is ready, not working:
        # working is the stable event-model signal (open turn), not the laggy tmux state (the user's
        # "working shows blue / flickers" regression)
        km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 5, "model": "Opus 4.8",
                                           "effort": "max", "context": 30, "compactPct": None, "color": None}}
        self.assertEqual(km.build_session(SID, NOW)["status"]["state"], "ready",
                         "ended turn -> ready even when tmux says working")

    def test_no_hidden_tab_state_exists(self):
        # hidden tabs are GONE (the user 2026-08-11): a running session is always visible — × means End
        # session. The old _set_hidden_tab/_hidden_tabs pair must stay deleted, or a secret running
        # session (no tab, no Fleet row, still judged and billed) comes back.
        self.assertFalse(hasattr(km, "_hidden_tabs"))
        self.assertFalse(hasattr(km, "_set_hidden_tab"))

    def test_open_dead_session_prompts_revive(self):
        # opening a DEAD session pops the chat's confirmRevive modal — no silent reopen — and now from
        # ANY pane (feed/timeline included), since dead = timeline-only (the user 2026-06-17). A LIVE
        # session just reopens/focuses, no prompt.
        # _reveal_chat_for since 2026-07-29: the reveal is aimed at the dashboard that asked (its wid),
        # so a jump in one window no longer drags every other open one to the same turn. With no client
        # in scope it still broadcasts, which is the path this exercises.
        cap, orig_rc, orig_tx, orig_pa = [], km._reveal_chat_for, km._tmux_sessions, km._push_all
        try:
            km._reveal_chat_for = lambda c, m: cap.append(m)
            km._push_all = lambda: None
            km._tmux_sessions = lambda: {SID: {}}            # SID alive; deadsid000 dead
            cap.clear(); km._open_or_revive("deadsid000")
            self.assertEqual([m["type"] for m in cap], ["confirmRevive"])
            self.assertEqual(cap[0]["id"], "deadsid000")
            cap.clear(); km._open_or_revive(SID)
            self.assertFalse(any(m.get("type") == "confirmRevive" for m in cap), "a live session reopens, no prompt")
            focus = next(m for m in cap if m.get("type") == "focus" and m.get("id") == SID)
            self.assertNotIn("live", focus, "a plain open focuses without forcing the live tail")
            # `live=True` (a blocked card's picker chip) → the focus carries live so the chat lands on the prompt
            cap.clear(); km._open_or_revive(SID, live=True)
            live_focus = next(m for m in cap if m.get("type") == "focus" and m.get("id") == SID)
            self.assertTrue(live_focus.get("live"), "live open lands the chat on its live tail (the picker prompt)")
        finally:
            km._reveal_chat_for = orig_rc; km._tmux_sessions = orig_tx; km._push_all = orig_pa

    def test_revive_session_resumes(self):
        # confirming the modal's "Revive" must actually resume the session. The kernel owns the resume
        # now (`romp <name> --resume <sid> --detach`): the old `romp-postal-service revive` subcommand
        # was REMOVED in 2b5e181 but _revive_session kept shelling it — the CLI exits 0 on unknown
        # commands with output DEVNULL'd, so the picker's Revive silently did nothing (the user
        # 2026-07-05). Full coverage: tests/test_kernel_revive.py.
        import subprocess as _sp
        calls, saved = [], km.subprocess.run
        km.subprocess.run = (lambda *a, **k:
                             calls.append(list(a[0])) or _sp.CompletedProcess(a[0], 0, "", ""))
        try:
            km._revive_session("deadsid000")
        finally:
            km.subprocess.run = saved
        self.assertTrue(calls, "revive must shell out to the resume path")
        argv = calls[0]
        self.assertTrue(str(argv[0]).endswith("/romp"),
                        "the kernel owns the resume (bin/romp), never the removed postal subcommand")
        # --name pins the recorded name; this fixture has none, so _name_of falls back to the sid
        self.assertEqual(argv[1:], ["resume", "deadsid000", "--name", "deadsid000", "--detach"])

    def test_split_reminders(self):
        p, r = km._split_reminders("do the thing <system-reminder>be careful</system-reminder> now")
        self.assertNotIn("system-reminder", p); self.assertIn("do the thing", p); self.assertIn("now", p)
        self.assertEqual(r, ["be careful"])
        self.assertEqual(km._split_reminders("plain prompt"), ("plain prompt", []))
        # background-task notifications are peeled too, so they don't render as a blue "your message"
        # bubble (the user 2026-06-16). A message that's ONLY a notification → empty prompt.
        p2, r2 = km._split_reminders("<task-notification><task-id>abc</task-id> done (exit code 0)</task-notification>")
        self.assertEqual(p2, "", "a pure task-notification leaves no prompt → no bubble")
        self.assertEqual(len(r2), 1)
        self.assertIn("exit code 0", r2[0])
        # mixed: a real prompt with both kinds of injected block → only the prompt survives
        p3, r3 = km._split_reminders("real ask <system-reminder>x</system-reminder> mid <task-notification>y</task-notification> end")
        self.assertNotIn("task-notification", p3); self.assertNotIn("system-reminder", p3)
        self.assertIn("real ask", p3); self.assertIn("mid", p3); self.assertIn("end", p3)
        self.assertEqual(r3, ["x", "y"])

    def test_img_hydration_and_dropped_file_host_handlers(self):
        # ported host handlers (the user 2026-06-16): a path-image hydrates to a data: URL, and a
        # dropped file's bytes are saved under the state dir's drops/ for the prompt to reference.
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
        p = os.path.join(self.td.name, "shot.png")
        with open(p, "wb") as f:
            f.write(png)
        self.assertTrue((km._img_data_url(p) or "").startswith("data:image/png;base64,"))
        self.assertIsNone(km._img_data_url("relative/x.png"), "non-absolute path → None")
        self.assertIsNone(km._img_data_url("/tmp/not-an-image.txt"), "non-image extension → None")
        fp = km._save_dropped_file("My Shot!.png", base64.b64encode(b"hello").decode())
        self.assertTrue(os.path.isfile(fp))
        self.assertEqual(open(fp, "rb").read(), b"hello")
        self.assertIn("drops", fp)
        # a save that fails returns None — and the WS handler NACKS it (dropSaveFailed) so the
        # client's pending chip never pulses forever over a file that is not coming (fail loudly,
        # the user 2026-08-11; source pin — the branch lives inline in the WS message loop)
        self.assertIsNone(km._save_dropped_file("bad.png", "%%%not-base64%%%"), "undecodable bytes → None")
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('_reply(client, {"type": "dropSaveFailed", "name": str(msg["name"])})', src)

    def test_permission_mode_cycle_presses(self):
        # shift+tab press count from current → target in the cycle (the user 2026-06-16): there's no
        # slash command for permission mode, so the picker cycles like the terminal UI.
        self.assertEqual(km._MODE_CYCLE, ["auto", "default", "acceptEdits", "plan"])
        self.assertEqual(km._mode_presses("default", "acceptEdits"), 1)
        self.assertEqual(km._mode_presses("default", "plan"), 2)
        self.assertEqual(km._mode_presses("acceptEdits", "plan"), 1)
        self.assertEqual(km._mode_presses("plan", "auto"), 1)               # wraps forward to the top of the cycle
        self.assertEqual(km._mode_presses("plan", "default"), 2)            # plan(3) → auto(0) → default(1)
        self.assertEqual(km._mode_presses("auto", "plan"), 3)              # auto(0) → … → plan(3)
        self.assertEqual(km._mode_presses("plan", "plan"), 0)              # already there → no presses
        self.assertIsNone(km._mode_presses("default", "bypassPermissions"))  # flag-only, not a cycle target
        self.assertIn("@claude-permission-mode", km.TmuxBackend.LANE_FMT)  # kernel reads the mode var (now a TmuxBackend const)

    def test_cycle_mode_records_the_new_mode(self):
        # The user's bug (2026-06-18): a chat mode switch flipped the terminal but the chat LABEL stayed
        # stale. Claude Code never exposes the permission mode in its statusLine JSON, so @claude-permission-
        # mode has no event source to self-heal from — _cycle_mode must record the mode it just cycled to,
        # or the var stays frozen (and the next press count is computed from a stale `cur`).
        calls, saved_run, saved_sleep = [], km.subprocess.run, km.time.sleep
        saved_tmux, saved_thread, saved_push = km._tmux_sessions, km.threading.Thread, km._push_all
        class _SyncThread:                                  # run go() inline so the test sees the result
            def __init__(self, target=None, daemon=None): self._t = target
            def start(self): self._t()
        km.subprocess.run = lambda args, **k: calls.append(list(args)) or type("R", (), {"stdout": ""})()
        km.time.sleep = lambda *_a, **_k: None
        km._tmux_sessions = lambda: {SID: {"mode": "auto"}}   # current mode is auto
        km.threading.Thread = _SyncThread
        km._push_all = lambda: calls.append(["__push_all__"])
        try:
            km._cycle_mode("mysess", SID, "plan")
        finally:
            km.subprocess.run, km.time.sleep = saved_run, saved_sleep
            km._tmux_sessions, km.threading.Thread, km._push_all = saved_tmux, saved_thread, saved_push
        btab = [c for c in calls if c[:2] == ["tmux", "send-keys"] and "BTab" in c]
        self.assertEqual(len(btab), 3, "auto → plan is 3 shift+tab presses")
        self.assertIn(["tmux", "set", "-t", "mysess", "@claude-permission-mode", "plan"], calls,
                      "after cycling, the kernel records the new mode so the chat label updates")
        self.assertIn(["__push_all__"], calls, "and re-renders so the label flips immediately")

    def test_tmux_set_mode_refuses_a_mode_the_cycle_cannot_reach(self):
        # The picker gained Bypass for SDK sessions (the user 2026-08-15). shift+tab is the only handle
        # the TUI gives us, so a tmux session cannot reach bypassPermissions/dontAsk at all — and
        # set_mode used to return True regardless, telling the caller a permission mode had been set
        # when _cycle_mode had already declined it. Refuse, so the kernel can say so.
        saved_tmux, saved_cycle = km._tmux_sessions, km._cycle_mode
        cycled = []
        km._tmux_sessions = lambda: {SID: {"mode": "auto"}}
        km._cycle_mode = lambda name, sid, target: cycled.append(target)
        try:
            be = km.TmuxBackend()
            self.assertFalse(be.set_mode(SID, "bypassPermissions"), "no keystroke reaches it → say no")
            self.assertFalse(be.set_mode(SID, "dontAsk"), "same for the other flag-only mode")
            self.assertEqual(cycled, [], "and don't pretend to cycle")
            for m in km._MODE_CYCLE:
                self.assertTrue(be.set_mode(SID, m), "every cycle mode still goes through: %s" % m)
            self.assertEqual(cycled, list(km._MODE_CYCLE))
        finally:
            km._tmux_sessions, km._cycle_mode = saved_tmux, saved_cycle

    def test_recency_colormap_chooser(self):
        # the colormap chooser (the user 2026-06-16): several perceptually-uniform maps + a persisted pick.
        for name in ("hawaii", "viridis", "magma", "inferno", "plasma", "cividis"):
            self.assertIn(name, km.cm.COLORMAPS)
        age = 3600
        self.assertNotEqual(km.cm.age_rgb(age, "viridis"), km.cm.age_rgb(age, "hawaii"))  # name changes the colour
        self.assertEqual(km.cm.age_rgb(age, "nope"), km.cm.age_rgb(age, "aurora"))        # unknown → default (aurora)
        # kernel selection: default aurora (the user 2026-06-27), set persists + round-trips, unknown ignored
        (km.jd.STATE / "colormap").unlink(missing_ok=True)                                # no persisted pick → default
        self.assertEqual(km._colormap(), "aurora")
        km._set_colormap("magma"); self.assertEqual(km._colormap(), "magma")
        km._set_colormap("bogus"); self.assertEqual(km._colormap(), "magma")             # unknown ignored
        # the gear exposes the chooser (now a bar-options dropdown — see test_gear_colormap_dropdown_options_*)
        # and posts setColormap to the kernel
        self.assertIn("id=rs-cmap-btn", _gear_src())
        self.assertIn("setColormap", _gear_src())

    def test_webview_colormaps_match_the_kernel(self):
        # the ledger (render.ts) colours recency itself, while the feed/modals get colour from the
        # kernel's trgb — so for ONE global colormap to actually match across views (the user 2026-06-17)
        # the two stop tables must be IDENTICAL. Guard against drift.
        import re
        here = os.path.dirname(os.path.realpath(__file__))
        render = open(os.path.join(here, "..", "ui", "webview", "render.ts")).read()
        for name, stops in km.cm.COLORMAPS.items():
            m = re.search(r"\b" + name + r":\s*(\[\[.*?\]\])", render)
            self.assertIsNotNone(m, "render.ts COLORMAPS is missing '%s'" % name)
            nums = [int(x) for x in re.findall(r"-?\d+", m.group(1))]
            flat = [c for stop in stops for c in stop]
            self.assertEqual(nums, flat, "render.ts '%s' stops drifted from romp_colormap.py" % name)

    def test_name_of_resolves_sid(self):
        # a postal atom's peer is the sender's SID; resolve it to a name (+ color via _name_color)
        self.assertEqual(km._name_of(SID), "testsess")
        self.assertEqual(km._name_color(SID), {"bg": "#abcdef", "fg": "#ffffff"})
        self.assertIsNone(km._name_of("no-such-sid"))

    def test_postal_connectors(self):
        # timeline message connectors from the postal log: a sent row joined to its exec by id. At least
        # ONE end must be a local lane — a CROSS-MACHINE message's far end is a sid this kernel has never
        # seen, and it is emitted ONE-SIDED so the browser's federation merge can stitch it onto the peer
        # host's lane (stitchMessages); a connector matching no lane is dropped by the view, as before.
        md = jd.STATE / "timeline"; md.mkdir(parents=True, exist_ok=True)
        a, b = "aaaa1111", "bbbb2222"
        (md / "messages.jsonl").write_text(
            json.dumps({"ev": "sent", "id": "m1", "from_id": a, "to_id": b, "t": NOW - 30,
                        "from": "alpha", "body": "do X"}) + "\n"
            + json.dumps({"ev": "exec", "id": "m1", "t": NOW - 20}) + "\n"
            + json.dumps({"ev": "sent", "id": "m2", "from_id": a, "to_id": "foreignsid", "t": NOW - 30,
                          "from": "alpha", "body": "y"}) + "\n"
            + json.dumps({"ev": "sent", "id": "m3", "from_id": "strange1", "to_id": "strange2",
                          "t": NOW - 30, "from": "who", "body": "z"}) + "\n"
            + json.dumps({"ev": "sent", "id": "m4", "from_id": "", "to_id": a, "t": NOW - 30,
                          "from": "Romp Postal Service", "body": "bounce"}) + "\n")
        msgs = {m["id"]: m for m in km._postal_messages(NOW, {a, b}, {a: "alpha", b: "beta"})}
        self.assertEqual(set(msgs), {"m1", "m2"},
                         "one local end suffices; neither-end-local and bus-origin (no sender sid) stay dropped")
        m = msgs["m1"]
        self.assertEqual((m["fromId"], m["toId"]), (a, b))
        self.assertEqual(m["exec"], NOW - 20); self.assertTrue(m["hasExec"]); self.assertFalse(m["pending"])
        self.assertEqual(m["text"], "do X")
        self.assertEqual(msgs["m2"]["toId"], "foreignsid")
        self.assertEqual(msgs["m2"]["to"], "", "foreign recipient: no local name — the merge fills it")

    def test_postal_exec_joins_regardless_of_recipient_liveness(self):
        # the 2026-08-24 floating-point report's leg (b), verified CLEAN and pinned: a message
        # consumed by a session/thread that later DIED keeps its real exec — the join is by id,
        # never gated on alive_sids, so the view can draw the true sent→exec span for dead-thread
        # mail instead of an un-arrived point
        md = jd.STATE / "timeline"; md.mkdir(parents=True, exist_ok=True)
        a = "aaaa1111"
        (md / "messages.jsonl").write_text(
            json.dumps({"ev": "sent", "id": "m9", "from_id": a, "to_id": "dead-thread-sid",
                        "t": NOW - 300, "from": "alpha", "body": "do the piece"}) + "\n"
            + json.dumps({"ev": "exec", "id": "m9", "t": NOW - 60}) + "\n")
        m = km._postal_messages(NOW, {a}, {a: "alpha"})[0]
        self.assertTrue(m["hasExec"], "the exec joined although the recipient is in no alive set")
        self.assertEqual(m["exec"], NOW - 60)
        self.assertFalse(m["pending"])

    def test_postal_connector_ships_no_dead_goal_binding(self):
        # toGoal (the courier-planted goal id) shipped on every connector but no view ever rendered it —
        # dropped (2026-07-07 payload audit), along with the hardcoded-False `parked`.
        md = jd.STATE / "timeline"; md.mkdir(parents=True, exist_ok=True)
        a, b = "aaaa1111", "bbbb2222"
        (md / "messages.jsonl").write_text(
            json.dumps({"ev": "sent", "id": "m1", "from_id": a, "to_id": b, "t": NOW - 30, "from": "alpha", "body": "do X"}) + "\n")
        msgs = {m["id"]: m for m in km._postal_messages(NOW, {a, b}, {a: "alpha", b: "beta"})}
        self.assertNotIn("toGoal", msgs["m1"])
        self.assertNotIn("parked", msgs["m1"])

    def test_postal_connector_summary_from_captions(self):
        # the connector carries the caption (from _msg_summaries, now sourced from captions/); the
        # timeline shows it over the verbose raw body, which stays as the fallback
        md = jd.STATE / "timeline"; md.mkdir(parents=True, exist_ok=True)
        a, b = "aaaa1111", "bbbb2222"
        (md / "messages.jsonl").write_text(
            json.dumps({"ev": "sent", "id": "m1", "from_id": a, "to_id": b, "t": NOW - 30, "from": "alpha",
                        "body": "a long verbose body that the user finds too noisy"}) + "\n"
            + json.dumps({"ev": "exec", "id": "m1", "t": NOW - 20}) + "\n")
        saved = km._msg_summaries
        km._msg_summaries = lambda: {"m1": "asked for X"}
        try:
            m = km._postal_messages(NOW, {a, b}, {a: "alpha", b: "beta"})[0]
        finally:
            km._msg_summaries = saved
        self.assertEqual(m["summary"], "asked for X", "connector carries the caption")
        self.assertEqual(m["text"], "a long verbose body that the user finds too noisy", "raw body kept as fallback")

    def test_postal_card_carries_caption(self):
        # the incoming CHAT card carries the caption too (renderPostalService shows it over the verbose body,
        # full message on hover); the raw body stays as the fallback
        saved = km._msg_summaries
        km._msg_summaries = lambda: {"m1": "asks to rebase onto main"}
        try:
            ev = {"kind": "user", "md": "see this <!-- romp-msg-id: m1 -->", "uuid": "u", "ts": "t"}
            index = {"m1": {"from": "alpha", "fromId": None, "body": "a long verbose handoff body the user finds noisy",
                            "id": "m1", "t": NOW - 30, "park": False}}
            cards = km._hydrate_postal([ev], index)
        finally:
            km._msg_summaries = saved
        self.assertEqual(len(cards), 1)
        self.assertEqual((cards[0]["kind"], cards[0]["direction"]), ("postal-service", "in"))
        self.assertEqual(cards[0]["summary"], "asks to rebase onto main", "card carries the caption")
        self.assertEqual(cards[0]["body"], "a long verbose handoff body the user finds noisy", "raw body kept (hover)")

    def test_msg_summaries_joins_caption_to_msgid(self):
        # _msg_summaries maps a postal msgId -> the caption of the RECIPIENT segment that bore the
        # romp-msg-id marker, sourced from captions/ (replacing the retired message-summaries.jsonl
        # the old backfill wrote). Join: msgId --_seg_mids--> segment --captions/--> its caption.
        recs = [uline(T0, "please take this over <!-- romp-msg-id: m1 -->", "p1", ps="typed"),
                aline(T0 + 10, "Picked it up.", "pa1", "p1", stop="end_turn")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        saved_t, saved_sess = km.time.time, km._sessions
        km.time.time = lambda: NOW                       # keep _parse's window consistent with the fixture
        km._sessions = lambda now: [{"sid": SID, "name": "testsess", "path": str(self.tpath), "mtime": NOW}]
        try:
            session = km._parse(str(self.tpath), SID, NOW)
            seg = next(s for t in session["turns"] for s in em.segments(t) if "m1" in km._seg_mids(s))
            (jd.CAPDIR / (SID + ".jsonl")).write_text(json.dumps(
                {"id": seg["id"], "grain": "segment", "t": seg["t"], "caption": "handoff received"}) + "\n")
            km._msg_sum_cache.clear()
            self.assertEqual(km._msg_summaries().get("m1"), "handoff received")
        finally:
            km.time.time, km._sessions = saved_t, saved_sess
            km._msg_sum_cache.clear()

    def test_msg_summaries_rescans_only_changed_sessions(self):
        # the user 2026-07-03 (who found startup slow and opening each session slow): the old memo keyed the whole
        # map on the FLEET signature, so any one session writing re-scanned ALL transcripts on every
        # build_session (~1.2s/open on a busy fleet). Now each session's submap caches against its OWN
        # mtime — an unchanged peer is never re-scanned, only the session that actually changed is.
        scanned = []
        real_scan = km._msg_sum_scan_session
        km._msg_sum_scan_session = lambda sid, path, now: (scanned.append(sid) or {sid + ":m": "cap"})
        saved_sess = km._sessions
        fleet = [{"sid": "A", "name": "a", "path": "/x/a", "mtime": 100},
                 {"sid": "B", "name": "b", "path": "/x/b", "mtime": 100}]
        km._sessions = lambda now: fleet
        try:
            km._msg_sum_cache.clear()
            m = km._msg_summaries()
            self.assertEqual(sorted(scanned), ["A", "B"], "first build scans the whole fleet once")
            self.assertEqual(m, {"A:m": "cap", "B:m": "cap"}, "the union covers every session")
            scanned.clear()
            km._msg_summaries()
            self.assertEqual(scanned, [], "nothing changed → no re-scan at all (was: re-scan everything)")
            fleet[1] = {**fleet[1], "mtime": 200}          # only B wrote (the session you opened)
            km._msg_summaries()
            self.assertEqual(scanned, ["B"], "only the changed session re-scans; the peer stays cached")
            fleet.pop()                                     # B died
            m2 = km._msg_summaries()
            self.assertEqual(m2, {"A:m": "cap"}, "a dead session drops from the union (per-cache can't grow unbounded)")
        finally:
            km._msg_sum_scan_session = real_scan
            km._sessions = saved_sess
            km._msg_sum_cache.clear()

    def test_seg_mids_extracts_markers(self):
        seg = {"atoms": [
            {"message": {"content": [{"type": "text", "text": "hi <!-- romp-msg-id: m1 -->"}]}},
            {"message": {"content": [{"type": "tool_result", "content": "inbox: <!-- romp-msg-id: m2 -->"}]}},
            {"message": {"content": "plain <!-- romp-msg-id: m3 -->"}}]}
        self.assertEqual(set(km._seg_mids(seg)), {"m1", "m2", "m3"},
                         "msg ids from text blocks, check_inbox tool_results, and string content")

    def test_bind_message_exec_id_join(self):
        """A connector binds its exec to the recipient segment that carries its msg id (process-start),
        so the line shows transit = sent → became-actionable, not the log delivery time."""
        turns = {"B": [{"start": 1000, "mids": ["m1"], "prompt": "x"}]}
        messages = [{"id": "m1", "toId": "B", "fromId": "A", "from": "alpha", "fromOrig": "alpha",
                     "sent": 900, "exec": 905, "pending": False}]
        km._bind_message_execs(messages, turns)
        self.assertEqual(messages[0]["exec"], 1000, "exec bound to the recipient's process-start")
        self.assertFalse(messages[0]["pending"])

    def test_bind_message_exec_text_heuristic(self):
        """No marker on the recipient turn → bind by a turn soon after send whose prompt names the sender."""
        turns = {"B": [{"start": 1000, "mids": [], "prompt": "picking up a note from alpha"}]}
        messages = [{"id": "m9", "toId": "B", "fromId": "A", "from": "alpha", "fromOrig": "alpha",
                     "sent": 998, "exec": 998, "pending": False}]
        km._bind_message_execs(messages, turns)
        self.assertEqual(messages[0]["exec"], 1000, "bound by the sender-naming turn")

    def test_bind_message_exec_unbound_left_alone(self):
        turns = {"B": [{"start": 1000, "mids": [], "prompt": "unrelated work"}]}
        messages = [{"id": "mz", "toId": "B", "from": "alpha", "fromOrig": "alpha",
                     "sent": 900, "exec": 905, "pending": True}]
        km._bind_message_execs(messages, turns)
        self.assertEqual((messages[0]["exec"], messages[0]["pending"]), (905, True),
                         "no id-join and no text match → connector keeps its log exec/pending")

    def test_goal_segments_collects_subtree_trails(self):
        """_goal_segments(goalId) → every segment id in the goal's subtree (the timeline work-bars to
        light when the feed card is hovered — showAskPath reverse highlight)."""
        top, sub, step = "%s:g1" % SID, "%s:g2" % SID, "%s:g3" % SID
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 3,
            "nodes": {
                top:  {"id": top,  "text": "Top",  "parentId": None, "nodeComplete": False, "blocked": False,
                       "cleared": False, "trail": ["segA"], "t": NOW - 90},
                sub:  {"id": sub,  "text": "Sub",  "parentId": top,  "nodeComplete": False, "blocked": False,
                       "cleared": False, "trail": ["segB", "segC"], "t": NOW - 80},
                step: {"id": step, "text": "Step", "parentId": sub,  "nodeComplete": True,  "blocked": False,
                       "cleared": False, "trail": ["segD"], "t": NOW - 70}},
            "placements": {}, "status": {top: "working"}}))
        self.assertEqual(set(km._goal_segments(top)), {"segA", "segB", "segC", "segD"}, "the whole subtree")
        self.assertEqual(set(km._goal_segments(sub)), {"segB", "segC", "segD"}, "a sub-goal → itself + its steps")
        self.assertEqual(km._goal_segments("%s:gX" % SID), [], "unknown goal → empty")

    def test_session_events_and_ledger_carry_tlid_dot_vs_bar(self):
        """Each event carries tlId — a message/prompt → the segment's DOT (promptId), work → the BAR
        (workId) — and a TOC bullet → the turn's DOT, so a chat hover lights the right glyph (the
        restored dot/bar split)."""
        m = km.build_session(SID, NOW)
        seg = em.segments(em.parse_session(str(self.tpath), rompuuid=SID,
                                           candidate_files=[str(self.tpath)], now=NOW)["turns"][0])[0]
        prompt_id, work_id = seg["trigger"], km._seg_anchors(seg["atoms"])[0]
        u = next(e for e in m["events"] if e["kind"] == "user")
        self.assertEqual(u["tlId"], prompt_id, "a user message lights the DOT (promptId)")
        work = [e for e in m["events"] if e["kind"] in ("assistant", "tool")]
        self.assertTrue(work and all(e["tlId"] == work_id for e in work), "work events light the BAR (workId)")

    def test_timeline_bars_carry_prompt_and_work_ids(self):
        """Timeline bars carry promptId (the dot atom) + workId (the bar atom) — the targets the chat
        hover's tlId matches, splitting message→dot from work→bar in the view's dotLit/barLit."""
        bars = km.build_timeline(NOW)["turns"][SID]
        seg = em.segments(em.parse_session(str(self.tpath), rompuuid=SID,
                                           candidate_files=[str(self.tpath)], now=NOW)["turns"][0])[0]
        self.assertEqual(bars[0]["promptId"], seg["trigger"], "bar promptId = the prompt atom (dot)")
        self.assertEqual(bars[0]["workId"], km._seg_anchors(seg["atoms"])[0], "bar workId = the first work atom (bar)")

    def test_hydrate_postal_in_uses_clean_body_not_boilerplate(self):
        """A received message (user text with the romp-msg-id marker) → a clean 'in' card whose body
        comes from the timeline log, NOT the delivered #### banner/footer boilerplate (the user)."""
        a = "aaaa1111"
        (jd.NAMES / a).write_text("alpha\t/dir\t#00ff00\n")
        index = {"m1": {"id": "m1", "from": "alpha", "fromId": a, "toId": SID,
                        "body": "the clean message", "t": NOW - 10, "park": False}}
        raw = ("####################\n## 📬 from alpha\n####################\n"
               "the clean message\n<!-- romp-msg-id: m1 -->\n(to reply: romp --mail send ...)")
        out = km._hydrate_postal([{"kind": "user", "md": raw, "uuid": "u1", "ts": "x", "human": False}], index)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["kind"], out[0]["direction"]), ("postal-service", "in"))
        self.assertEqual(out[0]["body"], "the clean message", "renders the log body, not the boilerplate")
        self.assertEqual(out[0]["peer"], "alpha")
        self.assertEqual(out[0]["color"], {"bg": "#00ff00", "fg": "#ffffff"})

    def test_hydrate_postal_out_from_send_tool(self):
        """A send_message tool call → an OUTGOING card (the sent-message rendering the user wants back)."""
        (jd.NAMES / "zzzz9999").write_text("beta\t/dir\t#0000ff\n")
        ev = {"kind": "tool", "name": "mcp__romp-postal__send_message",
              "input": json.dumps({"to": "beta", "body": "ASK: do X"}), "output": "Delivered to 'beta'.",
              "isError": False, "uuid": "t1", "ts": "x"}
        out = km._hydrate_postal([ev], {})
        self.assertEqual((out[0]["kind"], out[0]["direction"]), ("postal-service", "out"))
        self.assertEqual((out[0]["peer"], out[0]["body"], out[0]["status"]), ("beta", "ASK: do X", "delivered"))
        self.assertEqual(out[0]["color"], {"bg": "#0000ff", "fg": "#ffffff"}, "recipient color resolved by name")

    def test_hydrate_postal_out_from_cli_bash_send(self):
        """A `romp --mail send` Bash call → an outgoing card too, once delivery is confirmed. The
        dashed spelling is the pre-round-3 one — old transcripts carry it forever, so it must keep
        matching."""
        ev = {"kind": "tool", "name": "Bash", "input": 'romp --mail send beta "hi there"',
              "output": "[romp mail] delivered to beta", "isError": False, "uuid": "t2", "ts": "x"}
        out = km._hydrate_postal([ev], {})
        self.assertEqual((out[0]["direction"], out[0]["peer"], out[0]["body"]), ("out", "beta", "hi there"))

    def test_hydrate_postal_out_from_cli_bash_send_bare_spelling(self):
        """`romp mail send` (the round-3 spelling, 2026-07-25) matches the same outgoing-card path."""
        ev = {"kind": "tool", "name": "Bash", "input": 'romp mail send --kind question beta "when?"',
              "output": "[romp mail] delivered to beta", "isError": False, "uuid": "t3", "ts": "x"}
        out = km._hydrate_postal([ev], {})
        self.assertEqual((out[0]["direction"], out[0]["peer"], out[0]["body"], out[0]["intent"]),
                         ("out", "beta", "when?", "question"))

    def test_hydrate_postal_passes_through_unresolved(self):
        """A marker with no matching log entry stays a plain event (never half-rendered) — but KEEPS its
        ids, so a deep-link carrying the message id can still land on the turn (2026-07-23). It used to be
        returned byte-identical, which silently cost the turn its only handle. A plain event is untouched."""
        ev = {"kind": "user", "md": "hi <!-- romp-msg-id: missing -->", "uuid": "u9"}
        out = km._hydrate_postal([ev], {})
        self.assertEqual([e["kind"] for e in out], ["user"], "unresolved marker → still not a card")
        self.assertEqual(out[0]["md"], ev["md"], "...and the body is unchanged")
        self.assertEqual((out[0]["mid"], out[0]["mids"]), ("missing", ["missing"]), "but the id survives")
        plain = {"kind": "assistant", "md": "just a reply", "uuid": "a1"}
        self.assertEqual(km._hydrate_postal([plain], {}), [plain], "a non-postal event is untouched")

    def test_postal_cards_carry_declared_kind_as_intent(self):
        """Every postal card surfaces the sender-declared kind as `intent`, so the chat can restore the
        interaction-type chip (the user 2026-07-15: the chip vanished when send_message moved the kind from
        a leading body token to an explicit `kind` param). Covers all three sources + the legacy marker."""
        # outgoing via the MCP tool — kind from the tool input
        mcp = {"kind": "tool", "name": "mcp__romp-postal__send_message",
               "input": json.dumps({"to": "beta", "body": "please do X", "kind": "delegate"}),
               "output": "Delivered to 'beta'.", "isError": False, "uuid": "t1", "ts": "x"}
        self.assertEqual(km._hydrate_postal([mcp], {})[0]["intent"], "delegate")
        # outgoing via the CLI — kind from --kind (the recipient/body groups shift past it)
        cli = {"kind": "tool", "name": "Bash", "input": 'romp --mail send --kind question beta "when?"',
               "output": "delivered to beta", "isError": False, "uuid": "t2", "ts": "x"}
        c = km._hydrate_postal([cli], {})[0]
        self.assertEqual((c["intent"], c["peer"], c["body"]), ("question", "beta", "when?"))
        # incoming — kind from the log's x-kind on the index record
        index = {"m1": {"id": "m1", "from": "alpha", "fromId": "", "toId": SID,
                        "body": "heads up", "kind": "coordinate", "t": NOW - 5, "park": False}}
        inc = {"kind": "user", "md": "x <!-- romp-msg-id: m1 -->", "uuid": "u1", "ts": "x", "human": False}
        self.assertEqual(km._hydrate_postal([inc], index)[0]["intent"], "coordinate")
        # legacy: no explicit kind, but the body carries the courier's marker → still derived
        leg = {"kind": "tool", "name": "mcp__romp-postal__send_message",
               "input": json.dumps({"to": "beta", "body": "do it\n<!-- romp-msg-kind: delegate -->"}),
               "output": "Delivered to 'beta'.", "isError": False, "uuid": "t3", "ts": "x"}
        self.assertEqual(km._hydrate_postal([leg], {})[0]["intent"], "delegate")
        # no kind anywhere → empty intent (chip simply absent, not a bogus one)
        bare = {"kind": "tool", "name": "mcp__romp-postal__send_message",
                "input": json.dumps({"to": "beta", "body": "hello"}),
                "output": "Delivered to 'beta'.", "isError": False, "uuid": "t4", "ts": "x"}
        self.assertEqual(km._hydrate_postal([bare], {})[0]["intent"], "")

    def test_ordered_alive_is_stable_under_activity(self):
        """Lanes/tabs must not auto-shuffle when a session becomes active: a fresh session is appended
        once and keeps its slot even when its mtime later jumps ahead (the user 2026-06-15)."""
        saved = km._alive_sessions
        try:
            km._alive_sessions = lambda now, tmux: [{"sid": "A", "mtime": 100}, {"sid": "B", "mtime": 50}]
            first = [s["sid"] for s in km._ordered_alive(NOW, {})]
            # B now becomes the most-recently-active (its mtime jumps past A) — the order must NOT change
            km._alive_sessions = lambda now, tmux: [{"sid": "A", "mtime": 100}, {"sid": "B", "mtime": 999}]
            second = [s["sid"] for s in km._ordered_alive(NOW, {})]
            self.assertEqual(first, ["A", "B"], "new sessions frozen newest-active-first, once")
            self.assertEqual(second, first, "activity (mtime) must not reorder existing lanes/tabs")
        finally:
            km._alive_sessions = saved

    def test_session_order_roundtrip_and_sort(self):
        # the shared order persists, and chat tabs + timeline lanes follow it (drag-sync parity)
        km._write_session_order(["b", "a", "c"])
        self.assertEqual(km._session_order(), ["b", "a", "c"])
        fake = [{"sid": "a", "mtime": 3}, {"sid": "b", "mtime": 2}, {"sid": "c", "mtime": 1}]
        saved = km._alive_sessions
        km._alive_sessions = lambda now, tmux: list(fake)
        try:
            self.assertEqual([s["sid"] for s in km._ordered_alive(NOW, {})], ["b", "a", "c"],
                             "living sessions follow the saved shared order")
        finally:
            km._alive_sessions = saved

    def test_chat_chip_sinceepoch_is_millis(self):
        # render's elapsedMs does Date.now()(ms) - sinceEpoch, so sinceEpoch must be epoch MILLIS,
        # not seconds (a seconds value rendered ~494,000h — the "400,000 hours" bug)
        st = km.build_session(SID, NOW)["status"]
        self.assertIsNotNone(st["sinceEpoch"])
        self.assertGreater(st["sinceEpoch"], 10 ** 12, "sinceEpoch is epoch millis")

    def test_timeline_lane_and_segment_bar(self):
        # the fixture wrote only a turn-grain caption; bind a segment-grain one so the bar tooltip resolves
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = em.segments(session["turns"][0])[0]
        with (jd.CAPDIR / (SID + ".jsonl")).open("a") as fh:
            fh.write(json.dumps({"id": seg["id"], "grain": "segment", "t": seg["t"],
                                 "caption": "Fixed the feed flicker"}) + "\n")
        m = km.build_timeline(NOW)
        self.assertEqual(m["type"], "timeline")
        self.assertEqual(m["messages"], [], "no postal log in the test sandbox")
        self.assertIsNone(m["usage"], "no usage.json in the temp state")
        self.assertIsNone(m["focus"]); self.assertIsNone(m["hover"])
        lane = next(s for s in m["sessions"] if s["id"] == SID)
        self.assertEqual(lane["color"], "#abcdef", "lane color is the hex string, not {bg,fg}")
        self.assertEqual(lane["state"], "ready", "turn ended → chip 'ready' (the shared derivation, the user 2026-07-03)")
        self.assertEqual(lane["model"], "", "tmux-sourced lane decorations are deferred")
        bars = m["turns"][SID]
        self.assertEqual(len(bars), 1, "the one-input turn is one segment bar")
        bar = bars[0]
        self.assertEqual(bar["start"], T0)
        self.assertGreater(bar["end"], bar["start"])
        self.assertEqual(bar["prompt"], "fix the feed flicker")
        self.assertEqual(bar["summary"], "Fixed the feed flicker", "caption binds to the segment id")
        self.assertEqual(bar["src"], "typed")
        self.assertEqual(bar["workUuid"], "a1", "first assistant atom = work anchor")
        self.assertEqual(bar["replyUuid"], "a2", "last assistant-with-text = reply anchor")
        self.assertFalse(bar["open"], "the turn ended -> bar not open")

    def test_dead_lane_with_open_turn_is_not_working(self):
        """A session that DIED mid-turn — e.g. an SDK turn that stalled on API retries, never returned a
        result, then was ended — must NOT read as 'working' on the timeline: a dead lane is never active
        (the user 2026-06-23). build_timeline's dead branch used 'working if open_now', leaving a zombie
        WORKING badge + an open (growing-to-now) bar after death."""
        g1 = "%s:g1" % SID                               # no blocked goal (blocked would short-circuit to 'awaiting', masking it)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g1,
            "nodes": {g1: {"id": g1, "text": "x", "parentId": None, "nodeComplete": True,
                           "blocked": False, "cleared": False, "trail": [], "t": T0}},
            "placements": {}, "status": {g1: "completed"}}))
        with self.tpath.open("a") as f:                  # an OPEN turn: assistant emitted a tool_use, no result → mid-work
            f.write(json.dumps(uline(NOW - 60, "do a thing", "uOpen", parent="a2", ps="typed")) + "\n")
            f.write(json.dumps(aline(NOW - 50, "On it.", "aOpen", parent="uOpen", tools=("Bash",), stop="tool_use")) + "\n")
        km._parse_cache.clear()
        self.assertTrue(km._session_working(em.parse_session(str(self.tpath), rompuuid=SID,
                        candidate_files=[str(self.tpath)], now=NOW)["turns"]), "fixture IS working by the event model")
        km._tmux_sessions = lambda: {}                   # DEAD: not in the live map
        m = km.build_timeline(NOW)
        lane = next(s for s in m["sessions"] if s["id"] == SID)
        self.assertFalse(lane["live"], "session is dead (not in tmux)")
        self.assertEqual(lane["state"], "idle", "a dead lane with an unfinished turn is idle, NOT working")
        self.assertFalse(m["turns"][SID][-1]["open"], "a dead lane's last bar is not an open (growing-to-now) bar")

    def test_lane_and_helper_report_backend(self):
        """Each session carries a backend label ('sdk'|'tmux') so the ui peer can show it (tab tooltip +
        timeline lane). Live metadata's field wins; else SDK-registry ownership; a non-SDK session is tmux
        (the user 2026-06-22, via the ui peer)."""
        saved = km._sdk
        km._sdk = lambda: None                               # deterministic: no SDK backend unless overridden
        try:
            self.assertEqual(km._session_backend("x", {"backend": "sdk"}), "sdk")    # live field wins
            self.assertEqual(km._session_backend("x", {"state": "idle"}), "tmux")    # no field, no SDK reg → tmux
            km._sdk = lambda: type("B", (), {"owns": lambda self, s: True})()
            self.assertEqual(km._session_backend("x", None), "sdk")                  # dead SDK lane → sdk via ownership
            km._sdk = lambda: None
            lane = next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)
            self.assertNotIn("backend", lane, "the lane never read it — dropped (2026-07-07 payload audit)")
            self.assertEqual(km.build_session(SID, NOW)["status"]["backend"], "tmux")
        finally:
            km._sdk = saved

    def test_retrying_state_maps_to_retrying_chip(self):
        """An SDK session stalled in an api_retry storm publishes state 'retrying'; the chat chip surfaces
        it distinctly (not 'working'), so the user sees it's an API issue, not a hang (the user 2026-06-23)."""
        km._tmux_sessions = lambda: {SID: {"state": "retrying", "since": NOW - 5, "model": "Opus 4.8",
                                           "effort": "high", "context": None, "compactPct": None,
                                           "color": None, "backend": "sdk"}}
        self.assertEqual(km.build_session(SID, NOW)["status"]["state"], "retrying")

    def test_requestSessions_payload_carries_default_dir(self):
        """The new-session picker prefills the dir field with the kernel's real default path (ROMP_DIR — the
        romp install dir — when no override file is set), sent in the sessionList payload (the user 2026-06-23)."""
        wd = tempfile.mkdtemp()
        saved_sdk, saved_dir, saved_ddfile = km._sdk, os.environ.get("ROMP_DIR"), km._DEFAULT_DIR_FILE
        km._sdk = lambda: None                               # don't construct the real SDK backend
        os.environ["ROMP_DIR"] = wd
        km._DEFAULT_DIR_FILE = Path(tempfile.mkdtemp()) / "default-dir"   # no user override → ROMP_DIR wins
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "app": "chat"}
        try:
            km.Handler._dispatch_ws(None, {"type": "requestSessions"}, client)
        finally:
            km._sdk = saved_sdk
            km._DEFAULT_DIR_FILE = saved_ddfile
            if saved_dir is None:
                os.environ.pop("ROMP_DIR", None)
            else:
                os.environ["ROMP_DIR"] = saved_dir
        sl = next((m for m in sent if m.get("type") == "sessionList"), None)
        self.assertIsNotNone(sl, "requestSessions returns a sessionList")
        self.assertEqual(sl.get("defaultDir"), km._tilde(wd), "the dir field prefills the kernel's default path")

    def test_requestSessions_payload_carries_self_host(self):
        """The picker's Host row labels its this-machine option with the machine's real name instead of
        'local' (the user 2026-08-12). The name rides the sessionList payload as selfHost, and it is the
        SAME identity peers see (_self_host — short hostname, ROMP_HOST_NAME override), so the picker
        and federation never disagree about what this machine is called."""
        saved_sdk, saved_hn = km._sdk, os.environ.get("ROMP_HOST_NAME")
        km._sdk = lambda: None                               # don't construct the real SDK backend
        os.environ["ROMP_HOST_NAME"] = "TESTHOST"
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "app": "chat"}
        try:
            km.Handler._dispatch_ws(None, {"type": "requestSessions"}, client)
        finally:
            km._sdk = saved_sdk
            if saved_hn is None:
                os.environ.pop("ROMP_HOST_NAME", None)
            else:
                os.environ["ROMP_HOST_NAME"] = saved_hn
        sl = next((m for m in sent if m.get("type") == "sessionList"), None)
        self.assertIsNotNone(sl, "requestSessions returns a sessionList")
        self.assertEqual(sl.get("selfHost"), "TESTHOST", "the payload names this machine as peers know it")

    def test_createSession_sdk_backend_unavailable_warns_instead_of_tmux_fallback(self):
        """The user asked for an SDK session on a kernel without the SDK venv and got a MYSTERY TMUX session
        instead (TESTHOST, 2026-07-02) — the handler silently fell through to _spawn_session. Now it warns
        (naming bin/romp-sdk-setup) and creates nothing."""
        saved_sdk, saved_spawn, saved_tmux = km._sdk, km._spawn_session, km._tmux_sessions
        km._sdk = lambda: None                               # the backend is unavailable (no venv / py<3.10)
        spawned = []
        km._spawn_session = lambda nm, cwd: spawned.append(nm)
        km._tmux_sessions = lambda: {}
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "app": "chat"}
        try:
            km.Handler._dispatch_ws(None, {"type": "createSession", "name": "sdlkless", "backend": "sdk"}, client)
            time.sleep(0.05)                                 # the tmux path spawns on a thread — give it a beat
        finally:
            km._sdk, km._spawn_session, km._tmux_sessions = saved_sdk, saved_spawn, saved_tmux
        warn = next((m for m in sent if m.get("type") == "warn"), None)
        self.assertIsNotNone(warn, "the client is told, not silently given a different backend")
        self.assertIn("romp-sdk-setup", warn["text"], "the warn names the fix")
        self.assertEqual(spawned, [], "no tmux fallback session is created")

    def test_timeline_state_and_metadata_from_tmux(self):
        # live lanes take model/effort/context from tmux @claude-* vars; the STATE is the shared
        # _session_chip derivation (the user 2026-07-03) — an idle tmux 'waiting' reads as chip 'ready'.
        # badgeFor hides the badge unless live, so live must be true here
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 10, "model": "Opus 4.8",
                                           "effort": "xhigh", "context": 43, "compactPct": None,
                                           "color": "#abcdef"}}
        lane = next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)
        self.assertTrue(lane["live"])
        self.assertEqual(lane["state"], "ready", "the lane speaks the CHIP vocabulary now (the user 2026-07-03: one shared derivation with the chat)")
        self.assertEqual(lane["model"], "Opus 4.8")
        self.assertEqual(lane["effort"], "xhigh")
        self.assertEqual(lane["context"], 43)

    def test_chat_chip_and_timeline_lane_are_ONE_derivation(self):
        # the user 2026-07-03: after an API error the chat chip read API ERROR → READY while the timeline
        # lane sat on raw-snapshot 'working' — two derivations of one fact. Both surfaces now call the
        # shared _session_chip, so under ANY backend snapshot they read the SAME state: tmux claims
        # 'working' here, but the transcript's turn ENDED → both say 'ready', together.
        km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        lane = next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)
        chip = km.build_session(SID, NOW)["status"]["state"]
        self.assertEqual(lane["state"], chip, "one shared derivation — the surfaces cannot disagree")
        self.assertEqual(chip, "ready", "the event model (turn ended) wins over the stale snapshot")

    def test_awaiting_bg_is_its_own_chip_state_on_both_surfaces(self):
        # The user 2026-07-13 ("differentiate working from awaiting"): an idle session waiting on
        # background work it dispatched is no longer folded into "working" — the shared _session_chip
        # emits `awaitingBg`, so the chat chip (straw "Awaiting") and the timeline lane split together.
        saved = km._session_awaiting
        km._session_awaiting = lambda sid, path, idle, stamp=False: {"kind": "agents", "why": "bg agents"} if idle else None
        try:
            chip = km.build_session(SID, NOW)["status"]["state"]
            lane = next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)["state"]
        finally:
            km._session_awaiting = saved
        self.assertEqual(chip, "awaitingBg", "idle + awaiting bg work → its own state, not working")
        self.assertEqual(lane, chip, "one shared derivation — both surfaces split together")

    def test_an_open_turn_still_reads_working_not_awaiting(self):
        # working beats the awaiting flavor: while the main thread is actually producing, the chip says
        # Working — awaitingBg only covers the idle-but-held stretch.
        saved_aw, saved_w = km._session_awaiting, km._session_working
        km._session_awaiting = lambda sid, path, idle, stamp=False: {"kind": "agents", "why": "bg agents"}
        km._session_working = lambda turns: True
        try:
            chip = km.build_session(SID, NOW)["status"]["state"]
        finally:
            km._session_awaiting, km._session_working = saved_aw, saved_w
        self.assertEqual(chip, "working")

    def test_feed_carries_the_awaiting_name_list_beside_working(self):
        # the straw dots (feed cards/headers + chat tabs) key on feed["awaiting"] exactly as the yellow
        # dots key on feed["working"] — same names, same federation prefixing (ARRAY_ID).
        saved = km._session_awaiting
        km._session_awaiting = lambda sid, path, idle, stamp=False: {"kind": "agents", "why": "bg agents"} if idle else None
        try:
            feed = km.build_feed(NOW)
        finally:
            km._session_awaiting = saved
        self.assertEqual(feed["awaiting"], ["testsess"], "the idle awaiting session is listed by name")
        self.assertEqual(feed["working"], [], "awaiting is not working — the lists are disjoint")

    def test_skeleton_lane_and_chat_chip_share_the_live_merged_input(self):
        # the user 2026-07-03, second round of the split: the FORMULA was shared (_session_chip) but not
        # the INPUT — the chat merged the SDK live tail while the lane badge rides the SKELETON build
        # (the {type:"bars"} message carries no states), which computed over the unmerged cached parse.
        # A live work atom then read WORKING on the chat and READY on the lane on EVERY push. The
        # skeleton merges the live tail too now: same input, same formula, same answer — with the live
        # atom present, and again once the turn-settle retirement clears it.
        class _FakeBE:
            def __init__(self, inner):
                self._inner = inner
                self.atoms = [{"type": "assistant", "uuid": "live-w1", "t": NOW - 5,
                               "message": {"role": "assistant", "stop_reason": None,
                                           "content": [{"type": "text", "text": "streaming"}]}}]
            def live_atoms(self, sid): return list(self.atoms)
            def prune_live(self, *a, **k): pass
            def __getattr__(self, n): return getattr(self._inner, n)
        saved_bf, saved_tmux = km.Sessions.backend_for, km._tmux_sessions
        fake = _FakeBE(saved_bf(SID))
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        try:
            km.Sessions.backend_for = lambda sid: fake
            km._parse(km._path_of(SID), SID, NOW)          # warm the cache — the skeleton reads _parse_cached
            chip = km.build_session(SID, NOW)["status"]["state"]
            lane = next(s for s in km.build_timeline(NOW, with_bars=False)["sessions"] if s["id"] == SID)["state"]
            self.assertEqual(chip, "working", "a live WORK atom holds the merged turn open")
            self.assertEqual(lane, chip, "the skeleton lane merges the SAME live tail — no divergence")
            fake.atoms = []                                # turn settle retired the stream atoms
            chip2 = km.build_session(SID, NOW)["status"]["state"]
            lane2 = next(s for s in km.build_timeline(NOW, with_bars=False)["sessions"] if s["id"] == SID)["state"]
            self.assertEqual((chip2, lane2), ("ready", "ready"),
                             "both surfaces fall back to the disk truth together")
        finally:
            km.Sessions.backend_for = saved_bf
            km._tmux_sessions = saved_tmux

    def test_timeline_includes_dead_sessions_for_scrollback(self):
        # the user 2026-06-16: dead sessions appear as struck lanes so scrolling back surfaces them. The
        # regression was build_timeline feeding only LIVING sessions; it now includes window-dead ones
        # too (the render's active-filter only shows a dead lane when the window covers its activity).
        # SID has a transcript but is passed NO tmux → it must still be a lane, marked dead.
        s = {x["id"]: x for x in km.build_timeline(NOW, tmux={})["sessions"]}
        self.assertIn(SID, s, "a window-dead session is still a timeline lane")
        self.assertFalse(s[SID]["live"], "no tmux → a dead lane (the render strikes it)")

    def test_model_pending_flows_to_chat_status_and_timeline_lane(self):
        # the user 2026-07-03: while a /model switch resolves, BOTH the chat chip and the timeline lane
        # show switching-dots — so the SDK snapshot's modelPending must reach both surfaces (the kernel
        # merges it in Sessions.live() and passes it through build_session + build_timeline).
        km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 10, "model": "Fable 5",
                                           "effort": "high", "context": 20, "compactPct": None,
                                           "color": None, "modelPending": True}}
        st = km.build_session(SID, NOW)["status"]
        self.assertTrue(st.get("modelPending"), "the chat status carries the switching signal")
        lane = next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)
        self.assertTrue(lane.get("modelPending"), "the timeline lane carries it too")
        # a snapshot without the key must not crash and reads False (tmux sessions never set it)
        km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 10, "model": "Opus 4.8",
                                           "effort": "high", "context": 20, "compactPct": None, "color": None}}
        self.assertFalse(km.build_session(SID, NOW)["status"].get("modelPending"))

    def test_model_pending_from_tmux_reaches_both_surfaces_regardless_of_which_ui_clicked(self):
        # the user 2026-07-03 (follow-up): tmux tracks NO modelPending of its own (only the SDK backend
        # does), so switching a tmux session's model used to show dots ONLY on whichever surface's own
        # LOCAL click heuristic fired — clicking the timeline's picker left the chat chip with no cue at
        # all, catching up only once the next tmux poll happened to report the new name. Fix: the kernel
        # stamps ONE shared pending signal (_mark_model_pending) the instant EITHER surface's pick is
        # accepted (_set_model_or_park is the single funnel both the chat's setModel and the timeline's
        # sendCommand route through) — so both build_session and build_timeline show it identically.
        saved_push, km._push_all = km._push_all, lambda: None
        try:
            km._model_switch_pending.clear()
            km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 10, "model": "Haiku",
                                               "effort": "high", "context": 20, "compactPct": None, "color": None}}
            fake_be = types.SimpleNamespace(set_model=lambda sid, value: None)
            km._set_model_or_park(fake_be, SID, "opus")   # accepted, from EITHER surface — same call either way
            st = km.build_session(SID, NOW)["status"]
            self.assertTrue(st.get("modelPending"), "the chat chip shows the switching dots too")
            lane = next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)
            self.assertTrue(lane.get("modelPending"), "…and so does the timeline lane, from the SAME stamp")
            # the live tmux model now reflects the pick (a later poll) → the signal clears on BOTH surfaces
            km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 10, "model": "Opus 4.8",
                                               "effort": "high", "context": 20, "compactPct": None, "color": None}}
            self.assertFalse(km.build_session(SID, NOW)["status"].get("modelPending"), "cleared once the name lands")
        finally:
            km._push_all = saved_push
            km._model_switch_pending.clear()

    def test_unify_model_labels_borrows_the_fleet_versioned_name(self):
        # the user 2026-07-03: some lanes said "Opus", others "Opus 4.8" — a version-less best-effort
        # label (from a /model switch that hasn't run a turn, incl. a stale-badge heal) sits next to a
        # real versioned name. The fleet already knows opus == "Opus 4.8", so the short one borrows it.
        rows = {"a": {"model": "Opus"}, "b": {"model": "Opus 4.8"}, "c": {"model": "Fable"},
                "d": {"model": "Sonnet 5"}, "e": {"model": "Sonnet"}, "f": {"model": ""}}
        km._unify_model_labels(rows)
        self.assertEqual(rows["a"]["model"], "Opus 4.8", "the bare label borrows the fleet's versioned name")
        self.assertEqual(rows["b"]["model"], "Opus 4.8", "the versioned one is unchanged")
        self.assertEqual(rows["e"]["model"], "Sonnet 5", "same across families")
        self.assertEqual(rows["c"]["model"], "Fable", "no versioned variant in the fleet → keep the short label")
        self.assertEqual(rows["f"]["model"], "", "empty stays empty; never crashes")

    def test_unify_model_labels_never_rewrites_a_versioned_name_and_stays_bare_when_ambiguous(self):
        # the user 2026-07-27, during the Opus 4.8 → 5 default flip: the old unify relabeled EVERY row to
        # the family's "richest" name, and its tiebreak preferred the longer string — so "Opus 4.8" beat
        # "Opus 5" and sessions genuinely running Opus 5 (their own turns said so) DISPLAYED as 4.8, which
        # is how a brand-new session on the new default looked stuck on the old one. A versioned label is
        # that session's own report and must never be rewritten; a bare "Opus" amid TWO live versions is
        # ambiguous (a fresh session resolves the alias to the NEW version, not the fleet's dominant one)
        # and must stay bare rather than guess.
        rows = {"old": {"model": "Opus 4.8"}, "new": {"model": "Opus 5"}, "fresh": {"model": "Opus"},
                "s": {"model": "Sonnet"}, "s5": {"model": "Sonnet 5"}}
        km._unify_model_labels(rows)
        self.assertEqual(rows["new"]["model"], "Opus 5", "a session's own versioned report is ground truth")
        self.assertEqual(rows["old"]["model"], "Opus 4.8")
        self.assertEqual(rows["fresh"]["model"], "Opus", "two live versions → a bare label stays bare")
        self.assertEqual(rows["s"]["model"], "Sonnet 5", "one live version → still borrows")

    def test_timeline_keeps_dead_lanes(self):
        # the timeline is a complete activity history (the user 2026-06-17): a dead session within the
        # lane window is still a struck lane, with no tab needed. (The ×-hidden variant of this pin died
        # with hidden tabs, the user 2026-08-11 — there is no tab state that could erase a lane anymore.)
        s = {x["id"]: x for x in km.build_timeline(NOW, tmux={})["sessions"]}
        self.assertIn(SID, s, "a dead session in-window is STILL a timeline lane")
        self.assertFalse(s[SID]["live"], "and it's a dead (struck) lane")

    def test_dead_session_is_timeline_only_until_viewed(self):
        # the user 2026-06-17: a dead session is TIMELINE-ONLY — no auto chat tab. It gets a read-only
        # tab ONLY on demand (View read-only → _kept_open); ×-close forgets it (timeline-only again).
        saved = set(km._kept_open)
        # tmux={} is AMBIGUOUS to _alive_sessions: "zero live sessions" (trust it, show nothing) vs
        # "no tmux here at all" (headless → fall back to every file-derived session). It disambiguates
        # with _has_tmux(), i.e. whether a tmux BINARY exists on the machine running the tests. Left
        # inherited, this test therefore asserts the opposite thing on a box without tmux: the fallback
        # fires, SID comes back alive, and "not auto-kept as a tab" fails. Ubuntu runners ship tmux and
        # macOS runners do not, so it passed on Linux CI and failed on macOS CI. Pin it: this test is
        # about _kept_open in a tmux-capable environment, not about the headless fallback.
        saved_has = km._has_tmux
        km._has_tmux = lambda: True
        try:
            km._kept_open.discard(SID)
            tabs = lambda: {x["sid"] for x in km._chat_tab_sessions(NOW, {})}   # tmux={} → SID is dead
            self.assertNotIn(SID, tabs(), "a dead session is NOT auto-kept as a tab")
            km._kept_open.add(SID)                       # 'View read-only'
            self.assertIn(SID, tabs(), "View read-only → a read-only tab")
            km._kept_open.discard(SID)                   # ×-close
            self.assertNotIn(SID, tabs(), "×-close forgets it → timeline-only again")
        finally:
            km._has_tmux = saved_has
            km._kept_open.clear(); km._kept_open.update(saved)

    def test_headless_box_falls_back_to_file_derived_sessions(self):
        """The other side of that ambiguity, which nothing covered: with NO tmux binary, an empty tmux
        map means headless, not 'zero sessions', so surfaces fall back to file-derived sessions rather
        than going blank. This is what made the test above machine-dependent, so pin both directions."""
        saved_has = km._has_tmux
        try:
            km._has_tmux = lambda: False
            self.assertIn(SID, {s["sid"] for s in km._alive_sessions(NOW, {})},
                          "no tmux at all → fall back so a headless box isn't blank")
            km._has_tmux = lambda: True
            self.assertNotIn(SID, {s["sid"] for s in km._alive_sessions(NOW, {})},
                             "tmux present + empty result → a genuine zero, show nothing")
        finally:
            km._has_tmux = saved_has

    def test_chat_chip_maps_tmux_state(self):
        # the chat chip maps tmux state: permission -> awaiting, plus model/effort/ctx for the statusline
        km._tmux_sessions = lambda: {SID: {"state": "permission", "since": NOW - 5, "model": "Opus 4.8",
                                           "effort": "max", "context": 20, "compactPct": None, "color": None}}
        st = km.build_session(SID, NOW)["status"]
        self.assertEqual(st["state"], "needsInput", "permission -> the needs-input chip (renamed 2026-08-15)")
        self.assertEqual(st["model"], "Opus 4.8")
        self.assertEqual(st["ctx"], "20")


class CrossPane(unittest.TestCase):
    def test_send_to_app_routes_by_app(self):
        got = {"chat": [], "feed": []}
        chat = {"app": "chat", "send": lambda s: got["chat"].append(s), "alive": True}
        feed = {"app": "feed", "send": lambda s: got["feed"].append(s), "alive": True}
        with km._clients_lock:
            km._clients[:] = [chat, feed]
        try:
            km._send_to_app("chat", {"type": "focus", "id": "S1"})
        finally:
            with km._clients_lock:
                km._clients[:] = []
        self.assertEqual(len(got["chat"]), 1, "only chat clients get the chat-routed message")
        self.assertEqual(len(got["feed"]), 0)
        self.assertIn("focus", got["chat"][0]); self.assertIn("S1", got["chat"][0])

    def test_showontimeline_anchor_maps_to_focus_kind(self):
        # a feed TITLE click sends anchor:"prompt" → land on the user's MESSAGE turn; a sub-thing /
        # work click sends no anchor → the nearest turn (the assistant response). (the user 2026-06-15)
        self.assertEqual(km._focus_kind("prompt"), "user")
        self.assertIsNone(km._focus_kind("work"))
        self.assertIsNone(km._focus_kind(None))

    def test_showontimeline_forwards_anchoruuid_as_id_deeplink_with_time_fallback(self):
        # a feed click that knows the exact turn (anchorUuid, kernel 996ebd7) → the chat focus message's
        # `anchor` (uuid), tried FIRST; t + kind stay the FALLBACK. Kills the nearest-time mismatch
        # (delegation/card clicks landing on unrelated user messages). (the user 2026-06-17, via rompinfra.)
        f = km._show_on_timeline_focus({"sid": "S1", "t": 1700, "anchor": "work",
                                        "anchorUuid": "11111111-2222-3333-4444-555555555555"})
        self.assertEqual(f["type"], "focus"); self.assertEqual(f["id"], "S1")
        self.assertEqual(f["anchor"], "11111111-2222-3333-4444-555555555555", "uuid → id-based deep-link")
        self.assertEqual(f["anchorT"], 1700); self.assertIsNone(f["anchorKind"], "'work' → no kind gate")
        # a "prompt"-intent title click keeps the user-kind time fallback alongside the uuid
        f2 = km._show_on_timeline_focus({"sid": "S2", "t": 1800, "anchor": "prompt", "anchorUuid": None})
        self.assertIsNone(f2["anchor"], "null uuid → fall straight through to the time path")
        self.assertEqual(f2["anchorKind"], "user", "'prompt' → land on the user's message by time")


class TestApiError(unittest.TestCase):
    """km._api_error — is the session BLOCKED on an API error right now? Event-based on the transcript's
    isApiErrorMessage flag (the invariant across 500 / timeout / model-not-found). Synthetic records only."""

    def setUp(self):
        km._api_err_cache.clear()
        self.td = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.td.name, "t.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def _write(self, *recs):
        with open(self.p, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

    def test_error_is_last_returns_it(self):
        self._write(uline(T0, "do it", "u1"), apierr_line(T0 + 5, "e1", "u1"))
        e = km._api_error(self.p)
        self.assertIsNotNone(e)
        self.assertEqual(e["status"], 500)
        self.assertEqual(e["category"], "server_error")
        self.assertIn("Internal server error", e["text"])

    def test_user_retry_after_error_clears(self):
        self._write(uline(T0, "do it", "u1"), apierr_line(T0 + 5, "e1", "u1"),
                    uline(T0 + 9, "retry", "u2", parent="e1"))
        self.assertIsNone(km._api_error(self.p))

    def test_fresh_assistant_output_after_error_clears(self):
        self._write(uline(T0, "do it", "u1"), apierr_line(T0 + 5, "e1", "u1"),
                    aline(T0 + 9, "Back on track.", "a2", "e1"))
        self.assertIsNone(km._api_error(self.p))

    def test_no_error_returns_none(self):
        self._write(uline(T0, "do it", "u1"), aline(T0 + 5, "done", "a1", "u1"))
        self.assertIsNone(km._api_error(self.p))

    def test_burst_returns_latest(self):
        # Claude Code logs several consecutive retries; while none recovered, the LATEST stands.
        self._write(uline(T0, "do it", "u1"),
                    apierr_line(T0 + 5, "e1", "u1", text="Request timed out", status=None, category="unknown"),
                    apierr_line(T0 + 7, "e2", "e1"))
        self.assertEqual(km._api_error(self.p)["status"], 500)

    def test_timeout_has_no_status(self):
        self._write(apierr_line(T0 + 5, "e1", None, text="Request timed out", status=None, category="unknown"))
        e = km._api_error(self.p)
        self.assertIsNone(e["status"])
        self.assertEqual(e["category"], "unknown")

    def test_cache_keys_on_mtime_size(self):
        self._write(uline(T0, "do it", "u1"), apierr_line(T0 + 5, "e1", "u1"))
        self.assertIsNotNone(km._api_error(self.p))
        with open(self.p, "a") as f:                       # append a retry → recovered; the cache must bust
            f.write(json.dumps(uline(T0 + 9, "retry", "u2", parent="e1")) + "\n")
        self.assertIsNone(km._api_error(self.p), "append busts the (mtime,size) cache")

    def test_spend_limit_is_classified_on_you(self):
        # a monthly SPEND cap (a billing limit) is on you — raise it; distinct from a transient error AND
        # from a rate window (the user 2026-07-14). It must never auto-retry, so it carries spendLimit.
        self._write(uline(T0, "do it", "u1"),
                    apierr_line(T0 + 5, "e1", "u1",
                                text="You've hit your monthly spend limit. Raise it at claude.ai/settings/usage.",
                                status=None, category="usage_limit"))
        e = km._api_error(self.p)
        self.assertTrue(e["spendLimit"], "the spend-cap phrasing is classified as a spend limit")
        self.assertFalse(e["tooLong"], "a spend cap is not a prompt-too-long error")

    def test_transient_and_ratewindow_errors_are_not_spend_limits(self):
        # a plain 500 is transient (auto-retries); a 5h/7d RATE window has its own countdown-and-retry path
        # (_auto_pause_on_limit via the usage report) and must NOT be misread as a no-reset spend cap.
        self._write(apierr_line(T0 + 5, "e1", None))                      # default 500 server_error
        self.assertFalse(km._api_error(self.p)["spendLimit"])
        self._write(apierr_line(T0 + 5, "e2", None,
                                text="Usage limit reached. Your limit resets at 3pm.", status=429,
                                category="rate_limit"))
        self.assertFalse(km._api_error(self.p)["spendLimit"],
                         "a rate-window reset message is not a spend cap (no 'raise it')")

    def test_is_spend_limit_predicate(self):
        for t in ("You've hit your monthly spend limit.",
                  "Reached your spending limit for this month.",
                  "Please raise your budget at claude.ai/settings/usage"):
            self.assertTrue(km._is_spend_limit(t), t)
        for t in ("API Error: 500 Internal server error.", "Request timed out",
                  "Usage limit reached — resets at 3pm.", "prompt is too long"):
            self.assertFalse(km._is_spend_limit(t), t)


class ApiRetryAndTabOrderRoutes(unittest.TestCase):
    """WS handlers hard to drive through the socket — assert the routing is wired in source (mirrors
    CompactSessionRoute): the Retry button pastes "retry"; the kernel pushes the saved tab order on connect."""

    def test_routes_present(self):
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('t == "apiRetry"', src, "Retry button → apiRetry, handled in the unified _drive")
        self.assertIn('be.send(sid, RETRY_MSG)', src,
                      "apiRetry pastes RETRY_MSG on BOTH backends (→ a gray romp bubble; the planner skips a "
                      "work-less retry instead of minting a junk goal — the user 2026-06-30)")
        self.assertIn(r'RETRY_MSG = "retry\n\n<!-- romp-injected -->"', src,
                      "RETRY_MSG is the shared, romp-injected-tagged retry text")
        self.assertIn('"type": "tabOrder"', src,
                      "kernel pushes the saved tab order on connect so the UI stops reordering (#11)")


class TestPendingQueued(unittest.TestCase):
    """km._pending_queued / _genuine_queued — still-pending queued messages folded FIFO from the
    transcript's queue-operation records (event-based; replaces the pane scrape that dropped a 2nd queued
    message and lost both). Synthetic records only — no real session data."""

    def setUp(self):
        km._queued_parse_cache.clear()
        self.td = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.td.name, "t.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def _write(self, *ops):
        # each op is (operation,) or (operation, content)
        recs = []
        for op in ops:
            o = {"type": "queue-operation", "operation": op[0]}
            if len(op) > 1:
                o["content"] = op[1]
            recs.append(json.dumps(o))
        with open(self.p, "w") as f:
            f.write("\n".join(recs) + "\n")

    def test_single_pending(self):
        self._write(("enqueue", "fix the flaky test"))
        self.assertEqual(km._pending_queued(self.p), ["fix the flaky test"])

    def test_two_pending_keep_submission_order(self):
        # the regression: TWO queued messages must BOTH show, oldest→newest (the user 2026-06-16).
        self._write(("enqueue", "first"), ("enqueue", "second"))
        self.assertEqual(km._pending_queued(self.p), ["first", "second"])

    def test_dequeue_resolves_fifo_front(self):
        self._write(("enqueue", "first"), ("enqueue", "second"), ("dequeue",))
        self.assertEqual(km._pending_queued(self.p), ["second"], "the oldest enqueue is the one consumed")

    def test_remove_also_resolves(self):
        self._write(("enqueue", "first"), ("enqueue", "second"), ("remove",), ("remove",))
        self.assertEqual(km._pending_queued(self.p), [], "remove drains like dequeue")

    def test_popAll_clears_the_whole_queue(self):
        # The phantom (the user 2026-08-26): popAll — the CLI's record for the whole queue being recalled
        # at once — was UNHANDLED, so its enqueues stayed pending forever. Nothing here is still owed.
        self._write(("enqueue", "first"), ("enqueue", "second"), ("popAll", "first"))
        self.assertEqual(km._pending_queued(self.p), [], "a recalled queue owes nothing")

    def test_popAll_does_not_shift_later_resolutions(self):
        # The DAMAGE the unhandled op did, and the actual bug reported: with popAll ignored, its two
        # enqueues stayed on the pending list, so the dequeue below resolved one of THEM instead of the
        # message it actually delivered — leaving the delivered one on screen as a queued bubble for good.
        self._write(("enqueue", "recalled one"), ("enqueue", "recalled two"), ("popAll", "recalled one"),
                    ("enqueue", "typed after the recall"), ("dequeue",))
        self.assertEqual(km._pending_queued(self.p), [],
                         "the dequeue resolves the message that followed the recall, not a recalled one")

    def test_enqueue_after_popAll_is_still_pending(self):
        # the other direction: a recall clears what was queued THEN, never what arrives after it
        self._write(("enqueue", "recalled"), ("popAll", "recalled"), ("enqueue", "still waiting"))
        self.assertEqual(km._pending_queued(self.p), ["still waiting"])

    def test_remove_with_content_takes_that_entry_not_the_oldest(self):
        # The CLI's removes are content-addressed single-item discards, routinely of a NON-oldest entry
        # (measured live 2026-08-18 — _undelivered_wake_tail resolves the same ledger this way). Folding
        # one as "the oldest went" mispairs every later resolution, which is the same class of drift popAll
        # caused: the survivor shown is not the message still waiting.
        self._write(("enqueue", "first"), ("enqueue", "second"), ("enqueue", "third"),
                    ("remove", "second"))
        self.assertEqual(km._pending_queued(self.p), ["first", "third"])

    def test_a_content_remove_naming_nothing_pending_still_resolves_the_oldest(self):
        # The deliberate split from _undelivered_wake_tail, which resolves nothing here. This fold credits
        # dequeues, so a dequeue may already have taken the named entry — and for a DISPLAY the two errors
        # are not symmetric: an unresolved entry is a bubble that never leaves (the reported bug), while an
        # over-resolved one self-heals at the next record.
        self._write(("enqueue", "first"), ("enqueue", "second"), ("dequeue",), ("remove", "first"))
        self.assertEqual(km._pending_queued(self.p), [],
                         "the CLI resolved something; the display must not strand the survivor")

    def test_drops_postal_and_harness_injections(self):
        # romp delivers a peer message by ENQUEUEing it (carries romp-msg-id / 📬 / a #### banner); those
        # must not masquerade as the user's pending input — only the genuine typed message remains.
        self._write(("enqueue", "#################### \U0001F4EC from peer\nromp-msg-id: 11111111-2222"),
                    ("enqueue", "my real queued ask"))
        self.assertEqual(km._pending_queued(self.p), ["my real queued ask"])

    def test_empty_when_no_records_or_missing_file(self):
        self._write(("enqueue", ""))                       # blank content is not genuine
        self.assertEqual(km._pending_queued(self.p), [])
        self.assertEqual(km._pending_queued(os.path.join(self.td.name, "nope.jsonl")), [])

    def test_genuine_queued_filter(self):
        self.assertTrue(km._genuine_queued("fix the bug"))
        self.assertFalse(km._genuine_queued(""))
        self.assertFalse(km._genuine_queued("   "))
        self.assertFalse(km._genuine_queued("text with romp-msg-id: 11111111 inside"))
        self.assertFalse(km._genuine_queued("\U0001F4EC delivered"))
        self.assertFalse(km._genuine_queued("####################\nbanner"))

    def test_genuine_queued_excludes_system_wrappers(self):
        # a harness <task-notification> / <system-reminder> is not the user's typed input (the user 2026-06-30)
        self.assertFalse(km._genuine_queued('<task-notification>\n<task-id>x</task-id></task-notification>'))
        self.assertFalse(km._genuine_queued('<system-reminder>be concise</system-reminder>'))
        self.assertTrue(km._genuine_queued("a normal message"))

    def test_drops_queued_system_wrappers(self):
        # a backgrounded agent's <task-notification> gets QUEUED when it lands while the session is busy/
        # compacting — a harness injection, NOT typed input, so it must not show as a "queued message" (the
        # user 2026-06-30: it rendered as a raw "1 queued message" in the chat). Synthetic: invented ids, TESTHOST.
        notif = ('<task-notification>\n<task-id>11111111aaaa</task-id>'
                 '<tool-use-id>toolu_0abc</tool-use-id>'
                 '<output-file>/tmp/TESTHOST/tasks/11111111aaaa.output</output-file>'
                 '<status>completed</status><summary>Agent "widget audit" came to rest</summary>'
                 '<result>done</result></task-notification>')
        self._write(("enqueue", notif), ("enqueue", "my real queued ask"))
        self.assertEqual(km._pending_queued(self.p), ["my real queued ask"],
                         "the queued task-notification is filtered, only the typed message remains")

    def test_cache_keys_on_mtime_size(self):
        # build_session calls this every push; an unchanged transcript returns the cached list, a changed
        # one (an enqueue appended) re-reads.
        self._write(("enqueue", "first"))
        a = km._pending_queued(self.p)
        self.assertEqual(a, ["first"])
        with open(self.p, "a") as f:
            f.write(json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "second"}) + "\n")
        self.assertEqual(km._pending_queued(self.p), ["first", "second"], "append busts the (mtime,size) cache")


class CompactSessionRoute(unittest.TestCase):
    """The chat context-battery posts {compactSession, id}; the kernel must route it to /compact for that
    session's tmux name — the SAME action as the timeline's {compact, name}. Without the handler the click
    was silently dropped (the user 2026-06-16)."""

    def test_compact_handler_source_routes_both_shapes(self):
        # both the chat (compactSession/id) and timeline (compact/name) shapes route /compact through the
        # owning backend (was a tmux-only _tmux_send), unified in _drive.
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('t in ("compact", "compactSession")', src,
                      "_drive handles both compact shapes (chat battery + timeline)")
        self.assertIn('be.send(sid, "/compact")', src,
                      "compact sends the same /compact through whichever backend owns the sid")


class TmuxInject(unittest.TestCase):
    def test_tmux_send_sequence(self):
        calls = []
        real_run, real_sleep = km.subprocess.run, km.time.sleep
        km.subprocess.run = lambda args, **k: calls.append(list(args)) or type("R", (), {"stdout": ""})()
        km.time.sleep = lambda s: None
        try:
            km._tmux_send("mysess", "hello world", _async=False)
        finally:
            km.subprocess.run, km.time.sleep = real_run, real_sleep
        # set-buffer the text → bracketed paste-buffer to the session → Enter to submit
        self.assertTrue(any(a[:2] == ["tmux", "set-buffer"] and "hello world" in a for a in calls))
        self.assertTrue(any(a[:2] == ["tmux", "paste-buffer"] and "mysess" in a for a in calls))
        self.assertTrue(any(a[:2] == ["tmux", "send-keys"] and "Enter" in a for a in calls))


class ParentWatch(unittest.TestCase):
    def test_pid_alive(self):
        self.assertTrue(km._pid_alive(os.getpid()))
        self.assertFalse(km._pid_alive(2147483646), "a non-existent pid is not alive")


class WsFraming(unittest.TestCase):
    def test_accept_key(self):
        # RFC6455 example key → accept
        self.assertEqual(km._ws_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class ServeSecurity(unittest.TestCase):
    """The serve-layer gate (docs/read-side.md): Origin validation on every request AND the /ws
    upgrade (kills the cross-site WS hole token-free), + the serve token REQUIRED on every gated
    route, loopback included (Jupyter's model — loopback is reachable by every local user, so the
    0600 token file, not the socket, is the same-user boundary). Runs the REAL handler over a
    loopback server (GET /feed is a static page → no model calls)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _code(self, path, headers):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_loopback_needs_token_and_all_forms_work(self):
        # Loopback is NOT a trust boundary: token-free → 403 even from 127.0.0.1. Every credential
        # form authorizes: ?token= (browser bootstrap), the cookie it seeds, X-Romp-Token (CLI/hooks).
        self.assertEqual(self._code("/feed", {}), 403)
        self.assertEqual(self._code("/feed?token=testtok", {}), 200)
        self.assertEqual(self._code("/feed", {"Cookie": "romp_token=testtok"}), 200)
        self.assertEqual(self._code("/feed", {"X-Romp-Token": "testtok"}), 200)
        self.assertEqual(self._code("/feed", {"X-Romp-Token": "wrong"}), 403)

    def test_restart_endpoint_acks_post(self):
        """The web Restart button (↻) POSTs /restart; the kernel must ACK {ok,restarting} (and, with a
        manager, relay /restart-all so the kernel process relaunches). Regression guard: the Python
        rewrite dropped do_POST entirely, so the button silently no-op'd and the user had to pkill.
        No ROMP_MANAGER_PORT here → it acks without restarting anything."""
        import urllib.request, json as _json
        saved = os.environ.pop("ROMP_MANAGER_PORT", None)   # never trigger a real restart-all in a test
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/restart?token=testtok" % self.port,
                                         method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 200)
                # the ack also names WHICH kernel acked (boot id, 2026-07-27) — see RestartReloadRaceTest
                # `fleet` says which kind of restart it took (the user 2026-07-29): with remotes attached
                # this covers the whole fleet, so the ack names it rather than leaving the caller guessing
                self.assertEqual(_json.loads(r.read().decode()),
                                 {"ok": True, "restarting": True, "boot": km._BOOT_ID, "fleet": True})
        finally:
            if saved is not None:
                os.environ["ROMP_MANAGER_PORT"] = saved

    def test_tick_endpoint_wakes_producer(self):
        """POST /tick is the event-driven judge trigger: the Stop / UserPromptSubmit hooks poke it the
        instant a turn ends / a prompt lands, and it must wake the producer (set _producer_wake) so the
        judges run NOW instead of on the next 20s backstop tick. The hook authorizes with the
        X-Romp-Token header (read from the 0600 token file) — exercised here the same way."""
        import urllib.request, json as _json
        km._producer_wake.clear()
        self.assertFalse(km._producer_wake.is_set())
        req = urllib.request.Request("http://127.0.0.1:%d/tick" % self.port, method="POST", data=b"",
                                     headers={"X-Romp-Token": "testtok"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(_json.loads(r.read().decode()), {"ok": True, "woke": True})
        self.assertTrue(km._producer_wake.is_set())
        km._producer_wake.clear()

    def test_unknown_post_path_is_404(self):
        # authorized but unknown → 404 (auth runs first: unauthorized would be 403)
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d/nope?token=testtok" % self.port,
                                     method="POST", data=b"")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 404)

    def test_timeline_page_served(self):
        # the combined shell's third pane: /timeline injects the shared obsidian TimelinePanel verbatim
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/timeline?token=testtok" % self.port, timeout=5) as r:
            self.assertEqual(r.status, 200)
            body = r.read().decode("utf-8", "replace")
        self.assertIn("TimelinePanel", body, "the shared obsidian view is injected")
        self.assertIn("app=timeline", body, "the page drives panel.update over the kernel WS")

    def test_landing_has_three_panes(self):
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/?token=testtok" % self.port, timeout=5) as r:
            body = r.read().decode("utf-8", "replace")
        for pane in ("src=/chat", "src=/feed", "src=/timeline"):
            self.assertIn(pane, body)

    def test_landing_has_a_focused_pane_cue(self):
        # the user 2026-06-23: NO dimming — the active section is shown by a RING (an inset border) around it.
        # The focused pane gets the ring, the others get nothing (only the splitters + the ring are visible).
        # Shell-only: the same-origin iframes are wired by the shell (pointerdown/focusin/window-focus,
        # event-based, no polling); chat is the default focus; it re-wires on iframe (re)load.
        html = km._landing()
        # the ring: an inset box-shadow ON the focused pane (NOT a fill, NOT on the others), click-through
        self.assertIn(".pane.pane-focused::after{content:'';position:absolute;inset:0;pointer-events:none;z-index:6;", html)
        self.assertIn("box-shadow:inset 0 0 0 2px rgba(120,170,225,0.55)}", html)
        self.assertNotIn("background:rgba(0,0,0,0.5)", html)            # the dimming veil is gone
        self.assertNotIn(".pane:not(.pane-focused)::after", html)       # the OTHERS are NOT touched
        self.assertNotIn("nav-typing", html)                           # the typing/dimming logic is gone
        # the wiring: maps each iframe id → its pane, toggles pane-focused exclusively, defaults to chat.
        # Fleet is its OWN pane now (the user 2026-06-24), so f-fleet maps to fleet-pane, not the chat pane.
        self.assertIn("var PANE={'f-chat':'chat-pane','f-fleet':'fleet-pane','f-feed':'feed-pane','f-timeline':'tl-pane'}", html)
        self.assertIn("classList.toggle('pane-focused'", html)
        self.assertIn("d.addEventListener('pointerdown',emit,true)", html)
        self.assertIn("d.addEventListener('focusin',emit,true)", html)
        self.assertIn("f.contentWindow.addEventListener('focus',emit)", html)
        self.assertIn("f.addEventListener('load',function(){wire(f);});wire(f);", html)   # re-wire on (re)load
        self.assertIn("setFocus('f-chat')", html)                                          # chat ringed by default

    def test_settings_is_a_fullscreen_modal(self):
        # the user 2026-06-23: the gear's settings is a full-WINDOW modal (was a cramped 240px corner panel).
        # #rsettings is the backdrop + .rs-card the centered card. The gear lives in the feed iframe, so it
        # asks the shell to lift the feed iframe over the whole window; the feed's html then goes TRANSPARENT
        # and its BODY is pinned to the feed pane's old screen rect, still painting (rs-lifted + --pane-*
        # vars) — so the dimmed dashboard shows through with the feed cards live and visible in place, not
        # a black hole where the pane was (the user 2026-08-08). Only an unmeasurable pane (hidden, or a
        # cross-origin parent like VS Code) hides the feed's content instead (rs-pane-gone).
        self.assertIn("#rsettings { position: fixed; inset: 0; z-index: 60; background: rgba(0, 0, 0, 0.55);", _gear_css_src())   # the one modal dim (the user 2026-08-08)
        self.assertIn(".rs-card {", _gear_css_src())
        self.assertIn(".rs-modal-open { background: transparent; }", _gear_css_src())            # the page's html steps aside
        self.assertIn("body.rs-lifted { position: fixed; left: var(--pane-x, 0); top: var(--pane-y, 0);", _gear_css_src())
        self.assertIn("body.rs-pane-gone #feed-head, body.rs-pane-gone #feed-list, body.rs-pane-gone #feed-foot { visibility: hidden; }",
                      _gear_css_src())
        self.assertIn("<div id=rsettings hidden><div class=rs-card>", _gear_src())
        self.assertIn("feedFull(true)", _gear_src())              # open → ask the shell to go full-window
        self.assertIn("setModalCls(true)", _gear_src())          # open → transparent html + body pinned in place
        self.assertIn("placeLifted(5)", _gear_src())             # measure the pane rect (retrying while the shell reacts)
        self.assertIn("getElementById('feed-pane')", _gear_src())
        self.assertIn("if (e.target === p) closeSettings()", _gear_src())   # backdrop click closes
        # shell side: the feed iframe lifts to cover the whole window (the panes show THROUGH the transparent
        # feed). background:transparent on the LIFTED IFRAME ELEMENT is load-bearing: the shell's default
        # iframe{background:#1e1e1e} otherwise sits under the transparent page and turns the modal's dim
        # into a full-window black-out (the user 2026-08-08).
        html = km._landing()
        self.assertIn("body.settings-open #f-feed{display:block;position:fixed;inset:0;z-index:200;background:transparent}", html)
        self.assertIn("m.romp==='settings'", html)
        self.assertIn("document.body.classList.toggle('settings-open',!!m.on)", html)

    def test_picker_is_a_fullscreen_modal(self):
        # the user 2026-07-05: the new-session picker lives INSIDE the /chat iframe, so its position:fixed;inset:0
        # only covered the chat PANE — a short pane couldn't scroll the session list. Same bridge as settings:
        # render.ts posts {romp:'picker',on} and the shell lifts the chat iframe over the whole window
        # (body.picker-open), so the overlay fills the screen and the list gets the full height to scroll.
        html = km._landing()
        # background:transparent — same as the settings lift: the opaque iframe element was the black-out.
        # Height rides --app-h (the shell's live VISIBLE height): the layout viewport ignores the phone
        # keyboard, so an inset:0 lift sat half behind it, and the --app-h sizing is what delivers the
        # keyboard to the iframe as its own resize — the event the picker's fold keys on (2026-08-10).
        self.assertIn("body.picker-open #f-chat{display:block;position:fixed;left:0;right:0;top:0;"
                      "height:var(--app-h,100dvh);z-index:200;background:transparent}", html)
        self.assertIn("body.picker-open #chat-pane{display:block!important}", html)         # un-hide it even if chat is toggled off
        self.assertIn("m.romp==='picker'", html)                                            # the shell listens for the picker post
        self.assertIn("document.body.classList.toggle('picker-open',!!m.on)", html)
        # the settings bridge is untouched (both share the one message handler)
        self.assertIn("document.body.classList.toggle('settings-open',!!m.on)", html)

    def test_log_panel_is_a_centered_modal(self):
        # the user 2026-08-08: ONE panel treatment — the Log wore the network modal's card but sat
        # anchored bottom-right over the feed; now its backdrop centers it like #rnet-back, at the
        # standard 0.55 dim, with the dashboard unchanged (dimmed, visible) behind it.
        html = km._landing()
        self.assertIn("#rerr-back{position:fixed;inset:0;z-index:210;display:flex;align-items:center;justify-content:center;", html)
        self.assertIn("background:rgba(0,0,0,0.55)}#rerr-back[hidden]{display:none}", html)
        # the panel is a flex child of the centered backdrop — no anchored positioning of its own
        self.assertNotIn("#rerr-panel{position:absolute", html)

    def test_palette_bundle_wired(self):
        # the command palette (Cmd/Ctrl+P) + quick-switcher hotkey (Cmd/Ctrl+O): a dist bundle the
        # shell page loads like age-color-global; behavior is pinned in ui/webview/palette.test.ts.
        html = km._landing()
        self.assertIn("<script src=/dist/palette-main.js?v=", html)

    def test_fleet_page_served(self):
        # Fleet (the user 2026-06-23): /fleet serves the by-session open-work view, rendered by dist/fleet.js.
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/fleet?token=testtok" % self.port, timeout=5) as r:
            self.assertEqual(r.status, 200)
            body = r.read().decode("utf-8", "replace")
        self.assertIn("id=fleet-list", body)
        self.assertIn("id=fleet-foot", body)
        self.assertIn("id=fleet-search", body)        # top name-filter search bar (the user 2026-06-29)
        self.assertIn("id=fleet-search-clear", body)   # trailing ✕ that clears the search (the user 2026-06-29)
        self.assertIn("/dist/fleet.js", body)
        # the fleet connects as its OWN app (the user 2026-06-29) so the kernel builds its per-session ledgers
        # even with no chat client open — it still rides the feed PAYLOAD, but as app=fleet, not app=feed.
        self.assertIn('app=fleet', body)
        # the romp loader RE-SHOWS on a kernel restart / WS drop (the user 2026-06-29): the shim fires
        # 'romp:wsdown' on ws.onclose and the pane loader un-fades over the stale pane until reconnect.
        self.assertIn("window.addEventListener('romp:wsdown',show)", body)        # loader re-shows
        self.assertIn('dispatchEvent(new Event("romp:wsdown"))', body)            # shim fires it on close
        self.assertIn("function show(){o.classList.remove('gone')", body)         # kept in the DOM, not removed

    def test_landing_fleet_is_its_own_pane_toggled_from_the_rail(self):
        # Fleet is its OWN pane now (the user 2026-06-24): the old .show-fleet SWAP (Fleet living inside the
        # chat pane) is gone. Fleet is the middle pane, toggled by the far-left rail's Fleet button (po-fleet).
        # For back-compat the chat tab bar / Fleet foot still post {romp:'toggleFleet'}; the shell routes that
        # to the same pane toggle (window.__rompPaneToggle('fleet',to?)). The old floating button stays gone.
        html = km._landing()
        self.assertIn("<iframe id=f-fleet src=/fleet>", html)
        self.assertIn("<div class=pane id=fleet-pane>", html)      # Fleet is a real pane, not an overlay
        self.assertNotIn("chat-fleet-toggle", html)               # the floating shell button is removed
        self.assertNotIn("show-fleet", html)                      # the swap mechanism is gone entirely
        self.assertIn("body:not(.po-fleet) #fleet-pane{display:none}", html)   # rail's po-fleet shows/hides it
        self.assertIn("m.romp!=='toggleFleet'", html)             # the shell still listens for the legacy toggle
        self.assertIn("if(m.to==='chat')window.__rompPaneToggle('chat',true)", html)   # open-from-Fleet reveals chat
        self.assertIn("else window.__rompPaneToggle('fleet')", html)                    # no `to` → toggle Fleet
        # f-fleet maps to its OWN pane in the focus map (interacting with it spotlights the fleet pane)
        self.assertIn("'f-fleet':'fleet-pane'", html)
        # ...and since 2026-07-11 the fleet is a mobile TAB too (Outline), not desktop-only
        self.assertIn(">Outline</button>", html)

    def test_landing_pins_height_to_visual_viewport(self):
        # Regression (the user 2026-06-19): on real Android Chrome, body{height:100dvh} left a dead slab
        # below the mobile Chat/Feed/Timeline bar — dvh didn't match the painted viewport. The shell now
        # pins the height to window.visualViewport.height via --app-h, keeping 100dvh only as a fallback.
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/?token=testtok" % self.port, timeout=5) as r:
            body = r.read().decode("utf-8", "replace")
        self.assertIn("visualViewport", body)               # the live-visible-height source
        self.assertIn("--app-h", body)                      # the custom prop the JS drives
        self.assertIn("height:var(--app-h,100dvh)", body)   # body height reads it, dvh only as fallback

    def test_cross_site_origin_rejected(self):
        self.assertEqual(self._code("/feed", {"Origin": "http://evil.example"}), 403)

    def test_cross_site_ws_upgrade_rejected(self):
        # the ClawJacked hole: a foreign-Origin /ws upgrade must be rejected before upgrading
        self.assertEqual(self._code("/ws?app=chat", {
            "Origin": "http://evil.example", "Upgrade": "websocket", "Connection": "Upgrade",
            "Sec-WebSocket-Key": "x", "Sec-WebSocket-Version": "13"}), 403)

    def test_same_origin_ws_passes_gate(self):
        # same-origin upgrade WITH the cookie passes the gate (101) — the served page always has it
        # (the page itself required the token to load). urllib can't complete the upgrade, so a 101
        # surfaces as a non-403 — assert it's NOT rejected. Token-free same-origin is 403 now.
        ws_headers = {
            "Origin": "http://127.0.0.1:%d" % self.port, "Host": "127.0.0.1:%d" % self.port,
            "Upgrade": "websocket", "Connection": "Upgrade",
            "Sec-WebSocket-Key": "x", "Sec-WebSocket-Version": "13"}
        self.assertEqual(self._code("/ws?app=chat", dict(ws_headers)), 403)
        self.assertNotEqual(self._code("/ws?app=chat",
                                       dict(ws_headers, Cookie="romp_token=testtok")), 403)

    def test_healthz_exempt(self):
        self.assertEqual(self._code("/healthz", {"Origin": "http://evil.example"}), 200)

    def test_healthz_carries_the_boot_id(self):
        # /healthz identifies WHICH kernel process answered (X-Romp-Boot): the restart button reloads
        # only when the id flips, because a bare 200 can still be the OLD kernel answering between the
        # /restart ack and its SIGTERM — reloading against it was the browser-error-page race (the user
        # 2026-07-27). The body stays "ok" for external probes that compare it.
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % self.port, timeout=5) as r:
            self.assertEqual(r.read().decode(), "ok")
            self.assertEqual(r.headers.get("X-Romp-Boot"), km._BOOT_ID)
            self.assertEqual(r.headers.get("Access-Control-Expose-Headers"), "X-Romp-Boot")

    def test_host_header_has_no_auth_power(self):
        # The Host header must carry NO authorization weight in either direction: a forged
        # non-local Host with a valid token still authorizes, and a forged local Host without
        # a token is still denied (the old proven bypass was Host-forged "locality"; locality
        # itself is gone — the token decides, loopback included).
        self.assertEqual(self._code("/feed?token=testtok", {"Host": "100.64.1.2:%d" % self.port}), 200)
        self.assertEqual(self._code("/feed", {"Host": "localhost:%d" % self.port}), 403)

    def test_login_page_on_bare_root_open(self):
        # A token-less browser open of "/" gets the paste-the-token page (Jupyter's login flow),
        # not a dead 403 — and it must not leak anything but the entry instructions.
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5) as r:
            self.assertEqual(r.status, 200)
            body = r.read().decode("utf-8", "replace")
        self.assertIn("<code>romp</code>", body)           # names the CLI that opens/prints the tokened link
        self.assertNotIn("testtok", body)                  # never echoes the token itself
        self.assertNotIn("src=/chat", body)                # none of the real shell is served


class NewSessionRoute(unittest.TestCase):
    """POST /new — `romp new` (2026-07-25): the WS createSession op as a one-shot token-gated POST.
    SDK is the default backend and there is NO silent fallback: an unavailable SDK answers ok:false
    with the remedy; backend "tmux" threads the same _spawn_session the WS op uses. Runs the REAL
    handler over loopback with the spawn/SDK seams patched (never a real session from a test)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, payload):
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:%d/new" % self.port, method="POST",
            data=json.dumps(payload).encode(),
            headers={"X-Romp-Token": "testtok", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_name_required_and_validated(self):
        code, body = self._post({"dir": "/tmp"})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])
        code, body = self._post({"name": "bad name!"})
        self.assertEqual(code, 400)
        self.assertIn("letters, digits", body["error"])

    def test_bad_dir_is_a_loud_refusal(self):
        code, body = self._post({"name": "api", "dir": "/nonexistent-romp-test-dir"})
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"])
        self.assertIn("directory not found", body["error"])

    def test_sdk_unavailable_never_falls_back_to_tmux(self):
        saved_sdk, saved_live = km._sdk, km._live_names
        km._sdk, km._live_names = (lambda: None), (lambda t: {})
        try:
            code, body = self._post({"name": "api", "dir": tempfile.gettempdir()})
        finally:
            km._sdk, km._live_names = saved_sdk, saved_live
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"], "no silent tmux session when the SDK is missing")
        # asserted by MEANING, not by the old phrasing: nothing was created, and the one command that
        # fixes it is named (the user 2026-07-28 — "SDK backend unavailable" named nothing to do)
        self.assertIn("not created", body["error"])
        self.assertIn("romp-sdk-setup", body["error"])

    def test_a_built_but_dependency_less_backend_is_NOT_a_yes(self):
        """The real-world shape of the failure: _sdk() hands back a live backend whose SDK cannot
        import (it stays built on purpose, to own the registry and the chat). The old gate read that
        as available and created a session that could never run, with no error anywhere — which is
        exactly what the user hit creating a session from the browser (2026-07-28)."""
        class _Unusable:
            def available(self):
                return False

        saved_sdk, saved_live = km._sdk, km._live_names
        km._sdk, km._live_names = (lambda: _Unusable()), (lambda t: {})
        try:
            code, body = self._post({"name": "api", "dir": tempfile.gettempdir()})
        finally:
            km._sdk, km._live_names = saved_sdk, saved_live
        self.assertFalse(body["ok"], "a backend that cannot import its SDK must refuse, not create")
        self.assertIn("romp-sdk-setup", body["error"])

    def test_existing_live_name_is_an_idempotent_ok(self):
        saved_live = km._live_names
        km._live_names = lambda t: {"api": "sid-existing"}
        try:
            code, body = self._post({"name": "api", "dir": tempfile.gettempdir()})
        finally:
            km._live_names = saved_live
        self.assertEqual(code, 200)
        self.assertEqual((body["ok"], body["existing"], body["id"]), (True, True, "sid-existing"))

    def test_tmux_backend_threads_the_spawn(self):
        calls = []
        saved_spawn, saved_live = km._spawn_session, km._live_names
        km._spawn_session, km._live_names = (lambda nm, cwd=None: calls.append((nm, cwd))), (lambda t: {})
        try:
            code, body = self._post({"name": "term1", "dir": tempfile.gettempdir(),
                                     "backend": "tmux"})
            for _ in range(100):                     # the spawn is threaded — wait for it
                if calls:
                    break
                time.sleep(0.05)
        finally:
            km._spawn_session, km._live_names = saved_spawn, saved_live
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"] and body.get("pending"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "term1")


class HostSuspend(unittest.TestCase):
    """Laptop-sleep awareness: time.monotonic() freezes during sleep but time.time() doesn't, so a
    wall-vs-monotonic gap over one producer tick IS the suspension — an exact resume event, not an age
    threshold. The timeline then closes turns left open across it (the user 2026-06-18)."""

    def test_detect_suspend_ignores_a_normal_tick(self):
        # wall and monotonic advance together (~20s) → no suspension detected
        self.assertIsNone(km._detect_suspend(1000.0, 5000.0, 1020.0, 5020.0))

    def test_detect_suspend_flags_a_sleep_and_returns_the_interval(self):
        # slept ~2h: monotonic moved ~1s (real loop time) while wall jumped 7201s
        iv = km._detect_suspend(1000.0, 5000.0, 8201.0, 5001.0)
        self.assertIsNotNone(iv)
        start, end = iv
        self.assertAlmostEqual(start, 1001.0, msg="start ≈ prev_wall + monotonic delta (sleep onset)")
        self.assertAlmostEqual(end, 8201.0, msg="end = resume (now)")
        self.assertGreater(end - start, 7000, "interval ≈ the 2h sleep")

    def test_detect_suspend_none_on_first_tick(self):
        self.assertIsNone(km._detect_suspend(None, None, 1000.0, 5000.0))

    def test_suspended_after_brackets_the_sleep(self):
        saved = list(km._downtime)
        km._downtime[:] = [(2000.0, 9200.0)]             # a sleep from t=2000 to t=9200
        try:
            self.assertTrue(km._suspended_after(1500), "activity before the sleep → suspended after it")
            self.assertFalse(km._suspended_after(9500), "activity after the sleep → not suspended since")
        finally:
            km._downtime[:] = saved

    def test_awake_spans_excises_a_sleep_that_straddles_the_bar(self):
        saved = list(km._downtime)
        km._downtime[:] = [(1500.0, 9000.0)]             # a sleep from t=1500 to t=9000
        try:
            # a sleep covering the rest of the bar → the leading awake stretch only (old clip-at-onset case)
            self.assertEqual(km._awake_spans(1000, 9999), [[1000, 1500], [9000, 9999]],
                             "a bar straddling the sleep keeps work BEFORE and AFTER it, the sleep excised")
            self.assertEqual(km._awake_spans(2000, 9999), [[9000, 9999]], "a bar opened during the sleep starts at wake")
            self.assertEqual(km._awake_spans(1000, 1400), [[1000, 1400]], "a bar ending before the sleep is one span")
        finally:
            km._downtime[:] = saved

    def test_awake_spans_excises_every_sleep_keeping_post_wake_work(self):
        # The bugz case (the user 2026-06-22): one long autonomous segment straddles SEVERAL sleeps. The old
        # clip-at-first-sleep dropped every later awake stretch — hours of real work vanished from the lane
        # while the captioner kept captioning the still-open segment. Excision keeps each awake stretch.
        saved = list(km._downtime)
        km._downtime[:] = [(200.0, 300.0), (500.0, 900.0)]   # two naps inside one [100, 1000] segment
        try:
            self.assertEqual(km._awake_spans(100, 1000), [[100, 200], [300, 500], [900, 1000]],
                             "a segment across two sleeps → three awake bars, no post-wake work erased")
        finally:
            km._downtime[:] = saved

    def test_awake_spans_drops_dark_wake_slivers_carrying_no_activity(self):
        # The user 2026-07-23: overnight work bars at 5am and 6am with the lid shut, all redrawing the SAME
        # finished work. macOS dark-wake stirs the host every few minutes while it sleeps, so ONE overnight
        # suspension lands in the log as a chain of naps with ~45s awake slivers between them; excision alone
        # read every sliver as work and drew a bar per sliver, each carrying the segment's one prompt/summary.
        saved = list(km._downtime)
        km._downtime[:] = [(1000.0, 2000.0), (2045.0, 3000.0), (3045.0, 4000.0), (4045.0, 5000.0)]
        try:
            acts = [100.0, 500.0, 900.0, 5100.0, 5400.0]   # work before the lid shut, then after the real wake
            self.assertEqual(km._awake_spans(100, 5400, acts), [[100, 1000], [5000, 5400]],
                             "the dark-wake slivers hold no atoms → no bars; pre-sleep and post-wake work kept")
            self.assertEqual(km._awake_spans(100, 5400),
                             [[100, 1000], [2000, 2045], [3000, 3045], [4000, 4045], [5000, 5400]],
                             "acts=None keeps the raw awake split — callers with no atom times are unchanged")
        finally:
            km._downtime[:] = saved

    def test_awake_spans_keeps_a_wake_stretch_that_carries_activity(self):
        # The other side of the same rule, so the drop can never over-reach: a stretch the host really did
        # work in (an auto-nudge that fired while the lid was shut) has atoms, so it stays a bar.
        saved = list(km._downtime)
        km._downtime[:] = [(1000.0, 2000.0), (2045.0, 3000.0)]
        try:
            self.assertEqual(km._awake_spans(100, 3200, [100.0, 2020.0, 3100.0]),
                             [[100, 1000], [2000, 2045], [3000, 3200]],
                             "an atom inside a short wake stretch makes it real work → kept")
        finally:
            km._downtime[:] = saved

    def test_awake_spans_never_drops_a_segment_entirely(self):
        # The never-drop rule survives the activity filter: a segment whose atoms land in no surviving span
        # still renders its leading span, exactly as a sleep covering the whole span always has.
        saved = list(km._downtime)
        km._downtime[:] = [(1000.0, 2000.0)]
        try:
            self.assertEqual(km._awake_spans(100, 2500, [7777.0]), [[100, 1000]],
                             "no span carries activity → the leading span survives")
            self.assertEqual(km._awake_spans(100, 900, [None, 200.0]), [[100, 900]],
                             "an atom with no timestamp is skipped, not crashed on")
        finally:
            km._downtime[:] = saved


class SessionListNameCollision(unittest.TestCase):
    """Regression (the user 2026-06-22): two functions were both named _session_list — the picker payload
    builder _session_list(now, tmux) and a 0-arg tmux query for GET /sessions (commit 7b89bd9). The 0-arg
    def came LATER, so it SHADOWED the picker's. The webview's `requestSessions` handler calls it with two
    args → TypeError → the WS handler thread died → the socket dropped → the client reconnected and BLANKED
    the chat (wiping the half-typed new-session name), and the picker dropdown showed NO existing sessions.
    Fix: the GET /sessions query is its OWN distinct 0-arg name (now _session_rows). Guard the re-collision."""

    def test_picker_session_list_keeps_its_now_tmux_signature(self):
        import inspect
        params = inspect.signature(km._session_list).parameters
        self.assertEqual(list(params)[:2], ["now", "tmux"],
                         "the picker payload builder must stay callable as _session_list(now, tmux) — requestSessions calls it that way")
        # Anything AFTER those two must be optional (`window`, the 30-day deep list, 2026-07-24), so the
        # bare two-arg call the handler makes can never become a TypeError again — which is what this
        # regression is really about. A 0-arg def shadowing it still fails the first assert.
        self.assertTrue(all(p.default is not inspect.Parameter.empty for p in list(params.values())[2:]),
                        "extra picker params must carry defaults, so _session_list(now, tmux) keeps working")

    def test_session_rows_is_a_distinct_zero_arg_function(self):
        import inspect
        self.assertTrue(hasattr(km, "_session_rows"), "the GET /sessions query has its OWN name now")
        self.assertEqual(list(inspect.signature(km._session_rows).parameters), [],
                         "_session_rows is the 0-arg unified (tmux+SDK) query GET /sessions serves")

    def test_requestSessions_call_shape_does_not_raise_typeerror(self):
        # exactly how the WS handler invokes it (now, tmux) — must NOT TypeError (the shadowing bug), and the
        # result is the list shape renderPicker consumes. Empty fixture dir → [] is fine; we only guard the call.
        items = km._session_list(int(time.time()), {})
        self.assertIsInstance(items, list, "the picker payload is a list of session rows")


class SegLastText(unittest.TestCase):
    """_seg_last_text: the LAST assistant prose atom in a segment, preferring a SUBSTANTIVE one (≥80
    chars) — the summary deep-link FALLBACK when the distiller stored no citation (the user 2026-07-01;
    replaces the biggest-text-block pick, whose 'longest ever' monotonicity let a long early analysis hold
    the anchor forever). The floor sits at 80, just above connective stubs, NOT 200 (the user 2026-07-02):
    a terse-note agent's wrap-ups run 90-190 chars, and a 200 floor filtered every one of them out — the
    only 'substantive' message left was the opening restatement, the one message this anchor must avoid.
    Skips API-error atoms (a failed turn carries text but is never a jump target, like _seg_anchors)."""

    @staticmethod
    def _a(uuid, text, api=False):
        a = {"type": "assistant", "uuid": uuid, "message": {"content": [{"type": "text", "text": text}]}}
        if api:
            a["isApiError"] = True
        return a

    def test_prefers_the_last_substantive_atom_over_a_longer_early_one(self):
        early_long = "an early analysis with the most words by far " + "x" * 500
        late_wrap = "Shipped: merged to main and the tests pass. " + "y" * 200
        atoms = [self._a("u1", early_long), self._a("u2", "ok"), self._a("u3", late_wrap)]
        u, sub = km._seg_last_text(atoms)
        self.assertEqual(u, "u3", "the most CURRENT substantive message wins, not the longest ever")
        self.assertTrue(sub)

    def test_terse_wrapups_beat_a_long_opening_restatement(self):
        # the incident shape (the user 2026-07-02): a 200+ char opening restatement of the goals, then a
        # whole session of terse working notes (90-190 chars each). Under the old 200 floor the opener was
        # the only "substantive" atom, so the summary link landed on the plan instead of the outcome.
        opener = ("Three items: the first fix, the second fix, and the third fix, restating everything "
                  "the user asked for in one long opening paragraph. Let me check peers and set up a "
                  "worktree before starting on any of it.")                      # >200 chars, FIRST
        notes = ["First fix done, with a test pin. Now mapping how the second one flows end to end.",
                 "Root cause found for the flaky suite: two stray control bytes committed at HEAD.",
                 "All suites green on the merge. Merged to main, pushed, and the worktree is cleaned up."]   # 80-190 each
        atoms = [self._a("u1", opener)] + [self._a("u%d" % (i + 2), t) for i, t in enumerate(notes)]
        u, sub = km._seg_last_text(atoms)
        self.assertEqual(u, "u4", "the LAST real note (the wrap-up) holds the anchor, never the opener")
        self.assertTrue(sub)

    def test_falls_back_to_the_last_short_prose_when_nothing_substantive(self):
        atoms = [self._a("u1", "ok"), self._a("u2", "done")]
        u, sub = km._seg_last_text(atoms)
        self.assertEqual(u, "u2", "no substantive prose → the last short prose, flagged non-substantive")
        self.assertFalse(sub)

    def test_skips_api_error_atoms_even_when_last_and_substantive(self):
        atoms = [self._a("u1", "the real reply, plenty substantive " + "z" * 200),
                 self._a("uErr", "API Error: overloaded — " + "x" * 300, api=True)]
        u, sub = km._seg_last_text(atoms)
        self.assertEqual(u, "u1", "a trailing API-error line is never the jump target")
        self.assertTrue(sub)

    def test_no_assistant_prose_returns_none(self):
        atoms = [{"type": "user", "uuid": "u1", "message": {"content": "hi"}},
                 {"type": "assistant", "uuid": "u2", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}]
        u, sub = km._seg_last_text(atoms)
        self.assertIsNone(u)
        self.assertFalse(sub)


class WsLoopResilience(unittest.TestCase):
    """A bug in ONE webview->kernel message handler must not tear the socket down (the user 2026-06-22): _ws
    calls _dispatch_ws inside a per-message try/except, so a generic handler exception is logged and the loop
    keeps going (the NEXT message still processes), while a real socket error still propagates to disconnect.
    Before this, an unexpected exception (e.g. the _session_list TypeError) escaped the loop, dropped the
    socket, and the reconnect blanked the chat."""

    def _run_loop(self, frames, dispatch):
        import io, contextlib
        seen = {"types": [], "n": 0}

        class FakeSelf:
            headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
            path = "/ws?app=chat"
            rfile = io.BytesIO()
            wfile = io.BytesIO()
            # Frames now go out on the SOCKET: each client owns a queue + sender thread so one wedged
            # client cannot stall the shared push/heartbeat walk (see _ws_sender). This loop exercises
            # only the READ side, so the socket just has to absorb writes.
            connection = type("FakeSock", (), {"sendall": lambda self, b: None,
                                               "shutdown": lambda self, how: None})()
            close_connection = False
            def send_response(self, *a): pass
            def send_header(self, *a): pass
            def end_headers(self): pass
            def _dispatch_ws(self, msg, client):
                seen["n"] += 1
                seen["types"].append((msg or {}).get("type"))
                dispatch(msg)

        seq = list(frames)
        saved = km._ws_recv
        km._ws_recv = lambda rfile: seq.pop(0) if seq else (0x8, b"", True)   # script the frames; (0x8)=close
        try:
            with contextlib.redirect_stderr(io.StringIO()):             # swallow the logged traceback
                km.Handler._ws(FakeSelf())                              # returns normally on close / socket error
        finally:
            km._ws_recv = saved
        return seen

    def test_a_throwing_handler_does_not_end_the_loop(self):
        TEXT = 0x1
        frames = [(TEXT, b'{"type":"a"}', True), (TEXT, b'{"type":"b"}', True), (0x8, b"", True)]   # a raises, b must still run
        seen = self._run_loop(frames, lambda msg: (_ for _ in ()).throw(RuntimeError("boom"))
                              if msg.get("type") == "a" else None)
        self.assertEqual(seen["types"], ["a", "b"], "'b' still processed after 'a' raised — the socket survived")

    def test_a_socket_error_propagates_and_stops_the_loop(self):
        TEXT = 0x1
        frames = [(TEXT, b'{"type":"a"}', True), (TEXT, b'{"type":"b"}', True), (0x8, b"", True)]
        seen = self._run_loop(frames, lambda msg: (_ for _ in ()).throw(BrokenPipeError("gone")))
        self.assertEqual(seen["types"], ["a"], "a real socket error ends the loop — 'b' never runs (genuine disconnect)")


class CreateDirResolution(unittest.TestCase):
    """A session's working directory is fixed at creation, so the kernel validates the UI-supplied dir up
    front (_resolve_create_dir) and reads it back for the lane/recent-dirs (_cwd_of). (the user 2026-06-22)"""

    def setUp(self):
        self.names = Path(tempfile.mkdtemp()) / "names"
        self.names.mkdir(parents=True)
        self._saved_names = km.NAMES
        km.NAMES = self.names
        self._saved_romp_dir = os.environ.get("ROMP_DIR")
        os.environ.pop("ROMP_DIR", None)                                 # control the default base (ROMP_DIR) per-test
        self._saved_ddfile = km._DEFAULT_DIR_FILE
        km._DEFAULT_DIR_FILE = Path(tempfile.mkdtemp()) / "default-dir"   # isolate from a real ~/.config/romp/default-dir

    def tearDown(self):
        km.NAMES = self._saved_names
        km._DEFAULT_DIR_FILE = self._saved_ddfile
        if self._saved_romp_dir is None:
            os.environ.pop("ROMP_DIR", None)
        else:
            os.environ["ROMP_DIR"] = self._saved_romp_dir

    def test_default_dir_file_overrides_install_dir_and_set_clear_validate(self):
        """The persisted file (gear/CLI) OVERRIDES the ROMP_DIR install-dir default; _set_default_dir writes/
        clears/validates; a file pointing at a now-missing dir is ignored (the user 2026-06-23)."""
        os.environ["ROMP_DIR"] = "/tmp"
        self.assertEqual(km._default_create_dir(), "/tmp")               # no file → the romp install dir (ROMP_DIR)
        d = tempfile.mkdtemp()
        path, err = km._set_default_dir(d)
        self.assertIsNone(err)
        self.assertEqual(path, os.path.realpath(d))
        self.assertEqual(km._default_create_dir(), os.path.realpath(d))  # file OVERRIDES ROMP_DIR
        _, err2 = km._set_default_dir("/no/such/xyz123")
        self.assertIn("not found", err2)                                 # a bad path is rejected
        self.assertEqual(km._default_create_dir(), os.path.realpath(d))  # ...and the file is unchanged
        km._set_default_dir("")                                          # clear
        self.assertEqual(km._default_create_dir(), "/tmp")               # revert to ROMP_DIR
        km._DEFAULT_DIR_FILE.write_text("/gone/missing\n")               # file points at a now-missing dir
        self.assertEqual(km._default_create_dir(), "/tmp")               # → ignored, falls through to ROMP_DIR

    def test_falls_back_to_home_when_romp_dir_unset_or_bogus(self):
        os.environ.pop("ROMP_DIR", None)                                 # no install dir in env → ~
        self.assertEqual(km._default_create_dir(), os.path.expanduser("~"))
        os.environ["ROMP_DIR"] = "/no/such/install/xyz123"               # set, but not a real directory → ignored
        self.assertEqual(km._default_create_dir(), os.path.expanduser("~"), "a bogus ROMP_DIR falls through to ~")

    def test_version_info_includes_default_dir(self):
        self.assertIn("defaultDir", km._version_info())                  # the gear loads its field from here

    def test_gear_persists_default_dir_with_browse(self):
        self.assertIn("rs-defaultdir-browse", _gear_src())             # the gear's Browse button
        self.assertIn("setDefaultDir", _gear_src())                      # change → kernel-side persist (the file)
        self.assertIn("target: 'gear'", _gear_src())                      # Browse posts browseDir target=gear

    def test_blank_dir_falls_back_to_default_no_error(self):
        os.environ["ROMP_DIR"] = "/tmp"
        for raw in ("", "   ", None):
            path, err = km._resolve_create_dir(raw)
            self.assertIsNone(err)
            self.assertEqual(path, "/tmp")               # default is NOT realpath'd, just ROMP_DIR

    def test_missing_dir_is_rejected(self):
        path, err = km._resolve_create_dir("/no/such/dir/xyz123")
        self.assertIsNone(path)
        self.assertIn("not found", err)

    def test_valid_dir_resolved_to_realpath(self):
        d = tempfile.mkdtemp()
        path, err = km._resolve_create_dir("  " + d + "  ")   # surrounding whitespace trimmed
        self.assertIsNone(err)
        self.assertEqual(path, os.path.realpath(d))

    def test_tilde_and_env_expanded(self):
        path, err = km._resolve_create_dir("~")
        self.assertIsNone(err)
        self.assertEqual(path, os.path.realpath(os.path.expanduser("~")))

    def test_pick_folder_exists_for_browse(self):
        self.assertTrue(callable(km._pick_folder), "a native folder picker backs the Browse button")

    def test_cwd_of_reads_names_second_field(self):
        sid = "11111111-2222-3333-4444-555555555555"
        (self.names / sid).write_text("mysess\t/work/proj\t#1EA1EB\twhite\n")
        self.assertEqual(km._cwd_of(sid), "/work/proj")
        self.assertEqual(km._cwd_of("no-such-sid"), "")    # missing names entry → ""
        (self.names / "noctab").write_text("justaname\n")  # no cwd field
        self.assertEqual(km._cwd_of("noctab"), "")


class SessionOrderStable(unittest.TestCase):
    """Tabs + timeline lanes must NOT auto-reorder by activity — even when a session goes dead/idle: it
    keeps its persisted slot, only a drag reorders (the user 2026-06-23). Before the fix, dead lanes were
    pulled into a separate mtime-sorted block, so a session jumped slots the moment it died."""
    def setUp(self):
        self._saved = (km._ordered_alive, km._alive_sessions, km._sessions, km._session_order,
                       set(km._kept_open))

    def tearDown(self):
        (km._ordered_alive, km._alive_sessions, km._sessions, km._session_order,
         kept) = self._saved
        km._kept_open.clear(); km._kept_open.update(kept)

    def _fleet(self):
        # B is the MOST recently active (mtime newest) — it would sort FIRST under the old mtime ordering.
        # mtimes are within the 12h dead-lane window (the user 2026-06-26) so dead B is still a timeline lane.
        A = {"sid": "A", "name": "a", "path": "/a", "mtime": NOW - 200}
        B = {"sid": "B", "name": "b", "path": "/b", "mtime": NOW - 5}
        C = {"sid": "C", "name": "c", "path": "/c", "mtime": NOW - 400}
        km._session_order = lambda: ["A", "B", "C"]      # the persisted (drag) order
        km._sessions = lambda now: [B, A, C]             # _sessions is mtime-DESC → B first
        # _chat_tab_sessions/_timeline_sessions now read _alive_sessions directly and order via _ordered
        # (the session-order refactor, 15f5037) — stub THAT for the live list; _ordered_alive is no longer
        # on their path. B has DIED → only A, C live, in persisted order.
        km._alive_sessions = lambda now, tmux: [A, C]
        km._ordered_alive = lambda now, tmux: [A, C]
        return A, B, C

    def test_dead_timeline_lane_keeps_its_slot(self):
        self._fleet()
        got = [s["sid"] for s in km._timeline_sessions(NOW, {})]
        self.assertEqual(got, ["A", "B", "C"], "dead B stays at its slot (idx 1), not mtime-first nor shoved to the end")

    def test_dead_chat_tab_keeps_its_slot(self):
        self._fleet()
        km._kept_open.clear(); km._kept_open.add("B")    # B's tab kept open (read-only) after death
        got = [s["sid"] for s in km._chat_tab_sessions(NOW, {})]
        self.assertEqual(got, ["A", "B", "C"], "kept-open dead tab keeps its slot, same stable key as the timeline")


class TmuxInputEcho(unittest.TestCase):
    """Optimistic input echo for tmux sends (the user via bugs 2026-06-25): the SDK backend echoes a
    composer message instantly, but tmux had none — a send whose Enter dropped at the pane prompt was
    INVISIBLE in the web chat, so the user thought they'd replied. _merge_live_atoms now also merges a
    kernel-side _tmux_echo for tmux sids: a successful send's echo prunes when the real user turn writes;
    a DROPPED send's echo PERSISTS so the lost message stays visible. Synthetic only."""

    def setUp(self):
        self._saved_sdk = km._sdk
        km._sdk = lambda: None                 # tmux path: no SDK backend owns the sid
        km._tmux_echo.clear()

    def tearDown(self):
        km._sdk = self._saved_sdk
        km._tmux_echo.clear()

    def _session(self, atoms):
        return {"turns": [{"id": "t", "trigger": None, "t": T0, "end": T0, "ended": True, "atoms": atoms}]}

    def _real_user(self, text, uid="real-1"):
        return {"type": "user", "uuid": uid,
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}}

    def test_echo_shows_instantly_before_the_transcript_has_it(self):
        km._tmux_echo_add(SID, "restart Obsidian and I'll check")
        merged = km._merge_live_atoms(self._session([self._real_user("earlier")]), SID)
        texts = [km._atom_user_text(a) for a in merged["turns"][-1]["atoms"]]
        self.assertIn("restart Obsidian and I'll check", texts, "the tmux send echoes instantly, ahead of disk")
        self.assertTrue(merged["turns"][-1]["ended"], "but an echo must NOT reopen the turn (it's a user msg, not work)")

    def test_successful_send_echo_prunes_when_the_real_user_atom_lands(self):
        text = "edit the files and I'll rerun"
        km._tmux_echo_add(SID, text)
        merged = km._merge_live_atoms(self._session([self._real_user(text)]), SID)
        texts = [km._atom_user_text(a) for a in merged["turns"][-1]["atoms"]]
        self.assertEqual(texts.count(text), 1, "no duplicate bubble: the echo dedups against the real atom")
        self.assertNotIn(SID, km._tmux_echo, "the echo is pruned once the real user turn writes")

    def test_dropped_send_echo_persists_so_the_lost_message_stays_visible(self):
        km._tmux_echo_add(SID, "this Enter dropped at the prompt")
        merged = km._merge_live_atoms(self._session([]), SID)   # transcript never gets it — the send dropped
        texts = [km._atom_user_text(a) for a in merged["turns"][-1]["atoms"]]
        self.assertIn("this Enter dropped at the prompt", texts, "a dropped send stays visible, not silent")
        self.assertIn(SID, km._tmux_echo, "the echo persists until the real turn lands")

    def test_no_echo_is_a_noop(self):
        sess = self._session([self._real_user("hello")])
        self.assertIs(km._merge_live_atoms(sess, SID), sess, "no live echo → session returned unchanged")

    def test_echo_suppressed_when_its_text_is_already_shown_as_queued(self):
        # No double-show: a send that's QUEUED behind a busy turn is surfaced by the event-based
        # kind:"queued" indicator. The echo for that same text must be hidden so it doesn't render twice.
        text = "do the thing while you're busy"
        km._tmux_echo_add(SID, text)
        merged = km._merge_live_atoms(self._session([]), SID, shown_texts=[text])
        texts = [km._atom_user_text(a) for a in merged["turns"][-1]["atoms"]]
        self.assertNotIn(text, texts, "a queued message is owned by the queued indicator, not double-shown by the echo")
        self.assertIn(SID, km._tmux_echo, "the echo is only HIDDEN while queued, not pruned — it retires when the real atom lands")

    def test_echo_only_merge_does_not_make_the_session_look_working(self):
        # THE chat↔timeline split (the user 2026-06-25): the chat is the only surface that merges live
        # atoms, and it forced the last turn open for ANY fresh atom — so a lingering input echo (a dropped
        # send persists forever) made the chat show 'working' + a counting timer while the timeline/feed (no
        # merge) correctly showed idle. An echo is a pending USER message, not the assistant working.
        ended_turn = self._session([self._real_user("earlier prompt")])  # last turn is ended=True
        km._tmux_echo_add(SID, "this send dropped — its echo lingers")
        merged = km._merge_live_atoms(ended_turn, SID)
        self.assertTrue(merged["turns"][-1]["ended"], "an echo-only merge keeps the turn ENDED (not reopened)")
        self.assertFalse(km._session_working(merged["turns"]), "a lone echo must NOT read as working")
        texts = [km._atom_user_text(a) for a in merged["turns"][-1]["atoms"]]
        self.assertIn("this send dropped — its echo lingers", texts, "the echo still renders (stays visible)")

    def test_live_assistant_work_still_reopens_the_turn(self):
        # The flip side: a genuine live ASSISTANT atom (an SDK stream reply leading the disk write) DOES
        # reopen the turn → working. Only the lone-echo case is suppressed.
        saved = km._sdk
        live = [{"type": "assistant", "uuid": "live-a", "t": NOW,
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}]}}]
        km._sdk = lambda: type("B", (), {"owns": lambda self, s: True,
                                         "live_atoms": lambda self, s: live,
                                         "prune_live": lambda self, s, u, t, hf=0: None})()
        try:
            merged = km._merge_live_atoms(self._session([self._real_user("go")]), SID)
        finally:
            km._sdk = saved
        self.assertFalse(merged["turns"][-1]["ended"], "live assistant work reopens the turn")
        self.assertTrue(km._session_working(merged["turns"]), "streaming assistant work reads as working")

    def test_queued_suppression_strips_whitespace(self):
        # _pending_queued .strip()s its texts; the echo stores the raw composer text. Match on stripped text.
        km._tmux_echo_add(SID, "padded message\n")
        merged = km._merge_live_atoms(self._session([]), SID, shown_texts=["padded message"])
        texts = [km._atom_user_text(a) for a in merged["turns"][-1]["atoms"]]
        self.assertNotIn("padded message", texts, "stripped-text match suppresses the echo against the queued bubble")


class TmuxEchoSettledByALaterTurn(unittest.TestCase):
    """A tmux echo the transcript has OVERTAKEN (the user 2026-08-26). The echo outlives a later turn on
    purpose — that is how a send the pane dropped stays visible — but "still visible" had come to mean
    "still posing as pending": days-old echoes were folded into the queued indicator on every busy push,
    and off it they drew as ordinary sent bubbles with no way to clear them. The settling EVENT is a
    genuine-human turn landing strictly later than the send: the pane is FIFO, so nothing typed after a
    message the CLI still held could overtake it. Past that, the echo is marked dropped — the shipped
    "never delivered" treatment, restore + dismiss — and never pruned out from under the user. Synthetic
    only; the SDK's own floor semantics live in test_sdk_echo_durability.py."""

    LANDED_AT = T0 + 500
    SENT_BEFORE = T0 + 100          # overtaken: the transcript took a human turn after this
    SENT_AFTER = T0 + 900           # still in flight: nothing has overtaken it

    def setUp(self):
        self._saved_sdk = km._sdk
        km._sdk = lambda: None                 # tmux path: no SDK backend owns the sid
        km._tmux_echo.clear()

    def tearDown(self):
        km._sdk = self._saved_sdk
        km._tmux_echo.clear()

    def _session_with_landed_human_turn(self):
        return {"turns": [{"id": "t", "trigger": None, "t": T0, "end": T0, "ended": True,
                           "atoms": [{"type": "user", "uuid": "real-1", "author": "human",
                                      "t": self.LANDED_AT,
                                      "message": {"role": "user",
                                                  "content": [{"type": "text", "text": "a later ask"}]}}]}]}

    def _echo_at(self, text, sent_at):
        km._tmux_echo_add(SID, text)
        for echo_atom in km._tmux_echo[SID].values():
            if echo_atom.get("_echo_text") == text:
                echo_atom["t"] = sent_at
                return echo_atom
        raise AssertionError("the echo was not stored")

    def test_overtaken_echo_is_MARKED_dropped_and_kept_visible(self):
        echo_atom = self._echo_at("this one never made it in", self.SENT_BEFORE)
        km._merge_live_atoms(self._session_with_landed_human_turn(), SID)
        self.assertTrue(echo_atom.get("dropped"), "an overtaken send reads as the loss it is")
        self.assertIn(SID, km._tmux_echo, "marked, never pruned — the only copy of the text is in here")

    def test_in_flight_echo_is_left_untouched(self):
        echo_atom = self._echo_at("just typed, still going out", self.SENT_AFTER)
        km._merge_live_atoms(self._session_with_landed_human_turn(), SID)
        self.assertFalse(echo_atom.get("dropped"), "nothing has overtaken it — it is still in flight")

    def test_a_send_in_the_SAME_second_as_a_landed_turn_stays_in_flight(self):
        # Strictly later, never at-or-later: the equality case is a send racing the turn that happens to
        # share its second, and treating that as overtaken would put the 2026-06-29 solid-then-dotted
        # flicker back for it.
        echo_atom = self._echo_at("same second as the turn", self.LANDED_AT)
        km._merge_live_atoms(self._session_with_landed_human_turn(), SID)
        self.assertFalse(echo_atom.get("dropped"))

    def test_an_interrupt_record_does_not_settle_an_echo(self):
        # _human_turn_floor excludes the interrupt record (the user 2026-07-07): it authors human but is a
        # STOP event, not a message that landed and processed the send.
        session = self._session_with_landed_human_turn()
        session["turns"][0]["atoms"] = [
            {"type": "user", "uuid": "int-1", "author": "human", "t": self.LANDED_AT,
             "message": {"role": "user", "content": [{"type": "text",
                                                      "text": "[Request interrupted by user]"}]}}]
        echo_atom = self._echo_at("sent just before the stop", self.SENT_BEFORE)
        km._merge_live_atoms(session, SID)
        self.assertFalse(echo_atom.get("dropped"), "a stop is not a delivered turn")

    def test_dismiss_echo_clears_a_settled_one_and_refuses_an_in_flight_one(self):
        # Without a tmux dismiss_echo the ✕ on the "never delivered" bubble was a fake affordance: the
        # kernel's drive op is gated on hasattr(be, "dismiss_echo"), so the click acknowledged and the
        # bubble returned on the next push.
        backend = km.TmuxBackend()
        in_flight = self._echo_at("still going out", self.SENT_AFTER)
        settled = self._echo_at("gone for good", self.SENT_BEFORE)
        km._merge_live_atoms(self._session_with_landed_human_turn(), SID)
        self.assertIsNone(backend.dismiss_echo(SID, uuid=in_flight["uuid"]),
                          "an in-flight send is not the user's to clear")
        self.assertEqual(backend.dismiss_echo(SID, uuid=settled["uuid"]), "gone for good")
        self.assertIsNone(backend.dismiss_echo(SID, uuid=settled["uuid"]), "idempotent: a miss is a no-op")
        remaining = [a.get("_echo_text") for a in km._tmux_echo_atoms(SID)]
        self.assertEqual(remaining, ["still going out"], "only the dismissed one goes")


class TestCloserSettledGate(unittest.TestCase):
    """Auto-nudge must wait for the closer's verdict on the turn AT ITS CURRENT SIZE, not merely the
    turn id's presence in closedTurns — an interrupt+resume reuses the turn id, so a turn closed at an
    earlier idle then grown is stale until the closer re-judges it (the user 2026-06-27). _closer_settled
    mirrors the closer's own closedSig freshness check (judge:2449)."""

    TID = "turn-aaaa-bbbb"

    def _store(self, closed=None, sig=None):
        return {"closedTurns": list(closed or []), "closedSig": dict(sig or {})}

    def test_not_closed_is_not_settled(self):
        self.assertFalse(km._closer_settled(self._store(), self.TID, 5),
                         "a turn the closer hasn't processed at all is not settled")

    def test_closed_at_current_size_is_settled(self):
        store = self._store(closed=[self.TID], sig={self.TID: 5})
        self.assertTrue(km._closer_settled(store, self.TID, 5),
                        "closed AND sig matches the current atom count → verdict reflects this turn")

    def test_closed_then_grown_is_not_settled(self):
        # The race: closer closed the turn at 5 atoms (an earlier idle), then it resumed/grew to 9.
        # Bare membership would (wrongly) pass; the sig mismatch holds the nudge until the re-judge lands.
        store = self._store(closed=[self.TID], sig={self.TID: 5})
        self.assertFalse(km._closer_settled(store, self.TID, 9),
                         "turn grew since the closer judged it → stale verdict, not settled")

    def test_legacy_close_without_sig_is_assumed_settled(self):
        store = self._store(closed=[self.TID], sig={})   # closed before closedSig existed
        self.assertTrue(km._closer_settled(store, self.TID, 9),
                        "legacy close (no sig) is assumed current, matching the closer (judge:2449)")

    def test_closer_off_is_always_settled(self):
        saved = jd.CLOSER_ON
        jd.CLOSER_ON = False
        try:
            self.assertTrue(km._closer_settled(self._store(), self.TID, 5),
                            "closer off → nothing to wait for, never suppress the nudge")
        finally:
            jd.CLOSER_ON = saved

    # ── _closer_pending: the feed's Analyzing… gap flag (the user 2026-07-13) rides the same event ──
    def _with_parse(self, turns):
        # _closer_pending must read the judge's OWN parse (parsed_session): its turn ids/atom counts are
        # what closedTurns/closedSig record — the kernel's states-less _parse diverges on idle-led turns.
        saved = jd.parsed_session
        jd.parsed_session = lambda sid, files, now: {"turns": turns}
        return saved

    def test_pending_while_the_latest_turn_awaits_the_closers_verdict(self):
        saved = self._with_parse([{"id": self.TID, "atoms": [{}] * 5}])
        try:
            self.assertTrue(km._closer_pending("sid", "/tmp/t.jsonl", 0, self._store()),
                            "settled turn, no verdict recorded → the Analyzing… gap is live")
            store = self._store(closed=[self.TID], sig={self.TID: 5})
            self.assertFalse(km._closer_pending("sid", "/tmp/t.jsonl", 0, store),
                             "verdict recorded at the turn's current size → gap over")
            grown = self._store(closed=[self.TID], sig={self.TID: 3})
            self.assertTrue(km._closer_pending("sid", "/tmp/t.jsonl", 0, grown),
                            "the turn grew past the recorded verdict → analyzing again (re-judge due)")
        finally:
            jd.parsed_session = saved

    def test_pending_is_false_on_an_empty_or_unreadable_parse(self):
        # No turns / a raising parse must read NOT pending — a cosmetic swirl never blocks the paint and
        # never spins on a session with nothing to judge.
        saved = self._with_parse([])
        try:
            self.assertFalse(km._closer_pending("sid", "/tmp/t.jsonl", 0, self._store()))
            jd.parsed_session = lambda sid, files, now: (_ for _ in ()).throw(OSError("gone"))
            self.assertFalse(km._closer_pending("sid", "/tmp/t.jsonl", 0, self._store()))
        finally:
            jd.parsed_session = saved


class SlashCommands(unittest.TestCase):
    """The composer's "/" autocomplete list (the user 2026-06-29): /commands serves the per-cwd slash-command
    list, sourced from the Agent SDK's get_server_info(), cached + background-warmed. The cache logic is tested
    directly (no live `claude` probe); the endpoint + designed-API source are pinned."""

    def test_cache_serves_fresh_without_reprobe(self):
        km._CMD_CACHE.clear()
        km._CMD_CACHE["/tmp/proj"] = {"commands": [{"name": "clear", "description": "d"}],
                                      "ts": km.time.time(), "warming": False, "err": ""}
        cmds, warming = km._commands_for_cwd("/tmp/proj")
        self.assertFalse(warming, "a fresh cache entry serves immediately, not warming")
        self.assertEqual(cmds[0]["name"], "clear")

    def test_cache_reports_warming_without_spawning_a_second_probe(self):
        # an entry already flagged warming → return warming, DON'T kick another probe thread
        km._CMD_CACHE.clear()
        km._CMD_CACHE["/tmp/cold"] = {"commands": [], "ts": 0.0, "warming": True, "err": ""}
        cmds, warming = km._commands_for_cwd("/tmp/cold")
        self.assertTrue(warming)
        self.assertEqual(cmds, [])

    def test_endpoint_and_designed_sdk_source(self):
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('if p == "/commands":', src)                      # the GET endpoint exists
        self.assertIn('json.dumps({"commands": cmds, "warming": warming})', src)
        # the list comes from the SDK's DESIGNED get_server_info()['commands'] — not pane-scraping, not a
        # hand-maintained built-in list (the repo rule: use the SDK's designed API)
        self.assertIn("c.get_server_info()", src)
        self.assertIn('.get("commands", [])', src)


class BootWarm(unittest.TestCase):
    """_boot_warm pre-parses the living fleet into the kernel parse cache at STARTUP, during the browser's
    reconnect/reload gap, so the first connect is warm instead of paying the cold serial parse (the user
    2026-07-03: local sessions take a long time to load on restart)."""
    def setUp(self):
        self._saved = (km._alive_sessions, km._has_parsing_client, km._parse, km._tmux_sessions, km.jd.discover)
        self.parsed = []
        km.jd.discover = lambda now: []
        km._tmux_sessions = lambda: {}
        km._parse = lambda path, sid, now: self.parsed.append(sid)

    def tearDown(self):
        (km._alive_sessions, km._has_parsing_client, km._parse, km._tmux_sessions, km.jd.discover) = self._saved

    def _wait(self, pred, timeout=1.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        return pred()

    def test_boot_warm_parses_every_live_session(self):
        km._has_parsing_client = lambda: False
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": "/p1"}, {"sid": "s2", "path": "/p2"}]
        km._boot_warm()
        self.assertTrue(self._wait(lambda: sorted(self.parsed) == ["s1", "s2"]),
                        "boot-warm parsed every live session into the cache")

    def test_boot_warm_stands_down_for_a_live_parsing_client(self):
        km._has_parsing_client = lambda: True     # the browser already reconnected → its build warms the cache
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": "/p1"}]
        km._boot_warm()
        time.sleep(0.1)
        self.assertEqual(self.parsed, [], "boot-warm defers to a live parsing client — no GIL contention")

    def test_boot_warm_is_wired_at_startup_before_the_producer(self):
        src = Path(BIN, "romp-kernel").read_text()
        self.assertLess(src.rindex("_boot_warm()"), src.index("threading.Thread(target=_producer"),
                        "the boot warm kicks off at startup, ahead of the producer/pusher threads")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# The gear moved from kernel-inline strings into the shared feed bundle
# (2026-07-13): ui/webview/gear.js is the single source both hosts render, so
# the gear pins read THAT file (and feed.css for its styling).
def _gear_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()


def _gear_css_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.css").read_text()


class PostalPeerTunnels(unittest.TestCase):
    """Peer-bus mode stage 1 (plans/postal-peer-buses.md): flag OFF keeps today's tunnel argv
    untouched; flag ON drops the fixed-port -R (it collides with the remote's OWN bus, and
    ExitOnForwardFailure would kill the whole tunnel) for a second ephemeral -L that dials the
    remote's bus — stage 2's peering protocol is duplex over that one connection."""

    R = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 50001, "bus_port": 50002}

    def test_flag_off_keeps_the_reverse_forward(self):
        os.environ["ROMP_POSTAL_PEERS"] = "0"          # peer mode is the DEFAULT now; 0 = legacy scheme
        argv = km._tunnel_argv(dict(self.R))
        self.assertIn("-R", argv, "today's singleton scheme is untouched with the flag off")
        self.assertIn("%d:127.0.0.1:%d" % (km.BUS_PORT, km.BUS_PORT), argv)

    def test_flag_on_swaps_the_reverse_forward_for_a_bus_dial(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        try:
            argv = km._tunnel_argv(dict(self.R))
        finally:
            os.environ.pop("ROMP_POSTAL_PEERS", None)
        self.assertNotIn("-R", argv, "no fixed-port reverse forward in peer mode")
        self.assertIn("50002:127.0.0.1:%d" % km.BUS_PORT, argv, "the ephemeral -L dials the remote's bus")
        self.assertIn("50001:127.0.0.1:29855", argv, "the kernel forward is unchanged")

    def test_notify_bus_peer_is_guarded(self):
        saved = km.BUS_PORT
        km.BUS_PORT = 1                    # nothing listens here → refused instantly
        try:
            self.assertFalse(km._notify_bus_peer("TESTHOST", 50002, True),
                             "postal down → False, never an exception (the supervisor must survive)")
        finally:
            km.BUS_PORT = saved


class CheckinMechanics(unittest.TestCase):
    """Peer-bus stage 3a (plans/postal-peer-buses.md; resolved decisions 1-3): the mobile machine
    publishes itself to a hub over its own OUTBOUND ssh — reverse forwards plus a token-PUSH
    handshake — and the hub records it like an attached remote it owns no ssh for. All credentials
    flow outward; the hub never holds a way into the mobile machine."""

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        os.environ.pop("ROMP_HOST_NAME", None)
        km._remotes.pop("TESTHOST", None)
        km._remotes.pop("hubhost", None)

    def test_checkin_argv_adds_the_reverse_forwards(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        r = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 50001, "bus_port": 50002,
             "checkin": True, "rk_port": 50003, "rb_port": 50004}
        argv = km._tunnel_argv(r)
        self.assertIn("50003:127.0.0.1:%d" % km.PORT, argv, "-R publishes our kernel on the hub")
        self.assertIn("50004:127.0.0.1:%d" % km.BUS_PORT, argv, "-R publishes our bus on the hub")
        self.assertEqual(argv.count("-R"), 2)

    def test_plain_peer_attach_argv_has_no_reverse_forwards(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        r = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 50001, "bus_port": 50002}
        self.assertNotIn("-R", km._tunnel_argv(r))

    def test_checkin_payload_pushes_ports_and_token(self):
        os.environ["ROMP_HOST_NAME"] = "TESTHOST"
        p = km._checkin_payload({"rk_port": 50003, "rb_port": 50004, "local_port": 50001})
        self.assertEqual((p["host"], p["kernelPort"], p["busPort"]), ("TESTHOST", 50003, 50004))
        self.assertTrue(p["token"], "the token is HANDED to the hub — it never fetches credentials")

    def test_checkin_apply_records_a_sshless_row(self):
        payload, status = km.checkin_apply({"host": "TESTHOST", "kernelPort": 50003,
                                            "busPort": 50004, "token": "tok"})
        self.assertEqual(status, 200)
        r = km._remotes["TESTHOST"]
        self.assertTrue(r["checkin_peer"])
        self.assertIsNone(r["proc"], "the hub owns no ssh for a checked-in host")
        self.assertEqual((r["local_port"], r["bus_port"], r["token"]), (50003, 50004, "tok"))

    def test_checkin_apply_validates_and_refuses_hijack(self):
        for bad in ({}, {"host": "x"}, {"host": "x", "kernelPort": 1},
                    {"host": "x", "kernelPort": 0, "busPort": 5},
                    {"host": "x", "kernelPort": True, "busPort": 5}):
            payload, status = km.checkin_apply(bad)
            self.assertEqual(status, 400, repr(bad))
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 1, "proc": None}
        payload, status = km.checkin_apply({"host": "TESTHOST", "kernelPort": 50003, "busPort": 50004})
        self.assertEqual(status, 409, "an ssh-attached row is never silently converted")

    def test_checkin_set_flags_ports_and_checkout_clears(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 50001,
                                   "bus_port": 50002, "proc": None, "status": "up", "detail": "", "sids": []}
        saved = km._checkin_stop_hub
        km._checkin_stop_hub = lambda r: None
        try:
            pub = km.checkin_set("TESTHOST", True)
            self.assertTrue(pub["checkin"])
            self.assertTrue(km._remotes["TESTHOST"].get("rk_port"), "enable picks the reverse-forward ports")
            pub = km.checkin_set("TESTHOST", False)
            self.assertFalse(pub["checkin"], "checkout clears the flag")
            self.assertIsNone(km.checkin_set("NOSUCH", True), "unknown host: attach it first")
        finally:
            km._checkin_stop_hub = saved

    def test_checkin_stop_only_forgets_checked_in_rows(self):
        os.environ["ROMP_POSTAL_PEERS"] = "0"          # keep detach's bus notify away from the real bus
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 1, "proc": None}
        self.assertFalse(km.checkin_stop("TESTHOST"), "an ssh-attached row is not checkout-able")
        km._remotes["hubhost"] = {"host": "hubhost", "checkin_peer": True, "kernel_port": 5,
                                  "local_port": 5, "bus_port": 6, "proc": None}
        self.assertTrue(km.checkin_stop("hubhost"))
        self.assertNotIn("hubhost", km._remotes, "checkout forgets the row and the pushed token with it")


class CheckinSurfaces(unittest.TestCase):
    """Stage 3b: the keep-connected checkbox is real, wired, and honest."""

    def test_popover_carries_the_keep_connected_checkbox(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("data-k=", js, "each attachable row gets the checkbox")
        self.assertIn("/tunnels/checkin", js, "...wired to the checkin backend")
        self.assertIn("checked in here", js, "a hub labels rows that checked in to it")
        self.assertIn("keep-connected on ", js, "failures alert loudly, never silently revert")

    def test_tunnels_payload_and_css_carry_the_surface(self):
        import inspect
        src = inspect.getsource(km)
        self.assertIn('"peersMode": _postal_peers_on()', src,
                      "/tunnels tells the popover whether to show the checkbox")
        self.assertIn(".rnet-keep{", src, "the checkbox has its own style, font-size matching .st")

    def test_peer_mode_is_the_default(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        self.assertTrue(km._postal_peers_on(), "peer-bus mode is the default (the user's activation)")
        os.environ["ROMP_POSTAL_PEERS"] = "off"
        try:
            self.assertFalse(km._postal_peers_on())
        finally:
            os.environ.pop("ROMP_POSTAL_PEERS", None)


class WaitGraphDelegatesAndStampSupersede(unittest.TestCase):
    """DELEGATE handoffs in the wait-for graph + the peer-answer supersede on durable ⏳ stamps (the user
    2026-07-25: a handoff to a peer wore the generic "Awaiting background agents" box because only
    QUESTION-kind rows made edges, and the closer's stamp kept the card awaiting 5h after the delegated
    peer had actually replied — the reply is the exact event the wait was for, so it must end the wait)."""

    A, B = "aaaa1111", "bbbb2222"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))
        (Path(self.td.name) / "timeline").mkdir(parents=True)
        jd.GOALDIR.mkdir(parents=True)
        km._POSTAL_WAIT_CACHE[:] = [None, None]
        km._SESSION_STAMP_CACHE.clear()

    def tearDown(self):
        jd._rebind_state(self.saved_state)
        km._POSTAL_WAIT_CACHE[:] = [None, None]
        km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _log(self, *rows):
        with open(jd.MESSAGES, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def _msg(self, f, t, ts, kind, body="synthetic body"):
        return {"ev": "sent", "id": "m%d" % ts, "from_id": f, "to_id": t, "t": ts,
                "from": "x", "body": body, "kind": kind}

    def test_a_cross_host_reply_addressed_to_the_relay_still_answers_the_ask(self):
        # obsidian↔lab_manager (2026-08-15): a cross-host reply is logged to_id "peer:<host>" (the
        # relay), not the recipient's sid — the (from,to) pair never closed and the asker wore
        # "Awaiting <peer>" forever after the answer landed. The row's toName resolves through the
        # alias map every remote sender's rows build (from_host + from -> from_id).
        self._log(
            # the remote asker's question, stamped with its host+name (this row TEACHES the alias)
            dict(self._msg(self.A, self.B, NOW - 300, "question"),
                 **{"from": "web", "from_host": "TESTHOST"}),
            # the local session's reply, addressed to the relay — the observed cross-host shape
            dict(self._msg(self.B, "peer:TESTHOST", NOW - 200, "coordinate"),
                 toName="TESTHOST:web"))
        g = km._wait_for_graph(NOW, {self.A, self.B})
        self.assertNotIn(self.A, g, "the relay-addressed reply answers the ask once toName resolves")

    def test_an_unresolvable_relay_row_behaves_as_before(self):
        self._log(self._msg(self.A, self.B, NOW - 300, "question"),
                  dict(self._msg(self.B, "peer:TESTHOST", NOW - 200, "coordinate"),
                       toName="TESTHOST:never-seen"))
        g = km._wait_for_graph(NOW, {self.A, self.B})
        self.assertIn(self.A, g, "no alias for the name -> the raw relay id keeps today's behavior")

    def test_a_delegate_transfers_ownership_and_sets_no_edge(self):
        # the user 2026-08-15 (reversing 2026-07-25): a handoff whose body said "no reply needed" still
        # parked its sender as awaiting-peer, and a sender with many outstanding handoffs read as
        # permanently stalled. Ownership transferred is not a dependency — only a QUESTION edges; real
        # handoff visibility rides the courier goal graph with the peer's completion as the exact end.
        self._log(self._msg(self.A, self.B, NOW - 300, "delegate"))
        self.assertEqual(km._wait_for_graph(NOW, {self.A, self.B}), {},
                         "the delegator is free the moment the handoff sends")

    def test_coordinate_makes_no_edge_and_question_keeps_its_kind(self):
        self._log(self._msg(self.A, self.B, NOW - 300, "coordinate"))
        self.assertEqual(km._wait_for_graph(NOW, {self.A, self.B}), {})
        self._log(self._msg(self.A, self.B, NOW - 200, "question"))
        self.assertEqual(km._wait_for_graph(NOW, {self.A, self.B})[self.A]["kind"], "question")

    def test_peer_answered_at_tracks_only_answered_pairs(self):
        self.assertEqual(km._peer_answered_at(self.A), 0, "no traffic → nothing answered")
        self._log(self._msg(self.A, self.B, NOW - 300, "question"))
        self.assertEqual(km._peer_answered_at(self.A), 0, "outstanding ask → not answered")
        self._log(self._msg(self.B, self.A, NOW - 200, "coordinate"))
        self.assertEqual(km._peer_answered_at(self.A), NOW - 200, "the reply time, once it lands")
        # a NEWER outstanding ask reopens the pair — the old answer no longer counts
        self._log(self._msg(self.A, self.B, NOW - 100, "question"))
        self.assertEqual(km._peer_answered_at(self.A), 0)

    def test_goal_awaiting_stamp_superseded_by_a_later_answer(self):
        g = "%s:g1" % self.A
        nodes = {g: {"id": g, "text": "top", "parentId": None, "t": NOW - 900,
                     "awaitingWhy": "handed to a peer", "awaitingAt": NOW - 500}}
        self.assertEqual(km._goal_awaiting_stamp(nodes, g), "handed to a peer")
        self.assertIsNone(km._goal_awaiting_stamp(nodes, g, answered_at=NOW - 100),
                          "an answer AFTER the stamp supersedes it")
        self.assertEqual(km._goal_awaiting_stamp(nodes, g, answered_at=NOW - 800), "handed to a peer",
                         "an answer the closer already saw (before the stamp) does not")
        nodes[g].pop("awaitingAt")
        self.assertEqual(km._goal_awaiting_stamp(nodes, g, answered_at=NOW - 100), "handed to a peer",
                         "a stamp with no awaitingAt can't be ordered against the reply — kept")

    def test_session_stamp_read_lifts_when_the_delegated_peer_replies(self):
        g = "%s:g1" % self.A
        (jd.GOALDIR / (self.A + ".json")).write_text(json.dumps({
            "rompUuid": self.A, "seq": 1, "lastNode": g,
            "nodes": {g: {"id": g, "text": "top", "parentId": None, "t": NOW - 900,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                          "awaitingWhy": "sent to a peer to build the flag parser",
                          "awaitingAt": NOW - 500}},
            "placements": {}, "status": {g: "working"}}))
        self._log(self._msg(self.A, self.B, NOW - 600, "question"))
        full, tops, _deleg = km._session_stamp_read(self.A)   # 3rd slot = delegated-peer sids (2026-08-08)
        self.assertEqual(full[2], "sent to a peer to build the flag parser")
        self.assertEqual(tops, frozenset({g}))
        # the peer's reply lands (also busts the postal-key on the stamp cache) → the stamp view lifts
        self._log(self._msg(self.B, self.A, NOW - 100, "coordinate", body="built and merged"))
        full, tops, _deleg = km._session_stamp_read(self.A)
        self.assertEqual(full, (None, None, None, None, ()), "the answered handoff supersedes the older stamp")
        self.assertEqual(tops, frozenset())


class ChatDivergenceTripwire(unittest.TestCase):
    """_note_chat_divergence: the debug-mode tripwire for the chat-says-working-while-settled bug class
    (the user 2026-07-25: the stale-"running" chat could not be diagnosed after a kernel restart cleared
    the live atoms — next time, the log has the atoms). Event-on-change: one row entering divergence
    (with the backend's live-atom summary), one row on clearing, nothing on repeats or when debug is off."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))
        self.saved = (jd._debug_mode, km._sdk)
        jd._debug_mode = lambda: True
        km._sdk = lambda: types.SimpleNamespace(live_atom_kinds=lambda sid: [
            {"uuid": "u1", "type": "assistant", "echo": False, "command": False,
             "apiError": False, "hasText": True}])
        km._CHAT_DIV_LAST.clear()

    def tearDown(self):
        jd._debug_mode, km._sdk = self.saved
        jd._rebind_state(self.saved_state)
        km._CHAT_DIV_LAST.clear()
        self.td.cleanup()

    def _rows(self):
        p = jd.STATE / "chat-divergence.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_logs_on_entering_and_clearing_with_live_atoms(self):
        km._note_chat_divergence(SID, "web", "working", "waiting", NOW)
        km._note_chat_divergence(SID, "web", "working", "waiting", NOW + 3)   # same state → no new row
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["sid"], rows[0]["chat"], rows[0]["row"]), (SID, "working", "waiting"))
        self.assertEqual(rows[0]["live"][0]["uuid"], "u1", "the atoms holding the turn open are captured")
        km._note_chat_divergence(SID, "web", "ready", "waiting", NOW + 6)     # divergence over → cleared row
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1].get("cleared"))

    def test_agreement_and_debug_off_write_nothing(self):
        km._note_chat_divergence(SID, "web", "ready", "waiting", NOW)         # settled agreement: no row
        self.assertEqual(self._rows(), [])
        jd._debug_mode = lambda: False
        km._note_chat_divergence(SID, "web", "working", "waiting", NOW)       # debug off: silent
        self.assertEqual(self._rows(), [])
