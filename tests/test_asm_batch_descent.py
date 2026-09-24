#!/usr/bin/env python3
"""A parallel tool batch's results fold onto the assembly entry as they land (the parse investigation of 2026-09-24).

When the model makes several tool calls in one message, the CLI writes each call as its own assistant record of that
message, chained call to call, and parents each call's RESULT at the record carrying that call (the parallel batch keep,
FileAdapter._batch_head, 2026-09-23). While the results land, the leaf moves from one call's branch to the next, so the
new leaf does not chain back to the old one through the appended records, and the assembly gate's leaf descent
(_asm_gates) demoted every such append: a restore from the leaf's document, or a whole parse when there is none, for each
result, and the chat's folded prefix rebuilt after each (the kernel's chat fold demotes at `parse` after any road but a
serve or a fold). The graph already keeps the batch's branches, so the fold's kept-invariance check can rule on them.

Pinned here, on the parse's road counters over synthetic episodes: each result of a three-call batch folds, on a whole
entry (no document) and on an entry restored from its document, with results in call order and out of it, a call block
written after an earlier call's result, and a hook landing in one append with the next result. The served tree equals a
cold parse after EVERY append, mid-batch included (a copy of the transcript parsed whole at a fresh path, the checkpoint
directory off, so the entry under test is untouched). The shapes the descent gate exists for still demote AT it (the
g:descent counter, not merely some demotion): an api_error spur off the batch's opener, a typed prompt re-parented at a
call (a rewind into the batch), a reply of another message hung at a call, a result answering a call its parent does not
carry, a new root, and a cycle among the appended records with no batch link; and so do the mixed appends that carry a
batch result BESIDE such a record (a spur and the prompt after it, a rewound prompt), since one batch link excuses no other
record. A batch result that passes the gate but moves an old record's kept membership (the leaf leaving an api_error spur
off the opener) demotes under g:kept and takes the restore road, the one the descent's demotion gave it before; a later
delta that chains back to the old leaf and is rejected there still parses whole, the batch bit cleared at its own gate.
Synthetic transcripts only: the notes-api demo world, invented commands, placeholder ids and a private placeholder session
id."""
import collections
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_batch_descent", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-00000bd5ce77"     # this module's own placeholder session id
NOW = 1781200000
T0 = NOW - 200000
FILLER = ("the search endpoint ranks notes by recency, the tokenizer splits on punctuation, "
          "and the pagination cursor carries the last note id so a reload resumes where it stopped")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def prompt(t, uid, parent, text, pid=None):
    r = {"type": "user", "uuid": uid, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed", "cwd": "/w/notes-api",
         "message": {"role": "user", "content": text}}
    if pid:
        r["promptId"] = pid
    return r


def reply(t, uid, parent, text, msg):
    return {"type": "assistant", "uuid": uid, "parentUuid": parent, "timestamp": iso(t), "cwd": "/w/notes-api",
            "message": {"id": msg, "role": "assistant", "model": "claude-test-1", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


def call(t, uid, parent, call_id, command, msg):
    """One tool_use block, as its own assistant record of the model message `msg` (the CLI writes one per block)."""
    return {"type": "assistant", "uuid": uid, "parentUuid": parent, "timestamp": iso(t), "cwd": "/w/notes-api",
            "message": {"id": msg, "role": "assistant", "model": "claude-test-1", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}]}}


def result(t, uid, parent, call_id, out, pid):
    """A call's result, parented at the record carrying its own call (not at the record written before it)."""
    return {"type": "user", "uuid": uid, "parentUuid": parent, "sourceToolAssistantUUID": parent, "promptId": pid,
            "timestamp": iso(t), "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": out}]}}


def hook(t, uid, parent):
    """The PostToolUse hook's attachment the CLI chains after a result: no atom, but it can be the leaf."""
    return {"type": "attachment", "uuid": uid, "parentUuid": parent, "timestamp": iso(t),
            "attachment": {"type": "hook_success", "hookEvent": "PostToolUse", "stdout": "", "stderr": "", "exitCode": 0}}


