#!/usr/bin/env python3
"""T323 stage 4a: every reader of an atom's BODY (message, toolUseResult, skillMd) in the kernel and the judges is one the
audit named, and each either hydrates first (em.hydrate) or reads raw transcript records or live SDK atoms, which are
never lazy. A new consumer that reads a body from a parsed tree without hydrating trips the loud sentinel at run time;
this test trips it at review time: any body-reading site outside the audited functions fails here with its line.
Facts only: the audit of 2026-09-11 grouped the sites by walk scope; the names below are that list."""
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
KERNEL = os.path.join(os.path.dirname(HERE), "kernel")

BODY_READ = re.compile(r'\.get\("message"\)|\["message"\]|"toolUseResult"|"skillMd"')

# kernel.py: the audited readers. Hydrating leaves (the comment names the stage), whole-turn hydrators, and readers of raw
# transcript records or live atoms (never lazy).
KERNEL_ALLOWED = {
    # leaves that hydrate the atom(s) they read (T358 moved _atom_prose_chars, _seg_anchors, _seg_jump, _seg_last_text and
    # _seg_mids to the scalar readers below: they read no body at all now)
    "_atom_md", "_atom_user_text", "_atom_user_texts", "_interrupt_cause", "_seg_prompt", "_open_turn_progress",
    "_seg_of_tool_uses", "_fold_tasks_turn", "_turn_landed", "_interrupt_settle",
    "_artifacts_walk",                                  # the Artifacts pane's walk hydrates the whole session first (plans/artifacts-pane.md, 2026-09-19)
    # the chat build hydrates the turns it renders before its loops
    "build_session",
    # readers of raw transcript records (jsonl rows), never atoms
    "_last_assistant_report", "_comment_prose_record", "_comment_cut_target", "_comment_msg_text", "_thread_messages",
    "_undelivered_wake_tail", "_gist_step", "_launch_ids_step", "_launch_step", "_api_error_pass", "_session_meta_step",
    "_transcript_tok_rows", "_rewind_target", "_subagent_meta_map", "_read_task_output",
    "_spend_file_rows",                                 # the spend guard prices the record cache's raw jsonl rows (T350)
    # live SDK atoms and echoes (constructed in-process, never lazy)
    "_merge_live_atoms", "_interrupt_marks_atoms", "_stamp_agents", "_ask_fill_answers", "_ask_fill_chosen", "_patch_rows",
    "_claudemd_paths", "_stamp_steps", "_hydrate_postal", "build_subagent", "_agent_alive",
}
EVENT_MODEL_ALLOWED = {
    # the emit and the adapter build atoms from raw records (never lazy); the readers of atoms hydrate first
    "FileAdapter", "_emit_state", "_chrono", "synthesize_orphans", "synthesize_idle", "is_interrupt_record", "_is_opener",
    "_turn_id", "segment_turns", "_finalize_turn", "_segment_id", "segments", "_seam_real_work", "split_segment",
    "_text_hash8", "_stop_reason", "_has_text", "_machine_written", "_lazy_of", "_atom_kind", "_atom_scalars", "_hydrate_one",
    "_prose_chars", "atom_mids", "atom_is_settle",     # scalar-first readers (T358): the lazy marker answers, the body only for a resident atom
    "hydrate", "is_lazy", "atom_tool_uses", "atom_tool_results", "atom_model", "asm_checkpoint_write", "declared_plan",
    "plan_atoms",                                       # reads blocks only of atoms with no lazy marker; a lazy one answers from tu/tr
    "_pre_tree_identity", "_restore_prefix_atoms", "_asm_heal", "_asm_full", "_asm_fold", "_asm_restore",
    # record-level helpers over jsonl rows and postal/state rows
    "_norm_message", "_content", "_text_of", "author_of", "_record_origin", "_is_tool_result", "_scan_bg_tasks", "_bg_step",
    "_bg_finish", "scan_bg_tasks_cached", "task_store_plan", "_load_postal_index", "postal_pairs", "injected_source",
    "strip_harness_preamble", "parse_teammate_message", "_absorbed", "_absorbed_atom", "_landing_t", "chain_verdicts",
    "_select_eclipsed_chains", "kept_uuids", "landed_text_uuids", "_adopt_detached_compactions", "_repair_compaction_stitches",
    "_stitch_resume_forks", "active_path", "_prepass", "_emit_fold", "_ingest", "_asm_gates", "_asm_serve", "_assemble", "parse_session",
    "chain_membership", "file_rewound", "_membership_of", "_seed_from_doc", "_entry_current", "_carry_encode", "_carry_decode",
    "_LazyBody", "_LazyBody._refuse", "_Unhydrated", "_ckpt_encode", "_ckpt_decode", "_fold_eof_fragment", "_trailing_record",
    "_atom_line", "_dump",                              # the module's own --test dump over a whole parse (never a restored tree)
    "fold_records", "_read_jsonl_entry_unlocked", "_read_jsonl_entry", "_scan_jsonl_bytes", "resume_fork_links",
    "_lineage_closure", "_load_states", "_read_jsonl", "_read_jsonl_incremental", "_tail_read", "_pinned_entry",
}
SDK_BACKEND_ALLOWED = None        # the backend builds live atoms and reads raw records only: every site allowed, listed for the record

