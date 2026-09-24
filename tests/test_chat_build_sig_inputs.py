#!/usr/bin/env python3
"""The chat-build signature's input census.

_built_chat serves every tab's payload, the watched one included, from its cache while _chat_build_sig(sess)
is unchanged, so the signature must contain every input build_session reads that can change the payload.
This module is the census that argument rests on: CENSUS classifies every module-level helper build_session
calls by name, DOTTED every attribute call on a module object or a backend local, and GLOBALS every module
global it reads without calling. Each entry is one of:

  sig    an input the signature folds, under the named label (_CHAT_SIG_LABELS);
  pure   a function of inputs already classified (the parse, the events, the args, a store the
         signature keys) and nothing else; the note says of what;
  memo   a cache whose own key is made of classified inputs, or that latches its first answer for
         the process's life (then constant, and an uncached build now would read the same entry);
  const  fixed for the life of the process (a module constant, an environment value, a path);
  out    a write or a counter, not an input.

The test derives the three sets from build_session's source by AST and requires each to EQUAL the
table's keys, so a helper added to build_session without a classification fails the suite, and so
does a stale entry for one removed. A second test requires every `sig` label to exist in the
signature's label tuple. Names, not lines: the tables say what each read is, the signature module
says how it is keyed. The census enforces ONE level: the helpers build_session calls directly. What
each helper reads in turn is the classification's claim (the note), verified by the differential
tests below that move one input at a time, not derived; a helper that gains a new read keeps its
entry and is caught only if a differential test covers the input.

Synthetic fixtures only: private synthetic sids, invented text, a hermetic state root.
"""
import ast
import contextlib
import inspect
import io
import itertools
import json
import os
import re
import tempfile
import textwrap
import time
import types
import unittest
from unittest import mock
from romp_load import load_source
from tests.needs_row_fixture import populated_ask   # noqa: E402  the shared fixture, a package module (romp_load put the checkout root on the path for a direct run)
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic state BEFORE the load (import-time root)
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_chatsiginputs", os.path.join(BIN, "romp-kernel"))
jd = km.jd


# ── the read inventory ───────────────────────────────────────────────────────────────────────────
# Module-level functions build_session calls by name. Where a helper reads through several inputs the
# label names the one that identifies it and the note lists the rest; every listed input has a label.
CENSUS = {
    "_api_error": ("pure", "the transcript's tail, memoized on its (mtime, size) (transcript)"),
    "_apply_rewind_hold": ("sig", "hold", "and the store; the kept-chain read is over the transcript, states and cut"),
    "_archive_roots": ("sig", "store", "the goals-archive identity is the store triple's third member"),
    "_ask_fill_answers": ("pure", "over a tool event and its result block"),
    "_ask_fill_chosen": ("pure", "over a tool event's output string"),
    "_atom_md": ("pure", "over an atom"),
    "_auth_avail_status": ("sig", "acct", "the availability half of the Billing choices, memoized once per pusher cycle: the account's tri-state read, the key presence and the managed-helper flag, folded whole beside the login label and the both-bit"),
    "_auth_both": ("sig", "acct", "the credential store's login and the settings' key presence"),
    "_awaiting_task_descs": ("sig", "bg", "the live task rows; the split reads the stamped tops (stamp), the store and the transcript"),
    "_awaiting_task_ids": ("sig", "bg", "as _awaiting_task_descs"),
    "_bg_service_ids": ("sig", "bg", "as _awaiting_task_descs"),
    "_awaiting_items_payload": ("sig", "bg", "the wait's own rows (as _session_awaiting), else the rows in flight mid-turn: the row's agents (row), the live task rows and the watches (watch)"),
    "_bg_tasks": ("sig", "row", "the row's live task set gates the transcript's scan; each output tail is a taskout dep; the spawn epoch reads reg and gone"),
    "_chat_agent_open_at": ("pure", "over the events"),
    "_chat_agents_moved": ("pure", "the fold's sealed-agent gate: over the sidecar map (a taskout dep, recorded by _subagent_meta_map), the transcript's task scan (transcript), the row and the spawn epoch (row, reg, gone)"),
    "_chat_fold_count": ("out", "a fold counter"),
    "_chat_fold_demote": ("out", "a fold counter"),
    "_chat_fold_get": ("memo", "the sealed prefix: every gate it checks is an input classified here, and its output is the events an unfolded build produces"),
    "_chat_fold_put": ("out", "the fold entry's write"),
    "_chat_memo_bump": ("out", "a memo counter's increment"),
    "_chat_postal_rev": ("sig", "postal", "this session's revision of the postal index: the records addressed to or from it, their outcomes, and the no-recipient bucket, folded when the payload carries postal traffic (2026-09-18)"),
    "_chat_postal_relevant": ("pure", "over a raw event"),
    "_chat_seam_open_at": ("pure", "over the events"),
    "_chat_stat_key": ("sig", "taskout", "the fold's per-output identity; the same stat the taskout dep re-takes"),
    "_chat_turn_fp": ("pure", "over a turn"),
    "_asm_cut_turn": ("pure", "over the turns' lazy markers: the first turn with a live atom (transcript)"),
    "_cursors_before": ("pure", "over the earlier turns' scalars and the note lists (transcript, states, note)"),
    "_uniq_event_uuids": ("pure", "over the built list: a key on a repeated uuid"),
    "_key_counts": ("pure", "over the sealed prefix: the uuid pass's seen map, kept in the fold entry"),
    "_claude_account_label": ("sig", "acct"),
    "_claude_login_display": ("sig", "acct", "the login as the Billing rows name it: the account file's name and organisation plus the credentials file's kind word (T346)"),
    "_claudemd_docs": ("sig", "claudemd", "the CLAUDE.md files on the chain from the cwd to its git root, plus the global one"),
    "_clearing_now": ("sig", "backend", "the backend's clearing bracket"),
    "_cmd_gestures": ("sig", "states"),
    "_colormap": ("sig", "colormap"),
    "_compacting": ("sig", "backend", "the backend's compacting bracket; else the row's state and since, the parse, and the optimistic stamp's clock boolean (clock)"),
    "_edit_diff": ("pure", "over a tool input"),
    "_effort_changes": ("sig", "states"),
    "_effort_color": ("pure", "over the effort string and the colormap name"),
    "_effort_tone": ("pure", "over the effort string"),
    "_feed_needs_input_of": ("sig", "needs", "the last feed build's needs-you set, as the boolean for this session (None and False share a value)"),
    "_feed_needs_input_count_of": ("sig", "needs", "the last feed build's needs-you card COUNT for this session, the numbered badge's value; keyed beside the boolean under the needs component (None and 0 share the no-dot value)"),
    "_chat_notices": ("sig", "notices", "the Needs you box's rows by id and face: this session's goal rows from the last feed build (plans/needs-you.md, phase three: title, brief, Continue, the credential fix; the row's time is _NEEDS_ROW_UNKEYED) and its needs-you notices with actions, the notice store's projection with the cleared ledger applied (2026-09-19)"),
    "_fold_tasks": ("memo", "pure over the parse's turns (transcript, live); the per-turn memo is keyed on each turn's atoms and fingerprint"),
    "_genuine_queued": ("pure", "over a queued text"),
    "_git_branch": ("sig", "cwd"),
    "_github_repo_of": ("sig", "cwd"),
    "_hydrate_postal": ("sig", "postal", "the index, the caption map and each card's peer identity (names)"),
    "_idle_faded": ("sig", "clock", "the faded boolean, from the row's since and now"),
    "_interrupt_settle": ("pure", "over the events and an atom"),
    "_launch_error": ("sig", "backend"),
    "_limit_hold": ("sig", "limit", "folded as its value while the tab can render a queued bubble (a queue, parked ops, or a non-forwarding backend's in-flight echo); None otherwise, when the build never reads it"),
    "_merge_live_atoms": ("sig", "live", "the backend's tail by revision; the transcript-side sets are pure over the parse"),
    "_chat_wm": ("sig", "live", "the frame's watermark (2026-09-22): the parse key the tree was served under (the transcript facet's own key) and the live revision read before the merge, both already folded by the signature"),
    "_model_color": ("pure", "over the model string and the colormap name"),
    "_model_pending_now": ("sig", "clock", "the row's flag, the kernel's stamp and its 20 s cap as one boolean"),
    "_model_tone": ("pure", "over the model string"),
    "_msg_summaries_scoped": ("sig", "postal", "the cycle's caption map (one fetch per pusher cycle; fresh on a handler thread), read only through the caption values each card embeds (the postal deps)"),
    "_name_color": ("sig", "names"),
    "_name_of": ("sig", "names"),
    "_goal_tree_walk": ("sig", "cleared", "the shared store walk (plans/outline-pane-provisional-row.md): over the store (store) and the parse's segment maps with the warm-anchor table by this sid's revision (anchors), reading the cleared set inside"),
    "_ledger_tree": ("sig", "flags", "the mute (_session_flag) and the cap of 80 over the walk's rows, shared with the Outline's provisional row"),
    "_norm_branch": ("pure", "over a branch string"),
    "_notify_session_effective": ("sig", "ncards", "the master bell; the session's own override is in flags"),
    "_op_qid": ("pure", "over a parked op"),
    "_orphan_replies": ("sig", "states"),
    "_parked_md": ("pure", "over a parked op"),
    "_parked_clear_op": ("pure", "over a parked op and the owning backend's identity (the row's backend, fixed for the session's life): the drain's rule that a one-line Codex command op by a clear head is a clear, read by the clearing fold too (2026-09-19)"),
    "_op_paths": ("pure", "over a parked op (its attachment list, T373 fold)"),
    "_parse": ("sig", "transcript", "memoized on the transcript's (mtime, size), the pending cut (cut) and the states file (states)"),
    "_parse_task_notification": ("pure", "over a reminder string"),
    "_patch_rows": ("pure", "over a structured patch"),
    "_path_links": ("sig", "pathlink", "a resolved token latches for the message's life; an unresolved one is retried, and the retry is the pathlink dep"),
    "_path_pins": ("sig", "pathlink", "the pins latched beside the links"),
    "_path_preview_verdicts": ("sig", "pathlink", "the preview popover's verdict per verified link, with the refusal for each link that does not preview (T351, T364): a stat of each target beside the links, and it warms the cache; it moves only when the links move or a file appears, the pathlink dep"),
    "_postal_card_deps": ("sig", "postal"),
    "_postal_index": ("sig", "postal", "memoized on the log's identity"),
    "_queue_recallable": ("sig", "backend"),
    "_queued_romp_flags": ("pure", "over a queued text"),
    "_read_task_store": ("sig", "tasks"),
    "_retry_gate_state": ("sig", "retry"),
    "_retry_gaveups": ("sig", "states"),
    "_retry_recoveries": ("sig", "states"),
    "_rewind_hold_get": ("sig", "hold"),
    "_romp_system_gist": ("pure", "over a notice's text"),
    "_sdk": ("const", "the SDK backend singleton, fixed once made"),
    "_sdk_sess": ("sig", "reg", "a transcript-less SDK session's row: its reg and names entry"),
    "_sdk_spawned_at": ("sig", "reg", "the reg's spawnedAt and the death marker (gone)"),
    "_seg_anchors": ("pure", "over a segment's atoms"),
    "_seg_jump": ("pure", "over a segment's atoms"),
    "_seg_key": ("pure", "over a segment id"),
    "_segs_seam": ("pure", "over a turn and the store's seams (store)"),
    "_self_host": ("sig", "host"),
    "_session_awaiting": ("sig", "bg", "the row's subagents and task set, the live task rows, the watches (watch), the states overlay (states), the durable stamp view with its delegation peers (stamp: the store, the journal and the postal log, since a peer's answer supersedes a peer wait), the blocked-yield read of the store (store, hold) and the peers' names (names)"),
    "_session_backend": ("sig", "row", "the row's backend field; else the reg's existence (reg)"),
    "_session_chip": ("pure", "over classified inputs: the parse and live tail, the row, the backend brackets, the clock booleans, the live task rows, the watches, the states overlay, the store and the downtime list"),
    "_session_cwd": ("sig", "cwd", "the names entry's cwd, else the transcript's stamp"),
    "_session_flag": ("sig", "flags"),
    "_mail_off_fields": ("sig", "flags", "the effective mail state and its reason from ONE derivation (postalServiceOff, mailOffWhy): the record's readability and threadOf through _thread_reg (reg), then the mailbox flag with its legacy twin (flags) (T356)"),
    "_session_meta": ("pure", "over the transcript's records, memoized by record identity (transcript)"),
    "_session_retry_suppressed": ("sig", "retry"),
    "_session_working": ("sig", "downtime", "over the turns, and the host suspensions recorded since boot"),
    "_sessions": ("sig", "names", "the cycle's discovery rows: the transcript path (transcript), the display name (names), and the 48 h discovery window (clock: a session that ages out leaves the roster and the tab list, so no signature is taken for it)"),
    "_space_paths": ("memo", "the first resolution of a message's spaced spans latches for the process's life"),
    "_split_followup": ("pure", "over a text"),
    "_split_reminders": ("pure", "over a text"),
    "_stamp_agents": ("sig", "taskout", "the sidecar directory and each agent transcript it reads are taskout deps (stat'd before the read, re-stat'd per cycle); the task scan is over the transcript, the liveness gate over the row and the spawn epoch (row, reg, gone)"),
    "_stamp_interrupt_causes": ("pure", "over the events"),
    "_stat_key": ("sig", "cleared", "cleared.jsonl's identity, the ledger memo's key beside the set _cleared_ids reads"),
    "_strip_hook_notices": ("pure", "over a text"),
    "_strip_fork_opener": ("pure", "over a text"),
    "_task_outputs_for": ("sig", "taskout", "the launch record from the transcript's scan; each output file's tail is a taskout dep"),
    "_branch_marker": ("sig", "reg"),   # the fork lineage chip: _thread_reg (the reg) and _name_of, one helper for the whole build and a page
    "_tilde": ("const", "the home directory"),
    "_live_map": ("sig", "row", "the liveness map when the caller passed none"),
    "_tree_of": ("sig", "cwd"),
    "_user_images": ("pure", "over a turn's blocks and text"),
    "iso": ("pure", "over a timestamp"),
}

