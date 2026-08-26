#!/usr/bin/env python3
"""A classifier REFUSAL is deterministic on the same input — never auto-retried (the user 2026-08-15).

The incident, all fixtures SYNTHETIC: the `api` session sent a prompt the model's safeguards refused.
The CLI wrote an assistant API-error record (isApiErrorMessage:true, error:"invalid_request") whose
text reads "API Error: <Model>'s safeguards flagged this message (https://www.anthropic.com/legal/aup)…",
plus, a few records later, a structured system record (subtype model_refusal_no_fallback) carrying the
refusal category and explanation. romp classified the error as TRANSIENT, so the auto-retry re-sent the
same prompt — which manufactured the same refusal, verbatim, TWELVE times in ~6 minutes: each refusal
wrote a NEW error record (new uuid → new episode), so the once-per-episode gate never terminated, and
in a fallback configuration each attempt would also manufacture another model downgrade.

Detection is event-based FIRST with the CLI's own wording as a co-equal signature, because neither
alone covers the evidence:
- the system refusal record (subtype model_refusal_no_fallback / model_refusal_fallback) is the exact
  event, linked to its episode by parentUuid — the refusal record and the assistant error BOTH carry
  the refused user message's uuid as parentUuid. (refusedUserMessageUuid is NOT the link: it was
  observed diverging from the episode's parent in 2 of 13 refusal records of one storm.)
- the text signature ("safeguards flagged" / the aup URL) is REQUIRED, not a legacy nicety: the same
  storm contained refusal-text errors with NO system record at all, and at a transcript's tail the
  error record can be flushed a beat before its system record — the signature rides the error record
  itself, so the very first read classifies right.
"""
import inspect
import json
import os
import tempfile
import types
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_refusal", os.path.join(BIN, "romp-kernel")).load_module()

T0 = 1786600000
RETRY = "retry\n\n<!-- romp-injected -->"
# The CLI's own boilerplate for a safeguards refusal, model name neutralised.
REFUSAL_TEXT = ("API Error: Opus 5's safeguards flagged this message "
                "(https://www.anthropic.com/legal/aup). Our intentionally broad safeguards allow us "
                "to deliver more capabilities faster, but can sometimes flag legitimate coding, "
                "cybersecurity, and biology tasks. Claude Code can't respond to this message with "
                "Opus 5.")
# An invalid_request whose wording carries NO refusal signature — isolates the event path.
GENERIC_TEXT = "API Error: the request was rejected by the server."


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def _apierr(t, uuid, parent, text, status=None, category="invalid_request"):
    o = {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                     "stop_reason": "stop_sequence"},
         "isApiErrorMessage": True, "error": category}
    if status is not None:
        o["apiErrorStatus"] = status
    return o


def _sysrefusal(t, uuid, parent, subtype="model_refusal_no_fallback", refused=None):
    return {"type": "system", "subtype": subtype, "timestamp": _iso(t), "uuid": uuid,
            "parentUuid": parent, "refusedUserMessageUuid": refused or parent,
            "apiRefusalCategory": "synthetic", "apiRefusalExplanation": "synthetic refusal",
            "originalModel": "opus-5"}


# The non-productive records the CLI interleaves between the error and its system record — they must
# neither clear the blocking error nor break the correlation (observed shape: error, queue-operations
# and/or a file-history-snapshot, THEN the system refusal record).
def _queueop():
    return {"type": "queue-operation", "operation": "synthetic"}


def _snapshot():
    return {"type": "file-history-snapshot", "messageId": "11111111-2222-3333-4444-000000000099"}


class IsRefusalTextPredicate(unittest.TestCase):
    def test_the_clis_own_phrasing_classifies(self):
        for t in (REFUSAL_TEXT,
                  "API Error: Fable 5's safeguards flagged this message.",
                  "API Error: blocked (https://www.anthropic.com/legal/aup)."):
            self.assertTrue(km._is_refusal_text(t), t)

    def test_other_failures_do_not(self):
        for t in ("API Error: 500 Internal server error.",
                  "Request timed out",
                  "You've hit your monthly spend limit. Raise it at claude.ai/settings/usage.",
                  "You've reached your Opus 5 limit. Run /usage-credits to continue or switch "
                  "models with /model",
                  GENERIC_TEXT):
            self.assertFalse(km._is_refusal_text(t), t)


