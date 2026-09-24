#!/usr/bin/env python3
"""kernel/gitpr.py — turning `gh` output into the PR payload the Outline chip renders, and the
event-keyed cache in front of it (the user 2026-08-17).

gh is stubbed here (a real network call in a test suite is a flake), but the SHAPE it returns is gh's
own `--json` vocabulary, so the normalizer is tested against the words GitHub actually sends.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
from romp_load import load_source
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.realpath(__file__))).parent
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
gp = load_source("romp_gitpr_hydrate", str(ROOT / "kernel" / "gitpr.py"))

REPO = "notes-api-org/notes-api"

OPEN_PASSING = {
    "number": 12, "title": "notes: index rebuild", "url": "https://github.com/%s/pull/12" % REPO,
    "headRefName": "dev/fix-notes-index", "state": "OPEN", "isDraft": False,
    "reviewDecision": "APPROVED", "updatedAt": "2026-08-17T10:00:00Z",
    "additions": 64, "deletions": 7, "changedFiles": 2,
    "statusCheckRollup": [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED"}],
}
DRAFT_FAILING = {
    "number": 15, "title": "notes: op values", "url": "https://github.com/%s/pull/15" % REPO,
    "headRefName": "dev/op-values", "state": "OPEN", "isDraft": True,
    "reviewDecision": None, "updatedAt": "2026-08-17T10:05:00Z",
    "additions": 12, "deletions": 0, "changedFiles": 1,
    "statusCheckRollup": [{"name": "pytest", "conclusion": "FAILURE", "status": "COMPLETED"},
                          {"name": "mypy", "conclusion": None, "status": "IN_PROGRESS"}],
}
MERGED = {
    "number": 9, "title": "notes: trashed link", "url": "https://github.com/%s/pull/9" % REPO,
    "headRefName": "dev/trashed-link", "state": "MERGED", "isDraft": False,
    "reviewDecision": "APPROVED", "updatedAt": "2026-08-17T09:00:00Z",
    "additions": 3, "deletions": 3, "changedFiles": 1, "statusCheckRollup": [],
}


def _stub_gh(monkeypatch, payload, code=0, stderr=""):
    """Replace the subprocess gitpr uses, recording every argv it builds."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr=stderr)

    monkeypatch.setattr(gp.subprocess, "run", fake_run)
    return calls


# ── normalize ────────────────────────────────────────────────────────────────────────────────────────

def test_open_and_passing():
    pr = gp.normalize(OPEN_PASSING)
    assert (pr["num"], pr["state"], pr["draft"]) == (12, "open", False)
    assert pr["checksState"] == "pass" and pr["checksFailing"] == []
    assert pr["reviewDecision"] == "approved"
    assert pr["branch"] == "dev/fix-notes-index"
    assert (pr["adds"], pr["dels"], pr["files"]) == (64, 7, 2)
    assert pr["updatedT"] > 0


def test_a_failure_outranks_a_still_running_check():
    """The worst KNOWN state is what the chip must show: 'running' would read as nothing wrong yet."""
    pr = gp.normalize(DRAFT_FAILING)
    assert pr["draft"] is True
    assert pr["checksState"] == "fail"
    assert pr["checksFailing"] == ["pytest"]
    assert pr["reviewDecision"] == "none"


def test_merged_with_no_checks():
    pr = gp.normalize(MERGED)
    assert pr["state"] == "merged" and pr["checksState"] == "none"


def test_running_only():
    raw = dict(OPEN_PASSING, statusCheckRollup=[{"name": "pytest", "conclusion": None,
                                                 "status": "IN_PROGRESS"}])
    assert gp.normalize(raw)["checksState"] == "running"


def test_a_cancelled_or_timed_out_check_counts_as_failing():
    for concl in ("CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"):
        raw = dict(OPEN_PASSING, statusCheckRollup=[{"name": "pytest", "conclusion": concl,
                                                    "status": "COMPLETED"}])
        assert gp.normalize(raw)["checksState"] == "fail", concl


