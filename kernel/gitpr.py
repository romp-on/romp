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
import subprocess
import sys
import threading
import time

GIT_BIN = os.environ.get("ROMP_GIT_BIN", "git")
GH_BIN = os.environ.get("ROMP_GH_BIN", "gh")
PR_STATUS_ENV = "ROMP_PR_STATUS"     # "off" seeds the kernel's PR status setting off on a first boot
_TIMEOUT = 20        # a hung network call must never stall a refresh forever
_GIT_TIMEOUT = 8     # a local git read
_MEMO_CAP = 512      # bound on every per-directory memo below
_ENABLED = [os.environ.get(PR_STATUS_ENV, "").strip().lower() != "off"]   # the kernel sets it from its setting


def enabled():
    """True while PR status is on; off skips every git and gh read and the judge's stamp."""
    return _ENABLED[0]


def set_enabled(on):
    """Turn PR status on or off for this process."""
    _ENABLED[0] = bool(on)


# (owner, repo) out of any GitHub remote form git writes: scp-like ssh, including a per-account host alias
# (git@github.com-personal:owner/repo.git), ssh:// with an optional port or GitHub's ssh.github.com host,
# and https with an optional :443. Anchored, so github.example.com and github.com.evil.io never match. The
# kernel's chat links and file viewer read this same one.
GITHUB_REMOTE_RE = re.compile(
    r"^(?:git@github\.com(?:-[\w.-]+)?:|ssh://git@(?:ssh\.)?github\.com(?:-[\w.-]+)?(?::\d+)?/"
    r"|https://github\.com(?::443)?/)"
    r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")

_MAIN_BRANCHES = ("main", "master")
_GH_REMOTE_ORDER = ("upstream", "github", "origin")   # gh's own ranking when no default repo is set
_GH_RESOLVED_BASE = "base"                           # remote.<name>.gh-resolved as `gh repo set-default` writes it
_REMOTE_CONFIG_RE = r"^remote\..*\.(url|gh-resolved)$"
_DEFAULT_PUSH_REMOTE = "origin"
_REMOTES_PREFIX = "refs/remotes/"
_HEADS_PREFIX = "refs/heads/"
_SYMREF_PREFIX = "ref:"
_GITDIR_PREFIX = "gitdir:"

# A command that pushes or acts on a PR, read per SIMPLE COMMAND the way bash splits it: quotes, `$'...'`,
# `$( )`, backticks and arithmetic are scanned whole, comments and heredoc bodies are dropped, and each piece
# is read past VAR=value prefixes, reserved words, wrapper commands and git/gh global options. Words inside
# an argument (`grep -rn 'git push' docs/`) never count, and read-only subcommands (view / list / checks /
# diff / status) are absent: looking at a PR is not acting on it. The judge's PR-ref mining and the
# kernel's push counter both read this one.
PR_ACT_VERBS = frozenset(("create", "edit", "merge", "ready", "close", "reopen", "comment", "review"))
_NEWLINE = "\n"
_OPERATORS = ("&&", "||", ";;", "|&", ";", "&", "|", "(", ")")          # longest first
_SEPARATORS = frozenset(_OPERATORS + (_NEWLINE,))
_REDIRECTS = ("&>>", "&>", ">>", ">&", ">|", "<>", "<&", ">", "<")      # longest first; each takes a target word
_HERESTRING = "<<<"
_HEREDOC = "<<"
_METACHARS = " \t\r\n;&|()<>"
_WRAPPERS = frozenset(("env", "time", "command", "sudo", "nohup", "exec", "builtin"))
_RESERVED = frozenset(("if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "in",
                       "case", "esac", "select", "{", "}", "!", "[[", "]]"))
_SHELLS = frozenset(("bash", "sh", "zsh", "dash", "ksh"))             # `bash -c '...'` runs its argument
_NESTED_SHELL_DEPTH = 3
_GIT_VALUE_OPTS = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace"))
GH_REPO_OPTS = frozenset(("-R", "--repo"))
GH_REPO_ENV = "GH_REPO"                                   # gh's own repo override, as a prefix or an export
_TIMEOUT_VALUE_OPTS = frozenset(("-s", "--signal", "-k", "--kill-after"))
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class GitPrError(Exception):
    """A gh call that could not answer, carrying the reason verbatim so a caller can render it."""


def _scan_single(s, i):
    """(text, end, closed) of a '...' body starting at `i`, just past the opening quote."""
    j = s.find("'", i)
    return (s[i:], len(s), False) if j < 0 else (s[i:j], j + 1, True)


def _scan_ansi(s, i):
    """(text, end, closed) of a $'...' body, where a backslash escapes the next character."""
    out = []
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
            continue
        if c == "'":
            return "".join(out), i + 1, True
        out.append(c)
        i += 1
    return "".join(out), i, False


def _scan_arith(s, i):
    """(end, closed) of a $(( )) body: a `<<` inside it is a shift, never a heredoc."""
    depth = 0
    while i < len(s):
        if s.startswith("))", i) and depth == 0:
            return i + 2, True
        depth += {"(": 1, ")": -1}.get(s[i], 0)
        i += 1
    return i, False


def _scan_backtick(s, i, nested):
    """(end, closed) of a `...` body; the commands inside it are lexed into `nested`."""
    out = []
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
            continue
        if c == "`":
            nested.append(_lex("".join(out), nested)[0])
            return i + 1, True
        out.append(c)
        i += 1
    return i, False


def _scan_double(s, i, nested):
    """(text, end, closed) of a "..." body, with $( ), $(( )) and backticks inside it scanned whole."""
    out = []
    while i < len(s):
        c = s[i]
        if c == '"':
            return "".join(out), i + 1, True
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt != _NEWLINE:
                out.append(nxt if nxt in '"\\$`' else c + nxt)
            i += 2
            continue
        if s.startswith("$((", i):
            j, ok = _scan_arith(s, i + 3)
        elif s.startswith("$(", i):
            sub, j, ok = _lex(s, nested, i + 2, in_sub=True)
            nested.append(sub)
        elif c == "`":
            j, ok = _scan_backtick(s, i + 1, nested)
        else:
            out.append(c)
            i += 1
            continue
        out.append(s[i:j])
        i = j
        if not ok:
            return "".join(out), i, False
    return "".join(out), i, False


def _heredoc_word(s, i):
    """(delimiter, end) of the word after `<<`: quotes and backslashes removed, as bash compares it."""
    while i < len(s) and s[i] in " \t":
        i += 1
    out = []
    while i < len(s) and s[i] not in _METACHARS:
        c = s[i]
        if c in "'\"":
            j = s.find(c, i + 1)
            j = len(s) if j < 0 else j
            out.append(s[i + 1:j])
            i = j + 1
        elif c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out), i