class ApiErrorCarriesTheRefusalFlag(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.td.name, "s.jsonl")
        km._api_err_cache.clear()

    def tearDown(self):
        km._api_err_cache.clear()
        self.td.cleanup()

    def _write(self, *rows):
        with open(self.p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        km._api_err_cache.clear()

    def test_the_text_signature_flags_without_the_system_record(self):
        # the CLI omits the system record for SOME refusal errors (observed in the audited storm), and
        # at the transcript tail the error can land a beat before its system record — the signature on
        # the error record itself covers both.
        self._write(_uline(T0, "synthetic refused ask", "11111111-2222-3333-4444-000000000001"),
                    _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                            "11111111-2222-3333-4444-000000000001", REFUSAL_TEXT))
        e = km._api_error(self.p)
        self.assertTrue(e["refusal"], "the CLI's refusal wording classifies on its own")
        self.assertFalse(e["tooLong"] or e["spendLimit"] or e["modelLimit"] or e["authErr"])

    def test_the_system_record_flags_the_episode_event_based(self):
        # the EXACT event: a system model_refusal record whose parentUuid equals the blocking error's
        # parentUuid (both point at the refused user message) — wording-independent, so a future CLI
        # rephrase of the error text still classifies.
        self._write(_uline(T0, "synthetic refused ask", "11111111-2222-3333-4444-000000000001"),
                    _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                            "11111111-2222-3333-4444-000000000001", GENERIC_TEXT),
                    _queueop(), _snapshot(),
                    _sysrefusal(T0 + 2, "11111111-2222-3333-4444-000000000003",
                                "11111111-2222-3333-4444-000000000001"))
        self.assertTrue(km._api_error(self.p)["refusal"],
                        "the linked system record marks the episode regardless of wording")

    def test_the_fallback_subtype_marks_too(self):
        # model_refusal_fallback with a still-BLOCKING error: each retry would also manufacture
        # another model downgrade, so it is every bit as non-retryable.
        self._write(_uline(T0, "synthetic refused ask", "11111111-2222-3333-4444-000000000001"),
                    _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                            "11111111-2222-3333-4444-000000000001", GENERIC_TEXT),
                    _sysrefusal(T0 + 2, "11111111-2222-3333-4444-000000000003",
                                "11111111-2222-3333-4444-000000000001",
                                subtype="model_refusal_fallback"))
        self.assertTrue(km._api_error(self.p)["refusal"])

    def test_a_plain_invalid_request_is_not_flagged(self):
        self._write(_uline(T0, "synthetic ask", "11111111-2222-3333-4444-000000000001"),
                    _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                            "11111111-2222-3333-4444-000000000001", GENERIC_TEXT))
        self.assertFalse(km._api_error(self.p)["refusal"],
                         "an invalid_request without refusal evidence stays transient")

    def test_a_transient_500_is_not_flagged(self):
        self._write(_uline(T0, "synthetic ask", "11111111-2222-3333-4444-000000000001"),
                    _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                            "11111111-2222-3333-4444-000000000001",
                            "API Error: 500 Internal server error.",
                            status=500, category="server_error"))
        self.assertFalse(km._api_error(self.p)["refusal"],
                         "a 500 still auto-retries in Working — this must not widen")

    def test_a_system_record_from_another_turn_does_not_mark(self):
        # an UNLINKED refusal record (parentUuid points at a different user message) belongs to some
        # other turn's story — it must not paint the current blocking error as a refusal.
        self._write(_uline(T0, "synthetic ask", "11111111-2222-3333-4444-000000000001"),
                    _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                            "11111111-2222-3333-4444-000000000001", GENERIC_TEXT),
                    _sysrefusal(T0 + 2, "11111111-2222-3333-4444-000000000003",
                                "11111111-2222-3333-4444-000000000042"))
        self.assertFalse(km._api_error(self.p)["refusal"])

    def test_the_flag_survives_the_cache(self):
        self._write(_uline(T0, "synthetic refused ask", "11111111-2222-3333-4444-000000000001"),
                    _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                            "11111111-2222-3333-4444-000000000001", REFUSAL_TEXT))
        self.assertTrue(km._api_error(self.p)["refusal"])
        self.assertIn(self.p, km._api_err_cache, "the first read populated the (mtime,size) cache")
        self.assertTrue(km._api_error(self.p)["refusal"], "the flag rides the cached record")

    def test_the_storm_shape_keeps_the_flag_per_episode(self):
        # the audited shape: each injected retry clears the error (a genuine user prompt), the next
        # refusal writes a NEW error record with a NEW uuid, and ITS system record must re-mark it.
        rows = [_uline(T0, "synthetic refused ask", "11111111-2222-3333-4444-000000000001"),
                _apierr(T0 + 1, "11111111-2222-3333-4444-000000000002",
                        "11111111-2222-3333-4444-000000000001", GENERIC_TEXT),
                _queueop(),
                _sysrefusal(T0 + 2, "11111111-2222-3333-4444-000000000003",
                            "11111111-2222-3333-4444-000000000001"),
                _uline(T0 + 10, RETRY, "11111111-2222-3333-4444-000000000004",
                       parent="11111111-2222-3333-4444-000000000003"),
                _apierr(T0 + 11, "11111111-2222-3333-4444-000000000005",
                        "11111111-2222-3333-4444-000000000004", GENERIC_TEXT),
                _snapshot(),
                _sysrefusal(T0 + 12, "11111111-2222-3333-4444-000000000006",
                            "11111111-2222-3333-4444-000000000004")]
        self._write(*rows)
        e = km._api_error(self.p)
        self.assertTrue(e["refusal"], "episode two is marked by ITS OWN system record")
        self.assertEqual(e["uuid"], "11111111-2222-3333-4444-000000000005",
                         "the latest episode's record stands")


