"""The notice card STORE (T370, plans/notice-cards.md, issue #1750): kernel-made feed cards a producer posts without the judges. The
store STATE/notices/<sid>.jsonl; post_notice(...) -> (row, error) behind its doors (in-process, the backend's hook, POST
/notice; `romp card` is tests/romp.bats'); the feed's card family under notice:<sid>:<key>:<rev>; the three moves (a
dismissal, a revision, an expiry); the actions allowlist executed by the kernel on the gesture; the attachment verdict by
the preview's confinement; retention to the archive; the memo bound. Hermetic: a temp state root, a synthetic session in
the notes-api demo world; nothing touches the live state root."""
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
_XDG = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(_XDG, "romp"), exist_ok=True)
open(os.path.join(_XDG, "romp", "session-hosts"), "w").write("off\n")
km = load_source("romp_kernel_notice_store", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_notice_store", os.path.join(BIN, "romp_sdk_backend.py"))
KSRC = open(os.path.join(BIN, "romp-kernel")).read()

SID = "11111111-2222-3333-4444-555555555555"     # web, the notes-api demo world
SID2 = "11111111-2222-3333-4444-666666666666"    # api


class World:
    """A hermetic state root with two named sessions (web, api) whose folder is the temp cwd; the kernel rebound to it."""
    def __init__(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.cwd = root / "notes-api"; self.cwd.mkdir()
        self.orig_state, self.orig_names = km.jd.STATE, km.NAMES   # the conftest guard: shared state goes back at close
        km.jd._rebind_state(root / "state")
        (km.jd.STATE / "session-hosts").parent.mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "session-hosts").write_text("off\n")
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        (km.jd.NAMES / SID).write_text("web\t%s\t#1EA1EB\t#ffffff\n" % self.cwd)
        (km.jd.NAMES / SID2).write_text("api\t%s\t#B69513\tblack\n" % self.cwd)
        km.NAMES = km.jd.NAMES
        self.saved = (km._live_map, km._cwd_of, km._mark_views_dirty, km._push_soon, km._deliver_text, km.Sessions.live)
        km._live_map = lambda: {}
        km.Sessions.live = staticmethod(lambda: {})
        km._cwd_of = lambda sid: str(self.cwd) if sid in (SID, SID2) else None
        self.dirty, self.pushes, self.delivered = [], [], []
        km._mark_views_dirty = lambda: self.dirty.append(1)
        km._push_soon = lambda: self.pushes.append(1)
        km._deliver_text = lambda sid, text, plain=False: (self.delivered.append((sid, text)) or (True, "", False))
        km._NOTICE_MEMO.clear(); km._NOTICE_SWEPT.clear()
        for k in km._NOTICE_MEMO_STATS: km._NOTICE_MEMO_STATS[k] = 0
        km._CLEARED_MEMO["slot"] = None

    def close(self):
        (km._live_map, km._cwd_of, km._mark_views_dirty, km._push_soon, km._deliver_text, km.Sessions.live) = self.saved
        km.jd._rebind_state(self.orig_state); km.NAMES = self.orig_names
        km._NOTICE_MEMO.clear(); km._NOTICE_SWEPT.clear(); km._CLEARED_MEMO["slot"] = None
        self.td.cleanup()

    def png(self, name="figure.png"):
        p = self.cwd / name
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return str(p)


_REAL_DELIVER = km._deliver_text   # the module's delivery door, before any test world stubs it


def _revs(ip):
    """The revision index's map (the file holds it beside the archive's stat it describes since round two of PR 1776)."""
    return json.loads(ip.read_text())["revs"]


def _rows(sid):
    p = km._notice_path(sid)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


class PostNotice(unittest.TestCase):
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def test_a_post_appends_one_row_with_the_kernels_rev_and_stamp_and_wakes_the_pusher(self):
        row, err = km.post_notice(SID, "figure", "A new version of the accuracy figure is ready", "Regenerated after the sweep.",
                                  producer="figure", now=1757000000, t=1756999990)
        self.assertIsNone(err); self.assertEqual((row["rev"], row["at"], row["t"], row["op"]), (1, 1757000000, 1756999990, "post"))
        self.assertEqual(_rows(SID), [row], "the file holds exactly the returned row")
        self.assertEqual(row["needsYou"], False); self.assertEqual(row["actions"], []); self.assertIsNone(row["attachment"])
        self.assertEqual((len(self.w.dirty), len(self.w.pushes)), (1, 1), "the views are dirtied and the pusher woken once")

    def test_every_refusal_is_said_and_nothing_is_written(self):
        cases = [
            (dict(sid=SID, key="bad key!", title="t"), "the key must match"),
            (dict(sid=SID, key="a" * 65, title="t"), "the key must match"),
            (dict(sid=SID, key="k", title=""), "needs a title"),
            (dict(sid=SID, key="k", title="x" * 201), "title is too long"),
            (dict(sid=SID, key="k", title="t", body="b" * (64 * 1024 + 1)), "body is too long"),
            (dict(sid=SID, key="k", title="t", producer="not ok"), "producer label"),
            (dict(sid="99999999-2222-3333-4444-555555555555", key="k", title="t"), "no session answers"),
            (dict(sid=SID, key="k", title="t", expires_at="soon"), "expiresAt must be"),
            (dict(sid=SID, key="k", title="t", expires_at=1757000000 - 1), "already past"),
            (dict(sid=SID, key="k", title="t", actions="x"), "actions must be a list"),
            (dict(sid=SID, key="k", title="t", actions=[{"label": "a", "route": "/send", "body": {"text": "x"}}] * 5), "at most 4"),
            (dict(sid=SID, key="k", title="t", actions=[{"label": "a", "route": "/watch", "body": {}}]), "not allowed"),
            (dict(sid=SID, key="k", title="t", actions=[{"label": "", "route": "/send", "body": {"text": "x"}}]), "needs a label"),
            (dict(sid=SID, key="k", title="t", actions=[{"label": "a", "route": "/send", "body": {}}]), "needs text"),
            (dict(sid=SID, key="k", title="t", actions=[{"label": "a", "route": "/send", "body": {"id": SID2, "text": "x"}}]), "names no target"),
            (dict(sid=SID, key="k", title="t", actions=[{"label": "a", "route": "/send", "body": {"name": "api", "text": "x"}}]), "names no target"),
            (dict(sid=SID, key="k", title="t", actions=[{"label": "a", "route": "/send", "body": {"text": "  /model opus"}}]), "never a command"),
            (dict(sid=SID, key="k", title="t", attachment="/nowhere/figure.png"), "attachment refused: not a file"),
        ]
        for kw, why in cases:
            kw.setdefault("producer", "figure"); kw.setdefault("now", 1757000000)
            row, err = km.post_notice(kw.pop("sid"), kw.pop("key"), kw.pop("title"), kw.pop("body", ""), **kw)
            self.assertIsNone(row, why); self.assertIn(why, err or "", "refusal names the rule: %r" % err)
        self.assertEqual(_rows(SID), [], "a refused post writes nothing"); self.assertEqual(self.w.dirty, [])

    def test_the_rev_counts_per_key_per_session_and_the_item_id_carries_it(self):
        r1, _ = km.post_notice(SID, "figure", "first", producer="figure", now=100)
        r2, _ = km.post_notice(SID, "figure", "second", producer="figure", now=200)
        o1, _ = km.post_notice(SID, "other", "other", producer="figure", now=300)
        a1, _ = km.post_notice(SID2, "figure", "api's own", producer="figure", now=400)
        self.assertEqual([r1["rev"], r2["rev"], o1["rev"], a1["rev"]], [1, 2, 1, 1])
        self.assertEqual(km._notice_item_id(SID, "figure", 2), "notice:%s:figure:2" % SID)

    def test_the_attachment_verdict_rides_the_row_an_image_pinned_and_a_refusal_carries_its_why(self):
        fp = self.w.png()
        row, err = km.post_notice(SID, "figure", "the figure", producer="figure", attachment=fp, now=100)
        self.assertIsNone(err)
        att = row["attachment"]
        self.assertEqual((att["path"], att["kind"], att["allowed"], att["why"]), (fp, "image", True, ""))
        self.assertTrue(att["pin"] and att["pin"].endswith(".png"), "an image is pinned at post time: %r" % att["pin"])
        (self.w.cwd / ".env").write_text("SECRET=1\n")
        row, err = km.post_notice(SID, "leak", "a secret", producer="figure", attachment=str(self.w.cwd / ".env"), now=100)
        self.assertIsNone(row); self.assertIn("attachment refused: a secrets-shaped name", err)
        outside = tempfile.mkdtemp(); fo = os.path.join(outside, "far.png"); open(fo, "wb").write(b"\x89PNG\r\n\x1a\n")
        # the temp dir sits under the home only when the home holds /tmp; a path outside the session's folder and the
        # home is refused by the confinement, one inside is allowed
        row, err = km.post_notice(SID, "far", "far away", producer="figure", attachment=fo, now=100)
        if os.path.realpath(outside).startswith(os.path.realpath(os.path.expanduser("~")) + os.sep):
            self.assertIsNone(err, "under the home: allowed")
        else:
            self.assertIsNone(row); self.assertIn("outside the session's folder and your home", err)


class TheCardFamily(unittest.TestCase):
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def _cards(self, now, cleared=None):
        return km._notice_cards(now, cleared or {})

    def test_the_newest_revision_per_key_is_the_card_and_every_field_but_the_age_colour_is_fixed(self):
        km.post_notice(SID, "figure", "first", "one", producer="figure", now=100, t=100)
        km.post_notice(SID, "figure", "second", "two", producer="figure", now=200, t=200)
        km.post_notice(SID, "note", "a note", producer="cli", now=150, t=150, needs_you=True)
        a = self._cards(1000); b = self._cards(2000)
        self.assertEqual([c["itemId"] for c in a], ["notice:%s:note:1" % SID, "notice:%s:figure:2" % SID], "post order, the superseded rev hidden")
        strip = lambda c: {k: v for k, v in c.items() if k != "trgb"}
        self.assertEqual([strip(c) for c in a], [strip(c) for c in b], "no field but trgb derives from the clock")
        fig = a[1]
        self.assertEqual((fig["text"], fig["column"], fig["name"], fig["sid"], fig["t"], fig["tree"], fig["live"], fig["blocked"]),
                         ("second", "completed", "web", SID, 200, [], False, None))
        self.assertEqual(fig["notice"], {"producer": "figure", "key": "figure", "rev": 2, "body": "two", "attachment": None,
                                         "actions": [], "expiresAt": None, "dismissOnAction": False, "acted": False})
        self.assertEqual(a[0]["column"], "needs_input", "needsYou files under Blocked")
        self.assertEqual(fig["color"], {"bg": "#1EA1EB", "fg": "#ffffff"})
        self.assertNotIn("_ageT", fig, "no private fold field rides the wire (round two, low b)")

    def test_the_cleared_ledger_applies_before_the_cap_so_a_dismissed_row_holds_no_slot(self):
        # round three, low a: fifty-one keys, the newest dismissed: all fifty undismissed show, the oldest among them
        for i in range(51):
            km.post_notice(SID, "k%03d" % i, "card %d" % i, producer="cli", now=1000 + i, t=1000 + i)
        newest = "notice:%s:k050:1" % SID
        cards = self._cards(5000, {newest: 4000})
        self.assertEqual(len(cards), 50); self.assertEqual(cards[0]["notice"]["key"], "k000", "the oldest undismissed card stands: the dismissed one held no slot")
        self.assertNotIn(newest, [c["itemId"] for c in cards])

    def test_a_session_shows_at_most_the_newest_fifty_keys_and_the_sweep_archives_the_rest(self):
        for i in range(60):
            km.post_notice(SID, "k%03d" % i, "card %d" % i, producer="cli", now=1000 + i, t=1000 + i)
        cards = self._cards(5000)
        self.assertEqual(len(cards), km.NOTICE_LIVE_KEYS_MAX, "the projection caps the live keys")
        self.assertEqual([c["notice"]["key"] for c in cards][:2], ["k010", "k011"], "the newest fifty by their post time stand; the oldest yield")
        moved = km._compact_notices(now=5000)
        self.assertEqual(moved, 10, "the ten oldest keys are superseded into the archive")
        self.assertEqual(sorted(r["key"] for r in _rows(SID))[:2], ["k010", "k011"]); self.assertEqual(len(_rows(SID)), 50)
        arch = [json.loads(l) for l in (km._notice_archive_dir() / (SID + ".jsonl")).read_text().splitlines()]
        self.assertEqual(sorted(r["key"] for r in arch), ["k%03d" % i for i in range(10)], "nothing deleted: the rows moved")

    def test_a_dismissal_hides_the_revision_and_a_new_revision_re_shows_after_it(self):
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        i1 = "notice:%s:figure:1" % SID
        self.assertEqual([c["itemId"] for c in self._cards(500, {i1: 400})], [], "cleared: hidden")
        km.post_notice(SID, "figure", "second", producer="figure", now=600, t=600)
        self.assertEqual([c["itemId"] for c in self._cards(700, {i1: 400})], ["notice:%s:figure:2" % SID], "new information re-shows under a new id")

    def test_an_expiry_leaves_at_the_next_build_and_an_expire_row_retires_early(self):
        km.post_notice(SID, "soon", "expires", producer="figure", now=100, t=100, expires_at=1000)
        self.assertEqual(len(self._cards(999)), 1); self.assertEqual(len(self._cards(1000)), 0, "expiresAt applies as a skip at build time")
        km.post_notice(SID, "keep", "stays", producer="figure", now=100, t=100)
        row, err = km.expire_notice(SID, "keep", now=200)
        self.assertIsNone(err); self.assertEqual((row["op"], row["rev"]), ("expire", 1))
        self.assertEqual([c["itemId"] for c in self._cards(300)], ["notice:%s:soon:1" % SID], "the retired key is gone; the one not yet expired stays")
        self.assertIn("no notice with key", km.expire_notice(SID, "never", now=200)[1])

    def test_the_family_keys_no_session_for_the_cleared_ledger(self):
        self.assertIn("notice:", km._CLEARED_NO_SESSION)
        self.assertEqual(km._cleared_foreign({"notice:%s:figure:1" % SID: 5, "%s:g1" % SID: 6}), ["%s:g1" % SID], "a notice id never rides as foreign")


class Actions(unittest.TestCase):
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def test_a_stored_send_action_delivers_through_the_one_door_and_dismisses_when_asked(self):
        acts = [{"label": "Send again", "route": "/send", "body": {"text": "please retry the sweep"}}]
        row, err = km.post_notice(SID, "dropped-sends", "1 message you typed before the restart was not re-sent",
                                  producer="dropped-sends", actions=acts, needs_you=True, dismiss_on_action=True, now=100)
        self.assertIsNone(err)
        iid = "notice:%s:dropped-sends:1" % SID
        ok, e = km._notice_action(iid, "/send", {"text": "please retry the sweep"})
        self.assertEqual((ok, e), (True, "")); self.assertEqual(self.w.delivered, [(SID, "please retry the sweep")])
        self.assertIn(iid, km._cleared_ids(), "dismissOnAction: a success clears the card")
        # round four, high: the dismissal is the event; a repeat click after it must not deliver the words a second time
        self.assertEqual(km._notice_action(iid, "/send", {"text": "please retry the sweep"}), (False, "that card's action ran already"))
        self.assertEqual(self.w.delivered, [(SID, "please retry the sweep")], "one delivery")
        # round five: Undo reverses the cleared ledger and brings the card back; the one-shot mark is the store's own acted row,
        # so the card returns with its action SPENT and a click delivers nothing
        km._undo_clear()
        self.assertNotIn(iid, km._cleared_ids(), "Undo restored the card's visibility")
        cards = km._notice_cards(500, km._cleared_ids())
        back = next(c for c in cards if c["itemId"] == iid)
        self.assertEqual((back["notice"]["acted"], back["notice"]["actions"]), (True, []), "back with its action spent")
        self.assertEqual(km._notice_action(iid, "/send", {"text": "please retry the sweep"}), (False, "that card's action ran already"))
        self.assertEqual(self.w.delivered, [(SID, "please retry the sweep")], "still one delivery after Undo")
        rows = _rows(SID); self.assertEqual([r["op"] for r in rows], ["post", "acted"], "the acted row rides the store")
        # a card the user cleared before ever clicking: the plain reason, no claim that its action ran
        km.post_notice(SID, "k2", "t", producer="cli", actions=[{"label": "Send", "route": "/send", "body": {"text": "x"}}], dismiss_on_action=True, now=100)
        iid2 = "notice:%s:k2:1" % SID; km._clear_ask(iid2)
        self.assertEqual(km._notice_action(iid2, "/send", {"text": "x"}), (False, "that card was dismissed"))
        self.assertEqual(km._notice_action(iid, "/send", {"text": "something else"}), (False, "no such action on that card"))
        self.assertEqual(km._notice_action(iid, "/watch", {}), (False, "no such action on that card"))
        self.assertEqual(km._notice_action("notice:%s:gone:1" % SID, "/send", {}), (False, "that notice is gone"))
        self.assertEqual(km._notice_action("%s:g1" % SID, "/send", {}), (False, "not a notice card"))

    def test_the_target_is_the_notices_own_session_and_the_text_takes_the_plain_message_door(self):
        # the review of PR 1757, high: an older row whose body names another session (written before the check refused it)
        # still delivers to the notice's OWN session; and a stored text never reaches the typed-command router
        row, err = km.post_notice(SID, "k", "t", producer="cli", actions=[{"label": "Send", "route": "/send", "body": {"text": "hello"}}], now=100)
        self.assertIsNone(err)
        rows = _rows(SID); rows[0]["actions"][0]["body"] = {"id": SID2, "name": "api", "text": "hello"}   # a row from before the check
        km._notice_path(SID).write_text("".join(json.dumps(r) + "\n" for r in rows)); km._NOTICE_MEMO.clear()
        iid = "notice:%s:k:1" % SID
        self.assertEqual(km._notice_action(iid, "/send", {"id": SID2, "name": "api", "text": "hello"}), (True, ""))
        self.assertEqual(self.w.delivered, [(SID, "hello")], "the row's sid, never the body's")
        # the plain door: the module's own _deliver_text (saved before the world stubbed it) never consults the command router
        # under plain=True and does under the route's own call
        saved = (km._route_meta_command, km.Sessions.backend_for, km._send_or_park, km._host_for_sid, km._postal_shaped)
        routed, parked = [], []
        try:
            km._route_meta_command = lambda be, sid, text, client=None, floating=False, state=None: (routed.append(text) or False)
            km.Sessions.backend_for = staticmethod(lambda sid: object()); km._host_for_sid = lambda sid: None; km._postal_shaped = lambda t: False
            km._send_or_park = lambda be, sid, text, **kw: (parked.append((sid, text, kw.get("user"))) or True)
            self.assertEqual(_REAL_DELIVER(SID, "/model opus", plain=True), (True, "", True)); self.assertEqual(routed, [], "plain: the router is never consulted")
            self.assertEqual(parked[-1], (SID, "/model opus", True), "the text goes as a message, as the user's words")
            self.assertEqual(_REAL_DELIVER(SID, "/model opus"), (True, "", True)); self.assertEqual(routed, ["/model opus"], "POST /send's own arm consults the router")
        finally:
            (km._route_meta_command, km.Sessions.backend_for, km._send_or_park, km._host_for_sid, km._postal_shaped) = saved

    def test_a_second_click_while_the_first_delivery_is_in_flight_is_refused_not_delivered_twice(self):
        # round three, low c: the pane re-arms on every push and a push the delivery causes can land before the answer
        import threading
        km.post_notice(SID, "k", "t", producer="cli", actions=[{"label": "Send", "route": "/send", "body": {"text": "hello"}}], now=100)
        iid = "notice:%s:k:1" % SID
        hold, started = threading.Event(), threading.Event()
        def slow(sid, text, plain=False):
            started.set(); hold.wait(5); self.w.delivered.append((sid, text)); return True, "", False
        km._deliver_text = slow
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("first", km._notice_action(iid, "/send", {"text": "hello"})), daemon=True); th.start()
        self.assertTrue(started.wait(5), "the first delivery is in flight")
        self.assertEqual(km._notice_action(iid, "/send", {"text": "hello"}), (False, "that action is already in flight"))
        hold.set(); th.join(5)
        self.assertEqual(out.get("first"), (True, "")); self.assertEqual(self.w.delivered, [(SID, "hello")], "one delivery")
        self.assertEqual(km._notice_action(iid, "/send", {"text": "hello"}), (True, ""), "after the answer the action runs again")

    def test_without_dismiss_on_action_the_card_stays(self):
        acts = [{"label": "Send", "route": "/send", "body": {"text": "hello"}}]
        km.post_notice(SID, "k", "t", producer="cli", actions=acts, now=100)
        iid = "notice:%s:k:1" % SID
        self.assertEqual(km._notice_action(iid, "/send", {"text": "hello"}), (True, ""))
        self.assertEqual(self.w.delivered, [(SID, "hello")], "a body without a target delivers to the card's own session")
        self.assertNotIn(iid, km._cleared_ids())
        self.assertEqual(km._notice_action(iid, "/send", {"text": "hello"}), (True, ""), "a card that stays is meant to run again")
        self.assertEqual(len(self.w.delivered), 2)


