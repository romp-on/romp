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
    for memo in (gp._CACHE, gp._GEN, gp._WANTED, gp._BRANCHES, gp._TRIED, gp._POLLED, gp._FULL, gp._POLL_BACKOFF,
                 gp._SESSION_BRANCH, gp._EXPECT, gp._FAILING):
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


def test_needs_poll_only_while_a_watched_check_runs(monkeypatch):
    _reset()
    running = dict(OPEN_PASSING, statusCheckRollup=[{"name": "pytest", "conclusion": None,
                                                    "status": "IN_PROGRESS"}])
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: gp.normalize(running)})
    monkeypatch.setattr(gp, "_gh_json", lambda args, what: {"number": 12, "statusCheckRollup": RUNNING})
    gp.repo_prs(REPO); _drain()
    assert gp.needs_poll(REPO) is False, "nobody cites 12 or is on its branch: its CI is not ours to wait on"
    gp.repo_prs(REPO, nums=[12]); _drain()
    assert gp.needs_poll(REPO) is True

    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: gp.normalize(OPEN_PASSING)})
    monkeypatch.setattr(gp, "_gh_json", lambda args, what: {"number": 12,
                                                            "statusCheckRollup": OPEN_PASSING["statusCheckRollup"]})
    gp.invalidate(REPO)
    gp.repo_prs(REPO); _drain()
    assert gp.needs_poll(REPO) is False, "a terminal check must end the poll"


RUNNING = [{"name": "pytest", "conclusion": None, "status": "IN_PROGRESS"}]
HOUR, STEP = 3600, 30


def _gh_counter(monkeypatch, fail=False):
    """Stub gh: the list holds PR 12 (checks running) and merged PR 9; every call is counted by verb."""
    calls = {"list": 0, "view": 0}

    def fake(args, what):
        calls[args[1]] += 1
        if fail:
            raise gp.GitPrError("API rate limit exceeded")
        if args[1] == "list":
            return [{k: v for k, v in raw.items() if k != "statusCheckRollup"} for raw in (OPEN_PASSING, MERGED)]
        return {"number": int(args[2]), "statusCheckRollup": RUNNING}

    monkeypatch.setattr(gp, "_gh_json", fake)
    return calls


