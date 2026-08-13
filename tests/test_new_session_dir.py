#!/usr/bin/env python3
"""The new-session directory: completion, status, and the create-it-or-edit-it fork (the user 2026-07-28).

A session's cwd is fixed at creation, so a wrong path can't be fixed later — it was already rejected up
front. What was missing is everything around that rejection: the picker typed paths blind (a datalist of
past dirs and a native dialog that only ever showed the LOCAL machine), and a missing directory came back
as a toast the "Opening…" cue was covering, so the create looked like it silently did nothing.

Now the kernel that will OWN the session answers three questions over the wire — what does this path
complete to, what IS it, and shall I make it — which is also what makes the field work for a session on a
remote host: federation routes the same ops to that host's kernel, and it reads its own disk.

Synthetic paths in a temp dir only.
"""
import inspect
import json
import os
import tempfile
import threading
import types
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_newdir", os.path.join(BIN, "romp-kernel")).load_module()


class _Dirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: None)   # the temp tree is the OS's to reap; nothing here is precious


class ResolveCreateDir(_Dirs):
    def test_an_existing_directory_resolves(self):
        p, err = km._resolve_create_dir(self.tmp)
        self.assertIsNone(err)
        self.assertEqual(os.path.realpath(p), os.path.realpath(self.tmp))

    def test_blank_is_the_kernel_default(self):
        self.assertEqual(km._resolve_create_dir("")[0], km._default_create_dir())
        self.assertEqual(km._resolve_create_dir(None)[1], None)

    def test_a_missing_directory_is_refused_without_create(self):
        p, err = km._resolve_create_dir(os.path.join(self.tmp, "nope"))
        self.assertIsNone(p)
        self.assertIn("directory not found", err)

    def test_create_makes_the_whole_missing_chain(self):
        target = os.path.join(self.tmp, "a", "b", "c")
        p, err = km._resolve_create_dir(target, create=True)
        self.assertIsNone(err)
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(os.path.realpath(p), os.path.realpath(target))

    def test_a_file_in_the_way_is_never_created_over(self):
        f = os.path.join(self.tmp, "afile")
        open(f, "w").close()
        for create in (False, True):
            p, err = km._resolve_create_dir(f, create=create)
            self.assertIsNone(p)
            self.assertIn("not a directory", err)
        self.assertTrue(os.path.isfile(f), "the file is untouched")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores the mode bits")
    def test_a_creation_that_fails_reports_instead_of_pretending(self):
        blocked = os.path.join(self.tmp, "ro")
        os.makedirs(blocked)
        os.chmod(blocked, 0o500)
        self.addCleanup(os.chmod, blocked, 0o700)
        p, err = km._resolve_create_dir(os.path.join(blocked, "child"), create=True)
        self.assertIsNone(p)
        self.assertIn("could not create", err)


class DirStatus(_Dirs):
    def test_blank_reports_the_default_and_offers_nothing_to_create(self):
        st = km._dir_status("")
        self.assertTrue(st["isDefault"])
        self.assertTrue(st["isDir"])
        self.assertFalse(st["canCreate"])

    def test_an_existing_directory(self):
        st = km._dir_status(self.tmp)
        self.assertTrue(st["exists"])
        self.assertTrue(st["isDir"])
        self.assertFalse(st["isFile"])
        self.assertFalse(st["canCreate"])
        self.assertEqual(st["missing"], 0)

    def test_a_missing_path_names_the_deepest_ancestor_and_how_much_is_missing(self):
        st = km._dir_status(os.path.join(self.tmp, "x", "y", "z"))
        self.assertFalse(st["exists"])
        self.assertTrue(st["canCreate"])
        self.assertEqual(st["missing"], 3)
        self.assertEqual(os.path.realpath(os.path.expanduser(st["nearest"])), os.path.realpath(self.tmp))

    def test_a_file_can_never_be_created_into_a_directory(self):
        f = os.path.join(self.tmp, "afile")
        open(f, "w").close()
        st = km._dir_status(f)
        self.assertTrue(st["isFile"])
        self.assertFalse(st["canCreate"], "no create offer for a path that is already something else")

    def test_tilde_and_vars_expand(self):
        st = km._dir_status("~")
        self.assertTrue(st["isDir"])
        os.environ["ROMP_TEST_DIR"] = self.tmp
        self.addCleanup(os.environ.pop, "ROMP_TEST_DIR", None)
        self.assertTrue(km._dir_status("$ROMP_TEST_DIR")["isDir"])


