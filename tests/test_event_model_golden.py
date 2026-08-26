#!/usr/bin/env python3
"""Golden contract tests for the rebuilt bottom-layer parser (bin/romp-event-model).

Each scenario builds a SYNTHETIC transcript (invented prompt text, placeholder
UUIDs, hostname TESTHOST — never real session data, per CLAUDE.md), runs the
REAL parse_session on it with a fixed clock, and compares the full Session ->
Turn -> Atom tree against a checked-in golden JSON file. The unit classes below
pin the subtle invariants that are hard to eyeball in a JSON diff: author
classification, the absorb-vs-queue turn boundary, turn/segment derivation,
`ended` inference, the resume/clear lineage walk, idle-from-the-state-log, and
popAll.

Run:    python3 tests/test_event_model_golden.py
Regen:  python3 tests/test_event_model_golden.py --regen   (then REVIEW the diff)
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "bin")
GOLDEN = Path(HERE) / "fixtures" / "event-model-golden"

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = SourceFileLoader("romp_event_model", os.path.join(SCRIPTS, "romp-event-model")).load_module()

NOW = 1781100000                      # fixed test clock — goldens depend on it
SID = "11111111-2222-3333-4444-555555555555"      # the session's stable ROMP UUID
PEER = "99999999-8888-7777-6666-000000000000"     # a peer session's ROMP UUID
MID = "1700000000.111_222.TESTHOST"               # a synthetic postal message id
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── synthetic on-disk line builders (mirror the real transcript shapes) ──
def uline(t, text, uuid, parent=None, ps="typed"):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "user", "content": text}}
    if ps is not None:
        r["promptSource"] = ps
    return r


def aline(t, text, uuid, parent=None, tools=(), stop="end_turn", thinking=None):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if text:
        content.append({"type": "text", "text": text})
    for i, n in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": n, "input": {}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def trline(t, tool_use_id, uuid, parent=None, content="ok"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result",
                        "tool_use_id": tool_use_id, "content": content}]}}


def qop(t, op, content=None):
    return {"type": "queue-operation", "timestamp": iso(t), "operation": op, "content": content}


def attline(t, prompt, uuid, parent=None):
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "isSidechain": False, "attachment": {"type": "queued_command", "prompt": prompt}}


def compact_line(t, uuid, logical_parent, trigger="manual", pre=263239, post=6514):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": None, "logicalParentUuid": logical_parent, "isMeta": False,
            "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post}}


def compact_line_broken(t, uuid, dangling_logical, preserved_tail, trigger="auto", pre=99999):
    # a compact_boundary whose logicalParentUuid points at a uuid that exists NOWHERE
    # (as seen in real transcripts); the real in-file pre-compaction leaf is in
    # compactMetadata.preservedSegment.tailUuid
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": None, "logicalParentUuid": dangling_logical, "isMeta": False,
            "compactMetadata": {"trigger": trigger, "preTokens": pre,
                                "preservedSegment": {"headUuid": preserved_tail,
                                                     "anchorUuid": preserved_tail,
                                                     "tailUuid": preserved_tail}}}


def compact_summary_line(t, uuid, parent, text="summary of the conversation so far"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
            "message": {"role": "user", "content": text}}


def manual_compact_lines(t_issue, t_done, tag, parent, summary_text="summary of the conversation so far",
                         summary_pid=True):
    """The on-disk tail of a LIVE manual /compact, exactly as the CLI writes it (shape verified
    against the live corpus 2026-08-19; all content here synthetic): the boundary + summary are
    appended FIRST, at COMPLETION time, as a DETACHED side branch — the boundary carries
    parentUuid:null + logicalParentUuid:<the pre-compact leaf>, and the summary is its only
    child. THEN come the command-wrapper records (the raw-text twin and the <command-name>
    wrapper, stamped with the earlier ISSUE time), then the stdout at completion time. The
    conversation chains through the wrappers — NOTHING on the active path visits the side
    branch, which is why the walk dropped the boundary and the chat lost its card.
    The summary record carries the invoking /compact's promptId (13/13 manual compacts in the
    live corpus) — the designed link the adoption repair keys on; summary_pid=False models a
    write without it, which the repair must still adopt via the file-order fallback.
    Returns (records, stdout_uuid); chain the next prompt off the stdout, as the CLI does."""
    cb, cs, rt, cw, so = ("cb" + tag, "cs" + tag, "rt" + tag, "cw" + tag, "so" + tag)
    summary = compact_summary_line(t_done, cs, parent=cb, text=summary_text)
    if summary_pid:
        summary["promptId"] = "p" + tag
    recs = [
        compact_line(t_done, cb, logical_parent=parent, trigger="manual"),
        summary,
        {"type": "user", "timestamp": iso(t_issue), "uuid": rt, "parentUuid": parent,
         "isMeta": True, "promptId": "p" + tag,
         "message": {"role": "user", "content": "/compact"}},
        {"type": "user", "timestamp": iso(t_issue), "uuid": cw, "parentUuid": rt,
         "promptId": "p" + tag,
         "message": {"role": "user", "content": "<command-name>/compact</command-name>\n"
                                                "<command-message>compact</command-message>\n"
                                                "<command-args></command-args>"}},
        {"type": "user", "timestamp": iso(t_done), "uuid": so, "parentUuid": cw,
         "promptId": "p" + tag,
         "message": {"role": "user", "content": "<local-command-stdout>Compacted "
                                                "(ctrl+o to see full summary)</local-command-stdout>"}},
    ]
    return recs, so


def tasknote_line(t, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "system",
            "message": {"role": "user", "content": "<task-notification>\nbackground agent finished\n</task-notification>"}}


def postal_line(t, text, uuid, parent, mid=MID, ps=None):
    body = text + "\n<!-- romp-msg-id: %s -->" % mid
    return uline(t, body, uuid, parent, ps=ps)


SENT_LOG = [{"t": T0 + 190, "ev": "sent", "id": MID, "from": "feeddesign",
             "from_id": PEER, "to_id": SID, "body": "ASK: bump the alpha"}]


# ───────────────────────── scenarios ─────────────────────────
def scenario_multi_input_absorbed():
    """A typed opener, then a mid-turn prompt spliced in (enqueue -> remove, recorded
    only as a queued_command attachment) while the assistant is mid-tool. One turn,
    two inputs, two segments."""
    return [
        uline(T0, "refactor the ledger", "u1", ps="typed"),
        aline(T0 + 20, "Reading romp-ledger.", "a1", "u1", tools=("Read",), stop="tool_use"),
        qop(T0 + 40, "enqueue", "also rename the digest file"),
        qop(T0 + 60, "remove"),
        attline(T0 + 60, "also rename the digest file", "att1", "a1"),
        aline(T0 + 90, "Folded the rename in too.", "a2", "att1", stop="end_turn"),
    ]


def scenario_author_kinds():
    """Each turn-opener author (human / sdk / peer) opens a turn; a system
    (task-notification) atom folds into the current turn, never opens one."""
    return [
        uline(T0, "human typed prompt", "u1", ps="typed"),
        aline(T0 + 10, "ack human", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "sdk injected prompt", "u2", "a1", ps="sdk"),
        aline(T0 + 110, "ack sdk", "a2", "u2", stop="end_turn"),
        postal_line(T0 + 200, "ASK: bump the recency alpha", "u3", "a2"),
        aline(T0 + 210, "ack peer", "a3", "u3", stop="end_turn"),
        tasknote_line(T0 + 300, "u4", "a3"),                 # folds into the peer turn
        aline(T0 + 310, "continued after the task note", "a4", "u4", stop="end_turn"),
    ]


def scenario_queued_new_turn():
    """A prompt that arrives AFTER end_turn (a dequeued queued prompt is just a normal
    user line) opens a NEW turn — the position-based boundary, no queue-op needed."""
    return [
        uline(T0, "first ask", "u1", ps="typed"),
        aline(T0 + 20, "first reply", "a1", "u1", stop="end_turn"),
        qop(T0 + 30, "enqueue", "second ask"),
        qop(T0 + 60, "dequeue"),
        uline(T0 + 60, "second ask", "u2", "a1", ps="queued"),
        aline(T0 + 90, "second reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_compaction_atom():
    """A compact_boundary system line becomes one compaction atom (pre_tokens mapped);
    its paired isCompactSummary line is dropped as an atom but kept in the graph, so the
    pre-compaction turn stays on the active path via logicalParentUuid (the stitch)."""
    return [
        uline(T0, "long running refactor", "u1", ps="typed"),
        aline(T0 + 30, "Working through it.", "a1", "u1", tools=("Edit",), stop="end_turn"),
        compact_line(T0 + 500, "c1", logical_parent="a1", trigger="manual", pre=263239),
        compact_summary_line(T0 + 505, "cs1", parent="c1"),
        uline(T0 + 520, "continue post-compaction", "u2", "cs1", ps="sdk"),
        aline(T0 + 530, "Continuing.", "a2", "u2", stop="end_turn"),
    ]


def scenario_compaction_broken_stitch():
    """Real-data case (3/69 compactions): a compact_boundary whose logicalParentUuid points
    at a uuid present in NO transcript line. Followed blindly it orphans ALL pre-compaction
    history; the repair re-points the stitch at compactMetadata.preservedSegment.tailUuid
    (the real in-file pre-compaction leaf), so u1 is retained, not dropped."""
    return [
        uline(T0, "pre-compaction ask", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "pre-compaction reply", "a1", "u1", stop="end_turn"),
        compact_line_broken(T0 + 500, "c1", dangling_logical="ghost-pre-compaction-leaf",
                            preserved_tail="a1", trigger="auto", pre=99999),
        uline(T0 + 520, "post-compaction ask", "u2", parent="c1", ps="sdk"),
        aline(T0 + 530, "post-compaction reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_manual_compact_detached():
    """A LIVE manual /compact (the user 2026-08-19): the boundary + summary land as a DETACHED
    side branch (10/13 manual boundaries in the live corpus; the other 3 are resume re-splices
    that arrive attached) while the conversation chains through the /compact command wrappers.
    The walk never visited the branch, so no compact atom was emitted and the chat showed no
    "Context compacted" card — while auto-compactions, which chain THROUGH their boundary,
    kept theirs. The adoption repair splices the pair back in at its anchor."""
    side, so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
    return [
        uline(T0, "start the long build", "u1", ps="typed"),
        aline(T0 + 30, "Working on it.", "a1", "u1", stop="end_turn"),
    ] + side + [
        uline(T0 + 500, "carry on with the build", "u2", so, ps="typed"),
        aline(T0 + 530, "Continuing.", "a2", "u2", stop="end_turn"),
    ]


def scenario_idle_atom():
    """An idle atom is synthesized from a real idle transition in the state log (NOT a
    silence heuristic) and folds into the turn it follows; the gap colors as not-working."""
    return [
        uline(T0, "investigate the crash", "u1", ps="typed"),
        aline(T0 + 30, "Reproduced it.", "a1", "u1", tools=("Bash",), stop="end_turn"),
        uline(T0 + 3600, "continue please", "u2", "a1", ps="sdk"),     # revived an hour later
        aline(T0 + 3630, "Resumed work.", "a2", "u2", stop="end_turn"),
    ]


IDLE_STATES = [
    {"t": T0 + 30, "state": "working"},
    {"t": T0 + 60, "state": "idle"},          # idle span [T0+60, T0+3600)
    {"t": T0 + 3600, "state": "working"},
]


def scenario_popall():
    """popAll clears the whole queue at once: every still-queued item is spliced into the
    continuation as an absorbed mid-turn atom (the old code missed this op)."""
    return [
        uline(T0, "start the big task", "u1", ps="typed"),
        aline(T0 + 20, "Working.", "a1", "u1", tools=("Read",), stop="tool_use"),
        qop(T0 + 30, "enqueue", "first queued note"),
        qop(T0 + 40, "enqueue", "second queued note"),
        qop(T0 + 50, "popAll"),
        attline(T0 + 50, "first queued note", "att1", "a1"),
        attline(T0 + 51, "second queued note", "att2", "att1"),
        aline(T0 + 90, "Folded both notes in.", "a2", "att2", stop="end_turn"),
    ]


def scenario_clear_breaks_lineage():
    """`/clear` starts a fresh root (parentUuid:null) with no link to pre-clear history,
    so the leaf->root walk stops at it and pre-clear atoms drop out for free."""
    return [
        uline(T0, "pre-clear ask", "u1", ps="typed"),
        aline(T0 + 30, "pre-clear reply", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "post-clear ask", "u2", parent=None, ps="typed"),   # fresh root
        aline(T0 + 130, "post-clear reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_rewind_off_path():
    """A rewound branch (its chain rejoins the active spine at a1) is intentionally
    dropped; only the surviving attempt remains."""
    return [
        uline(T0, "first attempt", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "did it one way", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "abandoned follow-up", "u2", parent="a1", ps="typed"),   # rewound
        aline(T0 + 130, "going down a dead end", "a2", "u2", stop="end_turn"),
        uline(T0 + 200, "second attempt instead", "u3", parent="a1", ps="typed"),
        aline(T0 + 230, "better approach done", "a3", "u3", stop="end_turn"),
    ]


def scenario_broken_chain_kept():
    """Safety floor (this repo's one fatal error is silently dropping a real ask): a real
    prompt whose parentUuid points at a uuid that exists NOWHERE (corruption / partial
    write) is NOT a proven rewind and NOT a clean null root, so it is KEPT — unlike a
    rewind fork or a /clear branch, which are intentionally dropped."""
    return [
        uline(T0, "main line ask", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "main reply", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "orphaned but real ask", "ux", parent="ghost-missing-uuid", ps="typed"),
        aline(T0 + 130, "orphan reply", "ax", "ux", stop="end_turn"),
        uline(T0 + 200, "second main ask", "u2", parent="a1", ps="typed"),       # the active leaf line
        aline(T0 + 230, "second main reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_slash_command_turn():
    """A turn opened ONLY by a slash command: the command line is skipped as an atom, but
    the assistant work that follows must still form a turn (trigger=null), never orphan."""
    return [
        {"type": "user", "timestamp": iso(T0), "uuid": "cmd1", "parentUuid": None,
         "message": {"role": "user", "content": "<command-name>/code-review</command-name>"}},
        aline(T0 + 30, "Reviewing the diff.", "a1", "cmd1", tools=("Bash",), stop="end_turn"),
    ]


# resume across a fork is two files; handled specially in run_scenario
def scenario_resume_lineage_fileA():
    return [
        uline(T0, "first ask before resume", "u1", ps="typed"),
        aline(T0 + 30, "reply in the parent transcript", "a1", "u1", stop="end_turn"),
    ]


def scenario_resume_lineage_fileB():
    # first line's parentUuid links into file A's a1 (a resume fork)
    return [
        uline(T0 + 100, "second ask after resume", "u2", parent="a1", ps="typed"),
        aline(T0 + 130, "reply in the resumed transcript", "a2", "u2", stop="end_turn"),
    ]


SINGLE_FILE = {
    "multi_input_absorbed": (scenario_multi_input_absorbed, None),
    "author_kinds": (scenario_author_kinds, SENT_LOG),
    "queued_new_turn": (scenario_queued_new_turn, None),
    "compaction_atom": (scenario_compaction_atom, None),
    "compaction_broken_stitch": (scenario_compaction_broken_stitch, None),
    "manual_compact_detached": (scenario_manual_compact_detached, None),
    "idle_atom": (scenario_idle_atom, IDLE_STATES),
    "popall": (scenario_popall, None),
    "clear_breaks_lineage": (scenario_clear_breaks_lineage, None),
    "rewind_off_path": (scenario_rewind_off_path, None),
    "broken_chain_kept": (scenario_broken_chain_kept, None),
    "slash_command_turn": (scenario_slash_command_turn, None),
}

# fsid stems for the resume scenario (placeholder UUIDs)
FSID_A = "aaaaaaaa-0000-0000-0000-000000000000"
FSID_B = "bbbbbbbb-0000-0000-0000-000000000000"


def run_single(name):
    records, sent = SINGLE_FILE[name]
    states = IDLE_STATES if name == "idle_atom" else None
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / (SID + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        return em.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR",
                                candidate_files=[str(path)], states=states,
                                postal_log=sent or [], now=NOW)


def run_recs(records):
    """Run the event model over a raw record list (for ad-hoc, non-golden scenarios)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / (SID + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return em.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR",
                                candidate_files=[str(path)], states=None, postal_log=[], now=NOW)


def run_resume():
    with tempfile.TemporaryDirectory() as td:
        pa = Path(td) / (FSID_A + ".jsonl")
        pb = Path(td) / (FSID_B + ".jsonl")
        pa.write_text("\n".join(json.dumps(r) for r in scenario_resume_lineage_fileA()) + "\n")
        pb.write_text("\n".join(json.dumps(r) for r in scenario_resume_lineage_fileB()) + "\n")
        return em.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR",
                                candidate_files=[str(pa), str(pb)], states=None,
                                postal_log=[], now=NOW)


