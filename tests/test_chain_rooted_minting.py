#!/usr/bin/env python3
"""Chain-rooted minting (the user 2026-08-25 ~19:4x, replacing the same-day view-side split; the
verdict, paraphrased: team-internal cards must not be CREATED in the first place — a means of
seeing them is not the ask). A delegate whose SENDER's linked goal traces to a HUMAN prompt mints
the recipient top card exactly as before — it is the ask flowing down. An untraceable delegate
mints NO standalone recipient top: the courier files the segment fyi (the coordinate treatment),
the SENDER-side tracking node still plants (the delegation stays one glance away on the sender's
board), and the tracker completes on the recipient's REPLY — the report-back event, the exact rule
the cross-host arm has always used, since no recipient goal will ever carry the msgId. At MINT
time uncertainty files QUIET — the inverse of the retired split's display default — and the
needs-you backstop is what makes that safe: the hard-block floor and the placeholder synthesize a
board card from the live prompt with ZERO goal nodes (tests/test_kernel_blocked_no_goal.py is the
standing behavioral pin), so quietly-filed work that needs the human still interrupts.
SYNTHETIC fixtures only; private synthetic sids."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from inspect import getsource, signature
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_chainmint", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_chainmint", os.path.join(BIN, "romp-kernel")).load_module()

NOW = 1_787_100_000
T0 = NOW - 3600
MGR = "c18a0001-1111-4222-8333-000000000001"    # private synthetic sids — never the shared placeholder
WKR = "c18a0001-1111-4222-8333-000000000002"
GRAND = "c18a0001-1111-4222-8333-000000000003"
MID = "1787099000.000001_1.TESTHOST"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _node(nid, text, parent, t=T0, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return base


def _fake_session(atoms):
    return {"turns": [{"atoms": atoms}]}


class RecordRule(unittest.TestCase):
    """_session_user_prompt_record: only a human prompt (or the queued dictation an attachment
    wraps) counts — returned as the RECORD itself, text + sid (T105) — and everything else — mail,
    romp lines, interrupts, missing records — is None. Truthiness pins here; the record's content
    is tests/test_root_ask_anchor.py's."""

    def _probe(self, atom, uuid="u1"):
        saved = jd.parsed_session
        jd.parsed_session = lambda sid, files, now: _fake_session([atom] if atom else [])
        try:
            return jd._session_user_prompt_record(MGR, "/dev/null", uuid, NOW)
        finally:
            jd.parsed_session = saved

    def test_human_prompt_is_true(self):
        self.assertTrue(self._probe({"uuid": "u1", "type": "user", "author": "human",
                                     "message": {"role": "user", "content": "ship the demo"}}))

    def test_interrupt_artifact_is_false(self):
        self.assertFalse(self._probe({"uuid": "u1", "type": "user", "author": "human",
                                      "message": {"role": "user",
                                                  "content": "[Request interrupted by user]"}}))

    def test_mail_and_romp_authors_are_false(self):
        self.assertFalse(self._probe({"uuid": "u1", "type": "user",
                                      "author": {"peer": WKR, "mid": MID, "kind": "coordinate"},
                                      "message": {"role": "user", "content": "fyi"}}))
        self.assertFalse(self._probe({"uuid": "u1", "type": "user", "author": "romp",
                                      "message": {"role": "user", "content": "[romp] restarted"}}))

    def test_attachment_dictation_is_true_and_marked_attachments_false(self):
        self.assertTrue(self._probe({"uuid": "u1", "type": "attachment",
                                     "message": {"content": [{"type": "text",
                                                              "text": "queued human dictation"}]}}))
        self.assertFalse(self._probe({"uuid": "u1", "type": "attachment",
                                      "message": {"content": [{"type": "text",
                                                  "text": "x <!-- romp-msg-id: %s -->" % MID}]}}))

    def test_a_missing_record_is_false(self):
        self.assertFalse(self._probe({"uuid": "other", "type": "user", "author": "human",
                                      "message": {"role": "user", "content": "hi"}}, uuid="u1"))


