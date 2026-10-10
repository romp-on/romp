"""Tail-only record cache, the third review of round two (2026-10-09): four defects, each reproduced, and their pins.

1. A tail record stamped in the gap between the document's watermark (its last pre-cut conversational stamp) and the cut's
   first record: the cold parse sorts it into the last pre-cut turn, the restore gave it a turn of its own (after a
   compaction on the append path, and at a boot).
2. A reply or tool_result parented on the settled pre-cut tip: the cold parse extends the last pre-cut turn (and drops the
   tail as rewound); the restore opened a turn with no prompt.
   One rule answers both: a restore over a turns section refuses unless the tail's first atom, in the cold parse's sort
   order, opens a fresh turn after the frozen last pre-cut turn (a compaction boundary, or a prompt after a turn that ended)
   and sorts at or after every pre-cut atom.
3. A document written before preGates (every document on disk at the deploy): the restore skipped the pre-cut prompt-id
   and Skill-link checks, so a command wrapper wearing the pre-cut twin's prompt id, or a Skill tool_use on the pre-cut
   payload's id, restored a wrong tree, and the restore after a compaction kept it. It now checks against the document's
   whole-adapter gates (a superset of the pre-cut sets): one over-refusal at most, after which the rewrite carries preGates.
4. A typed slash command at the cut: its raw twin makes no atom, so the cut fell on the wrapper with the twin before it,
   and every restore refused the wrapper's prompt id: two whole parses at every restart, then the entry held whole. The
   cut now takes the twin with its wrapper.
Should-fix pins: a content refusal whose rewrite would publish the same cut declines and marks the leaf (later boots pay
one whole parse and no writes); the writer's cut search is linear; the converge pass's floor and owed-mark changes; the
rest-of-append scan's watermark for a restored entry (docMaxPpt).

Every defect test fails on d088e830b; the pins and the gap-prompt control pass there, and each pin fails with its change
reverted (the docMaxPpt line, the converge loop body of c8219856f). Synthetic only: invented text, placeholder uuids."""
import gzip
import json
import os
import sys
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_record_cache_tail_r2 as R                   # noqa: E402  one kernel copy: R2Base, knobs, fresh
from test_asm_checkpoint_served import iso              # noqa: E402

T, em, km, SID, NOW, WINDOW = R.T, R.em, R.km, R.SID, R.NOW, R.WINDOW
WRAP = "<command-name>/review</command-name>\n<command-message>review</command-message>\n<command-args></command-args>"


def _ts(r):
    return em.parse_z(r["timestamp"])


class _Base(R._Roads):
    """Helpers over the 160-turn leaf: its document cuts at u158 (the last two turns are the tail), so the watermark is
    a157's stamp and the gap runs 40 s to u158's."""

    def _rec(self, u):
        return next(r for r in self.recs if r.get("uuid") == u)

    def _reply(self, u, parent, t):
        return {"type": "assistant", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "cwd": "/w/notes-api",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a reply " + u}], "stop_reason": "end_turn"}}

    def _tool_result(self, u, parent, t):
        return {"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "cwd": "/w/notes-api",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_gone", "content": "ok"}]}}

    def _counted(self, fn):
        s0, w0 = dict(em._ASM_STATS), em._ASM_CKPT_STATS.get("written", 0)
        out = fn()
        d = {k: v - s0.get(k, 0) for k, v in em._ASM_STATS.items() if v != s0.get(k, 0)}
        if em._ASM_CKPT_STATS.get("written", 0) != w0:
            d["_written"] = em._ASM_CKPT_STATS.get("written", 0) - w0
        return out, d

    def _doc_path(self):
        docs = [p for p in (self.td / "checkpoints").iterdir() if p.name.endswith(".json.gz") or p.suffix == ".gz"]
        self.assertEqual(len(docs), 1, [p.name for p in (self.td / "checkpoints").iterdir()])
        return docs[0]

    def _strip_pre_gates(self):
        """The document as a writer before preGates left it: the same bytes but that one field (the deploy's first boot)."""
        p = self._doc_path()
        st = os.stat(p)
        doc = json.loads(gzip.decompress(p.read_bytes()).decode("utf-8"))
        self.assertIn("preGates", doc)
        doc.pop("preGates")
        p.write_bytes(gzip.compress(json.dumps(doc, separators=(",", ":")).encode("utf-8"), compresslevel=6))
        os.utime(p, (st.st_atime, st.st_mtime))


