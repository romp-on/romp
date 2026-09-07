# tests/ — every suite in one place

Every bug fix or feature change lands with a test (repo rule). Four suites:

- **`test_*.py`** (pytest) — the Python pipeline: event model, judges, kernel,
  backends, postal. They load the sources by file path via `SourceFileLoader`
  (through the stable `bin/` names) and isolate state with `XDG_STATE_HOME`.
  Golden transcript fixtures: `test_romp_events_golden.py` + `fixtures/`.
  Run: `python3 -m pytest tests/ -q` (~20s; a stalled run is a hang, not slow).
  The `_HAVE_SDK`-gated classes in `test_sdk_backend.py` (OptionsAssembly, the
  runner and can_use_tool bridge suites) SKIP unless `claude_agent_sdk` imports,
  and a skip reads as green — to execute them, put romp's SDK venv on the path:
  `PYTHONPATH=~/.local/state/romp/sdkvenv/lib/python3.12/site-packages python3 -m
  pytest tests/test_sdk_backend.py -q` (the venv `bin/romp-sdk-setup` creates;
  match the python version to it).
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
  and in every exact argv pin.
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
full run left ~5,600 entries in `/tmp` and over a million had piled up). Still
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
`load git-hermetic` + `git_hermetic` in `setup`. A fixture must not depend on
the developer's git configuration (CI has none), and the env identity outranks
`git config user.*` and `-c user.*` — a test that must pin a particular author
exports its own `GIT_AUTHOR_*` after the floor. `tests/test_tempdir_hygiene.py`
and `tests/git-hermetic.bats` pin all of it.

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

`fixtures/` must stay SYNTHETIC: invented prompts, placeholder UUIDs, hostname
`TESTHOST` — never real session data.
