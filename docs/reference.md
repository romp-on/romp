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
| `romp status` | Manager and kernel status; a kernel stopped by `romp down` says so |
| `romp refresh` | Restart the postal bus and every kernel immediately, picking up new code (cut turns resume with their history) |
| `romp update [host…]` | Push this machine's committed Romp to attached remotes and restart them at once (every deploy restart is immediate; boot reconcile resumes the cut turns with their history); a remote stopped by `romp down` is synced and left stopped |
| `romp up` | Start the kernel: through the login service when one is installed, in the foreground otherwise. Clears a `romp down` marker |
| `romp down` | Stop the kernel and keep it stopped until `romp up`. Turns in flight get 5 seconds to finish first; sessions resume with their history at the next start. See [Stopping the kernel on purpose](#stopping-the-kernel-on-purpose) |
| `romp version` | Version report across the moving parts |
| `romp help` | The same list, from the terminal |

These are for scripting and for agents rather than daily use:

| Command | What it does |
|---|---|
| `romp url` | Print only the tokened dashboard URL, for piping |
| `romp sessions [--json]` | The fleet with each session's state, identity colours, directory and backend |
| `romp perf [--interval <s>] [--json]`, `romp perf log on\|off` | The kernel's performance counters as rates over two snapshots (below); `--json` prints one raw snapshot; `log on\|off` turns the `romp-perf` stderr log on or off without a restart |
| `romp perf client [--minutes <n>] [--json]` | What the open dashboards' browsers spent on the frames they received (below): handler milliseconds per minute by frame type with window p50/p90/p99 and max, the worst minute's main-thread-free p90, long animation frames and their attributed callbacks, the worst minute, heap and DOM, the slowest frames, per dashboard and pane over the last `<n>` minutes (default 10) |
| `romp api-health` | The API-health signal as JSON (see [The API-health signal](#the-api-health-signal)): per-credential, per-model-family retry and give-up rates over rolling windows, with a derived state |
| `romp restart-metrics [--json] [--window day\|week] [--anchor D] [--since D] [--until D] [--tz Z] [--no-live]` | What kernel restarts do to the sessions (see [Restart metrics](#restart-metrics)): turns cut per restart and per window, outage and reconcile times, quiet-window waits, orphans and reaps, crash heals, redo cost, turn latency, per-session and kernel memory and CPU; a text summary per window, or the whole document as JSON |
| `romp mail …` | The postal service from the shell (below) |
| `romp send <session> [--tag <label>] <text>` | Hand a session a message, on either backend. Anything a script, cron job, or launcher composes SHOULD carry a tag (one word, letters/digits/dashes, up to 24 chars): the chat then renders it as machine-sent under that label instead of as the user's typed words. Raw POST /send callers pass it as the JSON `tag` field (`{name, text, tag}`; a malformed tag fails the whole send, loudly); `--tag` is the CLI's equivalent. Both resolve to the `<!-- romp-tag: <label> -->` marker in the delivered text |
| `romp new --env NAME=VALUE <name>` | A per-session env var for the SDK session, repeatable; a re-run against a running `<name>` replaces the whole set; vars not re-named are dropped |
| `romp new --no-env <name>` | Clear a running SDK session's per-session env (declares the empty set) |
| `romp new --in <tag> <name>` | Put the new SDK or Codex session in `<tag>`, so its tab lands in that group (repeatable; a name that does not exist yet creates the tag). Applies to `<name>` if it already runs. The kernel echoes `tags` (the session's tags) and, per `--in`, the stored name it landed as (`tagsApplied`, beside `tagsRequested`): a name the store trimmed or clamped prints as "applied as"; a missing echo, or a tag the kernel refused, prints a warning |
| `romp new --no-inherit <name>` | Run inside a romp session, `romp new` sends that session's stable id (`ROMP_SID`) as the new session's `parent` (marked `parentAuto`), and the kernel copies the parent's tags onto the child; inside a comment thread, the parent is the session the thread belongs to. This flag withholds the parent, so the new session starts outside them. A kernel that never ran the calling session creates the session untagged and echoes `parentIgnored`, which the CLI reports in one line. Raw POST /new callers pass `parent` (a live name or a known sid; an unknown one is a 400 unless `parentAuto` is set) and `tags` (a list of names); opening a name that already runs never inherits; a name that is being registered by another request right now is a 409 whose `error` says which door holds it |
| `romp tag [<name>] [--add <session>…] [--remove <session>…] [--color <hex>] [--rename <new>] [--delete] [--host <kernel>]` | Session tags. Bare, it lists them; with a name, it merges one tag (created on first use). A tagged session leaves the untagged view, and its tab sits in that tag's section of the strip. `--host` edits an attached kernel's tag |
| `romp interrupt <session>` | Interrupt whatever turn a session is taking |
| `romp compact <session> [--wait] [--timeout <s>]` | Compact a session's context in place (Claude's `/compact`: summarize the history, keep the session's name, id, mailbox, and watches): the alternative to ending and recreating a long-lived session, and the external hand a session needs since it cannot `/compact` itself mid-turn. Quiet session → compacts now; open turn → queued, fires alone the moment the turn ends (the same safe path the chat's compact button uses). `--wait` blocks until the compaction has started and cleared, polling the kernel's own `compacting` signal on the `/sessions` rows (also the field to point a `romp watch` predicate at for scripted recycling); exits 1 honestly on timeout. A remote session's compaction is requested on its own kernel; `--wait` can't follow it from here and says so |
| `romp end <session>` | End a session |
| `romp move <session> <dir>` | Move a session's working directory to `<dir>` (the folder must already exist); the conversation, name, mail and history stay with the session. Quiet session → moves now; open turn → queued, fires when the turn ends. See [Moving a session to another folder](#moving-a-session-to-another-folder) |
| `romp checkin <host>` / `romp checkout <host>` | Publish this machine to an attached hub, or withdraw it. The hub files this machine under the name it declares only when that name is a machine name (letters, digits, dots, hyphens or underscores, starting with a letter or digit, at most 128 characters). Any other declared name is refused with a 400 that states the rule and echoes nothing, is recorded nowhere, and is said once on both machines: on the hub, one stderr line and one Log entry under the `refused` kind, naming the value as a clipped repr; on this machine, one stderr line, one dial-log record and one Log entry carrying the hub's reason, after which the same name is not re-sent until it, or the hub's kernel, changes. A hub's `POST /tunnels/trust` for a host it has never seen (the remembered-hosts entry that tiers relayed mail by origin) holds the wider rule that registry's writers share, a machine name or an ssh alias (letters, digits, dots, hyphens, underscores, at-signs, colons or square brackets, not starting with a hyphen, at most 255 characters), because a hub keys an attached peer by its ssh alias and carries that alias when you set trust between two of your machines; anything else is refused the same way, on the hub, with nothing recorded. `ROMP_HOST_NAME` (the kernel) and `ROMP_POSTAL_HOST` (the postal bus) override the declared name only when they clear the same rule; an unusable value (a space, an at-sign, a trailing newline) is set aside once, on stderr or in the bus log, and the derived name (the short hostname, else the platform's machine name, else a minted id) is used |
| `romp default-dir [PATH]` | The default working directory for new sessions; no argument prints it, `""` clears it |
| `romp debug [on\|off\|status]` | Judge debug mode, where rejection rows carry the full input and reply |
| `romp refresh --quiet` | Refresh at the next quiet window instead — waits for sessions to finish their turns (15-min backstop). The ONLY door to the quiet window: a deploy (a peer's `romp update`, a release self-update, an automatic converge) restarts immediately, by the user's 2026-09-08 decision |
| `romp down --wait <s>`, `romp down --now` | How long `romp down` waits for turns in flight to finish (0 to 600 seconds; default 5), or no wait at all |
| `romp up --foreground` | Run the manager in this terminal even with a login service installed (its log in front of you); the manager refuses to start beside a running one |

Raw `POST` callers, anything that talks to the kernel's routes directly rather
than through `romp`, follow one body contract, and the postal bus's own routes
share it. The request carries the serve token (`X-Romp-Token`, or `?token=`)
and is authorized before its body is read. The body is delimited by
`Content-Length` alone: no `Transfer-Encoding` (411), the header once and a
plain decimal (400 otherwise), and at most 1 MiB (413 beyond that, refused
before a byte is read). A body that arrives short of its announced length is
400, one that stalls for 30 seconds is 408, and every refusal closes the
connection. The body is a JSON object; an array, string, number or `null` is a
400 naming what arrived, echoed bounded and well formed. A flag field
(`delete`, `on`, `mkdir`, `tracked`, and the like) is a JSON boolean: `true`
and `false` apply, an absent field or an explicit `null` reads as the route's
default, and anything else (the string `"false"`, `0`, `1`) is a 400 naming the
field, with nothing acted on.

Three routes exist for the Obsidian timeline panel, which has the state
directory but no socket to the kernel: `POST /flag` (`{id, flag, value}`: one
of the lane gear's toggles, `hideFromFeed`, `postalServiceOff` or `notify`,
with a JSON boolean; here `value` is required, and an absent or `null` value is
a 400, never a default, since a missing value must not read as "off"), `POST
/views` (`{views, edited?, writeId?}`: the whole views blob, judged as the
dashboards' write is and answered with the same `viewsAck` document, `ok`, the
post-write `views` and `seq`, any `refused` tags and an `error` line), and
`POST /order` (`{order: [sid, …]}`, merged into the saved order so lanes the
drag did not carry keep their slots). Each lands through the setter its socket
op uses; a store that cannot be read or written answers 200 with `ok:false` and
the reason the dashboards see, and an unknown key or a wrong type is a 400
naming it. The panel finds the kernel through the `serve-port` record the
kernel writes beside `serve-token` in the state directory once its socket is
bound; with a record nothing answers on, the panel refuses the gesture and says
the kernel is not running rather than writing a file the kernel cannot check.
With no record at all (a kernel older than the panel wrote the token and no
port) it tries the port the command line resolves, `ROMP_KERNEL_PORT`, then
`ROMP_SERVE_PORT`, else `29855`, and its refusal says so when nothing answers
there. Record or fallback, the panel first asks the port to prove itself: a
`GET /healthz` with no token, on `127.0.0.1` only, must answer `200 ok` with
the kernel's `X-Romp-Boot` identity before the token is sent, and a port that
answers as anything else is refused by name and never sees the token.

`--env` gives one session its own environment, so two sessions in the same
directory can run with different toggles (a `FEATURE_FLAG=1`, a `CLAUDE_CODE_*`
switch) without editing the directory's `.claude/settings*.json`, which reaches
every session there and outlives them all. Re-running `romp new --env` against
a running session declares its full per-session env: any var you don't name
again is dropped, and `romp new --no-env <name>` declares the empty set, which
clears them all. Keep secrets out of it: each value is copied into
per-session files and the session registry under `~/.local/state/romp/`. A
credential never goes in `--env`, and never in `service.env` either: a payload
naming `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN`
is refused outright. A session's credential is Claude Code's own resolution,
the `apiKeyHelper` in its settings for a key and the login otherwise; see
[Service environment and credentials](#service-environment-and-credentials).

Two things to know before building on `romp sessions --json`. **`waiting` means
at rest**, the ordinary state of a session that has finished its turn, so
matching it as an alert badges the whole idle fleet as needing you; the states
that want a person are `permission` and `picker` (a live prompt) and `blocked`.
(`romp sessions` emits the RAW backend states; the dashboard's chip states,
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
<dir>`. The folder must already exist.

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
romp mail remote                 # legacy singleton scheme only (ROMP_POSTAL_PEERS=0): connect this remote machine to your laptop's bus; peer mode refuses
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

Not the same tools: romp peers are discovered only through the postal service's `list_agents`. Claude Code also ships its own `ListAgents` and `SendMessage` tools, which list the account's Anthropic cloud sessions and this session's own subagents: a different system, and a cloud session in that list is easy to mistake for a romp peer (the user 2026-09-08, who found one there that read like a session of theirs). The recommended setting is `"permissions": { "deny": ["ListAgents"] }` in the Claude Code settings, so the only list of agents a session sees is romp's; `SendMessage` must stay allowed, because continuing a subagent uses it.

### When a send is refused

A send whose record cannot be written, or that cannot be placed in the
recipient's inbox, is refused: the bus answers `503` with `ok: false` and the
reason, nothing is delivered and nothing is recorded, and the sender still
holds the text to retry. Two outcomes are not refusals, because the message is
already in the recipient's hands: the recipient read it in the instant before
its record failed, or the bus could not take it back out of the inbox. The
send then answers the id, and the bus says on stderr and on the dashboard that
the message log has no record of that message. A bus stopped between placing a
message and recording it writes the missing record from the message's own
headers at its next start. `check_sent` and `romp mail sent`
show a message the bus had to give up on later (a cross-host record it could
not write, a file it could not read, a write a restart found unfinished) as
`bounced`, marked `refused` with the reason; a peer's refusal that did come
back as a note still reads `undeliverable, returned to you`. A message file the bus cannot read, in a
recipient's inbox or in the cross-host outbox, is moved aside once (see the
state files below), its sender's receipt reads refused, and the dashboard's
error center says so under the `refused` kind.

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

### The file viewer's per-browser choices

The file viewer keeps two choices in the browser's own storage, not on the
kernel, so they survive a kernel restart and apply wherever the viewer opens
(over the chat or the feed, and for a document opened from a link on the
dashboard's own address): the Rendered or Raw view of a markdown file
(`romp:fileviewFmt`) and the text size (`romp:fileviewTextSize`, one of 70,
80, 90, 100, 115, 130, 150, 175 or 200 percent, set by the **A−** / **A+**
buttons or Ctrl/Cmd + wheel over the text). The size scales the prose, its
headings, the code and the Raw view together, and the prose measure with
them; a value outside the table reads as 100.

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

A Claude Code session switches model live but reloads to apply a new effort:
the chat shows "Reloading session…" and the effort badge shows switching-dots
until the reload completes, and a session that is mid-turn reloads when the
turn ends.

### Fast mode, from the chat statusline

The statusline's badges (permission mode, model, effort) are each a small
dropdown. A fourth appears when the session reports Claude Code's fast-mode
state (an Opus-only research preview, billed at a premium): it reads **Fast**
in orange while fast mode is on, **Slow** while it's off, and **Cooldown**
while fast requests are rate-limited. Picking On or Off sends the CLI's own
`/fast` command; the badge never appears on a session that cannot run fast
mode. Turning it on while the session is on a non-Opus model makes the CLI
switch to a fast-capable one, which the chat shows as the command's own
confirmation. If the CLI refuses the toggle (for example, the account has
extra usage turned off), a toast says why and the pick reverts to off;
the control never silently disappears.

### Per-session billing (login vs API key)

An SDK session bills either the machine's Claude login (subscription usage) or
the API key, chosen per session. The key is Claude Code's own: the CLI runs the
`apiKeyHelper` configured in its settings (the helper; setup under [A key from
a secret manager](#a-key-from-a-secret-manager)) and holds what it prints.
Romp holds no key and passes none to a session (the user 2026-09-08, who wants
romp to hold no key). The per-session pick decides only whether the helper
runs for that session.

The new-session picker's **Billing** row states the case whenever the backend
toggle says Claude Code: segmented buttons when the selected host offers both choices,
and with only one real choice, the same spot writes out which applies,
`Login (name@example.com)` or `API key`. The key choice exists when Claude
Code's settings for the kernel's working directory carry a helper; romp reads
the setting and never runs it for this. A live session's tab menu carries a
**Billing** submenu that lists BOTH choices on every box (since 2026-09-08; it
used to exist only when both were real): the choice this machine cannot bill
is greyed and inert, with the reason in its hover, `no Claude login signed in
on this machine`, `no apiKeyHelper configured`, or `the apiKeyHelper is set in
managed settings, login cannot apply`. The status payload carries the same
availability as `authAvail` (`authBoth` rides beside it for older clients),
and the machine's default beside it. The flyout opens on hover over the
Billing row, as the Tags flyout does (one gesture: a short hover opens, a
click opens at once, leaving both the row and the flyout closes it), and on
click. Switching reconnects the session to apply, with the same switching-dots
the effort badge wears.

Below the session's choices the flyout carries **Default for this machine**:
the same choices as a radio group, the current default marked. That default is
the seed every new session, and every session with no pick of its own, launches
on; it lives in the state root's `sdk-defaults.json` as `auth` (never a token),
and a pick there changes no session that carries its own pick; a session
with no pick of its own follows it, in its status at once and at its next
launch. A third choice, Automatic, is the rule that held before: the API key
when a helper is configured, else the login; it clears the explicit default,
and the group's sub-line says which rule holds. Until the default is set here,
the last per-session pick seeds it (as a model or effort pick does); once set
here, a per-session pick is about that session alone and moves no default. A
remote session's flyout names its host, and the pick sets that host's default
(the op routes to the session's owning kernel). The judges follow the same
resolution: a judge on a session with no pick of its own bills the machine's
default when the machine can bill it, else the helper rule, exactly as the
launch does. The flyout places itself to the right of its row, to the left
when the right would clip and the left has room, below the row when neither
side has room, above it when below does not fit, and only then clamped inside
the window; it never covers its row while a place beside or beyond it exists.

On a one-auth box the picker never chooses the missing side. The remembered
default falls to the side that exists, in both directions: a remembered login
pick on a machine with no login seeds new sessions on the API key, exactly as
a remembered key pick on a helper-less machine already fell to the login, and
the fall is said once per process as a problem row. An explicit pick that
names the missing side (a session picked "login" on a box that later lost its
login) launches on the other side when one exists and says so once per session
start, on the tab menu's Billing sub-line as `⚠ login unavailable, billing API key`
and in the log; the fall itself rides the status as `authPickFell`, so the hover
and the sub-line never infer one. A pick with nothing to fall to (a box with
neither side) launches as picked and the CLI decides; the sub-line then says
the side is unavailable and claims no fall. A side whose availability cannot
be read just now (the operator's settings file, or `~/.claude.json`, mid-rewrite
or unreadable) is cannot-tell: the launch keeps the pick as is, says so once
per session, and never falls on a read failure. `setAuth` refuses the
missing side with that same reason in the toast.

A pick reaches the CLI through the session's per-session settings layer, the
file the SDK hands the CLI as its `--settings` argument. A login pick writes
`"apiKeyHelper": ""` into that file: the layer outranks the settings files for
the same key, and the empty string disables the helper for that one process
(verified on Claude Code 2.1.257), so the session authenticates with the login.
A key pick, or no pick, writes nothing about the helper; the CLI runs it and
the session bills the key. On a box with a helper, every session without a
login pick therefore bills the key. Login tokens the kernel finds in its own
environment at startup (`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`) are
claimed at boot, so a key-billed session never inherits one, and handed back to
login-billed launches.

The login is named by its account (the email the credential store records);
the key option is labelled plainly `API key`. No fragment of the key, not even
a last-4 tail, ever reaches a browser or a screen, and romp never sees the key
at all. A new session defaults to the last pick made anywhere, and before any
pick to the key when a helper is configured. A remembered key pick on a box
whose settings carry no helper leaves new sessions unpicked, and the kernel
log says so once, naming the settings file to configure.

A tab not yet loaded after a reconnect shows "Not loaded yet — click to load"
as its hover tooltip, until its transcript arrives.

A Claude Code session's chat tab carries the same fact as a `Billing` row in its
hover tooltip, one-auth machines included; Codex sessions, which bill no Claude
account, show no row. The row has four readings. Unless one of the three cases below applies, it reads
`API key` or `Login (name@example.com)` (`Login` alone when the account name is
unknown). While a switch is still reconnecting the session, the row appends
`(applying — not confirmed yet)` to the side: `Login (applying — not confirmed
yet)`. A pick naming a side this machine cannot bill leads with the warning,
the reason, and the side the launch fell to: `⚠ Login picked, but no Claude
login signed in on this machine — this session bills the API key`; with
nothing to fall to, the tail says the launch went out as picked. A pick the
CLI's own report contradicts (a login pick whose CLI reports a key, a key pick
whose CLI landed on the login) leads with the warning too: `⚠ Login picked, but
the CLI reports the API key — this session bills that`, and, for a key pick,
the same with the sides swapped. The tab menu's Billing sub-line says the same
in fewer words: `API key` or `Login (name@example.com)`, `applying…`, `⚠ login
unavailable, billing API key`, and `⚠ CLI reports API key`.

Failures are loud rather than silent: a session that lands on the other auth
than it was launched for is flagged in the Log panel, and a dead credential
("Not logged in", an invalid or expired key) blocks the session's card with the
fix named, and is never auto-retried.

The auth check compares each session's landing against a declaration of the
box's design. `ROMP_EXPECTED_AUTH=key` (or `login`) in `service.env` (the
declaration) says which side the box's sessions are meant to bill: a session
landing on the declared side is quiet, and one landing on the other side is
flagged, naming the declaration. On a box with a helper every session without
a login pick bills the key, so `ROMP_EXPECTED_AUTH=key` describes such a box
truthfully. An undeclared box (the variable unset, or any other value)
compares each landing against what that session was launched for and stays
quiet when they agree. One explicit gear **Billing** pick supersedes the
declaration from then on: the remembered pick becomes the box's expectation
and the env var goes inert (it described the unpicked design), so re-seeded
spawns are judged against your pick, never against stale doctrine.

The kernel also checks, once at boot and before anything is spawned, that no
retired key path is still configured. A `service.env` that still carries a key
line from an earlier romp, a provider marker beside it, or a kernel
environment carrying one of the retired names stops the kernel with a message
that names the file and the variable names, never a value; the names and the
fix are under [Service environment and
credentials](#service-environment-and-credentials). Romp injects no credential
into any launch, so Claude Code's own resolution decides every landing, and
the per-init check above confirms each one.

The usage rail reflects a mixed machine: the window bars (5 hours / 7 days /
Fable 5) are drawn once, aggregated across every connected host's login as the
worst reading per window, and an `API` cell beside them carries the
key-billed dollars (5-hour burn and month-to-date, numbers only). Hovering
breaks both down per host, one column per host, side by side, and a host
can show its login's windows and its key's spend together. A click on the
readout opens the spend detail: a chart of spend over time stacked by session,
and under it the list of sessions with their dollars, turns and tokens. The
list follows the chart's range (one day by hour, seven days by hour, ninety
days by day): its rows are summed from exactly the buckets the chart draws, so
the list's total is the chart's total for every range, the header names the
range, and a session with nothing in the range has no row and no stack. An
attached machine on an older build sends its series without turns or
key-billed dollars per bucket: its rows show a dash in those columns, never a
zero that would read as a count, and a note under the list names the machine
on the ranges where such a row shows. The
key-billed dollars come from the sessions whose CLI reported a key source at init, judged
against the declaration; a login turn's computed cost is dollars nobody pays
and is left out.

The token count beside the dollars is every kind together: fresh input,
output, cache writes, and cache reads. Cache reads are most of it: every API
call within a turn (one per tool step) re-reads the whole context from the
cache, so a long session's single turn can read tens of millions of tokens at
a tenth of the input price. The hover splits each window's count by kind, so
the size of the number carries its explanation. A result that carries no
per-model usage map is counted from the main loop alone, and the error center
says so once: once per session when the CLI left the map out, once per kernel
run when the Agent SDK the kernel imported has no field for it.

### Several Claude logins

A machine holds one Claude login at a time, and Claude Code's `/login` replaces
it. A SECOND login (an enterprise account beside a personal one, or a
colleague's) is set up in one command:

    romp-login-setup <1password-vault> <label>

It needs the stored-logins change deployed (`romp login add`); on a romp
without it the command stops before any sign-in and says so. The label names
the 1Password item and romp's reference to it, so it may use letters, digits,
spaces, dashes and underscores only; anything else is refused before the
sign-in. Sign in when the browser opens, choosing the account and organisation
the token should bill. The command signs in under a scratch configuration (the
machine's own login is untouched), mints a `claude setup-token` (a one-year
credential), stores it in 1Password as an API Credential item titled with the
label (field `credential`, written through a template file rather than a
command argument), and registers the login with the running romp as
`romp login add <label> --op op://<vault>/<label>/credential`. On screen the
CLI's one line that carries the token reads `<token captured>`; the token
itself goes to one private directory under a temporary directory (the CLI's
captured output, the token file and the 1Password template, all removed when
the command ends, on every exit path) and to the 1Password item. Each step
fails loudly and stops the ones after it. The script is about twenty lines and
meant to be read before it is run. `--check`, accepted anywhere on the line
and off by default, then proves the stored token is what Claude Code accepts:
one request through an `apiKeyHelper` that reads the item, with the
environment's credential variables removed (the machine's own settings files
and keychain still apply), must be accepted, and the same request through a
helper that returns junk must be refused, which shows the helper, and nothing
else on the machine, is what authenticated.

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
- `ROMP_NO_SDK=1` skips the Agent SDK venv. Claude Code sessions need it, so
  run `bin/romp-sdk-setup` before starting one.

For the one-line installer (`bootstrap.sh`), which passes all of the above
through to `install.sh`:

- `ROMP_DIR=<path>` where to clone; default `~/romp`.
- `ROMP_REF=<tag|branch>` install a specific ref; default is the newest
  `vMAJOR.MINOR.PATCH` release tag (prerelease-suffixed tags are skipped),
  falling back to `main` when none is published.
- `ROMP_NO_PATH=1` leaves your shell rc alone.

### Judge concurrency

- `ROMP_JUDGE_CONCURRENCY=<1..16>` sets how many judge calls run at once,
  across every tier; the default is 6. The judges read it once, when they
  load, so set it where the kernel's service sees it (`service.env`, then a
  restart). A value outside the range is applied at the nearer bound; a value
  that is not an integer is ignored, with one line on the kernel's stderr. The
  same knob is a kernel setting, **Judge concurrency**, the last row of the
  gear's Judges section below the model and effort picks: a pick there
  applies on the judges' next pass with no restart, wins over the variable,
  and follows to every connected machine like the other judge settings; its
  Default option clears the setting back to the variable, else 6.

### Fast mode for the judges

- **Fast mode** (a checkbox beside each of the gear's judge model pickers:
  Triage, Distilling, Indexing; off by default) runs that tier's judges in
  Claude Code's fast mode, the same Opus-only research preview the chat
  statusline's Fast badge toggles for a session, billed at a premium (about
  twice the standard Opus rate). One flag per tier: the setting is read per
  call, for the tier the call runs in, and a call whose tier is on and whose
  model is Opus, by the bare alias or a pinned Opus version, carries the CLI's
  fast-mode opt-in in its per-call settings; every other call runs exactly as
  before. The gear says so per tier: when a tier's effective model cannot run
  fast (a Distilling pick of Follow triage takes the triage model), its box is
  greyed and its hint names the reason; the value is kept, not cleared, so
  pinning Opus for the tier later brings the box back live with no second
  click. An install that had the earlier single box on gets the same behaviour
  once: on its first start the kernel turns the new tiers' flags on where the
  tier's model can run fast and off where it cannot. Fast requests draw on fast
  mode's own rate limits, the pool your sessions' fast toggles share. Whether
  fast engaged is the CLI's answer, per account (an account with extra usage
  turned off, or an organisation with fast mode disabled, reports it off with
  the setting on): each row of `judge-usage.jsonl` keeps that answer in its
  `fast` field (`on`, `off` or `cooldown`; `null` when the CLI reported none)
  and the CLI's reason in `fastReason`, so a checkbox that reads on beside rows
  that read off names the account, not the setting. A declined ask is loud: the
  kernel records it per tier (`STATE/fast-refused.json`), the tier's box hint
  names the reason, and `judge-errors.jsonl` gets one `fast-refused` row per
  change of reason (never one per call); the next fast call that engages clears
  the record. A key-billed judge call that asks for fast carries the same
  org-check switch a key-billed session gets (the CLI's own probe would ask the
  saved login, not the paying account), asked once per kernel start and again
  after any refusal the CLI reports. The cost view needs no fast price
  table: the CLI's own per-call cost, which every usage row carries, already
  includes the fast premium (measured: the same prompt costs twice as much
  fast), so a fast row is priced at the fast rate. Like
  the other judge settings, a change applies on the judges' next pass with no
  restart and follows to every connected machine.

### Session backends

A session runs on one of two backends, chosen when it is created: **Claude
Code** (the default; the kernel runs the session through the Claude Agent SDK)
or **Codex** (see [Codex sessions](codex.md)). The gear's Default backend
setting picks the default for new sessions, and the two read as **Claude Code**
and **Codex** everywhere Romp names a backend. Raw `POST /new` callers pass the
backend as `sdk` (Claude Code, the default when the field is absent) or
`codex`; any other value is refused with `ok: false` and an error naming the
two. Every session's tab menu offers Move to folder.

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

### The kernel's Python

The kernel and its Agent SDK venv (`sdkvenv` under the state directory) must
run the same Python: the venv's compiled extensions import into the kernel
process. The match is on the tag venv names its `lib` directory with (`3.14`,
or `3.14t` for a free-threaded build), not on the version alone, so a
free-threaded build's venv matches that build and no other. `bin/romp-serve`
picks the interpreter in this order: `ROMP_PYTHON` if set, refused with one
line when it is not an executable interpreter (a pin naming a removed path
used to reach the exec and crash-loop the manager); otherwise the interpreter
the venv's `pyvenv.cfg` records, if it still runs and still reports the venv's
tag, the recorded X.Y plus the build its `lib` directory names (an upgrade
that repoints `python3` leaves the recorded path runnable while the venv is
stale); otherwise another interpreter of that same minor and the same build on
`PATH` or in `~/.local/bin`, which the venv still matches, with a line saying
so (`python3.14t` and then `python3.14` for a free-threaded venv; the build is
read from `sys.abiflags`, not from the file name, because uv's free-threaded
install links `python3.14` to `python3.14t`); otherwise the newest `pythonX.Y`
on `PATH` or in `~/.local/bin`, the rule for a machine with no venv yet, with a
line saying the venv must be rebuilt for it. So installing a newer Python does
not change what the kernel runs at its next restart. On a machine that runs
romp as a service, pin it anyway: `ROMP_PYTHON=/usr/bin/python3.12` in
`service.env` makes the choice explicit and holds if the venv is deleted or
rebuilt. Pin the versioned path, not `python3`, which an upgrade repoints.

Moving romp to another Python, whether another version or the free-threaded
build of the same one, takes four steps, and skipping any one of them leaves a
kernel that cannot start sessions: set `ROMP_PYTHON` to the new interpreter in
`service.env`, run `bin/romp-sdk-setup` with the same value, run the test
suite on that interpreter, then restart the manager. The setup script compares
the venv's record (the version `pyvenv.cfg` holds plus the tag of its
`lib/python3.X` directory, never the venv's own `bin/python`, a symlink that
follows a repointed base interpreter) against the new interpreter's tag,
rebuilds on any difference and says from what to what. A kernel that does come
up on a Python the venv was not built for logs one line naming both tags, and
each SDK session reports the mismatch and the remedy that fits: the
`ROMP_PYTHON` pin when the venv's recorded interpreter still runs as the
venv's python (the kernel runs it and reads its version and build), the
rebuild otherwise. `romp new` and the browser's create refuse with the same
verdict, read from the disk at the moment of the request, so a venv rebuilt
while the kernel runs is reported on both surfaces as set up after romp
started, with the restart as the remedy. The Codex venv (`codexvenv`, built by
`bin/romp-codex-setup`) follows the same pick and the same rebuild check, and
the kernel adds only the site-packages built for its own tag from it as well.

### Service environment and credentials

The manager runs as a login service (launchd on macOS, systemd --user on
Linux), so it does not receive variables exported by your shell rc. Configure
the service in `~/.config/romp/service.env` using plain `KEY=VALUE` lines and
owner-only permissions (`chmod 600`). The file carries the billing declaration
(`ROMP_EXPECTED_AUTH`, below) and the service knobs (the ports, the CLI scopes
and their memory limits, the perf log), never a key. The service reads the file
at manager startup, so a change needs a manager restart. `ROMP_SERVICE_ENV_FILE`
overrides the file's path.

The installed unit also sets `MALLOC_ARENA_MAX=2` for the manager and every kernel it spawns (2026-09-11): the kernel is a many-threaded Python process that rebuilds large record lists, and the allocator's per-thread arenas kept hundreds of megabytes of freed memory between restarts; two arenas return it. A line in `service.env` overrides it.

Romp holds no API key (the user 2026-09-08, who wants romp to hold no key). A
session's credential is Claude Code's own resolution: the `apiKeyHelper` in its
settings (the helper) for a key, the login otherwise. Romp injects no credential
into a session or a judge child, runs no key command, reads no
secret-manager reference, and keeps no key in `service.env`.

A retired key path stops the kernel at boot. A `service.env` that still carries
`ROMP_API_KEY_CMD`, `ROMP_API_KEY_REF` or `ANTHROPIC_API_KEY`, or one of the
1Password CLI's names (`OP_SERVICE_ACCOUNT_TOKEN`, `OP_CONNECT_HOST`,
`OP_CONNECT_TOKEN`, `OP_ACCOUNT`, `OP_SESSION_*`: romp no longer runs `op`, and a
helper that needs that token reads it from a file of its own), a
`service.env.source` marker beside it, or a kernel environment that carries one
of those names at boot is a boot failure: the kernel stops before anything is
spawned, and the message names the file and the variable names, never a value,
says that romp did not start, and gives the fix (remove the lines, configure
the helper, declare the billing, start again). The supervised manager retries
and writes the message to its `manager.log` each time until the file is
repaired. The manager refuses in the same way when its own environment carries
one of the names (it is what receives `service.env`). A key romp holds is a key
a session can print, so there is no quiet fallback anywhere.

At boot the kernel also names, once and as information rather than a problem,
the variables in its own environment shaped like credentials (names ending
`_API_KEY` or `_TOKEN`, and 1Password's own `OP_*` names) that reach every
session's Claude process and the shells it spawns: the SDK hands each session
the kernel's environment, and romp takes only the login tokens it claims at
boot (see [The login](#the-login)) out of it. The line carries names only,
never values, and a second provider's key placed there on purpose is nothing
to act on. To keep a variable away from sessions, remove it from `service.env`
or from the service unit's environment and restart the manager.

#### A key from a secret manager

Claude Code's own credential resolution is the only key path. Point Claude
Code's [`apiKeyHelper`](https://code.claude.com/docs/en/settings-reference#apikeyhelper)
at your secret manager: the CLI runs the helper, holds what it prints, and
re-runs it after `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` (five minutes by default)
and on a 401 or 403. Every session and every key-billed judge call runs the
helper inside its own Claude Code process. Romp never sees the key: the
Billing picker and the tooltip row read the setting to know that a key exists,
and no surface of romp's fetches it.

1. Write a script that prints the key from your secret manager, and make it
   executable. The script fetches its own credential: the CLI runs the helper
   as a child of the service, with no desktop app to unlock, so the secret
   manager needs a credential that works unattended, and that credential
   belongs in a `chmod 600` file the script reads, not in any environment.
   With 1Password that is a
   [service account](https://developer.1password.com/docs/service-accounts/)
   with read access to the one vault and nothing else:

        #!/bin/sh
        # ~/.config/romp/fetch-api-key (chmod 700): print the API key, nothing else
        OP_SERVICE_ACCOUNT_TOKEN="$(cat ~/.config/op/service-account-token)" \
            exec op read --no-newline "op://vault/item/field"

    Any secret manager's CLI works the same way (`aws secretsmanager
    get-secret-value --query SecretString --output text`, `vault kv get
    -field=…`, `bw get password …`, `gcloud secrets versions access latest
    --secret=…`, `pass show …`): one line on stdout, exit 0. The CLI must be on
    the service's PATH. The service installer records PATH at install time, so
    run `romp-service install` again after changing it.

2. Point Claude Code at the script in `~/.claude/settings.json`:

        { "apiKeyHelper": "/path/to/fetch-api-key" }

    Claude Code reads its settings files in a fixed precedence: managed
    settings (`/etc/claude-code/managed-settings.json`; `/Library/Application
    Support/ClaudeCode/managed-settings.json` on macOS), then a project's
    `.claude/settings.local.json` and `.claude/settings.json`, then
    `$CLAUDE_CONFIG_DIR/settings.json` (`~/.claude/settings.json` by default).
    The highest file that defines `apiKeyHelper` as a string wins; a `null`
    falls through to the next file. The kernel acts on the two files the
    operator of the box controls, the managed and the user file: they decide
    whether the box has a key side at all (the Billing picker's key choice,
    the default for unpicked sessions and judge calls), and they name the one
    helper the kernel runs in-process for its own two calls. A project's own
    `.claude/settings.json` is Claude Code's business: the CLI runs that helper
    for sessions in the project, behind its trust prompt, and the per-init auth
    check reports where such a session landed, but the kernel never runs a
    command a repository checked in, and its fast-mode probe stands down for a
    session whose project would resolve a different helper. The per-session
    settings layer romp writes for a login pick sits above the project files,
    which is how a login pick disables the helper for one session (see
    [Per-session billing](#per-session-billing-login-vs-api-key)). The kernel
    reads the files fresh on every check, so a helper added later counts at
    once; a settings file that cannot be read or parsed is a problem row in the
    Log panel, and the box reads as having no helper until it reads. A helper
    set in the MANAGED file outranks the per-session layer, so no login pick can
    disable it: on such a box the Billing picker offers no login choice and a
    login pick is refused with that reason, never billed to the key quietly.
    The kernel keeps the value its own two calls fetch only within the helper's
    TTL: it is cleared when the TTL ends, when a run fails, and when the helper
    is removed from the settings.

3. Declare the billing in `service.env`: `ROMP_EXPECTED_AUTH=key`. On a box
   with a helper every session without a login pick bills the key, so the
   declaration is true, and the per-init auth check stays quiet on every keyed
   landing and flags a login landing. Leave no key line in the file: one left
   over from an earlier romp, or a marker beside the file, is the boot failure
   above.

4. Restart the service once, for the declaration; `service.env` loads at
   manager startup. Sessions and judges run the helper from their next launch.

Judges (`claude -p` children of the kernel) launch with no credential in their
environment. A key-billed judge call resolves the helper itself, inside its own
CLI, the way a session does. A login-billed call passes the same helper
suppression (`--settings '{"apiKeyHelper": ""}'`) and gets back the login
tokens the kernel claimed at boot. A helper that fails inside a judge's CLI
fails that call with a credential error, which latches the session's
judge-auth-down state like any other credential failure (see
[judges.md](judges.md#billing-and-when-the-credential-itself-is-broken)); it
never falls back to the login.

The kernel makes two API calls of its own: the model catalog refresh and the
fast-mode organisation probe. Both read the helper from the settings files
above, in the same order, for the kernel's working directory, and run it
in-process. The value lives in the kernel's memory for the helper's TTL
(`CLAUDE_CODE_API_KEY_HELPER_TTL_MS`, five minutes by default, the CLI's own
interval) and goes to the one request that asked, never to an environment
variable, a file or a log line. The helper runs through `/bin/sh` with stdin
from `/dev/null`, a 15-second timeout, stderr discarded and never logged (a
secret manager's diagnostics can quote its own token), and a minimal
environment: `PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `LANG`, `LC_*`, `TERM`,
`CLAUDE_CONFIG_DIR` and the `XDG_*` names, and nothing of romp's, the serve
token included. Its output must be one non-empty line with no whitespace, at
most 16 KiB; a trailing newline is forgiven. A helper that fails is a problem
row in the Log panel in static words. With no helper configured the catalog
serves its cached list, or its built-in one, and the kernel log says why at
each refresh attempt (an install's first boot, when no cache exists, and once
per model id it does not know; a boot with a cache serves it, says so with the
cache's fetch time, and never runs the helper: the helper can be a desktop
prompt, and a boot is not an event); the
pickers still work, and Claude Code's own alias table still tracks each
family's newest. The fast-mode probe then leaves the CLI's own check standing
and says nothing.

#### The login

A box with no helper bills the login. Authenticate through Claude Code's own
CLI login flow (`claude /login`) and pick **Login** in Billing, or leave the
pick alone: with no helper the picker offers no key choice, and every session
and judge call lands on the login. No extracted OAuth token is needed. Login
tokens the kernel finds in its own environment at startup
(`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`) are claimed at boot and
handed only to login-billed launches, so a key-billed session never inherits
one. Declare `ROMP_EXPECTED_AUTH=login` when the box is meant to stay on the
login: a session whose CLI then reports a key (a helper in a project's
`.claude/settings.json`, say) is flagged in the Log panel, naming the
declaration.

### Rotating the key

Rotation is a change to the vault item behind the helper, and nothing else.
Claude Code caches what the helper printed and re-runs it after
`CLAUDE_CODE_API_KEY_HELPER_TTL_MS` (five minutes by default) and on a 401 or
403, so running sessions and judges pick the new key up within the TTL, or at
the first refusal of the old one, with no restart and no reconnect. The
kernel's own two calls re-run the helper on the same interval. Nothing in romp
needs to know: `romp keyswap` prints a short note saying that rotation is the
vault item, and does nothing else. Remote kernels each read their own
machine's Claude Code settings, so a key shared across machines rotates once,
in the vault, and everywhere within the TTL.

### Stopping the kernel on purpose

`romp down` stops the kernel and keeps it stopped until `romp up`. The manager
is supervised (`Restart=always` under systemd, `KeepAlive` under launchd), so a
kernel or manager that merely exits is back within seconds, and Ctrl+C is not
available to a manager the service runs. `romp down` instead stops the login
service itself (`systemctl --user stop romp-manager.service`; on macOS
`launchctl bootout` of the agent), which nothing respawns, and then probes the
processes themselves rather than trusting the exit code of `romp-service stop`.

Before stopping, `romp down` gives the turns in flight `--wait` seconds
(default 5, up to 600) to reach a turn boundary. It asks the kernel to quiesce
(`POST /down`), which holds new turn starts and new session creation, and then
reports whether the kernel went quiet or which sessions are still mid-turn and
about to be cut. The wait ends on the event the in-flight count reaches zero;
`--wait` is only its bound. `--now` skips the wait, not the request: when a
kernel answers on the port, the same `POST /down` goes out with a wait of 0 and
nothing is reported about it, so the token check below still comes first; the
hold it arms is the grace the kernel keeps after any wait, and the kernel probe
re-arms it right before the signal. A `romp new` or a dashboard create during
the hold is refused with one line saying the kernel is being stopped on purpose
and no new session can start; the line names no command, because inside a
session its reader is an agent, and an agent told to run `romp up` would undo
the stop. If the stop never lands, the kernel carries on by itself: the hold is
a lease, and it lapses 30 seconds after the wait. The stop then cuts what a
`romp refresh` cuts, and it comes back the same way (see
[What survives a restart](#what-survives-a-restart)).

`romp down` signals only a kernel it has confirmed as its own: one that
accepted this romp's serve token on `POST /down` and named the pid that
`GET /version` also reports. A kernel that rejects the token (HTTP 401 or 403)
is another romp's or another program's. When the quiesce request is rejected,
`romp down` prints `romp down: the kernel on :<port> is not the one this romp
manages (it rejected the serve token); not touching it. Check
ROMP_KERNEL_PORT and the state dir` and exits 1, before the marker is written
or anything is stopped. Under `--now` the same request goes out with a wait of
0 whenever a kernel answers on the port, so a rejected token ends the command
at the same point; the kernel probe asks again right before the signal, and a
rejection there removes the marker.

The exit code of `romp-service stop` decides the first step; the probes after
it run every time:

- Exit 0 (the unit or agent stopped), 3 (no login service installed) or 4
  (installed but not running): on to the probes. After a 3 or a 4, any manager
  running is outside the service (a foreground `romp up`, a hand
  `romp-manager up`).
- Any other exit: the service refused to stop, and the kernel is most likely
  still up. `romp down` releases the quiesce hold, removes its marker, prints
  `romp down: the login service did not stop` and exits 1.
- The manager probe: `romp-manager status` on the control port (`:7432` by
  default). A manager that answers is stopped through its own control endpoint
  (`romp-manager down`, a `POST /stop`) and polled until it leaves; the poll's
  bound is seven seconds, the manager's own five-second grace for its kernels
  (it sends SIGKILL to one still there) plus its exit. One still answering
  after that: `romp down` releases the hold, removes the marker, prints
  `romp down: a manager is still running on :<port> (pid <pid>)`, which says
  to stop it by hand and run `romp down` again, and exits 1.
- The kernel probe: `GET /healthz` on the kernel port (`:29855` by default). A
  kernel the earlier steps already asked to stop is polled for up to three
  seconds first, the bound on its own drain. One still answering (it ran with
  no manager, or outlived the manager's SIGTERM) must first be confirmed as
  this romp's: `POST /down` with a wait of 0 under the serve token must answer
  200 naming a pid, and `GET /version` must name the same pid. That pid is
  sent the manager's own stop signal (SIGTERM) and polled for up to six
  seconds to leave. Any other answer (a rejected token, a 200 without a pid, a
  pid `GET /version` disagrees with, another HTTP code, no answer) leaves the
  kernel alone: `romp down` releases the hold, removes the marker, appends a
  superseding `down-failed` row to `restart-audit.jsonl`, prints
  `romp down: the kernel on :<port> was not confirmed as the one this romp
  manages (<why>); not touching it. Check ROMP_KERNEL_PORT and the state dir`
  (a rejected token gets the rejected-token line instead) and exits 1. One
  still answering six seconds after the signal gets the same release and
  `down-failed` row, then
  `romp down: the kernel on :<port> (pid <pid>) is still running after being
  asked to stop`, which says to stop it by hand and run `romp down` again,
  and exits 1.
- Nothing left answering: a `[romp] down` line that says what stopped (the
  service, a manager outside it, a kernel the probe found, or a kernel that
  answered the quiesce and has since gone) and names `romp up`, or
  `[romp] nothing was running` when neither the service nor a manager was up
  (the auto-start stays held until `romp up`), and exit 0.

With a login service installed, the unit stays enabled, so it comes back at
`romp up` or when the service manager next starts it. On Linux that is the
next boot, not the next login: `romp-service install` enables linger, so your
`systemd --user` instance outlives your logins and a stopped unit stays
stopped through them (where the linger call failed, the instance ends at
logout and the next login starts the unit again). On macOS the booted-out
agent loads again at the next login.

The stop leaves a marker, `down-by-romp` under the state directory (with the
time and the command), so the stopped kernel reads as stopped on purpose. While
the marker exists and no manager answers, `romp status` prints
`down (romp down at HH:MM; romp up to start)` and exits 0 instead of the
manager's not-running error; a marker from an earlier day shows its date
(`down (romp down at 2026-09-04 17:12; romp up to start)`), and one with no
readable time drops it (`down (romp down; romp up to start)`). A manager that
does answer outranks the marker: `romp status` prints its usual report and
exits 0. `romp-service status` reports the marker too, as
`stopped by romp down at HH:MM (romp up to start)`.

The marker also blocks romp's other ways of bringing the kernel back.
`romp-manager ensure` refuses to bring the manager back. `ensure` is the
supervised start that `romp update <host>` and the dashboard's remote restart
run on the far host, so a remote stopped by `romp down` is left stopped:
`romp update` syncs its code, restarts nothing, and says so, and `romp up`
there boots the new code. The dashboard's Start button and an attach's
bootstrap, which boot a bare kernel on a host with no manager, decline the same
way and name `romp up` on that host. `romp up` clears the marker and starts the
service; a manager started any other deliberate way (the login service at the
next boot, a hand `systemctl --user start`) clears it too.

`romp down` also appends a row to `restart-audit.jsonl` that names the action,
so the kernel's restart-cut ledger records the cut as a `down`, not an
anonymous SIGTERM; a `romp down` whose stop did not land appends a superseding
`down-failed` row, so a later cut of the kernel it left running is never
blamed on it.

Sessions come back at the next `romp up` from what is already on disk: the
kernel's boot reconcile reads each session's registry entry and state tail and
needs nothing written at shutdown. A session whose turn had ended before the
stop is revived on demand, with its history, the next time something reaches
it; a session cut mid-turn is resumed at boot and told its turn was cut. When
the stop was a `romp down` (the newest `restart-audit.jsonl` request row is a
`down`, and the cut turn started at or before its time), the notice also gives
the stop time, the start time and the gap, so a model resumed hours later
re-checks what it was running before relying on it.

Only `romp refresh` stops the postal bus on purpose; `romp down` leaves it
alone, but on Linux a bus the kernel started dies with the service anyway: the
kernel runs `romp-postal-service ensure` at boot, which spawns the bus in a
process session of its own but inside the service's cgroup, and the service
stop kills that cgroup. A bus started from a session's postal MCP server lives
in that session's scope and keeps running. Either way the next kernel boot runs
`ensure` again, so at worst mail parks until `romp up`.

### What survives a restart

A kernel restart ends every session's CLI. On `romp refresh`, the manager's
restart-all, `romp down` or a service stop, the kernel receives SIGTERM and drains: it
closes each CLI, and a CLI still running when the drain's bound expires gets
SIGTERM, then SIGKILL. The manager does the same to the kernel: one still
running five seconds after the manager's SIGTERM, on a restart as on a stop,
gets SIGKILL, so no kernel outlives the stop that was meant for it. A crash respawn has no drain: the kernel died without
running one, its CLIs are orphaned, and the next kernel's boot reaper
terminates them (see below). The CLI's harness background tasks do not all end
with it. Its timers and monitors live inside the CLI process and end when it
does. A background shell is a separate process the CLI started, and a CLI
killed by SIGKILL runs no cleanup, so its shells are re-parented and may keep
running. The session resumes with its history and is told what was cut: its
in-flight turn, if it had one, and each background task, with a request to
check whether each is still running before relaunching it. A kernel restart has
never touched work a session deliberately detached: a tmux server it started
itself, `setsid` children and other processes that outlive their shell.

A terminal session from before 2026-09-11, when Romp's terminal (tmux) backend
was removed, is detached work of that kind from then on. One still running when
the new kernel starts keeps running inside its tmux server, but Romp no longer
sees it: it has no registry row and no liveness, and nothing it does reaches the
dashboard. End it from its terminal. The conversation continues from the
dashboard's Revive, which resumes the same transcript as a Claude Code session;
the old session's entry under the state directory's `names/` stays as history.

A boot reads no transcript for nobody. Until 2026-09-10 a fresh kernel parsed
every living session's whole transcript at startup (a warm for the first
dashboard's frames), parsed every session again for its own tick jobs on the
first cycle, and let the feed-only warm parse every session too; on a box with
47 live sessions that was 15 GB read and 6.6 GB resident within five minutes.
Now the startup warm only refreshes the shared session listing: a reconnecting
dashboard receives its active tab whole and every other tab as a skeleton, so
the one parse it needs is the one its own connect push runs. The feed-only warm
parses only sessions whose transcript, state log or goal store changed since
the boot, or that are working now. The interrupt-block and working-note tick
jobs skip a session whose transcript, state log and goal store are unchanged
since their last look, with the boot as the first baseline: a session blocked
before the restart and untouched after reads blocked from the store the
previous kernel wrote, with no parse. The judges' passes walk sessions newest
first and yield between them; their first pass still parses what it
enumerates, which the checkpoint work that follows removes. `/perf`'s `parses`
counts the cold parses, and `scripts/bench_boot_parse.py` measures a boot's
cost against transcript size on synthetic worlds.

The kernel and the judges share one parse. Until 2026-09-11 each kept its own
cache of parsed session trees (the kernel's keyed by transcript path, the
judges' by session), so every live transcript was parsed twice per file
version and held twice. The judges' cache is now the one store: the kernel's
display parse delegates to it, the tree the chat renders is the tree the
judges walk, and the store keys on every fact either side keyed on (the
transcript's and the states file's stat pair, the pending rollback cut, and
whether a backend owns the session, which one owner hook answers for both).
A session read under a new pending cut gets a slot of its own and the spent
cut's slot is dropped with it, so one tree per session holds through a
rollback. A parse of another transcript under a session's id (a subagent
viewer's agent file, an episode render) has a slot of its own beside the live
leaf's, so the two never evict each other. When a `/clear` or a resume fork
moves a session to a new transcript, the previous leaf's tree is dropped the
moment discovery first hands out the new one, so it holds across clears too.
The store evicts the least recently used entry past 256 instead of clearing
wholesale.

The folds' checkpoints survive a restart. Every append-incremental fold over a
JSONL file (the states overlay and the last-state readers, the background-task
pairing, the agent gists and launches, the postal log, the queue ledger, the
wake tail, the machine cut, the states notes, the state intervals, the session
meta) used to re-read its whole file from record zero after a kernel restart:
its cursor lived in the process. Since 2026-09-11 one small JSON file per
folded file under the state root's `checkpoints/` directory records the
reader's prefix witness (the byte offset past the last complete line, the up
to 64 bytes before it, the record count) and the state of every fold whose
cursor stood at that count. A fresh kernel verifies the guard bytes on disk,
reads only the bytes past the offset and resumes each fold from its recorded
state; a checkpoint that does not verify (its version, its path, a file that
shrank, a rewrite under the guard, a corrupt document) falls back to a whole
read, is counted per reason in `/perf` and said once on stderr. Every fold
holding a cursor inside the entry's held records is recorded at its own count
(a fold stepped by builds rather than by the settle may lag the leaf), and the
document's cut is the lowest of them, so the next kernel's tail read holds what
a lagging fold has yet to step and its restore is an append. A fold whose
encoded state would exceed the cap (8 MiB, sized to the machine) is left out
of the document and counted (a state that grows with its file, such as the
postal log fold's map of every sent row, would make the document a second
copy of the file); its cursor stays with the state's size as the reason, and
it cold-folds at first touch over the tail, while the bounded folds beside it
restore. A cursor recorded without a state for any other reason (a tail-only
state a cold fold left, or an older kernel's entry) restarts cold once, says
so, and is healed by one whole refold: a leaf's folds at the session's next
settle, before the write, so that write carries their states; another file's
fold (a states log's) is left out of its next checkpoint write and read whole
once at the next boot. After that the fold is written whole and every later
boot restores it warm. A fold that never ran in the process that wrote the
document has no entry there, and the next kernel reads the file whole for it
at first touch; the converge pass on the pusher's cycle then writes that
document (and, over the whole entry the read left, every leaf fold with it),
independent of settle evidence, so the read is paid once even for a session
that never settles again; the pass is bounded per cycle (`ROMP_CKPT_CONVERGE_MS`,
default 150 ms of wall, and `ROMP_CKPT_CONVERGE_MB`, default 8 MB of documents
written plus leaf bytes read for a heal), heals a legacy bare cursor under the
same budget, and never rewrites a document that already carries every fold
that ran. The settle's own write primes the transcript's queue-ledger and
wake-tail folds beside the leaf's five when the leaf's whole entry is resident,
once per read, so a live leaf whose document lacked them is no longer refolded
whole at every boot's first echo settle or wake (`refolds` names any that still
are). An idle session's leaf, which no settle reaches and the pass must
refuse, converges at the reader's quiescence drop instead: when a fold that
drops quiescent files ends over a file unchanged for two minutes, its document
is written from the entry in memory (the boot's own read, whichever fold made
it) if a write would improve it with a state the process holds (the pass's
rule, `_path_needs_write`; a dirty path counts here and not for the pass, and a
fold cold for want of a state counts for the pass, which heals it, and not
here, where it would only be written cold again), before the entry is popped,
and on a hit or a restore at the witness the entry stays as it always has. The
write is charged to the pusher cycle's byte budget, which the kernel begins at
each cycle's start and the pass shares near its end; over the budget the write
and the drop wait with the entry held (`converge.dropDeferred`), the drop then
owed and paid at the next cycle's start with the room that cycle has, oldest
first, or by the next fold over the file, whichever comes first. A document
already whole is never rewritten at a later drop (`converge.dropWrites` counts
the writes), and a dropped file's next fold restores its cursor from the
document over a tail read instead of reading the file whole, provided the
document's cursor carries a state: against a state the process holds, a cursor
without one (an over-cap, cold or legacy bare write) is refused and the fold
reads whole as before, so a complete state is never replaced by a tail-only one.
The knobs: `ROMP_CKPT_CONVERGE_MS=0` turns the pass off and the drop write with
it (the drop then pops as it did before the write existed); `ROMP_CKPT_CONVERGE_MB`
is the cycle budget both charge, and `0` turns the drop write off the same way
rather than deferring every drop; both are read where the drop lives, so they
hold from the first fold, before the first pusher cycle begins. The pass also
writes the ASSEMBLY document of an idle leaf that has none (the assembly
document is otherwise written only at a settle, which an idle session never
reaches, so the parse read those leaves whole at every boot: 31 of 60 on the
devbox, about 2.5 GB): from the whole assembly entry the boot's own parse built,
through the settle's writer, while the reader's whole record entry is still
resident (the writer takes its record offsets from it), so for a leaf the fold
half handles the assembly write runs inside the same hold, before the held drop
pops that entry, and both documents come from the one read; no read of records,
charged to the same cycle budget. A leaf is looked at once per file state:
written, or refused for a property of its cut, it is done; a blip is tried
twice (a blip inside the fold half's hold gets its second try over the entry
the paid drop popped, so that leaf waits for the next boot's read); a leaf with
no whole entry to write from is re-examined each cycle and counted once. The step's candidates are the assembly cache's whole entries, the parses the
boot actually did, whatever the session's age (the discover window's rows,
48 hours by default, would leave every older idle leaf out) and whether or not
the session still has a registry row: a leaf the boot parsed is one the next
boot parses, so its document is wanted, and the boot's sweep removes the
documents of vanished files. The document is written under the display
parse's flag, the one the next boot reads with, and with the turns section
from the parse under that same flag or none; a leaf parsed only under the
judges' flag is skipped and counted (`flagMismatch`), since the reader would
delete a document under the wrong flag. A leaf with no compaction boundary has
no cut and no document: it is read whole at every boot by design. `ROMP_ASM_CONVERGE=0` turns that step off, and so do the pass's
own switch and a zero byte budget, as for the drop write. The owed table
is bounded: over it the oldest owed drop is paid by its pop alone, and an owed
file since deleted has its entry popped when the cycle pays. A leaf unchanged for longer than the reader keeps a quiescent
file's whole entry (two minutes) is refused by the pass and counted under
`quiescent`: its heal would read the file whole every cycle and the write
would find no entry; the one exception, with the drop write on, is a leaf
whose whole entry from the boot's own read is still resident: the pass heals
and primes it in memory with its quiescence drops held, then pays them once,
so the launch fold's drop writes the document from that read (`viaDrop`, counted
only for a write that happened) and pops the entry when a fold stepped records
(a restore at the witness leaves it resident), after which the converged leaf
simply leaves the candidate set;
a path the pass refused or whose write produced nothing is skipped until its
file changes under the reader (`skipped` counts each such hold once, per file
state, and the check reads the reader's own entry rather than stat the file
while one is held); `ROMP_CKPT_CONVERGE_MS=0` turns the pass off. Every
write merges the on-disk document's states for folds the
writing process never ran (verified by that document's stat and guard as a
restore would), so a rewrite from one process's cursors strips no state an
earlier process stored. A fold's count may lag the entry's by 64 records or an
eighth of the entry, whichever is more, and still be written or carried at its
own count; further behind, the fold is left out (it refolds whole once when it
next runs), so a fold that ran early and stopped cannot drag the document's
cut, and every later boot's tail read, back to its count. Checkpoints
are written when a session's turn settles or its states log moves, and all of
them at exit; checkpoints of files that no longer exist are swept at boot. A
compaction appends records and changes nothing here.

The assembly checkpoint (2026-09-11) does the same for the parse itself. When a
session's tree holds a compaction boundary, a second document beside the fold
checkpoint records everything before the cut (the turn that holds the last
boundary, or the `/compact` command's turn for a manual one) as identities and
record locations: each record's uuid, verdict, type, order, time and file, each
emitted atom's scalar fields and the identity facts the ids and the turn
segmentation read, the kept chain, the gate facts, the emit carry with its text
sets as hashes, each file's witness and where its tail starts, and a hash over
the pre-cut turn ids, segment ids and atom uuids. A fresh kernel verifies the
document, rebuilds the pre-cut turns as atoms without bodies, reads the leaf
from the cut's byte offset only and parses that tail, proves the prefix by the
hash, and hands the judges and the display one tree. Since the lazy index
(2026-09-11, document version 4) the document also carries a `turns` section:
each pre-cut turn as its identity, its atoms' row indexes, its segments' spans
and the scalars the kernel's walkers read (the atoms' uuids, the last and
latest times, the last model, the tool calls), so a restore builds the turns
without building an atom. Document version 5 (T358) adds what the per-cycle
walkers read: each turn's assistant prose chars by uuid and its newest
genuine-human time, each segment's has-work verdict and postal message ids,
and on every lazy marker the prose chars and message ids; the caption
planner, the feed's transcript-side sets and citation gate, the timeline's
message-id join then read scalars and build no atom for a captioned or
already-rendered history, and a segment's atoms are a view that builds only
what is read. The summary anchors read scalars too (no body is hydrated) but
still build each pre-cut atom they walk on a cold pass, until the document
carries per-segment anchors. A version 4 document is refused and the
session parses whole once. The pre-cut rows stay as bytes; a turn's atoms are
a list whose slots are built one at a time when a consumer reaches for them,
through a process-wide LRU of 20000 built atoms across every session (eviction
drops the memo; a consumer's own reference stays whole), counted per consumer
under `/perf` `asmIndex`. A body before the cut is read on demand from its
record when a consumer asks for it, through a byte-capped memo; a consumer
that reads one without asking fails loudly rather than seeing an empty
message, and a serializer reaching a pre-cut turn's atoms is refused (a dump
goes through `plain_tree`). A document written without the parsed tree (the
exit path past its budget) carries no `turns` section and restores the atoms
as before, until the next settle rewrites it with one. A compaction after the document demotes to a
whole parse as before, and the next settle writes a new document; a rewrite
under the cut's guard, a shrunk or moved file, another session, other inputs,
a wrong version, a corrupt or unprovable document, or a document past 16 MB
each mean a whole parse, counted per reason in `/perf` and said once. The
agent files (the subagents' transcripts) get no document yet; that is the next
stage's. The gain is one tree per session, about
a quarter of the record cost the T311 report measured (0.25 GB of 6.6); the
record cache itself, the bulk, is the checkpoint work's target. The goal planner reads placement first (T377): a unit the
store already places is yielded with its key and scalars and no text or quote
(no pre-cut body read), the rest read their text after the placement check;
the lookup is an index built once per planner call with the episode floor
taken once per pass, and a consumer that plans a unit yielded as placed reads
its text then.

What the CLI itself does when its parent goes quiet was measured on Claude Code
2.1.257 (2026-09-10, the restart-surviving sessions program's stage 3 probe, run
against a throwaway config directory): a permission request (`can_use_tool`)
waits for its answer with no expiry within ten minutes and the turn continues
normally on a late answer; a tool hook callback (a `PreToolUse` hook on Bash,
the kind the probe module registers) waits 600 seconds by default, or the
matcher's `timeout` seconds when one is set, then the CLI cancels the request
(`control_cancel_request`), records a hook-timeout error as the tool's result
and goes on with the turn; the CLI instead treats a timed-out `UserPromptSubmit`
callback as a blocking decision and suppresses the prompt (Claude Code 2.1.266,
read from the CLI's hook dispatch rather than measured: that dispatch converts a
timed-out prompt-hook callback into a block and hands every other event's
timeout to that event's own handler; romp registers that hook and sets no
`timeout` on any matcher); what a timed-out `Stop`, `SubagentStart`,
`SubagentStop`, `PostToolUse` or `PostToolUseFailure` callback does is
unmeasured; a second `initialize` on the same stdin is accepted and its hook
table replaces the first; stdin end-of-file ends an idle CLI at once (0.02 s)
and a busy one after its turn (a 30 s tool call ran to completion first); an
unread stdout does not stall the CLI (the pipe's 64 kilobytes fill, the rest
buffers inside the process, the turn completes); `--resume` takes no lock, and
two processes on one session id both append to the one transcript; `claude --bg`
runs an interactive session on a pseudo-terminal under a daemon that stays in
the launcher's cgroup, and refuses `--print`, so a background session has no
stream-json channel. `tests/test_cli_control_protocol_probe.py` re-checks the
three facts that need no model call (the second initialize, the idle exit on
stdin end-of-file, the `--bg` refusal) when run with `ROMP_CLI_PROBE_LIVE=1` and
a `claude` on PATH; it skips otherwise, as every test that would reach the live
CLI must.

Who owns a running CLI is a lease, not its parent process. The kernel writes
`leases/<sid>.json` under the state directory the moment the SDK connect hands
it a CLI: the CLI's pid and start time, the kernel's own pid and start time as
the holder, the kernel's code version, and a heartbeat the kernel refreshes
every three seconds while the CLI runs; the lease holds for twelve seconds past
its last beat (the deploy drain hold's cadence: four beats, so it outlives a
missed beat and not a dead holder). The lease is removed when the CLI's client
closes, so only a kernel death leaves one behind. A lease is valid when its beat
is fresh, its holder is alive and its CLI is alive, each identified by pid and
start time together, never pid alone. The boot reaper reads the leases: a CLI
with a valid lease is owned by its holder whatever its parent, so a CLI
re-parented by a wrapper or a debugger (and, later, one kept by a per-session
host) survives the boot; a CLI parented to a live kernel without a lease is kept
and reported, so the sessions of a kernel from before leases survive the upgrade
boot; every other CLI of ours is an orphan and is ended with its tree. Since the
kernel is the holder, a crashed kernel's leases are invalid at the next boot and
its CLIs are reaped as before, keeping one writer per transcript. The scope sweep
spares an owned CLI's scope, and the interrupt escalation signals the leased CLI
first, so a re-parented CLI is still stoppable. Every anomaly the census meets (a
CLI without a lease, a lease without a live process or holder, a stale
heartbeat, a lease from another code version) is a problem row: prose in the
error center, the same prose with a JSON object on the kernel log line, and one
JSON line in `session-events.jsonl` under the state directory, the shape the
restart monitors read. Two CLIs on one conversation is the boot sweep's own row
there. The CLI takes no lock on a transcript it resumes, so the one writer per
conversation is entirely the lease's to keep.

A session can outlive the kernel that started it. By default, on every machine
on this version, a new session's CLI runs under a small per-session host
process, `bin/romp-session-host`, instead of as the kernel's child. The
`session-hosts` setting is the toggle: a bare value file under the state
directory. Write `off` to it to run a machine's sessions as plain kernel
children again; `on`, or no file at all, leaves hosts on (`on`, `1`, `true` and
`yes` read as on; an empty file, or one holding only whitespace, is the default,
on; any other content reads as off). It is read at each connect, so a
flip needs no restart: a session already running as a plain child becomes
hosted at its next respawn, whatever prompts it (a model or effort switch, a
crash resume, or the next kernel restart, which cuts a plain child's turn one
last time); a new session is hosted at once. The host spawns the CLI from a
spawn specification the kernel writes
(`hosts/<sid>/spawn.json`, the plain fields of the SDK's options, at mode 0600
in a 0700 directory, since it carries the environment overlay), through the
SDK's own subprocess transport, so the command line and the environment are
the SDK's byte for byte. It reads the CLI's stdout without pause and appends
every message to an append-only journal (`hosts/<sid>/journal-<n>.jsonl`, one
JSON object per line, offsets that are the record's ordinal since the CLI
started, 64 MB segments rotated at turn boundaries, acknowledged segments
deleted), serves one Unix socket (`hosts/<sid8>.sock`, mode 0600), and holds
the session's lease as the holder. The kernel keeps the SDK client, its hooks
and its permission callback and speaks to the host over the socket. On a
restart the drain detaches from every host instead of ending its CLI: the
host keeps the CLI and its turn, journals what it says, parks any permission
request or hook callback the CLI raises (a permission waits without expiry; a
hook the kernel registers with a 540 second timeout is answered by the host
itself with the event's neutral output after 480 seconds of parking, and each
such answer becomes a problem row when a kernel next attaches, since the
kernel never saw that hook), and the next kernel attaches by the lease,
replays the journal from the offset it last acknowledged in the registry
(`hostAck` on `sdk/<sid>.json`, written by the kernel, the registry's only
writer), and sends its own initialize, which the CLI accepts as a replacement
of its hook table. The turn was never cut: no continuation notice, no
`cutTurns` entry, and a `host.attached` row in `session-events.jsonl` for
every attach, at boot or later. The interrupt escalation's signal rungs and a
kill or a conserve close become requests to the host; a graceful end closes
the CLI's stdin and waits (an idle CLI exits at once, a busy one after its
turn), with SIGKILL only past a settable grace. A host whose kernel never
returns ends an idle CLI after `session-host-grace` seconds (900 by default).
If a host dies, its CLI finishes its turn on stdin end-of-file and exits; the
kernel files a `host.died` row, waits for that exit, replays the orphan
journal through the same path a live attach uses, and only then resumes the
session from the transcript, so a conversation never has two writers. On
Linux the host runs in a transient scope of its own (`romp-host-<sid8>-<t>`)
outside the service cgroup and starts the CLI through `bin/romp-cli-scope` as
before, so the CLI's own scope and its memory limits are unchanged; the boot
sweep stops a dead host's scope by its lease. On macOS the host is a plain
detached process and everything else is the same.

A message the kernel cannot handle does not end the session's CLI. The kernel
handles each streamed message on its own: when a handler raises, it logs the
exception type and the failing frame (file, line and function, first on the line
so the error center's clipped row still shows it), the message's type and
subtype, what that message lost (an assistant or user message is also a
transcript record, so the chat rebuilds it from disk; a compaction boundary is
one too, while a model or mode change's confirmation line is not; a turn result
still settles its turn, and the line says so only when the settle ran; a
stream-only frame's content is gone until the next such frame), the exception's
own text (uuid-shaped ids shortened to eight characters, clipped to 160
characters; it carries whatever the raising code put in it, never the message's
content), and a compact frame chain (innermost first: file, line and function
for at most the innermost eight frames, no locals, at most 600 characters,
dropping outer frames first so the failing frame is always named) to the kernel
log and the dashboard's error center, then goes on to the next message. A
failure while filing a turn result (its spend, its live-tail sweep) still
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
under systemd Romp runs each session's CLI in a transient systemd scope of its
own, outside that cgroup (`systemctl --user list-units 'romp-session-*'` lists
them). A session's own `setsid` children, detached servers and other detached work
live in the session's scope, and a service restart leaves them alive as a kernel restart
does; before 2026-09-05 they were in the service's cgroup and died with it. The
CLI itself still ends: the kernel receives the service's SIGTERM and runs the
same drain. A scoped CLI outlives a service restart only when the drain does not
reach it: a kernel killed before its drain finishes (SIGKILL at the service's
stop timeout), or a CLI the drain could not find. The reaper handles that case:
at the next kernel boot, an SDK-driven CLI holding one of the kernel's sessions
whose parent is not a live romp kernel is treated as orphaned and terminated.
Under `systemd --user` an orphan re-parents to the user manager, not to pid 1,
so a ppid check alone would miss it and did, before 2026-09-05.

`ROMP_CLI_SCOPE=0` in the service environment turns the scopes off for the
session CLIs. A manager run outside the service (`romp up`) scopes nothing
unless `ROMP_CLI_SCOPE=1` is set, which turns them on. The kernel logs which it chose at start (`cli scope: on` or `off`, with the
reason); when the scopes were wanted on Linux and the box cannot provide them
(no `systemd-run`, or a user manager that refuses to start one), that verdict
also appears in the dashboard's error center, since every session then runs
inside the service cgroup. The macOS launchd path is unchanged: there is no cgroup kill there.

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
naming the session and counts in `/api-health` (`cliScope.limitsIgnored`, see
[The API-health signal](#the-api-health-signal)); on a launch the kernel drove,
an `ignored:` line naming a rule means the value reached the wrapper some other
way.

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
process in it, systemd stops the whole scope, which ends the CLI and every
`setsid` job and detached server in it. With `continue`, only the killed
process is gone. systemd logs each kill to the user journal as `<unit>: A process of this unit
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
their `setsid` children, and any server a tool shell starts directly (a process it
forks and detaches). Outside it is anything a session starts as a transient
unit of its own (`systemd-run --user --scope …`, or a `systemd-run --user`
service): that is a sibling of the session's scope under the user manager,
outside its memory limits, so a server detached that way is outside them,
whereas the same server started directly from the tool shell is inside.
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
  `cycle_cpu_ms_sum` (the pusher thread's own CPU time), `cycle_ms_p50`,
  `cycle_ms_p90`, `cycle_ms_ring_max`, `ring_n` from the last 256 cycles,
  `sends` (every payload that went to a client; a deduped frame the client
  already holds is not one), and `idle_cycles`, `idle_ms_sum`, `idle_cpu_ms_sum`
  (cycles that set no wake, sent no payload and saved no goal store: what a
  longer wait between cycles would have skipped; a conservative undercount,
  since a wake set by another thread or a periodic repost of an unchanged
  frame marks a cycle busy).
- `checkpoints`: the folds' checkpoints since boot: `restored` (files whose
  folds resumed from one), `restoredFolds` (restores per fold name), `writes`,
  `swept` (checkpoints of vanished files removed at boot), `refolds` (per fold
  name, refolds that read: a fold with no cursor and nothing to restore, over a
  tail entry or from zero, the boot's first whole read of a file included, with
  count and the bytes the call read, an appended tail's among them), `skippedFolds`
  (fold states the codec could not encode), `oversizeFolds` (per fold name,
  states over the cap: the document keeps that fold's cursor without its
  state, with the state's KB as the reason, and the next kernel starts the fold
  cold at the cut over the tail only), `coldFolds` (per fold name, folds that
  started cold this boot, for that reason or for a cursor recorded without a
  state, which the next settle heals), `converge` (the converge pass: `passes`,
  `writes`, `bytes`, `heals`, `healBytes`, `primed`, `deferred`, `failed` for a
  write that wrote nothing, `unhealed` for a cold fold the pass could not rerun,
  whose cursor it dropped so its next run reads the file whole once, and
  `docReadBytes`, the documents the pass's writes read for their carry,
  `quiescent` for leaves refused as quiescent, `skipped` for candidates held
  off until their file changes, once per hold, `dropWrites` and `dropDeferred`
  for the documents written at the reader's quiescence drop and the drops
  deferred a cycle for the shared budget, `viaDrop` for the resident quiescent
  leaves the pass primed and the drop wrote), `coldWrites` (per fold name, writes that kept such a tail-only state
  out of the document so no later kernel restores it as complete), `droppedRestores` (a
  restore lost to a read that replaced the entry under it; the reader
  serializes reads per path, so this should stay at zero), `documentBytes`
  (what reading the checkpoint documents themselves cost since boot),
  `fallbacks` per reason (`version`, `path`, `shrunk`, `guard`, `rewrite`,
  `corrupt`), `dirty` (files whose folds moved since their last write),
  `readBytes` and `readByPath` (what the JSONL reader pulled off disk since
  boot, in total and per file).
- `stacks`: every thread's last six frames, keyed by the thread's ident and
  name, when the kernel runs with `ROMP_PERF_STACKS` set (a debugging aid for a
  served test on a runner nobody can log into); `null` otherwise.
- `recordCache`: the reader's record cache (the JSONL records held in memory):
  `entries`, `bytes`, `budgetBytes`, `countCap`, `inserts`, `evictions`,
  `evictedBytes`, `budgetEvictions`, `dropped` and `droppedBytes` (the
  quiescence drop), and `wholeReads`: every read that pulled a file whole,
  keyed `kind<-caller` (the reader's kind, one of `zero`, `rewrite`, `guard`,
  `shrunk` and `upgrade`, and the first calling function outside the event
  model and the parse family), with `count` and `bytes`; a tail read, an
  append and a restore's tail read are not whole reads and are not counted.
- `asmCheckpoint`: the assembly documents since boot: `written`, `restored`,
  `fallbacks` per reason (`version`, `session`, `inputs`, `lineage`, `shrunk`,
  `rewrite`, `guard`, `identity`, `corrupt`, `restore`), `skipped` per reason
  (`noEntry`, `restored`, `written`, `noBoundary`, `unsplittable`,
  `reconstruction`, `oversize`, `unencodable`, `offsets`, `stat`, `write`),
  `hydratedAtoms` and `hydratedBytes` (bodies read on demand for atoms before
  a cut), `hydratedBy` (those bytes per calling function), and `converge`: the
  pass's writes of idle leaves' documents from the boot's own parse
  (`candidates`, `writes`, `bytes`, `deferred`, `skipped` per the writer's
  reason).
- `asmIndex`: the lazy index (T323 stage 4c) a restored session's pre-cut turns
  come from: `materialized` atoms built from the document's rows since boot,
  `materializedBy` (per consumer), `resident` (the process-wide LRU, `cap`
  20000 atoms across every session; eviction drops the memo, never a field in
  place), `evictions`, and `restoredTurns`.
- `skillLoadIndex`: the judge's skill-load boot pass (the tops older stores minted from
  the harness's own skill load): `filesRead` and `bytesRead` (transcripts read raw this
  boot, appended tails only once the persisted index holds a file), `filesIndexed`, and
  `checked` (prompt anchors known not to be a wrapper, never read again).
- `chatPages`: the rendered pages of chat history before a session's render
  floor (the chat wire's `loadOlder`, `loadAround` and `loadNewer` answers, below):
  `hits`, `misses`, `evictions`, `pages` and `bytes` resident (a bound of 32
  pages or 16 MB per kernel), `renderMs` spent rendering; the warming, after
  the pusher's send stage (`push.warm`), with a board client and a proto-2 chat
  client connected: `warmed` pages rendered ahead of a click for the feed's
  cards' anchors (the distilled summary's own targets first, a completed card's
  too, then the active cards' heads and open rows; the feed's first 32 anchors,
  so a late session's summaries can fall past the cap; the warm SET is bounded
  to half the cache in pages and in bytes: anchors past it wait for the next
  board change, and a set that fits settles, an unchanged board costing one
  probe of its remembered keys; a set with an anchor whose session has no
  render floor yet is never remembered as settled, so the floor's return
  warms), `warmPending` (anchors waiting past the bound), `warmMs` (the
  probes' time included),
  `warmCycles`, and `warmSkipped` (cycles the warm stood down because the
  pusher's last cycle ran over 1.5 s). A page's cache key reads what a
  pre-floor render reads and none of the live tail (the reg's fork value, not
  the reg file, which every send rewrites), so a warmed page survives the turns
  that stream after it until the session's next judge publish (the goal store's
  identity is a component: the segment anchors come from it); the postal
  caption map is not a component, so a pre-floor page holding a card rendered
  before its caption landed keeps the caption-less card until an eviction.
- `parses`: the cold event-model parses through the one parse store the
  kernel and the judges share: `total` (every miss, whoever asked), `kernel`
  (the display's asks among them, with `bytes`, the parsed files' sizes, and
  `bySid`, per session by the first eight characters of its id), `judge` (the
  rest), `hits` (the display's asks served from the store) and `sharedHits`
  (every hit). The acceptance number of the lazy-transcript work: a boot with
  no client connected reads `kernel` zero, and a connecting chat client adds
  at most its shown tabs.
- `stages_ms`: `jobs` (the cycle's tick jobs outside the push), `push`, and
  inside it `push.chat`, `push.feed`, `push.timeline`, `push.send`. The
  `push.*` stages count every push, including the one a connecting page gets,
  so they can add up to more than `push`.
- `builds`: `chat`, `feed`, `timeline`, each with `cached`, `built`, `ms`.
  Every chat tab, the watched one included, is served from its cached build
  while one complete per-session signature holds: one component per input the
  build reads (the transcript and states files, the session's goal store and
  its journal and archive, the task store, the backend's live tail by
  revision, its queue and brackets, the liveness row, the clock crossings the
  payload renders, the parked ops, the account hold behind a queued bubble,
  the retry state, the live background-task rows, the watches, the awaiting
  stamp, the shared files, the cwd's branch and repository, the instruction
  files, and the files and postal values the last build embedded). `chat`
  also carries `active_built` and `bg_built` (rebuilds of the watched tab
  against rebuilds of a background tab), `moved` (builds not cached because
  an input moved while they ran; the next cycle builds them again) and
  `bg_miss`, a map from each labelled component of that signature
  (`transcript`, `states`, `store`, `hold`, `archive`, `episodes`, `reg`,
  `gone`, `tasks`, `cut`, `live`, `row`, `clock`, `backend`, `ops`, `limit`,
  `retry`, `bg`, `watch`, `stamp`, `anchors`, `downtime`, `names`, `flags`,
  `ncards`, `colormap`, `acct`, `cleared`, `host`, `cwd`, `claudemd`, `fork`,
  `note`, `needs`, `taskout`, `pathlink`, `postal`, plus `cold` for a tab with no cached
  build and `nosig` for one whose signature could not be taken) to the
  background rebuilds it caused. A rebuild with several moved components
  counts under each, so the map's sum can exceed `bg_built`. One session's
  goal-store publish moves that session's `store` component and no other
  tab's; the judge-pass generation busts the feed and timeline caches only.
  `romp perf` prints the split and the non-zero causes after the chat
  average, and the moved count when it is non-zero. `feed` also carries
  `memo`, the per-session card memo inside `build_feed`: each living
  session's cards are derived once and served while every input of that
  derivation stands (the transcript, states, names, captions, store, journal
  and archive by identity; the live row, the wait graph, the stall and nudge
  records, the session's own rows of the postal log, the watches and the
  background tasks by value; the interrupt and settle-gap booleans the
  clock decides and the billing offer's open window as the card renders it;
  the peers the cards read), so a rebuild
  re-derives only the sessions whose inputs moved. The sections that span
  sessions (the serving-fold join, the parked handoffs, the quarantine cards,
  the bell pass, the working and awaiting dot lists, the unreadable-state
  ring) are never memoized: every build recomposes them from the served
  entries, decoded fresh, so nothing memoized is mutated. `hit`, `miss` and
  `derived` count per session per build, `evict` the entries shed (a departed
  session, or the byte bound), `entries` and `bytes` are the resident set
  against `bound` (a sixty-fourth of the machine's memory, or
  `ROMP_FEED_MEMO_BYTES`), and `miss_by` maps each labelled component of the
  per-session key (`transcript`, `parse`, `cut`, `states`, `names`,
  `captions`, `store`, `anchors`, `reg`, `cleared`, `row`, `ask`, `live`,
  `bg`, `wait`, `postal`, `stalls`, `nudge`, `jauth`, `jactive`, `hide`,
  `watch`, `subagents`, `usage`, `offer`, `auth`, `downtime`, `debug`,
  `interrupting`, `closer`, `peers`, plus `cold` for a session with no
  entry) to the re-derivations it caused; a miss with several moved
  components counts under each. The nudge records, the key on hand, the
  host-suspension spans and the debug mode are board-wide inputs: a change
  to one re-derives every session. The clock is not a component of the key:
  a card's clock-derived fields either leave the memoized entry and are
  stamped per build (the age tint, a placeholder's time), or enter the key
  as the value the clock decides (the interrupt window and the settle gap
  as booleans, the billing offer's open window and its reset as the card
  renders them, a parse's trailing idle edge), so a served card shows what a
  rebuilt one would.
- `sends`: `full`, `delta`, `deduped`, each a map from slot name (`chat`,
  `feed`, `bars`, `taborder`, ...) to `count` and `bytes`. A deduplicated frame
  was built and compared, then not sent.
- `goals`: `loads`, `saves`, `writes` on the goal stores through the writer's
  loader (`load_goals`) and `save_goals`; the pusher's read-only loads go
  through the shared store cache and show under `memos.shared`, not here. A
  save that would rewrite identical bytes is a save without a write.
- `memos`: the identity memos on the goal-store path. `pass` is the
  judge pass's stat-keyed store memo (`hit`, `miss`, `fail`, `evict`, `punch`,
  and its occupancy `entries`, `bytes`); `shared` is the pusher's shared
  read-only store cache (`hit`, `miss`, `compare_miss`, `refuse`, `dup`,
  `absent`, `corrupt`, `unreadable_journal`, `evict`, `fallback`, `poisoned`,
  with `entries`, `bytes` and `off`); `chain` is the write-moment chain memo
  (`hit`, `miss`, `populate`, `bypass`); `nudgeGate` is the auto-nudge walk's
  planner-placement gate, derived once per (parse, store) and served while
  both stand (`served`, `derived`; a healthy quiet box serves almost every
  cycle); `cleared` is the feed's clear set, parsed once per state of
  `cleared.jsonl` (its stat, taken before the read) and served while the file
  stands (`served`, `derived`); `courierSkip` is the courier's change gate
  (`skipped`, `scanned`, `recorded`: a session whose parse, store, journal,
  archive and episode log have not moved since a scan that found nothing to
  place is skipped whole); `backref` is the sender-board walk behind the
  courier's link repair, built once per state of the sender stores and served
  while they stand (`served`, `built`); `captions` and `goalArchive` are the
  per-file read memos behind the index tier's caption readers and the re-plan's
  cleared context, each parsed once per file state (`served`, `parsed` or
  `loaded`). `goalArchive` memoizes a readable archive only: an archive that
  exists and cannot be read or parsed is answered empty, marks the running
  judge stage incomplete, and is not memoized, so the next call reads the file
  again. `plannerSkip` is the planner's inner change gate (`skipped`,
  `planned`, `recorded`). The planner runs behind two gates. The outer gate is
  the judge's evidence gate around `_plan_session` (`docs/judges.md`, "Ops and
  knobs"): a session whose signature equals the one the planner stamped after
  its last complete run is skipped before it is submitted. It keys on the
  inner gate's inputs, the reg by its `spawnedAt` and backend values rather
  than by identity, plus `cleared.jsonl`, the death marker and the session's
  stall records. The inner gate
  sits inside `_plan_session` and sees only the sessions the outer gate ran: a
  session whose parse, store, journal, archive, episode log, its leaf's task
  store, captions file and reg have not moved since a pass that had nothing to
  do, and none of whose running background launches has crossed its deadline,
  is not planned again. The inner gate records a pass only when it placed
  nothing, left the store's key where it was, and ran to completion; a
  deferral without a write, or a side file that exists and did not read,
  marks the run incomplete, and that session is planned again next pass. So
  `plannerSkip` counts the sessions the outer gate let through, not every
  planner skip: an idle session stops at the outer gate and appears in neither
  `skipped` nor `planned`. Outside a pass frame (`romp-judge --plan`) the
  outer gate stamps nothing, and the inner gate does the skipping. `liftGate`
  is the awaiting lift's per-session inputs gate and two-phase read: `skip`
  and `load` (session-cycles that took no store read against the ones that
  read it, a probe on the shared read-only view), `shared` (probes the shared
  cache answered), `writer` (session-ticks that loaded the writer's copy
  because a lift was due) and `noop` (writer loads whose fresh decision filed
  nothing, the store having moved between the probe and the load), and the
  gauge `entries` (sessions remembered). `bgTops` is the placed-launch memo
  behind the awaiting lift and the feed's background-task classification,
  keyed on the parse object and the store object: `hit` and `miss` (calls
  answered from the per-version map against looked up), `resolve` (launch ids
  looked up on a miss, placed or not), `walk` and `walk_neg` (transcript
  walks, and the walks that left a launch unresolved: an upper bound on what a
  negative walk cache would save), `idx_build` (placement indexes built, one
  per store object asked, a writer's private copy included) and the gauge
  `entries` (sessions holding a map). `intrMarks` is the interrupt-marks
  memo behind the interrupt tick, the nudge tick and the feed's badge, one
  entry per (session, parse family) keyed on the parse object's identity and
  the machine-cut stamp (`hit`, `miss`, `evict` for entries released when a
  session leaves the alive set or the memo is cleared at its cap, and the
  gauge `entries`). `statesOverlay` is the awaiting overlay's read of the
  states log through the shared append-incremental reader, one carried answer
  per states file (`hit`: the records were the cached ones and no row was
  stepped; `append`: only the appended rows were stepped; `refold`: every row
  was stepped again, after a rewrite or a shrink or on the file's first read;
  `fail`: a read that failed on a file that exists, answered as no overlay,
  memoized nothing and named once per episode on the kernel's stderr;
  `evict`: entries dropped for sessions that left the alive set; and the
  gauge `entries`). The compaction sweep after each judge pass evicts from
  `pass` and `shared` the entries of stores no session in the discover window
  owns, so both stay bounded by the live board; the courier's and the
  planner's change-gate tables are pruned to the sessions each pass discovers,
  the evidence gate's stamps are cleared at a fixed cap, and the awaiting
  lift's tick drops the gate's and the placed-launch memo's entries of
  sessions that left the alive set. The interrupt tick drops from `intrMarks`
  and `statesOverlay` the entries of sessions outside its alive set each
  cycle; the `statesOverlay` cache is also cleared whole above 256 entries, a
  drop `evict` does not count and `entries` shows. `lanes` is the timeline's
  per-lane segment memo: a live lane's bars, segment ends, last activity,
  compaction markers and judging marks, held while its parsed transcript and
  goal store are the previous build's objects and its captions file, archive
  file, branch clip and the host's recorded suspensions stand. One outcome per
  live lane per bars build: `hit`, `miss`, `live_tail` (a live tail was merged,
  so the lane was derived and not held), `complain_skip` (the parse or a stage
  failed) and `unshared_skip` (a private store with content); `evict` and the
  gauge `entries`; `segs_hit` and `segs_miss` count the segments served and
  derived. `dead_serve`, `dead_miss` and `dead_failed_serve` are the dead-lane
  memo's outcomes on the same block, so one block carries every lane.
  Four memos cover the chat build's per-build fixed costs, each keyed on the
  inputs it reads and evicted by the pusher with the tab set (a comment thread
  built this cycle is kept, like its fold prefix). `chatMergeSets` is the
  live-tail merge's memo of the sets it derives from a parsed transcript (the
  uuids and user texts the transcript already holds, and the newest human
  turn's time), one entry per session keyed on the parsed session object's
  identity and shared by the chat, feed and timeline builds of one cycle:
  `hit` and `miss` (merges served against derived) and the gauge `entries`
  (a session neither shown as a tab nor alive is dropped). `chatPostal` is
  the chat fold's memo of a tab's sealed postal cards, keyed on the values
  the cards embed from outside the transcript (the message log's identity
  and, per card, its caption and its peer's name and colour): `gate` (gate
  checks that re-hydrated a tab's sealed cards because one of those values
  moved, or because the entry was sealed outside the pusher's names snapshot
  and had to be verified), `hit` (checks that verified the sealed cards from
  their recorded values without hydrating), and `commit_new` (raw postal
  events hydrated at fold commits; each is hydrated once, when it is first
  sealed). Before this memo every judge pass re-hydrated every tab's sealed
  cards, although a caption is the only judge-written value a card carries.
  `chatLedger` is the chat build's memo of a session's goal-tree walk and
  live roots, keyed on the parsed transcript's identity, the store's
  identity and seams, `cleared.jsonl`'s identity and the warm-anchor table's
  per-session revision: `hit` and `miss`, `bypass_live` (a build that merged
  live atoms: the last turn's segments differ from the parse's, and since
  T344 a stale echo may sit in an earlier turn or a turn of its own),
  `bypass_hold` (an armed rewind hold filters a store copy per build),
  `bypass_empty` (a store with no nodes), `evict` (entries dropped for tabs
  no longer shown) and the gauge `entries`. `chatFoldTasks` is the per-turn
  memo of the transcript's task fold, keyed per session on each turn's atoms
  list and fingerprint: `hit` and `miss` count turns served from the memo
  against turns scanned, so a build of a working session with one moved turn
  is one miss, plus the gauge `entries` (sessions held).
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

The counters describe a running kernel. To time the same builders offline, on
a copy of a state directory and with no live kernel, `tools/perf-bench.py`
loads a checkout's kernel in-process and reports each builder's cost on
real-sized data; two checkouts can run against one copy for a before-and-after
comparison. Its module docstring is the reference.

### The chat wire's two protocols

A chat page announces the protocol it speaks in its `ready` frame. A bundle
that sends `{type: "ready"}` (an older page or extension) gets today's INDEX
frames: a session frame trimmed to the last 250 events with `headFrom` and
`headTotal` as indexes, `chatTail` deltas by index, `loadOlder` by index
answered by `chatHead`, all from a build over the whole transcript (its render
floor at turn 0 while such a client is connected). A bundle that sends
`{type: "ready", proto: 2}` gets the uuid-anchored frames, and the kernel
announces `chatProto2` in its `caps`:

- the session frame carries `proto: 2`, the post-boundary tail (the events from
  the assembly cut on, at most 250), `firstUuid` and `lastUuid`, `headKnown`
  (false until the head has been reached) and `headTotal` (a count only when
  the head is known, else null: the page shows no number); the cards above the
  first event (the system card, a `/clear` notice) ride as `headCards`;
- `chatTail` names the last unchanged event by `afterUuid`: the page truncates
  after it and appends; an anchor it does not hold is a gap (`needFull`);
- `loadOlder {id, before: <oldest resident uuid>}` is answered by `chatHead {id,
  beforeUuid, events, more}`; `more: false` is the head;
- `loadAround {id, uuid}` is answered by `chatWindow {id, anchor, events,
  moreBefore, moreAfter}` in one round trip (`missing: true` when the anchor is
  in no page); a window with `moreAfter` leaves the client DETACHED: it gets no
  delta until `loadNewer {id, after: <newest resident uuid>}`, answered by
  `chatMore {id, afterUuid, events, more}`, reaches the tail (`more: false`,
  the reply then carries the frame's status and ledger), or a `needFull`
  re-attaches it (the page's "Return to live" strip and its jump chip ask for
  one, and the full frame answering that ask merges into the held run it
  overlaps, so the pages the reader walked stay, the kernel's base keeping the
  run's older first edge with it (the page sends its newest resident keys with
  the ask, `reattachKeys`, and the kernel keeps the older edge when the highest
  of them still in the list lies inside the frame); every other full frame
  replaces the run, its
  in-list events being the fresh copies); a reconnect's `ready` starts a fresh
  base. A window that overlaps the run the client holds
  through the live tail, by turn span, keeps it attached (`connected`; a
  `loadOlder` advances the run's first edge, so the kernel's picture of the run
  follows the page's). A
  detached run whose edges left the transcript (a `/clear`, a fork, a rewind)
  gets a full frame; a `missing` reply on a held key is a gap the page answers
  with `needFull`. A reply that reaches the head carries the head cards first.
  Every slice of the list is turn-aligned. A remote kernel learns the protocol
  from a `ready` the page sends on each host socket's open; a redialed local
  socket carries it on its dial term (`&proto=`), since a redial posts no
  `ready`, and a page whose `ready` the kernel never answered posts it again on
  its next fresh dial. A socket whose `ready` has not arrived has no protocol
  yet and moves no render floor for its first thirty seconds; past that it
  counts as an index client.

The pages before the render floor are rendered on demand from the parse's
lazy atoms (a page hydrates its own turns), memoized in a bounded cache
(`/perf` `chatPages`), and equal the whole build's slice byte for byte
(`tests/test_chat_pages.py`). Every event carries a uuid, and a
`key` unique within its list (the uuid, or `uuid#n` for a second event built
from one record); the notes romp adds (a retry recovered, an effort change, an
orphan reply) carry synthetic uuids keyed by their second and ordinal.

## The file preview popover

Hovering a local file link in the chat (or focusing it from the keyboard) pops up
a card with the rendered head of the file, or the section a `path#slug` link
names, after a short dwell; it closes when the pointer leaves (with a grace to
cross into the card), on Escape, on a scroll, on a click elsewhere and at every
tab-strip rebuild. The card is the comment popover's card (its surface and its
fractions of the pane) and is never draggable or resizable; the romp loader shows
first and the text replaces it the moment it lands. The card carries no open
control: clicking the link itself opens the full file viewer, scrolled to the
section the link names.

**What a hover may fetch.** A hover is a gesture the user did not choose, so the
popover is stricter than the viewer (whose own rule, that any path the agent
named opens, is untouched). The kernel decides per link when it builds the
message and ships the verdict as `pathPreview` beside `pathLinks`, a map from
the message's token to the kind it may show: `markdown`, `image`, `code` or
`pdf`. Every judgement is of the **real** path (a symlink is what it points at,
and a link whose own name claims another kind than its target is refused; a hard
link is another name for the same bytes and no path check can see its other
names, so a `notes.md` hard-linked onto a `.env` passes the name rules and is
caught only by the content belt below). A
link absent from the map gets the text-only card (the path as words, the link
still opening the file) and **no request**: a path outside the session's folder and the user's home, one
the kernel could not verify, a secrets-shaped name (the `.env` family, `.netrc`,
`.npmrc`, `.pypirc`, any name carrying `credential`, `token`, `secret` or
`password`, `id_*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, key stores, and any file
under `.ssh`, `.gnupg`, `.aws`, `.docker`, `.kube`, `.azure`, `.gcloud`,
`.config/gh` or `.config/gcloud` in the home, matched without regard to case), a
kind the card cannot show, or a file over the caps (2 MB of text, 50 MB of
media). Under the name rules sits a content belt: a text shaped like a
credential (a private-key block, a key or token assignment, a provider token, a
JWT) is refused with "looks like a secret". For every verified link the kernel
does **not** allow, it ships the exact condition beside the kinds, as
`pathPreviewWhy` (token to why), and the text card says it: "shown as text: a
secrets-shaped name", "shown as text: looks like a secret", "shown as text:
outside the session's folder and your home", and so on; a link the kernel
shipped no verdict for at all says that instead. `pathPreview` rides every
message with verified links (empty when none previews), so a message sealed
before the kernel judged previews is rebuilt once and gains its verdicts.

**A session on another host.** A remote session's files live on that
machine's disk, so the card's fetches (the text slice and the image or PDF
bytes) ride this kernel's `/remote/<host>/file` relay with the bare session id,
exactly as the inline images do; the remote kernel builds that session's
messages and judges its own files, and the relay is available only while the
host is attached (a host reached through a relay alone shows the text card
until it attaches).

The belt reads the file's first
64 KB at load (so at warm time): a hit there means the file is never cached and
the link ships without a preview kind. It reads the served slice again on the
route: a secret past the first 64 KB passes the load-time read, so that file's
whole text does sit in the slice cache until eviction, and what the belt
refuses then is every slice that carries the secret (the section itself, or a
head long enough to reach it); a slice that does not carry it is served.

**The slice route.** `GET /file?path=…&sid=…&slice=1[&anchor=slug]` answers JSON
for a text kind: `kind`, `title` (the file's name), `text` (the file's head, or
the section from the heading whose slug matches through the line before the next
heading of the same or a higher level; capped at 64 KB, `truncated` when cut),
`found` (false when the anchor names no heading: the head is served and the card
says so in one line), `heading` (the section's own: level, text, slug, line),
`size`, `mtimeNs`, `hit` (the slice came from the cache); the heading index
stays on the kernel's side. The card stamps `data-render-ms` (the dwell's end to
its rendered content) and `data-slice-hit` on itself, so the served test reads
the latency off the card and pins the cached markdown case under 250 ms. For an image or
a PDF the same route answers the metadata only; the bytes ride the plain route.
A path the popover may not render answers 403 with `why` (the content belt
included); a text kind whose bytes are not text answers 415. Heading
slugs follow GitHub's rule, the same one the file viewer gives its headings
(`md-links.ts`), duplicates numbered `-1`, `-2`; the two ports are pinned over
`tests/fixtures/heading_slugs.json`.

**Near-instant.** The kernel keeps the text of recently linked markdown and code
files with their heading index, keyed on the path and its `mtime_ns` (a rewrite
is a new entry and the old one goes), bounded to 64 entries and 8 MB, least
recently read out first. The cache is warmed on the pusher's path: when the
message builder verifies a markdown link in a message about to ship, the file is
read and indexed then, so the hover's fetch is a hit. Never on a timer, never a
watcher: the events are the message build and the hover. `GET /perf` reports the
route under `fileSlice`: `hit`, `miss`, `bytes` served and `warm` (entries the
builder filled ahead of a hover).

**The content contract** (`ui/webview/file-preview.ts PreviewContent`). The card
renders one shape whoever fills it, so another provider can land its answer in
the same card:

```
{ kind: "markdown" | "section" | "image" | "code" | "pdf" | "text" | "term",
  title: string, subtitle?: string,
  body: { markdown?: string, html?: string, text?: string, url?: string, lang?: string },
  note?: string,
  open?: { label: string, path: string, frag?: string } }
```

Stage 1 fills it from the slice route (`markdown`, `section`, `code`) and the
bytes route (`image` at its natural size capped to the card, `pdf` as its first
page), or with the text-only card; a glossary term (below) is a path link to the
glossary file's section and previews as one, through the same slice route. A
previewed document renders on the
sanitizer's inert DOM and is stripped of every remote load there, before its
nodes join the page: an image's `src` or `srcset`, a picture's sources, a video's
poster or source, an audio, an SVG image, in any spelling the URL parser
resolves to another origin (a protocol-relative `//host`, backslashes, a tab or
newline anywhere in the value, which the browser deletes before it reads the
URL). An image becomes its alt text and the rest go, so a hover never sends a
request elsewhere; a previewed document's images load only from this kernel
(the file route, a relative path, a data: URI). The card closes when the link it
is anchored to leaves the document (a re-render, a tab pick), not on the tab
strip's rebuilds. The markdown grammar renders `[[wikilinks]]`
as their plain text and callout blockquotes (`> [!NOTE] …`) as blockquotes with
the kind as a small label, in the chat and in the viewer alike. Pending the lab
team's glossary format: a per-project glossary file whose headings (and their
aliases) are linkified in assistant text, mail bodies and cards at render time,
and a `GET /glossary/<term>` route answering `{title, markdown, source_path,
anchor}` that fills the `term` kind of the same card.

## The glossary

A team's coinages, linked where they are written. A linked term is an ordinary
link to the glossary file's section (the link colour, a solid underline, the
pointer): hovering it shows that section through the file preview, exactly as
hovering any file link with a section does, and clicking it opens the glossary
in the viewer at the heading; there is no term card of its own. One file per romp tag group,
`~/.claude/glossaries/<group>.md` (under `CLAUDE_CONFIG_DIR` when set), in the
grammar of that folder's README: an opening `## Not coinages` list of words never
linked (each bullet's bold lead, or the text before its colon, read as words), then
one `## <term>` section per coinage with a definition paragraph and the labelled
bullets `plain words`, `also` (aliases, spaces allowed), `scope`, `status`
(unconfirmed, confirmed, retired), `registered` (`<date> by <session>`) and
`link` (`all`, `first`, `off`; default `all`). A chat message is resolved
against its author's group: the session's tag group's file, else its own name's;
a mail body shown in a session's chat links the READER's group (the chat
session's index; the sender's group is a later refinement). The repo-local
`docs/glossary.md` is a seam kept for a second source with no file today.

The kernel parses a file once per `(path, mtime)` and ships each session a
`{type: "glossary"}` frame on the pusher's cycle, on its own dedup slot like the
comments frame (the stat is the event; no timer, no watcher): `group`, `path`,
`mtime`, `skip`, `terms` (term, slug, definition, plain words, also, scope,
status, registered, link) and `truncated`, the count of entries cut by the
index's byte cap (256 KB) or lying past the heading index's ceiling (256
headings), counted in `/perf` under `glossary` beside the parses and the frames,
terms and bytes BUILT per cycle (the dedup slot decides what is shipped). A file
over the preview route's 2 MB read ceiling is not read; the parsed cache holds
sixteen files, least recently read out first. Slugs come from the file's headings in order
through the viewer's own rule, the Not-coinages heading included, so a card opens
the viewer on the heading the viewer gave that id.

The chat page compiles one matcher per index (`glossary-links.ts`): every form
(the term, its aliases, and their plurals by the everyday rule; nothing shorter
than two characters) whole-word and case-insensitive, longest first, minus the
skip list (a listed word, its plurals and any alias equal to one of them), over
the prose of assistant and user text and mail bodies; never code, links,
headings, math, the composer, tool heads, the timeline, nor inside a path-shaped
or host-shaped token (a path the kernel could not verify stays plain, unsplit).
A term split across text nodes by an inline element is not matched. Each occurrence becomes a `.term-link` span carrying
the glossary path and the term's slug, exactly like a path link's absorbed
section: the same hover card (filled from the index, no fetch) and the same
click (the viewer at the heading). `link: first` links the first occurrence per
message; `off` links nothing; a retired term greys and its card says to use the
plain phrase. A new frame re-links the session's rendered view.

`GET /glossary/<term>?sid=` answers `{title, markdown (the whole section),
source_path, anchor, group, status, link}` for the lab's own consumers, matching
the term or an alias whole-word and case-insensitive; 404 with the paths tried
when the group has no file or the term is absent.

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
  `fed:feed` and `feed` add up to the frame's cost. The federation layer hands
  its merged frames (`feed`, `tabOrder`, `data`, `bars`) to the pane's handler
  by direct call once the pane has registered it (`window.__rompFed.onFrame`,
  through `ui/webview/frame-listener.ts`), so `fed:<type>` is that layer's own
  compute; it dispatches them on `window` only when nothing registered, and
  every other frame still arrives as a `window` `message` event. A `message`
  listener from another JavaScript world (a browser extension's content
  script) that reads `event.data` receives a structured clone of every frame
  dispatched on `window`, tens of milliseconds for a multi-megabyte board; the
  direct call keeps the merged frames out of its reach. See "A message
  listener from another world" in `CONTRIBUTING.md` for the check that finds
  such a listener. The timeline's listener is wrapped the same way on both
  hosts (the VS Code bundle directly; the kernel page's inline boot through
  the `window.__rompPerf` that `federation.js` publishes before it runs), so
  `data`, `bars`, `hover`, `activeChat`, `revealEvent` and `models` are timed
  like any pane's frames. The file viewer (`ui/webview/file-view.ts`) brackets
  each paint of a shown document's text body (a file on disk or a markdown URL,
  as rendered markdown or as the code view) as `fileview:paint` under the pane
  that hosts it (`chat` or `feed`), so painting a large document shows per
  minute beside the pane's frames, with the main-thread-free sample the
  collector takes after it.
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
- The dashboard shell (the top-level window that frames the panes) runs the
  same collector under app `shell` with no frame types at all
  (`ui/webview/shell-perf.ts`): Chromium reports a long animation frame to
  the top-level document and never to the iframe whose script ran it, so a
  pane script that blocked the main thread is attributed in the shell's row
  (`chat.js:paintAll@9000`) and nowhere else. The row goes over the shell's
  own socket; up to twenty rows are held, oldest dropped first, while that
  socket is closed, and go ahead of the next row once it is open. A browser
  that reports neither long animation frames nor long tasks gives the shell
  nothing to observe, and an idle minute posts nothing, so no shell row
  appears there.
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
  `timeline`), or `shell` for the top-level window; `since` is the minute's
  start on the browser's clock (epoch ms) and `span_ms` its length (shorter
  than a minute when the page was hidden or closed); `hist` is the 14 bucket
  counts; `free` is null when no sample was taken; `loaf.top` is the five
  largest keys by summed duration, `inv` the last invoker seen for each
  (`WebSocket.onmessage`, `Window.requestAnimationFrame`, `DIV.onclick`),
  `src` is `loaf`, `longtask` or `none`; `slow` counts the slowframe rows sent
  and the slow frames past the cap, with the worst of those; `heap_mb` is
  `performance.memory.usedJSHeapSize` and is absent outside Chrome; `dom` is
  the element count; `visible` is the document's visibility, `hidden_pane`
  the pane shim's test for a pane the shell has set to `display:none`: its
  zero-viewport probe, or the word the pane published as
  `window.__rompPaneHidden` from its own visibility events; `ua` is
  `chrome-desktop`, `safari-ios` or `other`.
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
plus how many more there were. The shell's row shows as one more pane of its
dashboard: no frame types, the long frames it observed and the pane scripts
they name. An absent file or one without perf rows is
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

The telemetry describes what the panes did while people used them. To measure
a pane change before and after on the same input, `tools/ui-bench.mjs` replays
a recorded or synthetic frame stream into the real pane page in a headless
Chromium and reports where the browser's time went; the "Measuring dashboard
pane performance" section of CONTRIBUTING.md describes it.

## The API-health signal

`GET /api-health` returns one JSON document describing how the API is treating
the sessions this kernel runs. It is computed from frames the kernel already
parses: the per-attempt retry frame, each successful response, and the settle
of a turn the CLI gave up on. The route takes the serve token, like every read
that is more than a bare counter; `romp api-health` prints the document. The
kernel takes no action on it: a consumer reads the signal and applies its own
policy (move traffic to another key, hold a batch).

Events are bucketed by **auth-source label** and **model family**
(`"<auth>|<family>"`, for example `key:helper|fable`), because rate
limits are per model family per account: pooled, one family's storm disappears
under another family's clean traffic. The auth label comes from the
`apiKeySource` the CLI reports at init. A key is labelled by its source word,
never by its material, since the kernel holds no key: `key:helper` for a key
the helper supplied, `key:env` for one the CLI found in its own environment,
`key:managed` for a managed login key, and `key:<source>` for any other source
word the CLI enumerates, lowercased. Two accounts behind one helper are one
bucket. A login is labelled by a salted digest of the account digest the usage
bars stamp, so the same login gives the same label within one install, and
nothing about the credential itself is in any label. The salt lives at
`STATE/api-health-salt`, minted once at 0600; an empty file makes a login's
label the account digest itself, so a bucket can be matched to the log.

### Top-level fields

- `schema`: `1`, incremented on any incompatible change.
- `asOf`: wall-clock epoch seconds at which this response was computed from the
  event ring. Every window and every state is computed at read time, so `asOf`
  is the response time. A clock step moves it; a reader that wants a freshness
  check a clock step cannot fake uses `seq`.
- `bootId`, `bootAt`, `uptimeS`: the kernel process identity, the same id
  `/version` and `X-Romp-Boot` carry. `bootAt` is the boot's stamp in this
  signal: the kernel's start truncated to the millisecond, the precision of
  every other stamp in the payload, or, when the previous kernel's last
  transition overlaps the start, one millisecond past that row; every bucket
  the boot seeded carries this same number as its `stateSince`, and so does
  every row the boot filed. `/version`'s `started` is the whole-second boot
  time. A changed `bootId` means a restart, and the windows restarted with it.
- `complete`: true once the longest window (900 s) fits inside the uptime.
- `seq`: count of ring events (attempts, successful responses and give-ups)
  ingested since boot. Monotonic within a boot: two reads with the same `seq`
  saw no traffic in between.
- `lastEventAt`: the newest event of any kind in the ring, across every bucket.
- `coverage`: `sdkSessionsLive` (SDK-backed sessions the backend holds that have
  not ended); `inTurn` (of those, sessions with a turn in flight: working or
  retrying); `retrying` (sessions inside a retry storm right now, the cheapest
  direct thrash indicator, independent of the ratio thresholds);
  `sidechainExcluded` (a constant `true`: subagent traffic is
  outside the signal on both sides of the ratio). Codex-backed sessions carry no
  Anthropic API traffic, are outside the signal, and are counted in none of
  these. A reader that sees `inTurn >
  0` and a `lastEventAt` minutes old should treat the signal as unknown rather
  than healthy.
- `cliScope`: scope bookkeeping carried on this payload, not part of the API
  signal itself: the per-session scopes (see "What survives a restart" and
  "Per-session memory limits").
  - `on`, true when the kernel chose at boot to run CLIs in scopes.
  - `fallbacks`, CLI launches since boot on which the scope wrapper's pre-flight
    scope failed, so it ran the CLI directly and reported `romp-cli-scope:
    fallback: …` on stderr. Each is also a problem line in the kernel log, in
    exactly this form: `cli scope: session <name> (<sid8>) started its CLI
    outside a scope — <line>`, where `<sid8>` is the first 8 characters of the
    session id and `<line>` is the wrapper's whole stderr line, its
    `romp-cli-scope: fallback:` prefix included. The wrapper's refusal,
    `romp-cli-scope: refused: …` (`ROMP_CLI_REAL` unset, exit status 127), is
    not counted: no CLI starts, and the failure is reported on the session's
    error card. `lastFallbackAt`, epoch seconds of the newest fallback; `null`
    when there was none. `on: true` with `fallbacks > 0` means scopes were on at
    boot and some launches ran without one: those sessions' work is in the
    service cgroup, and a service restart kills it.
  - The limits: `memoryMax`, `memoryHigh`, `memorySwapMax` (the size strings)
    and `oomScoreAdj` (an integer), each `null` when its variable is unset, when
    its value was rejected, or when the scopes are off (no scope starts, so no
    limit applies).
  - `rejected`, the names of the variables whose values were refused, by their
    rule or by this machine at the kernel's start (memory properties systemd
    rejected; an adjustment the process could not write), each also a problem
    line at the kernel's start.
  - `memoryControllerDelegated`, the kernel's start-time check of whether a
    probe scope carrying the memory properties had a `memory.max` file in its
    cgroup: `true`, `false` (systemd holds the sizes above and applies nothing;
    also a problem line), or `null` when no memory limit is set, the scopes are
    off, or the check could not be settled. A `null` beside a memory limit shown,
    with `on: true`, is that last case: a check at the kernel's start did not
    answer, and `unsettled` says which.
  - `unsettled`, the names of the kernel's start-time checks that were due and
    settled nothing: `memoryLimits` (the probe scope carrying the memory
    properties did not answer, or failed both with and without them),
    `memoryController` (the check inside it gave no marker), `oomScoreAdj` (the
    throwaway child's write did not answer). Empty when every due check
    answered, and when none was due (the scopes off, no limit set). A value
    listed above whose check is named here is set and handed to the wrapper as
    read, and whether it applies is not known at the kernel's start; the other
    fields cannot show this (`oomScoreAdj` present, `rejected` empty and
    `memoryControllerDelegated` `true` read the same whether the adjustment's
    check answered or not). Each named check is also a line in the boot log
    saying why.
  - `limitsIgnored`, wrapper `romp-cli-scope: ignored: …` lines since boot (each
    also a problem line, `cli scope: session <name> (<sid8>) started its CLI
    without a per-session limit — <line>`). It counts lines, not launches: one
    launch writes one line for each value the wrapper refuses, one for the
    memory properties together when systemd rejects them, and one for an
    adjustment it could not write. `on: true` with a limit set and
    `limitsIgnored > 0` means a value the kernel accepted at its start was
    refused at a launch: the machine changed under the running kernel, or the
    value reached the wrapper outside the kernel's hand-off (see "Per-session
    memory limits").
- `config`: the constants in force (see "Derived state").
- `overall`: `state`, the most severe state among buckets that are not
  `unknown` (`thrashing > degraded > recovering > healthy`; `unknown` when every
  bucket is), and `worstBucket`, the bucket that set it. There are no pooled
  windows: summing 429 rates across auth sources mixes unrelated quotas.
- `buckets`: keyed `"<auth>|<family>"`.
- `transitions`: the last 50 state transitions across every bucket, newest
  last, each `{t, bucket, auth, family, from, to, why, evidence}`.
- `rate429Basis`: the constant `"attempts"` (see "Windows").

### Windows

Each bucket carries three windows (`60`, `300`, `900` seconds, ending at
`asOf`), each with:

- `requests`: attempts with a status, `ok + rateLimited + overloaded +
  serverErrors + otherErrors`. `noStatus` (a connection-level failure) and
  `gaveUp` sit outside the sum: a give-up is already inside one of the status
  counters, since the exhausting attempt emits no retry frame and the settle is
  the only place it can be counted.
- `rate429` = `rateLimited / requests`, `rate5xx` = `(overloaded +
  serverErrors) / requests`; both `null` at zero requests.
- `retries`, `sessionsRetrying`, `turnsRetrying`: attempts, distinct sessions
  and distinct turns with at least one retry in the window.
- `complete`: false while the window is longer than the kernel's uptime.

`rate429` is an attempt share, not a request share: one stuck turn contributes
up to `max_retries` attempts. The payload says so (`"rate429Basis":
"attempts"`); read `sessionsRetrying` and `turnsRetrying` beside it to tell one
stuck session from a saturated key. A high `rate429` with `gaveUp` at zero is
traffic being slowed, not blocked; a consumer whose action is expensive should
require `gaveUp` or `turnsRetrying` over its own span, not `state` alone.

The signal covers each session's main thread only. A subagent's retries never
reach the kernel (the CLI folds them into a progress frame the SDK drops), so
its responses are not counted either; counting one side would dilute every
rate during a storm. `coverage.sidechainExcluded` is `true` to say so.
The judges' own calls have no SDK stream and are outside the signal.

Retries carry no model field, so they are attributed to the session's
last-learned family: attempts between a mid-storm model fallback and its first
successful reply file under the previous family. Successful responses use their
own model and are exact.

### Per-bucket state fields

- `state`: `unknown`, `healthy`, `thrashing`, `degraded` or `recovering` (see
  "Derived state").
- `stateSince`: epoch seconds of the read that recorded the transition into the
  current state. Every transition is stamped with the time of the read that
  found it (`transitions[].t`), and `stateSince` is that stamp for the newest
  one, so `asOf - stateSince` is how long the state has held as observed. For
  `unknown` it is the read that found no qualifying window, or the boot time
  after a restart.
- `evidence`: `{window, rate429, rate5xx, n}`, the window that decided the
  newest transition, its two rates and its `requests`, recorded at that
  transition and kept with the state; they are the numbers the transition's
  `why` carries. When the state is `unknown`, `window` and the rates are `null`
  and `n` is `requests` over 900 s at read time.
- `why`: the newest transition's reason in words, the same string as its row.
- `transitions`: this bucket's own last 50 transitions, newest last, in the
  same row shape as the top-level list. It is kept per bucket, not filtered
  from the top-level list, so a neighbour that churns through fifty
  transitions does not push this bucket's history out of view.
- `lastError`: the newest attempt or give-up that was not a success, from
  memory only (lost at restart): `at`; `status` (the HTTP status, or `null`);
  `category` (the CLI's error category string, for example `rate_limit`,
  `overloaded` or `server_error`; `null` when the frame carried none); `class`
  (the counter it landed in); `kind` (`retry` or `gaveup`). There is no text
  field, by design: the wire carries none today, and the transcript's 429 text
  names the organisation and the model.
- `series`: attempts per minute over the longest window, for a graph: `binS`
  (60), `from` (the start of the first bin; the last bin ends at `asOf`), and
  five arrays of one integer per bin, oldest first: `ok`, `rateLimited` (429),
  `serverErrors` (529 and other 5xx, the `rate5xx` numerator), `noStatus`
  (connection-level failures) and `other`. Additive: the field arrived after
  the document's other fields and `schema` stayed `1`; a reader that ignores it
  sees the document it always saw.

### On the dashboard

The shell's rail carries one dot for the signal, placed after the `API` label
of the spend readout: the accent colour when every connected kernel is fine,
red when errors are being met anywhere (a 429 storm, 5xx failures, a machine
offline, auto-retry paused), and the label gray when no kernel has API traffic
in the windows. The hover reads the document as counts, never as the state
machine's vocabulary: one line per machine, named by its kernel's own name,
with its successful requests in the accent and each failure class counted in
its own colour only when present (429s in the blocked red, 5xx with 529 in the
5xx magenta, no-connection and other-status failures in the other band's own
hue: a pale lime in the dark theme, an indigo in the light); no
traffic reads as "no API traffic"; a machine whose sessions are waiting or
whose kernel is paused shows that kernel's own words instead. The window the
lines count is named once at the top, this kernel's: the ledger's last 24
hours (a peer still on an older kernel counts its own longest window, and its
histogram says so). There is no summary sentence: the lines do the work,
and a machine not reachable keeps its own line saying so. The word `unknown`
stays in the document and appears nowhere on the dashboard. Under the lines,
the **History** draws one stacked histogram per machine from the `ledger`:
one bar per bin, successes in the accent, 429 attempts in red and 5xx in
magenta stacked on them, and a band of its own hue (a pale lime in the dark
theme, an indigo in the light) for no-connection and other-status failures
only when the range or a counted line holds any; one ceiling label, no peak
figure; along the bottom the clock times of the timeline pane's own axis (its
formatter and tick rule, lifted verbatim: local clock times at the timeline's
tick step (ten minutes on the hour range, three hours on the day), a tick of its
own at each local midnight the span crosses carrying that
day's date, and dates alone once the step is a day or more), never ages; a vertical, left-justified
legend whose class tokens (`429`, `5xx`, `other`) wear their colours with the
explanation beside them in plain text (429 on one line, 5xx below it, the other
line only when it applies; the accent band needs no row); the age of the read in words ("read
now", "read 3 minutes ago"), the time since this machine's document landed
measured on the browser's clock alone, recomputed at every repaint. The hover
draws the last 24 hours as 96 quarter-hour bars. A click on the dot (or Enter)
opens the detail, a centred modal in the spend modal's grammar: the same lines,
the waiting sessions and the pause control, and one large histogram per machine
with range chips for 1 hour (60 one-minute bars), 24 hours (96 quarter-hour
bars) and 7 days (168 hourly bars); the hover is unchanged by it.

The signal covers every connected kernel, not only the one serving the page.
Each kernel serves its own last shell frame at `GET /api-health/frame` (its
local half only, never its view of its peers), and the tunnel supervisor polls
every attached host's frame (once per supervisor pass, about every 15 s; kept on
a blip; kept and marked with a `fault` when the read is refused, a 403 from a
rotated token or a 500; cleared when the host answers that it has none) and
carries them in the shell frame under `hosts`, a map keyed by host name with
each machine's `state`, class, headline, waiting count, since, pause reason,
its `quiet` and `errs` flags when that kernel sends them, and a `stale` mark
when that tunnel is not up or the read was refused (the frame's own `type`,
`sessions` and `seq` stay on their kernel). The frame's `quiet` says that
kernel saw no API event in its longest window and `errs` counts the attempts
that failed in it; both come from the aggregator every cycle, so the frame
changes, and is pushed, the moment the last failure ages out. The dot follows
the frames alone: red when any reachable machine's frame is degraded, paused or
holds a failed attempt (`errs`), gray when every reachable machine's frame says
quiet, the accent otherwise; a machine whose tunnel is down or whose frame
could not be read is named in the popup and has no say. The hover's history
reads each attached host's document through `GET
/remote/<host>/api-health`, a read relay beside the `/ws` and `/file` relays:
the local token gates it, the remote's own token goes in the forwarded request,
its document passes through as answered (404 for an unknown host, 502 when the
tunnel is down). The merge happens in the browser and follows the federation
rule: per-host maps in, one line per machine out, the worst state wins for the
dot, and no count or clock is ever added to or compared with another kernel's.

### The ledger

Every attempt is also folded, the moment it lands, into `ledger`, a per-bucket
set of fixed-width bins behind the dashboard's histograms: `minute` (60
one-minute bins, the last hour), `fiveMin` (288 five-minute bins, the last 24
hours) and `hour` (168 hourly bins, the last 7 days), each tier an object with
`binS`, `from` (the first bin's start) and one integer array per class (`ok`,
`rateLimited`, `serverErrors` with 529, `noStatus`, `other`), oldest first, the
last bin the one holding `asOf`, zeros where nothing landed. The event ring
holds only the windows' span, so this is what lets the popup show the day and
the detail the week. Bounded: at most 516 bins per bucket, under about 100 KB
per bucket in memory when every bin has traffic and about 17 KB in the state
file (about 33 bytes a bin); buckets (auth times family) are few. A bin past
the event being folded (a clock that stepped back left it) is dropped with the
stale ones, so no phantom bar resurfaces when the clock reaches it. It is
written to `api-health.json` with the state (on a transition, and on the first
event of each new minute, monotone, so a restart loses at most the current
minute) and restored at boot, malformed pieces skipped and counted in the log.
Additive: a reader that ignores it sees the document it always saw.

### Derived state

`state` is computed at read time as a pure function of the bucket's event ring,
the last persisted `(state, stateSince)` and `asOf`. It has no other inputs and
no thread of its own.

- `unknown`: no window of the bucket has `requests >= minRequests` (10). Any
  state moves to `unknown` when that is so; it is also the state after boot and
  the state of a bucket whose traffic has stopped. `stateSince` is the read
  that found it so. From `unknown`, the first read with a qualifying window
  classifies afresh: an enter condition gives `thrashing` or `degraded`,
  otherwise `healthy`. `unknown` keeps no memory of the state before it; a
  consumer that wants to join an incident across an `unknown` gap reads
  `transitions`.
- `healthy`: the default once there is evidence.
- `thrashing`: the 429 share is high. The actionable state: a consumer can move
  traffic to another key or organisation.
- `degraded`: the server-side error share (`overloaded` plus `serverErrors`) is
  high while the 429 share is not. A provider-side problem another key may not
  fix, so a separate state.
- `recovering`: the exit condition has been met, but the hold time has not
  passed.

The transitions follow, with the constants that `config` echoes. A rule reads a
window only when that window has `requests >= minRequests`:

- Enter `thrashing`: `rate429(300 s) >= enter429` (0.20), or `rate429(900 s) >=
  enter429Slow` (0.15), or `rate429(60 s) >= enter429Fast` (0.50) with
  `requests(60 s) >= fastMinRequests` (20). From `healthy`, `recovering` and
  `unknown`, and from `degraded` at once: `thrashing` takes precedence over
  `degraded` whenever the 429 condition holds, on entry and afterwards.
- Enter `degraded`: the same conditions on `rate5xx` (`enter5xx` 0.20,
  `enter5xxSlow` 0.15, `enter5xxFast` 0.50) while the 429 condition does not
  hold, from `healthy`, `recovering` and `unknown`. There is no direct
  `thrashing -> degraded`: leaving `thrashing` goes through `recovering`, and
  `recovering -> degraded` fires in the same read when the 5xx condition holds
  (two rows with one `t`).
- `thrashing -> recovering`: `rate429(300 s) <= exit429` (0.10) and
  `rate429(900 s) <= exit429`, both windows qualifying, held at every instant of
  the last `holdS` (120 s). `degraded -> recovering`: the same on `rate5xx` with
  `exit5xx` (0.10).
- `recovering -> healthy`: both exit conditions (429 and 5xx) held throughout
  the last `holdS`, and `asOf - stateSince >= holdS`. Both are required because
  the persisted state is `(state, stateSince)` alone and nothing says which
  state `recovering` came from. A bucket with one rate between its exit and
  enter thresholds stays `recovering`, which is the accurate label.
- `recovering -> thrashing | degraded`: the enter condition again, immediately.

Enter and exit thresholds differ, exits need a hold on two windows, and every
decision needs a minimum sample, so a bucket near a cap does not flap. A
reading between exit and enter (0.10 to 0.15 on the 900 s window) holds the
state however long it lasts, and traffic too thin to qualify the 300 s window
cannot satisfy an exit, so it holds the state too; the windows beside the state
show what the traffic is doing.

"Held throughout the last `holdS`" is decided exactly, without sampling. A
window's counts change only at breakpoints, the instant an event's timestamp
enters the window and the instant it leaves, so the exit condition is
evaluated at `asOf - holdS`, at `asOf` and at each breakpoint between. Evaluating the 900 s window
at `asOf - holdS` needs events back to `asOf - 1020`, so `config.retentionS`
is 1020 and the ring keeps nothing older. A read that finds a transition stamps
it with `t = asOf`, appends it to `transitions`, rewrites the state file and
logs one line in the kernel log (`api-health: <bucket> <from> -> <to> — <why>`).
A reader polling every few seconds observes every transition within one poll of
its breakpoint; a sparser reader observes the state at its read times and the
transitions those reads find, and nothing in between: a state entered and left
between two reads is not recorded, and `recovering -> healthy` needs a read at
least `holdS` after the read that entered `recovering`. Nothing derives while
nobody reads.

### Persistence and restart

A read that observes a transition rewrites `STATE/api-health.json`, whole and
atomically (a temp in the same directory, then a rename). The file holds each
bucket's `state`, `stateSince`, `why` and `evidence` and the `transitions`
tail, so it stays bounded however many transitions pass; per-request events
are never written. The event ring itself is in memory only, so a restart
empties the windows: `seq` restarts at 0, `bootId` changes, `complete` stays
false until each window fits inside the new uptime, and every bucket the state
file knows comes back `unknown` with `stateSince` at the boot's stamp. For each
bucket whose persisted state was not already `unknown` the reload files
`<state> -> unknown` at that stamp, so the transitions list is continuous across
the restart, and the first read with enough evidence records `unknown -> <state>`
after it. The boot's stamp is the kernel's start truncated to the millisecond,
or one millisecond past the newest transition the file carries when that one
is not before the start (the previous kernel filed it after this one started,
or the clock stepped), so the restart row is always the newest row; the payload
serves that stamp as `bootAt`, and the kernel log says when it was moved. The
pre-restart state is not carried over: an empty ring is no
evidence. A state file, or an entry in it, that cannot be read is skipped and
logged, and never keeps the SDK backend from starting.

### The bottom bar's indicator

The dashboard's bottom bar carries an API cell (one small dot, placed inside
the spend readout right after its `API` label; see "On the dashboard" above
for its colours and its reading) whose frame is computed independently of this
signal, from two things the kernel owns directly:

- Each alive session's newest transcript API-error record, latched until the
  session produces assistant output again (a user prompt does not clear it,
  romp's own retry included), plus the live retrying state of SDK sessions.
- The retry-pause file (`retry-paused.json` under the state directory). A
  pause writes `paused`, `t` (when it began, the auto-resume floor) and its
  `reason`: `limit`, `spend`, or none for a manual stop. A spend pause adds
  `bills`, the billing the capped session was on (`login` or `key`); only
  fresh assistant output from a session on that billing lifts it. Un-pausing
  a spend pause, by that lift or by the Resume button, records `liftedAt`
  (the time of the output record that lifted it, or of the Resume click) and
  `supersedes` (the floor of the pause it cleared; informational, nothing
  reads it); both ride every later write until a newer spend un-pause
  replaces them, and a spend-limit record older than `liftedAt` engages
  nothing, since the lift already ruled on it. A limit or manual un-pause
  records neither. A limit pause lifts when the usage report stops naming an
  account-wide window at 100%, a manual pause when any live session not
  blocked on an API error writes to its transcript after the pause began.
  When a limit pause lifts while a spend-limit record is standing, the file
  reads unpaused for one cycle before the spend pause engages: each writer
  rules on one signal per cycle, and the spend engage runs before the lift in
  the pusher's order, so it sees a paused file and rules on the record the
  next cycle.

The kernel pushes the cell's frame to shell clients only when it changed, and
again to a shell that sends `ready`:

```json
{"type": "apiHealth", "state": "ok | degraded | paused",
 "cls": "429 | 529 | offline | errors | ''", "reason": "'' | limit | spend | manual",
 "text": "<the rail's words>", "waiting": 0, "retrying": 0, "blocked": 0,
 "since": 0, "seq": 0, "quiet": false, "errs": 0, "host": "<this kernel's name to its peers>",
 "sessions": [{"sid": "", "name": "", "color": null, "kind": "retrying | blocked",
               "cls": "", "status": null, "since": 0, "suppressed": false}],
 "hosts": {"<host>": {"state": "ok | degraded | paused", "cls": "", "text": "", "waiting": 0,
                      "retrying": 0, "blocked": 0, "since": 0, "reason": "", "quiet": false,
                      "errs": 0, "stale": false, "fault": "HTTP 403 (only when the last read was refused)"}}}
```

`seq` counts the retry-pause file's writes since the kernel started. A press
on the detail's pause button writes that file, so the frame that answers the
press carries a moved `seq` whatever state it brings, and the shell clears
the button's acknowledgment on it; a frame from before the press carries the
old one. It is an event counter, not a clock, and restarts at 0 with the
kernel. `waiting` is `retrying` plus `blocked`. `cls` is the plurality class
over the affected sessions, ties resolved 429, then 529, then offline, then
errors. `since` is the pause's time when paused, else the earliest affected
session's event (a record's timestamp, or the retrying turn's start), else 0.
Every timestamp is an event's time, never the clock, so an
unchanged world sends nothing. On-you failures (a too-long prompt, a spent
model allowance, a dead credential, a refusal) are not counted; a spend cap is,
and engages the `spend` pause in the same cycle. `quiet` is true when this
kernel's API-health aggregator saw no event in its longest window (or the
kernel has no SDK backend), the fact behind the dot's gray before any history
is read. `host` is this kernel's own name, the one its peers know it by
(`_self_host`): the popup's line for this machine carries it instead of "this
machine". `hosts` is every attached
machine's own frame as the tunnel supervisor last heard it (the fields above
minus `sessions`, `seq` and `host`, which stay on their kernel; the map's key
is the name), keyed by host name,
with `stale` true while that tunnel is not up; a kernel with no attached
machines sends an empty map, and a kernel serving `GET /api-health/frame` to
a peer sends its own frame without this map, so two kernels attached to each
other never nest each other's view.

The cell's hover and its click detail carry a **History** section read from
this signal: the shell fetches `GET /api-health` for this machine and `GET
/remote/<host>/api-health` for every host in the frame's `hosts` when the
hover or the detail opens, and again when a frame lands on an open one,
authenticating with the dashboard's own cookie the way its other reads do.
Nothing polls; the frame carries no history and is unchanged. Each machine's
document is read in the plain words of "On the dashboard" above: over the
longest window of `config.windows`, `requests` plus `noStatus` are the
attempts, `rateLimited`, `serverErrors` (with `overloaded`), `otherErrors`
and `noStatus` the failures, and `gaveUp` the turns that gave up; the lines
count the `ledger` instead when the kernel serves one (its five-minute tier,
the last 24 hours): successes as "N successful requests", each failure class
counted in its colour when present, no attempts as "no API traffic"; the state
machine's word itself is never shown. Under the lines sit the histograms from
the `ledger` (one per machine, the tier the range names, summed bin by bin
across one kernel's buckets; an older kernel's document, which has no ledger,
draws its 15-minute `series` the same way), then the legend, and this
machine's State changes: up to
four rows of `transitions` newest first with the state entered in plain words
(`rate-limit storm`, `API failing`, `recovering`, `fine`, `quiet`) and how
long it held (until the same bucket's next transition, `so far` for the
current one; a hold from before `bootAt` ends at the boot, since every bucket
comes back `unknown` at a restart; a bucket the boot seeded is `unknown` since
`bootAt`: the boot time or, when an older kernel's last row overlaps it, one
millisecond past that row, because the backend seeds its `stateSince` with the
stamp it serves as `bootAt`), and the payload's `asOf`. A row the boot filed
(`<state> -> unknown`, its `why` the restart reason) reads `kernel
restarted`; where the tail crosses `bootAt` without such a row (the bucket
was already `unknown` when the previous kernel stopped, so the boot filed
nothing), a `kernel restarted` divider is inserted, and it takes none of the
four slots. A read that fails (a non-2xx, no answer, or an answer without
the signal's shape) shows one line saying so in place of the rows,
never the previous numbers.

## Where things live

State is written under `${XDG_STATE_HOME:-~/.local/state}/romp/`. Transcripts
are read in place from where Claude Code writes them (`~/.claude/projects/`)
and never copied.

The self-updater's report, `update-report.json`, is read once, by the next
kernel boot or by the running kernel's banner poll, and archived as
`update-report-last.json`; `update.log` beside it has the updater's full
output. An update that landed on disk but was not restarted into (no manager,
or a manager that did not take the restart request or did not answer it within
60 seconds) is filed in the Log with the step that runs it: `romp refresh` when
a manager is there, `romp up` when none is. A boot that already runs the landed
release says so instead of asking for another restart. A report that is not a
JSON object is moved aside, never deleted, to
`update-report.json.corrupt-<UTC stamp>` (the same `-1`, `-2` suffix rule) with
one Log entry under the `refused` kind; one that cannot be moved stays where it
is and is said once per fault.

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

Two files there record restarts. `restart-audit.jsonl` gets a row from
whatever asks for one: `romp refresh`, `romp down`, the dashboard's restart
button, the kernel's own update, and the manager before each SIGTERM it sends
(action `manager-sigterm`, with a `trigger` naming what set it off: `restart`,
`restart-all`, `refresh` for the stale-manager self-bounce, `cli-down` for a
stop while `romp down`'s marker is on disk, `stop` for any other). When a
SIGTERM arrives, the kernel reads the last two hundred rows,
newest first, for a request within the last 90 seconds (20 minutes for a
request that asked to wait for a quiet window) and no older than its own
start: a request that predates the process was delivered to the kernel before
it, so the walk ends there, except for a quiet-window request, which the
manager parks and delivers to whichever kernel is running when the window
opens. A row with an action names the request. The kernel's own `signal` and
`parent-gone` rows are verdicts a previous kernel filed on its exit, never a
request, and are passed over. A `down-failed` row (written when a `romp down`
did not stop the kernel) cancels the `down` written before it: neither names a
later signal, and both are passed over. The manager's `manager-sigterm` row is
a note that the manager sent the signal, not a request: it answers only when no
request row written before it lies within the window and this kernel's
lifetime, with `manager-sigterm: <trigger>` as the reason, so a `down` followed
by the manager's `cli-down` note still reads as the `down`, and a note aimed at
another kernel's pid is ignored. Verdicts, notes and the two `down` rows are
passed over wherever they sit, an aged one included: one older than the window
or older than this kernel never ends the walk, so a quiet-window request
beneath it is still read. A row with no action (the `romp refresh` row) is
skipped, and the manager's `restart-all` note written after it is what names
the refresh; a `romp refresh --quiet` row is the parked deploy that holds the
automatic converge until the window opens, and the note written at the window
names its delivery the same way. A SIGTERM that reaches a kernel with a
quiet-window request parked and no manager note for its pid (a note naming no
pid counts as its own) is not that request's delivery: the kernel files a
`signal` row and leaves the request on record for the kernel the window will
restart. The manager's stop of one kernel (a `stop` note with trigger `stop`,
which leaves the manager's parked request armed; a stop of every kernel writes
the same note) is not the delivery either: that cut is named by the note,
`manager-sigterm: stop`, and the request stays on record. A restart note, or
the self-bounce's `refresh` note, is the delivery: the cut row names the
request and consumes it.

The automatic converge spaces itself: after a deploy restart lands on a box
(its own converge, a peer's push, a clicked Update), the next automatic
converge waits 25 minutes, so a batch of merges costs one restart, and it
stands down while a quiet deploy is parked for the code already on disk. Both
waits exist to spare in-flight turns from the restart's cut, so neither applies
to a restart that would cut none: when every working session runs under a host
(the default), the converge proceeds at once. Every pass in which main has
moved and the box does not converge says why on the kernel's log, each time it
holds: the cool-down's remaining seconds and the turns a restart would cut, the
parked quiet deploy, or that main could not be read (`git ls-remote` at the
release remote failed or timed out).

When no row qualifies, the kernel writes a row with action `signal`: the signal
name, its pid and its parent's pid, the manager pid it was started with,
whether a manager restart was pending, `managerRequested: false`, and
`managerStopped`. That last field is what the kernel can see of a service stop
or restart, which signals the kernel and the manager at once: the manager's pid
is already gone, or the manager's own stop note lands while the kernel drains
or within half a second after (the note is written before the kill, so the
wait bounds an event the kernel expects, not a guess). With `managerStopped:
true` the reason reads `signal; the manager was stopped too (a service stop or
restart)`; otherwise `signal, not requested through the manager`, which means
no request was on record when the kernel read the file, not that the sender
is known. The sender's pid is never recorded; a Python signal handler does not
receive it. A kernel whose manager disappears writes a row with action
`parent-gone` before it exits. `restart-cuts.jsonl` gets one row per exit
naming the turns the drain cut and the reason: the audit row's `action:
reason`, the `signal` row's reason, or `parent-gone: the manager exited; the
kernel followed it`. When the helper that files the `signal` row fails (a
`ROMP_MANAGER_PID` the kernel cannot use as a pid), no `signal` row is written,
and the cut row carries the plain `signal, not requested through the manager`
verdict plus a `reasonError` naming the fault, so the missing row is explained
on disk. A second SIGTERM during the drain is ignored; the first
writes the row. The manager's log says `exited without a restart request
(signal or crash); respawning` when a kernel exits that it did not ask to stop
or restart.

Two more ledgers there record what restarts do to the sessions, appended by
the SDK backend and read by `romp restart-metrics` (below). `session-events.jsonl`
gets one flat row per thing that went wrong with a session's process and one
per boot sweep: `{"t": <epoch s>, "pid": <the writing kernel>, "kind":
"<writer>.<what>", "sid": <the session, when about one>, "name": <its name
then>, ...fields, "text": <the prose>}`. The kinds: `reconcile.boot` (the
sweep summary of every boot that had a session to reconcile: `sessions`, `resumed` continuation notices queued,
`restored`, `notified`, `reaped`, `scopesStopped`, `toStart`, `durationS`),
`reconcile.orphan-reaped` (`cliPid`, `fsid`, `scope`, `signaled`, `forced`,
`tree`), `reconcile.scope-stopped` (`unit`, `sid8`, `cliPid`),
`reconcile.duplicate-cli` (two Claude Code processes holding one conversation
as the boot's process listing stood: `fsid`, `pids`, `n`), `crash.heal` and
`crash.loop` (`attempt`), `drain.unjoined` (a session the drain's bound left
closing: `inflight`, `reaped`), and the lease work's `lease.*` kinds. Every kind
but the boot summary carries `text` and is also a kernel-log line of the form
`<prose> ;; problem-row {json}`, the same object after the marker, so a log
reader parses it with a split on the marker; every kind but the boot summary
and `drain.unjoined` (written as the kernel exits, when the bell has no reader)
is a problem-ring entry too (the bell and error center show its prose).
`GET /session-events?since=<epoch s>&limit=<n>` (token-gated) returns the rows
newest first since the stamp (default this kernel's boot in whole seconds, the
resolution every row's `t` has and the `bootAt` the response names, so the
default rows and `count` are one predicate but for the boot summary and the
`limit` cap; at most 1000), each with `host`, and `count`, this kernel's
problems since its boot, never a sum across kernels. `turns.jsonl` gets one
row per settled turn: `t`, `sid`, `name`, `fedT` (the feed pop, when the text
left the queue for the CLI's stdin, at millisecond resolution), `firstOutT`
(the first streamed work atom), `resultT` (the ResultMessage), the CLI's own `durationMs`, `apiMs`,
`numTurns` and `isError`, the spend fold's `usd` and token columns (`tokIn`,
`tokOut`, `tokCacheR`, `tokCacheW`), `opener` (`human` or `injected`),
`fedTexts`, and `resumeNotice`, true when a text fed into the turn was the
boot or crash continuation notice, the turn that redoes cut work. Every stamp
is an event's time, and `fedT` and `firstOutT` are present only for a turn
this kernel fed: a turn the CLI opened by itself (a channel message, a
background task's notification, a scheduled prompt) has no feed, so its row
carries neither rather than the previous turn's stamps. Both files rotate at 32 MB to `<name>.1`, one predecessor
kept, so each pair stays under 64 MB; the reader reads both. The restart rows
of `restart-cuts.jsonl` carry the kernel process's own `rssKb` and `cpuS`,
sampled at its exit (the cut row) and at its settled boot (the boot row), so
the kernel's growth between restarts is a series without a sampler of its own.
And the manager writes a `quiet-window` row to `restart-audit.jsonl` when a
parked deploy refresh applies (`since`, `waitedS`, `reason` as the gate's
verdict, `backstop` when the fifteen-minute cap fired, `coalesced`, `mode`,
`lastInflight`, `misses`, and the park's drain-hold counts); it is a note, not
a request, and the kernel's restart-reason walk passes it over.

The two host registries there, `remotes.json` (attached and checked-in
machines, each row with that machine's serve token) and `remotes-known.json`
(machines remembered for re-attach, with the mail tier you set for each), are
read at boot under the rule their doors apply: a checked-in row's host must be
a machine name, an attached row's an ssh alias, a remembered row's either. A
row that fails (one filed before the doors applied the rule) is set aside,
never loaded and never written back: the rows are moved to
`<file>.refused-<UTC stamp>` beside the original (`-1`, `-2` when a second one
lands in the same second; the `remotes.json` sidecar is 0600, since its rows
carry tokens), the file is rewritten without them, and one stderr line plus one
Log entry under the `refused` kind names each host as a clipped repr, never the
raw string.

The postal service's own files live under `postal/` there: `mail/<session>/`
(a maildir per recipient), `outbox/<host>/` and `readbox/<host>/` (cross-host
mail and read receipts awaiting their peer). A record or message file the bus
cannot parse or read is moved aside once, never deleted, to
`<name>.corrupt-<UTC stamp>` beside the original (an inbox file lands beside
its `new/` directory, out of every listing; a `-1`, `-2` suffix when two land
in the same second), the rest of the store is served, the sender's receipt for
that message reads refused, and the error center says so under the `refused`
kind. At start the bus removes the temporary files a crash left behind (a
message written but never placed, a store record never finished), closes each
one's receipt as refused, and says so once. The sidecars are yours to inspect
or delete.

## The spend ceiling

Every pusher cycle the kernel reads each live session's spend rate: the
dollars its transcript and the agent transcripts beside it (the subagents and
workflow agents it fanned out) record over the last ten minutes, priced by the
same per-model table the cost view uses, scaled to an hour. The data is what
the kernel already holds for the chat and the feed (the record cache), so the
check reads nothing new; only an agent file that changed inside the window is
read. The ceiling is the `spend-ceiling-usd-per-hour` setting, a bare value
file under the state directory read at each check: 1000 dollars an hour with
no file, any number in the file, and `0` disables the guard. When a session's
rate crosses the ceiling, once per crossing, the kernel interrupts its turn
(the Stop button's road, so the fan-out ends at once), hands it one message in
your voice (about how much it is spending, and to stop whatever is fanning out
and say what it was before doing anything else), warns every connected
dashboard with a toast naming the session, the rate and the moment, and files
a `spend.ceiling` row in `session-events.jsonl` (with `usdPerHour`,
`ceilingUsdPerHour` and `windowS`), which the kernel log and the error center
carry and restart metrics count. The crossing is the event: nothing repeats
while the rate stays high. Once the rate falls under half the ceiling a
`spend.ceiling.cleared` row and a toast say so, and the guard is armed again.

## The spend ledger across a host re-attach

A session under a host keeps its CLI process across a kernel restart, and the
CLI's `total_cost_usd` is cumulative per process. The kernel folds only each
result's delta over a watermark, so every result persists that watermark on the
session's registry row (`costState`: the cumulative total, the token
watermarks, and the CLI's identity as pid and start time). A kernel that
attaches to a surviving host reads it at the first result and, when it names
that same CLI, seeds the watermarks from it, so the first result records only
its own turn; a fresh process still starts at zero and records its whole first
total. A surviving process with no matching watermark on record (a kernel
before this rule wrote none) records nothing for that first result, since its
total is the lifetime's and the turn's share is unknowable; the kernel log says
so, and the watermark is written from there. The replay of a dead host's
journal tail seeds the same way for the dead CLI before it drains. A result the
attach's replay hands over again folds nothing, whatever its total, decided
from the record's own journal position: the transport tags each result record
with its offset as it reads it, the kernel pops one tag for every result record
it receives, first thing and whatever the result holds (the SDK's buffered
reader runs a record ahead, so the transport's current offset is never the
handled record's), and a record before the offset the host's hello named as its
next is a replay; a dead host's journal replays through the same road, the
replay reader being the session's transport for the drain; its turn row says `redelivered` and carries
`journalOffset`. A live total below the watermark is a counter reset the kernel
did not see and folds whole, as before. An orphan journal's replay keeps the
dead CLI's watermark as its line: at or below it was folded, above it was not. The attach flag lives
one connect, so a rollback to hosts off records a fresh child's first turn in
full, and a `/clear` as the first turn after an attach retires the pending seed
so the zeroed counter stands. Each `turns.jsonl` row carries
`cumulativeUsd`, the CLI's own total at that result, and a first result's
`spendBaseline` (`fresh`, `seeded` or `attach-unknown`). Before this rule every
restart re-billed each hosted session's lifetime as one turn (2026-09-11: a
staircase of rows from $436 to $953 on one session across 21 restarts).

## Restart metrics

`romp restart-metrics` reads what kernel restarts do to the sessions, from the
state directory's ledgers (`restart-cuts.jsonl`, `restart-audit.jsonl`,
`session-events.jsonl`, `turns.jsonl`, the state logs under `states/`, and
`spend.json` for the day's total dollars) and from the running kernel's
`GET /version` and `GET /perf`; it loads no kernel module and writes nothing.
The text form prints one screen per window: restarts and the turns they cut
(with the clean restarts and the boots that had no cut row, a crash respawn,
whose cut count is unknown; the per-restart rate divides by the measured
restarts alone),
the reasons, the outage from exit to first serve and the reconcile settle (from
the `bootSettled` rows), the quiet windows' waits and backstop firings (from
the manager's `quiet-window` rows), the boot sweeps' orphans reaped, scopes
stopped, duplicate processes, crash heals and loops, sessions the drain left
closing, and lease problems (from `session-events.jsonl`), the continuation
notices and the redo turns with their dollars and tokens (from `turns.jsonl`;
the spend ledger's buckets cannot attribute a turn's cost, and the summary says
so), turn latency from the feed pop to the result and to the first output (from
`turns.jsonl`, at millisecond resolution) beside the same interval from the
state log's `working` and `waiting` rows (one-second resolution, the only
latency available for turns before `turns.jsonl` existed), the machine cuts by
cause (a state-log pair broken by a machine cut is not a turn), and the kernel
process's resident memory and CPU at its exits. The live
block reads each `romp-session-*` scope's `memory.current` and `cpu.stat` on
Linux (a `ps` tree walk where there is no cgroup), the kernel's pid, uptime,
CPU, resident size and the pusher's idle-cycle share, and lists any
conversation two Claude Code processes hold right now. Windows are days or
weeks (`--window`), weeks anchored on `--anchor` (default the first restart's
day in range), bounded by `--since` and `--until`, in the machine's local time
unless `--tz` names a zone; weeks are counted in local dates, so a clock change
inside a week moves no boundary off local midnight. The header names the machine `this machine`
unless `--label` says otherwise, so no hostname reaches the text by default. A
missing ledger is named at the top, never a silent zero. `--json` prints the whole document (`schema` 1): `restarts`
(each cut row joined to the boot that followed it), `quietWindows` (each
joined to the restart it released), `kernelSeries`, `events`, `buckets` (every
metric above per window, with capped latency samples for the distribution
figure), `sources`, and `live`.

`scripts/restart_metrics_report.py` draws the before-versus-after figures from
two or more of those JSON documents with cleanplots, which is not a romp
dependency, so it runs under uv:

```
uvx --with cleanplots --with matplotlib --with pandas python \
    scripts/restart_metrics_report.py --doc baseline=baseline.json --doc after=after.json --out DIR
```

The figures land in `--out` (default
`~/.local/state/romp-research/restart-metrics/`): turns cut per window and per
restart, outage and settle times, quiet-window waits, sessions gone wrong per
window, continuation notices and redo dollars, the turn-latency distribution,
resident memory per session scope and the kernel's own, and the kernel's
resident memory at each exit and boot over the days of each document; beside
them `figures.json` carries the numbers drawn and `summary.txt` the reader's
text per document. Session names are hidden by default (`session 1..N` by
memory rank; the kernel's own bar keeps its name and its own colour) for any
output directory outside your state root, because real session names are
private and must not reach a repository, an issue or a pull request; `--named`
shows them, and inside your own state root they show by default. Without
cleanplots the script says so and draws nothing.

## Repairing the spend ledger

`romp spend-repair [--day D] [--since INSTANT] [--apply]` recomputes a day's
`spend.json` hour and day buckets, their per-session rows and `turns.jsonl`
dollars after the re-attach re-bill (the section above on the ledger across a
host re-attach: before the fix, every kernel restart recorded each hosted
session's whole CLI lifetime as one turn, a staircase of rows on each session).
It reads the turn rows and the restart instants (each boot row of
`restart-cuts.jsonl` gives its `firstServe`, the epoch the new kernel began
serving; the row's own `t` is the settle, which can lag the first serve by
minutes; nothing else is an instant: a restart request in the audit ledger is
most often a parked one that no restart followed, and the dying kernel records
results for seconds after both a request and its own cut row) and judges each
session's first result strictly after a restart, a result at the first-serve
second being the old kernel's:
it is that process's cumulative when it stands at or above the previous
cumulative plus the rows recorded between (a process's total grows by at least
what its own rows recorded; a figure below that is a fresh process's first turn
and stands), and its true cost is the cumulative less the previous cumulative
less those rows. The day's first cumulative row counts as a typical turn (the
median of the session's rows that follow no restart) and only when a staircase
follows it. A row bearing the signature with no restart instant on record (a
crash leaves no audit row) is taken as a step only on a chain the session has
already shown. `--since` is the instant the per-session hosts came on: before
it every restart killed the CLI, so nothing there is a step. Rows the fixed
kernel writes (`cumulativeUsd`, `spendBaseline`) are never staircase steps; one
rule of their own reaches them: a row whose kernel figure equals its cumulative,
in a session whose `attach-unknown` row precedes it, is the lifetime billed
once more (the fix's first boot left the watermark at zero after a replayed
first result) and is corrected by the kernel's own arithmetic to the cumulative
less the previous same-session row's cumulative (a replayed row with no dollars
and a rising cumulative counts as that previous row), stamped `repairRule` 5.
The guard is the kernel's reset comparison, the cumulative above the previous
row's: the first paid turn after a mid-life `/clear` is written with its
dollars equal to its cumulative by design, a counter reset, and the rule stands
down with a note (never a clamp); the chain disarms on the row it judged, on a
reset and on a fresh or seeded baseline row.

It prints before and after per hour and per session and changes nothing unless
`--apply` is given. A corrected row keeps the kernel's figure as `usdRecorded`,
and every run judges a repaired row again on that figure, so a tightened rule
or a later `--since` restores what an earlier run took, and a run over a
repaired day re-judges every correction, staircase and lifetime alike, and
changes nothing when the judgements stand: a lifetime correction the rule no
longer believes is restored to `usdRecorded` and its buckets re-folded, the
same road the staircase rules use. Per-session figures fold under the session a row
bills (a comment thread's owner, the registry's `threadOf`), and the buckets'
`key` split moves only for sessions the registry marks as API-key billed; the
report says how many rows' split was left as recorded. The kernel may be
running: `--apply` copies both files beside themselves first
(`spend.json.bak-<stamp>`, `turns.jsonl.bak-<stamp>`), rewrites `turns.jsonl`
first carrying every row appended since its read, journals the rows' deltas
(`spend-repair.jsonl`), then reads `spend.json` again and folds the deltas on
what is there; a run that fails between the two writes leaves its deltas
journaled and the next run folds them first. A standing correction of a day's
first cumulative row is kept as it was made, so the day's later rows never
rewrite it.

## Switches

Effective immediately, no restart.

`touch` to **disable**, `rm` to re-enable:

- `~/.claude/romp-postal-off`: the postal service
