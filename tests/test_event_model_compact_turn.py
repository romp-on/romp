#!/usr/bin/env python3
"""A compact_boundary opens its OWN turn (the user 2026-07-13). As a non-opener it used to absorb into
the previous turn, whose end = max(atom ends) then stretched to the boundary's timestamp — so the
timeline drew a phantom work bar spanning the whole idle gap "leading up to the moment of compaction",
growing live while the compact ran. Now the boundary anchors a fresh turn: the prior turn's bar ends at
its real last activity, the post-compact continuation files under the boundary turn (its bar starts AT
the compaction), and a boundary-only tail reads ended (a completed event, not in-flight work) so it can
never fake an open bar / WORKING. Synthetic transcript only — placeholder UUIDs, no real data."""
import datetime
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_compact", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"


def _iso(ep):
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _turns(recs):
    td = tempfile.mkdtemp()
    p = os.path.join(td, SID + ".jsonl")
    open(p, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
    sess = em.parse_session(p, rompuuid=SID, name="impl", dir="/TESTDIR",
                            candidate_files=[p], states=None, postal_log=[], now=int(time.time()))
    return sess["turns"]


def _recs(now, with_continuation):
    t_old, t_done = now - 3600, now - 60
    recs = [
        {"type": "user", "timestamp": _iso(t_old), "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "please do the thing"}},
        {"type": "assistant", "timestamp": _iso(t_old + 30), "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "done."}],
                     "stop_reason": "end_turn"}},
        {"type": "system", "subtype": "compact_boundary", "timestamp": _iso(t_done), "uuid": "cb1",
         "parentUuid": None, "logicalParentUuid": "a1",
         "compactMetadata": {"trigger": "manual", "preTokens": 100000}},
        {"type": "user", "timestamp": _iso(t_done), "uuid": "cs1", "parentUuid": "cb1",
         "isCompactSummary": True,
         "message": {"role": "user", "content": "This session is being continued from a previous one..."}},
    ]
    if with_continuation:
        recs.append({"type": "assistant", "timestamp": _iso(t_done + 20), "uuid": "a2", "parentUuid": "cs1",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "resuming"}],
                                 "stop_reason": "end_turn"}})
    return recs


class CompactBoundaryTurn(unittest.TestCase):
    def test_boundary_never_stretches_the_previous_turn(self):
        now = int(time.time())
        turns = _turns(_recs(now, with_continuation=True))
        self.assertEqual(len(turns), 2, "the compaction is its own turn, not an absorbed tail")
        old, comp = turns
        self.assertEqual(old["end"], now - 3600 + 30,
                         "the prior turn's bar ends at its real last activity — an hour-long phantom "
                         "work period 'leading up to the compaction' was the bug")
        self.assertTrue(old["ended"])
        self.assertEqual(comp["t"], now - 60, "the compaction turn starts AT the boundary, never before")
        self.assertIn("cb1", [a.get("uuid") for a in comp["atoms"]])

    def test_continuation_work_files_under_the_boundary_turn(self):
        now = int(time.time())
        comp = _turns(_recs(now, with_continuation=True))[1]
        self.assertIn("a2", [a.get("uuid") for a in comp["atoms"]],
                      "the CLI's post-compact continuation rides the compaction turn")
        self.assertTrue(comp["ended"], "the continuation's end_turn settles it")

    def test_a_boundary_only_tail_is_self_contained(self):
        # mid-compaction parse: the boundary landed, the continuation hasn't — the turn must not read
        # open (a phantom growing bar / WORKING on an idle session)
        now = int(time.time())
        comp = _turns(_recs(now, with_continuation=False))[-1]
        self.assertTrue(comp["ended"], "a boundary with no assistant work yet is a completed event")
        self.assertEqual(comp["end"], comp["t"], "zero span — the marker, not a bar")


def _user(t, uuid, parent, text):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}}


