#!/usr/bin/env python3
"""Segment-identity canary (the user 2026-07-09, the cleared-cards-reappear regression).

Placement keys are `fsid:t:texthash`. If ANY deployed change shifts the derivation (em.segments'
atom-text reconstruction, the hash input, _seg_key normalization), every placement recorded under the
old derivation stops matching, and the planner replays dormant history as junk cards — cleared work
pops back onto the feed with old timestamps. That is exactly what happened on 07-07/07-08: a segment-
text change stepped the hash without a PLACEMENTS_V bump, and each kernel restart re-minted cards the
user had already cleared.

Identity has TWO dimensions, and the fixture exercises both: (1) the id derivation itself (t + text
hash), and (2) WHICH atoms parse out of a transcript at all — the 2026-07-10 absorbed-atom fix grew
the atom set (previously-lost spliced messages became visible) without a bump, and two dormant
sessions replayed their morning history as fresh goals within minutes (planned, done, auto-nudged).
The fixture therefore includes a mid-turn absorbed splice behind a dead enqueue, so its segment is
part of the pinned set.

This canary pins the derived seg ids for a fixed synthetic transcript. If it fails, the derivation
changed: bump jd.PLACEMENTS_V (sealing every older store's ready-unplaced history at the next pass)
and update these pins IN THE SAME commit. See the PLACEMENTS_V comment in bin/romp-judge for the
deploy rule. Synthetic fixtures only."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_canary", os.path.join(BIN, "romp-judge")).load_module()
em = SourceFileLoader("romp_em_canary", os.path.join(BIN, "romp-event-model")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


# One transcript, four segment flavors: a plain typed ask, a romp-injected goal nudge (marker-bearing
# text exercises the authorship/marker-sensitive path), a follow-up typed ask, and a mid-turn ABSORBED
# splice sitting behind a dead enqueue (a resolution the killed CLI never wrote) — the atom the old
# FIFO pairing lost entirely, pinned so the atom SET is part of placement identity too (2026-07-10).
RECORDS = [
    uline(T0, "please add a dark-mode toggle to the settings page", "u1"),
    aline(T0 + 60, "Added the toggle and wired the persistence.", "a1", "u1"),
    uline(T0 + 120, "<!-- romp-injected -->[romp] Status check: is goal g3 finished? <!-- romp-goal-id: g3 -->",
          "u2", "a1"),
    aline(T0 + 180, "Yes, g3 is finished.", "a2", "u2"),
    uline(T0 + 240, "now rename the exported CSV columns to snake_case", "u3", "a2"),
    aline(T0 + 300, "Working on the rename.", "a3", "u3", stop=None),
    {"type": "queue-operation", "timestamp": iso(T0 + 310), "operation": "enqueue",
     "content": "<task-notification>\n<task-id>t000</task-id>\n</task-notification>"},   # dead: never resolved
    {"type": "queue-operation", "timestamp": iso(T0 + 330), "operation": "enqueue", "content": None},
    {"type": "attachment", "timestamp": iso(T0 + 330), "uuid": "att1", "parentUuid": "a3",
     "attachment": {"type": "queued_command", "commandMode": "prompt",
                    "prompt": [{"type": "text", "text": "also gzip the exported CSV"}]}},
    {"type": "queue-operation", "timestamp": iso(T0 + 335), "operation": "remove", "content": None},
    aline(T0 + 360, "Renamed the columns, updated the importer tests, gzipped the export.", "a4", "att1"),
    # a COMPACTION much later (2026-07-13): the boundary opens its OWN turn (the phantom pre-compaction
    # work-bar fix) — pinned here so the compact-turn split is part of placement identity too.
    {"type": "system", "subtype": "compact_boundary", "timestamp": iso(T0 + 4000), "uuid": "cb1",
     "parentUuid": None, "logicalParentUuid": "a4",
     "compactMetadata": {"trigger": "manual", "preTokens": 90000}},
    aline(T0 + 4020, "Resuming after compaction.", "a5", "cb1"),
    # a DETACHED live manual /compact (2026-08-19): boundary+summary land as a side branch
    # (parentUuid null + logicalParentUuid; the conversation chains through the /compact
    # caveat/wrapper/stdout records) and the adoption repair splices them back in AFTER the
    # stdout. Pinned here because this class changes BOTH identity dimensions when it drifts:
    # the splice position decides whether the stdout atom mints a phantom triggerless WORK
    # unit, and the replay-dedup arming decides whether the user's next typed prompt — u7
    # deliberately repeats u3's text — survives in the atom set at all.
    uline(T0 + 5000, "now wire the audit log into the export page", "u6", "a5"),
    aline(T0 + 5060, "Wired the audit log through.", "a6", "u6"),
    {"type": "system", "subtype": "compact_boundary", "timestamp": iso(T0 + 5400), "uuid": "cb2",
     "parentUuid": None, "logicalParentUuid": "a6",
     "compactMetadata": {"trigger": "manual", "preTokens": 90000, "postTokens": 4000}},
    {"type": "user", "timestamp": iso(T0 + 5400), "uuid": "cs2", "parentUuid": "cb2",
     "isCompactSummary": True, "isVisibleInTranscriptOnly": True, "promptId": "pc2",
     "message": {"role": "user", "content": "synthetic summary of the audit-log work"}},
    {"type": "user", "timestamp": iso(T0 + 5300), "uuid": "rt2", "parentUuid": "a6",
     "isMeta": True, "promptId": "pc2", "message": {"role": "user", "content": "/compact"}},
    {"type": "user", "timestamp": iso(T0 + 5300), "uuid": "cw2", "parentUuid": "rt2", "promptId": "pc2",
     "message": {"role": "user", "content": "<command-name>/compact</command-name>\n"
                                            "<command-message>compact</command-message>\n"
                                            "<command-args></command-args>"}},
    {"type": "user", "timestamp": iso(T0 + 5400), "uuid": "so2", "parentUuid": "cw2", "promptId": "pc2",
     "message": {"role": "user", "content": "<local-command-stdout>Compacted "
                                            "(ctrl+o to see full summary)</local-command-stdout>"}},
    uline(T0 + 5500, "now rename the exported CSV columns to snake_case", "u7", "so2"),
    aline(T0 + 5560, "Renamed them again on the new export page.", "a7", "u7"),
]

# The pinned derivation, recorded under PLACEMENTS_V = 7 (2026-08-01; the derivation itself is unchanged
# since v6 — v7 seals for a GROWN atom set, the replay-guard scoping. The LAST id of the first five — a
# text-less segment — moved off the shared sha1('') hash da39a3ee onto its anchor atom's uuid, so text-less
# seams no longer alias each other; the four text-bearing ids above it are unchanged, they still key on
# content). The last four ids pin the detached manual-compact block (2026-08-19, no bump — additions only):
# u6's ask, the /compact command segment, the adopted boundary's own text-less segment, and u7's
# repeated-text ask (same text hash as u3's id, its own t — present at all only because the adopted
# boundary does not arm the replay dedup).
EXPECTED_SEG_IDS = [
    SID + ":1780000000:ca8d36fd",
    SID + ":1780000120:f03c5f4f",
    SID + ":1780000240:686c9d66",
    SID + ":1780000330:f3320ed1",
    SID + ":1780004000:d780b71b",
    SID + ":1780005000:d105998b",
    SID + ":1780005300:9b15c581",
    SID + ":1780005400:06676388",
    SID + ":1780005500:686c9d66",
]

# Dimension 2 pinned EXPLICITLY (WHICH atoms parse, in rendered order): seg ids alone cannot see an
# atom that changes segments without changing any id — the detached compact's stdout (so2) belongs to
# the /compact COMMAND turn (cb2 sorts after it), and u7 must be in the set at all.
EXPECTED_ATOM_UUIDS = [
    "u1", "a1", "u2", "a2", "u3", "a3", "att1", "a4", "cb1", "a5",
    "u6", "a6", "cw2", "so2", "cb2", "u7", "a7",
]

# The judge-visible units (seg id, kind, human): the /compact command segment and the adopted
# boundary's work-less segment yield NONE — a drift that mints a unit here replays as a fresh card
# in every dormant session that ever compacted manually. (cb1's unit is the ATTACHED shape's
# continuation work — a5 chains through the boundary — and predates this block.)
EXPECTED_UNITS = [
    (SID + ":1780000000:ca8d36fd", "work", True),
    (SID + ":1780000120:f03c5f4f", "nudge", False),
    (SID + ":1780000240:686c9d66", "work", True),
    (SID + ":1780000330:f3320ed1", "work", True),
    (SID + ":1780004000:d780b71b", "work", False),
    (SID + ":1780005000:d105998b", "work", True),
    (SID + ":1780005500:686c9d66", "work", True),
]


def _parse_fixture():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / (SID + ".jsonl")
        tp.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
        return jd.parsed_session(SID, [str(tp)], T0 + 4000)


class PlacementIdentityCanary(unittest.TestCase):
    def test_seg_id_derivation_is_pinned_to_placements_v(self):
        sess = _parse_fixture()
        ids = [seg["id"] for turn in sess["turns"] for seg in em.segments(turn)]
        self.assertEqual(
            ids, EXPECTED_SEG_IDS,
            "\n\nSegment-id derivation CHANGED. Stored placements no longer match, so dormant sessions"
            "\nwill replay their history as junk cards (the 2026-07-09 cleared-cards-reappear bug)."
            "\nIn THIS commit: bump jd.PLACEMENTS_V (seals v(n-1) stores' ready-unplaced units at the"
            "\nnext pass) and re-pin EXPECTED_SEG_IDS to the new derivation. Current PLACEMENTS_V=%d."
            % jd.PLACEMENTS_V)

    def test_atom_set_is_pinned(self):
        # Dimension 2 of placement identity: WHICH atoms parse, and in what order. A change here
        # without a bump replays dormant history (the 2026-07-10 absorbed-atom incident) or — worse —
        # silently DROPS a real message (the 2026-08-19 manual-compact replay-dedup incident, which
        # ate u7's shape while every pinned seg id of the day still matched).
        sess = _parse_fixture()
        self.assertEqual([a.get("uuid") for t in sess["turns"] for a in t["atoms"]],
                         EXPECTED_ATOM_UUIDS)

    def test_plan_units_are_pinned(self):
        # The judge-visible face of the same identity: a unit minted from a segment that never
        # yielded one before (the 2026-08-19 phantom "Compacted (ctrl+o…)" WORK unit) files fresh
        # cards for every manual compact in every existing session at the next kernel restart.
        sess = _parse_fixture()
        units = jd.plan_units({"turns": sess["turns"], "rompUuid": sess["rompUuid"]})
        self.assertEqual([(u[0], u[1], u[4]) for u in units], EXPECTED_UNITS)

    def test_placements_v_is_current(self):
        # The pins above were recorded under this version; a bump without re-pinning (or re-pinning
        # without a bump) should both fail loudly.
        # v6 (2026-07-22, the uuid-anchored text-less seg id) shifted the LAST pinned id off da39a3ee;
        # text-bearing ids are unchanged. Pins and version re-recorded together.
        # v8 (2026-08-13, the shape-B twin drop): this fixture carries no command wrappers, so every
        # pinned id is UNCHANGED — the bump seals transcripts that DO carry shape-B commands, whose
        # phantom twin atom drops out of the set.
        # v9 (2026-08-14, the resume-fork stitch): this fixture records no resumeFork lineage, so
        # every pinned id is UNCHANGED — the bump seals sessions whose machine-cut resumes forked
        # fresh-headed transcripts, whose previously-dropped pre-cut atoms rejoin the set
        # (tests/test_kernel_resume_fork_lineage.py covers the stitch itself).
        self.assertEqual(jd.PLACEMENTS_V, 9, "EXPECTED_SEG_IDS was pinned under PLACEMENTS_V=9 — "
                         "re-pin the ids and this version together, in the same commit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
