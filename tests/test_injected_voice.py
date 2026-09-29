#!/usr/bin/env python3
"""Every message romp injects into a session is written as the USER asking, not as romp reporting
(the user rule, 2026-07-24 — CLAUDE.md "Messages we inject into a session").

The recipient is an agent with NO idea it is being tracked. It has never seen the feed, has no concept
of a card, a goal, a board or a column, and cannot act on any of it. A message that narrates that
machinery reads as a system notice rather than the person it works for asking for something. The
2026-07-24 sweep found five: the two feed status asks, the multi-goal bundle, the nudge quote header,
and the fork/stalled nudge. (The clear wrap-up retired 2026-08-23: clear is a silent discard.)

This test renders each injected body from SYNTHETIC fixtures and fails on romp vocabulary in the PROSE.
It is the guardrail behind the CLAUDE.md rule, so the rule holds without anyone remembering it.

Scope note — what is deliberately NOT checked:
- the MARKER TAIL (everything from the first "<!--"). It names romp on purpose in `romp-goal-id` /
  `romp-injected`, and its romp-note describes the comments as "an external tracking system" precisely
  so it does NOT have to name romp to the model. Prose only.
- the SessionStart instruction, which asks for ordinary self-reporting (what you finished, what you're
  blocked on) and names no machinery.
- the session prompt's housekeeping note (claude/romp-session-prompt.md), the ONE place romp is named
  to a session on purpose: it pre-explains the [romp] / <!-- romp-* --> artifacts as an external
  session manager's bookkeeping to ignore (the user 2026-07-25). Pinned by test_session_prompt.py.
- sdk_backend's "[romp] The kernel restarted…" notices, which are genuinely ABOUT romp: they tell a
  session why its turn was cut, so naming it is the point (and the housekeeping note gives the
  name meaning). The rename ping (RENAME_NUDGE, 2026-08-24) is the same family — it tells a session
  its own new name — and is pinned below to stay one marker-free line with no romp nouns beyond
  the sanctioned prefix.

SYNTHETIC fixtures only (placeholder ids, invented goal text).
"""
import json
import os
import re
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_voice", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
TOP, SUB_OPEN, SUB_BLOCKED, TOP2 = (SID + ":g1", SID + ":g2", SID + ":g3", SID + ":g4")
T0 = 1781100000

# romp's OWN vocabulary — words that name a thing only romp knows about. A message using one of these
# is describing the tracking system to someone who has never heard of it.
ROMP_WORDS = [
    ("romp", "the product name — the recipient has never heard of it"),
    ("card", "a feed object; the agent sees no feed"),
    ("board", "a column layout the agent cannot see"),
    ("goal", "romp's unit of tracking, not a word the user would use to an agent"),
    ("cleared", "a board gesture"),
    ("dismissal", "a board gesture"),
    ("status check", "announces a form rather than asking a question"),
    ("nudge", "romp's name for this message"),
]


def _nodes():
    return {TOP: {"id": TOP, "text": "Ship the notes API", "parentId": None, "nodeComplete": False,
                  "blocked": False, "cleared": False, "why": "The client is waiting on it.",
                  "summary": "Endpoints are live and the client is wired up.", "t": T0, "mt": T0},
            SUB_OPEN: {"id": SUB_OPEN, "text": "Backfill the fixtures", "parentId": TOP,
                       "nodeComplete": False, "blocked": False,
                       "why": "Needed before the load test.", "t": T0, "mt": T0},
            SUB_BLOCKED: {"id": SUB_BLOCKED, "text": "Pick the rate-limit ceiling", "parentId": TOP,
                          "nodeComplete": False, "blocked": True,
                          "blockWhy": "Need you to choose a number.", "t": T0, "mt": T0},
            TOP2: {"id": TOP2, "text": "Write the migration guide", "parentId": None,
                   "nodeComplete": False, "blocked": False, "cleared": False, "t": T0, "mt": T0}}


