#!/usr/bin/env python3
"""_hydrate_postal must only hydrate mail this session actually received.

It scanned the output of EVERY tool for `romp-msg-id` markers and looked each id up in a BOX-WIDE
index, with no check of who the message was addressed to — then REPLACED the event when every id
resolved. So text the agent merely READ (a file, a fetched page, a grep hit, its own echo of mail)
could make a Bash/Read/WebFetch row vanish from the chat and come back as a postal card wearing
another session's name and colour; and an id belonging to a message between two OTHER sessions
rendered that message's body in a chat that was never a recipient.

Two scopes now: only the MAIL READERS' output is scanned (check_inbox, `romp mail inbox|recv|peek`,
the drain) alongside user text — those are the only places format_inbox / format_push output can
legitimately land — and a record whose to_id names someone else falls through to the existing loud
unresolved path instead of rendering.

A genuine multi-message drain must still hydrate EVERY id, so that stays covered here too.

SYNTHETIC fixtures only: invented sessions (notes-api's web/api/tests), placeholder UUIDs, TESTHOST.
"""
import io
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_hscope", os.path.join(BIN, "romp-kernel"))
pm = load_source("romp_postal_hscope", os.path.join(BIN, "romp-postal-service"))   # the bus's own banner formatter

ME = "11111111-2222-3333-4444-555555555555"       # the session whose chat is being built ('web')
PEER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"     # the sender ('api')
OTHER = "99999999-8888-7777-6666-555555555555"    # a third session ('tests') — never this chat

M1 = "1700000000.11111_22222.TESTHOST"
M2 = "1700000001.33333_44444.TESTHOST"
M3 = "1700000002.55555_66666.TESTHOST"

MARKER = "<!-- romp-msg-id: %s -->"


def row(mid, to, body="the changelog is the last thing left on the deploy"):
    return {"id": mid, "from": "api", "fromId": PEER, "fromHost": "", "toId": to,
            "body": body, "kind": "coordinate", "t": 1700000000, "park": False}


def tool(name, output, **kw):
    ev = {"kind": "tool", "name": name, "input": "", "output": output,
          "isError": False, "uuid": "cccccccc-dddd-eeee-ffff-000000000000", "ts": "x"}
    ev.update(kw)
    return ev


def bash(command, output):
    return tool("Bash", output, input=json.dumps({"command": command, "description": "d"}))


