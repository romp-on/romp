#!/usr/bin/env python3
"""perf-bench — offline timing of the romp kernel's payload builders against a COPY of a state directory.

The live kernel's pusher thread spends most of a core rebuilding payloads: build_session (chat),
build_feed, build_timeline (kernel/kernel.py) and jd.load_goals (kernel/judge.py). This tool imports a
checkout's kernel IN-PROCESS, points it at a state directory copy, reconstructs the pusher's liveness
snapshot from that copy's registry files, and times each builder on real-sized data — with no live
kernel, no restart, no network, no WebSocket. Two checkouts (HEAD and a candidate) run against the
same copy and `--compare` prints the per-benchmark deltas.

Usage (every path below is an example; the copy lives OUTSIDE any repo):

    # 1. copy the state directory, leaving out the SDK venv (hundreds of MB, not data) and the
    #    credential files the bench never needs (MIRROR_IGNORE below: the serve token, the Web Push key
    #    and subscriptions, the remote kernels' tokens)
    rsync -a --exclude sdkvenv --exclude serve-token --exclude push-vapid.json \\
        --exclude push-subscriptions.json --exclude remotes.json \\
        ~/.local/state/romp/ /tmp/romp-state-copy/
    #    transcripts are read from Claude's own directory; for a strict A/B (an active session's
    #    transcript grows between runs) copy that too and pass --claude-dir:
    rsync -a ~/.claude/projects/ /tmp/claude-copy/projects/

    # 2. bench this checkout, then a candidate checkout, against the same copy
    tools/perf-bench.py --state /tmp/romp-state-copy --profile --json /tmp/bench-head.json
    tools/perf-bench.py --state /tmp/romp-state-copy --repo ../romp-candidate --json /tmp/bench-cand.json

    # 3. deltas, A -> B (the header flags any difference in the two runs' worlds first)
    tools/perf-bench.py --compare /tmp/bench-head.json /tmp/bench-cand.json

The tool REFUSES the live default state directory ($ROMP_STATE_DIR, $XDG_STATE_HOME/romp, or
~/.local/state/romp) unless `--i-know-this-is-live` is passed — and even then it never runs the
kernel against that directory: it mirrors the directory to a fresh temp copy (sdkvenv and the
credential files excluded) and benches the mirror, so the live directory is only ever read. The
mirror is removed afterwards unless `--keep-mirror`.

The copy is only READ, whichever way it was given. Every write the kernel aims at the state
directory lands in a shadow directory under the tool's private temp dir instead (the state shadow,
below): the repo-root marker the kernel writes when it is imported, the session order the push
appends new sids to, the order audit log. The end-of-run census fingerprints the copy before the
kernel is imported and after the last row, on the error path too, and prints what changed; a run
that reports anything but 0 changed, 0 new, 0 removed found a writer the shadow does not take. Two
such writers are known. A state file the kernel cannot parse is quarantined by an os.replace to a
.corrupt-<stamp> sibling in the same directory, which goes through none of the shadowed doors, so
on a copy holding such a file the census lists that file removed and its sibling new. The boot pass
over the checkpoints directory (em.checkpoint_sweep) runs at kernel import through the kernel's own
provider, before any guard installs, and removes a document whose recorded file is gone or that
does not parse, so on a copy holding such a document the census lists it removed. Anything else is
a writer this tool does not know about. The census records mtimes, sizes, directories and
link targets, not modes: the kernel import sets the state root it is pointed at to 0700, a change
only on a copy whose root was not already 0700 (a live directory is, and rsync -a keeps it).

Redacted copies. A copy tool that rewrites the cwd in every registry file (a home path replaced by
X-es) while Claude's projects/ directory keeps the original name leaves discovery with nothing: the
kernel derives the project directory from the registry cwd (judge.py's _proj_dir), so no transcript
is found for any session. `--cwd-map FROM=TO` (repeatable) rewrites a registry cwd's leading path
components before that derivation (`--cwd-map /XXXX/XXXXXX=$HOME` maps /XXXX/XXXXXX/code/notes-api
to $HOME/code/notes-api), and the report counts, per rule, every _proj_dir call of the whole run
that the rule rewrote (discovery's, the tool's own transcript search's and the builders'), read
when the run ends. It touches nothing else: the cwd
the chat build hands to its git queries stays the redacted one (a directory that does not exist
here, so those queries fail as they would on a machine without the checkout). The no-transcript
error names the counts it worked from (live sessions, hits in the 48 h window and in the 365-day
backfill, registry cwds and how many of them map to an existing project directory) so a wrong
--claude-dir, a redacted cwd and a stale copy read differently.

What is measured. Unless noted, each row is one untimed warm-up call followed by `--iters` timed
calls, reported as ms min/median/max:
  liveness_snapshot      Sessions.live() — the pusher cycle's one liveness read
  names_snapshot         _names_snapshot() — the cycle's names-registry read
  discover_cold/warm     jd.discover(now) with and without its fingerprint cache
  build_session_cold:S   build_session for each of the K largest live transcripts with EVERY cache a
                         freshly started kernel lacks emptied before each call: the event model's
                         jsonl / assembly / trailing-record caches and the kernel's parse, chat-fold,
                         path-link and states-fold caches (COLD_KERNEL_CACHES and COLD_EM_CACHES below; a
                         name this kernel lacks is skipped and the report lists what was emptied). Each
                         sample is then checked: the event model's assembly counters must show a full
                         assembly, and the session's own transcript must be present in the assembly
                         cache the sample started with empty — proof it was assembled inside the
                         sample. A sample that fails either is an ERROR, not a number, so a cache this
                         tool does not know about (above or below the parse) is caught instead of
                         quietly warming the row. A serve or fold counted INSIDE a sample is reported
                         beside the row, not treated as pre-warmth: with the cache emptied under its
                         lock beforehand, such a hit can only be on an entry the same build created — a
                         transcript the build reads twice (a peer's, for postal resolution), folded
                         when that file grew between the two reads (a live session's).
  build_session_emwarm:S the same call with only the kernel-side caches emptied — the event model
                         still serves the assembled transcript. The difference to _cold is the parse.
  build_session_warm:S   the same call again with everything cached (an unchanged tab's push)
  warm_all_parses        one _parse() per live session from an empty parse cache (single call)
  build_feed             build_feed with every live session's parse cached (the steady state)
  build_feed_noparse     build_feed with the parse cache and the feed's card memo empty (the cards-first boot shape)
  build_timeline_bars    build_timeline(with_bars=True)
  build_timeline_skel    build_timeline(with_bars=False) — the lanes skeleton the push sends first
  load_goals:S           jd.load_goals for each of the K largest goal stores
  push_cold_cycle        ONE periodic _push over the fake clients with every cache empty and empty
                         dedup state: the pusher's first cycle after a restart (single call, no
                         warm-up). Reports the full frames' bytes per slot.
  push_connect:APP       _push([fresh client], connect=True) per app after a warming cycle: the path
                         a page load pays (pusher-warmed feed served, no baseline move). A fresh
                         client with empty dedup state for every sample.
  push_steady            the periodic _push over the same clients, repeated; no warm-up of its own
                         (it follows the rows above). Each sample records whether _cached_feed /
                         _cached_timeline rebuilt inside it (they do whenever the 5 s view signature
                         bucket rolls at least REBUILD_MIN_S after the previous build, which depends on
                         wall-clock alignment, not on the code under test); the row's numbers are the
                         samples WITHOUT a rebuild, and the samples with one form push_steady_rebuild.
                         The loop runs until `--iters` no-rebuild samples exist (at most 3x iters).
                         A loop that runs past _DEDUP_REPOST_S (60 s) also absorbs one re-send of every
                         unchanged slot; the per-sample bytes in the JSON show it.
Beside the build_session rows the report prints the git queries the chat build ran per call (see the
tripwire note below); the push rows report bytes handed to each fake client per slot.

How the liveness snapshot is reconstructed, and what is approximated:
  * The SDK backend object is built WITHOUT its constructor (no boot heal, no reconcile thread, no
    key claim, no scope probe): a subclass allocated with __new__ and given only the attributes the
    read-side methods use. Its live_sessions() reads sdk/<sid>.json exactly as the real backend does
    for a session with no running thread (_live_row), so every reg with alive=true is a live row.
  * The state of each row is the last STATE record of states/<sid>.jsonl, verbatim, and the row is
    marked connected — what a running session would report — instead of the dormant mapping that
    turns an in-flight state into "waiting". `--dormant-rows` keeps the dormant mapping instead.
    A running session's snapshot also carries live-only fields (subagents, bgTasks); those read
    empty here. ctxTokens and the context percentage come from the reg's persisted values, as for a
    dormant row. `--all-regs-live` also lists regs with alive=false.
  * Comment threads (threadOf regs) are skipped, as the real live_sessions skips them.
  * The SDK backend's manager key is pinned empty (dormant rows read auth=login).
  * The session list is discovery's (jd.discover, a 48 h window keyed to the real clock) restricted to
    the live rows, plus — as _alive_sessions does for the builders — every live sid outside that
    window resolved through the long backfill window, so the pick list, the parse warm-up and the
    builders see ONE world. A live sid with no transcript anywhere is listed in the report; a run
    where neither the window nor the backfill finds a transcript for ANY live sid is an error (a wrong
    --claude-dir; registry cwds a redaction rewrote, see --cwd-map above; or a copy older than the
    backfill window), raised only after the backfill has run.
  * Transcripts are read from Claude Code's own directory ($CLAUDE_CONFIG_DIR or ~/.claude), which
    the registry references; `--claude-dir` points at a copy or a synthetic one.

Side-effect guards (all reported in the output; a guard whose target a kernel revision lacks is an
error, never a silent skip):
  * The manager control-port variables are removed or poisoned before the import (ROMP_MANAGER_PORT
    is set to a dead port rather than unset — an ABSENT value maps to the default, live, port in
    one consumer; see tests/conftest.py), so nothing this process does can reach the live manager.
  * ROMP_MODEL_CATALOG=off, ROMP_CLI_SCOPE=0, ROMP_CLAUDE_BIN=/bin/false, the service env file
    pointed at a missing path, and every key-source and credential name tests/conftest.py pops removed
    (KEY_SOURCE_ENV below: the API keys, the key reference and command, the token credentials, the auth
    declaration and 1Password's names, plus every ANTHROPIC_* and OP_SESSION_* name).
  * Functions that would start a network fetch, a background parse thread, a desktop notification,
    a Web Push or a badge push are replaced with recorders (they are not builders; the push path
    reaches them from _cached_feed and the pricing table).
  * `subprocess` in every loaded romp module is replaced with a tripwire that raises on any spawn —
    except the kernel's read-only local git queries: `git rev-parse` and `git ls-files` (the chat
    build's path-link pass) and the pair `git remote get-url` (the file-link route; the pair only —
    `git remote` also has writing subcommands, and those trip). Those run and are counted per build.
    "Every loaded romp module" is the kernel, the judge, the event model, the SDK backend, and every
    sibling kernel/ module one of them loaded (credentials among them); the report's `neutralized` list
    names each one.
    The kernel caches their answers only for a cwd inside a git checkout (on the index and tree
    mtimes, and the config file's mtime for the remote); for any other cwd it re-runs `git ls-files`
    on EVERY build, so the per-build count beside the build_session rows says how much of a sample is
    spawn time. `--no-git` answers those queries as failures instead, for a strict zero-exec run.
  * pwd.getpwnam / pwd.getpwuid are wrapped as counters (nss_lookups in the output): the chat build's
    path-link pass calls os.path.expanduser on every path-shaped token, and a `~name/...` token makes
    glibc consult the name service — AF_UNIX connects to nscd and systemd-userdb, local, not network.
  * The state shadow: every kernel _atomic_write (the ONE write door for the small JSON state files
    among them), every Path.write_text the kernel import performs against the state directory (the
    repo-root marker), the order audit log's append and the event model's checkpoint documents (its
    directory provider is pointed at the shadow) are redirected to <private dir>/shadow/<same
    relative path>, and the kernel's ONE strict reader of the small JSON state files
    (_read_state_json) reads a shadowed file from the shadow, so a read-modify-write such as the
    session order's append of new sids lands once, as it does live, instead of re-firing on every
    build against a file that never changed. The provider redirect installs after the import, so the
    boot pass over the checkpoints directory (em.checkpoint_sweep, run at kernel import) still reads
    the copy's directory and removes a document whose recorded file is gone or that does not parse;
    the census lists those removed. A write aimed anywhere else is recorded and refused
    (refused_writes in the output). The copy's files, directories and symlink targets are
    fingerprinted before the kernel is imported and again at the end, on EVERY exit path (a guard's
    error, an exception out of the candidate kernel at import or inside a builder, Ctrl-C), and the
    output lists what changed (nothing; the quarantine move of an unparseable state file or the
    boot pass's removal of a checkpoint document, the two known writers the shadow does not take; or
    a writer this tool does not know about) beside the
    relative paths that were shadowed; the JSON, when asked for, is written on those paths too, with
    the error beside the census.
  * A guard that trips inside a call the kernel CATCHES is still an error: _push wraps its whole build in
    `except Exception` and returns, so a refused spawn or write during the push rows would otherwise be
    timed as an aborted cycle and the run would exit 0. Every refusal is recorded before it raises, and a
    run that ends with one on record fails naming the guard (check_guards_held).

Verification: run once under `strace -f -e trace=execve,connect`. Expected: the interpreter's own
execve, plus (without `--no-git`) one execve of git per counted git query and nothing else; connect()
calls only to AF_UNIX name-service sockets (/var/run/nscd/socket, /run/systemd/userdb/...), matching
nss_lookups in the report, and NO AF_INET / AF_INET6 connect:
    strace -f -e trace=execve,connect -o /tmp/bench.strace tools/perf-bench.py --state ... --no-git
    grep -E 'execve\\(|AF_INET' /tmp/bench.strace     # one execve line (python), no AF_INET line

Not a test (pytest collects test_*.py); tests/test_perf_bench.py drives it against a synthetic state
directory."""
import argparse
import cProfile
import gc
import importlib.util
import json
import os
import pstats
import pwd
import re
import shutil
import stat
import statistics
import subprocess as _real_subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 3          # the JSON shape: writes.shadowed and refused_writes beside the census, live_transcripts.searched, cwd_map
DEFAULT_CLIENTS = "chat,feed,timeline"
_TOOL_FILE = os.path.realpath(__file__)          # the tripwire's frame filter excludes this file, by path
PROFILE_TOP = 25
# What a mirror of the live directory (and the rsync recipe above) leaves out: the SDK venv, and every file the
# kernel writes 0600 because it holds a credential: the serve token, the Web Push VAPID key, the Web Push
# subscriptions (each carries a browser's auth secret) and the remote kernels' serve tokens. The bench reads
# none of them (the notification functions are recorders; remotes are re-attached only at kernel boot); the
# first form of this list copied the last two into every mirror (review find, 2026-09-08).
MIRROR_IGNORE = ("sdkvenv", "serve-token", "push-vapid.json", "push-subscriptions.json", "remotes.json")
# The kernel-side caches a freshly started kernel lacks and build_session reads (all plain dicts, no
# lock). Missing names are skipped: older revisions lack some, and the assembly-counter check below
# is what proves a sample cold, not this list.
COLD_KERNEL_CACHES = ("_parse_cache", "_built_chat", "_prev_chat_events", "_prev_chat_ledger", "_arch_tops_cache",
                      "_PATH_LINK_CACHE", "_states_notes_cache", "_state_ev_cache", "_bgtasks_cache", "_bgall_cache",
                      "_queued_parse_cache", "_wake_tail_cache", "_session_meta_cache", "_session_tok_cache",
                      "_machine_cut_cache")
