"""TABS-FIRST (the user 2026-06-26): the tabOrder push carries name+color per tab so the client can paint the
WHOLE strip as placeholders up front (no one-by-one pop-in). Both emit sites — the periodic/connect _push and
the WS 'ready' handler — send a `tabs` list of {id, name, color} alongside the sid `order`.
"""
import inspect
import os
import unittest
from importlib.machinery import SourceFileLoader
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
km = SourceFileLoader("romp_kernel", KPATH).load_module()


class TabsFirst(unittest.TestCase):
    def test_push_taborder_carries_name_and_color_per_tab(self):
        src = inspect.getsource(km._push)
        self.assertIn('tab_meta = [{"id": s["sid"], "name": s.get("name", ""), "color": _name_color(s["sid"])}', src,
                      "the periodic push builds a name+color list per tab")
        self.assertIn('{"type": "tabOrder", "order": tab_order, "tabs": tab_meta, "views": _views_client()}', src,
                      "and ships it as the tabs field alongside the sid order")

    def test_connect_ready_handler_also_sends_tabs(self):
        text = open(KPATH).read()
        self.assertIn('{"type": "tabOrder", "order": _o, "tabs": _tabs, "views": _views_client()}', text,
                      "the WS 'ready' connect push also carries name+color tabs")
        self.assertIn('_tabs = [{"id": s["sid"], "name": s.get("name", ""), "color": _name_color(s["sid"])}', text)

    def test_name_color_shape_matches_the_client_color_type(self):
        # _name_color returns {bg,fg} or None — exactly the render.ts Color the placeholder applies.
        # A sid with no names entry → None (no color), which the client tolerates.
        self.assertIsNone(km._name_color("11111111-2222-3333-4444-555555555555"))


if __name__ == "__main__":
    unittest.main()
