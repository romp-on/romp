#!/usr/bin/env python3
"""Courier durable-retry-then-abandon + debug-surface for peer-message summaries (the user 2026-07-21).

A courier call only comes back empty while the ACCOUNT is usage-limited (the rate gate in _judge_run
short-circuits every triage judge to "") or the API errors. run_courier used to `continue` on that empty
reply with NO trace at all — so a peer message stayed unsummarized silently, indistinguishable from a bug,
and (in a limit window that outlives the session's activity) forever.

Now, for a peer segment whose courier call comes back empty:
  - retry every pass (unchanged — a doomed call is never the model's fault, never a give-up strike), but
  - record a per-segment `courierDeferred` marker + log ONE "deferred" judge-error row (the debug surface:
    the feed only joins error rows onto cards in debug mode), and
  - once the message ages past COURIER_RETRY_HORIZON (48h, matching discover()'s WINDOW), ABANDON it —
    mark it processed ('fyi'), drop the marker, log a "give-up" row — so a long limit window can't
    re-attempt a stale message endlessly on a session that stays live for other work.

Synthetic fixtures only (placeholder UUIDs, invented text)."""
import json
import os
import re
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
jd = SourceFileLoader("romp_judge_courierdefer", os.path.join(BIN, "romp-judge")).load_module()

RECIP = "11111111-2222-3333-4444-555555555555"
SENDER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MID = "1781100000.11111_22222.TESTHOST"
T0 = 1781100000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class CourierDeferAbandon(unittest.TestCase):
    def setUp(self):
        # chain-rooted minting (2026-08-25) gates recipient tops on a user-rooted sender chain —
        # ORTHOGONAL to this file's subject, so the gate is held open here; its own truth table
        # lives in tests/test_chain_rooted_minting.py
        self._rooted_saved = jd._delegate_user_rooted
        jd._delegate_user_rooted = lambda *a, **k: True
        self.addCleanup(lambda: setattr(jd, "_delegate_user_rooted", self._rooted_saved))
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        (proj / munged).mkdir(parents=True)
        # recipient: a delivered peer (postal) message + a reply; its body carries the romp-msg-id marker
        recip_recs = [uline(T0, "DELEGATE: wire up the export button\n<!-- romp-msg-id: %s -->" % MID, "u1"),
                      aline(T0 + 30, "On it.", "a1", "u1")]
        (proj / munged / (RECIP + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in recip_recs) + "\n")
        # sender: a minimal transcript so it's discoverable (its goal store is auto-created empty)
        (proj / munged / (SENDER + ".jsonl")).write_text(
            json.dumps(uline(T0 - 10, "start the export work", "s1")) + "\n")
        names = td / "names"; names.mkdir()
        (names / RECIP).write_text("recip\t%s\t#abcdef\n" % str(cdir))
        (names / SENDER).write_text("sender\t%s\t#fedcba\n" % str(cdir))
        # the postal index: MID -> SENDER, so the event model authors the message atom {"peer": SENDER}
        tl = td / "timeline"; tl.mkdir()
        (tl / "messages.jsonl").write_text(json.dumps({"id": MID, "from_id": SENDER}) + "\n")

        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.MESSAGES, jd.ERRORS, jd.courier_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.MESSAGES = tl / "messages.jsonl"
        jd.ERRORS = td / "judge-errors.jsonl"
        jd._PARSE_CACHE.clear()
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.MESSAGES, jd.ERRORS, jd.courier_llm) = self.saved
        self.td.cleanup()

    def _seg_id(self):
        session = jd.parsed_session(RECIP, [str(jd.PROJECTS / os.listdir(jd.PROJECTS)[0] / (RECIP + ".jsonl"))], T0 + 100)
        peer = next(s for turn in session["turns"] for s in jd._segs(turn, jd.load_goals(RECIP)) if jd._seg_peer(s))
        return peer["id"]

    def _rows(self, err):
        try:
            rows = [json.loads(l) for l in jd.ERRORS.read_text().splitlines()]
        except OSError:
            return []
        return [r for r in rows if r.get("err") == err and r.get("judge") == "courier"]

    def test_empty_reply_defers_not_places(self):
        """A usage-limited (empty) courier reply leaves the segment UNPLACED, records a courierDeferred
        marker, and logs exactly ONE 'deferred' row across repeated failing passes — never a give-up."""
        jd.courier_llm = lambda *a, **k: ""                # simulate the rate-gated empty reply
        seg_id = self._seg_id()
        jd.run_courier(now=T0 + 100)
        store = jd.load_goals(RECIP)
        self.assertNotIn(seg_id, store["placements"], "an empty reply never places the segment")
        self.assertIn(seg_id, store.get("courierDeferred", {}), "the deferral is recorded per-segment")
        self.assertEqual(store["nodes"], {}, "nothing planted on a deferred message")
        # a second failing pass must NOT re-log the row (one per deferral, not one per quiet pass)
        jd.run_courier(now=T0 + 200)
        self.assertEqual(len(self._rows("deferred")), 1, "deferred logs exactly once, not per pass")
        self.assertEqual(self._rows("give-up"), [], "a doomed call is never a give-up strike")
        self.assertIn(seg_id, jd.load_goals(RECIP).get("courierDeferred", {}), "still deferred, still retrying")

    def test_abandons_past_the_retry_horizon(self):
        """Past COURIER_RETRY_HORIZON the message is abandoned: marked 'fyi', deferral dropped, give-up logged."""
        jd.courier_llm = lambda *a, **k: ""
        seg_id = self._seg_id()
        jd.run_courier(now=T0 + 100)                        # defer it first (fresh)
        self.assertIn(seg_id, jd.load_goals(RECIP).get("courierDeferred", {}))
        jd.run_courier(now=T0 + jd.COURIER_RETRY_HORIZON + 100)   # now it's aged out
        store = jd.load_goals(RECIP)
        self.assertEqual(store["placements"].get(seg_id), "fyi", "aged-out message is abandoned (processed)")
        self.assertNotIn(seg_id, store.get("courierDeferred", {}), "the deferral marker is dropped on abandon")
        gu = self._rows("give-up")
        self.assertEqual(len(gu), 1, "abandonment logs one give-up row")
        self.assertIn("retry horizon", gu[0]["note"])
        self.assertEqual(gu[0].get("seg"), seg_id, "the give-up row names the abandoned segment")

    def test_recovery_clears_the_deferral_and_places(self):
        """Once the limit lifts (a real reply), the deferral clears and the message is summarized normally."""
        jd.courier_llm = lambda *a, **k: ""
        seg_id = self._seg_id()
        jd.run_courier(now=T0 + 100)                        # deferred while limited
        self.assertIn(seg_id, jd.load_goals(RECIP).get("courierDeferred", {}))
        jd.courier_llm = lambda *a, **k: '{"verdict": "delegating", "goal": 0, "text": "wire up export button"}'
        jd.run_courier(now=T0 + 200)                        # limit lifted → a real verdict lands
        store = jd.load_goals(RECIP)
        self.assertNotIn(seg_id, store.get("courierDeferred", {}), "a landed reply clears the deferral")
        self.assertIn(seg_id, store["placements"], "the message is now placed")
        planted = [nd for nd in store["nodes"].values() if (nd.get("origin") or {}).get("msgId") == MID]
        self.assertEqual(len(planted), 1, "the delegation goal is planted for the message")

    def test_horizon_matches_discover_window(self):
        self.assertEqual(jd.COURIER_RETRY_HORIZON, jd.WINDOW,
                         "the retry horizon tracks discover()'s 48h WINDOW")


if __name__ == "__main__":
    unittest.main()
