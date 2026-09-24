#!/usr/bin/env python3
"""PRs as a session's artifacts: the git and gh reads behind the Outline pane's per-goal PR chip.

Every `git` and `gh` shell-out for that surface lives here, so build_session stays a pure assembler and
the one place that talks to the network is the one place that caches and reports its own failures.

Rules this module is bound by:
  * `gh` is the authoritative source for PR state, never scraped prose.
  * When git or gh cannot answer, say so: a blank chip would claim "no PR" when the truth is "we could
    not look".
  * Refresh is event-driven: a moved ref, a push/gh-pr command, a newly cited PR number, a user click.
    The one timer is the poll that waits for running CI checks (and for a failed read to recover).
"""
import calendar
import json
import os
import re
import shlex
import subprocess
import threading
import time

GIT_BIN = os.environ.get("ROMP_GIT_BIN", "git")
GH_BIN = os.environ.get("ROMP_GH_BIN", "gh")
PR_STATUS_OFF = os.environ.get("ROMP_PR_STATUS", "").strip().lower() == "off"   # skips every git and gh read
_TIMEOUT = 20        # a hung network call must never stall a refresh forever
_MEMO_CAP = 512      # bound on every per-directory memo below

# owner/repo out of any GitHub remote form, including a per-account ssh host alias
# (git@github.com-personal:owner/repo.git). The optional .git suffix and trailing slash are stripped.
_REMOTE_RE = re.compile(r"^(?:https://github\.com/"
                        r"|(?:ssh://)?git@github\.com(?:-[A-Za-z0-9._-]+)?[:/])"
                        r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")

_MAIN_BRANCHES = ("main", "master")
_CANONICAL_REMOTES = ("upstream", "origin")   # the canonical repo's remote, in the fork layout first
_HEADS_PREFIX = "refs/heads/"
_SYMREF_PREFIX = "ref:"
_GITDIR_PREFIX = "gitdir:"

# A command that pushes or acts on a PR, read per SIMPLE COMMAND: the line is tokenized with quotes kept
# whole, split at shell separators, and each piece is read past VAR=value prefixes, wrapper commands and
# git/gh global options. Words inside an argument (`grep -rn 'git push' docs/`) never count, and read-only
# subcommands (view / list / checks / diff / status) are absent: looking at a PR is not acting on it. The
# judge's PR-ref mining and the kernel's push counter both read this one.
PR_ACT_VERBS = frozenset(("create", "edit", "merge", "ready", "close", "reopen", "comment", "review"))
_SEPARATORS = frozenset((";", "&", "&&", "|", "||", "(", ")", "\n"))
_WRAPPERS = frozenset(("env", "time", "command", "sudo", "nohup", "exec"))
_GIT_VALUE_OPTS = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace"))
GH_REPO_OPTS = frozenset(("-R", "--repo"))
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT_RE = re.compile(r"(?<!\S)\d*[<>]{1,2}&?\d*(?!\S)")   # 2>&1, >, >> — never a command word


class GitPrError(Exception):
    """A gh call that could not answer, carrying the reason verbatim so a caller can render it."""


def simple_commands(cmd):
    """The shell line `cmd` as a list of token lists, one per simple command; [] when it will not
    tokenize (an unbalanced quote), so a caller infers nothing rather than something wrong."""
    lexer = shlex.shlex(_REDIRECT_RE.sub(" ", (cmd or "").replace("\n", " ; ")), posix=True,
                        punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    out, cur = [], []
    for tok in tokens:
        if tok in _SEPARATORS or set(tok) <= set(";&|()"):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _command_words(tokens):
    """`tokens` past VAR=value prefixes and wrapper commands (`env FOO=1`, `time`, `command`)."""
    i = 0
    while i < len(tokens) and (_ASSIGN_RE.match(tokens[i]) or tokens[i] in _WRAPPERS):
        i += 1
    return tokens[i:]


def _past_options(words, value_opts):
    """`words` past leading options, where each of `value_opts` also consumes the word after it."""
    i = 0
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in value_opts else 1
    return words[i:]


def gh_pr_action(tokens):
    """(verb, words after it, names another repo) when `tokens` is a PR-acting gh command, else None."""
    words = _command_words(tokens)
    if not words or words[0] != "gh":
        return None
    other_repo = any(w in GH_REPO_OPTS or w.startswith("--repo=") for w in words)
    rest = _past_options(words[1:], GH_REPO_OPTS)
    if len(rest) < 2 or rest[0] != "pr" or rest[1] not in PR_ACT_VERBS:
        return None
    return rest[1], rest[2:], other_repo


def _is_git_push(tokens):
    words = _command_words(tokens)
    if not words or words[0] != "git":
        return False
    rest = _past_options(words[1:], _GIT_VALUE_OPTS)
    return bool(rest) and rest[0] == "push"


def is_push_command(cmd):
    """True when a Bash command pushed or acted on a PR: a refresh event."""
    return any(_is_git_push(t) or gh_pr_action(t) for t in simple_commands(cmd))


def _git(cwd, *args, timeout=8):
    """(ok, stdout); never raises. Local git failing is "not a repo" or "no upstream", read as absence."""
    if not cwd or not os.path.isdir(cwd):
        return False, ""
    try:
        p = subprocess.run([GIT_BIN, "-C", cwd] + list(args), capture_output=True, text=True,
                           timeout=timeout)
    except Exception:
        return False, ""
    return p.returncode == 0, (p.stdout or "").strip()


# ── the local half: pointer files, statted, so an unchanged checkout costs no fork ────────────────────

def _first_line(path):
    """The first line of a small pointer file, stripped, or '' when unreadable (absent, or torn bytes)."""
    try:
        with open(path) as f:
            return f.readline().strip()
    except (OSError, ValueError):
        return ""


def _mtime(path):
    """st_mtime_ns, or None when the path is absent: absence is part of a signature, not an error."""
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _resolve(base, rel):
    """`rel` as an absolute path, relative paths taken against `base`."""
    return rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base, rel))


