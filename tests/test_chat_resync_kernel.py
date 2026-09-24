#!/usr/bin/env python3
"""The kernel half of the chat resync fix (2026-09-23): never send a delta from a stale ledger.

Since 2026-09-19 a message the user had just sent could stay off the chat until a reload, and the previous agent message
could blink out and back. Three changes combined (66486701 a second whole-session builder at the SDK queue pop, fa92e8a4 a
landing sent as a delta whose base could end on a streamed atom, 5d68aa6c targeted pushes sending tails instead of the
fulls that used to overwrite a stale page), and a live reproduction caught the blink on builds before them too: at a turn
boundary a full frame went out built from a parse that had not read the agent message's records and a live tail the settle
had already cleared of its atoms. This module pins the kernel's side of the fix:

  1. build_session reads the live tail BEFORE the transcript, so an atom that left the tail (a prune, the settle's
     retire) had its record on disk before the parse read the file: no build holds a message in neither store.
  2. _last_anchor ends a client's base on a recorded event only, never on a streamed atom (marked `streamed` by the
     build), an echo or an overlay card, so every delta is cut from the last record; since 2026-09-23 _chat_skip_held
     then skips the events the page holds unchanged past it (tests/test_chat_noop_tail.py, StreamedRunIsSentOnce).
  3. Every proto-2 delta carries `baseFp`, [n, crc32] over the keys of the n events ending at its anchor (from the
     client's own first edge at most), for the page to verify before it applies anything (chat-resync.ts).
  4. A needFull keeps the page's watermark as the floor: its answer is never an older build than the page holds (refused
     once, the cycle's cached copy dropped and the sid named to the cycle), so the repair cannot be refused by the page.
  5. One builder of chat frames: the SDK connect handshake and the Codex backend's per-event pushes name their sid to the
     pusher cycle (_push_session_soon) like the queue pop; a census pins every remaining call site of a whole-session
     builder with its disposition, so a new off-cycle builder fails here.

SYNTHETIC only: the notes-api demo world, placeholder UUIDs, TESTHOST.
"""
import ast
import json
import os
import re
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_resync", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_resync", os.path.join(ROOT, "kernel", "sdk_backend.py"))
jd = km.jd

KERNEL_SRC = Path(ROOT, "kernel", "kernel.py").read_text()
SDK_SRC = Path(ROOT, "kernel", "sdk_backend.py").read_text()

# private synthetic sids (the goal-store fixture rule: never the shared placeholder)
S1 = "7e5a1111-2222-4333-8444-555555550001"   # web: the tab the page holds
S2 = "7e5a1111-2222-4333-8444-555555550002"   # api: another tab
NAMES = {S1: "web", S2: "api"}
LEAF1 = "/tmp/TESTHOST/projects/notes-api/%s.jsonl" % S1


def _crc(keys):
    return zlib.crc32("\n".join(keys).encode("utf-8")) & 0xFFFFFFFF


# ── 2. the anchor ───────────────────────────────────────────────────────────────────────────────

class LastAnchorIsDurable(unittest.TestCase):
    def test_never_a_streamed_event_an_echo_or_an_overlay(self):
        evs = [{"kind": "user", "uuid": "u1"}, {"kind": "assistant", "uuid": "a1"},
               {"kind": "user", "uuid": "echo:11111111222233334444555555555555"},
               {"kind": "assistant", "uuid": "m1", "streamed": True},
               {"kind": "tool", "uuid": "m1", "key": "m1#1", "streamed": True},
               {"kind": "todo", "uuid": "todo"}]
        self.assertEqual(km._last_anchor(evs), "a1", "the last RECORDED event: past the streamed reply, the echo and the card")

    def test_a_recorded_event_after_streamed_ones_is_the_anchor(self):
        evs = [{"kind": "user", "uuid": "u1"}, {"kind": "assistant", "uuid": "m1", "streamed": True}, {"kind": "assistant", "uuid": "a2"}]
        self.assertEqual(km._last_anchor(evs), "a2")

    def test_a_list_with_nothing_recorded_keeps_the_old_fallbacks(self):
        self.assertEqual(km._last_anchor([{"kind": "user", "uuid": "echo:abc"}, {"kind": "assistant", "uuid": "m1", "streamed": True}]), "m1",
                         "a transcript-less session: the last non-overlay, non-transient event (one full at its first landing, as before)")
        self.assertEqual(km._last_anchor([{"kind": "user", "uuid": "echo:abc"}]), "echo:abc")
        self.assertIsNone(km._last_anchor([]))

    def test_the_old_rule_took_the_streamed_atom(self):
        """What fa92e8a4's rule returned for the same list: the streamed atom, a key the next list may not hold."""
        evs = [{"kind": "user", "uuid": "u1"}, {"kind": "assistant", "uuid": "m1", "streamed": True}]
        old = next(km._event_key(e) for e in reversed(evs) if not (e.get("kind") in km._OVERLAY_KINDS or km._transient_key(km._event_key(e))))
        self.assertEqual(old, "m1")
        self.assertEqual(km._last_anchor(evs), "u1")


