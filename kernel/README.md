# kernel/ — the always-on core

The Python backend: one process (`kernel.py`) that reads every session's Claude
Code transcript, builds the event tree, runs the judges, drives the session
backends, and serves the four panes over HTTP + WebSocket on `127.0.0.1:29855`.
Spawned by `bin/romp-serve` (the `bin/romp-kernel` symlink points here); see
`docs/architecture.md` for the data-flow picture.

Layered bottom-up:

| File | Layer | What it is |
|---|---|---|
| `event_model.py` | 1 | Transcript JSONL → event tree (atoms / segments / turns). The schema is pinned in `docs/event-model.md`. |
| `judge.py` | 2 | The judge engine + every judge prompt (captioner, archiver, planner, …). Writes the durable records (captions, archive, goal tree). `docs/judges.md`. |
| `kernel.py` | 3 | The read side: selects and displays what the layers below computed — HTTP + WebSocket server, pane payload builders, session lifecycle, nudges. `docs/read-side.md`. |

Session control (how romp drives Claude Code) sits behind one seam:

| File | What it is |
|---|---|
| `session_backend.py` | The `SessionBackend` ABC — the single interface both backends implement (guarded by `tests/test_session_api.py`). |
| `sdk_backend.py` | The Agent SDK backend (current default): an exact, event-based control channel. `docs/sdk-backend.md`. The tmux backend lives inside `kernel.py` (`TmuxBackend`). |
| `askparse.py` | tmux backend only: recovers the AskUserQuestion picker from a captured pane (SDK sessions get it natively). |

Shared lookup tables: `colormap.py` (recency tints, single source shared with
the web bundles) and `palette.py` (session-identity colors).

`keysource.py` selects the manager's live API key source: a
`ROMP_API_KEY_REF=op://vault/item/field` reference or a legacy
`ANTHROPIC_API_KEY`. Source inspection is separate from resolution so UI/status
reads do not fetch secrets. A selected reference is resolved with `op read
--no-newline` for each Claude session launch/reconnect, key-billed judge call,
and direct model-catalog refresh. Explicit cycle checks also resolve the key
to detect rotations; a reconnect resolves it again at launch. Resolved provider
keys are not cached or written to disk. Resolution failures fail closed.
`cli/keyswap.py` (`romp keyswap`) shares the path and parser to switch references or legacy keys without
resolving them. Removing a service-file source cannot restore a stale startup
key. See `docs/reference.md` for migration and service authentication setup.

Everything here is loaded by file path (`SourceFileLoader`), not installed as a
package — the repo runs straight from a git clone. Python tests live in
`tests/test_*.py`.