def _an_hour_of_polls():
    """Drive the poll for an hour in 30 s steps, refreshing in the foreground; returns the refresh count."""
    now, n = 0.0, 0
    for _ in range(HOUR // STEP):
        now += STEP
        if gp.poll_due(REPO, now):
            gp._refresh(REPO)
            n += 1
    return n


def test_a_running_check_polls_only_its_own_checks(monkeypatch):
    _reset()
    calls = _gh_counter(monkeypatch)
    gp.repo_prs(REPO, nums=[12, 9]); _drain()
    listed = calls["list"]
    polls = _an_hour_of_polls()
    assert polls == HOUR // STEP
    assert calls["list"] == listed, "a poll never re-reads the list"
    assert calls["view"] <= 2 + polls, "one checks read per poll, for the one running PR"


def test_a_failing_read_backs_off_and_a_retry_resets_it(monkeypatch):
    _reset()
    _gh_counter(monkeypatch, fail=True)
    gp.repo_prs(REPO); _drain()
    assert gp.needs_poll(REPO)
    assert _an_hour_of_polls() <= 10, "doubling from 30 s to the 900 s ceiling"
    gp.retry(REPO)
    assert REPO not in gp._POLL_BACKOFF


def test_a_merged_pr_with_a_pending_status_does_not_keep_the_poll(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {9: gp.normalize(dict(MERGED, statusCheckRollup=RUNNING))})
    monkeypatch.setattr(gp, "_gh_json", lambda args, what: {"number": 9, "statusCheckRollup": RUNNING})
    gp.repo_prs(REPO, nums=[9]); _drain()
    assert gp.needs_poll(REPO) is False


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
    monkeypatch.setattr(gp, "hydrate_head", lambda repo, b, owner="": (heads.append(b),
                                                                       gp.normalize(dict(MERGED, number=3, headRefName=b)))[1])
    _checks_stub(monkeypatch)
    gp.repo_prs(REPO, branch="dev/old-work"); _drain()
    prs, err = gp.repo_prs(REPO)
    assert heads == ["dev/old-work"] and gp.branch_pr(prs, "dev/old-work") == 3 and err == ""


def test_the_current_branch_outranks_old_ones_and_terminal_prs_come_last():
    prs = {n: {"branch": "dev/n%d" % n, "state": "merged"} for n in range(100, 114)}
    prs[50] = {"branch": "dev/now", "state": "open"}
    prs[20] = {"branch": "dev/cited", "state": "open"}
    order = gp._check_order(prs, [("dev/now", ""), ("dev/n113", "")], {20, 100})
    assert order == [50, 113, 20, 100]


def test_a_branch_nobody_asked_about_lately_is_not_current():
    _reset()
    gp._BRANCHES[REPO] = {"dev/old": (0.0, ""), "dev/now": (1000.0, "")}
    assert gp._current_branches(REPO, 1000.0 + 1) == [("dev/now", "")]


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


# ── what a poll reads ────────────────────────────────────────────────────────────────────────────────

def _row(n, branch=None, state="OPEN", **kw):
    """A list row for PR `n`, no rollup."""
    return dict({k: v for k, v in OPEN_PASSING.items() if k != "statusCheckRollup"}, number=n,
                headRefName=branch or "dev/n%d" % n, state=state, **kw)


def _gh_script(monkeypatch, rows, checks, fail=None):
    """Stub gh. `rows` is the list; `checks(n, count)` answers PR n's checks read; `fail(args)` may raise.
    Returns the per-number checks-read counts and the list count."""
    seen = {"list": 0, "views": {}}

    def fake(args, what):
        if fail:
            fail(args)
        if args[1] == "list":
            seen["list"] += 1
            return [dict(r) for r in rows]
        n = int(args[2])
        if "statusCheckRollup" not in args[-1]:
            return next((dict(r) for r in rows if r["number"] == n), None)
        seen["views"][n] = seen["views"].get(n, 0) + 1
        return {"number": n, "statusCheckRollup": checks(n, seen["views"][n])}

    monkeypatch.setattr(gp, "_gh_json", fake)
    return seen


def _polls(seconds, step=STEP, start=0.0):
    """Drive poll_due and repo_prs the way the build pass does; returns the polls that fired."""
    now, fired = start, []
    while now < start + seconds:
        now += step
        if gp.poll_due(REPO, now):
            fired.append(now)
        gp.repo_prs(REPO)
        _drain()
    return fired


def test_a_poll_reads_only_the_prs_a_session_cites_or_is_on(monkeypatch):
    """Uncited open PRs with running CI above the session's own: the poll re-reads the branch PR and the
    cited one only, and stops once they settle."""
    _reset()
    rows = [_row(n) for n in range(40, 60)] + [_row(12, branch="dev/mine"), _row(15)]
    done_at = {12: 3, 15: 2}
    seen = _gh_script(monkeypatch, rows, lambda n, k: RUNNING if k < done_at.get(n, 10 ** 6) else
                      OPEN_PASSING["statusCheckRollup"])
    gp.repo_prs(REPO, nums=[15], branch="dev/mine"); _drain()
    fired = _polls(HOUR)
    assert set(seen["views"]) == {12, 15}, "no uncited PR's checks are read"
    assert gp.needs_poll(REPO) is False and len(fired) == 2, fired


def test_the_session_s_pr_is_not_starved_by_newer_running_ones(monkeypatch):
    _reset()
    rows = [_row(n) for n in range(100, 130)] + [_row(12, branch="dev/mine")]
    seen = _gh_script(monkeypatch, rows, lambda n, k: RUNNING if (n != 12 or k < 2) else [])
    gp.repo_prs(REPO, branch="dev/mine"); _drain()
    _polls(STEP * 2)
    assert gp.repo_prs(REPO)[0][12]["checksState"] == "none"
    assert gp.needs_poll(REPO) is False


def test_the_branch_pr_s_checks_are_read_ahead_of_a_full_budget_of_cited_ones(monkeypatch):
    """More cited running PRs than one read's checks budget: the branch's own PR is read first, every time."""
    _reset()
    cited = list(range(100, 100 + gp._MAX_CHECK_FETCHES + 2))
    rows = [_row(n) for n in cited] + [_row(12, branch="dev/mine")]
    seen = _gh_script(monkeypatch, rows, lambda n, k: RUNNING)
    gp.repo_prs(REPO, nums=cited, branch="dev/mine"); _drain()
    assert seen["views"].get(12) == 1, "the full read reached it"
    gp.poll_due(REPO, 10 ** 6); gp.repo_prs(REPO); _drain()
    assert seen["views"][12] == 2, "and so did the poll"


def test_the_poll_re_reads_a_watched_pr_whose_checks_were_never_read(monkeypatch):
    """"unknown" is unsettled: a watched open PR the last read could not reach is read by the next poll."""
    _reset()
    rows = [_row(12, branch="dev/mine"), _row(15)]
    seen = _gh_script(monkeypatch, rows, lambda n, k: RUNNING)
    gp.repo_prs(REPO, nums=[15], branch="dev/mine"); _drain()
    cached = gp._CACHE[REPO]
    prs = dict(cached["prs"])
    prs[15] = dict(prs[15], checksState="unknown")
    gp._CACHE[REPO] = dict(cached, prs=prs)
    before = dict(seen["views"])
    gp.poll_due(REPO, 10 ** 6); gp.repo_prs(REPO); _drain()
    assert seen["views"][15] == before[15] + 1


def test_a_poll_never_changes_the_dict_a_reader_holds(monkeypatch):
    _reset()
    _gh_script(monkeypatch, [_row(12, branch="dev/mine")], lambda n, k: RUNNING if k < 2 else [])
    gp.repo_prs(REPO, branch="dev/mine"); _drain()
    held = gp.repo_prs(REPO)[0]
    snapshot = {n: dict(pr) for n, pr in held.items()}
    gp.poll_due(REPO, 10 ** 6); gp.repo_prs(REPO); _drain()
    assert held == snapshot and gp.repo_prs(REPO)[0][12]["checksState"] == "none"


def test_a_check_that_passes_on_its_third_read_ends_the_poll(monkeypatch):
    """Through repo_prs, as the build pass reaches it: the poll's invalidation is what makes the next build
    re-read."""
    _reset()
    _gh_script(monkeypatch, [_row(12, branch="dev/mine")], lambda n, k: RUNNING if k < 3 else [])
    gp.repo_prs(REPO, branch="dev/mine"); _drain()
    fired = _polls(STEP * 10)
    assert len(fired) == 2 and gp.needs_poll(REPO) is False, fired


def test_a_closed_pr_with_a_pending_status_does_not_keep_the_poll(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {9: gp.normalize(dict(MERGED, state="CLOSED",
                                                                            statusCheckRollup=RUNNING))})
    monkeypatch.setattr(gp, "_gh_json", lambda args, what: {"number": 9, "statusCheckRollup": RUNNING})
    gp.repo_prs(REPO, nums=[9]); _drain()
    assert gp.needs_poll(REPO) is False


def test_a_running_check_costs_exactly_one_checks_read_per_poll(monkeypatch):
    _reset()
    calls = _gh_counter(monkeypatch)
    gp.repo_prs(REPO, nums=[12, 9]); _drain()
    polls = _an_hour_of_polls()
    assert polls == HOUR // STEP and calls["list"] == 1
    assert calls["view"] == 2 + polls


def test_a_failing_read_polls_exactly_seven_times_an_hour(monkeypatch):
    _reset()
    _gh_counter(monkeypatch, fail=True)
    gp.repo_prs(REPO); _drain()
    assert _an_hour_of_polls() == 7, "30, 60, 120, 240, 480 s, then the 900 s ceiling"


def test_a_clean_read_resets_the_backoff(monkeypatch):
    """A failure, a clean read while a check runs, then a new failure: the new one starts again from 30 s."""
    _reset()
    state = {"fail": True}

    def fail(args):
        if state["fail"]:
            raise gp.GitPrError("HTTP 502")

    _gh_script(monkeypatch, [_row(12, branch="dev/mine")], lambda n, k: RUNNING, fail=fail)
    gp.repo_prs(REPO, branch="dev/mine"); _drain()
    _polls(STEP * 8)
    assert gp._POLL_BACKOFF[REPO] > gp._POLL_SECS * 2
    state["fail"] = False
    _polls(STEP * 30, start=10 ** 5)                   # the next due poll reads cleanly
    assert REPO not in gp._POLL_BACKOFF
    state["fail"] = True
    fired = _polls(STEP * 3, start=2 * 10 ** 5)
    assert fired[1] - fired[0] == gp._POLL_SECS, fired


# ── an error the poll must retry, not clear ──────────────────────────────────────────────────────────

def _flaky(first_failures, match):
    """A fail() that raises for calls whose args satisfy `match`, the first `first_failures` times."""
    left = {"n": first_failures}

    def fail(args):
        if match(args) and left["n"] > 0:
            left["n"] -= 1
            raise gp.GitPrError("HTTP 502: 502 Bad Gateway")

    return fail


def test_a_cited_pr_outside_the_window_arrives_once_gh_recovers(monkeypatch):
    _reset()
    _gh_script(monkeypatch, [], lambda n, k: [])
    left = {"n": 1}

    def one(repo, n):
        if left["n"]:
            left["n"] -= 1
            raise gp.GitPrError("HTTP 502: 502 Bad Gateway")
        return gp.normalize(_row(n, state="MERGED"))

    monkeypatch.setattr(gp, "hydrate_one", one)
    gp.repo_prs(REPO, nums=[7]); _drain()
    assert "502" in gp.repo_prs(REPO)[1]
    _polls(STEP * 2)
    prs, err = gp.repo_prs(REPO)
    assert 7 in prs and err == ""


def test_a_branch_lookup_arrives_once_gh_recovers(monkeypatch):
    _reset()
    _gh_script(monkeypatch, [], lambda n, k: [])
    left = {"n": 1}

    def head(repo, b, owner=""):
        if left["n"]:
            left["n"] -= 1
            raise gp.GitPrError("HTTP 502: 502 Bad Gateway")
        return gp.normalize(_row(3, branch=b, state="MERGED"))

    monkeypatch.setattr(gp, "hydrate_head", head)
    gp.repo_prs(REPO, branch="dev/old-work"); _drain()
    assert "502" in gp.repo_prs(REPO)[1]
    _polls(STEP * 2)
    prs, err = gp.repo_prs(REPO)
    assert gp.branch_pr(prs, "dev/old-work") == 3 and err == ""


def test_a_merged_pr_s_checks_read_is_retried(monkeypatch):
    _reset()
    rows = [_row(9, state="MERGED")]
    _gh_script(monkeypatch, rows, lambda n, k: [], fail=_flaky(1, lambda a: a[1] == "view"))
    gp.repo_prs(REPO, nums=[9]); _drain()
    assert "checks for #9" in gp.repo_prs(REPO)[1]
    _polls(STEP * 2)
    prs, err = gp.repo_prs(REPO)
    assert prs[9]["checksState"] == "none" and err == ""


def test_a_failure_gh_never_recovers_from_stays_up_all_hour(monkeypatch):
    _reset()
    _gh_script(monkeypatch, [], lambda n, k: [])
    monkeypatch.setattr(gp, "hydrate_one", lambda repo, n: (_ for _ in ()).throw(gp.GitPrError("HTTP 403")))
    gp.repo_prs(REPO, nums=[7]); _drain()
    for _ in range(HOUR // STEP):
        _polls(STEP, start=_ * STEP)
        assert "403" in gp.repo_prs(REPO)[1]


def test_a_malformed_row_is_an_error_and_the_next_read_is_full(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: gp.normalize(OPEN_PASSING)})
    gp.repo_prs(REPO); _drain()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: gp.normalize({"number": "twelve"})})
    gp.invalidate(REPO)
    gp.repo_prs(REPO); _drain()
    prs, err = gp.repo_prs(REPO)
    assert "ValueError" in err and sorted(prs) == [12], "the old snapshot beside the error, never a fresh read"
    assert gp._CACHE[REPO]["fullErr"] is True


