#!/usr/bin/env python3
"""Postal addressing checks IDENTITY and refuses ambiguity, instead of tiebreaking silently.

A session reported (2026-07-29) that send_message to its OWN name returned "Delivered" three
times and each report arrived back in its own inbox rendered exactly like a peer's message. The
loopback is indistinguishable from a real reply, so it actively CONFIRMS that the peer is
reachable and answering while the reports go nowhere. The cause is that a recipient reference is
an unqualified session NAME, unique only by convention, and the resolver took the first match --
whose worst possible tiebreak is the sender itself.

So: self is a hard error, more than one candidate is a hard error that names the alternatives,
'(you)' is decided by session id rather than by name, mail that did arrive from yourself says so,
and "Delivered" is reserved for actually delivered. Synthetic ids and hostnames only.
"""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = SourceFileLoader("romp_postal_self_send", os.path.join(BIN, "romp-postal-service")).load_module()

ME = "11111111-2222-3333-4444-555555555555"
PEER = "22222222-3333-4444-5555-666666666666"
TWIN = "33333333-4444-5555-6666-777777777777"


class ResolverBase(unittest.TestCase):
    """Drives resolve_recipient over a synthetic fleet. Every collaborator it reads is a
    module-level function, so a plain attribute swap is the whole harness."""

    def setUp(self):
        self._saved = {k: getattr(pm, k) for k in
                       ("all_agents", "self_host", "peers_on", "_postal_off")}
        self._agents = []
        self._isolated = set()
        pm.all_agents = lambda threads=False: list(self._agents)
        pm.self_host = lambda: "TESTHOST"
        pm.peers_on = lambda: True
        pm._postal_off = lambda sid: sid in self._isolated
        pm.PEER_STATE.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(pm, k, v)
        pm.PEER_STATE.clear()

    def live(self, name, sid, remote=False):
        self._agents.append({"name": name, "id": sid, "remote": remote})

    def on_peer(self, host, name, sid):
        pm.PEER_STATE.setdefault(host, {"presence": [], "seenAt": 1})
        pm.PEER_STATE[host]["presence"].append({"name": name, "id": sid})


class SelfSendRefused(ResolverBase):
    def test_own_name_is_a_hard_error(self):
        self.live("web", ME)
        res = pm.resolve_recipient("web", ME)
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["status"], 409)
        self.assertIn("THIS session's own name", res["error"])
        self.assertIn("(you)", res["error"], "point them at the list_agents row that proves it")

    def test_self_refused_even_when_a_peer_shares_the_name(self):
        # The reported case: a peer on another host answered to the same name, so the reply the
        # sender was waiting for could never come -- it was mailing itself.
        self.live("web", ME)
        self.on_peer("otherhost", "web", PEER)
        res = pm.resolve_recipient("web", ME)
        self.assertEqual(res["kind"], "error")
        self.assertIn("host:name", res["error"], "tell them how to reach the peer they meant")

    def test_self_by_host_qualified_own_name_is_still_self(self):
        self.live("web", ME)
        res = pm.resolve_recipient("TESTHOST:web", ME)
        self.assertEqual(res["kind"], "error")
        self.assertIn("own name", res["error"])

    def test_a_peer_with_my_name_is_reachable_when_host_qualified(self):
        self.live("web", ME)
        self.on_peer("otherhost", "web", PEER)
        res = pm.resolve_recipient("otherhost:web", ME)
        self.assertEqual(res["kind"], "relay")
        self.assertEqual(res["host"], "otherhost")
        self.assertEqual(res["agent"]["id"], PEER)

    def test_no_sender_id_still_resolves(self):
        # CLI/legacy callers may not carry a from_id; the self check simply does not apply.
        self.live("web", ME)
        self.assertEqual(pm.resolve_recipient("web", "")["kind"], "direct")


