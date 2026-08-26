#!/usr/bin/env python3
"""Mint hygiene (the user 2026-08-25, from the dashboard provenance audit): romp's own bookkeeping is
never an ask, and mail that files nothing never becomes a card's root.

Two deterministic floors:
(1) A work-run whose segment was opened by romp's OWN line — a restart/resume/tasks-died notice
    (romp-authored) or the CLI's '[Request interrupted…]' stop artifact — never opens a fresh
    TOP-level card (_seg_bookkeeping + _strip_top_mints). Its work may still advance EXISTING goals:
    menu-targeted sub/done/block ops pass through, with same-reply refs remapped. The advisory
    housekeeping note asked the model for this; the audit found a third of one team's board rooted
    in exactly these records, so the floor is now mechanical. The clear wrap-up stays exempt: its
    one blocked card is the designed needs-you escape.
(2) A MINT's promptUuid never names a record that must file nothing (_mint_anchor_uuid): a
    coordinate/question peer mail (binding, no courier call — the audited specimen was a to-do
    mirror top rooted at a kind=coordinate mail) or a bookkeeping record. The substitute anchor is
    the segment's first assistant atom (still a landable deep-link); a delegate mail keeps its
    anchor (the courier plants from that record by design). The to-do mirror keeps MINTING either
    way — the agent's declared work stays one glance away — only the false root claim is refused.

SYNTHETIC fixtures only; private synthetic sid (goal-store fixture rule, 2026-08-24)."""
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
jd = SourceFileLoader("romp_judge_mint_hyg", os.path.join(BIN, "romp-judge")).load_module()
em = jd.em

NOW = 1_787_000_000
T0 = NOW - 3600
SID = "d15c4a11-7e00-4b22-9c33-000000000015"   # private synthetic sid — never the shared placeholder

NOTICE = ("<!-- romp-injected --><!-- romp-system -->[romp] The kernel restarted and cut this "
          "session's in-flight turn; pick the work back up where it stopped.")
CLEARWRAP = ("<!-- romp-injected --><!-- romp-clear-wrap -->I'm dropping the items above — stop, "
             "park anything unfinished, and raise at most one thing that genuinely needs me.")
INTERRUPT = "[Request interrupted by user]"
MAIL_BODY = "Heads-up: the notes-api demo world regenerated; nothing needed from you."
MID = "1787000000.000000_1.TESTHOST"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def mail(t, uuid, parent, kind, body=MAIL_BODY):
    text = "%s\n<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: %s -->" % (body, MID, kind)
    return uline(t, text, uuid, parent, ps="sdk")


def _atom(uuid, typ, text, author=None):
    a = {"type": typ, "uuid": uuid,
         "message": {"role": typ, "content": [{"type": "text", "text": text}]}}
    if author is not None:
        a["author"] = author
    return a


class StripTopMints(unittest.TestCase):
    """The op transform: mints drop, chained ops drop with them, surviving refs remap."""

    def test_lone_mint_drops(self):
        self.assertEqual(jd._strip_top_mints([{"do": "mint", "why": "w", "text": "junk"}]), [])

    def test_sub_chained_onto_a_mint_drops_with_it(self):
        ops = [{"do": "mint", "why": "w", "text": "junk"},
               {"do": "sub", "why": "w", "ref": 1, "text": "step"}]
        self.assertEqual(jd._strip_top_mints(ops), [])

    def test_menu_ops_pass_and_refs_remap(self):
        ops = [{"do": "mint", "why": "w", "text": "junk"},
               {"do": "sub", "why": "w", "under": 2, "text": "step"},
               {"do": "done", "why": "w", "ref": 2}]
        out = jd._strip_top_mints(ops)
        self.assertEqual([o["do"] for o in out], ["sub", "done"])
        self.assertEqual(out[0]["under"], 2, "menu-targeted subs are untouched")
        self.assertEqual(out[1]["ref"], 1, "the ref follows its node to the new created position")

    def test_verdict_on_a_dropped_mint_evaporates_and_menu_verdicts_stand(self):
        ops = [{"do": "mint", "why": "w", "text": "junk"},
               {"do": "done", "why": "w", "ref": 1},
               {"do": "block", "why": "w", "goal": 3}]
        self.assertEqual(jd._strip_top_mints(ops), [{"do": "block", "why": "w", "goal": 3}])

    def test_a_chain_of_mints_drops_whole(self):
        ops = [{"do": "mint", "why": "w", "text": "junk"},
               {"do": "mint", "why": "w", "text": "junk2"},
               {"do": "sub", "why": "w", "ref": 2, "text": "step"},
               {"do": "done", "why": "w", "ref": 3}]
        self.assertEqual(jd._strip_top_mints(ops), [])

    def test_mintless_replies_are_byte_identical(self):
        ops = [{"do": "sub", "why": "w", "under": 1, "text": "step"},
               {"do": "done", "why": "w", "goal": 2}]
        self.assertEqual(jd._strip_top_mints(ops), ops)


