"""unittest-side twin of conftest.py's state floor (2026-08-12): `python -m unittest tests.test_x`
imports this package first, so the floor lands before any test module loads bin/romp-* — which
resolve their state root at import time. conftest.py covers only pytest; without this, a bare
unittest run operated on the REAL ~/.local/state/romp (tests/test_kernel.py overwrote the real
remotes.json that way today). Does NOT cover `cd tests && python -m unittest test_x`, where the
per-module preambles are the floor (tests/test_state_isolation_order.py enforces them). A direct
`python3 tests/test_x.py` reaches this package through the module's own `from romp_load import
load_source` (tests/romp_load.py's bootstrap, 2026-09-14), so it has this floor from that import on;
the module's preamble stays its first line of defence and follows the import.
Since 2026-09-14 the run's private temp root, the TMPDIR redirect into it and the owner marker the
kernel's sweep reads live here too (below), for the same reason: they were conftest.py's, so a bare
run had none of them."""
import atexit
import json
import os
import shutil
import sys
import tempfile
import time

# Temp-directory hygiene, the in-process half (2026-09-06): the suite used to leak every directory it
# made. This floor and conftest.py's each minted a state root per process and never removed it (two
# per pytest process: eighteen for a `pytest -n 8` run), and about three hundred test modules call
# tempfile.mkdtemp with no cleanup of their own. On a developer machine that ran the suite many times
# a day that left about 1.9 million directories under the system temp directory, enough that listing
# it took minutes and tens of gigabytes of memory, and the out-of-memory kills that followed took a
# running romp kernel down. The fix is at the source: every tempfile.mkdtemp call made in THIS
# process is recorded, and everything recorded is removed at interpreter exit (and, under pytest, at
# session end; see conftest.py). Only directories this process created, and only under the process
# temp dir, are ever removed: tempfile.gettempdir(), which is the private per-run root minted below
# under every entry point that imports this package. tempfile.TemporaryDirectory goes through the
# same mkdtemp and cleans itself first, so the exit sweep finds nothing of it.
# What this hook cannot see is the other half, the root's: directories made by CHILD processes
# (kernels, git, a shell's `mktemp -d`), files from mkstemp, and os.mkdir paths. Those land in the
# root because TMPDIR points there, and the root is removed whole at exit (and, under pytest, at
# run end by conftest.py, which names a survivor).
_MADE_DIRS = []
_REAL_MKDTEMP = tempfile.mkdtemp


def _tracked_mkdtemp(*a, **k):
    p = _REAL_MKDTEMP(*a, **k)
    _MADE_DIRS.append(p)
    return p


_tracked_mkdtemp.romp_tracked = True          # tests/test_tempdir_hygiene.py asserts the hook is installed
if not getattr(tempfile.mkdtemp, "romp_tracked", False):
    tempfile.mkdtemp = _tracked_mkdtemp


def remove_made_dirs():
    """Remove every directory this process minted through tempfile.mkdtemp. Idempotent; registered
    with atexit here and called again from conftest's pytest_sessionfinish."""
    root = os.path.realpath(tempfile.gettempdir())
    for p in reversed(list(_MADE_DIRS)):
        try:
            rp = os.path.realpath(p)
            if rp != root and rp.startswith(root + os.sep) and os.path.isdir(rp) and not os.path.islink(p):
                shutil.rmtree(rp, ignore_errors=True)
        except Exception:
            pass
    _MADE_DIRS.clear()


# The private temp root, the child-process half (2026-09-06, in conftest.py until 2026-09-14): every
# temp path a run creates lives under ONE `romp-tests-*` root, removed when the run ends. The hook
# above records and removes what THIS process mints through tempfile.mkdtemp, but a test's children
# — kernels, git, `mktemp -d` in a shell — and mkstemp or os.mkdir paths are outside its sight (a
# full run left ~5,600 of those per run at up to ten a second). So the process's temp dir is
# redirected: tempfile.tempdir is set directly (gettempdir() caches its first answer, and the line
# recording the system temp dir has just called it), and TMPDIR is exported so every child inherits
# the same root. Import-time, and in this package rather than conftest.py: the package is the first
# thing BOTH entry points import (pytest imports it before conftest.py; `python -m unittest
# tests.test_x` imports it before the module), so the root, the redirect and the marker below are in
# place for a bare run too. While these were conftest.py's, a bare run had no redirect and no marker:
# every child's `mktemp -d`, every mkstemp file and (once killed mid-run, with the hook's exit sweep
# never reached) every tracked directory and the romp-tests-state-* dir landed loose in the system
# temp dir, and with no marker the kernel's sweep had to refuse them, so a shared machine
# accumulated thousands of them from runs that were killed (2026-09-14).
# The root itself is minted with the REAL mkdtemp: it is not the hook's to remove (the hook's scope
# is the inside of the root), so it is not recorded. The state dir below is minted AFTER the redirect,
# through the hook, and so sits INSIDE the root; nothing a run makes sits outside it.
# Under pytest-xdist this runs in the controller and in every worker: each imported this package and
# so owns a root of its own (a worker's sits inside the controller's, since it inherits that TMPDIR).
# The system temp dir — the one the RUN was handed, before any redirect — is recorded once, by the
# first process to import this package: an xdist worker inherits the controller's record along with
# its TMPDIR (setdefault, not an assignment: a worker's own gettempdir() is the controller's root, and
# recording that put the worker's fallback one level deeper than a socket path can bear under a
# long TMPDIR — four socket tests failed at bind under -n 2). A test that must leave the root (an
# AF_UNIX socket path that would not fit sun_path under a nested root) falls back to it, and only
# to it — a literal system path in a `dir=` would bypass the redirect (one did).
os.environ.setdefault("ROMP_TESTS_SYSTEM_TMPDIR", tempfile.gettempdir())
TMP_ROOT = _REAL_MKDTEMP(prefix="romp-tests-")
tempfile.tempdir = TMP_ROOT
os.environ["TMPDIR"] = TMP_ROOT