# Attribute calls whose base is a module-level object or one of the backend locals build_session binds.
DOTTED = {
    "_RENDER_FLOOR.get": ("sig", "floor", "the floor the pusher last used, read by a build outside its cycle (the pusher's decision is the component)"),
    "Sessions.backend_for": ("sig", "reg", "ownership: the SDK backend owns a sid whose reg exists"),
    "Sessions.working_note": ("sig", "note", "the working-note file (working/<sid>) by identity"),
    "Sessions.live_rev": ("sig", "live", "the live tail's revision, read before the merge for the frame's watermark (_chat_wm, 2026-09-22): the signature folds the same counter"),
    "jd.episode_rows": ("sig", "episodes"),
    "jd.episode_settles": ("sig", "episodes"),
    "jd.load_archive": ("sig", "archive"),
    "jd.load_goals_shared_or_fault": ("sig", "store", "the shared read behind the per-session fault boundary"),
    "em.MSG_TAG_RE.search": ("pure", "over a text"),
    "em.parse_teammate_message": ("pure", "over a text"),
    "em.injected_source": ("pure", "over a message record"),
    "em.strip_harness_preamble": ("pure", "over a text"),
    "em.hydrate": ("pure", "over the tree's atoms: fills a body before the assembly cut from the transcript the parse key already covers (T323 stage 4a)"),

    "em.parse_z": ("pure", "a rendered orphan note's stamp, parsed for the fold's orphan gate window (round 2, item 11)"),
    "sb.echo_text_key": ("pure", "over a text"),
    "cm.context_rgb": ("pure", "over a percentage"),
    "cm.ramp": ("pure", "over a fraction and the colormap's stops"),
    "cm.stops_for": ("pure", "over the colormap name"),
    "_SEND_TOOL_RE.search": ("const", "a module regex"),
    "_FOLLOWUP_GOAL_RE.search": ("const", "a module regex (the queued follow-up's goal id, for the rescind's chip, T373)"),
    "_PATH_LINK_CACHE.get": ("sig", "pathlink", "which tokens are still unresolved"),
    "_chat_fold.pop": ("out", "the fold entry's eviction"),
    "_ledger_memo.get": ("memo", "keyed on the seams, cleared.jsonl and the anchor revision, held by parse and store identity"),
    "_node_anchor_rev.get": ("sig", "anchors"),
    "_parse_mode.get": ("memo", "the parse's own mode, written under the parse's key"),
    "_pending_ops.get": ("sig", "ops"),
    "be.owns": ("sig", "reg"),
    "be.live_atoms": ("sig", "live", "the live tail, snapshotted BEFORE the parse since 2026-09-23 (an atom that left it had its record on disk first) and handed to the merge; the signature folds its revision (Sessions.live_rev)"),
    "be.pending_queued": ("sig", "backend", "the owning backend's queue by value"),
    "_cbe.pending_queued_meta": ("sig", "backend", "each queued copy's (qid, qts) beside its text, folded as _qmeta where the backend keeps them"),
    "be.qids_for_landing": ("sig", "live", "the queued-copy ids a landed record pairs with: a landing is a transcript record (transcript) or a live-tail change (live), and the fed ledger it reads fills with the feed that bumps live_rev"),
    "_be_fk.fork_children": ("sig", "fork"),
    "os.path.exists": ("sig", "transcript", "whether the transcript exists yet"),
    "os.path.realpath": ("sig", "cwd", "the two tree tops compared through the filesystem"),
    "os.path.expanduser": ("const", "the home directory"),
    "os.path.basename": ("pure", "over a path string"),
    "os.path.dirname": ("pure", "over a path string"),
    "json.dumps": ("pure", "over a value"),
    "re.sub": ("pure", "over a text"),
    "bisect.bisect_right": ("pure", "over a list"),
    "sys.stderr.write": ("out", "a log line"),
    "traceback.format_exc": ("out", "a log line"),
}

# Module globals build_session reads without calling.
GLOBALS = {
    "Sessions": ("const", "the backend-agnostic session API class; its calls are in DOTTED"),
    "_CHAT_FOLD_STATS": ("out", "counters"),
    "_PATH_LINK_CACHE": ("sig", "pathlink"),
    "_SEND_TOOL_RE": ("const", "a module regex"),
    "_FOLLOWUP_GOAL_RE": ("const", "a module regex"),
    "_chat_fold": ("memo", "see _chat_fold_get"),
    "_RENDER_FLOOR": ("sig", "floor", "the render floor the pusher's last build used; the pusher's own decision is the floor component"),
    "_PAGE_FILL_TURNS": ("const", "the turns stepped past a page for late fills"),
    "_chat_fold_last": ("out", "the perf line's per-thread record"),
    "_chat_fold_lock": ("const", "a lock"),
    "_chat_fold_warned": ("out", "a once-flag"),
    "_chat_dep_scope": ("out", "the running build's dependency record, written for the pusher (see _chat_build_deps)"),
    "_chat_postal_stats": ("out", "counters"),
    "_ledger_memo": ("memo", "see _ledger_memo.get"),
    "_ledger_memo_stats": ("out", "counters"),
    "_live_scope": ("sig", "names", "the names snapshot the thread holds (the cycle's, or the push's own), digested"),
    "_node_anchor_rev": ("sig", "anchors"),
    "_parse_mode": ("memo", "see _parse_mode.get"),
    "_pending_ops": ("sig", "ops"),
    "bisect": ("const", "a module"), "cm": ("const", "a module"), "em": ("const", "a module"),
    "jd": ("const", "a module"), "json": ("const", "a module"), "os": ("const", "a module"),
    "re": ("const", "a module"), "sb": ("const", "a module"), "sys": ("const", "a module"),
    "traceback": ("const", "a module"),
}

# The local names build_session binds a backend to, derived from its source by AST (_backend_locals: every
# name assigned from a call to _sdk(), _codex() or Sessions.backend_for(), or from another such name,
# transitively) and pinned here, so a read through a backend bound to a new name, or a method called
# directly on _sdk()/_codex() (reported as `_sdk().<attr>`), reaches DOTTED instead of slipping past it.
BACKEND_LOCALS = ("_be_fk", "_cbe", "be")
BACKEND_FACTORIES = ("_sdk", "_codex")
KINDS = ("sig", "pure", "memo", "const", "out")


def _module_names():
    """Every name bound at kernel module level: functions, classes, assignments, imports."""
    tree = ast.parse(inspect.getsource(km))
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                for x in ast.walk(t):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def _is_backend_factory(call):
    f = call.func
    return ((isinstance(f, ast.Name) and f.id in BACKEND_FACTORIES)
            or (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "Sessions" and f.attr == "backend_for"))


def _backend_locals(fn):
    """Every local name bound to a backend object, transitively: assigned from a backend factory call, or
    from a name already known to hold one."""
    names, changed = set(), True
    while changed:
        changed = False
        for x in ast.walk(fn):
            if not (isinstance(x, ast.Assign) and len(x.targets) == 1 and isinstance(x.targets[0], ast.Name)):
                continue
            v = x.value
            bound = (isinstance(v, ast.Call) and _is_backend_factory(v)) or (isinstance(v, ast.Name) and v.id in names)
            if bound and x.targets[0].id not in names:
                names.add(x.targets[0].id)
                changed = True
    return names


def _census_of(fn_src, module_names):
    """(calls, dotted, globals, backend_locals): the module-level functions called by name, the
    attribute-call chains on module objects or backend locals (a method called on a factory's result
    directly reads `_sdk().<attr>`), the module globals read without a call, and the backend locals the
    function binds (derived, see _backend_locals)."""
    fn = ast.parse(fn_src).body[0]
    backends = _backend_locals(fn)
    local = set()
    for x in ast.walk(fn):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
            local.add(x.id)
        elif isinstance(x, ast.FunctionDef) and x is not fn:
            local.add(x.name)
        elif isinstance(x, ast.arg):
            local.add(x.arg)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            local.add(x.name)
    calls, dotted, reads = set(), set(), set()
    for x in ast.walk(fn):
        if isinstance(x, ast.Call):
            f = x.func
            if isinstance(f, ast.Name) and f.id in module_names and f.id not in local:
                calls.add(f.id)
            elif isinstance(f, ast.Attribute):
                base, chain = f.value, [f.attr]
                while isinstance(base, ast.Attribute):
                    chain.insert(0, base.attr)
                    base = base.value
                if isinstance(base, ast.Name) and ((base.id in module_names and base.id not in local)
                                                   or base.id in backends):
                    dotted.add(base.id + "." + ".".join(chain))
                elif isinstance(base, ast.Call) and _is_backend_factory(base) and isinstance(base.func, ast.Name):
                    dotted.add(base.func.id + "()." + ".".join(chain))   # a method called on the factory's result
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in module_names and x.id not in local:
            reads.add(x.id)
    return calls, dotted, reads - calls, backends


def _key_reads(src, key):
    """The line numbers at which `src` holds a string constant equal to `key` (2026-09-21). The counted shapes, on any
    receiver, are the whole claim: a read that names the field as a constant EQUAL to it, the first argument of a
    `.get` or a `.pop`, a subscript's slice, the left operand of an `in` test, an entry of the tuple a loop reads
    through (the shape _row_fields in test_feed_memo_inputs documents it cannot derive), and an f-string's real
    subscript. Not counted (2026-09-21, a stated blind spot the way _row_fields states its own; the kernel holds none
    of these today): a read that renders the field through a format placeholder, a percent-format key, a `.format`
    or `.format_map` field, and one that names it as a keyword argument, `dict(lastTool=...)`, since neither is a
    constant equal to the key. Prose never is: a comment is not in the tree, and a docstring or a sentence literal
    that mentions the field is a constant that does not equal it. So the count is zero on prose and one per counted
    read, where the word-regex mention count this replaces could not tell a comment from a reader (#1817 and #1819
    were green alone and red together over kernel prose; #1825 reworded two kernel docstrings to satisfy the count).
    A write or a listing that names the key counts too, which is the pin's intent: it hunts the kernel naming the
    field as a key outside the regions that may. Never strip string literals first: the key of a real read is itself
    a string literal, and a census that stripped them would go blind to the reader it hunts. `src` is a file's text,
    a function's source (dedented so a method's parses; dedent removes no line, so it changes no number), or a tree
    already parsed. The numbers are the parse's own: a whole file's parse yields the file's line numbers, the ones
    _reads_outside's ranges apply to, and a snippet's parse numbers it from 1 at its def or first decorator."""
    tree = src if isinstance(src, ast.AST) else ast.parse(textwrap.dedent(src))
    return {n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value == key}


def _reads_outside(src, key, permitted):
    """The lines _key_reads finds in `src` for `key` that lie in none of the `permitted` ranges: (first, stop) pairs,
    1-based and half-open, the way inspect.getsourcelines counts a function's lines."""
    return {n for n in _key_reads(src, key) if not any(first <= n < stop for first, stop in permitted)}


def _unkeyed_field_permits():
    """The kernel's text and, per dropped row field, the line ranges where naming it is legitimate: for both fields
    the projection block between _chat_sig_deps and _chat_build_sig (the constants, their comment and _chat_row_sig,
    which name the field in order to drop it) and _chat_build_sig itself (the key, whose row comment says so; a read
    there would be a key input, not a payload read); for ctxTokens also the compaction-suggestion tick (its one
    reader, a tick, not a build) and Sessions.live (the merge that writes it). Ranges as _reads_outside takes them."""
    def span(f):
        src, at = inspect.getsourcelines(f)
        return at, at + len(src)
    text = Path(inspect.getsourcefile(km._chat_build_sig)).read_text(encoding="utf-8")
    key = (span(km._chat_sig_deps)[1], span(km._chat_build_sig)[1])
    return text, {"lastTool": (key,), "ctxTokens": (key, span(km._compact_suggest_tick), span(km.Sessions.live))}


