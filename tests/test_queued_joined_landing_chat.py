#!/usr/bin/env python3
"""The chat build of a JOINED text's landing: tagged machine lines queued back to back are fed as one text
(SdkSession._join_queued_locked), so the CLI writes one record for all of them. That record must carry every
part's copy id (the chat retires its pending copies by id) and retire every part's echo; before, each part's echo
waited for a record of its own text that never comes.

Borrows the real-backend world of test_queued_copy_identity. SYNTHETIC fixtures only: invented text, a private
synthetic sid, placeholder uuids."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from test_queued_copy_identity import (_World, at_clock, attline, aline, RUNNING, T0, SID, km, sb)  # noqa: E402,F401


def _tagged(body, label="watch"):
    return body + "\n\n<!-- romp-tag: " + label + " -->"


class AJoinedLandingCarriesEveryId(unittest.TestCase):
    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()
        km._pending_ops.pop(SID, None)

    def _feed_joined(self):
        s = self.w.s
        with s._lock:
            item, meta = s._pop_for_feed_locked(0)
            item, qids = s._join_queued_locked(item, meta)
        if qids:
            self.w.be._stamp_joined_echoes(SID, qids, item)
        return item

    def test_one_record_of_three_joined_tagged_lines_carries_all_three_ids_and_retires_their_echoes(self):
        live = at_clock(self.w.now)
        self.w.write(RUNNING, shift=live)
        texts = [_tagged("lint passed"), _tagged("tests passed", label="ci"), _tagged("build passed", label="ci")]
        for t in texts:
            self.assertTrue(self.w.be.send(SID, t))
        qids = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        joined = self._feed_joined()
        self.assertEqual(joined, "\n\n".join(texts))
        mid = self.w.build()                      # fed, not landed: every part's echo is still shown
        echoes = [e for e in mid["events"] if str(e.get("uuid", "")).startswith("echo:")]
        self.assertEqual(sorted(e.get("uuid") for e in echoes), sorted(qids))
        self.w.write(RUNNING + [attline(T0 + 85, joined, "att1", "tr1"),
                                aline(T0 + 105, "Noted.", "a3", "att1", tools=("Bash",), stop="tool_use")], shift=live)
        m = self.w.build()
        landed = [e for e in m["events"] if e.get("kind") == "user" and e.get("uuid") == "att1"]
        self.assertEqual(len(landed), 1, [(e.get("kind"), e.get("uuid")) for e in m["events"]])
        self.assertEqual(landed[0].get("qids"), qids, "the one record names every joined copy's id, in order")
        self.assertIsNone(landed[0].get("qid"))
        self.assertEqual([e for e in m["events"] if str(e.get("uuid", "")).startswith("echo:")], [],
                         "every part's echo retired on the joined record")
        self.assertNotIn(SID, self.w.be._live, "…and left the live store")


if __name__ == "__main__":
    unittest.main()
