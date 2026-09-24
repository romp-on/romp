#!/usr/bin/env python3
"""A machine-cut turn whose RESUME forks the transcript keeps its history (the user 2026-08-14).

Some CLI resumes of a machine-cut turn fork a FRESH-HEADED transcript (parentUuid-null head, no
cross-file back-link) instead of continuing the chain. On disk that fork is byte-indistinguishable
from a /clear, so the parser dropped the entire pre-cut conversation (kept_uuids' clear branch), the
planner never saw the cut turn's work (no card, ever), and the kernel's episode check settled the
session's open cards as if the user typed /clear. Observed live: an hourly PR watch's finding — a
full recommendation with a decision for the user — vanished to two mid-turn restarts.

The fix records the fork as an exact event: the resumed CLI's init reports the new fsid, the backend
appends a states/ resumeFork row binding old->new, the parser stitches the fork head onto the resumed
file's tail (and follows the lineage when assembling candidate files), and the episode check stands
down on a recorded fork. A genuine /clear records no lineage, so its history keeps dropping.

SYNTHETIC fixtures only (placeholder uuids, temp dirs), modeled on test_placements_canary."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_rfl", os.path.join(BIN, "romp-judge"))
em = load_source("romp_em_rfl", os.path.join(BIN, "romp-event-model"))
sb = load_source("romp_sdk_backend_rfl", os.path.join(BIN, "romp_sdk_backend.py"))
km = load_source("romp_kernel_rfl", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
F2 = "66666666-7777-8888-9999-aaaaaaaaaaaa"
F3 = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
T0 = 1780000000
NOTICE = ("<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted and cut this "
          "session's in-flight turn; the session has been resumed with its history intact.")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def write_jsonl(path, records):
    Path(path).write_text("\n".join(json.dumps(r) for r in records) + "\n")


# The pre-cut conversation: a real ask, work, and the machine cut's interrupt record.
ANCHOR_RECORDS = [
    uline(T0, "review the new pull request on notes-api and recommend a disposition", "u1"),
    aline(T0 + 60, "Reading the diff and running the touched tests now.", "a1", "u1", stop=None),
    uline(T0 + 300, "[Request interrupted by user]", "u2", "a1"),
]
# The resume fork: a FRESH head (parentUuid None) carrying the restart notice, then the finish.
FORK_RECORDS = [
    uline(T0 + 320, NOTICE, "f1", None),
    aline(T0 + 380, "Review finished: recommend merging the notes-api pull request.", "f2", "f1"),
]


def parse(td, states_rows, leaf=F2, candidates=None):
    files = candidates if candidates is not None else [str(Path(td) / (F2 + ".jsonl")),
                                                       str(Path(td) / (SID + ".jsonl"))]
    return em.parse_session(str(Path(td) / (leaf + ".jsonl")), rompuuid=SID,
                            candidate_files=files, states=states_rows, now=T0 + 400)


def turn_texts(sess):
    out = []
    for turn in sess["turns"]:
        for a in turn["atoms"]:
            blocks = (a.get("message") or {}).get("content") or []
            if isinstance(blocks, str):
                out.append(blocks)
            else:
                out.extend(b.get("text", "") for b in blocks if isinstance(b, dict))
    return "\n".join(out)


CYCLE_WALK_LIMIT_SECS = 10

class ResumeForkStitch(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        write_jsonl(Path(self.td.name) / (SID + ".jsonl"), ANCHOR_RECORDS)
        write_jsonl(Path(self.td.name) / (F2 + ".jsonl"), FORK_RECORDS)

    def tearDown(self):
        self.td.cleanup()

    def test_a_recorded_fork_keeps_the_precut_conversation(self):
        rows = [{"t": T0 + 310, "machineCut": "restart"},
                {"t": T0 + 320, "resumeFork": {"from": SID, "to": F2}}]
        sess = parse(self.td.name, rows)
        text = turn_texts(sess)
        self.assertIn("review the new pull request", text,
                      "the pre-cut prompt must survive a recorded resume fork")
        self.assertIn("recommend merging", text, "the post-fork finish stays too")
        # …and the pre-cut prompt's segment is derivable again, anchored at ITS OWN t — the unit the
        # planner keys a card on (its absence was the lost-card bug).
        ts = [seg["t"] for turn in sess["turns"] for seg in em.segments(turn)]
        self.assertIn(T0, ts, "a segment anchored at the original prompt's t must exist")

    def test_without_lineage_the_fork_still_drops_history_like_a_clear(self):
        # A genuine /clear records no resumeFork row — pre-fork history keeps dropping by design.
        sess = parse(self.td.name, [{"t": T0 + 310, "machineCut": "restart"}])
        text = turn_texts(sess)
        self.assertNotIn("review the new pull request", text)
        self.assertIn("recommend merging", text)

    def test_lineage_extends_candidates_across_a_fork_chain(self):
        # Two restarts, two forks: SID -> F2 -> F3. The caller passes only leaf+anchor (the real
        # callers' shape); the middle file joins via the lineage closure inside parse_session.
        write_jsonl(Path(self.td.name) / (F3 + ".jsonl"),
                    [uline(T0 + 500, NOTICE, "g1", None),
                     aline(T0 + 560, "Chain finish after the second restart.", "g2", "g1")])
        rows = [{"t": T0 + 320, "resumeFork": {"from": SID, "to": F2}},
                {"t": T0 + 500, "resumeFork": {"from": F2, "to": F3}}]
        sess = parse(self.td.name, rows, leaf=F3,
                     candidates=[str(Path(self.td.name) / (F3 + ".jsonl")),
                                 str(Path(self.td.name) / (SID + ".jsonl"))])
        text = turn_texts(sess)
        self.assertIn("review the new pull request", text, "history crosses BOTH forks")
        self.assertIn("recommend merging", text, "the middle fork's records joined via the closure")
        self.assertIn("Chain finish", text)

    def test_an_intact_backlink_is_never_overridden(self):
        # A resume that DID continue the chain (back-linked head) keeps its own parent even if a
        # stray lineage row names it.
        linked = [dict(FORK_RECORDS[0], parentUuid="u2")] + FORK_RECORDS[1:]
        write_jsonl(Path(self.td.name) / (F2 + ".jsonl"), linked)
        rows = [{"t": T0 + 320, "resumeFork": {"from": SID, "to": F2}}]
        sess = parse(self.td.name, rows)
        self.assertIn("review the new pull request", turn_texts(sess))


QUEUED_NOTE = {"type": "attachment", "timestamp": iso(T0 + 315), "uuid": "r-att", "parentUuid": None,
               "attachment": {"type": "queued_command", "prompt": "a queued note"}}


def _adapter(fork_records, extra=None, links=None):
    """A FileAdapter over the anchor, the fork, and any `extra` {fsid: records} files."""
    files = {SID: ANCHOR_RECORDS, F2: fork_records, **(extra or {})}
    with tempfile.TemporaryDirectory() as td:
        paths = {fs: Path(td) / (fs + ".jsonl") for fs in files}
        for fs, recs in files.items():
            write_jsonl(paths[fs], recs)
        leaf = list(paths.values())[-1]
        return em.FileAdapter([str(pth) for pth in paths.values()], str(leaf),
                              resume_links=links or {F2: SID})


class ContinuedRootStitches(unittest.TestCase):
    """Only the root the conversation continues from is stitched; the fork's other roots stay roots."""

    def test_the_continued_root_is_stitched_and_the_note_beside_it_is_not(self):
        adapter = _adapter([QUEUED_NOTE, uline(T0 + 320, NOTICE, "f1", None),
                            dict(aline(T0 + 330, "A sub-agent's own opening.", "sc1", None), isSidechain=True),
                            aline(T0 + 380, "Review finished.", "f2", "f1")])
        self.assertEqual(adapter.parent_of.get("f1"), "u2", "the root the reply chains on")
        self.assertIsNone(adapter.parent_of.get("r-att"), "a sibling root is not stitched")
        self.assertIsNone(adapter.parent_of.get("sc1"), "the sidechain root beside the continued root stays parentless")
        self.assertNotIn("r-att", em._membership_of(adapter)["rewind"], "nobody rewound the note")

    def test_a_note_that_is_still_the_leaf_carries_the_history(self):
        adapter = _adapter([QUEUED_NOTE])
        self.assertEqual(adapter.parent_of.get("r-att"), "u2")

    def test_a_parented_attachment_does_not_end_the_opening_run(self):
        adapter = _adapter([QUEUED_NOTE,
                            {"type": "attachment", "timestamp": iso(T0 + 316), "uuid": "r-att2",
                             "parentUuid": "r-att", "attachment": {"type": "skill_listing"}},
                            uline(T0 + 320, NOTICE, "f1", None),
                            aline(T0 + 380, "Review finished.", "f2", "f1")])
        self.assertEqual(adapter.parent_of.get("f1"), "u2", "the pre-cut history stays")
        rewound = em._membership_of(adapter)["rewind"]
        self.assertFalse({"r-att", "r-att2"} & set(rewound), "neither attachment reads rewind")

    def test_a_middle_file_holding_only_the_notice_still_chains(self):
        adapter = _adapter([uline(T0 + 320, NOTICE, "f1", None)],
                           extra={F3: [uline(T0 + 500, NOTICE, "g1", None),
                                       aline(T0 + 560, "Chain finish.", "g2", "g1")]},
                           links={F2: SID, F3: F2})
        self.assertEqual(adapter.parent_of.get("g1"), "f1")
        self.assertEqual(adapter.parent_of.get("f1"), "u2", "the walk crosses both restarts")

    def test_a_later_root_in_the_fork_is_a_clear_and_stays_unstitched(self):
        adapter = _adapter(FORK_RECORDS + [uline(T0 + 600, "start over on the notes-api index", "c1", None),
                                           aline(T0 + 660, "Starting fresh.", "c2", "c1")])
        self.assertIsNone(adapter.parent_of.get("c1"), "an in-file /clear root keeps its history dropping")
        self.assertIn("u1", em._membership_of(adapter)["clear"])

    def test_a_parent_cycle_in_the_fork_ends_the_walk_without_a_stitch(self):
        import threading
        built = {}
        t = threading.Thread(target=lambda: built.setdefault("a", _adapter(
            [uline(T0 + 320, "loop a", "f1", "f2"), aline(T0 + 380, "loop b", "f2", "f1")])), daemon=True)
        t.start()
        t.join(CYCLE_WALK_LIMIT_SECS)
        self.assertFalse(t.is_alive(), "the walk over a parent cycle ends")   # a hang fails here, not in CI's timeout
        adapter = built["a"]
        self.assertEqual((adapter.parent_of.get("f1"), adapter.parent_of.get("f2")), ("f2", "f1"))