def test_a_neutral_or_skipped_check_is_not_a_failure():
    raw = dict(OPEN_PASSING, statusCheckRollup=[{"name": "lint", "conclusion": "SKIPPED",
                                                 "status": "COMPLETED"}])
    assert gp.normalize(raw)["checksState"] == "pass"


def test_an_unparseable_timestamp_yields_no_age_rather_than_a_wrong_one():
    assert gp.normalize(dict(OPEN_PASSING, updatedAt="whenever"))["updatedT"] == 0


def test_failing_names_are_capped():
    many = [{"name": "c%d" % i, "conclusion": "FAILURE", "status": "COMPLETED"} for i in range(20)]
    assert len(gp.normalize(dict(OPEN_PASSING, statusCheckRollup=many))["checksFailing"]) == 6


# ── hydrate ──────────────────────────────────────────────────────────────────────────────────────────

def test_hydrate_shells_out_once_and_keys_by_number(monkeypatch):
    calls = _stub_gh(monkeypatch, [OPEN_PASSING, DRAFT_FAILING, MERGED])
    prs = gp.hydrate(REPO)
    assert len(calls) == 1, "one gh call per repo, not per PR"
    assert sorted(prs) == [9, 12, 15]
    assert prs[15]["checksFailing"] == ["pytest"]
    assert "--repo" in calls[0] and REPO in calls[0]


def test_hydrate_raises_with_gh_s_own_reason(monkeypatch):
    _stub_gh(monkeypatch, "", code=4, stderr="gh: To use GitHub CLI, run: gh auth login\n")
    try:
        gp.hydrate(REPO)
    except gp.GitPrError as e:
        assert "gh auth login" in str(e)
    else:
        raise AssertionError("expected GitPrError")


def test_hydrate_raises_when_gh_is_missing(monkeypatch):
    def fake_run(argv, **kw):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(gp.subprocess, "run", fake_run)
    try:
        gp.hydrate(REPO)
    except gp.GitPrError as e:
        assert "gh" in str(e)
    else:
        raise AssertionError("expected GitPrError")


def test_hydrate_raises_on_output_that_is_not_json(monkeypatch):
    _stub_gh(monkeypatch, "not json at all")
    try:
        gp.hydrate(REPO)
    except gp.GitPrError:
        pass
    else:
        raise AssertionError("expected GitPrError")


def test_hydrate_one_fetches_a_single_pr(monkeypatch):
    calls = _stub_gh(monkeypatch, OPEN_PASSING)
    pr = gp.hydrate_one(REPO, 12)
    assert pr["num"] == 12
    assert "view" in calls[0]


# ── the cache ────────────────────────────────────────────────────────────────────────────────────────

def _drain():
    """Wait for the background refresh to land — repo_prs is non-blocking by design."""
    for _ in range(200):
        with gp._LOCK:
            busy = bool(gp._INFLIGHT)
        if not busy:
            return
        time.sleep(0.01)
    raise AssertionError("background refresh never finished")


def _reset():
    for memo in (gp._CACHE, gp._GEN, gp._WANTED, gp._BRANCHES, gp._TRIED, gp._POLLED):
        memo.clear()


def _bare(raw):
    """A row the way the list query returns it: no rollup, so checks are "unknown"."""
    return gp.normalize({k: v for k, v in raw.items() if k != "statusCheckRollup"})


def _checks_stub(monkeypatch, conclusion="FAILURE"):
    """Stub the per-PR gh calls: a checks rollup for any number, recording the numbers asked."""
    asked = []

    def fake(args, what):
        asked.append(int(args[2]))
        return {"number": int(args[2]),
                "statusCheckRollup": [{"name": "pytest", "conclusion": conclusion, "status": "COMPLETED"}]}

    monkeypatch.setattr(gp, "_gh_json", fake)
    return asked


def test_repo_prs_never_blocks_and_fills_in_behind(monkeypatch):
    """gh is a network call reached from the per-push build pass, so it is never called inline: the first
    read serves what it has (nothing) and schedules the work."""
    _reset()
    started = []
    monkeypatch.setattr(gp, "hydrate", lambda repo: (started.append(repo),
                                                     {12: gp.normalize(OPEN_PASSING)})[1])
    prs, err = gp.repo_prs(REPO)
    assert prs == {} and err == "", "first read must not wait for gh"
    _drain()
    prs, err = gp.repo_prs(REPO)
    assert sorted(prs) == [12] and err == ""
    assert started == [REPO]