def _skip_heredocs(s, i, pending):
    """The index past every pending heredoc body, each ending at a line that is exactly its delimiter (leading
    tabs stripped for `<<-`). A body is raw: no quote, comment or continuation inside it means anything."""
    for delim, strip_tabs in pending:
        while i < len(s):
            j = s.find(_NEWLINE, i)
            line = s[i:j if j >= 0 else len(s)]
            i = j + 1 if j >= 0 else len(s)
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
    pending.clear()
    return i


def _lex(s, nested, i=0, in_sub=False):
    """(tokens, end, closed) for shell text from `i`: words with their quotes removed, and each operator and
    unquoted newline as its own token. Commands inside $( ) and backticks are appended to `nested`. With
    `in_sub` the scan ends at the `)` closing a $( ). A construct left open at the end drops the line it
    started on, since bash runs no part of a command it cannot finish parsing."""
    toks, word = [], []
    started = quoted = skip_target = False
    pending, line_mark, depth = [], 0, 0

    def end_word():
        nonlocal started, quoted, skip_target
        if started:
            if skip_target:
                skip_target = False
            else:
                toks.append("".join(word))
        word.clear()
        started = quoted = False

    def unclosed(at):
        del toks[line_mark:]
        return toks, at, False

    while i < len(s):
        c = s[i]
        if c == "\\":
            if s.startswith("\\\n", i):
                i += 2
                continue
            word.append(s[i + 1:i + 2])
            started = quoted = True
            i += 2
        elif c == "'":
            text, i, ok = _scan_single(s, i + 1)
            word.append(text)
            started = quoted = True
            if not ok:
                return unclosed(i)
        elif c == '"':
            text, i, ok = _scan_double(s, i + 1, nested)
            word.append(text)
            started = quoted = True
            if not ok:
                return unclosed(i)
        elif s.startswith("$'", i):
            text, i, ok = _scan_ansi(s, i + 2)
            word.append(text)
            started = quoted = True
            if not ok:
                return unclosed(i)
        elif s.startswith("$((", i) or s.startswith("$(", i) or c == "`":
            if s.startswith("$((", i):
                j, ok = _scan_arith(s, i + 3)
            elif c == "`":
                j, ok = _scan_backtick(s, i + 1, nested)
            else:
                sub, j, ok = _lex(s, nested, i + 2, in_sub=True)
                nested.append(sub)
            word.append(s[i:j])
            started, i = True, j
            if not ok:
                return unclosed(i)
        elif c == "#" and not started:
            j = s.find(_NEWLINE, i)
            i = len(s) if j < 0 else j            # a comment ends at its newline; a backslash never continues it
        elif c in " \t\r":
            end_word()
            i += 1
        elif c == _NEWLINE:
            end_word()
            toks.append(_NEWLINE)
            i = _skip_heredocs(s, i + 1, pending)
            line_mark = len(toks)
        elif c in "<>" or s.startswith("&>", i):
            if started and not quoted and "".join(word).isdigit():
                word.clear()
                started = False                   # the fd of `2>&1`
            end_word()
            if s.startswith(_HERESTRING, i):
                i += len(_HERESTRING)
                skip_target = True
            elif s.startswith(_HEREDOC, i):
                i += len(_HEREDOC)
                strip_tabs = s.startswith("-", i)
                delim, i = _heredoc_word(s, i + strip_tabs)
                pending.append((delim, strip_tabs))
            else:
                op = next(o for o in _REDIRECTS if s.startswith(o, i))
                i += len(op)
                skip_target = True
        elif c in ";&|()":
            end_word()
            if in_sub and c == ")" and depth == 0:
                return toks, i + 1, True
            if in_sub:
                depth += 1 if c == "(" else -1 if c == ")" else 0
            op = next(o for o in _OPERATORS if s.startswith(o, i))
            toks.append(op)
            i += len(op)
        else:
            word.append(c)
            started = True
            i += 1
    end_word()
    return unclosed(i) if in_sub else (toks, i, True)