def _find_dotgit(cwd):
    """The `.git` entry of the work tree containing cwd, walking up; '' outside every tree."""
    d = os.path.abspath(cwd)
    while True:
        cand = os.path.join(d, ".git")
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def git_dirs(cwd):
    """(gitdir, commondir) for the tree containing cwd, read from its pointer files, or None.

    A worktree's `.git` is a file naming its private gitdir, whose `commondir` names the shared dir that
    holds the refs, packed-refs and config every worktree reads. No fork: this runs every build pass."""
    if not cwd:
        return None
    dotgit = _find_dotgit(cwd)
    if not dotgit:
        return None
    gitdir = dotgit
    if os.path.isfile(dotgit):
        line = _first_line(dotgit)
        if not line.startswith(_GITDIR_PREFIX):
            return None
        gitdir = _resolve(os.path.dirname(dotgit), line[len(_GITDIR_PREFIX):].strip())
    rel = _first_line(os.path.join(gitdir, "commondir"))
    return gitdir, (_resolve(gitdir, rel) if rel else gitdir)


_REPO_OF = {}     # commondir → ('owner/repo' or '', config mtime)


def repo_of(cwd):
    """'owner/repo' of the canonical GitHub repo for this checkout, else '': `upstream` when the clone
    has one (the fork layout, where `origin` is the fork), else `origin`. Memoized on the config file's
    mtime, the file `git remote add` / `set-url` rewrites, so a remote added later is seen."""
    dirs = git_dirs(cwd)
    if not dirs:
        return ""
    config_mtime = _mtime(os.path.join(dirs[1], "config"))
    hit = _REPO_OF.get(dirs[1])
    if hit is not None and hit[1] == config_mtime:
        return hit[0]
    repo = ""
    for remote in _CANONICAL_REMOTES:
        ok, url = _git(cwd, "remote", "get-url", remote)
        m = _REMOTE_RE.match(url) if (ok and url) else None
        if m:
            repo = m.group(1)
            break
    if len(_REPO_OF) > _MEMO_CAP:
        _REPO_OF.clear()
    _REPO_OF[dirs[1]] = (repo, config_mtime)
    return repo


def _ref_mtimes(common, refs):
    """The mtimes of each loose ref file plus packed-refs: a moved ref rewrites one of them."""
    return tuple(_mtime(os.path.join(common, r)) for r in refs) + (_mtime(os.path.join(common, "packed-refs")),)


_UPSTREAM = {}    # cwd → ((HEAD line, config mtime), upstream full ref name or '')
_LOCAL = {}       # cwd → (signature, (branch, ahead))