class BookkeepingRoots(unittest.TestCase):
    """_seg_bookkeeping: romp-authored triggers and interrupt artifacts, clear wrap-up exempt."""

    def _seg(self, trig_atom, extra=None):
        atoms = [trig_atom] + (extra or [_atom("a1", "assistant", "resumed and verified")])
        return {"trigger": trig_atom["uuid"], "atoms": atoms}

    def test_romp_notice_is_bookkeeping(self):
        self.assertTrue(jd._seg_bookkeeping(self._seg(_atom("u1", "user", NOTICE, author="romp"))))

    def test_interrupt_artifact_is_bookkeeping(self):
        self.assertTrue(jd._seg_bookkeeping(self._seg(_atom("u1", "user", INTERRUPT, author="human"))))

    def test_a_human_prompt_is_not(self):
        self.assertFalse(jd._seg_bookkeeping(self._seg(_atom("u1", "user", "fix the exporter", author="human"))))

    def test_peer_mail_is_not(self):
        trig = _atom("u1", "user", MAIL_BODY)
        trig["author"] = {"peer": None, "mid": MID, "kind": "coordinate"}
        self.assertFalse(jd._seg_bookkeeping(self._seg(trig)),
                         "mail is the courier's jurisdiction, not the bookkeeping gate's")

    def test_clear_wrap_is_exempt(self):
        self.assertFalse(jd._seg_bookkeeping(self._seg(_atom("u1", "user", CLEARWRAP, author="romp"))),
                         "the wrap-up's one blocked card is the designed needs-you escape")


class MintAnchorVets(unittest.TestCase):
    """_mint_anchor_uuid: files-nothing records never become a mint's root."""

    def _seg(self, trig_atom, atoms=None):
        return {"trigger": trig_atom["uuid"], "atoms": [trig_atom] + (atoms or [])}

    def test_human_trigger_keeps_its_anchor(self):
        seg = self._seg(_atom("u1", "user", "fix the exporter", author="human"),
                        [_atom("a1", "assistant", "on it")])
        self.assertEqual(jd._mint_anchor_uuid(seg), "u1")

    def test_coordinate_mail_substitutes_the_assistant_atom(self):
        trig = _atom("u1", "user", MAIL_BODY)
        trig["author"] = {"peer": None, "mid": MID, "kind": "coordinate"}
        seg = self._seg(trig, [_atom("a1", "assistant", "noted; tracking the verification")])
        self.assertEqual(jd._mint_anchor_uuid(seg), "a1",
                         "a coordinate mail files nothing — it never becomes a card's root")

    def test_question_mail_substitutes_too(self):
        trig = _atom("u1", "user", MAIL_BODY)
        trig["author"] = {"peer": None, "mid": MID, "kind": "question"}
        seg = self._seg(trig, [_atom("a1", "assistant", "answering")])
        self.assertEqual(jd._mint_anchor_uuid(seg), "a1")

    def test_delegate_mail_keeps_its_anchor(self):
        trig = _atom("u1", "user", MAIL_BODY)
        trig["author"] = {"peer": None, "mid": MID, "kind": "delegate"}
        seg = self._seg(trig, [_atom("a1", "assistant", "taking it")])
        self.assertEqual(jd._mint_anchor_uuid(seg), "u1",
                         "the courier plants from a delegate's record by design")

    def test_romp_notice_substitutes(self):
        seg = self._seg(_atom("u1", "user", NOTICE, author="romp"),
                        [_atom("a1", "assistant", "picked the work back up")])
        self.assertEqual(jd._mint_anchor_uuid(seg), "a1")

    def test_interrupt_artifact_substitutes(self):
        seg = self._seg(_atom("u1", "user", INTERRUPT, author="human"),
                        [_atom("a1", "assistant", "parked the run")])
        self.assertEqual(jd._mint_anchor_uuid(seg), "a1")

    def test_no_assistant_atom_means_no_anchor_over_a_false_one(self):
        seg = self._seg(_atom("u1", "user", INTERRUPT, author="human"))
        self.assertIsNone(jd._mint_anchor_uuid(seg),
                          "an anchorless mint beats a false confession")


class _PlanWorld(unittest.TestCase):
    """E2E harness (the test_judge_apierror pattern): a synthetic transcript on disk, planner
    LLMs faked per prompt text, one real _plan_session pass."""

    def _run(self, records, reply_for, task_plan=None):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store,
                     em.task_store_plan)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"

            def fake(text, *a, **k):
                return reply_for(text)
            jd.plan_llm = jd.opener_llm = fake
            jd._group_store = lambda *a, **k: None
            if task_plan is not None:
                em.task_store_plan = lambda fsid: task_plan
            try:
                jd._PARSE_CACHE.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store,
                 em.task_store_plan) = saved
            return store