def _assistant(t, uuid, parent, text):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _codex_boundary(t, uuid, logical_parent):
    """A compact_boundary exactly as the Codex normalizer writes it (kernel/codex_events.py
    ThreadNormalizer._compacted): trigger only, no token counts — and, the shape that matters
    here, NO isCompactSummary record and NO replayed tail follow it. The runtime compacts at the
    top of the next turn, so the record after the boundary is the person's next prompt."""
    return {"type": "system", "subtype": "compact_boundary", "uuid": uuid, "parentUuid": None,
            "logicalParentUuid": logical_parent, "timestamp": _iso(t), "sessionId": SID,
            "cwd": "/TESTDIR", "version": "0.0.0", "isMeta": False,
            "compactMetadata": {"trigger": "auto"}}


def _claude_boundary(t, uuid, logical_parent):
    return {"type": "system", "subtype": "compact_boundary", "uuid": uuid, "parentUuid": None,
            "logicalParentUuid": logical_parent, "timestamp": _iso(t), "isMeta": False,
            "compactMetadata": {"trigger": "auto", "preTokens": 100000, "postTokens": 5000}}


def _claude_summary(t, uuid, parent):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
            "message": {"role": "user", "content": "This session is being continued from a previous one..."}}


def _manual_compact_lines(t_issue, t_done, tag, parent):
    """The on-disk tail of a LIVE manual /compact, the shape the golden module's manual_compact_lines
    models (verified against the live corpus 2026-08-19; every value here synthetic): the boundary and
    its summary land FIRST, at completion time, as a DETACHED side branch (parentUuid null,
    logicalParentUuid the pre-compact leaf, the summary its only child and stamped with the invoking
    /compact's promptId), then the command's raw twin and wrapper at issue time, then the stdout at
    completion time. The conversation chains through the wrappers, so the adoption repair splices the
    pair in after the stdout and records the boundary in _adopted. Returns (records, stdout uuid);
    chain the next prompt off the stdout, as the CLI does."""
    cb, cs, rt, cw, so = ("cb" + tag, "cs" + tag, "rt" + tag, "cw" + tag, "so" + tag)
    return [
        {"type": "system", "subtype": "compact_boundary", "timestamp": _iso(t_done), "uuid": cb,
         "parentUuid": None, "logicalParentUuid": parent, "isMeta": False,
         "compactMetadata": {"trigger": "manual", "preTokens": 100000, "postTokens": 5000}},
        {"type": "user", "timestamp": _iso(t_done), "uuid": cs, "parentUuid": cb, "promptId": "p" + tag,
         "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
         "message": {"role": "user", "content": "summary of the conversation so far"}},
        {"type": "user", "timestamp": _iso(t_issue), "uuid": rt, "parentUuid": parent,
         "isMeta": True, "promptId": "p" + tag, "message": {"role": "user", "content": "/compact"}},
        {"type": "user", "timestamp": _iso(t_issue), "uuid": cw, "parentUuid": rt, "promptId": "p" + tag,
         "message": {"role": "user", "content": "<command-name>/compact</command-name>\n"
                                                "<command-message>compact</command-message>\n"
                                                "<command-args></command-args>"}},
        {"type": "user", "timestamp": _iso(t_done), "uuid": so, "parentUuid": cw, "promptId": "p" + tag,
         "message": {"role": "user", "content": "<local-command-stdout>Compacted "
                                                "(ctrl+o to see full summary)</local-command-stdout>"}},
    ], so


