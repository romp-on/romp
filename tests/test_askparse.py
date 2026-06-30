#!/usr/bin/env python3
"""Tests for bin/romp-askparse — the Python port of chat-view/src/askparse.ts.

Pane-capture fixtures verified against the real Claude Code picker screens
documented in askparse.ts. Ported 1:1 from chat-view/src/askparse.test.ts: same
synthetic fixtures, same expected outputs, same assertions. Synthetic only:
invented prompt text, no real session data.
"""
import os
import re
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
ap = SourceFileLoader("romp_askparse", os.path.join(BIN, "romp-askparse")).load_module()

parse = ap.parse_ask_pane

FOOTER = "Enter to select · ↑/↓ to navigate · Esc to cancel"


class TestAskParse(unittest.TestCase):
    def test_single_select_header_question_options_descriptions_cursor(self):
        pane = "\n".join([
            "",
            "☐ Auth method",
            "Which library should we use?",
            "❯ 1. passport",
            "     battle-tested, callback-style",
            "  2. lucia",
            "     newer, typescript-first",
            "",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(ask["multiSelect"], False)
        self.assertEqual(ask["header"], "Auth method")
        self.assertEqual(ask["question"], "Which library should we use?")
        self.assertEqual(len(ask["options"]), 2)
        self.assertEqual(
            [[o["n"], o["label"], o.get("desc"), o["selected"]] for o in ask["options"]],
            [[1, "passport", "battle-tested, callback-style", True],
             [2, "lucia", "newer, typescript-first", False]],
        )
        self.assertEqual(ask["cursor"], 1)
        self.assertEqual(ask["cursorFound"], True)

    def test_cursor_on_later_row_sig_changes_when_cursor_moves(self):
        def mk(cur):
            return "\n".join([
                "Pick one",
                ("❯ " if cur == 1 else "  ") + "1. alpha",
                ("❯ " if cur == 2 else "  ") + "2. beta",
                FOOTER,
            ])
        a = parse(mk(1))
        b = parse(mk(2))
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(b["cursor"], 2)
        self.assertNotEqual(a["sig"], b["sig"])

    def test_no_cursor_captured_cursorfound_false_defaults_to_first(self):
        ask = parse("\n".join(["Pick one", "  1. alpha", "  2. beta", FOOTER]))
        self.assertIsNotNone(ask)
        self.assertEqual(ask["cursorFound"], False)
        self.assertEqual(ask["cursor"], 1)

    def test_multi_select_selection_screen_tab_bar_checkboxes(self):
        pane = "\n".join([
            "←  ☒ Toppings  ✔ Submit  →",
            "Which toppings do you want?",
            "❯ 1. [✔] Pizza",
            "  2. [ ] Sushi",
            "  3. [✔] Salad",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "multi")
        self.assertEqual(ask["multiSelect"], True)
        self.assertEqual(ask["header"], "Toppings")
        self.assertEqual(
            [[o["label"], o.get("checked")] for o in ask["options"]],
            [["Pizza", True], ["Sushi", False], ["Salad", True]],
        )

    def test_multi_select_submit_screen_no_footer_chosen_submit_row(self):
        pane = "\n".join([
            "←  ☒ Toppings  ✔ Submit  →",
            "Review your answers",
            " ● Which toppings do you want?",
            "   → Pizza, Salad",
            "Ready to submit your answers?",
            "❯ 1. Submit answers",
            "  2. Cancel",
            "",
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "submit")
        self.assertEqual(ask["question"], "Which toppings do you want?")
        self.assertEqual(ask["chosen"], ["Pizza", "Salad"])
        self.assertEqual(ask["options"][0]["label"], "Submit answers")
        self.assertEqual(ask["cursor"], 1)

    def test_multi_question_wizard_tab_submit_bar_checkboxless_is_single(self):
        # verbatim shape of a captured AskUserQuestion pane: two question tabs +
        # ✔ Submit; rows have NO checkboxes (Enter picks and advances). The tab
        # bar must NOT make this "multi".
        pane = "\n".join([
            "────────────────────────────────────────",
            "←  ☐ Color  ☐ Size  ✔ Submit  →",
            "",
            "Favorite color?",
            "",
            "❯ 1. Red",
            "     The color red.",
            "  2. Green",
            "     The color green.",
            "  3. Blue",
            "     The color blue.",
            "  4. Type something.",
            "────────────────────────────────────────",
            "  5. Chat about this",
            "",
            "Enter to select · Tab/Arrow keys to navigate · Esc to cancel",
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(ask["multiSelect"], False)
        self.assertEqual(ask["question"], "Favorite color?")
        self.assertEqual(ask["header"], "Color")  # first unanswered ☐ tab
        self.assertEqual(len(ask["options"]), 5)
        self.assertEqual(ask["options"][0]["label"], "Red")
        self.assertEqual(ask["options"][3]["label"], "Type something.")
        self.assertEqual(ask["options"][4]["label"], "Chat about this")
        self.assertEqual(ask["cursor"], 1)
        self.assertEqual(ask["cursorFound"], True)

    def test_multi_question_wizard_second_tab_first_open_tab_is_header(self):
        pane = "\n".join([
            "←  ☒ Color  ☐ Size  ✔ Submit  →",
            "",
            "Pick a size?",
            "",
            "❯ 1. Small",
            "  2. Large",
            "  3. Type something.",
            "",
            "Enter to select · Tab/Arrow keys to navigate · Esc to cancel",
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(ask["header"], "Size")
        self.assertEqual(ask["question"], "Pick a size?")

    def test_multiselect_question_new_tab_bar_style_still_multi(self):
        # verbatim shape: single multiSelect question — ☐ tab, [ ] rows
        pane = "\n".join([
            "←  ☐ Toppings  ✔ Submit  →",
            "",
            "Pick toppings?",
            "",
            "❯ 1. [ ] Cheese",
            "  Add cheese.",
            "  2. [✔] Mushroom",
            "  Add mushrooms.",
            "  3. [ ] Type something",
            "",
            "Enter to select · ↑/↓ to navigate · Esc to cancel",
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "multi")
        self.assertEqual(ask["header"], "Toppings")
        self.assertEqual(
            [[o["label"], o.get("checked")] for o in ask["options"]],
            [["Cheese", False], ["Mushroom", True], ["Type something", False]],
        )

    def test_multi_question_review_screen_every_pair_collected(self):
        # verbatim shape of the wizard's Submit tab
        pane = "\n".join([
            "←  ☒ Color  ☒ Size  ✔ Submit  →",
            "",
            "Review your answers",
            "",
            " ● Favorite color?",
            "   → Red",
            " ● Pick a size?",
            "   → Extra medium",
            "",
            "Ready to submit your answers?",
            "",
            "❯ 1. Submit answers",
            "  2. Cancel",
            "",
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "submit")
        self.assertEqual(ask["pairs"], [
            {"q": "Favorite color?", "a": "Red"},
            {"q": "Pick a size?", "a": "Extra medium"},
        ])
        self.assertEqual(ask["chosen"], ["Red", "Extra medium"])
        self.assertIsNone(ask["question"])  # multi-question: no single question line
        self.assertEqual(ask["options"][0]["label"], "Submit answers")

    def test_earlier_prose_numbering_not_swallowed_into_option_block(self):
        pane = "\n".join([
            "Here is my plan:",
            "1. refactor the parser",
            "2. add tests",
            "Some prose in between that is not part of a picker.",
            "Do you want to proceed?",
            "❯ 1. Yes, proceed",
            "  2. No, revise it",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(len(ask["options"]), 2, "plan numbering must not join the option block")
        self.assertEqual(ask["options"][0]["label"], "Yes, proceed")
        # nearby prose (up to 8 lines) is folded into the question — pin that the
        # real ask is its tail, not that the prose is excluded
        self.assertTrue(ask["question"].endswith("Do you want to proceed?"))

    def test_non_picker_screens_parse_to_null(self):
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("just some assistant prose\nand more prose"))
        # a footer with no numbered options above it
        self.assertIsNone(parse("\n".join(["type your answer below", FOOTER])))

    def test_path_c_footerless_confirmation_parses_answered_copy_does_not(self):
        # verbatim shape of a captured pane: no key-hint footer, and the picker
        # replaces the composer + status line — options are the last content
        picker = "\n".join([
            "⏺ Some prior output from the conversation.",
            "",
            "❯ /model fable",
            "────────────────────────────────────────",
            "  Switch model?",
            "  Your next response will be slower and use more tokens",
            "",
            "  This conversation is cached for the current model. Switching to Fable 5",
            "  means the full history gets re-read on your next message.",
            "",
            "  ❯ 1. Yes, switch to Fable 5",
            "    2. No, go back",
            "",
        ])
        ask = parse(picker)
        self.assertIsNotNone(ask, "footer-less confirmation must parse")
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(len(ask["options"]), 2)
        self.assertEqual(ask["options"][0]["label"], "Yes, switch to Fable 5")
        self.assertEqual(ask["cursor"], 1)
        self.assertIn("Switch model?", ask["question"])
        # the same picker text ABOVE a restored composer (= answered) is scrollback
        answered = picker + "\n────────────\n❯ \n────────────\n  ctx:2%   Fable 5 high   /tmp/x\n  ⏵⏵ accept edits on (shift+tab to cycle)"
        self.assertIsNone(parse(answered))
        # a numbered list in plain output with a composer beneath stays null too
        list_output = "\n".join([
            "⏺ Here are your choices:",
            "  ❯ 1. First thing",
            "    2. Second thing",
            "",
            "  ctx:5%   Fable 5 high   /tmp/x",
        ])
        self.assertIsNone(parse(list_output))

    def test_path_c_spaced_dash_rule_caps_question_no_transcript_bleed(self):
        # The native /effort (and /model) confirmation is footer-less (PATH C).
        # Claude Code separates it from the prior turn with a SPACED dashed rule
        # ("─ ─ ─ …"), not a solid run. RULE_RE must treat that as a rule so the
        # upward question walk stops there — otherwise it climbs into the chat
        # transcript above and bleeds unrelated prose into the question (the bug
        # the user hit). Synthetic prose; no real session data.
        picker = "\n".join([
            "⏺ Some unrelated prior assistant prose that must NOT be captured,",
            "  the kind of sentence that wraps across a couple of lines here.",
            "─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─",
            "  Change effort level?",
            "  Your next response will be slower and use more tokens",
            "",
            "  This conversation is cached for the current effort level.",
            "  Switching to xhigh means the full history gets re-read.",
            "",
            "  ❯ 1. Yes, switch to xhigh",
            "    2. No, go back",
            "",
        ])
        ask = parse(picker)
        self.assertIsNotNone(ask, "footer-less confirmation must parse")
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(len(ask["options"]), 2)
        self.assertIn("Change effort level?", ask["question"])
        # the spaced rule must have capped the walk — no transcript bleed
        self.assertNotIn("prior assistant prose", ask["question"])
        self.assertNotIn("wraps across", ask["question"])

    def test_side_by_side_preview_box_captured_kept_out_of_labels(self):
        # An AskUserQuestion whose options carry a `preview` renders the option
        # list on the LEFT and a bordered diagram box on the RIGHT, on the SAME
        # pane rows. Invented content — no real session data.
        W = 30

        def row(left, box):
            return left.ljust(W) + box

        pane = "\n".join([
            "☐ Deploy strategy",
            "Which rollout should the canary use?",
            row("❯ 1. Blue-green",          "╭─ Topology ───────────────╮"),
            row("     instant cutover",      "│ inbound → LB → [a] (live) │"),
            row("  2. Rolling",              "│           ↘ [b] (staged)  │"),
            row("     gradual, N at a time", "╰───────────────────────────╯"),
            "",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask, "preview-bearing picker must still parse")
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(len(ask["options"]), 2)
        self.assertEqual(ask["options"][0]["label"], "Blue-green")
        self.assertEqual(ask["options"][1]["label"], "Rolling")
        self.assertEqual(ask["options"][0].get("desc"), "instant cutover")
        self.assertEqual(ask["options"][1].get("desc"), "gradual, N at a time")
        self.assertEqual(ask["cursor"], 1)
        self.assertEqual(ask["cursorFound"], True)
        # The preview must stay OUT of the options/question/header (no garble)…
        import json
        opt_text = json.dumps({"q": ask["question"], "h": ask["header"], "opts": ask["options"]})
        for leak in ["Topology", "LB", "staged", "↘", "│", "╭", "╰"]:
            self.assertNotIn(leak, opt_text, 'preview text "%s" leaked into the options' % leak)
        # …and be captured VERBATIM in ask.preview.
        self.assertTrue(ask["preview"], "the preview box must be captured into ask.preview")
        self.assertRegex(ask["preview"], r"╭─ Topology ─+╮")
        self.assertRegex(ask["preview"], r"│ inbound → LB → \[a\] \(live\) │")
        self.assertRegex(ask["preview"], r"↘ \[b\] \(staged\)")
        self.assertRegex(ask["preview"], r"╰─+╯")
        # it carries the border verbatim and never bleeds into the option column
        self.assertNotIn("Blue-green", ask["preview"])
        self.assertNotIn("Rolling", ask["preview"])

    def test_preview_is_the_box_only_no_clipped_tails_of_other_lines(self):
        # REGRESSION (the user 2026-06-16): the preview box is drawn to the RIGHT of the options, but
        # the WRAPPED QUESTION above it and the FOOTER below it also extend past the box's left-border
        # column. The old extractor sliced EVERY pane line at that column, bleeding those lines'
        # left-clipped TAILS into ask.preview — the user saw garbled half-lines. The preview must be
        # the BOX'S OWN ROWS only. Invented content — no real session data.
        W = 30

        def row(left, box):
            return left.ljust(W) + box

        pane = "\n".join([
            "☐ Strategy",
            "A long question that wraps and bleeds well past the preview box border column over here",
            row("❯ 1. Alpha", "╭─ preview ────────────────╮"),
            row("     first",  "│ a code line in the box   │"),
            row("  2. Beta",   "│ another box content line │"),
            row("     second", "╰──────────────────────────╯"),
            "",
            "Enter to select · ↑/↓ to navigate · Esc to cancel",
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        pv = ask["preview"]
        self.assertTrue(pv, "the box must still be captured")
        self.assertRegex(pv, r"╭─ preview ─+╮")
        self.assertRegex(pv, r"│ a code line in the box +│")
        self.assertRegex(pv, r"╰─+╯")
        # NO bled tails from the wrapped question or the footer
        for leak in ["bleeds", "border column", "over here", "navigate", "Esc to cancel", "select"]:
            self.assertNotIn(leak, pv, 'preview leaked an unrelated line: "%s"' % leak)
        # exactly the box's 4 rows
        self.assertEqual(len(pv.splitlines()), 4)

    def test_preview_captured_per_focused_option_rekeys_sig(self):
        # Moving the cursor swaps which option's box the TUI draws; a re-capture
        # must surface the new box AND change sig. Synthetic boxes.
        W = 22

        def row(l, b):
            return l.ljust(W) + b

        def pane(box):
            return "\n".join([
                "Which layout?",
                row("❯ 1. Stacked", box[0]),
                row("  2. Columns", box[1]),
                row("", box[2]),
                row("", box[3]),
                "",
                FOOTER,
            ])
        a = parse(pane(["╭──────╮", "│ A    │", "│      │", "╰──────╯"]))
        b = parse(pane(["╭──────╮", "│ B  B │", "│ B    │", "╰──────╯"]))
        self.assertTrue(a["preview"] and b["preview"], "both captures carry a preview")
        self.assertRegex(a["preview"], r"│ A {4}│")
        self.assertRegex(b["preview"], r"│ B {2}B │")
        self.assertNotEqual(a["preview"], b["preview"])
        self.assertNotEqual(a["sig"], b["sig"])
        self.assertEqual([o["label"] for o in a["options"]], ["Stacked", "Columns"])  # options unaffected


# Queued-message detection moved OUT of this pane parser to an EVENT-BASED reader in bin/romp-kernel
# (_pending_queued, folding the transcript's queue-operation records). Its tests live in test_kernel.py
# (TestPendingQueued); the old pane-scrape parse_queued + its tests were removed (the user 2026-06-16).


class TestDiffParsing(unittest.TestCase):
    """Diff/body/planBody extraction from Edit/Write/NotebookEdit/ExitPlanMode permission panes."""

    def test_edit_permission_diff_extracted_question_stays_clean(self):
        fence = "╌" * 40
        pane = "\n".join([
            "─" * 40,
            " Edit file",
            " demo.txt",
            fence,
            " 1  context line stays",
            " 2 -old line goes away",
            " 2 +new line takes its place",
            " 3  trailing context",
            fence,
            " Do you want to make this edit to demo.txt?",
            " ❯ 1. Yes",
            "   2. Yes, allow all edits during this session (shift+tab)",
            "   3. No",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["kind"], "single")
        self.assertEqual(ask["question"], "Do you want to make this edit to demo.txt?",
                         "diff must not bleed into the question")
        self.assertEqual(ask["diff"],
                         "  context line stays\n-old line goes away\n+new line takes its place\n  trailing context")
        self.assertEqual(len(ask["options"]), 3)

    def test_edit_file_header_captured_above_diff(self):
        fence = "╌" * 40
        pane = "\n".join([
            "─" * 40,
            " Edit file",
            " ../../tmp/demo/notes.md",
            " This will modify /tmp/demo/notes.md (outside working directory) via a symlink",
            "",
            fence,
            " 1  context",
            " 2 -old",
            " 2 +new",
            fence,
            " Do you want to make this edit to notes.md?",
            " ❯ 1. Yes",
            "   2. No",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["question"], "Do you want to make this edit to notes.md?")
        self.assertEqual(
            ask["fileHead"],
            "Edit file\n../../tmp/demo/notes.md\n"
            "This will modify /tmp/demo/notes.md (outside working directory) via a symlink",
        )

    def test_edit_blank_diff_lines_stay_own_rows(self):
        fence = "╌" * 40
        pad = " " * 30
        pane = "\n".join([
            fence,
            " 3  intro paragraph." + pad,
            " 4  ",                                  # blank line, number only
            " 5 -## Section A" + pad,
            " 5 +## Section A (revised)" + pad,
            " 6  - item one",                        # leading-dash bullet = context
            " 7 +- item three" + pad,
            " 8  ",                                  # another blank
            " 9  ## Section B",
            fence,
            " Do you want to make this edit to notes.md?",
            " ❯ 1. Yes",
            "   2. No",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        rows = ask["diff"].split("\n")
        self.assertFalse(any(re.search(r"\b[0-9]\s*$", l) for l in rows),
                         "a line number leaked into content:\n%s" % ask["diff"])
        self.assertEqual(rows[0], "  intro paragraph.", "no trailing '4' folded in")
        self.assertEqual(rows[1], "  ", "blank line is its own empty context row")
        self.assertEqual(rows[2], "-## Section A")
        self.assertEqual(rows[3], "+## Section A (revised)")
        self.assertEqual(rows[4], "  - item one", "leading-dash bullet is context, not a deletion")
        self.assertEqual(rows[5], "+- item three", "no trailing '8' folded in")

    def test_write_new_file_no_marker_column(self):
        fence = "╌" * 40
        pane = "\n".join([
            "─" * 40,
            " Create file",
            " notes.md",
            fence,
            " 1 ---",
            " 2 title: Notes",
            " 3 ---",
            " 4 # Heading",
            " 5 - first bullet",
            " 6 - second bullet",
            " 7 Some closing prose.",
            fence,
            " Do you want to create notes.md?",
            " ❯ 1. Yes",
            "   2. No",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["question"], "Do you want to create notes.md?")
        expected = "\n".join([
            "+---", "+title: Notes", "+---", "+# Heading",
            "+- first bullet", "+- second bullet", "+Some closing prose.",
        ])
        self.assertEqual(ask["diff"], expected,
                         "every new-file line is an addition; leading '-' is content, not a deletion")
        self.assertFalse(any(l.startswith("-") for l in ask["diff"].split("\n")),
                         "no phantom deletions")

    def test_notebook_edit_rounded_box_diff(self):
        bx = lambda s: "│ " + s + "                    │"
        pane = "\n".join([
            "─" * 40,
            " Edit notebook",
            " This will modify /tmp/demo/demo.ipynb (outside working directory) via a symlink",
            "",
            "╭" + "─" * 40 + "╮",
            bx("/tmp/demo/demo.ipynb"),
            bx("Replace cell contents for cell cell-0"),
            bx(""),
            bx(' 1 -print("hello")'),
            bx(" 1   No newline at end of file"),
            bx(" 2 +import math"),
            bx(" 3 +"),
            bx(" 4 +for n in range(5):"),
            "╰" + "─" * 40 + "╯",
            " Do you want to make this edit to demo.ipynb?",
            " ❯ 1. Yes",
            "   2. No",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["question"], "Do you want to make this edit to demo.ipynb?")
        rows = ask["diff"].split("\n")
        self.assertEqual(rows[0], '-print("hello")')
        self.assertEqual(rows[2], "+import math")
        self.assertIn("+for n in range(5):", rows)
        # no box-drawing characters in parsed output
        self.assertFalse(re.search(r"[│╭╮╰╯]", ask["diff"]),
                         "box borders must be stripped from the diff")
        self.assertFalse(re.search(r"[│╭╮╰╯]", ask["fileHead"]),
                         "box borders must be stripped from the file header")
        # file header carries the label, warning, in-box path and cell subtitle
        self.assertIn("Edit notebook", ask["fileHead"])
        self.assertIn("/tmp/demo/demo.ipynb", ask["fileHead"])
        self.assertIn("Replace cell contents for cell cell-0", ask["fileHead"])

    def test_exit_plan_mode_boxed_plan_is_markdown(self):
        bx = lambda s: "│ " + s + "        │"
        pane = "\n".join([
            "─" * 40,
            " Ready to code?",
            " Here is Claude's plan:",
            "╭" + "─" * 40 + "╮",
            bx("## Plan"),
            bx(""),
            bx("1. First do a thing"),
            bx("   - a nested detail"),
            bx("2. Then the next thing"),
            "╰" + "─" * 40 + "╯",
            " Would you like to proceed?",
            " ❯ 1. Yes, and auto-accept edits",
            "   2. Yes, and manually approve edits",
            "   3. No, keep planning",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["question"], "Would you like to proceed?")
        self.assertNotIn("diff", ask, "a plan is not a diff")
        self.assertIn("planBody", ask)
        self.assertIn("## Plan", ask["planBody"], "heading preserved")
        self.assertIn("   - a nested detail", ask["planBody"], "nested-list indentation preserved")
        self.assertFalse(re.search(r"[│╭╮╰╯]", ask["planBody"]),
                         "box borders stripped from the plan")
        self.assertEqual(len(ask["options"]), 3)
        self.assertEqual(ask["options"][0]["label"], "Yes, and auto-accept edits")

    def test_plain_permission_prompt_has_no_diff(self):
        pane = "\n".join([
            " Do you want to proceed?",
            " ❯ 1. Yes",
            "   2. No",
            FOOTER,
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertNotIn("diff", ask)
        self.assertEqual(ask["question"], "Do you want to proceed?")

    def test_webfetch_permission_body_extracted(self):
        pane = "\n".join([
            "─" * 40,
            " Fetch",
            "",
            '   url: "https://example.com", prompt: "What is the heading?"',
            "   Claude wants to fetch content from example.com",
            "",
            " Do you want to allow Claude to fetch this content?",
            " ❯ 1. Yes",
            "   2. Yes, and don't ask again for example.com",
            "   3. No, and tell Claude what to do differently (esc)",
        ])
        ask = parse(pane)
        self.assertIsNotNone(ask)
        self.assertEqual(ask["question"],
                         "Do you want to allow Claude to fetch this content?",
                         "detail must not bleed into question")
        self.assertEqual(
            ask["body"],
            'url: "https://example.com", prompt: "What is the heading?"\n'
            "Claude wants to fetch content from example.com",
        )
        self.assertNotIn("diff", ask)
        self.assertEqual(len(ask["options"]), 3)


if __name__ == "__main__":
    unittest.main()