class AGapStampedRecord(_Base):
    """Defect 1: a reply stamped one second after the watermark, before the cut's first record."""

    def test_after_a_compaction_in_the_same_append(self):
        wm = _ts(self._rec("a157"))
        def mutate():
            b = self._boundary(1, self.t)
            self._write(b + [self._reply("gap1", b[-1]["uuid"], wm + 1)])
        d, got = self._go(mutate)
        self.assertEqual(got, self._ref(), d)

    def test_at_a_boot(self):
        wm = _ts(self._rec("a157"))
        d, got = self._go(lambda: self._write([self._reply("gap2", self.parent, wm + 1)]), boot=True)
        self.assertEqual(got, self._ref(), d)

    def test_control_a_prompt_in_the_gap_still_restores(self):
        """A prompt in the gap opens its own turn in the cold parse too: the restore answers it (no refusal)."""
        wm = _ts(self._rec("a157"))
        def mutate():
            self._write([{"type": "user", "uuid": "gapu", "parentUuid": self.parent, "timestamp": iso(wm + 1), "promptSource": "typed",
                          "cwd": "/w/notes-api", "message": {"role": "user", "content": "a prompt in the gap"}}])
        d, got = self._go(mutate, boot=True)
        self.assertEqual((d.get("restore"), d.get("full", 0)), (1, 0), d)
        self.assertEqual(got, self._ref(), d)


class ARecordOnTheSettledPreCutTip(_Base):
    """Defect 2: a record that opens no turn, parented on a157 (the pre-cut tip), stamped current."""

    def test_a_reply_after_an_append(self):
        d, got = self._go(lambda: self._write([self._reply("tip1", "a157", self.t)]))
        self.assertEqual(got, self._ref(), d)

    def test_a_reply_at_a_boot(self):
        d, got = self._go(lambda: self._write([self._reply("tip2", "a157", self.t)]), boot=True)
        self.assertEqual(got, self._ref(), d)

    def test_a_tool_result_at_a_boot(self):
        d, got = self._go(lambda: self._write([self._tool_result("tip3", "a157", self.t)]), boot=True)
        self.assertEqual(got, self._ref(), d)


class AnOldDocumentWithoutPreGates(_Base):
    """Defect 3: the document written before preGates, a pre-cut twin (u10, pid-pre) and payload (u12, toolu_pre_sk), and a
    tail record re-classifying one of them written after the document."""
    def setUp(self):
        _Base.setUp(self)
        for r in self.recs:
            if r.get("uuid") == "u10":
                r["promptId"] = "pid-pre"
                r["message"]["content"] = "/review"
            if r.get("uuid") == "u12":
                r["sourceToolUseID"] = "toolu_pre_sk"
                r["message"]["content"] = "instructions for the deploy skill"
        Path(self.path).write_text("".join(json.dumps(x) + "\n" for x in self.recs))

    _wrapper = R.APreCutTwinAndPayload._wrapper
    _skill = R.APreCutTwinAndPayload._skill
    WRAP = WRAP

    def _old(self, rec, compaction_after=False):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            tree = self.parse()
            self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree), em.asm_checkpoint_stats())
            del tree
            self._strip_pre_gates()
            r_ = rec(self.parent)
            self._write([r_])
            self._reset()
            tree, d = self._counted(self.parse)
            em.hydrate(tree, SID)
            got = [T._strip_tree(tree)]
            if compaction_after:                          # the review: the restore after the compaction kept the wrong tree
                self._write(R.tail_turn(50, r_["uuid"], self.t + 100, boundary=True))
                tree = self.parse()
                em.hydrate(tree, SID)
                got.append(T._strip_tree(tree))
        return d, got

    def test_a_wrapper_at_the_first_boot(self):
        d, got = self._old(self._wrapper)
        self.assertEqual(got[0], self._ref(), d)

    def test_a_skill_link_at_the_first_boot(self):
        d, got = self._old(self._skill)
        self.assertEqual(got[0], self._ref(), d)

    def test_a_wrapper_then_a_compaction(self):
        d, got = self._old(self._wrapper, compaction_after=True)
        self.assertEqual(got[1], self._ref(), d)


