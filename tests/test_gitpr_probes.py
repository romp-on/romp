#!/usr/bin/env python3
"""kernel/gitpr.py — the local git probes behind the Outline pane's PR chip (the user 2026-08-17).

Every git/gh shell-out for the PR surface lives in one module, so the read side (build_session) stays a
pure assembler and the one place that talks to the network is the one place that caches and reports its
own failures. These tests cover the LOCAL half: which repo a session sits in, which branch, and how far
ahead of its upstream it is. Real git repos in tmp_path — a mock of git tells us nothing about whether the
argv is right.
"""
import os
import subprocess
import tempfile
from romp_load import load_source
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.realpath(__file__))).parent
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
gp = load_source("romp_gitpr", str(ROOT / "kernel" / "gitpr.py"))


def _run(cwd, *args):
    subprocess.run(list(args), cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo(tmp_path, remote="https://github.com/notes-api-org/notes-api.git"):
    """A synthetic repo with one commit, optionally with a GitHub origin."""
    d = tmp_path / "notes-api"
    d.mkdir()
    _run(d, "git", "init", "-q", "-b", "main")
    _run(d, "git", "config", "user.email", "dev@example.invalid")
    _run(d, "git", "config", "user.name", "Dev")
    (d / "README.md").write_text("notes-api\n")
    _run(d, "git", "add", "README.md")
    _run(d, "git", "commit", "-qm", "init")
    if remote:
        _run(d, "git", "remote", "add", "origin", remote)
    return d


def test_repo_of_an_https_remote(tmp_path):
    assert gp.repo_of(str(_repo(tmp_path))) == "notes-api-org/notes-api"


def test_repo_of_an_ssh_remote(tmp_path):
    d = _repo(tmp_path, remote="git@github.com:notes-api-org/notes-api.git")
    assert gp.repo_of(str(d)) == "notes-api-org/notes-api"


def test_repo_of_an_ssh_host_alias(tmp_path):
    """A per-account host alias (github.com-personal) is still github.com — the identity machinery is
    the user's business, not this module's."""
    d = _repo(tmp_path, remote="git@github.com-personal:notes-api-org/notes-api.git")
    assert gp.repo_of(str(d)) == "notes-api-org/notes-api"


def test_repo_of_a_non_github_remote_is_empty(tmp_path):
    d = _repo(tmp_path, remote="https://git.example.invalid/notes-api.git")
    assert gp.repo_of(str(d)) == ""


def test_repo_of_a_repo_with_no_remote_is_empty(tmp_path):
    assert gp.repo_of(str(_repo(tmp_path, remote=None))) == ""


def test_repo_of_a_non_repo_is_empty(tmp_path):
    assert gp.repo_of(str(tmp_path)) == ""


def test_repo_of_a_missing_directory_is_empty():
    assert gp.repo_of("/nonexistent/notes-api") == ""


def test_repo_of_sees_an_origin_added_after_a_miss(tmp_path):
    """The memo is keyed on the config file's mtime, so a remote added later is read, not the cached ''."""
    d = _repo(tmp_path, remote=None)
    assert gp.repo_of(str(d)) == ""
    _run(d, "git", "remote", "add", "origin", "https://github.com/notes-api-org/notes-api.git")
    assert gp.repo_of(str(d)) == "notes-api-org/notes-api"


def test_repo_of_a_directory_that_becomes_a_repo(tmp_path):
    d = tmp_path / "notes-api"
    d.mkdir()
    assert gp.repo_of(str(d)) == ""
    _run(d, "git", "init", "-q", "-b", "main")
    _run(d, "git", "remote", "add", "origin", "git@github.com:notes-api-org/notes-api.git")
    assert gp.repo_of(str(d)) == "notes-api-org/notes-api"


def _clone(tmp_path):
    """A clone of a synthetic upstream, so its branch has an upstream to be ahead of."""
    up = _repo(tmp_path)
    clone = tmp_path / "clone"
    _run(tmp_path, "git", "clone", "-q", str(up), str(clone))
    _run(clone, "git", "config", "user.email", "dev@example.invalid")
    _run(clone, "git", "config", "user.name", "Dev")
    return up, clone


def _commit(d, name):
    (d / (name + ".txt")).write_text(name + "\n")
    _run(d, "git", "add", name + ".txt")
    _run(d, "git", "commit", "-qm", name)


def test_local_state_names_the_branch(tmp_path):
    d = _repo(tmp_path)
    _run(d, "git", "checkout", "-q", "-b", "dev/fix-notes-index")
    assert gp.local_state(str(d))[:2] == ("dev/fix-notes-index", 0)


def test_local_state_from_a_subdirectory(tmp_path):
    d = _repo(tmp_path)
    (d / "src").mkdir()
    assert gp.local_state(str(d / "src"))[0] == "main"


def test_local_state_of_a_detached_head_has_no_branch(tmp_path):
    """Detached is not a branch: nothing to open or attribute a PR to."""
    d = _repo(tmp_path)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(d), capture_output=True,
                         text=True).stdout.strip()
    _run(d, "git", "checkout", "-q", sha)
    assert gp.local_state(str(d))[:2] == ("", 0)