def prose(body):
    """The part a model actually reads as instruction: everything before the marker tail."""
    return body.split("<!--")[0]


def _atom(role, text, tools=0):
    blocks = [{"type": "text", "text": text}] + [{"type": "tool_use", "name": "Read"} for _ in range(tools)]
    return {"type": role, "t": 1, "message": {"role": role, "content": blocks}}


_RELAY_TURN = {"t": 1, "end": 2, "atoms": [_atom("user", "start on the exporter"),
                                         _atom("assistant", "which client should it target?", tools=2)]}


class InjectedBodiesSpeakAsTheUser(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_goaldir, self.saved_state = jd.GOALDIR, jd.STATE
        jd.GOALDIR, jd.STATE = Path(self.td.name), Path(self.td.name)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 4, "nodes": _nodes(), "placements": {}, "status": {}}))

    def tearDown(self):
        jd.GOALDIR, jd.STATE = self.saved_goaldir, self.saved_state
        self.td.cleanup()

    def _bodies(self):
        """Every message romp injects, by name, rendered from the same synthetic store."""
        nodes = _nodes()
        bodies = {
            "auto-nudge": km.AUTO_NUDGE_TEXT,
            "fork nudge": km.AUTO_NUDGE_STALLED_TEXT,
            "nudge on a hierarchical goal":
                km._followup_body(TOP, None, km.AUTO_NUDGE_TEXT, injected=True, auto=True),
            "fork nudge on a hierarchical goal":
                km._followup_body(TOP, None, km.AUTO_NUDGE_STALLED_TEXT, injected=True, auto=True,
                                  stalled=True),
            "typed follow-up on a summary": km._followup_body(TOP, None, "ship it"),
            # the Continue button's canned reply (the user 2026-08-08) — rides the typed-reply path,
            # rendered exactly as the recipient session will see it
            "continue button": km._followup_body(TOP, None, km.CONTINUE_TEXT),
            "multi-goal bundle": km._nudge_bundle_body([TOP, TOP2], nodes, set()),
            "multi-goal bundle (fork)": km._nudge_bundle_body([TOP, TOP2], nodes, {TOP}),
            # the Merge handoff (the user 2026-08-23): a comment thread's discussion folded back into
            # the parent session — the reader has never heard of romp; it must read as the person's
            # own record of a side discussion
            "comment-thread merge": km._merge_body(
                "the caching layer should be write-through",
                [{"who": "user", "text": "should we make the cache write-through instead?"},
                 {"who": "assistant", "text": "Yes: write-through avoids the stale-read window and "
                                              "the extra invalidation pass; the cost is one write "
                                              "per update, which this workload absorbs."}]),
            # the spend guard's one message to a session over the hourly ceiling (T350, the user 2026-09-11)
            "spend ceiling": km._spend_ceiling_body(1240.0, 1000.0),
            "debt reminder (question)": km._debt_reminder_body(
                [("web", T0, "question", "Which port should the staging server use?")]),
            "debt reminder (handoff)": km._debt_reminder_body(
                [("api", T0, "delegate", "Take over the fixtures backfill and report when it lands.")]),
            "debt reminder (several)": km._debt_reminder_body(
                [("web", T0, "question", "Which port should the staging server use?"),
                 ("api", T0 + 5, "delegate", "Take over the fixtures backfill.")]),
            # the awaiting BACKSTOP (kernel AWAITING_BACKSTOP_TEXT): missed by the 2026-07-24 sweep's
            # index, so it shipped saying "goal" twice and announcing "(Automated re-check…)" until
            # 2026-08-11 — exactly the drift this index exists to catch
            "awaiting backstop": km.AWAITING_BACKSTOP_TEXT,
            # the relayed question (T334): a worker's block toward the peer that delegated its work, sent as the
            # worker's own words; the why is the closer's prose, scrubbed of any clause that speaks romp
            "relayed question": km._relay_body("api", "which client should the exporter target?"),
            "relayed question (procedural why)": km._relay_body("api", jd.NUDGE_BLOCK_WHY),
            "relayed question (with the conversation)": km._relay_body(
                "api", "which client should the exporter target?",
                jd._relay_excerpt([_RELAY_TURN], 1, 4096, who="api")),   # the REAL header and turn rendering, so the
            #                                                             scan reads the words the code emits (the
            #                                                             manager's fourth verdict: a copied header went stale)
            # a comment thread's opening message (the user 2026-08-13): the highlight + comment are
            # the user's own words; the quoting frame around them is romp-authored and scanned here
            "comment thread opener": km._comment_first_message(
                "Cap the retry delay at two minutes.", "Why two minutes and not five?"),
            # the same opener for a passage highlighted in a FILE: the frame names the file instead
            "comment thread opener (file passage)": km._comment_first_message(
                "Cap the retry delay at two minutes.", "Why two minutes and not five?",
                src="~/notes-api/docs/retry.md"),
            # the dashboard-edit trace (the user 2026-08-22): the file viewer saved over a file in this
            # session's tree, and the session is told in the person's voice — never edited under silently
            "edit trace": km._edit_trace_body("/TESTDIR/notes-api/README.md"),
            # the compaction suggestion (the user 2026-08-30): idle + a lot of context → the person
            # suggests a /compact at a natural boundary; /compact is a CLI feature the session
            # already knows, and the thresholds behind the timing are never mentioned
            "compaction suggestion": km._compact_suggest_body("web"),
        }
        # every repeat-nudge variant wears the same voice as the first fire (the user 2026-08-11): the
        # rotation exists so a re-ask doesn't read canned, so a variant that broke the voice rule would
        # defeat its own purpose
        for i, v in enumerate(km.AUTO_NUDGE_VARIANTS, 1):
            bodies["auto-nudge variant %d" % i] = v
        for i, v in enumerate(km.AUTO_NUDGE_STALLED_VARIANTS, 1):
            bodies["fork nudge variant %d" % i] = v
        return bodies

    def test_the_relayed_conversation_fixture_is_rendered_by_the_real_excerpt(self):
        body = self._bodies()["relayed question (with the conversation)"]
        self.assertIn("The conversation this question ends, oldest first: 1 of 1 turn shown.", body)
        self.assertIn("user: start on the exporter", body)
        self.assertIn("(2 tool calls)", body)
        self.assertNotIn("1 of 1 turn.\n", body, "the pre-change header wording is gone from the fixture")

    def test_the_relay_scrub_speaks_this_lists_words(self):
        # the kernel scrubs a relayed question's why by the same vocabulary this file scans for (T334): one list
        self.assertEqual(tuple(w for w, _why in ROMP_WORDS), km.ROMP_VOICE_WORDS)

    def test_no_romp_vocabulary_reaches_the_session(self):
        for name, body in self._bodies().items():
            # THE ONE ALLOWANCE, deliberate and ruling-backed (T212, the user 2026-09-01): a
            # backtick-quoted `romp compact …` COMMAND is practical information the recipient
            # must literally type — an SDK session cannot run /compact, and the session-prompt
            # housekeeping note already gives the name its meaning (the sanctioned precedent).
            # Scoped to the exact command span, never the word: "romp" in PROSE still fails.
            text = re.sub(r"`romp compact[^`]*`", "", prose(body)).lower()
            for word, why in ROMP_WORDS:
                with self.subTest(message=name, word=word):
                    self.assertNotIn(word, text,
                                     "%r speaks romp at the session (%r: %s). Write it as the person "
                                     "it works for asking — see CLAUDE.md, 'Messages we inject into a "
                                     "session'." % (name, word, why))

    def test_the_command_allowance_is_the_span_not_the_word(self):
        # the T212 allowance must never become a whitelist: bare "romp" in prose, or any other
        # romp command, still speaks romp at the session and still fails the scan
        self.assertIn("romp", re.sub(r"`romp compact[^`]*`", "", "romp says hi").lower())
        self.assertIn("romp", re.sub(r"`romp compact[^`]*`", "", "`romp status`").lower())
        self.assertNotIn("romp", re.sub(r"`romp compact[^`]*`", "",
                                        "run `romp compact web` in your shell").lower())

    def test_the_rename_ping_stays_one_clean_mechanics_line(self):
        # the [romp] prefix is the sanctioned mechanics family (the restart notices' shape); past
        # it, the line must speak plainly — no markers (it joins an EXISTING message and would
        # re-author it), no romp nouns, one line
        import os as _os
        sb = load_source("romp_sdk_backend_voice", _os.path.join(BIN, "romp_sdk_backend.py"))
        line = sb.RENAME_NUDGE % "tests"
        self.assertTrue(line.startswith("[romp] "), "the sanctioned mechanics prefix")
        self.assertNotIn("\n", line, "one line")
        self.assertNotIn("<!--", line, "marker-free — it rides inside an existing message")
        body = line.split("]", 1)[1].lower()
        for word, why in ROMP_WORDS:
            self.assertNotIn(word, body, "the ping speaks plainly past its prefix (%r: %s)" % (word, why))
        self.assertIn("renamed", body)
        self.assertIn("'tests'", body, "…and it names the new name itself")

    def test_the_lost_tasks_notice_asks_for_a_check_in_the_persons_voice(self):
        # the lost-background-tasks notice (task_death_notice) is the same [romp]-prefixed mechanics
        # family; past the prefix it speaks plainly, to "you". Since 2026-09-05 it says the tasks were
        # CUT OFF, never that they died: under the per-session scopes a task's shell can outlive the
        # CLI, so the ask is to check whether each still runs before relaunching it. Since 2026-09-06
        # it names the session once (as "you") and says whose process ended (the one that started the
        # tasks) — the earlier wording said "session" twice in one clause and left "its" dangling.
        import os as _os
        sb = load_source("romp_sdk_backend_voice", _os.path.join(BIN, "romp_sdk_backend.py"))
        for tasks in ([{"desc": "watching the CI run"}],
                      [{"desc": "watching the CI run"}, {"desc": "tailing the deploy log"}, {}]):
            text = sb.task_death_notice(tasks)
            prose = text[text.index("[romp]"):]
            self.assertNotIn("<!--", prose, "markers lead, prose follows")
            self.assertNotIn("\n", prose, "one line")
            body = prose.split("]", 1)[1].lower()
            for word, why in ROMP_WORDS:
                self.assertNotIn(word, body, "the notice speaks plainly past its prefix (%r: %s)" % (word, why))
            self.assertIn("%d background task" % len(tasks), body)
            self.assertIn("cut off when the claude process that started", body)
            self.assertNotIn("session", body, "the recipient is \"you\"; the noun appears in neither clause")
            self.assertNotIn(" its claude process", body, "the antecedent-free wording is gone")
            self.assertIn("will never arrive", body)
            self.assertIn("still running before relaunching", body)
            self.assertNotIn("died", body)
            self.assertIn("watching the ci run", body, "the descriptions name what was lost")
        # singular and plural agree throughout; an empty description is skipped, not printed
        self.assertIn("1 background task you had running was cut off when the claude process that started it "
                      "ended (a restart or crash). Its completion notification will never arrive. Check whether it "
                      "is still running before relaunching it; if it isn't needed, carry on.",
                      sb.task_death_notice([{}]))
        three = sb.task_death_notice([{"desc": "a"}, {"desc": "b"}, {}])
        self.assertIn("3 background tasks you had running were cut off when the claude process that started them "
                      "ended (a restart or crash): a; b. Their completion notifications will never arrive. Check "
                      "whether each is still running before relaunching it; if they aren't needed, carry on.", three)
        # the reconnect cause reads as the parenthesis after "ended", with "it" the process that ended
        self.assertIn("ended (a settings switch or a rewind restarted it): a",
                      sb.task_death_notice([{"desc": "a"}], cause=sb.SdkSession._RECONNECT_CAUSE))

    def test_the_untitled_fallback_names_no_romp_object(self):
        # a node with no text still renders SOMETHING; that placeholder must not smuggle in "goal"
        nodes = _nodes()
        nodes[TOP]["text"] = ""
        for name, body in (("bundle", km._nudge_bundle_body([TOP], nodes, set())),):
            self.assertIn("(untitled)", prose(body), name)
            self.assertNotIn("goal", prose(body).lower(), name)

    def test_the_marker_tail_is_exempt_and_still_explains_itself(self):
        # the tail names romp in its markers ON PURPOSE, and its note describes them WITHOUT naming
        # romp — that split is the point, so the test must not have banned it by accident
        body = km._followup_body(TOP, None, "ship it")
        tail = body[body.index("<!--"):]
        self.assertIn("romp-goal-id", tail, "the judge's marker still rides")
        note = tail.split("romp-note:", 1)[1].split("-->", 1)[0]     # the human-readable sentence
        self.assertIn("external tracking system", note)
        self.assertNotIn("romp", note,
                         "the note DESCRIBES the markers without naming the product — naming it would "
                         "explain nothing to a model that has never heard of it")

    def test_the_asks_still_elicit_the_planners_four_verdicts(self):
        # the rule is about VOCABULARY, not content: dropping the labeled reply slots must not drop the
        # question. Each nudge still asks for progress, for what is owed by the user, and permits "drop it".
        for name, body in self._bodies().items():
            # the wrap-up is a stop order, not a status ask; a TYPED follow-up carries the user's OWN
            # words as its body, so there is no romp-authored ask in it to check; the DEBT reminder
            # asks for a reply to a PEER, not a progress report to the user; a comment thread's
            # opener is the user's own comment on a quoted passage — a conversation, never a nudge
            # …and the edit trace is an FYI about something the user already DID (a file changed under
            # the session) — telling, not asking; a status question bolted on would be noise
            # …and the MERGE handoff is a record handed over with direction ("account for it"),
            # never a status ask — bolting a progress question onto it would be noise
            # …and the spend ceiling's message is a STOP order with one question (what was fanning out), not a
            # progress ask: the session is to halt, not to report where things stand
            if name in ("typed follow-up on a summary",
                        "debt reminder (question)", "debt reminder (handoff)",
                        "debt reminder (several)", "comment thread opener",
                        "comment thread opener (file passage)", "edit trace",
                        "comment-thread merge", "compaction suggestion", "spend ceiling",
                        "relayed question", "relayed question (procedural why)",
                        "relayed question (with the conversation)"):
                #        ^ a housekeeping suggestion, not a progress ask: it elicits nothing; and the relayed
                #          question is a WORKER's question to the peer that delegated its work, in the worker's
                #          words, never a progress ask to the user (T334)
                continue
            text = prose(body).lower()
            with self.subTest(message=name):
                self.assertTrue("stand" in text or "what's next" in text or "keep going" in text,
                                "%r no longer asks for progress" % name)
                self.assertIn("from me", text, "%r no longer asks what it needs from the user" % name)


class TheRuleIsWrittenDown(unittest.TestCase):
    def test_claude_md_carries_the_rule_and_its_exceptions(self):
        md = (Path(HERE).parent / "CLAUDE.md").read_text()
        self.assertIn("the agent does not know romp exists", md)
        self.assertIn("No romp nouns in the prose", md)
        self.assertIn("No taxonomy handed over as reply slots", md)
        self.assertIn("tests/test_injected_voice.py", md, "the rule points at its own guardrail")
        # the exceptions are part of the rule: without them someone "fixes" the marker note next
        self.assertIn("SessionStart instruction", md)
        self.assertIn("marker tail", md)
        self.assertIn("housekeeping note", md)


if __name__ == "__main__":
    unittest.main()