def test_a_thread_that_cannot_start_clears_its_in_flight_mark(monkeypatch):
    _reset()

    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(gp.threading, "Thread", NoThread)
    try:
        gp._kick(REPO)
    except RuntimeError:
        pass
    assert REPO not in gp._INFLIGHT


# ── what a full read keeps ───────────────────────────────────────────────────────────────────────────

def test_a_full_read_keeps_only_merged_prs_someone_cites_or_is_on(monkeypatch):
    _reset()
    first = {9: gp.normalize(MERGED), 30: gp.normalize(dict(MERGED, number=30, headRefName="dev/mine")),
             31: gp.normalize(dict(MERGED, number=31, headRefName="dev/other"))}
    monkeypatch.setattr(gp, "hydrate", lambda repo: dict(first))
    _checks_stub(monkeypatch)
    gp.repo_prs(REPO, nums=[9], branch="dev/mine"); _drain()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {})
    fetched = []
    monkeypatch.setattr(gp, "hydrate_one", lambda repo, n: fetched.append(n))
    gp.invalidate(REPO)
    gp.repo_prs(REPO); _drain()
    assert sorted(gp.repo_prs(REPO)[0]) == [9, 30], "the uncited merged one goes"
    assert fetched == [], "a kept merged PR is not fetched again"


