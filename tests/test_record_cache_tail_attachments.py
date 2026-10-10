"""A compaction summary reaches its boundary through attachment records (2026-10-09).

The Claude CLI now writes a compaction as the boundary (system, compact_boundary), then attachment records, then the summary
(isCompactSummary), parented on the LAST attachment and stamped about a second BEFORE the boundary. The restore's orphan check
(_restore_tail_refusal) and the fold's (_asm_summary_orphan) required the summary's direct parent to be the boundary, so every
such compaction read as an orphan: the boot restore refused (restore:summaryRefused) and the fold demoted with summary-orphan,
both into a whole parse of the transcript, whose retained bytes climbed outside the record cache's count. Now both follow the
parent link up through attachment records (at most _SUMMARY_ATTACH_HOPS of them) via _summary_boundary; a chain that meets a
prompt or a reply before a boundary is still an orphan.

The served-road tests here fail on c0c640f64 (the counters show the refusal and the demotion). Synthetic only: invented text,
placeholder uuids."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_record_cache_tail_r2 as R                  # noqa: E402  the round-two bases (a module reference: its tests are not re-collected)
from test_asm_checkpoint_served import iso              # noqa: E402

T, em = R.T, R.em
SID = R.SID


def live_compaction(k, parent, t, attachments=3, summary_parent=None):
    """A compaction in the live CLI's shape: the boundary at `t`, `attachments` attachment records chained on it, then the
    summary parented on the last attachment and stamped one second BEFORE the boundary. `summary_parent` overrides the
    summary's parent (the negative shapes)."""
    b = "lb%d" % k
    out = [{"type": "system", "subtype": "compact_boundary", "uuid": b, "parentUuid": None, "logicalParentUuid": parent,
            "timestamp": iso(t), "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}}]
    p = b
    for j in range(attachments):
        u = "la%d_%d" % (k, j)
        out.append({"type": "attachment", "uuid": u, "parentUuid": p, "timestamp": iso(t),
                    "attachment": {"type": "context_note_%d" % j, "content": "restored context item %d" % j}})
        p = u
    out.append({"type": "user", "uuid": "ls%d" % k, "parentUuid": summary_parent or p, "timestamp": iso(t - 1),
                "isCompactSummary": True,
                "message": {"role": "user", "content": "summary so far: the retry budget work, part %d" % k}})
    return out


def after(k, parent, t):
    """One turn chained on `parent` (a summary), as the CLI's continuation writes it."""
    return [{"type": "user", "uuid": "lu%d" % k, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed",
             "cwd": "/w/notes-api", "message": {"role": "user", "content": "carry on with part %d of the retry cap" % k}},
            {"type": "assistant", "uuid": "lr%d" % k, "parentUuid": "lu%d" % k, "timestamp": iso(t + 20), "cwd": "/w/notes-api",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "part %d done " % k + "ok " * 30}],
                         "stop_reason": "end_turn"}}]


class TheLiveShapeIsServed(R._Roads):
    """The live shape restores at a boot and folds after an append, reading nothing before the window, and the tree is the
    cold whole parse's. On c0c640f64: the boot refuses (restore:summaryRefused, a whole parse) and the fold demotes with
    summary-orphan (a whole re-read)."""

    def _live(self, k=1):
        def mutate():
            recs = live_compaction(k, self.parent, self.t)
            recs += after(k, recs[-1]["uuid"], self.t + 2)
            self._write(recs)
            self.parent, self.t = recs[-1]["uuid"], self.t + 120
        return mutate

    def _counted(self, mutate, boot):
        """(_go's counter deltas, the tree, records read before a window during the measured parse)."""
        box = {}
        def m():
            mutate()
            box["c0"] = R.cold()[0]
        d, got = self._go(m, boot=boot)
        return d, got, R.cold()[0] - box["c0"]

    def test_a_boot_restores_over_the_live_shape(self):
        d, got, cold = self._counted(self._live(), boot=True)
        self.assertNotIn("restore:summaryRefused", d, "the summary reaches its boundary through the attachments: %r" % d)
        self.assertEqual(d.get("restore"), 1, "the boot restored from the document: %r" % d)
        self.assertEqual(d.get("full", 0), 0, "no whole parse: %r" % d)
        self.assertEqual(cold, 0, "nothing read before the window: %r" % d)
        self.assertEqual(got, self._ref())

    def test_a_fold_after_an_append_carrying_the_live_shape(self):
        def mutate():
            self.parse()                                  # the restored entry is live: the append below meets the fold's gates
            self._live()()
        d, got, cold = self._counted(mutate, boot=False)
        self.assertNotIn("g:summary:orphan", d, "no summary-orphan demotion: %r" % d)
        self.assertEqual(d.get("full", 0), 0, "no whole parse: %r" % d)
        self.assertEqual(cold, 0, "nothing read before the window: %r" % d)
        self.assertEqual(got, self._ref())

    def test_two_live_compactions_at_a_boot(self):
        def mutate():
            self._live(1)()
            self._live(2)()
        d, got, cold = self._counted(mutate, boot=True)
        self.assertNotIn("restore:summaryRefused", d, d)
        self.assertEqual(d.get("restore"), 1, d)
        self.assertEqual(cold, 0, d)
        self.assertEqual(got, self._ref())