def api_error(t, uid, parent):
    """The record the CLI flushes when a request fails and is retried: parented where the request started (T209)."""
    return {"type": "system", "subtype": "api_error", "uuid": uid, "parentUuid": parent, "level": "error",
            "timestamp": iso(t), "error": {"type": "overloaded_error"}}


def history(turns):
    """`turns` settled exchanges, each a typed prompt and its reply: the conversation before the batch. The restored test
    takes enough of them that the document's pre-cut part outweighs the tail by more than the churn bound (_ASM_TAIL_SHARE)
    with the whole batch in the tail, so the restored entry is not demoted for its tail's share."""
    recs, parent = [], None
    for k in range(turns):
        t = T0 + 600 * k
        u, a = "hu%03d" % k, "ha%03d" % k
        recs.append(prompt(t, u, parent, "step %d of the notes-api search work: %s" % (k, FILLER), pid="11111111-2222-3333-4444-5555%08d" % k))
        recs.append(reply(t + 30, a, u, "Step %d is in: %s." % (k, FILLER), "msg_h%03d" % k))
        parent = a
    return recs


def batch_pid(tag):
    """The batch turn's promptId: the CLI stamps the prompt and every result of the turn with it."""
    return "11111111-2222-3333-4444-0000000%s00b1" % tag


def batch(t, parent, tag="b", k=3):
    """The CLI's parallel batch (tests/test_parallel_tool_batch.py's shape): a prompt, k calls of one message chained call to
    call, each call's result parented at its own call with the PostToolUse hook after it, and the reply chained off the
    last hook. Returns (the prompt and the calls, [each call's result with its hook, in call order], the reply)."""
    pid = batch_pid(tag)
    head = [prompt(t, tag + "p", parent, "run the three notes-api checks at once, then summarize", pid=pid)]
    prev = tag + "p"
    for i in range(k):
        head.append(call(t + 5, "%sc%d" % (tag, i), prev, "toolu_%s%d" % (tag, i), "uv run pytest -q tests/test_part%d.py" % i,
                         "msg_%s_calls" % tag))
        prev = "%sc%d" % (tag, i)
    results = [[result(t + 10 + 2 * i, "%sr%d" % (tag, i), "%sc%d" % (tag, i), "toolu_%s%d" % (tag, i), "part %d: %d passed" % (i, 10 + i), pid),
                hook(t + 10 + 2 * i, "%sh%d" % (tag, i), "%sr%d" % (tag, i))] for i in range(k)]
    tail = [reply(t + 30, tag + "reply", "%sh%d" % (tag, k - 1), "All three checks passed: search, notes and lint.", "msg_%s_reply" % tag)]
    return head, results, tail


def _strip(tree):
    """A tree as JSON compares it (tests/test_asm_checkpoint.py's _strip): a restored tree's pre-cut turns built into plain
    turns, the lazy markers dropped once hydrated, the cut's own fact dropped."""
    t = json.loads(json.dumps(em.plain_tree(tree), default=lambda o: "<unserializable>"))
    t.pop("cutTurn", None)
    for turn in t["turns"]:
        for a in turn["atoms"]:
            a.pop("lazy", None)
    return t