def _split(tokens):
    """Token lists, one per simple command, split at every operator and newline."""
    out, cur = [], []
    for tok in tokens:
        if tok in _SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _shell_arg(words):
    """The script a `bash -c '...'` runs, or None when `words` is no such call."""
    if not words or os.path.basename(words[0]) not in _SHELLS:
        return None
    for j, w in enumerate(words[1:], 1):
        if not w.startswith("-") or w.startswith("--"):
            return None
        if "c" in w[1:]:
            return words[j + 1] if j + 1 < len(words) else None
    return None


def simple_commands(cmd, _depth=0):
    """The shell text `cmd` as a list of token lists, one per simple command: those inside $( ) and
    backticks, and a `bash -c` script's, are read as commands of their own."""
    nested = []
    tokens = _lex(cmd or "", nested)[0]
    out = []
    for stream in [tokens] + nested:
        for piece in _split(stream):
            out.append(piece)
            script = _shell_arg(_command_words(piece))
            if script is not None and _depth < _NESTED_SHELL_DEPTH:
                out += simple_commands(script, _depth + 1)
    return out


def _command_words(tokens):
    """`tokens` past VAR=value prefixes, reserved words and wrapper commands (`env FOO=1`, `time`,
    `timeout 60`, `if`, `do`, `{`)."""
    i = 0
    while i < len(tokens):
        if _ASSIGN_RE.match(tokens[i]) or tokens[i] in _WRAPPERS or tokens[i] in _RESERVED:
            i += 1
        elif tokens[i] == "timeout":
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 2 if tokens[i] in _TIMEOUT_VALUE_OPTS else 1
            i += 1                                 # the duration
        else:
            break
    return tokens[i:]


def _past_options(words, value_opts):
    """`words` past leading options, where each of `value_opts` also consumes the word after it."""
    i = 0
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in value_opts else 1
    return words[i:]


def _names_repo(word):
    """True for a gh option naming a repo: `-R x`, `--repo x`, `--repo=x` or an attached `-Rx`."""
    return word in GH_REPO_OPTS or word.startswith("--repo=") or (word.startswith("-R") and len(word) > 2)


def gh_pr_action(tokens, repo_exported=False):
    """(verb, words after it, names another repo) when `tokens` is a PR-acting gh command, else None.
    `repo_exported` says an earlier command of the same call exported GH_REPO."""
    words = _command_words(tokens)
    if not words or words[0] != "gh":
        return None
    other_repo = (repo_exported or any(_names_repo(w) for w in words)
                  or any(t.startswith(GH_REPO_ENV + "=") for t in tokens[:len(tokens) - len(words)]))
    rest = _past_options(words[1:], GH_REPO_OPTS)
    if len(rest) < 2 or rest[0] != "pr" or rest[1] not in PR_ACT_VERBS:
        return None
    return rest[1], rest[2:], other_repo


def _exports_repo(tokens, exported):
    """Whether GH_REPO is exported once this command has run, given whether it was before."""
    words = _command_words(tokens)
    if words[:1] == ["export"] and any(w.startswith(GH_REPO_ENV + "=") for w in words[1:]):
        return True
    if words[:1] == ["unset"] and GH_REPO_ENV in words[1:]:
        return False
    return exported


def pr_actions(cmd):
    """Every PR-acting gh command in the shell text `cmd`, as gh_pr_action's triples, in order."""
    if "gh" not in (cmd or ""):
        return []
    out, exported = [], False
    for tokens in simple_commands(cmd):
        act = gh_pr_action(tokens, exported)
        if act:
            out.append(act)
        exported = _exports_repo(tokens, exported)
    return out


def _is_git_push(tokens):
    words = _command_words(tokens)
    if not words or words[0] != "git":
        return False
    rest = _past_options(words[1:], _GIT_VALUE_OPTS)
    return bool(rest) and rest[0] == "push"


def is_push_command(cmd):
    """True when a Bash command pushed or acted on a PR: a refresh event."""
    if "push" not in (cmd or "") and "gh" not in (cmd or ""):
        return False                                   # the reader's cost is paid only where a match is possible
    return any(_is_git_push(t) or gh_pr_action(t) for t in simple_commands(cmd))


def _git(cwd, *args, timeout=_GIT_TIMEOUT):
    """(ok, stdout, ran); never raises. `ran` False means git did not answer (absent binary, timeout), which
    is no verdict and is never memoized; a non-zero exit is git's answer ("not a repo", "no upstream")."""
    if not cwd or not os.path.isdir(cwd):
        return False, "", True
    try:
        p = subprocess.run([GIT_BIN, "-C", cwd] + list(args), capture_output=True, text=True,
                           timeout=timeout)
    except Exception:
        return False, "", False
    return p.returncode == 0, (p.stdout or "").strip(), True


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


def parse_remote(url):
    """'owner/repo' of a GitHub remote url in any form git writes, or ''."""
    m = GITHUB_REMOTE_RE.match((url or "").strip())
    return "%s/%s" % (m.group(1), m.group(2)) if m else ""


def same_repo(a, b):
    """True when two 'owner/repo' slugs name one GitHub repo, which compares them without case."""
    return bool(a) and bool(b) and a.lower() == b.lower()


_REMOTES = {}     # commondir → ((remote name → slug, gh's resolved choice), config mtime)


