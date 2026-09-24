#!/usr/bin/env python3
"""The ledgers payload's PR slice (the user 2026-08-17): per-node prNums filtered to the session's own
repo, plus the per-session branch / current PR / prs map the Outline pane's chips read.

The split under test: the judge records every PR ref it saw (it has the atoms, not the checkout), and the
KERNEL filters to the session's own repo and stamps `live` (it has the cwd, and therefore the remote).
"""
import os
import subprocess
import tempfile
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_prpayload", os.path.join(BIN, "romp-kernel"))

REPO = "notes-api-org/notes-api"
OTHER = "someone-else/other"


def _pr(num, branch, **kw):
    base = {"num": num, "url": "https://github.com/%s/pull/%d" % (REPO, num), "title": "t",
            "branch": branch, "state": "open", "draft": False, "checksState": "pass",
            "checksFailing": [], "reviewDecision": "approved", "adds": 1, "dels": 0, "files": 1,
            "updatedT": 100}
    base.update(kw)
    return base


# ── _node_pr_nums: the repo filter ───────────────────────────────────────────────────────────────────

def test_node_pr_nums_keeps_only_this_repo():
    nd = {"prRefs": [[REPO, 12], [OTHER, 9], [REPO, 15]]}
    assert km._node_pr_nums(nd, REPO) == [12, 15]


def test_node_pr_nums_without_a_repo_is_empty():
    """No GitHub remote → nothing qualifies, rather than every ref the session happened to mention."""
    assert km._node_pr_nums({"prRefs": [[REPO, 12]]}, "") == []


def test_node_pr_nums_dedupes():
    assert km._node_pr_nums({"prRefs": [[REPO, 12], [REPO, 12]]}, REPO) == [12]


def test_node_pr_nums_with_no_refs():
    assert km._node_pr_nums({}, REPO) == []
    assert km._node_pr_nums({"prRefs": None}, REPO) == []


def test_node_pr_nums_skips_a_malformed_ref():
    """A build pass must never die on one bad stored row."""
    nd = {"prRefs": [["only-one-field"], [REPO, "twelve"], None, [REPO, 12]]}
    assert km._node_pr_nums(nd, REPO) == [12]


# ── _session_pr_slice: the session payload ───────────────────────────────────────────────────────────

def test_slice_names_the_current_branch_s_pr_and_marks_it_live():
    prs = {12: _pr(12, "dev/fix-notes-index")}
    out = km._session_pr_slice(repo=REPO, branch="dev/fix-notes-index", ahead=2, prs=prs, err="",
                               node_nums={12})
    assert out["branch"] == "dev/fix-notes-index"
    assert out["prNum"] == 12
    assert out["prs"]["12"]["live"] is True
    assert out["prError"] is None


def test_live_is_false_without_unpushed_work():
    """live keys on the ahead count — an event — not on whether a turn happens to be open."""
    prs = {12: _pr(12, "dev/fix-notes-index")}
    out = km._session_pr_slice(repo=REPO, branch="dev/fix-notes-index", ahead=0, prs=prs, err="",
                               node_nums={12})
    assert out["prs"]["12"]["live"] is False


def test_live_is_false_on_another_branch_s_pr():
    prs = {12: _pr(12, "dev/fix-notes-index"), 15: _pr(15, "dev/op-values")}
    out = km._session_pr_slice(repo=REPO, branch="dev/op-values", ahead=3, prs=prs, err="",
                               node_nums={12, 15})
    assert out["prs"]["15"]["live"] is True
    assert out["prs"]["12"]["live"] is False
    assert out["prNum"] == 15


def test_only_referenced_and_current_prs_ship():
    """A repo can hold a hundred PRs; the payload carries this session's."""
    prs = {n: _pr(n, "dev/b%d" % n) for n in (1, 2, 3)}
    out = km._session_pr_slice(repo=REPO, branch="dev/b2", ahead=0, prs=prs, err="", node_nums={3})
    assert sorted(out["prs"]) == ["2", "3"]


def test_a_referenced_pr_missing_from_the_hydration_is_simply_absent():
    out = km._session_pr_slice(repo=REPO, branch="", ahead=0, prs={}, err="", node_nums={99})
    assert out["prs"] is None and out["prNum"] is None


def test_an_error_rides_beside_the_last_snapshot():
    """A failed gh read keeps what was known and says why it is not current; the pane renders both."""
    prs = {12: _pr(12, "dev/x")}
    out = km._session_pr_slice(repo=REPO, branch="dev/x", ahead=0, prs=prs, err="gh auth login",
                               node_nums=set())
    assert out["prError"] == "gh auth login"
    assert sorted(out["prs"]) == ["12"] and out["prNum"] == 12