class DirCompletions(_Dirs):
    def setUp(self):
        super().setUp()
        for d in ("alpha", "album", "beta", ".hidden"):
            os.makedirs(os.path.join(self.tmp, d))
        open(os.path.join(self.tmp, "alfile"), "w").close()

    def names(self, raw, **kw):
        return [i["name"] for i in km._dir_completions(raw, **kw)["items"]]

    def test_a_trailing_slash_lists_the_children(self):
        self.assertEqual(self.names(self.tmp + "/"), ["album", "alpha", "beta"])

    def test_a_fragment_narrows_by_prefix(self):
        self.assertEqual(self.names(os.path.join(self.tmp, "al")), ["album", "alpha"])

    def test_files_are_never_offered(self):
        # a session cwd is a directory; "alfile" matches the prefix and must still not appear
        self.assertNotIn("alfile", self.names(os.path.join(self.tmp, "al")))

    def test_hidden_directories_appear_only_once_the_dot_is_typed(self):
        self.assertNotIn(".hidden", self.names(self.tmp + "/"))
        self.assertEqual(self.names(os.path.join(self.tmp, ".")), [".hidden"])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(self.names(os.path.join(self.tmp, "AL")), ["album", "alpha"])

    def test_the_reply_is_capped_and_says_so(self):
        big = os.path.join(self.tmp, "many")
        for i in range(8):
            os.makedirs(os.path.join(big, "d%d" % i))
        out = km._dir_completions(big + "/", limit=3)
        self.assertEqual(len(out["items"]), 3)
        self.assertTrue(out["truncated"])
        self.assertFalse(km._dir_completions(big + "/", limit=50)["truncated"])

    def test_an_unreadable_or_absent_base_answers_empty_rather_than_raising(self):
        out = km._dir_completions(os.path.join(self.tmp, "nothing-here", "x"))
        self.assertEqual(out["items"], [])
        self.assertFalse(out["truncated"])

    def test_paths_come_back_home_collapsed(self):
        out = km._dir_completions("~/")
        self.assertTrue(out["base"].startswith("~"), out["base"])
        for it in out["items"]:
            self.assertTrue(it["path"].startswith("~/"), it["path"])


