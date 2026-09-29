# hooks/ — Claude Code hooks

Shell hooks that romp registers with Claude Code (see `install.sh`). They are
the event-driven glue between a running session and the rest of romp — each
fires on a Claude Code lifecycle event for romp sessions, which a hook that acts
only for them identifies by `ROMP_SID` (the id the kernel's SDK backend gives
every session it launches); none of them poll. None records a session's state:
the SDK backend writes the `states/<sid>.jsonl` rows itself.

| Hook | Event | What it does |
|---|---|---|
| `romp-wake.sh` | turn end / prompt / compaction end | Wakes the kernel — the judges and the parked-op drain — when an event creates new work for them (event-based over time heuristics, by design). |
| `romp-postal-ensure.sh` | SessionStart | Makes sure the postal bus is running (async, singleton). |
| `romp-postal-context.sh` | SessionStart | Gives a romp session whose mail is on a compact pointer to the postal skill (not the full skill body). |
| `romp-postal-drain.sh` | Stop | Delivers queued peer mail at turn end, so mail never interleaves with a working turn. |
| `romp-postal-revive.sh` | SessionStart | On revival with unread mail (a parked handoff), makes the session act on that mail. |

Disable the postal hooks with `~/.claude/romp-postal-off`. Shell tests:
`tests/*.bats` (`romp-wake-hook.bats`, `romp-postal-context.bats`, …) — keep
them GNU/BSD-portable, CI runs on Linux.