class AmbiguityRefused(ResolverBase):
    def test_two_local_sessions_with_one_name(self):
        self.live("web", ME)
        self.live("web", TWIN)
        res = pm.resolve_recipient("web", PEER)
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["status"], 409)
        self.assertIn("ambiguous", res["error"])
        self.assertIn(ME[:8], res["error"], "identify the candidates that no address can separate")
        self.assertIn(TWIN[:8], res["error"])
        self.assertIn("renamed", res["error"])

    def test_local_and_peer_with_one_name(self):
        # Previously the local row silently won and the peer was never considered.
        self.live("web", ME)
        self.on_peer("otherhost", "web", PEER)
        res = pm.resolve_recipient("web", TWIN)
        self.assertEqual(res["kind"], "error")
        self.assertIn("TESTHOST:web", res["error"])
        self.assertIn("otherhost:web", res["error"])

    def test_host_qualifier_resolves_the_tie_both_ways(self):
        self.live("web", ME)
        self.on_peer("otherhost", "web", PEER)
        here = pm.resolve_recipient("TESTHOST:web", TWIN)
        self.assertEqual(here["kind"], "direct")
        self.assertEqual(here["agent"]["id"], ME)
        there = pm.resolve_recipient("otherhost:web", TWIN)
        self.assertEqual(there["kind"], "relay")
        self.assertEqual(there["host"], "otherhost")

    def test_two_peer_hosts_with_one_name(self):
        self.on_peer("hosta", "web", PEER)
        self.on_peer("hostb", "web", TWIN)
        res = pm.resolve_recipient("web", ME)
        self.assertEqual(res["kind"], "error")
        self.assertIn("hosta:web", res["error"])
        self.assertIn("hostb:web", res["error"])

    def test_an_isolated_twin_does_not_make_a_name_ambiguous(self):
        # An isolated session can't receive at all, so it is not a candidate -- the one reachable
        # session still resolves, exactly as before.
        self.live("web", ME)
        self.live("web", TWIN)
        self._isolated.add(TWIN)
        res = pm.resolve_recipient("web", PEER)
        self.assertEqual(res["kind"], "direct")
        self.assertEqual(res["agent"]["id"], ME)


class UnchangedBehaviour(ResolverBase):
    """The refusals above are additive: every pre-existing outcome still comes out the same."""

    def test_unambiguous_local_name_resolves(self):
        self.live("api", PEER)
        res = pm.resolve_recipient("api", ME)
        self.assertEqual(res["kind"], "direct")
        self.assertEqual(res["agent"]["id"], PEER)

    def test_unknown_name_is_404_live_only(self):
        self.live("api", PEER)
        res = pm.resolve_recipient("ghost", ME)
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["status"], 404)
        self.assertIn("no live romp session named 'ghost'", res["error"])

    def test_isolated_recipient_still_says_isolation(self):
        self.live("api", PEER)
        self._isolated.add(PEER)
        res = pm.resolve_recipient("api", ME)
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["status"], 403)
        self.assertIn("isolation: the RECIPIENT", res["error"])

    def test_lone_peer_name_relays(self):
        self.on_peer("otherhost", "api", PEER)
        res = pm.resolve_recipient("api", ME)
        self.assertEqual(res["kind"], "relay")
        self.assertEqual(res["host"], "otherhost")

    def test_peers_off_ignores_peer_presence(self):
        pm.peers_on = lambda: False
        self.on_peer("otherhost", "web", PEER)
        self.live("web", ME)
        res = pm.resolve_recipient("web", TWIN)
        self.assertEqual(res["kind"], "direct", "legacy mode never consults the peer table")


class SendRouteUsesTheResolver(unittest.TestCase):
    def test_handler_delegates_and_never_indexes_a_match_list(self):
        import inspect
        src = inspect.getsource(pm.Handler.do_POST)
        send = src.split('if u.path == "/send":', 1)[1].split('if u.path == "/recall":', 1)[0]
        self.assertIn("resolve_recipient(to, frm_id)", send,
                      "the send route must resolve through the one identity-checking path")
        self.assertNotIn("match[0]", send,
                         "picking the first name match is the bug: ambiguity is refused, not tiebroken")


class SelfMailIsLabelled(unittest.TestCase):
    """Rendering can't be the only defence, but mail already on disk (or looped in from a peer)
    must never read as somebody else's reply."""

    def test_own_message_is_flagged_in_the_inbox(self):
        msgs = [{"from": "web", "from_id": ME, "body": "the survey", "id": "m1", "date": ""}]
        self.assertIn("YOUR OWN message", pm.format_inbox(msgs, ME))

    def test_a_real_peer_message_is_not_flagged(self):
        msgs = [{"from": "api", "from_id": PEER, "body": "on it", "id": "m1", "date": ""}]
        self.assertNotIn("YOUR OWN", pm.format_inbox(msgs, ME))

    def test_no_reader_id_renders_as_before(self):
        msgs = [{"from": "api", "from_id": PEER, "body": "on it", "id": "m1", "date": ""}]
        self.assertNotIn("YOUR OWN", pm.format_inbox(msgs))