class OwnerLess(unittest.TestCase):
    """Owner-less cards (plans/notice-cards.md, "Owner-less cards and the terse command"; the user 2026-09-18): a card with no
    session lives in the reserved file notes.jsonl, shows under Notes with no colour, carries the board model's fields, has no
    actions, and rides the ledger, the pass and the archive bound as any notice card does."""
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def test_an_empty_sid_posts_to_the_reserved_home_and_the_card_reads_notes_with_no_colour(self):
        row, err = km.post_notice("", "k1", "Remember the standup moved", body="to 10:30", producer="cli", now=100, t=100)
        self.assertIsNone(err); self.assertEqual(row["sid"], km.NOTICE_OWNERLESS_SID)
        self.assertEqual(km.NOTICE_OWNERLESS_SID, "notes", "a word: a uuid is hex and hyphens, so no sid can collide")
        self.assertTrue((km.jd.STATE / "notices" / "notes.jsonl").exists(), "the reserved file under STATE/notices")
        self.assertEqual([r["op"] for r in _rows(km.NOTICE_OWNERLESS_SID)], ["post"])
        cards = km._notice_cards(500, set())
        self.assertEqual(len(cards), 1); c = cards[0]
        self.assertEqual(c["itemId"], "notice:notes:k1:1", "the same id shape, the reserved key in the sid slot")
        self.assertEqual((c["sid"], c["name"], c["color"]), ("notes", "Notes", None), "the run reads Notes with no identity colour")
        self.assertEqual((c["board"], c["category"], c["column"]), ("feed", "completed", "completed"), "the board model's fields beside the column")
        self.assertEqual(c["text"], "Remember the standup moved"); self.assertEqual(c["notice"]["body"], "to 10:30")
        # needs-you still decides the category: an owner-less card that needs you files under needs_input
        km.post_notice(None, "k2", "Decide the venue", producer="cli", needs_you=True, now=200, t=200)
        by = {c["itemId"]: c for c in km._notice_cards(500, set())}
        self.assertEqual((by["notice:notes:k2:1"]["category"], by["notice:notes:k2:1"]["column"]), ("needs_input", "needs_input"))
        # every session card carries the two fields too
        km.post_notice(SID, "s1", "Mine", producer="cli", now=300, t=300)
        by = {c["itemId"]: c for c in km._notice_cards(500, set())}
        self.assertEqual((by["notice:%s:s1:1" % SID]["board"], by["notice:%s:s1:1" % SID]["category"]), ("feed", "completed"))
        self.assertEqual((by["notice:%s:s1:1" % SID]["name"], by["notice:%s:s1:1" % SID]["color"] is not None), ("web", True), "a session's card keeps its name and colour")

    def test_an_owner_less_card_carries_no_actions_and_a_hand_made_action_id_is_refused(self):
        acts = [{"label": "Send", "route": "/send", "body": {"text": "x"}}]
        row, err = km.post_notice("", "k1", "t", producer="cli", actions=acts, now=100)
        self.assertEqual(row, None); self.assertEqual(err, "an owner-less card has no session to send to: actions need a session")
        self.assertEqual(_rows(km.NOTICE_OWNERLESS_SID), [], "nothing written")
        km.post_notice("", "k1", "t", producer="cli", now=100)
        self.assertEqual(km._notice_action("notice:notes:k1:1", "/send", {"text": "x"}), (False, "an owner-less card has no actions"))
        self.assertEqual(self.w.delivered, [], "nothing delivered")

    def test_the_reserved_word_as_a_name_is_refused_and_only_an_empty_sid_takes_the_owner_less_road(self):
        # round two of PR 1831: _sid_of hands an unresolved name back unchanged, so "notes" as a name reached post_notice as the
        # reserved sid and the store's session check answered yes; the owner-less road is an EMPTY sid alone
        row, err = km.post_notice("notes", "k1", "t", producer="cli", now=100)
        self.assertEqual((row, err), (None, 'no session answers to "notes"'), "the reserved word arriving as a sid is a name no session answers to")
        self.assertFalse(km._notice_path("notes").exists(), "nothing reached the reserved home")
        self.assertFalse(km._notice_session_known("notes"), "the session check never answers for the key")
        row, err = km.expire_notice("notes", "k1")
        self.assertEqual((row, err), (None, 'no session answers to "notes"'), "the expire door too")
        # a DEAD session actually named notes: its name resolves to no live session, so it is refused as any dead name is,
        # and never retargets the shared home
        dead = "11111111-2222-3333-4444-777777777777"
        (km.jd.NAMES / dead).write_text("notes\t%s\t#B69513\tblack\n" % self.w.cwd)
        self.assertEqual(km._sid_of("notes"), "notes", "the dead name falls through unresolved (the registry is sid-keyed)")
        row, err = km.post_notice(km._sid_of("notes"), "k1", "t", producer="cli", now=100)
        self.assertEqual((row, err), (None, 'no session answers to "notes"'))
        row, err = km.post_notice(dead, "k1", "t", producer="cli", now=100)
        self.assertEqual((err, row["sid"]), (None, dead), "by its sid the dead session takes its own home, as any session's card does")
        self.assertFalse(km._notice_path("notes").exists())
        # the owner-less road: an empty sid, and nothing else
        row, err = km.post_notice("", "k1", "t", producer="cli", now=100)
        self.assertEqual((err, row["sid"]), (None, "notes"))
        row, err = km.post_notice(None, "k2", "t", producer="cli", now=100)
        self.assertEqual((err, row["sid"]), (None, "notes"))

    def test_the_reveal_road_refuses_the_reserved_key_loudly_instead_of_offering_a_revive(self):
        # round three of PR 1831, medium A: showOnTimeline with sid notes reached _reveal_or_confirm, which found notes absent from
        # the live map and popped confirmRevive for a session that never existed. The confirm travels _reveal_chat_for ->
        # _send_to_view (the asking window's chat), never the asking client's own send, so THAT is the stub (round four, low: an
        # assertFalse over the client's messages could never fail); a dead real session is the control that shows the stub sees one.
        seen = []
        saved = (km._send_to_view, km._reaffirm_active_chat)
        km._send_to_view = lambda app, msg, wid="": seen.append((app, msg))
        km._reaffirm_active_chat = lambda client: None
        try:
            sent = []; client = {"send": lambda m: sent.append(json.loads(m)), "wid": "w1"}
            dead = "11111111-2222-3333-4444-777777777777"
            km._reveal_or_confirm(dead, {"type": "showOnTimeline", "itemId": "notice:%s:k:1" % dead}, client)
            self.assertEqual([(a, m["id"]) for a, m in seen if m.get("type") == "confirmRevive"], [("chat", dead)], "a dead real session still gets the confirm, through the asking window's chat")
            self.assertEqual(sent, [], "and no error")
            seen.clear()
            km._reveal_or_confirm("notes", {"type": "showOnTimeline", "itemId": "notice:notes:k:1"}, client)
            self.assertEqual(seen, [], "nothing reaches the chat for the reserved key: no confirmRevive, no focus")
            self.assertEqual(len(sent), 1); self.assertEqual(sent[0]["type"], "err", "an error to the asking pane instead")
            self.assertIn("belongs to no session", sent[0]["text"]); self.assertIn("nothing to revive", sent[0]["text"])
        finally:
            km._send_to_view, km._reaffirm_active_chat = saved

    def test_owner_less_cards_ride_the_ledger_the_pass_the_index_and_undo_like_any_notice_card(self):
        km.post_notice("", "k1", "first", producer="cli", now=100, t=100)
        iid = "notice:notes:k1:1"
        km._clear_ask(iid)
        self.assertEqual(km._notice_cards(200, km._cleared_ids()), [], "dismissed: hidden")
        self.assertEqual(km._compact_notices(now=300), 1, "the pass archives the owner-less file's row")
        self.assertTrue((km._notice_archive_dir() / "notes.jsonl").exists()); self.assertTrue(km._notice_revs_path("notes").exists(), "its own revision index")
        self.assertEqual(json.loads(km._notice_revs_path("notes").read_text())["revs"], {"k1": 1})
        row, err = km.post_notice("", "k1", "second", producer="cli", now=400, t=400)
        self.assertEqual((err, row["rev"]), (None, 2), "the index counts the archived revision: never rev 1 again")
        self.assertEqual(km._undo_clear(), {})
        self.assertEqual(sorted(c["itemId"] for c in km._notice_cards(500, km._cleared_ids())), ["notice:notes:k1:2"], "rev 1 back live but superseded by rev 2")
        self.assertEqual([r["rev"] for r in _rows("notes") if r["op"] == "post"], [2, 1], "the restored row is live again")
        # the fifty-live-keys cap applies to the owner-less home on its own
        for i in range(60):
            km.post_notice("", "cap%02d" % i, "c", producer="cli", now=1000 + i, t=1000 + i)
        live = km._notice_cards(2000, km._cleared_ids())
        self.assertEqual(len(live), km.NOTICE_LIVE_KEYS_MAX, "capped as one session's would be")