# (cache, its lock) in the event model: the parse layer under _parse. Missing names are skipped here too
# (the trailing-record cache is newer than the assembly counters this tool requires); cold_caches in the
# report says which of both lists were emptied, and tests/test_perf_bench.py checks the ones the cold
# proof rests on.
COLD_EM_CACHES = (("_JSONL_CACHE", "_JSONL_CACHE_LOCK"), ("_ASM_CACHE", "_ASM_LOCK"), ("_TRAILING_CACHE", "_TRAILING_LOCK"))
WORLD_KEYS = (("liveness.live", "live rows"), ("live_transcripts.count", "transcripts"), ("iters", "iters"),
              ("sessions_requested", "sessions"), ("git_queries.answered_as_failure", "no-git"),
              ("push_rebuilds", "push_steady rebuild samples"),
              ("error", "run error"))      # a run that stopped on a guard is flagged before its rows are diffed
# Every key-source and credential name tests/conftest.py pops before any test runs (its KEY_SOURCE_ENV_NAMES
# and KEY_SOURCE_ENV_PREFIXES: credentials.FLOOR_ENV_NAMES, which is the retired provider names, the login
# tokens, the auth declaration and 1Password's names, plus credentials.FLOOR_ENV_PREFIXES and
# sdk_backend.AUTH_ENV_NAMES), removed here before the import for the same reason: every shell under a
# romp-managed session inherits the manager's credentials, a retired provider name in the kernel's
# environment is a boot failure (credentials.check_boot_environment), and the login tokens would be claimed
# for a launch. The first form dropped ANTHROPIC_* only (review find, 2026-09-08); tests/test_perf_bench.py
# pins this list against the kernel's own constants.
KEY_SOURCE_ENV = ("ANTHROPIC_API_KEY", "ROMP_API_KEY_REF", "ROMP_API_KEY_CMD", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                  "ROMP_EXPECTED_AUTH", "OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT")
KEY_SOURCE_ENV_PREFIXES = ("ANTHROPIC_", "OP_SESSION_")


class BenchError(Exception):
    pass


# ── argument parsing ────────────────────────────────────────────────────────────────────────────
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="perf-bench", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", help="a COPY of a romp state directory (see the module docstring)")
    ap.add_argument("--repo", help="the checkout whose kernel to bench (default: the one this script lives in)")
    ap.add_argument("--claude-dir", help="CLAUDE_CONFIG_DIR for the run: where transcripts (projects/) live")
    ap.add_argument("--iters", type=int, default=5, help="timed calls per benchmark after one warm-up (default 5)")
    ap.add_argument("--sessions", type=int, default=5, help="how many of the largest transcripts / goal stores to bench (default 5)")
    ap.add_argument("--clients", default=DEFAULT_CLIENTS,
                    help="fake client apps for the push benchmarks, comma-separated (default %s); empty skips them" % DEFAULT_CLIENTS)
    ap.add_argument("--profile", action="store_true", help="cProfile one call per builder; print the top %d by cumulative and by tottime" % PROFILE_TOP)
    ap.add_argument("--json", help="write the machine-readable result here")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"), help="print per-benchmark deltas A -> B and exit")
    ap.add_argument("--no-git", action="store_true",
                    help="answer the kernel's read-only git queries (rev-parse, ls-files, remote get-url) as failures instead of running git")
    ap.add_argument("--dormant-rows", action="store_true", help="liveness rows exactly as a restarted kernel reports dormant sessions")
    ap.add_argument("--all-regs-live", action="store_true", help="treat regs with alive=false as live too")
    ap.add_argument("--i-know-this-is-live", action="store_true",
                    help="allow --state to name the live default state directory; it is mirrored to a temp copy first")
    ap.add_argument("--keep-mirror", action="store_true", help="keep the temp mirror of a live directory (default: removed at exit)")
    ap.add_argument("--cwd-map", action="append", default=[], metavar="FROM=TO",
                    help="rewrite a registry cwd's leading path FROM to TO before the project directory is derived "
                         "from it (a redacted copy whose projects/ kept the original names; see the module docstring); "
                         "repeatable, first matching rule wins")
    return ap.parse_args(argv)


def parse_cwd_maps(specs):
    """[(FROM, TO)] from the --cwd-map values; a rule without '=' or with an empty FROM is a BenchError.
    FROM and TO lose a trailing slash so the match is on whole path components."""
    out = []
    for spec in specs or ():
        if "=" not in spec:
            raise BenchError("--cwd-map %r: expected FROM=TO" % spec)
        frm, to = spec.split("=", 1)
        frm, to = frm.rstrip("/"), to.rstrip("/")
        if not frm:
            raise BenchError("--cwd-map %r: FROM is empty" % spec)
        out.append((frm, to))
    return out


def install_cwd_map(jd, maps):
    """Wrap jd._proj_dir so a registry cwd under a FROM prefix is rewritten to TO before the kernel
    derives Claude's project directory from it (whole-component prefix match; the first matching rule
    wins). judge.py resolves _proj_dir by name on every call and the kernel reaches it as jd._proj_dir,
    so the wrapper is seen by discovery, the liveness rows' transcript paths and the chat build alike;
    nothing else reads the map. Returns the per-rule hit counters (a list the wrapper updates for as
    long as the run lasts, so a count read at the end covers every _proj_dir call of the run: the
    discovery rows', the tool's own transcript search's (one per distinct registry cwd) and the
    builders' transcript-path derivations). With no rules nothing is wrapped: the default run's timed
    rows call the kernel's own _proj_dir, with no extra frame."""
    if not maps:
        return []
    real = jd._proj_dir
    hits = [0] * len(maps)

    def mapped(d):
        cwd = str(d)
        for i, (frm, to) in enumerate(maps):
            if cwd == frm or cwd.startswith(frm + "/"):
                hits[i] += 1
                cwd = to + cwd[len(frm):]
                break
        return real(cwd)
    jd._proj_dir = mapped
    return hits


