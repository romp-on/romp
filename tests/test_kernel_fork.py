#!/usr/bin/env python3
"""Fork a session (the user 2026-08-13): a NEW parallel session branches from the parent's conversation —
from just before a chosen user message (the chat's fork button; the same _rewind_target resolution the
edit/delete rewind uses) or from the tip — and the parent is untouched.

The load-bearing half is STORE HYGIENE: the fork's transcript carries the parent's history VERBATIM
(uuids + timestamps survive the CLI's fork copy — verified live 2026-08-13), and an unseeded new sid
would re-judge all of it as fresh work (the v3 replay storm: every copied prompt re-minted as a card,
every turn re-captioned). _seed_fork_stores runs BEFORE the session becomes discoverable and writes:
  1. an episode seed+boundary → episode_floor(new) = the cut record's t (the planner's retire gate);
  2. a transform-copy of the parent's captions (no Haiku re-caption storm);
  3. a goal store born with the parent's placements SEALED (value None) — the courier's cover.
run_courier additionally grew its own episode-floor guard: a fork's copied history is the first shape
that leaves OLD peer segments visible to it (a /clear's null-rooted head drops them from the parse).

SYNTHETIC fixtures only (placeholder UUIDs, invented text)."""
import contextlib
import errno
import json
import os
import re
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
km = load_source("romp_kernel_fork", os.path.join(BIN, "romp-kernel"))
jd = km.jd

PARENT = "11111111-2222-3333-4444-555555555555"
NEWSID = "99999999-8888-7777-6666-555555555555"
T_U1, T_A1, T_U2, T_A2 = 1781100000, 1781100010, 1781100100, 1781100110


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def write_transcript(path):
    recs = [uline(T_U1, "set up the api", "u1"),
            aline(T_A1, "done — the api is up", "a1", "u1"),
            uline(T_U2, "now add the tests", "u2", "a1"),
            aline(T_A2, "tests added", "a2", "u2")]
    Path(path).write_text("\n".join(json.dumps(r) for r in recs) + "\n")