class Retention(unittest.TestCase):
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def test_the_sweep_archives_dismissed_expired_and_superseded_rows_and_deletes_none(self):
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km.post_notice(SID, "figure", "second", producer="figure", now=200, t=200)
        km.post_notice(SID, "soon", "expires", producer="figure", now=100, t=100, expires_at=500)
        km.post_notice(SID, "gone", "dismissed", producer="figure", now=100, t=100)
        km.post_notice(SID, "keep", "stays", producer="figure", now=100, t=100)
        km.expire_notice(SID, "keep", now=150)
        km.post_notice(SID, "keep", "stays again", producer="figure", now=160, t=160)
        km._clear_ask("notice:%s:gone:1" % SID)
        before = _rows(SID)
        moved = km._compact_notices(now=600)
        live, arch = _rows(SID), [json.loads(l) for l in (km._notice_archive_dir() / (SID + ".jsonl")).read_text().splitlines()]
        self.assertEqual(moved, len(arch)); self.assertEqual(len(live) + len(arch), len(before), "nothing deleted")
        self.assertEqual(sorted((r["key"], r["rev"], r["op"]) for r in live), [("figure", 2, "post"), ("keep", 2, "post")])
        self.assertEqual(sorted((r["key"], r["rev"], r["op"]) for r in arch),
                         [("figure", 1, "post"), ("gone", 1, "post"), ("keep", 1, "expire"), ("keep", 1, "post"), ("soon", 1, "post")])
        # an acted mark goes with its target's post and not before (Undo may still show the spent card until then)
        km.post_notice(SID, "act", "t", producer="cli", actions=[{"label": "Send", "route": "/send", "body": {"text": "x"}}], dismiss_on_action=True, now=700)
        km._notice_action("notice:%s:act:1" % SID, "/send", {"text": "x"}); km._undo_clear()
        self.assertEqual(km._compact_notices(now=800), 0, "the spent card, visible after Undo, keeps its post and its acted mark")
        km._clear_ask("notice:%s:act:1" % SID)
        self.assertEqual(km._compact_notices(now=900), 2, "dismissed again: the post and its acted mark go together")
        self.assertEqual(km._compact_notices(now=700), 0, "an unmoved file is skipped")
        self.assertEqual([c["itemId"] for c in km._notice_cards(700, km._cleared_ids())], ["notice:%s:keep:2" % SID, "notice:%s:figure:2" % SID], "post order by t")

    def test_undo_restores_a_dismissed_card_the_housekeeping_pass_had_archived(self):
        # round six, high: the archive pass runs inside the housekeeping pass, seconds after a Clear, so Undo must find the
        # card's rows in the archive and move them back (the reference: Clear; Undo restores it)
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        iid = "notice:%s:figure:1" % SID
        self.assertEqual(km._compact_notices(now=200), 0, "a live card stays")
        km._clear_ask(iid)
        self.assertEqual(km._compact_notices(now=300), 1, "the pass archives the dismissed row")
        self.assertEqual(_rows(SID), [], "gone from the live file")
        self.assertEqual(km._undo_clear(), {}, "no fault")
        self.assertNotIn(iid, km._cleared_ids())
        self.assertEqual([c["itemId"] for c in km._notice_cards(400, km._cleared_ids())], [iid], "post, pass, Clear, pass, Undo: the card is back")
        self.assertEqual([(r["key"], r["rev"], r["op"]) for r in _rows(SID)], [("figure", 1, "post")], "its row is live again")
        self.assertEqual((km._notice_archive_dir() / (SID + ".jsonl")).read_text(), "", "moved back, not copied")
        self.assertEqual(km._compact_notices(now=500), 0, "restored and undismissed: the pass keeps it")
        # the one-shot mark rides back with its post: a spent card returns spent, and a click delivers nothing
        acts = [{"label": "Send", "route": "/send", "body": {"text": "x"}}]
        km.post_notice(SID, "act", "t", producer="cli", actions=acts, dismiss_on_action=True, now=600, t=600)
        aid = "notice:%s:act:1" % SID
        self.assertEqual(km._notice_action(aid, "/send", {"text": "x"}), (True, ""))
        self.assertEqual(km._compact_notices(now=700), 2, "the post and its acted mark are archived together")
        km._undo_clear()
        back = next(c for c in km._notice_cards(800, km._cleared_ids()) if c["itemId"] == aid)
        self.assertEqual((back["notice"]["acted"], back["notice"]["actions"]), (True, []), "back spent")
        self.assertEqual(km._notice_action(aid, "/send", {"text": "x"}), (False, "that card's action ran already"))
        self.assertEqual(self.w.delivered, [(SID, "x")], "one delivery across click, Clear, pass, Undo, click")
        # Undo walks back one batch a press, so an older batch's rows must come back from the archive too, not the newest's alone
        km._clear_ask(iid); km._clear_ask(aid)
        self.assertEqual(km._compact_notices(now=900), 3)
        km._undo_clear()
        self.assertEqual([c["itemId"] for c in km._notice_cards(1000, km._cleared_ids())], [aid], "the newest batch first")
        km._undo_clear()
        self.assertEqual(sorted(c["itemId"] for c in km._notice_cards(1100, km._cleared_ids())), sorted([iid, aid]), "then the older")

    def test_a_repost_under_a_dismissed_key_takes_the_next_revision_once_the_pass_archived_the_first(self):
        # round six, medium: the revision counted the live file alone, so a repost after the pass took rev 1 again, an id the
        # cleared ledger still held: the projection dropped it while the producer was told the card was up
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km._clear_ask("notice:%s:figure:1" % SID)
        self.assertEqual(km._compact_notices(now=200), 1)
        row, err = km.post_notice(SID, "figure", "second", producer="figure", now=300, t=300)
        self.assertEqual((err, row["rev"]), (None, 2), "post, dismiss, pass, repost: rev 2")
        self.assertEqual([c["itemId"] for c in km._notice_cards(400, km._cleared_ids())], ["notice:%s:figure:2" % SID], "the card shows")
        # the count follows the rows wherever they sit: Undo moves rev 1 back live (superseded by rev 2), a third post is rev 3
        km._undo_clear()
        self.assertEqual([c["itemId"] for c in km._notice_cards(500, km._cleared_ids())], ["notice:%s:figure:2" % SID], "rev 1 back but superseded")
        row, err = km.post_notice(SID, "figure", "third", producer="figure", now=600, t=600)
        self.assertEqual((err, row["rev"]), (None, 3))
        # the archive bound: a post reads the revision index, never the archive; an index that cannot be read refuses the post
        # with its reason, an absent index over an archive is rebuilt from one read, an absent index over an archive that
        # cannot be read refuses; never a revision minted blind
        km._clear_ask("notice:%s:figure:3" % SID); self.assertEqual(km._compact_notices(now=700), 3)
        ap, ip = km._notice_archive_dir() / (SID + ".jsonl"), km._notice_revs_path(SID)
        self.assertEqual(_revs(ip), {"figure": 3}, "the pass wrote the high-water mark")
        ip.unlink(); ip.mkdir()
        row, err = km.post_notice(SID, "figure", "fourth", producer="figure", now=800)
        self.assertEqual(row, None); self.assertIn("revision index could not be read", err)
        ip.rmdir()
        row, err = km.post_notice(SID, "figure", "fourth", producer="figure", now=800)
        self.assertEqual((err, row["rev"]), (None, 4), "absent index over the archive: rebuilt, then read")
        self.assertEqual(_revs(ip), {"figure": 3}, "the rebuilt index stands on disk")
        ip.unlink(); saved = ap.read_bytes(); ap.unlink(); ap.mkdir()
        row, err = km.post_notice(SID, "figure", "fifth", producer="figure", now=900)
        self.assertEqual(row, None); self.assertIn("the notice archive could not be read", err)
        ap.rmdir(); ap.write_bytes(saved)
        row, err = km.post_notice(SID, "figure", "fifth", producer="figure", now=900)
        self.assertEqual((err, row["rev"]), (None, 5))

    def test_an_unreadable_archive_keeps_the_undo_owed_and_says_so(self):
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        iid = "notice:%s:figure:1" % SID
        km._clear_ask(iid); self.assertEqual(km._compact_notices(now=200), 1)
        ap = km._notice_archive_dir() / (SID + ".jsonl"); saved = ap.read_text(); ap.unlink(); ap.mkdir()
        faults = km._undo_clear()
        self.assertEqual(list(faults), ["notice:" + SID], "keyed apart from a goals-file fault"); self.assertIn("the notice archive could not be read", faults["notice:" + SID])
        self.assertIn(iid, km._cleared_ids(), "re-journaled: the batch stays owed for the next Undo")
        self.assertEqual(km._notice_cards(300, km._cleared_ids()), [], "nothing restored blind")
        # the refusal is worded per store (round six, low): the notice archive's fault says notice cards and names the session,
        # never that none of its cards came back; a goals-file fault keeps its own sentence
        sent = []; client = {"send": lambda m: sent.append(json.loads(m))}
        km._gesture_store_refusal(client, "undo", faults)
        self.assertEqual((sent[0]["type"], sent[0]["sid"]), ("err", SID))
        self.assertIn("notice cards were not restored", sent[0]["text"]); self.assertIn("notice archive", sent[0]["text"])
        self.assertNotIn("goals file", sent[0]["text"]); self.assertIn("Its other cards and the other sessions were not affected", sent[0]["text"])
        km._gesture_store_refusal(client, "undo", {SID: "a goals fault"})
        self.assertIn("goals file (a goals fault)", sent[1]["text"]); self.assertNotIn("notice", sent[1]["text"])
        ap.rmdir(); ap.write_text(saved)
        self.assertEqual(km._undo_clear(), {})
        self.assertEqual([c["itemId"] for c in km._notice_cards(400, km._cleared_ids())], [iid], "the next Undo restores it")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads through chmod 0; the permission fault needs a real one")
    def test_an_unreadable_archive_directory_refuses_the_post_rather_than_minting_a_revision_blind(self):
        # round six, low: the stat helper folds a FAILED stat into None, the absent-file answer, so with the archive directory
        # unreadable a repost minted rev 1 blind and reported success on an invisible card; the stat is explicit now, on the
        # revision index, which the post reads in the archive's place (the archive bound)
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km._clear_ask("notice:%s:figure:1" % SID); self.assertEqual(km._compact_notices(now=200), 1)
        ad = km._notice_archive_dir(); ap = ad / (SID + ".jsonl"); ip = km._notice_revs_path(SID)
        try:
            os.chmod(ad, 0)                                   # the directory: the index's stat fails, which is not an absent file
            row, err = km.post_notice(SID, "figure", "second", producer="figure", now=300)
            self.assertEqual(row, None); self.assertIn("revision index could not be read", err)
        finally:
            os.chmod(ad, 0o755)
        try:
            os.chmod(ip, 0)                                   # the index file: its stat reads, its bytes do not
            row, err = km.post_notice(SID, "figure", "second", producer="figure", now=300)
            self.assertEqual(row, None); self.assertIn("revision index could not be read", err)
        finally:
            os.chmod(ip, 0o644)
        try:
            os.chmod(ap, 0)                                   # the archive itself unreadable: the post never opens it
            row, err = km.post_notice(SID, "figure", "second", producer="figure", now=300)
            self.assertEqual((err, row["rev"]), (None, 2), "the index answers in the archive's place")
        finally:
            os.chmod(ap, 0o644)
        row, err = km.post_notice(SID, "figure", "third", producer="figure", now=400)
        self.assertEqual((err, row["rev"]), (None, 3), "readable again: the count resumes")

    def test_the_pass_writes_the_revision_index_before_it_archives_and_an_undo_never_lowers_it(self):
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km.post_notice(SID, "figure", "second", producer="figure", now=110, t=110)
        km.post_notice(SID, "other", "o", producer="figure", now=120, t=120)
        ip = km._notice_revs_path(SID)
        self.assertFalse(ip.exists(), "nothing archived, no index")
        self.assertEqual(km._compact_notices(now=200), 1, "rev 1 of figure superseded")
        self.assertEqual(_revs(ip), {"figure": 1})
        km._clear_ask("notice:%s:figure:2" % SID); km._clear_ask("notice:%s:other:1" % SID)
        self.assertEqual(km._compact_notices(now=300), 2)
        self.assertEqual(_revs(ip), {"figure": 2, "other": 1}, "the high-water mark of every post row that left")
        km._undo_clear(); km._undo_clear()
        self.assertEqual(_revs(ip), {"figure": 2, "other": 1}, "Undo moves rows back live and never lowers the mark")
        row, err = km.post_notice(SID, "figure", "third", producer="figure", now=400, t=400)
        self.assertEqual((err, row["rev"]), (None, 3), "1 + max(live 2, index 2)")
        # the pass holds a session's rows when its index cannot be written: nothing archived, nothing lost, the reason said
        km._clear_ask("notice:%s:figure:3" % SID)
        ip.unlink(); ip.mkdir()
        try:
            self.assertEqual(km._compact_notices(now=500), 0, "held")
            self.assertEqual(len(_rows(SID)), 3, "every row still live (figure 2 and other 1 back from Undo, figure 3)")
        finally:
            ip.rmdir()
        # the archive holds figure 1 alone (the two restored rows left it), so the rebuild reads {figure: 1}; then the pass
        # archives rev 3 (dismissed) and rev 2 (superseded) and raises the mark; other 1 is live and undismissed, so it stays
        self.assertEqual(km._compact_notices(now=600), 2, "rebuilt from the archive, then the pass runs")
        self.assertEqual(_revs(ip), {"figure": 3})

    def test_the_index_memo_counts_under_the_rows_memos_byte_bound_and_in_the_report(self):
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km._clear_ask("notice:%s:figure:1" % SID); self.assertEqual(km._compact_notices(now=200), 1)
        bound = km.NOTICE_MEMO_BYTES
        try:
            with km._notice_lock:
                km._NOTICE_MEMO.clear(); km._NOTICE_ARCH_REVS.clear()
            km.post_notice(SID, "figure", "second", producer="figure", now=300, t=300)   # the post read the index into its memo
            rep = km._notice_memo_report()
            self.assertEqual(rep["entries"], 1, "the index memo is an entry of memos.notices")
            self.assertEqual(rep["bytes"], len(km._notice_revs_path(SID).read_text()), "its bytes are the file's")
            km._notice_rows(SID)
            rep2 = km._notice_memo_report()
            self.assertEqual(rep2["entries"], 2); self.assertGreater(rep2["bytes"], rep["bytes"], "the rows memo joins the same total")
            km.NOTICE_MEMO_BYTES = 1
            evicted = km._NOTICE_MEMO_STATS["evicted"]
            km.post_notice(SID, "figure", "third", producer="figure", now=400, t=400)   # the live file moves (the index memo is a hit: no read)
            km._notice_rows(SID)                                                           # a fresh read under the tiny bound sheds
            self.assertEqual(km._NOTICE_MEMO_STATS["evicted"], evicted + 2, "over the bound the largest entries go, whichever memo holds them")
            self.assertLessEqual(km._notice_memo_report()["bytes"], 1)
        finally:
            km.NOTICE_MEMO_BYTES = bound

    def test_the_restore_reads_the_archive_from_the_tail_and_stops_at_its_batchs_pass(self):
        block = km.NOTICE_ARCHIVE_READ_BLOCK
        try:
            km.NOTICE_ARCHIVE_READ_BLOCK = 64                  # rows are a few hundred bytes: many blocks a pass
            for i in range(1, 7):
                km.post_notice(SID, "k%d" % i, "card %d" % i, producer="figure", now=100 + i, t=100 + i)
            ids = ["notice:%s:k%d:1" % (SID, i) for i in range(1, 7)]
            km._clear_all(ids[0:2]); self.assertEqual(km._compact_notices(now=200), 2)
            km._clear_all(ids[2:4]); self.assertEqual(km._compact_notices(now=300), 2)
            km._clear_all(ids[4:6]); self.assertEqual(km._compact_notices(now=400), 2)
            ap = km._notice_archive_dir() / (SID + ".jsonl")
            arch = [json.loads(l) for l in ap.read_text().splitlines()]
            self.assertEqual([r["archivedAt"] for r in arch], [200, 200, 300, 300, 400, 400], "one stamp a pass, one block a pass")
            km._NOTICE_ARCH_READ.update(rows=0, bytes=0)
            self.assertEqual(km._undo_clear(), {})
            self.assertEqual(km._NOTICE_ARCH_READ["rows"], 3, "the newest batch's two rows and the one older row that stopped the read")
            self.assertLess(km._NOTICE_ARCH_READ["bytes"], ap.stat().st_size + 2 * 64 + 400, "the head was not read")
            self.assertEqual([r["key"] for r in map(json.loads, ap.read_text().splitlines())], ["k1", "k2", "k3", "k4"], "the head byte for byte, the tail's other lines kept")
            self.assertEqual(sorted(c["itemId"] for c in km._notice_cards(500, km._cleared_ids())), sorted(ids[4:6]))
            self.assertTrue(all("archivedAt" not in r for r in _rows(SID)), "the stamp is stripped on the way back")
            km._NOTICE_ARCH_READ.update(rows=0, bytes=0)
            km._undo_clear()
            self.assertEqual(km._NOTICE_ARCH_READ["rows"], 3, "the next press walks to the next pass and no further")
            km._NOTICE_ARCH_READ.update(rows=0, bytes=0)
            km._undo_clear()
            self.assertEqual(km._NOTICE_ARCH_READ["rows"], 2, "the oldest pass: the file's start ends the read")
            self.assertEqual(ap.read_text(), "", "the archive is left empty, not duplicated")
            self.assertEqual(len(km._notice_cards(600, km._cleared_ids())), 6)
        finally:
            km.NOTICE_ARCHIVE_READ_BLOCK = block

    def test_an_index_left_behind_the_archive_is_rebuilt_not_trusted(self):
        # round two, low 1: a rollback to a kernel that archives and writes no index leaves the index BEHIND the archive; a
        # repost trusting it recycled an id the cleared ledger holds, invisible with a success answer. The index records the
        # archive's size and mtime it describes, and a mismatch rebuilds it from one whole read
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km._clear_ask("notice:%s:figure:1" % SID); self.assertEqual(km._compact_notices(now=200), 1)
        ap, ip = km._notice_archive_dir() / (SID + ".jsonl"), km._notice_revs_path(SID)
        self.assertEqual(json.loads(ip.read_text())["archive"], {"size": ap.stat().st_size, "mtimeNs": ap.stat().st_mtime_ns},
                         "the pass's second write describes the archive as appended")
        rebuilt = km._NOTICE_ARCH_REBUILDS["count"]
        km.post_notice(SID, "figure", "second", producer="figure", now=300, t=300)
        self.assertEqual(km._NOTICE_ARCH_REBUILDS["count"], rebuilt, "the index describes the archive: no rebuild at a post")
        # the older kernel's pass, by hand: rev 2 dismissed and moved to the archive, the index untouched
        km._clear_ask("notice:%s:figure:2" % SID)
        live = _rows(SID); row2 = next(r for r in live if r["rev"] == 2)
        with open(ap, "a") as f:
            f.write(json.dumps(row2) + "\n")
        km._notice_path(SID).write_text("".join(json.dumps(r) + "\n" for r in live if r["rev"] != 2))
        row, err = km.post_notice(SID, "figure", "third", producer="figure", now=400, t=400)
        self.assertEqual((err, row["rev"]), (None, 3), "post, an older kernel's pass, repost: rev 3, never rev 2 again")
        self.assertEqual(km._NOTICE_ARCH_REBUILDS["count"], rebuilt + 1, "one whole read rebuilt the index")
        self.assertEqual(_revs(ip), {"figure": 2}); self.assertNotIn("notice:%s:figure:3" % SID, km._cleared_ids())
        # a bare map of the first shape reads as behind an unknown archive: rebuilt too
        ip.write_text(json.dumps({"figure": 1}))
        with km._notice_lock:
            km._NOTICE_ARCH_REVS.clear()
        row, err = km.post_notice(SID, "figure", "fourth", producer="figure", now=500, t=500)
        self.assertEqual((err, row["rev"], km._NOTICE_ARCH_REBUILDS["count"]), (None, 4, rebuilt + 2))
        # the restore rewrites the archive and refreshes the stat the index describes: the next post rebuilds nothing
        km._undo_clear()
        n = km._NOTICE_ARCH_REBUILDS["count"]
        km.post_notice(SID, "figure", "fifth", producer="figure", now=600, t=600)
        self.assertEqual(km._NOTICE_ARCH_REBUILDS["count"], n, "the restore's index write covers its rewrite")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes through chmod; the permission fault needs a real one")
    def test_a_rebuild_whose_index_write_fails_still_answers_the_post(self):
        # round two, low 2: the true map was in hand and the post was refused for a write that failed; the map is returned
        # uncached, the next post rebuilds again, and only an archive that cannot be read refuses
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km._clear_ask("notice:%s:figure:1" % SID); self.assertEqual(km._compact_notices(now=200), 1)
        ad, ip = km._notice_archive_dir(), km._notice_revs_path(SID)
        ip.unlink(); rebuilt = km._NOTICE_ARCH_REBUILDS["count"]
        try:
            os.chmod(ad, 0o555)                          # the archive readable, the index unwritable
            row, err = km.post_notice(SID, "figure", "second", producer="figure", now=300, t=300)
            self.assertEqual((err, row["rev"]), (None, 2), "rebuilt from the archive and answered")
            self.assertFalse(ip.exists(), "the write failed: no index")
            row, err = km.post_notice(SID, "figure", "third", producer="figure", now=400, t=400)
            self.assertEqual((err, row["rev"], km._NOTICE_ARCH_REBUILDS["count"]), (None, 3, rebuilt + 2), "rebuilt again, uncached")
        finally:
            os.chmod(ad, 0o755)
        row, err = km.post_notice(SID, "figure", "fourth", producer="figure", now=500, t=500)
        self.assertEqual((err, row["rev"]), (None, 4)); self.assertTrue(ip.exists(), "writable again: the index stands")

    def test_the_index_memo_counts_its_hits_and_misses_in_the_report(self):
        # round two, low 3: the report counted entries and bytes for both memos but hits and misses for the rows memo alone
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km._clear_ask("notice:%s:figure:1" % SID); self.assertEqual(km._compact_notices(now=200), 1)
        with km._notice_lock:
            km._NOTICE_MEMO.clear(); km._NOTICE_ARCH_REVS.clear()
        hit, miss = km._NOTICE_MEMO_STATS["hit"], km._NOTICE_MEMO_STATS["miss"]
        km.post_notice(SID, "figure", "second", producer="figure", now=300, t=300)
        self.assertEqual((km._NOTICE_MEMO_STATS["hit"], km._NOTICE_MEMO_STATS["miss"]), (hit, miss + 1), "the index file read is a miss")
        km.post_notice(SID, "figure", "third", producer="figure", now=400, t=400)
        self.assertEqual((km._NOTICE_MEMO_STATS["hit"], km._NOTICE_MEMO_STATS["miss"]), (hit + 1, miss + 1), "a stat-match is a hit")
        rep = km._notice_memo_report()
        self.assertEqual((rep["hit"], rep["miss"]), (km._NOTICE_MEMO_STATS["hit"], km._NOTICE_MEMO_STATS["miss"]))

    def test_an_undo_landing_on_an_index_behind_the_archive_does_not_bless_it(self):
        # round three, medium: the restore re-described the STANDING map over the rewritten archive, so an index a rollback had
        # left behind was blessed by an Undo that landed before the next post, and that post minted a revision the cleared
        # ledger holds with a success answer and an empty board. The refresh happens only when the description the restore
        # read matched the archive as it stood before the rewrite; otherwise the next post pays one rebuild
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km.post_notice(SID, "other", "o", producer="figure", now=110, t=110)
        km._clear_ask("notice:%s:figure:1" % SID); km._clear_ask("notice:%s:other:1" % SID)   # other's clear is the newest batch
        self.assertEqual(km._compact_notices(now=200), 2)
        row, err = km.post_notice(SID, "figure", "second", producer="figure", now=300, t=300)
        self.assertEqual((err, row["rev"]), (None, 2))
        # an older kernel's pass, by hand: rev 2 dismissed (a ledger row older than other's clear) and moved to the archive
        ap, ip = km._notice_archive_dir() / (SID + ".jsonl"), km._notice_revs_path(SID)
        live = _rows(SID); row2 = next(r for r in live if r["key"] == "figure" and r["rev"] == 2)
        with (km.jd.STATE / "cleared.jsonl").open("a") as f:
            f.write(json.dumps({"id": "notice:%s:figure:2" % SID, "t": 1.0, "op": "clear"}) + "\n")
        with open(ap, "a") as f:
            f.write(json.dumps(row2) + "\n")
        km._notice_path(SID).write_text("".join(json.dumps(r) + "\n" for r in live if not (r["key"] == "figure" and r["rev"] == 2)))
        self.assertEqual(_revs(ip), {"figure": 1, "other": 1}, "the index is behind the archive")
        self.assertEqual(km._undo_clear(), {}, "Undo restores other before any post")
        self.assertEqual([c["itemId"] for c in km._notice_cards(400, km._cleared_ids())], ["notice:%s:other:1" % SID])
        self.assertNotEqual(json.loads(ip.read_text())["archive"], {"size": ap.stat().st_size, "mtimeNs": ap.stat().st_mtime_ns},
                            "the restore left the stale description alone: it did not describe the archive it rewrote")
        rebuilt = km._NOTICE_ARCH_REBUILDS["count"]
        row, err = km.post_notice(SID, "figure", "third", producer="figure", now=500, t=500)
        self.assertEqual((err, row["rev"], km._NOTICE_ARCH_REBUILDS["count"]), (None, 3, rebuilt + 1), "Undo, then post: one rebuild, rev 3, never rev 2 again")
        self.assertNotIn("notice:%s:figure:3" % SID, km._cleared_ids())
        # an index that DID describe the archive is re-described by the restore, and the next post rebuilds nothing
        km._clear_ask("notice:%s:other:1" % SID); self.assertEqual(km._compact_notices(now=600), 1)
        km._undo_clear(); n = km._NOTICE_ARCH_REBUILDS["count"]
        km.post_notice(SID, "figure", "fourth", producer="figure", now=700, t=700)
        self.assertEqual(km._NOTICE_ARCH_REBUILDS["count"], n, "the refresh covered the rewrite")

    def test_an_index_over_a_vanished_archive_keeps_its_marks_and_a_rebuild_never_lowers_them(self):
        # round three, low: an index over a VANISHED archive was reset to {}, so a dismissed key minted rev 1 again against the
        # reference's promise; the marks stand and the index says it describes no archive, and a rebuild merges the standing
        # marks so an archive that came back older cannot lower them
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        km._clear_ask("notice:%s:figure:1" % SID); self.assertEqual(km._compact_notices(now=200), 1)
        km.post_notice(SID, "figure", "second", producer="figure", now=300, t=300)
        km._clear_ask("notice:%s:figure:2" % SID); self.assertEqual(km._compact_notices(now=400), 1)
        ap, ip = km._notice_archive_dir() / (SID + ".jsonl"), km._notice_revs_path(SID)
        self.assertEqual(_revs(ip), {"figure": 2})
        older = ap.read_text().splitlines()[0]        # rev 1's row, as an older backup would hold it
        ap.unlink()                                   # the archive vanishes under its index
        rebuilt = km._NOTICE_ARCH_REBUILDS["count"]
        row, err = km.post_notice(SID, "figure", "third", producer="figure", now=500, t=500)
        self.assertEqual((err, row["rev"]), (None, 3), "the marks stand: never rev 1 or 2 again")
        self.assertEqual((_revs(ip), json.loads(ip.read_text())["archive"], km._NOTICE_ARCH_REBUILDS["count"]), ({"figure": 2}, None, rebuilt), "no rebuild, the index describes no archive")
        self.assertNotIn("notice:%s:figure:3" % SID, km._cleared_ids())
        # the archive comes back older (rev 1 alone): the rebuild merges the standing mark, never lowering it
        ap.write_text(older + "\n")
        km._clear_ask("notice:%s:figure:3" % SID)   # rev 3 dismissed, still live: the post below counts it live either way
        row, err = km.post_notice(SID, "figure", "fourth", producer="figure", now=600, t=600)
        self.assertEqual((err, row["rev"], km._NOTICE_ARCH_REBUILDS["count"]), (None, 4, rebuilt + 1))
        self.assertEqual(_revs(ip), {"figure": 2}, "merged: the archive's 1 never lowered the standing 2")

    def test_rows_archived_before_the_stamp_existed_are_read_as_one_block(self):
        for i in range(1, 4):
            km.post_notice(SID, "k%d" % i, "card %d" % i, producer="figure", now=100 + i, t=100 + i)
        ids = ["notice:%s:k%d:1" % (SID, i) for i in range(1, 4)]
        km._clear_all(ids[0:1]); km._compact_notices(now=200)
        km._clear_all(ids[1:3]); km._compact_notices(now=300)
        ap = km._notice_archive_dir() / (SID + ".jsonl")
        ap.write_text("".join(json.dumps({k: v for k, v in r.items() if k != "archivedAt"}) + "\n" for r in map(json.loads, ap.read_text().splitlines())))
        km._NOTICE_ARCH_READ.update(rows=0, bytes=0)
        km._undo_clear()
        self.assertEqual(km._NOTICE_ARCH_READ["rows"], 3, "no stamp to stop at: the whole file")
        self.assertEqual([r["key"] for r in map(json.loads, ap.read_text().splitlines())], ["k1"])
        self.assertEqual(sorted(c["itemId"] for c in km._notice_cards(400, km._cleared_ids())), sorted(ids[1:3]))

    def test_the_pass_reads_the_ledger_under_the_lock_so_an_undo_landing_during_it_is_not_archived_back_out(self):
        # round six, low: the pass snapshotted the cleared ledger once before its per-session loop, so an Undo that landed
        # while it walked an earlier session had its restored row archived back out under the stale snapshot, and no later
        # Undo could reach it; the ledger is read per session, under the lock, after the file's stat
        km.post_notice(SID, "figure", "first", producer="figure", now=100, t=100)
        iid = "notice:%s:figure:1" % SID
        km._clear_ask(iid); self.assertEqual(km._compact_notices(now=200), 1)
        real, fired = km._stat_key, []
        def stat_then_undo(path):
            if not fired and Path(path) == km._notice_path(SID):   # the pass reaches this session's file: the Undo lands first
                fired.append(1); km._undo_clear()
            return real(path)
        km._stat_key = stat_then_undo
        try:
            moved = km._compact_notices(now=300)
        finally:
            km._stat_key = real
        self.assertEqual(fired, [1]); self.assertEqual(moved, 0, "the restored row stays: the ledger was read after the Undo")
        self.assertEqual([c["itemId"] for c in km._notice_cards(400, km._cleared_ids())], [iid], "the card is on the board")
        self.assertEqual((km._notice_archive_dir() / (SID + ".jsonl")).read_text(), "", "and not in the archive")

    def test_the_memo_is_bounded_by_bytes_as_a_fraction_of_memory_with_the_environment_override(self):
        saved = km._mem_total_bytes
        try:
            km._mem_total_bytes = lambda: 8 * 1024 ** 3
            os.environ.pop("ROMP_NOTICE_MEMO_BYTES", None)
            self.assertEqual(km._notice_memo_bound(), 32 * 1024 * 1024)
            os.environ["ROMP_NOTICE_MEMO_BYTES"] = "4096"; self.assertEqual(km._notice_memo_bound(), 4096)
            os.environ["ROMP_NOTICE_MEMO_BYTES"] = "0"; self.assertEqual(km._notice_memo_bound(), 32 * 1024 * 1024, "zero is not a bound")
            os.environ["ROMP_NOTICE_MEMO_BYTES"] = "lots"; self.assertEqual(km._notice_memo_bound(), 32 * 1024 * 1024)
        finally:
            km._mem_total_bytes = saved; os.environ.pop("ROMP_NOTICE_MEMO_BYTES", None)
        km.post_notice(SID, "k", "t", producer="cli", now=100)
        km._notice_rows(SID); km._notice_rows(SID)
        rep = km._notice_memo_report()
        self.assertEqual(set(rep), {"entries", "bytes", "bound", "hit", "miss", "evicted"})
        self.assertEqual((rep["entries"], rep["bound"]), (1, km.NOTICE_MEMO_BYTES)); self.assertGreaterEqual(rep["hit"], 1)
        saved_bound = km.NOTICE_MEMO_BYTES
        try:
            km.NOTICE_MEMO_BYTES = 1                    # a bound smaller than one file: the entry is shed after the read, not kept
            km.post_notice(SID2, "k", "t", producer="cli", now=100)
            km._notice_rows(SID2)
            self.assertEqual(km._notice_memo_report()["entries"], 0, "over the bound the largest goes first")
        finally:
            km.NOTICE_MEMO_BYTES = saved_bound