class YouIsAnIdentityClaim(unittest.TestCase):
    def test_you_is_matched_by_id_not_name(self):
        agents = [{"name": "web", "id": ME}, {"name": "web", "id": TWIN, "remote": True}]
        out = pm.format_agents(agents, "web", ME)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(sum("(you)" in ln for ln in lines), 1,
                         "exactly one row is you, however many sessions share the name")
        self.assertIn("(you)", lines[0])

    def test_name_fallback_when_no_id_is_known(self):
        agents = [{"name": "web", "id": ME}]
        self.assertIn("(you)", pm.format_agents(agents, "web"))


class DeliveredMeansDelivered(unittest.TestCase):
    """A relayed or parked message is not a delivered one, and the tool surface has to say which."""

    def setUp(self):
        self._saved = {k: getattr(pm, k) for k in ("_http", "my_name", "my_id", "_heartbeat")}
        pm.my_name = lambda: "web"
        pm.my_id = lambda: ME
        pm._heartbeat = lambda *a, **k: None

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(pm, k, v)

    def _send(self, resp, kind="coordinate"):
        pm._http = lambda *a, **k: resp
        out, err = pm._mcp_call("send_message", {"to": "api", "body": "hi", "kind": kind})
        self.assertFalse(err)
        return out

    def test_parked_for_an_unreachable_host_does_not_claim_delivery(self):
        out = self._send({"ok": True, "id": "px-1",
                          "note": "parked for otherhost (unreachable) — delivers on reconnect, "
                                  "or bounces back to you"})
        self.assertNotIn("Delivered", out)
        self.assertIn("parked for otherhost", out)

    def test_relaying_says_relaying(self):
        out = self._send({"ok": True, "id": "px-1", "note": "relaying to 'api' on otherhost"})
        self.assertNotIn("Delivered", out)
        self.assertIn("relaying", out)

    def test_a_plain_local_delivery_is_unchanged(self):
        self.assertEqual(self._send({"ok": True, "to": "api"}), "Delivered to 'api'.")

    def test_the_declaration_echo_still_rides_a_real_delivery(self):
        out = self._send({"ok": True, "to": "api"}, kind="question")
        self.assertIn("waiting on their reply", out)


if __name__ == "__main__":
    unittest.main()


class UuidRecipients(unittest.TestCase):
    """A uuid-shaped `to` addresses the STABLE session id (the user 2026-08-23): names are labels
    renames retire; the sid survives them. Unique by construction, so no ambiguity arm; the
    self-send refusal still applies to your own sid."""
    A = "11111111-2222-3333-4444-555555555555"
    B = "66666666-7777-8888-9999-000000000000"

    def setUp(self):
        self._agents = [{"name": "web", "id": self.A, "remote": False},
                        {"name": "web", "id": self.B, "remote": False}]   # a shared NAME, distinct ids
        self._saved = (pm.all_agents, pm.peers_on, pm._postal_off)
        pm.all_agents = lambda threads=False: list(self._agents)
        pm.peers_on = lambda: False
        pm._postal_off = lambda sid: False

    def tearDown(self):
        pm.all_agents, pm.peers_on, pm._postal_off = self._saved

    def test_an_id_resolves_where_the_shared_name_is_ambiguous(self):
        res = pm.resolve_recipient(self.B, frm_id=self.A)
        self.assertEqual(res["kind"], "direct")
        self.assertEqual(res["agent"]["id"], self.B, "the sid picks exactly one session")
        by_name = pm.resolve_recipient("web", frm_id="")
        self.assertEqual(by_name["kind"], "error", "…where the name alone is refused as ambiguous")

    def test_your_own_id_is_still_a_self_send(self):
        res = pm.resolve_recipient(self.A, frm_id=self.A)
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["status"], 409, "mailing your own sid is the same loopback")

    def test_an_unknown_id_errors_live_only(self):
        res = pm.resolve_recipient("99999999-8888-7777-6666-555544443333", frm_id=self.A)
        self.assertEqual(res["kind"], "error")
