#!/usr/bin/env python3
"""The Tier policy check's FETCHER: reads a PR (or every open PR) through the GitHub REST API with the
workflow's GITHUB_TOKEN, builds the record scripts/ci/tier_policy.py evaluates, and posts the verdict as a
check run named "Tier policy" on the PR's head sha. API reads only - it never checks out or runs PR code.

Trust model, stated once: the workflow that runs this is the BASE branch's copy (pull_request_target), so
a PR cannot rewrite its own gate; the token holds checks:write (to post the verdict), pull-requests:read,
issues:read, contents:read and nothing else. Nothing in the policy is timed (the owner's rules of
2026-09-08: the gate depends on the tier and on the author's role, and no tier has a time-based path), so
this fetcher reads no check-run history, no timeline and no commit date; the record it builds carries no
time field at all. The collaborator permission is fetched for the AUTHOR as well as for every reviewer: the
policy reads the author's role from the same map (admin is the repository owner; a non-collaborator's 404
reads as "none", a contributor).
A dismissed review's original state and its dismisser come from the issue events API's review_dismissed
event (actor + dismissed_review.{review_id,state}; the reviews API itself only says DISMISSED); a dismissed
review with no such event raises.
The workflow's own job carries a DIFFERENT name so exactly one family of same-named runs exists. A renamed
or copied file is recorded under both its paths, and a listing the API truncated (3000-file cap, checked
against the PR's changed_files) is flagged; the head is re-read at the end so a push during evaluation
raises instead of grading a mixed record. Commit dates are never read.
An opened or reopened run first waits for the PR's tier label (wait_for_tier_label): `gh pr create --label`
fires `opened` before the label lands, and a check that read at once failed the PR for a state that lasted
seconds; every other event, the hourly sweep and a dispatch judge at once. The pause between re-reads is
the fetcher's only use of the clock, and it reads nothing from it.
GITHUB_TOKEN is the GitHub Actions app's installation token, which is what the Checks API's "GitHub Apps
only" write rule admits; the ruleset requiring this check must select the run posted by the GitHub Actions
app (a bare context match would accept any write-holder's commit status of the same name).
Usage: tier_policy_check.py --pr N | --all-open (env GITHUB_TOKEN, GITHUB_REPOSITORY; ACTION, the workflow
event's action, is what makes a --pr run wait)."""
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from time import sleep             # the pause between re-reads of a just-opened PR; no clock is read

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from tier_policy import TIERS, TIER_ALIASES, evaluate  # noqa: E402

API = "https://api.github.com"
CHECK_NAME = "Tier policy"
MAX_ISSUE_REFS = 5                 # a body can be 64 KiB of "#1 " - bound the work (and the token budget)
GRACE_ACTIONS = ("opened", "reopened")  # the events whose run can read before `gh pr create --label` lands the label
GRACE_TRIES = 12                   # re-reads after the first when a just-opened PR has no tier label (TIER_GRACE_TRIES)
GRACE_STEP_SECS = 5                # seconds between them (TIER_GRACE_STEP_SECS): twelve of five is the minute


def _iso(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _req(method, path, token, body=None):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "romp-tier-policy"})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return (json.loads(txt) if txt else None), r.headers


def _get_all(path, token):
    """Every page of a list endpoint, following the Link header."""
    out, url = [], path if path.startswith("http") else API + path
    sep = "&" if "?" in url else "?"
    url += sep + "per_page=100"
    while url:
        page, hdrs = _req("GET", url, token)
        out.extend(page)
        m = re.search(r'<([^>]+)>;\s*rel="next"', hdrs.get("Link", "") or "")
        url = m.group(1) if m else None
    return out


def _is_bot(user):
    return not user or user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def _permission(repo, login, token):
    """The collaborator permission, "none" for a NON-collaborator (404). Any other error raises: a 403,
    429 or 5xx silently mapped to "none" would deny every approval while posting a normal-looking verdict
    (the review's catch) - fail loudly instead, leaving no verdict for this run."""
    try:
        p, _ = _req("GET", "/repos/%s/collaborators/%s/permission" % (repo, urllib.parse.quote(login)), token)
        return p.get("permission")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "none"
        raise


