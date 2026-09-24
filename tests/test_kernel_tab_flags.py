"""The chat tab's right-click menu gained the timeline lane's per-session toggles (the user 2026-06-26): mute
from the feed (hideFromFeed) and isolate from the postal service (postalServiceOff). For the menu to show the
right state + action, build_session now carries both flags (mirroring build_timeline, legacy postalOff
fallback included), and the kernel handles a chat-side setSessionFlag the same way as the timeline's."""
import inspect
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
KPATH = os.path.join(BIN, "romp-kernel")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", KPATH)


class TabFlags(unittest.TestCase):
    def test_build_session_carries_the_feed_and_postal_flags(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"hideFromFeed": _session_flag(sid, "hideFromFeed")', src)
        self.assertIn('**_mail_off_fields(sid)', src,
                      "the EFFECTIVE state and its reason from one derivation (_mail_off_fields: canonical postalServiceOff with the legacy "
                      "postalOff fallback, and a comment thread's mail-off default, T356), like build_timeline's lane row")
        self.assertIn("_postal_isolation_flag(sid)", inspect.getsource(km._mail_off_why_k),
                      "the one reader resolves isolation through the shared flag helper")
        iso_src = inspect.getsource(km._postal_isolation_flag)
        self.assertIn('for flag in ("postalServiceOff", "postalOff")', inspect.getsource(km._postal_own_flag),
                      "the legacy fallback lives in the own-key helper, the session's own key winning either way")
        self.assertIn("own = _postal_own_flag(sid)", iso_src)
        self.assertIn('_session_flag(POSTAL_ALL_KEY, "postalServiceOff")', iso_src,
                      "a session with no key of its own takes the master default")

    def test_kernel_handles_a_chat_side_setSessionFlag(self):
        text = open(KPATH).read()
        self.assertIn('msg.get("type") == "setSessionFlag"', text)
        # the value is a checked boolean, never a bool() coercion: bool("false") is True (the string a
        # third-party client sent used to flip the flag ON)
        self.assertIn('value, ferr = _as_bool(msg.get("value"), "value")', text)
        self.assertIn("_set_session_flag(str(msg[\"id\"]), str(msg[\"flag\"]), value)", text)

    def test_set_session_flag_round_trips(self):
        sid = "11111111-2222-3333-4444-555555555555"
        try:
            km._set_session_flag(sid, "postalServiceOff", True)
            self.assertTrue(km._session_flag(sid, "postalServiceOff"))
            km._set_session_flag(sid, "postalServiceOff", False)
            self.assertFalse(km._session_flag(sid, "postalServiceOff"))
        finally:
            km._set_session_flag(sid, "postalServiceOff", False)


if __name__ == "__main__":
    unittest.main()