def test_repo_prs_caches_until_invalidated(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(gp, "hydrate", lambda repo: (calls.append(repo),
                                                     {12: gp.normalize(OPEN_PASSING)})[1])
    gp.repo_prs(REPO); _drain()
    gp.repo_prs(REPO); gp.repo_prs(REPO)
    assert len(calls) == 1, "a fresh cache is served without touching gh"
    gp.invalidate(REPO)
    gp.repo_prs(REPO); _drain()
    assert len(calls) == 2


def test_only_one_refresh_per_repo_is_in_flight(monkeypatch):
    """A burst of pushes must not fan out into a pile of concurrent gh calls."""
    _reset()
    calls = []
    gate = threading.Event()

    def slow(repo):
        calls.append(repo)
        gate.wait(2)
        return {}

    monkeypatch.setattr(gp, "hydrate", slow)
    for _ in range(5):
        gp.repo_prs(REPO)
    gate.set()
    _drain()
    assert len(calls) == 1


def test_an_invalidation_during_a_refresh_is_not_lost(monkeypatch):
    """A push landing while the list read is in flight: that read predates the push, so it publishes
    stale and the next read re-fetches."""
    _reset()
    calls, gate = [], threading.Event()

    def slow(repo):
        calls.append(repo)
        gate.wait(2)
        return {12: gp.normalize(OPEN_PASSING)}

    monkeypatch.setattr(gp, "hydrate", slow)
    gp.repo_prs(REPO)
    gp.note_push_turn(REPO)
    gate.set()
    _drain()
    assert gp._CACHE[REPO]["fresh"] is False
    gp.repo_prs(REPO); _drain()
    assert len(calls) == 2
    assert gp._CACHE[REPO]["fresh"] is True


def test_a_refresh_never_mutates_the_published_dict(monkeypatch):
    """A reader holding the published PR dict must never see it change size: a refresh assembles its
    own dict (cited PRs outside the window, checks) and swaps it in whole."""
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: _bare(OPEN_PASSING)})
    gp.repo_prs(REPO); _drain()
    published = gp.repo_prs(REPO)[0]
    before = {n: dict(pr) for n, pr in published.items()}
    monkeypatch.setattr(gp, "hydrate_one", lambda repo, n: gp.normalize(dict(MERGED, number=n)))
    _checks_stub(monkeypatch)
    gp.repo_prs(REPO, nums=[3, 12]); _drain()
    assert published == before, "the dict a reader held is untouched"
    assert sorted(gp.repo_prs(REPO)[0]) == [3, 12], "the new one carries the cited PR"


def test_a_failed_refresh_keeps_the_last_good_snapshot_and_surfaces_the_reason(monkeypatch):
    """PR state is a snapshot: show what we knew with the error beside it, and keep polling to recover."""
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: gp.normalize(OPEN_PASSING)})
    gp.repo_prs(REPO); _drain()

    def boom(repo):
        raise gp.GitPrError("HTTP 502: 502 Bad Gateway")

    monkeypatch.setattr(gp, "hydrate", boom)
    gp.invalidate(REPO)
    gp.repo_prs(REPO); _drain()
    prs, err = gp.repo_prs(REPO)
    assert sorted(prs) == [12], "the known PR is still shown"
    assert "502" in err, "and the failure is visible, not swallowed"
    assert gp.needs_poll(REPO) is True, "a failed read is retried by the poll"


def test_a_first_refresh_that_fails_serves_nothing_plus_the_reason(monkeypatch):
    _reset()

    def boom(repo):
        raise gp.GitPrError("gh auth login")

    monkeypatch.setattr(gp, "hydrate", boom)
    gp.repo_prs(REPO); _drain()
    prs, err = gp.repo_prs(REPO)
    assert prs == {} and "gh auth login" in err


