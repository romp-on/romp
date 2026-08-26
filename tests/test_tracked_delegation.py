#!/usr/bin/env python3
"""TRACKED delegation (the user 2026-08-24): a report-back variant of delegate mail whose ONE
primary card lives under the DELEGATOR. The flag rides the postal sent row (wire metadata, never
message prose); the courier marks the sender's tracking node primary (handoff.tracked) and the
recipient's planted goal its satellite (origin.tracked); build_feed ships delegTracked identities on
the primary card and a satellite mark on the recipient's — both keys only when present, so every
untracked payload stays byte-identical. Untracked mail is today's behavior exactly. A demoted
tracked delegate plants neither side (no orphan primary); a non-local sender degrades to a plain
delegate (a satellite without a reachable primary would hide work). run_propagate and the clear's
msgId link cross the pair unchanged. Synthetic notes-api world only (web/api, placeholder UUIDs,
TESTHOST)."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_POSTAL_HOST"] = "TESTHOST"
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([{"id": "sess-web", "name": "web", "dir": "/tmp/notes-api",
                                    "state": "waiting", "working": ""}]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = SourceFileLoader("romp_postal_tracked", os.path.join(BIN, "romp-postal-service")).load_module()
jd = SourceFileLoader("romp_judge_tracked", os.path.join(BIN, "romp-judge")).load_module()

RECIP = "11111111-2222-3333-4444-555555555555"   # the worker (web)
SENDER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # the delegator (api)
MID = "1781100000.11111_22222.TESTHOST"
T0 = 1781100000
DELEGATING = '{"verdict": "delegating", "goal": 0, "text": "ship the exporter"}'
COORDINATING = '{"verdict": "coordinating", "goal": 0, "text": ""}'

KSRC = open(os.path.join(BIN, "romp-kernel")).read()
PSRC = open(os.path.join(BIN, "romp-postal-service")).read()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class PostalTrackedWire(unittest.TestCase):
    """The flag's one durable record is the sent row — additive, delegate-only, prose-free."""

    def test_deliver_row_carries_tracked(self):
        ps.deliver("sess-web", "api", "sess-api", "own the exporter work", kind="delegate", tracked=True)
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        row = rows[-1]
        self.assertIs(row.get("tracked"), True, "the sent row is the flag's authoritative record")
        self.assertEqual(row.get("kind"), "delegate")

    def test_untracked_row_has_no_key_at_all(self):
        ps.deliver("sess-web", "api", "sess-api", "plain handoff", kind="delegate")
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        self.assertNotIn("tracked", rows[-1], "absent flag = today's row byte-for-byte")

    def test_no_prose_ever_carries_the_flag(self):
        # the injected-voice rule: tracked is wire metadata — the recipient's inbox render says
        # NOTHING about it (no marker, no banner word), and no header is written either
        msgs = [{"id": MID, "from": "api", "from_id": "sess-api", "body": "own the exporter work",
                 "kind": "delegate", "date": ""}]
        txt = ps.format_inbox(msgs, me_id="sess-web")
        self.assertNotIn("tracked", txt.lower())

    def test_route_gates_the_flag_to_delegates_and_the_relay_drops_it(self):
        # source pins on the /send route: coordinate/question can never carry it, and the
        # cross-host relay branch deliberately drops it (a satellite with an unreachable primary
        # would hide work)
        self.assertIn('tracked = bool(data.get("tracked")) and kind == "delegate"', PSRC)
        self.assertIn("`tracked` deliberately does NOT ride the relay", PSRC)
        self.assertIn('mid = deliver(a0["id"], frm, frm_id, body, kind=kind, tracked=tracked)', PSRC)

    def test_mcp_and_cli_expose_the_flag(self):
        self.assertIn('"tracked": {"type": "boolean"', PSRC, "the send_message schema offers it")
        self.assertIn('"--tracked"', PSRC.replace("'--tracked'", '"--tracked"'), "the CLI flag parses")
        self.assertIn("--tracked is for delegations only", PSRC, "…and refuses non-delegate kinds loudly")


