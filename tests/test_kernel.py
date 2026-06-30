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
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
em = SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"            # known token for the serve-security test
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


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
        pdir = proj / jd.re.sub(r"[/.]", "-", os.path.realpath(str(cdir)))
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
        self.assertTrue(any(b["text"] == "Fixed the feed flicker" for b in m["ledger"]["bullets"]))

    def test_ledger_bullets_are_newest_first(self):
        # A second, LATER captioned turn → the ledger must list newest-first: render shows bullets[0] at
        # the TOP and reads it as "the newest" for the summary hue. Regression for the oldest-on-top bug.
        # u2's parentUuid chains to the prior turn's last assistant (a2) so the leaf (a3) traces back
        # through BOTH turns — that's how a real transcript tree links successive prompts.
        with self.tpath.open("a") as f:
            f.write(json.dumps(uline(T0 + 100, "now fix the sort order", "u2", parent="a2", ps="typed")) + "\n")
            f.write(json.dumps(aline(T0 + 120, "Sorted it.", "a3", "u2", stop="end_turn")) + "\n")
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        turns = session["turns"]
        self.assertGreaterEqual(len(turns), 2, "fixture should now span two turns")
        caps = [{"id": turns[0]["id"], "grain": "turn", "t": turns[0]["t"], "caption": "Fixed the feed flicker"},
                {"id": turns[1]["id"], "grain": "turn", "t": turns[1]["t"], "caption": "Sorted the ledger"}]
        (jd.CAPDIR / (SID + ".jsonl")).write_text("\n".join(json.dumps(c) for c in caps) + "\n")
        bullets = km.build_session(SID, NOW)["ledger"]["bullets"]
        ts = [b["t"] for b in bullets]
        self.assertEqual(ts, sorted(ts, reverse=True), "ledger bullets must be newest-first")
        self.assertEqual(bullets[0]["text"], "Sorted the ledger", "newest caption sits on top")

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
        self.assertIsNotNone(cur, "an open (unfinished) turn → a working-on line")
        self.assertEqual(cur["text"], "wire the ledger overview strip")

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
        # working (the user 2026-06-22, "bugz is working but it says ready").
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
        # (where it began) — the chat-view click-to-jump nav lands done/blocked goals on mt, open on t
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

    def test_ledger_tree_derives_done_when_all_children_complete(self):
        # Completion propagates UP (the user 2026-06-16): a parent that ISN'T explicitly nodeComplete but
        # whose children are ALL done is "derived done" — done=True + derived=True (render dims its ✓
        # disc). The full tree is emitted (the render folds, the kernel doesn't prune). A parent with an
        # open child is NOT derived; a blocked parent is never auto-completed.
        dp, dc1, dc2 = (SID + ":dp", SID + ":dc1", SID + ":dc2")   # derived parent, both children done
        mp, mc1, mc2 = (SID + ":mp", SID + ":mc1", SID + ":mc2")   # mixed parent, one child still open
        bp, bc = (SID + ":bp", SID + ":bc")                        # blocked parent, child done
        def gn(nid, text, parent, done, blocked=False):
            return {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                    "blocked": blocked, "cleared": False, "trail": [], "t": T0}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 9, "lastNode": None,
            "nodes": {dp: gn(dp, "derived parent", None, False), dc1: gn(dc1, "dc one", dp, True), dc2: gn(dc2, "dc two", dp, True),
                      mp: gn(mp, "mixed parent", None, False), mc1: gn(mc1, "mc done", mp, True), mc2: gn(mc2, "mc open", mp, False),
                      bp: gn(bp, "blocked parent", None, False, blocked=True), bc: gn(bc, "bc done", bp, True)},
            "placements": {}, "status": {}}))
        tree = km.build_session(SID, NOW)["ledger"]["tree"]
        byid = {n["text"]: n for n in tree}
        # derived parent: done by virtue of its children, flagged derived; its children are still emitted
        self.assertTrue(byid["derived parent"]["done"])
        self.assertTrue(byid["derived parent"]["derived"], "all children done → derived done")
        self.assertIn("dc one", byid, "the full tree is emitted (the render folds, the kernel doesn't prune)")
        self.assertFalse(byid["dc one"]["derived"], "an explicitly-done child stays a full disc")
        # mixed parent: one child still open → not done, not derived, children shown
        self.assertFalse(byid["mixed parent"]["done"])
        self.assertFalse(byid["mixed parent"]["derived"])
        self.assertIn("mc open", byid, "an open child keeps its parent expanded")
        # blocked parent: never derived-done, even with all children done
        self.assertFalse(byid["blocked parent"]["derived"], "a blocked node is not auto-completed")
        self.assertFalse(byid["blocked parent"]["done"])

    def test_feed_tree_propagates_completion_both_ways(self):
        # In the FEED card tree completion rolls UP and DOWN (the user 2026-06-16): a done parent checks
        # off its children (roll-down), and all-children-done makes the parent done (roll-up). Explicit
        # done → derived False (full ✓ disc); a derived case → derived True (dimmed disc). Unlike the
        # ledger, the feed does NOT prune, so the rolled-off children stay VISIBLE (dimmed).
        ta, ca = (SID + ":ta", SID + ":ca")                        # done parent, open child (roll-DOWN)
        tb, cb1, cb2 = (SID + ":tb", SID + ":cb1", SID + ":cb2")    # open parent, both children done (roll-UP)
        def gn(nid, text, parent, done):
            return {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                    "blocked": False, "cleared": False, "trail": [], "t": T0}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 3, "lastNode": "other",
            "nodes": {ta: gn(ta, "done top", None, True), ca: gn(ca, "open child", ta, False),
                      tb: gn(tb, "rollup top", None, False), cb1: gn(cb1, "kid one", tb, True), cb2: gn(cb2, "kid two", tb, True)},
            "placements": {}, "status": {}}))
        nodes = {n["text"]: n for a in km.build_feed(NOW)["asks"] for n in a["tree"]}
        # roll-DOWN: a done parent checks off its child → the child is done + derived (dimmed), still shown
        self.assertEqual(nodes["open child"]["status"], "done")
        self.assertTrue(nodes["open child"]["derived"], "a done ancestor rolls down → derived done")
        self.assertFalse(nodes["done top"]["derived"], "the explicitly-done parent is a full disc")
        # roll-UP: all children done → parent derived-done; the explicit children stay explicit (full disc)
        self.assertEqual(nodes["rollup top"]["status"], "done")
        self.assertTrue(nodes["rollup top"]["derived"], "all children done → derived done")
        self.assertFalse(nodes["kid one"]["derived"])

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
        self.assertEqual(card["blockWhy"], "waiting on the user's choice",
                         "the card surfaces the latest still-blocked node's blockWhy")
        nodes = {n["text"]: n for n in card["tree"]}
        self.assertEqual(nodes["the goal"]["why"], "user asked for the goal")
        self.assertEqual(nodes["a blocked step"]["blockWhy"], "waiting on the user's choice")
        self.assertEqual(nodes["a finished step"]["doneWhy"], "shipped the fix")

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
        # nudge echoes as "romp" (gray), a typed follow-up as "human" (blue).
        import inspect
        src = inspect.getsource(km._drive)
        self.assertIn('_optimistic_echo(sid, body, author="romp" if msg.get("nudge") else "human")', src)

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
        km._session_awaiting = lambda sid, path, idle: "Waiting on the 3 research agents it dispatched."
        try:
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        finally:
            km._session_awaiting = saved
        self.assertEqual(card["column"], "working", "an awaiting goal is held in the working column, NOT needs-input")
        self.assertIsNotNone(card["awaiting"], "it carries an awaiting badge")
        self.assertEqual(card["awaiting"]["why"], "Waiting on the 3 research agents it dispatched.")
        self.assertIsNone(card["blocked"], "an awaiting goal is not a live block")

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
                         "Waiting on 2 background jobs it launched.", "the genuine awaiting badge still shows")

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
        km._session_awaiting = lambda sid, path, idle: None      # isolate the POSTAL path
        try:
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == top)
        finally:
            km._wait_for_graph, km._session_awaiting = saved_w, saved_a
        self.assertEqual(card["column"], "working", "the stale block yields to the live peer-wait → working")
        self.assertIsNotNone(card["awaiting"], "shown as awaiting (a working flavor)")
        self.assertIsNotNone(card["waitingOn"], "the 'Awaiting <peer>' chip is restored (no longer suppressed by the block)")
        self.assertEqual(card["waitingOn"]["peerSid"], "peerY")

    def test_inflight_bg_tool_detects_an_unresolved_background_launch(self):
        # the transcript stopgap (the user 2026-06-22): a run_in_background tool with no tool_result is in
        # flight; its result resolves it; a genuine new prompt means the session moved on.
        def recs(resolved=False, later_prompt=False):
            out = [{"type": "user", "uuid": "u1", "timestamp": iso(T0),
                    "message": {"role": "user", "content": [{"type": "text", "text": "kick off a long job"}]}},
                   {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": iso(T0 + 5),
                    "message": {"role": "assistant", "stop_reason": "end_turn", "content": [
                        {"type": "tool_use", "id": "tu_bg", "name": "Bash",
                         "input": {"command": "sleep 999", "run_in_background": True}}]}}]
            if resolved:
                out.append({"type": "user", "uuid": "r1", "parentUuid": "a1", "timestamp": iso(T0 + 9),
                            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_bg", "content": "ok"}]}})
            if later_prompt:
                out.append({"type": "user", "uuid": "u2", "parentUuid": "a1", "timestamp": iso(T0 + 50),
                            "message": {"role": "user", "content": [{"type": "text", "text": "never mind, do this instead"}]}})
            return out
        p = Path(self.td.name) / "bg.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in recs()) + "\n"); km._bgtool_cache.clear()
        self.assertIsNotNone(km._inflight_bg_tool(str(p)), "an unresolved run_in_background launch → in flight")
        p.write_text("\n".join(json.dumps(r) for r in recs(resolved=True)) + "\n"); km._bgtool_cache.clear()
        self.assertIsNone(km._inflight_bg_tool(str(p)), "its tool_result resolves it → not awaiting")
        p.write_text("\n".join(json.dumps(r) for r in recs(later_prompt=True)) + "\n"); km._bgtool_cache.clear()
        self.assertIsNone(km._inflight_bg_tool(str(p)), "a genuine new prompt → the session moved on, not awaiting")

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
        self.assertEqual(km._session_awaiting(SID, "/nonexistent", True), "3 agents in flight",
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
        self.assertEqual(card["doneWhy"], "shipped the fix",
                         "the completed card surfaces the most-recently-completed node's doneWhy")

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
        self.assertEqual(card["created"], T0, "created still records the true mint time")

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
        self.assertEqual(card["doneWhy"], "shipped the fix", "card subline stays the closer's doneWhy")
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
        self.assertEqual(card["blockWhy"], "which store?", "blockWhy stays emitted (becomes the tooltip)")
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

    def _goal_store(self, nodes, status, last=None, closed=None):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": len(nodes), "lastNode": last, "closedTurns": closed or [],
            "nodes": nodes, "placements": {}, "status": status}))

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
            {g1: "completed"}, last=g1)
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

    def test_provisional_card_shows_the_message_caption_once_it_lands(self):
        # The user 2026-06-19: the card reads the captioner's persisted MESSAGE caption ('<segid>#p') — the
        # SAME gist the timeline dot uses, no separate 'gist' judge call. Until it lands, the raw prompt;
        # once the captioner writes it, "Analyzing: <caption>".
        self._open_turn_transcript(ended=False)
        g1 = SID + ":g1"
        self._goal_store({g1: {"id": g1, "text": "first ask", "parentId": None, "nodeComplete": True,
                               "blocked": False, "cleared": False, "trail": [], "t": T0}},
                         {g1: "completed"}, last=g1)
        self._working_tmux()
        first = next(a for a in km.build_feed(NOW)["asks"] if a.get("provisional"))
        self.assertIn("empty space", first["text"], "no message caption yet → the raw prompt")
        self.assertNotIn("Analyzing", first["text"], "no stuck 'Analyzing…' placeholder — just the raw prompt")
        self._write_msg_caption("trimming the empty space below the cards")
        p = next(a for a in km.build_feed(NOW)["asks"] if a.get("provisional"))
        self.assertEqual(p["text"], "Analyzing: trimming the empty space below the cards")

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
        km._chat_tab_sessions = lambda now, tmux: [{"sid": "A"}, {"sid": "B"}, {"sid": "C"}]
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
        km._chat_tab_sessions = lambda now, tmux: [{"sid": "A"}, {"sid": "B"}, {"sid": "C"}]
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

    def _orphaned_goal(self, idle=True, closer_done=True):
        # an idle (or still-open) session whose top goal still shows "working". closer_done puts the latest
        # turn's id in closedTurns, so the closer-verdict gate lets the nudge through (the realistic case: the
        # closer ran and left the goal working). closer_done=False = the closer hasn't classified it yet.
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
                         last=g, closed=closed)
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

    def test_auto_nudge_fires_for_every_working_top(self):
        # all of a session's WORKING top goals get nudged each stop — not just the first (the user 2026-06-28).
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
            self.assertEqual(len(sent), 2, "BOTH working tops nudged in one stop, not just the first")
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

    def test_auto_nudge_re_arms_on_a_nudge_response_that_stays_working(self):
        # the user 2026-06-25: keep nudging UNTIL blocked/completed — so the agent's OWN nudge-response turn,
        # if it ends still-working, re-arms too (it's a new turn id). This replaces the prior "don't re-arm on
        # a nudge-response" rule; the two-per-turn cap (tested above) is what now bounds the loop.
        base = [uline(T0, "a1", "u1", ps="typed"), aline(T0 + 10, "d1", "a1", "u1", stop="end_turn")]
        g = self._stall_transcript(base)
        km._set_auto_nudge(True)
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 1, "the genuine stall is nudged")
            nudge = "Status?\n\n<!-- romp-injected --><!-- romp-goal-id: %s -->" % g   # romp-authored turn
            self._stall_transcript(base + [uline(T0 + 100, nudge, "u2", "a1", ps="typed"),
                                           aline(T0 + 110, "still working", "a2", "u2", stop="end_turn")])
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(len(sent), 2, "a nudge-response that stays working re-arms — keep nudging til resolved")
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
        km._session_awaiting = lambda sid, path, idle: "Waiting on its background agents."
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())
            self.assertEqual(sent, [], "an awaiting session is held, not nudged")
            km._session_awaiting = lambda sid, path, idle: None   # no longer awaiting → the genuine stall is nudged
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
        sent, restore = self._stub_nudge()
        try:
            km._auto_nudge_tick(NOW, km._tmux_sessions())          # never enabled
            self.assertEqual(sent, [], "off by default → no nudges")
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
        self.assertFalse(km._auto_nudge_on(), "off by default")
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
            {g1: "completed"}, last=g1)
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
        # the highlight (current) and the → arrow (recent) are the SAME node — the cursor (told), NOT the max-mt leaf
        self.assertTrue(byid["older top"]["current"] and byid["older top"]["recent"], "cursor carries BOTH the highlight and the arrow")
        self.assertFalse(byid["freshly done leaf"].get("recent"), "the arrow no longer follows the freshest-mt node")
        self.assertEqual(next(n for n in tree if n.get("recent"))["text"], "older top", "exactly one arrow, on the cursor")
        # onpath follows the cursor; an off-cursor branch is off-path (render folds it) but still EMITTED
        self.assertTrue(byid["older top"]["onpath"])
        self.assertFalse(byid["freshly done leaf"]["onpath"], "the freshest leaf is off the cursor's expand path now")
        self.assertIn("pruned child", byid, "the full tree is still emitted")

    def test_ledger_tree_shows_cleared_nodes_faded(self):
        # A cleared (dismissed) node is no longer hidden — it's emitted, counted DONE, and flagged `cleared`
        # so the render shows it as a FADED ✓ (the user 2026-06-16).
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
        self.assertTrue(byid["dismissed step"]["done"], "cleared counts as done (faded ✓)")
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
        # A CLEARED (dismissed) top counts as done for roll-down too (the user 2026-06-16): its open
        # children fade with it (derived ✓) instead of sitting as ○ under a faded-✓ parent.
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
        self.assertTrue(byid["dismissed top"]["cleared"] and byid["dismissed top"]["done"])
        self.assertTrue(byid["open child"]["done"], "a child under a CLEARED top is done (roll-down through clear)")
        self.assertTrue(byid["open child"]["derived"], "and shown as a derived (faded) ✓, not ○")

    def test_followup_body_quotes_context(self):
        # A feed follow-up quotes the ask it answers ('> <ask>') so the recipient session has context;
        # an explicit group title wins over the node lookup; unknown/none → bare text (the user 2026-06-16).
        # Every follow-up ALSO ends with the hidden goal marker (see the dedicated test below); fold it in.
        iid = SID + ":g2"                                   # fixture g2 = "Awaiting a decision", blocked, a top
        # default (the user TYPED this follow-up) ends with the goal-id only (→ reopen); NO romp-injected,
        # because it's the user's words → blue bubble. The romp-injected split is the dedicated test below.
        def mk(s, i=iid): return s + "\n\n<!-- romp-goal-id: " + i + " -->"
        # no title → node path: the node text + its status (g2 is blocked; it's a top so no "under")
        self.assertEqual(km._followup_body(iid, None, "go with option A"),
                         mk("> Awaiting a decision (blocked)\n\ngo with option A"))
        # explicit title (group modal) → verbatim, no node enrichment
        self.assertEqual(km._followup_body(iid, "Pick a database", "postgres"),
                         mk("> Pick a database\n\npostgres"))
        self.assertEqual(km._followup_body(SID + ":nope", None, "hi"),
                         mk("hi", SID + ":nope"), "no context → no empty quote (marker still appended)")

    def test_followup_body_enriches_with_path_status_and_why(self):
        # the user 2026-06-17: the follow-up must carry more than a one-line title so the recipient session
        # understands WHAT it's following up on — the node's place (top goal), status, and the planner's why.
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
        self.assertIn("Unfinished pieces under this goal", out)
        self.assertIn("• Migrate the session store to Redis", out, "an open own-work leaf is listed")
        self.assertIn("• Add CSRF tokens (blocked) — Need you to pick the token TTL.", out,
                      "a blocked leaf carries its tag + the planner's why")
        self.assertNotIn("Update the login tests", out, "a DONE leaf is not an unfinished piece")
        self.assertNotIn("Peer is porting the client", out, "a DELEGATED leaf is peer work, not this session's")
        self.assertIn("report per piece", out, "the body asks for a status on each piece, not the whole goal")
        self.assertNotIn(km.AUTO_NUDGE_TEXT, out, "the single-line 'status on the goal above' body is replaced")
        # still a proper nudge: gray-bubble marker + the reopen goal-id, targeting the TOP goal
        self.assertTrue(out.endswith("<!-- romp-injected --><!-- romp-goal-id: " + top + " -->"))

    def test_auto_nudge_gets_the_same_hierarchical_enumeration(self):
        # the auto-nudge (injected + auto) refines IDENTICALLY to the manual button (the user 2026-06-24).
        top = self._hier_goal_store()
        auto = km._followup_body(top, None, km.AUTO_NUDGE_TEXT, injected=True, auto=True)
        self.assertIn("Unfinished pieces under this goal", auto)
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
        self.assertNotIn("Unfinished pieces under this goal", out, "nothing to enumerate on a flat goal")
        self.assertIn(km.AUTO_NUDGE_TEXT, out, "the single-line nudge body is preserved verbatim")

    def test_typed_followup_on_a_hierarchical_top_is_not_enumerated(self):
        # the enumeration is a NUDGE refinement (injected) only — a follow-up the USER TYPES on the top card
        # keeps the existing single-node quote, so we don't expand their reply into a per-sub status request.
        top = self._hier_goal_store()
        out = km._followup_body(top, None, "use Redis, TTL 1h")   # injected defaults False (the user typed it)
        self.assertNotIn("Unfinished pieces under this goal", out)
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

    def test_card_carries_summary_anchor_for_the_summary_deep_link(self):
        # the distilled summary LINE deep-links to the biggest contiguous assistant-text block in the goal's
        # work span (the user 2026-06-22): the card's summaryAnchorUuid = _seg_best_text over the node's trail.
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        seg = em.segments(session["turns"][0])[0]
        expect, n = km._seg_best_text(seg["atoms"])
        self.assertTrue(expect and n > 0, "the fixture segment has assistant prose to anchor on")
        nid = SID + ":g42"
        store = {"rompUuid": SID, "seq": 42, "nodes": {
            nid: {"id": nid, "text": "Ship it", "parentId": None, "nodeComplete": True,
                  "blocked": False, "trail": [seg["id"]], "t": NOW}}, "placements": {}, "status": {}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        card = {a["itemId"]: a for a in km.build_feed(NOW)["asks"]}[nid]
        self.assertEqual(card["summaryAnchorUuid"], expect,
                         "the card links its summary to the segment's biggest assistant-text block")

    def test_followup_body_appends_goal_marker(self):
        # The follow-up judge reopens the tagged goal: every follow-up ends with a hidden
        # `<!-- romp-goal-id: <itemId> -->` marker (itemId = the card's top-goal node id), matched by the
        # judge's `romp-goal-id:\s*([^\s>]+)` (coordinated w/ the `judges` session, 2026-06-17). The
        # romp-injected author marker (→ gray bubble) rides ONLY a nudge (injected=True), NOT a follow-up
        # the user types — that's the user's words → blue bubble (the user 2026-06-20).
        iid = SID + ":g2"
        typed = km._followup_body(iid, "ctx", "do the thing")                  # default: the user typed it
        self.assertTrue(typed.endswith("\n\n<!-- romp-goal-id: " + iid + " -->"),
                        "a typed follow-up ends with the goal-id alone — no romp-injected (blue bubble)")
        self.assertNotIn("<!-- romp-injected -->", typed)
        self.assertEqual(re.search(r"romp-goal-id:\s*([^\s>]+)", typed).group(1), iid)   # the judge's parser
        nudge = km._followup_body(iid, "ctx", "do the thing", injected=True)   # romp's OWN nudge (the BUTTON)
        self.assertTrue(nudge.endswith("\n\n<!-- romp-injected --><!-- romp-goal-id: " + iid + " -->"),
                        "a nudge adds romp-injected (gray bubble) ahead of the goal-id")
        self.assertNotIn("<!-- romp-auto -->", nudge, "a Nudge BUTTON click is NOT auto → no romp-auto marker")
        # an AUTO-nudge (the kernel's background _auto_nudge_tick) ALSO carries romp-auto → the romp-logo marker
        auto = km._followup_body(iid, "ctx", "status?", injected=True, auto=True)
        self.assertTrue(auto.endswith("\n\n<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: " + iid + " -->"),
                        "an auto-nudge carries BOTH romp-injected and romp-auto, then the goal-id")

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
        # the user 2026-06-17 REVERSED "keep a tab when it dies": a session shown alive then dead is now
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

    def test_dead_kept_tab_excluded_once_hidden(self):
        # ×-closing a dead-kept tab dismisses it for good.
        saved_seen, saved_has = set(km._seen_live), km._has_tmux
        km._has_tmux = lambda: True
        try:
            km._seen_live.clear(); km._seen_live.add(SID)
            km._set_hidden_tab(SID, True)
            tabs = [s["sid"] for s in km._chat_tab_sessions(NOW, {})]
            self.assertNotIn(SID, tabs, "a ×-hidden dead tab is not shown")
        finally:
            km._seen_live.clear(); km._seen_live.update(saved_seen); km._has_tmux = saved_has

    def test_rel_ago_buckets(self):
        self.assertEqual(km._rel_ago(1000, 1000), "just now")
        self.assertEqual(km._rel_ago(1000, 1000 - 120), "2m ago")
        self.assertEqual(km._rel_ago(1000 + 7200, 1000), "2h ago")
        self.assertEqual(km._rel_ago(3 * 86400, 0), "3d ago")

    def test_todo_card_folds_taskcreate_taskupdate(self):
        # TaskCreate/TaskUpdate fold into ONE {kind:"todo"} card (the old TS transcript.foldTasks); the
        # task id comes from TaskCreate's "Task #N" result; the raw Task* tool calls are NOT emitted (the
        # webview hides them via ACK_TOOLS, so the kernel skips them and emits only the folded checklist).
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
        m = km.build_session(SID, NOW)
        todos = [e for e in m["events"] if e["kind"] == "todo"]
        self.assertEqual(len(todos), 1, "exactly one folded todo card")
        tasks = todos[0]["tasks"]
        self.assertEqual([t["id"] for t in tasks], ["1"])
        self.assertEqual(tasks[0]["subject"], "Wire the picker")
        self.assertEqual(tasks[0]["activeForm"], "Wiring the picker")
        self.assertEqual(tasks[0]["status"], "in_progress", "TaskUpdate moved it to in_progress")
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
        self.assertNotIn("id=rs-explanations", km._GEAR_HTML)
        self.assertNotIn("id=rs-subgoals", km._GEAR_HTML)
        self.assertNotIn("explanations", km._GEAR_JS)                               # every trace of the pref is gone
        self.assertIn("dispatchEvent(new Event('romp:settings'))", km._GEAR_JS)     # same-doc re-render signal (compact toggle etc.)
        # the ↻ refresh + ⛭ gear BUTTONS moved to the shell's far-left rail (the user 2026-06-25); only the
        # settings MODAL stays in the feed, opened by the rail gear via a {romp:'openSettings'} postMessage.
        self.assertNotIn("id=rrefresh", km._GEAR_HTML)                              # refresh is on the rail now
        self.assertIn("e.data.romp==='openSettings'", km._GEAR_JS)                  # the modal opens on the rail's request
        landing = km._landing()
        self.assertIn("id=rail-gear", landing)
        self.assertIn("id=rail-refresh", landing)
        self.assertIn("fetch('/restart',{method:'POST'})", landing)                 # the rail ↻ POSTs /restart

    def test_gear_polish_tooltips_colormap_bar_no_emoji(self):
        # the user 2026-06-23: descriptions become HOVER tooltips (decluttered), and the analytics button drops
        # its 📊 emoji.
        self.assertIn("#rsettings .rs-sub{display:none}", km._GEAR_CSS)               # descriptions hidden by default
        self.assertRegex(km._GEAR_CSS, r"#rsettings \.rs-row:hover \.rs-sub\{display:block;position:absolute")  # float on hover
        self.assertNotIn("\U0001F4CA", km._GEAR_HTML)                                 # the 📊 emoji is gone
        self.assertIn("Token usage analytics", km._GEAR_HTML)                          # the label itself stays

    def test_gear_colormap_dropdown_options_are_bars_not_names(self):
        # the user 2026-06-23: the feed-colormap selector's OPTIONS are the gradient bars themselves — no map
        # NAMES. A custom widget (native <select> can't render gradient options): a button shows the picked
        # map's bar, and the list is one bar per map; clicking a bar selects it + posts setColormap.
        self.assertNotIn("<select id=rs-colormap", km._GEAR_HTML)                      # the native name-list select is gone
        self.assertNotIn(">Hawaii<", km._GEAR_HTML)                                    # no map names listed
        self.assertIn("id=rs-cmap-btn", km._GEAR_HTML)                                 # the button shows the picked bar
        self.assertIn("id=rs-cmap-list", km._GEAR_HTML)                                # the bar list
        self.assertIn(".rs-cmap-opt{", km._GEAR_CSS)                                   # each option is a styled bar
        self.assertIn("function cmGrad(name)", km._GEAR_JS)                            # builds a bar gradient per map
        self.assertIn("linear-gradient(to right,", km._GEAR_JS)
        self.assertIn("type:'setColormap',name:name", km._GEAR_JS)                     # picking a bar persists + posts
        self.assertNotIn("renderCmapBar", km._GEAR_JS)                                 # the old preview-bar fn is gone

    def test_gear_has_show_git_branch_toggle(self):
        # the user 2026-06-23: a "Show git branch" checkbox controls whether the chat bottom-bar shows the
        # session's git branch beside the dir. ON by default (showBranch !== false). It mirrors render.ts'
        # loadSettings().showBranch read, persisted in romp:settings.
        self.assertIn("id=rs-branch", km._GEAR_HTML)
        self.assertIn("Show git branch", km._GEAR_HTML)
        self.assertIn("s.showBranch=gb.checked", km._GEAR_JS)        # change → persist
        self.assertIn("gb.checked=s.showBranch!==false", km._GEAR_JS)  # open → reflect (default ON)
        self.assertIn("showBranch:true", km._GEAR_JS)                # load() default ON, both branches

    def test_chat_body_has_an_explicit_send_button(self):
        # The web-dashboard composer (kernel _chat_body, a SECOND copy of chat-view page-skeleton.chatBody)
        # carries an explicit send button beside 📎, so ⏎ isn't the only way to send (the user 2026-06-17).
        body = km._chat_body()
        self.assertIn('id="composer-send"', body)
        self.assertLess(body.index("composer-attach"), body.index("composer-send"),
                        "send sits to the RIGHT of the 📎 attach button")

    def test_feed_cards_are_top_level_goals_only(self):
        # The feed's cards are top-level GOALS only (read-side.md, the user 2026-06-16). A completed
        # goal → its own COMPLETED card; the blocked goal → a BLOCKED card. Turn captions are NOT cards:
        # despite a "Fixed the feed flicker" caption in the fixture, the stream is empty — emitting
        # captions as standalone DETAILS cards is the bug that flooded the columns.
        d = km.build_feed(NOW)
        self.assertEqual(d["type"], "feed")
        self.assertEqual(d["items"], [], "no caption stream — feed cards are top-level goals only")
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
        self.assertEqual(card["blocked"]["since"], NOW - 30)
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
        self.assertEqual(card["blocked"]["since"], NOW - 30)
        self.assertEqual(card["column"], "needs_input", "a picker-floored card files under BLOCKED directly")
        self.assertIn("input", card["blocked"]["what"], "picker wording reflects a question, not an approval")
        # the session chip (build_session payload) also reads "awaiting" on a picker, like a permission
        self.assertEqual(km.build_session(SID, NOW)["status"]["state"], "awaiting",
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

    def test_feed_recheck_on_plain_reply_after_block(self):
        g = self._blocked_store()
        saved = km._last_plain_user_turn_t
        try:
            km._last_plain_user_turn_t = lambda turns: NOW - 10      # a plain reply AFTER the block (mt NOW-100)
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertTrue(card["recheck"], "plain reply after the block → re-check (de-urgented)")
            self.assertEqual(card["column"], "working", "re-check drops out of needs-input into Working (the user 2026-06-27)")
            km._last_plain_user_turn_t = lambda turns: NOW - 300     # a reply that PRE-dates the block
            card2 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertFalse(card2["recheck"], "no reply since the block → still urgent")
        finally:
            km._last_plain_user_turn_t = saved

    def test_feed_recheck_is_INSTANT_from_a_just_sent_reply_still_in_the_echo(self):
        # The delay fix (the user 2026-06-29): a plain reply de-urgents a blocked card the INSTANT it's sent —
        # while still only an optimistic echo (not yet a transcript turn). build_feed reads the cached parse
        # (no plain reply there), so without counting the echo the card stays Blocked until the atom lands.
        g = self._blocked_store()
        km._tmux_echo.pop(SID, None)
        # the parsed transcript shows NO plain reply (cache-only) → recheck would be False on its own
        card_before = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertFalse(card_before["recheck"], "no reply yet → still urgent (Blocked)")
        self.assertEqual(card_before["column"], "needs_input")
        # now the user sends a plain reply — only an optimistic echo so far
        km._tmux_echo_add(SID, "go ahead, do option B", author="human")
        try:
            card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertTrue(card["recheck"], "the just-sent echo de-urgents the card AT ONCE")
            self.assertEqual(card["column"], "working", "→ moves out of Blocked into Working immediately")
            # a TARGETED reply (romp-goal-id) is NOT a plain reply → does NOT sweep via this path
            km._tmux_echo.pop(SID, None)
            km._tmux_echo_add(SID, "answer <!-- romp-goal-id: x:g1 -->", author="human")
            card3 = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
            self.assertFalse(card3["recheck"], "a targeted card-reply doesn't sweep the session's blocks")
        finally:
            km._tmux_echo.pop(SID, None)

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
        self.assertEqual(card["origin"], {"peer": "sendersess", "peerSid": sender,
                                          "color": {"bg": "#ff8800", "fg": "#ffffff"}},
                         "origin.peer (a sid) resolves to the sender's name + color")

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

    def test_feed_handoff_origin_hidden_when_fully_absorbed(self):
        """Once the sender's linked goal is done/cleared/gone (or there was no link), the handoff is fully
        absorbed → origin=None, so the badge hides and the card reads as the recipient's native goal."""
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
        self.assertIsNone(card["origin"], "sender's linked goal is done → fully absorbed → no badge")
        write_origin(None, "m-y.3")                              # no link at all
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertIsNone(card["origin"], "no link (goalId null) → absorbed → no badge")
        write_origin(sender + ":gGONE", "m-y.4")                 # link to a goal that no longer exists
        card = next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == g)
        self.assertIsNone(card["origin"], "link to a missing goal → absorbed → no badge")

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
        ids = [a["itemId"] for a in d0["asks"]] + [c["itemId"] for c in d0["items"]]
        self.assertTrue(ids, "fixture has cards to clear")
        km._clear_all(ids)
        d1 = km.build_feed(NOW)
        self.assertEqual(len(d1["asks"]) + len(d1["items"]), 0, "clear-all empties the feed")
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

    def test_alive_stale_transcript_still_shown(self):
        # a live tmux session whose transcript is outside discover()'s 48h window must still
        # appear — _alive_sessions synthesizes an entry from the names registry
        stale_sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        (km.NAMES / stale_sid).write_text("stale-sess\t/tmp\t#1EA1EB\twhite")
        tmux = {SID: {"state": "idle"}, stale_sid: {"state": "idle"}}
        alive = km._alive_sessions(NOW, tmux)
        sids = [s["sid"] for s in alive]
        self.assertIn(SID, sids, "in-window session present")
        self.assertIn(stale_sid, sids, "stale-transcript live session present")

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

    def test_close_session_hides_tab(self):
        # the × hides the tab (reversible), does not kill the session
        self.assertEqual(km._hidden_tabs(), set())
        km._set_hidden_tab(SID, True)
        self.assertIn(SID, km._hidden_tabs())
        km._set_hidden_tab(SID, False)
        self.assertNotIn(SID, km._hidden_tabs())

    def test_open_dead_session_prompts_revive(self):
        # opening a DEAD session pops the chat's confirmRevive modal — no silent reopen — and now from
        # ANY pane (feed/timeline included), since dead = timeline-only (the user 2026-06-17). A LIVE
        # session just reopens/focuses, no prompt.
        cap, orig_rc, orig_tx, orig_pa = [], km._reveal_chat, km._tmux_sessions, km._push_all
        try:
            km._reveal_chat = lambda m: cap.append(m)
            km._push_all = lambda: None
            km._tmux_sessions = lambda: {SID: {}}            # SID alive; deadsid000 dead
            cap.clear(); km._open_or_revive("deadsid000")
            self.assertEqual([m["type"] for m in cap], ["confirmRevive"])
            self.assertEqual(cap[0]["id"], "deadsid000")
            cap.clear(); km._open_or_revive(SID)
            self.assertFalse(any(m.get("type") == "confirmRevive" for m in cap), "a live session reopens, no prompt")
            self.assertTrue(any(m.get("type") == "focus" and m.get("id") == SID for m in cap))
        finally:
            km._reveal_chat = orig_rc; km._tmux_sessions = orig_tx; km._push_all = orig_pa

    def test_revive_session_resumes_and_unhides_tab(self):
        # confirming the modal's "Revive" must actually resume the session (romp-postal-service revive → romp
        # --resume --detach) AND un-hide its tab. Regression: the kernel had no reviveSession handler,
        # so the modal's Revive did nothing — "it didn't revive it" (the user 2026-06-16).
        km._set_hidden_tab("deadsid000", True)            # it was hidden when closed
        calls, saved = [], km.subprocess.run
        km.subprocess.run = lambda *a, **k: calls.append(list(a[0]))
        try:
            km._revive_session("deadsid000")
        finally:
            km.subprocess.run = saved
        self.assertTrue(calls, "revive must shell out to the resume path")
        argv = calls[0]
        self.assertTrue(str(argv[0]).endswith("romp-postal-service"))
        self.assertEqual(argv[-2:], ["revive", "deadsid000"])
        self.assertNotIn("deadsid000", km._hidden_tabs(), "the revived tab is un-hidden")

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
        self.assertIn("id=rs-cmap-btn", km._GEAR_HTML)
        self.assertIn("setColormap", km._GEAR_JS)

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
        # timeline message connectors from the postal log: a sent row joined to its exec by id, with
        # BOTH ends alive lanes; a message to a non-alive session is dropped (no endpoint)
        md = jd.STATE / "timeline"; md.mkdir(parents=True, exist_ok=True)
        a, b = "aaaa1111", "bbbb2222"
        (md / "messages.jsonl").write_text(
            json.dumps({"ev": "sent", "id": "m1", "from_id": a, "to_id": b, "t": NOW - 30,
                        "from": "alpha", "body": "do X"}) + "\n"
            + json.dumps({"ev": "exec", "id": "m1", "t": NOW - 20}) + "\n"
            + json.dumps({"ev": "sent", "id": "m2", "from_id": a, "to_id": "deadsid", "t": NOW - 30,
                          "from": "alpha", "body": "y"}) + "\n")
        msgs = km._postal_messages(NOW, {a, b}, {a: "alpha", b: "beta"})
        self.assertEqual(len(msgs), 1, "only the connector with BOTH ends alive")
        m = msgs[0]
        self.assertEqual((m["fromId"], m["toId"]), (a, b))
        self.assertEqual(m["exec"], NOW - 20); self.assertTrue(m["hasExec"]); self.assertFalse(m["pending"])
        self.assertEqual(m["text"], "do X")

    def test_postal_connector_binds_to_planted_goal(self):
        # a courier connector carries toGoal = the goal it planted in the recipient (origin.msgId match)
        md = jd.STATE / "timeline"; md.mkdir(parents=True, exist_ok=True)
        a, b = "aaaa1111", "bbbb2222"
        (md / "messages.jsonl").write_text(
            json.dumps({"ev": "sent", "id": "m1", "from_id": a, "to_id": b, "t": NOW - 30, "from": "alpha", "body": "do X"}) + "\n"
            + json.dumps({"ev": "exec", "id": "m1", "t": NOW - 20}) + "\n"
            + json.dumps({"ev": "sent", "id": "m9", "from_id": a, "to_id": b, "t": NOW - 25, "from": "alpha", "body": "fyi"}) + "\n")
        gb = "%s:g1" % b
        (jd.GOALDIR / (b + ".json")).write_text(json.dumps({
            "rompUuid": b, "seq": 1, "nodes": {gb: {"id": gb, "text": "Handed-off work", "parentId": None,
                "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": NOW - 20,
                "origin": {"peer": a, "goalId": a + ":g1", "msgId": "m1"}}},
            "placements": {}, "status": {gb: "working"}}))
        msgs = {m["id"]: m for m in km._postal_messages(NOW, {a, b}, {a: "alpha", b: "beta"})}
        self.assertEqual(msgs["m1"]["toGoal"], gb, "the connector binds to the goal it planted")
        self.assertIsNone(msgs["m9"]["toGoal"], "a message that planted no goal has toGoal None")

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
        self.assertTrue(m["ledger"]["bullets"] and all(b.get("tlId") == prompt_id for b in m["ledger"]["bullets"]),
                        "a TOC bullet lights the turn's start dot")

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
        """A `romp --mail send` Bash call → an outgoing card too, once delivery is confirmed."""
        ev = {"kind": "tool", "name": "Bash", "input": 'romp --mail send beta "hi there"',
              "output": "[romp mail] delivered to beta", "isError": False, "uuid": "t2", "ts": "x"}
        out = km._hydrate_postal([ev], {})
        self.assertEqual((out[0]["direction"], out[0]["peer"], out[0]["body"]), ("out", "beta", "hi there"))

    def test_hydrate_postal_passes_through_unresolved(self):
        """A marker with no matching log entry, or a plain event, stays unchanged (never half-rendered)."""
        ev = {"kind": "user", "md": "hi <!-- romp-msg-id: missing -->", "uuid": "u9"}
        self.assertEqual(km._hydrate_postal([ev], {}), [ev], "unresolved marker → unchanged")
        plain = {"kind": "assistant", "md": "just a reply", "uuid": "a1"}
        self.assertEqual(km._hydrate_postal([plain], {}), [plain], "a non-postal event is untouched")

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
        self.assertEqual(lane["state"], "idle", "turn ended, no blocked goal -> idle")
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
            self.assertEqual(lane["backend"], "tmux", "the fixture session is tmux (no SDK registry)")
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

    def test_timeline_state_and_metadata_from_tmux(self):
        # live lanes take state + model/effort/context from tmux @claude-* vars (the READY badge =
        # state "waiting"); badgeFor hides the badge unless live, so live must be true here
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 10, "model": "Opus 4.8",
                                           "effort": "xhigh", "context": 43, "compactPct": None,
                                           "color": "#abcdef"}}
        lane = next(s for s in km.build_timeline(NOW)["sessions"] if s["id"] == SID)
        self.assertTrue(lane["live"])
        self.assertEqual(lane["state"], "waiting", "tmux state drives the lane (waiting -> READY badge)")
        self.assertEqual(lane["model"], "Opus 4.8")
        self.assertEqual(lane["effort"], "xhigh")
        self.assertEqual(lane["context"], 43)

    def test_timeline_includes_dead_sessions_for_scrollback(self):
        # the user 2026-06-16: dead sessions appear as struck lanes so scrolling back surfaces them. The
        # regression was build_timeline feeding only LIVING sessions; it now includes window-dead ones
        # too (the render's active-filter only shows a dead lane when the window covers its activity).
        # SID has a transcript but is passed NO tmux → it must still be a lane, marked dead.
        s = {x["id"]: x for x in km.build_timeline(NOW, tmux={})["sessions"]}
        self.assertIn(SID, s, "a window-dead session is still a timeline lane")
        self.assertFalse(s[SID]["live"], "no tmux → a dead lane (the render strikes it)")

    def test_timeline_lane_survives_hidden_tab(self):
        # the user 2026-06-17 (reversing d52f69f): ×-hiding a tab is a tab-strip preference and must NOT
        # erase the lane from the timeline — the timeline is a complete activity history. So a dead AND
        # ×-hidden session still appears on the timeline (the render's active-filter alone gates it by
        # window overlap); only the chat tab strip honors the hidden set.
        km._set_hidden_tab(SID, True)
        try:
            self.assertIn(SID, km._hidden_tabs(), "SID is ×-hidden from the tab strip")
            s = {x["id"]: x for x in km.build_timeline(NOW, tmux={})["sessions"]}
            self.assertIn(SID, s, "a ×-hidden dead session is STILL a timeline lane")
            self.assertFalse(s[SID]["live"], "and it's a dead (struck) lane")
            # the tab strip still hides it (the hidden set is the tab-strip's, not the timeline's)
            self.assertNotIn(SID, {x["sid"] for x in km._chat_tab_sessions(NOW, {})},
                             "the tab strip still honors the hidden set")
        finally:
            km._set_hidden_tab(SID, False)

    def test_dead_session_is_timeline_only_until_viewed(self):
        # the user 2026-06-17: a dead session is TIMELINE-ONLY — no auto chat tab. It gets a read-only
        # tab ONLY on demand (View read-only → _kept_open); ×-close forgets it (timeline-only again).
        saved = set(km._kept_open)
        try:
            km._kept_open.discard(SID)
            tabs = lambda: {x["sid"] for x in km._chat_tab_sessions(NOW, {})}   # tmux={} → SID is dead
            self.assertNotIn(SID, tabs(), "a dead session is NOT auto-kept as a tab")
            km._kept_open.add(SID)                       # 'View read-only'
            self.assertIn(SID, tabs(), "View read-only → a read-only tab")
            km._kept_open.discard(SID)                   # ×-close
            self.assertNotIn(SID, tabs(), "×-close forgets it → timeline-only again")
        finally:
            km._kept_open.clear(); km._kept_open.update(saved)

    def test_chat_chip_maps_tmux_state(self):
        # the chat chip maps tmux state: permission -> awaiting, plus model/effort/ctx for the statusline
        km._tmux_sessions = lambda: {SID: {"state": "permission", "since": NOW - 5, "model": "Opus 4.8",
                                           "effort": "max", "context": 20, "compactPct": None, "color": None}}
        st = km.build_session(SID, NOW)["status"]
        self.assertEqual(st["state"], "awaiting", "permission -> awaiting chip")
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