class ForkStoreSeeding(unittest.TestCase):
    """_seed_fork_stores: the three writes, keyed to the cut."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, PARENT + ".jsonl")
        write_transcript(self.path)

    def tearDown(self):
        for d in (jd.EPIDIR, jd.CAPDIR, jd.GOALDIR):
            for f in Path(d).glob("*"):
                f.unlink()
        self.td.cleanup()

    def test_episode_floor_lands_on_the_cut(self):
        err = km._seed_fork_stores(PARENT, NEWSID, self.path, "a1")   # fork from before u2
        self.assertIsNone(err)
        rows = jd.episode_rows(NEWSID)
        self.assertEqual([r["head"] for r in rows], ["u1", "a1"],
                         "seed = the conversation ROOT (dedups the boundary tick's sighting); boundary = the cut")
        self.assertEqual(rows[0]["fsid"], PARENT, "the seed names the file the fork copies from")
        self.assertEqual(rows[1]["fsid"], NEWSID, "the boundary is the fork's own (pinned) fsid")
        self.assertEqual(jd.episode_floor(NEWSID), T_A1,
                         "floor = the cut record's t — the planner retires every copied unit below it")

    def test_fork_from_tip_floors_at_the_leaf(self):
        self.assertIsNone(km._seed_fork_stores(PARENT, NEWSID, self.path, ""))
        self.assertEqual(jd.episode_floor(NEWSID), T_A2)

    def test_captions_transform_copy(self):
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (jd.CAPDIR / (PARENT + ".jsonl")).write_text("\n".join([
            json.dumps({"id": "%s:%d:aaaa" % (PARENT, T_U1), "cap": "set up the api"}),
            json.dumps({"id": "%s:%d:bbbb" % (PARENT, T_U2), "cap": "add the tests"}),   # after the cut
            json.dumps({"id": "%s:%d:cccc" % (PARENT, T_U1), "cap": "working…", "live": True}),
            json.dumps({"id": "othersid:%d:dddd" % T_U1, "cap": "not ours"}),
        ]) + "\n")
        self.assertIsNone(km._seed_fork_stores(PARENT, NEWSID, self.path, "a1"))
        got = [json.loads(l) for l in (jd.CAPDIR / (NEWSID + ".jsonl")).read_text().splitlines()]
        self.assertEqual([o["id"] for o in got], ["%s:%d:aaaa" % (NEWSID, T_U1)],
                         "kept: settled rows at/before the cut, resid'd; dropped: post-cut, live, foreign")
        self.assertEqual(got[0]["cap"], "set up the api")

    def test_placements_arrive_sealed_and_no_nodes_copy(self):
        pstore = jd.load_goals(PARENT)
        pstore["placements"] = {"%s:%d:aaaa" % (PARENT, T_U1): "g1",
                                "%s:%d:bbbb#p" % (PARENT, T_U2): None}
        pstore["nodes"] = {"%s:g1" % PARENT: {"text": "a card the fork must NOT inherit"}}
        jd.save_goals(PARENT, pstore)
        self.assertIsNone(km._seed_fork_stores(PARENT, NEWSID, self.path, "a1"))
        store = jd.load_goals(NEWSID)
        self.assertEqual(store["placements"],
                         {"%s:%d:aaaa" % (NEWSID, T_U1): None, "%s:%d:bbbb#p" % (NEWSID, T_U2): None},
                         "every parent placement arrives resid'd and SEALED (processed, no goal)")
        self.assertEqual(store["nodes"], {}, "no cards copy — they would duplicate across both boards")

    def test_empty_transcript_fails_loudly(self):
        empty = os.path.join(self.td.name, "empty.jsonl")
        Path(empty).write_text("")
        self.assertIn("nothing to fork", km._seed_fork_stores(PARENT, NEWSID, empty, "") or "")


@contextlib.contextmanager
def _reads_fault(target):
    """Fail every byte read of ONE path with an EIO for the duration of the block (the views store's proved
    reader reads bytes); everything else reads normally."""
    real_rb, real_rt = Path.read_bytes, Path.read_text
    tgt = str(target)

    def rb(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rb(self, *a, **k)

    def rt(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rt(self, *a, **k)
    Path.read_bytes, Path.read_text = rb, rt
    try:
        yield
    finally:
        Path.read_bytes, Path.read_text = real_rb, real_rt


class _FakeForkBackend:
    def __init__(self):
        self.events = []

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, opener=""):
        self.events.append(("fork", name, parent_sid, cut_uuid, sid))
        self.opener = opener
        return sid

    def connect(self, sid):
        self.events.append(("connect", sid))


class ForkSessionOp(unittest.TestCase):
    """_fork_session: validation, the rewind-family cut resolution, and seeding BEFORE the backend."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, PARENT + ".jsonl")
        write_transcript(self.path)
        self.be = _FakeForkBackend()
        self.saved = (km.Sessions.backend_for, km._sdk_ready, km._sessions, km._pick_identity_color,
                      km._reveal_chat_for, km._mark_views_dirty, km._push_session_now, km._seed_fork_stores,
                      km._live_map)
        km._live_map = lambda: {}   # the fork door's live snapshot (names reserved atomically) — never the box's live map
        km.Sessions.backend_for = lambda sid: self.be
        km._sdk_ready = lambda: True
        km._sessions = lambda now: [{"sid": PARENT, "path": self.path}]
        km._pick_identity_color = lambda: ("#123456", "#ffffff")
        km._reveal_chat_for = lambda c, m: None
        km._mark_views_dirty = lambda: None
        km._push_session_now = lambda sid: None
        self._real_seed = km._seed_fork_stores
        km._seed_fork_stores = lambda *a: self.be.events.append(("seed",) + a[:2]) or None
        # a PRIVATE views store (the goal-store fixture rule, for timeline-views.json): every kernel
        # module in one pytest worker shares the one romp_judge module, hence one jd.STATE, and the
        # store's stale-writer guard would keep another module's newer-stamped tags over this test's
        self.vtd = tempfile.TemporaryDirectory()
        self._saved_state = jd.STATE
        jd.STATE = Path(self.vtd.name)
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self._saved_state
        km._flags_cache.clear()
        self.vtd.cleanup()
        (km.Sessions.backend_for, km._sdk_ready, km._sessions, km._pick_identity_color,
         km._reveal_chat_for, km._mark_views_dirty, km._push_session_now, km._seed_fork_stores,
         km._live_map) = self.saved
        for d in (jd.EPIDIR, jd.CAPDIR, jd.GOALDIR):
            for f in Path(d).glob("*"):
                f.unlink()
        self.td.cleanup()

    def test_bad_name_refused(self):
        self.assertIn("letters, digits", km._fork_session(PARENT, "", "bad name!") or "")
        self.assertEqual(self.be.events, [])

    def test_a_backend_without_fork_is_refused(self):
        km.Sessions.backend_for = lambda sid: object()   # no .fork
        self.assertIn("needs a Claude Code session", km._fork_session(PARENT, "", "api-fork") or "")   # the backend's name since T288

    def test_seeding_precedes_the_backend_and_the_cut_resolves_like_a_rewind(self):
        self.assertIsNone(km._fork_session(PARENT, "u2", "api-fork"))
        kinds = [e[0] for e in self.be.events]
        self.assertEqual(kinds, ["seed", "fork", "connect"],
                         "stores are seeded BEFORE the names/ entry exists (discoverability)")
        fork = next(e for e in self.be.events if e[0] == "fork")
        self.assertEqual(fork[3], "a1", "forking 'before u2' cuts at its nearest message ancestor — "
                                        "the same _rewind_target the edit/delete rewind uses")
        self.assertEqual(fork[4], self.be.events[0][2], "the backend gets the sid the seeds were written for")

    def test_fork_from_tip_passes_no_cut(self):
        self.assertIsNone(km._fork_session(PARENT, "", "api-fork"))
        fork = next(e for e in self.be.events if e[0] == "fork")
        self.assertEqual(fork[3], "", "no message uuid → the whole conversation")

    def test_the_fork_door_arms_the_line_that_says_what_a_fork_is(self):
        # 2026-09-24: a fork holds the parent's whole history in the parent's voice; its first message now
        # opens by saying it was split off and must leave the original's unfinished work alone. The eager
        # connect starts the CLI with no message, so the door arms the line for the first one romp sends.
        self.assertIsNone(km._fork_session(PARENT, "u2", "api-fork"))
        self.assertEqual(self.be.opener, km._FORK_FRAME)
        self.assertIn("split this conversation off from the original", km._FORK_FRAME)
        self.assertIn("don't pick up its unfinished tasks or background work", km._FORK_FRAME)
        self.assertNotEqual(km._FORK_FRAME, km._THREAD_FRAME, "a fork is not a side question about a passage")

    def test_seed_failure_aborts_the_fork(self):
        km._seed_fork_stores = lambda *a: "fork not created — seeding its judge state failed: boom"
        self.assertIn("seeding", km._fork_session(PARENT, "", "api-fork") or "")
        self.assertEqual([e[0] for e in self.be.events], [],
                         "an unprotected fork must never be created (the replay storm)")

    # ── the parent idle past discover's 48h horizon (the user 2026-09-14) ──────────────────────────
    # _sessions(now) reaches back only the caption window, so a 4-day-idle session — tab and chat right
    # there via _alive_sessions' wide walk — was refused as "no transcript for this session yet" by the
    # fork door (and the comment/rewind doors). _session_row resolves it the way build_session does.

    def _stub(self, name, value):
        self.addCleanup(setattr, km, name, getattr(km, name))
        setattr(km, name, value)

    def _sdk_owner(self, *sids):
        return type("Owner", (), {"owns": staticmethod(lambda sid: sid in sids)})()

    def test_an_sdk_parent_idle_past_the_discovery_window_still_forks(self):
        km._sessions = lambda now: []                       # the 48h walk has forgotten it
        self._stub("_sdk", lambda: self._sdk_owner(PARENT))
        real_reg = km._thread_reg
        self._stub("_thread_reg", lambda sid: ({"name": "parent", "lastSid": PARENT, "cwd": self.td.name}
                                               if sid == PARENT else real_reg(sid)))
        self._stub("_thread_transcript_path", lambda reg, sid: self.path)   # cwd + lastSid → the CURRENT transcript
        self._stub("_name_of", lambda sid: "parent" if sid == PARENT else "")
        row = km._session_row(PARENT, T_A2 + 5 * 86400)
        self.assertEqual((row["sid"], row["name"], row["path"], row["anchor"]), (PARENT, "parent", self.path, PARENT),
                         "the registry names the row: same shape _sessions hands out, no directory walk")
        self.assertIsNone(km._fork_session(PARENT, "u2", "api-fork"))
        self.assertEqual([e[0] for e in self.be.events], ["seed", "fork", "connect"])
        self.assertEqual(next(e for e in self.be.events if e[0] == "fork")[3], "a1",
                         "the cut resolved against the transcript the registry pointed at")

    def test_a_parent_no_sdk_registry_names_resolves_through_the_wide_walk(self):
        # a session the SDK backend does not own (a Codex one): the same cached wide walk _alive_sessions
        # keeps its tab alive through
        km._sessions = lambda now: []
        self._stub("_sdk", lambda: None)
        self._stub("_discover_wide", lambda now, window: {PARENT: (PARENT, Path(self.path), PARENT, "parent")})
        self.assertIsNone(km._fork_session(PARENT, "", "api-fork"))
        self.assertEqual([e[0] for e in self.be.events], ["seed", "fork", "connect"])

    def test_an_sdk_session_that_never_ran_is_still_refused(self):
        # a registry without a transcript file is the genuine "no transcript yet" — the refusal keeps its name
        km._sessions = lambda now: []
        self._stub("_sdk", lambda: self._sdk_owner(PARENT))
        self._stub("_thread_reg", lambda sid: {"name": "parent", "cwd": self.td.name})
        self._stub("_thread_transcript_path", lambda reg, sid: os.path.join(self.td.name, "never-ran.jsonl"))
        self._stub("_discover_wide", lambda now, window: {})
        self.assertIn("no transcript", km._fork_session(PARENT, "", "api-fork") or "")
        self.assertEqual(self.be.events, [])

    def test_a_session_with_no_transcript_anywhere_is_still_refused(self):
        km._sessions = lambda now: []
        self._stub("_sdk", lambda: None)
        self._stub("_discover_wide", lambda now, window: {})
        self.assertIn("no transcript", km._fork_session(PARENT, "", "api-fork") or "")
        self.assertEqual(self.be.events, [])

    def test_the_fork_inherits_the_parents_tags_before_its_first_push(self):
        # tab groups on tags (the user 2026-09-04): a fork joins every tag holding its parent, at the
        # creation event — the parent keeps its tags — and BEFORE connect/push, so the first tabOrder
        # frame (which carries the views blob) already sections the new tab under its group
        km._flags_cache.clear()
        km._set_timeline_views({"active": "all", "tags": [
            {"id": "g1", "name": "pool", "members": [PARENT]},
            {"id": "g2", "name": "other", "members": ["someone-else"]}]})
        seen_at_connect = []
        real_connect = self.be.connect
        def connect(sid):
            seen_at_connect.append([m["sid"] for m in km._timeline_views()["tags"][0]["members"]])
            return real_connect(sid)
        self.be.connect = connect
        self.assertIsNone(km._fork_session(PARENT, "", "api-fork"))
        fork_sid = next(e for e in self.be.events if e[0] == "fork")[4]
        v = km._timeline_views()
        pool = next(t for t in v["tags"] if t["name"] == "pool")
        self.assertEqual(sorted(m["sid"] for m in pool["members"]), sorted([PARENT, fork_sid]),
                         "the fork joined pool; the parent kept it")
        self.assertEqual([m["sid"] for m in next(t for t in v["tags"] if t["name"] == "other")["members"]],
                         ["someone-else"], "a tag the parent is not in is untouched")
        self.assertIn(fork_sid, seen_at_connect[0], "membership landed BEFORE connect — ahead of the direct push")

    def test_an_untagged_parents_fork_lands_untagged(self):
        km._flags_cache.clear()
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": ["someone-else"]}]})
        self.assertIsNone(km._fork_session(PARENT, "", "api-fork"))
        fork_sid = next(e for e in self.be.events if e[0] == "fork")[4]
        self.assertNotIn(fork_sid, [m["sid"] for m in km._timeline_views()["tags"][0]["members"]])

    def test_a_faulting_views_store_leaves_the_fork_standing_and_says_it_did_not_inherit(self):
        # the views store's proved read: a fault under the inherit RAISES (never an empty parent read off a
        # fabricated store), and the fork site's boundary keeps the spawn -- the session already exists -- and
        # says what did not happen: one refused-kind notice naming the child, nothing written to the store
        km._flags_cache.clear()
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [PARENT]}]})
        km._flags_cache.clear()                      # a cold display cache: the fault must be met by the READ, not bypassed by a hit
        p = jd.STATE / "timeline-views.json"
        before = p.read_bytes()
        notices, saved = [], km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": notices.append((text, ok, kind))
        try:
            with _reads_fault(p):
                self.assertIsNone(km._fork_session(PARENT, "", "api-fork"), "the fork stands")
        finally:
            km._sync_notice = saved
        self.assertEqual([e[0] for e in self.be.events], ["seed", "fork", "connect"], "seeded, forked, connected as ever")
        self.assertEqual(p.read_bytes(), before, "nothing was written to the store")
        rows = [(t, k) for t, ok, k in notices if not ok]
        self.assertEqual(len(rows), 1, "one notice for the one thing that did not happen")
        self.assertIn('"api-fork" did not inherit the tags of', rows[0][0])
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", rows[0][0])
        self.assertEqual(rows[0][1], "refused")
        km._flags_cache.clear()
        fork_sid = next(e for e in self.be.events if e[0] == "fork")[4]
        self.assertEqual([m["sid"] for m in km._timeline_views()["tags"][0]["members"]], [PARENT],
                         "the parent keeps its tag; the fork is not in it (tag it again once the store reads)")
        self.assertNotEqual(fork_sid, PARENT)