def _upstream_ref(cwd, head_line, common):
    """The full ref name of the branch's upstream ('refs/remotes/origin/x'), or ''. It changes only
    with the checked-out branch or the config, so it is re-read only when one of those moved."""
    key = (head_line, _mtime(os.path.join(common, "config")))
    hit = _UPSTREAM.get(cwd)
    if hit is not None and hit[0] == key:
        return hit[1]
    ref = ""
    if head_line.startswith(_SYMREF_PREFIX):
        ok, out = _git(cwd, "rev-parse", "--symbolic-full-name", "@{u}")
        ref = out if ok else ""
    if len(_UPSTREAM) > _MEMO_CAP:
        _UPSTREAM.clear()
    _UPSTREAM[cwd] = (key, ref)
    return ref


def local_state(cwd):
    """(branch, ahead, moved) for the checkout at cwd.

    `branch` is '' when detached or outside a repo. `ahead` counts commits on HEAD its upstream lacks (0
    with no upstream). `moved` is True when HEAD, the branch ref or its upstream moved since the last
    call: the event that says "re-read this repo's PRs". An unchanged checkout costs stats, no fork."""
    dirs = git_dirs(cwd)
    if not dirs:
        return "", 0, False
    gitdir, common = dirs
    head_line = _first_line(os.path.join(gitdir, "HEAD"))
    head_ref = head_line[len(_SYMREF_PREFIX):].strip() if head_line.startswith(_SYMREF_PREFIX) else ""
    upstream = _upstream_ref(cwd, head_line, common)
    sig = (head_line, upstream) + _ref_mtimes(common, [r for r in (head_ref, upstream) if r])
    hit = _LOCAL.get(cwd)
    if hit is not None and hit[0] == sig:
        return hit[1][0], hit[1][1], False
    branch = head_ref[len(_HEADS_PREFIX):] if head_ref.startswith(_HEADS_PREFIX) else ""
    ahead = 0
    if upstream:
        ok, out = _git(cwd, "rev-list", "--count", "@{u}..HEAD")
        ahead = int(out) if (ok and out.isdigit()) else 0
    if len(_LOCAL) > _MEMO_CAP:
        _LOCAL.clear()
    _LOCAL[cwd] = (sig, (branch, ahead))
    return branch, ahead, hit is not None


def commits_ahead_of_main(cwd):
    """Commits on HEAD that the repo's main branch lacks, or 0 when the count cannot be taken, which
    reads as "nothing to open a PR for" rather than "open one anyway"."""
    for base in _MAIN_BRANCHES:
        ok, out = _git(cwd, "rev-list", "--count", "origin/%s..HEAD" % base)
        if ok and out.isdigit():
            return int(out)
    return 0


# ── gh: the authoritative PR state ───────────────────────────────────────────────────────────────────
# ONE list call per repo, shared by every session in it; 100 rows covers the PRs a working day touches,
# and a cited PR older than that window is fetched on its own. statusCheckRollup is kept out of the list
# because asking for it across 100 PRs makes GitHub's GraphQL endpoint time out on a busy repo; checks
# are fetched only for the few PRs a session references.
_LIST_LIMIT = 100
_GH_FIELDS = ("number,title,url,headRefName,state,isDraft,reviewDecision,"
              "updatedAt,additions,deletions,changedFiles")
_CHECK_FIELDS = "number,statusCheckRollup"
_MAX_SINGLE_FETCHES = 12   # cited PRs outside the list window, per refresh
_MAX_CHECK_FETCHES = 12    # checks rollups per refresh
_MAX_FAILING_NAMES = 6
_BRANCH_CURRENT_SECS = 600   # a branch no session has asked about for this long is no longer current
# gh's answers for a PR that does not exist; anything else is a failure to show, not a number to forget
_GONE_MARKERS = ("no pull requests found", "could not resolve to a pullrequest")
_PR_STATES = ("open", "merged", "closed")

# Conclusions that mean a check FAILED. SKIPPED / NEUTRAL / SUCCESS are not failures.
_CHECK_BAD = ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")
_CHECK_PENDING = ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED")
# A commit status (StatusContext) carries `state` and `context` instead of `conclusion` and `name`.
_STATUS_CONTEXT = "StatusContext"
_STATUS_BAD = ("FAILURE", "ERROR")
_STATUS_PENDING = ("PENDING", "EXPECTED")