class Census(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = _module_names()
        cls.calls, cls.dotted, cls.reads, cls.backends = _census_of(inspect.getsource(km.build_session), cls.names)

    def test_every_helper_build_session_calls_is_classified_and_nothing_stale_remains(self):
        self.assertEqual(self.calls, set(CENSUS),
                         "a helper build_session calls by name is missing from CENSUS (an unclassified read), "
                         "or CENSUS names one build_session no longer calls: %r"
                         % sorted(self.calls ^ set(CENSUS)))

    def test_every_attribute_call_on_a_module_object_or_backend_is_classified(self):
        self.assertEqual(self.dotted, set(DOTTED), sorted(self.dotted ^ set(DOTTED)))

    def test_the_backend_locals_are_the_pinned_ones(self):
        self.assertEqual(self.backends, set(BACKEND_LOCALS),
                         "build_session binds a backend to a new name (or dropped one): pin it so its reads are censused")
        src = "def f():\n    be = _sdk()\n    x = be\n    y = Sessions.backend_for(sid)\n    z = other()\n    _codex().owns(sid)\n    y.pending_queued(sid)\n"
        calls, dotted, reads, backends = _census_of(src, {"_sdk", "Sessions", "other", "_codex"})
        self.assertEqual(backends, {"be", "x", "y"}, "transitive: a name assigned from a backend name is one too")
        self.assertEqual(dotted, {"_codex().owns", "y.pending_queued", "Sessions.backend_for"})

    def test_every_module_global_read_is_classified(self):
        self.assertEqual(self.reads, set(GLOBALS), sorted(self.reads ^ set(GLOBALS)))

    def test_every_entry_has_a_known_kind_and_a_sig_entry_names_a_label(self):
        for table in (CENSUS, DOTTED, GLOBALS):
            for name, ent in table.items():
                self.assertIn(ent[0], KINDS, name)
                self.assertGreaterEqual(len(ent), 2, "%s: a sig entry names its label; every other kind says of what" % name)

    def test_the_census_names_resolve_in_the_kernel(self):
        for name in CENSUS:
            self.assertTrue(callable(getattr(km, name, None)), name)
        for name in GLOBALS:
            self.assertTrue(hasattr(km, name), name)

    def test_every_sig_entry_is_a_component_of_the_signature(self):
        labels = set(km._CHAT_SIG_LABELS)
        for table in (CENSUS, DOTTED, GLOBALS):
            for name, ent in table.items():
                if ent[0] == "sig":
                    self.assertIn(ent[1], labels, "%s is keyed under %r, which the signature has no component for" % (name, ent[1]))
        self.assertNotIn("judge_gen", labels, "the global judge-pass counter is no longer a chat input")
        self.assertEqual(km._PerfStats.CHAT_MISS, km._CHAT_SIG_LABELS + ("cold", "nosig"),
                         "the /perf attribution carries one counter per label")
        self.assertEqual(km._CHAT_SIG_LABELS[-len(km._CHAT_SIG_DEPS):], km._CHAT_SIG_DEPS,
                         "the dependency components are the tail, so a post-build signature can be compared without them")
        for retired in ("_active_chat_sig", "_clock_predicates", "_external_sig", "_ACTIVE_SIG_FILES"):
            self.assertFalse(hasattr(km, retired), "%s: the watched tab's separate key is retired" % retired)

    def test_the_row_projection_drops_only_fields_no_chat_reader_reads(self):
        """The `row` component is the liveness row without _CHAT_ROW_UNKEYED and, per task row, without
        _CHAT_TASK_ROW_UNKEYED (_chat_row_sig, 2026-09-18). A dropped field must stay unread by the build, or the
        memo rule breaks silently: a stale payload served while the key holds. Two pins, both by READ, not by
        mention (_key_reads, 2026-09-21): a read is a string constant equal to the field, the key of a get call, a
        subscript or a membership test on any receiver, or an entry of the tuple a loop reads through; prose naming
        the field, a comment or a docstring, is not one and does not count (#1817 and #1819 were green alone and red
        together over kernel prose, and #1825 reworded two kernel docstrings to satisfy the old word count). The
        named chat readers' own source (the reads the census classifies) reads neither field. And, as the transitive
        backstop a reader added in a helper they call would slip past, the whole kernel source: every read of lastTool
        lies in the projection block itself (the constants, their comment and _chat_row_sig, which name the field in
        order to drop it) or in _chat_build_sig (the key, whose row comment says so; a read there would be a key
        input, not a payload read), and every read of ctxTokens in those two, in the compaction-suggestion tick (its
        one reader, a tick, not a build) or in Sessions.live (the merge that writes it). _interrupting reads snapT and
        interrupting by design and is folded under clock; it is deliberately not among the readers."""
        self.assertEqual(km._CHAT_ROW_UNKEYED, {"snapT", "interrupting", "ctxTokens"})
        self.assertEqual(km._CHAT_TASK_ROW_UNKEYED, {"lastTool"})
        readers = (km.build_session, km._light_status, km._session_chip, km._bg_live_norm, km._bg_tasks,
                   km._agent_alive, km._awaiting_live_rows, km._session_background_items, km._model_pending_now,
                   km._compacting, km._session_backend, km._session_retrying)
        for f in readers:
            own = ast.parse(textwrap.dedent(inspect.getsource(f)))
            for k in ("ctxTokens", "lastTool"):
                self.assertFalse(_key_reads(own, k),
                                 "%s is dropped from the row component, so no chat reader may name it as a key; %s does"
                                 % (k, f.__name__))
        text, permits = _unkeyed_field_permits()
        lines = text.splitlines()
        deps_src, deps_at = inspect.getsourcelines(km._chat_sig_deps)
        _sig_src, sig_at = inspect.getsourcelines(km._chat_build_sig)
        block = "\n".join(lines[deps_at - 1 + len(deps_src):sig_at - 1])
        self.assertIn("def _chat_row_sig", block, "the projection sits between _chat_sig_deps and _chat_build_sig")
        tree = ast.parse(text)
        self.assertEqual(sorted(_reads_outside(tree, "lastTool", permits["lastTool"])), [],
                         "lastTool is named as a key outside the projection and the key: a reader there would serve a stale payload under an unchanged key")
        self.assertEqual(sorted(_reads_outside(tree, "ctxTokens", permits["ctxTokens"])), [],
                         "ctxTokens is named as a key beyond the projection, the key, the compaction tick and the merge writing it")

    def test_the_read_census_counts_reads_not_mentions(self):
        """The census behind the previous test tells a read from prose (2026-09-21). A synthetic body first: the helper
        returns the lines of the reads, through a get, a subscript, an `in` test and a tuple a loop reads through, on
        any receiver, plus an f-string's real subscript, and none of the comment, the docstring, the sentence literal or
        the percent-format key that name the fields (2026-09-21, the post-merge review of #1939: the counted and uncounted
        shapes the census helper's docstring claims are asserted here, so an interpreter that moves f-string nodes or a
        helper change that starts counting placeholders fails this line rather than the docstring). Then the
        pin on the real kernel text with a comment naming both fields and a single-quoted get appended past every
        permitted range: the comment is no hit (under the word-regex mention count it was one per field, which is how
        two PRs went green alone and red together) and the get is exactly one, so the census is not blind to the
        reader it hunts (a census that stripped string literals would be). One parse of the kernel, not two."""
        src = textwrap.dedent('''
            def body(t, tm, row):
                """Reads lastTool off a task row and ctxTokens off the liveness row."""
                # a comment naming lastTool, which is not a read
                a = t.get("lastTool")
                b = (tm or {})["ctxTokens"]
                c = "lastTool" in row
                for k in ("ctxTokens",):
                    d = tm.get(k)
                note = "the raw ctxTokens count, which the payload renders as a percent"
                e = f"{tm['ctxTokens']}"
                f = "%(ctxTokens)s" % tm
                return a, b, c, d, note, e, f
        ''')
        line = lambda frag: next(i for i, l in enumerate(src.splitlines(), 1) if frag in l)
        self.assertEqual(_key_reads(src, "lastTool"), {line('t.get("lastTool")'), line('"lastTool" in row')})
        self.assertEqual(_key_reads(src, "ctxTokens"),
                         {line('["ctxTokens"]'), line('for k in ("ctxTokens",)'), line("f\"{tm['ctxTokens']}\"")})
        text, permits = _unkeyed_field_permits()
        tail = text + "\n# a note naming lastTool\n# and ctxTokens\ndef _probe(t):\n    return t.get('lastTool')\n"
        tree = ast.parse(tail)
        self.assertEqual(_reads_outside(tree, "ctxTokens", permits["ctxTokens"]), set(),
                         "a comment naming ctxTokens past every permitted range is not a read")
        self.assertEqual(_reads_outside(tree, "lastTool", permits["lastTool"]), {tree.body[-1].body[0].lineno},
                         "the comment naming lastTool is not a read; the single-quoted get on the last line is the one hit")


# ── the differential tests ────────────────────────────────────────────────────────────────────────
# One hermetic world (a session discovery finds, with a fixed liveness row, and no backend owns); each test moves one
# input and requires the signature to miss under exactly that input's label, so a component that stopped
# covering its input fails here by name. The dependency components are exercised through a hand-made cache
# record, the way _chat_sig_deps evaluates one; the pusher tests drive the REAL build_session through _push.
SID = "77777777-8888-9999-aaaa-ccccccccccc1"      # this module's private synthetic sids: goal stores are minted under SID
PEER = "77777777-8888-9999-aaaa-ccccccccccc2"
OTHER_A = "77777777-8888-9999-aaaa-ccccccccccc3"   # two sessions this tab is party to no message with
OTHER_B = "77777777-8888-9999-aaaa-ccccccccccc4"
NOW = 1781100000
T0 = NOW - 3600


def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def _aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


class _TailBackend:
    """An owning backend with a live tail the test moves by hand, shaped like the Codex backend: no live_rev
    counter (Sessions.live_rev keys its tail on the atoms' serialized value) and no unqueue (its in-flight
    echo is what lets the build render a queued bubble, so the limit hold is read while one stands)."""

    def __init__(self):
        self.atoms = {}

    def owns(self, sid): return True
    def live_atoms(self, sid): return [dict(a) for a in self.atoms.get(sid, [])]
    def pending_queued(self, sid): return []
    def compacting(self, sid): return False
    def clearing(self, sid): return False
    def pending_cut(self, sid): return ""


class _World(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.cdir = td / "launchdir"
        self.cdir.mkdir()
        proj = td / "projects"
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(self.cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in [
            _uline(T0, "start the notes-api spike", "u1"), _aline(T0 + 40, "Spike is up.", "a1", "u1"),
            _uline(T0 + 100, "now the tests", "u2", "a1"), _aline(T0 + 140, "Tests pass.", "a2", "u2")]) + "\n")
        state = td / "state"
        state.mkdir()
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES, km.WORKING_DIR, km._GLOBAL_CLAUDE_MD, km._live_map, km._sdk,
                      os.environ.get("CLAUDE_CONFIG_DIR"), os.environ.get("ROMP_HOST_NAME"),
                      len(km._downtime), km._claude_account_label, km._auth_avail_status, km._MENTION_PINS)
        jd._rebind_state(state)                       # every STATE-derived dir (goals, states, episodes, gone, sdk, ...)
        km._MENTION_PINS = None                       # the pin dir latches at first use (_pin_dir): this world's root, not a prior one's
        jd.PROJECTS = proj
        jd.NAMES.mkdir()
        (jd.NAMES / SID).write_text("web\t%s\t#1EA1EB\twhite\n" % self.cdir)
        km.NAMES = jd.NAMES
        km.WORKING_DIR = state / "working"
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        os.environ["CLAUDE_CONFIG_DIR"] = str(td / "claude")   # the task-store root (_tasks_base)
        self.row = {"state": "idle", "since": NOW - 100, "model": "", "effort": "", "context": None,
                    "compactPct": None, "color": None, "backend": "sdk"}
        self.live_map = {SID: self.row}
        km._live_map = lambda: self.live_map
        km._sdk = lambda: None
        km._built_chat.clear()
        km._live_scope.chat_shared = None
        km._live_scope.usage = km._live_scope.spend_pause = None   # a drain scope another module left on this thread
        self.sess = {"sid": SID, "name": "web", "path": str(self.tpath), "anchor": SID}

    def tearDown(self):
        (state, proj, names, wdir, gmd, live_fn, sdk, cfg, host, ndown, acct, avail, pins) = self.saved
        jd._rebind_state(state)
        jd.PROJECTS = proj
        km.NAMES, km.WORKING_DIR, km._GLOBAL_CLAUDE_MD, km._live_map, km._sdk = names, wdir, gmd, live_fn, sdk
        km._claude_account_label, km._auth_avail_status = acct, avail
        km._MENTION_PINS = pins
        for k, v in (("CLAUDE_CONFIG_DIR", cfg), ("ROMP_HOST_NAME", host)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        del km._downtime[ndown:]
        for d in (km._node_anchor_rev, km._pending_ops, km._auto_retry_state,
                  km._interrupt_clicked, km._compact_clicked, km._model_switch_pending):
            d.pop(SID, None)
        with km._watch_lock:
            km._watches[:] = [w for w in km._watches if w.get("sid") != SID]
        km._rewind_hold_clear(SID)
        km._built_chat.clear()
        km._live_scope.names = None
        km._live_scope.snapshot = None
        km._live_scope.chat_shared = None
        for cache in (km._PATH_LINK_CACHE, km._SPACE_PATH_CACHE):
            for k in [k for k in cache if k[0] == SID]:
                cache.pop(k, None)
        km._PIN_ASSOC_MEMO.pop(SID, None)
        self.td.cleanup()

    def sig(self, now=NOW, deps=None, tm=None):
        """The complete key, as the pusher takes it: the session, its liveness row, the push's clock and map."""
        return km._chat_build_sig(self.sess, self.row if tm is None else tm, now, live_map=self.live_map, deps=deps)

    def moved(self, before, after):
        return km._chat_sig_miss(before, after)

    def tail_backend(self):
        """Hand the sid to a _TailBackend for this test (the world's default owner is the unowned route)."""
        be = _TailBackend()
        saved = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: be)
        self.addCleanup(lambda: setattr(km.Sessions, "backend_for", staticmethod(saved)))
        return be

    def store(self, nodes=None, status=None):
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({"rompUuid": SID, "nodes": nodes or {}, "status": status or {}}))


