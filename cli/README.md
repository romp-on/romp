# cli/ — terminal tools

Python implementations of the terminal-facing romp commands. Run them via
their `bin/` symlinks (`romp version`, `romp update`, `romp keyswap`)
— see `bin/README.md` for the command surface.

| File | Command | What it is |
|---|---|---|
| `version.py` | `romp version` | Version report across the moving parts (working tree vs running kernel vs built bundles). |
| `update.py` | `romp update [host]` | Pushes this machine's committed romp to attached remote kernels over ssh and restarts them. |
| `keyswap.py` | `romp keyswap [<name>]` | Switches the API key source without a kernel restart: selects `ROMP_API_KEY_REF` or legacy `ANTHROPIC_API_KEY` from a sibling profile, removing the competing assignment. Listing and selection never resolve secrets; `--cycle`/`--cycle-all` asks the kernel to reconnect running sessions. Prints source identities, never key values. |
| `idle_dots.py` | (hook-fired) | tmux backend only: heals stranded `working` state by inspecting tmux panes. Fired from `hooks/tmux-status.sh`. |