def test_a_first_failed_read_carries_only_the_reason():
    out = km._session_pr_slice(repo=REPO, branch="dev/x", ahead=0, prs={}, err="gh auth login",
                               node_nums=set())
    assert out["prError"] == "gh auth login" and out["prs"] is None


def test_a_reused_branch_shows_its_open_pr_and_only_that_one_is_live():
    """A branch name reused after a closed PR: the open one is the branch's PR, and `live` marks it alone."""
    prs = {4: _pr(4, "dev/x", state="closed"), 9: _pr(9, "dev/x")}
    out = km._session_pr_slice(repo=REPO, branch="dev/x", ahead=1, prs=prs, err="", node_nums={4})
    assert out["prNum"] == 9
    assert out["prs"]["9"]["live"] is True
    assert out["prs"]["4"]["live"] is False


def test_no_repo_yields_an_empty_slice():
    assert km._session_pr_slice(repo="", branch="", ahead=0, prs={}, err="", node_nums=set()) == \
        {"branch": "", "prNum": None, "prs": None, "prError": None}


def test_a_detached_head_has_no_current_pr_but_still_ships_the_goals():
    """Detached is not a branch, so nothing is 'current' — the goals' own PRs still show."""
    prs = {12: _pr(12, "dev/fix-notes-index")}
    out = km._session_pr_slice(repo=REPO, branch="", ahead=0, prs=prs, err="", node_nums={12})
    assert out["prNum"] is None
    assert out["prs"]["12"]["live"] is False


# ── _pr_note_push: the push event ────────────────────────────────────────────────────────────────────

def test_push_event_fires_only_on_a_rise(monkeypatch):
    fired = []
    monkeypatch.setattr(km.gp, "note_push_turn", lambda repo: fired.append(repo))
    km._pr_push_seen.pop("s1", None)
    assert km._pr_note_push("s1", REPO, 2) is False, "first sight is history, not an event"
    assert km._pr_note_push("s1", REPO, 2) is False
    assert km._pr_note_push("s1", REPO, 3) is True
    assert fired == [REPO]


def test_push_event_needs_a_repo(monkeypatch):
    monkeypatch.setattr(km.gp, "note_push_turn", lambda repo: (_ for _ in ()).throw(AssertionError()))
    km._pr_push_seen.pop("s2", None)
    km._pr_note_push("s2", "", 1)
    assert km._pr_note_push("s2", "", 5) is False


# ── the tree the chips actually describe ─────────────────────────────────────────────────────────────

def test_pr_work_dir_prefers_the_edited_tree_over_the_registered_dir(monkeypatch):
    """This repo's convention puts real work on a per-session worktree beside a clone that is DETACHED at a
    release tag. Reading the registered dir there reports no branch at all, so every chip would go dark on
    exactly the setup the pane exists for (found by probing the live sessions: all of them read detached)."""
    monkeypatch.setattr(km, "_session_meta", lambda p: {"lastEditPath": "/w/notes-api-web/api/routes.py"})
    monkeypatch.setattr(km, "_tree_of", lambda d: ("/w/notes-api-web", "dev/notes-index")
                        if d == "/w/notes-api-web/api" else ("", ""))
    monkeypatch.setattr(km, "_cwd_of", lambda sid: "/w/notes-api")
    assert km._pr_work_dir("s1", "/t.jsonl") == "/w/notes-api-web"


def test_pr_work_dir_falls_back_to_the_registered_dir(monkeypatch):
    """Nothing edited yet — the registered dir is all we know, and it is better than nothing."""
    monkeypatch.setattr(km, "_session_meta", lambda p: {"lastEditPath": ""})
    monkeypatch.setattr(km, "_tree_of", lambda d: ("", ""))
    monkeypatch.setattr(km, "_cwd_of", lambda sid: "/w/notes-api")
    assert km._pr_work_dir("s1", "/t.jsonl") == "/w/notes-api"


def test_session_pr_payload_uses_the_detected_worktree(monkeypatch):
    seen = []
    monkeypatch.setattr(km.gp, "repo_of", lambda cwd: (seen.append(cwd), "")[1])
    km._session_pr_payload("s1", None, {"dir": "~/notes-api-web", "branch": "dev/notes-index"})
    assert seen == [os.path.expanduser("~/notes-api-web")], "the ~ form must be expanded before git sees it"


