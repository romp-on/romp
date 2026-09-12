# bin/ — the command surface

`bin/` is the stable **entry-point surface**: every runnable romp command lives
here (put it on `$PATH`: `export PATH="$PATH:<repo>/bin"`). The Python
*implementations* live in the logical source folders — `kernel/`, `postal/`,
`cli/` — and the corresponding bin entries are **symlinks** to them, so external
consumers (launchd/systemd, hooks, the MCP config, remote kernels)
keep stable paths while the code stays navigable by component. `ls -l bin` is
the live map of that indirection.

Only the shell/Node launch glue is a real file here — it *is* the command, with
no separate implementation to point at.

## Real files (launch chain + shell commands)

`romp-service` (login agent) → `romp-node-launch` → `romp-manager` →
`romp-serve` → `kernel/kernel.py`.

| File | Lang | What it is |
|---|---|---|
| `romp` | Bash | The launcher/dispatcher: `romp new`, `romp send`, `romp end`, `romp up` / `down` / `refresh` / `status`, `romp update`, `romp mail`, `romp version` and the rest of the command table in `docs/reference.md`. A retired flag (`-d`, `-f`, `-j`, `--on`, `--status`, …) exits 2 naming the command that replaced it. |
| `romp-service` | Bash | Installs/removes the launchd (macOS) / systemd-user (Linux) login agent, and stops/starts it (`stop`/`start`: the supervisor halves of `romp down` / `romp up`; `stop` exits 3 when none is installed and 4 when one is installed but not running). Run by `install.sh`. |
| `romp-node-launch` | POSIX sh | Runs the manager under a romp-owned copy of node (`romp-node`) so macOS TCC permissions can be granted to romp alone. |
| `romp-manager` | Node | The kernel **supervisor**: spawns kernels via `romp-serve`, respawns on crash, `up/ensure/restart-all/status/down`. Reached via `romp up` / `romp refresh` / `romp status`, and by `romp down`, which probes it after the service stop and stops one still answering through `down`. `ensure` holds while a `romp down` marker exists; `up` clears it. Before every SIGTERM it sends, it appends a `manager-sigterm` row (with a `trigger`: `restart`, `restart-all`, `refresh`, `cli-down`, `stop`) to the kernel's `restart-audit.jsonl`; that row says the manager was the messenger, and the kernel's exit reader uses its trigger to name what asked. A kernel still running five seconds after its SIGTERM gets SIGKILL, on a stop as on a restart. A kernel exit it did not ask for is logged as `exited without a restart request (signal or crash)`. |
| `romp-serve` | Bash | The manager→kernel seam: maps the manager's spawn contract onto the kernel's env, picks the python (`ROMP_PYTHON`, refused with one line if it is not an executable interpreter; else the interpreter the SDK venv's `pyvenv.cfg` records, checked to run and to be the recorded X.Y and build (free-threaded `t` or not); else another python of that same minor and build on PATH or in `~/.local/bin`, which the venv still matches; else the newest `pythonX.Y` found, which needs a `romp-sdk-setup` rebuild), then `exec`s the kernel (PID preserved for the supervisor; the kernel self-builds stale UI bundles). |
| `romp-cli-scope` | POSIX sh | Linux + systemd only: the kernel spawns each session's `claude` CLI through it, and it `exec`s `systemd-run --user --scope` in place, so the CLI (and every tool shell, `setsid` child and detached server it later starts) runs in a transient scope of its own instead of the manager service's cgroup, which a service restart empties. PID, parent and argv preserved. `ROMP_CLI_SCOPE=0`, or no `ROMP_SID` (a probe, not a session), runs the CLI directly. So does a failed pre-flight scope, with one stderr line saying why, which the kernel logs as a problem at once. Opt-in per-session limits: `ROMP_CLI_SCOPE_MEMORY_MAX` / `_MEMORY_HIGH` / `_MEMORY_SWAP_MAX` become `-p MemoryMax=` / `MemoryHigh=` / `MemorySwapMax=` on the scope (with `OOMPolicy=continue`, so one OOM kill takes one process, not the scope), and `ROMP_CLI_SCOPE_OOM_SCORE_ADJ` is written to the wrapper's own `oom_score_adj` before the exec; a value it refuses, a property systemd rejects, or an adjustment that parses but Linux will not let it write, is skipped with one `romp-cli-scope: ignored:` stderr line each, logged by the kernel as a problem, and the CLI still starts in its scope; the kernel runs the same steps once at its start, so a refusal by the machine is reported once instead of on every launch. |
| `romp-sdk-setup` | Bash | Provisions the Agent SDK venv for the SDK backend, with the same python pick as `romp-serve` so the two agree: a re-run keeps the venv's interpreter and upgrades the SDK, moves to another python of the same minor and build if the recorded one is gone, and rebuilds for a different interpreter tag only when `ROMP_PYTHON` names one or no python of the venv's minor and build is left, saying from what to what. What the venv was built for is read from its record (`pyvenv.cfg`'s version plus its `lib/python3.X{t}` directory), never from its `bin/python`, a symlink that follows a repointed base interpreter. A `ROMP_PYTHON` that is not an executable interpreter is refused as such, not called a too-old python. Run by `install.sh`. |
| `romp-codex-setup` | Bash | Provisions the Codex backend's dedicated venv (`codexvenv`, holding the pinned `openai-codex` SDK) and the pinned Codex runtime under the state directory, with the same python pick as `romp-serve` and `romp-sdk-setup`, read from the SDK venv's record so the two venvs and the kernel agree, and the same rebuild for a different interpreter tag, saying from what to what. Links the bundled `codex` CLI into `~/.local/bin` when no `codex` is on PATH, for `codex login`. Not run by `install.sh`: run it yourself, once per machine, before Codex sessions (`docs/codex.md`). |
| `romp-login-setup` | Bash | One command for a SECOND Claude login (T393): a scratch sign-in mints a `claude setup-token`, masked on screen and stored in 1Password through a template file, then `romp login add` registers it; `--check` proves the stored token is what Claude Code accepts. |

## Symlinks → `kernel/` (the always-on core)

| Command | Source | What it is |
|---|---|---|
| `romp-kernel` | `kernel/kernel.py` | **The** kernel: parses transcripts into the event tree, runs the judges, serves chat/feed/fleet/timeline over HTTP+WebSocket on `127.0.0.1:29855`. Spawned by `romp-serve`. |
| `romp-event-model` | `kernel/event_model.py` | Layer 1: transcript → event tree (atoms/segments/turns). Loaded by the kernel and the judges. |
| `romp-judge` | `kernel/judge.py` | Layer 2: the judge engine + all judge prompts (captioner, archiver, planner, …). `docs/judges.md`. |
| `romp_sdk_backend.py` | `kernel/sdk_backend.py` | The **SDK session backend**, which runs every Claude Code session: drives them via the Claude Agent SDK. |
| _(no bin entry)_ | `kernel/credentials.py` | romp's whole contact with API credentials since 2026-09-08: reads Claude Code's `apiKeyHelper` from its settings files in the CLI's precedence, runs it in-process for the kernel's two API calls (model catalog, fast-mode probe) with an in-memory TTL memo, and stops the kernel at boot when `service.env` or the environment still carries a retired provider line. romp holds no key. |
| `romp_session_backend.py` | `kernel/session_backend.py` | The `SessionBackend` ABC — the one seam both backends (Claude Code, Codex) implement. |
| `romp_colormap.py` | `kernel/colormap.py` | The recency colormaps, single source of truth shared with the web bundles. |
| `romp_palette.py` | `kernel/palette.py` | The session-identity color palettes. |

## Symlinks → `postal/`

| Command | Source | What it is |
|---|---|---|
| `romp-postal-service` | `postal/postal_service.py` | Inter-session mail: MCP server + CLI (`romp mail`). `romp-postal` is a symlink alias. |

## Symlinks → `cli/` (terminal tools)

| Command | Source | What it is |
|---|---|---|
| `romp-update` | `cli/update.py` | Pushes this machine's committed romp to attached remote kernels and restarts them (`romp update [host]`). |
| `romp-version` | `cli/version.py` | Version report across the moving parts (`romp version`). |
| `romp-spend-rebuild` | `cli/spend_rebuild.py` | Recounts the token columns of the spend ledger (`spend.json`) from the transcripts' per-call usage; dollars and turn counts untouched. Dry run by default, `--apply` writes with a backup (`romp spend-rebuild`). |
