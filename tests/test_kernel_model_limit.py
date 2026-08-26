#!/usr/bin/env python3
"""A MODEL's own allowance running out is ON YOU, not a transient API error (the user 2026-08-01).

The incident, all fixtures SYNTHETIC: the `api` session was running on a model whose included weekly
allowance was spent. The user sent it a request; the CLI wrote the message to the transcript and the
very next record was an API-error record reading "You've reached your <model> limit. Run
/usage-credits to continue or switch models with /model". Every romp auto-retry for the next 80
minutes got the identical error, so the turn never ran and the message sat unanswered.

romp classified only two API errors as on-you: "prompt is too long" and a monthly spend cap. A spent
model allowance fell through as TRANSIENT, and every consequence followed from that one call:
- the card stayed in Working, so nothing said the thread was stuck;
- the auto-nudge was suppressed (a session sitting on an API error is deliberately not "orphaned"),
  so the working card had NOTHING that could move it — the exact invariant the fire-list deadlock
  broke by another route (tests/test_nudge_injected_turn_arm.py);
- the card's badge read "stopped on an API error — Retry to resume" when Retry could not work;
- the tab rendered amber-retrying, which says "romp has this", the opposite of the truth.

romp knew the whole time: usage.json carried that model's window at 100%.

Classified here on the CLI's own REMEDY phrasing, because a model-scoped limit is the only failure
that offers "switch models" or a credits top-up. An ACCOUNT-WIDE 5h/weekly window carries no such
remedy and keeps its countdown-and-retry path (_auto_pause_on_limit reads the usage report for that),
and a spend cap keeps its own. Deliberately NOT a global retry-pause: that was tried for a
model-scoped limit on 2026-07-03 and flapped 262 times, starving the judges — the account keeps
serving every other model, so this belongs on the one session's card.
"""
import inspect
import json
import os
import tempfile
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
km = SourceFileLoader("romp_kernel_modellimit", os.path.join(BIN, "romp-kernel")).load_module()

T0 = 1781100000
# The CLI's own words for a spent model allowance, model name neutralised.
MODEL_LIMIT_TEXT = ("You've reached your Opus 5 limit. Run /usage-credits to continue or switch "
                    "models with /model")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def _apierr(t, uuid, parent, text, status=None, category="usage_limit"):
    o = {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                     "stop_reason": "stop_sequence"},
         "isApiErrorMessage": True, "error": category}
    if status is not None:
        o["apiErrorStatus"] = status
    return o


class IsModelLimitPredicate(unittest.TestCase):
    def test_the_clis_own_phrasing_classifies(self):
        for t in (MODEL_LIMIT_TEXT,
                  "You've reached your Fable 5 limit. Run /usage-credits to continue.",
                  "Model limit reached — switch models with /model to keep going."):
            self.assertTrue(km._is_model_limit(t), t)

    def test_other_failures_do_not(self):
        for t in ("API Error: 500 Internal server error.",
                  "Request timed out",
                  "You've hit your monthly spend limit. Raise it at claude.ai/settings/usage.",
                  # an ACCOUNT-WIDE rate window: a real countdown, no model/credits remedy
                  "You've hit your session limit · resets 1:10pm (America/Los_Angeles)",
                  "Usage limit reached. Your limit resets at 3pm.",
                  # prose that merely mentions models must not trip it — there is no limit here
                  "I'll switch models for the next step."):
            self.assertFalse(km._is_model_limit(t), t)


class ApiErrorCarriesTheFlag(unittest.TestCase):
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

    def test_the_error_is_classified_model_limit_and_nothing_else(self):
        self._write(_uline(T0, "capture the frame", "u1"),
                    _apierr(T0 + 1, "e1", "u1", MODEL_LIMIT_TEXT))
        e = km._api_error(self.p)
        self.assertTrue(e["modelLimit"], "a spent model allowance is classified on-you")
        self.assertFalse(e["spendLimit"], "it is not a billing cap — the account is fine")
        self.assertFalse(e["tooLong"])

    def test_a_transient_error_is_not_a_model_limit(self):
        self._write(_uline(T0, "capture the frame", "u1"),
                    _apierr(T0 + 1, "e1", "u1", "API Error: 500 Internal server error.",
                            status=500, category="server_error"))
        self.assertFalse(km._api_error(self.p)["modelLimit"],
                         "a 500 still auto-retries in Working — this must not widen")

    def test_the_retry_storm_keeps_the_classification(self):
        # the audited shape: romp's injected "retry" turns each get the same error back. The LATEST
        # record stands, so the card must still read on-you at attempt 20, not just attempt 1.
        rows = [_uline(T0, "capture the frame", "u1"), _apierr(T0 + 1, "e0", "u1", MODEL_LIMIT_TEXT)]
        for i in range(1, 20):
            rows.append(_uline(T0 + i * 60, "retry\n\n<!-- romp-injected -->", "u%d" % i))
            rows.append(_apierr(T0 + i * 60 + 1, "e%d" % i, "u%d" % i, MODEL_LIMIT_TEXT))
        self._write(*rows)
        self.assertTrue(km._api_error(self.p)["modelLimit"])


class SurfacesPinTheOnYouTreatment(unittest.TestCase):
    """The flag has to reach every surface that already distinguishes on-you from transient, or the
    card moves while the tab still says romp is handling it."""

    def test_the_card_floors_to_needs_you(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('or aerr.get("modelLimit")', src,
                      "api_block must include the model limit — otherwise the card sits in Working "
                      "with the nudge suppressed and nothing able to move it")
        self.assertIn('or aerr.get("authErr") or aerr.get("refusal"))))', src)

    def test_the_card_names_the_real_remedy(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('"modelLimit": bool(aerr.get("modelLimit"))', src)
        self.assertIn("switch its model or add credits to continue", src)
        self.assertIn("this session stopped on an API error — Retry to resume", src,
                      "the transient wording stays for genuinely transient errors")

    def test_the_status_flag_reaches_the_tab(self):
        self.assertIn('"apiModelLimit": bool(aerr and aerr.get("modelLimit"))',
                      inspect.getsource(km.build_session))

    def test_it_does_not_engage_the_global_retry_pause(self):
        # the 2026-07-03 flap: pausing the fleet on a model-scoped limit starved the judges, because
        # the account keeps serving and _auto_resume_retry cleared the pause every tick
        src = inspect.getsource(km._auto_pause_on_limit)
        self.assertIn('k != "fable"', src, "account-wide windows only still gate the global pause")
        self.assertNotIn("modelLimit", src)


if __name__ == "__main__":
    unittest.main()
