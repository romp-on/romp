#!/usr/bin/env python3
"""The false-refusal honesty arms on the CROSS-HOST legs (2026-08-31). The sending resolver got
its arms in the answered-but-absent round; two verified specimens showed the class surviving on
the relay path: (i) an inbound relay bounced "postal isolation" — final by norm — with no flag
set on either kernel (the isolation ruling was INFERRED from two disagreeing listing fetches);
(ii) sends to a live far-host session were refused no-live-session while the sid stood in the
sending box's own remote-sids mirror (far-kernel restart flapped presence empty). Three fixes:
_relay_in takes ONE checked snapshot and corroborates both bounces (unanswered/blinked → 'retry',
which crosses the wire as silence so the sender's outbox re-relays); resolve_recipient consults
the remote-sids mirror for id-shaped forms before its 404; and the presence PRODUCER serves the
last ANSWERED rows when the local listing doesn't answer, so a local blink never gossips as
"nobody lives here". Fold-in: set_working without its text param refused loudly, never a clear.

SYNTHETIC fixtures only: placeholder UUIDs, invented names.
"""
import io
import json
import os
import tempfile
import unittest
import uuid
from romp_load import load_source
from pathlib import Path

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_relay", os.path.join(BIN, "romp-postal-service"))

ALPHA = "11111111-2222-3333-4444-555555555555"
GHOST = "99999999-8888-7777-6666-555555555555"


def _set_live(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(rows, f)
    f.close()
    os.environ["ROMP_SESSIONS_FILE"] = f.name


def _fresh_mid():
    return "relayt-" + uuid.uuid4().hex


class _RelayBase(unittest.TestCase):
    def setUp(self):
        # no seam unless a test sets one: the source under test is the kernel listing. The prior value
        # goes back at cleanup, because the bus reads the seam from os.environ at call time and a later
        # module in the same process may have set it once at import.
        self._prior_seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self._base = pm.KERNEL_BASE
        self.addCleanup(lambda: (setattr(pm, "KERNEL_BASE", self._base),
                                 restore_env("ROMP_SESSIONS_FILE", self._prior_seam),
                                 pm.HEARTBEATS.clear()))

    def _msg(self, to):
        return {"mid": _fresh_mid(), "to": to, "frm": "web", "frm_id": ALPHA, "body": "hi"}


class RelayInboundHonesty(_RelayBase):
    """_relay_in's bounces are FINAL on the sender side, so both must be corroborated rulings."""

    def test_one_listing_snapshot_for_the_whole_ruling(self):
        # specimen (i)'s mechanism: two fetches, a blink between them → a false FINAL isolation.
        # Pinned structurally: the whole ruling pays exactly one listing fetch.
        _set_live([])
        calls = []
        saved = pm.local_agents_checked
        pm.local_agents_checked = lambda threads=False: (calls.append(1), saved(threads=threads))[1]
        try:
            pm._relay_in("TESTHOST", self._msg("web"))
        finally:
            pm.local_agents_checked = saved
        self.assertEqual(len(calls), 1, "one snapshot rules match, isolation, and death together")

    def test_true_isolation_still_bounces(self):
        _set_live([{"id": ALPHA, "name": "web"}])
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text(json.dumps({ALPHA: {"postalServiceOff": True}}))
        self.addCleanup(lambda: pm.SESSION_FLAGS.unlink(missing_ok=True))
        verdict, bounce = pm._relay_in("TESTHOST", self._msg("web"))
        self.assertEqual(verdict, "bounce")
        self.assertIn("postal isolation", bounce["why"], "a TRUE flag read still rules isolation")

    def test_unanswered_listing_retries_never_bounces(self):
        # a mid-restart kernel proves nothing — neither isolation nor death may be minted from it
        pm.KERNEL_BASE = "http://127.0.0.1:9"      # nothing listens: every fetch fails fast
        m = self._msg("web")
        verdict, bounce = pm._relay_in("TESTHOST", m)
        self.assertEqual((verdict, bounce), ("retry", None))
        self.assertFalse(pm.peer_seen_check(m["mid"]), "the re-relay must be processed in full")

    def test_blink_with_durable_reg_retries(self):
        # answered listing omits the name, but its durable reg stands alive → a blink, not a death
        _set_live([])
        root = pm.STATE.parent
        (root / "sdk").mkdir(parents=True, exist_ok=True)
        (root / "sdk" / (GHOST + ".json")).write_text(json.dumps({"sid": GHOST, "alive": True}))
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)
        (pm.NAMES_DIR / GHOST).write_text("web\t/work/web\t#112233\t#ffffff\n")
        self.addCleanup(lambda: ((root / "sdk" / (GHOST + ".json")).unlink(missing_ok=True),
                                 (pm.NAMES_DIR / GHOST).unlink(missing_ok=True)))
        verdict, bounce = pm._relay_in("TESTHOST", self._msg("web"))
        self.assertEqual((verdict, bounce), ("retry", None))

    def test_genuinely_absent_still_bounces_honestly(self):
        _set_live([])
        verdict, bounce = pm._relay_in("TESTHOST", self._msg("nobody-here"))
        self.assertEqual(verdict, "bounce")
        self.assertIn("no live session named 'nobody-here'", bounce["why"])