def user(text):
    return {"kind": "user", "md": text, "uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "ts": "x", "human": False}


INBOX_TOOL = "mcp__romp-postal-service__check_inbox"


def banner(mid, frm="api", body="the deploy is handed over"):
    """A bus banner exactly as the bus pushes it (format_push), one message."""
    return pm.format_push([{"from": frm, "from_id": PEER, "body": body, "id": mid, "date": ""}])


def tagged(body, label="watch"):
    """A tagged machine line as `romp send --tag` builds it."""
    return body + "\n\n<!-- romp-tag: " + label + " -->"


class HydrateScope(unittest.TestCase):
    def setUp(self):
        km._POSTAL_UNRESOLVED_RESET()                   # T234: the warning is once per (session, id) per
        #                                                kernel life - each test is its own life, so a pin
        #                                                that expects the loud line never inherits an
        #                                                earlier test's sighting of the same pair
    """Only a mail reader's output (and user text) is scanned for message ids."""

    def _run(self, events, index, sid=ME):
        err = io.StringIO()
        saved_sum, saved_err = km._msg_summaries, km.sys.stderr
        km._msg_summaries, km.sys.stderr = (lambda: {}), err
        try:
            return km._hydrate_postal(events, index, sid), err.getvalue()
        finally:
            km._msg_summaries, km.sys.stderr = saved_sum, saved_err

    def test_a_file_read_that_mentions_a_message_id_is_not_hydrated(self):
        # The core bug: a Read of a file that happens to carry the marker replaced the tool row with a
        # postal card. The row must survive intact — and this is not a postal event, so nothing is warned.
        ev = tool("Read", "notes.md line 3: %s\nsee the handoff above" % (MARKER % M1))
        out, warned = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev], "a Read row is untouched — no card, no mid stamped on it")
        self.assertEqual(warned, "", "and nothing is reported: it was never postal traffic")

    def test_a_bash_row_echoing_a_received_id_keeps_its_output(self):
        # An agent's own echo of mail it received (grep, cat, a script that prints the marker back)
        # used to swap the Bash row out for a duplicate card of a message already shown once.
        ev = bash("grep -n romp-msg-id handoff.md", "handoff.md:7:%s" % (MARKER % M1))
        out, _ = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev], "the command and its output stay in the chat")

    def test_a_web_fetch_carrying_a_marker_is_not_hydrated(self):
        ev = tool("WebFetch", "the page said: %s" % (MARKER % M1))
        out, _ = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev])

    def test_a_command_that_merely_mentions_the_subcommand_is_not_a_mail_read(self):
        # The matcher is anchored at a command position, so quoting the subcommand as an ARGUMENT
        # doesn't turn an ordinary grep into a mailbox reader.
        ev = bash('grep -rn "romp mail inbox" docs/', "docs/reference.md:86:%s" % (MARKER % M1))
        out, _ = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev], "'romp mail inbox' inside a quoted argument is not a mail read")

    def test_the_check_inbox_tool_output_still_hydrates(self):
        out, warned = self._run([tool(INBOX_TOOL, "\U0001F4EC New message(s):\nhi\n" + MARKER % M1)],
                                {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        self.assertEqual((out[0]["direction"], out[0]["peer"], out[0]["mid"]), ("in", "api", M1))
        self.assertEqual(warned, "")

    def test_the_mail_reading_cli_subcommands_still_hydrate(self):
        for cmd in ("romp mail inbox", "romp mail recv", "romp mail peek", "romp --mail inbox",
                    "romp-postal-service inbox", "romp mail drain --id " + ME,
                    "cd /tmp/notes-api && romp mail inbox"):
            with self.subTest(cmd=cmd):
                out, _ = self._run([bash(cmd, "from api:\n" + MARKER % M1)], {M1: row(M1, ME)})
                self.assertEqual([e["kind"] for e in out], ["postal-service"], cmd)

    def test_delivered_mail_as_user_text_still_hydrates(self):
        # How mail actually reaches a session: the Stop-hook drain's block reason and the kernel's
        # pane/socket push both land as user records. That path is unchanged.
        out, _ = self._run([user("####\n\U0001F4EC from api\n####\nbody\n" + MARKER % M1)],
                           {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])

    def test_the_outgoing_card_paths_are_untouched(self):
        # send_message / `romp mail send` are matched before the scan and never depended on it.
        mcp = tool("mcp__romp-postal-service__send_message", "Delivered to 'api'.",
                   input=json.dumps({"to": "api", "body": "taking the deploy"}))
        cli = tool("Bash", "[romp mail] delivered to api",
                   input='romp mail send api "taking the deploy"')
        out, _ = self._run([mcp, cli], {})
        self.assertEqual([(e["kind"], e["direction"]) for e in out],
                         [("postal-service", "out")] * 2)


class HydrateRecipient(unittest.TestCase):
    def setUp(self):
        km._POSTAL_UNRESOLVED_RESET()                   # T234: the warning is once per (session, id) per
        #                                                kernel life - each test is its own life, so a pin
        #                                                that expects the loud line never inherits an
        #                                                earlier test's sighting of the same pair
    """Only mail addressed to THIS session is rendered in its chat."""

    def _run(self, events, index, sid=ME):
        err = io.StringIO()
        saved_sum, saved_err = km._msg_summaries, km.sys.stderr
        km._msg_summaries, km.sys.stderr = (lambda: {}), err
        try:
            return km._hydrate_postal(events, index, sid), err.getvalue()
        finally:
            km._msg_summaries, km.sys.stderr = saved_sum, saved_err

    def test_a_message_addressed_to_another_session_is_not_rendered(self):
        # A peer's message body (delivered as user text) quoting an id from a conversation between two
        # OTHER sessions used to render THAT conversation's body here, wearing its sender's identity.
        secret = row(M2, OTHER, body="the staging credentials rotate on friday")
        out, warned = self._run([user("look at this: " + MARKER % M2)], {M2: secret})
        self.assertEqual([e["kind"] for e in out], ["user"], "no card for someone else's mail")
        self.assertNotIn("the staging credentials rotate on friday",
                         json.dumps(out[0]), "and its body never reaches this chat")
        self.assertEqual(out[0]["mids"], [M2], "the turn still carries the id it mentioned (deep-link)")
        self.assertIn("unresolved", warned, "it goes down the existing loud path, not silence")
        self.assertIn(M2, warned)

    def test_the_same_id_addressed_to_us_does_render(self):
        out, warned = self._run([user("mail: " + MARKER % M1)], {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        self.assertEqual(warned, "")

    def test_a_check_inbox_output_naming_someone_elses_id_is_not_rendered(self):
        out, warned = self._run([tool(INBOX_TOOL, "spoofed: " + MARKER % M2)], {M2: row(M2, OTHER)})
        self.assertEqual([e["kind"] for e in out], ["tool"], "even a real mail reader is scoped")
        self.assertIn("unresolved", warned)

    def test_a_genuine_multi_message_drain_hydrates_every_id(self):
        # One drain legitimately carries several messages — all of them must still become cards.
        ev = user("\n".join(["\U0001F4EC New message(s):"]
                            + [MARKER % m for m in (M1, M2, M3)]))
        idx = {M1: row(M1, ME, "one"), M2: row(M2, ME, "two"), M3: row(M3, ME, "three")}
        out, warned = self._run([ev], idx)
        self.assertEqual([e["kind"] for e in out], ["postal-service"] * 3)
        self.assertEqual([e["mid"] for e in out], [M1, M2, M3])
        self.assertEqual([e["body"] for e in out], ["one", "two", "three"])
        self.assertEqual(warned, "")

    def test_a_drain_with_one_foreign_id_stays_all_or_nothing(self):
        ev = user("\n".join(MARKER % m for m in (M1, M2)))
        out, warned = self._run([ev], {M1: row(M1, ME), M2: row(M2, OTHER)})
        self.assertEqual([e["kind"] for e in out], ["user"], "a partial run never half-renders")
        self.assertEqual(out[0]["mids"], [M1, M2])
        self.assertIn("1 of 2", warned)

    def test_a_log_row_with_no_recipient_still_renders(self):
        # Safety net: pre-schema rows (and any writer that omits to_id) must not lose their cards —
        # the check scopes what we render, it is not a reason to drop real mail.
        out, warned = self._run([user(MARKER % M1)], {M1: row(M1, "")})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        self.assertEqual(warned, "")

    def test_a_caller_that_passes_no_sid_keeps_the_old_behavior(self):
        out, _ = self._run([user(MARKER % M2)], {M2: row(M2, OTHER)}, sid=None)
        self.assertEqual([e["kind"] for e in out], ["postal-service"],
                         "no sid to compare against → no recipient check (direct callers)")

    def test_build_session_passes_its_sid_in(self):
        # Without the wiring the recipient check is dead code in the only caller that matters: a spy on the
        # hydrator records the sid every hydration build_session makes carries (the tail pass at least).
        seen = []
        real = km._hydrate_postal

        def spy(events, index, sid=None, captions=None):
            seen.append(sid)
            return real(events, index, sid, captions=captions)
        km._hydrate_postal = spy
        self.addCleanup(setattr, km, "_hydrate_postal", real)
        saved = (km._sessions, km._live_map, km._msg_summaries)
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tx = os.path.join(td.name, ME + ".jsonl")
        with open(tx, "w") as f:
            f.write(json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-09-01T10:00:00.000Z",
                                "message": {"role": "user", "content": "hello"}}) + "\n")
        km._sessions = lambda now, **kw: [{"sid": ME, "name": "web", "anchor": ME, "path": tx, "mtime": 1}]
        km._live_map = lambda: {}
        km._msg_summaries = lambda: {}
        try:
            km.build_session(ME, 1700000000, {})
        finally:
            km._sessions, km._live_map, km._msg_summaries = saved
        self.assertTrue(seen, "build_session hydrated at least once")
        self.assertEqual(set(seen), {ME}, "every hydration carried the session's own sid")


class HydrateMixedJoinedRecord(unittest.TestCase):
    """A busy session is fed queued peer mail and tagged machine lines as ONE text (the SDK backend's
    _join_queued_locked), so the CLI writes one user record holding both. Its mail becomes cards as before, and its
    tagged lines must stay visible beside them: replacing the whole record with the cards, as a mail-only record is,
    hid every tagged line in it from the chat."""

    def setUp(self):
        km._POSTAL_UNRESOLVED_RESET()

    def _run(self, events, index, sid=ME):
        err = io.StringIO()
        saved_sum, saved_err = km._msg_summaries, km.sys.stderr
        km._msg_summaries, km.sys.stderr = (lambda: {}), err
        try:
            return km._hydrate_postal(events, index, sid), err.getvalue()
        finally:
            km._msg_summaries, km.sys.stderr = saved_sum, saved_err

    def test_a_mixed_joined_record_keeps_its_tagged_lines_and_turns_its_mail_into_cards(self):
        t1, t2 = tagged("lint passed on TESTHOST"), tagged("tests passed on TESTHOST", label="ci")
        ev = dict(user("\n\n".join([t1, banner(M1), t2, banner(M2, body="the changelog is next")])),
                  qids=["echo:11111111-2222-3333-4444-000000000001", "echo:11111111-2222-3333-4444-000000000002"],
                  tag="watch")
        out, warned = self._run([ev], {M1: row(M1, ME, "the deploy is handed over"), M2: row(M2, ME, "the changelog is next")})
        self.assertEqual([e["kind"] for e in out], ["user", "postal-service", "postal-service"],
                         "the tagged lines first, where they sat, then a card per message; before, the cards alone")
        self.assertEqual(out[0]["md"], t1 + "\n\n" + t2, "every tagged line stays visible, in order, and no mail text")
        self.assertEqual((out[0]["uuid"], out[0]["qids"], out[0]["tag"]), (ev["uuid"], ev["qids"], "watch"),
                         "the remaining entry is the record's own: its uuid and its sends' ids ride with it")
        self.assertNotIn("mids", out[0])
        self.assertEqual([e["mid"] for e in out[1:]], [M1, M2])
        self.assertEqual([e["body"] for e in out[1:]], ["the deploy is handed over", "the changelog is next"])
        self.assertEqual(warned, "")

    def test_a_record_that_opens_with_mail_shows_its_card_before_the_tagged_line(self):
        t1 = tagged("coverage 91 percent")
        out, _ = self._run([user(banner(M1) + "\n\n" + t1)], {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service", "user"])
        self.assertEqual(out[1]["md"], t1)

    def test_a_mail_only_joined_record_renders_exactly_as_today(self):
        ev = user(banner(M1) + "\n\n" + banner(M2))
        out, _ = self._run([ev], {M1: row(M1, ME), M2: row(M2, ME)})
        self.assertEqual([(e["kind"], e["mid"]) for e in out], [("postal-service", M1), ("postal-service", M2)])

    def test_a_tagged_only_record_is_untouched(self):
        ev = user(tagged("build 7 green") + "\n\n" + tagged("build 8 green"))
        out, _ = self._run([ev], {M1: row(M1, ME)})
        self.assertEqual(out, [ev])

    def test_mail_beside_untagged_text_renders_exactly_as_today(self):
        # only tagged lines are kept: any other text around a banner keeps today's cards-only rendering
        out, _ = self._run([user("mail for you:\n\n" + banner(M1))], {M1: row(M1, ME)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])

    def test_a_mixed_record_with_unresolved_mail_passes_through_whole(self):
        ev = user(tagged("deploy queued") + "\n\n" + banner(M2))
        out, warned = self._run([ev], {M2: row(M2, OTHER)})
        self.assertEqual([e["kind"] for e in out], ["user"], "all-or-nothing, as before")
        self.assertEqual(out[0]["md"], ev["md"])
        self.assertIn("unresolved", warned)


class HydrateIndependence(unittest.TestCase):
    """hydrate(A + B) == hydrate(A) + hydrate(B). The chat fold's commit hydrates only the raw postal events
    new since its seal and prepends the sealed cards (2026-09-09); that equals a hydration of the whole list
    only while hydration carries no state from one event to the next. Pinned over the four event shapes: an
    outgoing send, a resolved incoming id, an unresolved id, a plain Bash row, and a mixed joined record (a tagged line beside a banner), split at every
    position."""

    M_OUT = "1700000009.77777_88888.TESTHOST"

    def setUp(self):
        km._POSTAL_UNRESOLVED_RESET()

    def _events(self):
        return [tool("mcp__romp-postal-service__send_message", "Delivered to 'api'.",
                     input=json.dumps({"to": "api", "body": "taking the deploy"}), ts="2026-09-07T10:00:00.000Z"),
                user("mail: " + MARKER % M1),
                user("look at this: " + MARKER % M3),        # no row: passes through, ids kept
                bash("uv run pytest -q", "ok"),
                user(tagged("lint passed") + "\n\n" + banner(M1))]   # a mixed joined record: its line, then its card

    def _index(self):
        out_row = dict(row(self.M_OUT, PEER, body="taking the deploy"), fromId=ME)   # the send's own log row
        return {M1: row(M1, ME), self.M_OUT: out_row}

    def test_every_split_equals_the_whole(self):
        evs, idx = self._events(), self._index()
        caps = lambda: {M1: "api: the deploy is handed over", self.M_OUT: "web: taking the deploy"}
        err, saved = io.StringIO(), km.sys.stderr
        km.sys.stderr = err
        try:
            whole = km._hydrate_postal(list(evs), idx, ME, captions=caps)
            for k in range(len(evs) + 1):
                split = (km._hydrate_postal(evs[:k], idx, ME, captions=caps)
                         + km._hydrate_postal(evs[k:], idx, ME, captions=caps))
                self.assertEqual(split, whole, "split at %d" % k)
        finally:
            km.sys.stderr = saved
        self.assertEqual([(e["kind"], e.get("direction")) for e in whole],
                         [("postal-service", "out"), ("postal-service", "in"), ("user", None), ("tool", None),
                          ("user", None), ("postal-service", "in")])
        self.assertEqual(whole[4]["md"], tagged("lint passed"))
        self.assertEqual(whole[0].get("summary"), "web: taking the deploy", "the send joined its row and caption")
        self.assertEqual(whole[1]["summary"], "api: the deploy is handed over")
        self.assertEqual(whole[2]["mids"], [M3])

    def test_the_captions_getter_stands_in_for_msg_summaries_and_is_read_once(self):
        calls = []

        def caps():
            calls.append(1)
            return {M1: "cap"}
        saved = km._msg_summaries
        km._msg_summaries = lambda: self.fail("_msg_summaries must not be read when a getter is given")
        try:
            out = km._hydrate_postal([user(MARKER % M1), user(MARKER % M1)], {M1: row(M1, ME)}, ME, captions=caps)
        finally:
            km._msg_summaries = saved
        self.assertEqual([e["summary"] for e in out], ["cap", "cap"])
        self.assertEqual(len(calls), 1, "fetched once per hydration, on first need")
        self.assertEqual(km._hydrate_postal([bash("ls", "ok")], {}, ME, captions=lambda: self.fail("no card, no map")),
                         [bash("ls", "ok")])


if __name__ == "__main__":
    unittest.main()