class ATypedSlashCommandAtTheCut(_Base):
    """Defect 4: a typed slash command (raw twin, wrapper with the twin's prompt id, reply), then one more turn, settled by a
    whole parse's write; every restart after it must restore, not parse whole twice."""

    def _command_turns(self):
        t = self.t
        twin = {"type": "user", "uuid": "cmdtwin", "parentUuid": self.parent, "timestamp": iso(t), "promptSource": "typed",
                "promptId": "pid-cmd", "cwd": "/w/notes-api", "message": {"role": "user", "content": "/review"}}
        wrap = {"type": "user", "uuid": "cmdwrap", "parentUuid": "cmdtwin", "timestamp": iso(t + 1), "promptId": "pid-cmd",
                "cwd": "/w/notes-api", "message": {"role": "user", "content": WRAP}}
        rep = self._reply("cmdrep", "cmdwrap", t + 20)
        self._write([twin, wrap, rep])
        self.parent, self.t = "cmdrep", t + 60
        self.append()

    def test_restarts_restore(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self._command_turns()
            self._reset()
            tree = self.parse()                           # no document yet: the whole parse, then the settle's write
            self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree), em.asm_checkpoint_stats())
            del tree
            for boot in range(2):
                self._reset()
                _, d0 = self._counted(self.parse)
                _, d1 = self._counted(self.parse)
                self.assertEqual((d0.get("full", 0), d0.get("restore"), d1.get("full", 0)), (0, 1, 0), (boot, d0, d1))
            self._reset()
            tree = self.parse()
            em.hydrate(tree, SID)
            got = T._strip_tree(tree)
        self.assertEqual(got, self._ref())


class AContentRefusalWithTheSameCut(_Base):
    """Should-fix: a content refusal whose rewrite publishes the same cut (an orphan compaction summary in the last two
    turns, while a pre-cut boundary stands: the writer's cut cannot move past the last two turns). On d088e830b each boot
    paid two whole parses and two writes and kept the entry whole; now the rewrite declines (sameCut), the leaf is marked
    at its stat, and a later boot pays one whole parse and no write."""

    def _boots(self, recs):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            tree = self.parse()
            self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree))
            del tree
            self._write(recs)
            old = time.time() - 600
            os.utime(self.path, (old, old))               # idle from here
            per_boot = []
            for boot in range(3):
                self._reset()
                ds = [self._counted(self.parse)[1] for _ in range(3)]
                per_boot.append((sum(d.get("full", 0) for d in ds), sum(d.get("_written", 0) for d in ds)))
            tree = self.parse()
            em.hydrate(tree, SID)
            got = T._strip_tree(tree)
        return per_boot, got

    def test_one_whole_parse_per_boot_no_rewrite(self):
        per_boot, got = self._boots([self._orphan("orph1", self.parent, self.t), self._reply("orrep", "orph1", self.t + 20)])
        self.assertLessEqual(per_boot[0][0], 2, per_boot)
        self.assertEqual(per_boot[1:], [(1, 0), (1, 0)], per_boot)
        self.assertEqual(got, self._ref())

    def test_a_prompt_without_a_stamp_restores(self):
        """Its repaired stamp (the previous parseable one, alike in both parses) is read: no refusal, no whole parse."""
        u = {"type": "user", "uuid": "nostamp", "parentUuid": self.parent, "promptSource": "typed", "cwd": "/w/notes-api",
             "message": {"role": "user", "content": "a prompt without a stamp"}}
        per_boot, got = self._boots([u, self._reply("nsrep", "nostamp", self.t + 20)])
        self.assertEqual(per_boot, [(0, 0)] * 3, per_boot)
        self.assertEqual(got, self._ref())