class TheDoors(unittest.TestCase):
    """POST /notice in /watch's shape, the backend's hook, and the boot wiring pin."""
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def _post(self, body, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = os.environ["ROMP_SERVE_TOKEN"]
        req = urllib.request.Request("http://127.0.0.1:%d/notice" % self.port, data=body if isinstance(body, bytes) else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode() or "{}"
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    def test_the_route_answers_in_the_watch_shape(self):
        self.assertNotEqual(self._post({"id": SID, "key": "k", "title": "t"}, token=False)[0], 200, "no token: refused")
        st, r = self._post(b"[]"); self.assertEqual(st, 400)
        st, r = self._post({"id": SID, "title": "t"}); self.assertEqual(st, 400); self.assertIn("key and title required", r["error"])
        st, r = self._post({"id": SID, "key": "bad key", "title": "t"}); self.assertEqual((st, r["ok"]), (200, False)); self.assertIn("the key must match", r["error"])
        st, r = self._post({"id": "99999999-2222-3333-4444-555555555555", "key": "k", "title": "t"}); self.assertEqual((st, r["ok"]), (200, False)); self.assertIn("no session answers", r["error"])
        st, r = self._post({"id": SID, "key": "k", "title": "t", "actions": [{"label": "x", "route": "/watch", "body": {}}]}); self.assertEqual(r["ok"], False); self.assertIn("not allowed", r["error"])
        st, r = self._post({"id": SID, "key": "k", "title": "t", "attachment": "/nowhere.png"}); self.assertEqual(r["ok"], False); self.assertIn("attachment refused", r["error"])
        st, r = self._post({"id": SID, "key": "figure", "title": "posted over http", "body": "the body", "producer": "http-test", "needsYou": True})
        self.assertEqual((st, r["ok"]), (200, True)); n = r["notice"]
        self.assertEqual((n["sid"], n["key"], n["rev"], n["needsYou"], n["producer"], n["title"]), (SID, "figure", 1, True, "http-test", "posted over http"))
        st, r = self._post({"id": SID, "key": "figure", "title": "again"}); self.assertEqual((r["ok"], r["notice"]["rev"], r["notice"]["producer"]), (True, 2, "http"))
        saved = (km.Sessions.live, km._live_names)
        try:                                            # a LIVE session's name resolves, as on every name-keyed route
            km.Sessions.live = staticmethod(lambda: {SID: {"state": "waiting"}}); km._live_names = lambda live: {"web": SID}
            st, r = self._post({"name": "web", "key": "byname", "title": "by name"}); self.assertEqual((r["ok"], r["notice"]["sid"]), (True, SID))
        finally:
            km.Sessions.live, km._live_names = saved
        st, r = self._post({"id": SID, "expire": "figure"}); self.assertEqual((st, r["ok"], r["notice"]["op"], r["notice"]["rev"]), (200, True, "expire", 2))
        st, r = self._post({"id": SID, "expire": "never"}); self.assertEqual(r["ok"], False)

    def test_the_route_posts_owner_less_only_when_id_and_name_are_absent_and_still_refuses_an_unknown_name(self):
        # a name no session answers to is refused, never guessed owner-less (the design's rule)
        st, r = self._post({"name": "nobody", "key": "k", "title": "t"})
        self.assertEqual((st, r.get("ok"), r.get("error")), (200, False, 'no session answers to "nobody"'))
        # the reserved word as a NAME through the route: refused like any name no session answers to (round two of PR 1831)
        st, r = self._post({"name": "notes", "key": "k", "title": "t"})
        self.assertEqual((st, r.get("ok"), r.get("error")), (200, False, 'no session answers to "notes"'))
        st, r = self._post({"id": "notes", "key": "k", "title": "t"})
        self.assertEqual((st, r.get("ok"), r.get("error")), (200, False, 'no session answers to "notes"'), "and as an id")
        st, r = self._post({"name": "notes", "expire": "k"})
        self.assertEqual((st, r.get("ok")), (200, False)); self.assertIn("no session answers", r.get("error", ""))
        self.assertFalse(km._notice_path("notes").exists(), "nothing reached the reserved home by name")
        # a whitespace-only id or name names nobody: refused, never guessed owner-less (round three of PR 1831, low)
        for who in ({"id": "   "}, {"name": " \t"}):
            st, r = self._post({**who, "key": "k", "title": "t"})
            self.assertEqual((st, r.get("ok")), (200, False), r); self.assertIn("no session answers to", r.get("error", ""))
        self.assertFalse(km._notice_path("notes").exists(), "nothing reached the reserved home")
        st, r = self._post({"key": "k1", "title": "Owner-less through the route", "body": "b"})
        self.assertEqual((st, r.get("ok")), (200, True), r); self.assertEqual(r["notice"]["sid"], "notes")
        self.assertEqual([c["itemId"] for c in km._notice_cards(500, set())], ["notice:notes:k1:1"])
        # a missing key or title is still a 400 with its words
        st, r = self._post({"title": "t"}); self.assertEqual(st, 400); self.assertIn("key and title required", r["error"])
        # the owner-less expire road: no id, no name, the key
        st, r = self._post({"expire": "k1"})
        self.assertEqual((st, r.get("ok")), (200, True), r); self.assertEqual([x["op"] for x in _rows("notes")], ["post", "expire"])
        self.assertEqual(km._notice_cards(500, set()), [], "retired")

    def test_the_backend_helper_resolves_the_hook_defensively_and_the_kernel_wires_it_at_boot(self):
        class Bare(sb.SdkBackend):                 # a stand-in class carrying no hook, as the backend's own tests bind
            def __init__(self): pass
        be = Bare()
        self.assertEqual(be.post_notice(SID, "k", "t", producer="dropped-sends"), (None, "no notice door is wired on this backend (the kernel wires on_notice at boot)"))
        calls = []
        Bare.on_notice = staticmethod(lambda sid, key, title, body="", **kw: (calls.append((sid, key, title, body, kw)) or ({"rev": 1}, None)))
        self.assertEqual(be.post_notice(SID, "k", "t", "b", producer="dropped-sends", needs_you=True), ({"rev": 1}, None))
        self.assertEqual(calls, [(SID, "k", "t", "b", {"producer": "dropped-sends", "needs_you": True})])
        Bare.on_notice = staticmethod(lambda *a, **k: 1 / 0)
        self.assertIn("could not be posted", be.post_notice(SID, "k", "t", producer="x")[1])
        self.assertIn("type(_sdk_backend).on_notice = staticmethod(post_notice)", KSRC, "the boot wiring, beside the model-fallback hook")
        self.assertIn('asks.extend(_notice_cards(now, cleared))', KSRC, "the feed attaches the family after the quarantine cards")
        self.assertIn('("notices", _notice_memo_report)', KSRC, "/perf reports the memo")
        self.assertIn('_nmoved = _compact_notices()', KSRC, "the retention pass runs beside the goal-store sweep")


if __name__ == "__main__":
    unittest.main()
