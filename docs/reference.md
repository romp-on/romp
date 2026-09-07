# Reference

This page lists every command and knob. It is here for driving Romp from the
terminal, for scripting against it, and for debugging: you do not need any of it
for ordinary use, where the user interface covers everything. Everything here
runs on the machine that hosts the kernel.

## The `romp` command

Run `romp` on its own and it opens the dashboard, which is all most days need.
Every other command is a bare word after it, and a session's name is always an
argument rather than the command itself, so the two can never collide: `romp new
update` starts a session called "update".

| Command | What it does |
|---|---|
| `romp` | Open the dashboard in your browser, printing the tokened link too |
| `romp new <name>` | Start a session, run by the kernel and watched from the dashboard |
| `romp new -d <dir> <name>` | Start it in `<dir>` instead of the current folder |
| `romp new -t <name>` | Start it as a terminal (tmux) session and attach; add `--detach` to leave it running |
| `romp resume` | Resume a past conversation, chosen from a full-screen picker |
| `romp status` | Manager and kernel status |
| `romp refresh` | Restart the postal bus and every kernel immediately, picking up new code (cut turns resume with their history) |
| `romp update [host…]` | Push this machine's committed Romp to attached remotes and restart them |
| `romp up` | Run the kernel manager in the foreground; rare, since the login service runs it |
| `romp version` | Version report across the moving parts |
| `romp keyswap [<name>] [--cycle <session,…>\|--cycle-all]` | Switch the API key source without restarting the manager: selects a key command, a 1Password reference or a legacy key from `service.env.<name>`, and `--cycle` reconnects running sessions onto it. Bare, it reports the configured source and candidates without fetching secrets. See [Switching which API key the sessions bill](#switching-which-api-key-the-sessions-bill-romp-keyswap) |
| `romp help` | The same list, from the terminal |

These are for scripting and for agents rather than daily use:

| Command | What it does |
|---|---|
| `romp url` | Print only the tokened dashboard URL, for piping |
| `romp sessions [--json]` | The fleet with each session's state, identity colours, directory and backend |
| `romp perf [--interval <s>] [--json]`, `romp perf log on\|off` | The kernel's performance counters as rates over two snapshots (below); `--json` prints one raw snapshot; `log on\|off` turns the `romp-perf` stderr log on or off without a restart |
| `romp perf client [--minutes <n>] [--json]` | What the open dashboards' browsers spent on the frames they received (below): handler milliseconds per minute by frame type with window p50/p90/p99 and max, the worst minute's main-thread-free p90, long animation frames and their attributed callbacks, the worst minute, heap and DOM, the slowest frames — per dashboard and pane over the last `<n>` minutes (default 10) |
| `romp mail …` | The postal service from the shell (below) |
| `romp send <session> [--tag <label>] <text>` | Hand a session a message, on either backend. Anything a script, cron job, or launcher composes SHOULD carry a tag (one word, letters/digits/dashes, up to 24 chars): the chat then renders it as machine-sent under that label instead of as the user's typed words. Raw POST /send callers pass it as the JSON `tag` field (`{name, text, tag}` — a malformed tag fails the whole send, loudly); `--tag` is the CLI's equivalent. Both resolve to the `<!-- romp-tag: <label> -->` marker in the delivered text |
| `romp new --env NAME=VALUE <name>` | A per-session env var for the SDK session, repeatable; a re-run against a running `<name>` replaces the whole set — vars not re-named are dropped |
| `romp new --no-env <name>` | Clear a running SDK session's per-session env (declares the empty set) |
| `romp new --in <tag> <name>` | Put the new SDK or Codex session in `<tag>`, so its tab lands in that group (repeatable; a name that does not exist yet creates the tag). Applies to `<name>` if it already runs. The kernel echoes `tags` (the session's tags) and, per `--in`, the stored name it landed as (`tagsApplied`, beside `tagsRequested`): a name the store trimmed or clamped prints as "applied as"; a missing echo, or a tag the kernel refused, prints a warning |
| `romp new --no-inherit <name>` | Run inside a romp session, `romp new` sends that session's stable id (`ROMP_SID`) as the new session's `parent` (marked `parentAuto`), and the kernel copies the parent's tags onto the child; inside a comment thread, the parent is the session the thread belongs to. This flag withholds the parent, so the new session starts outside them. A kernel that never ran the calling session creates the session untagged and echoes `parentIgnored`, which the CLI reports in one line. Raw POST /new callers pass `parent` (a live name or a known sid; an unknown one is a 400 unless `parentAuto` is set) and `tags` (a list of names); opening a name that already runs never inherits |
| `romp tag [<name>] [--add <session>…] [--remove <session>…] [--color <hex>] [--rename <new>] [--delete] [--host <kernel>]` | Session tags. Bare, it lists them; with a name, it merges one tag (created on first use). A tagged session leaves the untagged view, and its tab sits in that tag's section of the strip. `--host` edits an attached kernel's tag |
| `romp interrupt <session>` | Interrupt whatever turn a session is taking |
| `romp compact <session> [--wait] [--timeout <s>]` | Compact a session's context in place (Claude's `/compact`: summarize the history, keep the session's name, id, mailbox, and watches) — the alternative to ending and recreating a long-lived session, and the external hand a session needs since it cannot `/compact` itself mid-turn. Quiet session → compacts now; open turn → queued, fires alone the moment the turn ends (the same safe path the chat's compact button uses). `--wait` blocks until the compaction has started and cleared, polling the kernel's own `compacting` signal on the `/sessions` rows (also the field to point a `romp watch` predicate at for scripted recycling); exits 1 honestly on timeout. A remote session's compaction is requested on its own kernel — `--wait` can't follow it from here and says so |
| `romp end <session>` | End a session |
| `romp move <session> <dir>` | Move an SDK session's working directory to `<dir>` (the folder must already exist); the conversation, name, mail and history stay with the session. Quiet session → moves now; open turn → queued, fires when the turn ends. See [Moving a session to another folder](#moving-a-session-to-another-folder) |
| `romp checkin <host>` / `romp checkout <host>` | Publish this machine to an attached hub, or withdraw it |
| `romp default-dir [PATH]` | The default working directory for new sessions; no argument prints it, `""` clears it |
| `romp debug [on\|off\|status]` | Judge debug mode, where rejection rows carry the full input and reply |
| `romp resume <id> [--name <n>] [--detach]` | Resume one exact conversation by UUID |
| `romp refresh --quiet` | Refresh at the next quiet window instead — waits for sessions to finish their turns (15-min backstop) |

`--env` gives one session its own environment, so two sessions in the same
directory can run with different toggles (a `FEATURE_FLAG=1`, a `CLAUDE_CODE_*`
switch) without editing the directory's `.claude/settings*.json`, which reaches
every session there and outlives them all. Re-running `romp new --env` against
a running session declares its full per-session env: any var you don't name
again is dropped, and `romp new --no-env <name>` declares the empty set — it
clears them all. Keep real secrets out of it: each value is copied into
per-session files and the session registry under `~/.local/state/romp/`. For
runtime API keys, configure a key provider in the service environment
instead; see [Service environment and credentials](#service-environment-and-credentials).

Two things to know before building on `romp sessions --json`. **`waiting` means
at rest**, the ordinary state of a session that has finished its turn, so
matching it as an alert badges the whole idle fleet as needing you; the states
that want a person are `permission` and `picker` (a live prompt) and `blocked`.
(`romp sessions` emits the RAW backend states — the dashboard's chip states,
`needsInput`/`awaitingBg`, never appear here.) And
**`id` is the durable key**, not `lastSid`: everything Romp files per session is
keyed by `id`, while `lastSid` is the live transcript's id and forks on
`/clear`.

That key opens the per-session records under `~/.local/state/romp/`. The
per-turn one-liners live in `captions/<id>.jsonl`, one JSON record a line, with
the text under `caption`. A record's own `id` field is not the session's: it
identifies the turn within it. There is no `summaries/` directory; an older
layout had one, and reading it fails silently, since a missing directory just
yields nothing rather than an error.

### Moving a session to another folder

A session's working directory can change after it starts, so when a subproject
moves to its own repository, the session working on it can follow. Right-click
the session's tab and choose **Move to folder…**, or run `romp move <session>
<dir>`. The folder must already exist. Terminal (tmux) sessions cannot be
moved; start a new one in the folder instead.

What moves with the session:

- The conversation. Claude Code moves the transcript, with the tool-results,
  subagent and workflow files beside it, into the new folder's project
  directory under `~/.claude/projects/`; Romp moves the session's earlier
  transcripts (its `/clear` episodes and resume forks) the same way, so history
  and search keep working.
- The session's name, colour, mailbox, goals, cards and captions, all keyed by
  the session id rather than the folder.
- What the agent sees. Claude Code tells the model where it now is and loads
  the new folder's `CLAUDE.md`; from the next turn on, permission rules, hooks,
  skills and project MCP servers come from the new folder.

What does not move, because Claude Code keys it by folder rather than by
session: the old project's auto-memory (`~/.claude/projects/<old
folder>/memory/`), the old folder's entry in `~/.claude.json` (its allowed
tools, MCP approvals and trust), and the old repository's
`.claude/settings.local.json`. A comment thread opened on the session also
keeps its own folder. What Claude Code keys by session id (its debug log, task
store and file-history checkpoints) needs no move.

A move never interrupts a turn: on a session mid-turn it is queued as a chip in
the chat and fires the moment the turn ends, like a queued `/compact`. If Claude
Code reports a turn Romp could not see (one it started itself), the chip waits
for that turn to end too; the chip can be cancelled like any queued item. A
closed session is revived first, in its old folder, then moved. Only one move
per session is in flight at a time; a second request while one is pending is
refused. Every refusal (a folder that does not exist, a path that is a file, a
move already pending) is reported where you asked. If Claude Code's reply to
the move is lost, Romp settles the outcome by where the transcript is, the same
check it runs after a restart that interrupted a move; a move it cannot settle
is reported and left for the next kernel start, with nothing changed.

The move is Claude Code's own relocation (the `set_cwd` control behind the
interactive `/cd`), with Romp moving its own records alongside. It fires Claude
Code's `CwdChanged` hook, not `SessionStart`; Romp registers no `CwdChanged`
hook, so nothing on Romp's side re-runs.

## The Romp Postal Service

How sessions message each other, from either side. Inside a session it is an MCP
server, so an agent calls the tools below directly; from a terminal the same
mailbox is behind `romp mail`. See
[Inter-agent communication](guide.md#inter-agent-communication-the-romp-postal-service)
for what it is for.

### Mail from the terminal

```bash
romp mail send [--kind delegate|coordinate|question] <name> "<text>"
romp mail inbox                  # read your messages, and clear them
romp mail peek                   # read them without clearing
romp mail agents                 # who is live, their branch and working-note
romp mail working "<note>"       # publish what this session is working on
romp mail sent                   # your sent messages, and whether each was read
romp mail recall <to> [id]       # unsend a message the recipient has not read
romp mail remote                 # connect this remote machine to your laptop's bus
```

### Mail inside a session (MCP tools)

| Tool | What it does |
|---|---|
| `send_message(to, body, kind)` | Message a live session by name; `kind` declares delegate / coordinate / question |
| `check_inbox()` | Read messages sent to you (also delivered at the end of each turn) |
| `list_agents()` | The live sessions, each with its branch and working-note |
| `set_working(text)` | Publish what you hold so peers steer clear |
| `check_sent()` | Whether your sent messages were read yet |
| `recall_message(to, id?)` | Unsend a message the recipient hasn't read |

### Claude Code 2.1.224 or newer

Mail to a terminal (tmux) session delivers through Claude Code's per-session
inbox socket, which the CLI added in 2.1.224: delivery is instant and never
touches a half-typed draft. An older Claude Code still works — delivery falls
back to typing the mail into the pane, which is slower and waits for a free
prompt — and `romp` says so at launch, with the upgrade being one
`claude update` away.

## Configuration

### Folder click, in your terminal or editor

The chat statusline shows the session's working directory; clicking it opens
that folder. The default is the OS opener (`open` / `xdg-open`). To open it
elsewhere, set a command via the env var `ROMP_OPEN_FOLDER` or the first
non-comment line of `~/.config/romp/open-folder`; `{dir}` is replaced with
the clicked path (omitted, the path is appended). The command runs on the
kernel's machine.

```bash
# ~/.config/romp/open-folder: pick one line
open -a Ghostty {dir}               # macOS: a new Ghostty window there
ghostty --working-directory={dir}   # Linux: Ghostty
code {dir}                          # VS Code instead
```

### Model and effort, from the statusline or a typed command

Typing `/model X` or `/effort X` into the chat composer, or sending one with
`romp send`, is the same setting change as a pick from the statusline's model
and effort dropdowns: the kernel takes it through its own setters, so what it
remembers (the value a reconnect relaunches with, the defaults new sessions
start from) follows the switch. A typed `/fast on|off` goes through the same
setters and matches a pick from the fast badge the next section describes, a
separate toggle rather than one of the two dropdowns. A bare `/model` (the
CLI's own picker), a value the kernel cannot vouch for (a typo), or a longer
message that merely opens with the command goes to the CLI verbatim, and the
chat shows the CLI's own reply.

The two backends apply the change differently. An SDK session switches model
live but reloads to apply a new effort: the chat shows "Reloading session…"
and the effort badge shows switching-dots until the reload completes, and a
session that is mid-turn reloads when the turn ends. A terminal (tmux) session
gets the CLI's own command typed into its pane. `/model` there asks for a
confirmation, which the kernel accepts on your behalf so the pane is never
left waiting on a keystroke the dashboard cannot send; `/effort` and `/fast`
apply in place.

### Fast mode, from the chat statusline

The statusline's badges — permission mode, model, effort — are each a small
dropdown. A fourth appears when the session reports Claude Code's fast-mode
state (an Opus-only research preview, billed at a premium): it reads **Fast**
in orange while fast mode is on, **Slow** while it's off, and **Cooldown**
while fast requests are rate-limited. Picking On or Off sends the CLI's own
`/fast` command; the badge never appears on a session that cannot run fast
mode. Turning it on while the session is on a non-Opus model makes the CLI
switch to a fast-capable one, which the chat shows as the command's own
confirmation. If the CLI refuses the toggle (for example, the account has
extra usage turned off), a toast says why and the pick reverts to off —
the control never silently disappears.

### Per-session billing (login vs API key)

An SDK session can bill either the machine's Claude login (subscription usage)
or the configured API key source — per session. That source can be a key
provider run at use time (a key command, or a 1Password reference) or a legacy
`ANTHROPIC_API_KEY`.

The new-session picker's **Billing** row states the case whenever the backend
toggle says SDK: segmented buttons when the selected host offers both choices,
and with only one real choice, the same spot simply writes out which applies —
`Login (name@example.com)` or `API key` — so what a session will bill is never
a mystery. A live session additionally wears a statusline badge for
*switching*, beside mode/model/effort, and that control keeps the stricter
rule: it exists only when both choices are real (a one-option selector is
noise). Switching reconnects the session to apply (the key rides the launch
environment), with the same switching-dots the effort badge wears.

The login is named by its account (the email the credential store records);
the key option is labelled plainly `API key` — no fragment of the key, not
even a last-4 tail, ever reaches a browser or a screen. A new session
defaults to the last pick made anywhere, and before any pick to the key when
one is configured — exactly what an ambient key did before the selector
existed. tmux sessions are not covered by the picker: their CLI lives in the
tmux server's environment, which the kernel does not control. What Romp does
do there, when a key provider is configured, is keep the manager's
startup `ANTHROPIC_API_KEY` out of the server's globals, so a terminal
session falls to Claude Code's own auth (login or `apiKeyHelper`) rather than
billing a key nobody chose; see [API keys from a secret manager at
runtime](#api-keys-from-a-secret-manager-at-runtime).

Each chat tab's hover tooltip carries the same fact as a `Billing` row —
`API key`, or `Login (name@example.com)` — whenever the session's backend
reports it, one-auth machines included; only tmux sessions, whose billing romp
cannot know, show no row. When the CLI's own report disagrees with what the
session was launched for — a key found through `apiKeyHelper`, say — the row
carries both: `Login (CLI reports API key)`.

Failures are loud rather than silent: a session that lands on the other auth
than it was launched for (say, a key found through `apiKeyHelper`) is flagged
in the Log panel, and a dead credential — "Not logged in", an invalid or
expired key — blocks the session's card with the fix named, and is never
auto-retried.

One side of that check can be the box's *design*: on a machine whose sessions
are all meant to bill a key that arrives through `apiKeyHelper` — so it never
appears in `service.env` — the landed-on-the-other-auth warning would fire on
every init, permanently. Declaring the intent fixes it: set
`ROMP_EXPECTED_AUTH=key` (or `login`) in `service.env`, and a session landing
on the declared side is quiet while one landing on the other side is flagged,
naming the declaration. The check inverts rather than disappearing; unset (or
any other value), it compares against what the session was launched with, as
before. One explicit gear **Billing** pick supersedes the declaration from then
on: the remembered pick becomes the box's expectation and the env var goes
inert (it described the unpicked design), so re-seeded spawns are judged
against your pick, never against stale doctrine.

The declaration is also checked once, when the kernel starts, against the
key source a launch would select. Under `ROMP_EXPECTED_AUTH=login`, a selected
API key source (a `ROMP_API_KEY_CMD=` line, a `ROMP_API_KEY_REF=` line, or an
`ANTHROPIC_API_KEY=` line with a value in `service.env`; for a foreground
manager, the same names exported in its environment) is a contradiction: that
source is injected at launch for every session without an explicit Billing
pick, so those sessions bill the key. One problem line in the Log panel says so
before anything launches, naming the file or the environment and the variable
but never a value; fix whichever side is wrong. A source that is selected but
cannot be used (both provider lines in the file; a provider line removed while
the marker beside the file still names it) is still a selection, and the line
says so instead: those sessions will try the source and fail to launch rather
than bill the login. Under `key`, a selected source agrees with the declaration
and nothing is said. With no key source selected anywhere the launch looks,
Romp injects nothing and Claude Code's own credential applies (see [API keys
from a secret manager at runtime](#api-keys-from-a-secret-manager-at-runtime)),
so nothing is said; nor with no declaration, nor once a Billing pick has made
it inert. The per-init check above still confirms each landing.

The usage rail reflects a mixed machine: the window bars (5 hours / 7 days /
Fable 5) are drawn once, aggregated across every connected host's login as the
worst reading per window, and an `API` cell beside them carries the
key-billed dollars (5-hour burn and month-to-date, numbers only). Hovering
breaks both down per host — one column per host, side by side — and a host
can show its login's windows and its key's spend together. Only turns whose
session billed the key count toward the API numbers — a login turn's computed
cost is dollars nobody pays.

The token count beside the dollars is every kind together: fresh input,
output, cache writes, and cache reads. Cache reads are most of it — every API
call within a turn (one per tool step) re-reads the whole context from the
cache, so a long session's single turn can read tens of millions of tokens at
a tenth of the input price. The hover splits each window's count by kind, so
the size of the number carries its explanation.

### Self-scheduled work wakes an idle session

A session's own scheduled work (a recurring Monitor, a cron firing, a
background task's completion notice) arrives as a queued notification even
while the session is idle. The Claude Code CLI usually delivers it on its
own, starting the turn within a fraction of a second; but a session can fall
into a stuck state where the CLI only queues, nothing ever starts the turn
that reads the queue, and the backlog waits silently until your next message.
Romp watches for that: once a queued notification has sat undelivered for a
minute (well past the CLI's own delivery window) with no turn running, one
driven turn delivers every text that has waited out that minute, verbatim and
with no words of Romp's own, and logs one kernel-log line per wake; a newer
arrival waits out its own minute rather than delaying the rest. A
notification that arrives mid-turn is delivered once the turn settles, and
one whose delivery a kernel restart interrupted is re-driven on the next
boot rather than dropped. Notifications
the CLI delivers itself in either state (a background agent finishing) are
left to it, and sessions that are mid-turn, compacting, blocked on an API
error, retry-paused, or that you interrupted or ended are left alone. On the
first run after an upgrade, a session holding a genuinely old queued backlog
may get one catch-up turn delivering it; that is this feature doing its job
once.

### Install-time switches

For `./install.sh`:

- `ROMP_NO_SERVICE=1` skips the login service.
- `ROMP_NO_EXT=1` skips the VS Code / Cursor extension.
- `ROMP_NO_SDK=1` skips the SDK backend's venv (tmux sessions still work).

For the one-line installer (`bootstrap.sh`), which passes all of the above
through to `install.sh`:

- `ROMP_DIR=<path>` where to clone; default `~/romp`.
- `ROMP_REF=<tag|branch>` install a specific ref; default is the newest `v*`
  release tag, falling back to `main` when none is published.
- `ROMP_NO_PATH=1` leaves your shell rc alone.

### Ports

- `ROMP_KERNEL_PORT=<port>` moves the kernel and its dashboard off the default
  `29855`. `ROMP_SERVE_PORT` is a second name for the same port, the one the
  manager and the supervised service use. Set either and the other follows; set
  both to different values and the kernel refuses to start rather than picking
  one for you.
- `ROMP_POSTAL_PORT=<port>` moves the postal bus off the default `25302`.

Set these if something else on the machine already holds the default. Both have
to agree across everything that talks to the kernel, so export them where the
whole environment sees them rather than for one command.

Run `romp-service install` again after changing one. The service unit bakes in
whatever is set at install time, so a renumbered port that only lives in your
shell leaves the supervised manager on the old one, and the two collide.

### Service environment and credentials

The manager runs as a login service (launchd on macOS, systemd --user on
Linux), so it does not receive variables exported by your shell rc. Configure
the service in `~/.config/romp/service.env` using plain `KEY=VALUE` lines and
owner-only permissions (`chmod 600`). The service reads this file at manager
startup; API key source settings are also read live before use. Other changes
need a manager restart. `ROMP_SERVICE_ENV_FILE` overrides the file's path.
Supervised services use this file as their API key source. An empty or missing
source cannot fall back to credentials inherited from an earlier manager
start, including after a kernel refresh or crash restart. Foreground managers
can use an environment source when no service-file source governs them.

#### API keys from a secret manager at runtime

To keep API key values out of Romp's configuration files, configure a **key
provider**: something Romp runs at the moment a key is needed, which prints
the key. Romp knows nothing about any particular secret manager; the
recommended route is a key command, and a 1Password reference is a built-in
shorthand for one.

**Recommended: a key command (`ROMP_API_KEY_CMD`).** Any command line that
prints the key on stdout and exits 0 — the same contract as Claude Code's
`apiKeyHelper`, so a helper script you already have works as-is:

    ROMP_API_KEY_CMD=~/.config/romp/fetch-api-key

Romp runs it with `/bin/sh -c`, stdin from `/dev/null`, a 15-second timeout,
and reads stdout: one line (a trailing newline is tolerated and stripped),
non-empty, no NUL, at most 16 KiB. A non-zero exit, a timeout, an empty or
multi-line output, or a command the shell cannot find (exit 127) fails the
operation with a static "key command failed / timed out / not found"
message. The command's stderr is discarded and never logged, since a secret
manager's diagnostics can quote its own token.

The command runs in a **minimal environment**, not a copy of the kernel's:
`PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `LANG`, `LC_*`, `TERM`, the
`XDG_*` names, and any `OP_*` credential names Romp claimed (below). Nothing
of Romp's — the serve token, a startup `ANTHROPIC_API_KEY`, the
`ROMP_API_KEY_CMD` line itself — reaches it. The script is therefore expected
to fetch **its own credential** rather than have Romp carry one for it. The
recommended shape, here for 1Password with a
[service account](https://developer.1password.com/docs/service-accounts/)
whose token sits in a `chmod 600` file of its own:

    #!/bin/sh
    # ~/.config/romp/fetch-api-key (chmod 700): print the API key, nothing else
    OP_SERVICE_ACCOUNT_TOKEN="$(cat ~/.config/op/service-account-token)" \
        exec op read --no-newline "op://vault/item/field"

Any secret manager's CLI works the same way (`aws secretsmanager get-secret-value
--query SecretString --output text`, `vault kv get -field=…`, `bw get
password …`, `gcloud secrets versions access latest --secret=…`, `pass show
…`): the command owns how it authenticates, Romp owns when it runs and what
happens to the value. The command's identity on every Romp surface (the
`romp keyswap` listing, the kernel log, `/keycycle`) is a fingerprint of the
command line, never the line itself, which may name a vault or a path.

**The 1Password shorthand (`ROMP_API_KEY_REF`).** A
[1Password secret reference](https://www.1password.dev/cli/secret-references)
is the built-in equivalent of a key command that runs
[`op read --no-newline`](https://www.1password.dev/cli/reference/commands/read)
on the reference:

    ROMP_API_KEY_REF=op://vault/item/field

Same runner, same timeout, same environment and validation as a key command
(except that with `--no-newline` a trailing newline in the output is a
malformed key). The reference is passed as one argument, never evaluated by
a shell. The difference from the command route is where `op`'s own credential
lives: with a reference, Romp expects the service-account token in
`service.env` beside it,

    OP_SERVICE_ACCOUNT_TOKEN=ops_...
    ROMP_API_KEY_REF=op://vault/item/field

and takes `op`'s credential names (`OP_SERVICE_ACCOUNT_TOKEN`,
`OP_SESSION_*`, `OP_CONNECT_*`, `OP_ACCOUNT`) out of its environment as its
first act at startup, says which names it claimed in the kernel log, and hands
them to the provider subprocess alone: no Claude session, judge call, or tmux
launch inherits them, so an agent running `env` sees neither the API key nor
the credential. Give the account read access to that one vault, and nothing
else. Like the rest of `service.env`, the token line loads when the manager
starts; changing it needs a manager restart, where the reference itself is
read live. Do not put the token in a per-session environment (`romp new
--env`), which is copied into per-session files. With a key command, none of
that applies: the script reads its token itself and Romp never holds it.

Configure **one** provider: a `service.env` with both a `ROMP_API_KEY_CMD=` and
a `ROMP_API_KEY_REF=` line is an error ("configure one of ROMP_API_KEY_CMD or
ROMP_API_KEY_REF, not both"), not a precedence rule. A provider line takes
priority over a legacy `ANTHROPIC_API_KEY` line in the same file, and an
empty or invalid provider is an error, not a request to use the legacy key.
Remove competing plaintext assignments when migrating; `romp keyswap` does
this automatically when selecting a profile.

With no key source selected — the env file has no provider line and no
`ANTHROPIC_API_KEY=` line with a value, and no provider was selected earlier
(a removed provider stays an error until another source is configured; see
below) — Romp injects nothing, and Claude Code's own credential resolution
applies: its `apiKeyHelper`, which can fetch a key from any secrets manager,
or its login. A session's API-key Billing pick then only takes effect once a
source exists; until one does, the kernel says so once in its log. A provider
or key line in `service.env` is for boxes where Romp should manage the key —
swap it, fingerprint it, cycle sessions onto it. The two routes can coexist
on one box only in the sense that Romp's source, when present, is what Romp's
sessions launch on; a box that wants Claude Code's `apiKeyHelper` to decide
keeps `service.env` free of any source.

Romp runs the provider for each Claude SDK session launch or reconnect, each
API-key-billed judge call, and each direct model-catalog refresh. A paginated
catalog refresh uses that credential for all its pages. An explicit
`romp keyswap --cycle` resolves the key once per request to check which quiet
sessions need a reconnect. When a retrieval fails, the judges do not retry it
on every call: the failure holds for the rest of that judging pass and is
retried when the next pass begins or the source changes, so an unreachable
provider costs one timeout per pass. Romp captures the value in memory and
passes it to that operation. It does not write the resolved key to disk or
cache it for later operations. A running Claude process retains the key it
received at launch until it reconnects; this is not retrieval before every
message in an existing session.

The command (or `op`) must be on the **service's PATH**, and its secret
manager access must work for the OS user running the service. The service
installer records PATH at install time; run `romp-service install` again after
changing it. An interactive terminal sign-in does not by itself establish that
a headless login service can read the same secret: a service has no desktop
app to unlock. For 1Password the supported unattended route is a service
account, as above.

The tmux server needs the same care as the kernel's children, because every
pane inherits the **server's** globals, not the launching client's. Whenever
Romp becomes the provider consumer — at kernel start, or later when
`romp keyswap` selects a provider on a box that started without one, with no
manager restart — it unsets the `OP_*` names **and** the manager's startup
`ANTHROPIC_API_KEY` from the tmux server's global environment; the manager
starts the server without them, and `romp new -t` scrubs them again before
the pane exists (reading the provider from its own environment or from
`service.env`'s line). A terminal session on a provider-governed box therefore
never bills the key the manager started with: with no `ANTHROPIC_API_KEY` in
its environment it falls to Claude Code's own auth (login or `apiKeyHelper`).
Panes that already existed when the provider was selected keep the
environment they launched with; end or relaunch them. A box with no provider
configured is untouched: static-key panes rely on inheriting the key, and an
`apiKeyHelper` box's sessions need `op`'s environment.

This keeps the token out of every agent's shell by default; it is
inheritance hygiene, not isolation. The files stay readable to the same OS
user (keep them `chmod 600`), and a same-user process can read the manager's
original environment, which is why a service account must see only the one
vault.

A supervised manager (the systemd or launchd service) reads its key source
from `service.env` **only**. A key that reaches the manager some other way, a
systemd drop-in `Environment=` or a launchd plist entry, is ignored and said
so once in the kernel log with its fingerprint; sessions without an explicit
Billing pick then launch on the login. Move such a key into `service.env`, or
replace it with a provider.

For a foreground manager, the same provider can be supplied in its
environment:

    ROMP_API_KEY_CMD=~/.config/romp/fetch-api-key romp up
    ROMP_API_KEY_REF=op://vault/item/field romp up

The Billing picker, status displays, and `romp keyswap` listing and selection
inspect the configured source without running the provider. A configured
provider therefore means "API key available to try", not "secret manager
access verified". If a selected provider cannot resolve the key, the operation
fails with a credential error. It does not use an ambient key, a previous
resolved key, or a Claude login as a fallback. Choosing **Login** explicitly
still uses Claude Code's supported login flow and does not run the provider.

To migrate an existing service:

1. Put the API key in your secret manager and write the command that prints
   it (or, for 1Password, obtain the field's secret reference).
2. Replace `ANTHROPIC_API_KEY=...` in `service.env` with
   `ROMP_API_KEY_CMD=<command>` (or `ROMP_API_KEY_REF=op://vault/item/field`).
   Replace any sibling profiles used by `romp keyswap` in the same way, and
   remove obsolete plaintext copies.
3. Refresh the kernel once to load this version of Romp. Future source edits
   take effect without restarting the manager: the next session launch or
   judge call reads the provider, and that first read also scrubs the tmux
   server of `op`'s names and the retired `ANTHROPIC_API_KEY`.
4. Reconnect existing key-billed SDK sessions with `romp keyswap --cycle-all`
   when they are quiet. Newly launched sessions and subsequent judge/model
   requests use the configured source immediately. Terminal (tmux) sessions
   launched before the migration still carry the plaintext key they started
   with; end and relaunch them.

Once a provider has been selected from `service.env`, Romp remembers it on
disk in the sibling file `service.env.source` (the word `command` or `op`,
mode 600, never a command line, a reference or a key), so the memory survives
kernel restarts: deleting the provider line and restarting does not make
sessions fall to the login. Selecting a static key — `romp keyswap <static
profile>`, or writing an `ANTHROPIC_API_KEY=` line — removes the marker, so an
intentional switch is not an error. `romp keyswap` does not list the marker
as a profile.

Removing or emptying an explicit service-file key source cannot revive the
key inherited when the kernel started. After removing a selected provider,
API-key operations fail until a valid source is selected; choose **Login**
explicitly to use that mode. Removing a static `ANTHROPIC_API_KEY=` line
while the kernel runs is different: the file stays authoritative and
sessions launch with nothing of Romp's injected, whatever their Billing
pick, so Claude Code's own credential (its `apiKeyHelper` or login) applies;
the kernel log says so once, with the removed key's fingerprint and the
file's path. Removing a source does not revoke a credential already held by a
running Claude process; reconnect or end those sessions too. Rotate a
previously exposed key with its issuer as appropriate.

#### Existing API keys and Claude login

A key provider is optional for general use. Without one selected, Romp
continues to support legacy `ANTHROPIC_API_KEY` configuration, including in
`service.env`, and the existing Claude Code login. A foreground manager can
also use its inherited API key when no service-file source governs it. For
login, authenticate through Claude Code's supported CLI login flow and select
**Login** in Billing; no extracted OAuth token is needed.

Plaintext keys remain supported for compatibility, but do not meet policies
that require secrets to be fetched from a secret manager at runtime. File
permissions do not change that distinction.

#### A key from a secret manager, with none held by Romp

If your key lives in a secret manager and you would rather Romp not hold it,
configure no key source in Romp and point Claude Code's
[`apiKeyHelper`](https://code.claude.com/docs/en/settings-reference#apikeyhelper)
at the secret manager instead. Romp passes a key to a session only when it has
one itself, so with no source configured every session and every API-key-billed
judge call uses Claude Code's own authentication. Romp cannot see that key: the
Billing picker offers no API-key choice, a session's Billing row reads
`Login (CLI reports API key)`, and `romp keyswap --cycle` skips every session
as billing the login.

1. Write a script that prints the key from your secret manager, and make it
   executable. With 1Password, for example:

        #!/bin/sh
        exec op read --no-newline "op://<vault>/<item>/credential"

    The helper runs inside each session's Claude Code process (and each judge
    call), a child of the service, so the secret manager's CLI needs a
    credential that works without a desktop app: for 1Password,
    `OP_SERVICE_ACCOUNT_TOKEN=` in `service.env`, scoped to the one vault. With
    no reference configured Romp leaves that token in the sessions'
    environment, where the helper needs it and where an agent's shell can read
    it. Any secret manager whose CLI can print the key works the same way.

2. Point Claude Code at the script in `~/.claude/settings.json`:

        { "apiKeyHelper": "/path/to/anthropic-key.sh" }

3. Leave `service.env` with no `ANTHROPIC_API_KEY` and no `ROMP_API_KEY_REF`
   line, and set `ROMP_EXPECTED_AUTH=key` so the per-init auth check knows the
   key arrives through the helper and stays quiet (see
   [Per-session billing](#per-session-billing-login-vs-api-key)). If a
   1Password reference was ever selected on this box, also remove the
   `service.env.source` marker beside the file: with it in place the removed
   reference stays an error rather than an absent source (see [API keys from
   1Password at runtime](#api-keys-from-1password-at-runtime)).
4. Restart the service once.

Rotating the key is then a change in the secret manager alone: Claude Code
re-runs the helper on its own refresh interval
(`CLAUDE_CODE_API_KEY_HELPER_TTL_MS`), so running sessions pick up the new key
with no Romp restart and no `romp keyswap`.

The model-catalog refresh is the one call Romp makes itself rather than
through a session. It reads only a key source Romp holds (or an
`ANTHROPIC_AUTH_TOKEN` in the service's environment, which would also change
what the sessions bill), so on a helper-only box it has no credential. Romp
then serves its built-in list, or the last one it fetched and cached, and the
kernel log says so at each refresh attempt (boot, and once per model id it does
not know); the pickers still work, and Claude Code's own alias table still
tracks each family's newest. Nothing else Romp does on such a box needs a key.

### Switching which API key the sessions bill (`romp keyswap`)

The key a session bills rides its launch environment, and Romp checks the
API key source in `service.env` **at every session launch**.
So changing keys — moving to another organisation's key, rotating a leaked
one, switching between a high-priority and a batch key — costs no manager
restart, and no session loses an open turn.

Keep one file per profile beside `service.env`, each with a single
`ROMP_API_KEY_CMD=<command>` (or `ROMP_API_KEY_REF=op://vault/item/field`)
assignment, `chmod 600`:

    ~/.config/romp/service.env.highprio
    ~/.config/romp/service.env.lowprio

Then:

    romp keyswap                       # configured source and candidate profiles
    romp keyswap lowprio               # select the source from service.env.lowprio
    romp keyswap lowprio --cycle-all   # …and move the running sessions onto it too
    romp keyswap lowprio --cycle web,api

Legacy profiles containing a single `ANTHROPIC_API_KEY=` assignment also
work. `romp keyswap <name>` writes the selected assignment and removes the
competing key-source assignments, keeping all unrelated lines as they were
(line endings come out as LF). A temp file and rename make the update atomic,
and the mode stays `600` (a looser one is tightened). A symlinked `service.env`
is written through: the target changes, the link stays. A profile without a
usable source is rejected, as is one with both provider lines. Listing or
selecting a provider never runs it; an explicit `--cycle` check asks the
kernel to resolve it.

After the rewrite:

* **new sessions, and any session you revive, bill the new key immediately** —
  nothing else to do;
* **already-running sessions keep the key their process started with**, because
  the key is handed over at launch. `--cycle-all` (or `--cycle <session,…>`)
  reconnects them so they re-present the new one. A reconnect resumes the same
  conversation with its history intact — the same mechanism a reasoning-effort
  or billing switch uses — and only for a session that is quiet right now. Sessions billing the machine login are
  skipped, dormant ones are reported as needing nothing, a session already
  launched on the current resolved key reads `current`, and a session with a turn,
  subagents or background tasks in flight is skipped and named — a reconnect
  would kill that work — so re-run `--cycle` for those sessions once they are
  quiet;
* **the judges and direct model-catalog refreshes** pick the new source up on
  their next call, with no cycling at all;
* **terminal (tmux) sessions** are not cycled. A swap from a static key to a
  provider removes the old `ANTHROPIC_API_KEY` from the tmux server's
  globals at the kernel's next key read and at the next `romp new -t`, so new
  panes fall to Claude Code's own auth; panes already open keep what they
  launched with — end and relaunch them.

No key value is printed or sent in Romp's status/control responses. Legacy
keys are identified by the first 12 hex of their sha256, such as
`sha256:1a2b3c4d5e6f`. Provider status uses a fingerprint of the command line
or reference, without resolving it; this cannot verify the secret value or
detect a rotation behind an unchanged provider. An explicit `--cycle` resolves
the current key for each quiet session and reconnects it if that key differs
from its launch key, including when the provider itself is unchanged. A session already
using that key reads `current` and stays connected. A reconnect resolves the
source again at the actual launch, so it does not reuse a key cached by the
cycle check.

**One restart, once:** a running kernel needs to load this version to support
runtime providers. Take the update with `romp refresh` — or `romp refresh
--quiet`, which waits for sessions to finish their turns first — and later
source swaps need no manager restart. `romp keyswap --cycle-all` reports when
it encounters a kernel too old to support cycling.

Remote kernels each have their own `service.env` and their own key: run
`romp keyswap` on that machine.

`ROMP_SERVICE_ENV_FILE` overrides the path of the file. The installer bakes
the path it resolved into the unit and, when that is not the default, exports
it to the service as well, so the kernel's live read and the installer name
one file; a service installed before that carries only the default. `romp
keyswap` compares the running kernel's source identity with the file's and
says `MISMATCH` when they differ — the check to make after a swap.

### What survives a restart

A kernel restart ends every session's CLI. On `romp refresh`, the manager's
restart-all or a service stop, the kernel receives SIGTERM and drains: it
closes each CLI, and a CLI still running when the drain's bound expires gets
SIGTERM, then SIGKILL. A crash respawn has no drain: the kernel died without
running one, its CLIs are orphaned, and the next kernel's boot reaper
terminates them (see below). The CLI's harness background tasks do not all end
with it. Its timers and monitors live inside the CLI process and end when it
does. A background shell is a separate process the CLI started, and a CLI
killed by SIGKILL runs no cleanup, so its shells are re-parented and may keep
running. The session resumes with its history and is told what was cut: its
in-flight turn, if it had one, and each background task, with a request to
check whether each is still running before relaunching it. A kernel restart has
never touched work a session deliberately detached: tmux servers, `setsid`
children and other processes that outlive their shell.

A message the kernel cannot handle does not end the session's CLI. The kernel
handles each streamed message on its own: when a handler raises, it logs the
exception type and the failing frame (file, line and function, first on the line
so the error center's clipped row still shows it), the message's type and
subtype, what that message lost (an assistant or user message is also a
transcript record, so the chat rebuilds it from disk — a compaction boundary is
one too, while a model or mode change's confirmation line is not; a turn result
still settles its turn, and the line says so only when the settle ran; a
stream-only frame's content is gone until the next such frame), the exception's
own text (uuid-shaped ids shortened to eight characters, clipped to 160
characters; it carries whatever the raising code put in it, never the message's
content), and a compact frame chain (innermost first: file, line and function
for at most the innermost eight frames, no locals, at most 600 characters,
dropping outer frames first so the failing frame is always named) to the kernel
log and the dashboard's error center, then goes on to the next message. A
failure while filing a turn result — its spend, its live-tail sweep — still
settles the turn: the session reads waiting, its queue moves, and a reconnect
that waited for the turn's end runs; the spend accounting runs last among the
result's bookkeeping, so its failure skips nothing else. A handler that fails on
every message is one error-center entry, showing its first occurrence: the
repeat count is kept on the kernel's problem ring (appended to the row's text,
past what the error center displays), every repeat is a kernel log line, and an
entry the ring has since dropped re-enters with its full detail.
Before 2026-09-06 one such exception ended the receive loop, which closed the
CLI in the middle of its work (the in-flight turn, its subagents, its background
tasks) and resumed the session as after a crash. A fault of the stream itself,
such as the CLI exiting or its transport closing, still ends the loop; the log
names the failing task and its frame chain, and the session resumes with its
history, told what was cut.

A service restart (`systemctl --user restart romp-manager`, or the machine's
own service management) kills everything in the service's cgroup, so on Linux
under systemd Romp runs each session's CLI, and the default tmux server the
manager starts, in a transient systemd scope of its own, outside that cgroup
(`systemctl --user list-units 'romp-session-*' 'romp-tmux-*'` lists them). A
session's tmux servers, `setsid` children and other detached work live in the
session's scope, and a service restart leaves them alive as a kernel restart
does; before 2026-09-05 they were in the service's cgroup and died with it. The
CLI itself still ends: the kernel receives the service's SIGTERM and runs the
same drain. A scoped CLI outlives a service restart only when the drain does not
reach it: a kernel killed before its drain finishes (SIGKILL at the service's
stop timeout), or a CLI the drain could not find. The reaper handles that case:
at the next kernel boot, an SDK-driven CLI holding one of the kernel's sessions
whose parent is not a live romp kernel is treated as orphaned and terminated.
Under `systemd --user` an orphan re-parents to the user manager, not to pid 1,
so a ppid check alone would miss it and did, before 2026-09-05.

One-time caveat when this lands: the first service restart after it still
empties the current cgroup, tmux servers included, because the running manager
and its tmux server predate the change and are still inside the service's
cgroup. The guarantee holds from the following restart on.

`ROMP_CLI_SCOPE=0` in the service environment turns the scopes off, for
session CLIs and the tmux server alike. A manager run outside the service
(`romp up`) scopes nothing unless `ROMP_CLI_SCOPE=1` is set, which turns both
on. The kernel logs which it chose at start (`cli scope: on` or `off`, with the
reason); when the scopes were wanted on Linux and the box cannot provide them
(no `systemd-run`, or a user manager that refuses to start one), that verdict
also appears in the dashboard's error center, since every session then runs
inside the service cgroup. The macOS launchd path is unchanged: there is no cgroup kill there,
and the tmux server keeps its launchd lineage.

#### Per-session memory limits (opt-in)

A session's scope can carry a memory limit, so a runaway process is killed
inside its own session before a machine-wide OOM killer has to pick a victim. On
2026-09-06 a session's shell expanded a glob over a large `/tmp`, grew past 30
GB, and the machine's userspace OOM killer (earlyoom) killed the largest process
it saw at that instant: the romp kernel, which ended every session. No limit is
set by default; the size is the user's choice, per machine. The kernel reads
each of the variables below once at its start and hands it to the session's
scope wrapper, so, like the other service variables, a change takes effect at
the next manager restart. Four variables in the service environment
(`~/.config/romp/service.env`) opt in:

- `ROMP_CLI_SCOPE_MEMORY_MAX`: the hard limit (systemd `MemoryMax=`). Above it,
  the cgroup's OOM killer sends SIGKILL to the largest process in the scope and
  to nothing else (the wrapper's `OOMPolicy=continue`, below, confines the
  kill). When that is a tool's process, as in the incident, the session sees a
  failed tool call: the Bash tool reports the command killed (exit status 137),
  and the scope keeps running with the CLI in it. When the CLI is itself the
  largest process, it is the one killed, and the session is cut as after any CLI
  death. The kernel and the other sessions are untouched either way.
- `ROMP_CLI_SCOPE_MEMORY_HIGH`: the soft limit (`MemoryHigh=`). Above it, the
  scope is throttled and its memory reclaimed; the limit itself kills nothing.
  A throttled scope does raise memory pressure, and on a machine where
  `systemd-oomd` is set to act on the user manager's pressure (`systemctl show
  user@$(id -u).service -p ManagedOOMMemoryPressure` prints `kill`) it can kill
  the whole scope, `OOMPolicy=continue` notwithstanding; check that setting
  before relying on the soft limit alone.
- `ROMP_CLI_SCOPE_MEMORY_SWAP_MAX`: the swap limit (`MemorySwapMax=`). Without
  it, a scope at `MemoryMax` pushes pages to swap instead of being killed, until
  the machine's swap is used up, and the swapping slows every other process. On
  a machine with swap, set this too.
- `ROMP_CLI_SCOPE_OOM_SCORE_ADJ`: an integer from -1000 to 1000, written to the
  `oom_score_adj` of the process that becomes the CLI, before the CLI starts, on
  every path that starts one: a launch that falls back to a direct run, outside
  a scope, still carries it, since the write needs no scope. The CLI and
  everything it spawns inherit it; the kernel keeps its own. Linux's OOM killer
  and earlyoom rank processes by a score this value is added to, so a session
  with a raised value is chosen before the kernel when the whole machine runs
  out of memory. Raising the value needs no privilege. Lowering it below the
  user manager's own `oom_score_adj` needs privilege and is refused (see the
  note on `OOMScoreAdjust=` at the end of this section).

Sizes are an integer with an optional `K`, `M`, `G` or `T` suffix (powers of
1024, as systemd reads them) or `infinity`. The rule is narrower than systemd's
own size syntax: systemd takes `50%` (a share of the machine's memory), `1.5G`,
`16 G`, `16P` and `1G 512M` for `MemoryMax=`, and the rule refuses them all as
not a size, along with a lowercase suffix (`16g`). Each is dropped before it
reaches systemd, with the problem line described below; write `16G`. The
adjustment takes no leading zero: Linux reads `0400` as octal. A value that
fails its rule is dropped and reported, and the session still starts in its
scope with the other limits. The kernel checks the rules once at its start: a
value it refuses is a problem line (a kernel log entry that the dashboard's
error center also shows) naming the variable and the rule, and the wrapper
receives that variable empty, so the value is applied nowhere. The probe at the
kernel's start, described below, catches a value that passes the rule but that
systemd refuses (a size past its range; `OOMPolicy=` on a scope before systemd
253) and reports it the same way, quoting systemd.
The wrapper checks the same rule on every launch and reports a value it refuses
on stderr as `romp-cli-scope: ignored: …`, which the kernel logs as a problem
naming the session; on a launch the kernel drove, an `ignored:` line naming a
rule means the value reached the wrapper some other way.

The rules are syntax, and two kinds of value that pass them can still be refused
by the machine: a memory property this systemd does not take on a scope
(`OOMPolicy=` on scopes needs systemd 253), and an adjustment the process cannot
write, because it is below the user manager's own `oom_score_adj` or because
`/proc/self/oom_score_adj` cannot be opened for writing (a read-only `/proc` in
a hardened container). Without a check at the kernel's start, each would be
refused again on every launch, one `ignored:` line and one problem each, while
the kernel's boot line said the value was in force. So with the scopes on, the
kernel runs the wrapper's own steps once at its start: it starts a probe scope
carrying the memory properties, and has a throwaway child write the adjustment
to its own `oom_score_adj`. A refusal there is a problem line at the kernel's
start and reaches the wrapper as an empty variable, so no launch repeats it. The
adjustment's problem line quotes the shell and says which step failed: it names
the floor only when the file opened and the write was refused; otherwise it says
the file could not be opened, and why. The wrapper's `ignored:` line makes the
same distinction. A probe that does not answer (the user bus away at that
moment) settles nothing. The kernel says so in its log (a plain line, not a
problem), hands the values down as read, and lists them in its boot line as set
but not settled, naming the check; the values whose checks did answer keep their
own verdict in the same line, so an unanswered check for one value never makes
another unknown. Whether the values apply is then known from the wrapper's
report on each launch. The wrapper keeps the same guard on every launch. Its
pre-flight scope carries the properties. If that fails, it retries bare; if the
bare scope starts, it tries once more with the properties, and only that second
failure drops them, for that launch, with one `ignored:` line quoting the
failure that decided. (A bare failure is the fallback described above.) The CLI
then starts in its scope without the memory limits; the adjustment is still
written. On a launch the kernel drove, an `ignored:` line quoting a systemd
rejection means the machine changed under the running kernel.

Whenever a memory limit is set, the wrapper also sets `OOMPolicy=continue` on
the scope. A scope's default is `stop`: when Linux's OOM killer kills one
process in it, systemd stops the whole scope, which ends the CLI and every tmux
server and `setsid` job in it. With `continue`, only the killed process is gone.
systemd logs each kill to the user journal as `<unit>: A process of this unit
has been killed by the OOM killer` (`journalctl --user --since today | grep
'romp-session-'`).

The limits need the memory controller delegated to the systemd user manager;
stock systemd delegates it (`systemctl show user@$(id -u).service -p
DelegateControllers` lists `memory`). Without it, systemd accepts the
properties, reports them from `systemctl --user show`, and applies nothing; the
cases are an administrator's drop-in on `user@.service`, the legacy cgroup
hierarchy, a kernel booted with the controller off, and a container whose cgroup
subtree lacks it. The kernel checks for this at its start, inside the probe
scope above: the scope's cgroup has a `memory.max` file when, and only when, the
controller is there. A missing one is a problem line at the kernel's start. A
probe that exits non-zero or does not answer (its scope fails to start, it does
not finish, its command is killed or exits without a marker) is tried once more;
one that exits 0 without printing a marker is not. When no try gives a verdict,
that is a problem line too: it says what each try did (one try, or two) and
quotes systemd's refusal, the exit status, or what was printed; and the check is
left unsettled.
Whether the memory limits apply is then unknown until the next kernel start. To
check a live session, run from a shell inside it: `cat /sys/fs/cgroup$(cut -d:
-f3 /proc/self/cgroup)/memory.max` prints the limit in bytes, `max` when none
applies, and fails when the controller is not there.

A suggested starting point for a shared 64 GB machine:
`ROMP_CLI_SCOPE_MEMORY_MAX=16G`, `ROMP_CLI_SCOPE_MEMORY_HIGH=12G`,
`ROMP_CLI_SCOPE_MEMORY_SWAP_MAX=0`, `ROMP_CLI_SCOPE_OOM_SCORE_ADJ=500`. One
session can still take a quarter of the machine, more than any ordinary tool
call needs; the kernel (a few GB), the other sessions and the system keep the
rest. A session is throttled once it passes 12 GB and killed when it reaches 16
GB, without swapping first. An adjustment of 500 adds 500 points to each
session's OOM score, on a scale where 1000 points is the whole of the machine's
memory, so the machine-wide killers also choose a runaway session before the
kernel.

The limits cover what runs in the session's scope: the CLI, its tool shells,
their `setsid` children, and a private tmux server started directly from a tool
shell (`tmux -L <name>`). Two kinds of work are outside it. Work a session hands
to the server the manager started (`tmux new-session` on the default socket)
runs in that server's scope (`romp-tmux-*`), not the session's. And anything a
session starts as a transient unit of its own (`systemd-run --user --scope …`,
or a `systemd-run --user` service) is a sibling of the session's scope under the
user manager, outside its memory limits: a tmux server detached that way is
outside them, whereas the same server started with a plain `tmux -L` is inside.
A `--scope` job started that way still inherits the session's raised
`oom_score_adj` (`systemd-run` runs the command in place); a transient service
does not (the user manager spawns it, not the session).

`OOMScoreAdjust=` on the manager unit cannot separate the kernel from the
sessions, which is why the adjustment is a raised score on the session tree. A
user unit's `OOMScoreAdjust=` cannot go below the user manager's own
`oom_score_adj`: 100 on a typical machine, where the romp manager and the kernel
sit at 200, so a drop-in asking for -500 lands at 100. It can bring the manager
and the kernel down to that floor and no lower, and, with no session-side
adjustment set, the sessions follow, because the kernel spawns them and they
inherit its value: lowering the kernel's score lowers every session's by the
same amount. The raise on the session side separates the tiers: the wrapper
writes it in the session's own process, after the kernel has spawned it, so the
kernel keeps its own. None of this subsection applies on the launchd path.

## Kernel performance counters

`GET /perf` returns one JSON document of counters the kernel keeps at all
times: what its pusher, judge and HTTP threads have done since the process
started. The route takes the serve token. The counters cost a lock and a few
dictionary increments per event, so they stay on; nothing is formatted or
serialized until a request reads them. `romp perf` takes two snapshots
`--interval` seconds apart (default 10) and prints the difference as rates on
one screen: pusher cycles and wakes per second, cycle time percentiles, the
share of cycle time in each stage, CPU split between the pusher thread, the
judge threads and the rest of the process, builds served from cache against
rebuilds, bytes sent per slot as full frames, deltas and deduplicated frames,
goal-store loads and writes per second, judge passes and their durations,
memory and thread count. `romp perf --json` prints one raw snapshot. If the
kernel restarted between the two snapshots the counters have started over, so
the command says so and exits non-zero instead of printing negative rates; a
refused token is reported as such, not as a dead kernel.

The snapshot's fields, all plain numbers (`ms` is milliseconds of wall time):

- `now`, `since`, `uptime_s`, `log`: the clock, when the counters started,
  seconds since the process started, and whether the `romp-perf` log is on.
- `process`: `rss_kb` (resident set size in KB: the current size on Linux,
  read from `/proc`; the peak, `ru_maxrss`, on macOS, which has no `/proc`),
  `threads`, `cpu_s`, `pid`.
- `pusher`: `cycles`, `wakes` (every wake call; a burst of wakes runs one
  cycle), `wakes_event` and `wakes_backstop` (how the loop's wait ended),
  `cycle_ms_sum`, `cycle_ms_max` (since start), `cycle_ms_last`,
  `cycle_cpu_ms_sum` (the pusher thread's own CPU time), and `cycle_ms_p50`,
  `cycle_ms_p90`, `cycle_ms_ring_max`, `ring_n` from the last 256 cycles.
- `stages_ms`: `jobs` (the cycle's tick jobs outside the push), `push`, and
  inside it `push.chat`, `push.feed`, `push.timeline`, `push.send`. The
  `push.*` stages count every push, including the one a connecting page gets,
  so they can add up to more than `push`.
- `builds`: `chat`, `feed`, `timeline`, each with `cached`, `built`, `ms`.
- `sends`: `full`, `delta`, `deduped`, each a map from slot name (`chat`,
  `feed`, `bars`, `taborder`, ...) to `count` and `bytes`. A deduplicated frame
  was built and compared, then not sent.
- `goals`: `loads`, `saves`, `writes` on the goal stores. A save that would
  rewrite identical bytes is a save without a write.
- `judge`: `passes`, `ms_sum`, `ms_last`, `ms_mean` (wall time; a pass waits
  on model calls), `cpu_ms_sum` (CPU time of the judge tier threads and every
  per-session worker they run; the workers' share is `cpu_ms_workers`).
- `http`: request `count` and `ms` per `METHOD /path` for GET, POST, HEAD and
  OPTIONS, the query string removed and `/dist/*`, `/media/*` and
  `/remote/*/…` collapsed to one key each, for at most 256 keys; further keys
  fold into `other`. A WebSocket upgrade is counted when it arrives and not
  timed, since its handler runs for the life of the socket.

`POST /perf` with the body `{"log": true}` or `{"log": false}` turns the
`romp-perf` stderr log on or off in the running kernel (`romp perf log on|off`).
The log prints one line per chat build and per frame sent or deduplicated. It
goes where the manager's stderr goes: under systemd, `journalctl --user -u
romp-manager -f | grep romp-perf`; under launchd (macOS), `tail -f
~/.local/state/romp/manager.log | grep romp-perf`. Setting `ROMP_PERF=1` in the
kernel's environment still turns it on at start.

## Browser-side performance telemetry

The counters above say what the kernel spent. What the browser spent on the
frames it received is measured in the panes themselves, by
`ui/webview/perf-telemetry.ts`:

- The feed, Outline and chat bundles wrap their window `message` handler, so
  each frame's synchronous handling time is recorded by frame type: the
  frame's `type` string as it is (`feed`, `chatTail`, `session`, `tabOrder`,
  `bars`, or any other type that is a short identifier: letters, digits,
  `_ . : -`, at most 32 characters), a raw delta as `delta:<slot>`
  (`delta:other` when the slot is not such an identifier), a shell message (a
  `romp` field and no `type`) as `shell`, and `other` for a frame with
  neither, a `type` that is not a short identifier, or any type past the 32
  distinct types a minute the pane tracks; frames the handler ignores count
  too. The federation layer, which every kernel page loads, times its
  own prefixing, delta application and merge of each frame as `fed:<type>`,
  nested outside the pane's handler; each level records its own time, so
  `fed:feed` and `feed` add up to the frame's cost. The timeline's listener is
  wrapped the same way on both hosts (the VS Code bundle directly; the kernel
  page's inline boot through the `window.__rompPerf` that `federation.js`
  publishes before it runs), so `data`, `bars`, `hover`, `activeChat`,
  `revealEvent` and `models` are timed like any pane's frames.
- Per type and minute: count, summed and maximum handler time, the exact
  number of frames over 16.7 ms (one dropped frame at 60 Hz) and at or over
  100 ms, and a 14-bucket log2 histogram (under 1 ms, 1-2, 2-4, ..., 2048-4096,
  4096 and over) that is additive across minutes, so `romp perf client`
  computes window percentiles from it.
- Two `requestAnimationFrame` callbacks after the outermost handler it records
  how long the main thread stayed busy with the work the handler queued (a
  deferred render, layout, paint). A hidden document or a pane the shell has
  set to `display:none` takes no sample, and a sample armed before such a hide
  is cancelled (on `visibilitychange`, or on the `resize` that takes the
  pane's viewport to zero).
- A `PerformanceObserver` on `long-animation-frame` entries (Chrome 123+;
  `longtask` where that is missing, with no attribution) records each frame
  over 50 ms with its blocking time and the scripts the browser attributes it
  to. The browser names the top-level callback it invoked, not the hottest
  function, so a key is `<file>:<function>@<character position>`
  (`feed.js:render@1200`, `feed.js:(anonymous)@48213`), and an inline page
  script (the pane shim, whose socket callback runs for every frame) is
  `page:<function>@<position>`. The release build keeps identifiers, which
  keeps the function name in a key readable across rebuilds (the position
  still moves with any edit to the bundle); whitespace and syntax are still
  minified.
- Once a minute the pane posts ONE `clientDiag` row on the socket it already
  uses for breadcrumbs, only when something happened that minute (a frame
  arrived or a long frame was observed); the kernel appends it to
  `client-diag.jsonl` under the state directory with the dashboard id (`wid`)
  and its own clock. A frame whose whole synchronous handling ran 100 ms or
  more also posts a `slowframe` row at once, carrying the long-frame
  attribution when the browser reports one for that frame; at most five such
  rows a minute per pane, the rest counted in the minute row.
- The kernel rotates `client-diag.jsonl` once it reaches 8 MB: the file
  becomes `client-diag.jsonl.1` (replacing the previous one) and a new file
  starts, so at most two files, about 16 MB, are kept. A minute row is about
  1 KB, so one open dashboard writes a few MB a day.

Rows carry numbers and code identifiers only, never card text, session names,
file paths or transcript content: an element id inside an invoker name is
stripped (`DIV#tab-web.onclick` is recorded as `DIV.onclick`), an element
source as `[src]`, and a script URL as its basename.

The two rows, as the kernel writes them (`t` its clock, `wid` the dashboard id):

- `{"t", "wid", "surface": "perf", "what": "minute", "data": {app, since,
  span_ms, frames: {<type>: {n, ms_sum, ms_max, n16, n100, hist}}, free: {n,
  p50, p90, max} | null, loaf: {n, blocking_ms, worst_ms, top: [{k, ms, n,
  inv}], src}, slow: {sent, suppressed, suppressed_worst_ms}, heap_mb?, dom,
  visible, hidden_pane, ua}}`. `app` is the pane (`chat`, `feed`, `fleet`,
  `timeline`); `since` is the minute's start on the browser's clock (epoch ms)
  and `span_ms` its length (shorter than a minute when the page was
  hidden or closed); `hist` is the 14 bucket counts; `free` is null when no
  sample was taken; `loaf.top` is the five largest keys by summed duration,
  `inv` the last invoker seen for each (`WebSocket.onmessage`,
  `Window.requestAnimationFrame`, `DIV.onclick`), `src` is `loaf`, `longtask`
  or `none`; `slow` counts the slowframe rows sent and the slow frames past
  the cap, with the worst of those; `heap_mb` is
  `performance.memory.usedJSHeapSize` and is absent outside Chrome; `dom` is
  the element count; `visible` is the document's visibility, `hidden_pane`
  the zero-viewport test the pane shim uses for a pane the shell has set to
  `display:none`; `ua` is `chrome-desktop`, `safari-ios` or `other`.
- `{"t", "wid", "surface": "perf", "what": "slowframe", "data": {app, type, ms,
  dom, loaf?: {ms, blocking_ms, top: [{k, ms, inv}]}}}`. `type` is the frame
  as received on the wire and `ms` its whole synchronous handling, the
  federation layer included.

`romp perf client [--minutes <n>] [--json]` reads the file and its `.1`
predecessor (no kernel round trip, so it works with the kernel down) and folds
the last `<n>` minutes (default 10) per dashboard id and pane into one screen:
the pane's total handler milliseconds per minute, then each frame type sorted
by its share, with frames per minute, milliseconds per minute, p50/p90/p99 as
histogram bucket upper bounds over the whole window, the maximum, and the
share of frames over 16.7 ms and at or over 100 ms; the worst minute's
main-thread-free p90 (each minute row's p90 is over that minute's samples, and
the screen shows the largest, so it is not a window percentile like the handler
columns); long frames and blocking milliseconds per minute with the worst
entry; the top attributed keys with their invokers; the worst minute (the one
with the most handler time: its span from the minute's start to the row's
arrival at the kernel, frame counts and long frames); heap and DOM at the last
sample; and the five slowest slow frames in the window with their attribution,
plus how many more there were. An absent file or one without perf rows is
reported as no browser telemetry yet (the bundles predate it or no dashboard
has loaded them: rebuild the bundles and reload the dashboard); perf rows all
older than the window are reported with their age. `--json` prints the folded
panes, with a per-minute array (`t`, `since`, `total_ms`, `loaf_n`,
`blocking_ms`) so a spike is visible without re-reading the file.

In DevTools, `window.__rompPerf.snapshot()` in a pane's frame is the minute
in progress in the same shape, plus a derived `p90_le` per type, `active`
(whether it will be sent), `observer` (`loaf`, `longtask`, `none`),
`pending_slow` (slow frames waiting for their long-frame report) and
`free_pending` (a main-thread sample armed). A page without `performance.now`
(the node test stand-ins) gets no telemetry and an unwrapped handler; every
other browser API is behind a feature check, and nothing in the module throws
into the pane.

## Where things live

State is written under `${XDG_STATE_HOME:-~/.local/state}/romp/`. Transcripts
are read in place from where Claude Code writes them (`~/.claude/projects/`)
and never copied.

Three small files there hold settings you set by hand: `session-flags.json`
(per-session flags, the postal isolation switch among them), `session-order.json`
(the saved tab and lane order) and `notify-cards.json` (the bell overrides). A
change to one of them is refused, never written over an empty, when the file
exists but cannot be read; the refusal reaches the dashboard's error center
under the `not saved` kind, with the reason, and the same change can be tried
again. A file whose bytes cannot be parsed (a torn write) is moved aside, never
deleted, to `<file>.corrupt-<UTC stamp>` in the same directory (a `-1`, `-2`
suffix when two land in the same second), the store starts over empty, and an
entry under the same kind says so. A file that cannot be read at all keeps
showing its last-read values until it can. Nothing here needs a restart; the
sidecars are yours to inspect or delete.

Two small ledgers there, `auto-nudge.json` (the auto-nudge switch and its
per-goal records) and `retry-suppressed.json` (the sessions whose auto-retry
you stopped), are moved aside rather than overwritten when their bytes do not
parse: the file is renamed `<name>.corrupt-<UTC stamp>` beside the original
(`-1`, `-2`, ... when a second one lands in the same second), the dashboard's
error center says so, and the ledger reads as a fresh install until you
restore it from that file. Nothing is deleted. Any other read fault leaves the
file untouched: the kernel serves the last copy it read, writes nothing to it,
and says so once, until the file reads again. A write that fails (a full or
read-only disk) is told to the gesture that asked, the gear's toast or the
stop button's warning, and said once per fault episode in the error center;
the automatic pass sends nothing whose record could not land, and the file
keeps what it holds.

## Switches

Effective immediately, no restart.

`touch` to **disable**, `rm` to re-enable:

- `~/.claude/romp-postal-off`: the postal service

`touch` to **enable**, `rm` to turn back off:

- `~/.claude/romp-summarize-on`: the live tmux activity phrase. Off by default,
  because it spends tokens on every turn and the SDK backend reports what a
  session is doing without it.