def test_a_fetched_open_single_is_kept_and_read_again(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {})
    _checks_stub(monkeypatch)
    asked = []
    monkeypatch.setattr(gp, "hydrate_one", lambda repo, n: (asked.append(n), gp.normalize(_row(n)))[1])
    gp.repo_prs(REPO, nums=[4]); _drain()
    monkeypatch.setattr(gp, "hydrate_one", lambda repo, n: (asked.append(n),
                                                            (_ for _ in ()).throw(gp.GitPrError("HTTP 502")))[1])
    gp.invalidate(REPO)
    gp.repo_prs(REPO); _drain()
    assert asked == [4, 4] and 4 in gp.repo_prs(REPO)[0], "re-read, and kept when the re-read fails"


def test_heads_are_asked_before_cited_numbers_and_the_rest_follow_on_the_next_read(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {})
    _checks_stub(monkeypatch)
    order = []
    monkeypatch.setattr(gp, "hydrate_one", lambda repo, n: (order.append(n), gp.normalize(_row(n, state="MERGED")))[1])
    monkeypatch.setattr(gp, "hydrate_head", lambda repo, b, owner="": (order.append(b),
                                                                       gp.normalize(_row(99, branch=b)))[1])
    cited = list(range(1, 21))
    gp.repo_prs(REPO, nums=cited, branch="dev/mine"); _drain()
    assert order[0] == "dev/mine" and len(order) == gp._MAX_SINGLE_FETCHES
    assert gp._CACHE[REPO]["fresh"] is False, "numbers still unasked: the next build reads again"
    gp.repo_prs(REPO); _drain()
    assert set(cited) <= set(gp.repo_prs(REPO)[0])