class RepeatedPromptAfterBoundary(unittest.TestCase):
    """The post-compaction replay dedup arms on the record that BEGINS a replay — the isCompactSummary
    record — never on the boundary alone (2026-09-19). The Claude CLI writes boundary, summary, then a
    verbatim copy of the recent tail, and a user prompt inside that burst whose text repeats an earlier
    one is restored context, not a second ask. A Codex boundary is followed by NOTHING of the kind: the
    runtime compacts at the top of the next turn, so the record right after the boundary is the person's
    new prompt. Armed at the boundary, the dedup read a repeated prompt there ("continue", a canned
    follow-up sent more than once) as a replay and silently dropped it — the reply then filed as a
    triggerless continuation of the boundary's turn. A silently dropped ask is the one loss this
    module's neighbors treat as fatal. Synthetic transcript only — placeholder ids, invented text."""

    def _flat(self, turns):
        return [a for t in turns for a in t["atoms"]]

    def _utexts(self, turns):
        return [em._text_of(em._content(a.get("message"))) for a in self._flat(turns)
                if a.get("type") == "user"]

    def _holder(self, turns, uuid):
        return next(t for t in turns if uuid in [a.get("uuid") for a in t["atoms"]])

    def _cards(self, turns):
        return [a for a in self._flat(turns) if a.get("subtype") == "compact_boundary"]

    def test_a_repeated_prompt_right_after_a_codex_boundary_survives(self):
        now = int(time.time())
        t0, t1 = now - 3600, now - 60
        recs = [_user(t0, "u1", None, "continue"),
                _assistant(t0 + 30, "a1", "u1", "done."),
                _codex_boundary(t1, "cb1", "a1"),
                _user(t1 + 5, "u2", "cb1", "continue"),          # the SAME text, a new ask, no replay
                _assistant(t1 + 25, "a2", "u2", "carrying on")]
        turns = _turns(recs)
        uuids = [a.get("uuid") for a in self._flat(turns)]
        self.assertIn("u2", uuids, "the person's prompt after a Codex boundary was read as a replay and "
                                   "dropped — there is no restore burst to be a replay of")
        self.assertEqual(self._utexts(turns).count("continue"), 2,
                         "both asks render — the second is a message the person actually sent")
        self.assertIn("cb1", uuids, "the boundary itself still emits")
        holder = self._holder(turns, "a2")
        self.assertEqual((holder.get("trigger") or {}).get("uuid"), "u2",
                         "the reply files under the prompt that asked for it, not as a triggerless "
                         "continuation of the boundary's turn")
        self.assertIn("u2", [a.get("uuid") for a in holder["atoms"]])
        self.assertEqual(len(turns), 3, "the first exchange, the boundary's own turn, the new exchange")

    def test_a_claude_replay_after_the_summary_still_dedupes(self):
        # The shape the dedup was written for, pinned so the re-arming keeps it: boundary, summary, a
        # replayed copy of the earlier prompt (new uuid and stamp, same text — the tail the golden
        # fixtures model, user records only), the CLI's continuation, then a fresh prompt that repeats
        # the text once more AFTER work resumed.
        now = int(time.time())
        t0, t1 = now - 3600, now - 60
        recs = [_user(t0, "u1", None, "continue"),
                _assistant(t0 + 30, "a1", "u1", "done."),
                _claude_boundary(t1, "cb1", "a1"),
                _claude_summary(t1, "cs1", "cb1"),
                _user(t1 + 1, "u1r", "cs1", "continue"),         # the restored tail: a copy of u1
                _assistant(t1 + 20, "a2", "u1r", "resuming"),
                _user(t1 + 100, "u3", "a2", "continue"),          # typed after work resumed: a real ask
                _assistant(t1 + 130, "a3", "u3", "again")]
        turns = _turns(recs)
        uuids = [a.get("uuid") for a in self._flat(turns)]
        self.assertNotIn("u1r", uuids, "the replayed copy is restored context, not a second ask")
        self.assertIn("u1", uuids, "the original survives")
        self.assertIn("u3", uuids, "a repeat typed after work resumed is a message the person sent")
        self.assertEqual(self._utexts(turns).count("continue"), 2, "the original and the fresh ask, no copy")
        self.assertEqual((self._holder(turns, "a3").get("trigger") or {}).get("uuid"), "u3")
        self.assertEqual((self._holder(turns, "a2").get("trigger") or {}).get("uuid"), None,
                         "the CLI's continuation rides the boundary's turn, as before")

    def test_a_summary_stamped_a_second_before_its_boundary_still_arms_the_window(self):
        # The Claude CLI stamps the summary record a second BEFORE its boundary in about a third of
        # live auto compactions (2026-09-19, one machine's corpus; every such summary parents to its
        # boundary), and the pre-pass walks (second, read order), so there the summary is walked
        # first, with no boundary walked yet. Keyed on the last boundary walked, the window never
        # opened for a transcript's first compaction and the replayed copy rendered as a second ask;
        # keyed on the summary's own boundary (its parentUuid) it opens on either stamp order.
        now = int(time.time())
        t0, t1 = now - 3600, now - 60
        recs = [_user(t0, "u1", None, "continue"),
                _assistant(t0 + 30, "a1", "u1", "done."),
                _claude_boundary(t1, "cb1", "a1"),
                _claude_summary(t1 - 1, "cs1", "cb1"),           # stamped a second before its boundary
                _user(t1 + 1, "u1r", "cs1", "continue"),          # the restored tail: a copy of u1
                _assistant(t1 + 20, "a2", "u1r", "resuming"),
                _user(t1 + 100, "u3", "a2", "continue"),           # typed after work resumed: a real ask
                _assistant(t1 + 130, "a3", "u3", "again")]
        turns = _turns(recs)
        uuids = [a.get("uuid") for a in self._flat(turns)]
        self.assertNotIn("u1r", uuids, "the replayed copy is restored context, not a second ask, "
                                       "whichever of the pair the CLI stamped first")
        self.assertIn("u1", uuids, "the original survives")
        self.assertIn("u3", uuids, "a repeat typed after work resumed is a message the person sent")
        self.assertEqual(self._utexts(turns).count("continue"), 2, "the original and the fresh ask, no copy")
        self.assertEqual((self._holder(turns, "a3").get("trigger") or {}).get("uuid"), "u3")
        self.assertEqual([c.get("summary") for c in self._cards(turns)],
                         ["This session is being continued from a previous one..."],
                         "the card carries its own summary whichever of the pair was walked first")

    def test_a_summary_stamped_before_its_boundary_arms_after_an_adopted_manual_pair(self):
        # The same inverted pair later in a session that already holds an adopted manual /compact.
        # Keyed on the last boundary walked, the summary met the ADOPTED boundary there, and the
        # adopted-pair gate (right for a live manual compact, which replays no tail) disarmed this
        # unrelated auto compaction's replay dedup and hung its summary on the manual card; keyed on
        # its own boundary, the window arms and each card keeps its own summary.
        now = int(time.time())
        t0, tm, t1 = now - 3600, now - 1800, now - 60
        side, so = _manual_compact_lines(tm - 10, tm, "0", parent="a1")
        recs = ([_user(t0, "u1", None, "start the long build"),
                 _assistant(t0 + 30, "a1", "u1", "on it")]
                + side
                + [_user(tm + 100, "u2", so, "continue"),           # the person's next prompt after the manual compact
                   _assistant(tm + 130, "a2", "u2", "continuing"),
                   _claude_boundary(t1, "cb1", "a2"),
                   _claude_summary(t1 - 1, "cs1", "cb1"),          # stamped a second before its boundary
                   _user(t1 + 1, "u2r", "cs1", "continue"),         # the restored tail: a copy of u2
                   _assistant(t1 + 20, "a3", "u2r", "resuming"),
                   _user(t1 + 100, "u4", "a3", "continue"),          # typed after work resumed: a real ask
                   _assistant(t1 + 130, "a4", "u4", "again")])
        turns = _turns(recs)
        uuids = [a.get("uuid") for a in self._flat(turns)]
        self.assertEqual([c["uuid"] for c in self._cards(turns)], ["cb0", "cb1"],
                         "the adopted manual card and the auto card both render, in order")
        self.assertIn("so0", uuids, "the adopted pair still arms nothing: its stdout renders")
        self.assertIn("u2", uuids, "the genuine prompt after the manual compact renders")
        self.assertNotIn("u2r", uuids, "the auto compaction's replayed copy is still restored context")
        self.assertIn("u4", uuids, "a repeat typed after work resumed is a message the person sent")
        self.assertEqual(self._utexts(turns).count("continue"), 2, "the real ask and the fresh ask, no copy")
        self.assertEqual((self._holder(turns, "a4").get("trigger") or {}).get("uuid"), "u4")
        self.assertEqual([c.get("summary") for c in self._cards(turns)],
                         ["summary of the conversation so far",
                          "This session is being continued from a previous one..."],
                         "each card carries its own summary; the auto pair's never lands on the manual card")


