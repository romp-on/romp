#!/usr/bin/env python3
"""The chat's pinned "system context" card (the user 2026-06-19): _session_meta scrapes the latest
cwd/gitBranch/version/permissionMode straight from raw transcript records (Claude Code writes no
system:init atom, so the event model drops them); _claudemd_docs collects the global ~/.claude/CLAUDE.md
plus the project CLAUDE.md chain from the git root down to cwd, home-abbreviated. The card is reference-
only — the verbatim harness prompt is never recorded, so it's never included. Synthetic fixtures only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_sysmeta", os.path.join(BIN, "romp-kernel"))


class SessionMeta(unittest.TestCase):
    def test_scrapes_latest_cwd_branch_version_and_user_permission_mode(self):
        recs = [
            {"type": "user", "cwd": "/work/proj", "gitBranch": "main", "version": "1.0.0",
             "permissionMode": "default", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "cwd": "/work/proj", "gitBranch": "feature/x", "version": "1.2.3",
             "message": {"role": "assistant", "content": [], "model": "claude-opus-4-8"}},
            {"type": "user", "cwd": "/work/proj", "gitBranch": "feature/x", "version": "1.2.3",
             "permissionMode": "acceptEdits", "message": {"role": "user", "content": "go"}},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
            path = f.name
        try:
            km._session_meta_cache.clear()
            meta = km._session_meta(path)
        finally:
            os.unlink(path)
        self.assertEqual(meta["cwd"], "/work/proj")
        self.assertEqual(meta["gitBranch"], "feature/x", "the LATEST branch wins")
        self.assertEqual(meta["version"], "1.2.3")
        self.assertEqual(meta["permissionMode"], "acceptEdits", "permissionMode comes from the latest USER record")

    def test_counts_push_and_pr_commands_but_not_mere_mentions(self):
        """pushCount is the event behind re-reading a repo's PR state: a push moves the REMOTE while HEAD
        and branch stay put, so nothing else in a build pass would notice the checks restarting. A command
        that merely CONTAINS the words must not count (the user 2026-08-17)."""
        ids = iter(range(1, 100))

        def bash(cmd):
            tid = "toolu_%d" % next(ids)
            return [{"type": "assistant", "cwd": "/work/proj", "version": "1.2.3",
                     "message": {"role": "assistant", "model": "claude-opus-4-8",
                                 "content": [{"type": "tool_use", "id": tid, "name": "Bash",
                                              "input": {"command": cmd}}]}},
                    {"type": "user", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}]
        recs = [
            *bash("git push -u fork dev/notes-index"),
            *bash("gh pr create --draft --title x"),
            *bash("git status --porcelain"),
            *bash("grep -rn 'git push' docs/"),
            {"type": "assistant", "cwd": "/work/proj", "version": "1.2.3",
             "message": {"role": "assistant", "model": "claude-opus-4-8",
                         "content": [{"type": "tool_use", "name": "Write",
                                      "input": {"file_path": "/work/proj/a.py"}}]}},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
            path = f.name
        try:
            km._session_meta_cache.clear()
            meta = km._session_meta(path)
        finally:
            os.unlink(path)
        self.assertEqual(meta["pushCount"], 2, "the push and the gh pr call, not the status or the grep")
        self.assertEqual(meta["lastEditPath"], "/work/proj/a.py", "the edit scan still works alongside it")

    def test_a_meta_restored_from_an_older_checkpoint_still_counts_pushes(self):
        """A checkpoint written before pushCount existed restores a five-key meta; the step must add the key
        rather than raise into its own guard and leave the count missing for good."""
        old = {"cwd": "/work/proj", "gitBranch": "main", "version": "1.2.3", "permissionMode": "",
               "lastEditPath": ""}
        call = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "git push"}}]}}
        result = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}}
        self.assertEqual(km._session_meta_step(old, call).get("pushCount", 0), 0, "still running")
        self.assertEqual(km._session_meta_step(old, result)["pushCount"], 1, "counted when it lands")
        self.assertEqual(km._session_meta_step(old, result)["pushCount"], 1, "once")

    def test_missing_file_is_empty_not_an_error(self):
        meta = km._session_meta("/no/such/transcript.jsonl")
        self.assertEqual(meta, {"cwd": "", "gitBranch": "", "version": "", "permissionMode": "",
                                "lastEditPath": "", "pushCount": 0, "pushPending": []})

    def test_cache_keys_on_mtime_size(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "cwd": "/a", "message": {"role": "user", "content": "x"}}) + "\n")
            path = f.name
        try:
            km._session_meta_cache.clear()
            first = km._session_meta(path)
            self.assertIs(km._session_meta(path), first, "unchanged file → the SAME cached object")
        finally:
            os.unlink(path)


class ClaudeMdDocs(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.saved_global = km._GLOBAL_CLAUDE_MD

    def tearDown(self):
        km._GLOBAL_CLAUDE_MD = self.saved_global
        self.td.cleanup()

    def test_collects_global_then_project_chain_root_first(self):
        glob = self.root / "global-claude.md"
        glob.write_text("# global rules\n")
        km._GLOBAL_CLAUDE_MD = glob
        proj = self.root / "repo"
        (proj / ".git").mkdir(parents=True)
        (proj / "CLAUDE.md").write_text("# repo root rules\n")
        sub = proj / "pkg" / "mod"
        sub.mkdir(parents=True)
        (sub / "CLAUDE.md").write_text("# subdir rules\n")
        docs = km._claudemd_docs(str(sub))
        scopes = [(d["scope"], d["text"].strip()) for d in docs]
        self.assertEqual(scopes, [
            ("global", "# global rules"),
            ("project", "# repo root rules"),   # git root first (Claude Code load order)
            ("project", "# subdir rules"),      # …down to cwd
        ])

    def test_stops_at_git_root_does_not_walk_above_it(self):
        km._GLOBAL_CLAUDE_MD = self.root / "missing-global.md"   # no global → only the project chain
        above = self.root / "above"
        (above / "CLAUDE.md").parent.mkdir(parents=True)
        (above / "CLAUDE.md").write_text("# should NOT appear (above the git root)\n")
        repo = above / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text("# repo rules\n")
        docs = km._claudemd_docs(str(repo))
        texts = [d["text"].strip() for d in docs]
        self.assertEqual(texts, ["# repo rules"], "the walk includes the git root but never its parents")

    def test_empty_files_are_skipped(self):
        km._GLOBAL_CLAUDE_MD = self.root / "missing.md"
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text("   \n\n")   # whitespace-only → not a doc
        self.assertEqual(km._claudemd_docs(str(repo)), [])

    def test_home_path_is_abbreviated_to_tilde(self):
        # a CLAUDE.md whose path is under $HOME must display as ~/… (no personal absolute path in the UI)
        home = Path(os.path.expanduser("~"))
        km._GLOBAL_CLAUDE_MD = self.root / "missing.md"
        # fabricate a repo under HOME inside a temp dir we control, then point cwd at it
        with tempfile.TemporaryDirectory(dir=str(home)) as hd:
            repo = Path(hd) / "repo"
            (repo / ".git").mkdir(parents=True)
            (repo / "CLAUDE.md").write_text("# rules\n")
            docs = km._claudemd_docs(str(repo))
            self.assertTrue(docs and docs[0]["path"].startswith("~/"),
                            "a path under $HOME is shown tilde-abbreviated, never as the absolute home path")
            self.assertNotIn(str(home), docs[0]["path"])


class Tilde(unittest.TestCase):
    def test_abbreviates_home_prefix_only(self):
        home = os.path.expanduser("~")
        self.assertEqual(km._tilde(home), "~")
        self.assertEqual(km._tilde(os.path.join(home, "a", "b")), "~/a/b")
        self.assertEqual(km._tilde("/etc/passwd"), "/etc/passwd", "non-home paths are untouched")
        self.assertEqual(km._tilde(home + "x/y"), home + "x/y", "only a real $HOME/ boundary abbreviates")


if __name__ == "__main__":
    unittest.main()
