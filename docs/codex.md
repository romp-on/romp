# Codex sessions

romp runs **OpenAI Codex** agents alongside — or instead of — Claude Code
sessions: same board, same cards, chat, timeline, and postal bus. A Codex
session is just another kind of session: pick **Codex** in the new-session
dialog's Backend toggle (or set it as the gear's Default backend). The
`romp` command and the `romp` command are the same program — romp is
built on romp, and either name works everywhere below.

Under the hood, romp drives Codex through the official `codex app-server`
protocol and materializes each thread as a transcript romp's read side already
understands — design in `plans/codex-backend.md`.

## Setup

Codex sessions need, once per machine:

1. **The Codex SDK:**

        romp-codex-setup

    This provisions a dedicated, pinned venv under romp's state dir (it never
    touches your system Python) and bundles the Codex binary.
    Sessions use that SDK-bundled runtime even if another `codex` is on PATH.
    Your existing CLI remains available for `codex login`.

2. **A Codex login:**

        codex login          # or: codex login --device-auth on a headless box

    Codex sessions bill the ChatGPT plan (or API key) this login carries.

If either is missing, creating a Codex session tells you exactly what to run —
romp never silently substitutes a different backend.

By default romp's *own* intelligence — the judges that caption work, build the
cards, and route your attention — runs on Claude, so the machine also wants a
Claude Code login. On a Codex-only machine, switch the judges to Codex instead
(next section) and no Claude login is needed at all.

## Running the judges on Codex

    romp engine codex      # back: romp engine claude — current posture: romp engine status

One switch, two knobs: the judges move to Codex, and new sessions (`romp new`,
the kernel API) default to the Codex backend — the dashboard's + dialog keeps
its own per-create toggle. No restart needed: judges read the setting on their
next call, and running sessions are never touched. Every judge becomes a
one-shot `codex exec` call — ephemeral (no session files), using romp's custom
`romp_judge` permission profile, and billing the machine's Codex login. The
profile grants only Codex's minimal runtime files plus read access to the fresh
empty scratch workspace supplied by `-C`; it denies writes, network access, and
host-wide reads. Verified live: the real caption and gist
judges answer correctly in ~5–6s per call.

Honest caveats while this is new:

- **Quality**: the judge prompts were tuned on Claude models; on Codex they
  validate well but have less mileage. If cards read oddly, switch back —
  it's one command.
- **Cost/quota**: judges make many small calls (every turn gets captioned);
  on a ChatGPT plan they draw from the same usage pool as your Codex sessions.
- **Accounting**: `codex exec` doesn't report token counts, so the analytics
  show judge call counts and durations but not token costs; and the
  Claude-account rate-limit gate doesn't apply (Codex limits surface per call
  instead).
- Model picks in the gear's judge-model settings are Claude aliases; on the
  codex engine they're ignored (ChatGPT-plan accounts only allow the account's
  default model — a `gpt-*` value in `~/.local/state/romp/judge-model` is
  honored where the plan permits).

## Sandboxing

Codex runs its commands inside its own Linux sandbox (bubblewrap). Every
thread and turn selects romp's custom `romp_workspace` permission profile
and supplies exactly that session's working directory in
`runtimeWorkspaceRoots`. The profile permits only Codex's minimal runtime
files plus that workspace, so other user files on the host are not readable.
Network remains enabled so git and web work keep working.

There is an important pinned-runtime limitation: Codex 0.144.4 does not enforce
narrower child rules against arbitrary processes inside a custom writable root.
A shell command can therefore still modify `.git`, `.agents`, and `.codex`.
The built-in `:workspace` profile enforces the metadata masks, but also grants
read access to the whole host; romp currently chooses user-file
confidentiality and documents this
remaining metadata-integrity gap rather than silently restoring host-wide
reads. Two host notes:

- The sandbox needs **unprivileged user namespaces**. Some images (notably
  newer GCP Ubuntu) restrict them; every command then fails visibly with
  `bwrap: … Permission denied`. To enable:

        sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0

    (persist it in `/etc/sysctl.d/` to survive reboots).

- There is no permission-prompt flow yet (approvals are pinned off; the
  sandbox is the guardrail). Codex approval prompts surfacing as romp's
  needs-you cards is planned.

## What works, what's coming

Working today: lanes and status, task cards and judging, full chat (prompts,
replies, thinking, commands, file diffs, web searches), steering a running
turn, interrupts, model and reasoning-effort switches, resume after restarts,
and postal delivery into Codex sessions.

Not yet (tracked in `plans/codex-backend.md`): approval prompts as needs-you
cards, Codex plan items on the card checklist, subagent lanes, rate-limit
gating, and MCP server management from the dashboard (Codex sessions read MCP
servers from `~/.codex/config.toml`).

## Installing romp

Install romp itself as described in [install.md](install.md). Then, in a new
terminal, the two setup steps above (`romp-codex-setup`, `codex login`) — and
`romp engine codex` if the machine should run Codex-only. `romp` opens the
dashboard; a machine that never runs these steps is unaffected and defaults to
Claude Code.