# Owner marker (2026-09-10): a run that dies without reaching any removal below — pytest-timeout's
# os._exit, a kernel restart cutting the tool shell, the cut-turn reaper's kill — leaves its root
# standing, and on a shared machine those roots piled into millions of files that the next boot's
# /tmp cleanup spent 39 minutes deleting. Nothing in this process can run after such a death, so the
# removal has to come from outside: the kernel's boot reconcile sweeps `romp-tests-*` roots under the
# system temp dir whose owner is dead (sdk_backend.sweep_dead_test_roots). This marker is what it
# reads — the owning pid, written at mint time so it is there for the whole life of the root. A root
# WITHOUT a marker is not touched (the sweep cannot tell a foreign directory from a pre-marker one).
# One marker per root: the package state dir sits inside the root now and goes with it.
TEST_ROOT_OWNER_MARKER = "romp-tests-owner.json"     # the kernel's sdk_backend.TEST_ROOT_OWNER_MARKER agrees


def write_owner_marker(d):
    if not d:
        return
    try:
        with open(os.path.join(d, TEST_ROOT_OWNER_MARKER), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "started": time.time(),
                                 "argv": [os.path.basename(a) for a in sys.argv[:3]]}))
    except OSError:
        pass                                 # a root we cannot write into is one we cannot leak into either


write_owner_marker(TMP_ROOT)


def remove_tmp_root():
    """Remove the root whole, whatever the hook's sweep could not see. Idempotent and silent: under
    pytest, conftest's pytest_unconfigure does the same first and names a survivor; this is the exit
    fallback for a bare run and for a pytest exit that skipped the hooks."""
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


# atexit runs the last registration first: the root's removal is registered BEFORE the sweep's, so at
# exit the sweep runs first (every tracked directory, all inside the root) and the root goes after it,
# whole. Nothing runs after an os._exit (pytest-timeout's thread method ends a hung run that way, and a
# kill is the same), so such a run leaves ONE top-level entry in the system temp dir, this root with
# its marker, for the kernel's sweep.
atexit.register(remove_tmp_root)
atexit.register(remove_made_dirs)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="romp-tests-state-")
# Minted after the redirect above, through the hook: inside the root, recorded, removed by the sweep
# at exit and with the root either way. conftest.py reads STATE_DIR for its own pins.
STATE_DIR = os.environ["XDG_STATE_HOME"]
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# No unittest run may reach a REAL manager or kernel either: the same three ports conftest.py poisons
# for pytest (its comment has the readers and the incident). Set to a dead port, never popped: every
# reader maps an absent variable to the default port, which on a machine running romp is the live one.
os.environ["ROMP_MANAGER_PORT"] = "1"
os.environ["ROMP_KERNEL_PORT"] = "1"
os.environ["ROMP_SERVE_PORT"] = "1"
os.environ["ROMP_CKPT_FIRST_DOC_KB"] = "0"   # the young-session floor off for the fixtures, the twin of conftest.py's line (1721 round two): a
#                                                bare unittest run gave 113 failures with every checkpoint shape skipped young without it
os.environ["ROMP_GC_FREEZE"] = "off"   # #1735: the freeze off in the suite, the twin of conftest.py's line, so an in-process pusher cycle
#                                        never freezes the test interpreter's heap; the freeze's tests drive the controller directly
os.environ.setdefault("ROMP_REG_LOCK_GUARD", "raise")   # the twin of conftest.py's line: a session loop thread that waits on the
#                                        backend's registry lock raises in the suite (sdk_backend._RegLock)

# `from romp_load import load_source` in a test module (tests/romp_load.py): under pytest and
# `python -m unittest tests.test_x` the test modules are imported as members of this package, so the
# bare name resolves only because it is registered here; a direct script run finds the file on
# sys.path itself, and that bare module is what imports THIS package (its bootstrap, 2026-09-14), so
# the setdefault keeps it and the package-relative import below is a second object. Nothing rides on
# which one a test holds: load_source shares through sys.modules, not through the module.
from . import romp_load as _romp_load  # noqa: E402
sys.modules.setdefault("romp_load", _romp_load)
# `from fs_clock import move_ctime` in a test module (tests/fs_clock.py, 2026-09-12): the shared force-until-moved ctime
# helper the memo tests and the thread mail-off test import, registered under its bare name like romp_load above
from . import fs_clock as _fs_clock  # noqa: E402
sys.modules.setdefault("fs_clock", _fs_clock)
# `from git_fixture import git, init_repo` (tests/git_fixture.py, T299): the throwaway-repo fixtures' one git
# runner, registered the same way for the same reason.
from . import git_fixture as _git_fixture  # noqa: E402
sys.modules.setdefault("git_fixture", _git_fixture)