PID = "aaaaaaaa-1111-2222-3333-444444444444"


def _twin_recs(now, bare_text="/usage", with_wrapper=True, name="/usage"):
    """The CLI 2.1.215+ dual shape: a typed slash command lands TWICE — a raw-text user record (the
    submitted prompt verbatim, carrying promptId) and the <command-name> wrapper (same promptId),
    parent-chained straight through. Synthetic."""
    t0, tc = now - 3600, now - 120
    recs = [
        {"type": "user", "timestamp": _iso(t0), "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "please do the thing"}},
        {"type": "assistant", "timestamp": _iso(t0 + 30), "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "done."}],
                     "stop_reason": "end_turn"}},
        {"type": "user", "timestamp": _iso(tc), "uuid": "u2", "parentUuid": "a1", "promptId": PID,
         "message": {"role": "user", "content": bare_text}},
    ]
    if with_wrapper:
        recs += [
            {"type": "user", "timestamp": _iso(tc), "uuid": "u3", "parentUuid": "u2", "promptId": PID,
             "message": {"role": "user", "content": "<command-name>%s</command-name>\n"
                         "<command-message>%s</command-message>\n<command-args></command-args>"
                         % (name, name.lstrip("/"))}},
            {"type": "user", "timestamp": _iso(tc + 1), "uuid": "u4", "parentUuid": "u3",
             "message": {"role": "user", "content": "<local-command-stdout>ok</local-command-stdout>"}},
        ]
    return recs


