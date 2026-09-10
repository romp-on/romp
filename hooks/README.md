# hooks/ — Claude Code hooks

Shell hooks that romp registers with Claude Code (see `install.sh`). They are
the event-driven glue between a running session and the rest of romp — each
fires on a Claude Code lifecycle event; none of them poll.

| Hook | Event | What it does |
|---|---|---|
| `romp-wake.sh` | turn end / prompt / compaction end | Wakes the kernel — the judges and the parked-op drain — when an event creates new work for them (event-based over time heuristics, by design). |
| `romp-summarize.sh` | turn events | The announcer: writes a short live phrase to the `@claude-summary` tmux var that status lines and dashboards render. Display-only. |
| `tmux-status.sh` | status events | The passive status pipe serving both backends: membership, `working`/idle state, `@romp-session-id` re-anchoring after `/clear` forks. Never spawns or toasts. |
| `romp-postal-ensure.sh` | SessionStart | Makes sure the postal bus is running (async, singleton). |
| `romp-postal-context.sh` | SessionStart | Gives a romp session a compact pointer to the postal skill (not the full skill body). |
| `romp-postal-drain.sh` | Stop | Delivers queued peer mail at turn end, so mail never interleaves with a working turn. |
| `romp-postal-revive.sh` | SessionStart | On revival with unread mail (a parked handoff), makes the session act on that mail. |
| `romp-usertodo-context.sh` | SessionStart | On resume/compact, re-hands a session its open user todos as passive context, so it withdraws the moot ones after its working memory is wiped. Silent while the user-todos switch is off (the gear's User todos checkbox; off by default). |

Disable the postal hooks with `~/.claude/romp-postal-off`. Shell tests:
`tests/*.bats` (`romp-wake-hook.bats`, `tmux-status-hook.bats`,
`romp-postal-context.bats`, …) — keep them GNU/BSD-portable, CI runs on Linux.