def _remotes(cwd):
    """(remote name → 'owner/repo' for its GitHub url, the repo `gh repo set-default` chose or '') for the
    checkout, memoized on the config file's mtime; None when git did not answer."""
    dirs = git_dirs(cwd)
    if not dirs:
        return {}, ""
    config_mtime = _mtime(os.path.join(dirs[1], "config"))
    hit = _REMOTES.get(dirs[1])
    if hit is not None and hit[1] == config_mtime:
        return hit[0]
    ok, out, ran = _git(cwd, "config", "--get-regexp", _REMOTE_CONFIG_RE)
    if not ran:
        return None
    slugs, resolved, resolved_remote = {}, "", ""
    for line in out.splitlines() if ok else ():
        key, _, value = line.partition(" ")
        name, _, field = key[len("remote."):].rpartition(".")
        if field == "url":
            slugs[name] = parse_remote(value)
        elif value == _GH_RESOLVED_BASE:
            resolved_remote = name
        elif value:
            resolved = parse_remote("https://github.com/" + value)
    resolved = resolved or slugs.get(resolved_remote, "")
    if len(_REMOTES) > _MEMO_CAP:
        _REMOTES.clear()
    _REMOTES[dirs[1]] = ((slugs, resolved), config_mtime)
    return slugs, resolved


def base_repo(cwd):
    """'owner/repo' of the GitHub repo a bare `gh pr` command in this checkout acts on, else '': the repo
    `gh repo set-default` recorded, then gh's own remote order (upstream, github, origin, the rest)."""
    got = _remotes(cwd)
    if not got:
        return ""
    slugs, resolved = got
    if resolved:
        return resolved
    for name in list(_GH_REMOTE_ORDER) + sorted(n for n in slugs if n not in _GH_REMOTE_ORDER):
        if slugs.get(name):
            return slugs[name]
    return ""


def repo_of(cwd):
    """base_repo for the PR chip, or '' with PR status off, so no remote is read and no row carries a PR."""
    return base_repo(cwd) if enabled() else ""


def head_owner(cwd, remote):
    """The lowercased owner of the repo `remote` names, where the branch's pushes land, or ''."""
    got = _remotes(cwd) or ({}, "")
    slug = got[0].get(remote or _DEFAULT_PUSH_REMOTE) or ""
    return slug.split("/")[0].lower()


_PACKED = {}      # commondir → (packed-refs mtime, {ref: sha})


def _ref_sha(common, ref):
    """The sha a ref names, from its loose file or packed-refs, or ''."""
    loose = _first_line(os.path.join(common, ref))
    if loose:
        return loose
    path = os.path.join(common, "packed-refs")
    mtime = _mtime(path)
    hit = _PACKED.get(common)
    if hit is None or hit[0] != mtime:
        table = {}
        try:
            with open(path) as f:
                for line in f:
                    sha, _, name = line.strip().partition(" ")
                    if name and not sha.startswith(("#", "^")):
                        table[name] = sha
        except OSError:
            pass
        if len(_PACKED) > _MEMO_CAP:
            _PACKED.clear()
        _PACKED[common] = hit = (mtime, table)
    return hit[1].get(ref, "")


_UPSTREAM = {}    # cwd → ((HEAD line, config mtime), upstream full ref name or '')
_LOCAL = {}       # cwd → (moved key, ahead key, (branch, ahead, remote, tracking sha))


def _upstream_ref(cwd, head_line, common):
    """The full ref name of the branch's upstream ('refs/remotes/origin/x'), or ''. It changes only
    with the checked-out branch or the config, so it is re-read only when one of those moved; None when git
    did not answer."""
    key = (head_line, _mtime(os.path.join(common, "config")))
    hit = _UPSTREAM.get(cwd)
    if hit is not None and hit[0] == key:
        return hit[1]
    ref = ""
    if head_line.startswith(_SYMREF_PREFIX):
        ok, out, ran = _git(cwd, "rev-parse", "--symbolic-full-name", "@{u}")
        if not ran:
            return None
        ref = out if ok else ""
    if len(_UPSTREAM) > _MEMO_CAP:
        _UPSTREAM.clear()
    _UPSTREAM[cwd] = (key, ref)
    return ref


def _tracking_ref(common, branch, upstream):
    """(remote, ref) the branch's pushes land on: `refs/remotes/<remote>/<branch>` when it exists, so a
    branch cut from origin/main and pushed without -u counts against its own ref, else the upstream."""
    remote = upstream[len(_REMOTES_PREFIX):].split("/")[0] if upstream.startswith(_REMOTES_PREFIX) else ""
    same_name = "%s%s/%s" % (_REMOTES_PREFIX, remote or _DEFAULT_PUSH_REMOTE, branch)
    if branch and _ref_sha(common, same_name):
        return remote or _DEFAULT_PUSH_REMOTE, same_name
    return remote, upstream


def local_state(cwd):
    """(branch, ahead, moved) for the checkout at cwd; see local_state_ex."""
    return local_state_ex(cwd)[:3]


