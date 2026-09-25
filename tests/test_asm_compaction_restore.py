#!/usr/bin/env python3
"""A compaction appended past the assembly document's cut was parsed whole (the reparse investigation of 2026-09-24). The fold's
gates demote a delta holding a compact_boundary (`boundary`) or its isCompactSummary record (`summary`), rightly: the fold only
carries the emit state forward, and a compaction changes how records around it parse. But neither reason was among the
demotions that fall to the restore road (_ASM_RESTORE_AFTER_DEMOTE), so the entry went to a whole parse of the transcript
although the standing document still covers everything before its cut, and a restore parses the tail from the cut, compaction
included, exactly as every boot over a document already does. Measured on synthetic transcripts on the cluster, that whole
parse cost 0.6 to 1.7 s per compaction at 150 to 184 MB, the settle after it rewrote the document for another 2.3 to 2.7 s,
and the next parse re-seated the new whole entry on the document for 0.3 to 0.4 s.

Pinned here, each against a cold whole parse turn by turn: a compaction past the cut takes the restore road (`g:boundary` then
`restore:afterDemote`, no `full`), from an entry restored at boot and from the whole entry the settle wrote the document from; a
summary written after its boundary restores again at `g:summary`; the live manual /compact shape restores; the entry after
the compaction is the restored shape, and the next append folds onto it; and a compaction anchored before the cut still
refuses at the chain proof and parses whole.

Two limits on that road, from the review of the same day. A compaction masks nothing later in its delta: a record past it that
the fold refuses for its own reason (a stamp before the records folded, a Skill call a pre-cut payload names, a uuid a pre-cut
record named as its parent, a command-wrapper record wearing a pre-cut prompt id) keeps its whole parse, since the restore
proves only that the tail chains onto the document and would serve a tree a cold parse does not build, at every later boot
too. And a compaction restores only while the tail past the document's cut is under the churn bound's share of the pre-cut
bytes; once it has reached the share the compaction parses whole (`restore:pastShare`, or `g:tailShare` on an entry the fold's
churn gate already holds to the share, a re-seated one) and the settle writes a document with a later cut, so the tail every
restore reads stays bounded. A compaction on an entry with no document to measure parses whole as it always did and is not
counted past the share. And a compaction parses whole on a whole entry whose postal author still waits on the log, as the
writer declines to re-seat it, and on any entry whose document was written while an author waited (`postalWait`), whether it
wrote that document and has healed since or was restored from it: the document carries the provisional author and no heal
state. A document with no `postalWait` at all, written before the writer recorded the wait, counts as one written while an
author waited. The writer keeps that record on the entry as soon as it replaces the document, so a sidecar write that fails
after the replace leaves no stale record.
Synthetic transcripts only: invented notes-api text, placeholder uuids."""
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source  # noqa: F401  the direct run's floor lands at this import (tests/romp_load.py)

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs conftest's floor (a bare
# unittest or script run otherwise writes REAL state). The harness below loads the event model under the same floor.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import em, G, SID, NOW, Harness, _strip, _doc, _write_doc   # noqa: E402  the stage 4a harness and the golden builders

WORDS = ("search", "index", "notes", "query", "ranking", "cursor", "page", "cache", "schema", "migration", "endpoint", "fixture")


def episode(t0, turns=240, compact_every=80, skill_at=None, orphan_at=None, wrapper_at=None):
    """A long synthetic session: `turns` prompt-and-reply turns, with an auto compaction (the boundary anchored on the last
    record, its summary the next prompt's parent) every `compact_every` turns. `skill_at` chains a skill payload (a meta user
    record naming the Skill call `tu_skill_<k>` by sourceToolUseID) after turn k; `orphan_at` adds, after turn k, a prompt and
    reply off the chain whose parent `ghost-<k>` no record holds; `wrapper_at` chains, after turn k, a local command's wrapper
    record wearing the prompt id `pp_usage_<k>`. None of them draws from the random stream."""
    rnd = random.Random(924)
    recs, parent, t = [], None, t0
    for k in range(turns):
        if k and k % compact_every == 0:
            b, s = "b%d" % k, "s%d" % k
            recs += [G.compact_line(t, b, parent, trigger="auto"),
                     G.compact_summary_line(t + 1, s, b, text="summary so far: " + " ".join(rnd.choice(WORDS) for _ in range(80)))]
            parent = s
            t += 2
        u, a = "u%d" % k, "a%d" % k
        recs += [G.uline(t, " ".join(rnd.choice(WORDS) for _ in range(30)) + " %d" % k, u, parent),
                 G.aline(t + 20, " ".join(rnd.choice(WORDS) for _ in range(200)), a, u, stop="end_turn")]
        parent = a
        t += 60
        if k == skill_at:
            recs.append(dict(G.uline(t, "skill instructions: the notes-api search conventions", "sp%d" % k, parent),
                             sourceToolUseID="tu_skill_%d" % k, isMeta=True))
            parent = "sp%d" % k
            t += 5
        if k == orphan_at:
            recs += [G.uline(t, "an ask whose parent no transcript holds", "uo%d" % k, "ghost-%d" % k),
                     G.aline(t + 10, "a reply under it", "ao%d" % k, "uo%d" % k, stop="end_turn")]
            t += 30
        if k == wrapper_at:
            recs.append(dict(G.uline(t, "<command-name>/usage</command-name>\n<command-message>usage</command-message>\n"
                                        "<command-args></command-args>", "uw%d" % k, parent), promptId="pp_usage_%d" % k))
            parent = "uw%d" % k
            t += 5
    return recs