JUDGE_ALLOWED = {
    "_atom_text", "_unit_text", "_seg_launches", "_human_prompt_record", "_awaiting_bg_hold",   # _has_asst_work reads scalars (T358)
    "_relay_turn_text",   # the relayed question's conversation excerpt (T334 follow-on): hydrates the atom it renders
    "_atom_parts",        # the PR-ref scan's block splitter: its one caller _seg_pr_refs hydrates the segment first
    # raw records, the states log, captions
    "transcript_head", "_bg_step", "_bg_unresolved",
    "_skill_load_index",                                # the skill-load boot pass reads raw jsonl rows it json.loads itself (T333)
}


def _enclosing(tree, lineno):
    """The module-level function holding `lineno` (a closure inside an audited function is that function's read)."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.lineno <= lineno <= getattr(node, "end_lineno", node.lineno):
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.lineno <= lineno <= sub.end_lineno:
                        return node.name + "." + sub.name
                return node.name
            return node.name
    return "<module>"


def _sites(path):
    src = open(path).read()
    tree = ast.parse(src)
    out = []
    in_doc = False
    for i, line in enumerate(src.split("\n"), start=1):
        stripped = line.strip()
        if stripped.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        code = line.split("#", 1)[0]
        if BODY_READ.search(code):
            out.append((i, _enclosing(tree, i), code.strip()[:110]))
    return out


class BodyReadersAreAudited(unittest.TestCase):
    def _check(self, name, allowed):
        sites = _sites(os.path.join(KERNEL, name))
        self.assertTrue(sites, "the scan finds body reads in %s" % name)
        strays = [(ln, fn, code) for ln, fn, code in sites if fn not in allowed]
        self.assertEqual(strays, [], "body reads outside the audited functions in %s (hydrate first, or add the function to the "
                                     "audited list with its reason):\n%s" % (name, "\n".join("  %s:%d %s: %s" % (name, ln, fn, code) for ln, fn, code in strays)))

    def test_kernel_body_readers_are_the_audited_ones(self):
        self._check("kernel.py", KERNEL_ALLOWED)

    def test_judge_body_readers_are_the_audited_ones(self):
        self._check("judge.py", JUDGE_ALLOWED)

    def test_event_model_body_readers_are_the_audited_ones(self):
        """The event model's own readers of atom bodies (the seam split's real-work test, the declared plan) hydrate; its
        many other message reads are over raw records, which are never lazy, and are listed as such."""
        sites = _sites(os.path.join(KERNEL, "event_model.py"))
        strays = [(ln, fn, code) for ln, fn, code in sites if fn not in EVENT_MODEL_ALLOWED and not fn.startswith("FileAdapter")]
        self.assertEqual(strays, [], "body reads outside the audited functions in event_model.py:\n%s"
                         % "\n".join("  event_model.py:%d %s: %s" % s for s in strays))
        src = open(os.path.join(KERNEL, "event_model.py")).read()
        for fn in ("_seam_real_work", "declared_plan"):
            i = src.index("def %s(" % fn)
            body = src[i:src.index("\ndef ", i + 1)]
            self.assertIn("hydrate(", body, "%s hydrates before it reads" % fn)
            self.assertLess(body.index("hydrate("), body.index('.get("message")'), "%s hydrates first" % fn)

    def test_every_hydrating_leaf_calls_hydrate_before_its_read(self):
        """The leaves the audit named hydrate at the top of their body: the call sits before any body read."""
        for name, fns in (("kernel.py", ["_atom_md", "_atom_user_text", "_atom_user_texts", "_seg_prompt", "_open_turn_progress",
                                         "_fold_tasks_turn", "_turn_landed"]),
                          ("judge.py", ["_atom_text", "_unit_text", "_seg_launches", "_human_prompt_record", "_relay_turn_text"])):
            src = open(os.path.join(KERNEL, name)).read()
            tree = ast.parse(src)
            defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
            lines = src.split("\n")
            for fn in fns:
                node = defs[fn]
                body = "\n".join(lines[node.lineno - 1:node.end_lineno])
                first_read = next((i for i, l in enumerate(body.split("\n")) if BODY_READ.search(l.split("#", 1)[0])), None)
                hyd = next((i for i, l in enumerate(body.split("\n")) if "em.hydrate(" in l), None)
                self.assertIsNotNone(hyd, "%s.%s hydrates" % (name, fn))
                if first_read is not None:
                    self.assertLess(hyd, first_read, "%s.%s hydrates before its first body read" % (name, fn))



class ScalarReadersReadNoBody(unittest.TestCase):
    """T358: the per-cycle walkers over a session's history read scalars, never a body. The kernel's and the judges'
    segment readers contain no body read and no hydrate call (a lazy atom answers from its marker through the event
    model's helpers); the event model's scalar-first helpers branch on the lazy marker before their body read, so the
    body path is reached only for a resident atom."""
    def _defs(self, name):
        src = open(os.path.join(KERNEL, name)).read()
        tree = ast.parse(src)
        lines = src.split("\n")
        return {n.name: "\n".join(lines[n.lineno - 1:n.end_lineno]) for n in tree.body if isinstance(n, ast.FunctionDef)}

    def test_segment_readers_read_no_body(self):
        for name, fns in (("kernel.py", ["_atom_prose_chars", "_seg_anchors", "_seg_jump", "_seg_last_text", "_seg_mids",
                                         "_human_turn_floor", "_merge_tx_sets"]),
                          ("judge.py", ["_has_asst_work", "_seg_work", "_turn_work"])):   # _ready_tasks hydrates a planned segment once, on purpose
            defs = self._defs(name)
            for fn in fns:
                body = "\n".join(l.split("#", 1)[0] for l in defs[fn].split("\n") if '"""' not in l)
                self.assertIsNone(BODY_READ.search(body), "%s.%s reads no body" % (name, fn))
                self.assertNotIn("em.hydrate(", body, "%s.%s hydrates nothing" % (name, fn))

    def test_event_model_helpers_branch_on_the_marker_first(self):
        defs = self._defs("event_model.py")
        for fn, via in (("atom_prose_chars", "_prose_chars("), ("atom_mids", None), ("atom_is_settle", None), ("atom_tool_uses", None),
                        ("_has_text", None)):
            body = defs[fn]
            code = body.split('"""', 2)[-1]                     # after the docstring
            lz = code.index('.get("lazy")')
            read = code.index(via) if via else BODY_READ.search(code).start()   # the body read, or the helper that does it
            self.assertLess(lz, read, "%s asks the marker before the body" % fn)
        self.assertNotIn(".get(\"message\")", defs["atom_has_work"], "atom_has_work composes scalar helpers only")

if __name__ == "__main__":
    unittest.main()
