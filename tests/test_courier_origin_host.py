#!/usr/bin/env python3
"""Courier origin provenance snapshots the sender's name + host at plant time (the user 2026-07-26).

A goal planted from a FEDERATED peer's message carries origin.peer = the sender's sid — which the
recipient kernel's names registry can never resolve (the sender lives on another host), so the feed's
"↪ from" chip degraded to a bare sid prefix. The postal bus now stamps the origin host on cross-host
delivery (messages.jsonl `from_host`), and run_courier snapshots {peerName, peerHost} into the planted
goal's origin: the live local name when the sender is a session of THIS kernel (no host), else the
message log's name + host.

Synthetic fixtures only (placeholder UUIDs, invented text, hostname TESTHOST)."""
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
jd = SourceFileLoader("romp_judge_courierorigin", os.path.join(BIN, "romp-judge")).load_module()

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


class CourierOriginHost(unittest.TestCase):
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
        self.proj_dir = proj / munged
        recip_recs = [uline(T0, "DELEGATE: wire up the export button\n<!-- romp-msg-id: %s -->" % MID, "u1"),
                      aline(T0 + 30, "On it.", "a1", "u1")]
        (self.proj_dir / (RECIP + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in recip_recs) + "\n")
        names = td / "names"; names.mkdir()
        (names / RECIP).write_text("recip\t%s\t#abcdef\n" % str(cdir))
        self.names, self.cdir, self.tl = names, cdir, td / "timeline"
        self.tl.mkdir()

        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.MESSAGES, jd.ERRORS, jd.courier_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.MESSAGES = self.tl / "messages.jsonl"
        jd.ERRORS = td / "judge-errors.jsonl"
        jd.courier_llm = lambda *a, **k: '{"verdict": "delegating", "goal": 0, "text": "wire up the export button"}'
        jd._PARSE_CACHE.clear()
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd._postal_from_memo["key"] = None

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.MESSAGES, jd.ERRORS, jd.courier_llm) = self.saved
        jd._postal_from_memo["key"] = None
        self.td.cleanup()

    def _planted_origin(self):
        jd.run_courier(now=T0 + 100)
        store = jd.load_goals(RECIP)
        planted = [nd for nd in store["nodes"].values() if isinstance(nd.get("origin"), dict)]
        self.assertEqual(len(planted), 1, "the delegating message plants exactly one goal")
        return planted[0]["origin"]

    def test_federated_sender_origin_carries_log_name_and_host(self):
        """A cross-host sender has no local names entry and no local transcript; the origin snapshots
        the message log's `from` + `from_host` so the chip can say host:name, never a sid stub."""
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0 - 5, "ev": "sent", "id": MID, "from": "api", "from_id": SENDER,
             "to_id": RECIP, "from_host": "TESTHOST2", "body": "DELEGATE: wire up the export button"}) + "\n")
        origin = self._planted_origin()
        self.assertEqual(origin["peer"], SENDER)
        self.assertEqual(origin["peerName"], "api")
        self.assertEqual(origin["peerHost"], "TESTHOST2")

    def test_local_sender_prefers_live_name_and_carries_no_host(self):
        """A sender that is a session of THIS kernel resolves through discover's id2name (the live
        registry name, which tracks renames) and gets NO peerHost — local mail is never host-stamped."""
        (self.proj_dir / (SENDER + ".jsonl")).write_text(
            json.dumps(uline(T0 - 10, "start the export work", "s1")) + "\n")
        (self.names / SENDER).write_text("sender\t%s\t#fedcba\n" % str(self.cdir))
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0 - 5, "ev": "sent", "id": MID, "from": "sender", "from_id": SENDER,
             "to_id": RECIP, "body": "DELEGATE: wire up the export button"}) + "\n")
        jd._discover_cache["fp"] = None
        origin = self._planted_origin()
        self.assertEqual(origin["peerName"], "sender")
        self.assertNotIn("peerHost", origin)


if __name__ == "__main__":
    unittest.main()