def test_local_state_outside_a_repo(tmp_path):
    assert gp.local_state(str(tmp_path)) == ("", 0, False)


def test_local_state_in_a_worktree(tmp_path):
    """A worktree's .git is a pointer file; its refs live in the shared dir."""
    _up, clone = _clone(tmp_path)
    wt = tmp_path / "wt"
    _run(clone, "git", "worktree", "add", "-q", "-b", "dev/op-values", str(wt))
    _commit(wt, "a")
    assert gp.local_state(str(wt))[:2] == ("dev/op-values", 0), "no upstream yet"


def test_local_state_counts_unpushed_commits_and_reports_the_move(tmp_path):
    _up, clone = _clone(tmp_path)
    assert gp.local_state(str(clone)) == ("main", 0, False), "first sight is history, not a move"
    _commit(clone, "a")
    _commit(clone, "b")
    assert gp.local_state(str(clone)) == ("main", 2, True)
    assert gp.local_state(str(clone)) == ("main", 2, False)


def test_local_state_sees_the_upstream_move(tmp_path):
    """A push or fetch moves the upstream ref with HEAD unchanged; that is an event too."""
    up, clone = _clone(tmp_path)
    _commit(clone, "a")
    assert gp.local_state(str(clone))[1] == 1
    _commit(up, "b")
    _run(clone, "git", "fetch", "-q", "origin")
    assert gp.local_state(str(clone)) == ("main", 1, True)


def test_an_unchanged_checkout_costs_no_fork(tmp_path, monkeypatch):
    """The pusher builds every session each cycle; an unchanged checkout must cost stats, not forks."""
    _up, clone = _clone(tmp_path)
    _commit(clone, "a")
    gp.repo_of(str(clone))
    gp.local_state(str(clone))
    forks = []
    real = gp.subprocess.run
    monkeypatch.setattr(gp.subprocess, "run", lambda *a, **k: (forks.append(a[0]), real(*a, **k))[1])
    for _ in range(5):
        gp.repo_of(str(clone))
        assert gp.local_state(str(clone)) == ("main", 1, False)
    assert forks == []


def test_commits_ahead_of_main(tmp_path):
    """'this branch carries work' — measured against origin/main, which is what a PR would target."""
    up = _repo(tmp_path)
    clone = tmp_path / "clone"
    _run(tmp_path, "git", "clone", "-q", str(up), str(clone))
    _run(clone, "git", "config", "user.email", "dev@example.invalid")
    _run(clone, "git", "config", "user.name", "Dev")
    assert gp.commits_ahead_of_main(str(clone)) == 0
    _run(clone, "git", "checkout", "-q", "-b", "dev/op-values")
    (clone / "a.txt").write_text("a\n")
    _run(clone, "git", "add", "a.txt")
    _run(clone, "git", "commit", "-qm", "one")
    assert gp.commits_ahead_of_main(str(clone)) == 1


def test_commits_ahead_of_main_without_an_origin_main_is_zero(tmp_path):
    """Unknowable reads as 'nothing to open a PR for', never as 'open one anyway'."""
    assert gp.commits_ahead_of_main(str(_repo(tmp_path))) == 0


def test_a_torn_pointer_file_reads_as_no_repo(tmp_path):
    """A `.git` pointer file with bytes that are not text is an absence, never a raise into the push."""
    d = tmp_path / "wt"
    d.mkdir()
    (d / ".git").write_bytes(b"\xff\xfe gitdir")
    assert gp.git_dirs(str(d)) is None
    assert gp.repo_of(str(d)) == ""
    assert gp.local_state(str(d)) == ("", 0, False)


def test_repo_of_a_fork_clone_reads_upstream(tmp_path):
    """In the fork layout `origin` is the fork; PRs live on the canonical repo `upstream` names."""
    d = _repo(tmp_path, remote="git@github.com:someone/notes-api.git")
    _run(d, "git", "remote", "add", "upstream", "https://github.com/notes-api-org/notes-api.git")
    assert gp.repo_of(str(d)) == "notes-api-org/notes-api"


def test_the_push_matcher_reads_past_wrappers_and_global_options():
    for cmd in ("git -C notes-api push", "env FOO=1 git push", "time git push", "command git push",
                "gh --repo a/b pr create", "gh -R a/b pr merge 5"):
        assert gp.is_push_command(cmd), cmd
    assert not gp.is_push_command("grep -rn 'a; git push' docs/"), "a separator inside quotes is text"
