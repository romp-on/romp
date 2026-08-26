#!/usr/bin/env python3
"""GET /views + POST /tag (the user 2026-08-23, manager/worker workflow): the worker roster IS a
session tag, so an agent reads the views blob over GET /views and keeps ONE tag current over
POST /tag. NOT the setTimelineViews WS op re-exposed — that op replaces the whole blob, and an
agent replaying a stale read would clobber the active view and every tag it never looked at;
/tag is a targeted merge on one NAMED tag (_edit_tag): live names resolve to sids, opaque
ids (dead sids, host-prefixed remote ids) pass through verbatim, unknown names refuse loudly.
Drives the REAL Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel_gr", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "22222222-3333-4444-5555-666666666666"
DEAD = "33333333-4444-5555-6666-777777777777"                        # a sid no live session answers to
REMOTE = "TESTHOST:11111111-2222-3333-4444-555555555555"             # a host-prefixed remote id


class TagRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = km.jd.STATE
        km.jd.STATE = Path(self.td.name)
        km._flags_cache.clear()
        self._saved = (km._tmux_sessions, km._live_names, km._mark_views_dirty)
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: {"web": SID, "api": SID2}
        self.dirty = []                                       # the routes must poke the views push
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        (km._tmux_sessions, km._live_names, km._mark_views_dirty) = self._saved
        km.jd.STATE = self._state
        km._flags_cache.clear()
        self.td.cleanup()

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/tag" % self.port, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def _views(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/views" % self.port,
            headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def test_get_views_serves_the_normalized_default(self):
        st, v = self._views()
        self.assertEqual(st, 200)
        self.assertEqual(v, {"active": "all", "tags": [],
                             "actives": {"chat": {"all": True}, "timeline": {"all": True}, "outline": {"all": True}}})   # per-surface lenses (2026-08-25)

    def test_create_resolves_a_live_name_and_mints_the_ui_id_shape(self):
        st, r = self._post({"name": "pool", "add": ["web"]})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok"), r)
        g = r.get("tag") or {}
        self.assertRegex(g.get("id") or "", r"^g[0-9a-z]+$",
                         "the UI's Date.now-base36 mint shape — one id shape either birthplace")
        self.assertEqual(g.get("name"), "pool")
        self.assertEqual(g.get("members"), [SID], "the live NAME resolved to its sid")
        self.assertTrue(self.dirty, "a successful edit marks views dirty — the dashboards' repaint signal")

    def test_sids_and_remote_ids_pass_through_verbatim(self):
        st, r = self._post({"name": "mixed", "add": [DEAD, REMOTE]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["tag"]["members"], sorted([DEAD, REMOTE]),
                         "opaque ids stored as sent — the blob's own contract")

    def test_remove_drops_a_member_even_a_dead_one_by_sid(self):
        st, r = self._post({"name": "pool", "add": ["web", DEAD]})
        self.assertEqual(r["tag"]["members"], sorted([SID, DEAD]))
        st, r = self._post({"name": "pool", "remove": [DEAD]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["tag"]["members"], [SID], "a dead member goes by sid")

    def test_color_is_stored_on_the_tag(self):
        self._post({"name": "pool", "add": ["web"]})
        st, r = self._post({"name": "pool", "color": "#DD42FF"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["tag"]["color"], "#DD42FF")
        self.assertEqual(r["tag"]["members"], [SID], "a color edit does not clobber membership")

    def test_delete_removes_the_tag_and_the_active_view_falls_back(self):
        gid = "g1a2b3c"
        km._set_timeline_views({"active": gid, "hidden": [],
                                "tags": [{"id": gid, "name": "pool", "color": "",
                                            "members": [SID]}]})
        km._flags_cache.clear()
        st, v = self._views()
        self.assertEqual(v["active"], gid, "precondition: the tag IS the active view")
        st, r = self._post({"name": "pool", "delete": True})
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("deleted"))
        st, v = self._views()
        self.assertEqual(v["tags"], [])
        self.assertEqual(v["active"], "all", "the normalizer falls the orphaned active back to all")

    def test_an_unknown_add_name_refuses_loudly_and_writes_nothing(self):
        st, r = self._post({"name": "pool", "add": ["ghost"]})
        self.assertFalse(r.get("ok"))
        self.assertIn("no live session named", r.get("error") or "")
        st, v = self._views()
        self.assertEqual(v["tags"], [], "a refused edit writes nothing — no member no session matches")

    def test_two_same_named_tags_refuse_any_edit(self):
        km._set_timeline_views({"active": "all", "hidden": [], "tags": [
            {"id": "g1", "name": "dup", "color": "", "members": []},
            {"id": "g2", "name": "dup", "color": "", "members": []}]})
        km._flags_cache.clear()
        st, r = self._post({"name": "dup", "add": ["web"]})
        self.assertFalse(r.get("ok"))
        self.assertIn("rename one in the dashboard first", r.get("error") or "")

    def test_the_tag_cap_refuses_a_33rd_instead_of_silently_dropping_it(self):
        tags33 = [{"id": "g%02d" % i, "name": "grp%02d" % i, "color": "", "members": []}
                  for i in range(32)]
        km._set_timeline_views({"active": "all", "hidden": [], "tags": tags33})
        km._flags_cache.clear()
        st, r = self._post({"name": "overflow", "add": []})
        self.assertFalse(r.get("ok"))
        self.assertIn("caps at 32", r.get("error") or "",
                      "the normalizer would drop the appended 33rd SILENTLY — the route must refuse")
        st, v = self._views()
        self.assertEqual(len(v["tags"]), 32)
        self.assertNotIn("overflow", [g["name"] for g in v["tags"]])

    def test_back_to_back_creates_mint_distinct_ids_and_delete_hits_only_its_tag(self):
        st, ra = self._post({"name": "alpha"})
        st, rb = self._post({"name": "beta"})
        ida, idb = ra["tag"]["id"], rb["tag"]["id"]
        self.assertNotEqual(ida, idb, "same-millisecond creates must not share an id")
        st, r = self._post({"name": "alpha", "delete": True})
        self.assertTrue(r.get("ok"), r)
        st, v = self._views()
        self.assertEqual([g["id"] for g in v["tags"]], [idb],
                         "delete filters by id, so a shared id would have taken beta with it")

    def test_a_long_name_is_clamped_at_entry_so_it_stays_addressable(self):
        long_name = "x" * 44
        self._post({"name": long_name, "add": ["web"]})
        st, r = self._post({"name": long_name, "add": ["api"]})
        self.assertTrue(r.get("ok"), r)
        st, v = self._views()
        self.assertEqual(len(v["tags"]), 1,
                         "the raw name must address the stored (clamped) tag — never mint a duplicate")
        self.assertEqual(v["tags"][0]["name"], "x" * 40)
        self.assertEqual(v["tags"][0]["members"],
                         [{"host": "", "sid": s} for s in sorted([SID, SID2])],
                         "stored members are canonical pairs (federation v0)")

    def test_a_host_prefixed_NAME_refuses_like_any_unknown_name(self):
        st, r = self._post({"name": "pool", "add": ["TESTHOST:exp-ghost"]})
        self.assertFalse(r.get("ok"), "host:NAME is not an id — storing it would never match a session")
        self.assertIn("no live session named", r.get("error") or "")

    def test_missing_name_is_a_400(self):
        st, r = self._post({"add": ["web"]})
        self.assertEqual(st, 400)

    def test_an_edit_merges_and_never_clobbers_the_rest_of_the_blob(self):
        GB = {"id": "gb", "name": "beta", "color": "#DD42FF", "members": ["s2"]}
        km._set_timeline_views({"active": "gb", "tags": [
            {"id": "ga", "name": "alpha", "color": "", "members": ["s1"]}, GB]})
        km._flags_cache.clear()
        st, r = self._post({"name": "alpha", "add": ["api"]})
        self.assertTrue(r.get("ok"), r)
        st, v = self._views()
        self.assertEqual(v["active"], "gb", "the active view survives the merge")
        self.assertNotIn("hidden", v, "the hidden key is retired (2026-08-24) — never re-minted by an edit")
        want = dict(GB, members=[{"host": "", "sid": "s2"}])
        self.assertEqual([g for g in v["tags"] if g["id"] == "gb"], [want],
                         "the tag the edit never looked at is untouched (stored as pairs)")
        alpha = next(g for g in v["tags"] if g["id"] == "ga")
        self.assertEqual(alpha["members"], [{"host": "", "sid": s} for s in sorted(["s1", SID2])])


class HostForward(TagRoute):
    """POST /tag {"host": ...} — tag federation v0's edit path: the edit targets an ATTACHED
    kernel's store through the tunnel it already holds (Model A home-kernel ownership). The body
    forwards minus `host`; the TARGET resolves member names against ITS sessions; failures refuse
    loudly — an unreachable kernel must never silently no-op an edit."""

    def setUp(self):
        super().setUp()
        self._remotes_saved = dict(km._remotes)
        self._fwd_saved = km._remote_forward
        km._remotes.clear()
        km._remotes["alpha"] = {"host": "alpha", "status": "up"}
        km._remotes["down1"] = {"host": "down1", "status": "down"}
        self.forwarded = []
        km._remote_forward = lambda r, path, body: (self.forwarded.append((r["host"], path, body))
                                                    or {"ok": True, "tag": {"name": body.get("name")}})

    def tearDown(self):
        km._remotes.clear(); km._remotes.update(self._remotes_saved)
        km._remote_forward = self._fwd_saved
        super().tearDown()

    def test_host_edit_forwards_to_the_target_kernel_minus_the_host_key(self):
        st, r = self._post({"name": "team", "host": "alpha", "add": ["web"], "color": "#DD42FF"})
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(self.forwarded, [("alpha", "/tag",
                                           {"name": "team", "add": ["web"], "color": "#DD42FF"})],
                         "the target resolves names itself — the body forwards verbatim, minus host")
        self.assertEqual(km._timeline_views()["tags"], [],
                         "nothing lands on THIS kernel's store — the edit belongs to alpha")

    def test_unknown_and_down_hosts_refuse_loudly(self):
        st, r = self._post({"name": "team", "host": "ghost", "add": ["web"]})
        self.assertFalse(r["ok"]); self.assertIn('no attached kernel named "ghost"', r["error"])
        st, r = self._post({"name": "team", "host": "down1", "add": ["web"]})
        self.assertFalse(r["ok"]); self.assertIn("not reachable", r["error"])
        self.assertEqual(self.forwarded, [], "no forward is attempted either way")

    def test_a_dead_forward_surfaces_never_silently_noops(self):
        km._remote_forward = lambda r, path, body: None
        st, r = self._post({"name": "team", "host": "alpha", "add": ["web"]})
        self.assertFalse(r["ok"]); self.assertIn("never landed", r["error"])


class RenameAndHomeFrame(TagRoute):
    """Federation v1: /tag gains rename (collision-refusing), and a bare sid routed here from a
    THIRD kernel resolves into THIS kernel's own frame — viewer C adding kernel-B's session to our
    tag cannot know our name for B, but sids are global, so we look the sid up in our remotes'
    cached session lists and store the canonical pair ourselves."""

    def test_rename_lands_and_a_collision_refuses(self):
        self._post({"name": "alpha", "add": []})
        self._post({"name": "beta", "add": []})
        st, r = self._post({"name": "alpha", "rename": "gamma"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["tag"]["name"], "gamma")
        self.assertEqual(sorted(t["name"] for t in km._timeline_views()["tags"]), ["beta", "gamma"])
        st, r = self._post({"name": "gamma", "rename": "beta"})
        self.assertFalse(r["ok"])
        self.assertIn('a tag named "beta" already exists', r["error"])

    def test_a_third_kernels_bare_sid_lands_in_OUR_frame(self):
        # TESTHOST-A (this kernel) holds the tag; TESTHOST-B owns the session; viewer TESTHOST-C
        # routed the edit here with the bare sid tail — we know that sid as TESTHOST-B's
        bsid = "44444444-5555-6666-7777-888888888888"
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            km._remotes["TESTHOST-B"] = {"host": "TESTHOST-B", "status": "up", "sids": [bsid]}
            st, r = self._post({"name": "team", "add": [bsid]})
            self.assertTrue(r["ok"])
            self.assertEqual(km._timeline_views()["tags"][0]["members"],
                             [{"host": "TESTHOST-B", "sid": bsid}],
                             "the canonical pair carries OUR label for B — never the viewer's")
            # …while a sid nobody knows stays bare (legacy behavior: inert until known)
            ghost = "99999999-aaaa-bbbb-cccc-dddddddddddd"
            self._post({"name": "team", "add": [ghost]})
            self.assertIn({"host": "", "sid": ghost}, km._timeline_views()["tags"][0]["members"])
        finally:
            km._remotes.clear(); km._remotes.update(saved)


class EditTagOpPins(unittest.TestCase):
    """The WS op the dialog rides (federation v1) — source pins: only the BARE sid tail crosses
    kernels (this viewer's host labels mean nothing on the owner), the failure pushes a LOUD
    tagEditFailed to the asking dashboard, and a landed edit marks views dirty either way."""

    def test_the_op_tails_ids_forwards_and_fails_loudly(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('msg.get("type") == "editTag"', src)
        self.assertIn('tail = lambda x: x.rsplit(":", 1)[-1]', src)
        self.assertIn('ans, err = _forward_tag_edit(host, body)', src)
        self.assertIn('{"type": "tagEditFailed", "host": host, "name": nm,', src)
        self.assertIn('(client or {}).get("wid") or ""', src, "the refusal goes to the ASKING dashboard")


class ForwardHelper(TagRoute):
    """_forward_tag_edit — the one channel every remote edit rides (romp tag --host, the dialog's
    editTag op): loud refusals, response passthrough, and the FAST ECHO (a landed edit drops the
    owner's poll gate and refreshes its cached views inline, so the optimistic copy reconciles
    within a push, not a poll period)."""

    def setUp(self):
        super().setUp()
        self._saved = (dict(km._remotes), km._remote_forward, km._poll_remote_views)
        km._remotes.clear()
        km._remotes["alpha"] = {"host": "alpha", "status": "up", "_views_at": 12345.0,
                                "views": {"tags": []}}
        self.polled = []
        km._remote_forward = lambda r, path, body: {"ok": True, "tag": {"name": body.get("name")}}
        km._poll_remote_views = lambda r: (self.polled.append(r["host"]) or {"tags": [{"id": "g1", "name": "team", "members": []}]})

    def tearDown(self):
        km._remotes.clear(); km._remotes.update(self._saved[0])
        km._remote_forward, km._poll_remote_views = self._saved[1], self._saved[2]
        super().tearDown()

    def test_a_landed_edit_echoes_fast(self):
        ans, err = km._forward_tag_edit("alpha", {"name": "team", "add": []})
        self.assertIsNone(err)
        self.assertTrue(ans["ok"])
        self.assertEqual(self.polled, ["alpha"], "the owner's views re-poll inline — the fast echo")
        self.assertNotIn("_views_at", km._remotes["alpha"], "…and the poll gate stays dropped for the loop")
        self.assertEqual(km._remotes["alpha"]["views"]["tags"][0]["name"], "team")

    def test_refusals_stay_loud(self):
        self.assertEqual(km._forward_tag_edit("ghost", {"name": "t"})[1],
                         'no attached kernel named "ghost" (see the network panel)')
        km._remotes["alpha"]["status"] = "down"
        self.assertIn("not reachable", km._forward_tag_edit("alpha", {"name": "t"})[1])
        km._remotes["alpha"]["status"] = "up"
        km._remote_forward = lambda r, path, body: None
        self.assertIn("never landed", km._forward_tag_edit("alpha", {"name": "t"})[1])


class GroupAliasSurvives(TagRoute):
    """The pre-rename surface (same-day rename, 2026-08-23): an un-updated remote's bin/romp still
    POSTs /group and reads the "group" response key — both stay as quiet aliases of /tag."""

    def test_post_group_still_merges_and_answers_with_both_keys(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/group" % self.port, data=json.dumps({"name": "legacy", "add": ["web"]}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["tag"]["name"], "legacy")
        self.assertEqual(resp["group"], resp["tag"], "the pre-rename key mirrors the tag row")
        self.assertEqual(resp["tag"]["members"], [SID], "a live name resolved through the alias route too")


if __name__ == "__main__":
    unittest.main()