def test_session_pr_payload_without_a_worktree_uses_the_registered_dir(monkeypatch):
    seen = []
    monkeypatch.setattr(km.gp, "repo_of", lambda cwd: (seen.append(cwd), "")[1])
    monkeypatch.setattr(km, "_cwd_of", lambda sid: "/w/notes-api")
    km._session_pr_payload("s1", None, None)
    assert seen == ["/w/notes-api"]


def test_an_empty_owner_ref_belongs_to_this_session_s_repo():
    """The judge reads a bare number out of a `gh pr …` command, which acts on the checkout it ran in — so
    the owner is left empty and resolved here, where the remote is known."""
    assert km._node_pr_nums({"prRefs": [["", 503]]}, REPO) == [503]


def test_an_empty_owner_ref_still_needs_a_repo():
    assert km._node_pr_nums({"prRefs": [["", 503]]}, "") == []


def test_an_empty_owner_ref_dedupes_against_the_explicit_one():
    assert km._node_pr_nums({"prRefs": [[REPO, 503], ["", 503]]}, REPO) == [503]


# ── the payload through a real checkout ──────────────────────────────────────────────────────────────

def _git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _checkout(tmp_path):
    """A synthetic GitHub-origin repo on a feature branch with one commit ahead of its upstream."""
    up = tmp_path / "up"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    for d in (up,):
        _git(d, "config", "user.email", "dev@example.invalid")
        _git(d, "config", "user.name", "Dev")
    (up / "README.md").write_text("notes-api\n")
    _git(up, "add", "README.md")
    _git(up, "commit", "-qm", "init")
    _git(up, "branch", "dev/notes-index")
    work = tmp_path / "notes-api"
    _git(tmp_path, "clone", "-q", "-b", "dev/notes-index", str(up), str(work))
    _git(work, "config", "user.email", "dev@example.invalid")
    _git(work, "config", "user.name", "Dev")
    (work / "a.py").write_text("x = 1\n")
    _git(work, "add", "a.py")
    _git(work, "commit", "-qm", "a")
    _git(work, "remote", "set-url", "origin", "https://github.com/%s.git" % REPO)
    return work


def test_session_pr_payload_reads_a_real_checkout(tmp_path, monkeypatch):
    """The local half runs for real (repo, branch, ahead); only the gh read is stubbed, since the real one
    starts a background thread."""
    work = _checkout(tmp_path)
    asked = []
    prs = {7: _pr(7, "dev/notes-index"), 12: _pr(12, "dev/other")}
    monkeypatch.setattr(km.gp, "repo_prs", lambda repo, nums=(), branch="": (asked.append((repo, set(nums), branch)),
                                                                           (prs, ""))[1])
    monkeypatch.setattr(km, "_cwd_of", lambda sid: str(work))
    ledger = {"tree": [{"prNums": [12]}, {"prNums": None}]}
    out = km._session_pr_payload("s-real", ledger, None)
    assert asked == [(REPO, {12}, "dev/notes-index")]
    assert out["branch"] == "dev/notes-index" and out["prNum"] == 7
    assert out["prs"]["7"]["live"] is True, "one commit ahead of its upstream"
    assert out["prs"]["12"]["live"] is False
    assert km._pr_repo_seen["s-real"] == REPO


def test_the_off_switch_skips_every_read(monkeypatch):
    monkeypatch.setattr(km.gp, "PR_STATUS_OFF", True)
    monkeypatch.setattr(km.gp, "repo_of", lambda cwd: (_ for _ in ()).throw(AssertionError("read")))
    assert km._session_pr_payload("s1", None, None) == km._NO_PR_PAYLOAD


def test_one_session_s_failure_costs_only_its_chips(monkeypatch):
    """The pusher attaches a payload per session; a raise there must not abort the whole push."""
    def boom(*a, **k):
        raise RuntimeError("dictionary changed size during iteration")

    monkeypatch.setattr(km, "_session_pr_payload", boom)
    got = km._safe_pr_payload("s1", None, None)
    assert {k: got[k] for k in ("branch", "prNum", "prs")} == {"branch": "", "prNum": None, "prs": None}
    assert got["prError"] == "PR status could not be built: RuntimeError", "said, never read as no PR"


def test_the_error_chip_retries_the_session_s_repo(monkeypatch):
    retried = []
    monkeypatch.setattr(km.gp, "retry", lambda repo: retried.append(repo))
    km._pr_repo_seen["s-retry"] = REPO
    assert km._pr_retry("s-retry") is True
    assert retried == [REPO]
    assert km._pr_retry("s-unknown") is False, "a session with no repo has nothing to retry"
