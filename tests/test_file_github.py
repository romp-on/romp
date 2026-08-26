#!/usr/bin/env python3
"""_file_github_url + the fileGitLink WS op — the viewer's GitHub link (the user 2026-08-15).

An empty url is a VERDICT, not an error: untracked files, non-repo paths, and non-GitHub origins
honestly have no link, and the viewer never shows the button. The ref is the current branch (what a
human expects to read), or the sha when HEAD is detached. Real temp git repos, synthetic names only
(TESTORG / notes-api — the demo world).
"""
import json
import os
import subprocess
import tempfile
import time
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
km = SourceFileLoader("romp_kernel_github", os.path.join(BIN, "romp-kernel")).load_module()


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@testhost", "-c", "user.name=t"] + list(args),
                   cwd=cwd, check=True, capture_output=True)


class _Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _git("init", "-q", "-b", "main", cwd=self.tmp)
        os.makedirs(os.path.join(self.tmp, "src", "deep dir"))
        self.fp = os.path.join(self.tmp, "src", "app.py")
        with open(self.fp, "w") as f:
            f.write("print('hi')\n")
        self.spaced = os.path.join(self.tmp, "src", "deep dir", "notes file.md")
        with open(self.spaced, "w") as f:
            f.write("# notes\n")
        with open(os.path.join(self.tmp, "loose.txt"), "w") as f:
            f.write("untracked\n")
        _git("add", "src", cwd=self.tmp)
        _git("commit", "-q", "-m", "init", cwd=self.tmp)


class GitHubUrl(_Repo):
    def test_the_ssh_remote_spelling_builds_the_blob_url(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_the_https_remote_spelling_builds_the_same_url(self):
        _git("remote", "add", "origin", "https://github.com/TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_path_segments_are_quoted_but_slashes_survive(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.spaced, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/deep%20dir/notes%20file.md")

    def test_a_slashed_branch_name_stays_literal_like_githubs_own_urls(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        _git("checkout", "-q", "-b", "feat/deep-work", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/feat/deep-work/src/app.py")

    def test_a_detached_head_links_the_sha(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp,
                             capture_output=True, text=True).stdout.strip()
        _git("checkout", "-q", sha, cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/%s/src/app.py" % sha)

    def test_a_symlinked_path_prefix_still_links(self):
        # executed repro: git reports the PHYSICAL toplevel, so a logical path through a symlink
        # escaped relpath and silently un-linked every tracked file behind one
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        outer = tempfile.mkdtemp()
        link = os.path.join(outer, "via-link")
        os.symlink(self.tmp, link)
        self.assertEqual(km._file_github_url(os.path.join(link, "src", "app.py"), None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_dotdot_through_a_symlink_never_links_the_wrong_file(self):
        # executed repro: a LEXICAL '..' collapse linked a different file than the bytes the viewer
        # shows; realpath resolves the symlink first, and the escape gets the honest no-link
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        os.symlink(tempfile.mkdtemp(), os.path.join(self.tmp, "ext"))
        self.assertEqual(
            km._file_github_url(os.path.join(self.tmp, "ext", "..", "src", "app.py"), None), "",
            "the OS would read outside the repo — a wrong link is worse than none")

    def test_port_bearing_and_ssh_over_https_origins_link(self):
        # GitHub's own SSH-over-HTTPS doc writes ssh://git@ssh.github.com:443/OWNER/REPO.git
        for url in ("ssh://git@ssh.github.com:443/TESTORG/notes-api.git",
                    "ssh://git@github.com:22/TESTORG/notes-api.git",
                    "https://github.com:443/TESTORG/notes-api.git"):
            m = km._GITHUB_REMOTE.match(url)
            self.assertIsNotNone(m, url)
            self.assertEqual((m.group(1), m.group(2)), ("TESTORG", "notes-api"), url)
        for url in ("git@github.example.com:TESTORG/notes-api.git",
                    "https://github.com.evil.io/TESTORG/notes-api.git"):
            self.assertIsNone(km._GITHUB_REMOTE.match(url), url)

    def test_a_root_file_named_with_leading_dots_still_links(self):
        # the escape guard tests the path relation, never a name prefix
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        dd = os.path.join(self.tmp, "..cfg")
        with open(dd, "w") as f:
            f.write("k=v\n")
        _git("add", "--", "..cfg", cwd=self.tmp)
        _git("commit", "-q", "-m", "cfg", cwd=self.tmp)
        self.assertEqual(km._file_github_url(dd, None),
                         "https://github.com/TESTORG/notes-api/blob/main/..cfg")

    def test_no_link_verdicts_untracked_nonrepo_and_nongithub(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(os.path.join(self.tmp, "loose.txt"), None), "",
                         "untracked file — no link to a thing not there")
        self.assertEqual(km._file_github_url("/tmp", None), "", "not a repo")
        _git("remote", "set-url", "origin", "git@gitlab.example.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None), "", "origin is not GitHub")

    def test_a_relative_path_resolves_against_the_sessions_cwd(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        real = km._cwd_of
        km._cwd_of = lambda sid: self.tmp if sid == "11111111-2222-3333-4444-000000000001" else None
        try:
            self.assertEqual(km._file_github_url("src/app.py", "11111111-2222-3333-4444-000000000001"),
                             "https://github.com/TESTORG/notes-api/blob/main/src/app.py")
            self.assertEqual(km._file_github_url("src/app.py", None), "", "no sid, no base — no guess")
        finally:
            km._cwd_of = real


class GitLinkWire(_Repo):
    """The WS op through the real dispatcher. The op answers on a THREAD (three git subprocesses must
    not block the recv loop), so the harness waits for the reply instead of reading it synchronously."""

    def setUp(self):
        super().setUp()
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.sent = []
        self.client = {"app": "feed", "alive": True,
                       "send": lambda s: self.sent.append(json.loads(s))}
        self.handler = object.__new__(km.Handler)

    def send_and_wait(self, msg, timeout=10):
        km.Handler._dispatch_ws(self.handler, msg, self.client)
        deadline = time.time() + timeout
        while not self.sent and time.time() < deadline:
            time.sleep(0.02)
        return self.sent[-1] if self.sent else None

    def test_the_reply_echoes_the_request_id_with_the_url(self):
        r = self.send_and_wait({"type": "fileGitLink", "path": self.fp, "reqId": 6})
        self.assertIsNotNone(r, "the threaded op must still always reply")
        self.assertEqual(r["type"], "fileGitLink")
        self.assertEqual(r["reqId"], 6, "echoed so a reply landing after a newer open is dropped")
        self.assertEqual(r["url"], "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_the_no_link_verdict_still_replies(self):
        r = self.send_and_wait({"type": "fileGitLink", "path": os.path.join(self.tmp, "loose.txt"),
                                "reqId": 7})
        self.assertEqual(r["url"], "", "an empty url is the verdict, never a dropped reply")


if __name__ == "__main__":
    unittest.main()
