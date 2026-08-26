#!/usr/bin/env python3
"""AskUserQuestion answers for the chat's "answered Claude's question" box, TWO paths:

- AUTHORITATIVE (_ask_fill_answers, via the build_session attach loop): the tool_result's user
  record carries the STRUCTURED answers at top level — toolUseResult = {"questions": [...],
  "answers": {exact-question-text: answer}} (string per question; a LIST of picked labels for
  multiSelect). Filled by exact key, no parsing, so quotes/equals/commas in questions and answers
  survive verbatim.
- FALLBACK (_ask_fill_chosen, old records without a dict toolUseResult): regex-scrape the flat
  output string's `"<q>"="<a>"` pairs. A double-quote inside a question's text breaks that scrape
  (the capture stops at the quote), which is the bug the authoritative path fixes — 13 of 95 real
  answers silently vanished from the answered box before it.

Synthetic fixtures only: placeholder UUIDs, invented questions/answers."""
import json
import os
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_aa", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd


def _blk(q, multi=False, header=None, labels=()):
    return {"question": q, "header": header, "multiSelect": multi,
            "options": [{"label": l, "description": ""} for l in labels], "chosen": []}


def test_single_question_picked_option():
    b = [_blk("Pick a color")]
    km._ask_fill_chosen(b, 'Your questions have been answered: "Pick a color"="Blue"')
    assert b[0]["chosen"] == ["Blue"]


def test_free_text_other_is_kept_verbatim_not_split():
    b = [_blk("Pick a color")]
    km._ask_fill_chosen(b, '"Pick a color"="chartreuse, actually"')
    assert b[0]["chosen"] == ["chartreuse, actually"], "single-select free-text is one value, commas and all"


def test_multiselect_splits_joined_labels():
    # The split must be EARNED: it survives only because the parts name option labels (parseAskRaw's
    # rule, ported) — a real picker block always carries its options, so the fixture does too.
    b = [_blk("Pick features", multi=True, labels=("Alpha", "Beta", "Gamma"))]
    km._ask_fill_chosen(b, '"Pick features"="Alpha, Beta, Gamma"')
    assert b[0]["chosen"] == ["Alpha", "Beta", "Gamma"]


def test_multiselect_single_comma_bearing_label_is_not_shredded():
    # A ONE-label pick whose label itself contains ", " arrives as that exact string. Unguarded, the
    # fallback's split shredded it into bogus fragments (two wrong "Other" rows, lost highlight). An
    # answer that IS an option label is that label, whole — the split is only for genuinely joined picks.
    b = [_blk("Pick a city", multi=True, labels=("Boston, MA", "Denver"))]
    km._ask_fill_chosen(b, '"Pick a city"="Boston, MA"')
    assert b[0]["chosen"] == ["Boston, MA"]


def test_multiselect_comma_bearing_free_text_stays_whole():
    # A free-typed "Other" answer with commas names no label even after splitting — keep it verbatim
    # (parseAskRaw's rule: keep the split only when a part actually names an option label).
    b = [_blk("Pick features", multi=True, labels=("Alpha", "Beta"))]
    km._ask_fill_chosen(b, '"Pick features"="reads, writes, and nothing else"')
    assert b[0]["chosen"] == ["reads, writes, and nothing else"]


def test_multiselect_split_kept_when_a_part_names_a_label():
    # Joined picks that include a free-typed "Other": at least ONE part naming a label keeps the split
    # (parseAskRaw's exact rule — some(), not every(): label parts highlight, the rest render as Other).
    b = [_blk("Pick features", multi=True, labels=("Alpha", "Beta"))]
    km._ask_fill_chosen(b, '"Pick features"="Alpha, my own idea"')
    assert b[0]["chosen"] == ["Alpha", "my own idea"]


def test_multi_question_matches_by_question():
    b = [_blk("First?"), _blk("Second?")]
    km._ask_fill_chosen(b, '"First?"="yes" "Second?"="no"')
    assert b[0]["chosen"] == ["yes"] and b[1]["chosen"] == ["no"]


def test_unanswered_stays_empty():
    b = [_blk("Pending?")]
    km._ask_fill_chosen(b, "")
    assert b[0]["chosen"] == []