class Harness(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()    # the parse is handed the REAL path: the record cache and the assembly
        #                                                  cache key on the realpath (tests/test_asm_checkpoint.py's note)
        self.ck = self.td / "checkpoints"
        em.set_checkpoint_dir(lambda: self.ck)
        self.leaf = str(self.td / (SID + ".jsonl"))
        self.ncold = 0
        self.fresh()

    def tearDown(self):
        em.set_checkpoint_dir(None)
        self.fresh()
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        """A kernel restart's in-memory side: every reader entry, assembly entry, hydrated body and memoized document gone."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
            em._ASM_DOC_MEMO.clear(); em._ASM_DOC_MEMO_BYTES[0] = 0
        em._LAZY_FILES.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()

    def start(self, recs):
        """Write the transcript and parse it whole (no document stands): the entry every later append folds onto."""
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in recs))
        tree, mode = self.parse()
        self.assertEqual(mode, "full")
        self.assertSameTree(tree, "the first parse")
        return tree

    def restored(self, turns=120):
        """`turns` settled exchanges parsed whole, their document written from that entry, then a restart: the entry the
        parse restores from the document. Enough turns that the document's pre-cut part outweighs a whole batch in the
        tail by more than the churn bound (history's note). Returns the history."""
        saved = em._ASM_FIRST_DOC_MIN
        em._ASM_FIRST_DOC_MIN = 0                               # the young-session floor off, as the suite's floors set it: this
        self.addCleanup(setattr, em, "_ASM_FIRST_DOC_MIN", saved)   #  history is far under a live first document's bytes
        base = history(turns)
        whole = self.start(base)
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SID, tree=whole), em.asm_checkpoint_stats())
        self.fresh()                                            # a restart: the parse restores from the document
        tree, mode = self.parse()
        self.assertEqual(mode, "restore")
        self.assertGreater(tree.get("cutTurn") or 0, 0, "the restored shape: the pre-cut turns come from the document")
        self.assertSameTree(tree, "the restored entry")
        return base

    def append(self, recs):
        with open(self.leaf, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))

    def parse(self):
        modes = []
        tree = em.parse_session(self.leaf, rompuuid=SID, name="web", dir="/w/notes-api", candidate_files=[self.leaf],
                                states=None, postal_log=[], now=NOW, asm_mode_out=modes)
        return tree, (modes[-1] if modes else None)

    def land(self, groups, what="the append"):
        """Append each group of records and parse after it, as the kernel does when the transcript grows, and hold the served
        tree to a cold parse after EACH append (the chat shows every one of them, not only the last): the road each parse
        took, the counters those parses moved (the cold parses' own are left out), and the last tree."""
        modes, moved, tree = [], collections.Counter(), None
        for i, grp in enumerate(groups):
            self.append(grp)
            s0 = dict(em._ASM_STATS)
            tree, mode = self.parse()
            s1 = dict(em._ASM_STATS)
            moved.update({k: s1.get(k, 0) - s0.get(k, 0) for k in set(s0) | set(s1) if s1.get(k, 0) != s0.get(k, 0)})
            modes.append(mode)
            self.assertSameTree(tree, "%s, after append %d of %d (road %s)" % (what, i + 1, len(groups), mode))
        return modes, dict(moved), tree

    def cold(self):
        """The reference: a copy of the transcript at a FRESH path parsed whole from nothing, the checkpoint directory off
        (no document to restore from). The entry under test, keyed on its own path, is untouched, so the append after this
        still meets the entry the last parse left; the copy's entries leave both caches before it goes."""
        self.ncold += 1
        d = self.td / ("cold%03d" % self.ncold)
        d.mkdir()
        leaf = str(d / (SID + ".jsonl"))
        shutil.copyfile(self.leaf, leaf)
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None
        try:
            modes = []
            tree = em.parse_session(leaf, rompuuid=SID, name="web", dir="/w/notes-api", candidate_files=[leaf], states=None,
                                    postal_log=[], now=NOW, asm_mode_out=modes)
            self.assertEqual(modes[-1:], ["full"], "the reference is a whole parse")
            return _strip(tree)
        finally:
            em._CKPT_DIR_FN = saved
            em.evict_document(leaf)
            shutil.rmtree(d, ignore_errors=True)

    def assertSameTree(self, tree, what):
        """The served tree against a cold parse: the turn count, then the first turn that differs, then the tree's other keys,
        so a mismatch names its turn rather than printing two whole sessions. A restored tree's pre-cut bodies are read in
        first, as every consumer of a pre-cut atom does."""
        if tree.get("cutTurn"):
            em.hydrate(tree, SID)
        got = _strip(tree)
        want = self.cold()
        self.assertEqual(len(got["turns"]), len(want["turns"]), "%s: the turn count against a cold parse" % what)
        for i, (g, w) in enumerate(zip(got["turns"], want["turns"])):
            if g != w:
                self.assertEqual(g, w, "%s: turn %d (%s) differs from the cold parse's" % (what, i, w.get("id")))
        self.assertEqual({k: v for k, v in got.items() if k != "turns"}, {k: v for k, v in want.items() if k != "turns"},
                         "%s: the tree's other keys against a cold parse" % what)

    def after(self, recs, dt):
        return max(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp")) + dt


class ResultsFold(Harness):
    """Each result of a parallel batch is a fold onto the entry, never a descent demotion."""

    def test_each_result_folds_on_a_whole_entry(self):
        base = history(12)
        self.start(base)                                        # a whole entry and no document: a descent here parses whole
        head, results, tail = batch(self.after(base, 60), base[-1]["uuid"])
        self.assertEqual(self.land([head], "the prompt and the calls")[0], ["fold"], "the prompt and the calls chain on the leaf")
        modes, moved, tree = self.land(results + [tail], "the batch")
        self.assertEqual(moved.get("g:descent", 0), 0, "no descent demotion across the batch: roads %s, counters moved %s" % (modes, moved))
        self.assertEqual(modes, ["fold"] * 4, "every result and the reply folded: %s (%s)" % (modes, moved))
        self.assertEqual(moved.get("full", 0), 0, "and nothing parsed whole: %s" % moved)
        self.assertEqual(sum(1 for t in tree["turns"] for a in t["atoms"] if a.get("uuid") in ("br0", "br1", "br2")), 3,
                         "all three results are in the served tree")

    def test_each_result_folds_on_an_entry_restored_from_its_document(self):
        base = self.restored()
        head, results, tail = batch(self.after(base, 60), base[-1]["uuid"])
        self.assertEqual(self.land([head], "the prompt and the calls")[0], ["fold"],
                         "the prompt and the calls fold onto the restored entry")
        modes, moved, tree = self.land(results + [tail], "the batch on the restored entry")
        self.assertEqual(moved.get("g:descent", 0), 0, "no descent demotion across the batch: roads %s, counters moved %s" % (modes, moved))
        self.assertEqual(modes, ["fold"] * 4, "every result and the reply folded onto the restored entry: %s (%s)" % (modes, moved))
        self.assertEqual((moved.get("restore", 0), moved.get("full", 0)), (0, 0), "no restore and no whole parse: %s" % moved)
        self.assertGreater(tree.get("cutTurn") or 0, 0, "the entry is still the restored one")

    def test_results_landing_out_of_call_order_fold(self):
        # the last call's result lands first (it chains on the leaf, the last call), then the first's and the second's
        base = history(12)
        self.start(base)
        t = self.after(base, 60)
        head, _, _ = batch(t, base[-1]["uuid"])
        self.land([head], "the prompt and the calls")
        late = [[result(t + 10, "br2", "bc2", "toolu_b2", "part 2: 12 passed", head[0]["promptId"]), hook(t + 10, "bh2", "br2")],
                [result(t + 12, "br0", "bc0", "toolu_b0", "part 0: 10 passed", head[0]["promptId"]), hook(t + 12, "bh0", "br0")],
                [result(t + 14, "br1", "bc1", "toolu_b1", "part 1: 11 passed", head[0]["promptId"]), hook(t + 14, "bh1", "br1")],
                [reply(t + 30, "breply", "bh1", "All three checks passed.", "msg_b_reply")]]
        modes, moved, tree = self.land(late, "results out of call order")
        self.assertEqual(moved.get("g:descent", 0), 0, "no descent demotion: roads %s, counters moved %s" % (modes, moved))
        self.assertEqual(modes, ["fold"] * 4, "%s (%s)" % (modes, moved))

    def test_a_call_block_written_after_an_earlier_calls_result_folds(self):
        # the CLI streams the message's blocks while the first call runs: the second call's record, chained on the first
        # call's, lands after the first call's result and hook, when the leaf is that hook
        base = history(12)
        self.start(base)
        t = self.after(base, 60)
        pid = "11111111-2222-3333-4444-000000000b2d"
        groups = [[prompt(t, "sp", base[-1]["uuid"], "run the two notes-api checks at once", pid=pid),
                   call(t + 5, "sc0", "sp", "toolu_s0", "uv run pytest -q tests/test_search.py", "msg_s_calls")],
                  [result(t + 10, "sr0", "sc0", "toolu_s0", "search: 12 passed", pid), hook(t + 10, "sh0", "sr0")],
                  [call(t + 11, "sc1", "sc0", "toolu_s1", "uv run pytest -q tests/test_notes.py", "msg_s_calls")],
                  [result(t + 15, "sr1", "sc1", "toolu_s1", "notes: 30 passed", pid), hook(t + 15, "sh1", "sr1")],
                  [reply(t + 20, "sreply", "sh1", "Both checks passed.", "msg_s_reply")]]
        modes, moved, tree = self.land(groups, "a call block after a result")
        self.assertEqual(moved.get("g:descent", 0), 0, "no descent demotion: roads %s, counters moved %s" % (modes, moved))
        self.assertEqual(modes, ["fold"] * 5, "%s (%s)" % (modes, moved))

    def test_a_hook_landing_with_the_next_result_folds(self):
        # the parse ran between a result and its hook: the next append carries that hook (continuing the old leaf, the
        # result) and the next call's result (hanging off its own call)
        base = history(12)
        self.start(base)
        head, results, tail = batch(self.after(base, 60), base[-1]["uuid"])
        self.land([head], "the prompt and the calls")
        groups = [[results[0][0]], [results[0][1]] + results[1], results[2], tail]
        modes, moved, tree = self.land(groups, "a hook with the next result")
        self.assertEqual(moved.get("g:descent", 0), 0, "no descent demotion: roads %s, counters moved %s" % (modes, moved))
        self.assertEqual(modes, ["fold"] * 4, "%s (%s)" % (modes, moved))


class OtherShapesStillDemote(Harness):
    """The shapes the leaf descent exists for, appended after a finished batch, still demote AT the descent gate: none
    hangs off a record by the batch's own link, so the new rule does not reach them. Each is served by the whole parse
    (there is no document here) and equals a cold parse."""

    def _finished_batch(self):
        base = history(8)
        self.start(base)
        head, results, tail = batch(self.after(base, 60), base[-1]["uuid"])
        self.land([head] + results + [tail], "the finished batch")
        return self.after(head + tail, 60)

    def _check(self, name, recs):
        modes, moved, tree = self.land([recs], name)
        self.assertEqual(moved.get("g:descent", 0), 1, "%s: demoted at the descent gate: roads %s, counters moved %s" % (name, modes, moved))
        self.assertEqual(modes, ["full"], "%s: parsed whole (no document stands): %s" % (name, moved))

    def test_an_api_error_spur_off_the_batchs_opener_demotes(self):
        # the T209 geometry in a batch turn: the CLI flushes an api_error record parented at the turn's opener, and the next
        # prompt chains onto that spur
        t = self._finished_batch()
        self._check("an api_error spur", [api_error(t, "e1", "bp"), prompt(t + 10, "u_next", "e1", "and the export endpoint?")])

    def test_a_prompt_re_parented_at_a_call_demotes(self):
        t = self._finished_batch()
        self._check("a rewind into the batch", [prompt(t, "u_rw", "bc0", "actually, only run the search tests")])

    def test_a_reply_of_another_message_hung_at_a_call_demotes(self):
        t = self._finished_batch()
        self._check("another message's reply at a call", [reply(t, "a_other", "bc2", "A reply from another message.", "msg_other")])

    def test_a_result_answering_a_call_its_parent_does_not_carry_demotes(self):
        t = self._finished_batch()
        self._check("a result for another record's call", [result(t, "r_wrong", "bc0", "toolu_b1", "notes: 30 passed",
                                                                  batch_pid("b"))])

    def test_a_new_root_demotes(self):
        t = self._finished_batch()
        self._check("a new root", [prompt(t, "u_root", None, "start over: plan the notes-api export")])

    def test_a_cycle_with_no_batch_link_demotes(self):
        # two appended records naming each other as parent: every parent lies inside the delta, so no record hangs off a
        # known one by a batch link, and the rule's at-least-one-link clause is what sends the delta to the descent gate
        t = self._finished_batch()
        self._check("a cycle with no batch link", [prompt(t, "u_cx", "u_cy", "a record in a ring"),
                                                   prompt(t + 1, "u_cy", "u_cx", "and its partner")])


class MixedAppendsStillDemote(Harness):
    """One append that carries a batch result BESIDE a record the descent gate exists for still demotes at it: the batch
    link excuses only the record that takes it, so every other record whose parent lies outside the delta must take a
    link of its own (or continue the old leaf). Mid-batch, no document: the whole parse serves it."""

    def _mid_batch(self, landed):
        base = history(8)
        self.start(base)
        t = self.after(base, 60)
        head, results, tail = batch(t, base[-1]["uuid"])
        modes, _, _ = self.land([head] + results[:landed], "the batch so far")
        self.assertEqual(modes, ["fold"] * (landed + 1), "the batch so far folded")
        return base, t, results

    def _check(self, name, recs):
        modes, moved, tree = self.land([recs], name)
        self.assertEqual(moved.get("g:descent", 0), 1, "%s: demoted at the descent gate: roads %s, counters moved %s" % (name, modes, moved))
        self.assertEqual(modes, ["full"], "%s: parsed whole (no document stands): %s" % (name, moved))

    def test_a_result_with_a_spur_and_the_next_prompt_in_one_append_demotes(self):
        # the last result (a batch link), an api_error spur off the batch's opener, and the prompt that chains onto the spur
        base, t, results = self._mid_batch(2)
        self._check("a result beside a spur", results[2] + [api_error(t + 25, "e1", "bp"),
                                                            prompt(t + 30, "u_next", "e1", "and the export endpoint?")])

    def test_a_result_with_a_rewound_prompt_in_one_append_demotes(self):
        # the second result (a batch link) and a prompt re-parented two exchanges back (a rewind past the batch)
        base, t, results = self._mid_batch(1)
        self._check("a result beside a rewind", results[1] + [prompt(t + 30, "u_rw", base[-3]["uuid"], "go back to the ranking")])


class KeptRejectionRestores(Harness):
    """A batch result the gate lets through and the fold's kept-invariance check then rejects takes the RESTORE road when a
    document stands, the one the descent demotion gave the same append before batches folded: the leaf leaves an api_error
    spur off the batch's opener for the next result's call, so the spur's records leave the kept set. The restore's own
    chain proof still rules on the tail, and the served tree equals a cold parse at every step."""

    def test_a_batch_result_the_kept_check_rejects_restores_from_the_document(self):
        base = self.restored()
        t = self.after(base, 60)
        head, results, tail = batch(t, base[-1]["uuid"])
        self.assertEqual(self.land([head, results[0]], "the calls and the first result")[0], ["fold", "fold"])
        modes, moved, _ = self.land([[api_error(t + 11, "e1", "bp"), api_error(t + 11, "e2", "e1")]], "a spur off the opener")
        self.assertEqual((modes, moved.get("g:descent", 0)), (["restore"], 1),
                         "the spur demotes at the descent and restores from the document: %s" % moved)
        modes, moved, tree = self.land([results[1]], "the next result, off the spur's spine")
        self.assertEqual(moved.get("g:kept", 0), 1, "the gate let the result through and the kept check rejected it: %s" % moved)
        self.assertEqual(modes, ["restore"], "the kept rejection restored from the document instead of parsing whole: %s" % moved)
        self.assertEqual((moved.get("restore:afterDemote", 0), moved.get("full", 0)), (1, 0), "%s" % moved)
        self.assertGreater(tree.get("cutTurn") or 0, 0, "the entry is a restored one again")
        modes, moved, _ = self.land([results[2], tail], "the rest of the batch")
        self.assertEqual(modes, ["fold", "fold"], "and the batch folds on from there: %s" % moved)


class TheBatchBitSpeaksForOneDelta(Harness):
    """The gate leaves a bit on the calling thread when a delta passes on the batch link, and the fold's kept rejection of
    that delta takes the restore road. The gate clears the bit before every walk, so it never outlives the delta it names:
    a later delta that chains back to the old leaf and still moves an old record's kept membership parses whole, as a kept
    rejection always has, even when a batch folded on the same thread just before it and a document stands. The shape here
    is a retry storm: a prompt hung at the turn's opener with no reply under it, beside the api_error spur the CLI flushed
    off that opener. While the spur is the transcript's tail the eclipse keeps that branch (nothing proves a person
    abandoned it); the next prompt, chained past the spur, completes the flush and the eclipse drops the branch as rollback
    residue (FileAdapter._select_eclipsed_chains), so the kept check rejects the one-record delta that brought it."""

    def test_a_kept_rejection_after_a_batch_folded_parses_whole(self):
        base = self.restored()
        t = self.after(base, 60)
        head, results, tail = batch(t, base[-1]["uuid"])
        modes, moved, _ = self.land([head] + results + [tail], "the batch")
        self.assertEqual(modes, ["fold"] * 5, "the batch folded on the restored entry, its results on the batch link: %s" % moved)
        t2 = self.after(tail, 60)
        storm = [prompt(t2, "u_ask", "breply", "now add the export endpoint to the notes-api"),
                 prompt(t2 + 5, "u_side", "u_ask", "and keep the old route answering"),
                 api_error(t2 + 10, "e1", "u_ask"), api_error(t2 + 12, "e2", "e1")]
        modes, moved, tree = self.land([storm], "the storm, its spur the tail")
        self.assertEqual(modes, ["fold"], "the storm chains back to the old leaf and folds: %s" % moved)
        self.assertIn("u_side", {a.get("uuid") for turn in tree["turns"] for a in turn["atoms"]},
                      "the branch beside the spur is kept while the spur is the tail")
        asked = []
        real = em._asm_restore

        def restore(*a, **k):
            asked.append(a[1] if len(a) > 1 else k.get("leaf_path"))
            return real(*a, **k)

        with mock.patch.object(em, "_asm_restore", restore):
            modes, moved, tree = self.land([[prompt(t2 + 20, "u_next", "e2", "did the export endpoint land?")]],
                                           "the next prompt, past the spur")
        self.assertEqual(moved.get("g:kept", 0), 1, "the walk passed and the kept check rejected the delta: %s" % moved)
        self.assertNotIn("u_side", {a.get("uuid") for turn in tree["turns"] for a in turn["atoms"]},
                         "the completed flush dropped the branch beside the spur")
        self.assertEqual((modes, moved.get("full:demoted", 0), asked), (["full"], 1, []),
                         "a kept rejection of a delta off no batch link parses whole and never asks the restore, though a "
                         "batch folded on this thread just before it: %s" % moved)


if __name__ == "__main__":
    unittest.main()
