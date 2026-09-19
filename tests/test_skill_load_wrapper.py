#!/usr/bin/env python3
"""The harness's OWN skill load is never the user's ask (the user 2026-09-10: feed cards titled after a skill
their worker sessions never asked for). The CLI loads a skill for the model by itself as a command wrapper
with a BARE name and a <skill-format> tag, an isMeta user record right after the prompt, and the command
branch read it as a typed "/name": a human command atom that opened a segment of its own, so the work that
answered the real prompt was filed under a card titled from the skill, anchored on a record the feed took
for the user's words. Pinned here, on synthetic transcripts and stores (placeholder uuids, an invented
skill name, invented text):
- the event model emits no atom for the wrapper, on the file parse and on the live stream, and the prompt it
  rode in on keeps its segment and its work; a TYPED skill or command (leading slash) is still a command turn;
- the feed's read-side heal nests a top older stores minted from such a record under the human ask, with a
  why that names the skill, and never lets one host; a blocked one keeps its card and wears the face."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_skill_load", os.path.join(BIN, "romp-kernel"))
jd = km.jd
em = km.em
sb = load_source("romp_sdk_backend_skill_load", os.path.join(BIN, "romp_sdk_backend.py"))

NOW = 1781200000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
SKILL = "notes-review"                       # an invented skill name
STAMP = {"askAnchor": "machine", "askAnchorRecord": {"kind": "skill-load", "skill": SKILL}}   # what the latch writes
WRAPPER = ("<command-message>%s</command-message>\n<command-name>%s</command-name>\n"
           "<skill-format>true</skill-format>" % (SKILL, SKILL))
TYPED = ("<command-message>%s</command-message>\n<command-name>/%s</command-name>\n"
         "<command-args>look over the retry notes</command-args>" % (SKILL, SKILL))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, meta=False, prompt_id=None):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "promptSource": "typed", "message": {"role": "user", "content": text}}
    if meta:
        r["isMeta"] = True
    if prompt_id:
        r["promptId"] = prompt_id
    return r


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


def skill_turn(t0=T0):
    """A prompt, the harness's skill load (wrapper + markdown, both isMeta), then the model's work."""
    return [uline(t0, "Add retries to the notes-api client", "u1", prompt_id="p1"),
            uline(t0 + 1, WRAPPER, "u2", "u1", meta=True, prompt_id="p1"),
            uline(t0 + 1, [{"type": "text", "text": "# Notes review reference\n\nHow to review notes."}],
                  "u3", "u2", meta=True),
            aline(t0 + 60, "Adding the retry loop to the client.", "a1", "u3", stop="tool_use"),
            aline(t0 + 400, "Wrote the retry loop with a test.", "a2", "a1")]


class FileParse(unittest.TestCase):
    def _parse(self, recs, td):
        p = os.path.join(td, SID + ".jsonl")
        with open(p, "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in recs) + "\n")
        return em.parse_session(p, rompuuid=SID)

    def test_the_harness_skill_load_is_no_atom_and_the_prompt_keeps_its_work(self):
        with tempfile.TemporaryDirectory() as td:
            sess = self._parse(skill_turn(), td)
            users = [a for t in sess["turns"] for a in t["atoms"] if a["type"] == "user"]
            self.assertEqual([a["uuid"] for a in users], ["u1"], "the prompt is the one user atom")
            self.assertFalse(any(a.get("command") for t in sess["turns"] for a in t["atoms"]),
                             "the bare-named wrapper is not a typed command")
            segs = [seg for turn in sess["turns"] for seg in em.segments(turn)]
            self.assertEqual(len(segs), 1, "one segment: the prompt's, holding the work")
            self.assertTrue(jd._seg_human(segs[0]))
            self.assertFalse(jd._seg_command(segs[0]))
            texts = " ".join(jd._atom_text(a) for a in segs[0]["atoms"] if a["type"] == "assistant")
            self.assertIn("Wrote the retry loop", texts)

    def test_the_planner_sees_one_human_unit_carrying_the_work(self):
        with tempfile.TemporaryDirectory() as td:
            units = jd.plan_units(self._parse(skill_turn(), td))
            self.assertEqual(len(units), 1, "no unit for the skill load")
            self.assertTrue(units[0][4], "the one unit is the human ask")
            self.assertIn("retries", units[0][3])

    def test_a_typed_skill_is_still_a_worked_command_turn(self):
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "/%s look over the retry notes" % SKILL, "u1", prompt_id="p1"),
                    uline(T0 + 1, TYPED, "u2", "u1", prompt_id="p1"),
                    aline(T0 + 60, "Looking over the notes.", "a1", "u2")]
            segs = [seg for turn in self._parse(recs, td)["turns"] for seg in em.segments(turn)]
            cmd = [s for s in segs if jd._seg_command(s)]
            self.assertEqual(len(cmd), 1, "the leading slash is the typed-invocation tell")
            self.assertTrue(jd._seg_command_worked(cmd[0]))

    def test_the_slash_or_an_arguments_slot_decides_never_the_tag_alone(self):
        self.assertTrue(em.is_skill_load_wrapper(WRAPPER))
        self.assertFalse(em.is_skill_load_wrapper(WRAPPER + "\n<command-args>staging now</command-args>"),
                         "a name written without its slash but with something typed after it: a command")
        self.assertFalse(em.is_skill_load_wrapper(WRAPPER.replace("</command-name>", "</command-name>\n<command-args></command-args>")),
                         "an empty slot is still a slot: nothing the harness writes")
        self.assertFalse(em.is_skill_load_wrapper(TYPED + "\n<skill-format>true</skill-format>"),
                         "a CLI that tags a typed skill too: still typed")
        self.assertFalse(em.is_skill_load_wrapper("prose that quotes " + WRAPPER), "only a record that begins with a wrapper")
        self.assertFalse(em.is_skill_load_wrapper(WRAPPER.replace("<skill-format>true</skill-format>", "")),
                         "a bare name with no skill-format tag is left to the command branch")

    def test_the_parse_reports_the_wrappers_it_skipped_by_uuid_and_skill(self):
        with tempfile.TemporaryDirectory() as td:
            sess = self._parse(skill_turn(), td)
            self.assertEqual(sess["skillLoads"], {"u2": SKILL}, "the record the judge's stamp reads")
            recs = [uline(T0, "/%s look over the retry notes" % SKILL, "u1", prompt_id="p1"),
                    uline(T0 + 1, TYPED, "u2", "u1", prompt_id="p1"), aline(T0 + 60, "Looking.", "a1", "u2")]
            self.assertEqual(self._parse(recs, td)["skillLoads"], {}, "a typed skill is an invocation, not a load")


class LiveStream(unittest.TestCase):
    """The SDK stream leads the disk write: the live twin must drop the same wrapper, or the live tail opens
    the phantom command segment the file parse no longer does."""

    class _TextBlock:
        def __init__(self, text):
            self.text = text

    class _UserMessage:
        def __init__(self, content):
            self.content, self.uuid, self.tool_use_result, self.origin = content, "u2", None, None

    _TextBlock.__name__ = "TextBlock"
    _UserMessage.__name__ = "UserMessage"

    def test_the_wrapper_is_no_live_atom(self):
        self.assertIsNone(sb.msg_to_atom(self._UserMessage([self._TextBlock(WRAPPER)]), "s", "f", 5))

    def test_the_two_adapters_read_every_shape_the_same(self):
        shapes = [WRAPPER, TYPED, TYPED + "\n<skill-format>true</skill-format>", WRAPPER + "\n<command-args>x</command-args>",
                  WRAPPER.replace("<skill-format>true</skill-format>", ""), "prose " + WRAPPER, "", None]
        for w in shapes:
            self.assertEqual(sb._is_skill_load_wrapper(w), em.is_skill_load_wrapper(w), repr(w)[:60])

    def test_the_landing_scan_never_reads_the_wrapper_as_a_human_input(self):
        recs = skill_turn()
        self.assertFalse(sb._human_input_record(recs[1]), "the harness's load")
        self.assertTrue(sb._human_input_record(recs[0]), "the prompt it rode in on")
        self.assertTrue(sb._human_input_record(uline(T0, TYPED, "u5", prompt_id="p2")), "a typed skill, however marked")

    def test_a_typed_skill_is_still_the_live_command_atom(self):
        a = sb.msg_to_atom(self._UserMessage([self._TextBlock(TYPED)]), "s", "f", 5)
        self.assertEqual(a.get("command"), "/" + SKILL)


class LatchStamps(unittest.TestCase):
    """The judge is the one writer: _latch_skill_load_anchors stamps a top anchored on a skill-load record 'machine'
    off the record, re-stamping an older 'human' latch once, and never touches a top anchored elsewhere; the
    general latch then runs as before."""

    def _session(self, td):
        p = os.path.join(td, SID + ".jsonl")
        with open(p, "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in skill_turn()) + "\n")
        sess = em.parse_session(p, rompuuid=SID)
        return sess, sess["skillLoads"]

    def test_an_older_human_latch_on_the_wrapper_is_restamped_once(self):
        with tempfile.TemporaryDirectory() as td:
            sess, loads = self._session(td)
            self.assertEqual(loads, {"u2": SKILL})
            store = {"nodes": {"g1": {"parentId": None, "promptUuid": "u1", "askAnchor": "human"},
                               "g2": {"parentId": None, "promptUuid": "u2", "askAnchor": "human", "log": []},
                               "g2s": {"parentId": "g2", "text": "Read the retry notes", "log": []},
                               "g3": {"parentId": None, "promptUuid": "u2"},
                               "g4": {"parentId": None, "promptUuid": "a1"}}}
            n = jd._latch_skill_load_anchors(store, loads, NOW)
            self.assertEqual(n, 2, "the re-stamp and the fresh skill-load top")
            self.assertEqual(jd._latch_ask_anchors(SID, sess, store), 1, "the general latch: the fresh machine top only")
            self.assertEqual(store["nodes"]["g1"]["askAnchor"], "human", "a human latch on a real prompt is never touched")
            self.assertNotIn("askAnchorRecord", store["nodes"]["g1"])
            for nid in ("g2", "g3"):
                self.assertEqual(store["nodes"][nid]["askAnchor"], "machine")
                self.assertEqual({k: v for k, v in store["nodes"][nid]["askAnchorRecord"].items() if k != "at"},
                                 {"kind": "skill-load", "skill": SKILL})
            self.assertEqual(store["nodes"]["g4"]["askAnchor"], "machine")
            for nid in ("g2", "g2s"):                    # resolved: romp's done verdict, the why naming the skill
                self.assertTrue(store["nodes"][nid]["nodeComplete"], nid)
                done = [e for e in store["nodes"][nid]["log"] if e.get("kind") == "done"]
                self.assertEqual([(e["src"], e["ev_t"]) for e in done], [("romp", NOW)])
                self.assertIn("%s skill the harness loaded" % SKILL, done[0]["why"])
            self.assertFalse(store["nodes"]["g1"].get("nodeComplete"), "the real ask is untouched")
            self.assertEqual(jd._latch_skill_load_anchors(store, loads, NOW), 0, "idempotent")
            self.assertEqual(len([e for e in store["nodes"]["g2"]["log"] if e.get("kind") == "done"]), 1, "one verdict")

    def test_a_block_the_agent_filed_for_the_user_is_never_resolved_away(self):
        with tempfile.TemporaryDirectory() as td:
            sess, loads = self._session(td)
            closer = [{"kind": "block", "src": "closer", "ev_t": T0 + 400, "why": "asked which client to change"}]
            nudge = [{"kind": "block", "src": "nudge", "ev_t": T0 + 400, "why": "no reply to the nudge"}]
            store = {"nodes": {"g2": {"parentId": None, "promptUuid": "u2", "askAnchor": "human", "log": []},
                               "g2s": {"parentId": "g2", "blocked": True, "log": list(closer)},
                               "g5": {"parentId": None, "promptUuid": "u2", "askAnchor": "human", "blocked": True, "log": list(nudge)}}}
            self.assertEqual(jd._latch_skill_load_anchors(store, loads, NOW), 2, "both stamped")
            self.assertTrue(jd._asks_user(store["nodes"], "g2"))
            self.assertFalse(store["nodes"]["g2"].get("nodeComplete"), "the agent is waiting on the user under it: not resolved")
            self.assertTrue(store["nodes"]["g2s"]["blocked"])
            self.assertFalse(jd._asks_user(store["nodes"], "g5"), "a failed nudge is romp's own block")
            self.assertTrue(store["nodes"]["g5"]["nodeComplete"], "resolved: the later done supersedes the nudge block")
            self.assertFalse(store["nodes"]["g5"]["blocked"])

    def test_a_top_stamped_while_the_agent_asked_resolves_once_the_answer_clears_the_block(self):
        with tempfile.TemporaryDirectory() as td:
            sess, loads = self._session(td)
            store = {"nodes": {"g2": {"parentId": None, "promptUuid": "u2", "askAnchor": "human", "log": []},
                               "g2s": {"parentId": "g2", "blocked": True,
                                       "log": [{"kind": "block", "src": "closer", "ev_t": T0 + 400, "why": "asked which client"}]}}}
            self.assertEqual(jd._latch_skill_load_anchors(store, loads, NOW), 1)
            self.assertFalse(store["nodes"]["g2"].get("nodeComplete"), "stamped, not resolved: the agent is waiting")
            # the user answers: the unblocker lifts the step's block
            jd.record_verdict(store, store["nodes"]["g2s"], "romp", "unblock", NOW + 10)
            self.assertFalse(store["nodes"]["g2s"]["blocked"])
            self.assertEqual(jd._latch_skill_load_anchors(store, loads, NOW + 20), 0, "no new stamp")
            self.assertTrue(store["nodes"]["g2"]["nodeComplete"], "revisited on the next pass: resolved")
            self.assertTrue(store["nodes"]["g2s"]["nodeComplete"])

    def test_the_done_carries_the_mint_time_and_a_users_reply_on_the_card_keeps_it_open(self):
        with tempfile.TemporaryDirectory() as td:
            sess, loads = self._session(td)
            store = {"nodes": {"g2": {"parentId": None, "promptUuid": "u2", "askAnchor": "human", "t": T0 + 2, "log": []},
                               "g2s": {"parentId": "g2", "t": T0 + 90, "blocked": True,
                                       "log": [{"kind": "block", "src": "closer", "ev_t": T0 + 400, "why": "asked which client"}]}}}
            self.assertEqual(jd._latch_skill_load_anchors(store, loads, NOW), 1)
            self.assertEqual(store["nodes"]["g2"]["askAnchorRecord"]["at"], NOW)
            # the user replies on the card: a user reopen with a message (what optimistic_followup files) unblocks the
            # subtree and stamps the follow-up floor; the continuation places the reply's step under the top
            jd.record_verdict(store, store["nodes"]["g2"], "user", "reopen", NOW + 10, msg=True)
            jd.record_verdict(store, store["nodes"]["g2s"], "user", "reopen", NOW + 10, msg=True)
            store["nodes"]["g2f"] = {"parentId": "g2", "t": NOW + 15, "text": "Change the retry client", "log": []}
            self.assertFalse(store["nodes"]["g2s"]["blocked"])
            self.assertTrue(jd._floor_of(store, store["nodes"]["g2"]), "the follow-up floor stands")
            self.assertEqual(jd._latch_skill_load_anchors(store, loads, NOW + 20), 0)
            for nid in ("g2", "g2s", "g2f"):
                self.assertFalse(store["nodes"][nid].get("nodeComplete"), "%s stays open: the user engaged" % nid)
                self.assertEqual([e for e in store["nodes"][nid]["log"] if e.get("kind") == "done"], [])
            # a top nobody replied on resolves at its mint time, never the pass clock
            store2 = {"nodes": {"g2": {"parentId": None, "promptUuid": "u2", "askAnchor": "human", "t": T0 + 2, "log": []},
                                "g2s": {"parentId": "g2", "t": T0 + 90, "log": []}}}
            jd._latch_skill_load_anchors(store2, loads, NOW)
            for nid in ("g2", "g2s"):
                done = [e for e in store2["nodes"][nid]["log"] if e.get("kind") == "done"]
                self.assertEqual([e["ev_t"] for e in done], [T0 + 2], nid)

    def test_the_general_latch_alone_leaves_an_older_human_latch_as_it_was(self):
        with tempfile.TemporaryDirectory() as td:
            sess, _ = self._session(td)
            store = {"nodes": {"g2": {"parentId": None, "promptUuid": "u2", "askAnchor": "human"}}}
            self.assertEqual(jd._latch_ask_anchors(SID, sess, store), 0)
            self.assertEqual(store["nodes"]["g2"]["askAnchor"], "human")
            self.assertEqual(jd._latch_skill_load_anchors(store, {}, NOW), 0, "no records, no stamp")

    def test_the_planner_pass_hands_the_latch_the_records(self):
        import inspect
        src = inspect.getsource(jd._plan_session)
        self.assertIn('_latch_skill_load_anchors(store, session.get("skillLoads") or {}, now)', src)
        self.assertLess(src.index("_latch_skill_load_anchors("), src.index("_latch_ask_anchors(fsid, session, store)"),
                        "the stamp runs first, so the general latch skips these as latched")


class HealOlderStores(unittest.TestCase):
    """build_feed over a store the old parse minted: the skill-load top nests, never hosts, and a blocked one
    keeps its card with the face."""

    def setUp(self):
        km._downtime[:] = []
        km._HEAL_LOG["n"] = 0
        km._HEAL_LOG["h"] = 0
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in skill_turn()) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        km._live_map = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._bgall_cache.clear()
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._SKILL_INDEX_FAILED.clear()
        jd._WRAP_LOADED["v"] = False; jd._WRAP_DIRTY["v"] = False
        jd._WRAP_READS.update({"n": 0, "bytes": 0})

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map) = self.saved
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._SKILL_INDEX_FAILED.clear()
        jd._WRAP_LOADED["v"] = False; jd._WRAP_DIRTY["v"] = False
        jd._WRAP_READS.update({"n": 0, "bytes": 0})
        self.td.cleanup()

    @staticmethod
    def _node(nid, text, parent=None, t=T0, **kw):
        d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False, "cleared": False,
             "trail": [], "t": t, "mt": t}
        d.update(kw)
        return d

    def _store(self, nodes, status=None, last=None):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": len(nodes), "lastNode": last, "nodes": nodes, "placements": {}, "status": status or {}}))

    def _feed(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            f = km.build_feed(NOW)
        return {a["itemId"]: a for a in f["asks"] if a["sid"] == SID}, err.getvalue()

    def _old_store(self, **skill_kw):
        ask, skill, late, own = SID + ":g1", SID + ":g2", SID + ":g3", SID + ":g4"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     # the old parse read the harness's wrapper as a typed command and the judge latched it "human";
                     # the latch has since re-stamped it off the record (LatchStamps below)
                     skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP, **skill_kw),
                     # a human top whose anchor the transcript no longer holds: never touched
                     late: self._node(late, "Rename the colour tokens", t=T0 + 700, promptUuid="u9", askAnchor="human"),
                     # a machine-rooted top minted right after the skill top: its host must be the ask, never the skill top
                     own: self._node(own, "Tidy the client's imports", t=T0 + 3, promptUuid="a1", askAnchor="machine")})
        return ask, skill, late, own

    def test_a_skill_load_top_nests_under_the_ask_and_never_hosts(self):
        ask, skill, late, own = self._old_store()
        asks, err = self._feed()
        self.assertEqual(set(asks), {ask, late}, "the skill-load top is no card; the human tops stay")
        rows = {r["id"]: r for r in asks[ask]["tree"]}
        self.assertIn(skill, rows)
        self.assertTrue(rows[skill]["born"]["healed"])
        self.assertIn("%s skill the harness loaded" % SKILL, rows[skill]["born"]["why"])
        self.assertIn(own, rows)
        h = km._heal_session_tops(str(self.tpath), json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"], {})
        self.assertEqual(h[own][0], ask, "the machine top's host is the ask, not the skill-load top minted just before it")
        self.assertEqual(h[skill][0], ask)
        self.assertIsNone(asks[late]["sessionStarted"])
        self.assertIn("feed: 2 session-started top(s) nested", err)

    def test_a_blocked_skill_load_top_is_nested_all_the_same(self):
        # a failed nudge blocked it (the diary row says so): romp's own door, and the card must not come back through
        # it (the manager's review of T333, item 1); a block with NO diary row reads as the agent's question (fail open)
        ask, skill, late, own = self._old_store(blocked=True, log=[{"kind": "block", "src": "nudge", "ev_t": T0 + 400}])
        asks, _ = self._feed()
        self.assertEqual(set(asks), {ask, late})
        self.assertIn(skill, {r["id"] for r in asks[ask]["tree"]})

    def test_a_skill_load_top_with_no_request_in_the_store_is_hidden(self):
        # a worker session whose only top IS the skill-load card (its dispatch arrived as a record the courier never
        # planted a goal for): nothing to sit under, so the feed shows no card; the session's own view keeps the work
        skill, step = SID + ":g1", SID + ":g2"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP),
                     step: self._node(step, "Read the retry notes", parent=skill, t=T0 + 90)})
        asks, err = self._feed()
        self.assertEqual(set(asks), set(), "no card")
        self.assertIn("feed: 1 session-started top(s) hidden (rooted in a skill the harness loaded", err)
        asks2, err2 = self._feed()
        self.assertEqual(set(asks2), set())
        self.assertEqual(err2, "", "said once")

    def test_a_floor_that_resolves_to_a_hidden_top_brings_it_back_as_a_card(self):
        # the session stopped on a permission prompt under the hidden top: the floor keys the card on it, so it shows
        skill, step = SID + ":g1", SID + ":g2"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP),
                     step: self._node(step, "Read the retry notes", parent=skill, t=T0 + 90)}, last=step)
        km._live_map = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        asks, err = self._feed()
        self.assertEqual(set(asks), {skill})
        self.assertEqual(asks[skill]["column"], "needs_input")
        self.assertIn("%s skill the harness loaded" % SKILL, asks[skill]["sessionStarted"]["why"])
        self.assertNotIn("hidden", err, "brought back before the count is said")

    def test_a_standing_stall_hold_never_brings_a_hidden_top_back(self):
        # a stale deferral record on the goal (the nudge walk no longer mints one for a skill-load top): not a door
        skill = SID + ":g1"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP)})
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "deferred": {skill: {"why": "the judge tiers are paused (nothing could revive it)",
                                                   "at": NOW - 100, "sid": SID}}}))
        asks, err = self._feed()
        self.assertEqual(set(asks), set(), "the hold is romp's own machinery, not a request: still hidden")
        self.assertIn("1 session-started top(s) hidden", err)

    def test_an_exported_blocked_status_is_no_door_either(self):
        skill = SID + ":g1"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP)},
                    status={skill: "blocked"})
        asks, err = self._feed()
        self.assertEqual(set(asks), set(), "an exported block is no door either")
        self.assertIn("1 session-started top(s) hidden", err)

    def test_a_nudge_failed_block_with_no_request_stays_hidden(self):
        skill = SID + ":g1"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP, blocked=True,
                                       log=[{"kind": "block", "src": "nudge", "ev_t": T0 + 400}])})
        asks, _ = self._feed()
        self.assertEqual(set(asks), set(), "a nudge-failed block never brings the skill-titled card back")

    def test_a_users_reply_on_the_card_keeps_it(self):
        skill = SID + ":g1"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP, followupAt=T0 + 900)})
        asks, err = self._feed()
        self.assertEqual(set(asks), {skill}, "the user replied on it: theirs now, the card stays")
        self.assertNotIn("hidden", err)

    def test_the_agents_question_to_the_user_keeps_the_card_with_the_face(self):
        skill, step = SID + ":g1", SID + ":g2"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP),
                     step: self._node(step, "Which client should change?", parent=skill, t=T0 + 90, blocked=True,
                                      log=[{"kind": "block", "src": "closer", "ev_t": T0 + 400, "why": "asked the user"}])},
                    status={skill: "blocked"})
        asks, err = self._feed()
        self.assertEqual(set(asks), {skill}, "the closer's block is the agent waiting on the user: needs-you shows")
        self.assertEqual(asks[skill]["column"], "needs_input")
        self.assertIn("%s skill the harness loaded" % SKILL, asks[skill]["sessionStarted"]["why"])
        self.assertNotIn("hidden", err)

    def test_the_boot_sweep_reaches_a_wrapper_in_a_pre_clear_file_and_resolves_the_top(self):
        # the store's top was minted before a /clear: its wrapper sits in a file the lineage no longer links, so
        # the planner's stamp (the walked chain) never sees it; the once-per-boot store-side pass looks the anchor
        # up raw across the project directory, stamps it, resolves it, and the feed hides it; it hosts nothing
        old = self.tpath.with_name("22222222-3333-4444-5555-666666666666.jsonl")
        old.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 - 9000, "Set up the notes-api repo", "w1"),
            uline(T0 - 8999, WRAPPER, "w2", "w1", meta=True),
            aline(T0 - 8900, "Set it up.", "wa1", "w2")]) + "\n")
        ask, skill, step, own = SID + ":g1", SID + ":g2", SID + ":g3", SID + ":g4"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 - 8990, promptUuid="w2", askAnchor="human"),
                     step: self._node(step, "Read the retry notes", parent=skill, t=T0 - 8950),
                     own: self._node(own, "Tidy the client's imports", t=T0 + 3, promptUuid="a1", askAnchor="machine")})
        jd._WRAP_INDEX.clear()
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW), (1, 1), "(stores, tops)")
        store = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertEqual(store["nodes"][skill]["askAnchor"], "machine")
        self.assertEqual({k: v for k, v in store["nodes"][skill]["askAnchorRecord"].items() if k != "at"},
                         {"kind": "skill-load", "skill": SKILL})
        self.assertTrue(store["nodes"][skill]["nodeComplete"] and store["nodes"][step]["nodeComplete"], "resolved")
        self.assertEqual(store["nodes"][ask]["askAnchor"], "human", "the real ask keeps its latch")
        self.assertTrue(store["status"].get(skill) == "completed" or skill in (store.get("confirming") or []),
                        "rolled up before the save: the exports are never staler than the flags (%r)" % store["status"].get(skill))
        self.assertTrue((jd.STATE / "skill-load-index.json").exists(), "the index persists across boots")
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW), (0, 0), "idempotent")
        asks, err = self._feed()
        self.assertEqual(set(asks), {ask}, "the skill-load top is no card; the ask stays")
        h = km._heal_session_tops(str(self.tpath), json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"], {})
        self.assertEqual(h[own][0], ask, "the machine top's host is the ask, never the skill-load top")
        # the skill-load top was minted BEFORE the ask (a pre-clear lineage): no host was current at its mint, so it is
        # hidden, not nested (2026-09-18: the oldest-host fallback that nested it under the later ask swept old roots
        # into a newer request's tree; the feed's outcome above, no card, is the same either way)
        self.assertIsNone(h[skill][0], "no host minted before it")
        self.assertTrue(h[skill][1].get("hidden"), "hidden: the session's own view keeps the work")
        self.assertIn("hidden (rooted in a skill the harness loaded", err)

    def test_the_boot_pass_converges_a_second_boot_reads_the_appended_bytes_only(self):
        # boot one indexes the directory whole and checks the real ask for good; boot two (the persisted index and memo
        # loaded from disk) reads only what grew, for a fresh candidate; boot three, nothing changed, reads nothing
        ask, skill = SID + ":g1", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", askAnchor="human")})
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._WRAP_LOADED["v"] = False
        jd._WRAP_READS.update({"n": 0, "bytes": 0})
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW), (1, 1))
        whole = self.tpath.stat().st_size
        self.assertEqual(jd._WRAP_READS["bytes"], whole, "boot one: the transcript read whole")
        self.assertIn("u1", jd._CHECKED, "the real ask, looked up in a complete directory index: checked for good")
        self.assertNotIn("u2", jd._CHECKED)
        idx = json.loads((jd.STATE / "skill-load-index.json").read_text())
        self.assertEqual(sorted(idx["checked"]), ["u1"])
        self.assertEqual(idx["files"][str(self.tpath)][3], {"u2": SKILL})
        # a day of work: the transcript grows, and a fresh top with an anchor no parse showed appears
        tail = json.dumps(uline(T0 + 900, "And the docs page", "u9")) + "\n"
        with open(self.tpath, "a") as fh:
            fh.write(tail)
        store = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        store["nodes"][SID + ":g9"] = self._node(SID + ":g9", "Write the docs page", t=T0 + 900, promptUuid="u9", askAnchor="human")
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._WRAP_LOADED["v"] = False      # boot two
        jd._WRAP_READS.update({"n": 0, "bytes": 0})
        jd._wrap_index_load()
        self.assertIn("u1", jd._CHECKED, "the persisted memo is read back before any file is")
        self.assertIn(str(self.tpath), jd._WRAP_INDEX)
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW + 100), (0, 0))
        self.assertEqual(jd._WRAP_READS["bytes"], len(tail.encode()), "boot two: the appended tail only")
        self.assertIn("u9", jd._CHECKED)
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._WRAP_LOADED["v"] = False      # boot three
        jd._WRAP_READS.update({"n": 0, "bytes": 0})
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW + 200), (0, 0))
        self.assertEqual((jd._WRAP_READS["n"], jd._WRAP_READS["bytes"]), (0, 0), "boot three: nothing to read, nothing read")

    def test_the_byte_budget_bounds_a_boot_and_leaves_the_directory_unchecked(self):
        skill = SID + ":g2"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", askAnchor="human")})
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._WRAP_LOADED["v"] = False
        jd._WRAP_READS.update({"n": 0, "bytes": 0})
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW, byte_budget=0), (0, 0), "the budget is spent before the first file")
        self.assertEqual(jd._WRAP_READS["bytes"], 0)
        self.assertEqual(jd._CHECKED, set(), "an incomplete directory checks nothing")

    def test_a_parse_that_shows_the_anchor_as_an_atom_checks_the_top_without_a_read(self):
        ask, skill = SID + ":g1", SID + ":g2"
        store = {"nodes": {ask: self._node(ask, "Add retries", promptUuid="u1", askAnchor="human"),
                           skill: self._node(skill, "Review notes", promptUuid="u2", askAnchor="human")}}
        jd._CHECKED.clear()
        self.assertEqual(jd._note_checked(store, {"u1", "a1"}), 1)
        self.assertEqual(jd._CHECKED, {"u1"}, "the wrapper's uuid is no atom, so its top stays a candidate")
        self.assertEqual([nd["promptUuid"] for nd in jd._skill_load_candidates(store["nodes"])], ["u2"])
        jd._CHECKED.clear()

    def test_the_perf_endpoint_counts_the_passs_reads(self):
        snap = km._PERF_STATS.snapshot()["skillLoadIndex"]
        self.assertEqual(set(snap), {"filesRead", "bytesRead", "filesIndexed", "checked"})
        self.assertEqual(snap["filesRead"], jd._WRAP_READS["n"])

    def test_the_debt_escalation_never_lands_on_a_skill_load_top(self):
        ask, skill = SID + ":g1", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", **STAMP)},
                    status={ask: "working", skill: "working"})
        self.assertTrue(km._debt_escalate(SID, "22222222-3333-4444-5555-666666666666", NOW, NOW))
        store = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertTrue(store["nodes"][ask]["blocked"], "the newest ordinary working top takes the block")
        self.assertFalse(store["nodes"][skill].get("blocked"), "never the hidden skill-load top")

    @unittest.skipIf(os.geteuid() == 0, "root reads every file: the unreadable case cannot be staged")
    def test_an_unreadable_transcript_leaves_its_directory_incomplete_and_is_said_once(self):
        # a transcript the kernel cannot open is no evidence of absence: nothing in its directory is checked for good,
        # judge-errors names it once, and the next boot that can read it stamps the top
        old = self.tpath.with_name("22222222-3333-4444-5555-666666666666.jsonl")
        old.write_text("\n".join(json.dumps(r) for r in [uline(T0 - 9000, "Set up the repo", "w1"),
                                                         uline(T0 - 8999, WRAPPER, "w2", "w1", meta=True)]) + "\n")
        skill = SID + ":g2"
        self._store({skill: self._node(skill, "Review notes via /%s" % SKILL, t=T0 - 8990, promptUuid="w2", askAnchor="human")})
        errs = jd.ERRORS
        before = len(errs.read_text().splitlines()) if errs.exists() else 0
        old.chmod(0)
        try:
            self.assertEqual(jd.restamp_skill_load_tops_all(NOW), (0, 0))
            self.assertEqual(jd._CHECKED, set(), "an incomplete directory checks nothing")
            rows = errs.read_text().splitlines()[before:]
            self.assertEqual(len([r for r in rows if "unreadable for the skill-load index" in r]), 1)
            self.assertEqual(jd.restamp_skill_load_tops_all(NOW + 1), (0, 0))
            self.assertEqual(len([r for r in errs.read_text().splitlines()[before:] if "unreadable for the skill-load index" in r]), 1,
                             "said once")
        finally:
            old.chmod(0o644)
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW + 2), (1, 1), "readable again: the top is stamped")

    def test_the_stores_own_transcripts_come_first_under_a_cut_budget(self):
        # two stores in one project directory, each wrapper in its own transcript, and a large unrelated file that sorts
        # first: with the budget set to exactly the two transcripts, both tops are stamped, the big file is never read,
        # and the directory stays unchecked
        sid2 = "11111111-2222-3333-4444-666666666666"
        (jd.NAMES / sid2).write_text("api\t%s\t#abcdef\n" % str(Path(self.td.name) / "launchdir"))
        leaf2 = self.tpath.with_name(sid2 + ".jsonl")
        shift = lambda r: dict(r, uuid=r["uuid"] + "b", parentUuid=(r["parentUuid"] + "b") if r.get("parentUuid") else None)
        leaf2.write_text("\n".join(json.dumps(shift(r)) for r in skill_turn(T0 + 5000)) + "\n")
        big = self.tpath.with_name("00000000-0000-0000-0000-000000000000.jsonl")
        big.write_text("".join(json.dumps(uline(T0 - 50000 + i, "an old line %d" % i, "z%d" % i)) + "\n" for i in range(3000)))
        self._store({SID + ":g2": self._node(SID + ":g2", "Review notes via /%s" % SKILL, t=T0 + 2, promptUuid="u2", askAnchor="human")})
        (jd.GOALDIR / (sid2 + ".json")).write_text(json.dumps(
            {"rompUuid": sid2, "seq": 1, "lastNode": None, "placements": {}, "status": {},
             "nodes": {sid2 + ":g2": self._node(sid2 + ":g2", "Review notes via /%s" % SKILL, t=T0 + 5002, promptUuid="u2b", askAnchor="human")}}))
        budget = self.tpath.stat().st_size + leaf2.stat().st_size
        self.assertGreater(big.stat().st_size, budget)
        self.assertEqual(jd.restamp_skill_load_tops_all(NOW, byte_budget=budget), (2, 2), "both stores stamped from their own transcripts")
        self.assertEqual(jd._WRAP_READS["bytes"], budget, "the two transcripts, nothing more")
        self.assertNotIn(str(big), jd._WRAP_INDEX, "the big file waits for the next boot")
        self.assertEqual(jd._CHECKED, set(), "the directory is incomplete: nothing checked for good")


class RawIndexIsIncremental(unittest.TestCase):
    """The sweep's raw index reads a grown transcript from its kept offset only, a shrunk or rewritten one whole,
    and an unchanged one not at all."""

    def setUp(self):
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._SKILL_INDEX_FAILED.clear()
        jd._WRAP_LOADED["v"] = False; jd._WRAP_DIRTY["v"] = False
        jd._WRAP_READS.update({"n": 0, "bytes": 0})

    def tearDown(self):
        jd._WRAP_INDEX.clear(); jd._CHECKED.clear(); jd._SKILL_INDEX_FAILED.clear()
        jd._WRAP_LOADED["v"] = False; jd._WRAP_DIRTY["v"] = False
        jd._WRAP_READS.update({"n": 0, "bytes": 0})

    def test_a_grown_file_costs_its_tail_and_an_unchanged_one_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            recs = skill_turn()
            head = "\n".join(json.dumps(r) for r in recs[:2]) + "\n"
            p.write_text(head)
            self.assertEqual(jd._skill_load_index(str(p)), {"u2": SKILL})
            self.assertEqual(jd._WRAP_READS["bytes"], len(head.encode()))
            self.assertEqual(jd._skill_load_index(str(p)), {"u2": SKILL})
            self.assertEqual(jd._WRAP_READS["n"], 1, "an unchanged size is served from the memo")
            tail = "\n".join(json.dumps(r) for r in recs[2:]) + "\n"
            with open(p, "a") as fh:
                fh.write(tail)
            self.assertEqual(jd._skill_load_index(str(p)), {"u2": SKILL})
            self.assertEqual(jd._WRAP_READS["bytes"], len(head.encode()) + len(tail.encode()), "only the appended tail was read")
            self.assertEqual(jd._WRAP_READS["n"], 2)

    def test_a_rewrite_is_read_whole_and_a_cut_line_waits(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            recs = skill_turn()
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            self.assertEqual(jd._skill_load_index(str(p)), {"u2": SKILL})
            # a REWRITE that happens to be larger: the kept bytes no longer match, so the whole file is read again
            other = [uline(T0, "Add retries to the notes-api client", "u1"), uline(T0 + 1, WRAPPER, "u7", "u1", meta=True),
                     aline(T0 + 60, "A different reply, longer than the one before it by a fair margin.", "a1", "u7")]
            p.write_text("\n".join(json.dumps(r) for r in other) + "\n" + " " * 400 + "\n")
            self.assertEqual(jd._skill_load_index(str(p)), {"u7": SKILL}, "the old uuid is gone: no splice")
            # a shrink: read whole
            p.write_text(json.dumps(recs[1]) + "\n")
            self.assertEqual(jd._skill_load_index(str(p)), {"u2": SKILL})
            # a writer caught mid-append: the cut line is not consumed and lands next time
            with open(p, "a") as fh:
                fh.write(json.dumps(uline(T0 + 5, WRAPPER, "u8", "u2", meta=True))[:-9])
            self.assertEqual(jd._skill_load_index(str(p)), {"u2": SKILL})
            with open(p, "a") as fh:
                fh.write(json.dumps(uline(T0 + 5, WRAPPER, "u8", "u2", meta=True))[-9:] + "\n")
            self.assertEqual(jd._skill_load_index(str(p)), {"u2": SKILL, "u8": SKILL})


class ParseSpansAndFolds(unittest.TestCase):
    """The parse's report crosses a stitched resume fork, and a wrapper landing after its prompt folds
    incrementally instead of demoting the session to a full re-parse."""

    def test_the_report_crosses_a_resume_fork(self):
        with tempfile.TemporaryDirectory() as td:
            pa = Path(td) / "33333333-4444-5555-6666-777777777777.jsonl"
            pb = Path(td) / (SID + ".jsonl")
            pa.write_text("\n".join(json.dumps(r) for r in skill_turn()) + "\n")
            pb.write_text("\n".join(json.dumps(r) for r in [
                uline(T0 + 1000, "Now the docs page", "u9", "a2", prompt_id="p9"),
                aline(T0 + 1030, "Docs page done.", "a9", "u9")]) + "\n")
            sess = em.parse_session(str(pb), rompuuid=SID, candidate_files=[str(pa), str(pb)], states=None, now=NOW)
            self.assertEqual(sess["skillLoads"], {"u2": SKILL}, "the wrapper in the resumed-from file is reported")
            self.assertEqual(len([a for t in sess["turns"] for a in t["atoms"] if a["type"] == "user"]), 2)

    def test_a_wrapper_landing_after_its_prompt_folds_rather_than_demotes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            recs = skill_turn()
            p.write_text(json.dumps(recs[0]) + "\n")
            kw = {"candidate_files": [str(p)], "states": None, "now": NOW}
            em.parse_session(str(p), rompuuid=SID, **kw)
            full0, fold0 = em._ASM_STATS["full"], em._ASM_STATS["fold"]
            with open(p, "a") as fh:
                fh.write("\n".join(json.dumps(r) for r in recs[1:]) + "\n")
            sess = em.parse_session(str(p), rompuuid=SID, **kw)
            self.assertEqual(em._ASM_STATS["full"], full0, "no demote to a full re-parse")
            self.assertEqual(em._ASM_STATS["fold"], fold0 + 1, "the appended records folded")
            self.assertEqual(sess["skillLoads"], {"u2": SKILL})


class NudgeWalkSkips(unittest.TestCase):
    """The nudge walk never asks about a skill-load top (the belt under the stamp's done verdict): the same store
    without the stamp is walked. Drives the real _auto_nudge_session with the fresh-guard harness idiom."""

    G1 = SID + ":g1"
    ARM_T = NOW - 600

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        km._autonudge_cache.clear()
        self._orig_km = {n: getattr(km, n) for n in (
            "_session_flag", "_compacting_now", "_api_error", "_session_working",
            "_interrupt_suppresses_nudge", "_backend_queued", "_backend_rewind_pending",
            "_last_state", "_session_awaiting", "_closer_settled", "_revivers_pending",
            "_pending_ops", "_last_assistant_report", "_all_outstanding_delegated")}
        self._orig_jd = {n: getattr(jd, n) for n in ("parsed_session", "load_goals", "load_goals_shared_or_fault", "_segs",
                                                     "plan_units", "nudge_redundant")}
        self._orig_backend = km.Sessions.backend_for
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="", **k: False
        km._backend_queued = lambda sid: False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a, **k: False
        km._closer_settled = lambda *a: True
        km._revivers_pending = lambda *a: ""
        km._all_outstanding_delegated = lambda nodes, gid: False
        km._pending_ops = {}
        jd._segs = lambda tn, store: []
        jd.plan_units = lambda session, store, **kw: []   # the callers pass lazy_text (T396)
        uid = "u-t1"
        turns = [{"id": "t1", "t": self.ARM_T, "end": self.ARM_T + 60, "ended": True, "trigger": {"uuid": uid},
                  "atoms": [{"uuid": uid, "type": "user", "author": "human", "t": self.ARM_T}]}]
        jd.parsed_session = lambda sid, paths, now: {"turns": turns}
        self.store = None
        jd.load_goals = lambda sid: self.store
        jd.load_goals_shared_or_fault = lambda sid: (self.store, None)
        km._last_assistant_report = lambda path, cap=4000: ("working through the queue", self.ARM_T + 50)
        jd.nudge_redundant = lambda gtxt, recent: False
        self.sent = []
        test = self

        class FakeBackend:
            def send(self, sid, body):
                test.sent.append(body)
        km.Sessions.backend_for = staticmethod(lambda sid: FakeBackend())

    def tearDown(self):
        for n, v in self._orig_km.items():
            setattr(km, n, v)
        for n, v in self._orig_jd.items():
            setattr(jd, n, v)
        km.Sessions.backend_for = self._orig_backend
        jd.STATE = self.saved_state
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _store(self, **top_kw):
        top = {"id": self.G1, "text": "Review notes via /%s" % SKILL, "parentId": None, "nodeComplete": False,
               "blocked": False, "cleared": False, "trail": [], "t": self.ARM_T - 100, "mt": self.ARM_T - 100,
               "log": [], "promptUuid": "u2"}
        top.update(top_kw)
        step = {"id": self.G1 + "s", "text": "Read the retry notes", "parentId": self.G1, "nodeComplete": False,
                "blocked": False, "cleared": False, "trail": [], "t": self.ARM_T - 50, "mt": self.ARM_T - 50, "log": []}
        return {"rompUuid": SID, "seq": 2, "placements": {}, "status": {}, "confirming": [],
                "nodes": {self.G1: top, self.G1 + "s": step}}

    def _tick(self):
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({"enabled": True, "nudged": {}}))
        rows_p = jd.STATE / "nudge-events.jsonl"
        if rows_p.exists():
            rows_p.unlink()                        # each tick reads its own rows only
        km._autonudge_cache.clear()
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        km._auto_nudge_session({"sid": SID, "path": "/nonexistent.jsonl"}, NOW, {}, nudged, {})
        data = km._auto_nudge_data()
        rows = [json.loads(l) for l in rows_p.read_text().splitlines()] if rows_p.exists() else []
        return {"sent": list(self.sent), "nudged": self.G1 in (data.get("nudged") or {}),
                "deferred": self.G1 in (data.get("deferred") or {}), "rows": [r.get("verdict") for r in rows if r.get("gid") == self.G1]}

    def test_a_skill_load_top_with_an_open_step_is_never_nudged(self):
        self.store = self._store()                      # the control: an ordinary working top with an open step
        control = self._tick()
        self.assertTrue(control["sent"] or control["nudged"] or control["deferred"] or control["rows"],
                        "the harness walks the control top: %r" % control)
        self.sent.clear()
        self.store = self._store(**STAMP)               # the same top, stamped as the harness's skill load
        stamped = self._tick()
        self.assertEqual(stamped, {"sent": [], "nudged": False, "deferred": False, "rows": []},
                         "nothing asked, held or logged about a skill-load top")


if __name__ == "__main__":
    unittest.main()