class IdShapedAddressingRoutes(_RelayBase):
    """specimen (ii)'s uuid leg, cured by ROUTING (review find 2026-09-01): presence rows have
    always carried ids, but peer_route matched names only — a uuid or short-id send to a live
    far-host session had NO route at all and fell through to the refusal tail as false deadness
    (a first-cut mirror-503 here prescribed a retry that could never succeed; removed)."""

    def _far(self, rows):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        pm.PEER_STATE["farhost"] = {"presence": rows, "epoch": 1, "seenAt": 0}
        self.addCleanup(lambda: (pm.PEER_STATE.clear(), os.environ.pop("ROMP_POSTAL_PEERS", None)))

    def test_uuid_send_relays_to_the_presence_row(self):
        _set_live([])
        self._far([{"id": GHOST, "name": "web"}])
        res = pm.resolve_recipient(GHOST)
        self.assertEqual((res["kind"], res["host"]), ("relay", "farhost"))

    def test_short_id_prefix_relays_too(self):
        _set_live([])
        self._far([{"id": GHOST, "name": "web"}])
        res = pm.resolve_recipient(GHOST[:8])
        self.assertEqual((res["kind"], res["host"]), ("relay", "farhost"))

    def test_host_qualified_uuid_relays(self):
        _set_live([])
        self._far([{"id": GHOST, "name": "web"}])
        res = pm.resolve_recipient("farhost:" + GHOST)
        self.assertEqual((res["kind"], res["host"]), ("relay", "farhost"))

    def test_ambiguous_prefix_refuses_never_guesses(self):
        _set_live([])
        self._far([{"id": GHOST, "name": "web"},
                   {"id": GHOST[:8] + "aaaa-bbbb-cccc-dddddddddddd", "name": "api"}])
        res = pm.resolve_recipient(GHOST[:8])
        self.assertEqual((res["kind"], res["status"]), ("error", 409))

    def test_unknown_uuid_keeps_the_honest_404(self):
        _set_live([])
        self._far([{"id": ALPHA, "name": "web"}])
        res = pm.resolve_recipient(GHOST)
        self.assertEqual((res["kind"], res["status"]), ("error", 404))

    def test_park_carries_the_resolved_sid_alongside_the_name(self):
        # review find 2026-09-01: the park rewrote an id-addressed send to the far session's NAME,
        # discarding what the sid was chosen for (rename-proof, ambiguity-proof). The relay message
        # carries toId now; this pins the dict literal so the key can't quietly drop.
        src = open(os.path.join(os.path.dirname(HERE), "postal", "postal_service.py")).read()
        self.assertIn('"toId": str(hit.get("id") or "")', src)

    def test_toid_matches_exactly_never_falls_back_to_a_same_named_sibling(self):
        # two live sessions share a name; the mail carries the sid of the SECOND — a name match
        # would hand it to the first (silent misdelivery, the ambiguity the sid bypasses)
        _set_live([{"id": ALPHA, "name": "web"}, {"id": GHOST, "name": "web"}])
        pm.PEERS["TESTHOST"] = {"port": 1, "up": True, "trust": "trusted"}
        delivered = []
        saved = pm.deliver
        pm.deliver = lambda *a, **k: delivered.append(a) or "m-x"
        self.addCleanup(lambda: (setattr(pm, "deliver", saved), pm.PEERS.clear()))
        m = dict(self._msg("web"), toId=GHOST)
        verdict, _ = pm._relay_in("TESTHOST", m)
        self.assertEqual(verdict, "ack")
        self.assertEqual(delivered[0][0], GHOST, "the sid picks the session, not the name order")

    def test_toid_miss_with_standing_reg_retries_never_misdelivers(self):
        # the toId's session blinked out of the listing (rename mid-park is the specimen shape) —
        # a name fallback could misdeliver; the durable reg by ID corroborates a retry instead
        _set_live([{"id": ALPHA, "name": "web"}])
        root = pm.STATE.parent
        (root / "sdk").mkdir(parents=True, exist_ok=True)
        (root / "sdk" / (GHOST + ".json")).write_text(json.dumps({"sid": GHOST, "alive": True}))
        self.addCleanup(lambda: (root / "sdk" / (GHOST + ".json")).unlink(missing_ok=True))
        verdict, bounce = pm._relay_in("TESTHOST", dict(self._msg("web"), toId=GHOST))
        self.assertEqual((verdict, bounce), ("retry", None))

    def test_toid_gone_everywhere_bounces_honestly(self):
        _set_live([{"id": ALPHA, "name": "web"}])
        verdict, bounce = pm._relay_in("TESTHOST", dict(self._msg("web"), toId=GHOST))
        self.assertEqual(verdict, "bounce")

    def test_a_same_named_replacement_never_holds_a_gone_sid_in_limbo(self):
        # skeptic repro (2026-09-01): the pinned sid is gone for good, but a NEW session took the
        # name — with reg and names entry, as every live SDK session has. The gate's name arm read
        # that replacement's reg as evidence and returned retry FOREVER (parked mail, sender never
        # told). A pinned sid corroborates by ID alone: gone everywhere → final honest bounce.
        _set_live([{"id": ALPHA, "name": "web"}])
        root = pm.STATE.parent
        (root / "sdk").mkdir(parents=True, exist_ok=True)
        (root / "sdk" / (ALPHA + ".json")).write_text(json.dumps({"sid": ALPHA, "alive": True}))
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)
        (pm.NAMES_DIR / ALPHA).write_text("web\t/work/web\t#112233\t#ffffff\n")
        self.addCleanup(lambda: ((root / "sdk" / (ALPHA + ".json")).unlink(missing_ok=True),
                                 (pm.NAMES_DIR / ALPHA).unlink(missing_ok=True)))
        verdict, bounce = pm._relay_in("TESTHOST", dict(self._msg("web"), toId=GHOST))
        self.assertEqual(verdict, "bounce", "the replacement's standing reg is not the pinned sid's")

    def test_a_malformed_wire_toid_never_reaches_the_registry_read(self):
        # skeptic find (2026-09-01): the wire toId flowed into a registry-path read — a crafted
        # "../" made that stat a path-traversal alive-oracle. Malformed shapes degrade to name
        # matching (old-mail behavior) and never touch the filesystem as a path.
        _set_live([{"id": ALPHA, "name": "web"}])
        pm.PEERS["TESTHOST"] = {"port": 1, "up": True, "trust": "trusted"}
        delivered = []
        saved_del, saved_dur = pm.deliver, pm._durable_session
        pm.deliver = lambda *a, **k: delivered.append(a) or "m-x"
        pm._durable_session = lambda bare, by_id: (_ for _ in ()).throw(
            AssertionError("the malformed toId must not reach the durable read"))
        self.addCleanup(lambda: (setattr(pm, "deliver", saved_del),
                                 setattr(pm, "_durable_session", saved_dur), pm.PEERS.clear()))
        verdict, _ = pm._relay_in("TESTHOST", dict(self._msg("web"), toId="../../etc/hostname"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(delivered[0][0], ALPHA, "degrades to name matching, delivers normally")

    def test_the_forward_hop_routes_by_the_pinned_sid_through_a_rename(self):
        # skeptic repro (2026-09-01): the hub's forward matched names only, so a session renamed
        # during the park window final-bounced at the hub while living one hop away — the gossip
        # row carries the sid, and the forward routes by it now (name stays the old-mail fallback)
        _set_live([])
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        pm.PEER_STATE["spokec"] = {"presence": [{"id": GHOST, "name": "web2"}], "epoch": 1, "seenAt": 0}
        parked = []
        saved_put, saved_get = pm.outbox_put, pm.outbox_get
        pm.outbox_put = lambda h, m: parked.append((h, m)) or True   # the park reports success as a bool
        pm.outbox_get = lambda h, mid: None
        self.addCleanup(lambda: (setattr(pm, "outbox_put", saved_put), setattr(pm, "outbox_get", saved_get),
                                 pm.PEER_STATE.clear(), os.environ.pop("ROMP_POSTAL_PEERS", None)))
        verdict, _ = pm._relay_in("TESTHOST", dict(self._msg("web"), toId=GHOST))
        self.assertEqual(verdict, "hold", "forwarded by sid, not bounced on the stale name")
        self.assertEqual(parked[0][0], "spokec")
        self.assertEqual(parked[0][1].get("toId"), GHOST, "the sid rides the hop intact")

    def test_pinned_mail_never_forwards_to_a_namesake_host(self):
        # skeptic repro (2026-09-01, round 2): with the pinned sid blinked out of gossip and a
        # same-named session on ANOTHER host, the name fallback forwarded the pinned mid there —
        # double-parked across hosts, and the namesake host's final bounce could beat the real
        # delivery ack. Pinned mail routes by sid or not at all.
        _set_live([])
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        pm.PEER_STATE["spokec"] = {"presence": [{"id": ALPHA, "name": "web"}], "epoch": 1, "seenAt": 0}
        parked = []
        saved_put, saved_get = pm.outbox_put, pm.outbox_get
        pm.outbox_put = lambda h, m: parked.append((h, m)) or True   # the park reports success as a bool
        pm.outbox_get = lambda h, mid: None
        self.addCleanup(lambda: (setattr(pm, "outbox_put", saved_put), setattr(pm, "outbox_get", saved_get),
                                 pm.PEER_STATE.clear(), os.environ.pop("ROMP_POSTAL_PEERS", None)))
        verdict, _ = pm._relay_in("TESTHOST", dict(self._msg("web"), toId=GHOST))
        self.assertNotEqual(verdict, "hold", "the namesake host is not this mail's destination")
        self.assertEqual(parked, [], "nothing forwarded on a name the mail did not trust")

    def test_relay_in_matches_an_id_addressed_arrival(self):
        # the far side of the same cure: a relayed message whose `to` is the uuid (a resolved row
        # with no name) must find its local session by id, not bounce no-live
        _set_live([{"id": ALPHA, "name": "web"}])
        pm.PEERS["TESTHOST"] = {"port": 1, "up": True, "trust": "trusted"}
        delivered = []
        saved = pm.deliver
        pm.deliver = lambda *a, **k: delivered.append(a) or "m-x"
        self.addCleanup(lambda: (setattr(pm, "deliver", saved), pm.PEERS.clear()))
        verdict, _ = pm._relay_in("TESTHOST", self._msg(ALPHA))
        self.assertEqual(verdict, "ack")
        self.assertEqual(delivered[0][0], ALPHA)


class PresenceBlinkHonesty(_RelayBase):
    """The producer half of specimen (ii): an unanswered local listing must never gossip as empty."""

    def setUp(self):
        super().setUp()
        pm._LOCAL_PRESENCE_GOOD[0], pm._LOCAL_PRESENCE_GOOD[1] = [], False
        pm._PRESENCE_SERVE_WARNED[0] = False
        # serve() creates the postal state directory before any listing is read; these tests drive
        # _local_presence without it, and the twin's write failure is swallowed by design, so without
        # this the first answered read leaves no twin and the disk-twin test depends on whether an
        # earlier test in the same process made the directory (the relay ack tests do, through
        # peer_seen_add) — red when selected alone or on an xdist worker that had not run one of them
        pm._PRESENCE_GOOD_FILE.parent.mkdir(parents=True, exist_ok=True)
        pm._PRESENCE_GOOD_FILE.unlink(missing_ok=True)

    def test_unanswered_serves_the_last_answered_rows(self):
        _set_live([{"id": ALPHA, "name": "web"}])
        self.assertEqual([a["id"] for a in pm._local_presence()], [ALPHA])   # answered → cached
        os.environ.pop("ROMP_SESSIONS_FILE", None)
        pm.KERNEL_BASE = "http://127.0.0.1:9"
        self.assertEqual([a["id"] for a in pm._local_presence()], [ALPHA],
                         "a blink serves the last answered rows, never an empty claim")

    def test_answered_empty_is_the_truth(self):
        _set_live([{"id": ALPHA, "name": "web"}])
        pm._local_presence()
        _set_live([])
        self.assertEqual(pm._local_presence(), [], "an ANSWERED empty listing serves and caches as truth")
        os.environ.pop("ROMP_SESSIONS_FILE", None)
        pm.KERNEL_BASE = "http://127.0.0.1:9"
        self.assertEqual(pm._local_presence(), [], "the cached truth is the empty listing")

    def test_never_answered_claims_nothing(self):
        pm.KERNEL_BASE = "http://127.0.0.1:9"
        self.assertEqual(pm._local_presence(), [])

    def test_disk_twin_carries_the_cache_across_a_bus_restart(self):
        # review find 2026-09-01: a bus restart overlapping a kernel restart (the normal
        # self-update path) boots with an empty in-memory cache and would gossip the blink as
        # authoritative emptiness — the disk twin primes it
        _set_live([{"id": ALPHA, "name": "web"}])
        pm._local_presence()                                     # answered → disk twin written
        self.assertTrue(pm._PRESENCE_GOOD_FILE.exists(), "an answered read writes the disk twin")
        pm._LOCAL_PRESENCE_GOOD[0], pm._LOCAL_PRESENCE_GOOD[1] = [], False   # a fresh bus process
        os.environ.pop("ROMP_SESSIONS_FILE", None)
        pm.KERNEL_BASE = "http://127.0.0.1:9"
        self.assertEqual([a["id"] for a in pm._local_presence()], [ALPHA],
                         "the disk twin serves through the double-restart window")

    def test_no_twin_still_claims_nothing(self):
        pm._PRESENCE_GOOD_FILE.unlink(missing_ok=True)
        pm.KERNEL_BASE = "http://127.0.0.1:9"
        self.assertEqual(pm._local_presence(), [])


class QuarantineApproveHonesty(_RelayBase):
    """The approve arm shares this round's diseases (2026-09-01): it paid TWO kernel fetches (its
    own fetch-pair TOCTOU, and 2x6s could outlast the kernel's client cap on /quarantine/act —
    the budget pair now reconciled at 20s client-side), and it read a mid-restart blink as
    "no longer a live local session" — a false terminal on a held message."""

    def setUp(self):
        super().setUp()
        self.rec = {"mid": "q-1", "to": "web", "toId": ALPHA, "frm": "api", "frmId": GHOST,
                    "body": "held hello", "origin": "TESTHOST", "kind": "coordinate"}
        self.delivered, self.deleted = [], []
        self._saved = (pm.quarantine_get, pm.quarantine_del, pm.deliver)
        pm.quarantine_get = lambda mid: dict(self.rec) if mid == "q-1" else None
        pm.quarantine_del = lambda mid: self.deleted.append(mid)
        pm.deliver = lambda *a, **k: self.delivered.append((a, k)) or "m-q"
        self.addCleanup(lambda: (setattr(pm, "quarantine_get", self._saved[0]),
                                 setattr(pm, "quarantine_del", self._saved[1]),
                                 setattr(pm, "deliver", self._saved[2])))

    def test_unanswered_listing_refuses_retryably_and_keeps_the_hold(self):
        pm.KERNEL_BASE = "http://127.0.0.1:9"
        ok, err = pm.quarantine_decide("q-1", "approve")
        self.assertFalse(ok)
        self.assertIn("retry the approve shortly", err)
        self.assertEqual((self.delivered, self.deleted), ([], []),
                         "nothing delivered, nothing dropped — the held message stays put")

    def test_answered_approve_pays_one_fetch_and_delivers(self):
        _set_live([{"id": ALPHA, "name": "web"}])
        calls = []
        saved = pm.local_agents_checked
        pm.local_agents_checked = lambda threads=False: (calls.append(1), saved(threads=threads))[1]
        try:
            ok, err = pm.quarantine_decide("q-1", "approve")
        finally:
            pm.local_agents_checked = saved
        self.assertTrue(ok, err)
        self.assertEqual(len(calls), 1, "one snapshot rules the whole approve")
        self.assertEqual(self.delivered[0][0][0], ALPHA)

    def test_kernel_client_cap_matches_the_pair(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('HTTPConnection("127.0.0.1", _bus_port(), timeout=20)', src,   # the dial reads the bus's port record (2026-09-18)
                      "the client half of the approve budget pair — the halves move together")


class RelayRowCarriesTheStableId(_RelayBase):
    """The cross-host send's LOCAL record names the recipient by its stable id (2026-09-08). The wire
    message has carried `toId` since 2026-09-01, but the sent row kept only "<host>:<name>", so every
    wait reader joined it back to a sid through a name→sid alias learned from the peer's own rows —
    a join a reused name could re-point at a stranger. The id was already in hand at the write."""

    def _post_send(self, payload):
        # the /send route lives inline in do_POST: drive the handler with a fake request
        h = object.__new__(pm.Handler)
        raw = json.dumps(payload).encode()
        h.path = "/send"
        h.headers = {"Content-Length": str(len(raw)), "X-Romp-Token": pm.SERVE_TOKEN}
        h.rfile = io.BytesIO(raw)
        out = []
        h._send = lambda obj, code=200: out.append((obj, code))
        h.do_POST()
        return out[0]

    def test_the_relay_sent_row_carries_to_sid_equal_to_the_wire_toid(self):
        _set_live([{"id": ALPHA, "name": "web"}])            # the sender lives here …
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        pm.PEER_STATE["TESTHOST"] = {"presence": [{"id": GHOST, "name": "api"}], "epoch": 1, "seenAt": 0}
        parked = []                                           # … the recipient on TESTHOST
        saved_put = pm.outbox_put
        pm.outbox_put = lambda h, m: parked.append((h, m)) or True   # the park reports success as a bool
        self.addCleanup(lambda: (setattr(pm, "outbox_put", saved_put), pm.PEER_STATE.clear(),
                                 os.environ.pop("ROMP_POSTAL_PEERS", None)))
        obj, code = self._post_send({"to": "api", "from": "web", "from_id": ALPHA,
                                     "body": "which port does staging use?", "kind": "question"})
        self.assertEqual(code, 200, obj)
        self.assertEqual(parked[0][1]["toId"], GHOST, "the wire carries the resolved sid (2026-09-01)")
        rows = [json.loads(l) for l in (pm.TLDIR / "messages.jsonl").read_text().splitlines()]
        row = next(r for r in rows if r.get("id") == obj["id"])
        self.assertEqual(row["to_sid"], GHOST, "the local row carries the SAME sid")   # KeyError on main
        self.assertEqual((row["to_id"], row["toName"], row["kind"]),
                         ("peer:TESTHOST", "TESTHOST:api", "question"), "the old keys are untouched")


class QuarantineApproveIsIdStrict(_RelayBase):
    """Held mail that was ID-addressed on the wire may only ever reach that id (2026-09-08). The held
    record kept only the RESOLVED local sid, and approve re-matched by NAME whenever that sid was no
    longer live — so id-addressed mail whose recipient had ended went to whatever session now wore
    the name, the exact swap intake refuses ("never a name fallback, which could hand the mail to a
    same-named sibling"). The record now remembers the wire id (`toWireId`) and approve is id-strict
    for it: nothing live by that id → a loud refusal that keeps the hold. Name-addressed mail (an
    older sender) still re-matches by name."""

    def setUp(self):
        super().setUp()
        pm.PEERS["TESTHOST"] = {"port": 1, "up": True, "trust": "directed"}
        self.delivered = []
        saved = pm.deliver
        pm.deliver = lambda *a, **k: self.delivered.append((a, k)) or "m-q"
        self.addCleanup(lambda: (setattr(pm, "deliver", saved), pm.PEERS.clear()))

    def _hold(self, m):
        verdict, _ = pm._relay_in("TESTHOST", m)
        self.assertEqual(verdict, "ack", "directed → held, ack'd")
        rec = pm.quarantine_get(m["mid"])
        self.assertIsNotNone(rec, "the message is in the hold")
        self.addCleanup(lambda: pm.quarantine_del(m["mid"]))
        return rec

    def test_id_addressed_mail_whose_recipient_ended_refuses_a_name_squatter(self):
        _set_live([{"id": GHOST, "name": "web"}])
        m = dict(self._msg("web"), toId=GHOST)
        rec = self._hold(m)
        self.assertEqual(rec["toWireId"], GHOST, "the hold remembers the wire chose a sid")   # KeyError on main
        _set_live([{"id": ALPHA, "name": "web"}])            # GHOST ended; ALPHA wears the name now
        ok, err = pm.quarantine_decide(m["mid"], "approve")
        self.assertFalse(ok, "main delivers to the squatter here")
        self.assertEqual(self.delivered, [], "nothing reached the same-named stranger")
        self.assertIn("no live session carries the id", err,
                      "worded on what the listing proved (review find, 2026-09-08: it said 'has ended', "
                      "which a dormant session is not)")
        self.assertNotIn("has ended", err)
        self.assertIn("'web'", err, "the refusal names the session the mail was addressed to")
        self.assertIn(GHOST[:8], err, "…and its id")
        self.assertIsNotNone(pm.quarantine_get(m["mid"]), "the record stays held (deny carries a note back)")

    def test_id_addressed_mail_whose_recipient_renamed_still_delivers_to_it(self):
        # a GUARD, not the id-strict branch: the held sid is still live, so approve never enters the
        # gone-sid arm at all (the rename changes nothing the record keys on). Passes on main too.
        # There is no positive arm to reach in that branch (review find, 2026-09-08): the held sid is
        # the wire id, so once it is gone nothing live can match it: see the id-strict refusal above.
        _set_live([{"id": GHOST, "name": "web"}])
        m = dict(self._msg("web"), toId=GHOST)
        self._hold(m)
        _set_live([{"id": ALPHA, "name": "web"}, {"id": GHOST, "name": "web2"}])   # renamed, still live
        ok, err = pm.quarantine_decide(m["mid"], "approve")
        self.assertTrue(ok, err)
        self.assertEqual(self.delivered[0][0][0], GHOST, "the sid picks the session, not the name")

    def test_id_addressed_mail_whose_recipient_is_gone_refuses_with_nobody_wearing_the_name_too(self):
        # the same refusal with no squatter in the listing: the branch never looks for one
        _set_live([{"id": GHOST, "name": "web"}])
        m = dict(self._msg("web"), toId=GHOST)
        self._hold(m)
        _set_live([{"id": ALPHA, "name": "api"}])
        ok, err = pm.quarantine_decide(m["mid"], "approve")
        self.assertFalse(ok)
        self.assertIn("no live session carries the id", err)
        self.assertEqual(self.delivered, [])
        self.assertIsNotNone(pm.quarantine_get(m["mid"]), "still held")

    def test_the_id_strict_branch_has_no_name_fallback_and_no_dead_arm(self):
        # structural pin (review find, 2026-09-08): between reading toWireId and the name re-match
        # there is exactly one statement path, the refusal, and no re-match on the wire id
        import inspect
        src = inspect.getsource(pm.quarantine_decide)
        body = src[src.index('wire = str(rec.get("toWireId")'):src.index("NAME-addressed")]
        self.assertNotIn("== wire", body, "no candidate scan on the wire id: it cannot succeed")
        self.assertNotIn('a["name"] == rec.get("to")', body, "no name re-match for id-addressed mail")
        self.assertEqual(body.count("return False"), 1)

    def test_name_addressed_mail_still_rematches_by_name(self):
        # a GUARD (passes on main too): an older sender parks no toId, so the name is all there is
        _set_live([{"id": GHOST, "name": "web"}])
        m = self._msg("web")
        rec = self._hold(m)
        self.assertNotIn("toWireId", rec, "name-addressed: no wire id recorded")
        _set_live([{"id": ALPHA, "name": "web"}])
        ok, err = pm.quarantine_decide(m["mid"], "approve")
        self.assertTrue(ok, err)
        self.assertEqual(self.delivered[0][0][0], ALPHA, "the name re-match stands for name-addressed mail")

    def test_the_hold_sanitizes_the_wire_id_itself(self):
        # belt for callers that pass no wire_id: the record reads the wire toId and drops a malformed
        # shape exactly as intake does — it is a match key, never a path
        m = dict(self._msg("web"), toId=GHOST)
        self.assertTrue(pm._quarantine_put("TESTHOST", m, ALPHA))
        self.addCleanup(lambda: pm.quarantine_del(m["mid"]))
        self.assertEqual(pm.quarantine_get(m["mid"])["toWireId"], GHOST)                # KeyError on main
        m2 = dict(self._msg("web"), toId="../../etc/hostname")
        self.assertTrue(pm._quarantine_put("TESTHOST", m2, ALPHA))
        self.addCleanup(lambda: pm.quarantine_del(m2["mid"]))
        self.assertNotIn("toWireId", pm.quarantine_get(m2["mid"]), "a malformed wire id is not a key")


class SetWorkingMissingParamRefuses(unittest.TestCase):
    """Fold-in: a missing param is never a clear command."""

    def setUp(self):
        self.saved = (pm._self_identity, pm._publish_working)
        self.calls = []
        pm._self_identity = lambda: (ALPHA, "web")     # the one resolver every tool call reads (2026-09-06)
        pm._publish_working = lambda mid, text: self.calls.append((mid, text))
        self.addCleanup(lambda: (setattr(pm, "_self_identity", self.saved[0]),
                                 setattr(pm, "_publish_working", self.saved[1])))

    def test_missing_text_refuses_and_changes_nothing(self):
        msg, is_err = pm._mcp_call("set_working", {})
        self.assertTrue(is_err)
        self.assertIn("nothing was changed", msg)
        self.assertEqual(self.calls, [], "the note is untouched")

    def test_null_text_refuses_too(self):
        msg, is_err = pm._mcp_call("set_working", {"text": None})
        self.assertTrue(is_err)
        self.assertEqual(self.calls, [])

    def test_explicit_empty_string_still_clears(self):
        msg, is_err = pm._mcp_call("set_working", {"text": ""})
        self.assertFalse(is_err)
        self.assertIn("Cleared", msg)
        self.assertEqual(self.calls, [(ALPHA, "")], "text='' stays the documented clear command")


if __name__ == "__main__":
    unittest.main()
