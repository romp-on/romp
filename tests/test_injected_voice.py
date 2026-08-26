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
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = SourceFileLoader("romp_kernel_voice", os.path.join(BIN, "romp-kernel")).load_module()
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
            # a comment thread's opening message (the user 2026-08-13): the highlight + comment are
            # the user's own words; the quoting frame around them is romp-authored and scanned here
            "comment thread opener": km._comment_first_message(
                "Cap the retry delay at two minutes.", "Why two minutes and not five?"),
            # the dashboard-edit trace (the user 2026-08-22): the file viewer saved over a file in this
            # session's tree, and the session is told in the person's voice — never edited under silently
            "edit trace": km._edit_trace_body("/TESTDIR/notes-api/README.md"),
        }
        # every repeat-nudge variant wears the same voice as the first fire (the user 2026-08-11): the
        # rotation exists so a re-ask doesn't read canned, so a variant that broke the voice rule would
        # defeat its own purpose
        for i, v in enumerate(km.AUTO_NUDGE_VARIANTS, 1):
            bodies["auto-nudge variant %d" % i] = v
        for i, v in enumerate(km.AUTO_NUDGE_STALLED_VARIANTS, 1):
            bodies["fork nudge variant %d" % i] = v
        return bodies

    def test_no_romp_vocabulary_reaches_the_session(self):
        for name, body in self._bodies().items():
            text = prose(body).lower()
            for word, why in ROMP_WORDS:
                with self.subTest(message=name, word=word):
                    self.assertNotIn(word, text,
                                     "%r speaks romp at the session (%r: %s). Write it as the person "
                                     "it works for asking — see CLAUDE.md, 'Messages we inject into a "
                                     "session'." % (name, word, why))

    def test_the_rename_ping_stays_one_clean_mechanics_line(self):
        # the [romp] prefix is the sanctioned mechanics family (the restart notices' shape); past
        # it, the line must speak plainly — no markers (it joins an EXISTING message and would
        # re-author it), no romp nouns, one line
        import os as _os
        from importlib.machinery import SourceFileLoader as _L
        sb = _L("romp_sdk_backend_voice", _os.path.join(BIN, "romp_sdk_backend.py")).load_module()
        line = sb.RENAME_NUDGE % "tests"
        self.assertTrue(line.startswith("[romp] "), "the sanctioned mechanics prefix")
        self.assertNotIn("\n", line, "one line")
        self.assertNotIn("<!--", line, "marker-free — it rides inside an existing message")
        body = line.split("]", 1)[1].lower()
        for word, why in ROMP_WORDS:
            self.assertNotIn(word, body, "the ping speaks plainly past its prefix (%r: %s)" % (word, why))
        self.assertIn("renamed", body)
        self.assertIn("'tests'", body, "…and it names the new name itself")

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
            if name in ("typed follow-up on a summary",
                        "debt reminder (question)", "debt reminder (handoff)",
                        "debt reminder (several)", "comment thread opener", "edit trace",
                        "comment-thread merge"):
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