# ── live-directory refusal ──────────────────────────────────────────────────────────────────────
def live_state_dirs(env):
    """Every path the CALLER's environment would resolve as the live state directory."""
    out = set()
    if env.get("ROMP_STATE_DIR"):
        out.add(os.path.realpath(os.path.expanduser(env["ROMP_STATE_DIR"])))
    xdg = env.get("XDG_STATE_HOME") or "~/.local/state"
    out.add(os.path.realpath(os.path.join(os.path.expanduser(xdg), "romp")))
    out.add(os.path.realpath(os.path.expanduser("~/.local/state/romp")))
    return out


def mirror_state(src):
    """Copy `src` (sdkvenv and the credential files excluded) into a fresh temp directory; return
    (root, copy). A file that vanished between the directory listing and its copy — a live kernel's
    atomic-write temp file — is tolerated; any other copy error is raised."""
    root = tempfile.mkdtemp(prefix="romp-perf-live-mirror-")
    dst = os.path.join(root, "romp")
    try:
        shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(*MIRROR_IGNORE))
    except shutil.Error as e:
        errors = e.args[0] if e.args and isinstance(e.args[0], list) else []
        if not errors or not all(("[Errno 2]" in str(why)) or ("No such file" in str(why)) for _s, _d, why in errors):
            shutil.rmtree(root, ignore_errors=True)       # a half-made mirror is not benched; leave nothing behind
            raise
        sys.stderr.write("perf-bench: %d file(s) vanished during the mirror copy (a live writer's temp files); continuing\n" % len(errors))
    return root, dst


# ── environment for the in-process kernel ───────────────────────────────────────────────────────
def prepare_env(state, claude_dir, private_dir):
    """Rewrite os.environ for the kernel import. Returns the list of applied changes (for the report)."""
    changes = []
    for k in ("ROMP_SERVE_PORT", "ROMP_MANAGER_PID", "ROMP_SUPERVISED", "TMUX", "TMUX_PANE",
              "ROMP_SID", "ROMP_SESSION_NAME", "ROMP_STATE_DIR"):
        if k in os.environ:
            os.environ.pop(k)
            changes.append("unset " + k)
    for k in [k for k in os.environ if k in KEY_SOURCE_ENV or k.startswith(KEY_SOURCE_ENV_PREFIXES)]:
        os.environ.pop(k)
        changes.append("unset " + k)
    no_env = os.path.join(private_dir, "no-such-service.env")
    sets = {
        "ROMP_MANAGER_PORT": "1",                 # a dead port: absent maps to the default (live) port in _run_main_update
        "ROMP_KERNEL_NO_OPEN": "1",
        "ROMP_MODEL_CATALOG": "off",
        "ROMP_CLI_SCOPE": "0",
        "ROMP_CLAUDE_BIN": "/bin/false",
        "ROMP_SERVE_TOKEN": "perf-bench-token-not-for-use",
        "ROMP_SERVICE_ENV_FILE": no_env,
        "ROMP_SERVICE_ENV": no_env,
        "ROMP_STATE_DIR": state,
        "XDG_STATE_HOME": os.path.dirname(state),
    }
    if claude_dir:
        sets["CLAUDE_CONFIG_DIR"] = claude_dir
    for k, v in sets.items():
        os.environ[k] = v
        changes.append("set %s" % k)
    return changes


# ── the state shadow (where writes aimed at the copy land) ──────────────────────────────────────
class StateShadow:
    """Where every write the kernel aims at the state copy lands instead: <root>/<the same relative
    path>, in the tool's private temp dir. The copy stays byte-identical, and the kernel's own re-reads
    of the small JSON state files (installed on _read_state_json by install_guards) see the shadow, so a
    read-modify-write (the session order's append of new sids) lands once, as it does live, instead of
    re-firing on every build against a file that never changed (each re-fire would add an audit record
    with a captured stack to the very rows this tool times). `written` lists every relative path
    redirected to the shadow, in order, duplicates kept, landed or not: a file when a write to it is
    diverted, the checkpoints directory when install_guards points the event model's provider at it,
    whether or not a document follows; the report dedups it. A write aimed anywhere else is appended
    to `refused` (the recorder's refused_writes list) before it is refused, so a caller that swallows
    the error cannot hide it (check_guards_held)."""

    def __init__(self, state, root, refused=None):
        self.state = Path(state).resolve()
        self.root = Path(root)
        self.written = []
        self.refused = refused if refused is not None else []

    def rel(self, path):
        """The path relative to the state copy, or None when `path` is not under it."""
        p = Path(path).resolve()
        if p == self.state or self.state in p.parents:
            return p.relative_to(self.state)
        return None

    def target(self, path):
        """Where a write to `path` lands: its shadow when it aims at the copy (recorded), itself when it
        is already in the shadow (the audit log's own trim); a write aimed anywhere else is refused."""
        r = self.rel(path)
        if r is not None:
            self.written.append(str(r))
            t = self.root / r
            t.parent.mkdir(parents=True, exist_ok=True)
            return t
        p = Path(path).resolve()
        if self.root.resolve() in p.parents:
            return p
        self.refused.append(str(p))      # on record BEFORE the raise: _push swallows the exception
        raise BenchError("perf-bench: _atomic_write outside the state copy: %s" % p)

    def read_path(self, path):
        """Where a read of `path` looks: its shadow once one has been written, else `path` itself. One
        resolve() and one stat per call, paid inside the timed rows by every read of a small JSON state
        file: microseconds against rows measured in milliseconds, and the same on both sides of an A/B."""
        r = self.rel(path)
        if r is not None and (self.root / r).exists():
            return self.root / r
        return Path(path)


def new_recorder():
    """What the guards see, made before the kernel loads so the census can read it whichever way the run
    ends: refused spawns, suppressed notifications and background parses, refused writes, the tripwires
    (for their git counters), the name-service lookups, the threads started, the PR chip's stood-down gh
    refreshes and the --cwd-map rules' hit counters (install_cwd_map's list, empty without rules)."""
    return {"spawns": [], "notifications": [], "refused_writes": [], "tripwires": [], "nss": {}, "warm_calls": [],
            "thread_starts": 0, "cwd_map_hits": [], "gh_kicks": []}


# ── side-effect tripwires ───────────────────────────────────────────────────────────────────────
class SubprocessTripwire:
    """Stands in for the `subprocess` module inside the loaded romp modules: constants and helpers pass
    through; every spawn raises, except the kernel's read-only local git queries — `git rev-parse` and
    `git ls-files` (the chat build's path-link pass), `git rev-list` (the PR chip's ahead-of counts) and
    the pair `git remote get-url` (the file-link route and the chip's repo; the get-url pair only, never
    `git remote` at large, whose add/set-url/remove forms rewrite config). Those are counted. With no_git
    they are answered as failures (the "no git on this box" shape) instead of run, so a strict zero-exec
    run is possible without replacing any kernel function.

    The chip's `gh` reads are NOT admitted here: they are a network call, and the guard that stands them
    down is gp._kick in install_guards, where the rest of the background threads are neutralized."""
    _SPAWN = ("Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput")
    _GIT_READ_ONLY = ("rev-parse", "ls-files", "rev-list")   # single-word queries admitted by their subcommand
    _GIT_READ_ONLY_PAIRS = (("remote", "get-url"),)      # two-word queries admitted only as the whole pair

    def __init__(self, real, log, no_git=False):
        self._real, self._log, self._no_git = real, log, no_git
        self.git_calls = {}

    @classmethod
    def _git_query(cls, argv):
        """The read-only git query `argv` spells — "rev-parse", "ls-files" or "remote get-url" (the
        counter's key) — or None for anything else, `git remote set-url` included."""
        if not (isinstance(argv, (list, tuple)) and argv and argv[0] == "git"):
            return None
        i = 1
        while i < len(argv) and argv[i] == "-C":
            i += 2
        rest = tuple(argv[i:])
        if rest and rest[0] in cls._GIT_READ_ONLY:
            return rest[0]
        for pair in cls._GIT_READ_ONLY_PAIRS:
            if rest[:2] == pair:
                return " ".join(pair)
        return None

    def __getattr__(self, name):
        if name in self._SPAWN:
            def refuse(*a, **k):
                import traceback
                argv = a[0] if a else k.get("args")
                sub = self._git_query(argv)
                if name == "run" and sub:
                    self.git_calls[sub] = self.git_calls.get(sub, 0) + 1
                    if self._no_git:
                        return self._real.CompletedProcess(argv, 1, "" if k.get("text") else b"", "" if k.get("text") else b"")
                    return self._real.run(*a, **k)
                # the caller's frames, this file's own excluded by identity: a substring match on the tool's name
                # emptied the attribution for any checkout or state path that spelled it (found by the test whose
                # scratch kernel lives under a perf-bench-* directory, 2026-09-08)
                where = [fr for fr in traceback.extract_stack()[:-1] if os.path.realpath(fr.filename) != _TOOL_FILE][-4:]
                via = " <- ".join("%s:%d %s" % (os.path.basename(fr.filename), fr.lineno, fr.name) for fr in reversed(where))
                self._log.append("subprocess.%s %r via %s" % (name, argv, via))
                raise BenchError("perf-bench tripwire: subprocess.%s called (argv %r) via %s" % (name, argv, via))
            return refuse
        return getattr(self._real, name)