# ---------------------------------------------------------------------------
# The AUTHORITATIVE path: _ask_fill_answers fills from toolUseResult's answers map.
# ---------------------------------------------------------------------------

def test_answers_map_string_value_becomes_single_chosen():
    b = [_blk("Pick a color")]
    km._ask_fill_answers(b, {"Pick a color": "Blue"})
    assert b[0]["chosen"] == ["Blue"]


def test_answers_map_list_value_is_the_multiselect_list():
    b = [_blk("Pick features", multi=True)]
    km._ask_fill_answers(b, {"Pick features": ["Alpha", "Gamma"]})
    assert b[0]["chosen"] == ["Alpha", "Gamma"], "a LIST answer (multiSelect) is taken as-is, never re-joined/split"


def test_answers_map_unknown_question_left_empty():
    b = [_blk("Pending?")]
    km._ask_fill_answers(b, {"A different question": "yes"})
    assert b[0]["chosen"] == []


def test_answers_map_quotes_equals_commas_survive_verbatim():
    q = 'Use the "fast" path?'
    ans = 'only "reads", and A="B" stays, commas too'
    b = [_blk(q)]
    km._ask_fill_answers(b, {q: ans})
    assert b[0]["chosen"] == [ans], "exact-key fill: no parsing, nothing garbled"


def test_answers_map_fill_count_is_returned():
    # (#2) The caller latches askAnswerFilled on this return: it must count blocks ACTUALLY filled,
    # not merely that a dict arrived — a keying mismatch fills nothing and must not disarm the
    # regex safety net.
    b = [_blk("Pick a color"), _blk("Second?")]
    assert km._ask_fill_answers(b, {"Pick a color": "Blue"}) == 1
    assert km._ask_fill_answers(b, {"Pick a color": "Blue", "Second?": "no"}) == 2


def test_answers_map_zero_fills_reports_zero():
    b = [_blk("Pending?")]
    assert km._ask_fill_answers(b, {"A different question": "yes"}) == 0
    assert km._ask_fill_answers(b, {}) == 0
    assert km._ask_fill_answers(b, {"Pending?": []}) == 0, "an empty multiSelect list fills nothing"
    assert b[0]["chosen"] == []


NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