MINT_JUNK = ('{"ops":[{"why":"post-restart sweep","do":"mint",'
             '"text":"Post-restart verification sweep"}]}')
MINT_REAL = '{"ops":[{"why":"asked to fix it","do":"mint","text":"Fix the exporter test"}]}'
SKIP = '{"ops":[{"why":"nothing to file","do":"skip"}]}'


class WorkRunBookkeepingGate(_PlanWorld):
    def test_a_restart_notice_never_mints_a_top(self):
        records = [
            uline(T0, NOTICE, "u1", ps="sdk"),
            aline(T0 + 10, "Resumed; re-verified the tree; all clean.", "a1", "u1"),
            uline(T0 + 100, "fix the exporter test", "u2", "a1"),
            aline(T0 + 110, "Fixed and green.", "a2", "u2"),
        ]

        def reply_for(text):
            return MINT_JUNK if "restarted" in text else MINT_REAL
        store = self._run(records, reply_for)
        texts = [nd.get("text") or "" for nd in store["nodes"].values()]
        self.assertTrue(any("exporter" in t for t in texts),
                        "the control: the same pass still mints from the human ask")
        self.assertFalse(any("Post-restart" in t for t in texts),
                         "romp's own notice is never an ask — the mint is refused mechanically")

    def test_notice_work_still_advances_existing_goals(self):
        records = [
            uline(T0, "fix the exporter test", "u1"),
            aline(T0 + 10, "Working on it.", "a1", "u1"),
            uline(T0 + 100, NOTICE, "u2", "a1", ps="sdk"),
            aline(T0 + 110, "Resumed; the fix landed and the suite is green.", "a2", "u2"),
        ]

        def reply_for(text):
            if "restarted" in text:
                return ('{"ops":[{"why":"sweep","do":"mint","text":"Post-restart verification sweep"},'
                        '{"why":"the fix landed and the suite is green","do":"done","goal":1}]}')
            return MINT_REAL
        store = self._run(records, reply_for)
        goal = next((nd for nd in store["nodes"].values() if "exporter" in (nd.get("text") or "")), None)
        self.assertIsNotNone(goal, "the human ask minted its goal")
        self.assertTrue(goal.get("nodeComplete"),
                        "the notice stretch's done op on a MENU goal survives the mint strip")
        self.assertFalse(any("Post-restart" in (nd.get("text") or "") for nd in store["nodes"].values()))


class MirrorAnchor(_PlanWorld):
    TODO = [{"key": "7", "text": "verify cross-run references", "status": "in_progress",
             "activeForm": "verifying"}]

    def _mirror_node(self, store):
        return next((nd for nd in store["nodes"].values()
                     if (nd.get("agentTask") or {}).get("key") == "7"), None)

    def test_mirror_never_roots_at_a_coordinate_mail(self):
        records = [
            uline(T0, "set up the demo world", "u1"),
            aline(T0 + 10, "Done.", "a1", "u1"),
            mail(T0 + 100, "u2", "a1", "coordinate"),
            aline(T0 + 110, "Noted — I'll verify the references as part of my open work.", "a2", "u2"),
        ]
        store = self._run(records, lambda text: SKIP, task_plan=self.TODO)
        nd = self._mirror_node(store)
        self.assertIsNotNone(nd, "the agent's declared to-do still mints — visibility holds")
        self.assertEqual(nd.get("promptUuid"), "a2",
                         "the anchor is the agent's own work, never the coordinate mail record")

    def test_mirror_keeps_a_delegate_mail_anchor(self):
        records = [
            uline(T0, "set up the demo world", "u1"),
            aline(T0 + 10, "Done.", "a1", "u1"),
            mail(T0 + 100, "u2", "a1", "delegate"),
            aline(T0 + 110, "Taking it.", "a2", "u2"),
        ]
        store = self._run(records, lambda text: SKIP, task_plan=self.TODO)
        nd = self._mirror_node(store)
        self.assertIsNotNone(nd)
        self.assertEqual(nd.get("promptUuid"), "u2",
                         "a delegate's record is a legitimate root — the courier plants from it too")

    def test_mirror_never_roots_at_a_romp_notice(self):
        records = [
            uline(T0, "set up the demo world", "u1"),
            aline(T0 + 10, "Done.", "a1", "u1"),
            uline(T0 + 100, NOTICE, "u2", "a1", ps="sdk"),
            aline(T0 + 110, "Resumed where it stopped.", "a2", "u2"),
        ]
        store = self._run(records, lambda text: SKIP, task_plan=self.TODO)
        nd = self._mirror_node(store)
        self.assertIsNotNone(nd)
        self.assertEqual(nd.get("promptUuid"), "a2",
                         "a restart notice never becomes the mirror card's root")


if __name__ == "__main__":
    unittest.main()