class ApiRetryAndTabOrderRoutes(unittest.TestCase):
    """WS handlers hard to drive through the socket — assert the routing is wired in source (mirrors
    CompactSessionRoute): the Retry button pastes "retry"; the kernel pushes the saved tab order on connect."""

    def test_routes_present(self):
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('t == "apiRetry"', src, "Retry button → apiRetry, handled in the unified _drive")
        self.assertIn(r'"retry\n\n<!-- romp-injected -->" if be is _TMUX else "retry"', src,
                      "apiRetry pastes 'retry' tagged romp-injected on tmux (→ a gray romp bubble), bare on the SDK")
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
    """The serve-layer gate (design/read-side.md): Origin/Host validation on every request AND the
    /ws upgrade (kills the cross-site WS hole token-free), + ROMP_SERVE_TOKEN for non-local reach.
    Runs the REAL handler over a loopback server (GET /feed is a static page → no model calls)."""

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

    def test_local_request_allowed(self):
        self.assertEqual(self._code("/feed", {}), 200)

    def test_restart_endpoint_acks_post(self):
        """The web Restart button (↻) POSTs /restart; the kernel must ACK {ok,restarting} (and, with a
        manager, relay /restart-all so the kernel process relaunches). Regression guard: the Python
        rewrite dropped do_POST entirely, so the button silently no-op'd and the user had to pkill.
        No ROMP_MANAGER_PORT here → it acks without restarting anything."""
        import urllib.request, json as _json
        saved = os.environ.pop("ROMP_MANAGER_PORT", None)   # never trigger a real restart-all in a test
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/restart" % self.port, method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(_json.loads(r.read().decode()), {"ok": True, "restarting": True})
        finally:
            if saved is not None:
                os.environ["ROMP_MANAGER_PORT"] = saved

    def test_tick_endpoint_wakes_producer(self):
        """POST /tick is the event-driven judge trigger: the Stop / UserPromptSubmit hooks poke it the
        instant a turn ends / a prompt lands, and it must wake the producer (set _producer_wake) so the
        judges run NOW instead of on the next 20s backstop tick. Local request → no token needed."""
        import urllib.request, json as _json
        km._producer_wake.clear()
        self.assertFalse(km._producer_wake.is_set())
        req = urllib.request.Request("http://127.0.0.1:%d/tick" % self.port, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(_json.loads(r.read().decode()), {"ok": True, "woke": True})
        self.assertTrue(km._producer_wake.is_set())
        km._producer_wake.clear()

    def test_unknown_post_path_is_404(self):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d/nope" % self.port, method="POST", data=b"")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 404)

    def test_timeline_page_served(self):
        # the combined shell's third pane: /timeline injects the shared obsidian TimelinePanel verbatim
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/timeline" % self.port, timeout=5) as r:
            self.assertEqual(r.status, 200)
            body = r.read().decode("utf-8", "replace")
        self.assertIn("TimelinePanel", body, "the shared obsidian view is injected")
        self.assertIn("app=timeline", body, "the page drives panel.update over the kernel WS")

    def test_landing_has_three_panes(self):
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5) as r:
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
        # asks the shell to lift the feed iframe over the whole window; the feed then goes TRANSPARENT and
        # hides its own content (rs-modal-open), so the dimmed three-pane DASHBOARD shows through behind the
        # card — not the feed cards blown up full-screen.
        self.assertIn("#rsettings{position:fixed;inset:0;z-index:60;background:#0000009c", km._GEAR_CSS)
        self.assertIn(".rs-card{", km._GEAR_CSS)
        self.assertIn(".rs-modal-open{background:transparent}", km._GEAR_CSS)            # feed iframe transparent while open
        self.assertIn("body.rs-modal-open #feed-list", km._GEAR_CSS)                     # feed cards hidden while open
        self.assertIn("<div id=rsettings hidden><div class=rs-card>", km._GEAR_HTML)
        self.assertIn("feedFull(true)", km._GEAR_JS)              # open → ask the shell to go full-window
        self.assertIn("setModalCls(true)", km._GEAR_JS)          # open → feed goes transparent + hides content
        self.assertIn("if(e.target===p)closeSettings()", km._GEAR_JS)   # backdrop click closes
        # shell side: the feed iframe lifts to cover the whole window (the panes show THROUGH the transparent feed)
        html = km._landing()
        self.assertIn("body.settings-open #f-feed{position:fixed;inset:0;z-index:200", html)
        self.assertIn("m.romp==='settings'", html)
        self.assertIn("document.body.classList.toggle('settings-open',!!m.on)", html)

    def test_fleet_page_served(self):
        # Fleet (the user 2026-06-23): /fleet serves the by-session open-work view, rendered by dist/fleet.js.
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/fleet" % self.port, timeout=5) as r:
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
        # desktop-only for now: hidden on the mobile one-pane layout
        self.assertIn("#fleet-pane{display:none!important}", html)

    def test_landing_pins_height_to_visual_viewport(self):
        # Regression (the user 2026-06-19): on real Android Chrome, body{height:100dvh} left a dead slab
        # below the mobile Chat/Feed/Timeline bar — dvh didn't match the painted viewport. The shell now
        # pins the height to window.visualViewport.height via --app-h, keeping 100dvh only as a fallback.
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5) as r:
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
        # same-origin upgrade passes the gate (101); urllib can't complete the upgrade, so a 101
        # surfaces as a non-403 — assert it's NOT rejected
        self.assertNotEqual(self._code("/ws?app=chat", {
            "Origin": "http://127.0.0.1:%d" % self.port, "Host": "127.0.0.1:%d" % self.port,
            "Upgrade": "websocket", "Connection": "Upgrade",
            "Sec-WebSocket-Key": "x", "Sec-WebSocket-Version": "13"}), 403)

    def test_healthz_exempt(self):
        self.assertEqual(self._code("/healthz", {"Origin": "http://evil.example"}), 200)

    def test_nonlocal_host_needs_token(self):
        h = {"Host": "100.64.1.2:%d" % self.port}
        self.assertEqual(self._code("/feed", h), 403)
        self.assertEqual(self._code("/feed?token=testtok", h), 200)


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