def local_state_ex(cwd):
    """(branch, ahead, moved, remote, tracking sha) for the checkout at cwd.

    `branch` is '' when detached or outside a repo. `ahead` counts commits on HEAD its tracking ref lacks
    (0 with none). `moved` is True when the branch, its upstream or the tracking ref's sha changed since
    the last call: a push or a fetch, the event that says "re-read this repo's PRs". A local commit moves
    only `ahead`, which is recounted without that event. An unchanged checkout costs stats, no fork."""
    dirs = git_dirs(cwd)
    if not dirs:
        return "", 0, False, "", ""
    gitdir, common = dirs
    head_line = _first_line(os.path.join(gitdir, "HEAD"))
    head_ref = head_line[len(_SYMREF_PREFIX):].strip() if head_line.startswith(_SYMREF_PREFIX) else ""
    branch = head_ref[len(_HEADS_PREFIX):] if head_ref.startswith(_HEADS_PREFIX) else ""
    upstream = _upstream_ref(cwd, head_line, common)
    if upstream is None:
        return branch, 0, False, "", ""
    remote, track = _tracking_ref(common, branch, upstream)
    track_sha = _ref_sha(common, track) if track else ""
    moved_key = (branch, upstream, track, track_sha)
    ahead_key = (head_line, _ref_sha(common, head_ref) if head_ref else "", _mtime(os.path.join(common, "packed-refs")))
    hit = _LOCAL.get(cwd)
    if hit is not None and hit[0] == moved_key and hit[1] == ahead_key:
        return hit[2][0], hit[2][1], False, hit[2][2], hit[2][3]
    ahead = 0
    if track:
        ok, out, ran = _git(cwd, "rev-list", "--count", "%s..HEAD" % track)
        if not ran:
            return branch, 0, False, remote, track_sha
        ahead = int(out) if (ok and out.isdigit()) else 0
    if len(_LOCAL) > _MEMO_CAP:
        _LOCAL.clear()
    _LOCAL[cwd] = (moved_key, ahead_key, (branch, ahead, remote, track_sha))
    return branch, ahead, hit is not None and hit[0] != moved_key, remote, track_sha


def commits_ahead_of_main(cwd):
    """Commits on HEAD that the repo's main branch lacks, or 0 when the count cannot be taken, which
    reads as "nothing to open a PR for" rather than "open one anyway"."""
    for base in _MAIN_BRANCHES:
        ok, out, _ran = _git(cwd, "rev-list", "--count", "origin/%s..HEAD" % base)
        if ok and out.isdigit():
            return int(out)
    return 0


# ── gh: the authoritative PR state ───────────────────────────────────────────────────────────────────
# ONE list call per repo, shared by every session in it; 100 rows covers the PRs a working day touches,
# and a cited PR older than that window is fetched on its own. statusCheckRollup is kept out of the list
# because asking for it across 100 PRs makes GitHub's GraphQL endpoint time out on a busy repo; checks
# are fetched only for the few PRs a session references.
_LIST_LIMIT = 100
_HEAD_LIMIT = 5            # rows a head lookup reads, so a same-named branch on another fork is passed over
_GH_FIELDS = ("number,title,url,headRefName,headRefOid,headRepositoryOwner,state,isDraft,reviewDecision,"
              "updatedAt,additions,deletions,changedFiles")
_CHECK_FIELDS = "number,headRefOid,statusCheckRollup"
_MAX_SINGLE_FETCHES = 12   # PRs fetched on their own per refresh: branch heads, then cited PRs outside the window
_MAX_CHECK_FETCHES = 12    # checks rollups per refresh
_MAX_FAILING_NAMES = 6
_ERR_CHARS = 300           # gh's stderr kept for the chip, whole lines joined
_BRANCH_CURRENT_SECS = 600   # backstop: a branch no session has asked about for this long is no longer current
_HEAD_WAIT_POLLS = 10      # polls a pushed head may take to reach GitHub before the chip stops waiting for it
# gh's answers for a PR that does not exist; anything else is a failure to show, not a number to forget
_GONE_MARKERS = ("no pull requests found", "could not resolve to a pullrequest")
_PR_STATES = ("open", "merged", "closed")
_UNSETTLED_CHECKS = ("running", "unknown")

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
        name = c.get("name") or c.get("context") or "check"
        if verdict == "bad" and name not in failing:
            failing.append(name)
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
    owner = raw.get("headRepositoryOwner")
    return {"num": int(raw.get("number") or 0),
            "url": raw.get("url") or "",
            "title": raw.get("title") or "",
            "branch": raw.get("headRefName") or "",
            "head": raw.get("headRefOid") or "",
            "headOwner": ((owner.get("login") if isinstance(owner, dict) else "") or "").lower(),
            "state": state if state in _PR_STATES else "open",
            "draft": bool(raw.get("isDraft")),
            "checksState": cstate,
            "checksFailing": failing[:_MAX_FAILING_NAMES],
            "reviewDecision": (raw.get("reviewDecision") or "").lower() or "none",
            "adds": int(raw.get("additions") or 0),
            "dels": int(raw.get("deletions") or 0),
            "files": int(raw.get("changedFiles") or 0),
            "updatedT": _iso_to_epoch(raw.get("updatedAt") or "")}


def branch_pr(prs, branch, owner=""):
    """The PR number for `branch`, or None. With `owner`, a PR whose head lives on another fork is passed
    over. A reused branch name can carry several PRs: an open one wins, the newest first; else the newest."""
    if not branch:
        return None
    on_branch = [n for n, pr in list(prs.items()) if pr.get("branch") == branch
                 and not (owner and pr.get("headOwner") and pr["headOwner"] != owner)]
    open_ones = [n for n in on_branch if prs[n].get("state") == "open"]
    pool = open_ones or on_branch
    return max(pool) if pool else None


