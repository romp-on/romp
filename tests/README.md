# tests/ — every suite in one place

Every bug fix or feature change lands with a test (repo rule). Five suites:

- **`test_*.py`** (pytest) — the Python pipeline: event model, judges, kernel,
  backends, postal. They load the sources by file path through the stable
  `bin/` names with `from romp_load import load_source` (`tests/romp_load.py`,
  which reaches `kernel/loadsource.py`) and isolate state with `XDG_STATE_HOME`.
  The older `SourceFileLoader(...).load_module()` form is deprecated, with
  removal documented for Python 3.15: `tools/loadsource-sweep.py` rewrites a
  module still written that way (idempotent; `--check` reports without writing),
  and `test_state_isolation_order.py` refuses the call by file and line, naming
  that command. Both read the AST, so the idiom inside a string handed to a
  child process is a hand edit; so is the `sys.path` line a module needs before
  `from romp_load import load_source` when another test executes it by file
  path from outside this directory (`smoke_codex_live.py` carries one). Name
  every module `test_<stem>.py`: pytest also collects `<stem>_test.py`, but
  unittest's discovery (`test*.py`), the state-isolation check and the fixture
  scan in `test_postal_marker_form.py` take the `test_` prefix only, and
  `test_state_isolation_order.py` pins that.
  Golden transcript fixtures: `test_romp_events_golden.py` + `fixtures/`.
  Run: `python3 -m pytest tests/ -q` (~20s; a stalled run is a hang, not slow).
  The `_HAVE_SDK`-gated classes in `test_sdk_backend.py` (OptionsAssembly, the
  runner and can_use_tool bridge suites) SKIP unless `claude_agent_sdk` imports,
  and a skip reads as green — to execute them, put romp's SDK venv on the path:
  `PYTHONPATH=~/.local/state/romp/sdkvenv/lib/python3.12/site-packages python3 -m
  pytest tests/test_sdk_backend.py -q` (the venv `bin/romp-sdk-setup` creates;
  match the python version to it).
  Tests that mint user todos call `km._set_user_todos(True)` first: the
  feature switch is OFF by default and every read is gated, so a fixture that
  skips it sees empty surfaces and green assertions about nothing. They also
  clear `km._user_todos_cache` (and `km._UT_FLOOR_ARM`, the escalation
  floor's per-session arm record) in setUp and tearDown; `_StoreSandbox` in
  `test_user_todos.py` is the template. Any kernel helper that writes the
  user-todo store carries `user_todo` in its name — `NoJudgeWritesTheStore`
  derives its token list from that pattern, and `NoInferenceWritesTheStore`
  resolves every call to one against an allow-list of the defs that may act.
- **`*.bats`** — the shell surfaces: `bin/romp`, the launch chain, hooks,
  postal CLI. Keep them GNU/BSD-portable (CI runs bats on ubuntu).
  Run: `bats tests/*.bats`.
  Any test whose subject shells out to tmux must isolate the tmux socket
  directory: `load tmux-private`, `tmux_private_socket_dir "$TEST_DIR"` in
  setup (it exports `TMUX_TMPDIR` under the test dir and creates it first;
  tmux 3.4 silently uses the machine's default socket directory when
  `TMUX_TMPDIR` names a missing one), and `tmux_private_kill && rm -rf
  "$TEST_DIR"` as the last line of teardown (the kill fails when the
  directory is already gone, since a server started under it has then
  leaked; it has to be teardown's final status, because bats swallows a
  failing command mid-teardown). A tmux mock on PATH
  covers only the tests that install one: on 2026-09-06 a full bats run
  ran `romp-manager-ensure.bats` while the machine's default tmux server
  was down, the real manager it starts ran `tmux start-server` on the
  default socket, and for the rest of the day the machine's tmux server was
  the test's, carrying the run's environment inside the service's cgroup.
  The same helper call floors `ROMP_CLI_SCOPE=0`: under `ROMP_SUPERVISED`
  (set by the service's unit, and inherited by a tool shell under a
  self-hosted install) `bin/romp-manager` starts that server through
  `systemd-run --scope` and the kernel spawns session CLIs the same way, so
  a suite that starts the real manager would otherwise leave a transient
  scope on the developer's user manager. Every suite that isolates tmux
  inherits the floor; `romp-manager-tmux-scope.bats` turns the switch back
  on only behind a fake `systemd-run` first on PATH. pytest's floor is
  `conftest.py`; `test_cli_scope_floor.py` pins both halves of it on the
  source, since a test that reads the value cannot tell the floor from
  `test_cli_scope.py`'s own import-time set.
  `conftest.py` also unsets the four `ROMP_CLI_SCOPE_*` limit variables
  (`ROMP_CLI_SCOPE_MEMORY_MAX` and the others): the kernel hands them to every
  session's CLI and a tool shell inherits them, so a suite run from a session on
  a self-hosted install would otherwise see them at every backend construction
  and in every exact argv pin. `conftest.py` and `__init__.py` set
  `ROMP_MANAGER_PORT`, `ROMP_KERNEL_PORT` and `ROMP_SERVE_PORT` to a dead port
  (never unset: to every reader an absent variable means the live default), so
  no test dials a live manager or kernel through an inherited value.
  Any suite that starts the real `bin/romp-manager` also gives it a state
  root of its own before its first `@test` (`unset ROMP_STATE_DIR` plus
  `export XDG_STATE_HOME="$TEST_DIR/state"`, or an exported
  `ROMP_STATE_DIR`), since the manager boots from its state root's
  `kernels.json` and reads the serve token there; `bats-state-isolation.bats`
  is the ratchet.
  Any test whose subject binds a loopback port picks it with `load
  free-port` + `free_port VAR...`, never a literal: a literal shared by two
  files collided within one run (`romp-manager-ensure.bats` once used
  `romp-manager-origin.bats`'s control port), and any literal collides when
  two checkouts run bats at once on one machine. The helper picks below
  the ephemeral range, so a transient source port cannot hold the pick.
- **node suites** — live beside their sources in `ui/webview/*.test.ts` and
  `vscode-extension/src/*.test.ts`, run with `npm test` from
  `vscode-extension/`. Many pin lines of `kernel/kernel.py` as strings — run
  BOTH this and pytest on every kernel change.
- **`manager-*.test.js`** — the node supervisor (`bin/romp-manager`): restart
  gating, the kernel registry, and the drain-poll handshake. Run:
  `node --test tests/manager-*.test.js`.
- **`ui-bench.test.mjs`** — the dashboard pane bench (`tools/ui-bench.mjs`):
  the classifier, synthesizer, temp-path guard, recording client, front
  server, Handler-subprocess isolation, in-page instrument, profile fold, and
  real headless replays of synthetic feed and timeline streams.
  Run `node --test tests/ui-bench.test.mjs` from the repo root after
  `cd vscode-extension && npm ci && npm run build`. The browser tests skip,
  saying why, when no Chromium, `python3` or dist is present;
  `ROMP_UI_BENCH_REQUIRE=1` (CI) turns that skip into a failure, and
  `ROMP_UI_BENCH_TIMING=1` (a quiet machine, never CI) also asserts the timing
  relations the replays otherwise report as diagnostics.

**Temp files and git are hermetic, suite-wide.** Two mechanisms, one per half.
`tests/__init__.py` wraps `tempfile.mkdtemp` so every directory the test process
mints is recorded and removed when the run ends (under pytest at session end,
under `python -m unittest` at exit): the in-process half, covering the 300-odd
module preambles and the per-test `mkdtemp()` calls nobody cleans up.
`tests/conftest.py` covers what that hook cannot see — directories made by
child processes (kernels, git, a shell's `mktemp -d`), `mkstemp` files,
`os.mkdir` paths — by pointing the process temp dir (`tempfile.tempdir` and
`TMPDIR`, so every child inherits it) at one private `romp-tests-*` root under
the system temp dir and removing the root when the run ends (before both, a
full run left ~5,600 entries in `/tmp` and over a million had piled up). A run
that dies before that removal (pytest-timeout's `os._exit`, a killed shell)
leaves the root standing, so conftest also writes an owner marker,
`romp-tests-owner.json` naming the run's pid, into the root at mint time. The
kernel's boot reconcile (`sweep_dead_test_roots` in `kernel/sdk_backend.py`)
removes `romp-tests-*` roots under the system temp dir whose marker names a
dead pid, renaming each to `<name>.sweeping` before deleting it so a partial
delete leaves a tombstone the next boot finishes. A root without a marker (a
foreign directory, a pre-marker root) is never touched by the sweep. Still
clean up what you create — `with tempfile.TemporaryDirectory()`,
`self.addCleanup(shutil.rmtree, ...)`, a `tearDownClass` for a class-level
fixture — so a fixture is gone when its test is, not at exit; bats suites use
`mktemp -d` in `setup` and `rm -rf` it in `teardown`, and stand in for any
subject that detaches work (bin/romp's resume picker-check, reached through
`ROMP_POSTAL_BIN`, re-created four to six test dirs per run by minting a
serve-token after the teardown). Never give a tempfile call a literal
directory as its `dir` — by keyword or position, composed (`f"/tmp/{x}"`,
`os.path.join("/tmp", x)`) or through a name bound to one — and never point
`mktemp` (`-p`, `--tmpdir`, a `TMPDIR=` prefix) at a path under `/tmp`: that
bypasses the redirect, and the hygiene test reads every test file for those
shapes. The one test that must leave the root — an AF_UNIX socket whose path
would not fit `sun_path` under a nested root — falls back to
`ROMP_TESTS_SYSTEM_TMPDIR`, the system temp dir conftest recorded once per run
before redirecting (an xdist worker inherits the controller's record), and
removes what it made. A root that cannot be removed at run end (a child
still writing under it, a 000-mode directory a test left behind) is named on
stderr: `[tests] not removed at run end: <path>`, instead of the run ending
green over it. The same conftest gives git no global or system config
(`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`) and a synthetic identity through
`GIT_AUTHOR_*` / `GIT_COMMITTER_*`; bats suites that run git get the same from
`load git-hermetic` + `git_hermetic` in `setup`, with a global config file of
the floor's own in place of none. That floor also forbids background git work:
the five no-background keys of `tests/git_fixture.py` (below) ride the
environment as `GIT_CONFIG_COUNT` pairs, inherited by every git a test, a script
under test or a hook runs, and sit in the floor's global file, which receive-pack
in a bare fixture remote reads (a push over a local path starts it without the
pairs); so no detached `git maintenance` writes into a fixture repo while its
teardown removes it. A fixture must not depend on the developer's git
configuration (CI has none), and the env identity outranks
`git config user.*` and `-c user.*` — a test that must pin a particular author
exports its own `GIT_AUTHOR_*` after the floor. `tests/test_tempdir_hygiene.py`
and `tests/git-hermetic.bats` pin all of it.

**A fixture that builds a throwaway git repository runs git through
`tests/git_fixture.py`** (`from git_fixture import git, init_repo,
forbid_background`, registered under the bare name like `romp_load`). Every
command it runs carries `-c` flags that forbid background work
(`maintenance.auto`, `maintenance.autoDetach`, `gc.auto`, `gc.autoDetach`,
`core.fsmonitor`), `init_repo` writes the same keys into the repo's own config,
and `forbid_background` does so for a clone or worktree the kernel will run git
against: `git commit`, fetch and merge spawn `git maintenance run --auto`,
which on recent git detaches from its parent and can still be writing into
`.git` while the fixture's `TemporaryDirectory` removes the repo (the CI flake
`Directory not empty: '.git'` from `rmtree`, 2026-09-10). A file keeps its own
thin runner (its identity, timeout and return shape) and delegates the body;
read-only git against the real checkout stays a plain `subprocess.run`.
`tests/test_git_fixture.py` pins the runner with git's own trace: a commit
through it, and a plain fetch in a `forbid_background` clone, spawn no
maintenance or gc child.

**A served-page class copies the built `vscode-extension/dist/` with
`tests.dist_copy.copy_dist`, never `shutil.copytree`.** Under `pytest -n` a
sibling class's build renames or removes its staging files
(`.<name>.tmp-<pid>-<n>`, `stagingPath` in `esbuild.js`) between the listing
and the copy, and a plain copytree raises `shutil.Error` before the class's
first test; `copy_dist` skips that shape. `tests/test_dist_copy_staging.py`
pins the copy and refuses a raw copytree of dist in any test module.

**No test report shows a process-environment value or a credential-shaped
token.** An assertion whose container is an environment mapping prints the
whole mapping when it fails (`assertNotIn("X", os.environ)` renders every
variable), and on a developer's machine that mapping can hold a live key. The
fix is to test membership and name the key; the report hook in
`tests/conftest.py` is the net for an assertion still written the other way.
It redacts two things from every report's text and captured-output sections,
whatever the outcome (so `-rA`/`-rP` output for passed tests too), and from
collection reports: a value present in the process environment that is 16
characters or longer becomes `[REDACTED-ENV-VALUE]`, whole, per
whitespace-separated chunk, and per piece left beside a cut pytest or
unittest made (`'<head>...<tail>'`, `[N chars]`) when that piece is a
substring of the value (values are noted at the moment they are written
into `os.environ`, so one set inside `mock.patch.dict` and gone before the
assertion is caught too; exempt, never when the name is credential-shaped,
are path-valued names such as `PWD`, `HOME`, `TMPDIR` and the interpreter
paths GitHub Actions exports, the names whose values are public, the ones
GitHub Actions describes a run in (`GITHUB_REF`, `GITHUB_REPOSITORY`,
`GITHUB_ACTOR` and the rest) and the conftest's own synthetic git identity,
the `XDG_*` and `PYTEST_*` families, and any value that is an absolute path
this machine has), and a credential-shaped token becomes
`[REDACTED-CREDENTIAL]` wherever it came from, by the patterns in
`tests/credential_patterns.py` (public key prefixes, a JWT by its shape, a
long token where a value sits, pytest's own renderings of a failed comparison
included; a named git sha and a dated Anthropic model id are left alone). A
report the hook changes is rebuilt from the scrubbed text with
its crash location kept, so the short test summary still ends in the
assertion message; that report loses pytest's colour and source highlighting.
One it leaves alone keeps pytest's own rendering.
`tests/test_env_value_redaction.py` pins the rule, the write-time capture,
the patterns, the scrub's cost and the hook end to end.

**A lab kernel's environment is built from a list of names, and the file a
relaunch reads from carries a shorter list.** Every module that boots a hermetic
kernel (`bin/romp-kernel` under a lab's own `XDG_STATE_HOME`,
`CLAUDE_CONFIG_DIR` and `ROMP_DIST_DIR`, at a free port with a synthetic serve
token) builds its environment with `kernel_env` in `tests/test_ship_reship.py`,
never from a copy of the runner's. A run from a shell on a machine running romp
carries the live kernel's exports, and a lab kernel that inherited them exited
when the live manager restarted (`ROMP_MANAGER_PID`, the kernel's parent-death
watchdog), bound where the live kernel serves (`ROMP_SERVE_HOST`) and dialled
the machine's postal bus, or started one that nothing stops. From the runner
`kernel_env` takes `PATH`, `HOME`, the `XDG_*` names and, of the floor
`tests/conftest.py` sets for the run's children, `TMPDIR`, `TMUX_TMPDIR`,
`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`, `ROMP_SERVICE_ENV_FILE`,
`ROMP_SERVICE_ENV`, `ROMP_CLAUDE_BIN` and `ROMP_CLI_SCOPE`; over those go the
lab's roots and seams, any seam the lab adds by keyword, and a postal bus of its
own that is never started (`ROMP_POSTAL_PORT` at a free port,
`ROMP_POSTAL_PEERS=0`, `ROMP_POSTAL_CLIENT_ONLY=1`). The served labs whose
driver kills and relaunches the kernel (`test_ship_reship.py`,
`test_dashboard_reload_served.py`) write the relaunch's command, environment and
log to the lab's `cfg.json` through `relaunch_cfg`, and the environment in that
file is narrowed once more by `relaunch_env`: the `ROMP_*` and `XDG_*` names,
`CLAUDE_CONFIG_DIR`, `PATH`, `HOME`, `TMPDIR`, `TMUX_TMPDIR`,
`GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM`, less the `ROMP_TESTS_*` names,
which conftest exports for the run's own tests (such as
`ROMP_TESTS_SYSTEM_TMPDIR` above) and no kernel reads. Nothing else a lab put in
its kernel's environment reaches the file; each served lab plants a probe name
in that environment and checks the written file for its absence. To give a lab
kernel another name of the runner's, add the name to the list with its reason
beside it. `LabKernelEnv` and `RelaunchEnv` in `tests/test_ship_reship.py` pin
both functions; the served legs check the file itself.

`fixtures/` must stay SYNTHETIC: invented prompts, placeholder UUIDs, hostname
`TESTHOST` — never real session data.