def filler(t, tag, parent, nbytes):
    """Settled turns chained onto `parent`, a minute apart from `t`, whose lines add up to at least `nbytes`: the tail past a
    document's cut grown in one write."""
    out, size, k = [], 0, 0
    while size < nbytes:
        u, a = "gu_%s_%d" % (tag, k), "ga_%s_%d" % (tag, k)
        pair = [G.uline(t + 60 * k, "index the next batch of notes, step %d" % k, u, parent),
                G.aline(t + 60 * k + 20, "indexed another batch of notes for the search endpoint. " * 20 + str(k), a, u, stop="end_turn")]
        out += pair
        size += sum(len(json.dumps(r)) + 1 for r in pair)
        parent, k = a, k + 1
    return out


def doc_cut(path):
    """The leaf's standing document as the bound reads it: the pre-cut bytes over the lineage and the leaf's cut offset."""
    d = _doc(path)
    pre = sum(int(f["size"]) if f.get("skip") else int((f.get("cut") or [0])[0]) for f in d["files"].values())
    return pre, int(((d["files"].get(Path(path).stem) or {}).get("cut") or [0])[0])


def compaction(t, tag, anchor):
    """An auto compaction as the CLI writes it: the boundary anchored (logicalParentUuid) on `anchor`, its summary its child.
    Returns the two records and the summary's uuid (the next prompt's parent)."""
    b, s = "cb_%s" % tag, "cs_%s" % tag
    return [G.compact_line(t, b, anchor, trigger="auto"),
            G.compact_summary_line(t + 1, s, b, text="summary so far: the notes-api search is wired (%s)" % tag)], s


def turn(t, tag, parent):
    u, a = "xu_%s" % tag, "xa_%s" % tag
    return [G.uline(t, "follow-up %s on the notes-api search" % tag, u, parent),
            G.aline(t + 20, "done with %s" % tag, a, u, stop="end_turn")]


def postal_episode():
    """The long session with turn 30's prompt a peer's message: the postal marker names a message id the log does not hold
    until `row` is handed to the parse, so the author stays provisional until then. Returns the records and the log's row."""
    recs = episode(NOW - 86400)
    i = next(j for j, r in enumerate(recs) if r.get("uuid") == "u30")
    recs[i] = dict(recs[i], message={"role": "user", "content": "please raise the notes-api search page size to 50\n"
                                                                "<!-- romp-msg-id: %s -->" % G.MID})
    recs[i].pop("promptSource", None)
    row = {"t": em.parse_z(recs[i]["timestamp"]) + 1, "ev": "sent", "id": G.MID, "from": "api", "from_id": G.PEER, "to_id": SID,
           "body": "ASK: raise the search page size"}
    return recs, row


def author_of(tree, uuid="u30"):
    """The served author of the atom `uuid` in a stripped tree."""
    return next((a.get("author") for t in tree["turns"] for a in t["atoms"] if a.get("uuid") == uuid), None)


