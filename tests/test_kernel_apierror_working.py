"""API errors (the user 2026-06-29): a TRANSIENT API error is not blocking — its card stays in Working with the
⚠ chip and auto-retry recovers it. BUT a "prompt is too long" error is on YOU (compact needed), so it (and only
it) floors the focus card to needs-input and gets the alarm-red tab. Source pins on _api_error + build_feed."""
import inspect
import os
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class ApiErrorWorking(unittest.TestCase):
    def test_api_error_carries_a_tooLong_flag(self):
        src = inspect.getsource(km._api_error)
        self.assertIn('"tooLong": "too long" in text.lower()', src)

    def test_only_tooLong_floors_the_card_to_needs_input(self):
        src = inspect.getsource(km.build_feed)
        # api_block fires ONLY for a "prompt too long" api_top; a transient error does NOT move the card
        self.assertIn('api_block = (nid == api_top and bool(aerr and aerr.get("tooLong")))', src)
        self.assertIn('column = ("needs_input" if (api_block or nid == perm_top or (col == "blocked" and not recheck))', src)

    def test_status_marks_tooLong_so_the_tab_can_color_it(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"apiTooLong": bool(aerr and aerr.get("tooLong"))', src)

    def test_the_card_blocked_badge_distinguishes_tooLong(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('"tooLong": bool(aerr.get("tooLong"))', src)
        self.assertIn("prompt is too long — compact it to continue", src)


if __name__ == "__main__":
    unittest.main()