def _atxt(a):
    return " ".join(b.get("text", "") for b in (a.get("message") or {}).get("content", [])
                    if isinstance(b, dict))


class BareInvocationTwin(unittest.TestCase):
    """CLI 2.1.215+ writes a typed slash command twice (see _twin_recs). The raw twin carries no
    wrapper, no isMeta, no isCompactSummary — left in, it walks the whole user-atom path and becomes a
    genuine HUMAN atom: a duplicate bubble in the chat, and a plannable human trigger the judges mint
    from (the rescue thread's 'Compact conversation context' card, 2026-07-20 — its quote was literally
    "/compact"). The wrapper is the one tracked command atom; its raw twin is invocation echo."""

    def _atoms(self, recs):
        return [a for t in _turns(recs) for a in t["atoms"]]

    def test_the_bare_twin_never_becomes_a_human_atom(self):
        atoms = self._atoms(_twin_recs(int(time.time())))
        plain = [a for a in atoms if a.get("type") == "user" and not a.get("command")
                 and _atxt(a).strip() == "/usage"]
        self.assertEqual(plain, [], "the raw '/usage' twin surfaced as a human atom — a duplicate "
                                    "bubble the judges also treat as a plannable prompt")

    def test_the_wrapper_still_lands_as_the_one_command_atom(self):
        atoms = self._atoms(_twin_recs(int(time.time())))
        self.assertEqual(len([a for a in atoms if a.get("command") == "/usage"]), 1,
                         "exactly one tracked command atom — the wrapper's")

    def test_a_lone_slash_message_without_a_wrapper_stays_human(self):
        # no wrapper anywhere on this promptId → the text is a genuine message, whatever it looks like
        atoms = self._atoms(_twin_recs(int(time.time()), with_wrapper=False))
        self.assertEqual(len([a for a in atoms if a.get("type") == "user" and not a.get("command")
                              and _atxt(a).strip() == "/usage"]), 1,
                         "without a <command-name> twin the record is the user's own words — keep it")

    def test_a_twin_only_matches_its_own_invocation_text(self):
        # same promptId as a wrapper, but the text is NOT the invocation → it stays a human atom
        atoms = self._atoms(_twin_recs(int(time.time()), bare_text="see /usage docs for details"))
        kept = [a for a in atoms if a.get("type") == "user" and not a.get("command")
                and "see /usage docs" in _atxt(a)]
        self.assertEqual(len(kept), 1, "only the invocation echo is dropped, never other text")

    def test_args_ride_the_twin_match(self):
        atoms = self._atoms(_twin_recs(int(time.time()), bare_text="/model opus", name="/model"))
        plain = [a for a in atoms if a.get("type") == "user" and not a.get("command")
                 and _atxt(a).strip() == "/model opus"]
        self.assertEqual(plain, [], "an args-bearing invocation echo drops too")


if __name__ == "__main__":
    unittest.main()
