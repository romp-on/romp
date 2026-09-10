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


class InjectedBodiesSpeakAsTheUser(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_goaldir, self.saved_state = jd.GOALDIR, jd.STATE
        jd.GOALDIR, jd.STATE = Path(self.td.name), Path(self.td.name)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 4, "nodes": _nodes(), "placements": {}, "status": {}}))
        # open user todos for the context block below — the same synthetic notes-api world
        km._user_todos_cache.clear()
        km._set_user_todos(True)                     # the feature switch is OFF by default (2026-09-03)
        km._add_user_todo(SID, "Need the auth-scheme decision to wire login — building the open "
                               "routes meanwhile")
        km._add_user_todo(SID, "Need a staging API key before the load test can run")

    def tearDown(self):
        jd.GOALDIR, jd.STATE = self.saved_goaldir, self.saved_state
        self.td.cleanup()
        km._user_todos_cache.clear()

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
            # the reply to a USER TODO (plans/user-todos.md): the todo's own short line anchors the
            # user's answer (`Re: <text> — <reply>`) — the frame is romp-authored and scanned here
            "user-todo answer": km._user_todo_answer_body(
                "Need the auth-scheme decision to wire login — building the open routes meanwhile",
                "Go with the session cookie for now."),
            # the SessionStart context block (plans/user-todos.md slice 3): a resumed/compacted
            # session's open todos as its OWN outstanding notes to the person it works for. Not an
            # injected MESSAGE (it rides additionalContext, costing no turn) but the same veil
            # applies — it must read as the agent's own notes, never a tracking system's; naming
            # withdraw_user_todo is correct (the agent holds that tool)
            "user-todo context block": km._user_todo_context_block(SID),
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

    def test_the_context_block_fixture_is_live(self):
        # the veil scan over an EMPTY block would pass trivially — pin that the rendered block
        # carries both seeded notes and the withdraw invitation, so the scan above sees real words
        block = self._bodies()["user-todo context block"]
        self.assertIn("Need the auth-scheme decision to wire login", block)
        self.assertIn("Need a staging API key", block)
        self.assertIn("withdraw_user_todo", block)

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
            # opener is the user's own comment on a quoted passage — a conversation, never a nudge;
            # a user-todo answer is the user's own reply to a need the agent flagged — same class;
            # the user-todo context block is the agent's OWN notes handed back after context loss —
            # a memory aid with a withdraw invitation, not a status ask
            # …and the edit trace is an FYI about something the user already DID (a file changed under
            # the session) — telling, not asking; a status question bolted on would be noise
            # …and the MERGE handoff is a record handed over with direction ("account for it"),
            # never a status ask — bolting a progress question onto it would be noise
            if name in ("typed follow-up on a summary",
                        "debt reminder (question)", "debt reminder (handoff)",
                        "debt reminder (several)", "comment thread opener", "user-todo answer",
                        "user-todo context block", "edit trace", "comment-thread merge",
                        "compaction suggestion"):
                #        ^ a housekeeping suggestion, not a progress ask — it elicits nothing
                continue
            text = prose(body).lower()
            with self.subTest(message=name):
                self.assertTrue("stand" in text or "what's next" in text or "keep going" in text,
                                "%r no longer asks for progress" % name)
                self.assertIn("from me", text, "%r no longer asks what it needs from the user" % name)