class Differential(_World):
    def test_a_quiet_world_holds_and_has_one_value_per_label(self):
        a = self.sig()
        self.assertEqual(len(a), len(km._CHAT_SIG_LABELS))
        self.assertEqual(self.sig(), a)
        self.assertEqual(self.moved(a, self.sig()), ())

    def test_a_transcript_append_misses_under_transcript_alone(self):
        a = self.sig()
        with open(self.tpath, "a") as f:
            f.write(json.dumps(_uline(T0 + 200, "and the docs", "u3", "a2")) + "\n")
        self.assertEqual(self.moved(a, self.sig()), ("transcript",))

    def test_a_states_row_misses_under_states(self):
        a = self.sig()
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        with open(jd.STATESDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": T0 + 150, "state": "idle"}) + "\n")
        self.assertEqual(self.moved(a, self.sig()), ("states",))

    def test_the_goal_store_its_journal_and_its_archive_each_miss_under_store_and_a_judge_pass_alone_does_not(self):
        a = self.sig()
        # a judge pass that moved nothing this session reads: not a chat input. The global generation used to be a
        # component, so one session's publish rebuilt every other tab byte-identical
        km._judge_gen[0] += 1
        try:
            self.assertEqual(self.moved(a, self.sig()), (), "the judge-pass counter alone moves no tab's key")
        finally:
            km._judge_gen[0] -= 1
        self.store()
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("store",), "a publish busts the tab at once (the live identity)")
        jd._overrides_dir().mkdir(parents=True, exist_ok=True)
        with open(jd._overrides_dir() / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": T0, "op": "noop"}) + "\n")
        c = self.sig()
        self.assertEqual(self.moved(b, c), ("store",), "the override journal is the store triple's second member")
        jd.GOALARCHDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALARCHDIR / (SID + ".json")).write_text(json.dumps({"rompUuid": SID, "nodes": {}, "status": {}}))
        self.assertEqual(self.moved(c, self.sig()), ("store",), "...and the goals-archive the third")

    def test_another_sessions_publish_moves_nothing_of_this_tabs_key(self):
        a = self.sig()
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (PEER + ".json")).write_text(json.dumps({"rompUuid": PEER, "nodes": {}, "status": {}}))
        km._bump_judge_gen_if_changed()                   # the producer pass's own bump over the moved store dir
        self.assertEqual(self.sig(), a, "one session's goal-store publish rebuilds no other session's tab")

    def test_a_rewind_hold_misses_under_hold_and_its_clear_restores_the_signature(self):
        a = self.sig()
        km._rewind_hold_set(SID, T0 + 10, "u1")
        self.assertEqual(self.moved(a, self.sig()), ("hold",))
        km._rewind_hold_clear(SID)
        self.assertEqual(self.sig(), a)

    def test_the_archive_headline_and_the_episode_log_each_miss_under_their_own_label(self):
        a = self.sig()
        jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (jd.ARCHDIR / (SID + ".json")).write_text(json.dumps({"headline": "a spike", "abstract": ""}))
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("archive",))
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        with open(jd.EPIDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": T0 + 50, "kind": "clear"}) + "\n")
        self.assertEqual(self.moved(b, self.sig()), ("episodes",))

    def test_the_sdk_registry_and_the_death_marker_each_miss_under_their_own_label(self):
        a = self.sig()
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"sid": SID, "name": "web", "alive": False}))
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("reg",))
        jd.GONEDIR.mkdir(parents=True, exist_ok=True)
        (jd.GONEDIR / (SID + ".json")).write_text(json.dumps({"t": NOW - 10, "by": "test"}))
        self.assertEqual(self.moved(b, self.sig()), ("gone",))

    def test_registry_host_offsets_do_not_change_the_key_or_mutate_the_record(self):
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        row = {"sid": SID, "name": "web", "alive": False}
        reg.write_text(json.dumps(row))
        before = self.sig()
        for fields in ({"hostAck": {"host": "1:2", "offset": 10}},
                       {"hostAck": {"host": "1:2", "offset": 20}, "hostLogPos": {"host": "1:2", "pos": 3}},
                       {}):
            with self.subTest(fields=fields):
                published = dict(row, **fields)
                pending = reg.with_suffix(".next")
                pending.write_text(json.dumps(published))
                pending.replace(reg)
                self.assertEqual(self.moved(before, self.sig()), ())
                self.assertEqual(km._thread_reg_read(SID), ("ok", published), "the shared record keeps its offsets")

    def test_registry_content_changes_still_invalidate_including_unknown_fields(self):
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        row = {"sid": SID, "name": "web", "alive": False}
        reg.write_text(json.dumps(row))
        for field, value in (("alive", True), ("cwd", "/proj/TESTHOST/app"), ("spawnedAt", NOW),
                             ("forkedFrom", {"sid": SID_A, "name": "api"}), ("threadOf", SID_B),
                             ("liveCtxTokens", 123), ("futureDisplayField", {"value": [1, 2]})):
            with self.subTest(field=field):
                before = self.sig()
                row[field] = value
                reg.write_text(json.dumps(row))
                self.assertEqual(self.moved(before, self.sig()), ("reg",))
        before = self.sig()
        del row["futureDisplayField"]
        reg.write_text(json.dumps(row))
        self.assertEqual(self.moved(before, self.sig()), ("reg",))

    def test_registry_missing_unreadable_and_empty_records_have_distinct_keys(self):
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        missing = self.sig()
        reg.write_text("{broken")
        unreadable = self.sig()
        self.assertEqual(self.moved(missing, unreadable), ("reg",))
        reg.write_text("{}")
        readable = self.sig()
        self.assertEqual(self.moved(unreadable, readable), ("reg",))
        reg.unlink()
        self.assertEqual(self.moved(readable, self.sig()), ("reg",))
        self.assertEqual(self.sig(), missing)

    @unittest.skipIf(os.geteuid() == 0, "root can read a mode-000 registry")
    def test_registry_permissions_repair_changes_the_chat_key_without_a_rewrite(self):
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("{}")
        readable = self.sig()
        original = reg.stat()
        try:
            reg.chmod(0)
            unreadable = self.sig()
            self.assertEqual(self.moved(readable, unreadable), ("reg",))
        finally:
            reg.chmod(0o600)
        repaired = reg.stat()
        self.assertEqual((repaired.st_ino, repaired.st_mtime_ns, repaired.st_size),
                         (original.st_ino, original.st_mtime_ns, original.st_size))
        self.assertEqual(self.moved(unreadable, self.sig()), ("reg",))
        self.assertEqual(self.sig(), readable)

    def test_unchanged_registry_is_decoded_once_across_chat_key_checks(self):
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"sid": SID, "hostAck": {"offset": 1}}))
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=Path.read_text) as read:
            for _ in range(5):
                self.sig()
            self.assertEqual(sum(call.args[0] == reg for call in read.call_args_list), 1)

    def test_the_task_store_misses_under_tasks(self):
        a = self.sig()
        d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "tasks" / SID
        d.mkdir(parents=True)
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "write the tests", "status": "pending"}))
        self.assertEqual(self.moved(a, self.sig()), ("tasks",))

    def test_the_live_tail_misses_under_live_for_an_echo_and_for_its_dropped_mark(self):
        # the owning backend's tail (Sessions.live_rev): a backend with no counter is keyed on the tail's
        # serialized value, so an echo arriving and a mark written on it are each a change under live
        be = self.tail_backend()
        a = self.sig()
        be.atoms[SID] = [{"t": NOW, "text": "please also fix the header", "author": "human"}]
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("live",))
        be.atoms[SID][0]["dropped"] = True
        self.assertEqual(self.moved(b, self.sig()), ("live",), "the dropped mark is a change to the tail")

    def test_the_liveness_row_misses_under_row_and_its_snapshot_stamp_does_not(self):
        a = self.sig()
        self.assertEqual(self.sig(tm=dict(self.row, snapT=NOW + 3)), a, "snapT moves every cycle and is not rendered")
        self.assertEqual(self.moved(a, self.sig(tm=dict(self.row, state="working"))), ("row",))
        self.assertEqual(self.moved(a, self.sig(tm=dict(self.row, context=42))), ("row",))
        self.assertEqual(self.moved(a, km._chat_build_sig(self.sess, None, NOW, live_map={})), ("row",),
                         "no row for the sid, and an empty map: a different key, never a false hit")

    def test_a_tasks_progress_and_the_raw_token_count_hold_the_row_and_a_start_or_end_moves_it(self):
        """The row component is the projection _chat_row_sig (2026-09-18): a background task's progress chatter
        (lastTool, which every task_progress that names a tool rewrites) and the raw token count (ctxTokens, which
        every usage report moves while the payload renders the percent) hold the key; a task starting, ending or
        learning its description or type, the percent, ctxOver, a subagent and the state each move it, as before."""
        km._live_map = self.saved[5]                 # the kernel's own reader, so the bg component reads the handed map
        aid = "a0123456789abcdef"
        task = {"toolUseId": "tu_1", "taskId": aid, "desc": "Running Map the parser", "since": NOW - 30,
                "type": "local_agent", "lastTool": ""}
        row = dict(self.row, bgTasks=[task], subagents=[], ctxTokens=120_000)
        key = lambda r: km._chat_build_sig(self.sess, r, NOW, live_map={SID: r})
        a = key(row)
        self.assertEqual(self.moved(a, key(dict(row, bgTasks=[dict(task, lastTool="Read")]))), (),
                         "a task_progress that rewrote lastTool: nothing the build reads changed")
        self.assertEqual(self.moved(a, key(dict(row, ctxTokens=120_512))), (),
                         "a usage report that moved the raw count and not the percent")
        task2 = dict(task, toolUseId="tu_2", taskId="a0123456789abcde0", desc="Running Check the docs")
        self.assertEqual(self.moved(a, key(dict(row, bgTasks=[task, task2]))), ("bg", "row"), "a second task started")
        self.assertEqual(self.moved(a, key(dict(row, bgTasks=[]))), ("bg", "row"), "the task ended")
        self.assertEqual(self.moved(a, key(dict(row, bgTasks=[dict(task, desc="Running Map the lexer")]))), ("bg", "row"),
                         "the task's description landed")
        self.assertEqual(self.moved(a, key(dict(row, bgTasks=[dict(task, type="local_bash")]))), ("bg", "row"),
                         "the task's type landed")
        self.assertEqual(self.moved(a, key(dict(row, context=42))), ("row",), "the percent stepped")
        self.assertEqual(self.moved(a, key(dict(row, ctxOver=True))), ("row",), "the overflow flag flipped")
        self.assertEqual(self.moved(a, key(dict(row, subagents=[{"type": "Explore", "since": NOW - 10, "agentId": aid}]))),
                         ("row",), "a subagent started")
        self.assertEqual(self.moved(a, key(dict(row, state="working"))), ("row",))
        self.assertEqual(key(dict(row, snapT=NOW + 3)), a, "snapT moves every cycle and is not rendered")

    def test_each_clock_boolean_misses_under_clock_exactly_at_its_crossing(self):
        tm = dict(self.row, state="ready", since=NOW - 3599)
        s0 = self.sig(now=NOW, tm=tm)
        self.assertEqual(self.sig(now=NOW + 1, tm=tm), s0, "one second short of the hour: nothing moved")
        s2 = self.sig(now=NOW + 2, tm=tm)
        self.assertEqual(self.moved(s0, s2), ("clock",), "faded flips at the hour")
        km._compact_clicked[SID] = NOW - 179
        s3 = self.sig(now=NOW + 2, tm=tm)                    # 181 s: the optimistic cap ran out (the stamp is popped)
        self.assertEqual(s2, s3, "a cap that already ran out is no fact")
        km._compact_clicked[SID] = NOW - 177
        s4 = self.sig(now=NOW + 2, tm=tm)                    # 179 s: still compacting, optimistically
        self.assertEqual(self.moved(s3, s4), ("clock",))
        self.assertEqual(self.sig(now=NOW + 4, tm=tm), s3, "...back to the clicked-nothing key, exactly at the crossing")
        km._model_switch_pending[SID] = {"target": "other", "until": time.time() + 60}
        self.assertEqual(self.moved(s3, self.sig(now=NOW + 4, tm=tm)), ("clock",), "a model switch in flight")
        km._model_switch_pending.pop(SID, None)
        # the interrupt stamp, on the transcript path (a row with no interrupting flag): the stop is in
        # flight until the CLI's stop record lands or 120 s pass, and the signature's own read pops the
        # stamp at the cap exactly as the build's would. Read at or after NOW + 2, where faded already holds.
        km._interrupt_clicked[SID] = NOW - 118
        s5 = self.sig(now=NOW + 2, tm=tm)                    # 120 s: the stop is in flight
        self.assertEqual(self.moved(s3, s5), ("clock",), "a stop dispatched: interrupting")
        self.assertEqual(self.sig(now=NOW + 2, tm=tm), s5, "a repeat read holds")
        self.assertEqual(self.sig(now=NOW + 3, tm=tm), s3, "121 s: the cap ran out, back to the clicked-nothing key")
        self.assertNotIn(SID, km._interrupt_clicked, "the signature's read popped the stamp, as the build's own would")
        # the same stamp with an SDK row, whose own flag is the settle: the row component strips the flag
        # and the snapshot stamp, so the clock component alone carries it
        km._interrupt_clicked[SID] = NOW - 118
        sdk = dict(tm, interrupting=True, snapT=NOW + 2)     # a snapshot taken after the click
        s6 = self.sig(now=NOW + 2, tm=sdk)
        self.assertEqual(self.moved(s3, s6), ("clock",), "the backend's own in-flight flag, under clock alone")
        sdk["interrupting"] = False
        self.assertEqual(self.sig(now=NOW + 2, tm=sdk), s3, "the flag's settle: back to the clicked-nothing key")
        self.assertNotIn(SID, km._interrupt_clicked)

    def test_parked_ops_miss_under_ops_and_the_limit_hold_is_read_only_while_something_is_queued(self):
        a = self.sig()
        lim = km._CHAT_SIG_LABELS.index("limit")
        self.assertIsNone(a[lim], "nothing queued or parked: the hold is not read")
        km._pending_ops[SID] = [("send", "a parked message", True)]
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("ops",), "the parked op; the hold is now read and, unlimited, still None")
        km._set_retry_paused(True, reason="spend")
        try:
            c = self.sig()
            self.assertEqual(self.moved(b, c), ("limit",), "the spend hold the queued bubble names")
            self.assertEqual(c[lim]["reason"], "spend")
        finally:
            km._set_retry_paused(False)
        km._pending_ops.pop(SID, None)
        km._set_retry_paused(True, reason="spend")
        try:
            self.assertEqual(self.sig(), a, "with no bubble to render the hold is neither read nor keyed")
        finally:
            km._set_retry_paused(False)

    def test_the_limit_hold_is_read_for_a_non_forwarding_backends_in_flight_echo_too(self):
        be = self.tail_backend()
        lim = km._CHAT_SIG_LABELS.index("limit")
        self.assertIsNone(self.sig()[lim])
        be.atoms[SID] = [{"t": NOW, "text": "typed while busy", "author": "human"}]
        km._set_retry_paused(True, reason="spend")
        try:
            self.assertEqual(self.sig()[lim]["reason"], "spend",
                             "a non-forwarding backend's echo folds into the queue while busy, so the hold is read")
        finally:
            km._set_retry_paused(False)

    def test_the_retry_state_misses_under_retry(self):
        a = self.sig()
        km._auto_retry_state[SID] = {"n": 2, "next": NOW + 30}
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("retry",))
        self.assertTrue(km._write_retry_suppress({SID: NOW}), "the suppression ledger's one writer")
        self.assertEqual(self.moved(b, self.sig()), ("retry",))

    def test_the_live_task_rows_miss_under_bg_as_the_builds_own_filter_returns_them(self):
        """The bg component is the row list _bg_live_norm returns, the build's own filtered view (a row
        drops out of it when em._bg_expired says its deadline passed, which that filter decides against
        the wall clock); the component follows the list, so a row leaving it is a change."""
        saved = km._bg_live_norm
        rows = [{"tid": "t1", "desc": "a watcher", "t": NOW - 60, "type": "local_bash"}]
        km._bg_live_norm = lambda sid, path: list(rows)
        try:
            a = self.sig()
            self.assertEqual(self.sig(now=NOW + 1), a)
            rows.clear()                                  # the filter dropped the row (as at its deadline)
            self.assertEqual(self.moved(a, self.sig(now=NOW + 2)), ("bg",))
        finally:
            km._bg_live_norm = saved

    def test_a_kernel_watch_misses_under_watch_and_so_does_one_replaced_under_the_same_note(self):
        a = self.sig()
        with km._watch_lock:
            km._watches.append({"id": "w-1", "cmd": "test -f done", "every": 60, "timeoutS": 600, "sid": SID,
                                "note": "the build", "at": NOW - 5})
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("watch",))
        with km._watch_lock:                              # cancelled and re-armed under the same note, a new id and predicate
            km._watches[:] = [w for w in km._watches if w.get("id") != "w-1"]
            km._watches.append({"id": "w-2", "cmd": "test -f really-done", "every": 60, "timeoutS": 600, "sid": SID,
                                "note": "the build", "at": NOW - 5})
        self.assertEqual(self.moved(b, self.sig()), ("watch",), "the cancel handle and the predicate the box renders moved")

    def test_the_awaiting_stamp_view_misses_under_stamp_when_the_postal_log_lands(self):
        """The durable awaiting stamp is read through _session_stamp_read, whose memo is keyed on the store, the
        override journal and the POSTAL LOG: a peer's answer supersedes a peer wait, so mail landing changes the
        chip of a tab that carries no postal card at all. The supersede predicate is held here (its own tests
        are elsewhere); the log append is the real event that re-reads the view."""
        a = self.sig()
        g = SID + ":g1"
        self.store({g: {"id": g, "text": "wait for the api", "parentId": None, "t": T0, "awaitingAt": T0 + 10,
                        "awaitingWhy": "waiting on api", "trail": []}}, {g: "working"})
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("stamp", "store"), "the stamp filed: the store's identity and the stamp view")
        saved = km._peer_stamp_superseded
        km._peer_stamp_superseded = lambda nd, answered: T0 + 20   # the awaited answer arrived after the stamp
        try:
            self.assertEqual(self.sig(), b, "the predicate is not re-asked while the view's inputs stand")
            jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
            with open(jd.MESSAGES, "a") as f:
                f.write(json.dumps({"ev": "sent", "id": "m1", "from_id": PEER, "to_id": SID, "body": "done", "t": T0 + 20}) + "\n")
            moved = self.moved(b, self.sig())
            self.assertIn("stamp", moved, "the log moved, the view re-read, the stamp superseded")
            self.assertLessEqual(set(moved), {"stamp", "postal"})
        finally:
            km._peer_stamp_superseded = saved

    def test_the_anchor_revision_the_suspension_list_and_the_names_digest(self):
        a = self.sig()
        km._node_anchor_rev[SID] = km._node_anchor_rev.get(SID, 0) + 1
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("anchors",))
        km._downtime.append((NOW - 500, NOW - 400))
        c = self.sig()
        self.assertEqual(self.moved(b, c), ("downtime",))
        (jd.NAMES / SID).write_text("web-renamed\t%s\t#1EA1EB\twhite\n" % self.cdir)
        self.assertEqual(self.moved(c, self.sig()), ("names",))

    def test_the_shared_files_each_miss_under_their_own_label(self):
        a = self.sig()
        (jd.STATE / "session-flags.json").write_text(json.dumps({SID: {"hideFromFeed": True}}))
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("flags",))
        (jd.STATE / "notify-cards.json").write_text(json.dumps({"*": True}))
        c = self.sig()
        self.assertEqual(self.moved(b, c), ("ncards",))
        (jd.STATE / "colormap").write_text("viridis\n")
        d = self.sig()
        self.assertEqual(self.moved(c, d), ("colormap",))
        with open(jd.STATE / "cleared.jsonl", "a") as f:
            f.write(json.dumps({"id": "x", "t": NOW}) + "\n")
        e = self.sig()
        self.assertEqual(self.moved(d, e), ("cleared",))
        os.environ["ROMP_HOST_NAME"] = "otherhost"
        self.assertEqual(self.moved(e, self.sig()), ("host",))

    def test_the_working_note_misses_under_note_and_its_clear_restores_the_signature(self):
        # the ledger carries the session's postal working note (the section-at-a-glance row's second line); a
        # note write touches no transcript, states file or store, so it is a component of its own, by identity
        a = self.sig()
        km._set_working_note(SID, "editing the notes-api tests")
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("note",))
        self.assertEqual(self.sig(), b, "byte-stable while the note stands: no rebuild per push")
        km._set_working_note(SID, "")
        self.assertEqual(self.sig(), a, "the clear (the kernel's idle-and-done lift, or the session's own) restores it")

    def test_the_feed_verdict_misses_under_needs_only_as_a_verdict_on_this_session(self):
        # the ledger carries the feed's per-session needs-you (needsInput); the set behind it is None until the
        # first feed build since start, and a push builds the chat sessions before the feed, so the raw tri-state would
        # give every tab a None on the first push and a False on the next: a whole-strip rebuild for a value the
        # row reads the same. Only True is a verdict.
        saved = km._feed_needs_input[0]
        try:
            km._feed_needs_input[0] = None
            a = self.sig()
            km._feed_needs_input[0] = frozenset()
            self.assertEqual(self.sig(), a, "the first feed build, no card of this session under needs-you: nothing the row shows changed")
            km._feed_needs_input[0] = frozenset([PEER])
            self.assertEqual(self.sig(), a, "another session's card: not this tab's")
            km._feed_needs_input[0] = frozenset([SID])
            self.assertEqual(self.moved(a, self.sig()), ("needs",), "a card of this session under needs-you is the verdict that rebuilds")
        finally:
            km._feed_needs_input[0] = saved

    def test_the_needs_you_count_keys_the_signature_so_a_stale_badge_never_survives_a_judge_pass(self):
        # the numbered badge (plans/tab-state-badge.md) reads needsYouCount; a session already under needs-you whose card
        # count moves (a judge pass files or clears one) stays in the SET, so the boolean is unchanged and only the count
        # keys the rebuild. None (before the first feed build) and 0 (no card) share the no-dot value: neither rebuilds.
        saved_in, saved_ct = km._feed_needs_input[0], km._feed_needs_input_count[0]
        try:
            km._feed_needs_input[0] = frozenset([SID])
            km._feed_needs_input_count[0] = None
            a = self.sig()
            km._feed_needs_input_count[0] = {SID: 0}
            self.assertEqual(self.sig(), a, "None and 0 share the no-dot value: no rebuild before the first feed build")
            km._feed_needs_input_count[0] = {SID: 1}
            b = self.sig()
            self.assertEqual(self.moved(a, b), ("needs",), "the first card gives the dot its count: the badge rebuilds")
            km._feed_needs_input_count[0] = {SID: 3}
            self.assertEqual(self.moved(b, self.sig()), ("needs",), "the count moving 1 to 3 rebuilds, so the number never goes stale under an unchanged membership")
            km._feed_needs_input_count[0] = {PEER: 9}
            self.assertEqual(self.sig(), a, "another session's count is not this tab's")
        finally:
            km._feed_needs_input[0], km._feed_needs_input_count[0] = saved_in, saved_ct

    def test_a_goal_rows_face_moves_notices_and_its_time_does_not(self):
        # the Needs you box's goal rows (plans/needs-you.md, phase three) ride the `notices` label by id AND face: a brief landing, a
        # Continue offered, a retitle (the judge retitles a top under its id) or the credential fix repaints the box; the row's `t` is
        # unkeyed (_NEEDS_ROW_UNKEYED: the box does not draw it), so a re-file that moves only the time rebuilds nothing
        saved_in, saved_rows = km._feed_needs_input[0], km._feed_needs_rows[0]
        try:
            km._feed_needs_input[0] = frozenset([SID])
            row = {"itemId": SID + ":g9", "kind": "goal", "title": "wire the fixtures", "body": "", "cont": False, "t": 1}
            km._feed_needs_rows[0] = {SID: [dict(row)]}
            a = self.sig()
            km._feed_needs_rows[0] = {SID: [dict(row, t=2)]}
            self.assertEqual(self.sig(), a, "the time alone: unkeyed, nothing the row draws")
            for k, v in (("body", "which database does the suite target?"), ("cont", True), ("title", "wire the fixtures into the suite"), ("fix", "credential")):
                km._feed_needs_rows[0] = {SID: [dict(row)]}                  # from the base row each time: one field moves, nothing reverts
                b = self.sig()
                km._feed_needs_rows[0] = {SID: [dict(row, **{k: v})]}
                self.assertEqual(self.moved(b, self.sig()), ("notices",), "the row's %s is its face: it moves the label" % k)
            km._feed_needs_rows[0] = {SID: []}
            self.assertEqual(self.moved(self.sig(), a), ("notices",), "a row coming or going moves it too")
        finally:
            km._feed_needs_input[0], km._feed_needs_rows[0] = saved_in, saved_rows

    def test_every_card_field_on_a_goal_row_moves_the_notices_label(self):
        # the row carries what the card carries (plans/needs-you.md, round two of the box content PR): each of the card's fields on the row is
        # FLIPPED against the signature, one at a time from the same base row, and each flip moves the notices label. The source-text
        # cross-check below reads that every name is keyed; this reads that the key is read from the row itself (a key read from the wrong
        # dict, or a field name mistyped in the tuple, passes the text and fails here). A structured sentinel, since the fields are shapes.
        saved_in, saved_rows = km._feed_needs_input[0], km._feed_needs_rows[0]
        try:
            km._feed_needs_input[0] = frozenset([SID])
            base = {"itemId": SID + ":g9", "kind": "goal", "title": "wire the fixtures", "body": "", "cont": False, "t": 1}
            base.update({f: None for f in km._NEEDS_ROW_CARD_FIELDS})
            for f in km._NEEDS_ROW_CARD_FIELDS:
                with self.subTest(field=f):
                    km._feed_needs_rows[0] = {SID: [dict(base)]}
                    before = self.sig()
                    km._feed_needs_rows[0] = {SID: [dict(base, **{f: {"moved": f}})]}
                    self.assertEqual(self.moved(before, self.sig()), ("notices",), "the row's %s moves the label" % f)
            # and ONE INNER ELEMENT of a structured value (a contributor's review of PR 2124: a None-to-value move passes a key that is constant
            # per structure; the key must read the value itself)
            shaped = {"briefParts": ([{"id": "a", "since": 1}, {"id": "b", "since": 2}], lambda v: [dict(v[0], since=9), v[1]]),
                      "summaryParts": ([{"id": "a", "since": 1}, {"id": "b", "since": 2}], lambda v: [v[0], dict(v[1], since=9)]),
                      "stalled": ({"why": "no turn in 2h", "since": 1, "note": None}, lambda v: dict(v, note="the fixtures wait on a port")),
                      "tree": ([{"id": "g9", "kind": "ask", "text": "wire the fixtures", "status": "open", "children": ["g9a"]}, {"id": "g9a", "kind": "ask", "text": "pick a port", "status": "open", "children": []}],
                               lambda v: [v[0], dict(v[1], status="done")]),
                      "awaiting": ({"why": "a job on the cluster", "kind": "task", "since": 5}, lambda v: dict(v, why="a second job on the cluster")),
                      "nudged": ({"count": 1, "times": [10]}, lambda v: {"count": 2, "times": [10, 20]}),
                      "waitingOn": ({"name": "api", "kind": "delegate", "since": 3}, lambda v: dict(v, name="tests")),
                      "origin": ({"peer": "api", "peerSid": SID + "-api", "live": True}, lambda v: dict(v, live=False)),
                      "handoffTo": ({"peer": "api", "peerSid": SID + "-api"}, lambda v: dict(v, peer="tests")),
                      # the six a contributor's second note on PR 2124 found missing: under a key constant per structure a second warning left the row stale
                      "warns": ([{"kind": "brief-failed", "t": 1, "msg": "the brief could not be written", "detail": ""}], lambda v: v + [{"kind": "summary-failed", "t": 2, "msg": "the takeaway could not be written", "detail": ""}]),
                      "failLog": ([{"t": 1, "line": "brief", "model": "opus", "note": "529"}], lambda v: [dict(v[0], note="overloaded")]),
                      "summaryAnchorsPara": ([{"u": "a1"}, None], lambda v: [dict(v[0], q="the fixtures"), None]),
                      "working": ({"since": 10, "toolUses": 3}, lambda v: dict(v, toolUses=4)),
                      "delegTracked": ([{"sid": SID + "-w", "name": "web"}], lambda v: [dict(v[0], name="worker")])}
            # WHICH fields are structured is read from the kernel's own row, never from a second hand-written list (the contributor's note on the
            # 0.17.1 fix: a constant read by no kernel code tied the table to itself, and a structured field added later to the card fields but
            # not to the constant would have passed as a scalar move): the one fully populated ask the row projection test reads too
            # (tests/needs_row_fixture.py), built into a row through _needs_you_rows, each field classified by what the ROW carries (a dict or a
            # list). A live-block object never rides a plain row (the credential floor builds the fix row, which carries no card field; every
            # other live block is a hard stop, which takes no row), so the row's blocked is None by construction and its move is the scalar
            # loop's; the derivation says so where a hand-written list had it structured
            row = km._needs_you_rows({"asks": [populated_ask(SID, km._board_needs_you(None))]})[SID][0]
            structured = {f for f in km._NEEDS_ROW_CARD_FIELDS if isinstance(row.get(f), (dict, list))}
            self.assertEqual(structured, set(shaped), "the table moves every structured member of the row's card fields, no more and no fewer (read from the row the kernel builds)")
            self.assertEqual({f for f in km._NEEDS_ROW_CARD_FIELDS if row.get(f) is None}, {"blocked"}, "premise: every card field but the live block rode onto the row")
            # a SCALAR member moves to another scalar (nudgeFailed among them: a bool)
            for f in set(km._NEEDS_ROW_CARD_FIELDS) - structured:
                with self.subTest(field=f, scalar=True):
                    km._feed_needs_rows[0] = {SID: [dict(base, **{f: "one"})]}
                    before = self.sig()
                    km._feed_needs_rows[0] = {SID: [dict(base, **{f: "another"})]}
                    self.assertEqual(self.moved(before, self.sig()), ("notices",), "a scalar move of the row's %s moves the label" % f)
            for f, (value, move) in shaped.items():
                with self.subTest(field=f, inner=True):
                    km._feed_needs_rows[0] = {SID: [dict(base, **{f: value})]}
                    before = self.sig()
                    km._feed_needs_rows[0] = {SID: [dict(base, **{f: move(value)})]}
                    self.assertEqual(self.moved(before, self.sig()), ("notices",), "one inner element of the row's %s moves the label" % f)
        finally:
            km._feed_needs_input[0], km._feed_needs_rows[0] = saved_in, saved_rows

    def test_the_unkeyed_row_field_is_pinned_by_value_and_the_rows_other_fields_are_the_key(self):
        # _NEEDS_ROW_UNKEYED by value, as _CHAT_ROW_UNKEYED is; and every field a goal row carries but the unkeyed one is in the
        # signature's tuple, so a field added to _needs_you_rows without a place in the key fails here (the third review of PR 1967)
        self.assertEqual(km._NEEDS_ROW_UNKEYED, {"t"})
        # the rows BUILT on a synthetic frame (the fourth executed review: the AST read kept only literals whose keys are all string constants,
        # so a spread or a subscripted field passed it), so a field added under any name, by any shape, fails the equality below until the key
        # reads it or the set declares it; both row shapes, the plain row and the credential row
        feed = {"asks": [{"itemId": SID + ":g1", "sid": SID, "text": "wire the fixtures", "category": "needs_input", "blockSummary": "which database does the suite target?", "live": True, "t": 5},
                         {"itemId": SID + ":g2", "sid": SID, "text": "fix the key", "category": "needs_input", "live": True, "t": 6,
                          "blocked": {"state": "judgeAuth", "what": "the judges' credential was refused"}}]}
        rows = km._needs_you_rows(feed).get(SID) or []
        self.assertEqual([r["itemId"] for r in rows], [SID + ":g1", SID + ":g2"], "the plain row and the credential row: %r" % rows)
        fields = set().union(*(set(r) for r in rows))
        CARD_FIELDS = ("summary", "blockSummary", "briefParts", "summaryParts", "distillState", "summaryStale", "relayNote", "background",
               "stalled", "tree", "awaiting", "recheck", "rejudging", "nudgeFailed", "nudged", "interrupting", "interrupted", "waitingOn", "origin", "handoffTo",
               "warns", "failLog", "summaryAnchorUuid", "summaryAnchorQuote", "summaryAnchorsPara", "doneConfirming", "blocked", "column", "judging", "working", "sessState", "delegTracked")   # written out, so a kernel whose rows lack them reds here on the rows (test_chat_notices holds the kernel's list to this one)
        self.assertEqual(fields - km._NEEDS_ROW_UNKEYED, {"itemId", "kind", "title", "body", "cont", "fix"} | set(CARD_FIELDS), "every field a built row carries is keyed or declared unkeyed (the card's fields since the row carries what the card carries)")
        # and the row literals' keys from the AST (the third review) read the same set: a second reading of the same rows
        tree = ast.parse(inspect.getsource(km._needs_you_rows).lstrip())
        lit = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict) and node.keys and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys):
                keys = {k.value for k in node.keys}
                if "itemId" in keys:
                    lit |= keys
        self.assertEqual(lit, fields, "the literals and the built rows carry the same fields")
        tup = inspect.getsource(km._chat_build_sig)
        for f in sorted(fields - km._NEEDS_ROW_UNKEYED):
            self.assertIn(('n["%s"]' % f) if f == "itemId" else ('n.get("%s")' % f), tup, "the key reads the row's %s" % f)
        self.assertNotIn('n.get("t")', tup, "and not the unkeyed time")

    def test_the_billing_readers_move_acct(self):
        a = self.sig()
        km._claude_account_label = lambda: "someone@example.invalid"
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("acct",), "the login label")
        km._auth_avail_status = lambda: {"login": True, "key": False, "keyWhy": "no helper"}
        self.assertEqual(self.moved(b, self.sig()), ("acct",), "the availability half the Billing hover reads")

    def test_the_cwd_rows_and_the_claudemd_chain(self):
        a = self.sig()
        (self.cdir / "CLAUDE.md").write_text("# project rules\n")
        b = self.sig()
        self.assertEqual(self.moved(a, b), ("claudemd",))
        (jd.NAMES / SID).write_text("web\t%s\t#1EA1EB\twhite\n" % (self.cdir / "sub"))
        moved = self.moved(b, self.sig())
        self.assertIn("cwd", moved)
        self.assertLessEqual(set(moved), {"cwd", "claudemd", "names"})

    def test_the_handed_liveness_map_is_served_to_every_nested_read(self):
        """_chat_build_sig serves the map it was handed (_serve_live) to the reads beneath it, so the bg
        component (through _bg_live_norm), the awaiting sources and the watch rows read the snapshot the
        build will, and a signature outside a pusher cycle (a connect push on a handler thread) takes no
        fresh liveness read and sweeps no registry per tab. A fresh liveness read during the call is the failure."""
        def fresh():
            raise AssertionError("a nested read took a fresh liveness snapshot instead of the handed map")
        km._live_map = self.saved[5]                 # the kernel's own reader: the served snapshot, else Sessions.live()
        saved_live = km.Sessions.live
        km.Sessions.live = staticmethod(fresh)
        aid = "a0123456789abcdef"
        row = dict(self.row)
        row["bgTasks"] = [{"toolUseId": "tu_1", "desc": "Running Map the parser", "since": NOW - 30,
                           "type": "local_agent", "taskId": aid}]
        try:
            s = km._chat_build_sig(self.sess, row, NOW, live_map={SID: row})
            self.assertEqual(s[km._CHAT_SIG_LABELS.index("bg")],
                             (("tu_1", "Map the parser", NOW - 30, "local_agent", None, aid),),
                             "the task rows come from the handed row, through the build's own normalizer")
            self.assertIsNone(getattr(km._live_scope, "snapshot", None), "the scope closes with the call")
            s2 = km._chat_build_sig(self.sess, row, NOW, live_map={SID: row})
            self.assertEqual(s, s2)
        finally:
            km.Sessions.live = saved_live

    def test_a_recorded_task_output_that_grows_misses_under_taskout(self):
        out = Path(self.td.name) / "task-out.log"
        out.write_text("line 1\n")
        rec = {"task_outs": [(str(out), km._chat_stat_key(str(out)))], "pl_pending": [], "pl_at": (), "pl_check": None,
               "postal_any": False, "postal_cards": []}
        a = self.sig(deps=rec)
        self.assertEqual(self.sig(deps=rec), a)
        with open(out, "a") as f:
            f.write("line 2\n")
        self.assertEqual(self.moved(a, self.sig(deps=rec)), ("taskout",))
        absent = Path(self.td.name) / "not-yet.log"
        rec2 = {"task_outs": [(str(absent), None)], "pl_pending": [], "pl_at": (), "pl_check": None,
                "postal_any": False, "postal_cards": []}
        b = self.sig(deps=rec2)
        absent.write_text("appeared\n")
        self.assertEqual(self.moved(b, self.sig(deps=rec2)), ("taskout",), "a file the build found absent appearing is a change")

    def test_a_pending_path_token_whose_file_appears_misses_under_pathlink(self):
        md = "see notes/report.md for the numbers"
        km._PATH_LINK_CACHE[(SID, "u9")] = ({}, ("notes/report.md",), {})
        rec = {"task_outs": [], "pl_pending": [("u9", md)], "pl_at": (("u9", None, None),), "pl_check": None,
               "postal_any": False, "postal_cards": []}
        a = self.sig(deps=rec)
        self.assertEqual(self.sig(deps=rec), a, "unresolved and nothing moved: the record's answers hold")
        (self.cdir / "notes").mkdir()
        (self.cdir / "notes" / "report.md").write_text("42\n")
        b = self.sig(deps=rec)
        self.assertEqual(self.moved(a, b), ("pathlink",), "the mention became a link")

    def test_a_fresh_worlds_pin_sidecar_is_read_under_a_reused_id(self):
        """The pin sidecar is loaded once per sid and served from memory from then on (_pin_assoc), so a
        world that resolved a token leaves the next world reading a sidecar it never wrote: an empty map
        where its own file says a pin was latched. The hazard is cross-test by construction, so this test
        runs two worlds itself, through the fixture's own hooks: the first resolves notes/report.md for u9,
        which loads the empty sidecar; the second writes one sidecar row for the same message and must
        build with that pin."""
        def pending():
            km._PATH_LINK_CACHE[(SID, "u9")] = ({}, ("notes/report.md",), {})
            (self.cdir / "notes").mkdir()
            (self.cdir / "notes" / "report.md").write_text("42\n")
            return {"task_outs": [], "pl_pending": [("u9", "see notes/report.md for the numbers")],
                    "pl_at": (("u9", None, None),), "pl_check": None, "postal_any": False, "postal_cards": []}
        self.sig(deps=pending())                            # the first world: the resolve loads SID's sidecar, which is empty
        self.tearDown()
        self.setUp()                                        # the next test's world (its temp dir is cleaned by the final tearDown)
        with open(km._pin_assoc_dir() / (SID + ".jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"u": "u9", "t": "notes/report.md", "p": "pin1"}) + "\n")
        s = self.sig(deps=pending())
        self.assertEqual(s[km._CHAT_SIG_LABELS.index("pathlink")][0][2], {"notes/report.md": "pin1"},
                         "pinned from this world's sidecar, not a prior world's memo")

    def test_each_world_latches_its_own_pin_directory(self):
        """The pin directory latches at first use (_pin_dir) and the sidecar's memo is cleared per sid at
        tearDown, so a later world's first resolve reloads the sidecar through _pin_assoc_dir, which
        mkdirs under the latched path. Left alone, that path is a torn-down world's: the fixture resets
        the latch per world and restores it at tearDown, so each resolve lands under its own state root
        and nothing is recreated under a removed one. Two worlds through the fixture's own hooks, as the
        sidecar test above. Between them the latch must no longer point at the first world, and is then
        set back to it, the value a fixture that never reset would inherit, so the second setUp has to
        reset it rather than find it clear."""
        def pending():
            km._PATH_LINK_CACHE[(SID, "u9")] = ({}, ("notes/report.md",), {})
            (self.cdir / "notes").mkdir()
            (self.cdir / "notes" / "report.md").write_text("42\n")
            return {"task_outs": [], "pl_pending": [("u9", "see notes/report.md for the numbers")],
                    "pl_at": (("u9", None, None),), "pl_check": None, "postal_any": False, "postal_cards": []}
        self.sig(deps=pending())                            # the first world resolves: the pin dir latches
        first = Path(self.td.name)
        self.tearDown()
        self.assertNotEqual(km._MENTION_PINS, first / "state" / "mention-pins",
                            "the latch does not point at the torn-down world")
        self.addCleanup(setattr, km, "_MENTION_PINS", km._MENTION_PINS)   # the final tearDown restores the stale value set below; put back the one before it
        km._MENTION_PINS = first / "state" / "mention-pins"   # a stale latch on the removed world: setUp resets it rather than inheriting it
        self.setUp()                                        # the next test's world (its temp dir is cleaned by the final tearDown)
        self.sig(deps=pending())                            # the memo was popped, so the resolve reloads through _pin_assoc_dir
        self.assertEqual(km._MENTION_PINS, jd.STATE / "mention-pins", "the pin directory is this world's")
        self.assertFalse(first.exists(), "nothing is recreated under the torn-down first world")

    def test_a_postal_dependency_misses_under_postal_when_the_log_moves_or_a_caption_changes(self):
        caps = {}
        saved = km._msg_summaries
        km._msg_summaries = lambda: dict(caps)
        try:
            jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
            with open(jd.MESSAGES, "a") as f:
                f.write(json.dumps({"ev": "sent", "id": "m7", "from_id": PEER, "to_id": SID, "body": "hello", "t": T0}) + "\n")
            card = {"kind": "postal-service", "direction": "in", "mid": "m7", "peer": "api"}
            rec = {"task_outs": [], "pl_pending": [], "pl_at": (), "pl_check": None, "postal_any": True, "postal_cards": [card]}
            a = self.sig(deps=rec)
            self.assertEqual(self.sig(deps=rec), a)
            caps["m7"] = "api: said hello"
            b = self.sig(deps=rec)
            self.assertEqual(self.moved(a, b), ("postal",), "a caption the card embeds landed")
            with open(jd.MESSAGES, "a") as f:
                f.write(json.dumps({"ev": "sent", "id": "m8", "from_id": PEER, "to_id": SID, "body": "more", "t": T0 + 1}) + "\n")
            moved = self.moved(b, self.sig(deps=rec))
            self.assertIn("postal", moved, "the log moved")
        finally:
            km._msg_summaries = saved

    def test_mail_between_two_other_sessions_leaves_the_postal_dependency_unmoved(self):
        """The postal component folds THIS session's revision of the postal log (2026-09-18): the records
        addressed to or from it, their outcomes, and the records with no recipient. A message between two
        other sessions, or an outcome on one, moves the log's identity and nothing this tab renders, so the
        component holds; a record touching this session, an outcome on one of its own messages, or a record
        with no recipient (which hydrates in every chat) moves it."""
        saved = km._msg_summaries
        km._msg_summaries = lambda: {}
        self.addCleanup(km._postal_index_memo.__setitem__, 0, None)
        km._postal_index_memo[0] = None
        try:
            jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)

            def row(r):
                with open(jd.MESSAGES, "a") as f:
                    f.write(json.dumps(r) + "\n")
            row({"ev": "sent", "id": "m7", "from_id": PEER, "to_id": SID, "body": "hello", "t": T0})
            card = {"kind": "postal-service", "direction": "in", "mid": "m7", "peer": "api"}
            rec = {"task_outs": [], "pl_pending": [], "pl_at": (), "pl_check": None, "postal_any": True, "postal_cards": [card]}
            a = self.sig(deps=rec)
            row({"ev": "sent", "id": "m8", "from_id": OTHER_A, "to_id": OTHER_B, "body": "unrelated", "t": T0 + 1})
            b = self.sig(deps=rec)
            self.assertEqual(self.moved(a, b), (), "mail between two other sessions: nothing this tab renders moved")
            row({"ev": "exec", "id": "m8", "t": T0 + 2})
            c = self.sig(deps=rec)
            self.assertEqual(self.moved(b, c), (), "an outcome on a third party's message: still nothing")
            row({"ev": "sent", "id": "m9", "from_id": PEER, "to_id": SID, "body": "more", "t": T0 + 3})
            d = self.sig(deps=rec)
            self.assertEqual(self.moved(c, d), ("postal",), "a record addressed to this session")
            # an outgoing card joined to its own row: an outcome landing on that row is the receipt the card renders
            row({"ev": "sent", "id": "m10", "from_id": SID, "to_id": PEER, "body": "ship it", "t": T0 + 4})
            out = {"kind": "postal-service", "direction": "out", "peer": "api", "mid": "m10", "body": "ship it"}
            rec2 = {"task_outs": [], "pl_pending": [], "pl_at": (), "pl_check": None, "postal_any": True, "postal_cards": [out]}
            e = self.sig(deps=rec2)
            row({"ev": "exec", "id": "m10", "t": T0 + 5})
            f = self.sig(deps=rec2)
            self.assertEqual(self.moved(e, f), ("postal",), "the receipt on this session's own message moved")
            row({"ev": "sent", "id": "m11", "from_id": "", "to_id": "", "body": "pre-schema", "t": T0 + 6})
            g = self.sig(deps=rec2)
            self.assertEqual(self.moved(f, g), ("postal",), "a record with no recipient hydrates in every chat")
        finally:
            km._msg_summaries = saved

    def test_opposite_outcomes_on_two_of_this_sessions_messages_move_the_postal_dependency(self):
        """The revision folds the outcome VALUES, not a count (2026-09-18, a review find on the design): an
        unexec on one of this session's messages beside an exec on another leaves a count where it was while
        both cards' receipts changed. By value the two rows cannot net to no change."""
        saved = km._msg_summaries
        km._msg_summaries = lambda: {}
        self.addCleanup(km._postal_index_memo.__setitem__, 0, None)
        km._postal_index_memo[0] = None
        try:
            jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)

            def rows(*rs):
                with open(jd.MESSAGES, "a") as f:
                    for r in rs:
                        f.write(json.dumps(r) + "\n")
            rows({"ev": "sent", "id": "m10", "from_id": SID, "to_id": PEER, "body": "ship it", "t": T0},
                 {"ev": "sent", "id": "m12", "from_id": SID, "to_id": PEER, "body": "and the docs", "t": T0 + 1})
            cards = [{"kind": "postal-service", "direction": "out", "peer": "api", "mid": "m10", "body": "ship it"},
                     {"kind": "postal-service", "direction": "out", "peer": "api", "mid": "m12", "body": "and the docs"}]
            rec = {"task_outs": [], "pl_pending": [], "pl_at": (), "pl_check": None, "postal_any": True, "postal_cards": cards}
            a = self.sig(deps=rec)
            rows({"ev": "exec", "id": "m10", "t": T0 + 2})
            b = self.sig(deps=rec)
            self.assertEqual(self.moved(a, b), ("postal",), "the first message was read")
            rows({"ev": "unexec", "id": "m10", "t": T0 + 3}, {"ev": "exec", "id": "m12", "t": T0 + 4})
            c = self.sig(deps=rec)
            self.assertEqual(self.moved(b, c), ("postal",),
                             "one receipt went back to pending and another landed: two cards changed")
        finally:
            km._msg_summaries = saved