def _gh_json(args, what):
    """Run gh and parse its JSON, or raise GitPrError carrying gh's own stderr."""
    try:
        p = subprocess.run([GH_BIN] + list(args), capture_output=True, text=True, timeout=_TIMEOUT)
    except FileNotFoundError:
        raise GitPrError("gh CLI not found on PATH — install it to see PR status")
    except subprocess.TimeoutExpired:
        raise GitPrError("%s timed out after %d s" % (what, _TIMEOUT))
    except Exception as e:
        raise GitPrError("%s failed: %s" % (what, str(e)[:160]))
    if p.returncode != 0:
        lines = [l.strip() for l in (p.stderr or "").strip().splitlines() if l.strip()]
        raise GitPrError(" ".join(lines)[:_ERR_CHARS] if lines else "%s failed (exit %d)" % (what, p.returncode))
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


def hydrate_head(repo, branch, owner=""):
    """The newest PR whose head is `branch` on `owner`'s fork, found even outside the list window, or None."""
    rows = _gh_json(["pr", "list", "--repo", repo, "--head", branch, "--state", "all",
                     "--limit", str(_HEAD_LIMIT), "--json", _GH_FIELDS], "gh pr list --head")
    prs = {pr["num"]: pr for pr in (normalize(r) for r in (rows if isinstance(rows, list) else [])
                                    if isinstance(r, dict)) if pr["num"]}
    num = branch_pr(prs, branch, owner)
    return prs[num] if num else None


def _is_gone(err):
    return any(m in str(err).lower() for m in _GONE_MARKERS)


def hydrate_one(repo, num):
    """A single PR outside the list window. Same shape as hydrate's values."""
    row = _gh_json(["pr", "view", str(num), "--repo", repo, "--json", _GH_FIELDS], "gh pr view")
    return normalize(row) if isinstance(row, dict) else None


def _fill_checks(repo, prs, nums, cached):
    """Fetch statusCheckRollup and the head sha for `nums` (in order, bounded) into the UNPUBLISHED dict
    `prs`. A failed call keeps the verdict `cached` held for that PR and returns the first reason, so the
    pane shows it beside the last known state and the poll retries."""
    first_err = ""
    for n in [n for n in nums if n in prs][:_MAX_CHECK_FETCHES]:
        try:
            row = _gh_json(["pr", "view", str(n), "--repo", repo, "--json", _CHECK_FIELDS], "gh pr view")
        except GitPrError as e:
            first_err = first_err or "checks for #%d: %s" % (n, e)
            old = cached.get(n) or {}
            if old.get("checksState") not in (None, "unknown"):
                prs[n] = dict(prs[n], checksState=old["checksState"], checksFailing=old.get("checksFailing") or [])
            continue
        state, failing = _checks((row or {}).get("statusCheckRollup"))
        prs[n] = dict(prs[n], checksState=state, checksFailing=failing[:_MAX_FAILING_NAMES],
                      head=(row or {}).get("headRefOid") or prs[n].get("head") or "")
    return first_err


# ── the cache ────────────────────────────────────────────────────────────────────────────────────────
# repo → {"prs": {num: PR}, "err": str, "fullErr": bool, "fresh": bool}. A published entry is never
# mutated: a refresh assembles its dict privately and swaps the entry in whole, so a build-thread reader
# cannot see a dict change size under it. `_GEN` counts invalidations; a refresh that finishes after one
# happened publishes `fresh` False, so a push landing mid-refresh is re-read rather than lost.
_CACHE = {}
_GEN = {}          # repo → invalidation count
_WANTED = {}       # repo → every PR number a session has cited (cumulative, so none is later evicted)
_BRANCHES = {}     # repo → {branch: (time.monotonic() a session last asked from it, head owner)}
_SESSION_BRANCH = {}   # (repo, session id) → the branch that session last reported
_TRIED = {}        # repo → cited numbers and ("head", branch) lookups gh said do not exist
_EXPECT = {}       # repo → {branch: [sha pushed, polls left]}: a push GitHub has not shown on the PR yet
_FULL = set()      # repos whose next refresh re-reads the list; a poll alone re-reads only unsettled checks
_INFLIGHT = set()  # repos with a refresh running
_FAILING = {}      # repo → the error last logged, so each failure and recovery is said once
_LOCK = threading.Lock()


def invalidate(repo, full=True):
    """Mark a repo's PR set stale, including one being refreshed right now. `full` False (the poll) asks
    only for the watched checks still running or unread."""
    with _LOCK:
        if full:
            _FULL.add(repo)
        _GEN[repo] = _GEN.get(repo, 0) + 1
        ent = _CACHE.get(repo)
        if ent and ent.get("fresh"):
            _CACHE[repo] = dict(ent, fresh=False)


def retry(repo):
    """A user asked for a re-read: forget which cited numbers gh could not return, then invalidate."""
    with _LOCK:
        _TRIED.pop(repo, None)
        _POLL_BACKOFF.pop(repo, None)
    invalidate(repo)