def _iso_to_epoch(s):
    """GitHub's '2026-08-17T10:00:00Z' as epoch seconds, or 0 when unparseable (no age, not a wrong one)."""
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return 0


def _check_verdict(c):
    """"bad", "running" or "ok" for one rollup entry, a check run or a commit status."""
    if c.get("__typename") == _STATUS_CONTEXT or ("state" in c and "conclusion" not in c):
        state = (c.get("state") or "").upper()
        return "bad" if state in _STATUS_BAD else "running" if (not state or state in _STATUS_PENDING) else "ok"
    concl = (c.get("conclusion") or "").upper()
    if concl in _CHECK_BAD:
        return "bad"
    return "running" if (not concl or (c.get("status") or "").upper() in _CHECK_PENDING) else "ok"


def _checks(rollup):
    """(state, failing names). A failure outranks a running check, since "running" reads as "nothing
    wrong yet". None means not asked yet: "unknown", distinct from "none" (this PR has no checks)."""
    if rollup is None:
        return "unknown", []
    failing, running, any_check = [], False, False
    for c in rollup or []:
        if not isinstance(c, dict):
            continue
        any_check = True
        verdict = _check_verdict(c)
        if verdict == "bad":
            failing.append(c.get("name") or c.get("context") or "check")
        elif verdict == "running":
            running = True
    if failing:
        return "fail", failing
    if running:
        return "running", []
    return ("pass", []) if any_check else ("none", [])


def normalize(raw):
    """One `gh pr list` row as the payload dict the pane renders, every enum lowercased. A row with no
    statusCheckRollup key (the cheap list query) yields checksState "unknown"."""
    state = (raw.get("state") or "").lower()
    cstate, failing = _checks(raw["statusCheckRollup"] if "statusCheckRollup" in raw else None)
    return {"num": int(raw.get("number") or 0),
            "url": raw.get("url") or "",
            "title": raw.get("title") or "",
            "branch": raw.get("headRefName") or "",
            "state": state if state in _PR_STATES else "open",
            "draft": bool(raw.get("isDraft")),
            "checksState": cstate,
            "checksFailing": failing[:_MAX_FAILING_NAMES],
            "reviewDecision": (raw.get("reviewDecision") or "").lower() or "none",
            "adds": int(raw.get("additions") or 0),
            "dels": int(raw.get("deletions") or 0),
            "files": int(raw.get("changedFiles") or 0),
            "updatedT": _iso_to_epoch(raw.get("updatedAt") or "")}


def branch_pr(prs, branch):
    """The PR number for `branch`, or None. A reused branch name can carry several PRs: an open one wins,
    the newest (largest number) first; else the newest of any state."""
    if not branch:
        return None
    on_branch = [n for n, pr in prs.items() if pr.get("branch") == branch]
    open_ones = [n for n in on_branch if prs[n].get("state") == "open"]
    pool = open_ones or on_branch
    return max(pool) if pool else None


def _gh_json(args, what):
    """Run gh and parse its JSON, or raise GitPrError carrying gh's own last stderr line."""
    try:
        p = subprocess.run([GH_BIN] + list(args), capture_output=True, text=True, timeout=_TIMEOUT)
    except FileNotFoundError:
        raise GitPrError("gh CLI not found on PATH — install it to see PR status")
    except Exception as e:
        raise GitPrError("%s failed: %s" % (what, str(e)[:160]))
    if p.returncode != 0:
        tail = [l for l in (p.stderr or "").strip().splitlines() if l.strip()]
        raise GitPrError(tail[-1][:200] if tail else "%s failed (exit %d)" % (what, p.returncode))
    try:
        return json.loads(p.stdout or "[]")
    except Exception:
        raise GitPrError("%s returned output that is not JSON" % what)


def hydrate(repo, limit=_LIST_LIMIT):
    """{number: PR} for one repo. Raises GitPrError on any gh failure."""
    rows = _gh_json(["pr", "list", "--repo", repo, "--state", "all", "--limit", str(limit),
                     "--json", _GH_FIELDS], "gh pr list")
    out = {}
    for r in rows if isinstance(rows, list) else []:
        pr = normalize(r)
        if pr["num"]:
            out[pr["num"]] = pr
    return out


