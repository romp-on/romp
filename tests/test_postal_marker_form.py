#!/usr/bin/env python3
"""The postal marker is romp's HTML comment, and no fixture may write it any other way.

docs/event-model.md states the detector ("Postal is detected by the `<!-- romp-msg-id: <id> -->`
marker") and postal/postal_service.py calls that form a consumer contract, but the event model used
to match the bare words anywhere in any text. That mismatch had two costs, and both were invisible:

  - Fixtures across eight test files wrote a wire form no emitter has ever produced, and passed —
    so the tests ratified the loose matcher instead of the real one. When the matcher was tightened,
    those fixtures stopped being postal deliveries at all, and their courier/planner assertions went
    quiet rather than red (a segment with no peer simply never reaches the courier).
  - In production the loose form ate real cards: any prompt, hook line or tool output that merely
    mentioned the words authored {"peer": None} — still a dict, so the segment reads
    peer-rather-than-human and both the planner and the courier drop it.

This is the ratchet for the first cost. The second is pinned by test_teammate_message.py's
mention-is-not-a-delivery case and by test_postal_multi_marker.py.
"""
import os
import re
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
SELF = os.path.basename(os.path.realpath(__file__))
# Hermetic state BEFORE the load — it resolves its state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = SourceFileLoader("romp_event_model_markerform",
                      os.path.join(os.path.dirname(HERE), "bin", "romp-event-model")).load_module()

# Assembled rather than written out, so this file is not its own first hit.
MARKER = re.compile("romp-msg-" + r"(?:id|kind)\s*:")

# Bare occurrences that are CORRECT, with the reason and the exact count. A new one anywhere else
# fails; a new one here has to be argued for in this table. Counts, not whole-file exemptions, so a
# genuine postal fixture added to one of these files still trips the guard.
EXPECTED_BARE = {
    # kernel.py's _pending_queued / _genuine_queued / _postal_shaped are plain substring tests,
    # deliberately over-broad: there a false positive only refuses a delivery (safe), while a false
    # negative is the postal-isolation bypass they were written for on 2026-07-10. Their fixtures
    # test the substring, so the bare form is the right input.
    "test_kernel.py": 2,
    # The mention-is-not-a-delivery case: writing the bare form IS the case.
    "test_teammate_message.py": 3,
    # The neutralizer suite (MarkerNeutralizerVariants) assembles whitespace VARIANTS of the
    # comment form ("<!--%sromp-msg-id: …" over ws ∈ {"", " ", "\n", …}) to prove
    # _neutralize_romp_markers breaks the whole "<!--\s*romp-" class the downstream matchers
    # accept — each assembled string IS comment-form (the test asserts POSTAL_RE matches it
    # before neutralizing); only the %s placeholder keeps this source line from reading as one.
    "test_marker_neutralizer.py": 1,
}


def _bare_hits(path):
    """[(lineno, line), ...] where the marker appears NOT wrapped in romp's HTML comment."""
    out = []
    with open(path, errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            for m in MARKER.finditer(line):
                if line[:m.start()].rstrip().endswith("<!--"):
                    continue                      # romp's own form
                out.append((i, line.strip()))
    return out


class NoBareMarkerInFixtures(unittest.TestCase):
    def test_every_test_module_writes_the_comment_form(self):
        for fn in sorted(os.listdir(HERE)):
            if not (fn.startswith("test_") and fn.endswith(".py")) or fn == SELF:
                continue
            hits = _bare_hits(os.path.join(HERE, fn))
            allowed = EXPECTED_BARE.get(fn, 0)
            self.assertEqual(
                len(hits), allowed,
                "%s: expected %d bare postal marker(s), found %d. A fixture must write romp's "
                "comment form — the only form any emitter produces — or the event model reads it as "
                "prose and the segment never becomes a peer delivery, silently. If a bare one is "
                "genuinely right here, add it to EXPECTED_BARE with a reason.\n  %s"
                % (fn, allowed, len(hits), "\n  ".join("%d: %s" % h for h in hits)))

    def test_the_table_has_no_stale_rows(self):
        for fn in EXPECTED_BARE:
            self.assertTrue(os.path.exists(os.path.join(HERE, fn)),
                            "EXPECTED_BARE names a file that is gone: %s" % fn)


class TheDetectorItself(unittest.TestCase):
    """The matcher the fixtures are held to, so the two cannot drift apart again."""

    def test_only_the_comment_form_is_a_marker(self):
        mid = "1781100000.111_222.TESTHOST"
        self.assertEqual(em.POSTAL_RE.findall("body\n<!-- romp-msg-id: %s -->" % mid), [mid])
        self.assertEqual(em.POSTAL_RE.findall("I saw romp-msg-id: %s in the log" % mid), [],
                         "a mention of the words is not a delivery")
        self.assertEqual(em.POSTAL_KIND_RE.findall("<!-- romp-msg-kind: delegate -->"), ["delegate"])
        self.assertEqual(em.POSTAL_KIND_RE.findall("the kind was romp-msg-kind: delegate"), [])


if __name__ == "__main__":
    unittest.main()