def _want(repo, nums, branch, owner="", sid=""):
    """Record what a session needs from `repo`; invalidate when it names a number never asked for, or a
    branch no session was on whose PR is not cached yet, so the next refresh fetches it."""
    with _LOCK:
        wanted = _WANTED.setdefault(repo, set())
        new = set(nums) - wanted
        wanted.update(new)
        seen = _BRANCHES.setdefault(repo, {})
        prev = _SESSION_BRANCH.get((repo, sid))
        if branch and prev and prev != branch and not any(b == prev for k, b in _SESSION_BRANCH.items()
                                                          if k[0] == repo and k[1] != sid):
            seen.pop(prev, None)                  # the session switched off that branch, and nobody else is on it
        new_branch = bool(branch) and branch not in seen
        if branch:
            if len(_SESSION_BRANCH) > _MEMO_CAP:
                _SESSION_BRANCH.clear()
            _SESSION_BRANCH[(repo, sid)] = branch
            seen[branch] = (time.monotonic(), owner)
        cached = (_CACHE.get(repo) or {}).get("prs") or {}
        new_branch = new_branch and branch_pr(cached, branch, owner) is None
    if new or new_branch:
        invalidate(repo)


def _current_branches(repo, now):
    """[(branch, head owner)] sessions asked from within _BRANCH_CURRENT_SECS, most recent first."""
    seen = _BRANCHES.get(repo) or {}
    for b in [b for b, v in seen.items() if now - v[0] > _BRANCH_CURRENT_SECS]:
        del seen[b]
    return [(b, seen[b][1]) for b in sorted(seen, key=lambda b: seen[b][0], reverse=True)]


def _branch_prs(prs, branches):
    """The current branches' own PR numbers, in branch order."""
    return list(dict.fromkeys(n for n in (branch_pr(prs, b, o) for b, o in branches) if n is not None))


def _check_order(prs, branches, wanted):
    """The PRs to fetch checks for: the current branches' own PRs first, then open cited PRs newest
    first, then the merged and closed ones."""
    heads = _branch_prs(prs, branches)
    cited = sorted((n for n in wanted if n in prs and n not in heads), reverse=True)
    return (heads + [n for n in cited if prs[n].get("state") == "open"]
            + [n for n in cited if prs[n].get("state") != "open"])


def _fetch_missing(repo, prs, wanted, branches, tried, carried):
    """Fetch, into `prs`, current branches with no PR in it, then cited PRs outside the list window never
    read, then `carried` open or closed singles (which can change state) again, within the per-refresh
    budget. What gh says does not exist is remembered in `tried`. Returns (first failure to show, True when
    a head or cited PR was left unasked for the next refresh)."""
    asks = [("head", b, o) for b, o in branches if branch_pr(prs, b, o) is None and ("head", b) not in tried]
    asks += [("num", n, "") for n in sorted(wanted - set(prs) - tried, reverse=True)]
    first_new = len(asks)
    asks += [("num", n, "") for n in sorted(carried, reverse=True) if prs[n].get("state") != "merged"]
    first_err = ""
    for kind, key, owner in asks[:_MAX_SINGLE_FETCHES]:
        try:
            one = hydrate_one(repo, key) if kind == "num" else hydrate_head(repo, key, owner)
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
    return first_err, first_new > _MAX_SINGLE_FETCHES


def _watch(repo):
    """(wanted numbers, current branches) under the lock: the PRs a session cited or is on."""
    with _LOCK:
        return set(_WANTED.get(repo) or ()), _current_branches(repo, time.monotonic())


def _assemble(repo, prs, cached, carried):
    """Complete a fresh list read in private: fetch what the window missed, then checks. Returns (prs,
    the first reason something could not be read or "", True when some head or cited PR is still unasked)."""
    wanted, branches = _watch(repo)
    with _LOCK:
        tried = _TRIED.setdefault(repo, set())
    missing_err, more = _fetch_missing(repo, prs, wanted, branches, tried, carried)
    checks_err = _fill_checks(repo, prs, _check_order(prs, branches, wanted), cached)
    return prs, missing_err or checks_err, more


def _awaited(repo, prs, branches):
    """Branch PR numbers whose head on GitHub is not yet the sha a push recorded."""
    out = []
    for branch, owner in branches:
        exp = (_EXPECT.get(repo) or {}).get(branch)
        n = branch_pr(prs, branch, owner)
        if exp and n is not None and prs[n].get("state") == "open" and prs[n].get("head") != exp[0]:
            out.append(n)
    return out


def _settle_expectations(repo, prs, branches, polled):
    """Drop each push expectation GitHub now shows, or that has waited _HEAD_WAIT_POLLS polls."""
    with _LOCK:
        exp = _EXPECT.get(repo) or {}
        for branch, owner in branches:
            n = branch_pr(prs, branch, owner)
            if branch in exp and n is not None and prs[n].get("head") == exp[branch][0]:
                del exp[branch]
            elif branch in exp and polled:
                exp[branch][1] -= 1
                if exp[branch][1] <= 0:
                    del exp[branch]


def _watched_unsettled(prs, wanted, branches):
    """The watched PRs whose checks are still running or unread, open ones only, in check order."""
    return [n for n in _check_order(prs, branches, wanted)
            if prs[n].get("state") == "open" and prs[n].get("checksState") in _UNSETTLED_CHECKS]


def _recheck(repo, cached):
    """A poll's refresh: the cached set, with only the watched PRs' unsettled checks, and a pushed head
    GitHub has not shown yet, re-read."""
    wanted, branches = _watch(repo)
    prs = dict(cached)
    nums = list(dict.fromkeys(_awaited(repo, prs, branches) + _watched_unsettled(prs, wanted, branches)))
    err = _fill_checks(repo, prs, nums, cached)
    _settle_expectations(repo, prs, branches, polled=True)
    return prs, err, False