def build_record(repo, number, token):
    pr, _ = _req("GET", "/repos/%s/pulls/%d" % (repo, number), token)
    head = pr["head"]["sha"]
    author = pr["user"]["login"]
    entries = _get_all("/repos/%s/pulls/%d/files" % (repo, number), token)
    files = []
    for f in entries:
        for p in (f.get("filename"), f.get("previous_filename")):     # renamed/copied: judge the source too
            if p and p not in files:
                files.append(p)
    changed = pr.get("changed_files")
    files_truncated = changed is not None and changed != len(entries)
    dismissals = {}                    # review id -> (who dismissed it, the review's ORIGINAL state)
    for e in _get_all("/repos/%s/issues/%d/events" % (repo, number), token):
        if e.get("event") == "review_dismissed":
            d = e.get("dismissed_review") or {}
            dismissals[d.get("review_id")] = ((e.get("actor") or {}).get("login"), str(d.get("state") or "").upper())
    reviews = []
    for r in _get_all("/repos/%s/pulls/%d/reviews" % (repo, number), token):
        if not r.get("user"):
            continue
        rec = {"user": r["user"]["login"], "state": r["state"], "commit_id": r.get("commit_id"),
               "submitted_at": _iso(r.get("submitted_at")), "dismissed": r["state"] == "DISMISSED", "dismissed_by": None}
        if rec["dismissed"]:
            if r.get("id") not in dismissals:
                raise RuntimeError("review %s by %s is DISMISSED but no review_dismissed event says who dismissed it"
                                   % (r.get("id"), rec["user"]))
            rec["dismissed_by"], rec["state"] = dismissals[r["id"]]
        reviews.append(rec)
    # every reviewer AND the author: the policy reads the author's role (admin = the owner) from this map
    perms = {u: _permission(repo, u, token) for u in {r["user"] for r in reviews} | {author}}
    issues = {}
    seen = []
    for a, b in re.findall(r"(?:^|[^\w/])#(\d+)\b|github\.com/%s/issues/(\d+)\b" % re.escape(repo), pr.get("body") or ""):
        n = int(a or b)
        if n in seen:
            continue
        seen.append(n)
        if len(seen) > MAX_ISSUE_REFS:
            break
        try:
            it, _ = _req("GET", "/repos/%s/issues/%d" % (repo, n), token)
            comments = [c["user"]["login"] for c in _get_all("/repos/%s/issues/%d/comments" % (repo, n), token)
                        if not _is_bot(c.get("user"))]
            issues[n] = {"exists": True, "is_pr": "pull_request" in it, "comments": comments}
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            issues[n] = {"exists": False, "is_pr": False, "comments": []}
    again, _ = _req("GET", "/repos/%s/pulls/%d" % (repo, number), token)
    if again["head"]["sha"] != head:
        raise RuntimeError("the PR's head moved during evaluation (%s -> %s); the push's own run grades the new head"
                           % (head[:8], again["head"]["sha"][:8]))
    return {"number": number, "author": author, "labels": [l["name"] for l in pr.get("labels") or []],
            "head_sha": head, "files": files, "files_truncated": files_truncated, "reviews": reviews,
            "permissions": perms, "body": pr.get("body") or "", "issues": issues}


def post_check(repo, head, verdict, token, number):
    body = {"name": CHECK_NAME, "head_sha": head, "status": "completed", "conclusion": verdict["conclusion"],
            "external_id": str(number),    # names the PR this verdict is for (one sha can head several PRs)
            "started_at": _utc_now(),      # completed_at is left for the server to stamp
            "output": {"title": verdict["title"], "summary": verdict["summary"]}}
    _req("POST", "/repos/%s/check-runs" % repo, token, body)