def install_guards(km, sbmod, shadow, rec, no_git=False):
    """Neutralize the non-builder side effects the push path can reach; return the list of names for
    the report (`rec`, from new_recorder(), collects what the guards saw; `shadow` takes the writes).
    A safety target the kernel revision lacks is an error: a renamed notification function would
    otherwise leave the real one in place (Web Push to every subscription in the copy's store)."""
    names = []

    def stub(attr, fn):
        if not hasattr(km, attr):
            raise BenchError("this kernel has no %s; the harness's guard list needs adjusting for this revision" % attr)
        setattr(km, attr, fn)
        names.append("km." + attr)

    # Each recorder takes whatever the kernel passes: _push_notify is called with kind=, card_id=, quiet=
    # and host= keywords the recorder never reads, and a shape mismatch would not fail the run. _push
    # catches every exception in its build, writes `push build: <traceback>` to stderr and returns, so
    # that cycle would be timed with its frames dropped and the notification count would come up short.
    stub("_refresh_remote_prices", lambda *a, **k: None)            # network fetch thread
    stub("_warm_fleet_bg", lambda *a, **k: rec["warm_calls"].append(time.time()))   # background parse thread; counted
    # The PR chip's gh refresh: a network call on its own thread, kicked from the push build pass. The cache
    # reads around it stay live, so the builders time the same code they run in production, minus the network.
    gp = getattr(km, "gp", None)
    if gp is not None:
        if not hasattr(gp, "_kick"):
            raise BenchError("this kernel's PR module has no _kick; the harness's guard list needs adjusting for this revision")
        gp._kick = lambda repo, nums=(): rec["gh_kicks"].append(repo)
        names.append("gp._kick (counted)")
    stub("_system_notify", lambda t, b, *a, **k: rec["notifications"].append(("system", t)))
    stub("_push_notify", lambda t, b, *a, **k: rec["notifications"].append(("push", t)))
    stub("_push_forward", lambda evs, *a, **k: rec["notifications"].append(("forward", len(evs))))
    stub("_badge_push", lambda n, *a, **k: rec["notifications"].append(("badge", n)))
    # Every loaded romp module: the four the harness drives, plus every sibling of kernel.py that one of them
    # loaded (sdk_backend loads credentials, whose apiKeyHelper runs as a subprocess). The first form guarded the four
    # only, while the docstring promised every loaded module (review find, 2026-09-08).
    mods = [km, getattr(km, "jd", None), getattr(km, "em", None), sbmod]
    kfile = getattr(km, "__file__", None)
    if isinstance(kfile, str):
        kdir = os.path.dirname(os.path.realpath(kfile))
        for m in list(sys.modules.values()):
            f = getattr(m, "__file__", None)
            if isinstance(f, str) and os.path.dirname(os.path.realpath(f)) == kdir and not any(m is x for x in mods):
                mods.append(m)
    for mod in mods:
        if mod is not None and isinstance(getattr(mod, "subprocess", None), type(sys)):
            tw = SubprocessTripwire(_real_subprocess, rec["spawns"], no_git=no_git)
            mod.subprocess = tw
            rec["tripwires"].append(tw)
            names.append("%s.subprocess%s" % (getattr(mod, "__name__", "?"), " (git answers as failure)" if no_git else ""))
    real_aw = km._atomic_write

    def shadowed_write(path, text, mode=None):
        t = shadow.target(path)                     # the shadow's path; a write aimed elsewhere is recorded, then refused
        return real_aw(t, text, mode) if mode is not None else real_aw(t, text)
    stub("_atomic_write", shadowed_write)
    names[-1] = "km._atomic_write (shadowed)"
    real_rsj = getattr(km, "_read_state_json", None)   # stub() below is what refuses a kernel without it

    def overlaid_read(path, st=None, *a, **k):
        t = shadow.read_path(path)
        if t != Path(path):
            st = None                               # the caller's stat is of the copy's file, not the shadow's
        return real_rsj(t, st, *a, **k)
    stub("_read_state_json", overlaid_read)
    names[-1] = "km._read_state_json (shadow overlay)"
    stub("_order_audit_path", lambda: shadow.target(Path(km.jd.STATE) / "order-audit.jsonl"))
    names[-1] = "km._order_audit_path (shadowed)"
    # The event model's checkpoints: one JSON document per JSONL file it reads resumably (fold_records with a ckpt name),
    # plus the assembly's documents in the same directory, written at run time with Path.write_text and os.replace into
    # the directory its provider names at call time; kernel/judge.py installs `lambda: STATE / "checkpoints"` at import, a
    # directory INSIDE the copy. Neither door above takes that write: the write_text diversion in load_kernel covers the
    # import only, and the document is not an _atomic_write. So a run wrote one document per transcript into the copy
    # (the census reported them as new) and warmed the next run's cold rows, since a fresh process that finds a document
    # restores from it and reads only the tail past it. The provider is replaced with one naming the shadow's checkpoints
    # directory, computed once (the bench never rebinds the state root; a call-time shadow.target would pay a resolve
    # and a mkdir inside timed rows and record an entry per document) and recorded as a redirected path when the guard
    # installs, so the report names the directory whether or not a document lands in the run. The setter also resets
    # the event model's pending restores and document memos, which are empty here, before the first build. An event
    # model with the directory provider but no setter is a renamed door and an error, as the notification stubs are;
    # one with neither predates the checkpoints and has nothing to divert. One reader of the copy's directory stays: the
    # boot pass over it (em.checkpoint_sweep) runs at kernel import through the kernel's own provider, before this guard
    # installs, and removes a document whose recorded file is gone or that does not parse; the census lists those removed.
    em = getattr(km, "em", None)
    if callable(getattr(em, "set_checkpoint_dir", None)):
        ckpt_dir = shadow.target(Path(km.jd.STATE) / "checkpoints")
        em.set_checkpoint_dir(lambda: ckpt_dir)
        names.append("em.set_checkpoint_dir (shadowed)")
    elif hasattr(em, "_CKPT_DIR_FN"):
        raise BenchError("this kernel's event model has a checkpoint directory provider (_CKPT_DIR_FN) but no "
                         "set_checkpoint_dir; the harness's guard list needs adjusting for this revision")
    for fn in ("getpwnam", "getpwuid"):            # os.path.expanduser's name-service lookups, counted
        real = getattr(pwd, fn)

        def counted(*a, _real=real, _fn=fn, **k):
            rec["nss"][_fn] = rec["nss"].get(_fn, 0) + 1
            return _real(*a, **k)
        setattr(pwd, fn, counted)
        names.append("pwd.%s (counted)" % fn)
    # Every thread started from here on is counted (thread_starts in the output). threads_new, the other
    # thread check, compares the live set at exit with the one before the import, so it sees only a thread
    # still alive at the end; a parse thread that ran and finished inside a sample would pass it.
    real_start = threading.Thread.start

    def counted_start(self, *a, **k):
        rec["thread_starts"] += 1
        return real_start(self, *a, **k)
    threading.Thread.start = counted_start
    return names


def check_guards_held(rec):
    """A guard that tripped is an error even when the kernel caught it. The tripwire and the write guard raise
    BenchError, but _push wraps its whole build in `except Exception` (it writes `push build: <traceback>` to
    stderr and returns), so a spawn or an out-of-copy write attempted during the push rows was refused and the
    cycle went on, timed with its frames dropped, while the run exited 0 with no `error` in the JSON (review
    find, 2026-09-08). Every refusal is recorded before its raise, so an entry still on record at the end of
    the run means a caller swallowed it; the run then ends the way any other tripped guard ends it (the rows
    so far print, the JSON carries `error`, the exit status is 1), naming the guard."""
    spawns, writes = rec["spawns"], rec["refused_writes"]
    if not spawns and not writes:
        return
    what = []
    if spawns:
        what.append("subprocess tripwire: %d refused spawn(s): %s" % (len(spawns), "; ".join(spawns[:3]) + ("; ..." if len(spawns) > 3 else "")))
    if writes:
        what.append("_atomic_write guard: %d refused write(s) outside the copy: %s" % (len(writes), ", ".join(writes[:3]) + (", ..." if len(writes) > 3 else "")))
    raise BenchError("a guard tripped inside a call the kernel catches (its traceback is on stderr under `push build:`), so the "
                     "push rows timed an aborted cycle; " + "; ".join(what))


def git_calls_total(rec):
    out = {}
    for tw in rec["tripwires"]:
        for sub, n in tw.git_calls.items():
            out[sub] = out.get(sub, 0) + n
    return out


def asm_delta(em, before):
    """The event model's assembly counters that moved since `before` (a copy of em._ASM_STATS)."""
    return {k: em._ASM_STATS.get(k, 0) - before.get(k, 0) for k in set(em._ASM_STATS) | set(before)
            if em._ASM_STATS.get(k, 0) != before.get(k, 0)}


def check_cold_sample(em, tag, path, asm_before):
    """Prove one cold build_session sample was cold: the assembly counters moved by at least one full
    assembly since `asm_before`, and `path` (the session's own transcript) is now a key of the assembly
    cache the sample started with empty. Returns the counter delta; raises BenchError otherwise, so a
    cache this tool does not clear (above or below the parse) is an error, never a warm row."""
    d = asm_delta(em, asm_before)
    if not d.get("full"):
        raise BenchError("build_session_cold:%s ran no full assembly (assembly counters moved %r): a cache "
                         "this harness does not clear served the parse" % (tag, d))
    asm_cache = getattr(em, "_ASM_CACHE", {})
    with em._ASM_LOCK:
        own = any(isinstance(k, tuple) and k and k[0] == path for k in asm_cache)
    if not own:
        raise BenchError("build_session_cold:%s never assembled its own transcript (%s is absent from the "
                         "assembly cache the sample started with empty; counters moved %r): a cache above the "
                         "event model served it" % (tag, os.path.basename(path), d))
    return d


def check_snapshot_rows(rows, regs, all_regs):
    """The liveness snapshot must hold exactly one row per qualifying registry entry (alive, not a comment
    thread; with all_regs, closed entries too). Sessions.live() catches and logs a failing backend merge and
    carries on with whatever rows it has, so a bench backend that a kernel revision drives differently would
    otherwise be timed against an empty or partial world and report success."""
    expected = sum(1 for r in regs if not r.get("threadOf") and (r.get("alive") or all_regs))
    if len(rows) != expected:
        raise BenchError("liveness snapshot has %d rows but %d registry entries qualify — the backend merge "
                         "failed inside the kernel (its traceback is on stderr); the bench backend needs "
                         "adjusting for this revision" % (len(rows), expected))
    return expected


def bucket_steady_samples(samples):
    """Split push_steady's samples into (quiet, rebuilt): a sample in which _cached_feed or _cached_timeline
    rebuilt is set aside, because the rebuild is decided by wall-clock alignment (the 5 s view-signature
    bucket rolling REBUILD_MIN_S after the previous build), not by the code under test."""
    quiet = [f for f in samples if not (f["rebuilt_feed"] or f["rebuilt_timeline"])]
    rebuilt = [f for f in samples if f["rebuilt_feed"] or f["rebuilt_timeline"]]
    return quiet, rebuilt


def steady_rows(samples, apps):
    """The push_steady row (the quiet samples' numbers, every sample listed) and the push_steady_rebuild
    row (None when no sample rebuilt)."""
    quiet, rebuilt = bucket_steady_samples(samples)
    st = ms_stats([f["ms"] for f in quiet])
    st.update({"bytes_per_push": (sum(f["bytes"] for f in quiet) / len(quiet)) if quiet else None,
               "samples": samples, "clients": apps, "rebuild_samples": len(rebuilt)})
    st2 = None
    if rebuilt:
        st2 = ms_stats([f["ms"] for f in rebuilt])
        st2["bytes_per_push"] = sum(f["bytes"] for f in rebuilt) / len(rebuilt)
    return st, st2


