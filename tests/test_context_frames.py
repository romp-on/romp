#!/usr/bin/env python3
"""Context-enriched summaries (the user 2026-08-25, from the confusing-worker-cards round): a
delegated card's summary should open in the REQUESTER's phrasing — usually the user's own words —
not the worker's implementation nouns. Two seams, both mechanical: at MINT, apply_courier stores
the delegating mail's cleaned first line as the additive node field `frame` (ledger body first,
the delivered segment's head as the fallback; zero LLM calls); at DISTILL, the distiller/briefer
prompts for a frame-carrying goal gain a marked <delegating-request> section plus the sender's
linked-ask title. A goal WITHOUT a frame — a session's own work, or any node minted before the
field existed — composes byte-identically to before (the pin). The 671 title clamp still governs
the planted title. SYNTHETIC fixtures only; private synthetic sids."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_ctxframe", os.path.join(BIN, "romp-judge")).load_module()

NOW = 1_787_200_000
T0 = NOW - 3600
MGR = "f20a0001-1111-4222-8333-000000000001"   # private synthetic sids — never the shared placeholder
WKR = "f20a0001-1111-4222-8333-000000000002"
MID = "1787199000.000001_1.TESTHOST"
FRAME_LINE = "user asks (dictated): the color workflow round, four items, one worktree"
BODY = (FRAME_LINE + "\n(1) drag-range selection over run rows\n"
        "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID)


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


class FrameHead(unittest.TestCase):
    def test_markers_strip_and_first_line_wins(self):
        self.assertEqual(jd._frame_head(BODY), FRAME_LINE)

    def test_whitespace_collapses_and_caps(self):
        self.assertEqual(jd._frame_head("  a \t b  \n second line"), "a b")
        self.assertEqual(len(jd._frame_head("x" * 500)), 220)

    def test_empty_and_marker_only_are_empty(self):
        self.assertEqual(jd._frame_head(""), "")
        self.assertEqual(jd._frame_head("<!-- romp-msg-id: x -->"), "")


class ApplyCourierFrame(unittest.TestCase):
    def _plant(self, **kw):
        st = {"rompUuid": WKR, "seq": 0, "nodes": {}, "placements": {}, "status": {}}
        nid = jd.apply_courier(st, "seg1", T0, "verify the staged refs",
                               {"peer": MGR, "goalId": "t1", "msgId": MID}, **kw)
        return st, nid

    def test_frame_is_stored_additively(self):
        st, nid = self._plant(frame=FRAME_LINE)
        self.assertEqual(st["nodes"][nid].get("frame"), FRAME_LINE)

    def test_absent_frame_writes_no_field(self):
        st, nid = self._plant()
        self.assertNotIn("frame", st["nodes"][nid], "old shape byte-identical — additive field only")

    def test_the_title_clamp_still_governs(self):
        st, nid = self._plant(frame=FRAME_LINE)
        st2 = {"rompUuid": WKR, "seq": 5, "nodes": {}, "placements": {}, "status": {}}
        nid2 = jd.apply_courier(st2, "seg2", T0, "y" * 400,
                                {"peer": MGR, "goalId": "t1", "msgId": "m2"}, frame="z")
        self.assertLessEqual(len(st2["nodes"][nid2]["text"]), 120, "the 671 clamp is untouched")


class PostalBodyHead(unittest.TestCase):
    def test_ledger_body_head_and_unknown_mid(self):
        with tempfile.TemporaryDirectory() as td:
            saved = jd.MESSAGES
            jd.MESSAGES = Path(td) / "messages.jsonl"
            jd.MESSAGES.write_text(json.dumps(
                {"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": MGR,
                 "to_id": WKR, "kind": "delegate", "body": BODY}) + "\n")
            try:
                self.assertEqual(jd._postal_body_head(MID), FRAME_LINE)
                self.assertEqual(jd._postal_body_head("nope"), "")
                self.assertEqual(jd._postal_row(MID)[:3][2], False, "the row contract's prefix holds")
            finally:
                jd.MESSAGES = saved


class CourierMintCarriesTheFrame(unittest.TestCase):
    """run_courier end to end: the planted recipient goal carries the delegating mail's first line —
    from the ledger row when present, from the delivered segment when not (the cross-host relay
    shape, whose row the local ledger may lack)."""

    def setUp(self):
        self._rooted = jd._delegate_user_rooted
        jd._delegate_user_rooted = lambda *a, **k: True   # rooting is orthogonal here (its own suite)
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.wpath = d / (WKR + ".jsonl")
        self.wpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, BODY, "m1", ps="sdk"),
            aline(T0 + 60, "On it.", "a1", "m1")]) + "\n")
        self.mpath = d / (MGR + ".jsonl")
        self.mpath.write_text(json.dumps(uline(T0 - 600, "kick off the color round", "hu")) + "\n")
        self._msgs = jd.MESSAGES
        jd.MESSAGES = d / "messages.jsonl"
        self._disc = jd.discover
        fleet = [(WKR, str(self.wpath), None, "api"), (MGR, str(self.mpath), None, "web")]
        jd.discover = lambda now, window=None, forks=True: fleet
        self._llm = jd.courier_llm
        jd.courier_llm = lambda text, menu, declared=None: '{"verdict": "delegating", "goal": 0, "text": "verify refs"}'
        jd._PARSE_CACHE.clear()

    def tearDown(self):
        jd._delegate_user_rooted = self._rooted
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

    def _planted(self):
        w = jd.load_goals(WKR)
        return next((nd for nd in w["nodes"].values()
                     if isinstance(nd.get("origin"), dict) and nd["origin"].get("msgId") == MID), None)

    def test_the_ledger_body_is_the_frame(self):
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": MGR,
             "to_id": WKR, "kind": "delegate", "body": BODY}) + "\n")
        jd.run_courier(now=NOW)
        nd = self._planted()
        self.assertIsNotNone(nd)
        self.assertEqual(nd.get("frame"), FRAME_LINE)

    def test_a_bodyless_ledger_row_falls_back_to_the_delivered_head(self):
        # the relay shape: the row exists (the bus logs delivery, so the sender resolves) but
        # carries no body — the frame falls back to the delivered segment's own head
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": MGR,
             "to_id": WKR, "kind": "delegate"}) + "\n")
        jd.run_courier(now=NOW)
        nd = self._planted()
        self.assertIsNotNone(nd)
        self.assertTrue((nd.get("frame") or "").startswith("user asks (dictated)"),
                        "the delivered segment's own head carries the same framing")


class LlmBuildersCarryTheFrame(unittest.TestCase):
    def _capture(self, fn, *args, **kw):
        seen = {}
        saved = jd._judge_run
        jd._judge_run = lambda model, sys_p, user, **k: seen.__setitem__("user", user) or ""
        try:
            fn(*args, **kw)
        finally:
            jd._judge_run = saved
        return seen.get("user") or ""

    def test_distill_gains_the_marked_section(self):
        u = self._capture(jd.distill_llm, "goal", "work", "done", frame=FRAME_LINE)
        self.assertIn("delegating-request", u)
        self.assertIn(FRAME_LINE, u)
        self.assertIn("Open the takeaway in those", u)

    def test_distill_without_frame_is_byte_identical(self):
        u0 = self._capture(jd.distill_llm, "goal", "work", "done")
        self.assertNotIn("delegating-request", u0)
        u1 = self._capture(jd.distill_llm, "goal", "work", "done", frame=None, user_ask=None)
        self.assertEqual(u0.replace(jd._mark() if False else "", ""), u0)   # sanity
        # marks differ per call; compare shape by stripping the random mark
        import re as _re
        norm = lambda s: _re.sub(r"[0-9a-f]{8}", "MK", s)
        self.assertEqual(norm(u0), norm(u1))

    def test_brief_gains_the_marked_section(self):
        u = self._capture(jd.brief_llm, "goal", "work", "owed thing", frame=FRAME_LINE)
        self.assertIn("delegating-request", u)
        self.assertIn(FRAME_LINE, u)
        u0 = self._capture(jd.brief_llm, "goal", "work", "owed thing")
        self.assertNotIn("delegating-request", u0)


class DelegFrameJoin(unittest.TestCase):
    def tearDown(self):
        for sid in (MGR, WKR):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass

    def _world(self, frame=FRAME_LINE, host=None):
        o = {"peer": MGR, "goalId": "t1", "msgId": MID}
        if host:
            o["peerHost"] = host
        st = {"rompUuid": WKR, "nodes":
              {"g1": _node("g1", "verify refs", None, origin=o,
                           **({"frame": frame} if frame else {}))},
              "placements": {}, "status": {}}
        jd.save_goals(MGR, {"rompUuid": MGR, "nodes":
                            {"ask": _node("ask", "Ship the color round", None),
                             "t1": _node("t1", "↪ delegated to api: verify refs", "ask")},
                            "placements": {}, "status": {}})
        return st

    def test_frame_plus_sender_ask(self):
        st = self._world()
        out = jd._deleg_frame(st, "g1")
        self.assertIn(FRAME_LINE, out)
        self.assertIn("Ship the color round", out)

    def test_no_frame_is_empty_even_with_origin(self):
        st = self._world(frame=None)
        self.assertEqual(jd._deleg_frame(st, "g1"), "",
                         "pre-fix delegated nodes re-distill byte-identically")

    def test_cross_host_keeps_the_frame_and_skips_the_store_read(self):
        st = self._world(host="TESTHOST")
        out = jd._deleg_frame(st, "g1")
        self.assertIn(FRAME_LINE, out)
        self.assertNotIn("Ship the color round", out)

    def test_non_delegated_is_empty(self):
        st = {"rompUuid": WKR, "nodes": {"g1": _node("g1", "own work", None)},
              "placements": {}, "status": {}}
        self.assertEqual(jd._deleg_frame(st, "g1"), "")


class OpenEventWiring(unittest.TestCase):
    """The card-open metric row (go-forward half of cleared-without-open): the kernel appends one
    row per modal open; the feed posts at all three open sites."""

    KERNEL = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()
    FEED = (Path(HERE).parent / "ui" / "webview" / "feed.ts").read_text()

    def test_the_kernel_route_appends_one_row(self):
        self.assertIn('msg.get("type") == "cardOpened"', self.KERNEL)
        self.assertIn('card-opens.jsonl', self.KERNEL)

    def test_the_feed_posts_on_every_open_path(self):
        self.assertEqual(self.FEED.count('type: "cardOpened"'), 3,
                         "single-card click, group click, and the rail-dot open")


if __name__ == "__main__":
    unittest.main()