class KeyCost(_World):
    def test_the_precheck_vouches_and_a_quiet_cycle_re_resolves_nothing(self):
        md = "see notes/report.md and docs/plan.md"
        km._PATH_LINK_CACHE[(SID, "u9")] = ({}, ("notes/report.md", "docs/plan.md"), {})
        # tokens present, none resolved: the build stores pathLinks {} (a verdict was rendered), no pins
        rec = {"task_outs": [], "pl_pending": [("u9", md)], "pl_at": (("u9", {}, None),), "pl_check": None,
               "postal_any": False, "postal_cards": []}
        km._live_scope.chat_shared = None
        self.sig(deps=rec)                                    # the first cycle re-resolves and vouches
        self.assertIsNotNone(rec["pl_check"], "every answer held: the pre-check is stored")
        calls = []
        saved = km._path_links
        km._path_links = lambda *a, **k: calls.append(a) or saved(*a, **k)
        try:
            km._live_scope.chat_shared = None
            self.sig(deps=rec)
            self.assertEqual(calls, [], "no candidate directory moved: nothing is re-resolved")
            (self.cdir / "docs").mkdir()
            (self.cdir / "docs" / "plan.md").write_text("the plan\n")
            km._live_scope.chat_shared = None
            s = self.sig(deps=rec)
            self.assertEqual(len(calls), 1, "a moved directory re-resolves the message that named it")
            self.assertEqual(s[km._CHAT_SIG_LABELS.index("pathlink")][0][1], {"docs/plan.md": "docs/plan.md"})
        finally:
            km._path_links = saved

    def test_a_push_outside_a_cycle_opens_its_own_scopes_and_closes_them(self):
        km._live_scope.names = None
        km._live_scope.msgsum = None
        km._chat_push_scopes_open()
        try:
            self.assertIsNotNone(km._live_scope.chat_shared)
            self.assertIsNotNone(km._live_scope.names)
            self.assertIsNotNone(km._live_scope.msgsum)
        finally:
            km._chat_push_scopes_close()
        self.assertIsNone(km._live_scope.chat_shared)
        self.assertIsNone(km._live_scope.names, "a scope this push opened is closed by it")
        self.assertIsNone(km._live_scope.msgsum)
        km._live_scope.names = {"kept": ["x"]}
        km._chat_push_scopes_open()
        km._chat_push_scopes_close()
        self.assertEqual(km._live_scope.names, {"kept": ["x"]}, "a cycle's own scope is never touched")
        km._live_scope.names = None

    def test_a_cycle_closes_the_push_scopes_its_jobs_left_open(self):
        """A push that raised between _chat_push_scopes_open and its close leaves the shared components on
        the pusher thread; _pusher_cycle's finally closes them, so the next cycle reads them fresh."""
        saved = km._pusher_cycle_jobs

        def jobs(now, live_map, any_client):
            km._chat_push_scopes_open()                       # ...and nothing closes them
            self.assertIsNotNone(km._live_scope.chat_shared)
        km._pusher_cycle_jobs = jobs
        try:
            km._pusher_cycle()
        finally:
            km._pusher_cycle_jobs = saved
        self.assertIsNone(getattr(km._live_scope, "chat_shared", None), "closed by the cycle's finally")
        self.assertIsNone(getattr(km._live_scope, "chat_push_owned", None))
        self.assertIsNone(getattr(km._live_scope, "names", None), "the cycle's own scopes are closed too")