def _tier_label_count(pr):
    """How many tier labels a PR object carries, counted the way the policy counts them (the alias included)."""
    return sum(1 for l in pr.get("labels") or [] if TIER_ALIASES.get(l["name"], l["name"]) in TIERS)


def wait_for_tier_label(repo, n, token, pr, action, pause=sleep):
    """The opened/reopened run's grace, and only theirs. `gh pr create --label` fires `opened` before the
    label lands (a hand-labelled PR takes longer still), so a run that judged at once failed a PR that was
    sorted a second later and notified its author about a state that lasted seconds: the label check did
    that eighteen times on 2026-09-08, one per PR opened, and this check races the same way. When `action`
    is opened or reopened and `pr` (as the caller just read it) carries no tier label, re-read the PR every
    TIER_GRACE_STEP_SECS seconds for up to TIER_GRACE_TRIES more reads (five and twelve: a minute) and return
    it as last read; the caller then judges what the API reports, so a PR that stays unlabelled still
    fails, a minute later. Two labels never wait (a doubly sorted PR is a real error, not a race), and every
    other event - labeled, unlabeled, synchronize, edited - plus the hourly sweep and a dispatch judge at
    once. The knobs exist so a test runs the wait in no time; `pause` so one can pin the default minute."""
    if action not in GRACE_ACTIONS or _tier_label_count(pr) != 0:
        return pr
    tries = int(os.environ.get("TIER_GRACE_TRIES") or GRACE_TRIES)
    step = float(os.environ.get("TIER_GRACE_STEP_SECS") or GRACE_STEP_SECS)
    for i in range(1, tries + 1):
        print("PR #%d: no tier label yet on a just-opened PR; waiting for one (%d)" % (n, i))
        pause(step)
        pr, _ = _req("GET", "/repos/%s/pulls/%d" % (repo, n), token)
        if _tier_label_count(pr) != 0:
            break
    return pr


def run_one(repo, n, token, action=None):
    """Evaluate one PR and post its verdict. `action` is the workflow event's action (None for the sweep and
    a dispatch): an opened/reopened run first waits for the PR's tier label (wait_for_tier_label). A failure
    while BUILDING the record posts a failing verdict naming the error when the head is known (never a
    silent gap on a required check), and re-raises so the job reads red; in --all-open the caller isolates
    it so one PR cannot starve the others."""
    head = None
    try:
        pr, _ = _req("GET", "/repos/%s/pulls/%d" % (repo, n), token)
        head = pr["head"]["sha"]
        pr = wait_for_tier_label(repo, n, token, pr, action)
        head = pr["head"]["sha"]           # a push during the wait: a failing verdict lands on the head the PR has now
        rec = build_record(repo, n, token)
        v = evaluate(rec)
    except Exception as e:
        if head:
            post_check(repo, head, {"conclusion": "failure", "title": "Tier policy: evaluation failed",
                                    "summary": "The policy could not be evaluated for this head: %r. A maintainer "
                                               "can re-run the workflow; the verdict is not a ruling on the tier." % (e,)},
                       token, n)
        raise
    post_check(repo, head, v, token, n)
    print("PR #%d (%s): %s - %s" % (n, head[:8], v["conclusion"], v["title"]))
    return v


def main(argv):
    token = os.environ.get("GITHUB_TOKEN") or ""
    repo = os.environ.get("GITHUB_REPOSITORY") or "romp-on/romp"
    if not token:
        sys.exit("GITHUB_TOKEN is required")
    if "--all-open" in argv:
        rc = 0
        for p in _get_all("/repos/%s/pulls?state=open" % repo, token):
            try:
                run_one(repo, p["number"], token)
            except Exception:
                sys.stderr.write("PR #%s: %s\n" % (p["number"], traceback.format_exc().strip().splitlines()[-1]))
                rc = 1
        return rc
    v = run_one(repo, int(argv[argv.index("--pr") + 1]), token, action=os.environ.get("ACTION"))
    return 1 if v["conclusion"] != "success" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
