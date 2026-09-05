"""The timeline skeleton frame dedups across a clock step (2026-09-04).

The kernel pushes the timeline in two frames: a small SKELETON ({"type": "data", "data": {sessions, ...,
now}}) and the heavy bars. Per-client dedup strips the always-ticking fields (_DEDUP_VOLATILE) before
comparing — but the skeleton's clock sits one level down, under "data", so every cycle produced a "new"
skeleton and the timeline pane tore down and rebuilt its whole SVG for a frame in which nothing had moved.
A replay of the real board measured that as one full redraw per pusher cycle, on the browser thread the
chat pane's clicks share. The signature now strips the nested clock too.
"""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
km = SourceFileLoader("romp_kernel", os.path.join(ROOT, "bin", "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-aaaaaaaaaaa1"


def _skel(now, **over):
    d = {"sessions": [{"id": SID, "name": "web", "live": True, "state": "working"}], "views": {}, "usage": {"pct": 12},
         "focus": None, "hover": None, "now": now}
    d.update(over)
    return {"type": "data", "data": d}


def _sig(m):
    return km._dedup_sig(m, json.dumps(m))


class SkeletonDedup(unittest.TestCase):
    def test_a_only_the_clock_moved_so_the_skeleton_dedups(self):
        self.assertEqual(_sig(_skel(1000)), _sig(_skel(1005)), "a nested `now` is as volatile as a top-level one")

    def test_b_a_real_change_still_crosses(self):
        self.assertNotEqual(_sig(_skel(1000)), _sig(_skel(1005, usage={"pct": 13})))
        changed = _skel(1005)
        changed["data"]["sessions"][0]["state"] = "idle"
        self.assertNotEqual(_sig(_skel(1000)), _sig(changed))

    def test_c_other_frames_are_untouched(self):
        bars = {"type": "bars", "turns": {}, "judging": [], "messages": [], "now": 1000}
        self.assertEqual(_sig(bars), _sig(dict(bars, now=1005)), "the top-level strip is what it was")
        chat = {"type": "session", "id": SID, "events": [], "now_ish": 1}
        self.assertEqual(_sig(chat), json.dumps(chat), "a clock-free payload still compares its own serialization")
        nested_other = {"type": "feed", "data": {"now": 1}}
        self.assertNotEqual(_sig(nested_other), _sig({"type": "feed", "data": {"now": 2}}),
                            "only the skeleton frame gets the nested strip: its shape is the one that carries a nested clock")

    def test_d_the_pusher_sends_the_skeleton_through_the_deduping_path(self):
        import inspect
        src = inspect.getsource(km._push)
        self.assertIn('_send_client(c, ("timeline",), {"type": "data", "data": skel})', src)


if __name__ == "__main__":
    unittest.main()
