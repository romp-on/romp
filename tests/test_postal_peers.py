#!/usr/bin/env python3
"""Peer-bus mode stage 1 (plans/postal-peer-buses.md): every machine runs its OWN bus — the
client-only special case is retired under the flag — and the kernel feeds the bus a peer table
over POST /peer on tunnel transitions. Synthetic only."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = SourceFileLoader("romp_postal_peers", os.path.join(BIN, "romp-postal-service")).load_module()


class PeerMode(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        pm.PEERS.clear()

    def test_flag_retires_client_only(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"
        try:
            self.assertFalse(pm.is_client_only(),
                             "peer mode: every machine runs its own bus — client-only is retired")
        finally:
            os.environ.pop("ROMP_POSTAL_CLIENT_ONLY", None)

    def test_flag_off_client_only_unchanged(self):
        os.environ["ROMP_POSTAL_PEERS"] = "0"          # peer mode is the DEFAULT now; 0 = legacy scheme
        os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"
        try:
            self.assertTrue(pm.is_client_only(), "legacy mode: the singleton scheme is untouched")
        finally:
            os.environ.pop("ROMP_POSTAL_CLIENT_ONLY", None)

    def test_peers_on_is_the_default(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        self.assertTrue(pm.peers_on(), "peer-bus mode is the default (the user's activation, 2026-07-20)")
        os.environ["ROMP_POSTAL_PEERS"] = "0"
        self.assertFalse(pm.peers_on(), "explicit 0 selects the legacy scheme")

    def test_peer_update_and_snapshot(self):
        payload, status = pm.peer_update({"host": "TESTHOST", "port": 50002, "up": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["up"], 1)
        snap = pm.peers_snapshot()["peers"]["TESTHOST"]
        self.assertEqual((snap["port"], snap["up"]), (50002, True))
        payload, status = pm.peer_update({"host": "TESTHOST", "port": 50002, "up": False})
        self.assertEqual(pm.peers_snapshot()["peers"]["TESTHOST"]["up"], False,
                         "a down transition keeps the row for introspection, marked down")
        self.assertEqual(payload["up"], 0)

    def test_peer_update_validates(self):
        for bad in ({}, {"host": "", "port": 1}, {"host": "h"}, {"host": "h", "port": "x"},
                    {"host": "h", "port": 0}, {"host": "h", "port": True}):
            payload, status = pm.peer_update(bad)
            self.assertEqual(status, 400, "rejected: %r" % (bad,))
        self.assertEqual(pm.PEERS, {}, "nothing recorded from rejected notifies")

    def test_origin_only_row_stores_trust_without_a_port(self):
        # Trust-by-origin (the user 2026-07-25): a tier for a host with no tunnel here. Portless,
        # no dialer, judged at delivery by true origin.
        payload, status = pm.peer_update({"host": "FARBOX", "trust": "trusted", "originOnly": True})
        self.assertEqual(status, 200)
        self.assertTrue(payload["originOnly"])
        row = pm.peers_snapshot()["peers"]["FARBOX"]
        self.assertEqual((row["port"], row["up"], row["trust"], row.get("originOnly")),
                         (None, False, "trusted", True))
        # applied to a CONNECTED row it touches only the trust — port/up/token survive
        pm.peer_update({"host": "HUB", "port": 50007, "up": True, "token": "tk", "trust": "trusted"})
        pm.peer_update({"host": "HUB", "trust": "directed", "originOnly": True})
        row = pm.peers_snapshot()["peers"]["HUB"]
        self.assertEqual((row["port"], row["up"], row["token"], row["trust"], row.get("originOnly")),
                         (50007, True, "tk", "directed", None))

    def test_origin_only_validates(self):
        for bad in ({"originOnly": True}, {"host": "h", "originOnly": True},
                    {"host": "h", "trust": "bogus", "originOnly": True}):
            payload, status = pm.peer_update(bad)
            self.assertEqual(status, 400, "rejected: %r" % (bad,))

    def test_via_reach_summarizes_far_spokes(self):
        pm.PEER_STATE.clear()
        try:
            import time as _t
            pm.PEER_STATE["hub"] = {"presence": [
                {"name": "a", "id": "1"},                       # the hub's own session — not via
                {"name": "b", "id": "2", "via": "FARBOX"},
                {"name": "c", "id": "3", "via": "FARBOX"},
                {"name": "d", "id": "4", "via": "PEERED"},      # directly peered here → excluded
            ], "seenAt": int(_t.time())}
            pm.peer_update({"host": "PEERED", "port": 50008, "up": True})
            pm.peer_update({"host": "FARBOX", "trust": "isolated", "originOnly": True})
            rows = pm.via_reach()
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual((r["host"], r["via"], r["agents"], r["trust"]),
                             ("FARBOX", "hub", 2, "isolated"))
            self.assertEqual(pm.peers_snapshot()["viaReach"], rows,
                             "the snapshot carries the summary for the kernel's popover proxy")
        finally:
            pm.PEER_STATE.clear()

    def test_routes_are_wired(self):
        import inspect
        src = inspect.getsource(pm)
        self.assertIn('if u.path == "/peer":', src)
        self.assertIn('if u.path == "/peers":', src)


if __name__ == "__main__":
    unittest.main()


_B_STATE = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _B_STATE
pmb = SourceFileLoader("romp_postal_peers_b", os.path.join(BIN, "romp-postal-service")).load_module()


class TwoBusExchange(unittest.TestCase):
    """The two-bus harness (plans/postal-peer-buses.md): A and B are two module instances with
    separate state dirs; the "tunnel" is a direct call — A builds a request, B handles it, A applies
    the response. Covers mail both directions, end-to-end acks, dedupe on a resent relay, bounce to
    the sender on a dead recipient, presence gossip, and the version handshake."""

    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        self._saved = (pm.self_host, pmb.self_host, pm.local_agents, pmb.local_agents)
        pm.self_host = lambda: "hosta"
        pmb.self_host = lambda: "hostb"
        pm.local_agents = lambda: [{"name": "alpha", "id": "sid-a", "dir": ""}]
        pmb.local_agents = lambda: [{"name": "beta", "id": "sid-b", "dir": ""}]
        for m in (pm, pmb):
            m.PEER_STATE.clear()
            m.PEERS.clear()
            m._peer_pending.clear()
            m._seen_ids = None
        import shutil
        for m in (pm, pmb):
            shutil.rmtree(m.OUTBOX, ignore_errors=True)
            shutil.rmtree(m.MAILROOT, ignore_errors=True)
            try:
                m.PEER_SEEN.unlink()
            except Exception:
                pass
            m.MAILROOT.mkdir(parents=True, exist_ok=True)
        # In production the kernel's /peer notify (peer_update) populates PEERS with each host's trust,
        # and the inbound gate HOLDS a directed peer's mail. These tests exercise the exchange/relay
        # MECHANICS, so mark the exchanged peers trusted (the gate keys on the relay's origin host: B sees
        # A's self_host "hosta"; A's dialer-apply uses the "srv" alias). Trust itself is covered in
        # test_postal_quarantine.py.
        pmb.PEERS["hosta"] = {"port": 1, "up": True, "trust": "trusted"}
        pm.PEERS["srv"] = {"port": 1, "up": True, "trust": "trusted"}

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        pm.self_host, pmb.self_host, pm.local_agents, pmb.local_agents = self._saved

    def _exchange(self):
        req = pm.build_exchange_request("srv", wait=False)
        resp, status = pmb.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        pm.peer_exchange_apply("srv", req, resp)
        return resp

    def test_quarantine_holds_cross_the_exchange_both_ways(self):
        # Slice 4 of the federation UI (the user 2026-07-25): each side's exchange payload carries a
        # summary of ITS held mail, so a hold is visible from the peer instead of only on the
        # holding machine's own dashboard.
        import shutil
        for m in (pm, pmb):
            shutil.rmtree(m.QUARANTINE, ignore_errors=True)
        try:
            pmb.QUARANTINE.mkdir(parents=True, exist_ok=True)
            (pmb.QUARANTINE / "h1.json").write_text(json.dumps(
                {"mid": "h1", "to": "beta", "frm": "api", "origin": "TESTHOST",
                 "body": "please review the parser fix before it merges", "at": 1000}))
            pm.QUARANTINE.mkdir(parents=True, exist_ok=True)
            (pm.QUARANTINE / "h2.json").write_text(json.dumps(
                {"mid": "h2", "to": "alpha", "frm": "web", "origin": "", "body": "ping", "at": 1001}))
            self._exchange()
            got = pm.PEER_STATE["srv"]["holds"]
            self.assertEqual([(h["mid"], h["frm"], h["to"], h["origin"]) for h in got],
                             [("h1", "api", "beta", "TESTHOST")], "B's hold arrived at A")
            self.assertIn("please review the parser fix", got[0]["gist"])
            self.assertEqual(pm.remote_holds()[0]["atHost"], "srv",
                             "stamped with the machine HOLDING it")
            got_b = pmb.PEER_STATE["hosta"]["holds"]
            self.assertEqual([h["mid"] for h in got_b], ["h2"], "A's hold rode the request to B")
        finally:
            for m in (pm, pmb):
                shutil.rmtree(m.QUARANTINE, ignore_errors=True)

    def test_mail_crosses_and_acks_clear_the_outbox(self):
        pm.outbox_put("srv", {"mid": "m1", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "hello over the wire", "kind": "question", "t": 1})
        self._exchange()
        box = pmb.read_box("sid-b", consume=True)
        self.assertEqual(len(box), 1, "the relay delivered on B")
        self.assertIn("hello over the wire", box[0]["body"])
        self.assertEqual(box[0]["kind"], "question", "the declared kind rides the relay")
        self.assertEqual(pm.outbox_list("srv"), [], "B's ack cleared A's outbox")
        self.assertEqual(pmb.PEER_STATE["hosta"]["presence"][0]["name"], "alpha", "presence gossiped A to B")
        self.assertEqual(pm.PEER_STATE["srv"]["presence"][0]["name"], "beta", "presence gossiped B to A")

    def test_resent_relay_delivers_exactly_once(self):
        pm.outbox_put("srv", {"mid": "m2", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "once", "kind": "", "t": 1})
        req = pm.build_exchange_request("srv", wait=False)
        r1, _ = pmb.peer_exchange_handle(req)
        r2, _ = pmb.peer_exchange_handle(req)          # the link flapped before the ack → A resent
        self.assertIn("m2", r1["acks"])
        self.assertIn("m2", r2["acks"], "the duplicate is re-acked, never re-delivered")
        self.assertEqual(len(pmb.read_box("sid-b", consume=True)), 1, "exactly one delivery")

    def test_dead_recipient_bounces_to_the_sender(self):
        pm.outbox_put("srv", {"mid": "m3", "to": "ghost", "frm": "alpha", "frm_id": "sid-a",
                              "body": "boo", "kind": "", "t": 1})
        self._exchange()
        self.assertEqual(pm.outbox_list("srv"), [], "a definitive refusal never stays parked")
        back = pm.read_box("sid-a", consume=True)
        self.assertEqual(len(back), 1, "the sender got the bounce note")
        self.assertIn("undeliverable to 'ghost'", back[0]["body"])
        self.assertEqual(back[0]["from"], "romp-postal", "bus-authored, clearly not a peer message")

    def test_return_mail_rides_the_response_and_acks_the_next_request(self):
        pmb.outbox_put("hosta", {"mid": "m4", "to": "alpha", "frm": "beta", "frm_id": "sid-b",
                                 "body": "reply", "kind": "", "t": 1})
        self._exchange()
        self.assertEqual(len(pm.read_box("sid-a", consume=True)), 1, "B-to-A mail rode the response")
        self.assertEqual(len(pmb.outbox_list("hosta")), 1, "B holds it until the end-to-end ack")
        self._exchange()
        self.assertEqual(pmb.outbox_list("hosta"), [], "the next request's ack cleared B's outbox")

    def test_version_drift_refuses_politely(self):
        req = pm.build_exchange_request("srv", wait=False)
        req["proto"] = 999
        resp, status = pmb.peer_exchange_handle(req)
        self.assertEqual(status, 409)
        self.assertIn("drift", resp["error"])

    def test_peer_route_resolves_and_disambiguates(self):
        pm.PEER_STATE["srv"] = {"presence": [{"name": "beta", "id": "sid-b"}], "seenAt": 1}
        pm.PEER_STATE["other"] = {"presence": [{"name": "beta", "id": "sid-c"}], "seenAt": 1}
        host, hits = pm.peer_route("beta")
        self.assertIsNone(host, "two hosts own 'beta' → ambiguous")
        self.assertEqual(len(hits), 2)
        host, hit = pm.peer_route("srv:beta")
        self.assertEqual(host, "srv", "host:name breaks the tie")
        self.assertEqual(hit["id"], "sid-b")


_C_STATE = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _C_STATE
pmc = SourceFileLoader("romp_postal_peers_c", os.path.join(BIN, "romp-postal-service")).load_module()


class ThreeBusRelay(unittest.TestCase):
    """Spoke-to-spoke through a shared hub (plans/postal-peer-buses.md 3b): A and C each exchange only
    with hub B. Presence gossips one hop with a `via` label; a relay for a far spoke forwards ONE hop
    with end-to-end acks relayed backward, so the origin keeps mail parked until the FAR side delivers."""

    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        self._saved = (pm.self_host, pmb.self_host, pmc.self_host,
                       pm.local_agents, pmb.local_agents, pmc.local_agents)
        pm.self_host = lambda: "hosta"
        pmb.self_host = lambda: "hostb"
        pmc.self_host = lambda: "hostc"
        pm.local_agents = lambda: [{"name": "alpha", "id": "sid-a", "dir": ""}]
        pmb.local_agents = lambda: [{"name": "beta", "id": "sid-b", "dir": ""}]
        pmc.local_agents = lambda: [{"name": "carol", "id": "sid-c", "dir": ""}]
        import shutil
        for m in (pm, pmb, pmc):
            m.PEER_STATE.clear()
            m.PEERS.clear()
            m._peer_pending.clear()
            m._seen_ids = None
            shutil.rmtree(m.OUTBOX, ignore_errors=True)
            shutil.rmtree(m.MAILROOT, ignore_errors=True)
            try:
                m.PEER_SEEN.unlink()
            except Exception:
                pass
            m.MAILROOT.mkdir(parents=True, exist_ok=True)
        # Mechanics test → mark the origin trusted so the inbound gate delivers (see TwoBusExchange.setUp).
        # The gate keys on the relay's ORIGIN host: B delivers to beta and C delivers a forwarded message
        # whose origin is "hosta" (B stamps origin when it forwards). Trust itself: test_postal_quarantine.
        pmb.PEERS["hosta"] = {"port": 1, "up": True, "trust": "trusted"}
        pmc.PEERS["hosta"] = {"port": 1, "up": True, "trust": "trusted"}
        # A forwarded message is ALSO capped at the forwarder's own tier (2026-08-05: a relay must
        # not out-rank itself by stamping an origin — test_postal_quarantine owns that rule), so the
        # hub C actually exchanges with needs a tier of its own. A real deployment always has one:
        # the kernel notifies a row for every host you dial. Without it the hub reads as an unknown
        # host, i.e. directed, and the relayed mail is HELD — correct, but the trust tests' business,
        # not this one's, which is about relay mechanics and end-to-end acks.
        pmc.PEERS["hub"] = {"port": 1, "up": True, "trust": "trusted"}

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        (pm.self_host, pmb.self_host, pmc.self_host,
         pm.local_agents, pmb.local_agents, pmc.local_agents) = self._saved

    def _xchg(self, dialer, dialed, alias):
        req = dialer.build_exchange_request(alias, wait=False)
        resp, status = dialed.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        dialer.peer_exchange_apply(alias, req, resp)

    def test_far_spoke_gossips_via_the_hub(self):
        self._xchg(pmc, pmb, "hub")                  # B learns carol
        self._xchg(pm, pmb, "hub")                   # A learns beta directly and carol via the hub
        names = {(a.get("name"), a.get("via")) for a in pm.PEER_STATE["hub"]["presence"]}
        self.assertIn(("beta", None), names)
        self.assertIn(("carol", "hostc"), names, "the far spoke arrives labeled via, one hop only")

    def test_far_holds_gossip_via_the_hub_one_hop_only(self):
        # A hold TWO machines away (on C) reaches A labeled via the hub — and never re-gossips
        # further (the same one-hop rule as presence).
        import shutil
        for m in (pm, pmb, pmc):
            shutil.rmtree(m.QUARANTINE, ignore_errors=True)
        try:
            pmc.QUARANTINE.mkdir(parents=True, exist_ok=True)
            (pmc.QUARANTINE / "h9.json").write_text(json.dumps(
                {"mid": "h9", "to": "carol", "frm": "ops", "origin": "RENTBOX",
                 "body": "held on the far spoke", "at": 1002}))
            self._xchg(pmc, pmb, "hub")              # B learns C's hold (direct, no via)
            self._xchg(pm, pmb, "hub")               # A learns it via the hub
            got = [h for h in pm.PEER_STATE["hub"]["holds"] if h["mid"] == "h9"]
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].get("via"), "hostc", "labeled with the machine holding it")
            self.assertEqual([r["atHost"] for r in pm.remote_holds() if r["mid"] == "h9"],
                             ["hostc"])
            self.assertNotIn("via", [k for h in pmb.PEER_STATE["hostc"]["holds"] for k in h
                                     if k == "via"], "the direct hop carries no via label")
            # A never re-gossips the via-labeled hold onward (one hop, like presence)
            self.assertEqual([h for h in pm.holds_payload("elsewhere") if h["mid"] == "h9"], [])
        finally:
            for m in (pm, pmb, pmc):
                shutil.rmtree(m.QUARANTINE, ignore_errors=True)

    def test_relay_hops_once_with_end_to_end_acks(self):
        self._xchg(pmc, pmb, "hub")
        self._xchg(pm, pmb, "hub")
        host, hit = pm.peer_route("carol")
        self.assertEqual(host, "hub", "A reaches carol through the peer it can dial")
        pm.outbox_put("hub", {"mid": "r1", "to": "carol", "frm": "alpha", "frm_id": "sid-a",
                              "body": "over the hub", "kind": "delegate", "t": 1})
        self._xchg(pm, pmb, "hub")                   # A→B: B forwards, does NOT ack yet
        self.assertEqual(len(pm.outbox_list("hub")), 1,
                         "the origin keeps it parked until the FAR side's ack (end-to-end)")
        self.assertEqual(len(pmb.outbox_list("hostc")), 1, "the hub holds it forwarded for C")
        self._xchg(pmc, pmb, "hub")                  # C→B: the response carries the relay → C delivers
        box = pmc.read_box("sid-c", consume=True)
        self.assertEqual(len(box), 1, "delivered on the far spoke")
        self.assertIn("over the hub", box[0]["body"])
        self._xchg(pmc, pmb, "hub")                  # C's next request acks → B routes it backward
        self.assertEqual(pmb.outbox_list("hostc"), [], "C's ack cleared the hub's forward")
        self._xchg(pm, pmb, "hub")                   # A's next exchange picks the relayed ack up
        self.assertEqual(pm.outbox_list("hub"), [], "the end-to-end ack finally clears the origin")

    def test_far_bounce_relays_backward_to_the_sender(self):
        self._xchg(pmc, pmb, "hub")
        self._xchg(pm, pmb, "hub")
        pm.outbox_put("hub", {"mid": "r2", "to": "carol", "frm": "alpha", "frm_id": "sid-a",
                              "body": "too late", "kind": "", "t": 1})
        self._xchg(pm, pmb, "hub")                   # forwarded
        pmc.local_agents = lambda: []                # carol died before delivery
        self._xchg(pmc, pmb, "hub")                  # C receives the relay → bounces it
        self._xchg(pmc, pmb, "hub")                  # C's bounce rides its next request → B routes backward
        self._xchg(pm, pmb, "hub")                   # A picks the bounce up → sender gets the note
        back = pm.read_box("sid-a", consume=True)
        self.assertEqual(len(back), 1, "the far refusal came all the way back")
        self.assertIn("undeliverable to 'carol'", back[0]["body"])
        self.assertEqual(pm.outbox_list("hub"), [], "nothing left parked after a definitive refusal")

    def test_a_hopped_message_never_hops_again(self):
        m = {"mid": "r3", "to": "nobody-anywhere", "frm": "alpha", "frm_id": "sid-a",
             "body": "x", "kind": "", "t": 1, "origin": "hosta"}
        pmb.PEER_STATE["hostc"] = {"presence": [{"name": "nobody-anywhere", "id": "sid-x"}], "seenAt": 1}
        verdict, bounce = pmb._relay_in("hosta", m)
        self.assertEqual(verdict, "bounce", "one hop max: an already-hopped message bounces, never re-forwards")
        self.assertIn("no live session", bounce["why"])


class RecallAndReceipts(unittest.TestCase):
    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        import shutil
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        try:
            (pm.TLDIR / "messages.jsonl").unlink()
        except Exception:
            pass

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def test_recall_reaches_the_outbox(self):
        pm.outbox_put("srv", {"mid": "q1", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "changed my mind", "kind": "", "t": 1})
        removed = pm._recall("sid-a", "", "q1")
        self.assertEqual([r["id"] for r in removed], ["q1"], "a recall that beats the truck wins")
        self.assertEqual(pm.outbox_list("srv"), [], "the parked message is gone")

    def test_recall_never_touches_forwarded_mail(self):
        pm.outbox_put("srv", {"mid": "q2", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "not mine to recall here", "kind": "", "t": 1, "origin": "hostz"})
        self.assertEqual(pm._recall("sid-a", "", "q2"), [], "forwarded mail belongs to the origin's sender")
        self.assertEqual(len(pm.outbox_list("srv")), 1)

    def test_receipts_show_parked_then_relayed(self):
        pm.outbox_put("srv", {"mid": "q3", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "hi", "kind": "", "t": 1})
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "q3", "from": "alpha",
                                         "from_id": "sid-a", "to_id": "peer:srv",
                                         "toName": "srv:beta", "body": "hi", "kind": ""})
        row = pm._sent_receipts("sid-a")[-1]
        self.assertEqual((row["to"], row["parked"]), ("srv:beta", "srv"), "parked shows, honestly")
        pm._ack_arrived("srv", "q3")                 # the end-to-end ack lands
        row = pm._sent_receipts("sid-a")[-1]
        self.assertEqual(row["parked"], None)
        self.assertTrue(row["relayed"], "delivery confirmation replaces parked")

    def test_a_parked_receipt_carries_the_link_state(self):
        # outbox residency alone is not unreachability (the user 2026-08-24): the receipt row now
        # rides the authoritative dial state the send path already branches on, so the client can
        # say "queued for relay" on a healthy link and "unreachable" only on a real dial failure
        self.addCleanup(lambda: pm.PEERS.pop("srv", None))   # a mid-test failure must not leak link state
        pm.outbox_put("srv", {"mid": "q9", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "hi", "kind": "", "t": 1})
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "q9", "from": "alpha",
                                         "from_id": "sid-a", "to_id": "peer:srv",
                                         "toName": "srv:beta", "body": "hi", "kind": ""})
        pm.PEERS["srv"] = {"up": True}
        self.assertTrue(pm._sent_receipts("sid-a")[-1]["parkedUp"], "healthy link -> queued, not lost")
        pm.PEERS["srv"] = {"up": False}
        self.assertFalse(pm._sent_receipts("sid-a")[-1]["parkedUp"], "down link -> honestly unreachable")
        pm.PEERS.pop("srv", None)
        self.assertFalse(pm._sent_receipts("sid-a")[-1]["parkedUp"],
                         "no tunnel record at all reads down, matching the send path's branch")
        row = pm._sent_receipts("sid-a")[-1]
        self.assertEqual(row["parked"], "srv", "the parked key keeps its host-string shape")