class TraceRule(unittest.TestCase):
    """_delegate_user_rooted over synthetic worlds: truthy (the ROOT record, T105) ONLY on a
    chain that reaches a human prompt; every dead-end/machine/mail/cross-host/cycle shape is
    None — uncertainty QUIETS."""

    def setUp(self):
        self._saved = jd.parsed_session
        self.by_sid = {}
        jd.parsed_session = lambda sid, files, now: _fake_session(self.by_sid.get(sid, []))
        self.paths = {MGR: "/dev/null", WKR: "/dev/null", GRAND: "/dev/null"}

    def tearDown(self):
        jd.parsed_session = self._saved
        for sid in (MGR, WKR, GRAND):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass

    def _store(self, sid, nodes, archive=False):
        st = {"rompUuid": sid, "nodes": nodes, "placements": {}, "status": {}}
        (jd.save_goal_archive if archive else jd.save_goals)(sid, st)

    def _human(self, sid, uuid="hu"):
        self.by_sid[sid] = [{"uuid": uuid, "type": "user", "author": "human",
                             "message": {"role": "user", "content": "the user's ask"}}]

    def test_a_human_rooted_link_mints(self):
        self._store(MGR, {"g1": _node("g1", "Ship the demo", None, promptUuid="hu")})
        self._human(MGR)
        self.assertTrue(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW))

    def test_nearest_evidence_climbs_ancestors(self):
        self._store(MGR, {"top": _node("top", "Ship the demo", None, promptUuid="hu"),
                          "kid": _node("kid", "a step", "top")})
        self._human(MGR)
        self.assertTrue(jd._delegate_user_rooted(MGR, "kid", self.paths, NOW))

    def test_a_machine_rooted_link_quiets(self):
        self._store(MGR, {"g1": _node("g1", "internal errand", None, promptUuid="n1")})
        self.by_sid[MGR] = [{"uuid": "n1", "type": "user", "author": "romp",
                             "message": {"role": "user", "content": "[romp] restarted"}}]
        self.assertFalse(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW))

    def test_no_link_quiets(self):
        self.assertFalse(jd._delegate_user_rooted(MGR, None, self.paths, NOW))

    def test_a_missing_node_or_record_quiets(self):
        self._store(MGR, {"g1": _node("g1", "evidence-free", None)})
        self.assertFalse(jd._delegate_user_rooted(MGR, "gone", self.paths, NOW))
        self.assertFalse(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW),
                         "no promptUuid anywhere on the chain → quiet, never a guess")

    def test_an_origin_hop_reaches_the_grand_senders_human_root(self):
        self._store(MGR, {"g1": _node("g1", "mid-chain ask", None,
                                      origin={"peer": GRAND, "goalId": "t1", "msgId": "m0"})})
        self._store(GRAND, {"g9": _node("g9", "the original ask", None, promptUuid="hu"),
                            "t1": _node("t1", "↪ delegated", "g9")}, archive=True)
        self._human(GRAND)
        self.assertTrue(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW),
                        "archive-held grand-sender chains still resolve")

    def test_a_cross_host_origin_quiets(self):
        self._store(MGR, {"g1": _node("g1", "mid-chain ask", None,
                                      origin={"peer": GRAND, "goalId": "t1", "msgId": "m0",
                                              "peerHost": "TESTHOST"})})
        self._store(GRAND, {"g9": _node("g9", "unreachable", None, promptUuid="hu"),
                            "t1": _node("t1", "↪ delegated", "g9")})
        self._human(GRAND)
        self.assertFalse(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW),
                         "another kernel's chain is not ours to read — quiet")

    def test_cycles_terminate_quiet(self):
        self._store(MGR, {"a": _node("a", "x", "b"), "b": _node("b", "y", "a")})
        self.assertFalse(jd._delegate_user_rooted(MGR, "a", self.paths, NOW))