def _full_read(repo, cached):
    """An event's refresh: the list, with PRs outside the window that a session cites or is on carried
    from the last read rather than dropped."""
    prs = hydrate(repo)
    wanted, branches = _watch(repo)
    keep = wanted | set(_branch_prs(cached, branches))
    carried = {n for n in cached if n not in prs and n in keep}
    for n in carried:
        prs[n] = cached[n]
    out = _assemble(repo, prs, cached, carried)
    _settle_expectations(repo, prs, branches, polled=False)
    return out


def _note_outcome(repo, err):
    """Log a repo's PR reads starting to fail, the backoff reaching its ceiling, and recovery, once each."""
    was = _FAILING.get(repo)
    if err and was is None:
        _FAILING[repo] = err
        print("gitpr: PR status for %s could not be read: %s" % (repo, err[:_ERR_CHARS]), file=sys.stderr)
    elif not err and was is not None:
        del _FAILING[repo]
        print("gitpr: PR status for %s reads again" % repo, file=sys.stderr)


def _refresh(repo):
    """The background body: one read, completed privately, published once."""
    with _LOCK:
        gen = _GEN.get(repo, 0)
        ent = _CACHE.get(repo)
        full = repo in _FULL or ent is None or bool(ent.get("fullErr"))
        _FULL.discard(repo)
        cached = (ent or {}).get("prs") or {}
    prs, more = None, False
    try:
        prs, err, more = _full_read(repo, cached) if full else _recheck(repo, cached)
    except GitPrError as e:
        err = str(e)
    except Exception as e:                     # a malformed row is a failure to show, never a fresh read
        err = "PR status read failed: %s: %s" % (type(e).__name__, str(e)[:160])
    with _LOCK:
        if prs is None:
            # Keep the last good snapshot beside the reason: the pane shows both, and the poll retries.
            prs = cached
        fresh = _GEN.get(repo, 0) == gen and not more
        if more:
            _FULL.add(repo)                        # the unasked heads and numbers wait for the next full read
        _CACHE[repo] = {"prs": prs, "err": err, "fullErr": bool(err) and full, "fresh": fresh}
        if not err:
            _POLL_BACKOFF.pop(repo, None)
    _note_outcome(repo, err)


def repo_prs(repo, nums=(), branch="", owner="", sid=""):
    """(prs, error) for one repo, never blocking. gh is a network call (seconds on a busy repo) reached
    from the per-push build pass, so a stale entry serves what it has and refreshes in the background."""
    if not repo:
        return {}, ""
    _want(repo, nums, branch, owner, sid)
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

    try:
        threading.Thread(target=run, name="gitpr-" + repo, daemon=True).start()
    except Exception:
        with _LOCK:
            _INFLIGHT.discard(repo)
        raise


def needs_poll(repo):
    """True while a watched PR's check is still running, a push has not reached its PR yet, or the last
    read failed. None of these produces a local event, so they are what the one poll waits on."""
    ent = _CACHE.get(repo) or {}
    if ent.get("err"):
        return True
    prs = ent.get("prs") or {}
    wanted, branches = _watch(repo)
    return (any(prs[n].get("checksState") == "running" for n in _watched_unsettled(prs, wanted, branches))
            or bool(_awaited(repo, prs, branches)))


# ── the refresh events ───────────────────────────────────────────────────────────────────────────────
_POLLED = {}      # repo → time.monotonic() of the last poll-driven refresh
_POLL_SECS = 30   # the one interval in this module; see needs_poll
_POLL_MAX_SECS = 900        # the ceiling a failing repo's poll backs off to
_POLL_BACKOFF = {}          # repo → the current interval while its reads fail; cleared by a clean read or a retry


def note_local_state(cwd, repo):
    """(branch, ahead, head owner) for cwd, invalidating `repo` when the branch, its upstream or the
    tracking ref moved; a moved tracking ref is a push, so its sha is what the branch's PR should show."""
    branch, ahead, moved, remote, track_sha = local_state_ex(cwd)
    if moved:
        if branch and track_sha:
            with _LOCK:
                _EXPECT.setdefault(repo, {})[branch] = [track_sha, _HEAD_WAIT_POLLS]
        invalidate(repo)
    return branch, ahead, head_owner(cwd, remote)


def note_push_turn(repo):
    """A turn ran git push / gh pr in this repo: the remote moved, so re-read it."""
    invalidate(repo)


def poll_due(repo, now):
    """True when the poll should re-read `repo` now: gated on needs_poll, paced by _POLL_SECS. `now` is
    time.monotonic(), passed in so a test can drive it."""
    if not needs_poll(repo):
        return False
    failing = bool((_CACHE.get(repo) or {}).get("err"))
    interval = _POLL_BACKOFF.get(repo, _POLL_SECS) if failing else _POLL_SECS
    last = _POLLED.get(repo)
    if last is not None and (now - last) < interval:
        return False
    _POLLED[repo] = now
    if failing:
        with _LOCK:
            nxt = min(interval * 2, _POLL_MAX_SECS)
            if nxt == _POLL_MAX_SECS and _POLL_BACKOFF.get(repo) != _POLL_MAX_SECS:
                print("gitpr: PR status for %s retries every %d s until it reads" % (repo, _POLL_MAX_SECS),
                      file=sys.stderr)
            _POLL_BACKOFF[repo] = nxt
    invalidate(repo, full=False)
    return True