def test_a_failed_checks_read_keeps_the_last_known_verdict(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: _bare(OPEN_PASSING)})
    _checks_stub(monkeypatch, conclusion="FAILURE")
    gp.repo_prs(REPO, nums=[12]); _drain()
    assert gp.repo_prs(REPO)[0][12]["checksState"] == "fail"
    monkeypatch.setattr(gp, "_gh_json", lambda args, what: (_ for _ in ()).throw(gp.GitPrError("rate limit")))
    gp.note_push_turn(REPO)
    gp.repo_prs(REPO); _drain()
    prs, err = gp.repo_prs(REPO)
    assert prs[12]["checksState"] == "fail" and prs[12]["checksFailing"] == ["pytest"] and "rate limit" in err


# ── branches ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_new_branch_with_no_cached_pr_asks_again(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {})
    heads = []
    monkeypatch.setattr(gp, "hydrate_head", lambda repo, b, owner="": heads.append(b))
    gp.repo_prs(REPO, branch="dev/a", sid="s1"); _drain()
    assert gp._CACHE[REPO]["fresh"] is True
    gp.repo_prs(REPO, branch="dev/b", sid="s2"); _drain()
    assert heads == ["dev/a", "dev/b"]


def test_a_session_that_leaves_a_branch_takes_it_out_of_the_current_set():
    _reset()
    gp._want(REPO, (), "dev/a", sid="s1")
    gp._want(REPO, (), "dev/shared", sid="s2")
    gp._want(REPO, (), "dev/shared", sid="s3")
    gp._want(REPO, (), "dev/b", sid="s1")
    gp._want(REPO, (), "dev/c", sid="s2")
    assert {b for b, _o in gp._current_branches(REPO, time.monotonic())} == {"dev/b", "dev/c", "dev/shared"}


