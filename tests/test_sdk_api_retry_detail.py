"""The api_retry payload is read with the field names the CLI actually sends (the user 2026-07-29).

romp has rendered a rich retry banner for a while — "API retrying — attempt 7 of 10", a live countdown to
the next attempt, and the status + message behind the backoff. None of it ever appeared, because the reader
was written against GUESSED field names (number / max_retries / retry_delay_ms / error_status / retryAt) and
every one of them was wrong. `retry_info` came back all-None on every real storm, so the whole detail UI
rendered blank and a session in trouble showed a bare "API retrying" with no reason and no progress. The
user, watching one thread fail for twenty minutes while a fresh session connected fine, had nothing in the
UI to tell the two apart.

The names here are VERIFIED against the two surfaces the frame actually reaches us on, not guessed again:

  * the WIRE frame (SDKAPIRetryMessage, subtype api_retry) — snake_case, per the CLI's own embedded schema:
    retry_in_ms / is_network_down / is_ssl_error / rate_limit_type;
  * the TRANSCRIPT twin it is written from (system / subtype api_error) — camelCase, and the shape observed
    in real transcripts: retryAttempt / maxRetries / retryInMs / error{status,formatted,requestId,
    isNetworkDown,rateLimits}.

Both are accepted, along with the old guesses, because either may reach us and dropping the old names would
be a silent regression on any build still sending them. A payload matching NONE of them now writes a
one-line diagnostic naming the keys it did send — the blank-detail failure above is exactly the kind of
silent degradation CLAUDE.md forbids.

Fixtures are synthetic: invented request ids and placeholder uuids, never a recorded one.
"""
import io
import os
import unittest
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_retrydetail", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
REQ = "req_TESTREQ00000000000000"          # synthetic: never a recorded request id


class _Sys:
    """Stand-in for the SDK's SystemMessage — subtype + the raw data dict, which is all the branch reads."""

    def __init__(self, subtype, data):
        self.subtype, self.data = subtype, data


class _Other:
    """A type the branch must not confuse with SystemMessage (stands in for Assistant/Result)."""


class _Backend:
    """Only what _on_message touches: the poke, and the unconditional raw-message forward at its tail."""

    def __init__(self):
        self.forwarded = []

    def _deliver_rename_ping(self, s):
        return False   # settle hook (2026-08-25); no ping in these worlds

    def _poke(self):
        pass

    def _forward(self, sess, msg):
        self.forwarded.append(msg)


class _Sess(sb.SdkSession):
    """The retry state alone — SdkSession.__init__ builds a whole client/thread these don't need."""

    def __init__(self):
        self.sid = SID
        self.backend = _Backend()
        self.retrying = False
        self.retry_count = 0
        self.retry_info = None
        self.marks = []

    def _mark(self, state):
        self.marks.append(state)


def _feed(sess, data):
    """Drive one api_retry frame through the real handler and hand back the detail it produced."""
    sess._on_message(_Sys("api_retry", data), _Other, _Other, _Sys)
    return sess.retry_info


# A real 529 storm frame as the TRANSCRIPT records it (camelCase, error as a dict). Field-for-field the
# shape observed in live transcripts; the values are invented.
TRANSCRIPT_SHAPE = {
    "retryAttempt": 7, "maxRetries": 10, "retryInMs": 38888,
    "error": {"message": '529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
              "status": 529, "requestId": REQ, "formatted": "529 Overloaded",
              "connection": None, "isNetworkDown": False, "rateLimits": None},
    "slug": "test-slug", "source": "request_retry", "entrypoint": "sdk-py",
}

# The same failure as the WIRE frame names it (snake_case, flat).
WIRE_SHAPE = {
    "retry_attempt": 7, "max_retries": 10, "retry_in_ms": 38888,
    "status_code": 529, "request_id": REQ, "display_message": "529 Overloaded",
    "is_network_down": False, "is_ssl_error": False, "rate_limit_type": None,
}