# ── the constructor-free SDK backend ────────────────────────────────────────────────────────────
def make_backend(sbmod, state, dormant_rows, all_regs):
    class BenchSdkBackend(sbmod.SdkBackend):
        def __getattr__(self, name):          # only reached for attributes the constructor would have set
            raise BenchError("perf-bench: the bench backend lacks attribute %r (a code path this tool "
                             "did not anticipate reached it — add it to make_backend)" % name)

        def live_sessions(self):
            out = {}
            for reg in sbmod.list_regs(self.state_dir):
                if reg.get("threadOf"):
                    continue
                if not reg.get("alive") and not self._bench_all_regs:
                    continue
                sid = reg.get("sid")
                if not sid:
                    continue
                row = self._live_row(reg, sid)                 # the real dormant-row derivation
                if not self._bench_dormant:
                    lsv = getattr(sbmod, "last_state_value", None)
                    if lsv is not None:
                        st = lsv(self.state_dir, sid)
                    else:
                        st = (sbmod.last_state(self.state_dir, sid) or {}).get("state") or ""
                    if st:
                        row["state"] = st
                    row["connected"] = True
                out[sid] = row
            return out

    be = BenchSdkBackend.__new__(BenchSdkBackend)
    be.__dict__.update({
        "state_dir": Path(state), "claude_bin": "/bin/false", "sessions": {},
        "_lock": threading.Lock(), "_reg_lock": threading.Lock(), "_pending_ask": {}, "_live": {},
        "_live_rev": {},    # the live tail's per-sid revision (Sessions.live_rev reads it for every chat signature)
        "_owns_memo": {},   # owns() memoizes on the reg file's identity here, and build_session and
        #   _alive_sessions reach owns() through Sessions.backend_for: without the slot the first chat
        #   build raises on the attribute instead of answering
        "_fork_children_memo": None, "_work_key_pin": "", "work_key": "",   # a property with a pin at
        #   HEAD (the pin wins), a plain attribute in older revisions (the instance value wins)
        "_problems": [], "_problem_seq": 0,
        "_problem_lock": threading.Lock(), "_sdk_missing": False, "_turn_seq": {}, "_drive_marks": {},
        "_drive_inflight": set(), "_heal_attempts": {}, "_notify": None, "_poke_cb": None,
        "_push_cb": None, "_push_session_cb": None, "_log_cb": None,
        "mcp_config": None, "append_prompt_path": None, "cli_scope": False, "thread_wake_model": None,
        "_bench_dormant": bool(dormant_rows), "_bench_all_regs": bool(all_regs),
    })
    return be


# ── loading ─────────────────────────────────────────────────────────────────────────────────────
def load_kernel(repo, shadow=None):
    """Import the checkout's kernel in-process. With a `shadow`, every Path.write_text the import
    performs against the state copy lands in the shadow instead (the kernel writes its repo-root
    marker at import, before any guard can be installed on the module); the diversion is removed
    once the import returns, and the guards install_guards puts on the named write doors take over."""
    kpath = os.path.join(repo, "kernel", "kernel.py")
    if not os.path.isfile(kpath):
        raise BenchError("no kernel at %s" % kpath)
    # The loader is the project's load_source (kernel/loadsource.py: a file-path import with the sys.modules
    # semantics of SourceFileLoader.load_module(), without the deprecated call). It comes from THIS tool's
    # checkout, not from --repo: it is stdlib glue independent of the revision under measurement, so a
    # candidate checkout without the module is still benchable, and it is bootstrapped here rather than at
    # module level because the tool loads no romp code at import.
    _ls_spec = importlib.util.spec_from_file_location(
        "romp_loadsource", os.path.join(os.path.dirname(os.path.dirname(_TOOL_FILE)), "kernel", "loadsource.py"))
    _ls_mod = importlib.util.module_from_spec(_ls_spec)
    _ls_spec.loader.exec_module(_ls_mod)
    load_source = _ls_mod.load_source
    real_write_text = Path.write_text

    def diverted_write_text(self, data, *a, **k):
        target = shadow.target(self) if shadow.rel(self) is not None else self
        return real_write_text(target, data, *a, **k)
    if shadow is not None:
        Path.write_text = diverted_write_text
    try:
        km = load_source("romp_kernel_perf_bench", kpath)
        # a backend already loaded under this name (by the kernel's import, or earlier in this process) is
        # reused: load_source executes a name already in sys.modules again, in place
        sbmod = sys.modules.get("romp_sdk_backend")
        if sbmod is None:
            sbmod = load_source("romp_sdk_backend", os.path.join(repo, "kernel", "sdk_backend.py"))
    finally:
        Path.write_text = real_write_text
    for sym in ("_live_scope", "Sessions", "build_session", "build_feed", "build_timeline", "_push",
                "_names_snapshot", "_sessions", "_parse", "_parse_cache", "_live_map", "_atomic_write",
                "_built_feed", "_built_timeline", "em", "jd"):
        if not hasattr(km, sym):
            raise BenchError("this kernel lacks %s; the harness does not know how to drive it" % sym)
    if not hasattr(km.em, "_ASM_STATS"):
        raise BenchError("this kernel's event model has no _ASM_STATS; the cold-build check cannot run")
    return km, sbmod


def git_head(repo):
    """The checkout's HEAD sha (12 chars) read from the git files directly — no `git` process, so a
    strace of this tool shows only the kernel's own spawns. None when it cannot be read."""
    try:
        dotgit = os.path.join(repo, ".git")
        gitdir = dotgit
        if os.path.isfile(dotgit):                       # a worktree: one line, gitdir: <private dir>
            with open(dotgit) as f:
                line = f.readline().strip()
            if not line.startswith("gitdir:"):
                return None
            gitdir = line[len("gitdir:"):].strip()
            if not os.path.isabs(gitdir):
                gitdir = os.path.normpath(os.path.join(repo, gitdir))
        with open(os.path.join(gitdir, "HEAD")) as f:
            head = f.read().strip()
        if not head.startswith("ref:"):
            return head[:12]
        ref = head[4:].strip()
        common = gitdir
        cf = os.path.join(gitdir, "commondir")           # a worktree's refs live in the main repo's dir
        if os.path.isfile(cf):
            with open(cf) as f:
                common = os.path.normpath(os.path.join(gitdir, f.read().strip()))
        rp = os.path.join(common, ref)
        if os.path.isfile(rp):
            with open(rp) as f:
                return f.read().strip()[:12]
        with open(os.path.join(common, "packed-refs")) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0][:12]
    except OSError:
        return None
    return None


# ── timing ──────────────────────────────────────────────────────────────────────────────────────
def ms_stats(samples):
    if not samples:
        return {"n": 0, "min": None, "median": None, "max": None, "mean": None}
    return {"n": len(samples), "min": round(min(samples), 2), "median": round(statistics.median(samples), 2),
            "max": round(max(samples), 2), "mean": round(statistics.fmean(samples), 2)}


def single(ms, **extra):
    d = {"n": 1, "min": None, "median": round(ms, 2), "max": None, "mean": None}
    d.update(extra)
    return d


def timed(fn, iters, before=None, warmup=1, after=None):
    """Call `before` (untimed) then `fn` (timed), `warmup` times discarded, then `iters` times kept;
    `after(sample_ms)` runs untimed after each kept sample."""
    last = None
    for _ in range(warmup):
        if before:
            before()
        last = fn()
    samples = []
    for _ in range(iters):
        if before:
            before()
        t0 = time.perf_counter()
        last = fn()
        ms = (time.perf_counter() - t0) * 1000.0
        samples.append(ms)
        if after:
            after(ms)
    return ms_stats(samples), last


def profile_once(fn, before=None):
    if before:
        before()
    pr = cProfile.Profile()
    pr.enable()
    t0 = time.perf_counter()
    fn()
    ms = (time.perf_counter() - t0) * 1000.0
    pr.disable()
    return pr, ms


def profile_rows(pr, key, top, repo):
    st = pstats.Stats(pr)
    idx = 3 if key == "cumulative" else 2
    rows = []
    for (fn, line, name), (cc, nc, tt, ct, _callers) in st.stats.items():
        rows.append((ct if idx == 3 else tt, fn, line, name, nc, cc, tt, ct))
    rows.sort(key=lambda r: r[0], reverse=True)
    out = []
    repo_p = os.path.realpath(repo) + os.sep
    for _k, fn, line, name, nc, cc, tt, ct in rows[:top]:
        f = fn
        if f.startswith(repo_p):
            f = f[len(repo_p):]
        elif f.startswith("<") or f.startswith("~"):
            pass
        else:
            f = os.path.basename(f)
        out.append({"func": name, "file": f, "line": line, "ncalls": nc if nc == cc else "%d/%d" % (nc, cc),
                    "tottime_ms": round(tt * 1000, 2), "cumtime_ms": round(ct * 1000, 2)})
    return out


def profile_entry(fn, before, repo):
    pr, ms = profile_once(fn, before=before)
    return {"ms": round(ms, 2), "cumulative": profile_rows(pr, "cumulative", PROFILE_TOP, repo),
            "tottime": profile_rows(pr, "tottime", PROFILE_TOP, repo)}


def fmt_profile(title, rows):
    lines = ["  %s" % title, "  %10s %12s %12s  %s" % ("ncalls", "tottime ms", "cumtime ms", "function")]
    for r in rows:
        lines.append("  %10s %12.2f %12.2f  %s:%s(%s)" % (r["ncalls"], r["tottime_ms"], r["cumtime_ms"],
                                                        r["file"], r["line"], r["func"]))
    return "\n".join(lines)


# ── state fingerprint (what did the run write) ──────────────────────────────────────────────────
def fingerprint(state):
    """Every directory (keyed with a trailing slash), symlink (its target) and file (mtime and size)
    under the copy, so a directory the kernel creates (its many mkdir sites on STATE subdirectories)
    or a link it retargets shows in the census beside a changed file."""
    out = {}
    for root, dirs, files in os.walk(state):
        dirs[:] = [d for d in dirs if d != "sdkvenv"]
        for d in dirs:
            p = os.path.join(root, d)
            try:
                out[os.path.relpath(p, state) + "/"] = ("link", os.readlink(p)) if os.path.islink(p) else "dir"
            except OSError:
                continue
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                out[os.path.relpath(p, state)] = ("link", os.readlink(p))
            else:
                out[os.path.relpath(p, state)] = (st.st_mtime_ns, st.st_size)
    return out


def fingerprint_diff(before, after):
    changed = sorted(k for k in after if k in before and after[k] != before[k])
    new = sorted(k for k in after if k not in before)
    gone = sorted(k for k in before if k not in after)
    return {"changed": len(changed), "new": len(new), "removed": len(gone),
            "sample": (["~ " + k for k in changed] + ["+ " + k for k in new] + ["- " + k for k in gone])[:40]}


# ── fake WebSocket clients ──────────────────────────────────────────────────────────────────────
_FRAME_HEAD_RE = re.compile(r'"(type|slot)": "([^"]*)"')


def frame_label(km, c, s):
    """The per-slot label for one frame handed to a fake client. _client_send leaves the dedup key on
    the client (`curSlot`) for the length of its call, and that key names the slot. A frame that arrives
    with no key set (a --repo checkout from before the keyed delta path went through _client_send, or a
    one-shot reply) is labelled from its own head: `{"type": "delta", "slot": "bars", ...}` books as
    bars-delta, and any other direct frame as its type."""
    key = c.get("curSlot")
    if key is not None:
        return km._perf_slot(key) if hasattr(km, "_perf_slot") else str(key)
    head = dict(_FRAME_HEAD_RE.findall(s[:160]))
    if head.get("slot"):
        return head["slot"] + "-delta"
    return head.get("type") or "unknown"