def test_retry_re_reads_and_forgets_unresolvable_numbers(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {})
    asked = []

    def gone(repo, n):
        asked.append(n)
        raise gp.GitPrError("no pull requests found")

    monkeypatch.setattr(gp, "hydrate_one", gone)
    gp.repo_prs(REPO, nums=[4]); _drain()
    gp.invalidate(REPO)
    gp.repo_prs(REPO, nums=[4]); _drain()
    assert asked == [4], "a number gh could not return is not re-fetched on every refresh"
    gp.retry(REPO)
    gp.repo_prs(REPO, nums=[4]); _drain()
    assert asked == [4, 4], "a user retry asks again"


def test_repo_prs_with_no_repo_is_a_silent_empty():
    assert gp.repo_prs("") == ({}, "")


def test_checks_are_fetched_only_for_the_referenced_prs(monkeypatch):
    """The rollup is excluded from the list query because it times GitHub out across 100 PRs, so checks
    are fetched for the handful a session references."""
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: _bare(OPEN_PASSING), 15: _bare(DRAFT_FAILING)})
    asked = _checks_stub(monkeypatch)
    gp.repo_prs(REPO, nums=[15]); _drain()
    prs, _ = gp.repo_prs(REPO)
    assert asked == [15], "only the referenced PR is fetched"
    assert prs[15]["checksState"] == "fail" and prs[15]["checksFailing"] == ["pytest"]
    assert prs[12]["checksState"] == "unknown", "the unreferenced one is left alone"


def test_the_branch_s_own_pr_gets_its_checks_first(monkeypatch):
    """The session row's chip is the branch's PR, which no goal need cite; its checks come first, ahead of
    the bound that trims the cited list."""
    _reset()
    rows = {n: _bare(dict(OPEN_PASSING, number=n, headRefName="dev/n%d" % n)) for n in range(1, 30)}
    monkeypatch.setattr(gp, "hydrate", lambda repo: dict(rows))
    asked = _checks_stub(monkeypatch)
    gp.repo_prs(REPO, nums=range(1, 20), branch="dev/n25"); _drain()
    assert asked[0] == 25
    assert len(asked) == gp._MAX_CHECK_FETCHES


def test_numbers_cited_by_a_second_session_are_not_lost(monkeypatch):
    """Two sessions on one repo, the second citing while the first's refresh runs: its number is fetched
    by a later refresh, and stays in the set once fetched."""
    _reset()
    gate = threading.Event()

    def slow(repo):
        gate.wait(2)
        return {12: _bare(OPEN_PASSING)}

    monkeypatch.setattr(gp, "hydrate", slow)
    monkeypatch.setattr(gp, "hydrate_one", lambda repo, n: gp.normalize(dict(MERGED, number=n)))
    _checks_stub(monkeypatch)
    gp.repo_prs(REPO, nums=[12])
    gp.repo_prs(REPO, nums=[3])
    gate.set(); _drain()
    gp.repo_prs(REPO); _drain()
    assert sorted(gp.repo_prs(REPO)[0]) == [3, 12]
    gp.invalidate(REPO)
    gp.repo_prs(REPO, nums=[12]); _drain()
    assert 3 in gp.repo_prs(REPO)[0], "cumulative: a later refresh keeps the older citation"


def test_branch_pr_prefers_an_open_pr_then_the_newest():
    prs = {4: {"branch": "dev/x", "state": "closed"}, 9: {"branch": "dev/x", "state": "open"},
           7: {"branch": "dev/x", "state": "open"}, 11: {"branch": "dev/x", "state": "merged"}}
    assert gp.branch_pr(prs, "dev/x") == 9
    assert gp.branch_pr({4: prs[4], 11: prs[11]}, "dev/x") == 11
    assert gp.branch_pr(prs, "dev/y") is None
    assert gp.branch_pr(prs, "") is None


def test_unknown_is_distinct_from_none():
    """"we have not asked" must never collapse into "this PR has no checks" — one is missing data, the
    other is a fact, and only the fact may read as green."""
    assert _bare(OPEN_PASSING)["checksState"] == "unknown"
    assert gp.normalize(dict(OPEN_PASSING, statusCheckRollup=[]))["checksState"] == "none"