# ── the pusher: one key for every tab ─────────────────────────────────────────────────────────────
SID_A = "77777777-8888-9999-aaaa-ddddddddddd1"
SID_B = "77777777-8888-9999-aaaa-ddddddddddd2"


class Pusher(unittest.TestCase):
    """_push with the builders stubbed: which tabs rebuild, and why."""

    STUBS = ("_live_map", "_live_names", "_chat_tab_sessions", "build_session",
             "_cached_feed", "_cached_timeline", "build_timeline", "_fleet_view_sig", "_comments_frame",
             "_retry_parked_creates", "_sdk")

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = td.name
        names = Path(self.tmp) / "names"
        names.mkdir()
        for sid, nm in ((SID_A, "web"), (SID_B, "api")):
            (names / sid).write_text("%s\t/proj/TESTHOST/app\t#1EA1EB\twhite\n" % nm)
        self.tx = {sid: Path(self.tmp) / (sid + ".jsonl") for sid in (SID_A, SID_B)}
        for p in self.tx.values():
            p.write_text('{"type": "user"}\n')
        self.saved = {nm: getattr(km, nm) for nm in self.STUBS}
        self.saved_state = (jd.STATE, dict(km._built_chat), dict(km._prev_chat_events),
                            dict(km._prev_chat_ledger), list(km._last_tab_order), km._judge_gen[0], km.NAMES,
                            [set(km._thread_fold_keep[0]), set(km._thread_fold_keep[1])])
        jd._rebind_state(Path(self.tmp) / "state")
        for d in (jd.STATESDIR, jd.GOALDIR):
            d.mkdir(parents=True, exist_ok=True)
        km.NAMES = names
        self.live_map = {}
        km._live_map = lambda: dict(self.live_map)
        km._sdk = lambda: None
        self.tail = _TailBackend()                            # the owning backend, its tail moved by hand
        self._saved_be = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: self.tail)
        km._live_names = lambda tm: {"web": SID_A, "api": SID_B}
        km._chat_tab_sessions = lambda now, live_map: [{"sid": s, "name": n, "path": str(self.tx[s]), "anchor": s}
                                                  for s, n in ((SID_A, "web"), (SID_B, "api"))]
        km.build_session = self._build_session
        km._cached_feed = lambda now, live_map, sig, connect=False: {"working": [], "awaiting": [], "now": now}
        km._cached_timeline = lambda now, live_map, sig, connect=False: {"turns": {}, "judging": [], "messages": [], "now": now}
        km.build_timeline = lambda now, live_map, **kw: {"lanes": [], "now": now}
        km._fleet_view_sig = lambda now, live_map: {"probe": 1}
        km._comments_frame = lambda sid, live_map: None
        km._retry_parked_creates = lambda: None
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        self.built = []
        self.chat = {"app": "chat", "alive": True, "sent": {}, "active": None, "send": lambda s: None}

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        st, bc, pe, pl, lo, jg, names, keep = self.saved_state
        jd._rebind_state(st)
        km.NAMES = names
        km._built_chat.clear(); km._built_chat.update(bc)
        km._prev_chat_events.clear(); km._prev_chat_events.update(pe)
        km._prev_chat_ledger.clear(); km._prev_chat_ledger.update(pl)
        km._last_tab_order[:] = lo
        km._judge_gen[0] = jg
        km._thread_fold_keep[0], km._thread_fold_keep[1] = keep
        km.Sessions.backend_for = staticmethod(self._saved_be)

    def _build_session(self, sid, now, live_map):
        self.built.append(sid)
        return {"type": "session", "id": sid, "name": "x", "events": [{"uuid": "e1", "type": "user"}],
                "ledger": None, "status": {"state": "waiting"}, "color": None}

    @staticmethod
    def _chat():
        return km._PERF_STATS.snapshot()["builds"]["chat"]

    @staticmethod
    def _delta(before, after):
        d = {k: after[k] - before[k] for k in ("cached", "built", "active_built", "bg_built", "moved")}
        d["bg_miss"] = {k: v - before["bg_miss"][k] for k, v in after["bg_miss"].items() if v - before["bg_miss"][k]}
        return d

    def test_one_sessions_store_publish_rebuilds_that_tab_alone(self):
        """Two background tabs; B's goal store is published and the producer's generation bump follows, as a
        judge pass does. Only B rebuilds, under the store label. Before this, the global judge generation was
        a component of every tab's key, so A rebuilt too, byte-identical."""
        km._push([self.chat])
        km._push([self.chat])
        self.assertEqual(sorted(self.built), sorted([SID_A, SID_B]), "the second push served both from the cache")
        c0 = self._chat()
        (jd.GOALDIR / (SID_B + ".json")).write_text(json.dumps({"rompUuid": SID_B, "nodes": {}, "status": {}}))
        self.assertTrue(km._bump_judge_gen_if_changed(), "the store directory moved: the producer bumps the generation")
        km._push([self.chat])
        self.assertEqual(self.built[2:], [SID_B], "B alone rebuilt")
        self.assertEqual(self._delta(c0, self._chat())["bg_miss"], {"store": 1})
        km._judge_gen[0] += 1                                 # a pass that moved nothing this world reads
        km._push([self.chat])
        self.assertEqual(self.built[2:], [SID_B], "the generation alone rebuilds no tab")

    def test_host_acknowledgements_rebuild_neither_the_active_nor_background_tab(self):
        self.chat["active"] = SID_A
        regs = jd.STATE / "sdk"
        regs.mkdir(parents=True, exist_ok=True)
        for sid in (SID_A, SID_B):
            (regs / (sid + ".json")).write_text(json.dumps({"sid": sid, "hostAck": {"offset": 1}}))
        km._push([self.chat])
        self.assertEqual(sorted(self.built), sorted([SID_A, SID_B]))
        before = self._chat()
        for sid in (SID_A, SID_B):
            pending = regs / (sid + ".next")
            pending.write_text(json.dumps({"sid": sid, "hostAck": {"offset": 2}}))
            pending.replace(regs / (sid + ".json"))
        km._push([self.chat])
        self.assertEqual(self.built[2:], [], "host progress is not new chat content")
        change = self._delta(before, self._chat())
        self.assertEqual((change["built"], change["cached"], change["bg_miss"]), (0, 2, {}))

    def test_a_bare_dirty_mark_rebuilds_nothing_and_a_live_tail_echo_rebuilds_its_tab(self):
        self.chat["active"] = SID_A
        km._push([self.chat]); km._push([self.chat])
        n = len(self.built)
        km._mark_views_dirty()
        km._push([self.chat])
        self.assertEqual(len(self.built), n, "a dirty mark with no moved input is no new information for the chat")
        self.tail.atoms[SID_A] = [{"t": 1.0, "text": "and also fix the header", "author": "human"}]
        km._push([self.chat])
        self.assertEqual(self.built[n:], [SID_A], "the watched tab's live tail moved: it rebuilds, and B is served")

    def test_the_cache_entry_is_the_signature_the_payload_its_serialization_and_the_dependency_record(self):
        self.chat["active"] = SID_A
        km._push([self.chat])
        for sid in (SID_A, SID_B):
            ent = km._built_chat[sid]
            self.assertEqual(len(ent), 4)
            self.assertEqual(len(ent[0]), len(km._CHAT_SIG_LABELS))
            self.assertEqual(ent[1]["id"], sid)
            self.assertIn("task_outs", ent[3])
            self.assertTrue(ent[2] is None or isinstance(ent[2], str), "index 2: the lazy serialization, a string once a full send materialized it")

    def test_a_build_whose_signature_moved_during_the_build_is_not_cached_and_counts_under_moved(self):
        def build(sid, now, live_map):
            self.built.append(sid)
            if sid == SID_B:
                km._node_anchor_rev[SID_B] = km._node_anchor_rev.get(SID_B, 0) + 1   # the build learned an anchor
            return {"type": "session", "id": sid, "name": "x", "events": [], "ledger": None,
                    "status": {"state": "waiting"}, "color": None}
        km.build_session = build
        try:
            c0 = self._chat()
            km._push([self.chat])
            self.assertNotIn(SID_B, km._built_chat, "the post-build signature differs: not cached")
            self.assertIn(SID_A, km._built_chat)
            self.assertEqual(self._delta(c0, self._chat())["moved"], 1)
        finally:
            km._node_anchor_rev.pop(SID_B, None)

    def test_a_signature_that_raises_is_said_once_per_episode_and_the_tab_builds_uncached(self):
        """One component's read raising for one session: its tab is built every cycle and never cached
        (counted under nosig), the fault is written to stderr with its traceback and filed as one bell row,
        ONCE per fault episode (the chat-build fault's rule): the same fault the next cycle says nothing, a
        different fault is a new episode, a signature that is taken ends it."""
        saved = (km._watch_awaiting, list(km._SYNC_NOTICES), dict(km._chat_sig_faults))
        del km._SYNC_NOTICES[:]
        km._chat_sig_faults.clear()
        fail = ["synthetic: the watch rows cannot be read"]
        orig = saved[0]

        def watch(sid):
            if sid == SID_B and fail[0]:
                raise OSError(fail[0])
            return orig(sid)
        km._watch_awaiting = watch

        def push():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                km._push([self.chat])
            return err.getvalue().count("push build: chat signature %s" % SID_B[:8])
        try:
            c0 = self._chat()
            self.assertEqual(push(), 1, "the first cycle says it")
            self.assertEqual(push(), 0, "the second cycle, same fault: not again")
            self.assertEqual(push(), 0)
            self.assertEqual(self.built.count(SID_B), 3, "the tab builds every cycle while its key cannot be taken")
            self.assertEqual(self.built.count(SID_A), 1, "the other tab is served")
            self.assertNotIn(SID_B, km._built_chat, "never cached")
            self.assertEqual(self._delta(c0, self._chat())["bg_miss"].get("nosig"), 3)
            self.assertEqual(len(km._SYNC_NOTICES), 1, "one episode, one bell row")
            self.assertIn("api", km._SYNC_NOTICES[-1]["text"])
            self.assertEqual(km._SYNC_NOTICES[-1].get("kind"), "refused")
            fail[0] = "synthetic: a different fault on the same session"
            self.assertEqual(push(), 1, "a different fault is a new episode")
            self.assertEqual(len(km._SYNC_NOTICES), 2)
            fail[0] = ""
            self.assertEqual(push(), 0)
            self.assertNotIn(SID_B, km._chat_sig_faults, "a signature that is taken ends the episode")
            self.assertIn(SID_B, km._built_chat, "...and the tab is cached like any other")
            fail[0] = "synthetic: the watch rows cannot be read"
            self.assertEqual(push(), 1, "the same fault after a success is a new episode, said anew")
        finally:
            km._watch_awaiting = orig
            del km._SYNC_NOTICES[:]
            km._SYNC_NOTICES.extend(saved[1])
            km._chat_sig_faults.clear()
            km._chat_sig_faults.update(saved[2])

    def test_a_push_closes_the_scopes_a_previous_push_on_its_thread_left_open(self):
        """A push that raised inside its chat loop leaves the scopes it opened set on the thread; the next
        push closes them first, so it opens its own fresh ones (and closes them) instead of adopting a
        stale names snapshot as a cycle's and leaving it on the thread for good."""
        km._live_scope.names = {"stale": ["x"]}
        km._live_scope.msgsum = [object()]
        km._live_scope.chat_shared = {"stale": 1}
        km._live_scope.chat_push_owned = ["chat_shared", "msgsum", "names"]
        try:
            km._push([self.chat])
            for k in ("names", "msgsum", "chat_shared", "chat_push_owned"):
                self.assertIsNone(getattr(km._live_scope, k, None), "%s: the stale scope is gone after the push" % k)
        finally:
            for k in ("names", "msgsum", "chat_shared", "chat_push_owned"):
                setattr(km._live_scope, k, None)

    def test_a_raise_reading_the_shared_components_leaks_none_of_the_slots_the_open_set(self):
        """_chat_push_scopes_open records what it owns BEFORE it reads the shared components (2026-09-18): a
        _chat_sig_shared that raised used to leave the names, msgsum and subagent_trees slots it had just opened
        set on the handler thread with no ownership record, so the push's except branch and the next push's
        opening close cleared nothing, and every later push and viewer frame on that connection's thread was
        served the stale samples for the connection's life (the open skips a slot already set, so the leak was
        adopted, never replaced)."""
        slots = ("names", "msgsum", "subagent_trees", "chat_shared", "chat_push_owned")
        for k in slots:
            setattr(km._live_scope, k, None)
        saved = km._chat_sig_shared

        def unreadable():
            raise RuntimeError("flags unreadable")
        km._chat_sig_shared = unreadable
        try:
            km._push([self.chat])                             # the raise lands in the push's except branch, which closes
        finally:
            km._chat_sig_shared = saved
        try:
            for k in slots:
                self.assertIsNone(getattr(km._live_scope, k, None), "%s: nothing the open set survives its raise" % k)
        finally:
            for k in slots:
                setattr(km._live_scope, k, None)