def hydrate_head(repo, branch):
    """The newest PR whose head is `branch`, found even outside the list window, or None."""
    rows = _gh_json(["pr", "list", "--repo", repo, "--head", branch, "--state", "all", "--limit", "1",
                     "--json", _GH_FIELDS], "gh pr list --head")
    return normalize(rows[0]) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None


def _is_gone(err):
    return any(m in str(err).lower() for m in _GONE_MARKERS)


def hydrate_one(repo, num):
    """A single PR outside the list window. Same shape as hydrate's values."""
    row = _gh_json(["pr", "view", str(num), "--repo", repo, "--json", _GH_FIELDS], "gh pr view")
    return normalize(row) if isinstance(row, dict) else None


def _fill_checks(repo, prs, nums):
    """Fetch statusCheckRollup for `nums` (in order, bounded) into the UNPUBLISHED dict `prs`. A failed
    call leaves that PR "unknown" and returns the first reason, so the pane shows it and the poll retries."""
    first_err = ""
    for n in [n for n in nums if n in prs][:_MAX_CHECK_FETCHES]:
        try:
            row = _gh_json(["pr", "view", str(n), "--repo", repo, "--json", _CHECK_FIELDS], "gh pr view")
        except GitPrError as e:
            first_err = first_err or "checks for #%d: %s" % (n, e)
            continue
        state, failing = _checks((row or {}).get("statusCheckRollup"))
        prs[n] = dict(prs[n], checksState=state, checksFailing=failing[:_MAX_FAILING_NAMES])
    return first_err


# ── the cache ────────────────────────────────────────────────────────────────────────────────────────
# repo → {"prs": {num: PR}, "err": str, "fresh": bool}. A published entry is never mutated: a refresh
# assembles its dict privately and swaps the entry in whole, so a build-thread reader cannot see a dict
# change size under it. `_GEN` counts invalidations; a refresh that finishes after one happened publishes
# `fresh` False, so a push landing mid-refresh is re-read rather than lost.
_CACHE = {}
_GEN = {}          # repo → invalidation count
_WANTED = {}       # repo → every PR number a session has cited (cumulative, so none is later evicted)
_BRANCHES = {}     # repo → {branch: time.monotonic() a session last asked from it}; current ones come first
_TRIED = {}        # repo → cited numbers gh could not return, so they are not re-kicked forever
_INFLIGHT = set()  # repos with a refresh running
_LOCK = threading.Lock()


def invalidate(repo):
    """Mark a repo's PR set stale, including one being refreshed right now."""
    with _LOCK:
        _GEN[repo] = _GEN.get(repo, 0) + 1
        ent = _CACHE.get(repo)
        if ent and ent.get("fresh"):
            _CACHE[repo] = dict(ent, fresh=False)


def retry(repo):
    """A user asked for a re-read: forget which cited numbers gh could not return, then invalidate."""
    with _LOCK:
        _TRIED.pop(repo, None)
    invalidate(repo)


def _want(repo, nums, branch):
    """Record what a session needs from `repo`; invalidate when it names a number never asked for, so
    the next refresh fetches it (and checks its CI) instead of serving a set that lacks it."""
    with _LOCK:
        wanted = _WANTED.setdefault(repo, set())
        new = set(nums) - wanted
        wanted.update(new)
        if branch:
            _BRANCHES.setdefault(repo, {})[branch] = time.monotonic()
    if new:
        invalidate(repo)


def _current_branches(repo, now):
    """The branches sessions asked from within _BRANCH_CURRENT_SECS, most recent first; older ones go."""
    seen = _BRANCHES.get(repo) or {}
    for b in [b for b, t in seen.items() if now - t > _BRANCH_CURRENT_SECS]:
        del seen[b]
    return sorted(seen, key=seen.get, reverse=True)


def _check_order(prs, branches, wanted):
    """The PRs to fetch checks for: the current branches' own PRs first, then open cited PRs newest
    first, then the merged and closed ones."""
    heads = list(dict.fromkeys(n for n in (branch_pr(prs, b) for b in branches) if n is not None))
    cited = sorted((n for n in wanted if n in prs and n not in heads), reverse=True)
    return (heads + [n for n in cited if prs[n].get("state") == "open"]
            + [n for n in cited if prs[n].get("state") != "open"])