class _Wire(_Dirs):
    """The two WS ops, driven through the real dispatcher with a fake client."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.client = {"app": "chat", "alive": True,
                       "send": lambda s: self.sent.append(json.loads(s))}
        self.handler = object.__new__(km.Handler)

    def send(self, msg):
        km.Handler._dispatch_ws(self.handler, msg, self.client)
        return self.sent[-1] if self.sent else None


class DirCompleteOp(_Wire):
    def test_the_reply_carries_the_completions_the_status_and_the_request_id(self):
        r = self.send({"type": "dirComplete", "value": self.tmp + "/", "reqId": 12})
        self.assertEqual(r["type"], "dirCompletions")
        self.assertEqual(r["reqId"], 12, "echoed so a late reply for an older keystroke can be dropped")
        self.assertEqual(r["value"], self.tmp + "/")
        self.assertIn("items", r)
        self.assertTrue(r["status"]["isDir"])


class CreateSessionDirFork(_Wire):
    def setUp(self):
        super().setUp()
        self.spawned = []
        self._real_sdk = km._create_sdk_session
        self._real_ready = km._sdk_ready
        km._create_sdk_session = lambda nm, cwd, auth="", model="", effort="": self.spawned.append((nm, cwd)) or "TESTSID"
        km._sdk_ready = lambda: True
        self.addCleanup(setattr, km, "_create_sdk_session", self._real_sdk)
        self.addCleanup(setattr, km, "_sdk_ready", self._real_ready)

    def test_a_missing_directory_asks_instead_of_warning_into_the_void(self):
        target = os.path.join(self.tmp, "not", "yet")
        r = self.send({"type": "createSession", "name": "web", "dir": target, "backend": "sdk"})
        self.assertEqual(r["type"], "createDirMissing")
        self.assertEqual(r["name"], "web")
        self.assertTrue(r["status"]["canCreate"])
        self.assertEqual(r["status"]["missing"], 2)
        self.assertEqual(self.spawned, [], "nothing is created until the user answers")
        self.assertFalse(os.path.exists(target))

    def test_answering_create_makes_the_directory_and_starts_there(self):
        target = os.path.join(self.tmp, "not", "yet")
        self.send({"type": "createSession", "name": "web", "dir": target, "backend": "sdk", "mkdir": True})
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(os.path.realpath(self.spawned[0][1]), os.path.realpath(target))

    def test_a_file_in_the_way_stays_a_plain_warning_there_is_nothing_to_offer(self):
        f = os.path.join(self.tmp, "afile")
        open(f, "w").close()
        r = self.send({"type": "createSession", "name": "web", "dir": f, "backend": "sdk"})
        self.assertEqual(r["type"], "warn")
        self.assertIn("not a directory", r["text"])

    def test_a_good_directory_still_just_starts(self):
        self.send({"type": "createSession", "name": "web", "dir": self.tmp, "backend": "sdk"})
        self.assertEqual(len(self.spawned), 1)
        self.assertNotIn("createDirMissing", [m.get("type") for m in self.sent])


class NativeDialogAvailability(unittest.TestCase):
    """Browse… (and 📎) reach a dialog on the KERNEL's machine, and that machine may have no way to show
    one. It was osascript-only, so on Linux the click hit a missing binary, the OSError was swallowed, no
    reply came back, and the button sat there looking alive. Ask the desktop what it has; when the answer
    is nothing, say so instead of returning silence."""

    def cmd(self, kind="folder", platform="linux", which=(), env=None):
        have = set(which)
        with mock.patch.object(km.sys, "platform", platform), \
             mock.patch.object(km.shutil, "which", lambda e: ("/usr/bin/" + e) if e in have else None), \
             mock.patch.dict(os.environ, env or {}, clear=False):
            for k in ("DISPLAY", "WAYLAND_DISPLAY"):
                if not (env or {}).get(k):
                    os.environ.pop(k, None)
            return km._dialog_cmd(kind)

    def test_a_headless_machine_has_no_dialog_at_all(self):
        self.assertIsNone(self.cmd(which=("zenity",)),
                          "zenity with no screen to draw on is the same silent nothing in a new hat")
        self.assertIsNone(self.cmd(which=()))

    def test_a_linux_desktop_uses_whichever_picker_it_has(self):
        z = self.cmd(which=("zenity",), env={"DISPLAY": ":0"})
        self.assertEqual(z[0], "zenity")
        self.assertIn("--directory", z)                       # a session cwd is a folder, never a file
        self.assertNotIn("--directory", self.cmd("file", which=("zenity",), env={"DISPLAY": ":0"}))
        k = self.cmd(which=("kdialog",), env={"WAYLAND_DISPLAY": "wayland-0"})
        self.assertEqual(k[0], "kdialog")                     # KDE, and Wayland counts as a desktop
        self.assertIn("--getexistingdirectory", k)
        self.assertIn("--getopenfilename", self.cmd("file", which=("kdialog",), env={"DISPLAY": ":0"}))

    def test_macos_still_uses_osascript_and_needs_no_display_var(self):
        c = self.cmd(platform="darwin", which=())
        self.assertEqual(c[0], "osascript")
        self.assertIn("choose folder", c[-1])
        self.assertIn("choose file", self.cmd("file", platform="darwin", which=())[-1])

    def test_the_capability_is_what_the_ui_is_told(self):
        with mock.patch.object(km, "_dialog_cmd", lambda kind: None):
            self.assertFalse(km._native_dialogs())
        with mock.patch.object(km, "_dialog_cmd", lambda kind: ["zenity"]):
            self.assertTrue(km._native_dialogs())

    def test_a_cancelled_or_failed_dialog_is_None_not_a_crash(self):
        with mock.patch.object(km, "_dialog_cmd", lambda kind: ["picker"]):
            with mock.patch.object(km.subprocess, "run",
                                   lambda *a, **k: types.SimpleNamespace(stdout="  /tmp/chosen \n")):
                self.assertEqual(km._run_dialog("folder"), "/tmp/chosen")
            with mock.patch.object(km.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="\n")):
                self.assertIsNone(km._run_dialog("folder"), "cancelled → empty stdout")
            with mock.patch.object(km.subprocess, "run", mock.Mock(side_effect=OSError("gone"))):
                self.assertIsNone(km._run_dialog("folder"))

    def test_the_reason_names_the_actual_cause_and_what_to_do_instead(self):
        with mock.patch.object(km.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None); os.environ.pop("WAYLAND_DISPLAY", None)
            why = km._no_dialog_why("folder")
            self.assertIn("No folder dialog", why)
            self.assertIn("no desktop session", why)
            self.assertNotIn("zenity", why, "no point telling a headless box to install a picker")
            self.assertIn("Type the folder path", why)
            f = km._no_dialog_why("file")
            self.assertIn("No file dialog", f, "the 📎 refusal is about a FILE, not a folder")
            self.assertIn("paste its path", f)
        with mock.patch.object(km.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False):
            self.assertIn("zenity", km._no_dialog_why("folder"), "a desktop with no picker CAN install one")


class NativeDialogWire(_Wire):
    """What the client sees: the capability up front, and a spoken refusal if a click lands anyway."""

    def test_the_session_list_advertises_whether_browse_can_work(self):
        for cap in (True, False):
            with mock.patch.object(km, "_native_dialogs", lambda c=cap: c):
                self.sent.clear()
                r = self.send({"type": "requestSessions"})
                self.assertEqual(r["type"], "sessionList")
                self.assertIs(r["nativeDialogs"], cap)

    def test_a_click_a_headless_kernel_cannot_serve_is_answered_not_swallowed(self):
        with mock.patch.object(km, "_native_dialogs", lambda: False):
            for msg, said in (({"type": "browseDir"}, "No folder dialog"),
                              ({"type": "browseDir", "target": "gear"}, "No folder dialog"),
                              ({"type": "pickFile"}, "No file dialog")):
                self.sent.clear()
                r = self.send(msg)
                self.assertIsNotNone(r, "silence is the bug: %r returned nothing" % (msg,))
                self.assertEqual(r["type"], "warn")
                self.assertIn(said, r["text"])

    def test_a_kernel_that_can_show_one_says_nothing_and_opens_it(self):
        opened = []
        with mock.patch.object(km, "_native_dialogs", lambda: True), \
             mock.patch.object(km, "_pick_folder", lambda: opened.append("folder") or ""), \
             mock.patch.object(km, "_pick_file", lambda: opened.append("file") or ""):
            for msg in ({"type": "browseDir"}, {"type": "pickFile"}):
                self.sent.clear()
                self.send(msg)
                self.assertEqual([m for m in self.sent if m.get("type") == "warn"], [],
                                 "an available dialog must not be talked about, only shown")
        for t in threading.enumerate():                      # the dialog runs off the message loop
            if t is not threading.current_thread() and t.daemon:
                t.join(timeout=2)
        self.assertEqual(sorted(opened), ["file", "folder"])

    def test_the_gear_learns_the_same_thing_from_its_own_route(self):
        # The gear has no socket — it fetches /version — so the capability rides there too, beside the
        # default directory it already reads.
        src = inspect.getsource(km._version_info)
        self.assertIn('"nativeDialogs": _native_dialogs()', src)
        self.assertIn("v.nativeDialogs", _gear())
        self.assertIn("ddb.style.display", _gear(), "no dialog on this machine → no Browse button")


def _gear():
    return Path(os.path.dirname(HERE), "ui", "webview", "gear.js").read_text()


class HeadlessParity(unittest.TestCase):
    def test_the_new_route_can_create_the_directory_too(self):
        import inspect
        src = inspect.getsource(km.Handler)
        self.assertIn('_resolve_create_dir(b.get("dir"), create=bool(b.get("mkdir")))', src,
                      "`romp new` gets the same create-it answer the dashboard offers")
        self.assertIn('"dirStatus": _dir_status(b.get("dir"))', src,
                      "a headless caller is told WHY, in the same shape the picker reads")


if __name__ == "__main__":
    unittest.main()