class BuildSessionAskAnswer(unittest.TestCase):
    """End-to-end through build_session: the attach loop fills chosen from the record's structured
    toolUseResult (authoritative), and only records WITHOUT it fall back to the output-string regex.
    Fixture mirrors test_teammate_message's discover setup (names + transcript in the munged dir)."""

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
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
                      km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global.md"           # keep a real ~/.claude/CLAUDE.md out of the fixture
        km._read_task_store = lambda fsid, fold=None: []                # no to-do card in the way
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._parse_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
         km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD) = self.saved
        km._parse_cache.clear()
        self.td.cleanup()

    def _iso(self, t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _write(self, questions, output, tool_use_result=None, is_error=False):
        """A minimal ask exchange: prompt → AskUserQuestion tool_use → its tool_result (optionally
        carrying the top-level structured toolUseResult) → a closing reply."""
        tr = {"type": "tool_result", "tool_use_id": "toolu_ask1", "content": output}
        if is_error:
            tr["is_error"] = True
        result_rec = {"type": "user", "timestamp": self._iso(T0 + 20), "uuid": "u2", "parentUuid": "a1",
                      "message": {"role": "user", "content": [tr]}}
        if tool_use_result is not None:
            result_rec["toolUseResult"] = tool_use_result
        recs = [
            {"type": "user", "timestamp": self._iso(T0), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed", "message": {"role": "user", "content": "kick things off"}},
            {"type": "assistant", "timestamp": self._iso(T0 + 10), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": "toolu_ask1", "name": "AskUserQuestion",
                                      "input": {"questions": questions}}]}},
            result_rec,
            {"type": "assistant", "timestamp": self._iso(T0 + 30), "uuid": "a2", "parentUuid": "u2",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}],
                         "stop_reason": "end_turn"}},
        ]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _ask_event(self):
        events = km.build_session(SID, NOW)["events"]
        asks = [e for e in events if e.get("kind") == "tool" and e.get("name") == "AskUserQuestion"]
        self.assertEqual(len(asks), 1)
        return asks[0]

    QUOTED_Q = 'Use the "fast" path?'

    def _two_questions(self):
        return [{"question": self.QUOTED_Q, "header": "Path", "multiSelect": False,
                 "options": [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]},
                {"question": "Deploy target?", "header": "Target", "multiSelect": False,
                 "options": [{"label": "staging", "description": ""}, {"label": "prod", "description": ""}]}]

    def test_quote_bearing_question_custom_answer_fills(self):
        # (a) A double-quote inside the question breaks the output-string scrape (the capture stops at
        # the quote), and in a MULTI-question ask there is no vals[0] rescue — the typed answer vanished.
        custom = "only for reads, not writes"
        self._write(self._two_questions(),
                    'User has responded with the following answers: "%s"="%s" "Deploy target?"="staging"'
                    % (self.QUOTED_Q, custom),
                    {"questions": [q["question"] for q in self._two_questions()],
                     "answers": {self.QUOTED_Q: custom, "Deploy target?": "staging"}})
        blocks = self._ask_event()["askAnswer"]
        self.assertEqual(blocks[0]["chosen"], [custom], "the free-typed answer must not vanish")
        self.assertEqual(blocks[1]["chosen"], ["staging"])

    def test_quote_bearing_question_listed_pick_fills(self):
        # (b) Same break, picked option instead of free text.
        self._write(self._two_questions(),
                    '"%s"="Yes" "Deploy target?"="prod"' % self.QUOTED_Q,
                    {"questions": [], "answers": {self.QUOTED_Q: "Yes", "Deploy target?": "prod"}})
        blocks = self._ask_event()["askAnswer"]
        self.assertEqual(blocks[0]["chosen"], ["Yes"])
        self.assertEqual(blocks[1]["chosen"], ["prod"])

    def test_multiselect_list_answer_is_the_list(self):
        # (c) multiSelect answers arrive as a LIST in the structured record; chosen is that list
        # (render highlights each), never one joined "Alpha, Gamma" string that reads as an Other row.
        qs = [{"question": "Pick features", "header": "Features", "multiSelect": True,
               "options": [{"label": "Alpha"}, {"label": "Beta"}, {"label": "Gamma"}]}]
        self._write(qs, '"Pick features"="Alpha, Gamma"',
                    {"questions": [], "answers": {"Pick features": ["Alpha", "Gamma"]}})
        blocks = self._ask_event()["askAnswer"]
        self.assertEqual(blocks[0]["chosen"], ["Alpha", "Gamma"])
        self.assertIs(blocks[0]["multiSelect"], True,
                      "the block carries multiSelect (documents the list-valued answer; render may want it)")

    def test_plain_prose_matches_what_the_regex_produced(self):
        # (d) No-regression: plain questions answer identically through the authoritative path.
        qs = [{"question": "Pick a color", "multiSelect": False, "options": [{"label": "Blue"}]},
              {"question": "Second?", "multiSelect": False, "options": [{"label": "yes"}, {"label": "no"}]}]
        self._write(qs, '"Pick a color"="Blue" "Second?"="no"',
                    {"questions": [], "answers": {"Pick a color": "Blue", "Second?": "no"}})
        blocks = self._ask_event()["askAnswer"]
        self.assertEqual([b["chosen"] for b in blocks], [["Blue"], ["no"]])

    def test_old_record_without_tooluseresult_falls_back_to_regex(self):
        # (e) Old transcripts carry no structured record — the output-string scrape still fills.
        qs = [{"question": "Pick a color", "multiSelect": False, "options": [{"label": "Blue"}]},
              {"question": "Second?", "multiSelect": False, "options": [{"label": "yes"}, {"label": "no"}]}]
        self._write(qs, '"Pick a color"="Blue" "Second?"="no"', tool_use_result=None)
        blocks = self._ask_event()["askAnswer"]
        self.assertEqual([b["chosen"] for b in blocks], [["Blue"], ["no"]])

    def test_old_record_multiselect_splits_joined_labels(self):
        # (e, multiSelect) The fallback's ", " split needs the block to KNOW it's multiSelect — before
        # the flag was copied onto the block, the split was dead and the picks rendered as one joined
        # quoted "Other" row.
        qs = [{"question": "Pick features", "multiSelect": True,
               "options": [{"label": "Alpha"}, {"label": "Beta"}, {"label": "Gamma"}]}]
        self._write(qs, '"Pick features"="Alpha, Gamma"', tool_use_result=None)
        self.assertEqual(self._ask_event()["askAnswer"][0]["chosen"], ["Alpha", "Gamma"])

    def test_old_record_single_comma_bearing_label_not_shredded(self):
        # (#1, e2e) The fallback path with a ONE-label pick whose label contains ", ": it must render
        # as that label (highlighted), never shredded into two bogus "Other" rows — the label-match
        # guard, through the whole build.
        qs = [{"question": "Pick a city", "multiSelect": True,
               "options": [{"label": "Boston, MA"}, {"label": "Denver"}]}]
        self._write(qs, '"Pick a city"="Boston, MA"', tool_use_result=None)
        self.assertEqual(self._ask_event()["askAnswer"][0]["chosen"], ["Boston, MA"])

    def test_mismatched_answers_map_does_not_disarm_the_fallback(self):
        # (#2, e2e) askAnswerFilled must latch only on an ACTUAL fill: a keying mismatch (a future
        # harness renaming) fills nothing, and latching anyway disarmed the regex safety net — the
        # whole ask rendered pending despite recoverable pairs in the flat string.
        qs = [{"question": "Pick a color", "multiSelect": False, "options": [{"label": "Blue"}]}]
        self._write(qs, '"Pick a color"="Blue"',
                    {"questions": [], "answers": {"A renamed question key": "Blue"}})
        self.assertEqual(self._ask_event()["askAnswer"][0]["chosen"], ["Blue"],
                         "zero fills -> the output-string scrape still recovers the answer")

    def test_empty_answers_map_does_not_disarm_the_fallback(self):
        # (#2, e2e) Same latch, the empty-map shape.
        qs = [{"question": "Pick a color", "multiSelect": False, "options": [{"label": "Blue"}]}]
        self._write(qs, '"Pick a color"="Blue"', {"questions": [], "answers": {}})
        self.assertEqual(self._ask_event()["askAnswer"][0]["chosen"], ["Blue"])

    def test_dismissed_picker_stays_unanswered(self):
        # (f) A dismissed picker: is_error tool_result, toolUseResult a plain STRING — no crash,
        # blocks stay unanswered (the pending look), exactly as before.
        self._write(self._two_questions(), "User dismissed the questions.",
                    tool_use_result="User dismissed the questions.", is_error=True)
        ev = self._ask_event()
        self.assertTrue(ev["isError"])
        self.assertEqual([b["chosen"] for b in ev["askAnswer"]], [[], []])

    def test_answer_with_quotes_and_equals_survives_verbatim(self):
        # (g) The scrape garbles an answer containing quotes/equals; the exact-key fill must not.
        tricky = 'use "romp: fix"; keep A="B" as-is'
        qs = [{"question": "Commit message?", "multiSelect": False, "options": [{"label": "default"}]},
              {"question": "Second?", "multiSelect": False, "options": [{"label": "yes"}]}]
        self._write(qs, '"Commit message?"="%s" "Second?"="yes"' % tricky,
                    {"questions": [], "answers": {"Commit message?": tricky, "Second?": "yes"}})
        blocks = self._ask_event()["askAnswer"]
        self.assertEqual(blocks[0]["chosen"], [tricky])
        self.assertEqual(blocks[1]["chosen"], ["yes"])

    def test_authoritative_fill_skips_the_regex(self):
        # (#2) When the structured record filled chosen, the output-string scrape must STAND DOWN:
        # a single-question ask's vals[0] rescue would otherwise overwrite the real answer with
        # whatever fragment the scrape extracted.
        qs = [{"question": "Pick a color", "multiSelect": False, "options": [{"label": "Blue"}]}]
        self._write(qs, '"Pick a color"="scraped wrong"',
                    {"questions": [], "answers": {"Pick a color": "the real answer"}})
        self.assertEqual(self._ask_event()["askAnswer"][0]["chosen"], ["the real answer"])