class CourierTracked(unittest.TestCase):
    """The courier marks both sides from the row — or neither, when demoted or non-local."""

    def setUp(self):
        # chain-rooted minting (2026-08-25) gates recipient tops on a user-rooted sender chain —
        # ORTHOGONAL to this file's subject, so the gate is held open here; its own truth table
        # lives in tests/test_chain_rooted_minting.py
        self._rooted_saved = jd._delegate_user_rooted
        jd._delegate_user_rooted = lambda *a, **k: True
        self.addCleanup(lambda: setattr(jd, "_delegate_user_rooted", self._rooted_saved))
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        proj = td / "projects"
        names = td / "names"; names.mkdir()
        self.dirs = {}
        for sid, nm in ((RECIP, "web"), (SENDER, "api")):
            cdir = td / ("launch-" + nm); cdir.mkdir()
            munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
            (proj / munged).mkdir(parents=True)
            self.dirs[sid] = proj / munged
            (names / sid).write_text("%s\t%s\t#abcdef\n" % (nm, str(cdir)))
        tl = td / "timeline"; tl.mkdir()
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.MESSAGES, jd.ERRORS, jd.courier_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.MESSAGES = tl / "messages.jsonl"
        jd.ERRORS = td / "judge-errors.jsonl"
        jd.courier_llm = lambda *a, **k: self.reply
        self.reply = DELEGATING
        jd._PARSE_CACHE.clear()
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd._postal_from_memo["key"] = None

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.MESSAGES, jd.ERRORS, jd.courier_llm) = self.saved
        jd._postal_from_memo["key"] = None
        self.td.cleanup()

    def _run(self, tracked=True, sender_id=SENDER, both_transcripts=True):
        row = {"t": T0 - 5, "ev": "sent", "id": MID, "from": "api", "from_id": sender_id,
               "to_id": RECIP, "body": "own the exporter work", "kind": "delegate"}
        if tracked:
            row["tracked"] = True
        jd.MESSAGES.write_text(json.dumps(row) + "\n")
        recs = [uline(T0, "own the exporter work\n<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID, "u1"),
                aline(T0 + 30, "Starting on it.", "a1", "u1")]
        (self.dirs[RECIP] / (RECIP + ".jsonl")).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        if both_transcripts:
            # the sender must be a DISCOVERED local session for tracked to qualify
            (self.dirs[SENDER] / (SENDER + ".jsonl")).write_text(
                json.dumps(uline(T0 - 60, "kick off the exporter", "s1")) + "\n")
        jd._PARSE_CACHE.clear()
        jd._discover_cache["fp"] = None
        jd._postal_from_memo["key"] = None
        jd.run_courier(now=T0 + 100)
        rstore = jd.load_goals(RECIP)
        sstore = jd.load_goals(sender_id)
        planted = [nd for nd in rstore["nodes"].values() if isinstance(nd.get("origin"), dict)]
        handoffs = [nd for nd in sstore["nodes"].values() if isinstance(nd.get("handoff"), dict)]
        return rstore, planted, handoffs

    def test_tracked_delegate_marks_primary_and_satellite(self):
        _, planted, handoffs = self._run(tracked=True)
        self.assertEqual(len(planted), 1)
        self.assertEqual(len(handoffs), 1)
        self.assertIs(handoffs[0]["handoff"].get("tracked"), True, "the sender's node is the PRIMARY")
        self.assertIs(planted[0]["origin"].get("tracked"), True, "the recipient's goal is its SATELLITE")
        self.assertEqual(planted[0]["origin"]["goalId"], handoffs[0]["id"],
                         "the pair link is the same msgId graph run_propagate and clears already walk")

    def test_untracked_delegate_is_todays_shape_exactly(self):
        _, planted, handoffs = self._run(tracked=False)
        self.assertEqual((len(planted), len(handoffs)), (1, 1))
        self.assertNotIn("tracked", handoffs[0]["handoff"], "no flag key at all — byte-compatible")
        self.assertNotIn("tracked", planted[0]["origin"])

    def test_a_demoted_tracked_delegate_leaves_no_orphan_primary(self):
        # the courier may demote a declared delegate whose body hands nothing over; a tracked one
        # must then plant NOTHING on either side — a primary with no satellite (or vice versa)
        # would be a card about work that does not exist
        self.reply = COORDINATING
        rstore, planted, handoffs = self._run(tracked=True)
        self.assertEqual(planted, [], "demoted → no satellite")
        self.assertEqual(handoffs, [], "…and no primary either: no orphan")
        seg_id = next(k for k in rstore["placements"] if not k.endswith(("#p", "#d", "#live")))
        self.assertEqual(rstore["placements"][seg_id], "fyi")

    def test_a_non_local_sender_degrades_to_a_plain_delegate(self):
        # the primary would live on a kernel this courier cannot write; a satellite without a
        # primary hides work, so the flag drops and the pair renders exactly like today
        ghost = "99999999-8888-7777-6666-555555555555"   # no names entry → not a local session
        _, planted, handoffs = self._run(tracked=True, sender_id=ghost, both_transcripts=False)
        self.assertEqual(len(planted), 1, "the delegation itself still plants")
        self.assertNotIn("tracked", planted[0]["origin"], "…but untracked: no satellite mark")
        for nd in handoffs:
            self.assertNotIn("tracked", nd.get("handoff") or {})

    def test_propagate_completes_the_primary_across_the_tracked_pair(self):
        rstore, planted, handoffs = self._run(tracked=True)
        gid = planted[0]["id"]
        # done lands the way every real writer lands it: the diary row first (the fold recomputes
        # the flags from it on every load), then the flag materialization
        jd.record_verdict(rstore, rstore["nodes"][gid], "closer", "done", T0 + 500, why="shipped the exporter")
        jd._mark_node_done(rstore, gid, "shipped the exporter", T0 + 500, src="closer")
        jd.save_goals(RECIP, rstore)
        jd.run_propagate(now=T0 + 600)
        sstore = jd.load_goals(SENDER)
        prim = sstore["nodes"][handoffs[0]["id"]]
        self.assertTrue(prim.get("nodeComplete"),
                        "the recipient's completion checks the tracked primary off — same event as ever")


class FeedPayloadPins(unittest.TestCase):
    """build_feed's card shape, pinned at source (the build_feed internals idiom): the primary
    carries delegTracked identities, the satellite carries its mark, and BOTH ship only when
    present so untracked payloads (and the goldens over them) stay byte-identical."""

    def test_the_primary_names_its_tracked_recipients(self):
        self.assertIn('if isinstance(nodes[x].get("handoff"), dict) and nodes[x]["handoff"].get("tracked")', KSRC)
        self.assertIn('**({"delegTracked": _tracked_peers} if _tracked_peers else {}),', KSRC)

    def test_the_satellite_hides_only_while_the_pair_is_intact_and_nothing_needs_you(self):
        # review 2026-08-24: a needs-you block always surfaces, and a closed/cleared primary
        # un-hides the copy — a pair divergence self-heals to a visible card, never work in secret
        self.assertIn('**({"satellite": True} if isinstance(o, dict) and o.get("tracked")\n'
                      '                   and origin and origin.get("live") and column != "needs_input" else {}),', KSRC)

    def test_completed_and_cleared_handoffs_drop_off_the_primary(self):
        self.assertIn('and not nodes[x].get("nodeComplete") and not nodes[x].get("cleared")]', KSRC)


if __name__ == "__main__":
    unittest.main()
