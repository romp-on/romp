#!/usr/bin/env python3
"""Anonymous mail is refused at the door, and legacy "unknown" mail never renders the mint.

A sender that failed to resolve its own identity used to mail anyway: the bus minted a message
literally "from unknown" (empty from_id), and the recipient's injected banner printed that word
raw — a canned-sounding body over a ghost sender, met by the user on their laptop 2026-08-18
("a greeting from an unknown thread"; the 2026-07-27 clear-fork minted the same ghost). Three
seams now hold, all pinned here:
  - the bus /send handler REFUSES a from_id-less send with an error naming the sender's own
    identity resolution as the breakage (fail loudly, 2026-07-03);
  - the MCP/CLI sender paths say the same thing BEFORE posting, with the actionable half;
  - the banner formatters never print the literal "unknown"/"?" for mail already on disk or
    arriving from an older peer bus — "an unidentified session" is what is true.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ps = load_source("romp_postal_sender_identity",
                      os.path.join(BIN, "romp-postal-service"))


class FromDisplay(unittest.TestCase):
    def test_minted_ghosts_render_as_unidentified(self):
        for ghost in ("unknown", "Unknown", "?", "", None):
            self.assertEqual(ps._from_disp({"from": ghost}), "an unidentified session")

    def test_real_names_pass_through(self):
        self.assertEqual(ps._from_disp({"from": "web"}), "web")
        self.assertEqual(ps._from_disp({"from": "TESTHOST:api"}), "TESTHOST:api")

    def test_push_banner_uses_the_guard(self):
        out = ps.format_push([{"from": "unknown", "date": "", "body": "How's it going?",
                               "id": "m1", "kind": ""}])
        self.assertIn("from an unidentified session", out)
        self.assertNotIn("from unknown", out)

    def test_inbox_banner_uses_the_guard(self):
        out = ps.format_inbox([{"from": "?", "date": "", "body": "checking in",
                                "id": "m2", "kind": ""}], me_id="abc")
        self.assertIn("from an unidentified session", out)
        self.assertNotIn("from ?", out)


class SendRefusal(unittest.TestCase):
    def test_handler_source_refuses_fromless_sends(self):
        # the handler is route-bound (an HTTP class method); pin the guard at source the way
        # the UI pins render branches — the bats drive the live route
        src = open(os.path.join(BIN, "romp-postal-service")).read()
        self.assertIn('if not frm_id:', src)
        self.assertIn("sender identity required", src)

    def test_mcp_and_cli_guards_precede_the_post(self):
        src = open(os.path.join(BIN, "romp-postal-service")).read()
        self.assertIn("identity did not resolve", src, "the MCP surface guards (always in-session)")
        self.assertIn("pass --from <label>", src,
                      "the CLI surface guards AND names the non-session door (2026-08-19: the "
                      "refusal broke launchd scripts that had been mailing as 'unknown')")
        self.assertIn('SCRIPT_SENDER_PREFIX = "ext:"', src)
        self.assertIn('mid = frm_label, SCRIPT_SENDER_PREFIX + frm_label', src,
                      "--from mails placeable under a stable synthetic id, never anonymously")


if __name__ == "__main__":
    unittest.main()
