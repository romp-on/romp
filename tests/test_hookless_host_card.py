#!/usr/bin/env python3
"""A hookless host must FAIL LOUDLY (the authoritative-sources rule): a live tmux session with a
conversation but no @claude-state ever written has NO state source — its chip can never say
Awaiting, so a session stopped on a question reads calm everywhere, silently. _hookless_tmux_card
surfaces that as ONE dismissible needs-you card naming the host and the dark sessions (the user
2026-08-13, whose fresh remote host skipped install.sh and hit exactly this). Synthetic fixtures
only: TESTHOST, placeholder sids."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["ROMP_HOST_NAME"] = "TESTHOST"
km = SourceFileLoader("romp_kernel_hookless", os.path.join(BIN, "romp-kernel")).load_module()

NOW = 1781200000
SID_WEB = "11111111-2222-3333-4444-555555555555"
SID_API = "66666666-7777-8888-9999-aaaaaaaaaaaa"


def _alive(*rows):
    return [{"sid": sid, "name": name} for sid, name in rows]


class HooklessHostCard(unittest.TestCase):
    def setUp(self):
        self._orig_hist = km._session_has_history
        km._session_has_history = lambda sid: True

    def tearDown(self):
        km._session_has_history = self._orig_hist

    def test_dark_tmux_session_yields_one_needs_you_card(self):
        tmux = {SID_WEB: {"state": "", "backend": "tmux"}}
        cards = km._hookless_tmux_card(NOW, tmux, _alive((SID_WEB, "web")), set())
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual(c["itemId"], "hooks:TESTHOST")
        self.assertEqual(c["column"], "needs_input")
        self.assertIsNone(c["blocked"], "no ⏸ chip — feed.ts's chip copy speaks permission/picker only")
        self.assertEqual(c["sid"], "", "host-scoped, not a session card")
        self.assertIn("TESTHOST", c["blockSummary"])
        self.assertIn("web", c["blockSummary"])
        self.assertIn("install.sh", c["blockSummary"])

    def test_any_recorded_state_means_hooks_fired(self):
        for st in ("working", "waiting", "idle", "permission", "picker", "compacting"):
            tmux = {SID_WEB: {"state": st, "backend": "tmux"}}
            self.assertEqual(km._hookless_tmux_card(NOW, tmux, _alive((SID_WEB, "web")), set()), [],
                             "state %r is evidence the hooks run" % st)

    def test_only_tmux_backend_counts(self):
        # SDK sessions publish state through their own backend, no hooks involved
        tmux = {SID_WEB: {"state": "", "backend": "sdk"}}
        self.assertEqual(km._hookless_tmux_card(NOW, tmux, _alive((SID_WEB, "web")), set()), [])

    def test_a_fresh_shell_with_no_conversation_is_not_evidence(self):
        # pre-first-prompt there is no guarantee any hook has had cause to fire yet
        km._session_has_history = lambda sid: False
        tmux = {SID_WEB: {"state": "", "backend": "tmux"}}
        self.assertEqual(km._hookless_tmux_card(NOW, tmux, _alive((SID_WEB, "web")), set()), [])

    def test_clear_dismisses_for_good(self):
        tmux = {SID_WEB: {"state": "", "backend": "tmux"}}
        self.assertEqual(
            km._hookless_tmux_card(NOW, tmux, _alive((SID_WEB, "web")), {"hooks:TESTHOST"}), [])

    def test_many_dark_sessions_fold_into_one_card(self):
        tmux, rows = {}, []
        for i in range(8):
            sid = "%08d-1111-2222-3333-444444444444" % i
            tmux[sid] = {"state": "", "backend": "tmux"}
            rows.append((sid, "sess%d" % i))
        cards = km._hookless_tmux_card(NOW, tmux, _alive(*rows), set())
        self.assertEqual(len(cards), 1)
        self.assertIn("+2 more", cards[0]["blockSummary"])

    def test_build_feed_wires_the_card_in(self):
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertIn("_hookless_tmux_card(now, tmux, alive, cleared)", src)


if __name__ == "__main__":
    unittest.main()