class CourierEpisodeFloor(unittest.TestCase):
    """run_courier skips segments below the episode floor — a fork's copied history is the first
    shape that leaves OLD peer segments visible to it (mirrors the planner's pre-episode retire)."""

    RECIP = "11111111-2222-3333-4444-555555555555"
    SENDER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    MID = "1781100000.11111_22222.TESTHOST"
    T0 = 1781100000

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        (proj / munged).mkdir(parents=True)
        self.proj_dir = proj / munged
        names = td / "names"; names.mkdir()
        (names / self.RECIP).write_text("recip\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.MESSAGES, jd.ERRORS, jd.EPIDIR, jd.courier_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.EPIDIR = td / "episodes"
        jd.MESSAGES = td / "messages.jsonl"
        jd.ERRORS = td / "judge-errors.jsonl"
        jd.MESSAGES.write_text(json.dumps(
            {"t": self.T0 - 5, "ev": "sent", "id": self.MID, "from": "sender", "from_id": self.SENDER,
             "to_id": self.RECIP, "body": "what subnet is the new box on?"}) + "\n")
        self.calls = []
        jd.courier_llm = lambda *a, **k: self.calls.append(1) or '{"verdict": "delegating", "goal": 0, "text": "x"}'
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._discover_cache.clear()
        jd._postal_from_memo["key"] = None
        # the marker rides the comment-wrapped wire form (see test_courier_origin_host)
        recs = [uline(self.T0, "what subnet is the new box on?\n<!-- romp-msg-id: %s -->" % self.MID, "u1"),
                aline(self.T0 + 30, "It's on the flat /24.", "a1", "u1")]
        (self.proj_dir / (self.RECIP + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in recs) + "\n")

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.MESSAGES, jd.ERRORS, jd.EPIDIR, jd.courier_llm) = self.saved
        jd._postal_from_memo["key"] = None
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._discover_cache.clear()
        self.td.cleanup()

    def test_pre_floor_peer_segment_is_skipped(self):
        jd.append_episode(self.RECIP, "u0", "oldfsid", self.T0 - 100)   # seed
        jd.append_episode(self.RECIP, "cut", self.RECIP, self.T0 + 50)  # boundary → floor ABOVE the message
        jd.run_courier(now=self.T0 + 100)
        store = jd.load_goals(self.RECIP)
        self.assertEqual(store["nodes"], {}, "nothing planted from pre-episode history")
        self.assertEqual(store["placements"], {}, "not even placed — skipped outright, no model call")
        self.assertEqual(self.calls, [])

    def test_current_episode_peer_segment_still_plants(self):
        jd.append_episode(self.RECIP, "u0", "oldfsid", self.T0 - 100)
        jd.append_episode(self.RECIP, "cut", self.RECIP, self.T0 - 50)  # floor BELOW the message
        jd.run_courier(now=self.T0 + 100)
        store = jd.load_goals(self.RECIP)
        self.assertEqual(len(self.calls), 1, "an in-episode delegation still reaches the courier model")
        self.assertTrue(store["placements"], "and gets placed")


if __name__ == "__main__":
    unittest.main()
