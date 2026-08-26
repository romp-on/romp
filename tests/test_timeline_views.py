#!/usr/bin/env python3
"""Session views (the user 2026-08-18; TAG model 2026-08-23): one timeline-views.json blob under
STATE — {"active", "tags"} (the hidden set retired 2026-08-24, migrated into an "archived" tag) — deciding which sessions show on the timeline lanes AND the
chat tab strip. TWO built-in sentinels: "all" — the DEFAULT (2026-08-24) — shows every session
(literally everything since 2026-08-24); "untagged" keeps the old default's meaning (a TAG marks a SPECIALIZED
session, excluded from the untagged view and shown under its tag views). "all" used to MEAN
untagged, so reinterpreting it lands every legacy blob on the new All default. A tagged
session is a BACKGROUND session: still judged and carded, surfaced by the feed, the pickers, and
the "N more" cue. The legacy "groups" key (pre-rename files and un-updated panels) reads as tags.
Local-kernel persisted (a viewer display pref, not federated). These pin the storage helpers, the
visibility decision, the normalizer + migration, the churn heal, the WS op, the payload echoes,
and the reveal rule. Synthetic only."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_tv", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

G1 = {"id": "g1", "name": "pool", "color": "#DD42FF", "members": ["s2", "s3"]}


class TagOrderRoundTrip(unittest.TestCase):
    """The union DISPLAY order (the user 2026-08-25): a NAME list on the views blob, viewer-side —
    so a dragged remote-homed tag holds its position without any cross-kernel write. The normalizer
    passes it through (clamped, deduped); pre-order blobs round-trip without the key."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_order_survives_normalize_and_store(self):
        blob = {"active": "all", "tags": [G1], "tagOrder": ["experiments", "pool", "remotename"]}
        n = km._norm_timeline_views(blob)
        self.assertEqual(n["tagOrder"], ["experiments", "pool", "remotename"],
                         "names pass through unvalidated — a remote-homed name is unknowable here by design")
        km._set_timeline_views(blob)
        self.assertEqual(km._timeline_views()["tagOrder"], ["experiments", "pool", "remotename"],
                         "the drag persists: the stored blob re-reads with the order intact")
        self.assertEqual(km._views_client()["tagOrder"], ["experiments", "pool", "remotename"],
                         "…and the rendered blob every client holds carries it")

    def test_absent_stays_absent_and_junk_drops(self):
        n = km._norm_timeline_views({"active": "all", "tags": [G1]})
        self.assertNotIn("tagOrder", n, "pre-order blobs round-trip without the key")
        n = km._norm_timeline_views({"tags": [G1], "tagOrder": [3, "", None, "pool", "pool"]})
        self.assertEqual(n["tagOrder"], ["pool"], "junk entries drop quietly; duplicates collapse")
        n = km._norm_timeline_views({"tags": [G1], "tagOrder": "pool"})
        self.assertNotIn("tagOrder", n, "a wrong-typed order drops whole, never raises")