class TheCutSearchIsLinear(R.R2Base):
    """Should-fix: a kept prompt in the last turn stamped near the start steps the writer's cut back one turn per candidate;
    each candidate re-scanned every kept record and every record (d088e830b: 2.1 s at 1,000 turns, 69.8 s at 4,000 in the
    review's probe; 5.9 s against 0.5 s for the same leaf without the stale stamp at 2,000 turns here). The search now reads
    per-candidate facts precomputed once, so the stale leaf's write costs about what the plain leaf's does."""

    def _time_write(self, turns, stale):
        from test_asm_checkpoint_served import transcript
        recs = transcript(NOW - 86400, turns=turns, compact_every=100000)
        last, t = recs[-1]["uuid"], NOW - 86400 + turns * 60
        recs.append({"type": "user", "uuid": "stale", "parentUuid": last, "promptSource": "typed", "cwd": "/w/notes-api",
                     "timestamp": iso(NOW - 86400 + 30 if stale else t + 10),
                     "message": {"role": "user", "content": "a prompt stamped near the start"}})
        recs.append({"type": "assistant", "uuid": "stalea", "parentUuid": "stale", "timestamp": iso(t + 99), "cwd": "/w/notes-api",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}})
        recs += R.tail_turn(9000, "stalea", t + 200)
        Path(self.path).write_text("".join(json.dumps(r) + "\n" for r in recs))
        old = time.time() - 600
        os.utime(self.path, (old, old))
        ck = self.td / "checkpoints"
        for p in (list(ck.iterdir()) if ck.exists() else []):
            p.unlink()
        self._reset()
        with T.knobs(0, 4, roots=[str(self.proj)]):
            tree = self.parse()
            t0 = time.perf_counter()
            ok = em.asm_checkpoint_write(self.path, SID, tree=tree)
            dt = time.perf_counter() - t0
            self.assertTrue(ok, em.asm_checkpoint_stats().get("skipped"))
            return dt

    def test_a_stale_stamp_costs_about_a_plain_write(self):
        plain = min(self._time_write(2000, False) for _ in range(2))
        stale = min(self._time_write(2000, True) for _ in range(2))
        sys.stderr.write("cut search at 2,000 turns: plain %.2f s, stale %.2f s\n" % (plain, stale))
        self.assertLess(stale, 2 * plain + 0.25, (plain, stale))


class TheRestOfAnAppendIsHeldToTheDocumentsWatermark(_Base):
    """Pin for the second review's N6d (docMaxPpt): after a compaction restored the entry from the document, a summary
    written a second before its own boundary, in the next append, restores again (the rest-of-append scan holds it to the
    document's watermark, not to the entry's, which counts the tail). With the entry's watermark it takes a whole parse."""

    def test_a_late_summary_restores(self):
        def mutate():
            bt = self.t + 30
            b = self._boundary(7, bt)
            self._write([b[0]])                            # the boundary alone: the restore after its demotion
            self.parse()
            s = dict(b[1], timestamp=iso(bt - 1))
            self._write([s] + R.tail_turn(70, s["uuid"], bt + 10))
        d, got = self._go(mutate)
        self.assertEqual((d.get("full", 0), d.get("restore")), (0, 1), d)
        self.assertEqual(got, self._ref())



SID2 = "aaaaaaaa-4444-4222-8333-777777777777"