def _fetch_missing(repo, prs, wanted, branches, tried):
    """Fetch, into `prs`, cited PRs outside the list window and current branches with no PR in it.
    What gh says does not exist is remembered in `tried`; any other failure is returned to show."""
    asks = [("num", n) for n in sorted(wanted - set(prs) - tried, reverse=True)]
    asks += [("head", b) for b in branches if branch_pr(prs, b) is None and ("head", b) not in tried]
    first_err = ""
    for kind, key in asks[:_MAX_SINGLE_FETCHES]:
        try:
            one = hydrate_one(repo, key) if kind == "num" else hydrate_head(repo, key)
        except GitPrError as e:
            if not _is_gone(e):
                first_err = first_err or "PR %s: %s" % (key if kind == "head" else "#%d" % key, e)
                continue
            one = None
        if one:
            prs[one["num"]] = one
        else:
            with _LOCK:
                tried.add(key if kind == "num" else (kind, key))
    return first_err


def _assemble(repo, prs):
    """Complete a fresh list read in private: fetch what the window missed, then checks. Returns
    (prs, the first reason something could not be read, or "")."""
    with _LOCK:
        wanted = set(_WANTED.get(repo) or ())
        branches = _current_branches(repo, time.monotonic())
        tried = _TRIED.setdefault(repo, set())
    missing_err = _fetch_missing(repo, prs, wanted, branches, tried)
    checks_err = _fill_checks(repo, prs, _check_order(prs, branches, wanted))
    return prs, missing_err or checks_err


def _refresh(repo):
    """The background body: one list read, completed privately, published once."""
    with _LOCK:
        gen = _GEN.get(repo, 0)
    try:
        prs, err = _assemble(repo, hydrate(repo))
    except GitPrError as e:
        prs, err = None, str(e)
    with _LOCK:
        if prs is None:
            # Keep the last good snapshot beside the reason: the pane shows both, and the poll retries.
            prs = (_CACHE.get(repo) or {}).get("prs") or {}
        _CACHE[repo] = {"prs": prs, "err": err, "fresh": _GEN.get(repo, 0) == gen}


def repo_prs(repo, nums=(), branch=""):
    """(prs, error) for one repo, never blocking. gh is a network call (seconds on a busy repo) reached
    from the per-push build pass, so a stale entry serves what it has and refreshes in the background."""
    if not repo:
        return {}, ""
    _want(repo, nums, branch)
    ent = _CACHE.get(repo)
    if not (ent and ent.get("fresh")):
        _kick(repo)
    return (ent or {}).get("prs") or {}, (ent or {}).get("err") or ""


def _kick(repo):
    """Start one background refresh per repo, never two. A request made while one runs is not lost: it
    bumped the generation or the wanted set, which the running refresh re-reads or publishes as stale."""
    with _LOCK:
        if repo in _INFLIGHT:
            return
        _INFLIGHT.add(repo)

    def run():
        try:
            _refresh(repo)
        finally:
            with _LOCK:
                _INFLIGHT.discard(repo)

    threading.Thread(target=run, name="gitpr-" + repo, daemon=True).start()


def needs_poll(repo):
    """True while a cached PR's check is still running, or the last read failed. Neither CI finishing
    nor the network recovering produces a local event, so these are what the one poll waits on."""
    ent = _CACHE.get(repo) or {}
    if ent.get("err"):
        return True
    return any(pr.get("checksState") == "running" for pr in list((ent.get("prs") or {}).values()))


# ── the refresh events ───────────────────────────────────────────────────────────────────────────────
_POLLED = {}      # repo → time.monotonic() of the last poll-driven refresh
_POLL_SECS = 30   # the one interval in this module; see needs_poll


def note_local_state(cwd, repo):
    """(branch, ahead) for cwd, invalidating `repo` when HEAD, the branch or its upstream moved."""
    branch, ahead, moved = local_state(cwd)
    if moved:
        invalidate(repo)
    return branch, ahead


def note_push_turn(repo):
    """A turn ran git push / gh pr in this repo: the remote moved, so re-read it."""
    invalidate(repo)


def poll_due(repo, now):
    """True when the poll should re-read `repo` now: gated on needs_poll, paced by _POLL_SECS. `now` is
    time.monotonic(), passed in so a test can drive it."""
    if not needs_poll(repo):
        return False
    last = _POLLED.get(repo)
    if last is not None and (now - last) < _POLL_SECS:
        return False
    _POLLED[repo] = now
    invalidate(repo)
    return True