class ReadsTheRealFieldNames(unittest.TestCase):
    def test_the_transcript_shape_fills_every_detail_field(self):
        info = _feed(_Sess(), TRANSCRIPT_SHAPE)
        self.assertEqual(info["attempt"], 7)
        self.assertEqual(info["max"], 10, "maxRetries — the guessed 'max_retries' never matched this shape")
        self.assertEqual(info["status"], 529)
        self.assertEqual(info["error"], "529 Overloaded",
                         "error.formatted is the human string; error.message is a raw JSON envelope")
        self.assertEqual(info["requestId"], REQ)
        self.assertIs(info["networkDown"], False)

    def test_the_wire_shape_fills_every_detail_field_too(self):
        info = _feed(_Sess(), WIRE_SHAPE)
        self.assertEqual((info["attempt"], info["max"], info["status"]), (7, 10, 529))
        self.assertEqual(info["error"], "529 Overloaded")
        self.assertEqual(info["requestId"], REQ)
        self.assertIs(info["networkDown"], False)

    def test_both_spellings_agree(self):
        """The same failure must describe itself identically whichever surface delivered it."""
        a, b = _feed(_Sess(), TRANSCRIPT_SHAPE), _feed(_Sess(), WIRE_SHAPE)
        for k in ("attempt", "max", "status", "error", "requestId", "networkDown"):
            self.assertEqual(a[k], b[k], "%s differs between the wire and transcript spellings" % k)

    def test_the_countdown_epoch_comes_from_the_backoff_delay(self):
        """retryInMs → an absolute epoch, which is what the live countdown ticks against."""
        import time
        before = time.time()
        info = _feed(_Sess(), TRANSCRIPT_SHAPE)
        self.assertIsNotNone(info["retryAt"], "no epoch → the chat's countdown never renders")
        self.assertGreaterEqual(info["retryAt"], before + 38.0)
        self.assertLessEqual(info["retryAt"], time.time() + 39.5)

    def test_an_absolute_retry_at_in_millis_is_normalised_to_seconds(self):
        info = _feed(_Sess(), {"retryAt": 2000000000000, "maxRetries": 10})
        self.assertAlmostEqual(info["retryAt"], 2000000000.0, places=3)

    def test_the_old_guessed_names_still_work(self):
        """Dropping them would silently regress any build that does send them."""
        info = _feed(_Sess(), {"number": 3, "max_retries": 8, "error_status": 500,
                               "retry_delay_ms": 2000, "message": "500 Server Error"})
        self.assertEqual((info["attempt"], info["max"], info["status"]), (3, 8, 500))
        self.assertEqual(info["error"], "500 Server Error")
        self.assertIsNotNone(info["retryAt"])

    def test_a_network_drop_is_distinguishable_from_an_overloaded_api(self):
        """Opposite problems, same red card — the flag is the only thing that separates them."""
        info = _feed(_Sess(), {"is_network_down": True, "status_code": 0, "retry_attempt": 1})
        self.assertIs(info["networkDown"], True)

    def test_a_quota_429_carries_which_limit_it_hit(self):
        info = _feed(_Sess(), {"status_code": 429, "rate_limit_type": "output_tokens", "retry_attempt": 2})
        self.assertEqual(info["rateLimitType"], "output_tokens")

    def test_the_attempt_count_falls_back_to_our_own_tally(self):
        """A payload with no attempt number still counts: the storm's size is romp's own observation."""
        s = _Sess()
        _feed(s, {"status_code": 529})
        info = _feed(s, {"status_code": 529})
        self.assertEqual(info["attempt"], 2)

    def test_the_frame_marks_the_session_retrying(self):
        s = _Sess()
        _feed(s, TRANSCRIPT_SHAPE)
        self.assertTrue(s.retrying)
        self.assertEqual(s.marks, ["retrying"])


class AnUnknownShapeIsLoud(unittest.TestCase):
    def setUp(self):
        sb.SdkSession._retry_shape_warned = False

    def tearDown(self):
        sb.SdkSession._retry_shape_warned = False

    def test_a_payload_we_cannot_read_says_so_once(self):
        """The blank-detail bug in reverse: if the names change again we hear about it immediately."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            _feed(_Sess(), {"totallyRenamedField": 1, "somethingElse": "x"})
        out = buf.getvalue()
        self.assertIn("api_retry payload", out)
        self.assertIn("totallyRenamedField", out, "the diagnostic must name the keys it actually got")

    def test_it_does_not_repeat_for_every_attempt_in_a_storm(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            for _ in range(5):
                _feed(_Sess(), {"totallyRenamedField": 1})
        self.assertEqual(buf.getvalue().count("api_retry payload"), 1)

    def test_a_readable_payload_stays_quiet(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _feed(_Sess(), TRANSCRIPT_SHAPE)
        self.assertEqual(buf.getvalue(), "")

    def test_an_empty_payload_is_not_treated_as_a_rename(self):
        """No data at all is a frame carrying nothing, not a schema we failed to parse."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            _feed(_Sess(), {})
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
