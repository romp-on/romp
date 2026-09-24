#!/usr/bin/env python3
"""The Outline pane's provisional row (plans/outline-pane-provisional-row.md, 2026-09-15): a row the Outline (app `fleet`, the pane
that rides feed["ledgers"]) shows for a cold tab WITHOUT a build, so the cold-tab gate (tests/test_cold_tab_gate.py) stays on with
the pane connected. The gate's deploy boot skipped nothing because the user's dashboard keeps the Outline connected and the gate
stood down for any connected pane; with the pane declaring the capability (`provrows=1` on its dial, read at the handshake into
`provRows`, gated on its app), the periodic push skips the cold tabs and ships a provisional row for each in build order: the name
and colour from the session's record, the light status the skeleton tabs already get, the mail-off fields, and a ledger from the
goal store alone (the shared walk, no deep-link anchors, the mute's zeroing and the cap of 80 inside the shared helper, a parse-free
memo per session); the built row replaces it on the next frame once the tab is built. An Outline WITHOUT the flag disables the
gate as before. Drives the REAL _push over fake clients with build_session stubbed and counted, as the gate's own test does; real
temp transcript files; a real goal store in a hermetic state root. Synthetic only (the notes-api demo world, placeholder ids)."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
SRC = open(os.path.join(BIN, "romp-kernel")).read()
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_outline_provisional_rows", os.path.join(BIN, "romp-kernel"))

S1 = "11111111-2222-3333-4444-777777777771"   # web: the tab the page is looking at
S2 = "11111111-2222-3333-4444-777777777772"   # api: the biggest transcript; carries a goal store
S3 = "11111111-2222-3333-4444-777777777773"   # tests: the smallest transcript
S4 = "11111111-2222-3333-4444-777777777774"   # docs: just created: no transcript on disk
NAMES = {S1: "web", S2: "api", S3: "tests", S4: "docs"}
TAB_ORDER = [S2, S1, S3, S4]
# the ledgers' order is the kernel's BUILD order, not the strip's: the watched tab and the transcript-less one first, the rest in
# the strip's order (a stable sort in _push); the pane's rows came in this order before the capability (every tab built with the
# gate down), so the merged list must keep it: a flagged Outline and an unflagged one show the same rows in the same order
BUILD_ORDER = [S1, S4, S2, S3]
SIZES = {S2: 3000, S1: 2000, S3: 1000}
FEED = {"type": "feed", "items": [], "asks": [], "working": [], "awaiting": [], "stateUnknown": [], "order": [], "sessions": [], "hosts": [],
        "pendingHosts": [], "pendingDead": []}


def _sess(sid, n, state):
    return {"type": "session", "id": sid, "name": NAMES[sid],
            "events": [{"kind": "assistant", "uuid": "u%d" % i, "md": "m%d" % i} for i in range(n)],
            "status": {"state": state, "sinceEpoch": None},
            "ledger": {"summary": "", "tree": [{"id": "b1", "text": "built row", "depth": 0, "children": []}], "current": None,
                       "recent": [], "workingNote": "", "needsInput": None}}


def _store(nodes, last=None):
    return {"rompUuid": S2, "seq": 1, "placementsV": getattr(km.jd, "PLACEMENTS_V", 1), "nodes": nodes, "status": {}, "lastNode": last}


def _fake_self(path):
    """A connect handler with a peer that closes at once (tests/test_chat_skeleton_reconnect.py's shape)."""
    class FakeSelf:
        headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
        rfile = io.BytesIO(); wfile = io.BytesIO()
        connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
        close_connection = False
        def send_response(self, *a): pass
        def send_header(self, *a): pass
        def end_headers(self): pass
    FakeSelf.path = path
    return FakeSelf()


class OutlineProvisionalRows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for sid, n in SIZES.items():
            p = os.path.join(self.tmp, sid + ".jsonl")
            with open(p, "w") as f:
                f.write("x" * n)
            self.paths[sid] = p
        self.paths[S4] = os.path.join(self.tmp, S4 + ".jsonl")   # never written
        self.SESS = {S1: _sess(S1, 5, "working"), S2: _sess(S2, 7, "working"), S3: _sess(S3, 3, "waiting"), S4: _sess(S4, 0, "waiting")}
        self._saved = (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame, km._push_subagents,
                       km.NAMES, km.jd.STATE, list(km._clients))
        km._chat_tab_sessions = lambda now, live_map: [{"sid": sid, "name": NAMES[sid], "path": self.paths[sid], "anchor": sid} for sid in TAB_ORDER]
        self.live = {sid: {"state": "working" if sid in (S1, S2) else "waiting", "since": 1781100000, "model": "", "effort": "", "mode": "",
                           "backend": "sdk"} for sid in TAB_ORDER}
        km._live_map = lambda: dict(self.live)
        km._cached_feed = lambda *a, **k: json.loads(json.dumps(FEED))
        self.built = []

        def build(sid, now, live_map=None, **kw):
            self.built.append(sid)
            return json.loads(json.dumps(self.SESS[sid]))
        km.build_session = build
        km._comments_frame = lambda sid, live_map: None
        km._push_subagents = lambda clients, now, live_map: None
        km.NAMES = Path(self.tmp) / "names"; km.NAMES.mkdir()
        (km.NAMES / S2).write_text("api\t/home/user/projects/notes-api\t#1EA1EB\t#ffffff\n")   # the strip's record: the row's colour
        state = Path(self.tmp) / "state"; state.mkdir(parents=True, exist_ok=True)
        km.jd._rebind_state(state)                     # GOALDIR and every derived dir follow (the goal-store fixture rule)
        km.jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (state / "session-hosts").write_text("off\n")
        self.store_path = km.jd.GOALDIR / (S2 + ".json")
        self.store_path.write_text(json.dumps(_store({
            "g1": {"text": "index the notes", "t": 1781100000, "mt": 1781100000, "parentId": None},
            "g2": {"text": "write the search route", "t": 1781100100, "mt": 1781100100, "parentId": "g1"}}, last="g2")))
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        if hasattr(km, "_prov_ledger_memo"):
            km._prov_ledger_memo.clear()
        del km._clients[:]
        km._pusher_wake.clear()
        km._PERF_STATS.reset()

    def tearDown(self):
        (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame, km._push_subagents,
         km.NAMES, state, clients) = self._saved
        km.jd._rebind_state(state)
        del km._clients[:]; km._clients.extend(clients)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        km._PERF_STATS.reset()

    def _client(self, app="chat", **kw):
        frames = []
        c = {"app": app, "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s)), "_frames": frames}
        c.update(kw)
        return c

    @staticmethod
    def _frames(c, typ):
        return [f for f in c["_frames"] if f["type"] == typ]

    def _ledgers(self, outline):
        feeds = self._frames(outline, "feed")
        self.assertTrue(feeds, "the Outline got its feed frame: %r" % [f["type"] for f in outline["_frames"]])
        return feeds[-1]["ledgers"]

    def _skipped(self):
        return km._PERF_STATS.snapshot()["builds"]["chat"]["coldSkipped"]

    def test_01_an_outline_with_the_flag_keeps_the_gate_on_and_gets_a_provisional_row_per_skipped_tab_in_build_order(self):
        c = self._client(reconnect=True, active=S1)      # the restart reload's dial: the diet, and the tab on screen
        o = self._client(app="fleet", provRows=True)     # the Outline, declaring the capability
        km._clients[:] = [c, o]
        km._push([c, o])
        self.assertEqual(sorted(self.built), sorted([S1, S4]), "the watched tab and the transcript-less one only: %r" % self.built)
        self.assertEqual(self._skipped(), 2)
        rows = self._ledgers(o)
        self.assertEqual([r["sid"] for r in rows], BUILD_ORDER, "every session has a row, in build order (the base's order with the gate down)")
        by = {r["sid"]: r for r in rows}
        for sid in (S2, S3):
            r = by[sid]; t = "\n  " + json.dumps(r)[:600]
            self.assertTrue(r.get("provisional"), sid + ": marked provisional" + t)
            self.assertTrue(r["status"].get("provisional"), "the light status, itself marked" + t)
            self.assertEqual(r["name"], NAMES[sid], t)
            self.assertEqual(set(r["ledger"]), {"tree", "current", "archivedTops"}, "the ledger carries what the pane reads, nothing more" + t)
            self.assertIsNone(r["ledger"]["current"], "current blank: the row's since is not the turn's start" + t)
            self.assertIn("postalServiceOff", r); self.assertIn("mailOffWhy", r)
        self.assertEqual((by[S2]["status"]["state"], by[S3]["status"]["state"]), ("working", "ready"), "the live row's word, as the skeleton tabs get it")
        self.assertEqual(by[S2]["color"], {"bg": "#1EA1EB", "fg": "#ffffff"}, "the strip's record colours the row")
        tree = by[S2]["ledger"]["tree"]; tt = "\n  " + json.dumps(tree)
        self.assertEqual([(n["text"], n["depth"]) for n in tree], [("index the notes", 0), ("write the search route", 1)], "the goal store's walk" + tt)
        self.assertEqual(tree[0]["children"], ["g2"], tt); self.assertTrue(tree[1]["current"], "the store's lastNode is the current node" + tt)
        for n in tree:
            self.assertIsNone(n["promptAnchorUuid"], "no deep-link anchor without the transcript" + tt); self.assertIsNone(n["anchorUuid"], tt)
        self.assertEqual(by[S3]["ledger"]["tree"], [], "no store, no rows")
        for sid in (S1, S4):
            self.assertNotIn("provisional", by[sid], sid + ": a built tab's row is the build's")
            self.assertEqual(by[sid]["ledger"]["tree"][0]["text"], "built row")

    def test_02_an_outline_without_the_flag_disables_the_gate_as_before(self):
        c = self._client(reconnect=True, active=S1)
        o = self._client(app="fleet")                    # an older pane: no capability declared
        km._clients[:] = [c, o]
        km._push([c, o])
        self.assertEqual(sorted(self.built), sorted(TAB_ORDER))
        self.assertEqual(self._skipped(), 0)
        self.assertFalse(any(r.get("provisional") for r in self._ledgers(o)), "every row is a built one")
        self.assertEqual([r["sid"] for r in self._ledgers(o)], BUILD_ORDER, "the same order a flagged Outline's merged list keeps")

    def test_03_a_mixed_set_one_old_pane_among_new_disables_the_gate(self):
        c = self._client(reconnect=True, active=S1)
        new, old = self._client(app="fleet", provRows=True), self._client(app="fleet")
        km._clients[:] = [c, new, old]
        km._push([c, new, old])
        self.assertEqual(sorted(self.built), sorted(TAB_ORDER)); self.assertEqual(self._skipped(), 0)

    def test_04_the_built_row_replaces_the_provisional_one_on_the_next_frame_once_the_tab_is_built(self):
        c = self._client(reconnect=True, active=S1)
        o = self._client(app="fleet", provRows=True)
        km._clients[:] = [c, o]
        km._push([c, o])
        self.assertTrue({r["sid"]: r for r in self._ledgers(o)}[S2].get("provisional"))
        # the page's click releases the skeleton (the pusher's own release road: the client's set loses the tab; the next push builds it)
        c["skeleton"].discard(S2)
        c["skeletonOrder"] = [s for s in c.get("skeletonOrder", []) if s != S2]
        del o["_frames"][:]
        km._push([c, o])
        self.assertIn(S2, self.built, "the released tab is built")
        by = {r["sid"]: r for r in self._ledgers(o)}
        self.assertNotIn("provisional", by[S2], "its row is the build's now"); self.assertEqual(by[S2]["ledger"]["tree"][0]["text"], "built row")
        self.assertTrue(by[S3].get("provisional"), "the still-skeleton tab keeps its provisional row")
        self.assertNotIn(S2, km._prov_ledger_memo, "the built tab's memo entry went at the attach: the gate never skips a warm tab again")

    def test_05_a_muted_session_shows_no_goals_and_the_tree_is_capped_at_eighty(self):
        (km.jd.STATE / "session-flags.json").write_text(json.dumps({S2: {"hideFromFeed": True}}))
        c = self._client(reconnect=True, active=S1); o = self._client(app="fleet", provRows=True)
        km._clients[:] = [c, o]
        km._push([c, o])
        self.assertEqual({r["sid"]: r for r in self._ledgers(o)}[S2]["ledger"]["tree"], [], "the mute's zeroing inside the shared helper")
        (km.jd.STATE / "session-flags.json").write_text("{}")
        km._flags_cache.clear() if hasattr(km, "_flags_cache") else None
        nodes = {"g%03d" % i: {"text": "goal %d" % i, "t": 1781100000 + i, "mt": 1781100000 + i, "parentId": None} for i in range(90)}
        self.store_path.write_text(json.dumps(_store(nodes)))
        del o["_frames"][:]; km._push([c, o])
        self.assertEqual(len({r["sid"]: r for r in self._ledgers(o)}[S2]["ledger"]["tree"]), 80, "the cap, inside the shared helper")
        self.assertEqual(len(km._prov_ledger_memo[S2][2]), 80, "the memo stores the CAPPED tree, not the walk's ninety")

    def test_06_the_parse_free_memo_hits_across_cycles_and_misses_on_a_store_write(self):
        c = self._client(reconnect=True, active=S1); o = self._client(app="fleet", provRows=True)
        km._clients[:] = [c, o]
        km._push([c, o])
        st0 = dict(km._prov_ledger_memo_stats)
        km._push([c, o])
        st1 = dict(km._prov_ledger_memo_stats)
        self.assertGreater(st1["hit"], st0["hit"], "the second cycle serves the memo: %r -> %r" % (st0, st1))
        self.assertEqual(st1["miss"], st0["miss"])
        time.sleep(0.02)
        self.store_path.write_text(json.dumps(_store({"g9": {"text": "a new goal", "t": 1781100900, "mt": 1781100900, "parentId": None}})))
        km._push([c, o])
        st2 = dict(km._prov_ledger_memo_stats)
        self.assertGreater(st2["miss"], st1["miss"], "a store write misses the memo: %r -> %r" % (st1, st2))
        self.assertEqual([n["text"] for n in {r["sid"]: r for r in self._ledgers(o)}[S2]["ledger"]["tree"]], ["a new goal"])
        self.assertIn("outlineProvisional", km._PERF_STATS.snapshot()["memos"], "the memo reports under /perf")

    def test_09_the_memo_eviction_survives_a_concurrent_insert_and_drops_the_dead_entries(self):
        # the review's medium (2026-09-15): _push runs on the pusher thread and on the connect handlers' threads at once, so a
        # comprehension over the live memo at the attach raised RuntimeError against an inserter and the broad except turned
        # that push into one that sent no frame. The eviction is a helper over a snapshot, popping with a default.
        import threading
        km._prov_ledger_memo.clear()
        km._prov_ledger_memo.update({("11111111-2222-3333-4444-%012d" % i): (None, None, []) for i in range(400)})
        stop, errs = threading.Event(), []
        def inserter():   # a connect push on a handler thread: _provisional_ledger stores a skipped tab's tree, and pops as it goes
            i = 0
            while not stop.is_set() and i < 2_000_000:   # loop-ok: bounded by the event the evictor sets and by the count
                km._prov_ledger_memo["insert-%d" % i] = (None, None, [])
                km._prov_ledger_memo.pop("insert-%d" % (i - 50), None)
                i += 1
        th = threading.Thread(target=inserter, daemon=True); th.start()
        try:
            for _ in range(2000):   # loop-ok: a bounded drive of the eviction against the inserter
                try:
                    km._prov_ledger_memo_evict({}, True)   # nothing listed: every entry is stale, as after a strip change
                except Exception as e:
                    errs.append(type(e).__name__ + ": " + str(e)[:80]); break
                km._prov_ledger_memo.update({("11111111-2222-3333-4444-%012d" % i): (None, None, []) for i in range(400)})
        finally:
            stop.set(); th.join(timeout=5)
        self.assertEqual(errs, [], "the eviction never raised against the concurrent inserter")
        # the semantics: with a reader, the listed and unbuilt entries stay; an unlisted or a built one goes; with no reader, all go
        km._prov_ledger_memo.clear()
        km._prov_ledger_memo.update({S1: (None, None, []), S2: (None, None, []), S3: (None, None, [])})
        km._built_chat[S1] = (None, {})
        try:
            km._prov_ledger_memo_evict({S1: 0, S2: 1}, True)
            self.assertEqual(set(km._prov_ledger_memo), {S2}, "S1 built (warm) and S3 unlisted went; S2, listed and cold, stays")
            km._prov_ledger_memo_evict({S1: 0, S2: 1}, False)
            self.assertEqual(km._prov_ledger_memo, {}, "no flagged Outline connected: no reader, every entry goes")
        finally:
            km._built_chat.pop(S1, None)
        # the attach goes through the helper, and no live-dict comprehension or bare del over the memo remains in the kernel
        self.assertIn("_prov_ledger_memo_evict(_bo, _flagged_outline)", SRC)
        self.assertNotIn("for _k in [x for x in _prov_ledger_memo", SRC); self.assertNotIn("del _prov_ledger_memo[", SRC)

    def test_10_the_memo_empties_when_the_flagged_outline_goes_away(self):
        # the review's low 1: the entries lingered after the Outline disconnected, each holding a frozen store reference
        c = self._client(reconnect=True, active=S1); o = self._client(app="fleet", provRows=True)
        km._clients[:] = [c, o]
        km._push([c, o])
        self.assertIn(S2, km._prov_ledger_memo, "the store-backed cold tab has an entry while the Outline reads")
        km._clients[:] = [c]                             # the Outline pane closed
        km._push([c])
        self.assertEqual(km._prov_ledger_memo, {}, "no reader: the attach dropped every entry")

    def test_07_the_handshake_reads_the_flag_for_the_outline_app_only(self):
        for path, expect in (("/ws?app=fleet&delta=1&iid=page-7&provrows=1", True), ("/ws?app=fleet&delta=1&iid=page-7", False),
                             ("/ws?app=chat&delta=1&iid=page-7&provrows=1", False)):
            got = []
            real_reg, real_recv = km._register_ws_client, km._ws_recv
            km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
            km._ws_recv = lambda rfile: (0x8, b"", True)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    km.Handler._ws(_fake_self(path))
            finally:
                km._register_ws_client, km._ws_recv = real_reg, real_recv
                for c in got:
                    if c in km._clients:
                        km._clients.remove(c)
            self.assertEqual(len(got), 1, path)
            self.assertEqual(bool(got[0].get("provRows")), expect, path)

    def test_08_source_pins_the_ledgers_comprehension_survives_the_dial_carries_the_term_for_the_outline_only(self):
        self.assertIn('feed["ledgers"] = [_outline_ledger_row(m) for m in chat_sessions]', SRC, "the built rows' comprehension stands (tests/test_kernel_fleet_ledgers.py pins it)")
        self.assertIn('(APP==="fleet"?"&provrows=1":"")', SRC, "the pane shim's dial carries the term for the Outline app only")
        self.assertIn('provrows = (q.get("provrows") or [""])[0] == "1" and app == "fleet"', SRC, "the handshake reads it gated on the app, as skeleton is on chat")
        self.assertNotIn("_any_sessions_pane", SRC, "the gate's condition names the Outline, not the Sessions pane")


if __name__ == "__main__":
    unittest.main()