BODY = ("Verify the staged run references and report drift.\n"
        "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID)
LINK_REPLY = '{"verdict": "delegating", "goal": 1, "text": "verify staged run references"}'


class CourierMintMatrix(unittest.TestCase):
    """run_courier end to end: a user-rooted delegate mints the recipient top exactly as before
    (origin, tracking node, propagate wiring untouched); an untraceable one files QUIET — no
    recipient goal, placements fyi, and the sender's tracker marked for report-back completion."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.wpath = d / (WKR + ".jsonl")
        self.wpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, BODY, "m1", ps="sdk"),
            aline(T0 + 60, "On it: verifying the staged references now.", "a1", "m1")]) + "\n")
        self.mpath = d / (MGR + ".jsonl")
        self._msgs = jd.MESSAGES
        jd.MESSAGES = d / "messages.jsonl"
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": MGR,
             "to_id": WKR, "kind": "delegate", "body": BODY.split("\n")[0]}) + "\n")
        self._disc = jd.discover
        fleet = [(WKR, str(self.wpath), None, "api"), (MGR, str(self.mpath), None, "web")]
        jd.discover = lambda now, window=None, forks=True: fleet
        self._llm = jd.courier_llm
        jd.courier_llm = lambda text, menu, declared=None: LINK_REPLY
        jd._PARSE_CACHE.clear()

    def tearDown(self):
        jd.MESSAGES = self._msgs
        jd.discover = self._disc
        jd.courier_llm = self._llm
        for sid in (MGR, WKR):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass
        self.td.cleanup()

    def _mgr_store(self, prompt_uuid, records):
        st = {"rompUuid": MGR, "seq": 1, "nodes":
              {MGR + ":g1": _node(MGR + ":g1", "Ship the staged-run verification", None,
                                  promptUuid=prompt_uuid)},
              "placements": {}, "status": {}}
        jd.save_goals(MGR, st)
        self.mpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_a_user_rooted_linked_delegate_links_into_the_ask_card(self):
        # T101 (the user 2026-08-26) supersedes the mint here: the courier's link resolved to the
        # sender's ask node, so the ASK CARD carries the dispatch — the tracker plants under it
        # (quiet: the reply-sweep owns its ending) and the recipient gets NO standalone top. The
        # mint survives only for the LINKLESS rooted shape (tests/test_ask_unit_cards.py holds
        # that fallback plus the fan-out matrix).
        self._mgr_store("hu", [uline(T0 - 600, "please verify the staged run references", "hu"),
                               aline(T0 - 540, "Dispatching.", "ha", "hu")])
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        self.assertEqual([nd for nd in w["nodes"].values()
                          if isinstance(nd.get("origin"), dict)], [],
                         "no recipient top — the ask card is the unit")
        self.assertIn("fyi", set(w["placements"].values()))
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values()
                    if isinstance(nd.get("handoff"), dict) and nd["handoff"].get("msgId") == MID]
        self.assertEqual(len(trackers), 1, "the sender tracking node plants either way")
        self.assertEqual(trackers[0].get("parentId"), MGR + ":g1", "…under the ask it serves")
        self.assertTrue(trackers[0]["handoff"].get("quiet"),
                        "no recipient goal will carry this msgId — the reply-sweep owns the ending")

    def test_an_untraceable_delegate_files_quiet(self):
        # the sender's linked goal roots at a COORDINATE mail record — team-internal, not the user
        self._mgr_store("cm", [uline(T0 - 600, "heads-up: refs regenerated\n"
                                     "<!-- romp-msg-id: 1787098000.000001_2.TESTHOST -->\n"
                                     "<!-- romp-msg-kind: coordinate -->", "cm", ps="sdk"),
                               aline(T0 - 540, "Noted; queueing a verification.", "ha", "cm")])
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        self.assertEqual([nd for nd in w["nodes"].values()
                          if isinstance(nd.get("origin"), dict)], [],
                         "no standalone recipient top for a team-internal chain")
        seg_vals = set(w["placements"].values())
        self.assertIn("fyi", seg_vals, "the segment is processed quietly — the coordinate treatment")
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values()
                    if isinstance(nd.get("handoff"), dict) and nd["handoff"].get("msgId") == MID]
        self.assertEqual(len(trackers), 1,
                         "the delegation still lives one glance away on the SENDER's board")
        self.assertTrue(trackers[0]["handoff"].get("quiet"),
                        "marked for report-back completion: no recipient goal will carry this msgId")


class QuietReportBack(unittest.TestCase):
    """run_propagate's reply sweep: a LOCAL quiet tracker completes on the recipient's reply (the
    report-back event, the cross-host rule); a LINKED local tracker stays the origin back-link's."""

    def setUp(self):
        self._msgs = jd.MESSAGES
        self.td = tempfile.TemporaryDirectory()
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        self._disc = jd.discover
        jd.discover = lambda now, window=None, forks=True: [(MGR, "/dev/null", None, "web")]

    def tearDown(self):
        jd.MESSAGES = self._msgs
        jd.discover = self._disc
        for d in (jd.GOALDIR, jd.GOALARCHDIR):
            try:
                (d / (MGR + ".json")).unlink()
            except OSError:
                pass
        self.td.cleanup()

    def _world(self, quiet, reply_at):
        h = {"peer": WKR, "msgId": MID}
        if quiet:
            h["quiet"] = True
        st = {"rompUuid": MGR, "seq": 1, "nodes":
              {MGR + ":t1": _node(MGR + ":t1", "↪ delegated to api: verify refs", None,
                                  t=T0, handoff=h)},
              "placements": {}, "status": {}}
        jd.save_goals(MGR, st)
        rows = []
        if reply_at:
            rows.append({"t": reply_at, "ev": "sent", "id": "r1", "from_id": WKR,
                         "to_id": MGR, "kind": "coordinate", "body": "verified; drift is zero"})
        jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))

    def test_a_quiet_tracker_completes_on_the_reply(self):
        self._world(quiet=True, reply_at=T0 + 900)
        jd.run_propagate(now=NOW)
        nd = jd.load_goals(MGR)["nodes"][MGR + ":t1"]
        self.assertTrue(nd.get("nodeComplete"))
        self.assertIn("quiet-filed", nd.get("doneWhy") or "")

    def test_no_reply_no_completion(self):
        self._world(quiet=True, reply_at=None)
        jd.run_propagate(now=NOW)
        self.assertFalse(jd.load_goals(MGR)["nodes"][MGR + ":t1"].get("nodeComplete"))

    def test_a_reply_before_the_send_does_not_count(self):
        self._world(quiet=True, reply_at=T0 - 900)
        jd.run_propagate(now=NOW)
        self.assertFalse(jd.load_goals(MGR)["nodes"][MGR + ":t1"].get("nodeComplete"))

    def test_a_linked_local_tracker_stays_the_back_links(self):
        self._world(quiet=False, reply_at=T0 + 900)
        jd.run_propagate(now=NOW)
        self.assertFalse(jd.load_goals(MGR)["nodes"][MGR + ":t1"].get("nodeComplete"),
                         "a linked recipient goal exists — its completion is the event, not the reply")


