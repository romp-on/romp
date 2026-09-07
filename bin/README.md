# bin/ — the command surface

`bin/` is the stable **entry-point surface**: every runnable romp command lives
here (put it on `$PATH`: `export PATH="$PATH:<repo>/bin"`). The Python
*implementations* live in the logical source folders — `kernel/`, `postal/`,
`cli/` — and the corresponding bin entries are **symlinks** to them, so external
consumers (launchd/systemd, tmux glue, hooks, the MCP config, remote kernels)
keep stable paths while the code stays navigable by component. `ls -l bin` is
the live map of that indirection.

Only the shell/Node launch glue is a real file here — it *is* the command, with
no separate implementation to point at.

## Real files (launch chain + shell commands)

`romp-service` (login agent) → `romp-node-launch` → `romp-manager` →
`romp-serve` → `kernel/kernel.py`.

| File | Lang | What it is |
|---|---|---|
| `romp` | Bash | The launcher/dispatcher: start/resume/attach sessions, `-d`/`-f`/`-j` terminal views, `--on/--refresh/--status`, `--mail`, `update`, `--version`. Also provisions the tmux server glue when using the tmux backend. |
| `romp-service` | Bash | Installs/removes the launchd (macOS) / systemd-user (Linux) login agent. Run by `install.sh`. |
| `romp-node-launch` | POSIX sh | Runs the manager under a romp-owned copy of node (`romp-node`) so macOS TCC permissions can be granted to romp alone. |
| `romp-manager` | Node | The kernel **supervisor**: spawns kernels via `romp-serve`, respawns on crash, `up/ensure/restart-all/status/down`. Reached via `romp up` / `romp refresh` / `romp status`. |
| `romp-serve` | Bash | The manager→kernel seam: maps the manager's spawn contract onto the kernel's env, picks the python, then `exec`s the kernel (PID preserved for the supervisor; the kernel self-builds stale UI bundles). |
| `romp-cli-scope` | POSIX sh | Linux + systemd only: the kernel spawns each session's `claude` CLI through it, and it `exec`s `systemd-run --user --scope` in place, so the CLI (and every tool shell, `setsid` child and tmux server it later starts) runs in a transient scope of its own instead of the manager service's cgroup, which a service restart empties. PID, parent and argv preserved. `ROMP_CLI_SCOPE=0`, or no `ROMP_SID` (a probe, not a session), runs the CLI directly. So does a failed pre-flight scope, with one stderr line saying why, which the kernel logs as a problem at once. Opt-in per-session limits: `ROMP_CLI_SCOPE_MEMORY_MAX` / `_MEMORY_HIGH` / `_MEMORY_SWAP_MAX` become `-p MemoryMax=` / `MemoryHigh=` / `MemorySwapMax=` on the scope (with `OOMPolicy=continue`, so one OOM kill takes one process, not the scope), and `ROMP_CLI_SCOPE_OOM_SCORE_ADJ` is written to the wrapper's own `oom_score_adj` before the exec; a value it refuses, a property systemd rejects, or an adjustment that parses but Linux will not let it write, is skipped with one `romp-cli-scope: ignored:` stderr line each, logged by the kernel as a problem, and the CLI still starts in its scope; the kernel runs the same steps once at its start, so a refusal by the machine is reported once instead of on every launch. |
| `romp-sdk-setup` | Bash | Provisions the Agent SDK venv for the SDK backend. Run by `install.sh`. |

## Symlinks → `kernel/` (the always-on core)

| Command | Source | What it is |
|---|---|---|
| `romp-kernel` | `kernel/kernel.py` | **The** kernel: parses transcripts into the event tree, runs the judges, serves chat/feed/fleet/timeline over HTTP+WebSocket on `127.0.0.1:29855`. Spawned by `romp-serve`. |
| `romp-event-model` | `kernel/event_model.py` | Layer 1: transcript → event tree (atoms/segments/turns). Loaded by the kernel and the judges. |
| `romp-judge` | `kernel/judge.py` | Layer 2: the judge engine + all judge prompts (captioner, archiver, planner, …). `docs/judges.md`. |
| `romp-askparse` | `kernel/askparse.py` | Parses the AskUserQuestion picker out of a captured tmux pane (tmux backend only; SDK sessions get the picker natively). |
| `romp_sdk_backend.py` | `kernel/sdk_backend.py` | The **SDK session backend** (current default): drives sessions via the Claude Agent SDK. |
| _(no bin entry)_ | `kernel/keysource.py` | The live API key source: an optional `ROMP_API_KEY_REF` resolved by `op` at runtime, or a legacy key. Source inspection never fetches secrets. Shared by the kernel and `romp keyswap`. |
| `romp_session_backend.py` | `kernel/session_backend.py` | The `SessionBackend` ABC — the one seam both backends (SDK, tmux) implement. |
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
| `romp-keyswap` | `cli/keyswap.py` | Switches key sources — `ROMP_API_KEY_CMD` key commands, `ROMP_API_KEY_REF` 1Password references or legacy API keys — using sibling `service.env.<name>` profiles without restarting the manager. Listing/selection never fetch secrets; `--cycle`/`--cycle-all` reconnects running sessions onto the source (`romp keyswap`). |
| `romp-idle-dots` | `cli/idle_dots.py` | tmux backend only: heals stranded `working` state / fades idle tab dots by inspecting tmux panes. Fired from `hooks/tmux-status.sh`. |
| `romp-spend-rebuild` | `cli/spend_rebuild.py` | Recounts the token columns of the spend ledger (`spend.json`) from the transcripts' per-call usage; dollars and turn counts untouched. Dry run by default, `--apply` writes with a backup (`romp spend-rebuild`). |

## tmux backend only (real files)

Still wired, only meaningful for tmux sessions. If the tmux backend is ever
dropped, these (plus `romp-askparse`, `romp-idle-dots`, and the tmux glue in
`romp` + dotfiles `tmux.conf`) go with it.

| File | Lang | What it is |
|---|---|---|
| `romp-interrupt-reset` | Bash | tmux Ctrl-C/Esc bind: resets a stuck `working` state (Claude fires no interrupt hook). |
| `romp-mail-clear` | Bash | Clears the postal badge in the tmux status bar on session switch. |