def fake_client(km, app, active=None):
    c = {"app": app, "wid": "perf-bench-" + app, "alive": True, "ready": True, "sock": None,
         "dlock": threading.RLock(), "qlock": threading.Lock(), "sent": {}, "echat": {},
         "delta": True, "caps": set(), "bytes": {}, "frames": 0}
    for cap in ("FEED_DELTA_CAP", "READY_GATE_CAP"):
        if hasattr(km, cap):
            c["caps"].add(getattr(km, cap))
    if active:
        c["active"] = active

    def send(s):
        label = frame_label(km, c, s)
        c["bytes"][label] = c["bytes"].get(label, 0) + len(s)
        c["frames"] += 1
    c["send"] = send
    return c


def reset_client_bytes(clients):
    for c in clients:
        c["bytes"] = {}
        c["frames"] = 0


def client_bytes(clients):
    return {c["app"]: {"frames": c["frames"], "slots": dict(sorted(c["bytes"].items()))} for c in clients}


def total_bytes(clients):
    return sum(sum(c["bytes"].values()) for c in clients)


# ── the run ─────────────────────────────────────────────────────────────────────────────────────
def run(args, state, mirror_of, out, private):
    repo = os.path.realpath(args.repo) if args.repo else os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    out["repo"], out["repo_head"], out["state"], out["state_mirror_of"] = repo, git_head(repo), state, mirror_of
    maps = parse_cwd_maps(args.cwd_map)
    out["cwd_map"] = [{"from": frm, "to": to, "hits": 0} for frm, to in maps]
    out["env_changes"] = prepare_env(state, args.claude_dir, private)
    rec = new_recorder()
    shadow = StateShadow(state, os.path.join(private, "shadow"), rec["refused_writes"])
    fp_before = fingerprint(state)
    threads_before = {t.name for t in threading.enumerate()}
    try:
        _bench(args, state, repo, out, shadow, rec, maps)
        check_guards_held(rec)
    finally:
        # the census runs on EVERY exit path: a run that stopped on an error has still imported the
        # kernel and may have built, and what it wrote into the copy is what the caller needs to know
        out["writes"] = fingerprint_diff(fp_before, fingerprint(state))
        out["writes"]["shadowed"] = sorted(set(shadow.written))
        for rule, n in zip(out["cwd_map"], rec["cwd_map_hits"]):   # read here, after the builders' calls, not at discovery
            rule["hits"] = n
        out["notifications_suppressed"] = len(rec["notifications"])
        out["warm_calls_suppressed"] = len(rec["warm_calls"])
        out["gh_refreshes_suppressed"] = len(rec["gh_kicks"])
        out["thread_starts"] = rec["thread_starts"]
        out["spawn_attempts"] = rec["spawns"]
        out["git_queries"] = {"answered_as_failure": bool(args.no_git), "calls": git_calls_total(rec)}
        out["nss_lookups"] = dict(rec["nss"])
        out["threads_new"] = sorted({t.name for t in threading.enumerate()} - threads_before)
        out["refused_writes"] = list(rec["refused_writes"])
    return out


def transcript_search(jd, state, sids):
    """Counts of what discovery had to work with for `sids`: their distinct names-registry cwds, how many
    of those resolve (through any --cwd-map) to a project directory that exists, and the transcripts in
    those directories. Counts only, no paths: this goes into the report and the no-transcript error."""
    cwds = set()
    for sid in sids:
        try:
            parts = (Path(state) / "names" / sid).read_text().rstrip("\n").split("\t")
        except OSError:
            continue
        if len(parts) > 1 and parts[1]:
            cwds.add(parts[1])
    present = jsonl = 0
    for cwd in cwds:
        pdir = jd._proj_dir(cwd)
        if os.path.isdir(pdir):
            present += 1
            jsonl += sum(1 for n in os.listdir(pdir) if n.endswith(".jsonl"))
    return {"cwds": len(cwds), "project_dirs": present, "transcripts": jsonl}