# ── the recording half: the real build makes the record the differential tests hand over ──────────
SID_R = "77777777-8888-9999-aaaa-eeeeeeeeeee1"
AID = "a0123456789abcdef"                        # an agent id of the CLI's shape: a plus 16 hex digits


class RecordedDependencies(unittest.TestCase):
    """The dependency record through the REAL build_session under _push. The differential tests above hand
    _chat_build_sig a record made by hand and check the re-evaluation; these pin the other half: the readers
    note the files whose tails the payload embeds as they read them (_chat_dep_note_taskout from
    _read_task_output, _subagent_meta_map, _subagent_file and _agent_steps), a fold hit carries the sealed
    prefix's outputs into the record, a raw postal event flags the log as a dependency, and the pusher
    consumes the record and leaves none on its thread. Synthetic session under a hermetic state root;
    records stamped against the real clock, which discovery keys on. Message ids are salted per test
    instance and tearDown clears the sid's per-message caches (_PATH_LINK_CACHE, _SPACE_PATH_CACHE, the pin
    sidecar's memo): a verdict cached under (sid, uuid) is served without re-reading the text, so an id
    reused across tests would hand one test's verdict to another's message. The pin directory's latch
    (_MENTION_PINS) is reset per world and restored at tearDown, as in _World, so a resolve never mkdirs
    under a torn-down world's state root."""

    _salt = itertools.count()                    # one value per test instance, read in setUp

    STUBS = ("_live_map", "_live_names", "_chat_tab_sessions", "_cached_feed", "_cached_timeline",
             "build_timeline", "_fleet_view_sig", "_comments_frame", "_retry_parked_creates", "_sdk", "_msg_summaries")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        td = Path(self.td.name)
        self.cdir = td / "launchdir"
        self.cdir.mkdir()
        proj = td / "projects"
        self.pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(self.cdir)))
        self.pdir.mkdir(parents=True)
        self.tpath = self.pdir / (SID_R + ".jsonl")
        self.tpath.write_text("")
        self.now = int(time.time())
        self.t = self.now - 3 * 86400
        self.saved = {nm: getattr(km, nm) for nm in self.STUBS}
        self.saved_state = (jd.STATE, jd.PROJECTS, km.NAMES, km._GLOBAL_CLAUDE_MD, os.environ.get("CLAUDE_CONFIG_DIR"),
                            dict(km._built_chat), dict(km._prev_chat_events), dict(km._prev_chat_ledger),
                            list(km._last_tab_order), [set(km._thread_fold_keep[0]), set(km._thread_fold_keep[1])],
                            km._MENTION_PINS)
        jd._rebind_state(td / "state")
        km._MENTION_PINS = None                       # the pin dir latches at first use (_pin_dir): this world's root, as _World
        jd.PROJECTS = proj
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID_R).write_text("web\t%s\t#1EA1EB\twhite\n" % self.cdir)
        km.NAMES = jd.NAMES
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        os.environ["CLAUDE_CONFIG_DIR"] = str(td / "claude")
        self.row = {"state": "working", "since": self.now - 100, "model": "", "effort": "", "context": None,
                    "compactPct": None, "color": None, "backend": "sdk"}
        km._live_map = lambda: {SID_R: self.row}
        km._sdk = lambda: None
        km._msg_summaries = lambda: {}
        km._live_names = lambda tm: {"web": SID_R}
        km._chat_tab_sessions = lambda now, live_map: [{"sid": SID_R, "name": "web", "path": str(self.tpath), "anchor": SID_R}]
        km._cached_feed = lambda now, live_map, sig, connect=False: {"working": [], "awaiting": [], "now": now}
        km._cached_timeline = lambda now, live_map, sig, connect=False: {"turns": {}, "judging": [], "messages": [], "now": now}
        km.build_timeline = lambda now, live_map, **kw: {"lanes": [], "now": now}
        km._fleet_view_sig = lambda now, live_map: {"probe": 1}
        km._comments_frame = lambda sid, live_map: None
        km._retry_parked_creates = lambda: None
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        km._parse_cache.clear(); km._task_out_cache.clear(); km._chat_fold.pop(SID_R, None)
        if isinstance(jd._discover_cache, dict):
            jd._discover_cache.clear()
        self.salt = next(self._salt)
        self.n = 0
        self.last = None
        self.chat = {"app": "chat", "alive": True, "sent": {}, "active": None, "send": lambda s: None}

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        st, proj, names, gmd, cfg, bc, pe, pl, lo, keep, pins = self.saved_state
        jd._rebind_state(st)
        jd.PROJECTS = proj
        km.NAMES, km._GLOBAL_CLAUDE_MD = names, gmd
        km._MENTION_PINS = pins
        if cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = cfg
        km._built_chat.clear(); km._built_chat.update(bc)
        km._prev_chat_events.clear(); km._prev_chat_events.update(pe)
        km._prev_chat_ledger.clear(); km._prev_chat_ledger.update(pl)
        km._last_tab_order[:] = lo
        km._thread_fold_keep[0], km._thread_fold_keep[1] = keep
        km._node_anchor_rev.pop(SID_R, None)
        km._chat_fold.pop(SID_R, None); km._parse_cache.clear(); km._task_out_cache.clear()
        for cache in (km._PATH_LINK_CACHE, km._SPACE_PATH_CACHE):
            for k in [k for k in cache if k[0] == SID_R]:
                cache.pop(k, None)
        km._PIN_ASSOC_MEMO.pop(SID_R, None)
        km._chat_dep_scope.deps = None
        if isinstance(jd._discover_cache, dict):
            jd._discover_cache.clear()

    # ── the transcript ──
    def uid(self):
        self.n += 1
        return "cccccccc-0000-0000-%04x-%012d" % (self.salt, self.n)

    def tick(self, dt=5):
        self.t += dt
        return self.t

    def append(self, recs):
        with open(self.tpath, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        os.utime(self.tpath, None)

    def turn(self, i):
        u, a = self.uid(), self.uid()
        recs = [_uline(self.tick(), "step %d: tighten the notes-api search" % i, u, self.last),
                _aline(self.tick(), "Step %d done." % i, a, u)]
        self.last = a
        return recs

    def agent_turn(self, out):
        """An Agent dispatched in the background and still running: the tool_use, the async launch ack naming
        the agent and its output file, a closing reply."""
        u, a, r, b = self.uid(), self.uid(), self.uid(), self.uid()
        recs = [_uline(self.tick(), "map the parser in the background", u, self.last),
                {"type": "assistant", "timestamp": _iso(self.tick()), "uuid": a, "parentUuid": u,
                 "message": {"role": "assistant", "content": [
                     {"type": "text", "text": "Dispatching."},
                     {"type": "tool_use", "id": "tu_agent1", "name": "Agent",
                      "input": {"description": "Map the parser", "prompt": "map the parser end to end",
                                "subagent_type": "general-purpose", "run_in_background": True}}],
                             "stop_reason": "tool_use"}},
                {"type": "user", "timestamp": _iso(self.tick()), "uuid": r, "parentUuid": a,
                 "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": AID,
                                   "description": "Map the parser", "prompt": "map the parser end to end",
                                   "outputFile": str(out)},
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_agent1",
                                                          "content": [{"type": "text", "text": "Async agent launched successfully."}]}]}},
                _aline(self.tick(), "The agent is running.", b, r)]
        self.last = b
        return recs

    def bash_turn(self, out):
        """A backgrounded shell command whose result landed: the launch, the task notification naming its
        output file (the standalone user-record shape), a closing reply. The card's detail embeds the file's tail."""
        u, a, r, b = self.uid(), self.uid(), self.uid(), self.uid()
        note = ("<task-notification>\n<task-id>bkv4ddzb1</task-id>\n<tool-use-id>tu_bash1</tool-use-id>\n"
                "<output-file>%s</output-file>\n<status>completed</status>\n<summary>Tests finished</summary>\n"
                "</task-notification>" % out)
        recs = [_uline(self.tick(), "run the tests in the background", u, self.last),
                {"type": "assistant", "timestamp": _iso(self.tick()), "uuid": a, "parentUuid": u,
                 "message": {"role": "assistant", "content": [
                     {"type": "text", "text": "Running them."},
                     {"type": "tool_use", "id": "tu_bash1", "name": "Bash",
                      "input": {"run_in_background": True, "command": "uv run pytest -q", "description": "Run the tests"}}],
                             "stop_reason": "tool_use"}},
                {"type": "user", "timestamp": _iso(self.tick()), "uuid": r, "parentUuid": a,
                 "message": {"role": "user", "content": note}, "promptSource": "sdk", "userType": "external"},
                _aline(self.tick(), "They pass.", b, r)]
        self.last = b
        return recs

    def agent_files(self, where=None):
        """The agent's sidecar meta and its own transcript beside the parent transcript (or under `where`)."""
        sdir = (where or self.pdir / SID_R) / "subagents"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / ("agent-%s.meta.json" % AID)).write_text(json.dumps(
            {"toolUseId": "tu_agent1", "agentId": AID, "agentType": "general-purpose", "description": "Map the parser"}))
        ap = sdir / ("agent-%s.jsonl" % AID)
        with open(ap, "w") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": _iso(self.tick()), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_a1", "name": "Read", "input": {"file_path": "parser.py"}}]}}) + "\n")
        return sdir, ap

    @staticmethod
    def _chat():
        return km._PERF_STATS.snapshot()["builds"]["chat"]

    def rebuilds(self, before):
        after = self._chat()
        return {k: v - before["bg_miss"][k] for k, v in after["bg_miss"].items() if v - before["bg_miss"][k]}

    def record(self):
        ent = km._built_chat.get(SID_R)
        self.assertIsNotNone(ent, "the build is cached: its static components held across it")
        return ent[1], ent[3], dict(ent[3]["task_outs"])

    def test_host_progress_reuses_the_real_chat_and_a_branch_change_rebuilds_it(self):
        self.append(self.turn(1))
        reg = jd.STATE / "sdk" / (SID_R + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        row = {"sid": SID_R, "name": "web", "alive": True, "hostAck": {"offset": 1}}

        def publish():
            pending = reg.with_suffix(".next")
            pending.write_text(json.dumps(row))
            pending.replace(reg)

        publish()
        km._push([self.chat])
        original, _, _ = self.record()
        self.assertIsNone(original.get("branch"))
        with mock.patch.object(km, "build_session", wraps=km.build_session) as build:
            row["hostAck"] = {"offset": 2}
            row["hostLogPos"] = {"pos": 3}
            publish()
            km._push([self.chat])
            build.assert_not_called()
            self.assertIs(self.record()[0], original)
            row["forkedFrom"] = {"sid": SID_A, "name": "api", "t": self.t}
            publish()
            km._push([self.chat])
            self.assertEqual(build.call_count, 1)
            self.assertEqual(self.record()[0]["branch"]["fromSid"], SID_A)

    def test_a_running_tasks_output_and_the_agents_own_transcript_are_recorded_as_they_are_read(self):
        out = Path(self.td.name) / "agent-out.log"
        out.write_text("line 1\n")
        sdir, ap = self.agent_files()
        self.append(self.turn(1) + self.agent_turn(out))
        km._push([self.chat])
        self.assertIsNone(getattr(km._chat_dep_scope, "deps", None), "the pusher consumed the record and left none")
        m, rec, touts = self.record()
        self.assertEqual(touts.get(str(out)), km._chat_stat_key(str(out)),
                         "the task box read the running task's output: recorded under the identity it was read under")
        self.assertEqual(m["bgTasks"]["tasks"][0]["output"], "line 1")
        self.assertEqual(touts.get(str(ap)), km._chat_stat_key(str(ap)), "the Agent card read the agent's own transcript")
        self.assertIn(str(sdir), touts, "and the sidecar directory whose listing named the agent")
        head = next(ev for ev in m["events"] if ev.get("kind") == "tool" and ev.get("name") == "Agent")
        self.assertEqual(head.get("stepsTotal"), 1)
        c = self._chat()
        with open(out, "a") as f:
            f.write("line 2\n")
        km._push([self.chat])
        self.assertEqual(self.rebuilds(c), {"taskout": 1}, "the output grew: the tab rebuilds under taskout")
        m, rec, touts = self.record()
        self.assertEqual(m["bgTasks"]["tasks"][0]["output"], "line 1\nline 2")
        c = self._chat()
        with open(ap, "a") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": _iso(self.tick()), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_a2", "name": "Grep", "input": {"pattern": "def parse"}}]}}) + "\n")
        km._push([self.chat])
        self.assertEqual(self.rebuilds(c), {"taskout": 1}, "the agent's transcript grew: the tab rebuilds under taskout")
        m, rec, touts = self.record()
        head = next(ev for ev in m["events"] if ev.get("kind") == "tool" and ev.get("name") == "Agent")
        self.assertEqual(head.get("stepsTotal"), 2)

    def test_an_agent_file_found_under_a_sibling_directory_records_the_directory_the_lookup_reads(self):
        """The agent's file is not beside its parent transcript (the sidecar directory moved under a fork's
        stem): the lookup lists every sibling's subagents/ directory. Those directories are the dependency,
        not their parents: a file landing inside <sib>/subagents/ moves that directory's mtime and no other."""
        out = Path(self.td.name) / "agent-out.log"
        out.write_text("line 1\n")
        other = self.pdir / "77777777-8888-9999-aaaa-eeeeeeeeeee2"
        sdir, ap = self.agent_files(where=other)
        self.append(self.turn(1) + self.agent_turn(out))
        km._push([self.chat])
        m, rec, touts = self.record()
        self.assertEqual(touts.get(str(ap)), km._chat_stat_key(str(ap)), "found under the sibling, and read")
        self.assertIn(str(sdir), touts, "the sibling's subagents/ directory is the recorded dependency")
        self.assertEqual(touts[str(sdir)], km._chat_stat_key(str(sdir)))
        own = self.pdir / SID_R / "subagents"
        self.assertEqual(touts.get(str(own / ("agent-%s.jsonl" % AID)), "unset"), None,
                         "the file's own place is recorded absent: its appearance there is a change")
        c = self._chat()
        (sdir / ("agent-%s.meta.json" % "a0123456789abcdee")).write_text("{}")   # a sibling file lands in the directory
        km._push([self.chat])
        self.assertEqual(self.rebuilds(c), {"taskout": 1}, "the directory the lookup reads moved")

    def test_a_fold_hit_carries_the_sealed_cards_output_into_the_record(self):
        """A completed task's card embeds its output file's tail; once its turn is sealed in the fold prefix
        the card is reused without re-reading the file, so the sealed prefix's outputs join the record on a
        fold hit. Without that a rebuild served from the prefix would drop the file from the key, and the
        card would show a stale tail until an unrelated input moved."""
        out = Path(self.td.name) / "tests-out.log"
        out.write_text("2 passed\n")
        self.append(self.bash_turn(out) + self.turn(2))
        km._push([self.chat])
        m, rec, touts = self.record()
        self.assertEqual(touts.get(str(out)), km._chat_stat_key(str(out)), "the cold build read the card's tail")
        self.assertIn(str(out), dict(km._chat_fold_get(SID_R)["task_outs"]), "and the seal recorded it")
        km._node_anchor_rev[SID_R] = km._node_anchor_rev.get(SID_R, 0) + 1   # an anchor learned: a rebuild over the same parse
        km._push([self.chat])
        self.assertGreater(km._chat_fold_last_info().get("prefix", 0), 0, "the second build reused the sealed prefix")
        m, rec, touts = self.record()
        self.assertEqual(touts.get(str(out)), km._chat_stat_key(str(out)),
                         "the fold hit's record still names the sealed card's output")
        c = self._chat()
        with open(out, "a") as f:
            f.write("1 failed\n")
        km._push([self.chat])
        self.assertEqual(self.rebuilds(c), {"taskout": 1}, "the sealed card's file grew: the tab rebuilds")

    def test_a_raw_postal_event_that_did_not_hydrate_makes_the_log_a_dependency(self):
        """A peer's message marker in the tail whose id the postal index does not hold yet renders from the
        log alone; the record flags postal traffic even though no card rendered, so the log landing (the
        index catching up) rebuilds the tab."""
        self.append(self.turn(1))
        u, a = self.uid(), self.uid()
        self.append([_uline(self.tick(), "<!-- romp-msg-id: m9 -->\nthe api tests are green now", u, self.last),
                     _aline(self.tick(), "Noted.", a, u)])
        self.last = a
        km._push([self.chat])
        m, rec, touts = self.record()
        self.assertFalse([ev for ev in m["events"] if ev.get("kind") == "postal-service"], "no card: the id is not indexed")
        self.assertTrue(rec["postal_any"], "the raw marker event depends on the log")
        c = self._chat()
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        with open(jd.MESSAGES, "a") as f:
            f.write(json.dumps({"ev": "sent", "id": "m9", "from_id": PEER, "to_id": SID_R, "body": "the api tests are green now",
                                "t": self.t}) + "\n")
        km._push([self.chat])
        self.assertIn("postal", self.rebuilds(c), "the log landed: the tab rebuilds under postal")

    def test_mail_between_two_other_sessions_does_not_rebuild_a_tab_that_carries_a_card(self):
        """A tab carrying a postal card depends on THIS session's revision of the log, not the log's identity
        (2026-09-18): a message between two other sessions used to rebuild every mail-bearing tab (1862 of
        5907 background chat rebuilds on one live kernel carried the postal label). A row addressed to this
        session still rebuilds it."""
        self.addCleanup(km._postal_index_memo.__setitem__, 0, None)
        km._postal_index_memo[0] = None
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)

        def row(r):
            with open(jd.MESSAGES, "a") as f:
                f.write(json.dumps(r) + "\n")
        row({"ev": "sent", "id": "m9", "from_id": PEER, "to_id": SID_R, "body": "the api tests are green now", "t": self.t})
        self.append(self.turn(1))
        u, a = self.uid(), self.uid()
        self.append([_uline(self.tick(), "<!-- romp-msg-id: m9 -->\nthe api tests are green now", u, self.last),
                     _aline(self.tick(), "Noted.", a, u)])
        self.last = a
        km._push([self.chat])
        km._push([self.chat])                            # served: the record stands
        m, rec, touts = self.record()
        self.assertEqual(len([ev for ev in m["events"] if ev.get("kind") == "postal-service"]), 1, "the marker hydrated: one card")
        c = self._chat()
        row({"ev": "sent", "id": "m10", "from_id": OTHER_A, "to_id": OTHER_B, "body": "unrelated", "t": self.t})
        km._push([self.chat])
        self.assertEqual(self.rebuilds(c), {}, "mail between two other sessions moved nothing this tab renders")
        c2 = self._chat()
        row({"ev": "sent", "id": "m11", "from_id": PEER, "to_id": SID_R, "body": "and the docs", "t": self.t})
        km._push([self.chat])
        self.assertIn("postal", self.rebuilds(c2), "a record addressed to this session rebuilds it")

    def test_a_targeted_push_leaves_no_dependency_record_on_its_thread(self):
        """_push_session_now builds one session outside the pusher's cache and must not leave the build's
        record on the handler thread, where a later reader (a subagent viewer's step fold) would append to it."""
        out = Path(self.td.name) / "agent-out.log"
        out.write_text("line 1\n")
        self.agent_files()
        self.append(self.turn(1) + self.agent_turn(out))
        with km._clients_lock:
            km._clients.append(self.chat)
        try:
            km._push_session_now(SID_R)
        finally:
            with km._clients_lock:
                km._clients[:] = [c for c in km._clients if c is not self.chat]
        self.assertTrue(self.chat["sent"], "the session reached the client")
        self.assertIsNone(getattr(km._chat_dep_scope, "deps", None))

    def test_a_fresh_worlds_message_under_a_reused_id_is_verified_on_its_own_text(self):
        """The path-link verdict is cached per (sid, uuid) and a hit is served without re-reading the text
        (_path_links), so a message id one test minted and a later test reused would carry the earlier
        message's verdict into the later test. The hazard is cross-test by construction, so this test runs
        two worlds itself, through the fixture's own hooks: the first caches a verdict for a token-free line,
        the second names a real file from the same counter position and must be verified on its own text."""
        self.append(self.turn(1))                           # the first world's user line: no path token
        km._push([self.chat])
        self.tearDown()
        self.setUp()                                        # the next test's world (a second temp dir cleanup is registered; both run when the test ends)
        (self.cdir / "notes").mkdir()
        (self.cdir / "notes" / "report.md").write_text("42\n")
        u, a = self.uid(), self.uid()                       # the first world's counter position again: the same id but for the salt
        self.append([_uline(self.tick(), "see notes/report.md", u, None), _aline(self.tick(), "Read it.", a, u)])
        km._push([self.chat])
        ev = next(e for e in km._built_chat[SID_R][1]["events"] if e.get("uuid") == u)
        self.assertIn("notes/report.md", ev.get("pathLinks") or {}, "verified on its own text, not a prior message's")


if __name__ == "__main__":
    unittest.main()
