#!/usr/bin/env python3
"""The phone session picker is click-safe and never lets a tap die silently (2026-08-19).

The user could not select a session on the phone: the picker's sync() wiped the list with
innerHTML='' on EVERY kernel push (0.5-3s cadence) — a tap whose finger was down when a push
landed had its row destroyed mid-press (no mousedown/mouseup pair, no click), and the wipe reset
the list's scroll so rows below the fold snapped away mid-reach. Separately, a PLACEHOLDER tab
(session payload not yet merged — a remote host still relaying, a fresh reconnect) painted as a
normal row whose forwarded .click() matched no [data-act] and silently did nothing.

The picker now follows the repo's own click-safety rule: ONE delegated listener on the stable
list, rows updated IN PLACE keyed by data-id (scroll position survives), a pointer-held defer so
a push mid-press flushes on release, and placeholder rows say "syncing" — tapping one marks it
pending ("opening") and the arrival-triggered sync activates it the moment the payload lands.
Source pins on the kernel-served JS/CSS strings."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_mpick", os.path.join(BIN, "romp-kernel")).load_module()


class MobilePickerClickSafe(unittest.TestCase):
    def test_the_list_is_never_wiped(self):
        self.assertNotIn("list.innerHTML=''", km._CHAT_MOBILE_JS,
                         "a wipe destroys the row under the finger and resets the scroll")
        self.assertIn("if(!row)row=rowMake(s);else rowUpdate(row,s);", km._CHAT_MOBILE_JS,
                      "rows update in place, keyed by data-id")

    def test_one_delegated_listener_on_the_stable_list(self):
        self.assertIn("list.addEventListener('click',function(e){", km._CHAT_MOBILE_JS)
        self.assertNotIn("row.addEventListener('click'", km._CHAT_MOBILE_JS,
                         "per-row listeners die with their row — the delegation rule applies here too")

    def test_pushes_defer_while_a_finger_is_down(self):
        self.assertIn("list.addEventListener('pointerdown',function(){held=true;});", km._CHAT_MOBILE_JS)
        self.assertIn("if(held){dirty=true;return;}", km._CHAT_MOBILE_JS)
        self.assertIn("document.addEventListener('pointercancel',release);", km._CHAT_MOBILE_JS)

    def test_a_placeholder_tap_goes_pending_and_activates_on_arrival(self):
        self.assertIn("ph:t.classList.contains('tab-placeholder')", km._CHAT_MOBILE_JS,
                      "the picker knows a not-yet-synced tab when it lists one")
        self.assertIn("pendingId=id;row.classList.add('pending');", km._CHAT_MOBILE_JS,
                      "the tap never dies silently")
        self.assertIn("if(prt&&!prt.classList.contains('tab-placeholder')){pendingId=null;prt.click();hide();}",
                      km._CHAT_MOBILE_JS, "the payload's arrival is the activation event")

    def test_the_rows_say_syncing_and_opening(self):
        self.assertIn(".mrow.ph::after{content:'syncing", km._CHAT_MOBILE_CSS)
        self.assertIn(".mrow.pending::after{content:'opening", km._CHAT_MOBILE_CSS)

    def test_the_trigger_chip_wears_the_chrome_tokens_and_a_light_skin(self):
        """The #mcur/#madd chip is the one mobile surface that never joined the token migration: raw
        #2a2a2a/#3a3a3a in this inline sheet outranked everything styles.css could say, so the chip sat
        as a dark slab on the light chat page (the user 2026-09-02). Surfaces ride tokens whose dark
        value is byte-identical to the old literals; text tiers take light-block overrides, with the
        .colored restatement keeping the identity color on the name."""
        css = km._CHAT_MOBILE_CSS
        self.assertNotIn("background:#2a2a2a", css, "no raw chip surface left — tokens with dark fallbacks")
        self.assertNotIn("border:1px solid #3a3a3a", css)
        self.assertEqual(css.count("background:var(--btn-bg,#2a2a2a)"), 3, "chip, colored chip, and +")
        self.assertEqual(css.count("border:1px solid var(--hairline,#3a3a3a)"), 3,
                         "chip + the '+' + the #mlist card share the one hairline")
        self.assertIn("body.theme-light #mcur{color:var(--menu-fg)}", css)
        self.assertIn("body.theme-light #mcur.colored{color:var(--cbg)}", css)
        self.assertIn("body.theme-light #madd{color:var(--text-muted)}", css)


if __name__ == "__main__":
    unittest.main()