class TheConvergePassChanges(R.R2Base):
    """Pins for the second review's three converge-pass changes (3d5099957), each failing with the loop body reverted:
    (a) the floor is spent once a pass, whether the write it lets through lands or is refused; (b) the owed mark goes only
    with a write, so another session's row over the same leaf that returns False first leaves it to the row that can write;
    (c) an owed leaf with no whole entry left is pruned."""

    def _leaves(self, n):
        from test_asm_checkpoint_served import transcript
        paths = [self.path]
        for i in range(1, n):
            p = str(self.proj / ("bbbbbbbb-4444-4222-8333-%012d.jsonl" % i))
            Path(p).write_text("".join(json.dumps(r) + "\n" for r in transcript(NOW - 86400, turns=160, compact_every=40)))
            paths.append(p)
        old = time.time() - 600
        for p in paths:
            os.utime(p, (old, old))
        return paths

    def _knobs(self, cap):
        for name, val in (("CKPT_CONVERGE_MS", 5000.0), ("CKPT_CONVERGE_BYTES", cap), ("ASM_CONVERGE", True)):
            saved = getattr(km, name); setattr(km, name, val); self.addCleanup(setattr, km, name, saved)
        for t in (km._ASM_CONVERGE_DONE, km._ASM_CONVERGE_BLIP, km._ASM_CONVERGE_NOENTRY, km._ASM_CONVERGE_OWED):
            t.clear()

    def _pass(self):
        km._begin_checkpoint_cycle()
        return km._converge_assembly(time.time(), time.monotonic())

    def test_a_the_floor_is_spent_once_a_pass(self):
        paths = self._leaves(2)
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            for i, p in enumerate(paths):
                km._parse(p, SID if i == 0 else "bbbbbbbb-4444-4222-8333-%012d" % i, NOW)
            self._knobs(min(max(4096, os.path.getsize(p) // 64) for p in paths) - 1)
            self.assertEqual(self._pass(), 0)             # both over the budget: both owed
            self.assertEqual(len(km._ASM_CONVERGE_OWED), 2)
            real, calls = em.asm_checkpoint_write, []
            def refuse_first(leaf, *a, **kw):             # the floor's write refused (a structural skip, say)
                calls.append(leaf)
                if len(calls) == 1:
                    (kw.get("reason_out") or []).append("noCut")
                    return False
                return real(leaf, *a, **kw)
            em.asm_checkpoint_write = refuse_first
            self.addCleanup(setattr, em, "asm_checkpoint_write", real)
            self._pass()
            self.assertEqual(len(calls), 1, "a second owed leaf took the floor in the same cycle: %r" % calls)
            self._pass()
            self.assertEqual(len(calls), 2, calls)

    def test_b_a_sibling_rows_false_leaves_the_owed_mark(self):
        self._leaves(1)                                   # quiescent
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            km._parse(self.path, SID, NOW)
            km._parse(self.path, SID2, NOW)               # a second session's row over the same leaf
            rows = [k for k in em.asm_whole_entries() if k[0] and str(k[0]) == os.path.realpath(self.path) or str(k[0]) == self.path]
            self.assertGreaterEqual(len(rows), 2, em.asm_whole_entries())
            self._knobs(max(4096, os.path.getsize(self.path) // 64) - 1)
            self._pass()                                  # over the budget: owed
            self.assertEqual(len(km._ASM_CONVERGE_OWED), 1, (em.asm_whole_entries(), em.asm_checkpoint_stats().get("converge"),
                                                             em._asm_ckpt_file(self.path).exists()))
            real, seen = km._converge_assembly_leaf, []
            def first_row_false(leaf, sid, *a, **kw):     # the first row returns False (a flag mismatch, say)
                seen.append(sid)
                if len(seen) == 1:
                    return False
                return real(leaf, sid, *a, **kw)
            km._converge_assembly_leaf = first_row_false
            self.addCleanup(setattr, km, "_converge_assembly_leaf", real)
            self.assertEqual(self._pass(), 1, "the row that can write took the floor (seen %r)" % seen)
            self.assertTrue(em._asm_ckpt_file(self.path).exists())

    def test_c_an_owed_leaf_with_no_whole_entry_is_pruned(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            km._parse(self.path, SID, NOW)
            self._knobs(10 ** 9)
            km._ASM_CONVERGE_OWED[str(self.proj / "gone.jsonl")] = True
            self._pass()
            self.assertNotIn(str(self.proj / "gone.jsonl"), km._ASM_CONVERGE_OWED)


if __name__ == "__main__":
    unittest.main()