class UserTodoToolDescriptionsKeepTheVeil(unittest.TestCase):
    """The two user-todo postal tools (plans/user-todos.md) describe an obligation to the PERSON
    THE AGENT WORKS FOR, so their descriptions ride the same veil as injected bodies: no romp
    machinery named. (The OTHER postal tools name romp on purpose — the bus is visible tooling
    with the product's name on it; these two must not teach the model a tracking system.)"""

    def test_the_descriptions_carry_no_romp_vocabulary(self):
        pm = load_source("romp_postal_voice", os.path.join(BIN, "romp-postal-service"))
        tools = {t["name"]: t for t in pm.MCP_TOOLS}
        for name in ("add_user_todo", "withdraw_user_todo"):
            self.assertIn(name, tools, "the tool exists to be scanned")
            desc = tools[name]["description"]
            self.assertIn("person you work for", desc, "%s speaks as the person the agent works for" % name)
            for word, why in ROMP_WORDS:
                with self.subTest(tool=name, word=word):
                    self.assertNotIn(word, desc.lower(),
                                     "%s's description speaks romp at the session (%r: %s)" % (name, word, why))
            for prop in tools[name]["inputSchema"]["properties"].values():
                for word, why in ROMP_WORDS:
                    with self.subTest(tool=name, word=word, field=prop):
                        self.assertNotIn(word, str(prop.get("description") or "").lower())

    def test_the_result_texts_carry_no_romp_vocabulary(self):
        # The RESULT texts land in the agent's context exactly as the descriptions do — the
        # tool's answer is read verbatim by the same model the veil protects — so the sweep
        # covers them too: every user-todo branch of _mcp_call is rendered (success, each
        # refusal, an unreachable kernel) and scanned. The shared "Not inside a romp session."
        # identity refusal is out of scope on purpose: it is every postal tool's answer, and
        # the bus names romp deliberately (visible tooling); identity is stubbed so no branch
        # here can reach it.
        pm = load_source("romp_postal_voice_results", os.path.join(BIN, "romp-postal-service"))
        saved = (pm._kernel_post, pm._self_identity, pm._heartbeat)
        canned = {}
        pm._kernel_post = lambda path, body, timeout=4.0: canned.get("res")
        pm._self_identity = lambda: (SID, "api")   # one identity resolution per call: _mcp_call reads _self_identity() before its argument checks
        pm._heartbeat = lambda *a, **k: None
        # the per-install switch (2026-09-03) is OFF by default: turn it on for the live branches,
        # then off again for the two refusals a still-connected session hears
        pm.USER_TODOS_SWITCH.parent.mkdir(parents=True, exist_ok=True)
        pm.USER_TODOS_SWITCH.write_text(json.dumps({"enabled": True, "gt": 1}))
        try:
            results = {}
            canned["res"] = {"ok": True, "todoId": "ut-9f2c1a34"}
            results["add: noted"] = pm._mcp_call("add_user_todo", {"text": "Need the port"})[0]
            results["add: no text"] = pm._mcp_call("add_user_todo", {"text": "  "})[0]
            canned["res"] = None                    # unreachable kernel / non-2xx
            results["add: couldn't save"] = pm._mcp_call("add_user_todo", {"text": "Need the port"})[0]
            results["withdraw: unreachable"] = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})[0]
            results["withdraw: no id"] = pm._mcp_call("withdraw_user_todo", {})[0]
            canned["res"] = {"ok": True}
            results["withdraw: withdrawn"] = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})[0]
            canned["res"] = {"ok": False}
            results["withdraw: no open note"] = pm._mcp_call("withdraw_user_todo", {"id": "ut-deadbeef"})[0]
            # the kernel's account (state / at / owner, 2026-09-07) words the ok:false four ways
            for state in ("answered", "dismissed", "withdrawn"):
                canned["res"] = {"ok": False, "state": state, "at": T0, "owner": True}
                results["withdraw: already " + state] = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})[0]
            canned["res"] = {"ok": False, "state": "unknown", "at": None, "owner": False}
            results["withdraw: not yours"] = pm._mcp_call("withdraw_user_todo", {"id": "ut-deadbeef"})[0]
            canned["res"] = {"ok": False, "state": "unknown", "at": None, "owner": True,
                             "error": "malformed closing stamp on ut-9f2c1a34: resolved=True"}
            results["withdraw: unreadable record"] = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})[0]
            # the two branches added after the first sweep (2026-09-07): the caps refusal, worded at
            # the tool, and the unreadable STORE (owner None — the kernel could not look at all)
            results["add: over the cap"] = pm._mcp_call("add_user_todo", {"text": "Need the port", "detail": "x" * 100_000})[0]
            canned["res"] = {"ok": False, "state": "unknown", "at": None, "owner": None,
                             "error": "the request store is unreadable (see the kernel log)"}
            results["withdraw: unreadable store"] = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})[0]
            pm.USER_TODOS_SWITCH.write_text(json.dumps({"enabled": False, "gt": 2}))
            results["add: switch off"] = pm._mcp_call("add_user_todo", {"text": "Need the port"})[0]
            results["withdraw: switch off"] = pm._mcp_call("withdraw_user_todo", {"id": "ut-9f2c1a34"})[0]
        finally:
            pm._kernel_post, pm._self_identity, pm._heartbeat = saved
            pm.USER_TODOS_SWITCH.unlink()
        # the sweep rendered the real branches, not seven copies of one fallback
        self.assertIn("Noted", results["add: noted"])
        self.assertIn("Withdrawn", results["withdraw: withdrawn"])
        self.assertIn("Nothing changed", results["withdraw: no open note"])
        self.assertIn("Already closed", results["withdraw: already answered"])
        self.assertIn("Already closed", results["withdraw: already dismissed"])
        self.assertIn("Already withdrawn", results["withdraw: already withdrawn"])
        self.assertIn("of yours", results["withdraw: not yours"])
        self.assertIn("Couldn't read the record", results["withdraw: unreadable record"])
        self.assertIn("one line", results["add: over the cap"])
        self.assertIn("Nothing changed", results["withdraw: unreadable store"])
        self.assertNotIn("Couldn't read the record", results["withdraw: unreadable store"], "the store branch, not the record's")
        self.assertIn("turned off on this machine", results["add: switch off"])
        self.assertIn("turned off on this machine", results["withdraw: switch off"])
        for name, text in results.items():
            for word, why in ROMP_WORDS:
                with self.subTest(result=name, word=word):
                    self.assertNotIn(word, text.lower(),
                                     "%s's result speaks romp at the session (%r: %s)" % (name, word, why))


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