# ── 1 + 2. a real build over a real live tail ─────────────────────────────────────────────────────

class LiveTailBeforeParse(unittest.TestCase):
    """build_session over a temp transcript and the REAL SdkBackend's live tail (its prune and its settle retire)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._sdk,
                      km.Sessions.__dict__["backend_for"], km._parse)
        names = td / "names"; names.mkdir()
        proj = td / "projects"; proj.mkdir()
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        for d in (jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR):
            d.mkdir()
        jd.STATE = td
        km.NAMES = names
        km._sdk = lambda: None
        cdir = td / "work"; cdir.mkdir()
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.path = str(pdir / (S1 + ".jsonl"))
        self.u1 = {"type": "user", "timestamp": "2026-09-23T10:00:00.000Z", "uuid": "u1", "parentUuid": None, "promptSource": "typed",
                   "message": {"role": "user", "content": "make the notes-api search page its results"}}
        Path(self.path).write_text(json.dumps(self.u1) + "\n")
        (names / S1).write_text("web\t%s\t#abcdef\n" % str(cdir))
        (td / "sdk").mkdir()
        self.be = sb.SdkBackend(str(td / "sdk"), "/bin/true", lambda *a, **k: None)
        self.be._reply_on_disk = lambda sid, uuid: True      # the settle runs once the CLI has written the turn (its records are on disk)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.m1_atom = {"type": "assistant", "uuid": "m1", "session_id": S1, "t": 1790000005, "fsid": S1, "parentUuid": None,
                        "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                    "content": [{"type": "text", "text": "Paging is in: the search returns 50 notes a page."}]}}
        self.m1_record = {"type": "assistant", "timestamp": "2026-09-23T10:00:05.000Z", "uuid": "m1", "parentUuid": "u1",
                          "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                      "content": [{"type": "text", "text": "Paging is in: the search returns 50 notes a page."}]}}
        self.be._stash_live(S1, "m1", dict(self.m1_atom))

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._sdk, be_for, km._parse) = self.saved
        km.Sessions.backend_for = be_for
        self.td.cleanup()

    def _uuids(self, m):
        return [e.get("uuid") for e in (m or {}).get("events") or []]

    def _write_record_and_retire(self):
        """What the CLI and the settle do at a turn boundary: M's record reaches the file, then its live atom retires."""
        st = os.stat(self.path)
        with open(self.path, "a") as f:
            f.write(json.dumps(self.m1_record) + "\n")
        os.utime(self.path, (st.st_mtime + 2, st.st_mtime + 2))
        self.be.retire_live_work(S1)

    def test_a_streamed_message_is_marked_and_never_anchors(self):
        m = km.build_session(S1, 1790000010, live_map={})
        evs = m["events"]
        m1 = [e for e in evs if e.get("uuid") == "m1"]
        self.assertTrue(m1, "the streamed reply is on the chat ahead of its record: %r" % self._uuids(m))
        self.assertTrue(all(e.get("streamed") for e in m1), "…marked streamed")
        self.assertFalse(any(e.get("streamed") for e in evs if e.get("uuid") == "u1"), "a recorded event is not")
        self.assertEqual(km._last_anchor(evs), "u1", "a client's base ends on the record before the stream")

    def test_once_recorded_the_message_is_durable_and_anchors(self):
        self._write_record_and_retire()
        m = km.build_session(S1, 1790000020, live_map={})
        m1 = [e for e in m["events"] if e.get("uuid") == "m1"]
        self.assertTrue(m1, "the record carries the message: %r" % self._uuids(m))
        self.assertFalse(any(e.get("streamed") for e in m1), "built from the record: not streamed")
        self.assertEqual(km._last_anchor(m["events"]), "m1")

    def test_the_turn_boundary_race_no_longer_loses_the_message(self):
        """The live observation's shape, kernel half: the build's parse reads the transcript just BEFORE the CLI writes M's
        record, and the settle retires M's live atom right after. Read in the old order (the parse, then the tail), the build
        held M in neither store and the frame omitted it; read tail-first, the snapshot holds M. Red before the change."""
        km.build_session(S1, 1790000008, live_map={})       # warm the parse cache on the file WITHOUT M's record
        orig = self.saved[-1]
        fired = []

        def racing_parse(path, sid, now):
            r = orig(path, sid, now)                          # the parse read the file as it was: no M record
            if not fired:
                fired.append(True)
                self._write_record_and_retire()               # …and in the same instant the CLI writes M, the settle retires its atom
            return r
        km._parse = racing_parse
        m = km.build_session(S1, 1790000010, live_map={})
        self.assertTrue(fired, "the race ran inside the build")
        self.assertIn("m1", self._uuids(m), "the agent message is on the frame: from the tail read before the parse")
        km._parse = orig
        m2 = km.build_session(S1, 1790000030, live_map={})
        self.assertEqual(self._uuids(m2).count("m1"), 1, "the next build carries it once, from the record")

    def test_source_order_the_tail_is_snapshotted_before_the_parse(self):
        i = KERNEL_SRC.index("def build_session(")
        body = KERNEL_SRC[i:i + 20000]
        snap = body.index("_live_snap = None if path_override else be.live_atoms(sid)")
        parse = body.index("parsed = _parse(sess[\"path\"], sid, now)")
        rev = body.index("_wm_live = None if path_override else Sessions.live_rev(sid, be)")
        self.assertLess(rev, snap, "the watermark's revision is read before the tail (a lower bound)")
        self.assertLess(snap, parse, "the tail before the transcript")
        self.assertIn("_merge_live_atoms(parsed, sid, shown_texts=queued, live=_live_snap)", body, "the merge folds the snapshot")