class CompactionRestore(Harness):
    def _documented(self, name, **shape):
        """A long session parsed whole, its document written from that whole entry (the settle's write); the entry stands."""
        recs = episode(NOW - 86400, **shape)
        path = self.write(name, recs)
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "the document is written: %s" % em.asm_checkpoint_stats()["skipped"])
        return path, recs

    def _restart(self, path):
        """A restart over the document: the next parse restores from it."""
        self.fresh()
        modes = []
        self.parse(path, modes)
        self.assertEqual(modes, ["restore"], "the restart restores from the document: %s" % em.asm_checkpoint_stats())

    @staticmethod
    def _append(path, recs):
        with open(path, "a") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in recs))

    @staticmethod
    def _entry(path):
        """The leaf's assembly entry as the cache holds it (None once a cold parse or a restart cleared the cache)."""
        with em._ASM_LOCK:
            return em._ASM_CACHE.get((os.path.realpath(path), SID, False))

    def _parse_counted(self, path):
        """One parse: its tree, its roads, and the road counters it moved (restore:afterDemote, g:<reason>, full:<why>, ...)."""
        before = dict(em._ASM_STATS)
        modes = []
        tree = self.parse(path, modes)
        after = dict(em._ASM_STATS)
        moved = {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after) if after.get(k, 0) != before.get(k, 0)}
        return tree, modes, moved

    def _served(self, tree):
        """The served tree as a comparison reads it: hydrated (a restored tree's pre-cut bodies), stripped, its cut noted first."""
        cut = tree.get("cutTurn", 0)
        em.hydrate(tree, SID)
        return _strip(tree), cut

    def _snapshot(self, path, tag):
        """The leaf's bytes now, under the same file name in a directory of their own: the cold parse's reference for this step,
        taken at the end (a cold parse clears the caches the steps after it read)."""
        d = self.td / ("ref-" + tag)
        d.mkdir()
        ref = d / Path(path).name
        shutil.copyfile(path, ref)
        return str(ref)

    def assertSameTree(self, got, want, what):
        """Two stripped trees, compared by length first and then turn by turn, naming the first turn that differs (a whole-tree
        assertEqual over hundreds of turns prints a diff nobody reads), then the session's own fields."""
        self.assertEqual(len(got["turns"]), len(want["turns"]), "%s: the turn count" % what)
        first = next((i for i, (a, b) in enumerate(zip(got["turns"], want["turns"])) if a != b), None)
        if first is not None:
            g, w = got["turns"][first], want["turns"][first]
            self.fail("%s: turn %d differs from the cold parse: served %s %r, cold %s %r" % (
                what, first, g.get("id"), [a.get("uuid") for a in g["atoms"]], w.get("id"), [a.get("uuid") for a in w["atoms"]]))
        self.assertEqual({k: v for k, v in got.items() if k != "turns"}, {k: v for k, v in want.items() if k != "turns"},
                         "%s: the session's fields" % what)

    def assertRestored(self, modes, moved, reason, what):
        self.assertEqual(moved.get("full", 0), 0, "%s: no whole parse for a compaction past the cut: road %s, counters moved %s"
                         % (what, modes, moved))
        self.assertEqual((modes, moved.get("g:" + reason, 0), moved.get("restore:afterDemote", 0)), (["restore"], 1, 1),
                         "%s: the gates demote at %s and the restore road serves: counters moved %s" % (what, reason, moved))

    def test_a_compaction_past_the_cut_restores_from_the_standing_document_and_the_next_append_folds(self):
        path, recs = self._documented("restored")
        self._restart(path)
        pair, s = compaction(self.after(recs, 120), "c1", recs[-1]["uuid"])
        more = pair + turn(self.after(recs, 150), "c1", s)
        self._append(path, more)
        tree, modes, moved = self._parse_counted(path)
        got, cut = self._served(tree)
        ref = self._snapshot(path, "compaction")
        nxt = turn(self.after(recs + more, 60), "next", more[-1]["uuid"])
        self._append(path, nxt)
        tree2, modes2, moved2 = self._parse_counted(path)
        got2, cut2 = self._served(tree2)
        self.assertSameTree(got, self.cold(ref), "after the compaction")
        self.assertSameTree(got2, self.cold(path), "after the next append")
        self.assertRestored(modes, moved, "boundary", "the compaction")
        self.assertGreater(cut, 0, "the restored shape: the pre-cut turns from the document, the render floor at the cut")
        self.assertEqual((modes2, moved2.get("full", 0)), (["fold"], 0), "the next append folds: %s" % moved2)
        self.assertGreater(cut2, 0, "onto the restored entry, not a whole one: every later fold re-derives the tail only")

    def test_a_compaction_on_the_whole_entry_the_document_was_written_from_restores_too(self):
        path, recs = self._documented("whole")                  # no restart: the entry the settle wrote from stands, whole
        pair, s = compaction(self.after(recs, 120), "c2", recs[-1]["uuid"])
        self._append(path, pair + turn(self.after(recs, 150), "c2", s))
        tree, modes, moved = self._parse_counted(path)
        got, cut = self._served(tree)
        reseated = (self._entry(path) or {}).get("reseated")
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path)
        skipped = dict(em.asm_checkpoint_stats()["skipped"])
        self.assertSameTree(got, self.cold(path), "after the compaction")
        self.assertRestored(modes, moved, "boundary", "the compaction")
        self.assertGreater(cut, 0, "the restored shape")
        self.assertEqual((wrote, skipped), (False, {"restored": 1}),
                         "the document stands: a restored entry writes nothing, so a compaction under the share leaves the cut")
        self.assertIs(reseated, True, "the writer marked the whole entry for its re-seat, and the compaction's restore carries the "
                      "mark, so the fold's churn gate holds the restored entry to the share")

    def test_a_summary_written_after_its_boundary_restores_at_each_step(self):
        """The two records can reach the parse in separate writes: the boundary demotes at `boundary`, the summary alone at
        `summary`, and each restores; the turn after them folds."""
        path, recs = self._documented("split")
        self._restart(path)
        pair, s = compaction(self.after(recs, 120), "c3", recs[-1]["uuid"])
        steps = [("boundary", pair[:1]), ("summary", pair[1:]), (None, turn(self.after(recs, 150), "c3", s))]
        served = []
        for reason, grp in steps:
            self._append(path, grp)
            tree, modes, moved = self._parse_counted(path)
            got, cut = self._served(tree)
            served.append((reason, modes, moved, got, cut, self._snapshot(path, "split-%d" % len(served))))
        for reason, modes, moved, got, cut, ref in served:
            self.assertSameTree(got, self.cold(ref), "after the %s step" % (reason or "turn"))
            if reason is None:
                self.assertEqual((modes, moved.get("full", 0)), (["fold"], 0), "the turn after the pair folds: %s" % moved)
            else:
                self.assertRestored(modes, moved, reason, "the %s alone" % reason)
            self.assertGreater(cut, 0, "the restored shape after the %s step" % (reason or "turn"))

    def test_a_live_manual_compact_past_the_cut_restores(self):
        """The live manual /compact shape (tests/test_event_model_golden.manual_compact_lines): the boundary and summary land as a
        detached pair, then the command wrappers and the stdout; the conversation chains through the wrappers."""
        path, recs = self._documented("manual")
        self._restart(path)
        t = self.after(recs, 120)
        side, so = G.manual_compact_lines(t, t + 10, "m1", parent=recs[-1]["uuid"])
        self._append(path, side + turn(t + 40, "m1", so))
        tree, modes, moved = self._parse_counted(path)
        got, cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after the manual compact")
        self.assertRestored(modes, moved, "boundary", "the manual compact")
        self.assertGreater(cut, 0, "the restored shape")

    def test_a_compaction_anchored_before_the_cut_is_refused_by_the_chain_proof_and_parsed_whole(self):
        """The guard the restore road brings with it: a compaction anchored on a pre-cut record (a rewind, then a compaction)
        invalidates the document's pre-cut verdicts, so the chain proof refuses it and the parse is whole, as at boot."""
        path, recs = self._documented("interior")
        self._restart(path)
        pair, s = compaction(self.after(recs, 120), "c5", "a10")
        self._append(path, pair + turn(self.after(recs, 150), "c5", s))
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after a compaction anchored before the cut")
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("restore:chainRefused", 0), moved.get("full:demoted", 0)),
                         (["full"], 1, 1, 1), "the restore is tried and the chain proof refuses it: counters moved %s" % moved)

    def _tip_time(self, path, recs):
        """The stamp of the standing document's pre-cut spine tip."""
        d = _doc(path)
        tip = d["records"][d["spine"][-1]][0]
        return next(em.parse_z(r["timestamp"]) for r in recs if r.get("uuid") == tip)

    def _past_the_compaction(self, reason, path, recs, s, t):
        """Records a fold refuses for `reason`, chained onto the compaction's summary `s`, from `t` on."""
        if reason == "ts":        # typed 30 s before the pre-cut tip, written after the compaction: a cold parse sorts it into
            #                       the pre-cut turns
            return [G.uline(self._tip_time(path, recs) - 30, "typed earlier, written after the compaction", "xu_ts", s),
                    G.aline(t + 20, "answered after the compaction", "xa_ts", "xu_ts", stop="end_turn")]
        if reason == "skill-link":   # the Skill call the pre-cut payload after turn 150 names
            call = G.aline(t + 10, "loading the notes skill", "xa_sk", "xu_sk", tools=("Skill",), stop="tool_use")
            call["message"]["content"][-1]["id"] = "tu_skill_150"
            return [G.uline(t, "use the notes skill", "xu_sk", s), call,
                    G.aline(t + 20, "used the notes skill", "xa_sk2", "xa_sk", stop="end_turn")]
        if reason == "uuid-known":   # the record the pre-cut orphan after turn 150 named as its parent
            return [G.uline(t, "the ask the orphaned one answered", "ghost-150", s),
                    G.aline(t + 20, "answered", "xa_gh", "ghost-150", stop="end_turn")]
        if reason == "promptid":     # the local command's output, wearing the prompt id of the pre-cut wrapper after turn 150: a
            #                          command-wrapper record under a folded prompt id can re-classify the records already read
            return [dict(G.uline(t, "<local-command-stdout>tokens: plenty left</local-command-stdout>", "xo_pi", s),
                         promptId="pp_usage_150")] + turn(t + 30, "pi", "xo_pi")
        raise AssertionError(reason)

    def test_a_compaction_masks_no_later_record_that_parses_whole_for_its_own_reason(self):
        """The gates read a delta to its end before a compaction's demotion is filed: a record past the compaction that the
        fold refuses for its own reason (a stamp before the records folded, a Skill call a pre-cut payload names, a uuid a
        pre-cut record named as its parent, a command-wrapper record wearing a pre-cut prompt id) demotes for that reason and
        parses whole, from the restored entry and from the whole one. The restore would have proved only that the tail chains
        onto the document, and it served a tree a cold parse does not build; the settle after the whole parse rewrites the
        document, so the next boot equals cold as well (review of 2026-09-24: under the first cut of the rule these restored,
        and every later boot served the same tree)."""
        shapes = {"ts": {}, "skill-link": {"skill_at": 150}, "uuid-known": {"orphan_at": 150}, "promptid": {"wrapper_at": 150}}
        ran = []
        for reason, shape in shapes.items():
            for entry in ("restored", "whole"):
                with self.subTest(reason=reason, entry=entry):
                    name = "mask-%s-%s" % (reason, entry)
                    path, recs = self._documented(name, **shape)
                    if entry == "restored":
                        self._restart(path)
                    pair, s = compaction(self.after(recs, 120), "mk", recs[-1]["uuid"])
                    self._append(path, pair + self._past_the_compaction(reason, path, recs, s, self.after(recs, 130)))
                    tree, modes, moved = self._parse_counted(path)
                    got, _cut = self._served(tree)
                    ref = self._snapshot(path, name)
                    self.doc(path)                                         # the settle after the step
                    self.fresh()
                    boot_modes = []
                    boot, _cut = self._served(self.parse(path, boot_modes))
                    self.assertSameTree(got, self.cold(ref), "live, a compaction then a record refused for %s" % reason)
                    self.assertSameTree(boot, self.cold(path), "at the next boot (road %s)" % boot_modes)
                    self.assertEqual((modes, moved.get("g:" + reason, 0), moved.get("g:boundary", 0), moved.get("restore:afterDemote", 0)),
                                     (["full"], 1, 0, 0), "the delta demotes for %s and parses whole: counters moved %s" % (reason, moved))
                    ran.append((reason, entry))
        self.assertEqual(len(ran), 8, "every shape ran on both entries: %s" % ran)

    def test_a_compaction_once_the_tail_has_reached_the_share_parses_whole_and_the_settle_advances_the_cut(self):
        """The churn bound holds on the restore road: a compaction restores only while the leaf's bytes past the standing
        document's cut, times the share, are under the pre-cut bytes. Once the tail has reached the share the compaction parses
        whole, the settle writes a document whose cut is past the compaction, and the next boot restores from it. Without this
        the cut of the entry a boot restores never moved again: the fold's own share gate reads only an atoms-only restore and a
        re-seated entry, a boot's restore through the lazy index is neither, and a restored entry writes nothing, so the tail
        every restore reads grew for the session's life (review of 2026-09-24: 34 MB past the cut after four compactions at 150
        to 184 MB, and the boot's restore doubled). On that boot-restored entry the share test is this change's own
        (`restore:pastShare` beside `g:boundary`). The whole entry the document was written from is re-seated on it by the
        growth's parse (the writer marked it), so the fold's churn gate already holds it: at the share the gate demotes it
        before the compaction is read (`g:tailShare`), and the compaction parses whole under that reason."""
        # per entry: the growth's road with its g:reseat count, then the compaction's road and counters
        # (g:boundary, g:tailShare, restore:pastShare, restore:afterDemote, full:demoted)
        want = {"restored": ((["fold"], 0), (["full"], 1, 0, 1, 0, 1)),
                "whole": ((["restore"], 1), (["full"], 0, 1, 0, 0, 1))}
        ran = []
        for entry in ("restored", "whole"):
            with self.subTest(entry=entry):
                name = "share-" + entry
                path, recs = self._documented(name)
                if entry == "restored":
                    self._restart(path)
                pre0, cut0 = doc_cut(path)
                grow = filler(self.after(recs, 60), entry, recs[-1]["uuid"],
                              pre0 // em._ASM_TAIL_SHARE - (os.path.getsize(path) - cut0) + 1)
                self._append(path, grow)
                _t, gmodes, gmoved = self._parse_counted(path)              # the growth: a fold, or the whole entry's re-seat;
                #                                                             nothing here settles
                pair, s = compaction(self.after(recs + grow, 120), "sh", grow[-1]["uuid"])
                self._append(path, pair + turn(self.after(recs + grow, 150), "sh", s))
                tail = os.path.getsize(path) - cut0
                tree, modes, moved = self._parse_counted(path)
                got, _cut = self._served(tree)
                ref = self._snapshot(path, name)
                em._ASM_CKPT_STATS["skipped"] = {}
                wrote = self.doc(path)
                skipped = dict(em.asm_checkpoint_stats()["skipped"])
                pre1, cut1 = doc_cut(path)
                self.fresh()
                boot_modes = []
                boot, _cut = self._served(self.parse(path, boot_modes))
                growth, counts = want[entry]
                self.assertEqual((gmodes, gmoved.get("g:reseat", 0)), growth, "the growth's road: counters moved %s" % gmoved)
                self.assertGreaterEqual(tail * em._ASM_TAIL_SHARE, pre0, "the compaction lands with the tail at the share")
                self.assertSameTree(got, self.cold(ref), "after the compaction")
                self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("g:tailShare", 0), moved.get("restore:pastShare", 0),
                                  moved.get("restore:afterDemote", 0), moved.get("full:demoted", 0)), counts,
                                 "the compaction parses whole once the tail has reached the share: counters moved %s" % moved)
                self.assertTrue(wrote, "the settle writes a new document: %s" % skipped)
                self.assertGreater(cut1, cut0, "the document's cut advanced")
                self.assertLess((os.path.getsize(path) - cut1) * em._ASM_TAIL_SHARE, pre1, "the tail past the new cut is under the share")
                self.assertEqual(boot_modes, ["restore"], "the next boot restores from the new document")
                self.assertSameTree(boot, self.cold(path), "at the next boot")
                ran.append(entry)
        self.assertEqual(ran, ["restored", "whole"])

    def test_a_compaction_on_an_entry_with_no_document_parses_whole_and_is_not_counted_past_the_share(self):
        """`restore:pastShare` names a compaction whose tail had reached the share of a document it could have restored from.
        A whole entry that wrote no document (here a session under the first document's floor, so the settle declines) has
        nothing to measure: its compaction parses whole as it always did, counted under `g:boundary` and `full:demoted` only.
        Counted past the share, every compaction of every young session would have filled the counter in /perf with cases
        that never had a restore to decline (verification of the review's fold, 2026-09-24)."""
        saved = em._ASM_FIRST_DOC_MIN
        self.addCleanup(setattr, em, "_ASM_FIRST_DOC_MIN", saved)
        em._ASM_FIRST_DOC_MIN = 10 ** 9                      # the floor holds the session's first document back
        recs = episode(NOW - 86400)
        path = self.write("nodoc", recs)
        self.fresh()
        self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path)
        skipped = dict(em.asm_checkpoint_stats()["skipped"])
        pair, s = compaction(self.after(recs, 120), "nd", recs[-1]["uuid"])
        self._append(path, pair + turn(self.after(recs, 150), "nd", s))
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        self.assertFalse(wrote, "the floor declines the first document: %s" % skipped)
        cp = em._asm_ckpt_file(path)
        self.assertTrue(cp is None or not cp.exists(), "no document stands for the leaf")
        self.assertSameTree(got, self.cold(path), "after the compaction")
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:pastShare", 0),
                          moved.get("restore:afterDemote", 0)), (["full"], 1, 1, 0, 0),
                         "a compaction with no document to measure parses whole, not counted past the share: %s" % moved)

    def _postal_compaction(self, name, restart, predates=False):
        """The postal episode parsed whole and its document written while the author waits on the log (with `predates`, the
        document as a writer before the wait record wrote it; and, with `restart`, a restart that restores the entry from that
        document); then a compaction under the share; the settle; the log catching up and a turn; the settle; and a restart.
        Returns what each step saw."""
        recs, row = postal_episode()
        path = self.write(name, recs)
        self.fresh()
        self.parse(path)
        out = {"waiting": set(((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec") or ())}
        out["wrote"] = self.doc(path)
        out["docWait"] = _doc(path).get("postalWait")
        if predates:
            d = _doc(path)
            d.pop("postalWait", None)                                # the document as a writer before the record wrote it
            _write_doc(path, d)
        if restart:
            self._restart(path)
        out["entryWait"] = (self._entry(path) or {}).get("docPostalWait")
        pair, s = compaction(self.after(recs, 120), name, recs[-1]["uuid"])
        more = pair + turn(self.after(recs, 150), name, s)
        self._append(path, more)
        pre, cut_off = doc_cut(path)
        out["under"] = (os.path.getsize(path) - cut_off) * em._ASM_TAIL_SHARE < pre
        _t, out["modes"], out["moved"] = self._parse_counted(path)
        self.doc(path)                                               # the settle after the compaction
        self.sent = [row]                                            # the log catches up
        self._append(path, turn(self.after(recs + more, 60), name + "-2", more[-1]["uuid"]))
        tree2, out["modes2"], out["moved2"] = self._parse_counted(path)
        out["got2"], _cut = self._served(tree2)
        self.doc(path)                                               # the settle after the heal
        self.fresh()
        boot_modes = []
        out["boot"], _cut = self._served(self.parse(path, boot_modes))
        out["bootModes"] = boot_modes
        out["cold"] = self.cold(path)
        return out

    def assertCompactionParsedWholeAndHealed(self, out, what):
        self.assertIn("u30", out["waiting"], "the fixture's premise: the marker waits on the log")
        self.assertTrue(out["wrote"], "the fixture's premise: the document is written while the author waits")
        self.assertTrue(out["under"], "the fixture's premise: the compaction lands with the tail under the share")
        moved = out["moved"]
        self.assertEqual((out["modes"], moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:afterDemote", 0),
                          moved.get("restore:pastShare", 0)), (["full"], 1, 1, 0, 0),
                         "%s: the compaction parses whole: counters moved %s" % (what, moved))
        self.assertEqual(out["modes2"], ["fold"], "%s: the whole entry folds the next turn: counters moved %s" % (what, out["moved2"]))
        self.assertEqual((author_of(out["got2"]) or {}).get("peer"), G.PEER,
                         "%s: the log caught up, and the served author is the peer's (%r)" % (what, author_of(out["got2"])))
        self.assertSameTree(out["got2"], out["cold"], "%s: after the log caught up" % what)
        self.assertEqual(out["bootModes"], ["restore"], "%s: the next restart restores" % what)
        self.assertSameTree(out["boot"], out["cold"], "%s: at the next restart" % what)

    def test_a_compaction_on_a_whole_entry_whose_postal_author_waits_on_the_log_parses_whole_and_heals(self):
        """A peer's message whose postal id the log does not hold yet gets a provisional author, and the whole entry re-authors
        it once the log catches up (the assembly's heal). The document carries the provisional author and no heal state, so the
        writer does not mark such an entry for its re-seat; restored from that document at a compaction, the entry served the
        provisional author after the log caught up, where a cold parse names the peer, until its next whole parse (found once
        main re-seated whole entries, 2026-09-24). The compaction parses whole instead, counted as it always was, and the parse
        after the log catches up folds, heals, and serves the cold parse's tree, as does the next restart."""
        self.assertCompactionParsedWholeAndHealed(self._postal_compaction("postal-whole", restart=False), "a whole entry")

    def test_a_compaction_on_an_entry_restored_from_a_document_written_while_a_postal_author_waited_parses_whole_and_heals(self):
        """The same author, frozen in the document, reaches an entry a restart restores from it: that entry has no heal state
        for the pre-cut part, so it serves the provisional author, and on main the compaction's whole parse was what healed it.
        Restored from the document at the compaction instead, the entry kept the provisional author after the log caught up and
        at the next restart, where a cold parse names the peer (a probe, 2026-09-24). The writer records its postal test in the
        document (`postalWait`), the restore carries it, and the compaction parses whole."""
        out = self._postal_compaction("postal-restored", restart=True)
        self.assertEqual((out["docWait"], out["entryWait"]), (True, True),
                         "the document records the wait and the restored entry carries it")
        self.assertCompactionParsedWholeAndHealed(out, "an entry restored at a restart")

    def test_a_compaction_on_a_whole_entry_healed_after_its_document_was_written_parses_whole(self):
        """The whole entry heals its provisional author once the log catches up, but the document it wrote while the author
        waited still carries that author, and the settle after the heal leaves the document standing (its tail is under the
        share). Restored from it at a compaction, the entry served the provisional author live and at the next restart, where
        a cold parse names the peer. The writer keeps the document's `postalWait` on the entry it wrote from, so the
        compaction parses whole, as every compaction does on main."""
        recs, row = postal_episode()
        path = self.write("postal-healed", recs)
        self.fresh()
        self.parse(path)
        self.assertIn("u30", ((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec") or (), "the premise: the marker waits")
        self.assertTrue(self.doc(path), "the premise: the document is written while the author waits")
        self.sent = [row]                                            # the log catches up
        t1 = turn(self.after(recs, 60), "healed-1", recs[-1]["uuid"])
        self._append(path, t1)
        _t, modes1, _m = self._parse_counted(path)                   # the whole entry folds and heals
        self.assertEqual(modes1, ["fold"], "the premise: the whole entry folds the turn")
        self.assertFalse(((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec"), "the premise: the entry healed")
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path), "the premise: the settle after the heal writes nothing")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"written": 1}, "the premise: the document stands")
        allr = recs + t1
        pair, s = compaction(self.after(allr, 120), "healed", t1[-1]["uuid"])
        self._append(path, pair + turn(self.after(allr, 150), "healed-2", s))
        pre, cut_off = doc_cut(path)
        self.assertLess((os.path.getsize(path) - cut_off) * em._ASM_TAIL_SHARE, pre, "the premise: under the share")
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        ref = self._snapshot(path, "postal-healed")
        self.doc(path)                                               # the settle after the compaction
        self.fresh()
        boot_modes = []
        boot, _cut = self._served(self.parse(path, boot_modes))
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:afterDemote", 0)),
                         (["full"], 1, 1, 0), "the compaction parses whole: counters moved %s" % moved)
        self.assertEqual((author_of(got) or {}).get("peer"), G.PEER, "the served author is the peer's (%r)" % author_of(got))
        self.assertSameTree(got, self.cold(ref), "after the compaction")
        self.assertEqual(boot_modes, ["restore"], "the next restart restores")
        self.assertSameTree(boot, self.cold(path), "at the next restart")

    def test_a_compaction_on_an_entry_restored_from_a_document_that_predates_the_wait_record_parses_whole_and_heals(self):
        """A document written before the writer recorded the wait carries no `postalWait`, even when an author waited as it
        was written, and it is still this version's document. Read as no wait, a compaction on an entry a restart restored
        from it restored again and kept the provisional author after the log caught up, live and at the next restart, where
        a cold parse names the peer (a probe, 2026-09-25). The restore reads the key's absence as a wait, so the compaction
        parses whole once, as on main, and the settle after it writes a document that records the wait either way."""
        self.assertCompactionParsedWholeAndHealed(self._postal_compaction("postal-predates", restart=True, predates=True),
                                                  "an entry restored from a document that predates the record")

    def test_a_compaction_after_a_rewrite_whose_sidecar_write_failed_parses_whole(self):
        """The writer replaces the document before it writes the sidecar, and a sidecar write that fails is the counted `write`
        skip with the new document already standing. Here an entry kept whole (a compaction anchored before the cut) rewrites its
        document while a peer's marker before the new cut waits on the log, the sidecar write fails, the log catches up and a fold
        heals the entry with no settle after it, and a compaction lands under the share. With the document's record of the wait
        kept on the entry only after the sidecar, the entry still said no wait, and the compaction restored the new document's
        provisional author, live and at the next restart, where a cold parse names the peer (a probe, 2026-09-25). The record is
        kept as soon as the document is replaced, so the compaction parses whole."""
        recs = episode(NOW - 86400)
        path = self.write("postal-sidecar", recs)
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "the premise: the first document is written")
        allr = list(recs)
        pair, s = compaction(self.after(allr, 120), "sck", "a10")      # anchored before the cut: the chain proof refuses it
        more = pair + turn(self.after(allr, 150), "sck", s)
        self._append(path, more)
        allr += more
        self.parse(path)
        self.assertTrue((self._entry(path) or {}).get("keepWhole"), "the premise: the entry is kept whole")
        t = self.after(allr, 60)
        more = [G.uline(t, "please raise the notes-api search page size to 50\n<!-- romp-msg-id: %s -->" % G.MID, "um_sc",
                        allr[-1]["uuid"], ps=None),
                G.aline(t + 20, "raised the notes-api search page size", "am_sc", "um_sc", stop="end_turn")]
        more += turn(self.after(allr + more, 60), "sc0", more[-1]["uuid"])
        more += turn(self.after(allr + more, 60), "sc1", more[-1]["uuid"])
        self._append(path, more)
        allr += more
        self.parse(path)
        self.assertIn("um_sc", ((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec") or (), "the premise: the marker waits")
        orig, reasons = Path.write_text, []
        def failing(p, *a, **k):
            if ".meta." in p.name:
                raise OSError("the sidecar write fails")
            return orig(p, *a, **k)
        Path.write_text = failing
        try:
            wrote = em.asm_checkpoint_write(path, SID, tree=self._trees.get(path), reason_out=reasons)
        finally:
            Path.write_text = orig
        self.assertEqual((wrote, reasons), (False, ["write"]), "the premise: the sidecar write fails")
        self.assertIs(_doc(path).get("postalWait"), True, "the premise: the rewritten document stands and records the wait")
        self.sent = [{"t": t + 1, "ev": "sent", "id": G.MID, "from": "api", "from_id": G.PEER, "to_id": SID,
                      "body": "ASK: raise the search page size"}]    # the log catches up
        nxt = turn(self.after(allr, 60), "sch", allr[-1]["uuid"])
        self._append(path, nxt)
        allr += nxt
        _t, modes1, _m = self._parse_counted(path)                   # a fold heals the entry; no settle before the compaction
        self.assertEqual(modes1, ["fold"], "the premise: the kept-whole entry folds the turn")
        self.assertFalse(((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec"), "the premise: the entry healed")
        pair, s = compaction(self.after(allr, 120), "scc", allr[-1]["uuid"])
        self._append(path, pair + turn(self.after(allr, 150), "scc", s))
        pre, cut_off = doc_cut(path)
        self.assertLess((os.path.getsize(path) - cut_off) * em._ASM_TAIL_SHARE, pre, "the premise: under the share")
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        ref = self._snapshot(path, "postal-sidecar")
        self.doc(path)                                               # the settle after the compaction
        self.fresh()
        boot_modes = []
        boot, _cut = self._served(self.parse(path, boot_modes))
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:afterDemote", 0)),
                         (["full"], 1, 1, 0), "the compaction parses whole: counters moved %s" % moved)
        self.assertEqual((author_of(got, "um_sc") or {}).get("peer"), G.PEER, "the served author is the peer's (%r)" % author_of(got, "um_sc"))
        self.assertSameTree(got, self.cold(ref), "after the compaction")
        self.assertEqual(boot_modes, ["restore"], "the next restart restores")
        self.assertSameTree(boot, self.cold(path), "at the next restart")


if __name__ == "__main__":
    unittest.main()