class ResumeForkStates(unittest.TestCase):
    def test_appender_reader_and_links_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            sb.append_resume_fork(Path(td), SID, SID, F2, t=T0 + 320.5)
            rows = [json.loads(l) for l in (Path(td) / "states" / (SID + ".jsonl")).read_text().splitlines()]
            self.assertEqual(rows[0]["resumeFork"], {"from": SID, "to": F2})
            self.assertEqual(em.resume_fork_links(rows), {F2: SID})

    def test_the_init_flip_records_lineage_only_for_true_resume_forks(self):
        # The one code path that knows the old->new binding: guarded so a /clear's flip and a
        # born-as-a-fork copy record nothing, and a first connect (no old fsid) has nothing to bind.
        src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
        self.assertIn("if old and not clearing and not self._fork_of:", src)
        self.assertIn("append_resume_fork(self.backend.state_dir, self.sid, old, fsid)", src)

    def test_judge_reader_sees_the_rows(self):
        # own sid: the suite shares one hermetic state root, and the common placeholder uuid's
        # states/episodes files belong to other tests' fixtures
        sid = "12121212-3434-5656-7878-909090909090"
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        (jd.STATESDIR / (sid + ".jsonl")).write_text(
            json.dumps({"t": T0, "resumeFork": {"from": sid, "to": F2}}) + "\n")
        try:
            rows = jd.resume_lineage(sid)
            self.assertEqual([(r["from"], r["to"]) for r in rows], [(sid, F2)])
        finally:
            (jd.STATESDIR / (sid + ".jsonl")).unlink()