# ── 3 + 4. the senders ──────────────────────────────────────────────────────────────────────────────

def _ev(i):
    return {"kind": "user" if i % 2 == 0 else "assistant", "uuid": "m%d" % i, "md": "step %d of the notes-api search" % i}


def _sess(sid, evs, tx_size, live):
    return {"type": "session", "id": sid, "name": NAMES[sid], "events": evs, "status": {"state": "working", "sinceEpoch": 1790000000},
            "wm": {"leaf": LEAF1, "tx": [[1790000000.0 + tx_size / 1e6, tx_size], [1790000000.0, 40]], "live": live}}


class Senders(unittest.TestCase):
    """The real _push / _push_session_now over fake clients, build_session stubbed (the test_chat_stale_build_guard.py harness)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for sid in (S1, S2):
            p = os.path.join(self.tmp, sid + ".jsonl")
            Path(p).write_text("x" * 1000)
            self.paths[sid] = p
        self.SESS = {S1: _sess(S1, [_ev(i) for i in range(5)], 2000, 7), S2: _sess(S2, [_ev(i) for i in range(3)], 900, 2)}
        self._saved = (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session,
                       km._comments_frame, km._push_subagents, km.NAMES, km.jd.STATE, list(km._clients))
        km._chat_tab_sessions = lambda now, live_map: [{"sid": sid, "name": NAMES[sid], "path": self.paths[sid], "anchor": sid} for sid in (S1, S2)]
        km._live_map = lambda: {}
        km._cached_feed = lambda *a, **k: None

        def build(sid, now, live_map=None, **kw):
            return json.loads(json.dumps(self.SESS[sid]))
        km.build_session = build
        km._comments_frame = lambda sid, live_map: None
        km._push_subagents = lambda clients, now, live_map: None
        km.NAMES = Path(self.tmp) / "names"; km.NAMES.mkdir()
        km.jd.STATE = Path(self.tmp) / "state"; km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear(); km._chat_baseline_raced.clear()
        km._CHAT_STALE_SAID.clear(); km._push_first.clear()
        del km._clients[:]
        km._pusher_wake.clear()

    def tearDown(self):
        (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session,
         km._comments_frame, km._push_subagents, km.NAMES, km.jd.STATE, clients) = self._saved
        del km._clients[:]
        km._clients.extend(clients)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear(); km._chat_baseline_raced.clear()
        km._CHAT_STALE_SAID.clear(); km._push_first.clear()

    def _client(self, **kw):
        frames = []
        c = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s)), "_frames": frames,
             "cid": "cid-%d" % len(km._clients), "kind": "page", "wid": "W1"}
        c.update(kw)
        return c

    @staticmethod
    def _chat(c, sid):
        return [f for f in c["_frames"] if f.get("type") in ("session", "chatTail") and f.get("id") == sid]

    def _grow(self, n, tx, live):
        self.SESS[S1] = _sess(S1, [_ev(i) for i in range(n)], tx, live)
        km._built_chat.clear()

    # 3. the fingerprint
    def test_every_proto2_delta_carries_the_base_it_was_cut_against(self):
        a = self._client(active=S1, proto=2)
        km._clients.append(a)
        km._push([a])
        self._grow(7, 2400, 8)
        km._push([a])
        full, tail = self._chat(a, S1)
        self.assertEqual((full["type"], tail["type"]), ("session", "chatTail"))
        self.assertEqual(tail["afterUuid"], "m4")
        self.assertEqual(tail["baseFp"], [5, _crc(["m0", "m1", "m2", "m3", "m4"])],
                         "the keys of the events ending at the anchor, from the client's first edge (the list is short: all five)")
        self.assertEqual(tail["baseFp"], km._chat_base_fp([_ev(i) for i in range(7)], 0, 5))

    def test_the_window_is_k_events_and_never_reaches_above_the_clients_first_edge(self):
        self.assertEqual(km.CHAT_BASE_FP_K, 64)
        evs = [_ev(i) for i in range(400)]
        self.assertEqual(km._chat_base_fp(evs, 300 - 64, 300), [64, _crc(["m%d" % i for i in range(236, 300)])])
        a = self._client(active=S1, proto=2)
        km._clients.append(a)
        self._grow(300, 3000, 7)
        km._push([a])
        first = self._chat(a, S1)[0]
        self.assertEqual(first["type"], "session")
        self._grow(302, 3100, 8)
        km._push([a])
        tail = self._chat(a, S1)[-1]
        self.assertEqual(tail["type"], "chatTail")
        start = 300
        self.assertEqual(tail["baseFp"], [64, _crc(["m%d" % i for i in range(start - 64, start)])], "the last 64 before the suffix")
        # a client whose tail run starts ten events before the anchor: the window stops at its first edge
        a["echat"][S1]["first"] = "m292"
        self._grow(304, 3200, 9)
        km._push([a])
        tail = self._chat(a, S1)[-1]
        self.assertEqual(tail["afterUuid"], "m301")
        self.assertEqual(tail["baseFp"], [10, _crc(["m%d" % i for i in range(292, 302)])], "never above what the client holds")

    def test_the_steady_state_is_deltas_only(self):
        """Zero fulls when page and kernel agree, kernel side: a steady stream of appends is served as deltas, each stamped."""
        a = self._client(active=S1, proto=2)
        km._clients.append(a)
        km._push([a])
        for n in range(6, 26):
            self._grow(n, 2000 + 100 * n, 7 + n)
            km._push([a])
        frames = self._chat(a, S1)
        self.assertEqual([f["type"] for f in frames], ["session"] + ["chatTail"] * 20)
        self.assertTrue(all(isinstance(f.get("baseFp"), list) for f in frames[1:]))

    # 4. the needFull's answer is never older than the page holds
    def test_a_needfull_keeps_the_floor_and_an_older_answer_is_refused_once_and_rebuilt_by_the_cycle(self):
        a = self._client(active=S1, proto=2)
        km._clients.append(a)
        km._push([a])
        self.assertEqual(a["echatWm"][S1]["tx"][0][1], 2000)
        km._client_reset_chat_sid(a, S1)                    # the page asked for S1 whole
        self.assertEqual(a["echatWm"][S1]["tx"][0][1], 2000, "the floor stays through the ask: the page still holds that build")
        self.assertIn(S1, a["askedFull"])
        self._grow(4, 1500, 9)                             # an OLDER reading (the record the page holds not among it)
        km._built_chat[S1] = ("sig", "stand-in")          # the cycle's cached copy, which the refusal must drop
        km._push_session_now(S1)
        self.assertEqual(len(self._chat(a, S1)), 1, "the older build did not answer the ask")
        self.assertNotIn(S1, km._built_chat, "…the cycle's cached copy is dropped, so its next build is fresh")
        self.assertIn(S1, km._push_first, "…and the sid is named to the cycle, which builds it first")
        self.assertTrue(km._pusher_wake.is_set())
        self.assertEqual(len([r for r in self._rows("chatStale")]), 1, "the refusal is filed")
        # the cycle's fresh build (a newer reading) answers: a full, the floor moves with it
        self._grow(6, 2400, 10)
        km._push([a])
        self.assertEqual([f["type"] for f in self._chat(a, S1)], ["session", "session"], "the answer is a full")
        self.assertNotIn(S1, a.get("askedFull", set()))
        self.assertEqual(a["echatWm"][S1]["tx"][0][1], 2400)

    def test_a_second_older_answer_to_the_same_ask_is_sent_a_full_always_wins(self):
        a = self._client(active=S1, proto=2)
        km._clients.append(a)
        km._push([a])
        km._client_reset_chat_sid(a, S1)
        self._grow(4, 1500, 9)
        km._push_session_now(S1)                           # refused once
        km._push_session_now(S1)                           # the same older world again: sent, the ask is answered
        frames = self._chat(a, S1)
        self.assertEqual([f["type"] for f in frames], ["session", "session"], "bounded by event: one refusal per ask, never a frozen tab")
        self.assertNotIn(S1, a.get("fullRefused", set()), "…and the refusal mark went with the full")
        km._client_reset_chat_sid(a, S1)                  # a NEW ask gets its own one refusal
        self._grow(3, 1000, 9)
        km._push_session_now(S1)
        self.assertEqual(len(self._chat(a, S1)), 2)

    def test_ready_still_drops_every_floor(self):
        a = self._client(active=S1, proto=2)
        km._clients.append(a)
        km._push([a])
        km._client_reset_chat_sid(a, S1)
        km._client_reset_chat_base(a)
        self.assertEqual(a.get("echatWm"), {}, "a renderer that holds nothing takes any build")
        self.assertNotIn("fullRefused", a)

    def _rows(self, what):
        fp = km.jd.STATE / "client-diag.jsonl"
        if not fp.exists():
            return []
        return [r for r in (json.loads(ln) for ln in fp.read_text().splitlines() if ln.strip()) if r.get("what") == what]

    # 5. the connect handshake's flip rides the cycle, and stays prompt
    def test_a_name_landing_mid_cycle_is_built_next(self):
        """The handshake names its sid (_push_soon → _push_session_soon); a cycle in flight builds it right after the tab it is
        on, ahead of every tab nobody is waiting on: the opening chip's flip waits one tab's build at most (the #2061 loop)."""
        S3 = "7e5a1111-2222-4333-8444-555555550003"
        NAMES[S3] = "tests"
        p3 = os.path.join(self.tmp, S3 + ".jsonl"); Path(p3).write_text("x" * 1000); self.paths[S3] = p3
        self.SESS[S3] = _sess(S3, [_ev(i) for i in range(2)], 500, 1)
        km._chat_tab_sessions = lambda now, live_map: [{"sid": sid, "name": NAMES[sid], "path": self.paths[sid], "anchor": sid} for sid in (S1, S2, S3)]
        built = []
        orig = km.build_session

        def build(sid, now, live_map=None, **kw):
            built.append(sid)
            if sid == S1:
                km._push_session_soon(S3)                  # the handshake lands while the watched tab builds
            return orig(sid, now, live_map, **kw)
        km.build_session = build
        a = self._client(active=S1, proto=2)
        km._clients.append(a)
        km._push([a])
        self.assertEqual(built, [S1, S3, S2], "the named session is built next: %r" % built)


# ── 5. one builder of chat frames: the census ─────────────────────────────────────────────────────

def _call_sites(src, name):
    """(enclosing function, line) for every call of `name` in `src` (a module's source), by the AST."""
    tree = ast.parse(src)
    out = []

    def walk(node, fn):
        for ch in ast.iter_child_nodes(node):
            f = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(ch, ast.Call):
                callee = ch.func
                nm = callee.id if isinstance(callee, ast.Name) else (callee.attr if isinstance(callee, ast.Attribute) else None)
                if nm == name:
                    out.append((fn, ch.lineno))
            walk(ch, f)
    walk(tree, "<module>")
    return out


# Every call site of a WHOLE-SESSION chat builder in kernel.py, by enclosing function, with its disposition (the PR body lists
# them). A new site fails this census: route it through the pusher cycle (_push_session_soon), or add it here with the reason
# it cannot race the cycle to a page.
BUILD_SESSION_SITES = {
    "_push": "the pusher cycle, and the connect push (_push_one: a fresh socket, a ready, a needFull), which shares the cycle's "
             "build cache and its single-flight claim (_chat_inflight_claim), so the two never build one tab concurrently",
    "_push_session_now": "the targeted push, kept for session CREATION only (a create, a fork, a thread promotion, a Codex create): "
                         "a session no client holds a base for yet, served from its own live-tail-first read",
    "_chat_history_reply": "history pages below the tail run (loadTurns/loadOlder/loadAround): sends no session frame; its prune is "
                           "justified by its own parse, which cannot starve another build now the tail is read first",
    "_thread_events": "a comment thread's popover (not a chat tab frame)",
    "build_subagent": "a subagent transcript viewer (path_override: no live merge, no chat tab frame)",
    "build_episode": "a closed episode's render (path_override: no live merge, no chat tab frame)",
    "_chat_history_page": "one history page of turns [lo, hi) (no live overlays, no chat tab frame)",
    "_dispatch_ws": "the proto-1 loadOlder slice (index clients' history; sends no session frame)",
    "_file_comment_anchor": "a file comment's anchor, only for a session no tab has built (the pusher's cached build "
                            "answers otherwise); sends no session frame",
}
PUSH_SESSION_NOW_CALLERS = {"_create_sdk_session_inner", "_create_codex_session_inner", "_fork_session_inner", "_comment_promote_inner"}


class OneBuilderCensus(unittest.TestCase):
    def test_every_build_session_call_site_is_listed_with_its_disposition(self):
        fns = {fn for fn, _ in _call_sites(KERNEL_SRC, "build_session")}
        self.assertEqual(sorted(fns - set(BUILD_SESSION_SITES)), [],
                         "a new whole-session build site: name it to the cycle (_push_session_soon) or list it here with its reason")
        self.assertEqual(sorted(set(BUILD_SESSION_SITES) - fns), [], "a listed site that no longer builds: drop it from the census")

    def test_the_targeted_push_is_left_to_session_creation(self):
        callers = {fn for fn, _ in _call_sites(KERNEL_SRC, "_push_session_now")}
        self.assertTrue(callers <= PUSH_SESSION_NOW_CALLERS | {"_push_session_now"},
                        "the targeted whole-session push gained a caller: %r" % sorted(callers - PUSH_SESSION_NOW_CALLERS))

    def test_no_backend_builds_beside_the_cycle(self):
        # the SDK backend: its handshake, like its queue pop, names the sid; nothing calls the threaded one-session push
        calls = [ln for ln in SDK_SRC.splitlines() if re.search(r"\._push_session\(", ln) and "def _push_session" not in ln]
        self.assertEqual(calls, [], "an SDK backend path builds a whole session beside the cycle: %r" % calls)
        i = SDK_SRC.index('The handshake IS the "this session is open" event')
        self.assertIn("self.backend._push_soon(self.sid)", SDK_SRC[i:i + 1500], "the handshake names its sid to the cycle")
        # the Codex backend: every per-event push_session call reaches the kernel's name-and-wake, never a build
        j = KERNEL_SRC.index("_codex_backend = cxmod.CodexBackend(")
        self.assertIn("push_session=_push_session_soon,", KERNEL_SRC[j:j + 600])

    def test_push_session_soon_builds_nothing(self):
        src = KERNEL_SRC[KERNEL_SRC.index("def _push_session_soon("):]
        body = src[:src.index("\ndef ", 10)]
        self.assertNotIn("build_session", body)
        self.assertNotIn("_push(", body)


if __name__ == "__main__":
    unittest.main()
