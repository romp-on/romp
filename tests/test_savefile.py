#!/usr/bin/env python3
"""_save_file + the saveFile WS op — the viewer's raw-mode edit (the file browser's slice 2).

THE invariant is optimistic concurrency: agents edit the same trees a human has open in the viewer,
so a save whose baseMtimeNs is older than the disk REFUSES loudly (reload-and-say-so) — never a
merge, never a silent last-writer-wins. The anchor is NANOSECONDS (a whole-second guard let a
same-second agent write slip through — the review's finding) and travels as a STRING (ns exceeds
JS's safe-integer range). Scope is what raw mode faithfully round-trips: _is_text_path names within
the text cap, existing files only, UTF-8 only (the latin-1 fallback would silently re-encode every
non-ASCII byte — review, executed repro). Writes go THROUGH symlinks (os.replace on the link itself
destroyed it), temp-file + os.replace, mode preserved.

Synthetic paths in a temp dir only (the notes-api demo world).
"""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_savefile", os.path.join(BIN, "romp-kernel")).load_module()


class _File(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fp = os.path.join(self.tmp, "app.py")
        with open(self.fp, "w") as f:
            f.write("print('v1')\n")
        os.chmod(self.fp, 0o640)
        self.ns = os.stat(self.fp).st_mtime_ns
        # These tests exercise the SAVE MECHANICS; the consent gate in front of them is opt-in
        # (off by default, the user 2026-08-22) and owns its own file: tests/test_file_editing_gate.py.
        km._set_file_editing(True)

    def tearDown(self):
        km._set_file_editing(False)


class SaveFile(_File):
    def test_a_clean_save_writes_and_returns_the_new_mtime_ns(self):
        mt, err = km._save_file(self.fp, None, "print('v2')\n", self.ns)
        self.assertIsNone(err)
        self.assertEqual(open(self.fp).read(), "print('v2')\n")
        self.assertEqual(mt, os.stat(self.fp).st_mtime_ns)

    def test_the_mode_survives_the_replace(self):
        km._save_file(self.fp, None, "print('v2')\n", self.ns)
        self.assertEqual(os.stat(self.fp).st_mode & 0o777, 0o640)

    def test_a_stale_anchor_refuses_and_names_the_conflict(self):
        # the concurrent-agent case: the disk moved after the viewer loaded — refuse, never merge
        os.utime(self.fp, ns=(self.ns + 5_000_000_000, self.ns + 5_000_000_000))
        mt, err = km._save_file(self.fp, None, "print('mine')\n", self.ns)
        self.assertIsNone(mt)
        self.assertIn("changed on disk", err)
        self.assertEqual(open(self.fp).read(), "print('v1')\n", "the refusal wrote NOTHING")

    def test_a_same_second_agent_write_is_still_caught(self):
        # the review's granularity finding: whole seconds let a same-second write slip the guard;
        # the ns anchor catches a one-nanosecond difference
        os.utime(self.fp, ns=(self.ns + 1, self.ns + 1))
        mt, err = km._save_file(self.fp, None, "print('mine')\n", self.ns)
        self.assertIsNone(mt)
        self.assertIn("changed on disk", err)

    def test_a_failed_replace_leaves_the_original_intact(self):
        real = os.replace
        def boom(a, b):
            raise OSError(28, "No space left on device")
        os.replace = boom
        try:
            mt, err = km._save_file(self.fp, None, "print('v2')\n", self.ns)
        finally:
            os.replace = real
        self.assertIsNone(mt)
        self.assertIn("No space left", err)
        self.assertEqual(open(self.fp).read(), "print('v1')\n", "atomicity: no truncated file")
        self.assertEqual([f for f in os.listdir(self.tmp) if f.startswith(".romp-save-")], [],
                         "the temp file is cleaned up on failure")

    def test_a_symlinked_path_writes_through_the_link(self):
        # os.replace on the link itself replaced the LINK with a regular file: the target never got
        # the edit and the link was destroyed (review)
        link = os.path.join(self.tmp, "app-link.py")
        os.symlink(self.fp, link)
        lns = os.stat(link).st_mtime_ns          # follows the link — the target's ns, same as the viewer saw
        mt, err = km._save_file(link, None, "print('v2')\n", lns)
        self.assertIsNone(err)
        self.assertTrue(os.path.islink(link), "the link survives")
        self.assertEqual(open(self.fp).read(), "print('v2')\n", "the TARGET got the edit")

    def test_binary_names_and_missing_files_and_creates_are_refused(self):
        self.assertIn("not a text file", km._save_file(os.path.join(self.tmp, "x.parquet"),
                                                       None, "data", 0)[1])
        self.assertIn("no such file", km._save_file(os.path.join(self.tmp, "new.py"),
                                                    None, "print()", 0)[1])

    def test_non_string_content_refuses_instead_of_truncating(self):
        # str(None or "") wrote ZERO bytes with a success ack before this check (review)
        mt, err = km._save_file(self.fp, None, None, self.ns)
        self.assertIsNone(mt)
        self.assertIn("no text", err)
        self.assertEqual(open(self.fp).read(), "print('v1')\n")

    def test_a_lone_surrogate_refuses_instead_of_hanging(self):
        mt, err = km._save_file(self.fp, None, "bad \ud800 text", self.ns)
        self.assertIsNone(mt)
        self.assertIn("UTF-8 cannot encode", err)

    def test_a_non_utf8_file_on_disk_refuses_the_reencode(self):
        # /file's latin-1 fallback re-decodes such a file; writing that back as UTF-8 silently
        # rewrote every non-ASCII byte (review, executed repro) — the save refuses instead
        lp = os.path.join(self.tmp, "legacy.log")
        with open(lp, "wb") as f:
            f.write(b"caf\xe9 line\n")
        ns = os.stat(lp).st_mtime_ns
        mt, err = km._save_file(lp, None, "café line\nfixed\n", ns)
        self.assertIsNone(mt)
        self.assertIn("not UTF-8 on disk", err)
        with open(lp, "rb") as f:
            self.assertEqual(f.read(), b"caf\xe9 line\n", "no byte was touched")

    def test_a_garbage_anchor_refuses_instead_of_hanging(self):
        mt, err = km._save_file(self.fp, None, "print('v2')\n", ["nonsense"])
        self.assertIsNone(mt)
        self.assertIn("anchor", err)

    def test_the_text_cap_is_enforced_and_named(self):
        mt, err = km._save_file(self.fp, None, "x" * (km._TEXT_MAX_BYTES + 1), self.ns)
        self.assertIsNone(mt)
        self.assertIn("text cap", err)

    def test_a_relative_path_resolves_against_the_sessions_cwd(self):
        real = km._cwd_of
        km._cwd_of = lambda sid: self.tmp if sid == "11111111-2222-3333-4444-000000000001" else None
        try:
            mt, err = km._save_file("app.py", "11111111-2222-3333-4444-000000000001",
                                    "print('v2')\n", self.ns)
            self.assertIsNone(err)
            self.assertEqual(open(self.fp).read(), "print('v2')\n")
        finally:
            km._cwd_of = real


class SaveFileWire(_File):
    """The WS op through the real dispatcher with a fake client (the listDir harness)."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.client = {"app": "feed", "alive": True,
                       "send": lambda s: self.sent.append(json.loads(s))}
        self.handler = object.__new__(km.Handler)

    def send(self, msg):
        km.Handler._dispatch_ws(self.handler, msg, self.client)
        return self.sent[-1] if self.sent else None

    def test_a_save_acks_with_the_request_id_and_a_string_mtime_ns(self):
        r = self.send({"type": "saveFile", "path": self.fp, "content": "print('v2')\n",
                       "baseMtimeNs": str(self.ns), "reqId": 3})
        self.assertEqual(r["type"], "fileSaved")
        self.assertEqual(r["reqId"], 3)
        self.assertIsInstance(r["mtimeNs"], str,
                              "ns exceeds JS's safe integers — a JSON number would round in the browser")
        self.assertEqual(r["mtimeNs"], str(os.stat(self.fp).st_mtime_ns))

    def test_a_refusal_nacks_with_the_kernels_words(self):
        r = self.send({"type": "saveFile", "path": self.fp, "content": "print('mine')\n",
                       "baseMtimeNs": str(self.ns - 10), "reqId": 4})
        self.assertEqual(r["type"], "fileSaveFailed")
        self.assertEqual(r["reqId"], 4)
        self.assertIn("changed on disk", r["error"])

    def test_a_malformed_frame_still_gets_a_reply(self):
        # exceptions escaping the op left the client hanging on "Saving…" forever (review)
        r = self.send({"type": "saveFile", "path": self.fp, "content": "x",
                       "baseMtimeNs": {"bad": True}, "reqId": 5})
        self.assertEqual(r["type"], "fileSaveFailed")
        self.assertEqual(r["reqId"], 5)


class FileHeaders(unittest.TestCase):
    def test_the_file_route_stamps_the_anchor_headers_on_every_success_path(self):
        import inspect
        src = inspect.getsource(km.Handler._file_preview)
        self.assertIn('self.send_header("Last-Modified", lastmod)', src)
        self.assertIn('self.send_header("X-Romp-Mtime-Ns", mtime_ns)', src)
        self.assertEqual(src.count('"X-Romp-Mtime-Ns": mtime_ns'), 2, "text AND media bodies")
        self.assertIn('"X-Romp-Text-Utf8": u8', src, "the Edit gate knows which decode branch ran")

    def test_the_remote_relay_mirrors_the_anchor_headers_unlike_content_type(self):
        import inspect
        src = inspect.getsource(km.Handler._remote_file)
        self.assertIn('lastmod = resp.getheader("Last-Modified")', src)
        self.assertIn('r_ns = resp.getheader("X-Romp-Mtime-Ns")', src)
        self.assertIn('r_u8 = resp.getheader("X-Romp-Text-Utf8")', src)


if __name__ == "__main__":
    unittest.main()