class FakeBackend:
    """A minimal backend: a controllable pending queue + a record of what got sent."""
    def __init__(self, pending=()):
        self._pending = list(pending)
        self.sent = []

    def pending_queued(self, sid):
        return list(self._pending)

    def send(self, sid, text):
        self.sent.append(text)
        return True


class RefusalNeverAutoRetried(unittest.TestCase):
    """The retry gate: a refusal-flagged error never collects an auto-retry — not once per episode,
    not once at all — because a retry re-sends the same prompt and manufactures the same refusal
    (measured 12/12 in the audited storm). Manual Retry-now keeps the existing override contract."""

    SID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self._saved = (km.Sessions, km._retry_paused_on, km._session_retry_suppressed,
                       km._api_error, km._path_of, km._alive_sessions)
        km._retry_paused_on = lambda: False
        km._session_retry_suppressed = lambda sid: False
        km._alive_sessions = lambda now, tmux: [{"sid": self.SID, "path": "/TESTDIR/x.jsonl"}]
        km._path_of = lambda sid, now=None: "/TESTDIR/x.jsonl"
        self.aerr = {"text": REFUSAL_TEXT, "status": None, "category": "invalid_request",
                     "uuid": "11111111-2222-3333-4444-000000000101",
                     "parentUuid": "11111111-2222-3333-4444-000000000100",
                     "tooLong": False, "spendLimit": False, "modelLimit": False,
                     "authErr": False, "refusal": True}
        km._api_error = lambda path: self.aerr
        self.be = FakeBackend()
        km.Sessions = types.SimpleNamespace(backend_for=lambda sid: self.be)
        km._auto_retried.clear()
        km._auto_retry_state.clear()

    def tearDown(self):
        (km.Sessions, km._retry_paused_on, km._session_retry_suppressed,
         km._api_error, km._path_of, km._alive_sessions) = self._saved
        km._auto_retried.clear()
        km._auto_retry_state.clear()

    def _tick(self):
        km._auto_retry_tick(1_000_000, {self.SID: {"state": ""}})

    def test_two_successive_refusal_episodes_never_fire(self):
        # THE storm shape: every refusal writes a new error record (new uuid → new episode), which is
        # exactly why the once-per-episode gate alone could never terminate the loop.
        self._tick()
        self.assertEqual(self.be.sent, [], "episode one: a refusal is never auto-retried")
        self.aerr = dict(self.aerr, uuid="11111111-2222-3333-4444-000000000102")
        self._tick()
        self.assertEqual(self.be.sent, [], "episode two (fresh uuid): still never — the storm is dead")

    def test_manual_retry_still_fires(self):
        # the existing manual-override contract: an explicit click is the user's call — they may have
        # rewritten or dropped the offending thread since.
        km._fire_api_retry(self.SID, self.be, manual=True)
        self.assertEqual(self.be.sent, [RETRY])

    def test_a_transient_server_error_still_auto_retries(self):
        # the guard must not over-block: a 500 keeps the recovery path it always had.
        self.aerr = {"text": "API Error: 500 Internal server error.", "status": 500,
                     "category": "server_error", "uuid": "11111111-2222-3333-4444-000000000103",
                     "tooLong": False, "spendLimit": False, "modelLimit": False,
                     "authErr": False, "refusal": False}
        self._tick()
        self.assertEqual(self.be.sent, [RETRY])


class SurfacesPinTheOnYouTreatment(unittest.TestCase):
    """The flag has to reach every surface that already distinguishes on-you from transient, or the
    tab keeps saying romp is handling a storm romp is deliberately not retrying."""

    def test_the_auto_retry_skip_names_refusal(self):
        fn = inspect.getsource(km._fire_api_retry)
        self.assertIn('or _rerr.get("refusal")', fn,
                      "the on-you skip must include the refusal — the kernel driver and every "
                      "client ask share this one decision")

    def test_the_card_floors_to_needs_you(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('or aerr.get("authErr") or aerr.get("refusal"))))', src,
                      "api_block must include the refusal — otherwise the card sits in Working "
                      "with the nudge suppressed and nothing able to move it")

    def test_the_card_names_the_real_remedy(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('"refusal": bool(aerr.get("refusal"))', src)
        self.assertIn("the model's safeguards refused this prompt — rewrite it or drop this thread",
                      src)
        self.assertIn("this session stopped on an API error — Retry to resume", src,
                      "the transient wording stays for genuinely transient errors")

    def test_the_status_flag_reaches_the_tab(self):
        self.assertIn('"apiRefusal": bool(aerr and aerr.get("refusal"))',
                      inspect.getsource(km.build_session))


if __name__ == "__main__":
    unittest.main()