class AChainThroughAPromptIsStillAnOrphan(R._Roads):
    """The negative: the summary's parent chain meets a user record (here between the attachments and the summary) before any
    boundary. The walk follows attachments only, so the summary is still an orphan: the boot refuses and the fold demotes, and
    the tree is the cold whole parse's."""

    def _orphaned(self):
        recs = live_compaction(3, self.parent, self.t, summary_parent="lx3")
        recs.insert(-1, {"type": "user", "uuid": "lx3", "parentUuid": "la3_2", "timestamp": iso(self.t),
                         "promptSource": "typed", "cwd": "/w/notes-api",
                         "message": {"role": "user", "content": "a prompt between the attachments and the summary"}})
        recs += after(3, recs[-1]["uuid"], self.t + 2)
        self._write(recs)
        self.parent, self.t = recs[-1]["uuid"], self.t + 120

    def test_at_a_boot(self):
        d, got = self._go(self._orphaned, boot=True)
        self.assertEqual(d.get("restore:summaryRefused"), 1, d)
        self.assertEqual(got, self._ref())

    def test_at_a_fold(self):
        def mutate():
            self.parse()
            self._orphaned()
        d, got = self._go(mutate)
        self.assertEqual(d.get("g:summary:orphan"), 1, d)
        self.assertEqual(got, self._ref())


class SummaryBoundaryWalk(unittest.TestCase):
    """_summary_boundary itself: through attachments only, at most _SUMMARY_ATTACH_HOPS of them, never round a cycle."""

    def _map(self, recs):
        return {r["uuid"]: r for r in recs}

    def test_the_live_shape_and_the_direct_parent(self):
        live = live_compaction(1, "p0", 1_000_000)
        self.assertEqual(em._summary_boundary(live[-1], self._map(live).get)["uuid"], "lb1")
        direct = live_compaction(2, "p0", 1_000_000, attachments=0)
        self.assertEqual(em._summary_boundary(direct[-1], self._map(direct).get)["uuid"], "lb2")

    def test_the_hop_limit(self):
        n = em._SUMMARY_ATTACH_HOPS
        ok = live_compaction(1, "p0", 1_000_000, attachments=n)
        self.assertEqual(em._summary_boundary(ok[-1], self._map(ok).get)["uuid"], "lb1")
        far = live_compaction(2, "p0", 1_000_000, attachments=n + 1)
        self.assertIsNone(em._summary_boundary(far[-1], self._map(far).get))

    def test_a_prompt_a_missing_parent_and_a_cycle(self):
        recs = live_compaction(1, "p0", 1_000_000)
        m = self._map(recs)
        m["la1_1"] = dict(m["la1_1"], type="user")       # a prompt in the chain
        self.assertIsNone(em._summary_boundary(recs[-1], m.get))
        self.assertIsNone(em._summary_boundary(dict(recs[-1], parentUuid="nowhere"), self._map(recs).get))
        m = self._map(recs)
        m["la1_0"] = dict(m["la1_0"], parentUuid="la1_2")   # attachments in a ring
        self.assertIsNone(em._summary_boundary(recs[-1], m.get))

    def test_the_repaired_links_when_given(self):
        recs = live_compaction(1, "p0", 1_000_000)
        links = {r["uuid"]: r.get("parentUuid") for r in recs}
        links["ls1"] = "lb1"                              # the whole parse's repaired link wins over the record's own
        self.assertEqual(em._summary_boundary(recs[-1], {r["uuid"]: r for r in recs}.get, links.get)["uuid"], "lb1")


if __name__ == "__main__":
    unittest.main()