def test_a_same_named_branch_on_another_fork_is_not_this_session_s():
    prs = {40: {"branch": "main", "state": "closed", "headOwner": "stranger"},
           12: {"branch": "main", "state": "open", "headOwner": "notes-api-org"}}
    assert gp.branch_pr(prs, "main", "notes-api-org") == 12
    assert gp.branch_pr({40: prs[40]}, "main", "notes-api-org") is None
    assert gp.branch_pr({40: prs[40]}, "main") == 40, "no owner known: as before"


def test_the_head_lookup_passes_over_another_fork_s_newer_pr(monkeypatch):
    _gh_script(monkeypatch, [_row(40, branch="main", headRepositoryOwner={"login": "Stranger"}),
                             _row(12, branch="main", headRepositoryOwner={"login": "Notes-API-Org"})],
               lambda n, k: [])
    assert gp.hydrate_head(REPO, "main", "notes-api-org")["num"] == 12


# ── a pushed head GitHub has not shown yet ───────────────────────────────────────────────────────────

def test_the_poll_waits_for_a_pushed_head_to_reach_its_pr(monkeypatch):
    _reset()
    heads = iter(["old", "old", "new"])
    rows = [_row(12, branch="dev/mine", headRefOid="old")]

    def fake(args, what):
        if args[1] == "list":
            return [dict(r) for r in rows]
        return {"number": 12, "headRefOid": next(heads, "new"), "statusCheckRollup": []}

    monkeypatch.setattr(gp, "_gh_json", fake)
    monkeypatch.setattr(gp, "local_state_ex", lambda cwd: ("dev/mine", 0, True, "origin", "new"))
    monkeypatch.setattr(gp, "head_owner", lambda cwd, remote: "")
    gp.note_local_state("/nonexistent", REPO)
    gp.repo_prs(REPO, branch="dev/mine"); _drain()
    assert gp.needs_poll(REPO) is True, "the checks read settled, but on the old head"
    fired = _polls(STEP * 10)
    assert len(fired) == 2 and gp.needs_poll(REPO) is False, fired


def test_a_pushed_head_that_never_arrives_stops_the_wait(monkeypatch):
    _reset()
    rows = [_row(12, branch="dev/mine", headRefOid="theirs")]
    monkeypatch.setattr(gp, "_gh_json", lambda args, what: [dict(r) for r in rows] if args[1] == "list" else
                        {"number": 12, "headRefOid": "theirs", "statusCheckRollup": []})
    monkeypatch.setattr(gp, "local_state_ex", lambda cwd: ("dev/mine", 0, True, "origin", "ours"))
    monkeypatch.setattr(gp, "head_owner", lambda cwd, remote: "")
    gp.note_local_state("/nonexistent", REPO)
    gp.repo_prs(REPO, branch="dev/mine"); _drain()
    fired = _polls(HOUR)
    assert len(fired) == gp._HEAD_WAIT_POLLS and gp.needs_poll(REPO) is False


def test_a_moved_ref_invalidates_and_a_still_one_does_not(monkeypatch):
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {})
    gp.repo_prs(REPO); _drain()
    monkeypatch.setattr(gp, "head_owner", lambda cwd, remote: "")
    monkeypatch.setattr(gp, "local_state_ex", lambda cwd: ("dev/mine", 1, False, "origin", "a"))
    gp.note_local_state("/nonexistent", REPO)
    assert gp._CACHE[REPO]["fresh"] is True
    monkeypatch.setattr(gp, "local_state_ex", lambda cwd: ("dev/mine", 0, True, "origin", "b"))
    gp.note_local_state("/nonexistent", REPO)
    assert gp._CACHE[REPO]["fresh"] is False


