"""API errors (the user 2026-06-29): a TRANSIENT API error is not blocking — its card stays in Working with the
⚠ chip and auto-retry recovers it. BUT an ON-YOU error floors the focus card to needs-input and gets the
alarm-red tab: "prompt is too long" (compact needed), a monthly spend cap (raise it, the user 2026-07-14), or a
spent MODEL allowance (switch model / add credits, the user 2026-08-01) — the spend cap ALSO stops auto-retry
entirely (no reset to wait out). Source pins on _api_error + build_feed. The model-limit case has its own
behavioural file, tests/test_kernel_model_limit.py."""
import inspect
import os
import unittest
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
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class ApiErrorWorking(unittest.TestCase):
    def test_api_error_carries_a_tooLong_flag(self):
        # the classification lives in _api_error_scan since the tail-window split — _api_error is now the
        # widening driver around it, so read the pair rather than pinning which half holds the flag
        src = inspect.getsource(km._api_error) + inspect.getsource(km._api_error_scan)
        self.assertIn('"tooLong": "too long" in text.lower()', src)

    def test_only_on_you_errors_floor_the_card_to_needs_input(self):
        src = inspect.getsource(km.build_feed)
        # api_block fires for an ON-YOU api_top — "prompt too long" (compact), a monthly spend cap (raise
        # it, the user 2026-07-14), a spent model allowance, a dead credential (per-session auth, the
        # user 2026-08-08), or a safeguards refusal (the user 2026-08-15); a transient error does NOT
        # move the card (it auto-retries in Working).
        self.assertIn('api_block = (nid == api_top and bool(aerr and (aerr.get("tooLong") or aerr.get("spendLimit")', src)
        self.assertIn('or aerr.get("modelLimit")', src)
        self.assertIn('or aerr.get("authErr") or aerr.get("refusal"))))', src)
        self.assertIn('column = ("needs_input" if (api_block or nid == jauth_top or nid == perm_top', src)   # stalled_floor retired 2026-07-07; jauth_top = the judge-auth floor (2026-08-12)
        self.assertIn('or (col == "blocked" and not recheck and not rejudging))', src)

    def test_spend_cap_is_classified_and_floors_like_tooLong(self):
        # a monthly spend cap is on you (raise it) AND never auto-retried — classified in _api_error, floored
        # to needs-input, and badged with the raise-your-cap guidance (the user 2026-07-14).
        self.assertIn('"spendLimit": _is_spend_limit(text)',
                      inspect.getsource(km._api_error) + inspect.getsource(km._api_error_scan))
        bf = inspect.getsource(km.build_feed)
        self.assertIn('"spendLimit": bool(aerr.get("spendLimit"))', bf)
        self.assertIn("monthly spend limit — raise it at claude.ai/settings/usage", bf)
        self.assertIn('"apiSpendLimit": bool(aerr and aerr.get("spendLimit"))', inspect.getsource(km.build_session))

    def test_status_marks_tooLong_so_the_tab_can_color_it(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"apiTooLong": bool(aerr and aerr.get("tooLong"))', src)

    def test_the_card_blocked_badge_distinguishes_tooLong(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('"tooLong": bool(aerr.get("tooLong"))', src)
        self.assertIn("prompt is too long — compact it to continue", src)


if __name__ == "__main__":
    unittest.main()