class EpisodeBoundaryStandsDown(unittest.TestCase):
    def test_a_recorded_fork_is_not_a_clear_boundary(self):
        # own sid (the suite shares one hermetic state root; the common placeholder uuid's files
        # belong to other tests), and everything goes through KM.JD: the kernel loads judge.py under
        # the shared module name "romp_judge", so in a full-suite run km's judge instance is the
        # FIRST-loaded kernel's — rooted in that module's state dir, not this module's (the
        # test_kernel_opening_chip lesson). Writing via this module's jd left km reading no lineage.
        kjd = km.jd
        sid = "13131313-2424-3535-4646-575757575757"
        with tempfile.TemporaryDirectory() as td:
            fork = Path(td) / (F2 + ".jsonl")
            write_jsonl(fork, FORK_RECORDS)
            kjd.STATESDIR.mkdir(parents=True, exist_ok=True)
            sp = kjd.STATESDIR / (sid + ".jsonl")
            sp.write_text(json.dumps({"t": T0 + 320, "resumeFork": {"from": sid, "to": F2}}) + "\n")
            try:
                before = list(kjd.episode_rows(sid))
                km._episode_boundary_check(sid, str(fork), T0 + 400)
                self.assertEqual(list(kjd.episode_rows(sid)), before,
                                 "a recorded resume fork must not append an episode boundary")
                # control: with NO lineage the same fresh head IS a boundary (the /clear path)
                sp.unlink()
                km._episode_boundary_check(sid, str(fork), T0 + 400)
                self.assertTrue(any(r.get("head") == "f1" for r in kjd.episode_rows(sid)),
                                "an unrecorded fresh head keeps the /clear boundary behavior")
            finally:
                if sp.exists():
                    sp.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