# ── the refresh in flight ────────────────────────────────────────────────────────────────────────────

def test_a_reader_mid_refresh_sees_the_old_entry_whole(monkeypatch):
    """The list read lands, then the singles and checks are fetched: until the refresh publishes, a reader
    sees the previous entry, never the new list half assembled (the #1982 shape)."""
    _reset()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: _bare(OPEN_PASSING)})
    _checks_stub(monkeypatch)
    gp.repo_prs(REPO, nums=[12]); _drain()
    old = gp._CACHE[REPO]
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(gp, "hydrate", lambda repo: {12: _bare(OPEN_PASSING), 15: _bare(DRAFT_FAILING)})

    def slow_one(repo, n):
        entered.set()
        release.wait(2)
        return gp.normalize(dict(MERGED, number=n))

    monkeypatch.setattr(gp, "hydrate_one", slow_one)
    gp.repo_prs(REPO, nums=[3]); assert entered.wait(2)
    assert gp._CACHE[REPO]["prs"] is old["prs"] and sorted(gp._CACHE[REPO]["prs"]) == [12]
    release.set(); _drain()
    assert sorted(gp.repo_prs(REPO)[0]) == [3, 12, 15]


def test_an_invalidation_after_the_read_began_is_not_lost(monkeypatch):
    """Deterministic: the push lands once the list read has started, never before it."""
    _reset()
    calls, entered, gate = [], threading.Event(), threading.Event()

    def slow(repo):
        calls.append(repo)
        entered.set()
        gate.wait(2)
        return {12: gp.normalize(OPEN_PASSING)}

    monkeypatch.setattr(gp, "hydrate", slow)
    gp.repo_prs(REPO)
    assert entered.wait(2)
    gp.note_push_turn(REPO)
    gate.set(); _drain()
    assert gp._CACHE[REPO]["fresh"] is False
    gp.repo_prs(REPO); _drain()
    assert len(calls) == 2 and gp._CACHE[REPO]["fresh"] is True


# ── what a failure says ──────────────────────────────────────────────────────────────────────────────

def test_a_status_and_a_check_of_one_name_list_it_once():
    both = [{"name": "ci", "conclusion": "FAILURE", "status": "COMPLETED"},
            {"__typename": "StatusContext", "context": "ci", "state": "FAILURE"}]
    assert gp._checks(both) == ("fail", ["ci"])


def test_gh_s_whole_stderr_is_kept(monkeypatch):
    _stub_gh(monkeypatch, "", code=4, stderr="To get started with GitHub CLI, please run:  gh auth login\n"
                                            "Alternatively, populate the GH_TOKEN environment variable.\n")
    try:
        gp.hydrate(REPO)
    except gp.GitPrError as e:
        assert "gh auth login" in str(e) and "GH_TOKEN" in str(e)
    else:
        raise AssertionError("expected GitPrError")


def test_a_timeout_says_it_timed_out(monkeypatch):
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(gp.subprocess, "run", slow)
    try:
        gp.hydrate(REPO + "-" + "x" * 400)
    except gp.GitPrError as e:
        assert "timed out" in str(e)
    else:
        raise AssertionError("expected GitPrError")


def test_failure_and_recovery_are_each_logged_once(monkeypatch, capsys):
    _reset()
    state = {"fail": True}

    def fail(args):
        if state["fail"]:
            raise gp.GitPrError("HTTP 502")

    _gh_script(monkeypatch, [_row(12, branch="dev/mine")], lambda n, k: RUNNING, fail=fail)
    gp.repo_prs(REPO, branch="dev/mine"); _drain()
    _polls(HOUR)
    state["fail"] = False
    _polls(HOUR, start=10 ** 5)
    err = capsys.readouterr().err
    assert err.count("could not be read") == 1 and err.count("reads again") == 1
    assert err.count("retries every %d s" % gp._POLL_MAX_SECS) == 1