def run_scenario(name):
    return run_resume() if name == "resume_lineage" else run_single(name)


ALL_SCENARIOS = list(SINGLE_FILE) + ["resume_lineage"]


# ───────────────────────── golden comparison ─────────────────────────
class GoldenTests(unittest.TestCase):
    maxDiff = None


def _add_case(name):
    def test(self):
        gp = GOLDEN / (name + ".json")
        self.assertTrue(gp.exists(), "missing golden %s — run with --regen and review" % gp)
        expected = json.loads(gp.read_text())
        actual = json.loads(json.dumps(run_scenario(name)))
        self.assertEqual(expected, actual,
                         "tree changed for %r — if intended, --regen and review the diff" % name)
    setattr(GoldenTests, "test_" + name, test)


for _n in ALL_SCENARIOS:
    _add_case(_n)


# ───────────────────────── invariant unit tests ─────────────────────────
def _authors(turns):
    return [t["trigger"] and _trigger_author(t) for t in turns]


def _trigger_author(turn):
    trig = turn["trigger"]
    if not trig:
        return None
    a = next((x for x in turn["atoms"] if x.get("uuid") == trig["uuid"]), None)
    return a.get("author") if a else None


class Authorship(unittest.TestCase):
    def test_opener_authors_human_sdk_peer(self):
        out = run_scenario("author_kinds")
        self.assertEqual([_trigger_author(t) for t in out["turns"]],
                         ["human", "sdk", {"peer": PEER, "mid": MID, "kind": ""}])

    def test_romp_auto_marker_flags_rompAuto_but_a_button_nudge_does_not(self):
        # An AUTO-nudge (kernel _auto_nudge_tick) carries romp-injected AND romp-auto → its trigger atom is
        # flagged rompAuto; a Nudge BUTTON (romp-injected only) is NOT (the user 2026-06-23). The timeline/chat
        # key the romp-logo on rompAuto, so only auto-nudges (+ postal) are marked, never the user's clicks.
        recs = [
            uline(T0, "Status?\n\n<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: g1 -->", "u1"),
            aline(T0 + 10, "ok", "a1", "u1"),
            uline(T0 + 100, "Nudge\n\n<!-- romp-injected --><!-- romp-goal-id: g1 -->", "u2", "a1"),
            aline(T0 + 110, "ok2", "a2", "u2"),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR",
                                   candidate_files=[str(path)], states=None, postal_log=[], now=NOW)
        def trig_atom(turn):
            trig = turn.get("trigger") or {}
            return next((x for x in turn["atoms"] if x.get("uuid") == trig.get("uuid")), None)
        autos = [bool((trig_atom(t) or {}).get("rompAuto")) for t in out["turns"] if t.get("trigger")]
        self.assertEqual(autos, [True, False], "auto-nudge trigger is rompAuto; the button-nudge trigger is not")

    def test_system_task_notification_folds_in(self):
        out = run_scenario("author_kinds")
        self.assertEqual(len(out["turns"]), 3, "system atom must NOT open a turn")
        peer_turn = out["turns"][2]
        sysauthors = [a.get("author") for a in peer_turn["atoms"]
                      if a["type"] == "user" and a.get("author") == "system"]
        self.assertEqual(sysauthors, ["system"], "task-notification folds into the peer turn")

    def test_peer_rompuuid_resolved_from_messages_log(self):
        out = run_scenario("author_kinds")
        self.assertEqual(_trigger_author(out["turns"][2]).get("peer"), PEER)

    def test_peer_null_when_id_absent_from_log(self):
        # same postal marker, but the message id is not in the log -> peer rompUuid null
        with tempfile.TemporaryDirectory() as td:
            recs = [postal_line(T0, "ASK: do a thing", "u1", None),
                    aline(T0 + 20, "done", "a1", "u1", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        self.assertEqual(_trigger_author(out["turns"][0]).get("peer"), None)


class RompInjectedAuthor(unittest.TestCase):
    """author_of('romp') ONLY for a message romp itself injected into a pane (a feed NUDGE / auto-nudge /
    Retry) — the romp-injected marker makes it a SYSTEM message so the chat renders the gray romp bubble
    instead of the blue user bubble. A follow-up the user TYPES carries only romp-goal-id ("which goal",
    for the reopen) and stays 'human' — it's the user's words, not romp's (the user 2026-06-20)."""

    @staticmethod
    def _blocks(text):
        return [{"type": "text", "text": text}]

    def test_goal_id_alone_is_human_a_typed_follow_up(self):
        # a follow-up the USER types: the kernel tags it with romp-goal-id (for the reopen) but NOT
        # romp-injected — it's the user's message, so it must render as the blue human bubble, not romp.
        b = self._blocks("> the goal\n\nWhat did you change?\n\n<!-- romp-goal-id: sid:g1 -->")
        self.assertEqual(em.author_of(b, "typed", {}), "human",
                         "romp-goal-id is 'which goal' metadata, not authorship — a typed follow-up stays human")

    def test_explicit_romp_injected_marker_authors_romp(self):
        b = self._blocks("Picking this back up.\n\n<!-- romp-injected -->")
        self.assertEqual(em.author_of(b, None, {}), "romp")

    def test_nudge_has_both_markers_and_authors_romp(self):
        # a Nudge button / auto-nudge: the kernel adds BOTH romp-injected (gray bubble) and romp-goal-id
        # (reopen). romp-injected wins over promptSource=typed — the nudge is pasted, not typed by you.
        b = self._blocks("> the goal\n\nStatus on the goal above?\n\n<!-- romp-injected --><!-- romp-goal-id: sid:g1 -->")
        self.assertEqual(em.author_of(b, "typed", {}), "romp",
                         "romp-injected authors romp even though promptSource=typed")

    def test_plain_typed_prompt_is_still_human(self):
        self.assertEqual(em.author_of(self._blocks("just a normal message"), "typed", {}), "human")

    def test_postal_marker_still_wins_for_a_peer_message(self):
        b = self._blocks("DELEGATE: do a thing\n\n<!-- romp-msg-id: m1 -->")
        self.assertEqual(em.author_of(b, "typed", {"m1": PEER}).get("peer"), PEER,
                         "a real peer message stays a peer card, not a romp injection")


class TurnBoundaries(unittest.TestCase):
    def test_absorbed_prompt_stays_in_turn(self):
        out = run_scenario("multi_input_absorbed")
        self.assertEqual(len(out["turns"]), 1, "an absorbed mid-turn prompt must not open a turn")
        inputs = [a for a in out["turns"][0]["atoms"]
                  if a["type"] == "user" and a.get("author") == "human"]
        self.assertEqual(len(inputs), 2, "the turn holds two inputs (opener + absorbed)")

    def test_absorbed_atom_anchors_on_attachment(self):
        out = run_scenario("multi_input_absorbed")
        absorbed = [a for a in out["turns"][0]["atoms"]
                    if a.get("uuid") == "att1" and a["type"] == "user"]
        self.assertEqual(len(absorbed), 1, "absorbed atom anchors on the queued_command attachment")

    def test_prompt_after_end_turn_opens_new_turn(self):
        out = run_scenario("queued_new_turn")
        self.assertEqual(len(out["turns"]), 2)
        self.assertEqual([t["trigger"]["uuid"] for t in out["turns"]], ["u1", "u2"])

    def test_romp_nudge_opens_its_own_turn_so_the_judges_process_it(self):
        # the user 2026-06-21: a romp NUDGE / auto-nudge (author 'romp', carrying romp-injected + romp-goal-id)
        # is a fresh prompt to the agent and MUST open its own turn. Before this, it folded into the prior
        # (already-completed) turn — the planner never read the romp-goal-id off a trigger, so the goal never
        # reopened and NO judge ran on the follow-up. Now it opens a turn with the nudge atom as the trigger.
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do the thing", "u1", ps="typed"),
                    aline(T0 + 10, "did it, done", "a1", "u1", stop="end_turn"),
                    uline(T0 + 100, "Status on the goal above?\n\n<!-- romp-injected --><!-- romp-goal-id: %s:g1 -->" % SID,
                          "u2", "a1", ps="typed"),
                    aline(T0 + 110, "everything is done", "a2", "u2", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        self.assertEqual(len(out["turns"]), 2, "the nudge opens a SECOND turn, not folded into the completed one")
        self.assertEqual(_trigger_author(out["turns"][1]), "romp", "the second turn is opened by the romp nudge")
        self.assertEqual(out["turns"][1]["trigger"]["uuid"], "u2",
                         "the nudge atom is the trigger — so _seg_followup reads its romp-goal-id and reopens the goal")


class TurnVsSegment(unittest.TestCase):
    """A turn is end_turn-bounded (may hold several inputs); a segment is the per-input
    span. The absorbed turn is ONE turn but TWO segments."""

    def test_absorbed_turn_is_one_turn_two_segments(self):
        out = run_scenario("multi_input_absorbed")
        turn = out["turns"][0]
        segs = em.segments(turn)
        self.assertEqual(len(segs), 2)
        self.assertEqual([s["trigger"] for s in segs], ["u1", "att1"])

    def test_popall_turn_three_segments(self):
        out = run_scenario("popall")
        self.assertEqual(len(out["turns"]), 1)
        segs = em.segments(out["turns"][0])
        self.assertEqual(len(segs), 3, "opener + two popAll-absorbed inputs = three segments")


class EndedInference(unittest.TestCase):
    def test_ended_true_on_end_turn(self):
        out = run_scenario("queued_new_turn")
        self.assertTrue(all(t["ended"] for t in out["turns"]))

    def test_ended_false_when_last_assistant_is_tool_use(self):
        # a turn whose last assistant line stopped on tool_use (interrupted / still working)
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do the thing", "u1", ps="typed"),
                    aline(T0 + 20, "calling a tool", "a1", "u1", tools=("Bash",), stop="tool_use")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        self.assertFalse(out["turns"][0]["ended"])


class Compaction(unittest.TestCase):
    def test_compaction_atom_shape(self):
        out = run_scenario("compaction_atom")
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0]["compact_metadata"],
                         {"trigger": "manual", "pre_tokens": 263239, "post_tokens": 6514})

    def test_compact_summary_line_not_emitted(self):
        out = run_scenario("compaction_atom")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("cs1", uuids, "the isCompactSummary payload is not an atom")

    def test_compaction_summary_is_attached_to_the_boundary_atom(self):
        # the summary payload is not its own atom, but its TEXT rides the boundary atom so the chat can show
        # it in a collapsible box (the user 2026-07-07).
        out = run_scenario("compaction_atom")
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0].get("summary"), "summary of the conversation so far")

    def test_long_compaction_summary_is_capped(self):
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do task X", "u1", ps="typed"),
                    aline(T0 + 30, "did X", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    {"type": "user", "timestamp": iso(T0 + 505), "uuid": "cs1", "parentUuid": "c1",
                     "isCompactSummary": True, "message": {"role": "user", "content": "x" * 9000}}]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(len(comp), 1)
        s = comp[0]["summary"]
        self.assertTrue(s.endswith("…(summary truncated)"), "an over-cap summary is truncated with a marker")
        self.assertLessEqual(len(s), em.SUMMARY_CAP + 40, "capped near SUMMARY_CAP")

    def test_post_compaction_replay_is_deduped(self):
        # the user 2026-06-22: compaction RESTORES the recent message tail verbatim (new uuids + timestamps).
        # Those replayed user prompts are NOT new work — without dedup the judges re-process them and re-mint
        # already-done (even CLEARED) goals with fresh ids that escape the clear. A post-compaction user
        # message whose text matches an earlier one is dropped; genuinely-new post-compaction work is kept.
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do task X", "u1", ps="typed"),
                    aline(T0 + 30, "did X", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    compact_summary_line(T0 + 505, "cs1", parent="c1"),
                    uline(T0 + 520, "do task X", "u2", "cs1", ps="sdk"),     # REPLAY of u1 (restored tail)
                    aline(T0 + 530, "redid X", "a2", "u2", stop="end_turn"),
                    uline(T0 + 600, "do task Z", "u3", "a2", ps="typed"),    # GENUINE new work
                    aline(T0 + 610, "did Z", "a3", "u3", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        utexts = [em._text_of(em._content(a.get("message"))) for t in out["turns"] for a in t["atoms"]
                  if a["type"] == "user"]
        self.assertEqual(utexts.count("do task X"), 1, "the replayed prompt is deduped — only the original remains")
        self.assertEqual(utexts.count("do task Z"), 1, "genuinely-new post-compaction work is kept")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("u2", uuids, "the replay atom (u2) is dropped, so the planner can't re-mint its goal")
        self.assertIn("u1", uuids, "the pre-compaction original survives")
        self.assertIn("u3", uuids, "genuine new work survives")

    def test_a_repeat_of_an_old_message_is_not_a_replay(self):
        """THE BUG (the user 2026-08-01): a message they sent, answered by the session, absent from the chat.

        The replay guard keyed on TEXT ALONE, so once a session had compacted, the second time anyone said
        a thing they had said before — "Now?", "retry", "[Request interrupted by user]", a romp notice —
        it was read as restored context and dropped, however many days apart. The reply still rendered,
        which is what made it look like the chat had lost a message rather than dropped one on purpose.
        A repeat AFTER work resumed is a message the person actually sent."""
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "Now?", "u1", ps="typed"),
                    aline(T0 + 30, "not yet", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    compact_summary_line(T0 + 505, "cs1", parent="c1"),
                    aline(T0 + 600, "carrying on", "a2", "cs1", stop="end_turn"),
                    uline(T0 + 90000, "Now?", "u2", "a2", ps="sdk"),        # the SAME word, a day later
                    aline(T0 + 90030, "333/358 done", "a3", "u2", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u2", uuids, "the repeat is a real message and must render")
        self.assertIn("u1", uuids, "…and so does the original")
        self.assertIn("a3", uuids, "the reply was never in doubt — which is what made the loss confusing")

    def test_a_verbatim_rewrite_at_the_same_second_is_still_deduped(self):
        # the other measured replay shape: the SAME record written twice (resume/compaction re-splice),
        # identical text at an identical timestamp — deduped wherever it lands, restore burst or not.
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do task X", "u1", ps="typed"),
                    aline(T0 + 30, "did X", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    compact_summary_line(T0 + 505, "cs1", parent="c1"),
                    aline(T0 + 600, "carrying on", "a2", "cs1", stop="end_turn"),
                    uline(T0, "do task X", "u2", "a2", ps="sdk"),          # same text AND same second as u1
                    aline(T0 + 700, "again", "a3", "u2", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("u2", uuids, "a verbatim re-write is the same record, not a second message")
        self.assertIn("u1", uuids)

    def test_pre_compaction_turn_survives_via_logical_parent(self):
        out = run_scenario("compaction_atom")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u1", uuids, "the stitch (logicalParentUuid) keeps pre-compaction history on path")
        self.assertIn("a1", uuids)

    def test_broken_stitch_repaired_via_preserved_segment(self):
        """When logicalParentUuid dangles, preservedSegment.tailUuid repairs the stitch so
        pre-compaction history is retained instead of orphaned."""
        out = run_scenario("compaction_broken_stitch")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u1", uuids, "pre-compaction history must survive a dangling logicalParentUuid")
        self.assertIn("a1", uuids)
        self.assertIn("u2", uuids)
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(comp[0]["parentUuid"], "a1", "the compaction atom's parent is the repaired stitch")


class DetachedManualCompaction(unittest.TestCase):
    """A LIVE manual /compact writes its boundary + summary as a DETACHED side branch — the
    boundary has parentUuid:null + logicalParentUuid:<pre-compact leaf>, the summary is its only
    child, and the conversation chains through the /compact command wrappers instead, so the
    leaf->root walk never visits the pair and the compact atom (the chat's "Context compacted"
    card) was silently never emitted (the user 2026-08-19; 10/13 manual boundaries in the live
    corpus). The adoption repair (FileAdapter._adopt_detached_compactions) splices the pair in
    at its anchor — keyed on the SHAPE (boundary off the active path, its anchor on it), never
    on trigger=manual: an attached manual must no-op, a detached auto must be adopted."""

    def _flat(self, out):
        return [a for t in out["turns"] for a in t["atoms"]]

    def _cards(self, out):
        return [a for a in self._flat(out) if a.get("subtype") == "compact_boundary"]

    def test_detached_manual_boundary_is_adopted_and_emits_the_card_atom(self):
        out = run_scenario("manual_compact_detached")
        comp = self._cards(out)
        self.assertEqual(len(comp), 1, "the detached boundary is adopted — the card exists")
        self.assertEqual(comp[0]["uuid"], "cb1")
        self.assertEqual(comp[0]["compact_metadata"]["trigger"], "manual")
        self.assertEqual(comp[0].get("summary"), "summary of the conversation so far",
                         "the side-branch summary rides the adopted atom, same as an attached one")
        self.assertEqual(comp[0]["parentUuid"], "so1",
                         "the adopted atom chains from its episode's stdout — the /compact exchange "
                         "stays intact on the path, with the pre-compact leaf reachable through it")

    def test_adopted_card_sits_where_the_compact_completed(self):
        out = run_scenario("manual_compact_detached")
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertLess(uuids.index("cw1"), uuids.index("cb1"),
                        "the card follows the /compact invocation")
        self.assertLess(uuids.index("so1"), uuids.index("cb1"),
                        "…AFTER the whole command exchange: splicing before the stdout pulled that "
                        "atom into the boundary's fresh turn as assistant work, minting a phantom "
                        "WORK unit per manual compact (2026-08-19 review)")
        self.assertLess(uuids.index("cb1"), uuids.index("u2"),
                        "…and precedes the next prompt — the moment the compaction happened")

    def test_adopted_boundary_opens_a_fresh_turn(self):
        out = run_scenario("manual_compact_detached")
        bturn = next(t for t in out["turns"] if any(a.get("uuid") == "cb1" for a in t["atoms"]))
        self.assertEqual(bturn["atoms"][0].get("uuid"), "cb1",
                         "the boundary anchors its own turn, exactly as an attached one does")
        self.assertIsNone(bturn["trigger"])
        u2turn = next(t for t in out["turns"] if any(a.get("uuid") == "u2" for a in t["atoms"]))
        self.assertEqual(u2turn["trigger"], {"uuid": "u2"},
                         "the genuine post-compact prompt still opens its own turn")

    def test_adoption_splices_in_and_never_reroutes_around(self):
        # everything that was already kept stays kept: the pre-compact history, the /compact
        # command atom, its stdout output, the post-compact conversation
        out = run_scenario("manual_compact_detached")
        uuids = [a.get("uuid") for a in self._flat(out)]
        for u in ("u1", "a1", "cw1", "so1", "u2", "a2"):
            self.assertIn(u, uuids)
        self.assertNotIn("cs1", uuids, "the summary is captured, never its own atom")

    def test_attached_auto_boundary_is_not_double_emitted(self):
        # the no-op leg, keyed on shape: an ATTACHED boundary (an auto-compaction's normal
        # shape — the continuation chains through boundary+summary) emits exactly once
        recs = [uline(T0, "do the long task", "u1", ps="typed"),
                aline(T0 + 30, "on it", "a1", "u1", stop="end_turn"),
                compact_line(T0 + 500, "cb1", logical_parent="a1", trigger="auto"),
                compact_summary_line(T0 + 505, "cs1", parent="cb1"),
                uline(T0 + 520, "carry on please", "u2", "cs1", ps="sdk"),
                aline(T0 + 530, "done", "a2", "u2", stop="end_turn")]
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual(len(comp), 1, "an attached boundary emits exactly once — the repair stands down")

    def test_attached_manual_resume_resplice_is_not_double_emitted(self):
        # the corpus's other manual shape: a RESUME rebuild re-splices the boundary into the
        # chain, arriving attached (the compaction_atom scenario IS that shape). The repair
        # must not fight the resume shape — same no-op leg, still exactly one atom.
        out = run_scenario("compaction_atom")
        self.assertEqual(len(self._cards(out)), 1)

    def test_two_sequential_manual_compacts_each_get_their_card_in_order(self):
        side1, so1 = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1",
                                          summary_text="first compact summary")
        side2, so2 = manual_compact_lines(T0 + 290, T0 + 300, "2", parent="a2",
                                          summary_text="second compact summary")
        recs = ([uline(T0, "kick off phase one", "u1", ps="typed"),
                 aline(T0 + 30, "phase one done", "a1", "u1", stop="end_turn")]
                + side1
                + [uline(T0 + 150, "kick off phase two", "u2", so1, ps="typed"),
                   aline(T0 + 180, "phase two done", "a2", "u2", stop="end_turn")]
                + side2
                + [uline(T0 + 350, "kick off phase three", "u3", so2, ps="typed"),
                   aline(T0 + 380, "phase three done", "a3", "u3", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([a["uuid"] for a in comp], ["cb1", "cb2"],
                         "each compaction adopts at its own anchor — two cards, in order")
        self.assertEqual([a.get("summary") for a in comp],
                         ["first compact summary", "second compact summary"])
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertLess(uuids.index("cb1"), uuids.index("u2"))
        self.assertLess(uuids.index("u2"), uuids.index("cb2"))
        self.assertLess(uuids.index("cb2"), uuids.index("u3"))
        # BOTH stdouts render, identical text and all: an adopted boundary never arms the
        # restore-burst dedup (a live manual compact replays no tail), so nothing after it is
        # "restored context". The first cut pinned so2 as eaten — that pin was the bug's own
        # signature, not a goal (2026-08-19 review).
        self.assertIn("so1", uuids)
        self.assertIn("so2", uuids)

    def test_boundary_whose_anchor_was_rewound_away_stays_hidden(self):
        # a detached boundary whose OWN anchor is off the active path — its pre-compact context
        # was rewound away, so the compaction is not part of visible history. No card. Pinned.
        side, _so = manual_compact_lines(T0 + 190, T0 + 200, "1", parent="ax")
        recs = ([uline(T0, "main line ask", "u1", ps="typed"),
                 aline(T0 + 30, "main reply", "a1", "u1", stop="end_turn"),
                 uline(T0 + 100, "abandoned tangent", "ux", "a1", ps="typed"),
                 aline(T0 + 130, "tangent reply", "ax", "ux", stop="end_turn")]
                + side
                + [uline(T0 + 300, "back on the main line", "u2", "a1", ps="typed"),
                   aline(T0 + 330, "continuing main", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        self.assertEqual(self._cards(out), [],
                         "a compaction whose context was rewound away stays hidden")
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertNotIn("ux", uuids, "…and the rewound branch stays dropped")

    def test_repeated_typed_prompt_after_manual_compact_renders(self):
        # The fatal shape (2026-08-19 review): the user's GENUINE next prompt after a live
        # manual compact repeats an earlier message's text ("continue", "retry", a nudge).
        # Arming the restore-burst dedup on the adopted boundary read it as replayed context
        # and silently dropped a real ask — the one loss class this file exists to prevent.
        side, so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
        recs = ([uline(T0, "run the full test suite", "u1", ps="typed"),
                 aline(T0 + 30, "all green", "a1", "u1", stop="end_turn")]
                + side
                + [uline(T0 + 500, "run the full test suite", "u2", so, ps="typed"),
                   aline(T0 + 530, "running now", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertIn("u2", uuids, "the repeated-text prompt is the user's real ask, not a replay")
        self.assertEqual([c["uuid"] for c in self._cards(out)], ["cb1"])

    def test_rewound_manual_compact_is_not_resurrected(self):
        # The user rewinds PAST the /compact: the next prompt re-parents at the pre-compact
        # leaf, so the episode (wrappers + stdout) is off-path but the ANCHOR is still on it.
        # Gating on the bare anchor resurrected the undone compaction's card (2026-08-19
        # review); the episode gate keeps it hidden with the history it belonged to.
        side, _so = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1")
        recs = ([uline(T0, "kick off the refactor", "u1", ps="typed"),
                 aline(T0 + 30, "refactor staged", "a1", "u1", stop="end_turn")]
                + side
                + [uline(T0 + 200, "different direction instead", "u2", "a1", ps="typed"),
                   aline(T0 + 230, "sure", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertNotIn("cw1", uuids, "the rewound /compact exchange stays dropped")
        self.assertNotIn("so1", uuids)
        self.assertEqual(self._cards(out), [], "an undone compaction gets no card")

    def test_same_anchor_double_compact_only_the_live_one_renders(self):
        # Compact, rewind to the anchor, compact again: two detached boundaries share ONE
        # anchor. Splicing both at the anchor threaded them through each other — boundary #1's
        # parent became boundary #2's summary, and the rewound one rendered (2026-08-19
        # review). Each boundary belongs to its OWN episode; only the live episode is on-path.
        side1, _so1 = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1",
                                           summary_text="first summary")
        side2, so2 = manual_compact_lines(T0 + 290, T0 + 300, "2", parent="a1",
                                          summary_text="second summary")
        recs = ([uline(T0, "start it", "u1", ps="typed"),
                 aline(T0 + 30, "started", "a1", "u1", stop="end_turn")]
                + side1 + side2
                + [uline(T0 + 400, "go on", "u2", so2, ps="typed"),
                   aline(T0 + 430, "going", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb2"],
                         "only the live compaction renders; the rewound one stays hidden")
        self.assertEqual(comp[0]["parentUuid"], "so2",
                         "…chained from its OWN episode's stdout, never through the other pair")
        self.assertEqual(comp[0].get("summary"), "second summary")

    def test_summary_without_promptid_adopts_via_the_file_order_fallback(self):
        # An older write whose summary lacks the promptId link: the episode is still
        # identified — the nearest /compact invoked from the boundary's anchor and appended
        # after it (the CLI writes boundary+summary first, then the episode records).
        side, so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1",
                                        summary_pid=False)
        recs = ([uline(T0, "start the long build", "u1", ps="typed"),
                 aline(T0 + 30, "Working on it.", "a1", "u1", stop="end_turn")]
                + side
                + [uline(T0 + 500, "carry on with the build", "u2", so, ps="typed"),
                   aline(T0 + 530, "Continuing.", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1"])
        self.assertEqual(comp[0].get("summary"), "summary of the conversation so far")

    def test_self_anchored_boundary_is_skipped(self):
        # a corrupt boundary whose logicalParentUuid is its own uuid: no card, no crash —
        # and no 1-cycle handed to any walk (the parent link is dropped at load)
        recs = [uline(T0, "only ask", "u1", ps="typed"),
                aline(T0 + 30, "only answer", "a1", "u1", stop="end_turn"),
                compact_line(T0 + 50, "cbS", logical_parent="cbS", trigger="manual")]
        out = run_recs(recs)
        self.assertEqual(self._cards(out), [])

    def test_stdout_stays_in_the_command_turn_and_the_boundary_turn_holds_no_work(self):
        # Defect 2's event-model contract (2026-08-19 review): the /compact stdout is the
        # command exchange's output — it must never migrate into the boundary's fresh turn,
        # where it reads as assistant work and mints a judge-visible unit.
        out = run_scenario("manual_compact_detached")
        cmd_turn = next(t for t in out["turns"] if any(a.get("uuid") == "cw1" for a in t["atoms"]))
        self.assertIn("so1", [a.get("uuid") for a in cmd_turn["atoms"]])
        bturn = next(t for t in out["turns"] if any(a.get("uuid") == "cb1" for a in t["atoms"]))
        self.assertEqual([a.get("uuid") for a in bturn["atoms"]], ["cb1"],
                         "the boundary's turn holds the boundary alone")

    def test_crash_truncated_compact_never_steals_a_same_anchor_retrys_episode(self):
        # The mid-write window's worst case: boundary+summary landed, the CLI died before the
        # episode records, then the user compacted AGAIN from the same anchor. The stale
        # summary's promptId names nothing on record — the designed link failed, so the stale
        # boundary stays hidden. Degrading to the file-order fallback handed it the RETRY's
        # episode: the stale summary rendered at the live splice, and the already-claimed
        # guard then hid the real compact's card (2026-08-19 second review).
        stale = compact_summary_line(T0 + 100, "cs1", parent="cb1",
                                     text="stale interrupted summary")
        stale["promptId"] = "p1"
        side2, so2 = manual_compact_lines(T0 + 290, T0 + 300, "2", parent="a1",
                                          summary_text="second live summary")
        recs = ([uline(T0, "kick off the sweep", "u1", ps="typed"),
                 aline(T0 + 30, "sweep done", "a1", "u1", stop="end_turn"),
                 compact_line(T0 + 100, "cb1", logical_parent="a1", trigger="manual"),
                 stale]
                + side2
                + [uline(T0 + 400, "keep going", "u2", so2, ps="typed"),
                   aline(T0 + 430, "going", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb2"],
                         "only the retry renders — a promptId that names no on-record episode "
                         "keeps its boundary hidden, never falls to adjacency")
        self.assertEqual(comp[0]["parentUuid"], "so2")
        self.assertEqual(comp[0].get("summary"), "second live summary")

    def test_replayed_episode_copy_never_reseats_the_card(self):
        # A later auto compaction's restore burst can replay the manual /compact episode
        # records VERBATIM — new uuids, promptId preserved (the documented replay shape).
        # The card's splice is the ORIGINAL episode, the copy seq-nearest its own
        # boundary+summary pair: last-copy-wins re-seated the card after the auto card, on
        # a parent atom the replay dedup drops (2026-08-19 second review).
        side1, so1 = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1",
                                          summary_text="manual compact summary")
        auto_summary = compact_summary_line(T0 + 500, "csA", parent="cbA",
                                            text="auto compact summary")
        auto_summary["promptId"] = "pA"
        replay = [
            {"type": "user", "timestamp": iso(T0 + 501), "uuid": "rt1r", "parentUuid": "csA",
             "isMeta": True, "promptId": "p1",
             "message": {"role": "user", "content": "/compact"}},
            {"type": "user", "timestamp": iso(T0 + 501), "uuid": "cw1r", "parentUuid": "rt1r",
             "promptId": "p1",
             "message": {"role": "user", "content": "<command-name>/compact</command-name>\n"
                                                    "<command-message>compact</command-message>\n"
                                                    "<command-args></command-args>"}},
            {"type": "user", "timestamp": iso(T0 + 501), "uuid": "so1r", "parentUuid": "cw1r",
             "promptId": "p1",
             "message": {"role": "user", "content": "<local-command-stdout>Compacted "
                                                    "(ctrl+o to see full summary)"
                                                    "</local-command-stdout>"}},
            uline(T0 + 501, "step two of the migration", "u2r", "so1r", ps="typed"),
        ]
        recs = ([uline(T0, "start the migration", "u1", ps="typed"),
                 aline(T0 + 30, "migration started", "a1", "u1", stop="end_turn")]
                + side1
                + [uline(T0 + 200, "step two of the migration", "u2", so1, ps="typed"),
                   aline(T0 + 230, "step two done", "a2", "u2", stop="end_turn"),
                   compact_line(T0 + 500, "cbA", logical_parent="a2", trigger="auto"),
                   auto_summary]
                + replay
                + [aline(T0 + 540, "resuming after the auto compact", "a3", "u2r",
                         stop="end_turn"),
                   uline(T0 + 600, "now step three", "u3", "a3", ps="typed"),
                   aline(T0 + 630, "step three done", "a4", "u3", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1", "cbA"])
        self.assertEqual(comp[0]["parentUuid"], "so1",
                         "the card seats at the ORIGINAL episode's stdout, never a replayed copy")
        self.assertEqual(comp[0].get("summary"), "manual compact summary")
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertLess(uuids.index("cb1"), uuids.index("cbA"),
                        "…so it stays where the manual compact happened, before the auto card")
        self.assertLess(uuids.index("so1"), uuids.index("cb1"))
        self.assertLess(uuids.index("cb1"), uuids.index("u2"))

    def test_mid_write_wrapper_leaf_build_adopts_at_the_wrapper(self):
        # The mid-write phase the episode-scan comment describes: the wrapper is the file
        # leaf, the stdout not yet written. The pair is NOT hidden — it adopts at the
        # episode's last landed record for this one build (parent = the wrapper) and
        # re-seats at the stdout next parse (the full-shape tests above ARE that parse).
        side, _so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
        cut = side[:-1]           # boundary, summary, caveat twin, wrapper — no stdout yet
        recs = ([uline(T0, "start the long build", "u1", ps="typed"),
                 aline(T0 + 30, "build started", "a1", "u1", stop="end_turn")]
                + cut)
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1"])
        self.assertEqual(comp[0]["parentUuid"], "cw1",
                         "mid-write, the card seats at the episode's last landed record")
        # ADOPTED is also the dedup's off-switch (the emit loop keys on _adopted membership).
        # Nothing can follow the wrapper in this phase, so no atoms-level probe exists —
        # pin the switch itself on the adapter.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            adapter = em.FileAdapter([str(p)], p)
            self.assertIn("cb1", adapter._adopted,
                          "mid-write adoption keeps the restore dedup unarmed")

    def test_mid_write_summary_leaf_build_is_attached_by_shape(self):
        # One write earlier — boundary+summary are the whole tail. The pair IS the active
        # path: attached by shape, native emit, no adoption needed (and the dedup's armed
        # window is empty — the file ends there).
        side, _so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
        recs = ([uline(T0, "start the long build", "u1", ps="typed"),
                 aline(T0 + 30, "build started", "a1", "u1", stop="end_turn")]
                + side[:2])       # boundary + summary only
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1"])
        self.assertEqual(comp[0]["parentUuid"], "a1",
                         "the leaf-anchored pair emits natively off its pre-compact anchor")


class Lineage(unittest.TestCase):
    def test_resume_keeps_pre_fork_history(self):
        out = run_scenario("resume_lineage")
        self.assertEqual(out["leafFsid"], FSID_B)
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u1", uuids, "resume links across files, pre-fork history kept")
        self.assertIn("u2", uuids)
        # provenance: each atom is tagged with the physical file it lives in
        fsid_of = {a["uuid"]: a.get("fsid") for t in out["turns"] for a in t["atoms"]}
        self.assertEqual(fsid_of["u1"], FSID_A)
        self.assertEqual(fsid_of["u2"], FSID_B)

    def test_clear_drops_pre_clear_history(self):
        out = run_scenario("clear_breaks_lineage")
        self.assertEqual(len(out["turns"]), 1, "only the post-clear turn survives")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("u1", uuids)
        self.assertIn("u2", uuids)

    def test_rewind_branch_is_dropped(self):
        out = run_scenario("rewind_off_path")
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("first attempt", texts)
        self.assertIn("second attempt instead", texts)
        self.assertNotIn("abandoned follow-up", texts, "a rewound branch is intentionally dropped")


class BrokenChainFloor(unittest.TestCase):
    """This repo's one fatal error is silently dropping a real ask. A dangling parent
    chain (corruption / partial write) is not a proven rewind or a /clear, so it is KEPT
    — even though it is off the leaf->root spine. (0 such cases in the live corpus; this
    is a safety net.)"""

    def test_dangling_parent_prompt_is_kept(self):
        out = run_scenario("broken_chain_kept")
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("orphaned but real ask", texts, "a real ask must never be silently dropped")
        self.assertIn("main line ask", texts)
        self.assertIn("second main ask", texts)


class SlashCommandTurn(unittest.TestCase):
    def test_command_turn_is_tracked_and_flagged(self):
        # the user 2026-06-29: a slash command is no longer dropped — its invocation is a `command`-flagged
        # user atom that OPENS a tracked turn (so it shows in the chat/timeline + counts as working), with the
        # model work absorbed into that turn. The `command` flag is what makes the planner skip it (no goal).
        out = run_scenario("slash_command_turn")
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual(turn["trigger"], {"uuid": "cmd1"}, "the command invocation opens (triggers) the turn")
        uuids = [a.get("uuid") for a in turn["atoms"]]
        self.assertEqual(uuids, ["cmd1", "a1"], "the invocation is an atom; the work absorbs into its turn")
        cmd = turn["atoms"][0]
        self.assertEqual(cmd.get("command"), "/code-review", "the invocation atom carries the command flag (the name)")
        self.assertEqual(_text(cmd), "/code-review", "its display text is the slash command itself")
        self.assertTrue(turn["ended"], "the turn ends (the model work stopped end_turn) — not stuck working")

    def test_local_command_output_becomes_a_synthetic_assistant_reply(self):
        # a LOCAL command (e.g. /usage) writes <command-name> then <local-command-stdout> with the output and
        # NO model turn. The invocation → command user atom; the stdout → a synthetic assistant reply, so the
        # turn has content + ends naturally and the working signal lifts when the output lands.
        recs = [
            {"type": "user", "timestamp": iso(T0), "uuid": "c1", "parentUuid": None,
             "message": {"role": "user", "content": "<command-name>/usage</command-name>"}},
            {"type": "user", "timestamp": iso(T0 + 1), "uuid": "o1", "parentUuid": "c1",
             "message": {"role": "user", "content": "<local-command-stdout>You have 42 credits left.</local-command-stdout>"}},
        ]
        out = run_recs(recs)
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual([a.get("uuid") for a in turn["atoms"]], ["c1", "o1"])
        self.assertEqual(turn["atoms"][0].get("command"), "/usage")
        self.assertEqual(turn["atoms"][1]["type"], "assistant", "the stdout becomes the turn's reply")
        self.assertTrue(turn["atoms"][1].get("command"), "the output atom is flagged as command output")
        self.assertIn("42 credits", _text(turn["atoms"][1]))
        self.assertTrue(turn["ended"], "the turn ends once the output lands")

    def test_bare_command_with_no_output_still_ends(self):
        # the user 2026-06-29 (the JLD /usage case): a command that produced NO output (no stdout, no model
        # work) must NOT leave the turn open forever — that read the session as "working" for hours and left a
        # stuck card. The _finalize_turn backstop ends a bare command turn so the session settles to idle.
        recs = [{"type": "user", "timestamp": iso(T0), "uuid": "c1", "parentUuid": None,
                 "message": {"role": "user", "content": "<command-name>/usage</command-name>"}}]
        out = run_recs(recs)
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual([a.get("uuid") for a in turn["atoms"]], ["c1"])
        self.assertEqual(turn["atoms"][0].get("command"), "/usage")
        self.assertTrue(turn["ended"], "a bare command turn self-ends — never traps the session in 'working'")

    def test_a_skill_invocation_with_message_first_is_still_a_command_atom(self):
        # The CLI does NOT fix the wrapper ORDER: a built-in writes <command-name> first, but a SKILL / custom
        # command writes <command-message> first. Anchored-only matching missed the latter entirely — the
        # record fell through to the harness-noise skip, the invocation never became an atom, and the work it
        # triggered was absorbed into the PRECEDING segment. That is why a JLD session (`/jld <request>`) ran
        # with its ask buried in the previous "/model" command turn and no card of its own (the user
        # 2026-07-22).
        recs = [
            {"type": "user", "timestamp": iso(T0), "uuid": "c1", "parentUuid": None,
             "message": {"role": "user",
                         "content": "<command-message>jld</command-message>\n"
                                    "<command-name>/jld</command-name>\n"
                                    "<command-args>design a curriculum</command-args>"}},
            {"type": "assistant", "timestamp": iso(T0 + 1), "uuid": "a1", "parentUuid": "c1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}],
                         "stop_reason": "end_turn"}},
        ]
        out = run_recs(recs)
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual(turn["trigger"], {"uuid": "c1"}, "the skill invocation OPENS its own turn")
        self.assertEqual(turn["atoms"][0].get("command"), "/jld", "recognized despite the message-first order")
        self.assertEqual(_text(turn["atoms"][0]), "/jld design a curriculum",
                         "its display text carries the args, so the ask is visible")

    def test_prose_quoting_the_command_tag_is_not_an_invocation(self):
        # the ordering fix searches for <command-name> ANYWHERE, so it is guarded by CMD_WRAP_RE: the record
        # must already BEGIN with a command wrapper. A real message that merely quotes the tag stays human.
        recs = [{"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
                 "message": {"role": "user",
                             "content": "the transcript shows <command-name>/usage</command-name> mid-line"}}]
        out = run_recs(recs)
        turn = out["turns"][0]
        self.assertIsNone(turn["atoms"][0].get("command"),
                          "prose quoting the tag is a real message, never an invocation")


def _text(atom):
    msg = atom.get("message") or {}
    return " ".join(b.get("text", "") for b in msg.get("content", [])
                    if isinstance(b, dict) and b.get("type") == "text").strip()


class ApiErrorAtom(unittest.TestCase):
    """Claude Code writes a failed turn as an assistant record with top-level isApiErrorMessage:true
    and a text block. em must TAG that atom isApiError so deep-link anchoring (_seg_anchors) can skip
    it — the error carries text but is a FAILURE, not a reply. (the user 2026-06-18.)"""

    def _atoms(self, records):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            out = em.parse_session(str(path), rompuuid=SID, dir="/TESTDIR",
                                   candidate_files=[str(path)], now=NOW)
        return [a for t in out["turns"] for a in t["atoms"]]

    def test_api_error_assistant_atom_is_tagged(self):
        err = aline(T0 + 20, "API Error: 500 server_error", "a1", "u1", stop="stop_sequence")
        err["isApiErrorMessage"] = True
        a1 = next(a for a in self._atoms([uline(T0, "do it", "u1"), err]) if a.get("uuid") == "a1")
        self.assertIs(a1.get("isApiError"), True, "API-error assistant atom must be tagged isApiError")

    def test_normal_assistant_atom_is_not_tagged(self):
        a1 = next(a for a in self._atoms([uline(T0, "do it", "u1"), aline(T0 + 20, "done", "a1", "u1")])
                  if a.get("uuid") == "a1")
        self.assertNotIn("isApiError", a1, "a real reply is never tagged isApiError")


class Idle(unittest.TestCase):
    def test_idle_atom_from_state_log(self):
        out = run_scenario("idle_atom")
        idles = [a for t in out["turns"] for a in t["atoms"] if a["type"] == "idle"]
        self.assertEqual(len(idles), 1)
        self.assertEqual((idles[0]["t"], idles[0]["end"]), (T0 + 60, T0 + 3600))

    def test_idle_folds_into_preceding_turn(self):
        out = run_scenario("idle_atom")
        self.assertTrue(any(a["type"] == "idle" for a in out["turns"][0]["atoms"]))
        self.assertFalse(any(a["type"] == "idle" for a in out["turns"][1]["atoms"]))

    def test_no_idle_atom_without_a_state_transition(self):
        # same one-hour assistant gap, but NO idle state row -> NO idle atom (not a heuristic)
        out = run_single_no_states("idle_atom")
        idles = [a for t in out["turns"] for a in t["atoms"] if a["type"] == "idle"]
        self.assertEqual(idles, [])


def run_single_no_states(name):
    records, sent = SINGLE_FILE[name]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / (SID + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        return em.parse_session(str(path), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(path)],
                                states=None, postal_log=sent or [], now=NOW)


class PopAll(unittest.TestCase):
    def test_popall_produces_one_absorbed_atom_per_queued_item(self):
        out = run_scenario("popall")
        absorbed = [a.get("uuid") for a in out["turns"][0]["atoms"]
                    if a.get("uuid") in ("att1", "att2")]
        self.assertEqual(absorbed, ["att1", "att2"])


class SafeDefault(unittest.TestCase):
    """parse_session must NOT glob the project dir by default (a footgun: it would read
    every unrelated transcript in the dir). The default candidate set is just [leaf];
    cross-file resume requires the caller to pass the explicit session file set."""

    def _two_files(self, td):
        # `other` is a resume PARENT of `leaf` (leaf's first prompt parents into other's x2)
        other = Path(td) / "cccccccc-0000-0000-0000-000000000000.jsonl"
        other.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, "sibling parent ask", "x1", ps="typed"),
            aline(T0 + 20, "sibling reply", "x2", "x1", stop="end_turn")]) + "\n")
        leaf = Path(td) / (SID + ".jsonl")
        leaf.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 + 100, "leaf ask resuming sibling", "u1", parent="x2", ps="typed"),
            aline(T0 + 120, "leaf reply", "a1", "u1", stop="end_turn")]) + "\n")
        return leaf, other

    def test_default_does_not_read_sibling_files(self):
        with tempfile.TemporaryDirectory() as td:
            leaf, other = self._two_files(td)
            out = em.parse_session(str(leaf), rompuuid=SID, dir="/TESTDIR", now=NOW)  # NO candidate_files
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("leaf ask resuming sibling", texts)
        self.assertNotIn("sibling parent ask", texts, "default must not glob/read sibling transcripts")

    def test_explicit_file_set_enables_cross_file_resume(self):
        with tempfile.TemporaryDirectory() as td:
            leaf, other = self._two_files(td)
            out = em.parse_session(str(leaf), rompuuid=SID, dir="/TESTDIR",
                                   candidate_files=[str(leaf), str(other)], now=NOW)
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("sibling parent ask", texts, "explicit candidate_files enables cross-file resume")


class WaitingStopClosesTheTurn(unittest.TestCase):
    """The tmux Stop hook writes state:"waiting" when the agent hands the floor back; it must terminate the
    turn the SAME as the later idle-prompt's state:"idle". Keying only on "idle" left a finished session
    whose last assistant message wasn't a clean end_turn (e.g. it ended on a tool_use) stuck reading
    "working" from Stop until the idle-prompt eventually landed (the user 2026-06-25, who asked to revert working)."""
    ATOMS = [{"t": 100, "session_id": "s"}, {"t": 200, "end": 200, "session_id": "s"}]

    def test_a_waiting_state_synthesizes_an_idle_atom_like_idle(self):
        out = em.synthesize_idle([{"t": 210, "state": "waiting"}], self.ATOMS, now=300)
        self.assertEqual([(a["type"], a["t"], a["end"]) for a in out], [("idle", 210, 300)])
        # ...exactly as a real idle-prompt "idle" record does
        self.assertEqual(em.synthesize_idle([{"t": 210, "state": "idle"}], self.ATOMS, now=300)[0]["type"], "idle")

    def test_working_never_synthesizes_an_idle_atom(self):
        self.assertEqual(em.synthesize_idle([{"t": 210, "state": "working"}], self.ATOMS, now=300), [])
        self.assertEqual(em._IDLE_STATES, ("idle", "waiting"))


def regen():
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name in ALL_SCENARIOS:
        out = run_scenario(name)
        p = GOLDEN / (name + ".json")
        p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
        print("wrote %s  (%d turns)" % (p, len(out["turns"])))


if __name__ == "__main__":
    if "--regen" in sys.argv:
        regen()
    else:
        unittest.main(verbosity=2)