class SessionListNameCollision(unittest.TestCase):
    """Regression (the user 2026-06-22): two functions were both named _session_list — the picker payload
    builder _session_list(now, tmux) and a 0-arg tmux query for GET /sessions (commit 7b89bd9). The 0-arg
    def came LATER, so it SHADOWED the picker's. The webview's `requestSessions` handler calls it with two
    args → TypeError → the WS handler thread died → the socket dropped → the client reconnected and BLANKED
    the chat (wiping the half-typed new-session name), and the picker dropdown showed NO existing sessions.
    Fix: the GET /sessions query is its OWN distinct 0-arg name (now _session_rows). Guard the re-collision."""

    def test_picker_session_list_keeps_its_now_tmux_signature(self):
        import inspect
        self.assertEqual(list(inspect.signature(km._session_list).parameters), ["now", "tmux"],
                         "the picker payload builder must stay _session_list(now, tmux) — requestSessions calls it that way")

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


class SegBestText(unittest.TestCase):
    """_seg_best_text: the biggest contiguous block of assistant TEXT in a segment → a distilled summary's
    deep-link target (the user 2026-06-22). Skips API-error atoms (a failed turn carries text but is never a
    jump target, like _seg_anchors)."""

    @staticmethod
    def _a(uuid, text, api=False):
        a = {"type": "assistant", "uuid": uuid, "message": {"content": [{"type": "text", "text": text}]}}
        if api:
            a["isApiError"] = True
        return a

    def test_picks_the_assistant_atom_with_the_most_text(self):
        big = "a much longer reply with a lot more words than the others here"
        atoms = [self._a("u1", "short"), self._a("u2", big), self._a("u3", "mid length reply")]
        u, n = km._seg_best_text(atoms)
        self.assertEqual(u, "u2")
        self.assertEqual(n, len(big))

    def test_skips_api_error_atoms_even_when_they_are_the_longest(self):
        atoms = [self._a("u1", "the real reply"),
                 self._a("uErr", "API Error: overloaded — " + "x" * 200, api=True)]
        u, _ = km._seg_best_text(atoms)
        self.assertEqual(u, "u1", "a long API-error line is never the jump target")

    def test_no_assistant_prose_returns_none(self):
        atoms = [{"type": "user", "uuid": "u1", "message": {"content": "hi"}},
                 {"type": "assistant", "uuid": "u2", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}]
        u, n = km._seg_best_text(atoms)
        self.assertIsNone(u)
        self.assertEqual(n, 0)


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
        km._ws_recv = lambda rfile: seq.pop(0) if seq else (0x8, b"")   # script the frames; (0x8)=close
        try:
            with contextlib.redirect_stderr(io.StringIO()):             # swallow the logged traceback
                km.Handler._ws(FakeSelf())                              # returns normally on close / socket error
        finally:
            km._ws_recv = saved
        return seen

    def test_a_throwing_handler_does_not_end_the_loop(self):
        TEXT = 0x1
        frames = [(TEXT, b'{"type":"a"}'), (TEXT, b'{"type":"b"}'), (0x8, b"")]   # a raises, b must still run
        seen = self._run_loop(frames, lambda msg: (_ for _ in ()).throw(RuntimeError("boom"))
                              if msg.get("type") == "a" else None)
        self.assertEqual(seen["types"], ["a", "b"], "'b' still processed after 'a' raised — the socket survived")

    def test_a_socket_error_propagates_and_stops_the_loop(self):
        TEXT = 0x1
        frames = [(TEXT, b'{"type":"a"}'), (TEXT, b'{"type":"b"}'), (0x8, b"")]
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
        self.assertIn("rs-defaultdir-browse", km._GEAR_HTML)             # the gear's Browse button
        self.assertIn("setDefaultDir", km._GEAR_JS)                      # change → kernel-side persist (the file)
        self.assertIn("target:'gear'", km._GEAR_JS)                      # Browse posts browseDir target=gear

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
                       set(km._kept_open), km._hidden_tabs)

    def tearDown(self):
        (km._ordered_alive, km._alive_sessions, km._sessions, km._session_order,
         kept, km._hidden_tabs) = self._saved
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
        km._hidden_tabs = lambda: set()
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
        text = "edit the files and I'll restart"
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