class NeedsYouStillSurfaces(unittest.TestCase):
    """THE CRITICAL PIN: quiet filing leaves zero goal nodes, and the needs-you surfaces are
    goal-independent — the placeholder synthesizes a needs-input card from the LIVE prompt alone
    (tests/test_kernel_blocked_no_goal.py holds the full behavioral matrix; this pins the
    store-independence that makes quiet filing safe)."""

    def test_the_placeholder_takes_no_goal_store(self):
        params = list(signature(km._blocked_placeholder).parameters)
        self.assertNotIn("store", params, "a session with ZERO goal nodes still surfaces")
        card = km._blocked_placeholder({"path": "/dev/null"}, "api", None, WKR, True,
                                       NOW, "permission", NOW - 60)
        self.assertEqual(card["column"], "needs_input")
        self.assertTrue(card["blocked"])


class TheSplitIsRetired(unittest.TestCase):
    """Absence pins (the user 2026-08-25 ~19:4x verdict, paraphrased: no lens/button — the cards
    must not be created): the build-time classifier and the footer toggle are gone."""

    FEED = (Path(HERE).parent / "ui" / "webview" / "feed.ts").read_text()
    KERNEL = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()

    def test_the_kernel_walk_is_gone(self):
        for tok in ("_ProvenanceWalk", "_prov_atom_klass", '"internal": True'):
            self.assertNotIn(tok, self.KERNEL, tok)

    def test_the_footer_toggle_is_gone(self):
        for tok in ("feed-internal-lens", "teamInternals", "internalLensOn", "internal?:"):
            self.assertNotIn(tok, self.FEED, tok)   # the retirement note may SAY "team-internal";
            #                                         no mechanism may remain

    def test_the_tag_lens_slot_family_survives(self):
        for tok in ("function viewScope", "function viewBase", "function viewFiltered"):
            self.assertIn(tok, self.FEED, "the T70 tag lens keeps its slots — only the internals slot retired")


if __name__ == "__main__":
    unittest.main()