def _bench(args, state, repo, out, shadow, rec, maps):
    km, sbmod = load_kernel(repo, shadow)
    jd, em = km.jd, km.em
    be = make_backend(sbmod, state, args.dormant_rows, args.all_regs_live)
    km._sdk_backend = be                       # _sdk() returns this without constructing the real one
    if hasattr(km, "_codex_backend"):
        km._codex_backend = False              # "module unavailable": _codex() returns None, loads nothing
    out["neutralized"] = install_guards(km, sbmod, shadow, rec, no_git=args.no_git)
    map_hits = rec["cwd_map_hits"] = install_cwd_map(jd, maps)   # run()'s census copies the counts when the run ends
    if maps:
        out["neutralized"].append("jd._proj_dir (cwd-map)")   # the derivation is wrapped: say so in the report
    bench = out["benchmarks"] = {}
    profiles = out["profiles"] = {}
    iters = max(1, args.iters)
    out["cold_caches"] = {"kernel": [n for n in COLD_KERNEL_CACHES + ("_chat_fold", "_feed_memo") if callable(getattr(getattr(km, n, None), "clear", None))],
                          "event_model": [n for n, _lock in COLD_EM_CACHES if isinstance(getattr(em, n, None), dict)]}

    def now():
        return int(time.time())

    def scope(live_map):
        """The pusher cycle's scope, as _pusher_cycle opens it: the liveness snapshot, the sid->path memo,
        the discover-rows memo (a per-cycle memo slot some kernel revisions read) and the names snapshot."""
        km._live_scope.snapshot = live_map
        km._live_scope.paths = {}
        km._live_scope.sessions = {}
        km._live_scope.names = km._names_snapshot()

    def unscope():
        km._live_scope.snapshot = None
        km._live_scope.names = None
        km._live_scope.paths = None
        km._live_scope.sessions = None

    def new_cycle():
        """A fresh cycle's per-cycle memos (what _pusher_cycle resets between two cycles)."""
        km._live_scope.paths = {}
        km._live_scope.sessions = {}

    def clear_kernel_caches():
        """The kernel-side caches a freshly started kernel lacks (build_session's inputs above the parse)."""
        for name in COLD_KERNEL_CACHES:
            d = getattr(km, name, None)
            if callable(getattr(d, "clear", None)):   # a dict, or the kernel's view over the shared parse store (T323 stage 2)
                d.clear()
        if hasattr(km, "_chat_fold"):
            lock = getattr(km, "_chat_fold_lock", None)
            if lock is not None:
                with lock:
                    km._chat_fold.clear()
            else:
                km._chat_fold.clear()
        clear_feed_memo()

    def clear_feed_memo():
        """The feed's per-session card memo, which a freshly started kernel also lacks (2026-09-18). An entry the memo
        holds under a WARM parse re-reads its parse in place when the store misses (the kernel's warm-to-stale
        re-read), so a feed built over an emptied parse store but a warm memo parses inside the build and never
        takes the cold branch that asks for the background warm: the cold rows and build_feed_noparse would measure
        that re-read, not the cards-first boot they name. Dropped through the kernel's own forget (every sid gone),
        so its counters stay right; a revision without the memo has nothing to drop."""
        if hasattr(km, "_feed_memo_forget"):
            km._feed_memo_forget(set())

    def clear_em_caches():
        """The event model's parse-layer caches, under their locks; a name this revision lacks is skipped
        (out["cold_caches"] says which were emptied; the per-sample assembly check is what proves a
        sample cold)."""
        for name, lock_name in COLD_EM_CACHES:
            d = getattr(em, name, None)
            if not callable(getattr(d, "clear", None)):
                continue
            lock = getattr(em, lock_name, None)
            if lock is not None:
                with lock:
                    d.clear()
            else:
                d.clear()

    def clear_all_caches():
        clear_kernel_caches()
        clear_em_caches()
        gc.collect()

    class Counters:
        """Per-row deltas of the git call counter, the assembly counters and the NSS counter, over the
        KEPT samples only: timed_counted() runs the warm-up first, then resets these, then times."""
        def __init__(self):
            self.reset()

        def reset(self):
            self.git0, self.asm0, self.nss0, self.n = git_calls_total(rec), dict(em._ASM_STATS), dict(rec["nss"]), 0

        def row(self):
            g1, nss1 = git_calls_total(rec), rec["nss"]
            git = {k: g1.get(k, 0) - self.git0.get(k, 0) for k in set(g1) | set(self.git0) if g1.get(k, 0) != self.git0.get(k, 0)}
            nss = {k: nss1.get(k, 0) - self.nss0.get(k, 0) for k in set(nss1) | set(self.nss0) if nss1.get(k, 0) != self.nss0.get(k, 0)}
            n = max(1, self.n)
            return {"git_per_build": {k: round(v / n, 2) for k, v in git.items()},
                    "asm": asm_delta(em, self.asm0), "nss_per_build": {k: round(v / n, 2) for k, v in nss.items()}}

    def timed_counted(fn, before=None, after=None):
        """timed() with the per-row counters reset AFTER the warm-up, so a row's git/asm/nss deltas
        describe exactly its kept samples."""
        if before:
            before()
        fn()
        ctr.reset()
        st, last = timed(fn, iters, before=before, warmup=0, after=after)
        st.update(ctr.row())
        return st, last

    # liveness + names snapshots (the pusher cycle's per-cycle reads)
    bench["liveness_snapshot"], live_map = timed(lambda: km.Sessions.live(), iters)
    bench["names_snapshot"], _ = timed(lambda: km._names_snapshot(), iters)
    regs = sbmod.list_regs(state)
    check_snapshot_rows(live_map, regs, args.all_regs_live)      # exact by construction: one row per counted reg
    out["liveness"] = {"live": len(live_map), "alive_regs": sum(1 for r in regs if r.get("alive") and not r.get("threadOf")),
                       "closed_regs": sum(1 for r in regs if not r.get("alive") and not r.get("threadOf")),
                       "thread_regs": sum(1 for r in regs if r.get("threadOf")),
                       "rows": "dormant" if args.dormant_rows else "states-file",
                       "states": {sid[:8]: (row.get("state") if isinstance(row, dict) else None) for sid, row in live_map.items()}}
    scope(live_map)
    try:
        # discovery
        discover_clears = [0]

        def cold_discover():
            cache = getattr(jd, "_discover_cache", None)
            if isinstance(cache, dict):
                cache.clear()
                discover_clears[0] += 1
        bench["discover_cold"], _ = timed(lambda: jd.discover(now()), iters, before=cold_discover)
        out["discover_cache_cleared"] = discover_clears[0]      # iters + the warm-up: one clear per sample
        bench["discover_warm"], _ = timed(lambda: jd.discover(now()), iters)

        # ONE world: discovery's live sessions, plus every live sid outside its window resolved the way
        # _alive_sessions resolves them for the builders
        sessions = [s for s in km._sessions(now()) if s["sid"] in live_map]
        have = {s["sid"] for s in sessions}
        missing = [sid for sid in live_map if sid not in have]
        backfilled = []
        if missing and hasattr(jd, "DEATH_BACKFILL_WINDOW"):
            wide = {f[0]: f for f in jd.discover(now(), window=jd.DEATH_BACKFILL_WINDOW)}
            for sid in missing:
                ent = wide.get(sid)
                if ent is None:
                    continue
                fsid, path, anchor, name = ent
                try:
                    mtime = os.stat(path).st_mtime
                except OSError:
                    mtime = 0
                sessions.append({"sid": fsid, "name": name or fsid[:8], "anchor": anchor, "path": str(path), "mtime": mtime})
                backfilled.append(sid)
        no_transcript = [sid for sid in missing if sid not in set(backfilled)]
        searched = transcript_search(jd, state, live_map)
        if live_map and not sessions:
            # after the backfill, not before it: a copy whose transcripts are all older than the
            # discovery window is a normal copy, and the backfill is what finds them
            raise BenchError("discovery found no transcript for any of the %d live sessions: %d in the %d h window, "
                             "%d more in the %d-day backfill; their %d registry cwd(s) resolve to %d existing project "
                             "director%s holding %d transcript(s)%s. Causes: a wrong --claude-dir; registry cwds a "
                             "redaction rewrote while the project directories kept their names (see --cwd-map); a copy "
                             "whose transcripts are older than the backfill window"
                             % (len(live_map), len(have), int(getattr(jd, "WINDOW", 0)) // 3600, len(backfilled),
                                int(getattr(jd, "DEATH_BACKFILL_WINDOW", 0)) // 86400, searched["cwds"],
                                searched["project_dirs"], "y" if searched["project_dirs"] == 1 else "ies",
                                searched["transcripts"],
                                (" (%s)" % ", ".join("cwd-map rule %d hit %d" % (i + 1, n) for i, n in enumerate(map_hits))) if maps else ""))
        for s in sessions:
            try:
                s["bytes"] = os.path.getsize(s["path"])
            except OSError:
                s["bytes"] = 0
        sessions.sort(key=lambda s: s["bytes"], reverse=True)
        picked = sessions[:max(0, args.sessions)]
        out["live_transcripts"] = {"count": len(sessions), "bytes": sum(s["bytes"] for s in sessions),
                                   "in_window": len(have), "backfilled": len(backfilled),
                                   "no_transcript": [sid[:8] for sid in no_transcript], "searched": searched}

        # build_session: cold (every cache), emwarm (kernel caches only), warm — per picked transcript
        out["benched_sessions"] = []
        ctr = Counters()
        for s in picked:
            sid, tag = s["sid"], s["sid"][:8]
            km._live_scope.paths = {}
            asm_before = {}

            def cold_before():
                clear_all_caches()
                asm_before.clear()
                asm_before.update(em._ASM_STATS)

            def cold_after(ms, _tag=tag, _path=os.path.realpath(s["path"])):
                check_cold_sample(em, _tag, _path, asm_before)
                ctr.n += 1
            st_cold, m = timed_counted(lambda: km.build_session(sid, now(), live_map), before=cold_before, after=cold_after)
            bench["build_session_cold:" + tag] = st_cold

            def emwarm_before():
                clear_kernel_caches()
                gc.collect()

            def count_after(ms):
                ctr.n += 1
            st_em, _ = timed_counted(lambda: km.build_session(sid, now(), live_map), before=emwarm_before, after=count_after)
            bench["build_session_emwarm:" + tag] = st_em
            st_warm, _ = timed_counted(lambda: km.build_session(sid, now(), live_map), after=count_after)
            bench["build_session_warm:" + tag] = st_warm
            out["benched_sessions"].append({"sid8": tag, "name": s.get("name", ""), "bytes": s["bytes"],
                                            "events": len((m or {}).get("events") or [])})
            if args.profile:
                profiles["build_session_cold:" + tag] = profile_entry(lambda: km.build_session(sid, now(), live_map), clear_all_caches, repo)
                profiles["build_session_warm:" + tag] = profile_entry(lambda: km.build_session(sid, now(), live_map), None, repo)

        # every live parse once from empty (the boot-time cost); kept warm for the feed/timeline
        clear_all_caches()
        t0 = time.perf_counter()
        for s in sessions:
            km._parse(s["path"], s["sid"], now())
        bench["warm_all_parses"] = single((time.perf_counter() - t0) * 1000, sessions=len(sessions))

        # feed + timeline in the steady state
        bench["build_feed"], feed = timed(lambda: km.build_feed(now(), live_map), iters)
        bench["build_feed"]["cards"] = {k: len(feed.get(k) or []) for k in ("asks", "working", "awaiting") if isinstance(feed, dict)}
        bench["build_timeline_bars"], tl = timed(lambda: km.build_timeline(now(), live_map, with_bars=True), iters)
        bench["build_timeline_bars"]["lanes"] = len((tl or {}).get("sessions") or [])
        bench["build_timeline_skel"], _ = timed(lambda: km.build_timeline(now(), live_map, with_bars=False), iters)
        if args.profile:
            profiles["build_feed"] = profile_entry(lambda: km.build_feed(now(), live_map), None, repo)
            profiles["build_timeline_bars"] = profile_entry(lambda: km.build_timeline(now(), live_map, with_bars=True), None, repo)
        # the feed with nothing parsed and nothing memoized (cards-first boot)
        def noparse_before():
            km._parse_cache.clear()
            clear_feed_memo()
        bench["build_feed_noparse"], _ = timed(lambda: km.build_feed(now(), live_map), iters, before=noparse_before)
        for s in sessions:
            km._parse(s["path"], s["sid"], now())

        # goal stores, largest first
        gdir = Path(jd.GOALDIR)
        stores = []
        if gdir.is_dir():
            for p in gdir.glob("*.json"):
                try:
                    stores.append((p.stat().st_size, p.stem))
                except OSError:
                    pass
        stores.sort(reverse=True)
        out["goal_stores"] = []
        for size, fsid in stores[:max(0, args.sessions)]:
            tag = fsid[:8]
            bench["load_goals:" + tag], store = timed(lambda: jd.load_goals(fsid), iters)
            out["goal_stores"].append({"fsid8": tag, "bytes": size, "nodes": len((store or {}).get("nodes") or {})})
            if args.profile and not any(k.startswith("load_goals:") for k in profiles):
                profiles["load_goals:" + tag] = profile_entry(lambda: jd.load_goals(fsid), None, repo)

        # the push over fake clients
        apps = [a.strip() for a in (args.clients or "").split(",") if a.strip()]
        if apps:
            active = picked[0]["sid"] if picked else None
            clients = [fake_client(km, a, active if a == "chat" else None) for a in apps]

            def built_at():
                return (km._built_feed[2], km._built_timeline[2])

            # the pusher's first cycle after a restart: every cache empty, empty dedup state
            clear_all_caches()
            for e in (km._built_feed, km._built_timeline):
                e[1] = None
            for name in ("_feed_wire", "_bars_wire"):
                if hasattr(km, name):
                    setattr(km, name, None)
            unscope()
            scope(live_map)
            asm0 = dict(em._ASM_STATS)
            t0 = time.perf_counter()
            km._push(clients, live_map=live_map)
            bench["push_cold_cycle"] = single((time.perf_counter() - t0) * 1000, bytes=client_bytes(clients), clients=apps,
                                              asm=asm_delta(em, asm0))   # every live transcript parsed inside the cycle
            reset_client_bytes(clients)
            new_cycle()
            km._push(clients, live_map=live_map)               # a warming cycle: baselines, wire caches, chat cache
            reset_client_bytes(clients)

            # a page load: one fresh client with empty dedup state, connect=True
            for app in apps:
                holder = {}

                def connect_before(_app=app):
                    holder["c"] = fake_client(km, _app, active if _app == "chat" else None)
                    new_cycle()

                def connect_push():
                    km._push([holder["c"]], connect=True, live_map=live_map)
                st, _ = timed(connect_push, iters, before=connect_before)
                st["bytes"] = client_bytes([holder["c"]])[app]
                bench["push_connect:" + app] = st
            reset_client_bytes(clients)

            # the periodic push, steady state; rebuild samples separated
            samples = []
            for _ in range(3 * iters):
                new_cycle()
                reset_client_bytes(clients)
                b0 = built_at()
                t0 = time.perf_counter()
                km._push(clients, live_map=live_map)
                ms = (time.perf_counter() - t0) * 1000
                b1 = built_at()
                samples.append({"ms": round(ms, 2), "rebuilt_feed": b1[0] != b0[0], "rebuilt_timeline": b1[1] != b0[1],
                                "bytes": total_bytes(clients)})
                if len(bucket_steady_samples(samples)[0]) >= iters:
                    break
            st, st2 = steady_rows(samples, apps)
            rebuilt = bucket_steady_samples(samples)[1]
            bench["push_steady"] = st
            if st2 is not None:
                bench["push_steady_rebuild"] = st2
            reset_client_bytes(clients)
            new_cycle()
            km._push(clients, live_map=live_map)
            bench["push_steady"]["bytes"] = client_bytes(clients)   # one further cycle's per-slot bytes
            out["push_rebuilds"] = len(rebuilt)
            if args.profile:
                def one_push():
                    new_cycle()
                    km._push(clients, live_map=live_map)
                profiles["push_steady"] = profile_entry(one_push, None, repo)
    finally:
        unscope()


# ── reporting ───────────────────────────────────────────────────────────────────────────────────
def fmt_ms(v):
    return "%9.2f" % v if isinstance(v, (int, float)) else "%9s" % "-"


def row_note(name, st):
    parts = []
    g = st.get("git_per_build") or {}
    if g:
        parts.append("git/build " + ",".join("%s=%s" % kv for kv in sorted(g.items())))
    a = st.get("asm") or {}
    if a:
        parts.append("asm " + ",".join("%s=%s" % kv for kv in sorted(a.items())))
    if name.startswith("build_session_cold") and (a.get("serve") or a.get("fold")):
        # the sample started with the assembly cache empty, so a serve or fold inside it can only be a
        # second read of a transcript the same build assembled (the emwarm row's serve is its definition)
        parts.append("(a transcript read twice inside one build)")
    if st.get("rebuild_samples") is not None:
        parts.append("%d rebuild sample(s) set aside" % st["rebuild_samples"])
    if st.get("bytes_per_push") is not None:
        parts.append("%.0f B/push" % st["bytes_per_push"])
    return "  ".join(parts)


