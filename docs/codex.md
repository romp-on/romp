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

    This installs Python SDK **0.144.4** in a dedicated venv and Codex runtime
    **0.153.3** under `codex-runtime/0.153.3/` in ROMP's state directory.
    The runtime comes from OpenAI's official release wheels, pinned by SHA-256,
    with its sandbox and code-mode helpers. Linux and macOS, x86-64 and ARM64,
    are supported. Neither system Python nor the SDK's own dependency is replaced.
    Sessions explicitly use this managed runtime even if another `codex` is on
    PATH. To point both the sessions and the judges at a specific binary, set
    `ROMP_CODEX_BIN`; unset, sessions take the managed runtime and the judges
    resolve `ROMP_CODEX_BIN`, then PATH, then the bundled binary. Your existing
    CLI remains available for `codex login`.

    Rerun setup to install the runtime when upgrading from an older ROMP version.
    Restart the ROMP kernel after setup if its Codex backend is already running.
    A missing or incomplete runtime produces a setup error; ROMP does not fall
    back to an older SDK runtime or a CLI from PATH.

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
host-wide reads. Every `ANTHROPIC_*` variable is removed from the child's
environment, so a key, proxy URL or custom header configured for Claude never
reaches Codex. Verified live: the real caption and gist
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

## Approval modes

Click the session's mode badge in the chat status line to choose **Sandboxed** or
**Auto** while the session is idle. The choice is saved per session and restored
when the kernel restarts. Existing and new sessions default to Sandboxed.

- **Sandboxed:** commands stay within the workspace permission profile;
  requests to execute outside it are denied.
- **Auto:** the sandbox applies only to commands the reviewer does not let
  out. Codex's own automatic reviewer evaluates escalation requests
  (`on-request` with `auto_review`); ROMP does not approve them itself. A
  command the reviewer approves runs OUTSIDE the sandbox, as the kernel's user,
  with that user's full access; the reviewer approves low- and medium-risk
  actions on its own judgement, so the read-confidentiality the Sandboxed mode
  gives is not promised here. A reviewer refusal remains a refusal. Requests
  routed back to ROMP for manual approval are declined with a warning because
  the current SDK cannot wait for a UI answer without blocking its shared
  reader.

Change modes between turns; an in-flight turn retains the mode it started with.
Auto may allow a reviewed command to run outside the sandbox, so it has a wider
execution scope than Sandboxed. It does not disable AppArmor or modify host
security settings. It also cannot repair a runtime that fails before a thread
starts, and reviewer availability is determined by Codex and your account.
If the API says a model needs a newer Codex, select a compatible model from the
model picker or update ROMP's pinned runtime; Auto cannot fix a model/runtime
version mismatch. The picker's list is the app-server's own. When it cannot be
had, the chat's menu shows the reason (the client not up yet, a failed list, no
live Codex session) instead of opening blank, and a menu opened on an empty list
asks for it again. Runtime 0.153.3 supports Astra, but the host must also support
sandbox creation as described below.

## Sandboxing

Codex runs its commands inside its own Linux sandbox (bubblewrap). Every
thread and turn selects romp's custom `romp_workspace` permission profile
and supplies exactly that session's working directory in
`runtimeWorkspaceRoots`. The profile permits only Codex's minimal runtime
files, the selected Codex executable and its packaged helpers, plus that
workspace. Runtime assets are read-only; unrelated user files and the containing
ROMP state directory are not exposed. Codex needs its own executable inside the
sandbox to apply seccomp before starting a command. Network remains enabled so
git and web work keep working.

The current profile grants write access to the entire workspace, including
`.git`, `.agents`, and `.codex`. Metadata protection needs narrower filesystem
rules; it is not provided by this profile. Two host notes:

- On Linux, install the distribution's **bubblewrap** package. Codex 0.153.3
  prefers a compatible system `bwrap` from PATH over its bundled helper.
  Ubuntu 24.04 can additionally require its standard **bwrap-userns-restrict**
  AppArmor profile, supplied by `apparmor-profiles`. That confined profile lets
  `/usr/bin/bwrap` create the sandbox and denies capabilities to its children;
  AppArmor and the global unprivileged-user-namespace restriction stay enabled.
  Have the host administrator review and load that profile as appropriate;
  see [Codex's Linux prerequisites](https://learn.chatgpt.com/docs/sandboxing#prerequisites).
  ROMP setup does not change host security policy automatically.

  Without a working helper, startup can fail while loading instructions with
  `bwrap: … Permission denied` or `Failed RTM_NEWADDR: Operation not permitted`.
  Auto review cannot repair this earlier initialization stage. The legacy
  Landlock fallback cannot enforce this profile's restricted host reads.

- Manual approval prompts are not yet interactive in ROMP. Auto mode uses
  Codex's reviewer; a manual request that reaches ROMP is declined, never
  silently accepted. Interactive needs-you approval cards remain planned.

## What works, what's coming

Working today: lanes and status, task cards and judging, full chat (prompts,
replies, thinking, commands, file diffs, web searches), steering a running
turn, interrupts, model and reasoning-effort switches, resume after restarts,
and postal delivery into Codex sessions.

Slash commands: `/model` and `/effort` work (they apply at the session's next
turn). `/clear` (and `/new`, Codex's own word for it) starts a fresh conversation
for the session: a new Codex thread under the same session, so its name, mail,
tags, color, mode, model and effort stay. Messages keep their order around it:
a message typed after the `/clear` lands on the fresh conversation; a message
queued before it runs first, on the conversation it was typed into, and the
`/clear` waits behind it; only a queue stuck behind a failed start of the old
conversation rides into the fresh one. The
cleared conversation stays reachable from the "Conversation cleared" card in the
chat and leaves the timeline, feed and judges, as it does after a Claude `/clear`;
that card, the bell notice and the settling of the old conversation's open cards
land on the FIRST prompt into the fresh conversation, not at the `/clear` itself
(the boundary is the new conversation's first record), so open cards stay open
until then. Typed mid-turn it queues and runs at the turn's end. Anything else
(`/compact`, a skill) typed into the composer, sent from the timeline's lane
menu, or sent with `romp send` is refused with a notice and not sent to the
model as text, and the composer's `/` list shows only what a Codex session
takes. Two paths still reach the model as text: a follow-up typed from a card
whose whole body is a slash command, and a slash command a Codex session had
already queued before the guard existed. Compacting a Codex conversation
natively is coming (`plans/codex-backend.md`).

The chat and timeline effort menus use the selected model's supported levels
from the Codex app-server's model catalog. Romp also validates effort changes
against that catalog; it does not maintain a separate list of Codex levels.
New levels appear when the installed app-server advertises them. A missing
catalog or unknown model provides no effort choices.

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
