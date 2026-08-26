#!/usr/bin/env python3
"""_list_dir + the listDir WS op — the dashboard file browser's listing.

The browser shows one directory at a time: files AND directories, sizes, a server-side `viewable`
verdict per file (the same tables /file's view half applies, so the client can mark download-only
rows up front), dirs-first ordering, an explicit hidden toggle, a hard cap with `truncated`+`total`
(no silent caps), and LOUD path-naming errors — never a silent empty list. Resolution is
_resolve_open_path semantics, the same rules /file uses, so a listed path feeds the /file URL
builder unchanged. Deliberately a SIBLING of _dir_completions: the completer's dirs-only picker
semantics stay pinned by tests/test_new_session_dir.py.

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
km = SourceFileLoader("romp_kernel_listdir", os.path.join(BIN, "romp-kernel")).load_module()


class _Tree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "src"))
        os.makedirs(os.path.join(self.tmp, "Docs"))
        for name, body in (("app.py", b"print('hi')\n"), ("README.md", b"# notes-api\n"),
                           ("data.parquet", b"\x00\x01binary"), ("zeta.log", b"line\n")):
            with open(os.path.join(self.tmp, name), "wb") as f:
                f.write(body)
        with open(os.path.join(self.tmp, ".env"), "w") as f:
            f.write("SECRET=1\n")
        os.symlink(os.path.join(self.tmp, "src"), os.path.join(self.tmp, "srclink"))

    def names(self, r):
        return [e["name"] for e in r["entries"]]


class ListDirShape(_Tree):
    def test_dirs_first_then_files_case_insensitive(self):
        r = km._list_dir(self.tmp)
        self.assertNotIn("error", r)
        self.assertEqual(self.names(r), ["Docs", "src", "srclink", "app.py", "data.parquet",
                                         "README.md", "zeta.log"])

    def test_the_base_and_parent_are_tilde_collapsed_and_root_has_no_parent(self):
        r = km._list_dir(self.tmp)
        self.assertEqual(r["base"], km._tilde(self.tmp))
        self.assertEqual(r["parent"], km._tilde(os.path.dirname(self.tmp)))
        self.assertIsNone(km._list_dir("/")["parent"])

    def test_viewable_matches_the_file_routes_own_tables(self):
        # the client marks download-only rows up front instead of letting every click 415
        r = km._list_dir(self.tmp)
        v = {e["name"]: e.get("viewable") for e in r["entries"]}
        self.assertTrue(v["app.py"])
        self.assertTrue(v["README.md"])
        self.assertFalse(v["data.parquet"], "off every view allowlist -> download only")
        self.assertNotIn("viewable", [k for e in r["entries"] if e["isDir"] for k in e if k == "viewable"],
                         "directories carry no viewable verdict — they are navigated, not viewed")

    def test_hidden_entries_only_when_asked(self):
        self.assertNotIn(".env", self.names(km._list_dir(self.tmp)))
        self.assertIn(".env", self.names(km._list_dir(self.tmp, hidden=True)))

    def test_a_symlinked_directory_is_typed_by_its_target_and_marked(self):
        r = km._list_dir(self.tmp)
        link = next(e for e in r["entries"] if e["name"] == "srclink")
        self.assertTrue(link["isDir"], "is_dir follows the link, like the completer")
        self.assertTrue(link["isLink"], "…but the row says it is one")

    def test_the_cap_is_stated_never_silent(self):
        r = km._list_dir(self.tmp, limit=2)
        self.assertTrue(r["truncated"])
        self.assertEqual(len(r["entries"]), 2)
        self.assertEqual(r["total"], 7, "total rides so the client can say what was left out")

    def test_files_carry_sizes_dirs_do_not_pretend_to(self):
        r = km._list_dir(self.tmp)
        by = {e["name"]: e for e in r["entries"]}
        self.assertEqual(by["app.py"]["size"], len(b"print('hi')\n"))
        self.assertEqual(by["src"]["size"], 0)
        self.assertGreater(by["app.py"]["mtime"], 0)


class ListDirErrors(_Tree):
    def test_a_missing_directory_errors_loudly_naming_the_path(self):
        r = km._list_dir(os.path.join(self.tmp, "nope"))
        self.assertIn("error", r)
        self.assertIn("nope", r["error"])
        self.assertIn("not a directory", r["error"])

    def test_an_error_reply_still_carries_a_walkable_trail(self):
        # base/parent ride error replies too, so a FIRST open that fails builds crumbs whose
        # ancestors are clickable — an error over an empty crumb bar was a dead end (review).
        r = km._list_dir(os.path.join(self.tmp, "nope"))
        self.assertEqual(r["base"], km._tilde(os.path.join(self.tmp, "nope")))
        self.assertEqual(r["parent"], km._tilde(self.tmp))

    def test_a_relative_path_without_a_session_errors_instead_of_guessing(self):
        # /file's own rule: a relative path resolves against the sid's cwd; with no sid there is no
        # base to guess from, and a silent fallback would list the wrong machine-state entirely.
        r = km._list_dir("src", sid=None)
        self.assertIn("error", r)
        self.assertIn("no session cwd", r["error"])

    def test_a_relative_path_resolves_against_the_sessions_cwd(self):
        real = km._cwd_of
        km._cwd_of = lambda sid: self.tmp if sid == "11111111-2222-3333-4444-000000000001" else None
        try:
            r = km._list_dir(".", sid="11111111-2222-3333-4444-000000000001")
            self.assertNotIn("error", r)
            self.assertEqual(r["base"], km._tilde(self.tmp))
            self.assertIn("app.py", self.names(r))
        finally:
            km._cwd_of = real


class ListDirWire(_Tree):
    """The WS op through the real dispatcher with a fake client (test_new_session_dir's harness)."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.client = {"app": "feed", "alive": True,
                       "send": lambda s: self.sent.append(json.loads(s))}
        self.handler = object.__new__(km.Handler)

    def send(self, msg):
        km.Handler._dispatch_ws(self.handler, msg, self.client)
        return self.sent[-1] if self.sent else None

    def test_the_reply_echoes_the_request_id_for_the_stale_drop(self):
        r = self.send({"type": "listDir", "path": self.tmp, "reqId": 7})
        self.assertEqual(r["type"], "dirListing")
        self.assertEqual(r["reqId"], 7, "echoed so a reply landing after a newer navigation is dropped")
        self.assertEqual(r["host"], "", "federation stamps a remote reply; local answers empty")
        self.assertIn("entries", r)

    def test_the_hidden_flag_rides_the_wire(self):
        vis = self.send({"type": "listDir", "path": self.tmp, "reqId": 1})
        hid = self.send({"type": "listDir", "path": self.tmp, "reqId": 2, "hidden": True})
        self.assertNotIn(".env", [e["name"] for e in vis["entries"]])
        self.assertIn(".env", [e["name"] for e in hid["entries"]])

    def test_an_error_still_answers_with_the_request_id(self):
        # fail loudly THROUGH the protocol: the client renders the kernel's words, not a blank
        r = self.send({"type": "listDir", "path": self.tmp + "/nope", "reqId": 9})
        self.assertEqual(r["type"], "dirListing")
        self.assertEqual(r["reqId"], 9)
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