def test_needs_poll_only_while_a_check_runs(monkeypatch):
    _reset()
    running = dict(OPEN_PASSING, statusCheckRollup=[{"name": "pytest", "conclusion": None,
                                                    "status": "IN_PROGRESS"}])
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: gp.normalize(running)})
    gp.repo_prs(REPO); _drain()
    assert gp.needs_poll(REPO) is True

    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: gp.normalize(OPEN_PASSING)})
    gp.invalidate(REPO)
    gp.repo_prs(REPO); _drain()
    assert gp.needs_poll(REPO) is False, "a terminal check must end the poll"


def test_the_push_matcher_reads_command_position():
    """One matcher for the judge's receipts and the kernel's push counter."""
    for cmd in ("git push -u origin dev/x", "cd notes-api && git push", "FOO=1 gh pr create --draft",
                "make test; gh pr merge 12"):
        assert gp.is_push_command(cmd), cmd
    for cmd in ("grep -rn 'git push' docs/", "gh pr view 12", "gh pr list", "echo gh pr create"):
        assert not gp.is_push_command(cmd), cmd


def test_commit_statuses_read_their_state_and_context():
    """A StatusContext rollup entry carries state/context, not conclusion/name."""
    ok = {"__typename": "StatusContext", "context": "ci/circleci", "state": "SUCCESS"}
    bad = {"__typename": "StatusContext", "context": "ci/circleci", "state": "FAILURE"}
    pending = {"__typename": "StatusContext", "context": "vercel", "state": "PENDING"}
    run = {"__typename": "CheckRun", "name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED"}
    assert gp._checks([run, ok]) == ("pass", [])
    assert gp._checks([run, bad]) == ("fail", ["ci/circleci"])
    assert gp._checks([run, pending]) == ("running", [])


def test_a_branch_pr_outside_the_list_window_is_fetched_by_head(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: _bare(OPEN_PASSING)})
    heads = []
    monkeypatch.setattr(gp, "hydrate_head", lambda repo, b: (heads.append(b),
                                                             gp.normalize(dict(MERGED, number=3, headRefName=b)))[1])
    _checks_stub(monkeypatch)
    gp.repo_prs(REPO, branch="dev/old-work"); _drain()
    prs, err = gp.repo_prs(REPO)
    assert heads == ["dev/old-work"] and gp.branch_pr(prs, "dev/old-work") == 3 and err == ""


def test_the_current_branch_outranks_old_ones_and_terminal_prs_come_last():
    prs = {n: {"branch": "dev/n%d" % n, "state": "merged"} for n in range(100, 114)}
    prs[50] = {"branch": "dev/now", "state": "open"}
    prs[20] = {"branch": "dev/cited", "state": "open"}
    order = gp._check_order(prs, ["dev/now", "dev/n113"], {20, 100})
    assert order == [50, 113, 20, 100]


def test_a_branch_nobody_asked_about_lately_is_not_current():
    _reset()
    gp._BRANCHES[REPO] = {"dev/old": 0.0, "dev/now": 1000.0}
    assert gp._current_branches(REPO, 1000.0 + 1) == ["dev/now"]


def test_a_transient_single_fetch_failure_is_shown_and_retried(monkeypatch):
    """Only gh's own "no such PR" is remembered as unresolvable; anything else is an error to show."""
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {})
    asked = []

    def flaky(repo, n):
        asked.append(n)
        raise gp.GitPrError("HTTP 502: 502 Bad Gateway")

    monkeypatch.setattr(gp, "hydrate_one", flaky)
    gp.repo_prs(REPO, nums=[4]); _drain()
    assert "502" in gp.repo_prs(REPO)[1] and gp.needs_poll(REPO) is True
    gp.invalidate(REPO)
    gp.repo_prs(REPO, nums=[4]); _drain()
    assert asked == [4, 4], "not remembered as gone"


def test_a_failed_checks_fetch_is_shown(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: _bare(OPEN_PASSING)})

    def boom(args, what):
        raise gp.GitPrError("HTTP 502")

    monkeypatch.setattr(gp, "_gh_json", boom)
    gp.repo_prs(REPO, nums=[12]); _drain()
    assert "checks for #12" in gp.repo_prs(REPO)[1]
