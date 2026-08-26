#!/usr/bin/env python3
"""Dashboard raw-mode editing is opt-in, and the SAVE ROUTE is the wall (the user 2026-08-22).

The viewer's Edit asks with a popup and posts setFileEditing; every kernel keeps its own copy of the
flag and _save_file refuses while it is off — hiding the button alone would leave every token-holder
the write path. These pin the boundary server-side: default OFF (absent/garbled state file included),
the setter flips it, /version + the mesh settings dict report it, and a successful save TELLS the
live session whose tree it hit (never edited under silently — the trace body itself is voice-checked
by tests/test_injected_voice.py).

Synthetic only: temp dirs, placeholder sids, no real session state.
"""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel_fileedit", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


def _mk_text(dirpath, name="doc.md", text="hello\n"):
    p = os.path.join(dirpath, name)
    with open(p, "w") as f:
        f.write(text)
    return p


class TheGateIsServerSide(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        km._set_file_editing(False)

    def tearDown(self):
        km._set_file_editing(False)
        self.td.cleanup()

    def test_default_is_off_and_the_save_refuses(self):
        # OFF must be the provable default: no state file at all
        try:
            os.unlink(str(km.jd.STATE / "file-editing.json"))
        except OSError:
            pass
        self.assertFalse(km._file_editing_on(), "absent file must read OFF — opt-in is provable or absent")
        p = _mk_text(self.td.name)
        ns = os.stat(p).st_mtime_ns
        mt, err = km._save_file(p, None, "changed\n", ns)
        self.assertIsNone(mt)
        self.assertIn("file editing is off", err, "the refusal says what is off and where the yes lives")
        with open(p) as f:
            self.assertEqual(f.read(), "hello\n", "a refused save writes NOTHING")

    def test_a_garbled_state_file_still_reads_off(self):
        (km.jd.STATE / "file-editing.json").write_text("not json {")
        self.assertFalse(km._file_editing_on(), "garbage must refuse, never default open")

    def test_the_setter_opens_the_gate_and_the_save_lands(self):
        km._set_file_editing(True)
        self.assertTrue(km._file_editing_on())
        p = _mk_text(self.td.name)
        ns = os.stat(p).st_mtime_ns
        mt, err = km._save_file(p, None, "changed\n", ns)
        self.assertIsNone(err)
        self.assertIsInstance(mt, int)
        with open(p) as f:
            self.assertEqual(f.read(), "changed\n")

    def test_version_and_the_mesh_settings_dict_report_it(self):
        # both surfaces: the local gear checkbox (top-level) and the peer-poll dict (settings)
        for want in (False, True):
            km._set_file_editing(want)
            v = km._version_info()
            self.assertIs(v["fileEditing"], want)
            self.assertIs(v["settings"]["fileEditing"], want)


class TheOwningSessionIsTold(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tree = os.path.join(self.td.name, "notes-api")
        self.deeper = os.path.join(self.tree, "web")
        os.makedirs(self.deeper)

    def tearDown(self):
        self.td.cleanup()

    def _with_sessions(self, mapping, fn):
        """Run fn with _tmux_sessions/_cwd_of faked to `mapping` ({sid: dir})."""
        old_t, old_c = km._tmux_sessions, km._cwd_of
        km._tmux_sessions = lambda: {s: {} for s in mapping}
        km._cwd_of = lambda s: mapping.get(s, "")
        try:
            return fn()
        finally:
            km._tmux_sessions, km._cwd_of = old_t, old_c

    def test_longest_prefix_wins(self):
        p = os.path.join(self.deeper, "a.md")
        got = self._with_sessions({SID: self.tree, OTHER: self.deeper},
                                  lambda: km._edit_trace_sid(p, None))
        self.assertEqual(got, OTHER, "the DEEPEST live tree containing the file owns the trace")

    def test_a_tie_prefers_the_viewer_s_session(self):
        p = os.path.join(self.tree, "b.md")
        got = self._with_sessions({SID: self.tree, OTHER: self.tree},
                                  lambda: km._edit_trace_sid(p, SID))
        self.assertEqual(got, SID, "same tree twice → the session whose viewer made the edit")

    def test_outside_every_tree_is_nobody(self):
        p = _mk_text(self.td.name, "loose.md")
        got = self._with_sessions({SID: self.tree}, lambda: km._edit_trace_sid(p, SID))
        self.assertIsNone(got, "an edit outside every worktree concerns no session's thread")

    def test_the_trace_sends_to_the_owner(self):
        p = os.path.join(self.tree, "c.md")
        sent = []

        class _FakeBE:
            def send(self, sid, body):
                sent.append((sid, body))

        old_bf = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: _FakeBE())
        try:
            self._with_sessions({SID: self.tree}, lambda: km._edit_trace(p, None))
        finally:
            km.Sessions.backend_for = old_bf
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], SID)
        self.assertIn("edited", sent[0][1])
        self.assertIn("romp-injected", sent[0][1], "the trace renders as an injected (gray) message")


if __name__ == "__main__":
    unittest.main()