class TimelineViews(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_default_shows_everything(self):
        # fresh blobs open on "all" — and since 2026-08-24 that sentinel means truly-ALL, so a
        # legacy blob persisted when "all" meant untagged lands on the new default automatically
        v = km._timeline_views()
        self.assertEqual(v, {"active": "all", "tags": [],
                         "actives": {"chat": {"all": True}, "timeline": {"all": True}, "outline": {"all": True}}})   # per-surface lenses seed from the scalar (2026-08-25)
        self.assertTrue(km._view_visible(v, "anything"))

    def test_all_shows_every_session_and_untagged_the_tagless_ones(self):
        # ALL — the default — shows LITERALLY EVERYTHING (the user 2026-08-24, retiring the hidden
        # set outright: the tag system covers backgrounding); a legacy hidden key is ignored
        km._set_timeline_views({"active": "all", "hidden": ["s9"], "tags": [G1]})
        km._flags_cache.clear()
        v = km._views_client()   # _view_visible reads the RENDERED shape (string members + remoteTags)
        self.assertTrue(km._view_visible(v, "s9"), "a legacy hidden entry is IGNORED — nothing hides from All")
        self.assertTrue(km._view_visible(v, "s2"), "TAGGED → All still shows it")
        self.assertTrue(km._view_visible(v, "s1"), "untagged → shown")
        # the untagged view keeps the old default's meaning under its own sentinel (the user
        # 2026-08-23 TAG rule: tagging says "specialized — out of the main view")
        km._set_timeline_views({"active": "untagged", "hidden": ["s9"], "tags": [G1]})
        km._flags_cache.clear()
        v = km._views_client()
        self.assertEqual(v["active"], "untagged", "the sentinel survives the normalizer round-trip")
        self.assertTrue(km._view_visible(v, "s9"), "…and legacy hidden does not hide in untagged either")
        self.assertFalse(km._view_visible(v, "s2"), "TAGGED → out of the untagged view")
        self.assertTrue(km._view_visible(v, "s1"), "tagless → shown")
        km._set_timeline_views({"active": "g1", "hidden": ["s2"], "tags": [G1]})
        km._flags_cache.clear()
        v = km._views_client()
        self.assertTrue(km._view_visible(v, "s2"), "a tag view shows exactly its members")
        self.assertFalse(km._view_visible(v, "s1"), "…and nothing else")

    def test_legacy_groups_key_reads_as_tags(self):
        # a pre-rename timeline-views.json (or an un-updated Obsidian panel posting the whole blob)
        # must lose nothing on upgrade; when BOTH keys appear, "tags" is the authoritative one
        v = km._norm_timeline_views({"active": "g1", "hidden": [], "groups": [G1]})
        want = dict(G1, members=[{"host": "", "sid": "s2"}, {"host": "", "sid": "s3"}])
        self.assertEqual(v["tags"], [want], "the legacy key migrates in — string members become pairs")
        self.assertEqual(v["active"], "g1", "…and its active tag survives the read")
        self.assertNotIn("groups", v, "the normalized shape carries tags only")
        both = km._norm_timeline_views({"tags": [G1], "groups": [{"id": "gX", "name": "stale"}]})
        self.assertEqual([t["id"] for t in both["tags"]], ["g1"], "tags wins when both keys appear")

    def test_normalizer_drops_junk_and_falls_back_to_all(self):
        v = km._norm_timeline_views({"active": "ghost", "hidden": ["a", 7, "", "a"],
                                     "tags": [{"id": "g1", "name": "x" * 99, "members": ["m", 3]},
                                              {"noid": True}, "junk"]})
        self.assertEqual(km._norm_timeline_views({"hidden": 7, "tags": "nope"}),
                         {"active": "all", "tags": [],
                          "actives": {"chat": {"all": True}, "timeline": {"all": True}, "outline": {"all": True}}},
                         "wrong-TYPED fields drop instead of raising")
        self.assertEqual(km._norm_timeline_views({"tags": [{"id": "g", "members": 3}]})["tags"][0]["members"],
                         [], "a wrong-typed members list drops, never raises")
        self.assertEqual(v["active"], "all", "an active tag that does not exist falls back to all")
        self.assertEqual(km._norm_timeline_views({"active": "untagged"})["active"], "untagged",
                         "the untagged sentinel passes the whitelist — a rewrite here is SILENT and"
                         " reads as flicker after the client's optimistic hold expires")
        self.assertNotIn("hidden", v, "a legacy hidden key is dropped outright (retired 2026-08-24)")
        self.assertEqual(len(v["tags"]), 1)
        self.assertEqual(len(v["tags"][0]["name"]), km._VIEWS_MAX_NAME)
        self.assertEqual(v["tags"][0]["members"], [{"host": "", "sid": "m"}])

    def test_cache_invalidates_on_write(self):
        self.assertEqual(km._timeline_views()["tags"], [])
        km._set_timeline_views({"tags": [{"id": "g9", "name": "pool", "members": ["s9"]}]})
        self.assertEqual([t["id"] for t in km._timeline_views()["tags"]], ["g9"], "mtime+size key sees the write")

    def test_churn_heal_copies_membership(self):
        # COPY, never move (the hidden half retired with the set, 2026-08-24): a still-alive
        # same-name session would have its state stolen by a move
        km._set_timeline_views({"active": "all", "tags": [
            {"id": "g1", "name": "pool", "members": ["old", "other"]}]})
        km._heal_timeline_views("old", "new")
        v = km._timeline_views()
        self.assertEqual(v["tags"][0]["members"],
                         [{"host": "", "sid": x} for x in ("new", "old", "other")],
                         "the fork inherits membership; the old sid keeps it")
        before = json.loads((jd.STATE / "timeline-views.json").read_text())
        km._heal_timeline_views("stranger", "new2")   # untouched sid → no write at all
        self.assertEqual(json.loads((jd.STATE / "timeline-views.json").read_text()), before)

    def test_ordered_fork_splice_heals_views(self):
        # the same name-keyed splice that inherits the ORDER slot carries the views state with it
        km._write_session_order(["old"])
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / "old").write_text("web\t/tmp\t#123456\twhite\n")
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": ["old"]}]})
        km._ordered([{"sid": "old", "name": "web"}, {"sid": "new", "name": "web"}])
        self.assertEqual(km._timeline_views()["tags"][0]["members"],
                         [{"host": "", "sid": "new"}, {"host": "", "sid": "old"}],
                         "membership copied, so the fork keeps its tag")

    def test_stored_hidden_entries_migrate_into_the_archived_tag_once(self):
        # the ONE-TIME MIGRATION (the user 2026-08-24, retiring hide-from-chat outright): a blob
        # persisted by the pre-retirement normalizer still carries hidden entries — the first read
        # maps them into an "archived" tag (muted color), drops the key, and persists; they show
        # under All (nothing hides from All now) and stay out of the untagged view.
        raw = {"active": "all", "hidden": ["s7", "s8"], "tags": [{"id": "g1", "name": "pool", "members": ["s2"]}]}
        (jd.STATE / "timeline-views.json").write_text(json.dumps(raw))
        km._flags_cache.clear()
        v = km._timeline_views()
        self.assertNotIn("hidden", v)
        arch = next(t for t in v["tags"] if t["name"] == "archived")
        self.assertEqual(arch["members"], [{"host": "", "sid": "s7"}, {"host": "", "sid": "s8"}])
        self.assertEqual(arch["color"], "#6b7280", "a muted slate — never a status color")
        on_disk = json.loads((jd.STATE / "timeline-views.json").read_text())
        self.assertNotIn("hidden", on_disk, "the mapping persisted — the next read has nothing to migrate")
        rendered = km._views_client()   # _view_visible reads the RENDERED shape (string members)
        self.assertTrue(km._view_visible(rendered, "s7"), "archived sessions SHOW under All")
        self.assertFalse(km._view_visible(dict(rendered, active="untagged"), "s7"), "…and stay out of untagged (tagged now)")
        # idempotent: a second read (fresh cache) neither duplicates members nor re-mints the tag
        km._flags_cache.clear()
        v2 = km._timeline_views()
        self.assertEqual([t["name"] for t in v2["tags"]].count("archived"), 1)
        self.assertEqual(next(t for t in v2["tags"] if t["name"] == "archived")["members"], arch["members"])
        # an install with NO hidden entries never mints the tag
        (jd.STATE / "timeline-views.json").write_text(json.dumps({"active": "all", "tags": []}))
        km._flags_cache.clear()
        self.assertEqual(km._timeline_views()["tags"], [], "minted only when hidden entries exist")

    def test_ws_op_persists_via_normalizer(self):
        # the handler body is _set_timeline_views + _mark_views_dirty; pin the setter's normalization
        km._set_timeline_views({"active": "g9", "hidden": ["x"], "tags": []})
        v = json.loads((jd.STATE / "timeline-views.json").read_text())
        self.assertEqual(v["active"], "all")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('msg.get("type") == "setTimelineViews"', src)
        self.assertIn('_set_timeline_views(msg["views"])', src)

    def test_payloads_echo_the_views_blob(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"views": _views_client(),', src, "the timeline payload carries the RENDERED shape")
        self.assertIn('"palette": pal.colors(_palette_name()),', src, "and the palette, for tag colors in every host")
        self.assertIn('"tabs": tab_meta, "views": _views_client()', src, "tabOrder pushes carry it")
        self.assertIn('"tabs": _tabs, "views": _views_client()', src, "the connect-time tabOrder carries it")

    def test_web_boot_exposes_the_set_views_hook(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("window.__rompTimelineSetViews=function(views)", src)
        self.assertIn('post({type:"setTimelineViews",views:views});', src)

    # ── tag federation v0 (the user 2026-08-24): canonical pairs, the rendered union, remote views ──

    def test_member_pairs_round_trip_all_three_spellings(self):
        # strings ("sid", "host:sid") and pair dicts all land as canonical pairs; the rendered
        # client shape respells them viewer-relative — a lossless round trip either way
        km._set_timeline_views({"active": "all", "hidden": [], "tags": [
            {"id": "g1", "name": "mixed", "members": ["s1", "alpha:s2", {"host": "beta", "sid": "s3"}]}]})
        km._flags_cache.clear()
        stored = km._timeline_views()["tags"][0]["members"]
        self.assertEqual(stored, [{"host": "", "sid": "s1"}, {"host": "alpha", "sid": "s2"},
                                  {"host": "beta", "sid": "s3"}])
        rendered = km._views_client()["tags"][0]["members"]
        self.assertEqual(rendered, ["s1", "alpha:s2", "beta:s3"],
                         "the rendering spelling is exactly the pre-pairs client contract")

    def _attach(self, host, views, status="up"):
        km._remotes[host] = {"host": host, "status": status, "views": views}

    def test_remote_tags_join_the_client_shape_read_only_and_host_stamped(self):
        # a tag created on kernel alpha, with a member of its own, one of beta's, and one of OURS —
        # the sid-first respelling: alpha's home member wears alpha's host here, beta's keeps
        # alpha's label for beta (it joins whenever beta is attached here too), and OUR session
        # (known locally by sid — uuids are global) renders bare, so the lane join lands
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / "00000000-aaaa-bbbb-cccc-000000000001").write_text("web\t/tmp\t#123456\twhite\n")
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("alpha", {"active": "all", "hidden": [], "tags": [
                {"id": "g9", "name": "team", "color": "#DD42FF",
                 "members": [{"host": "", "sid": "rs1"}, {"host": "beta", "sid": "rs2"},
                             {"host": "snape", "sid": "00000000-aaaa-bbbb-cccc-000000000001"}]}]})
            v = km._views_client()
            self.assertEqual(v["remoteTags"], [{
                "id": "alpha:g9", "host": "alpha", "name": "team", "color": "#DD42FF",
                "members": ["alpha:rs1", "beta:rs2", "00000000-aaaa-bbbb-cccc-000000000001"]}])
            # …and picking it filters exactly like a local tag, falling OPEN when the kernel detaches
            v["active"] = "alpha:g9"
            self.assertTrue(km._view_visible(v, "alpha:rs1"))
            self.assertFalse(km._view_visible(v, "somebody-else"))
            gone = {"active": "alpha:g9", "hidden": [], "tags": []}
            self.assertTrue(km._view_visible(gone, "somebody-else"),
                            "a vanished remote tag falls open, never trapping the viewer in an empty view")
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_a_tag_view_is_the_name_keyed_union_across_kernels(self):
        # user ruling 2026-08-24: a tag IS its name — whichever store's id is active, the view shows
        # the union of every same-name tag's members, local and remote joined. The stored duplicates
        # stay separate (anti-clobber); this is the read, and both client mirrors pin it identically.
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("TESTHOST-A", {"tags": [{"id": "g1", "name": "team", "color": "#DD42FF",
                                                  "members": [{"host": "", "sid": "rs1"}]}]})
            km._set_timeline_views({"active": "all", "hidden": [],
                                    "tags": [{"id": "gL", "name": "team", "color": "#123456",
                                              "members": ["local1"]}]})
            km._flags_cache.clear()
            v = km._views_client()
            v["active"] = "gL"                     # the LOCAL id activates the union
            self.assertTrue(km._view_visible(v, "local1"))
            self.assertTrue(km._view_visible(v, "TESTHOST-A:rs1"), "the remote store's member joins")
            self.assertFalse(km._view_visible(v, "other"))
            v["active"] = "TESTHOST-A:g1"          # …and so does the remote id — same union
            self.assertTrue(km._view_visible(v, "local1"))
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_same_name_tags_on_two_kernels_stay_two_entries(self):
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("alpha", {"tags": [{"id": "g1", "name": "team", "members": []}]})
            self._attach("beta", {"tags": [{"id": "g1", "name": "team", "members": []}]})
            ids = [t["id"] for t in km._views_client()["remoteTags"]]
            self.assertEqual(ids, ["alpha:g1", "beta:g1"], "host-disambiguated — never a silent merge")
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_a_down_kernels_CACHE_keeps_contributing_and_active_hidden_stay_local(self):
        # REVERSED from v0 (the user 2026-08-24, the untagged-view bug): visibility must not flap
        # with a peer's restart, so the last-known tags keep excluding from untagged and keep their
        # views pickable while the link reconnects. Bounded staleness — the auto-reconnect heals
        # within a pass, and detach pops the row and its cache with it.
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            self._attach("alpha", {"tags": [{"id": "g1", "name": "team", "members": []}]}, status="down")
            v = km._views_client()
            self.assertEqual([t["id"] for t in v.get("remoteTags") or []], ["alpha:g1"],
                             "the cached read stands while the kernel reconnects")
            # a remote-tag ACTIVE survives normalization (validated at read time, ":" is the marker);
            # hidden stays exactly the viewer-local list — neither is federated state
            n = km._norm_timeline_views({"active": "alpha:g1", "hidden": ["h1"], "tags": []})   # legacy hidden: dropped below
            self.assertEqual(n["active"], "alpha:g1")
            self.assertNotIn("hidden", n, "legacy hidden dropped (retired 2026-08-24); active stays local")
            # a client echoing the derived remoteTags back never persists it
            n2 = km._norm_timeline_views({"active": "all", "remoteTags": [{"id": "x"}], "tags": []})
            self.assertNotIn("remoteTags", n2)
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_untagged_excludes_by_the_UNION_both_tag_homes(self):
        # the user's repro (2026-08-24): they tagged a session from the chat while showing only
        # untagged, and it STAYED — the untagged branch counted local tags only, so a REMOTE-homed
        # tag never excluded. A tag is its NAME wherever it homes: held by any kernel's tag = tagged.
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            # remote-homed: the viewer's kernel holds no tag at all; alpha's tag holds two sessions —
            # one of alpha's own, and one of OURS (the local sid, known here)
            (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
            (jd.STATE / "names" / "00000000-aaaa-bbbb-cccc-000000000002").write_text("cards\t/tmp\t#123456\twhite\n")
            self._attach("alpha", {"tags": [{"id": "g1", "name": "workers", "color": "#DD42FF",
                                             "members": [{"host": "", "sid": "rsid1"},
                                                         {"host": "viewer", "sid": "00000000-aaaa-bbbb-cccc-000000000002"}]}]})
            km._set_timeline_views({"active": "untagged", "hidden": [], "tags": []})
            km._flags_cache.clear()
            v = km._views_client()
            self.assertFalse(km._view_visible(v, "alpha:rsid1"),
                             "a remote-homed tag excludes its member from untagged")
            self.assertFalse(km._view_visible(v, "00000000-aaaa-bbbb-cccc-000000000002"),
                             "…including OUR OWN session it holds — the exact reported case")
            self.assertTrue(km._view_visible(v, "someone-else"))
            # local-homed: unchanged behavior, pinned beside it
            km._set_timeline_views({"active": "untagged", "hidden": [],
                                    "tags": [{"id": "gL", "name": "local", "members": ["locsid"]}]})
            km._flags_cache.clear()
            v = km._views_client()
            self.assertFalse(km._view_visible(v, "locsid"))
        finally:
            km._remotes.clear(); km._remotes.update(saved)

    def test_per_surface_lenses_the_truth_table(self):
        # the multi-select decision (the user 2026-08-25): union over selected buckets, none = in
        # no tag home (name-keyed, remote included), All admits everything, surfaces independent
        views = {"active": "all",
                 "actives": {"chat": {"tags": ["workers"]},
                             "timeline": {"none": True, "tags": ["infra"]}},
                 "tags": [{"id": "g1", "name": "workers", "members": ["s1"]},
                          {"id": "g2", "name": "infra", "members": ["s2"]}],
                 "remoteTags": [{"id": "alpha:g9", "host": "alpha", "name": "workers",
                                 "members": ["alpha:r1"]}]}
        # chat: workers only — name-keyed union pulls the remote member in too
        self.assertTrue(km._view_visible(views, "s1", surface="chat"))
        self.assertTrue(km._view_visible(views, "alpha:r1", surface="chat"))
        self.assertFalse(km._view_visible(views, "s2", surface="chat"))
        self.assertFalse(km._view_visible(views, "loose", surface="chat"))
        # timeline: infra ∪ no-tags — arbitrary combinations, independent of chat's pick
        self.assertTrue(km._view_visible(views, "s2", surface="timeline"))
        self.assertTrue(km._view_visible(views, "loose", surface="timeline"))
        self.assertFalse(km._view_visible(views, "s1", surface="timeline"), "tagged, not selected")
        # a missing lens falls open (a surface never named yet)
        self.assertTrue(km._view_visible(views, "anything", surface="feed"))
        # the scalar path is untouched — legacy callers and their pins keep working
        self.assertTrue(km._view_visible(views, "anything"))

    def test_migration_seeds_every_surface_from_the_scalar_active(self):
        # lossless legacy (the user 2026-08-25): a blob without `actives` reads as if each surface
        # had selected the old shared view — untagged seeds the none bucket, a tag id its NAME
        v = km._norm_timeline_views({"active": "untagged"})
        self.assertEqual(v["actives"], {"chat": {"none": True}, "timeline": {"none": True},
                                        "outline": {"none": True}})
        v = km._norm_timeline_views({"active": "g1",
                                     "tags": [{"id": "g1", "name": "workers", "members": []}]})
        self.assertEqual(v["actives"], {"chat": {"tags": ["workers"]},
                                        "timeline": {"tags": ["workers"]},
                                        "outline": {"tags": ["workers"]}})
        # once a blob CARRIES actives, they win — and a surface absent from it reads All, never
        # a re-seed (the scalar may lag the lenses; seeding again would resurrect a stale pick)
        v = km._norm_timeline_views({"active": "untagged",
                                     "actives": {"chat": {"tags": ["workers"]}}})
        self.assertEqual(v["actives"]["chat"], {"tags": ["workers"]})
        self.assertEqual(v["actives"]["timeline"], {"all": True})

    def test_lens_normalization_never_strands_an_empty_selection(self):
        v = km._norm_timeline_views({"actives": {"chat": {"tags": []}, "timeline": {"none": False}}})
        self.assertEqual(v["actives"]["chat"], {"all": True})
        self.assertEqual(v["actives"]["timeline"], {"all": True})
        # unknown names are KEPT — they may live on a linked kernel (name-keyed federation)
        v = km._norm_timeline_views({"actives": {"chat": {"tags": ["only-on-alpha"]}}})
        self.assertEqual(v["actives"]["chat"], {"tags": ["only-on-alpha"]})

    def test_focus_never_mutates_the_views_blob(self):
        # A focus is a PEEK, not a view edit (the user 2026-08-24, superseding the 2026-08-18/19/23
        # reveal rule this test used to pin): the chat opens an out-of-view session as an EPHEMERAL
        # peek tab client-side, so _reveal_chat_for must leave timeline-views.json byte-identical and
        # never mark views dirty — for EVERY case the old rule used to rewrite (hidden under All,
        # tagged from untagged, tagless from a tag view, hidden+tagged, unknown sid), and for the
        # confirmRevive shape too. The focus/shell send pair itself is pinned by
        # tests/test_kernel_mobile.py::RevealRouting.
        G = {"id": "g1", "name": "pool", "members": ["s2"]}
        dirty = []
        saved = km._mark_views_dirty
        km._mark_views_dirty = lambda: dirty.append(1)
        try:
            for blob, sid in [
                ({"active": "g1", "hidden": [], "tags": [G]}, "s9"),      # tagless from a tag view
                ({"active": "untagged", "hidden": [], "tags": [G]}, "s2"),  # tagged from untagged
                ({"active": "all", "hidden": ["sX"], "tags": [G]}, "sX"),   # hidden under All
                ({"active": "all", "hidden": ["s2"], "tags": [G]}, "s2"),   # hidden AND tagged
                ({"active": "g1", "hidden": ["sX"], "tags": [G]}, "sX"),    # hidden tagless, tag view
                ({"active": "g1", "hidden": [], "tags": [G]}, "s2"),        # already visible member
            ]:
                km._set_timeline_views(blob)
                before = (jd.STATE / "timeline-views.json").read_bytes()
                km._reveal_chat_for({"wid": "w1"}, {"type": "focus", "id": sid})
                km._reveal_chat_for({"wid": "w1"}, {"type": "confirmRevive", "id": sid, "name": "web"})
                self.assertEqual((jd.STATE / "timeline-views.json").read_bytes(), before,
                                 "a focus gesture rewrote the views blob (%s → %s)" % (blob, sid))
            self.assertEqual(dirty, [], "no gratuitous views-dirty wake for a pure focus")
        finally:
            km._mark_views_dirty = saved
