#!/usr/bin/env python3
"""list_agents disambiguation (the user 2026-08-24): every row carries its short stable id, and a
remote row its host prefix — so a duplicate-name refusal ("candidates listed as host:name") can be
matched against the list instead of guessed at, and the id on the row is enough to ADDRESS by:
resolve_recipient matches an unambiguous id prefix of 8+ hex-ish chars (an exact NAME always wins
first; two prefix hits refuse like any duplicated name; your own id still self-refuses). Synthetic
notes-api world only."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_POSTAL_HOST"] = "TESTHOST"
WEB = "aaaaaaaa-1111-2222-3333-444444444444"
API = "abcdefab-5555-6666-7777-888888888888"
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([
    {"id": WEB, "name": "web", "dir": "/tmp/notes-api", "state": "working", "working": ""},
    {"id": API, "name": "api", "dir": "/tmp/notes-api", "state": "waiting", "working": ""},
]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = SourceFileLoader("romp_postal_agents_render", os.path.join(BIN, "romp-postal-service")).load_module()


class AgentsRowsDisambiguate(unittest.TestCase):
    def test_every_row_carries_its_short_stable_id(self):
        out = ps.format_agents([{"id": WEB, "name": "web", "state": "working"}], "web", WEB)
        self.assertIn("web (you) · aaaaaaaa", out, "the (you) row keeps its mark and gains the id")
        out = ps.format_agents([{"id": API, "name": "api", "state": "waiting"}], "web", WEB)
        self.assertIn("api · abcdefab", out)

    def test_a_remote_row_wears_its_host_prefix_and_its_own_uuid_part(self):
        row = {"id": "TESTHOST-B:%s" % API, "name": "api", "remote": True}
        out = ps.format_agents([row], "web", WEB)
        self.assertIn("TESTHOST-B:api [remote] · abcdefab", out,
                      "host:name matches the duplicate-name refusal's candidate form; the id is the "
                      "uuid part, the piece that addresses")

    def test_a_thread_row_keeps_its_minor_player_tag(self):
        rows = [{"id": WEB, "name": "web", "state": "working"},
                {"id": "bbbbbbbb-9999-0000-1111-222222222222", "name": "web-comment-1",
                 "thread": True, "parent": WEB}]
        out = ps.format_agents(rows, "api", API)
        self.assertIn("web-comment-1 (thread of web) · bbbbbbbb", out)


class ShortIdAddresses(unittest.TestCase):
    def setUp(self):
        # the sessions-file seam is read PER CALL, so a batch run's last-imported module owns the
        # env — bind OUR file for the duration of each test (the seam's own hermetic idiom)
        self._saved = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = _SESS

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ROMP_SESSIONS_FILE", None)
        else:
            os.environ["ROMP_SESSIONS_FILE"] = self._saved

    def test_a_unique_id_prefix_resolves_direct(self):
        res = ps.resolve_recipient("abcdefab", "some-other-sender")
        self.assertEqual(res["kind"], "direct")
        self.assertEqual(res["agent"]["id"], API, "the row's 8-char form is enough to address")

    def test_an_exact_name_always_beats_a_hex_shaped_prefix(self):
        # a session NAMED like a hex prefix must stay addressable by that name
        sess = json.loads(Path(_SESS).read_text())
        sess.append({"id": "cccccccc-1234-5678-9abc-def012345678", "name": "abcdefab",
                     "dir": "", "state": "waiting", "working": ""})
        Path(_SESS).write_text(json.dumps(sess))
        try:
            res = ps.resolve_recipient("abcdefab", "some-other-sender")
            self.assertEqual(res["kind"], "direct")
            self.assertEqual(res["agent"]["name"], "abcdefab", "name-exact wins before any id math")
        finally:
            Path(_SESS).write_text(json.dumps(sess[:-1]))

    def test_an_ambiguous_prefix_refuses_like_a_duplicated_name(self):
        sess = json.loads(Path(_SESS).read_text())
        sess.append({"id": "abcdefab-0000-1111-2222-333333333333", "name": "tests",
                     "dir": "", "state": "waiting", "working": ""})
        Path(_SESS).write_text(json.dumps(sess))
        try:
            res = ps.resolve_recipient("abcdefab", "some-other-sender")
            self.assertEqual(res["kind"], "error", "two prefix hits are an ambiguity, never a pick")
        finally:
            Path(_SESS).write_text(json.dumps(sess[:-1]))

    def test_too_short_a_prefix_never_id_matches(self):
        res = ps.resolve_recipient("abcdef", "some-other-sender")
        self.assertEqual(res["kind"], "error", "under 8 chars stays a name miss — luck can't address")

    def test_your_own_id_prefix_still_self_refuses(self):
        res = ps.resolve_recipient("abcdefab", API)
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["status"], 409, "the self-send loopback guard keys on identity, not spelling")


if __name__ == "__main__":
    unittest.main()