def render_writes(out):
    """The write census, one block: what changed in the copy (nothing, when every writer is known) and
    the relative paths the shadow redirects, whether or not a write landed on each. Printed whether or
    not the run got as far as a benchmark row, so an error run says what it did to the copy too."""
    w = out.get("writes")
    if w is None:
        return ["writes into the state copy: not measured (the run stopped before the census could start)"]
    L = ["writes into the state copy: %d changed, %d new, %d removed" % (w.get("changed", 0), w.get("new", 0), w.get("removed", 0))]
    for s in w.get("sample", []):
        L.append("  " + s)
    L.append("writes redirected to the private dir, not the copy (paths, landed or not): %s" % (", ".join(w.get("shadowed") or []) or "none"))
    return L


def render_text(out, profile):
    L = []
    L.append("perf-bench  repo=%s (%s)  state=%s%s" % (out["repo"], out.get("repo_head") or "no git",
                                                     out["state"], ("  [mirror of %s]" % out["state_mirror_of"]) if out.get("state_mirror_of") else ""))
    lv = out.get("liveness", {})
    lt = out.get("live_transcripts", {})
    L.append("liveness: %d live rows (%d alive regs, %d closed, %d threads skipped; rows from %s); "
             "%d live transcripts (%d in the discovery window, %d backfilled, %d without one), %.1f MB total"
             % (lv.get("live", 0), lv.get("alive_regs", 0), lv.get("closed_regs", 0), lv.get("thread_regs", 0), lv.get("rows", "?"),
                lt.get("count", 0), lt.get("in_window", 0), lt.get("backfilled", 0), len(lt.get("no_transcript") or []), lt.get("bytes", 0) / 1e6))
    if lt.get("no_transcript"):
        L.append("live sids with no transcript anywhere: " + ", ".join(lt["no_transcript"]))
    sr = lt.get("searched") or {}
    if sr:
        L.append("transcript search: %d registry cwd(s) -> %d existing project director%s holding %d transcript(s)%s"
                 % (sr.get("cwds", 0), sr.get("project_dirs", 0), "y" if sr.get("project_dirs", 0) == 1 else "ies",
                    sr.get("transcripts", 0),
                    ("; cwd-map hits: " + ", ".join("rule %d=%d" % (i + 1, r.get("hits", 0)) for i, r in enumerate(out["cwd_map"]))) if out.get("cwd_map") else ""))
    if out.get("benched_sessions"):
        L.append("transcripts benched: " + ", ".join("%s (%.1f MB, %d events)" % (s["sid8"], s["bytes"] / 1e6, s["events"])
                                                    for s in out["benched_sessions"]))
    if out.get("goal_stores"):
        L.append("goal stores benched: " + ", ".join("%s (%.0f KB, %d nodes)" % (g["fsid8"], g["bytes"] / 1e3, g["nodes"])
                                                     for g in out["goal_stores"]))
    L.append("")
    L.append("%-34s %4s %9s %9s %9s  %s" % ("benchmark", "n", "min ms", "median", "max", "notes"))
    for name, st in out["benchmarks"].items():
        L.append("%-34s %4d %s %s %s  %s" % (name, st.get("n", 0), fmt_ms(st.get("min")), fmt_ms(st.get("median")), fmt_ms(st.get("max")), row_note(name, st)))
    for name, st in out["benchmarks"].items():
        if name.startswith("push") and st.get("bytes"):
            L.append("")
            L.append("%s bytes per client slot:" % name)
            b = st["bytes"] if "frames" not in st["bytes"] else {name.split(":", 1)[1]: st["bytes"]}
            for app, d in b.items():
                slots = ", ".join("%s=%d" % (k, v) for k, v in d["slots"].items()) or "nothing sent"
                L.append("  %-9s %4d frames  %s" % (app, d["frames"], slots))
    L.append("")
    L.extend(render_writes(out))
    L.append("neutralized: " + ", ".join(out.get("neutralized", [])))
    g = out.get("git_queries", {})
    L.append("git read-only queries %s: %s" % ("answered as failures (--no-git)" if g.get("answered_as_failure") else "run",
                                             ", ".join("%s=%d" % kv for kv in sorted(g.get("calls", {}).items())) or "none"))
    L.append("name-service lookups (AF_UNIX connects under strace): %s"
             % (", ".join("%s=%d" % kv for kv in sorted(out.get("nss_lookups", {}).items())) or "none"))
    cc = out.get("cold_caches") or {}
    if cc:
        L.append("caches emptied before each cold build_session sample: kernel %s; event model %s"
                 % (", ".join(cc.get("kernel") or []) or "none", ", ".join(cc.get("event_model") or []) or "none"))
    L.append("notifications suppressed: %d; background parses suppressed: %d; gh refreshes suppressed: %d; refused spawns: %d; "
             "refused writes: %d; threads started: %d; new threads: %s"
             % (out.get("notifications_suppressed", 0), out.get("warm_calls_suppressed", 0), out.get("gh_refreshes_suppressed", 0),
                len(out.get("spawn_attempts", [])), len(out.get("refused_writes", [])), out.get("thread_starts", 0),
                out.get("threads_new") or "none"))
    if profile and out.get("profiles"):
        for name, p in out["profiles"].items():
            L.append("")
            L.append("profile %s (%.1f ms under cProfile)" % (name, p["ms"]))
            L.append(fmt_profile("top %d by cumulative time" % PROFILE_TOP, p["cumulative"]))
            L.append(fmt_profile("top %d by own time" % PROFILE_TOP, p["tottime"]))
    return "\n".join(L)


def _dig(d, dotted):
    for k in dotted.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def compare(a_path, b_path):
    with open(a_path) as f:
        a = json.load(f)
    with open(b_path) as f:
        b = json.load(f)
    L = ["perf-bench compare  A=%s (%s)  B=%s (%s)" % (a_path, a.get("repo_head") or "?", b_path, b.get("repo_head") or "?")]
    L.append("world:")
    mismatched = []
    for key, label in WORLD_KEYS:
        va, vb = _dig(a, key), _dig(b, key)
        flag = "" if va == vb else "   MISMATCH"
        if flag:
            mismatched.append(label)
        L.append("  %-28s A=%-8s B=%-8s%s" % (label, va, vb, flag))
    if mismatched:
        L.append("  the two runs saw different worlds (%s); deltas below compare unlike things" % ", ".join(mismatched))
    L.append("%-34s %9s %9s %10s %8s" % ("benchmark", "A median", "B median", "delta ms", "delta %"))
    ab, bb = a.get("benchmarks", {}), b.get("benchmarks", {})
    for name in ab:
        if name not in bb:
            continue
        ma, mb = ab[name].get("median"), bb[name].get("median")
        if not isinstance(ma, (int, float)) or not isinstance(mb, (int, float)):
            continue
        d = mb - ma
        pct = (d / ma * 100.0) if ma else float("inf")
        L.append("%-34s %9.2f %9.2f %+10.2f %+7.1f%%" % (name, ma, mb, d, pct))
    only_a = sorted(set(ab) - set(bb))
    only_b = sorted(set(bb) - set(ab))
    if only_a:
        L.append("only in A: " + ", ".join(only_a))
    if only_b:
        L.append("only in B: " + ", ".join(only_b))
    for name in sorted(set(ab) & set(bb)):
        if not name.startswith("push"):
            continue
        ba, bbb = (ab.get(name) or {}).get("bytes"), (bb.get(name) or {}).get("bytes")
        if not (ba and bbb):
            continue
        if "frames" in ba:                                  # a per-app row
            ba, bbb = {name.split(":", 1)[1]: ba}, {name.split(":", 1)[1]: bbb}
        for app in ba:
            if app in bbb:
                ta, tb = sum(ba[app]["slots"].values()), sum(bbb[app]["slots"].values())
                L.append("%s bytes %-9s A=%d B=%d delta=%+d" % (name, app, ta, tb, tb - ta))
    return "\n".join(L)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.compare:
        print(compare(*args.compare))
        return 0
    if not args.state:
        sys.stderr.write("perf-bench: --state DIR is required (or --compare A B)\n")
        return 2
    state = os.path.realpath(os.path.expanduser(args.state))
    if not os.path.isdir(state):
        sys.stderr.write("perf-bench: %s is not a directory\n" % state)
        return 2
    try:
        parse_cwd_maps(args.cwd_map)                 # an argument error, refused before anything is touched
    except BenchError as e:
        sys.stderr.write("perf-bench: %s\n" % e)
        return 2
    for sub in ("sdk", "names"):
        if not os.path.isdir(os.path.join(state, sub)):
            sys.stderr.write("perf-bench: %s has no %s/ — not a romp state directory?\n" % (state, sub))
            return 2
    mirror_of = mirror_root = None
    if state in live_state_dirs(os.environ):
        if not args.i_know_this_is_live:
            sys.stderr.write("perf-bench: %s is the LIVE state directory. Bench a copy (see --help), or pass "
                             "--i-know-this-is-live to have it mirrored to a temp directory first.\n" % state)
            return 2
        mirror_of = state
        mirror_root, state = mirror_state(state)
        sys.stderr.write("perf-bench: mirrored the live directory to %s (%s excluded); benching the mirror\n"
                         % (state, ", ".join(MIRROR_IGNORE)))
    private = tempfile.mkdtemp(prefix="romp-perf-private-")
    out = {"schema": SCHEMA, "tool": "perf-bench", "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "python": sys.version.split()[0], "iters": args.iters, "sessions_requested": args.sessions}
    rc = 0
    unexpected = None
    try:
        try:
            run(args, state, mirror_of, out, private)
        except BenchError as e:
            # keep what was measured: the rows so far print and the JSON carries the error, so a
            # revision that trips a guard late in the run still yields its earlier numbers
            out["error"] = str(e)
            rc = 1
        except BaseException as e:
            # a candidate kernel that raises at import or inside a builder, or a Ctrl-C: the census
            # run() took in its finally is the answer to "what did this do to the copy", so it prints
            # and the JSON is written before the exception continues out of main() with its traceback
            out["error"] = "%s: %s" % (type(e).__name__, e)
            rc = 1
            unexpected = e
        if out.get("benchmarks"):
            print(render_text(out, args.profile))
        else:
            print("\n".join(render_writes(out)))        # the run stopped before its first row: the census still prints
        if out.get("error"):
            sys.stderr.write("perf-bench: %s\n" % out["error"])
        if args.json:
            with open(args.json, "w") as f:
                json.dump(out, f, indent=1, sort_keys=True, default=repr)   # repr: a half-built row must not mask the error
            print("\njson written to %s%s" % (args.json, " (partial: the run stopped on an error)" if rc else ""))
        sys.stdout.flush()
        if unexpected is not None:
            raise unexpected
    finally:
        shutil.rmtree(private, ignore_errors=True)
        if mirror_root:
            if args.keep_mirror:
                sys.stderr.write("perf-bench: mirror kept at %s\n" % state)
            else:
                shutil.rmtree(mirror_root, ignore_errors=True)
                sys.stderr.write("perf-bench: mirror removed\n")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BenchError as e:
        sys.stderr.write("perf-bench: %s\n" % e)
        sys.exit(1)
