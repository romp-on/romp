# Reference

This page lists every command and knob, and the full detail behind each feature
the [guide](guide.md) introduces. You do not need any of it for ordinary use,
where the user interface covers everything: it is here for driving Romp from the
terminal, for scripting against it, for debugging, and for the moment you want
to know exactly how something behaves. Every command runs on the machine that
hosts the kernel.

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
| `romp down` | Stop the kernel and keep it stopped until `romp up`. The turns the stop would cut get 5 seconds to finish first (a turn under a session host keeps running); sessions resume with their history at the next start. See [Stopping the kernel on purpose](#stopping-the-kernel-on-purpose) |
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
| `romp compact <session> [--wait] [--timeout <s>]` | Compact a session's context in place (Claude's `/compact`: summarize the history, keep the session's name, id, mailbox, and watches; on a Codex session, Codex's own compaction of the thread, which produces no summary text): the alternative to ending and recreating a long-lived session, and the external hand a session needs since it cannot `/compact` itself mid-turn. Quiet session → compacts now; open turn → queued, fires alone the moment the turn ends (the same safe path the chat's compact button uses). `--wait` blocks until the compaction has ended, polling the `/sessions` rows (the row's `compacting` bit is also the field to point a `romp watch` predicate at for scripted recycling); exits 1 honestly on timeout, and exits 1 with the end's words on stderr when a Codex compaction ended loudly instead of finishing (the failure exit reads what the Codex compaction's end leaves on the row, the record below and the notice; a failed Claude `/compact` leaves nothing on the row, so it reads as a clean end, never as a failure). On a Codex session the judgment reads the row's `compactEnd`, the record the backend keeps of its compaction bracket's ends: a counter every end advances, with the last end's kind, words and stamp. Done when the record is no longer the one baselined (the counter advanced, or the same count under a new stamp: every end advances the counter and takes a fresh stamp) and the last end is clean; exit 1 with the words when it is loud (a failed compaction, the thread or the app-server going away, the session ending, a turn Codex accepted while the compaction stood, so that no divider was written). The record is judged rather than the row's `launchError` because the next accepted turn clears that notice, and a message parked behind the compaction is delivered at the loud end, so the notice was erased within milliseconds, before the next poll, and the wait printed done over an uncompacted thread. The baseline is the row as the CLI read it before its request for a compaction that runs at once, and the first sample that reads not compacting for a queued one (the earliest ours could have fired, so the prior compaction's loud end is not attributed to ours); a kernel restart starts the record over (the record is in memory; the bracket's bit is kept in the session's registry row, and the restarted kernel records the compaction it finds still running there as a loud end whose outcome is unknown, beside a notice saying the same), and the wait reads that record by its identity from every baseline (the restarted kernel's record starts at one, so its count alone matches a baseline of one; the stamp tells them apart): the restart's loud end exits 1 with its words, a clean end since the restart is done, and a count back at zero (a restart that found no compaction running) leaves the rest of that wait judged as a row without the record; a row baselined without the record that carries one on a later sample (a kernel from before the record, replaced mid-wait by one with it) is judged from a baseline of no end, a count above zero by its kind and a count of zero on the bit and the notice as before; a row read quiet with no notice and the record unchanged from its baseline is reported at the timeout, with a line saying the compaction's end was never recorded or its record was lost to a kernel restart right after it (a kernel that died after a clean end saved its bit, so that the restarted one serves zero again from a zero baseline; or a kernel from before the record's restart end). A row without the record (a Claude session, whose compaction path keeps none; a kernel from before the record) is judged as before, on the compacting bit and the `launchError` a loud end leaves while the bit falls: a notice that is new against the same baseline and marked by the kernel as a compaction's end (the notice's `noRetry`, so a turn's own rejection or failure, which can land after a queued wait's arming sample, is never read as the compaction's) exits 1 with its words, whether or not the compacting sample was caught, and a notice standing at the baseline is not read as this one's; else the bit having been seen and then fallen is done. When the session list cannot be read before the request, the wait on a compaction that runs at once is refused on stderr and the compaction is still requested; a queued compaction's wait is not refused, since its baseline is the first sample after the request that reads not compacting, not the read before the request. A remote session's compaction is requested on its own kernel; `--wait` can't follow it from here and says so |
| `romp end <session>` | End a session |
| `romp move <session> <dir>` | Move a session's working directory to `<dir>` (the folder must already exist); the conversation, name, mail and history stay with the session. Quiet session → moves now; open turn → queued, fires when the turn ends. See [Moving a session to another folder](#moving-a-session-to-another-folder) |
| `romp checkin <host>` / `romp checkout <host>` | Publish this machine to an attached hub, or withdraw it. The hub files this machine under the name it declares only when that name is a machine name (letters, digits, dots, hyphens or underscores, starting with a letter or digit, at most 128 characters). Any other declared name is refused with a 400 that states the rule and echoes nothing, is recorded nowhere, and is said once on both machines: on the hub, one stderr line and one Log entry under the `refused` kind, naming the value as a clipped repr; on this machine, one stderr line, one dial-log record and one Log entry carrying the hub's reason, after which the same name is not re-sent until it, or the hub's kernel, changes. A hub's `POST /tunnels/trust` for a host it has never seen (the remembered-hosts entry that tiers relayed mail by origin) holds the wider rule that registry's writers share, a machine name or an ssh alias (letters, digits, dots, hyphens, underscores, at-signs, colons or square brackets, not starting with a hyphen, at most 255 characters), because a hub keys an attached peer by its ssh alias and carries that alias when you set trust between two of your machines; anything else is refused the same way, on the hub, with nothing recorded. `ROMP_HOST_NAME` (the kernel) and `ROMP_POSTAL_HOST` (the postal bus) override the declared name only when they clear the same rule; an unusable value (a space, an at-sign, a trailing newline) is set aside once, on stderr or in the bus log, and the derived name (the short hostname, else the platform's machine name, else a minted id) is used |
| `romp default-dir [PATH]` | The default working directory for new sessions; no argument prints it, `""` clears it |
| `romp login add <label> --cmd '<shell line>'`, `romp login list`, `romp login remove <label>` | The stored Claude logins a session can be billed to beside the machine's own (see [Several Claude logins](#several-claude-logins)): `add` records the command that prints the login's setup-token on demand (`--op` is the 1Password shorthand for `op read`); `list` and `remove` print labels only, never a token |
| `romp debug [on\|off\|status]` | Judge debug mode, where rejection rows carry the full input and reply |
| `romp refresh --quiet` | Refresh at the next quiet window instead — waits for the turns and background work a restart would cut to finish (15-min backstop); a session under a host keeps its turn across the restart, so it is not waited on. The ONLY door to the quiet window: a deploy (a peer's `romp update`, a release self-update, an automatic converge) restarts immediately, by the user's 2026-09-08 decision |
| `romp down --wait <s>`, `romp down --now` | How long `romp down` waits for the turns it would cut to finish (0 to 600 seconds; default 5), or no wait at all |
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

### Notice cards: a feed card without a judge

`romp card -t <title> [-m <text>] [-k <key>] [-s <session> | --no-session] [-b <board>] [-c <category>] [--body-file <path>] [--attach <path>] [--needs-you] [--expires <seconds>] [--producer <label>]`, or the shorthand `romp card "title" "text"`, posts a **notice card** to the feed: a card the kernel makes from what you hand it, with no judge involved (design: plans/notice-cards.md). With no session named the card is **owner-less** and shows at the **top** of the feed under the name Notes, above every session's cards (outside a session that is the default; inside one `ROMP_SID` owns the card unless you pass `--no-session`); `-s <name>` gives it to a session. `-k` names the card: a second post under the same key is a **revision** (it replaces the earlier card on the board, and shows again even if you had dismissed the earlier one, since it carries new information, and its number counts the archived posts of the key too, so a dismissed card's id is never minted again); with no `-k` the command mints a key and prints it, so a later `romp card -k <key> ...` revises the card. The body is markdown, rendered through the chat's sanitizer; `--attach` names a file the card shows inline when it is an image (the kernel judges it as the hover preview does: your home or the session's folder, no secrets-shaped names, the size caps, and keeps a pinned copy of an image as posted; an owner-less card's attachment is judged against your home alone). A card leaves the board on your dismissal (Clear; Undo restores it, copying its rows back out of the archive when the retention pass has already moved them), on a revision, or at `--expires` seconds from the post. `--needs-you` files it under Needs you. The kernel keeps every post in `notices/<session>.jsonl` under the state directory (`notices/notes.jsonl` for owner-less cards) and archives dismissed, expired and superseded rows to `notices-archive/`; a home shows at most fifty live keys at once, the oldest superseded past that. The same door is `POST /notice` on the kernel (the `/watch` shape: `{"id"|"name", "key", "title", "body"?, "attachment"?, "needsYou"?, "expiresAt"?, "producer"?}`; with neither `id` nor `name` the card is owner-less; `{"id"|"name", "expire": "<key>"}` retires a card early), and producers inside romp call the kernel's `post_notice` in process. An owner-less card carries no actions. `-b <board>` files the card on a board of yours (`plans/card-boards.md`): an unknown board is created on first use with the category `-c` names (or `notes`), a category unknown to your board is added to it, and the posted line names the board and says when the post created it, so a typo in `-b` is a new board you can see at once (`romp board list` names it, `romp board remove` takes it away while no card stands on it). `-c` alone names a feed category (`working`, `needs_input`, `completed`). `--needs-you` on a board is that board's needs-you category, and is refused when its definition names none.

### Card boards: your own categories

`romp board define <id> (--from <path> | --json <text>) | list | show <id> | remove <id>` manages the **card boards** beyond the built-in feed (`plans/card-boards.md`). A board's definition is one JSON object: its `id`, a `title`, one to eight `categories` (each an `id`, a `title` and a `chip` from `working`, `blocked`, `completed` or `neutral`; `blocked` is the Needs you chip's dress, the magenta of the category, its name kept as a schema value, and no chip value paints red, which is the hard stop's alone), a `defaultCategory`, post-time `rules` (each `{when: {needsYou?, producer?, keyPrefix?}, category}`, the first match filing a card), a `sort` and optional `subSorts` (`{key: t | session | owner | title, dir: asc | desc}`), `groupBy` (`"session"` or `null`), `order` rules, the `notify` list (the categories whose entry rings the bell) and the `needsYou` category (the one the app badge counts), and `kinds`. The kernel validates every member and refuses an unknown one by name; `define` replaces a board whole but refuses to drop a category that still holds standing cards, and `remove` refuses while a card names the board. The feed itself is code-defined and cannot be redefined. Definitions live under the state root in `boards/<id>.json` and reach the dashboard on the next frame; a file edited in place there is read on the next frame too, and a file outside the schema is skipped with a line in the kernel log. The feed pane shows one board at a time: the View menu gains a **Board** row per board the kernel carries (the feed first) once a second board exists, the pick survives a reload, and a board created by `romp card -b` is a row on the next frame; a data board's cards sit under its own categories, sorted by its definition, with no session grouping unless the definition asks for it. A feed page opened with `?board=<id>` shows that board with the Board rows hidden, the hook a pane per board mounts on. A pick naming a board the frame no longer carries shows the feed and says so on the View button.

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

### Restarting a session in place

A session runs the Claude Code it launched with and keeps it for as long as it
lives. That matters when the CLI is upgraded: when a new model ships, a session
started before the upgrade cannot reach it — the short name resolves to the old
model and the new id comes back unrecognized — while a session started after it
can. **Restart session** replaces the program without disturbing the session.

Right-click the session's tab, or its row in the Sessions panel, and choose
**Restart session**. The agent's process ends and a fresh one resumes the same
conversation. The session id, name, folder, tags, colour, model and effort,
mailbox, goals, cards and whole history are untouched, and the session is never
marked closed on the way through: its tab stays where it is, still selected if
it was, and the transcript keeps reading as it did. This is the same place End
session followed by Revive arrives at, in one step that never presents the
session as dead.

If the session is idle, the restart happens with no dialog; the menu row reads
**Restarting…** until the kernel answers, then goes back. If it is working — a
turn in flight (including one paused on a permission or picker prompt, or
retrying the API), a compaction, or background work it dispatched — Romp confirms
first, naming its open cards and saying that the running turn is cut off; for a
session waiting on your answer to a prompt, it also says that the question goes
away with the turn. The
turn is interrupted and the relaunch follows at that turn's end, so the program
is never torn down from under a live turn. Work the old process was running,
its subagents and its background tasks, ends with it. A message you had queued
survives. The relaunch follows the first turn to end with nothing the old
process has picked up: a message you had queued runs in the old process while
it keeps taking your queue, and one still waiting at that turn's end is
delivered by the new one.

A session that is not running has nothing to restart: the row refuses and
points at Revive, which is the same thing for a closed session. Every refusal
(a session this kernel does not have, a backend with no relaunch of its own)
is reported in the pane you asked from and changes nothing.

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

The chat statusline shows the session's working directory by default (a widget
of the Status line section under Settings, Chat, beside the git branch, on by
default too); clicking it opens that folder. The default is the OS opener (`open` / `xdg-open`). To open it
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

### What a session needs from you

Above the composer, a box headed **Needs you** lists what the session you are reading needs from you.
It opens in steps: collapsed to its header line (the label and the count) until you click it, a first
click showing the items, a second the full context under each, a third folding it back to the header line;
the header is a button, so Tab reaches it and Enter or Space opens it, and the header's own gear opens the
setting below. While a row says romp's judges are refused their credential, the box stands open to the items,
so the fault is not hidden under the header. Each item has a way to act: a question a
judge filed (at the items level the card's title, its decision brief or takeaway clamped to four lines with More,
whatever section is open on the card, and the state badges the card wears, with **Reply**, which points the
composer at that card the way Follow up does, and **Clear**; at the full context the card's own section toggles,
Background, Summary, Stalled, the sub-goals and Awaiting task, opening the same sections, and in the browser a
section opened on the row or on the card opens on both, the two being one page's frames; in VS Code the chat and
the feed are separate views and each keeps its own), and a held
message from another session with its own **Approve** and
**Deny**; and, when romp's judges cannot read the session because the credential they bill is
refused, a row whose one action is **Fix credential…**, which opens the gear's Billing block (no
Clear: clearing would hide the fault while the refusals go on). A stop the chat already shows
inline, a permission prompt or an API error only you can clear, is not listed: the tab's red
**Blocked** ring and the card's mark say it. The box wears the Needs you
colour the way the background box wears the awaiting green while the session waits, and it hides when
the session has nothing for you. An item leaves with the frame that resolves it: the reply once the
judges file it, the decision, the clear. A clear is a row in the state directory's `cleared.jsonl`, the
clears log; a card an undo could not finish bringing back is noted in `cleared-owed.jsonl` beside it, so
the next Undo brings that card back first, across a restart. The gear's **Needs you box** setting, under Chat in the
section **Boxes below the transcript**, on by default, hides the box; the tab (its count dot, or the outline ring with
the badge off) and the feed still say what needs you. It is per browser, like the other chat settings.

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

### Always fast, and retrying an upgrade after a downgrade

Two switches under **Settings**, **Automation**, **Model**, both off by default, both
kernel-side (stored on the kernel like the judges' Fast mode boxes, stamped, and
following to every connected machine's kernel):

- **Always fast** runs every session in fast mode whenever its model allows it
  (Opus-only, billed at a premium). The kernel arms the CLI's fast-mode opt-in at
  each connect for a session whose model is Opus, and when a session lands on Opus
  later, by a pick or by an automatic fallback, it reconnects to arm it as soon as
  the session is quiet: no turn in flight, queued, or opened by the CLI itself (a
  background task's notification starts one), no question waiting on you, no
  subagent, no background task, so
  nothing is cut (the wait is said once in the kernel log, with what is running;
  the kernel looks once more the instant before the reconnect and stands down if
  the CLI has started work since); turning the switch on or off reaches every
  running session the same way. A session you set to **Slow** from its
  statusline stays slow until you set it to **Fast** again, and a comment thread
  launched slow, or forked from a slow session, counts as such a pick. If the CLI refuses fast mode for a
  session with a reason (extra usage off, an organisation gate), the kernel log
  says so once and that session runs at normal speed until the reason clears (the
  CLI reporting fast on for it, or your own Fast or Slow pick on it); the switch is
  never the literal `/fast on`, which on a non-Opus session would make the CLI
  change model.
- **Retry upgrades after downgrades** acts when a session's model changes to a
  lower tier without a pick, the automatic fallback the Completed card reports
  (`Model changed automatically: … → …`). Every ten minutes the kernel asks for
  the picked model again by reconnecting the session as soon as it is quiet, the
  same rule as above, so nothing is cut; a fresh CLI starts on the pick
  (or the account default when nothing is picked). A session that already sits
  below its pick when you turn the switch on is taken up at once. A fallback that
  happens again is logged once per attempt; its card follows the board's usual
  rule, nothing new while the swap's card stands, a fresh one once you cleared it. When a turn is served on the picked tier, a second
  Completed card says the session is back (`Model back on …`) and the retry ends.
  A pick of your own ends it too, as does turning the switch off. While a
  fallback stands, the session's model picker, in the chat statusline and in the
  timeline's lane picker alike, marks the requested model with a yellow tick
  beside the blue tick on the model that answers; its tooltip says why
  (the safety classifiers and their category, once the CLI has named them, which it
  does within seconds of the swap; a fallback that predates the kernel is read off
  the transcript when the kernel attaches) and whether romp is
  retrying, with the cadence and the next attempt, or where to turn retries on.

### Extra models from your API gateway

An install whose sessions reach the API through a gateway of its own can offer the models that
gateway serves, in every model picker beside the Claude families. The switch is **Settings**,
**General**, **This machine**, **Extra models from your API gateway**, off by default; while it is
off the pickers list Claude models only. It is a per-install setting, kept on the machine that
holds it and never sent to another (its gateway is that machine's): `~/.local/state/romp/router-models.json`,
`{"enabled": true, "gt": <gesture stamp>}`; an absent, unreadable or malformed file reads OFF.

Which models the gateway serves is declared to the service, not picked in the gear:
`ROMP_ROUTER_MODELS` in `service.env`, a comma-separated list of model ids, and optionally
`ROMP_ROUTER_MODELS_URL`, a gateway endpoint that lists its models, whose answer joins the declared
ids. Both are read when the service starts, so a change to them needs a service restart; the switch
itself applies live. At boot the declared families install into the catalog when the switch is on.
`ROMP_MODEL_CATALOG=off` (a hermetic lab's no-network rule) stops the listing fetch alone, on the
boot road and the live one; the declared install reads no network and is never held by it. A
picker row and the badge both read the model id itself, one name per model.

The gear's click posts `setRouterModels` (`{"enabled", "gt"}`) with a gesture stamp, under the
ordering and stale rules every stamped setting follows; there is no echo frame of its own, since an
applied flip changes the catalog and the kernel sends its usual `models` frame, on which every
picker and the gear redraw. Turning it on adds the families to the pickers; turning it off removes
them, but does not touch a session already running one, which keeps its model until you pick
another (a later pick of a removed model is refused). A gateway model whose id carries no Claude
family word has no capability tint in the pickers, and a swap to or from it is never read as a
capacity fallback: it is a cross-provider change on an explicit pick. An id that does carry a family
word (`gw-opus-mini`) is matched by the colour, tone and rank helpers wherever the word appears, so
it tints and ranks as that family, and a swap to it can read as one.

The switch's status line in the gear comes from the authed `/models` payload's `router` section,
`{"enabled", "declared", "gateway", "error"}`: the ids the kernel parsed out of the variable, whether
a gateway is configured (`ANTHROPIC_BASE_URL` in the service's environment first, else in Claude
Code's managed or user settings, pointing anywhere but Anthropic; `null` while the switch is off,
when nothing is probed), and the standing advisory: while on, nothing declared, no gateway, or a
settings file that could not be read (a fixed phrase; the detail goes to the kernel log), each read
live at every request so the line clears as soon as the operator fixes it, and a listing that could
not be fetched; after an off flip, the live sessions, the judge tiers and the default for new comment
threads still on a removed model (the stores are left as they are; a new thread whose default is a
removed model inherits its parent). A removed model is refused on every pick road, the comment
thread dialog and the new-session seed included. A declared id the first-party grammar owns (a Claude version id or family alias) is
skipped, said once in the log, and reported as not declared; a listing or an apply that lands after
a later flip installs nothing and changes nothing. `/version` carries
`routerModels`, the switch's value, for the gear's checkbox.

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
**Billing** submenu that lists the billings this machine can apply, the
machine's own login, every stored login and the API key, and those only (the
user 2026-09-14: what is set up, nothing greyed; from 2026-09-08 to then the
missing side was listed greyed with its reason in the hover). A machine with
nothing to bill shows one inert line in the reasons' own words, `no Claude
login signed in on this machine`, `no apiKeyHelper configured`, or `the
apiKeyHelper is set in managed settings, login cannot apply`. Each label shows
whole, the menu as wide as its longest label and bounded by the window alone
(the user 2026-09-14; a 22em cap had cut the machine login's `email ·
organisation · kind` to an ellipsis). The status payload carries the same
availability as `authAvail` (`authBoth` rides beside it for older clients),
and the machine's default beside it. The flyout opens on hover over the
Billing row, as the Tags flyout does (one gesture: a short hover opens, a
click opens at once, leaving both the row and the flyout closes it), and on
click. Switching reconnects the session to apply, with the same switching-dots
the effort badge wears.

Below the session's choices, behind a rule, the flyout carries one entry,
**Set default billing**, which opens a submenu holding exactly the same
choices, a stored login among them (the user 2026-09-14; until then the
submenu offered the machine's own login and the key only), the current
explicit default check-marked. That default is
the seed every new session, and every session with no pick of its own, launches
on; it lives in the state root's `sdk-defaults.json` as `auth` (never a token;
a stored login as `auth: login` with its record id under `authLogin`, and an
unpicked session then launches with that login's helper, reads it in its
status and bills its judges to it, exactly as a session that picked it would;
a stored login the machine cannot bill just now, refused, expired or removed,
falls through to the machine's own login),
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

A tab not yet loaded after a reconnect shows "Not loaded yet, click to load"
as its hover tooltip, prefixed with what needs you when a card of it does, until its transcript arrives.

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
breaks the windows down by ACCOUNT: one block per distinct login (the account
line as its head, the meters written once, since they are the account's
allowance), beneath them one line naming the machines logged into it in the
tab strip's quiet host dress, a machine whose own reading lagged the freshest
named as lagging beside its name, and one updated-ago line per block (the
oldest report of the group); two accounts are two blocks side by side; a
machine attached but not yet reporting is named after the blocks rather than
given a column; the key spend stays one section for every machine. A click on the
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

A machine holds one Claude login at a time: Claude Code keeps the signed-in
account in its own configuration directory, and `/login` replaces it. The
user (2026-09-11) has a personal and an enterprise account under one email and
wants a session billed to either, the way the Billing row offers Login vs API
key. Romp therefore keeps a registry of STORED logins beside the machine's
own: one record per login under `STATE/logins/<id>.json`, holding the label
the user gave it, the email, organisation and kind word (`personal` for a Pro
or Max subscription, `enterprise` for a Team or Enterprise one, read from
Claude Code's own record when the add flow could, never guessed from an
organisation's presence), and the COMMAND that prints the credential. The
credential itself is a `claude setup-token` bearer (a one-year token) and
lives wherever the user keeps it, nowhere in romp: no file under romp's state
directory holds it, and it never rides romp's environment or a log line. Romp
assumes nothing about where it is kept; it only runs the recorded command
(a secret manager's read command, a private file's `cat`: the choice, and the
setup that puts the token there, are the user's own, outside romp).

A session billed to a stored login gets the token the way the machine's own
login tokens already reach a launch: at launch, the kernel runs the record's
token command itself and puts the output into that ONE session's process
environment as `CLAUDE_CODE_OAUTH_TOKEN`, with the box's `apiKeyHelper`
disabled through the per-session settings layer (the same layer a login pick
uses). The machine's own login tokens are not restored into such a launch. The
token rides that process's environment, readable by processes of the same user
as the machine's own tokens are, and nothing else: no romp file, no log line,
no argument list. This environment road replaced the helper road on 2026-09-14,
after the check the design called for, run by the user on their own machine:
a setup-token handed to Claude Code through an `apiKeyHelper` hangs the request
(the CLI never answers and never reports an error), while the same token in
`CLAUDE_CODE_OAUTH_TOKEN`, under a scratch configuration with no other login to
fall back on, is accepted and billed to the subscription. A judge call billed
to a stored login runs the same command the same way for its own child.

The command runs the way the kernel runs the box's own key helper: under a
whitelisted environment (`PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `LANG`,
`LC_ALL`, `TERM`, `CLAUDE_CONFIG_DIR` and the `LC_*` and `XDG_*` names), never
the kernel's whole environment, whose serve token is full control of every
session; with its standard input closed; with its standard error discarded,
since a secret manager's diagnostics can quote the value it read; and bounded
at fifteen seconds, the kernel's own helper bound. Anything the tool needs
beyond that, the command provides itself: on a headless machine a secret
manager's CLI needs its own session or service credential, so the command
sources that from a private file (mode 0600) before the read, while a
desktop's unlocked app serves as is. The login records themselves are written
at mode 0600 in a 0700 directory.

A failing command is loud, never a quiet fall onto another account. When the
command fails at launch (a missing tool, a locked store, a bound passed), the
record is marked refused with the reason, the problem ring says so, and that
launch takes the same fall a dead machine login takes (the API key when a
helper is configured, else the machine's own login), said in the Billing row
as a fall. When the command answered but the CLI signed in with something else
(a managed key, a key found in a settings file, an `ANTHROPIC_API_KEY`), the
init's own report is the evidence: its source word names a key, where a bearer
login reports none. The problem ring names what the CLI used, the tab hover
reads `picked, but the CLI signed in with another credential`, the submenu's
sub-line `CLI used another credential`, the record is marked refused so every
menu leaves it out with that reason, and the session is reconnected so its
next launch takes the fall. The session is not ended, since that would drop
the conversation: it keeps running on the fallback side, flagged, and the
Billing menu switches it elsewhere on a click. The reconnect is asked once per
session, and only when the machine has a side to fall to (a helper, or a
signed-in machine login); with neither, a relaunch would land wrong again, so
the session stays where it landed, flagged. The API-health bucket and the
spend rows follow the credential that actually answered, never the pick, and
an API auth error (a revoked or expired token) marks a stored login refused
only on a session whose launch carried that login's token. That evidence is
per process: a relaunch that no longer carries the token (the login went
unavailable, then a model or effort change) starts with none, and it is kept
on the session's registry row so a session re-attached to its running CLI
after a kernel restart keeps it through the turn: an attach launches nothing
and resets nothing. A served reply on a session whose token did answer is the
deciding event the other way and clears the refusal; a judge call never clears
one (its envelope does not say which login answered), and the judges of a
session on a refused login take the same fallback, said once in the kernel
log. A command whose text carries a credential-shaped run (a setup-token's
prefix, forty or more token characters outside a path, or a JWT-shaped bearer
of three dot-joined segments) is refused at add time: it would ride the
shell's argument list on every run, readable to every process of the same
user, and the refusal says a value typed there is already exposed through the
shell's history and should be rotated. Dotted names pass (a secret manager's
key path, a host, a file), a forty-digit hex run inside a `gpg` command or
right after `--recipient` is a key fingerprint and passes, and the rule is
applied at add time only: a stored record is never re-read against it.

A machine or session with no stored login works exactly as today: the ordinary
Claude Code login and the API key path are untouched, and the stored logins
are an addition beside them. The user's own shape is the case the tests pin:
the personal account on the ordinary login as now, and the enterprise account
as a stored login whose command reads a setup-token from the user's secret manager.

Three things to know plainly. The judges bill the SAME account as the session
they judge: a session billed to a stored login has its planner, closer and
distiller calls carry that login's helper too, so its analysis is subscription
usage on that login; a session on the machine default is unchanged. A pasted
token's label is the user's word: romp cannot read an account or an
organisation out of a token it never sees, so a login added from the command
line carries only the label typed for it. And the tool the command calls must
work non-interactively for the user who runs romp (a signed-in secret manager
CLI, for example), as the machine's key helper already must.

One door adds a login. `romp login add <label> --cmd '<shell line>'` records
the command that prints the token; romp never reads, prints or stores the
token. Minting the token and putting it in a store is the user's own setup,
outside romp (a script of their own that runs `claude setup-token` under a
scratch configuration directory, hands the printed token to their store, and
ends by calling this command). `romp login list`
prints the labels, `romp login remove <label>` forgets a record (a label two
records share is refused; name the id instead); the token stays wherever it
was kept.

Every surface that offers a billing pick lists every login the machine knows
plus the API key: the new-session picker's Billing row (segmented buttons up
to three choices, one dropdown beyond; an unavailable choice greyed with its
reason), the tab menu's Billing submenu (the session's current login
check-marked, an unavailable one greyed with the reason in its hover), the tab
hover's Billing row and the submenu's sub-line (`Login (name@example.com ·
Org · enterprise)` for the machine's own login, `Login (<label> · Org ·
kind)` for a stored one, each piece only when known), and the gear's Account
section, which lists the stored logins with a Remove each. The pick reaches
the kernel as `login` (the machine's own), `key` or `login:<id>`; the
registry's `auth` stays `login` | `key`, and a new `authLogin` field names the
stored login, so every older reader keeps its meaning. A fork bills the same
login as its parent. The API-health signal gives a stored login its own
bucket, labelled `login:<salted digest of the record id>`, and the card names
such a bucket by the login's label when several share a model family.

Failures are loud. An API refusal of a stored login's credential names the
login by label on the session's card (`the <label> login was refused`) and
marks the record refused: every menu greys it with that reason until it is
removed or added again, and `setAuth` refuses it with the same sentence. A
stored login's one-year life is warned from eleven months in the gear and the
menus, and an expired one reads as unavailable. The machine's own login
signing out leaves a session billed to a stored login untouched (its helper
is its own; only the machine-login option greys). A single-login machine with
no stored logins behaves exactly as before.

The Billing surfaces that list the logins, name the enterprise one and switch a
session's pick ship with the registry and the credential road; the gear's
guided add flow is the second change.

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
  run `bin/romp-sdk-setup` before starting one. Notifications to a phone or
  browser read the `cryptography` package from the same venv, so they stay off
  until it runs too.

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
- `ROMP_POSTAL_PORT=<port>` moves the postal bus off the default `25302`. The kernel dials the port the bus actually bound, read from the bus's record `postal/postal-port` under the state directory (written after the bind, removed on a clean exit), and falls back to this variable only when the record is absent; a mismatch between the two is said once in the kernel's log. The two can disagree when a unit or profile sets the variable for one process and not the other, or when a stale legacy tunnel still reverse-forwards another machine's bus onto the fixed port: the operator's two checks when a held message's approve comes back refused.

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

One hazard comes with `uv`: `uv python install <version>` puts a `python3.X`
shim in `~/.local/bin`, which the newest-first fallback searches, so on a
machine with no SDK venv (or one whose recorded interpreter is gone) the next
restart runs the newest Python it finds. Install extra interpreters with `uv
python install --no-bin <version>` and reach them through `uv python find
<version>` or a venv, never as a bare `python3.X` on `PATH`.

Whatever the pick, 3.10 is the floor for an interpreter that reports a version:
`bin/romp-serve` runs the picked interpreter once for its version (its first
execution), reads the sentinel line the probe prints (`romp-pyver X.Y`, carriage
returns stripped, so a site customization's chatter or an `atexit` hook that
prints cannot pass for the version or hide it), and refuses to start the kernel
below 3.10, naming the interpreter, its version and the install commands, with an
exit code of its own (2). The probe is bounded to five seconds where `timeout`
exists, its whole process group signalled at the bound so a child the interpreter
left behind dies with it; an interpreter that runs out that clock, or exits 124 or
137 of its own accord (the codes the bound reads as), is refused as unresponsive
with exit code 1. Where there is no `timeout` (a stock mac) the probe is
unbounded, as the picker's own runs of a candidate are: the residual. The output
goes to a file (`TMPDIR`, then `/tmp`, then the state directory: a `TMPDIR` that
is stale or unwritable falls to the next directory, and only a `PATH` without
`mktemp`, or every directory unusable, falls to a pipe read, which the bound
covers for the interpreter but not for a helper it leaves holding the output),
read afterwards by the shell itself, so a helper the interpreter left holding its
output cannot hold the file read; the file goes with the shell, a stop mid-probe
included. An interpreter that reports no readable version is started on purpose
(the pick already checked it is an executable file, and a version nobody can read
is not a version below the floor). `bin/romp-serve --print-python` prints the
pick with that floor applied and starts nothing, which is what `install.sh`'s
preflight runs, claiming a Python cause on that code alone and passing the
script's other refusals (the two port spellings disagreeing, a kernel binary that
is not there, an unrunnable pin, an unresponsive interpreter) through with their
own line and a plain stop; every python the install runs afterwards is that same
interpreter, and under `ROMP_SKIP_PREFLIGHT` the pin (`ROMP_PYTHON`) stands in
for it.

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
overrides the file's path. The launcher reads the file line by line and never
sources it: a line that is not `KEY=VALUE`, or whose name the shell refuses to
assign (`UID`, `PPID`), is skipped and the rest reach the manager.

On macOS the login agent runs the manager under a copy of `node` named
`romp-node` in the state directory, so that Full Disk Access can be granted to
romp alone rather than to every script the shared `node` runs; the copy is
refreshed when `node` changes (a re-grant follows a node upgrade). A `node` whose
shared library is referenced relative to its own install (Homebrew's build, a
version manager's shim) cannot run from the copy: the launcher probes the copy
before using it and runs the manager on the system `node` instead, saying so once
in the manager log, and `romp-service install` removes such a copy rather than
leave it. `ROMP_NO_NODE_COPY=1` in `service.env` skips the copy altogether (the
grant then reads `node`); the launcher reads the file before it decides, so the
line works for a manager launchd started. The value rule is the same in both
readers: `0`, `false`, `no` and `off` (in any case) are off, any other non-empty
value is on (`disabled` and `none` included: only those four words turn it off),
and the last assignment in the file wins. The copy is probed under a ten-second
bound (`ROMP_NODE_PROBE_BOUND`, in whole seconds, read the same way by both
scripts: a value with no digits, or a digit among other characters, is the default
ten; leading zeros are dropped; zero is one second; a value of seven digits or more
after that folds to 3600; anything from 1 to 999999 is taken as given), and a probe
that hangs is
killed with everything under it, TERM then KILL, so a version manager's shim that
runs `node` without replacing itself leaks nothing.

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
kernel or manager that merely exits is back within seconds (on macOS, within a
minute when the manager had run for less than a minute before it exited: the
throttle that bounds a crash loop delays a manager's own refresh exit in that
window too), and Ctrl+C is not
available to a manager the service runs. `romp down` instead stops the login
service itself (`systemctl --user stop romp-manager.service`; on macOS
`launchctl bootout` of the agent), which nothing respawns, and then probes the
processes themselves rather than trusting the exit code of `romp-service stop`. A manager that dies as soon as it starts is another matter: launchd's `ThrottleInterval` in the agent is 60 seconds, so such a manager is retried once a minute rather than every ten seconds (a manager that ran longer than that before exiting, its own refresh, is respawned at once), and `romp-service status` reads the job's record rather than its mere presence, so it says `loaded but not running` with the last exit code instead of `running`, which is also what `install.sh` keys its skip-the-reinstall shortcut on. One such death has a reading of its own: when the agent's manager exited with code 1, its refusal to start beside a manager already holding the control port, and something answers on that port (the port the agent's manager would bind: `ROMP_MANAGER_PORT` in `service.env`, else the environment's, else 7432), `romp-service install` names the manager already serving, most likely a hand-run `romp up` outside the service, with the two ways out (leave it, and the agent takes over when that manager stops; or stop it and re-run the install), and exits 3; `install.sh` then finishes its run, link and banner included, and exits non-zero at the end. Any other exit code with a manager answering is reported as two facts, the agent's own death and its log first. Under systemd, `Restart=always` keeps the unit's default start limit (five starts within ten seconds and the unit stops), and `systemctl --user status romp-manager.service` tells the two apart.

Before stopping, `romp down` gives the turns in flight `--wait` seconds
(default 5, up to 600) to reach a turn boundary. It asks the kernel to quiesce
(`POST /down`), which holds new turn starts and new session creation, and then
reports whether the kernel went quiet or which sessions are still mid-turn and
about to be cut. A session under a host is neither waited on nor named: the
stop detaches it and its turn keeps running (see
[What survives a restart](#what-survives-a-restart)). The wait ends on the
event the in-flight count reaches zero;
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
stop kills that cgroup. A bus started from inside a session (its postal MCP
server, its turn-end mail check, or `romp refresh` or `romp mail` typed in its
shell) runs in a transient scope of its own, `romp-postal-bus-<pid>-<time>`, so
it keeps running. The kernel stopping that session's scope does not end it, and
neither does its reap of an orphaned session CLI's process tree, which spares
the bus's `serve` running in a scope of exactly that name (a bus the session's
postal MCP server started is still that server's child); before 2026-09-22 it
lived in the session's scope and died with it. When `systemd-run` cannot start
that scope, the bus starts in the session's scope after all, and `server.log`
says so in a `fallback:` line that quotes that launch's own `systemd-run`. A
`systemd-run` still waiting on the user manager when `ensure`'s few-second wait
ends is left to finish, with a `pending:` line there; if no bus comes of it,
the next `ensure` logs what it said and tries again. It still carries the
session's `oom_score_adj`: `systemd-run --scope` runs it in place, and a scope
has no `OOMScoreAdjust=` to reset it, so with `ROMP_CLI_SCOPE_OOM_SCORE_ADJ` set
the machine-wide OOM killers rank it with the sessions, not with the kernel.
Either way the next kernel boot runs `ensure` again, so at worst mail parks
until `romp up`.

### What survives a restart

A kernel restart does not end a hosted session's CLI. By default every session's
CLI runs under a per-session host process (the section on hosts below): on `romp
refresh`, the manager's restart-all, `romp down` or a service stop, the kernel
receives SIGTERM and drains by detaching from every host. The host keeps the CLI
and its turn, journals what it says and parks what it asks, and the next kernel
attaches by the lease and replays what it missed, so the turn is never cut and
the session is told nothing. A session running as a plain kernel child (the
`session-hosts` setting written `off`, or a session from before hosts that has
not respawned since) is closed by the drain as before: a CLI still running when
the drain's bound expires gets SIGTERM, then SIGKILL, and that session resumes
with its history and is told what was cut: its in-flight turn, if it had one, and
each background task, with a request to check whether each is still running
before relaunching it. The manager does the same to the kernel: one still
running five seconds after the manager's SIGTERM, on a restart as on a stop,
gets SIGKILL, so no kernel outlives the stop that was meant for it. A crash
respawn has no drain: the kernel died without running one; a hosted CLI keeps
running under its host and is attached at the next boot, a plain child is
orphaned and the next kernel's boot reaper terminates it (see below). The CLI's
harness background tasks do not all end with a plain child. Its timers and
monitors live inside the CLI process and end when it does. A background shell is
a separate process the CLI started, and a CLI killed by SIGKILL runs no cleanup,
so its shells are re-parented and may keep running. A kernel restart has never
touched work a session deliberately detached: a tmux server it started itself,
`setsid` children and other processes that outlive their shell.

The dashboard page stays on screen across a restart. Its panes reconnect as they
do after a dropped socket (the watched tab rebuilt whole, the other tabs as
skeletons that fill on demand), and a restart onto the same build is invisible
beyond that: no reload, no line. When the kernel serves a newer build than the
page loaded (a restart onto a new build, or a converge in place), the page offers
a reload rather than taking one: one line near the top of the window, "A newer
romp build is ready.", with **Reload** and **Not now**. Reload keeps drafts, scroll
position, the active tab and the notification center, and lands the fresh page on
the chat diet; Not now is kept per build, so the same build never asks again and a
later one does. The old page keeps working against the new kernel meanwhile: the
chat wire is negotiated per version, an action the new kernel does not know in the
old page's form falls back to the older path, and when that happens the line says
the page is behind the kernel. The rail's restart button follows the same rule
(an unchanged build reloads nothing, a changed one is offered); the update
banner's **Update** click, which asked for the update, still reloads once the new
kernel is up. A kernel that must force a reload for correctness can send the page
`reloadRequired`, honoured through the same holds a reload always waits on (a
held pointer, a draft, an upload in flight); nothing sends it today.

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
jobs skip a session whose keyed files (the transcript, the state log, the goal
store with its override journal and archive, the episode, clears, postal and
downtime logs and the nudge ledger, ten in all) are unchanged since their last look, with the boot as the first baseline: a session blocked
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
it (the drop then pops as it did before the write existed, except under the
incident scan's memo, which keeps a walked file's records resident when the
document write is off, since its memo cannot reach the disk); `ROMP_CKPT_CONVERGE_MB`
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

The assembly checkpoint (2026-09-11) does the same for the parse itself. A
second document beside the fold checkpoint records everything before the cut
(since 2026-09-15 the turn before the last SETTLED turn, a turn whose result
landed and whose next turn exists, or the turn that holds the last compaction
boundary, whichever is later; before that only the boundary's turn, so a
session that never compacted had no document. A standing document is
rewritten with a later cut only when the tail past its cut has grown to an
eighth of the pre-cut bytes or a compaction landed past it, and a session's
FIRST document waits until its pre-cut part holds an eighth of the fold cap,
1 MB by default (`ROMP_CKPT_FIRST_DOC_KB` sets it, 0 turns it off), since below
that a whole parse costs milliseconds and, with uniform turns, the share bound
alone is met at nearly every settle until the pre-cut part outgrows the two
to three turn lag (27 rewrites over a young session's first 30 settled turns
measured); above the floor the rewrites over a session's life are a logarithm
of its growth) as identities and
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
as before, until the next settle rewrites it with one. An entry built by a
whole parse is re-seated on the document written from it at its next parse
(2026-09-24): the restore road reads the document and the tail, so the folds
after it walk the tail alone instead of the whole history, and the chat renders
from the cut; the tail share then demotes the re-seated entry to the whole
parse that lets the settle advance the cut, and the entry stays held to the
share through a restore after a descent (an api_error spur, a rewind in the
tail). An entry whose document's tail already meets the share, whose leaf's
document a restore just refused, or whose history still holds a postal
author waiting on the log, stays whole, and its document is rewritten only
once the tail reaches the share or a compaction lands. Such an entry loses
the churn bound at a descent, deliberately: the restore that serves it after
the descent carries no bound, as every restore did before, so its cut waits
for the next whole parse; held to the share, an entry kept whole for an open
turn's tail would parse the history whole again at every descent.
A compaction after the document demotes to a
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
its text then. The planner's own callers take every unit that way (T396): the
emptiness gate that drops a textless segment is decided from the markers'
scalars and the user bodies alone, and a work unit's text and quote are read
by the plan pass after its own filters, so a unit that never reaches the model
is never read (42.8 MB of assistant bodies per boot before).

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

A boot reaps only what the booting kernel can prove it started. The unit list
and the process table are machine-wide, and a session id is not a kernel's: two
kernels with state directories of their own can hold the same session (a lab
copy of a live one), and until 2026-09-23 every boot of the second stopped the
first one's host and ended its CLI. Each kernel now tags what it starts with its
state tag, the first 16 hex digits of the SHA-256 of its resolved state
directory: a session CLI carries it as `ROMP_STATE_TAG` in its environment, and
a session scope or host scope carries `romp-state=<tag>` in its description.
The orphan reap, the leftover session-scope sweep and the host-scope sweep act
only on what carries the kernel's own tag. Anything with another kernel's tag,
or with none (a unit or CLI started by a build from before the tag, or a process
whose environment cannot be read), is left alone along with its CLI's scope,
and the kernel log names all of it on one `boot reconcile: left alone` line. A
kernel's own leftovers from before the upgrade carry no tag either, so every
boot on the new build leaves them in place and names them, until they exit or
are stopped by hand (`systemctl --user stop <unit>` for a scope, `kill` for an
orphaned CLI). A session whose conversation one of those spared processes still
holds is not resumed by that boot either, since a second CLI on the transcript
would be two writers on one conversation; the log says the session stays down,
and once the process is gone the next boot resumes it as usual.

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
sweep stops a dead host's scope by its lease and the kernel's state tag in the
scope's description. On macOS the host is a plain
detached process and everything else is the same.

A host upgrades itself in place when the kernel that attaches runs newer code.
Until 2026-09-18 a host kept the code it started with for as long as its
session lived, so a host bug outlived every kernel deploy (the lease census
reported each such host as `lease.version-skew`, thousands of rows a week, and
one host carried a stale open-turn count for three days). Now, at an attach
whose lease names another code version, the kernel first rewrites the host's
`spawn.json` with its own version and asks the host to re-exec (a `reexec`
frame carrying the kernel's interpreter, its own `bin/romp-session-host` and
its version). The host answers at once: `ok` with `when` `now` when its CLI is
idle, `at-turn-end` when a turn is open (the exec waits for that turn's
`result`, the event, never a timer), or `ok` false with a reason when it cannot
hand its descriptors over, in which case the kernel attaches to the old host as
before and files a `host.reexec-refused` row. To re-exec, the host holds its
stdout reader (no further record is taken off the CLI; bytes not yet read stay
in the pipe, which survives the exec), drains its stdin pump and its journal
writer, drains the attached kernel's socket backlog to the last byte (five
seconds at most), and then decides with nothing awaited between the decision
and the exec: the CLI is quiet, meaning no turn re-opened and the reader's
stream buffer holds no bytes, or the exec is deferred to the next `result`,
the event, and the reader runs on meanwhile (a `reexec-deferred` line names
the reason: output arriving, or a kernel that did not drain in time). Quiet, it
writes any record read during the drains to the journal itself so the count
it hands over and the journal agree, writes a handoff file
(`hosts/<sid>/reexec.json`: the CLI's pid, start time, spawn time and
conversation id, the three pipe descriptors, the read count, the open turns,
the open requests and the acknowledged offset), marks the descriptors
inheritable, writes `reexec-now` to the kernel (its backlog empty, the frame
reaches the socket at once), logs the `reexec` line, closes its socket, and
calls `execv` on the same pid: the CLI stays
its child, the pipes stay open (descriptors survive an execve), the lease
holder's pid and start time are unchanged, so `hostAck` still names this host
and the replay offset holds, and the journal is reopened from its segment
files (the index rebuilt from the files entry for entry as the live one held
it, the next offset from the last record, a deleted segment's offsets
unreadable). The new host confirms the inherited descriptors against the
CLI's own `/proc` descriptors on Linux before trusting the handoff, adopts the
CLI through the pipe transport over them, re-serves the socket, writes the
lease with the new version (in that order, so a kernel that reads the new
version finds a listener; the kernel's wait for the re-executed host also
connects before it trusts the lease), and waits for the kernel's attach; the
kernel, told `reexec-now`, treats the socket's close as the planned handover,
not a host death: no `host.died` row, no orphan replay, no resume, a wait for
the re-executed host (its lease with the new version under the same holder and
a listener that accepts, as on the `now` road, never a second request), one
re-attach from the same acknowledged offset, and a `host.reexeced` row. The
frame can miss the kernel: a socket full at that instant, or the kernel's own
write at the same `result` (its context refresh, an acknowledgment) hitting
the closed socket first, which makes asyncio close the whole connection with
the frame still unread. A kernel holding an accepted handover whose socket
ends unasked therefore reads the host's log, and when the latest re-exec line
since its own attach is `reexec` (or `reexeced`, the new code already
serving) and the lease still names the same live holder, it takes the same
planned road; a deferral, a failure, a dead host or no such line leaves the
close the lost host it reads as (2026-09-22). A re-exec that fails before the
exec leaves the old host running and says so (a `reexec-failed` line in the
host's log; a `fault` to an attached kernel, which files a
`host.reexec-failed` row, as does a kernel whose wait for the re-executed host
runs out); the fault also clears the wait its `reexec-now` frame armed, so the
next attach asks again. One that fails inside the new process, on a handoff
that does not check out, makes the new host exit with the CLI still running (a
`cli-adopt-failed` line), which the kernel's existing orphan road handles as a
host death: the CLI finishes its turn on end-of-file and the session resumes
from the transcript. The kernel's wait for the re-executed host ends the
moment that happens, with no row of its own: when the lease goes, names
another holder, or names a CLI or a holder that is no longer alive, or when
the host log's newest line records the new process ending (`cli-adopt-failed`
or `host-crashed`). A stale heartbeat alone does not end it: nothing beats
between the exec and the new code's first lease write, so a slow start is
still waited for, up to the bound. After any wait the connect reads the lease
again and takes the orphan road for a host that is gone, never an attach into
a socket nobody serves; past the whole bound, a lease that has not beaten
within its twelve seconds reads as it does at any connect's first read, an
orphan (2026-09-23). A connect that finds the host gone also drops the
handover it had asked for, so the next host's hello files no `host.reexeced`
row. The worst case is the pre-host behavior for one session, never
a dead one. What the guarantee covers: every record parsed off the CLI before
the exec is in the journal, numbered as the kernel was told; every byte still
in the pipe reaches the new host. What it cannot cover is a line the SDK's
reader has split across two chunks (its framer holds the first part between
reads), which with the stream buffer empty at the check is a record the CLI
is mid-write on at that instant, outside any turn: lost to the journal only,
never to the CLI.

The parked-op drain says so when a stale count holds a queue. The drain
delivers a session's parked input once the session is quiet, and its working
gate reads the backend's open-turn count, which under a host is the count the
host handed over at the attach. A stale count (the shape of the stuck-Working
defect: a host counted a message folded into a running turn as its own turn,
and every kernel adopted the count for days) holds the queue with the count
alone: a turn counted open and nothing queued to start. That is the one
source the belt reads, never the composite busy signal, which also holds for
a queued turn about to run and for a feeder waiting with the count at zero
(a parked deploy restart, an armed reconnect after a settings switch, a
pending rewind), holds that are correct. The drain records the hold on its
own thread; the jobs thread reads the session's transcript once per pass, and
when the count says open while the transcript, at rest, shows its last turn
closed, files a `pending-ops.held-working` problem row once per hold: the
ledger, the kernel log and the error center's ring, naming the session and
how many items wait, with the remedy (ending and reviving the session
replaces the count; a kernel restart does not, since the attach adopts the
count). The transcript is read only at rest, once per version of the file,
through the kernel's shared parse, and never on the pusher's thread; a
transcript that is absent, that parses to no turns or that keeps changing (a
turn streaming) is no verdict, and a later version that shows a turn open
files a `pending-ops.held-working-retracted` row. The belt never delivers:
the queue stays held until the count clears or the session is replaced, and
cancelling the queued chip clears the belt's state, so the next hold on that
session says again.

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
whose parent is not a live romp kernel, and whose environment carries the
kernel's state tag, is treated as orphaned and terminated.
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

### Messages across a restart

A message sent to a session while its CLI is busy waits in the kernel's queue
for that session; one the CLI has taken but not yet written to the transcript
is the CLI's to land. A restart ends the CLI, so at the next boot, and at any
fresh spawn for the session, the kernel checks every message the dead CLI was
holding: one that reached the transcript is left alone, and one that did not is
put back into the session's queue behind whatever is already waiting, in send
order, so the session reads it as if the restart had not happened. Two
variables bound this:

- `ROMP_REDELIVER_MAX_AGE_S=<seconds>` is the age line on that re-delivery;
  the default is `1800`, thirty minutes. A message older than this at the
  restart is not re-fed: it is kept in the chat marked never delivered, where
  it can be restored or dismissed, and a notice card (the section above) is
  posted for the session, under Needs you, one per session per restart, naming
  how many messages were dropped and, for each, its time and its text. The
  card offers **Send again** for each message (up to three; with two or more
  there is also **Send all again**, which re-sends them as one message in
  order, and with four or more that is the only button), and one click spends
  the card, so a message not re-sent from it is restored from the chat
  instead; a typed command gets no button. Clearing the card lets them go.
  The session itself is not told anything. The line exists
  because a landing the kernel's transcript scan cannot see would otherwise be
  re-fed at every restart, for days (measured 2026-09-12: the same texts re-fed
  at two restarts in one night, one of them landing six times). It applies
  only to messages the CLI was holding, never to the queue proper: a message
  still waiting its turn is delivered however long the kernel was down. `0`
  switches the line off, and every unlanded message is re-fed whatever its
  age. The kernel reads it once, when it starts, so set it where the kernel's
  service sees it (`service.env`, then a restart).
- `ROMP_KERNEL_HTTP_TIMEOUT_S=<seconds>` is how long `romp send`, `romp
  interrupt` and `romp end` wait for the kernel's answer; the default is `10`.
  A kernel that took the request but answered late (mid-restart, or under
  load) is exit `3`, with a line saying the message may already have been
  delivered and not to retry blindly; a kernel nobody is listening on is still
  `kernel not reachable`, exit `1`, with nothing sent; a refusal the kernel
  wrote is its own words, exit `1`. Widen it on a slow box. There is no off:
  a send with no bound would hang the script that runs it. Each command reads
  it from its own environment, so it can be set for one call.

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
rebuilds, the kernel's parse-store misses by road with the bytes of its whole
parses, bytes sent per slot as full frames, deltas and deduplicated frames,
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
- `heap`: where that resident size sits at the moment of the read, so a
  large `process.rss_kb` can be attributed live, without a restart or a
  debugger (the lag investigation, 2026-09-15, had to attribute a 5-6 GiB
  resident size from cumulative byte counters and lab runs). Every value is
  a GAUGE, the occupancy at the read and not a count since boot, with one
  exception named below. `allocatedBlocks` is the number of memory blocks
  the interpreter's object allocator holds at the read, of any size
  (`sys.getallocatedblocks`; 0 on a build that cannot count them); `gc` is
  the collector's `enabled`, its `counts` (the young generation's
  allocations since its last collection, then how many times each younger
  generation was collected since the older's last) and `thresholds` as
  lists, and `stats` (per generation: `collections`, `collected`,
  `uncollectable`), which is cumulative by nature; `tracing` says whether a tracemalloc tracer runs in
  this process. Then the caches that hold session content: `hydrated` (the
  lazy bodies read on demand: `entries`, and `bytes`, the records' length on
  disk, which `capBytes` bounds, a proxy that locates the holder without
  sizing it: decoded bodies usually weigh more, but escaped text can make the
  disk bytes exceed the decoded storage),
  `assemblyEntries` (the assembly cache's entries), `parseSlots` (the one
  parse store's slots, one per session, cut and leaf), `lazyIndexes` (the
  lazy indexes alive, a weak count), `materializedLruSlots` (the
  materialized-atom LRU's slots, not the atoms: on a kernel whose LRU holds
  its atom lists weakly a collected list's slots stay until they expire, so
  this is an upper bound on the live materialized atoms; where the LRU holds
  the lists strongly the two are equal; it is the same read as
  `asmIndex.resident`, repeated here so the holders sit together),
  `judgeUsageRows` (the judge-usage reader's rows in memory), `builtChat`
  (`tabs` cached, their `events`, the cached payloads' event counts, a count
  and not bytes, the occupancy measure of that cache, and `serializedBytes`, the sum over the
  cached JSON strings, which only the index wire, a proto-1 client, stores,
  so under the shipped wire it reads 0), `imgCache` (`entries` and `bytes`
  of the preview data URLs; the cache has no cap, so this gauge is
  O(entries) over whatever it holds, a refused file counting as an entry of
  zero bytes). These occupancy gauges attribute a resident size to its
  holders; they do not sum to it. The transcript record cache, the largest
  resident holder when the kernel is large, is not among them: its occupancy
  already rides this response under `recordCache` (`entries` and `bytes`
  against `budgetBytes`), so a resident size these gauges leave unaccounted
  for is read there first. The block reads a length or a counter per
  cache, under the cache's own lock where its readers take one and over a
  copied value list otherwise; it walks no object graph, collects nothing,
  evicts nothing, fills nothing and reads no file. A gauge this process
  cannot read (an accessor the runtime lacks, a container the source has not
  got, a cached entry of a shape the gauge does not know) is `null`, said
  once on stderr.
- `gc`: the interpreter's garbage collections, counted and timed (2026-09-16:
  pusher cycles stalled for 9-33 s and a profile of the process caught a 9.2 s
  generation-2 collection charged to whichever stage happened to be running,
  with no counter in the kernel to tie the one to the other; the collector's
  own stats carry no durations). A `gc.callbacks` hook the kernel installs
  once at boot times every collection from its start to its stop callback,
  wall time on whichever thread triggered it. The hook never waits on the
  kernel's own locks (`gc_event` in `kernel/kernel.py` says why: a collection
  can run inside a locked region of the very thread that holds the lock).
  `gen` maps each generation (`"0"`, `"1"`, `"2"`; a full collection is
  generation 2) to `collections` (how many ran since the counters started),
  `msSum`, `msMax` and `msLast` (their summed, largest and last pause) and
  `collectedLast` (the objects the last one freed). `thresholds` and `counts`
  are `gc.get_threshold()` and `gc.get_count()`, repeated from `heap.gc` so
  the block reads on its own (how near the next collection is); `frozen`
  counts the objects moved out of the collector's reach by `gc.freeze`, which
  it never scans (reading the count is a linear walk of the frozen generation,
  about 7 ms per million frozen, paid by the `/perf` read); `errors` counts callback failures (counted, never raised
  into the collector; the first in the process is said once on stderr, a
  line prefixed `perf: gc hook:`, the rest counted only); `hooked` says
  whether the kernel's `gc.callbacks` hook is installed, so zeros with
  `hooked` false mean no hook, not no collections. `freeze` reports the
  freeze controller (issue #1735): the kernel keeps the loaded decoded heap
  out of the collector's walk with `gc.freeze`, so a warm full collection
  walks only what was allocated since and no longer pauses the pusher for
  seconds. It reconciles at the pusher's idle boundary, never on a timer: a
  LOAD fold-in (`collect` then `freeze`, walking only the unfrozen) when the
  record cache's insert counter grows by a material number of trees, and a
  RELEASE reclaim when an ended session is observed to be a surviving cycle, or
  the backstop fires. The release is STAGED: a cheap `collect` with the freeze in
  place first (it takes a released cycle allocated since the last freeze), and only
  a survivor of that then drives `unfreeze`, a full collection and `freeze` (the
  frozen-heap walk); a release the cheap walk took whole counts as a load pass, so
  the backstop's bound does not stretch. WHEN to
  reclaim is MEASURED, not guessed from a state flag: every session-end pop
  registers a weakref to the session (with its worker thread), and this tick
  judges each by observation. A ref that died went by reference counting
  (acyclic, no reclaim); a ref still alive whose worker thread has finished is a
  cycle the collector must take (a reclaim); a ref alive whose thread still runs
  is not garbage yet (judged again next tick). A ref a live ROOT keeps (a helper
  thread still running when the tick judges it, its frame and its target), not a
  cycle, reads the same and is treated as a surviving cycle: the reclaim frees
  nothing, so it costs one reclaim, is counted a `survivor`, and is dropped (never
  re-registered). A record-cache pop is never a
  trigger: its decoded json is acyclic and dies by reference counting, so
  `recordCache.released` is a statistic. A BACKSTOP reclaim runs after `backstopFoldins`
  load fold-ins since the last reclaim (default 1000, about ten hours at the
  measured ~97 fold-ins an hour), so a cycle released by an owner nobody
  registered is bounded by the next thousand loads, not the process lifetime. The
  sub-block carries `enabled`, `active` (whether a freeze is held now; named
  apart from the integer `frozen` above, which is `gc.get_freeze_count()`),
  `loadTrees` and `backstopFoldins` (the two thresholds), `freezes` and
  `reclaims` (a freeze ran one collection and a reclaim ran one, EXCEPT a full
  release that unfroze runs TWO generation-2 collections for its one reclaim, so
  `reclaims` alone cannot derive the organic count), `collections` (every
  `gc.collect()` the run step issued, so the organic full collections are
  `gen."2".collections` less `collections`, an EQUALITY: a full release's two
  collects are both counted here), `endedPending` (ended sessions
  registered by weakref and not yet judged, awaiting their worker thread to
  finish), `lastReconcileMs` and
  `lastReconcileKind` (`initial`, `load`, `release` or `backstop`), `survivors`
  (owed refs a live root kept through a reclaim, a wasted pause each),
  `lastReleaseSurvivors` (of the last RUN's owed refs, how many a live root
  kept through it: 0 when the reclaim freed them, so the release line reads
  "reclaimed", else the count the line names as kept by a live root; rebound each
  run, so a load after a live-root release reads it back at 0, like `lastReleaseSids`),
  `lastReleaseSids` (the first eight characters of the sids the LAST JUDGEMENT
  owed a reclaim for, cleared each judgement, so a tick that owed nothing clears
  it and a cheap-collect release judged `load` shows them too; a full release also
  writes one stderr line, "reclaimed" when nothing survived and otherwise naming the kept sids),
  `totalReconcileMs` and `errors` (a reconcile that raised is counted here and
  said once on stderr, never ending the pusher). The reconcile's own collection
  pause lands after the cycle closed its ring row, so the pusher and jobs rings
  never show it; `lastReconcileMs` is where a reconcile's pause is read. The
  freeze is on by default; `ROMP_GC_FREEZE=off` (or `0`/`false`) turns it off
  for a measurement, and `ROMP_GC_FREEZE_LOAD_TREES` sets the load threshold (a
  value that is not a positive integer falls back to the default, said once and
  counted under `errors`, and it is floored at 1 so it cannot make the reconcile
  fire every idle cycle). A request that arrives during a reconcile waits that
  one collection; the idle boundary is the best moment for it, not a guarantee
  none arrives. To read a slow cycle: find
  its row in `pusher.stageRing` (or `jobs.stageRing`) and read the row's `gc`
  (`null` when the cycle closed without an opening mark): `n0`, `n1` and
  `n2`, the collections per generation that ran anywhere in the process
  while the cycle was open, on whichever thread triggered them (a collection
  holds the interpreter lock for its whole pause, so the cycle waited on it
  either way), and `ms2`, the generation-2 milliseconds among them; the
  young generations' pauses are in `gen.0` and `gen.1` only. A row whose
  `n2` is 1 and whose `ms2` is most of
  `s` x 1000 spent its time in the collector, not in the stage that was
  running, and the stage's own `ms` overstates it by that much. A collection
  inside overlapping pusher and jobs windows shows in both rings' rows, so
  neither ring sums to `gen.collections`. `heap.gc` beside it carries the
  collector's own gauges and its cumulative `stats`; the pauses live only
  here. A collector accessor this runtime lacks reads `null`, said once on
  stderr, as in `heap`; the tallies themselves need none. The kernel-samples
  rows carry the same generation-2 tallies as `gcGen2Collections` and
  `gcGen2MsSum`, cumulative, to difference per interval beside `rssKb`.
- `jobs`: the jobs thread, which runs the housekeeping (the sweeps, the
  reminder walk, the interrupt tick, the persists, the pause and retry
  family) off the pusher since 2026-09-13, so no browser frame waits on a
  cold read: `passes`, `pass_ms_sum`, `pass_ms_max`, `pass_ms_last`,
  `pass_cpu_ms_sum`, `pass_ms_p50`, `pass_ms_p90`, `pass_ms_ring_max`,
  `ring_n`, `passFailed` (a pass that raised out of the loop and was
  skipped), `splitFailed`, `firstPass` (the boot's first pass's stage split,
  the shape of `pusher.firstCycle`) and `stageRing`. The pass's container
  stage is `jobsPass`, its opening `jobs.prelude`; each job is still its
  `jobs.<job>` stage, so a stage name says which thread ran it by the list
  in `_pusher_cycle_jobs` (the pusher's: the checkpoint cycle, pending ops,
  turn notify, the checkpoint persist and converge, the boot row backstop,
  the kernel sample, the API health frame) against `_jobs_pass`.
- `pusher`: `cycles`, `wakes` (every wake call; a burst of wakes runs one
  cycle), `wakes_event` and `wakes_backstop` (how the loop's wait ended),
  `connectPush` (a fresh client's full push on its handler thread, the
  browser's own first draw after a reload or a restart: `count`, `ms_sum`,
  `ms_max`, `ms_last`, and the same per app under `byApp`; the pusher's
  cycles never see this push, so before it the restart's logo phase had no
  number),
  `cycle_ms_sum`, `cycle_ms_max` (since start), `cycle_ms_last`,
  `cycle_cpu_ms_sum` (the pusher thread's own CPU time), `cycle_ms_p50`,
  `cycle_ms_p90`, `cycle_ms_ring_max`, `ring_n` from the last 256 cycles,
  `sends` (every payload that went to a client; a deduped frame the client
  already holds is not one), and `idle_cycles`, `idle_ms_sum`, `idle_cpu_ms_sum`
  (cycles that set no wake, sent no payload and saved no goal store: what a
  longer wait between cycles would have skipped; a conservative undercount,
  since a wake set by another thread or a periodic repost of an unchanged
  frame marks a cycle busy), and `chatFullWhy` (every whole session frame the
  uuid-anchored chat wire sent, counted once the frame has left, so a frame the
  per-client dedup swallowed is no more one here than under `sends`; by the
  reason the sender had for it: `noBase`
  for a first send, a reset, the repost of a session whose built list is
  empty and the first content frame after it (an empty list records no base,
  so a just-created session is re-sent whole once per client per repost
  window until it has content; the reconnect-only reads of the held set are
  unaffected, since a client whose redial is unresolved holds no base for any
  session), and the re-entry of a tab that left the strip (the pusher forgets
  every client's base for it along with its baseline, since the page tore the
  tab down when the strip stopped listing it; one exception, stated not fixed:
  the page keeps a strip-omitted tab the frame's `live` field lists, so for a
  live session that the strip omits, the pusher forgets bases the page still
  holds, and the re-listing is a row-less `noBase` full per client, since the
  dedup slot is popped with the base); `baseGone` for a fork or a
  rewind; `lastGone:<family>` for a held last edge the next list no longer
  carried; `changeAt0` for a change at the list's first event against a held
  base: a genuine first-event change (a floor advance that moved the list's
  first event reads here too), or a change of 0 against a held base, which
  only a sender that read the shared baseline absent produces (a sid's first
  whole frame to reach a client seeds the baseline, whichever sender sent it,
  so its later senders diff against it instead of re-sending the whole
  session), in three faces, the boot's ordinary interleaving in either order
  (the cycle's cold build of the watched tab beside the attach handshake's
  targeted push): a strand's repair, when the sender's list was the older one
  and a whole-frame writer landed inside its build, so its seed popped the
  baseline and marked the session, and the next cycle whose loop reads the
  baseline absent sends every base holder the full and takes the mark off
  with its write (a cycle that sent tails leaves it for the next one); the
  detector's accepted false positive, when the sender's list was the newer
  one, so it marks the session with no client stale and sends one full and
  one row per client where a tail went before, and the next cycle's full
  goes once more only when the session frame moved or the 60-second repost
  window passed since, else it dedups on the client's slot and files no row;
  and the cycle itself as the sender that read the baseline absent with a
  seed landing inside its build, a race the detector does not mark (nothing
  marked, no strand), one full and one row per base holder where a tail went
  before, and at the next cycle tails with no new row, or nothing at all
  where the seed's list already matched the cycle's (2026-09-23); in the
  `chatFull` row below every change-0 face has `changeFrom` 0 with both edges
  held, the floor's has `firstHeld` false);
  `changeBelowFirst` for a change at or before the held first edge;
  `inverted` for a base whose last edge sits before its first; `empty` for a
  list with no events sent to a client holding a base; and `other` for a
  shape none of these names, also the label the rest fold under once the map
  holds as many labels as the sends map; a caught-up client is owed
  deltas, so `lastGone` is the recurrence meter for a base anchored on a key
  that vanished, an input echo's or the command chip's).
  `firstCycle` and `stageRing` (T397): the boot's first pusher cycle's stage
  split and the newest cycles' splits, each `{s, t, stages, gc}` (`gc` is the
  cycle's own collections, described under `gc` above) with, per stage,
  its wall `ms` (one decimal), the reader's `bytes` off disk and the assembly
  cut's `hydrated` bytes ON THE PUSHER'S THREAD since the previous stage
  boundary (another thread's reads in the window, the judges' first pass or
  a boot warm, are not the pusher's; a dashboard's connect push, which runs
  the same stages on the HTTP handler thread, feeds `stages_ms` and never the
  split); the `push` container carries its sub-stages' sums, the jobs before
  the push land in `jobs`, and the boundary sits at the push's entry, before
  the cards-first path. A plain GET carries the newest 16 splits and
  `stageRingLen` (how many splits the ring holds now, not how many were
  served); `GET /perf?ring=all` carries the whole ring, which holds
  `stageRingMax` cycles: `ROMP_PERF_STAGE_RING` when set, else one per 256 MiB
  of the machine's memory floored at 16, resolved once, never a literal
  count, and an override above the fraction is clamped to it.
  `GET /perf?stacks=1` (`romp perf stacks`) fills `stacks` on demand (its
  shape below), the read a slow boot needs to name the lock a thread waits
  on (the nudge walk queued behind a judge's parse) instead of inferring it
  from the byte rows (T401); token-gated like every `/perf` read. Under `jobs`
  every tick job is a sub-stage (`jobs.<job>`), and the bytes read between
  them go to `jobs.other`, which carries bytes only, never `ms` (the same for
  `push.other`); `prelude` is the cycle's opening (the liveness snapshot, the
  names), so the top stages sum to `s`; `splitFailed` counts a split the
  bookkeeping could not close; `cycleFailed` counts a cycle that raised out
  of the pusher's loop and was skipped (the loop goes on; before, one raise
  from the prologue or the finally ended the pusher for the process's life),
  said once per exception kind on stderr; the failing path clears the wake
  flag and paces its retry at the backstop, then doubling to five seconds
  until a clean cycle, so a cycle that woke the pusher itself before raising
  cannot spin the loop. The restart ledger's boot-health row carries
  the first cycle's `stages` beside `firstCycleS`, so a slow boot names its
  stage without the kernel alive. Since the housekeeping moved to the jobs
  thread the row carries two firsts: `firstCycleS` and `slow` are the
  pusher's first cycle, the browser's own wait, the meaning every earlier
  row had; `jobsFirstPassS` and `jobsSlow` are the jobs thread's first pass,
  where the boot's cold reads now sit. The row is written by whichever loop
  finishes its first LAST, so `stages` carries both splits (a key both own,
  `jobs.other`, is summed); a jobs pass still open ten minutes after the
  pusher's first cycle closed has the row written without it, marked
  `jobsFirstPassPending`. The row's `gc` carries each first split's collector
  delta on its own, `firstCycle` and `firstPass` (each the split row's `gc`,
  the shape the `stageRing` rows carry, or `null`), never summed: the tallies
  are process-wide, so a collection inside both windows is in both deltas and
  a sum would count it twice; the key is absent when neither split has one.
  The row also carries `parse`, the assembly's road counters at
  the first cycle's end (T398): `serve`, `fold`, `restore` (with
  `restore:afterDemote`, the restores taken over an entry the gates demoted
  instead of a whole parse, and `restore:chainRefused`, a document that stood
  but whose leaf tail does not chain onto it: every tail record bearing a
  uuid or a parentUuid key must REACH, through its parent chain within the
  tail, the pre-cut spine tip, and only when the document's `tipChildless` bit says the writer proved,
  from the resolved graph, that the tip had no pre-cut child (a compaction
  anchored on it counts; an older document without the bit is not proven); a
  compaction boundary in the tail is held to the same rule through its
  effective parent, resolved as the parse resolves it (the logical parent,
  else, for a truthy anchor naming no known record, the preserved segment's
  tail, anchor or head that does; a boundary with no anchor is a root); so a
  null or missing parent, a self-link, a cycle, a tail uuid reusing a pre-cut
  record's (a uuid repeated within the tail is resolved as the parse resolves
  it, the last record's parent winning), a parent anywhere else in the pre-cut part,
  an unproven tip, an unknown parent, or a boundary re-anchored into the
  interior or onto an unknown uuid refuses, whatever the record's type (one
  standing disagreement with the cold parse remains outside the rule: a tail
  record whose stamp precedes the cut or the tip chains soundly but the
  write-time stamp-order guard is not re-checked, so such a restore can
  differ from a cold parse; a later round); a document written before the bit is unproven, so a standing
  document is refused at its first restore after the change, booked
  `full:refused`, and rewritten from the whole parse that follows the
  refusal, then and there (`write:afterRefusal`), so the next restore takes
  it; when the writer declines that rewrite (`write:afterRefusalSkipped`)
  nothing is taken and the document is marked as below; a document refused
  for the tail's SHAPE (a re-rooted tail, a reused pre-cut uuid), or whose
  offered rewrite the writer declined for any reason, including a transient
  decline (the entry evicted between the parse and the write, `noEntry`),
  which marks a document whose only defect was the missing bit until the
  next accepted write clears it, a bounded cost, is marked refused in its
  sidecar at the leaf's stat (under
  the key lock, re-read after the write) ONLY when the accepted rewrite
  reproduced the refused cut; a rewrite that moved the cut (since 2026-09-15
  the cut advances with the settled turns and with a compaction) is counted
  `write:afterRefusalMovedCut` and not marked, since the writer retired the
  old mark with the sidecar it replaced (its bytes kept beside it as
  `.meta.retired-<stamp>`, swept with the document) and the new tail is
  proven at the next restore; while a mark stands every
  road goes straight to the whole or cold parse with no proof and no rewrite
  (`restore:refusedStanding`, `seeded:refusedStanding`); the mark clears when
  the leaf moves or a write the writer accepts replaces the sidecar, and the
  boot sweep retires a mark whose sidecar carries a document version below the
  current one (a mark belongs to the cut rule it was made under; the sidecar's
  bytes are kept as `.meta.retired-<stamp>`, one count under
  `removed.refusedMark:version`; a rewrite of the sidecar that fails leaves the
  document and the mark standing for the next boot, one count under
  `removed.refusedMark:versionFailed` and one stderr line; an aside that could
  not be written is counted under `removed.refusedMark:asideFailed` and said
  once, the retirement proceeding; a mark already kept aside is never copied
  twice), so the next parse takes the version-refusal
  road once and the settle's write produces the current document; a cyclic resolved
  graph (a reused uuid closing a ring) no longer refuses the document: the
  writer's spine walk ends at the first revisit as the parse's own walk does,
  so the document's spine is the one the chat shows (until 2026-09-14 a
  hop-bounded walk refused the whole document under `skipped.cycle`, retried
  at every settle); a record without a uuid is not a node of the
  chain walk; the restore falls to the whole parse, at boot
  and after a demotion alike, and `seeded:chainRefused` counts the same
  refusal by the chain-membership and file-rewound readers, which then walk
  the file cold, T402), `foreign:<reason>` (the judges' walk over ANOTHER
  session's leaf refused that session's document quietly for the reason named,
  the document standing for its owner: a reader that does not own a document
  never notes it and never unlinks it, 2026-09-15; `foreign:refusedStanding` is
  that reader's cold walk under a standing refusal mark), `seeded:asmDocMemo` (a
  seeded walk whose document decode was served from the per-process memo, keyed
  on the document file's size and mtime: a leaf named by several sessions'
  episode rows decodes its document once per boot, not once per naming session;
  every stat check and the guard read still run per walk, a document is
  memoized only once those checks passed, and an owner's fallback drops it;
  the memo is reported under `asmCheckpoint.asmDocMemo` with `entries`, `bytes`
  as the documents' RESIDENT weight, each file's compressed size times
  `multiple`, the measured 10 a decoded document weighs against its gzipped
  bytes, and `capBytes`, a ceiling on that weight of MemTotal / 512 floored at
  64 MiB, `ROMP_ASM_DOC_MEMO_CAP_MB`; unrelated to `checkpoints.docMemo`, the
  fold documents' read memo), `restore:asmDocMemo` (a restore whose document
  decode was served from the same memo, since 2026-09-24: a restore after a
  descent, rewrite or nonleaf demotion over a document the memo still holds
  reads none of it, and its checks run per restore as a walk's do), `full` with
  `full:demoted` (an entry the gates demoted, the `g:<reason>` beside it:
  `descent` when the new leaf does not chain to the old through the delta
  (a parallel tool batch's results, each parented at its own call, fold instead;
  one that then moves an old record's kept membership counts under `kept` and
  takes the restore road a descent takes),
  `rewrite` when the leaf's record entry was replaced by a from-zero read
  under a new generation, `nonleaf` when a lineage file moved or grew,
  `inputs`, `recs-gone`, `no-leaf-slot`, `empty-graph`, `uuid-known`,
  `boundary`, `summary`, `promptid`, `skill-link`, `ts`, `kept`, and
  `reseat`, a whole entry whose own document the settle or the converge
  pass has written, re-seated on it by the restore road, where it lands
  here only when that restore refused the document),
  `full:noDocument`, `full:noDir` (no checkpoint directory), `full:refused` (a document that stood but did not verify,
  its fallback reason counted), `bypass` (a pending cut armed on the session)
  and `fallback`; the same block rides `asmCheckpoint.parse` on GET /perf,
  beside `asmCheckpoint.removed`, the checkpoint directory's removals and
  retirements per reason: a document file removed (a fallback's reason, or the
  boot sweep), a refusal mark the sweep retired from a version-old sidecar
  (`refusedMark:version`, the document stays), a retirement whose sidecar
  rewrite failed and left the mark for the next boot (`refusedMark:versionFailed`,
  nothing removed), a mark whose forensic aside could not be written
  (`refusedMark:asideFailed`). The row also carries `nudgeWalk`
  (T401): the first eight characters of the session ids whose parses the
  boot's nudge walk `skipped` on its memo, those it `parsed` (at most forty
  each), and how many it `deferred` to a later pass.
  `firstCycleStacks` is the pusher's stack sampled through the first cycle
  only (and `firstPassStacks` the jobs thread's through its first pass, the
  same shape, with `firstPassStacksFailed`), once a second for the first thirty samples and every five seconds
  after, so the sixty-row cap covers three minutes and a long cycle shows
  where it ended (each row the seconds into the cycle, the stage mark and
  the eight innermost frames as "function (file:line)", the /perf sample's
  shape, no session content), by a daemon thread that ends with the cycle
  and whose start degrades to no samples when a thread cannot be started;
  `firstCycleStacksFailed` counts walks that raised, so a short list is not
  mistaken for a fast cycle, and a failed walk fills a cap slot like a row,
  so an all-failing sampler retires with the cap. The cost is one frame
  walk a sample (about 7 us) and about 330 bytes a sample on the row (20 KB
  for sixty, 30 KB at worst) in a ledger with no rotation: the boot-settled
  writer (`_append_boot_settled`) parses every line of it at each boot, and
  two other readers (`_last_deploy_restart_t`, `_consumed_audit_t`) read the
  whole file before slicing its tail, so a 20 KB row is read whole by each
  of them from then on, and the file grows by that once per boot whose
  first cycle ran that long. The sampler exists because two live reads of a
  slow boot missed the cycle (the watch's poll was slower than it).
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
  boot, in total and per file), `docConsults` (fold documents loaded through the one
  validated read that the two boot restore paths, a write's carry and a
  retirement's consult share; at boot the restore paths dominate it, one per
  checkpointed file), `docMemo` (the documents that read keeps for the write
  that follows: `entries`, `bytes` as their RESIDENT weight, each file's size
  on disk times `parseMultiple`, the measured 4.5 a parsed document weighs
  against its bytes on disk, and `capBytes`, a ceiling on that resident
  weight of MemTotal / 512 floored at 64 MiB, `ROMP_DOC_MEMO_CAP_MB`; the
  ceiling is what the memo may hold in memory, not a sum of file sizes).
  `rewoundMemo`: the judges' incident scan used to read every dead episode
  file of a lineage whole at every boot (`_per_file_rewound`, 542 MB on one
  devbox boot); its verdict set per frozen file is now the fold `rewoundUuids`
  of that file's fold document, written from the walk's own read at the
  quiescence drop and restored at the next boot, so such a file is read whole
  once (a live session's own files, its /clear anchor among them, stay
  resident instead, since the chain walk reads them at every pass; a leaf
  with no assembly document, one with no compaction boundary, takes the memo
  road too, since the leaf road's seeded walk had nothing to seed and read it
  whole at every boot, and so does every cleared or resume-forked session's
  leaf, whose document is written over its lineage and cannot seed the
  one-file walk). The
  counters: the memo's answers (`served`), the walks it took (`walked`), the
  walks over a memo the file's growth or rewrite retired (`stale`; a file
  whose entry merely left memory and came back is walked, not stale; a growing
  file on the memo road, a live leaf without a seeding document or a growing
  anchor named in a scan, ticks it once per judge pass, the routine retirement
  by growth, so a rising count beside a growing file is expected and only a
  rise with no growth is a surprise) and the
  walks whose memo could not be read or stored (`fallback`: a document state
  of the wrong shape, or no reader entry after the walk).
- `stacks`: every live thread's stack, keyed `"<ident> <kind>"`. The kind
  is the thread's name up to the naming convention's colon (`sdk` and
  `sdk-intr`, `sdk-fbcause` (a session reading a standing fallback's cause off its transcript at an attach) for a session's threads, `codex` for a Codex session's worker,
  `end-host` for a session's end hook, `port-up` for a dial's port watch, `peer` for a postal peer loop,
  `romp-refused-mark` for the refused-echo mark a cut-off boot re-delivery writes aside), the
  target function for a thread the code left unnamed (`_ask_poll`,
  `_parent_watch`, `_update_check_loop`, `_tunnel_supervisor`,
  `serve_forever`, ...), `handler` for the HTTP server's request threads,
  `judge-index`, `judge-triage` and the other tiers' pool workers, `pool`
  for an unprefixed pool worker, `thread` for a default name with no target,
  `pusher`, `producer`, `index`, `triage`, `parse-warm`, `boot-warm`,
  `sdk-boot`, `first-cycle-sampler`, `jobs` (the housekeeping loop split off the pusher), `main`; never a
  session's name, sid, host or path (the ident
  keeps two workers sharing a kind apart). Each row has `self` (the thread building the
  sample), `stage` (the thread's current stage mark: the pusher's
  `jobs.<job>` or `push`, a handler's `connect`, `null` outside one) and
  `frames`, "function (file:line)" strings innermost last, at most 40; no
  locals, arguments or session content. Filled when the kernel runs with
  `ROMP_PERF_STACKS` set (a debugging aid for a served test on a runner
  nobody can log into) or when the request says `?stacks=1` (`romp perf
  stacks`, T401); `null` otherwise.
- `recordCache`: the reader's record cache (the JSONL records held in memory):
  `entries`, `bytes`, `budgetBytes`, `countCap`, `inserts`, `evictions`,
  `evictedBytes`, `budgetEvictions`, `dropped` and `droppedBytes` (the
  quiescence drop), `released` (every pop that removed an entry, whatever the
  cause: an eviction, a re-read replacement, an OSError pop, a drop; a #1735
  statistic only, since these entries are acyclic and it never triggers a
  gc-freeze reclaim), and `wholeReads`: every read that pulled a file whole,
  keyed `kind<-caller` (the reader's kind, one of `zero`, `rewrite`, `guard`,
  `shrunk` and `upgrade`, and the first calling function outside the event
  model and the parse family), with `count` and `bytes`; a tail read, an
  append and a restore's tail read are not whole reads and are not counted;
  `wholeReadsByStage` is the same table keyed `<stage>:<kind><-<caller>`,
  the stage being the pusher thread's current tick job (`jobs.<job>`) or
  `push`, `connect` for a fresh client's full push on its handler thread (a
  browser reload or reconnect), `none` outside those (T401), and `asmCheckpoint.hydratedByStage`
  does the same for the hydration rows.
- `asmCheckpoint`: the assembly documents since boot: `written`, `restored`,
  `fallbacks` per reason (`version`, `rows` (a version-6 document whose atom
  row fails its shape check at load, or fails its decode at the first read
  by any accessor of the index: the document is refused to the whole parse,
  at load or at that first read, counted once),
  `session`, `inputs`, `lineage`, `shrunk`,
  `rewrite`, `guard`, `identity`, `corrupt`, `restore`), `skipped` per reason
  (`noEntry`, `restored`, `written`, `noCut`, `reuse`, `closure`, `unsplittable`,
  `reconstruction`, `oversize`, `unencodable`, `offsets`, `stat`, `write`;
  `offsets` is no reader entry at all, a tail entry (one read from a
  checkpoint's offset, its base above zero), or an entry holding fewer records
  than the tree read, or more for a lineage file or under another generation
  or over a base the tree's adapter did not read from zero: a LEAF entry that
  merely grew since the settle's parse lends the prefix the tree read, so a
  busy session's document is written between its appends; a lineage file's
  skip row carries the stat of the records the tree was parsed from, so a
  record it gained after the parse fails the next boot's check. The standing
  residual, shared with the reader's grown path: an early record edited in
  place at equal length plus an append passes the 64-byte guard, like a
  same-size same-mtime rewrite),
  `hydratedAtoms` and `hydratedBytes` (bodies read on demand for atoms before
  a cut), `hydratedBy` (those bytes per calling function), `restoreMs`, the
  restore's parts since boot in milliseconds to three decimals, each added on the
  return it names (`load`: the document read, decompressed, decoded and its
  file checks; `verify`: the turns section's identity and coverage, or the
  atoms-only form's rows built and its identity proven; `index`: the lazy
  index over the rows and the pre-cut turns; `seed`: the adapter's pre-cut
  graph facts; `total`: the whole restore, entry to return, so the unnamed
  remainder, the tail's parse through the seeded adapter, is `total` minus
  the four), so a boot read names the mover; since document version 6 the
  atom rows are stored as pre-serialized JSON strings, so the decode builds
  strings, not dicts, and the index takes each row's bytes with no re-encode
  (the deploy boot of that version refuses every standing document as
  `version` and the settle rewrites it: that boot is the migration, the boot
  after is the read), and `converge`: the
  pass's writes of idle leaves' documents from the boot's own parse
  (`candidates`, `writes`, `bytes`, `deferred`, `skipped` per the writer's
  reason).
- `asmIndex`: the lazy index (T323 stage 4c) a restored session's pre-cut turns
  come from: `materialized` atoms built from the document's rows since boot,
  `materializedBy` (per consumer), `materializedByStage` (the same builds
  under the calling thread's stage mark beside the consumer, as
  `hydratedByStage` does for bodies: `push`, `connect`, `push.session`
  (the backend's targeted one-session push, on a thread of the
  backend's own at a session's connect handshake; the mark is the
  thread's default, so a backend calling the push synchronously under
  a request keeps the request's route), `jobs.<job>`,
  `judge.<tier>` for a tier thread and every worker of the pools it
  submits to (the mark rides the submit, as the pass frame does, since a
  thread-local does not cross into a pool worker), `http.<METHOD>.<route>`
  for every request and the socket a GET becomes (the route is the path's
  first segment, or its first two under `/push`, `/tunnels` and `/usage`,
  whose roads differ by the second), `warm.parse`, `warm.boot`,
  `producer`, `revive`, `rewind.migration`, `rewind.holds`, `move`,
  `remote-ws`, `federation.push`, `federation.pull`, `federation.ask`,
  `ask-poll`; `none` means the build ran on a thread with no mark, which
  should not happen: the kernel's thread census (every Thread, Timer and
  pool construction site in the kernel, the judge and the two session
  backends, walked by the ast, and every kernel callback the backends are
  handed, since a backend runs those on threads of its own) holds every
  thread marked or listed as a pure I/O helper and every handed callback
  marked or listed, and a `none` row on a live `/perf` names a thread or a
  callback the census missed), `resident` (the entries in the
  process-wide LRU, `cap` of them across every session: the machine's memory
  over 32 KiB, never under 500,000; the LRU holds each turn's atom list by a
  weak reference, so a tree nobody holds any more keeps nothing resident but
  entries that expire, and `resident` counts those too until they do:
  before this (measured 2026-09-15) a strong reference kept every superseded
  generation's atoms, and its whole index behind them, resident until they
  aged past the cap, about 1.2 GiB on a box whose LRU sat exactly at its cap
  of a million entries, and live atoms evicted by stale ones were rebuilt),
  `evictions` (a live entry past the cap: its slot's memo dropped, never a
  field in place), `expired` (an entry whose list has been collected, dropped
  when it reaches the cap or when a live list registers a slot under the id the
  dead one held, no slot touched either way), `released` (entries popped the moment the
  assembly entry that owned their index was dropped or replaced, rather than
  a million entries later at the cap), `rowDecodes` (document rows decoded,
  a build's or a light read's), `userFacts` (below), and `restoredTurns`.
- `skillLoadIndex`: the judge's skill-load boot pass (the tops older stores minted from
  the harness's own skill load): `filesRead` and `bytesRead` (transcripts read raw this
  boot, appended tails only once the persisted index holds a file), `filesIndexed`, and
  `checked` (prompt anchors known not to be a wrapper, never read again).
- `chatPages`: the rendered pages of chat history before a session's render
  floor (the chat wire's `loadOlder`, `loadAround` and `loadTurns` answers, below):
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
  (the display's asks among them, with `byRoad`, `wholeBytes` and `bySid`,
  below), `judge` (the rest), `hits` (the display's asks served from the
  store) and `sharedHits` (every hit). The acceptance number of the
  lazy-transcript work: a boot with no client connected reads `kernel` zero,
  and a connecting chat client adds at most its shown tabs. A miss is not a
  whole parse: `byRoad` counts the display's misses by the road the parse
  took (`serve`: the transcript did not move and the held tree was served
  again, after a states row, a cleared rollback cut, or a stored parse the
  store dropped while the event model still held the tree; `fold`: the
  appended records folded onto the held tree; `restore`: the pre-cut part
  from the assembly document and the tail from its cut; `full`, `bypass` (a
  pending rollback's cut) and `fallback` (a road raised and a plain parse
  ran, counted here and not under the road that raised): the transcript
  walked from its first record), and the roads sum to `kernel`.
  `wholeBytes` adds the leaf transcript's size for the last three only, the
  parses that walk the whole file. It counts the leaf alone: a whole parse of
  a session resumed into a new transcript file also walks the files it
  resumed from, which are not added, so for such a session, whose new leaf
  is short and whose history sits in the earlier file, the figure reads low.
  Until 2026-09-24 a `bytes` field added the leaf's size at every miss, a
  fold's included, which read as a whole re-parse per miss; it is gone.
  `bySid` counts the misses per session by the first eight characters of its
  id.
- `stages_ms`: `prelude` (the cycle's opening: the liveness snapshot and the
  names), `jobs` (the cycle's tick jobs outside the push) and inside it one
  `jobs.<job>` per tick job (`jobs.interruptBlock`, `jobs.autoNudge`,
  `jobs.convergeCheckpoints` and the rest, T398), `push`, and inside it
  `push.chat`, `push.feed`, `push.timeline`, `push.send`, `push.warm`,
  `push.feedFirst`; a fresh snapshot lists every one at zero. The `push.*`
  stages count every push, including the one a connecting page gets, so they
  can add up to more than `push`.
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
  also carries `coldSkipped` (one count per tab per push the cold-tab gate skipped:
  a tab with a transcript, not built since the boot, watched by no connected chat client,
  held as a skeleton by every connected chat client, with no Sessions pane connected, and with
  a live row to state its status from (a tab with no live row is built, not skipped); the
  same tab counts again on every later push until the page asks for it, 2026-09-14),
  `active_built` and `bg_built` (rebuilds of the watched tab
  against rebuilds of a background tab), `moved` (builds not cached because
  an input moved while they ran; the next cycle builds them again),
  `baselineRaced` (the chat wire's shared delta baseline was popped by the
  seed's detector and the session marked: two whole-frame senders raced on a
  session with no baseline, or the detector's accepted false positive named
  under `changeAt0` above; the mark's only other trace is the next cycle's
  `changeAt0` rows, filed only for a base holder alive then whose repair did
  not dedup), `baselineRepaired` (a cycle whose loop read the baseline absent
  sent every base holder the full and its write took a standing mark off;
  raced minus repaired is the marks still standing plus the tabs that left the
  strip, whose eviction clears the mark with no repair) and
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
  components counts under each. `row_by` splits the `row` misses further by
  the live-row position that moved (`state`, `since`, `billing` (the row's
  authLive, auth, authLogin, authLoginLive and authLabel; distinct from
  `miss_by`'s `auth`, the machine's key on hand), `retry`, `agents`, `tasks`,
  plus `presence` for a row that appeared, left or changed shape): the key
  folds only the row fields a card reads, so a context refresh or a
  background agent's tool call moves no key. `failed` counts the card builds
  that raised (a memoized entry's decode, the key, the derivation, its
  dependency key, the serialization or the memo put), cumulative, and
  `failing` the sessions whose last build did, a standing fault rather than
  history. `coldLive` counts, per session per build, each living session not
  hidden from the feed, with a transcript, whose cache-only parse read
  missed; a session nothing has parsed at its current version (no client's
  tab, no judge, no background warm, none of the card build's own parse
  paths, nor any other road that parses through the parse store) rides it
  every build, and the warm gate leaves an unmoved, idle session cold by
  design, so a standing count is those sessions, not a fault.
  `coldFlip` counts the subset the memo held warm and re-read in place with
  one kernel parse (also under `parses.kernel`) instead of deriving cold;
  `coldFlip` climbing every build for one session with no appends means its
  parse never stores, which should not occur. `reg` is the SDK registry
  record's state plus the two fields a card reads, `bgLedger` and
  `spawnedAt`, and the death marker's identity; the record's other fields
  move `reg` no further, and a transcript-less live row's `cwd`, `lastSid`
  and `name` reach the key through its session row, under `transcript` and
  `names`. Nudge facts invalidate only entries that read
  the changed node's count, failure state or displayed history. The key on hand, the
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
  was built and compared, then not sent. Every frame the targeted one-session
  push sent, its tab strip and the cold-tab gate's status included, is counted
  under its slot with a `.targeted` suffix (`chat.targeted`,
  `status.targeted`, `taborder.targeted`), so a full from that road reads
  apart from the pusher's cycle. The suffix is the road's, whatever the stage
  mark: the two backend hand-offs (the SDK connect handshake, the Codex
  backend's stream events) run under the `push.session` stage above as their
  thread's default mark, while a create, a fork or a promote calls the push
  from a request handler and runs under that request's
  `http.<METHOD>.<route>` mark.
- `goals`: `loads`, `saves`, `writes` on the goal stores through the writer's
  loader (`load_goals`) and `save_goals`, and `carryBase` and `carryNoBase`, a
  save's rebase that had a field base for its carry (the bytes the holder read
  or wrote, which a loaded store keeps its own reference to) or had none and
  carried no plain field: a fresh store's rebase, with no parseable file at the
  load and another writer's publish before the save, or a holder's without its
  reference (a store rebuilt from JSON, a copy of the shared read-only view)
  whose version left the raw-parse memo and its histories; the pusher's
  read-only loads go through the shared store cache and show under
  `memos.shared`, not here. A save that would rewrite identical bytes is a save
  without a write.
- `memos`: the identity memos on the goal-store path. `pass` is the
  judge pass's stat-keyed store memo (`hit`, `miss`, `compare_miss` for a
  store whose bytes moved under an unchanged stat, `fail`, `evict`, `punch`,
  `skip` for the files a pass stepped over because the compaction sweep ruled
  their store unowned, its occupancy `entries`, `bytes`, and `unowned`, the
  stores currently ruled out, a gauge); `shared` is the pusher's shared
  read-only store cache (`hit`, `miss`, `compare_miss`, `refuse`, `dup`,
  `absent`, `corrupt`, `unreadable_journal`, `evict`, `fallback`, `poisoned`,
  with `entries`, `bytes` and `off`); `chain` is the write-moment chain memo
  (`hit`, `miss`, `populate`, `bypass`); `convergeDeclined` counts the main
  converges that asked no restart because this kernel was already leaving (the
  exit path held the lock: before the pull, the row's `phase` is before-pull
  with the target it did not pull, and the next kernel converges on its own;
  after the pull, after-pull with the checkout it moved, which the successor
  boots on; either way a `main-converge-declined` row stands in the
  restart-audit ledger where a second sigterm used to); `sessionsListing` is
  the kept GET /sessions listing (`built` by the pusher's cycle when its key
  moved, `served` to requests from memory, `requestBuilt` once before the first
  cycle, `faultBuilt` per request while a cycle's build failed and the kept
  listing may be stale, `missBy` the key input that moved: rows, names, notes
  or registry); `nudgeWalk` is the auto-nudge walk's
  parse gate (T401): `looks`, `skippedParses` (a session whose files are
  unchanged since its last completed look and whose clock legs, noted by that
  look with the instant each could flip, have not come due; the skip repeats
  the recorded verdict and does nothing else; only a look whose verdict came
  from a road marked file-keyed, or the full walk run to its end, records a
  skippable memo, every other exit an unbounded one), `parses`, `coldParses`
  (parses no cache held), `deferredSessions` (the yield: with a client
  connected the pass stops after a look that paid a cold parse; the first
  deferred session is the resume cursor, so the next pass rotates the
  recency order to start there and every session is reached within as many
  passes as there are cold parses), `unbounded` (memos refused because a leg's release is not one
  of the session's files: a deferral retired by a judge pass, a stamped wait
  a peer's bounce can end, an owed reminder a refused ledger write left
  standing), `clockDue` (memos refused because a noted flip has come) and
  `wakeOnly` (looks with injected follow-ups off, which neither skip nor
  record because the toggle is not a file, so that configuration keeps the
  boot's cold parses); the files the memo keys on are the transcript, the
  state log, the goal store with its override journal and archive, the
  episode log, the clears log, the postal log, the kernel's downtime log
  (the working verdict's suspension check reads a list that log refills)
  and the nudge ledger (one file for the box, so any ledger write moves every
  session's key and the next pass re-evaluates each alive session once);
  the pass takes every session's stat before it reads any pass-level
  snapshot, so no input a look reads is older than the key its memo is
  recorded under; a debtor's key also carries the registry row
  (`STATE/sdk/<asker>.json`, an absent row as a stable absent marker) of
  each peer with an open ask on it, oldest asks first and at most eight
  (the persisted memo row is 22 to 38 elements: the ten files and up to
  eight rows), because a dead asker's ask becomes owed again only when the
  asker revives and a revival writes that row; the debt leg reads a keyed
  asker's aliveness from that same row (alive true or false, the SDK
  backend's own liveness record), never from the pass's alive set, which is
  older than the key, so the verdict and the key come from one file and a
  revival landing between the two cannot record a memo that owes nothing;
  a row that cannot be read or decoded, or parses without an alive bit, is
  unproven, neither dead nor alive: the look notes None under
  `askerRowUnproved`, so one transient read fault never latches a
  skippable memo, and the ask follows the pass's alive set, the backend's
  own answer over that row or its last good content, so the reminder never
  asks a debtor to answer a peer the backend calls dead (a missing row is
  dead, the key's absent marker); a keyed
  dead asker notes nothing and the debtor skips like any quiet session;
  any asker beyond the eight keyed rows notes None under `askerOverflow`,
  alive or not, since its row is outside the key. The pass stats the postal
  log before it builds the asker index from it and the key carries that
  earlier stat, so the key never claims a newer log than the selection
  read. The limit:
  the row invariant holds for the SDK backend only; a Codex session's
  liveness is in memory with its registry at `STATE/codex/registry.json`,
  so a Codex asker's revival would move nothing in a debtor's key (not
  reachable today: a Codex session cannot identify itself to the bus and so
  cannot ask). The honest measure of what remains unbounded is
  `memos.nudgeWalk.unbounded` over looks on the first boot after this lands,
  since the leg counts are notes, not looks. `unboundedBy` counts the
  unbounded NOTES per leg at the look that recorded them; the legs the
  kernel emits are `askerOverflow`, `askerRowUnproved`, `debtUnproved`,
  `debtUnlanded`, `deferralNew`, `pausedTiers`, `deferralStanding`,
  `queuedSend`, `storeFault`, `allDelegated`, `awaitingPeer`,
  `stampedWait`, `unjudgeable`, `refusedWrite`, `legacyNoAnchor`, and
  `unmarked:<verdict>` when no named leg noted the look (the None-site
  census in the gate's test pins that every site names its leg with a
  literal); the legs partition the NOTES,
  not the looks (a look over two top goals can note two legs); the
  dead-asker notes (an ask in the postal wait maps whose asker is not alive
  now) were about four in five of the notes on the first boot with the
  counts, since an ask a dead peer left in the log stays there for good,
  which the keyed rows answer for the memo; ageing such an ask out of the
  wait maps would delete a wait the postal surfaces show and is the user's
  call, the open hygiene question here; while `unbounded` counts a LATER
  look's refused skip, so the two are not comparable;
  `spendTree` is the spend guard's memo of each live
  session's subagents tree (`entries`, `bytes`, `bound`, a sixty-fourth of
  the machine's memory or `ROMP_SPEND_GUARD_TREE_MEMO_BYTES`, and the
  reads since boot: `served` (passes that served an idle session's standing file list from the memo with no stat, plans/spend-guard-events.md), `dirStats`, `fileStats`, `entryStats` (the per-entry
  stats a listing performs), `listings`, `loaded`, `loadFailed`, `dropped`
  (paths outside the root a load discarded), `written`, `writeFailed` (a
  memo write that raised, a read-only directory or a full disk, said once a
  life; the memo stays dirty and is retried each cycle), `dumpSkipped` (a
  write skipped after three dumps lost the race with the pusher, said once
  a life), `evicted` (memos the byte bound shed), `swept`); the memo is
  persisted at `STATE/spend-tree/<sid>.json` when
  dirty and at exit and loaded lazily when the session's guard first runs
  after a boot. What the load saves is the listings (the scandir and its
  per-entry stat for every directory): a boot stats each directory once and
  lists only one whose mtime moved. One stat per file remains, because an
  append while the kernel was down moves no directory's mtime, and it is
  spread over the cycles after the load, hot files first, at most
  `SPEND_GUARD_RESTAT_PER_CYCLE` (400, about 2 ms) a cycle, so the largest
  tree is whole again within seven cycles and no cycle carries a whole
  tree. A corrupt or misshapen file, or one that does not name the
  session's own root, is a failed load and relisted, never raised; a path
  outside the root is dropped and counted; a memo the byte bound evicts is
  written first when it is dirty (a drain step marks it so, and so does any
  stat that changes a stored mtime, so a file that grew is carried to disk;
  a memo whose file already holds its state is not rewritten, since on a
  binding bound the eviction fires every cycle), with its remaining re-stat
  list, and its rescan clock stays in memory for the kernel's life (dropped
  when its file is swept or fails to load, or when the session leaves the
  live set), so the reload drains on and runs its full pass
  instead of restarting both; the directory is swept once per kernel life at
  the guard's first tick, before the disabled ceiling's early return, so a
  kernel with the guard off sweeps too (never the boot's first cycle, since
  the sweep parses every memo), of memos whose leaf is gone, that name no
  leaf or that do not parse, and of tmp files a kill left (a failed replace
  unlinks its own tmp at once); the guard's job itself skips
  the boot's first cycle, since its first pass lists every alive session's
  tree (4.2 s on one boot, 60 trees of 16,752 agent transcripts in 1,542
  directories, the largest 2,581 files) and a runaway spend is minutes,
  not the first cycle (T401 follow-up); `subagentTree` is the memo of each
  subagents directory tree the builds read (the sidecar map, the agent-file
  lookup, the feed key's subagents component): per root, the directories in
  walk order and each one's identity (inode, mtime, size, ctime) taken before
  it was listed, served while every identity stands because a directory
  entry's creation, removal or renaming moves its parent's stamps and every
  parent is in the list, with `hit` and `miss` (trees vouched for by one stat
  per known directory against trees walked), `scoped` (reads served from the
  cycle's one sample with no stat at all: one sample per subagents root per
  pusher cycle, jobs pass or connect push since 2026-09-18, the first reader
  validating or walking and every later reader of the cycle served it, so
  scoped over hit plus miss plus scoped is the share of reads that re-sampled
  a root another reader took in the same cycle), `evict` (roots dropped because
  no alive session's transcript names them, on every jobs pass and, as a
  belt, after each feed build and from the tracking-off frame), `dirStats`
  (the stats validations paid), `walkMs` and `validateMs` (the time in each,
  every thread), and the gauges `roots` (entries) and `dirs` (directories
  held); a directory stamped within the last two seconds, or one whose
  listing failed, is stored unvouched and walked again until it is quiet and
  lists cleanly, the racy-stamp rule, since a filesystem stamps with a
  coarser clock than the wall clock and a failure moves no stamp;
  `nudgeGate` is the auto-nudge walk's
  planner-placement gate, derived once per (parse, store, episode log, clears
  log) and served while all four stand (`served`, `derived`, and `failed`: the
  derivations that raised;
  the except leg answers NOT unplanned, so the walk skips the planner-queue
  hold and proceeds on the closer gate alone, and a non-zero `failed` means
  nudges were waved PAST the planner gate, not held; zero on a healthy box, and
  a healthy quiet box serves almost every cycle); `cleared` is the feed's clear set, parsed once per state of
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
  `planned`, `recorded`, and since T401 (5c) `restored`, `refused`,
  `persisted`: the gate's memo of "the key of the last pass that had
  nothing to do", one row per session, persists across boots in
  `STATE/planner-seen.json` (version 1, the tick-seen shape: a row is never
  an answer on its own, the key is recomputed at the pass and compared, a
  malformed row is refused, rows are dropped with the sessions a non-empty
  pass discovers (an empty discovery is unknown, not every session gone,
  and leaves the rows for the next non-empty pass), the write
  is atomic under a per-writer temporary and re-armed on a failed replace).
  The key holds every file the plan tier's inventory names (the parse, the
  store trio, the episode log, the leaf's task store, the captions file,
  the death marker and `cleared.jsonl` by stat; the reg by the values the
  pass reads, its `spawnedAt` and the SDK-owned bit; the stall slice by
  this session's records; and each running background launch's deadline
  bit under the pass clock). The rule: a persisted key term must be stable
  across the event it persists over, so a file rewritten at every boot (the
  reg at attach, the stall slice by the jobs pass) is keyed by the values
  the pass reads, never by its stat (derivation 1 keyed both by stat and no
  row stood across a boot: the second deploy boot read restored 20, skipped
  0). The file carries a derivation pair (the planner's derivation version,
  2 since that fix, and `PLACEMENTS_V`), and a file written under another
  pair is refused whole, so the first pass after such a change plans every
  session once and rewrites the rows (the v1 rows are refused once and
  rewritten under 2). `mismatchByTerm` counts, for every row that stood in
  the table and compared unequal at a pass, the indexes of the key terms
  that differed (reset with the process), so a read boot names a term that
  moves at boot instead of leaving it to a guess. `restored` counts the rows a boot loaded, `refused`
  the rows it would not trust (a file that cannot be read or decoded counts
  once per fault spell and leaves the load unlatched, so the next pass
  retries and the exit drain's forced write declines meanwhile; a torn,
  empty or other-shaped file counts
  once; another derivation counts every row), `persisted` the rows on disk
  after the last write. Before it every boot re-planned every session
  (`planned` 20 and `skipped` 0 on the 2026-09-14 read boots); the first
  boot after the change has no rows and re-plans everything while its
  passes record and persist, and the boot after that is the one to read.
  The planner runs behind two gates. The outer gate is
  the judge's evidence gate around `_plan_session` (`docs/judges.md`, "Ops and
  knobs"): a session whose signature equals the one the planner stamped after
  its last complete run is skipped before it is submitted. It keys on the
  same files as the inner gate by identity, plus derived values the inner
  key does not read (the reg's `spawnedAt` and backend, the stall slice's
  value, the task-store fingerprint). The inner gate
  sits inside `_plan_session` and sees only the sessions the outer gate ran: a
  session whose parse, store, journal, archive, episode log, its leaf's task
  store, captions file, reg file, death marker, `cleared.jsonl` and stall
  slice file have not moved since a pass that had nothing to do, and none of
  whose running background launches has crossed its deadline, is not planned
  again. The inner gate records a pass only when it placed
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
  gauge `entries`), and behind it a memo PERSISTED across boots at
  `STATE/intr-marks.json` (version 2: `{"v": 2, "rows": {sid: [mtime_ns,
  size, cut_t, cut_cause, sdk_owned, last_intr, last_human]}}`),
  one row per alive session keyed on the transcript's stat, the states log's
  newest machine-cut pair and the parse's sdk-ownership bit (the input that
  decides whether a programmatic prompt is the human's), all taken before
  the tally reads a row, and no key at all while a bare rollback's cut is
  armed for the session (the parse is then a truncated world no file
  records, so nothing is served or persisted until the arm clears; the arm
  is checked again after the tally, so a cut armed meanwhile is answered
  but not persisted); the row is the judge family's alone (the display
  family's parse carries live-merged atoms and takes no disk key); written
  when a row changed and at exit, dropped with the session when it leaves
  the alive set: `restored` counts a boot's marks served from a row under a
  matching key with no tally, `refused` a row the load would not trust
  (malformed, of another length or version, not under a uuid-shaped sid:
  recomputed, never read as dead; a refused row stands on disk until the
  next changed write), `computeMs` the whole milliseconds the cold tallies
  took, `persisted` the rows held. The light facts the tally reads are cached
  per pre-cut index (user rows only, about 447 bytes each, at most 8192 rows
  an index, the cache cleared whole past that; the parse cache holds up to
  256 indexes, so about 937 MB at the theoretical worst; `asmIndex.userFacts`
  on `/perf` is the gauge of resident facts summed over the live indexes,
  falling when an index is dropped) and never built into atoms; a row
  whose interrupt flag lives only in an inline body is handed to the build. The cold tally itself walks the transcript's USER rows
  through the pre-cut container's light facts (type, time, the recorded
  author, the interrupt flag from the lazy header) and builds no atom but
  the romp-authored notices a stop's classification reads, so a session that
  moved pays a tally linear in its rows instead of the whole atom build; the
  display family's live-merged atoms are not on disk and miss as before. `deadWait` is the dead-wait sweep's reads: `passes`,
  `candidates` (corroborated-dead sessions walked), `sharedLoads` (reads
  through the shared read-only store view, one per store per pass: the
  candidate's own and every alive session's for the peer-death arm),
  `loadFaults` (a view that could not be read or parsed, of any kind; the
  candidate stands down re-armed and the next pass retries; an OSError
  files a judge-errors row, `store-unreadable`, once per fault episode and
  prints nothing, and any other exception is said on stderr once per
  episode, an episode being the pair of the store and the fault's text; an
  alive session's store the view cannot read re-arms the candidate too, so
  that peer's conversion waits for the next pass rather than the next
  death), `sharedFallback` (a view that degraded internally to a private
  load: an absent store file, an unreadable journal, unparseable bytes, the
  shared cache switched off; told by the object the view returned, a plain
  store in place of the frozen one, never by a global load count another
  thread could move; while it climbs the pass is back to the private-load
  cost), `mutableLoads` (every private load the sweep's work makes: the two
  block writers' own, counted inside them so they mean what the writer did
  wherever it is called, the sweep's three sites and the wake goal's dormant
  branch alike, and the heal's one load when a briefless procedural block
  stands), `blocks` (counted inside the writers: each block written), and
  `healed` (a briefless procedural block whose brief was settled from its
  why, re-tested on the fresh node before the write). 
  `tickSeen` is the event-keyed tick jobs' memo (the
  interrupt block, the working note, the nudge walk's looks), the ten-file
  key compared per session, with the gauge `entries` and `byJob`, one block
  per job: `hits` (the one return that skips), `misses`, `neverSeen` (no
  kernel on record had looked), `noTranscript`, `clockParse` (the walk's
  parse on a matched key that a clock leg refused to serve: a flip due, a
  None flip, the closer toggle off), and `missBy[file]`, which counts, per
  miss, each key position that differed from the recorded one so a boot read
  can name what moved (the counts overlap: one miss counts under every
  position that moved, so their sum can exceed `misses`; read them beside
  `misses`); the positions in order are `transcript`, `states`
  (the state log), `store` (the goal store), `overrides` (its journal),
  `archive`, `episode`, `cleared`, `messages` (the postal log), `downtime`,
  `ledger` (the nudge ledger, one file for the box), then `askerRow` for the
  walk's asker registry rows and `shape` for a key of another length or an
  unreadable entry; per job, hits plus misses plus neverSeen plus
  noTranscript plus clockParse is the checks. The interrupt block's key
  keeps that shape but moves only with the files its road reads: the
  transcript, the state log, the downtime log, the goal store with its
  journal and archive, and the clears log (the store readers' override
  replay gates a journalled move on the clears log, so a clear or an undo
  row busts the key by design); the ledger position carries this session's
  own `intrBlocked` row (a checksum and its length) rather than the ledger's
  stat, while the episode and messages positions hold the constant pair
  `-1.0, -1`, which no stat can produce; so a postal message or a walk
  write to another session's row no longer re-evaluates every session's
  interrupt block (the quiet boot read of 2026-09-13 counted 125 interrupt
  block misses: messages 50, the ledger 50, cleared 25, and no episode
  row; the two constant positions and the ledger row answer 100 of them).
  `statesOverlay` is the awaiting overlay's read of the states log through
  the shared append-incremental reader, one carried answer
  per states file (`hit`: the records were the cached ones and no row was
  stepped; `append`: only the appended rows were stepped; `refold`: every row
  was stepped again, after a rewrite or a shrink or on the file's first read;
  `fail`: a read that failed on a file that exists, answered as no overlay,
  memoized nothing and named once per episode on the kernel's stderr;
  `evict`: entries dropped for sessions that left the alive set; and the
  gauge `entries`). `parkedHandoffs` is the feed's fold over the postal log
  for the handoffs parked in a dead session's maildir (2026-09-18): one
  carried set of the parked sends not yet recalled or bounced, so a quiet log
  is one cursor check per feed build where the scan walked every row of the
  log before (`hit`, `append`, `refold` and `fail` as above, the failure
  answered as no parked handoffs for that build, memoized nothing and named
  once per episode on the kernel's stderr; `restore`: the cursor came from
  the log's checkpoint and the tail alone was stepped; `cold`: a checkpointed
  cursor without its state, stepped from its cut; and the gauge `entries`,
  the candidates held). Whether each candidate is still parked is read from
  the maildir at every build, as before, for no more than the paths the walk
  checked; the fold only spares the walk. The compaction sweep after each
  judge pass evicts from
  `pass` and `shared` the entries of stores no session in the discover window
  owns, so both stay bounded by the live board; the courier's and the
  planner's change-gate tables are pruned to the sessions each pass discovers,
  the evidence gate's stamps are cleared at a fixed cap, and the awaiting
  lift's tick drops the gate's and the placed-launch memo's entries of
  sessions that left the alive set. The interrupt tick drops from `intrMarks`
  and `statesOverlay` the entries of sessions outside its alive set each
  cycle; past 256 entries the `statesOverlay` cache also sheds the cursors
  whose reader entry is gone or replaced (they could only refold or restore);
  a drop `evict` does not count and `entries` shows. `lanes` is the timeline's
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
  `judgingBand` is the timeline's judging band memo: a completed judge run's
  entry is held under its usage row while the row and the gloss it borrowed
  stand, so an unchanged entry is the same object build after build and the
  bars fill re-encodes only what changed, and a cursor skips the retained
  rows each verified to end before the horizon. `builds` and their wall
  `ms`; `rows_skipped` and `rows_visited` per build; `entries_reused` and
  `entries_minted`; `resets`, a cursor dropped for a rotated log, a left
  prune the reader did not count or a horizon moved back; `compact_reused`,
  `compact_minted` and `compact_ms` for the compact wire form's own identity
  memo; and the gauges `entries` and `compact` (the two memos' held
  entries), `bytes` (their containers, estimated) and `bound`, the band's
  wire cap (20,000 entries): only entries that reach the frame are held, so
  a judge storm's rows never widen the memo.
  Four memos cover the chat build's per-build fixed costs, each keyed on the
  inputs it reads and evicted by the pusher with the tab set (a comment thread
  built this cycle is kept, like its fold prefix). `chatMergeSets` is the
  live-tail merge's memo of the sets it derives from a parsed transcript (the
  uuids and user texts the transcript already holds, and the newest human
  turn's time), one entry per session keyed on the parsed session object's
  identity and shared by the chat, feed and timeline builds of one cycle:
  `hit` and `miss` (merges served against derived), the gauge `entries`
  (a session neither shown as a tab nor alive is dropped), and two numbers
  a miss records (T401 (5b)): `floorAgeMaxS`, the largest distance from the
  newest atom's time over every turn, live tail included, back to the
  oldest live echo's send that floors the derivation (a zero floor, an echo
  with no send time, is skipped, and a floor newer than every atom
  contributes zero), and `builtAboveFloor`, the restored pre-cut user rows
  the derivation itself built above such a floor since boot (never another
  road's builds, never the rows it read already built); a restored session's
  pre-cut turns above the floor are read through the index's light facts,
  building only the user rows that carry text, so the two say whether a
  dropped echo days back should hold the floor at all. `chatPostal` is
  the chat fold's memo of a tab's sealed postal cards, keyed on the values
  the cards embed from outside the transcript (this session's revision of
  the postal index: the records addressed to or from it, their outcomes and
  the records with no recipient, and, per card, its caption and its peer's
  name and colour; since 2026-09-18 a message between two other sessions
  moves none of these, so it is a `hit`): `gate` (gate checks that
  re-hydrated a tab's sealed cards because one of those values moved, or
  because the entry was sealed outside the pusher's names snapshot and had to
  be verified), `hit` (checks that verified the sealed cards from their
  recorded values without hydrating), and `commit_new` (raw postal events
  hydrated at fold commits; each is hydrated once, when it is first
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
- every history reply names its TURN SPAN (`span: [lo, hi)` in the kernel's turn
  numbering) so the page can place it among its regions, the runs it holds and
  the gaps it does not; the session frame carries `tailLo` (the tail run's first
  turn) and `pageTurns` (the page the gaps ask by), and the tail run is always
  resident and live: no client is ever detached, and no window pauses live
  updates;
- `loadAround {id, uuid}` is answered by `chatWindow {id, anchor, events, span,
  moreBefore, moreAfter}` in one round trip (`missing: true` when the anchor is
  in no page); the page inserts the window as a run by its span, and a
  navigation's window lands while any other fills in place; a reply with no
  `span` is an OLDER host speaking the pre-regions protocol, and the page says
  so rather than dropping the reader where a pre-jump left them;
- `loadTurns {id, lo, hi}` asks for a gap's page directly and is answered by
  `chatTurns {id, span, events, head}` (`head: true` at the head, the head cards
  riding along; an empty or out-of-range span is `missing`); `loadNewer` is
  retired (`missing, retired`): an OLD bundle against this kernel is the only
  caller left, and its detached client snaps to the tail on the retired reply,
  dropping the pages it had walked — acceptable, since an old bundle holds no
  regions to keep them in;
- three rules the page keeps for its regions: a socket death clears every
  in-flight history ask (the page asks, the landing's held gap, the notice, the
  cancelled mark), tells the reader once that a jump in flight was lost, and
  lets a gap met again on the healed socket ask anew; a gap is sized by its
  TURN count times the rendered run's measured pixels per turn (a turn is a
  user row plus its reply and any tool rows; a per-display-unit average drew
  gaps half true); a fill anchors on the first row that intersects the
  viewport, whatever the sign of its top; with no row on screen (or that row
  gone from the rebuild) it names the point under the viewport top as a TURN
  and a fraction into its gap and puts that turn back after the rebuild, so
  the point moves by less than a turn (the head stays at zero); every row
  carries its own turn, stamped when it is painted, so the point is named by
  the row's position and never by looking its uuid up (a row anchored on an
  answer's tool_result uuid has no event of its own), and a fill that leaves
  no row on screen re-windows once around the named point;
- the kernel's per-client base is TAIL-ONLY: a reply moves the base's first edge
  only when its span reaches the tail run, so the tail's deltas keep flowing to a
  reader in older history; a reconnect's `ready` starts a fresh base. The base's
  `last` skips the live overlay cards and the kernel's transient keys (an input
  echo, the command chip), which ride the suffix of the deltas after them like
  the overlays: the landing that replaces an echo with its record is a delta
  after the record before it, never a full frame. A run whose
  edges left the transcript (a `/clear`, a fork, a rewind) gets a full frame; a
  `missing` reply on a held key is a gap the page answers with `needFull`. A
  reply that reaches the head carries the head cards first.
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

### The judges' own process: `romp-judge --serve`

Stage three of the process split (plans/judges-process.md) moves the judge pass into one long-lived child, `romp-judge
--serve`, that the kernel starts at boot and speaks to over a line protocol on the child's stdin and stdout (JSON, one
object per line). The child announces `{"op":"ready","pid","judgeVersion","protocolVersion"}` once; the kernel sends
`{"op":"pass","seq","mayStart"}` per producer wake, with an OPTIONAL `now`, and `{"op":"quit"}` to end; the child
answers exactly one `{"op":"done","seq","wallMs","tierStarts","tierCpuMs","workerCpuMs","failures","recovered",
"recordCache","asmCheckpoint","parses","goalIo","tierGate"}` per pass. The request's `now`: absent or null, the tiers read
their own clock during the pass, the in-process producer's behaviour and the kernel's DEFAULT (it sends no `now`), so a
measured comparison of the two roads isolates the process split from the clock semantics; a number is the explicit clock
variant, truncated to the second and handed to both tiers for the whole pass, available for a measurement that wants it
on its own (2026-09-18). Every counter on the done line is a PER-PASS figure: `wallMs`, `tierCpuMs` and `workerCpuMs` are
the pass's own, `failures` its tier crashes, and the five blocks (`recordCache` and `asmCheckpoint` from the event model,
`parses` as the parse store's misses and hits, `goalIo` as the goal-store loads, saves and writes, `tierGate` as the tiers'
gate counters per stage (`plan`, `group`, `close`, `distill`, `unblock`, `consolidate`): `ran`, `skipped`, `stamped`,
`bypassed`, `incomplete`, `due_clock`, the admittance the pass ran under) are the DIFFERENCES against the previous pass's snapshot for every counter, so the kernel can feed its `/perf`
counters per pass, while each block's GAUGES ride as their current values: in `recordCache` the keys `entries`, `bytes`
(the cache's contents now), `budgetBytes` and `countCap` (its caps); in `asmCheckpoint` the key `asmDocMemo` (the document
memo's size and cap); in `tierGate` the key `stamps` (the stage stamps held now); `parses` and `goalIo` carry counters only. `asmCheckpoint.restoreMs` is a counter like its neighbours (the restore's parts
since boot, as described above), so the line carries the pass's own restore time. A non-numeric value (a name) rides as
current too. `recovered` is the child's judge-module recovery flag (the once-per-storm
edge `consume_judge_recovery` reads), consumed by the child and acted on by the kernel, which re-arms its given-up cards on
it as the in-process pass does. `mayStart` is the
kernel's composite gate, the same predicate the in-process pass reads (the Task tracking switch, a live session, retries not
paused), evaluated on the kernel side; the child gates on it and on nothing else, and an absent field reads false: no tier,
no kernel-initiated model call, still an answer. The pass body is ONE function, `run_pass` in kernel/judge.py, that the
in-process producer and the child both call: both tiers in parallel under one evidence frame, a barrier, the tier threads'
CPU and failures accounted under a lock, the frame ended in a finally. One pass at a time: a `pass` arriving before the
previous `done` is answered `{"op":"error","reason":"busy"}` and dropped, never queued; a malformed line answers
`malformed`, an unknown op `unknownOp`, and the loop continues. Every stderr line of the child carries the prefix
`romp-judge: `; the child's file descriptor 1 is redirected onto its stderr for the whole process and the protocol is
written to the saved descriptor, so no print, direct write or child process can reach the channel. The kernel's side (the
request, the hard bound, the restart count, the switch that defaults to the in-process loop) is described with the producer
above once it lands.

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

The comments frame follows the same own-slot model. The `{type: "comments"}` frame carries a
thread's anchorUuid, which the chat page joins with the anchored reply turn (`data-uuid`) to draw
the reply's mark, and it reaches a page on three roads: the pusher's full cycle, the create
handler's direct send when a comment is written, and the ready reset's re-send once a reconnecting
page's listeners are up.

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
- The kernel files one `wsopen` row (surface `kernel`) per socket it accepts: the
  app, the dashboard id, whether the dial was a reconnect, and the `kind`, decided
  by the terms the producers state: `relay` when the dial states `relay=1`, the
  term the federation splice writes into the query it forwards to the remote
  kernel; `page` when it states `client=ext`, the VS Code extension host's connect
  URL (Node's `ws` client sends no Origin and no User-Agent, so nothing else would
  name it); `page` when it carries an Origin or a User-Agent header (a browser
  carries both, a CLI such as curl a User-Agent); `relay` otherwise, the one
  producer of that shape being a hub kernel older than the relay term relaying a
  browser's federated dial (app and wid alone), a fallback bounded by hubs
  updating. The hub side of a spliced `/remote/HOST/ws` upgrade is accepted and
  spliced, never registered as a client; once the remote has answered 101 it files
  its own row, `kind` `hub`, naming the host, and a refusal files nothing. So an
  empty file means no browser was on a page this kernel serves, not a broken sink,
  and a browser's panes are told from another kernel's relay dials; a row that
  cannot be written is said on stderr once, since the reading rule holds only while
  writes succeed. A planned per-app split of the connect push (perf work) will read
  the same `kind`.
- The kernel files one `chatFull` row (surface `kernel`) per whole session frame
  the uuid-anchored chat wire sends to a client that already holds a base for the
  session, filed once the frame has left (a frame the per-client dedup swallowed
  files nothing): such a client is owed deltas, and the page treats a full for a
  held session as a reconnect repair. The row carries the client's `cid` and `kind`, the
  session, the `reason` (the `chatFullWhy` label under `/perf`), the change index,
  the list's length, and which base edges the list still held; a first send files
  nothing.
- Every chat frame and delta carries `wm`, what its build READ: the transcript the
  build parsed (`leaf`), the parse's fileset key (`tx`, one `[mtime, size]` row per
  file the parse read, taken before the read) and the live tail's revision
  (`live`, an integer; a backend without a counter, the Codex backend, carries no
  `live` component, `null`, and its frames order on the `tx` rows alone, so no
  event text rides the watermark). Two builders read the transcript in either order (the pusher cycle
  and the targeted push at the SDK queue pop), and a build from an older parse
  under a newer live tail once reached a page after the frame that had landed a
  just-sent message, taking the landed row off the page until a reload. The
  senders refuse a build older than the one a client holds (the same leaf, every
  parse row at or behind with one behind, or the same rows and a smaller live
  revision), say so once per session on stderr, and file one `chatStale` row
  (surface `kernel`) per refusal with the client, the session and both readings.
  A client holding no base takes any build; another leaf (a fork, a rewind), a key
  of another shape or a mixed reading is never older. The page applies the same
  rule on its side: a frame or delta whose `wm` is older than the session's is
  ignored and filed as `frame-stale` (surface `chat`, the wire and both
  watermarks), and a frame that removes a landed human turn the page held files
  `frame-drops-landed` whatever its watermark said (the wire, the count, the
  uuids' tails, whether the frame carried a watermark, and the expected cause when
  a rebased fork or a rewind the page asked for removed the row on purpose).
- Every row a page files carries `build`, the dist token the page was served
  with, and `boot`, the boot id of the kernel that served it. The page's one
  diag door stamps them (the pane shim, and the shell's twin) and the kernel
  keeps them; a page older than the stamp, or the VS Code webview, reads `null`
  for both. A row is then told to come from old or new page code by reading it.
- The chat pane follows each composer send from the kernel's copy of it to its
  landed turn, on every frame it applies (a full, a delta, an update, a history
  page), and files `landed`: the send's id (`key`, the id the `send` row
  carries), the landed turn's uuid, the ms since the press, the frame type,
  whether the record wore the id or the pending-send reconcile matched it by
  text (`by`), and how many events sit below it. `landed-lost` is filed when a
  frame takes a landed human turn off the newest three and not because the list
  slid off the top, a fork replaced it, or a rewind the page asked for or a
  rebased full removed it: the frame type, its watermark, the last six events
  before and after, and whether the frame's own events still carry the turn
  (`inKernel`). `landed-missing` is filed when the kernel's copies of a send it
  had shown (its echo, its queued copy, the page's held copy) are gone, no
  landed turn for the send is resident, and an agent message lands below where
  the copy sat: the frame type, its watermark, the copy last seen, the answer's
  uuid, and whether the frame carried the record (`inFrame`). At most 30 rows a
  minute per session and kind.
- A `tailmut` row (an element leaving the end of the chat view) carries the
  units' uuids beside their classes, the full list lengths (`nRemoved`,
  `nAdded`) beside the four it clips to, `gone` (the uuids that came back
  nowhere in the same batch), `slide` (the batch added or removed the top
  spacer: a window re-render), and `reAdded`, judged by the DOM node.
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
label the account digest itself, so a bucket can be matched to the log. A
session billed to a stored login (see [Several Claude
logins](#several-claude-logins)) hands its record id as the material instead,
so that login is its own bucket, `login:<salted digest of the id>`, and the
bucket carries the login's display label in `label` (empty for every other
bucket), which the dashboard's card uses to name it.

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
- `label`: the display label of the stored login this bucket's auth label
  names (see [Several Claude logins](#several-claude-logins)), `""` for every
  other bucket. Additive like `series`.

### On the dashboard

The shell's rail carries one dot for the signal, placed after the `API` label
of the spend readout: the accent colour when every connected kernel is fine,
red when errors are being met anywhere (a 429 storm, 5xx failures, a machine
offline, auto-retry paused), and the label gray when no kernel has API traffic
in the windows. The hover reads the document as counts, never as the state
machine's vocabulary: one line per machine, named by its kernel's own name,
with its successful requests in the accent and each failure class counted in
its own colour only when present (429s in the blocked red, 5xx with 529 in the
5xx purple, no-connection and other-status failures in the other band's own
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
purple stacked on them, and a band of its own hue (a pale lime in the dark
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

Three more relays of one call to an attached host sit beside it, all behind the
local token, all forwarding the remote's own token, all bounded at ten seconds
(a peer that accepts and never answers is reported as not answering then, and
the tunnel is re-dialed). `GET /remote/<host>/sessions` reads the peer's own
session roster: 200 with `{ok, host, sessions}` (each row the public shape with
its identity colors, nothing of this kernel's added), 404 in prose for an
unknown host, the peer's own status and prose for a refusal or an older build
without the route, 502 in prose for a dead tunnel or a body that is not a list.
`POST /remote/<host>/new` and `POST /remote/<host>/send` relay a control call
that lands on that machine: a session spawned there, and its briefing sent
before this kernel's poll has learned the new id (a `POST /send` here would
route it nowhere). The body must be a JSON object and crosses as sent; the
peer validates and answers for itself, and its status and JSON verdict are
mirrored (its 400 or 409 arrives as a 400 or 409 with its words). Every answer
this side writes is JSON `{ok, error}`: 404 for a path that names no host
(`/remote/new`), for an op other than `new` and `send`, or for an unknown host;
400 for a body that is not a JSON object (the peer is never reached); 502 for
a dead tunnel or a peer that answered without a JSON verdict.

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

`seq` counts the retry-pause file's writes since the kernel started, plus
each press the kernel refused because that file could not be read (the press
is told so on its own socket; nothing is changed). A press on the detail's
pause button writes that file, so the frame that answers the press carries a
moved `seq` whatever state it brings, and the shell clears the button's
acknowledgment on it; a frame from before the press carries the old one. It
is an event counter, not a clock, and restarts at 0 with the kernel. `waiting` is `retrying` plus `blocked`. `cls` is the plurality class
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
`lastInflight`, `lastCodex`, `misses`, and the park's drain-hold counts); it
is a note, not a request, and the kernel's restart-reason walk passes it over.
`lastInflight` counts the Claude turns in flight at the park's last answered
poll and `lastCodex` the Codex turns, so a park an open Codex turn held to the
backstop reads `lastInflight` 0 and `lastCodex` 1. Three more
manager notes sit beside it: `restart-folded` (a restart request that arrived
while a restart was in flight and its successor not yet spawned rode that
restart: `trigger`, `into` the pid signaled), `restart-trailing` (a request
during the successor's boot, kept as one trailing restart: `trigger`, `after`
the successor's pid) and `restart-trailing-current` (the successor answered
and its own `restart_pending` verdict said it runs the disk's code, so the
trail was dropped). The kernel's own `main-converge-declined` row (a converge
that found its kernel leaving, `phase` before-pull or after-pull) is the same
kind. None of the four signals a kernel, and the kernel's restart-reason walk
passes them over as it does the quiet-window note.

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

## The Task tracking switch

Task tracking has one master switch, at the top of Settings, Task tracking, on by default. It is a kernel-side,
per-install setting: `~/.local/state/romp/task-tracking.json`, `{"enabled": false, "gt": <gesture stamp>}`. An absent,
unreadable or malformed file reads ON; only the literal `false` turns tracking off, and reading never creates the file.
An absent file is the quiet default. A file that is present but cannot be read or is not the store's shape reads ON
too, and says so once per episode, one kernel log line and one error-center notice (the dashboard's bell) that the task
tracking switch file could not be read and tracking is running: unlike its siblings' defaults, which withhold a
capability, this one resumes spending the user may have opted out of. A clean read, or the file's absence, ends the
episode. The next flip in the gear rewrites the file where the path can be written; a directory in the file's place
refuses the write, nothing is applied, and the gear says so (the setting's stale toast names the fault), so the directory
has to be removed by hand.
The gear's click posts `setTaskTracking` with a gesture stamp; the setter follows the ordering, echo and stale rules every
gesture-stamped setting uses, and an applied flip is echoed to the socket that made it (a `taskTracking` frame), which is
when the gear greys its dependents and tells the shell. A refused write (a full disk, a read-only state directory) is
told on the same socket instead (a `settingStale` frame naming the fault and the kept value), so the gear snaps back to
the kernel's value and the rail and the panes stay as they were. It is one value across attached machines: the click
reaches every attached kernel; a kernel attached later, or polling one, used to adopt the newest stamp, the road Auto
Nudge, Suggest /compact and file editing took. Since phase one A of plans/settings-across-machines.md (2026-09-18) a remote
machine's newer value is a PROPOSAL, never a silent apply: the polling kernel (and a kernel a hub pushes to over
`/mesh-settings`) writes a record per proposing MACHINE under `settings-proposals.json` (the value, the peer's stamp, the
local value at the time; a peer is named by the key its row carries, the alias it was attached under; a poll with the
token learns the peer's own name from its `/version` and writes it on the row, saved with it, so a push naming itself
resolves to the row's key, a record a push filed under the self-name before the first poll moves onto the row's key, and a
detached row takes its records and kept stamps with it) and applies nothing; the same value under a newer stamp lifts the local stamp only; a pinned store raises
none; the record drops when the values come to equal or the peer's stamp is no longer newer. The user answers through
`POST /setting-proposal` (`{"store", "host", "gt", "answer": "apply" | "keep" | "pin"}`, this kernel's own record from that
machine only, the stamp checked: a stamp the machine has since moved past is refused, that machine changed its mind and
the line is refreshed): Apply runs the store's own gt-gated setter under the peer's stamp (a click on this machine that
already outranks the record is refused and drops it), Keep drops that machine's record and remembers the stamp as answered
so it is not proposed again, Pin sets the machine's pin and drops every record for the store. The pin (`settings-pins.json`, set
from this dashboard's own kernel by `setSettingPin`, never broadcast) keeps this machine's value against every remote input:
a proposal is never raised for a pinned store, and a gear click that reaches this kernel from a dashboard attached to
another machine (the broadcast carries `origin`, `local` or `remote`; a message without it is read as remote) stands down
with a `settingStale` frame carrying `pinned` and the kept value (the gear's toast: kept, that machine's value is pinned),
while this machine's own dashboard's click applies as ever; the pin itself (`setSettingPin`) is taken from the local origin
alone, and a stale pin gesture is said in the log with no frame. `/version` carries `settingsPinned` (the pinned stores) to
every caller and, to a caller with the token (the gear, a polling peer), `settingsProposals` (the pending records, a list
per store, the local value live) and `host` (this machine's name), all additive; the gear draws the pending proposal under the affected row (which machine, from what to what) with Apply and Keep
mine, and a pinned store's note; and every proposal is a needs-you NOTICE CARD on the owner-less Notes run (phase one B: the
producer `settings`, the key `proposal.<store>.<machine>`, one card per proposing machine, a new revision when the stamp
moves, expired when the record drops for any reason) with Apply, Keep mine and Keep mine and pin this machine as actions of
the `setting-proposal` kind, which the kernel alone posts and which hands the stored body to `/setting-proposal`'s own checks;
answering on the card or in the gear clears both.
Phase two (2026-09-19): with more than one kernel connected the settings card carries a MACHINE SELECTOR above its tabs.
"All kernels" is the synchronized view (a click broadcasts as ever, subject to each machine's pin) and a synchronized row
whose kernels disagree wears the flag "differs" in the warning tone, the machines and their values on hover, with the count
of differing rows on the selector itself. Picking one or several kernels scopes the four synchronized rows to their values (a
remote's from its `/tunnels` row, this machine's from `/version`) and a change there applies to those kernels alone and PINS
the store there: the message carries `scope: "pinned"` beside `origin` (`hosts` names the kernels; federation stamps each
copy), the pin gate passes a scoped remote-origin gesture, and the arm pins under the gesture's stamp; a broadcast never
carries the scope. Each synchronized row wears a pin glyph at its right edge, lit while the picked kernel (this machine under
All) pins the store, its hover naming the pinned value beside the other machines'; a click toggles `setSettingPin` on the
picked kernels (`hosts`, the scope), and an un-pin returns the row to the synchronized value, the newest stamp across the
attached machines read from this kernel's own per-machine records (a pinned store keeps a peer's newer value as a HELD record:
no card, not in the gear's map; it becomes a proposal when the pin lifts), never a dial at click time. The `/tunnels` row
carries `settingsGt` and `settingsPinned` beside `settings`, and a machine that pins any store wears a "pinned" mark in the
Remote kernels popover (and in the VS Code strip's network rows), the stores on hover. A peer's pinned store raises no proposal
here (its value stands there by its user's word; the flag says the machines disagree) and this kernel keeps a store it pinned to
itself (no push). The selector hides with one kernel; a store this machine pins keeps its glyph, so the un-pin is one click away.
In a mixed mesh a kernel from before phase two ignores `scope`: with the store pinned there a scoped change is refused as a
broadcast is, and with it unpinned the value applies but pins nothing, so the next broadcast walks it back; update the kernel. A peer that reports a store pinned is not pushed our value for it. A MIXED
mesh: an older kernel without this change still adopts the value a one-A kernel pushes to it and still applies our poll's
value on its side, so the two converge one way (toward the newer kernel's proposals being answered) until it updates.

**Across attached machines** the browser merges every host's feed frame into one. A host whose frame is the off stand-in
is named in the merged frame (`offHosts`, beside the per-host build counters), a host that is attached but has not yet
sent a frame is named too (`pendingHosts`), and a frame built before the browser has read the host list at all (a page
load's very first, which the local kernel's push produces before the first `/tunnels` answer) says so (`hostsUnread`)
and counts every card as not in hand until the answer lands, when the frame is re-emitted. This touches the
single-kernel page too: its first frames are unread until the first answer, which the poll delivers within a
cycle, and an answer that is not the list (a non-ok status, a failed fetch) leaves them unread and is filed once
in the client diagnostics (a `hostconn` row, `tunnels-poll-failing`, with the reason on one line) and its end
once (`tunnels-poll-recovered`, filed after the list is read, so it says the frames are no longer unread); the
frame's own
`off` stays the local kernel's word, so the notice and the gear row, which both read this dashboard's kernel, agree.
While any host is named in either list, its cards are not in hand, which is not the same as gone, and the feed pane's
writers that act on a card's absence stand down: nothing is confirmed, pruned, retired or forgotten because a card is
not in the frame (a pending clear's confirmation, a card's disclosure state, a predicted move's gone verdict, an
optimistic tick, a bell mark); presence-driven work goes on and the reporting hosts' cards still ring the bell. The
bell's card marks name their host from the mint (a remote card's as a trailing segment, a local card's as the empty
one), so only the marks of the hosts not in hand are kept and the reporting hosts' prune by absence as ever; a host
mints nothing while off, so what is kept for it is what its cards carried at the flip, and the store stays bounded
however long it stays off. A mark stored before the segment existed names no host: it is kept while any host is not in
hand and rewritten with its host the next time its card is seen, a finite set that only shrinks. The convergence above
does not reach an isolated peer (its settings are neither adopted nor pushed), so an attached isolated host with the
switch off stays named indefinitely: the marks kept for it are bounded as said, and the disclosure state grows only by
the user's own gestures, so a long mixed state costs stale entries for cards that have left, never growth without a
gesture. Before the pending hosts counted, every reload pruned every remote card's marks and disclosure state on its
first frame and re-rang every remote warn once the frames arrived.

**Off, the kernel stands down** the two judge tiers (the producer starts no index and no triage thread: no
kernel-initiated model call, no `judge-usage.jsonl` row), the feed and outline builds (the panes receive one frame with
`off` and show a notice in place of their list; the `/feed` and `/fleet` pages render the notice, and its button opens the
settings at Task tracking, through the shell when the pane sits in one, else by sending a standalone page to the
dashboard with `#settings=tasks`), and the goal nudges, which wait, since their redundancy read is a judge call. A call in
flight when the switch flips finishes; the next pass starts nothing. The stores stay on disk; on again resumes from them.

**Off, these carry on:** the chat and the Sessions pane (its judging band is empty), the sessions' working and awaiting
dots in the chat (derived from the transcripts, outside the feed build), the compaction suggestion, the reminders
about unanswered messages from other sessions, which need no judge and follow Auto Nudge's own switch, and the error
center (the dashboard's bell): a failed machine sync, a refused state write or a session that cannot start is told while
off as before, since the notice rings ride the off frame. An opt-out of judging is not an opt-out of being told when the
machine fails. One pre-existing gap stands, tracking on or off: a browser with the Feed pane turned off in the gear's Panes
section never loads the feed frame, so no ring row reaches that browser's bell; the shell should feed the bell from the
frame it already receives rather than from the feed frame alone. The producer's
episode settle, goals snapshot and evidence frame still run as store bookkeeping, and a rewind's reconcile runs as before.

The shell hides the Outline and Feed buttons and phone tabs (`body.no-task-tracking`) and closes an open pane of theirs
in memory (the stored pane set stands); the gear greys the judge rows, the two pane toggles and the Judging-bands boxes
with the tooltip "Enable task tracking to use this (Settings, Task tracking)."

Where to read it: `/version` carries `taskTracking` at the top level and in `settings`, with its stamp under
`settingsGt` as `task-tracking`; `/perf` carries `judge.tierStarts`, the count of judge tier threads started, flat while
off. `kernel/judge.py` `MODEL_CALLERS` is the census of every judge that makes a model call, each declaring its relation
to the switch; an ast test holds it to the module's call sites, and the entry point refuses an undeclared name.

## The judges' process (stage three)

`~/.local/state/romp/judges-process` reading `on` moves the judges' passes out of the kernel into one long-lived
`romp-judge --serve` child (plans/judges-process.md): each producer wake sends one `pass` line over the child's stdin and
reads one `done` line from its stdout; the kernel's bookkeeping (the episode boundary tick, the goals snapshot, the
compact, the recovery re-arm, the generation bump) stands around the request in the loop's order. Absent, or anything
but `on`, the tiers run in the kernel as before and no child starts; a file that cannot be read or decoded reads as
off and says so once (a sync notice). Effective on the next pass; the child is ended on the pass where the switch
turns off. Each request carries `now: null` by default, so the child's tiers read their own clock as the in-process
tiers do; a second file, `judges-process-clock`, reading `request` makes the request carry the wake's time instead,
which the child hands to both tiers truncated to the second. It is the measurement knob of the split's comparison
(the child's gate admittance differed between the two clocks), read on every request.

Bounds and counters, all on `/perf` under `judge`:

- `JUDGE_CHILD_PASS_HARD_S` (900 s): a child that answers nothing by then, a partial line included, is killed and the
  pass counted `passesLost`; it comes back on the next wake (`childRestarts`). A line that is not the pass's own `done`
  and a `ready` with a protocol version the kernel does not speak are handled the same way.
- `childFallbacks`: after three passes lost in a row from fresh starts the judges run in the kernel until the switch
  file is written again, said as a sync notice.
- `orphansSwept`: a child left by a kernel that is gone (its pid record under the state root, one per kernel pid as
  `judge-child.<pid>.json`, names a parent that answers no signal) is ended at the next kernel's boot and again at its
  first request, so the goal stores keep one writer. On Linux the child also dies with its parent by construction (a
  parent-death signal, asked for between fork and exec through a pointer the kernel bound at import, so the forked child
  does no work of its own); the kernel's exit road ends it first in every case: the quit and the SIGTERM go out at once,
  even with a pass in flight (that pass is lost and counted), and the bounded waits (a tenth of the manager's SIGTERM
  grace before the kill, a twentieth after) run on their own thread beside the exit's stages, which already spend the
  grace less a margin; after its cut row the exit joins that thread with what the grace has left and kills outright
  whatever still stands,
  so the exit stays inside the grace whatever the child does. A boot sweep that cannot list the state root leaves the
  sweep unmarked and the first request retries it.
- On the child road `parses.judge` and the `goals` block read zero: the judges' parses and store writes happen in the
  child, and their per-pass figures ride its done line as `judge.child.parses` and `judge.child.goalIo`.
- `cpu_ms_sum` counts the child's tier and worker CPU as it counts the in-process tiers and pools; `cpu_ms_child_workers`
  is the workers' share alone; `child` is the last done line (its wall, tier starts, CPU, failures, record cache and
  checkpoint blocks); `tierStarts` is counted at the request, so a long pass reads it during the pass.

## The interface, feature by feature

The sections below hold the detail behind the interface the [guide](guide.md)
walks through: the chat pane's own behaviour, the panes beside it, mail, remote
access and notifications. The guide gives each of these a paragraph; this is the
rest of it.

## The chat pane in detail

The [guide](guide.md#the-chat) names these in a line each. Here is each one in full.

### Dropping a file onto the chat

Drop an image or any file anywhere on the chat pane and it attaches to
the message box of the session you are looking at; a dashed ring shows the pane is the target
while you drag, and in a split each column takes its own drops. Dropped anywhere else on the
dashboard, a file is refused (the cursor says so) rather than opened in place of the page.

### Quoting a passage into the composer

Select any passage in the file viewer and it lands in the
composer as a quote chip, labeled with the file and the line the passage lives on. Type
what should change and press **⌘⏎** to set the note aside; keep reading, select the next
passage, and repeat: each staged note remembers its quote and its place. The list above
the composer shows about four staged notes and scrolls for the rest; its caret collapses
it to the count. **⏎** sends everything you staged along with whatever is in the box as
one message, so the session applies the lot in one pass, and you never copy a line out of
the document by hand. The line in each label is checked against the file at the moment
you select, so numbers that moved under you are caught rather than quietly carried. When
several sessions work in the same repository, or in worktrees of it, the viewer's title
bar says which one you opened the file from: a chip with the session's name, in the same
color as its tab. The title bar's **GitHub ↗** button opens the file on GitHub. While the
check runs, the button waits dimmed with pulsing dots beside it.
When there is nothing to open, the button stays in place, dimmed, and a caption beside it
says why (the file is not in a git repository, or not committed: untracked, staged but in no
commit, or on a branch with no commits yet; or the repository has no origin remote, its origin
is not on GitHub, or the path is relative and no session's directory resolves it); the
button's tooltip repeats the reason. A file on a branch that is not on origin keeps its link,
drawn with a dashed border, and the caption says the branch is not on origin yet. That check
trusts your clone's own refs: a branch deleted on GitHub reads as present until `git fetch
--prune`, and one pushed from another clone reads as absent until a fetch. A branch that has
never been pushed is asked of origin once and the answer kept until a push or fetch from this
clone writes its tracking ref; where nothing local could refresh the answer (a
`--single-branch` clone, or a branch on origin this clone has not fetched) origin is asked on
each open.
A pull request number in a message, a card, or a note (`#123`, `PR #123`, or
`owner/repo#123`) links to that pull request on GitHub, in the repository the session's
directory has as its `origin` remote; when that remote is not on GitHub, the number stays
plain text.

### Naming another session with `@`

Type `@` and the first letters of a session's name in the
message box, and the sessions whose names match are listed above it, twelve at most; when
more match, the last row says how many, and more letters narrow the list. Arrow to one and
press **⏎** or **Tab**, or click it, and `@name` goes into the message as plain text, the
name the session's mail tools take. Which form goes in depends on the session you are
writing to. When you write to a session on this machine, a session on another machine goes
in as `@host:name`, the way this machine knows it. When you write to a session on another
machine, every name goes in bare, because the dashboard cannot see what that machine calls
its peers; if the bare name is ambiguous there, the session's mail tools refuse the send and
list the candidates as `host:name`, and the session picks one. **Escape** closes the list
without inserting, and it stays closed for that `@` until you delete it: more letters, or a
caret move away and back, do not reopen it. In the sent message, a name that matches a live
session is shown as a chip: the name without its `@`, in that session's color on a dark
backing, the way the Awaiting chip names the session it waits on. Hover it for how that
session is doing; the message itself still carries the `@name` you typed, and so does a
copy of it.

### A message that has not gone yet

Send to a busy session and your message waits as a
dashed bubble under an hourglass until the session takes it: while it compacts, while a
turn runs, or in the beat before the kernel confirms the send. Until then it is still
yours: the **✎** in its corner takes it out of the queue and puts it back into the message
box (the words, the quote chips it was written against and its attachments), so you can
change it and send it again, or clear the box to drop it. Once the session has taken the
message it is no longer yours to recall: the bubble's dashes close, its header reads **with
the session**, and the ✎ goes. It stays that way until the message lands in the
conversation: inside a running turn that is the session's next step, so a message sent
mid-turn can sit there while the current step finishes. Several messages sent during one
turn reach the session one at a time, in the order you sent them: the next waits, shown as
queued, until the session has taken the one before it, so two messages are never joined
into one. A queued slash command, and a
notice romp itself queued, carry a **✕** instead: there is nothing to reword, so they just
cancel. If the session took the message before you pressed, the bubble says so and the
box is left as it was, so nothing is sent twice. One case to know about: a chat page
from before an update, still open on the new kernel. The page never reloads itself. A
line near the top of the window says a newer romp build is ready, with **Reload** and
**Not now**: Reload keeps your place, your drafts, your tab, the notification center and
the comment thread you had open — reopened on the same thread, back where you had moved its
box — and Not now keeps the line away for that build (a later build asks again). A new
comment you were still writing is not kept: its text was never sent. Neither is a thread
that was resolved, broken out or deleted while the page was away, or one whose highlighted
passage the fresh page does not render. Until you
reload, the old page keeps working against the new kernel: reading, sending and switching
tabs are unaffected, and the one thing that can go differently is an action the new kernel
no longer knows in the old page's form, which falls back to the older path (a pencil on a
queued message reverts and the message stays queued). When that happens the line says the
page is behind the kernel, so you know the reload is what puts it right. A restart of the
kernel onto the same build changes nothing on screen: the panes reconnect, and no line
appears.

### Opening a markdown document

A markdown link in the chat opens in the file viewer,
rendered, with **Raw** one click away. The link can be a path on the session's machine, or a
link to a file served from the dashboard's own address (a published report, an evidence doc).
Figures
and links inside the document resolve relative to the document, so a `![fig](fig.png)`
beside it shows, and a link to a sibling document opens in the same viewer. Links to files
on other sites open in a new tab, as before, and a ctrl- or ⌘-click still opens the file in
a tab. The document is set for reading: a sans face at a slightly larger size, headings in
proportion, a centred column about 80 characters wide, and task lists, keyboard keys and
aligned table columns as GitHub shows them. Every code block is numbered by line and carries a
**Copy** button that copies the block as the file holds it, tabs included; fences labelled
`rust`, `go`, `c`, `java`, `sql` or `toml` are highlighted, in addition to the languages the
chat already knows. Printing the page while a rendered file is open prints the file alone,
black on white, across as many pages as it needs.

### A file's own HTML

The Rendered view keeps the HTML a markdown file carries, under rules
modelled on those GitHub applies to a README, so nothing in a file can move, hide or cover the
viewer's own controls. A `<style>` block is dropped whole. A form, its controls and a
`<dialog>` are dropped but their text stays as prose. A task-list checkbox stays but cannot be
ticked. An inline `style` keeps only its `color` and `background-color`, and only when the
value is a color name, a hex code, or `rgb()`, `rgba()`, `hsl()` or `hsla()`; a span colored
with any other function, such as `var()`, loses its color. A `background=` attribute is
dropped, since it would load a remote image the moment the file opens. An inline `svg`, a
`canvas` or a `video` shrinks to the column, as a picture does, and a table wider than the
column scrolls sideways on its own. An element's `id` or `name` is prefixed `user-content-`,
as on GitHub; the viewer's own heading ids are not, so a link to a heading in the file still
lands on it. An image map (`<map>`, `usemap`) is dropped. The same rules apply to the HTML in
a chat message, where a link to an element's own `id` or `<a name>` lands on it under the
prefix.

### Text size and width

The **A−** and **A+** buttons in the viewer's title bar make the
text of any text file smaller or larger in fixed steps from 70% to 200%: a markdown file's
Rendered and Raw views, the code view of every other text file, and a document opened from
a link on the dashboard's own address. They appear wherever the viewer opens (over the chat
or the feed), and not for a picture or a PDF, which have no text to size. Ctrl (or Cmd) and
the mouse wheel over the text do the same. Once the size is off 100%, the percentage appears
between the buttons; click it to go back. The choice is kept in this browser and applies to
every file you open here. Prose keeps a readable line length that grows with the text size,
and code blocks keep that width and wrap long lines. A table is as wide as its columns need,
up to the width of the viewer, and scrolls sideways on its own beyond that; a table inside a
quote or a list item stays within the prose width. Pictures shrink to fit, so the page is
never wider than the viewer.

### Opening a PDF

A PDF the session mentions, or one you click in the file browser, opens
inside the dashboard like an image: the chat's PDF card opens it full-view, a path or a
file-browser row opens it in the file viewer. Cmd-click it instead (Ctrl on Windows and
Linux), or middle-click, and it opens in a new browser tab in the browser's own viewer, the
way a paper opens from OpenReview: full size, and it stays open beside the dashboard while
you keep working. If the browser blocks that new tab, the PDF opens inside the dashboard
instead; a PDF too large to show offers a download in its place.

### Links inside a file

Wherever the viewer shows a file's text, the links in that text work. A
web address opens in a new browser tab. A file path opens that file in the viewer, in place of
the one you were reading: a relative path such as `docs/guide.md` is taken from the folder of
the file you are reading, an absolute or `~/` path as written, on the machine of the session the
file belongs to, and a line written after the path (`src/app.py:12`, or `src/app.py#L12`)
scrolls the code view to that line. A Markdown file opens in its Raw view for that one open, since
the Rendered view has no lines; your Raw/Rendered choice is unchanged. A line past the end of the
file lands on the last line, with a notice saying so. In a Markdown file, a `[link](target)`
follows the same two rules: a web target opens a tab, a file target opens the file (a host with a
port, `127.0.0.1:3000` or `api.example.com:8443`, is neither, and says so). A link to a section
of another file (`report.md#results`) opens that file at the section. A link to a section of the
same document scrolls to it when the document has a heading or an anchor by that name
(`<a name="install">` included), and otherwise says so when you hover it; it scrolls under every
click, since a section of the shown file has no tab of its own. One click does one thing: a plain
click acts in the dashboard, and a Cmd-click (Ctrl on Windows and Linux) or a middle-click opens
the link in a browser tab of its own. Inside a file the test for a path is stricter than the one
a chat message's prose gets, and a fenced code block in a chat message follows the file's test
too, linking a path only once the kernel has verified it is a file: a path links only when it has a slash and a file extension, starts on its
own, at the start of a line or after a space, a quote, a bracket, a comma, a semicolon, an
equals sign, a pipe or Markdown's `*` (so `$HOME/docs/a.md`, `@scope/pkg/index.js` and
`C:/Users/x.txt` stay text), is not part of a web address, does not start with a site name
(`www.example.org/docs/index.html`), and is not the package an `import` statement or a
`require()` call names, whether the statement fits one line or its `from` starts the next (a
relative import such as `./app.css` still links, and so does a path after the English word
"from" in prose, unless that line holds nothing but `from` and the
quoted path). After a `*` the path must be the whole emphasised text, closed by a `*` of its
own: `*docs/a.md*` and `**./scripts/setup.sh**` link; a glob's `**/docs/a.md`, an operand's
`w*h/img.size` and the first path in `**docs/a.md and docs/b.md**` stay text. Web addresses and
paths found in the text wear a dotted underline that turns solid under the pointer; a Markdown
link that names a file keeps the ordinary link look. Selecting text across a link works as
before, and a click that lands while text is selected inside a link opens nothing.

### The rings on a tab

A tab wears a dashed red ring, **Blocked**, while its session is stopped: on a permission or
picker prompt, or on an API error only you can clear. When the feed shows one of the session's
cards under Needs you (it asked you something, it is waiting on a decision, a peer's message is
waiting for your say, or a stalled task needs a look), a small **magenta dot** sits at the tab's
top-right corner, **Needs you**, carrying a count of what needs you in the session (a number, "99+"
past ninety-nine), whether the session is idle, waiting on background work or still working, so the
sessions that need you stand out in the strip without a click through each of them; a working
session keeps its gold dot at the left. The count follows the feed, one refresh behind it at most,
and goes when the card does: answer it, resolve it or clear it and the tab is plain again. A session
**retrying** an API error on its own shows a hollow **amber left dot**, a ring around the dot's slot
whose distinct shape tells it from the filled working gold and awaiting green without relying on
colour. Blocked outranks Needs you, and Needs you outranks retrying. With the badge off a tab wears the one outline ring the
first applying state gives it; with the state badge on (the default), the Needs-you count dot also shows beside Blocked's red
ring and beside the retrying amber dot, so a session that needs you stands out even while it is blocked or retrying. The three
rings are rows of **Settings**, **Chat**, **Tab widgets** (**Blocked**, **Needs you**, **Retrying**), each with its own switch,
listed in that precedence order: Blocked over Needs you over retrying. A
cue switched off leaves the tab with its dot; the small dot on a folded group's header and the
phone's picker follow the same switches. With notifications on, the card entering Needs you is
also what notifies you (see [Notifications on your phone](guide.md#notifications-on-your-phone)):
the cue is that card, shown in the strip (the count dot, or the dashed ring with the badge off), and it stays as long as the card does, including across
a kernel restart, which announces nothing. On a phone, the session picker marks the same sessions
with the magenta count dot, on each picker row and on the button that names the current session. One colour, the Needs you colour, marks the category everywhere: the column's
chip, a card's question mark, the tab's count dot (its dashed ring with the badge off) and the phone picker's dot (its left bar off).

A per-browser setting, **State badge instead of the outline ring** (a checkbox in the gear's
**Chat** tab, beside the tab lock), is **on by default** and is what the paragraph above describes:
Needs you a small magenta dot with its count, retrying a hollow amber left dot. Turn it OFF to swap
those two back to the outline shapes: Needs you the dashed magenta ring, retrying the dashed amber
ring, and on a phone the picker row's magenta left bar and the current-session chip's dashed magenta
border in place of the count dot. Blocked keeps its red ring and fill either way. The retrying left
dot needs the **Status dot** widget on, since that is the slot it moves to; with the Status dot
widget off, retrying keeps its amber ring even under the badge.

### Tags and groups in the tab strip

A tag is a named, colored set of sessions; a session can be in
several. Right-click a tab and open **Tags** to add or remove them. Tags filter every
surface (the tag button in the strip narrows the tabs to the tags you pick), and they group
the tabs: as soon as any session carries a tag, the strip shows one section per tag, in your
tag order, each with a header in the tag's color, and the untagged sessions on a row of their
own at the end. A session with several tags appears under each of them; every copy is the same
session (click either to open it, and closing either ends it). Each header shows the tag's color and name, then a chevron and a
member count. Click a header, or press Enter on it, to fold its section down to the header
alone; the count then says how many tabs are folded away, and a small dot after it shows when
one of them is busy or needs you: red when one is stopped on you (a prompt, or an API error only
you can clear), otherwise magenta when one has a card that needs you, otherwise gold when one is working, otherwise amber
when one hit an API error and is retrying on its own (hover it for their names). To keep one tab visible while its section is folded, right-click
the tab and pick **Show when folded** under **Tags**;
the header's count then leaves that tab out; when every tab in a section is set to
show, the folded header shows the full count and its tooltip says nothing is hidden. Pick it
again to fold the tab with the rest. A tab set to show when folded keeps that setting when its
group is renamed. The section of the tab you are reading folds like any other; its header marks
that it holds the tab (the tag's name is underlined), and folded, the header stands in for the tab:
focus lands on it, and the left and right arrows step from there. The `archived` section starts
folded. Drag a header to reorder the groups, which
reorders the tags on every surface (the timeline's tag table shows the same order). To move
a tab into another group, right-click it and pick **Move to <tag>** under **Tags**: one click
adds that tag and drops the tag of the group you right-clicked it in, leaving its other tags alone. The row's
**+** adds the tag without moving the tab. Dragging does it too, and the place you drop says where the
tab should appear now: drop it inside a group's row, or on the group's header, and the session takes
that group's tag and lands at the slot you dropped it in, keeping every other tag it carries — so a
session under several tags goes on showing under each of them. The group about to take it wears the
accent while you hold the tab over it, and a drop inside the group it already belongs to just reorders
it. Drop a tab on the ungrouped row and it loses **every** tag instead: that row is not a group but the
sessions carrying no tags, so nothing short of clearing them would put it there. The tags going are
named under the tab before you let go, since dragging it back into one group restores that one tag and
not the others; there is no undo beyond dragging. Dragging a **header** still reorders the groups, as
above — which of the two gestures you get depends on what you picked up, never on where you dropped it. **Group tabs by tag**, at the foot of the tag
button's menu, turns the sections off for this browser. On a phone the session picker, which stands in
for the strip, lists the sessions the same way: each under its tag's heading, in the same order (its tag
menu has the same switch). The folds follow you: your kernel keeps which groups are folded, which of the
ones that start folded you opened, and which tabs are set to show when folded, so a group folded on one
device is folded on every other while you watch, without a reload, and the last fold wins. On the phone
the picker's group heading is the fold control, with the header's chevron and count and, folded, its
dot: tap it to fold or open the group, and the list stays open for the pick. A folded group lists its
heading alone, except the tabs set to show when folded. The session you are reading follows the
desktop's rule: its group folds like any other and the heading stands in for it, while the chip at the
top of the phone still names it. Until 2026-09-23 the phone never folded, since the picker is its only
switcher; a folded group's sessions are now one tap away instead. A fold made while a page is still
connecting is kept and applied over the folds the kernel serves, so neither is lost. Until you drag,
every browser, the phone included, reads the tabs in the order romp has kept for them. The order you
have DRAGGED your tabs into follows
you: your kernel keeps it, so the phone picker and every other browser you open read the sessions in
the order you arranged them on the desktop, and a drag on one device moves them on the others while you
watch, without a reload. The last drag wins — two devices dragging at the same moment settle on whichever
landed second. The phone shows the arrangement but cannot change it: the picker has no drag. The groups,
their order, and which sessions sit in each of them are the same everywhere the tabs are grouped. The Sessions pane has the same
sections: **Group by tag** in its Filter menu (off until you turn it on, per browser) lays the
lanes out one section per tag in the same order, each session under every tag it carries and
the untagged sessions behind a divider, with the tag's chip, the caret and the count on a row
of its own; a section folded in either place is folded in both, and while grouped the lanes
follow the tag order (dragging a lane pans, it does not reorder). A session reached from a card or the chat while its section is folded unfolds that section, in the strip too, and the arrow keys walk the rows on screen. The gear at the strip's right end, the same gear as the one at the bottom right of every romp page, opens the settings on the **Chat** tab. Its **Tab strip** section carries **Lock the tabs in place**, which freezes every tab move (a drag, a Move to, the Sessions pane's lanes) until you turn it off, and **State badge instead of the outline ring** (the state badge, above); the **Tab widgets** section next to it holds the per-widget rows. The strip's tag button, at the other end of the controls from the gear, shows no chips of its own: the tags show in the strip's sections when the tabs are grouped, and the button wears the accent while a filter is on. Every group starts on its own row; turning off
the gear's **One tag group per row in the tab strip** lets the groups follow one another across the
strip and wrap as they need, with the untagged sessions behind a thin divider, so a strip with many
tags stays short. Folded groups that follow one another in the tag order share one row, since each is
only its header; an open group always starts a row of its own, and so do the untagged sessions, so a
folded group between two open ones keeps its row too. The **Status line** section, next to Tab widgets in the same Chat tab, does the same for the line above the composer: the folder and the git branch are on by default, the session's name and the host of a remote session are there to switch on, and in both sections the rows reorder by dragging a row's grip or with the arrow keys on it, each section previewing the result below its rows; in Tab widgets a line marking the session name's place divides the list, and a row dragged above or below it renders on that side of the name; the three rings around a tab are listed below those rows without a place in the order, since a ring has no side of the name.

### A tag section at a glance

Clicking a tag section's header also shows the section in the transcript's place: one
row per session, with its color, a dot for its state (yellow working, red stopped on a prompt or an
API error only you can clear, amber retrying an API error on its own, teal compacting, green waiting
on background work, none while it is idle), a state chip when the state is worth a word, what it is
doing now in a few words, and how long ago it last did anything. The chip is the one the bar under
the transcript wears for the session you are reading, with the same words and colours: **Needs you**
when the feed shows one of the session's cards under Needs you or the session is stopped on a prompt
(**API error** when it is stopped on one only you can clear), and **Awaiting** with what is awaited
(**Awaiting 3 agents**, **Awaiting watch**, the peer's name) when it is waiting on background work.
A session that asked a question and went quiet shows the chip with no dot: the dot follows the
session's own state, the chip follows the feed. What it is doing now comes from its current task, else from the headline of
its work so far, else from the last task it had; a session that has published a note of what it is
working on shows the note as a quieter second line. Hover a row for its last message, shown without
its formatting; click one to open that session, which also opens its section if the section is
folded (with several tags, the first folded group of them). The rows update as the sessions work and
change only when something about a session changes; the **Needs you** chip follows the feed, one
refresh behind it at most. The transcript comes back when you pick a session, press Escape, or click
that header again while its section is open and holds the tab you are reading.

### Coming back after a dropped connection

When the dashboard's link to the kernel
drops and comes back (a laptop lid closed and opened, a network change, a phone that
slept), the page does not fetch every session again. The kernel sends the session you
were reading in full and lists the others as skeleton tabs: the strip is complete at
once, each tab with its name, color and status, and a transcript arrives only when it
is wanted. Click a skeleton tab and the romp loader stands in until its transcript
lands; the tabs you do not click fill in one at a time while the page is idle, never
while the browser tab is hidden. Until then a skeleton tab's hover tooltip says it is
not loaded yet.

![After a reconnect, the tab you were reading is back in full while the other tabs wait as skeletons](assets/guide/reconnect-skeleton-tabs.png){ width="32%" }
![Clicking a skeleton tab puts up the loader until its transcript arrives](assets/guide/reconnect-skeleton-click.png){ width="32%" }
![The clicked tab, loaded](assets/guide/reconnect-skeleton-loaded.png){ width="32%" }

### On a small screen

To keep more of the transcript in view, turn on the gear's
**Compact tabs and agents** setting. It tightens the rows in the background-work panel above the
composer (the one headed **Awaiting** or **In the background**) and shows about four of its rows,
scrolling for the rest; the cap lifts while a row's details are open. Where the tab strip is showing,
it also shrinks the tabs and group headers; on a phone the session picker stands in for the strip, so
there the setting tightens the panel alone. Like the other chat settings, it is per browser.

### Columns, and a hot key per tab

The chat can be split into columns, so two or three sessions
sit side by side instead of behind each other's tabs. Every column is one full chat with its
own tab strip and its own composer, and each session lives in exactly one column: the first
column holds every session not shown elsewhere. Drag a tab to the right edge of the chat and a
new column opens there on that session; drag a tab onto another column and the session moves
to it. Without the mouse, **⌘** / **Ctrl** with the backslash key, or **Move this session to a
new column** in the command palette, moves the session you are on to a new column at the right;
**Move this session to the next column** and **Move this session to the previous column** in
the palette walk it across the columns you have. Your unsent draft travels with the session.
Drag the gutter between two columns to resize them. The **×** in a column's top-right corner
closes it and returns its sessions to the first column, as does **Close this column** in the
palette (the column you are in, or the last one when you are in the first); a column whose last
tab leaves, whether moved away or ended, closes on its own (a column with a session still being
created in it waits for that session to open). Clicking a card in the feed or a
notification, or picking a session from the **+** picker, the switcher, an at-mention or a link
in a transcript when it is shown in another column, lands you in the column that holds it, so
no session is ever shown twice. The arrangement, each column's sessions and widths, is
remembered per browser across reloads. Four columns at most; the phone shows one pane at a time
and never splits.

A tab can have a **hot key**: right-click it, pick **Hot key…**, press a combination, and the
combination shows on the tab after its name; pressing it switches to that session, in the column that holds it. Once one is set the row reads
**Update hot key…**: press a new combination to change it, or Backspace or its **Remove**
button to take it away. **Focus the next chat column** and
**Focus the previous chat column** in **Keyboard shortcuts** take a hot key too, and cycle the
focus between the columns. **Go to the next session** and **Go to the previous session**
cycle the tabs of the column you are in, from the composer as well, and come bound
to Ctrl+Alt+→ and Ctrl+Alt+← (Control+Option on a Mac, where the browser keeps ⌘⌥ with the
arrows for its own tabs; the VS Code view binds its own pair to Ctrl+Alt+arrows, ⌘⌥ on a Mac,
while its panel is active). From another pane they step the chat column you last worked in,
showing a hidden chat pane first. A desktop or an assistive tool that binds Ctrl+Alt+arrows
itself (GNOME's workspace switch, VoiceOver on a Mac) takes the key first; rebind them in the
same dialog, where a combination you had already saved for another command keeps it and the
new default yields. The bare ← and → keys still switch sessions while nothing is being typed,
and the dialog shows them on the pair's rows as their built-in keys, beside the chord. **Toggle notifications for this session** flips the bell of the
session you are looking at (the tab menu's **Notify me**) and flashes "Notifications enabled
for web" or "disabled"; once it has a key, the menu's row shows it.

## The feed's layout controls

The **View** button in the feed's footer holds the layout choices: the sort
direction, a single-column layout, grouping each column's cards by session, and
**Show focused session**. That last switch puts the session you are reading in
the chat at the top of the feed, above a divider (a two-pixel rule, a step
up from the hairlines), under a label reading
**Current session:** followed by the session's name. Clicking the name opens the
session; clicking the label or its caret folds the whole section to that one
line, which then shows the session's card count, and clicking again unfolds it.
Under the label the session's cards sit in the same three blocks as the board
below, which stays as it is. The blocks have their own controls: the six-dot
grip on each block drags it to another slot within the section (the arrow keys
move a focused grip's block the same way), the gutter between two blocks resizes
them against each other (width only; the section's height follows its cards),
and each block's caret folds it to its head, a choice that holds for whichever
session is focused next. The section's blocks follow the board's arrangement
until the first drag in the section; from then on the two are arranged
independently.

## The Files pane

The Files pane holds the file viewer in a column of its own, beside the chat
and the feed, so an open file covers neither. While the pane is open, a file
link clicked in the chat opens in it. When it is closed, a link opens over the
pane you clicked; there is no setting to decide otherwise, the open pane is the
rule. The Files row (Settings, General, Panes) shows or hides the pane's
toggle; it is off by default. On a phone, closing the file takes you back to the
tab you came from. The folder
shown under the chat (the session's working directory), the **Directory** row
of the **System context** card and **Browse files** on a tab's right-click menu
open a listing of that folder by the same rule: in this pane while it is open,
otherwise over the chat. Pick a file in the
listing and it opens where the listing is. Selecting a passage in the viewer
puts the quote in the chat's composer, as it does from the viewer over the
chat. When no file is open, the pane lists the files most recently opened in
it; click one to open it again. The gear's **Files** row (Settings, General,
Panes) adds a Files toggle to the bottom bar (on a phone, a Files tab like the
others), and that toggle turns the pane on.

## The Artifacts pane (experimental)

A column listing the files a session's thread put in: a grid of large
thumbnails for its images, then a row per file with its name, its folder, one
word for how it got there (`written`, `edited`, `notebook`, `shown`,
`dropped`) and how long ago. Clicking a thumbnail opens the chat's lightbox,
whose arrows cycle that session's images; clicking a row opens the file in the
Files pane when that pane is on screen, otherwise in this pane's own viewer.
The bar's picker names the session, and its padlock decides whether the pane
follows the chat's active tab or stays on the session you picked.

The pane is experimental and off by default: its row is the last one in
Settings, General, Panes, and anything but a stored `true` reads as off. Turn
it on and its column and its rail button appear, along with the palette's
**Show or hide the Artifacts pane**. It has no tab on a phone, and the Panes
rows are shown on the dashboard only, not in the editor extension's gear.

What it lists is read from the session's transcript alone, with no judge and no
model call: the paths of `Write`, `Edit`, `MultiEdit` and `NotebookEdit` calls;
paths the chat linked or rendered from what the agent wrote; and files you
dropped into the session. Commands the agent ran are deliberately not read. One
entry per path, newest first, five hundred at most. A file that has since gone
is listed struck through rather than hidden, a kind the preview cannot show is
listed plain, and a path under the Claude configuration directory is refused.
The pane only reads: it writes nothing and puts nothing into a session.

## Pane docking

Off by default, per browser, at Settings, General, Panes, **Pane docking**.
With it on, the panes stop being a fixed row and become a layout you arrange
yourself.

You move a pane by its empty space, never by a title bar: the frame around it,
the gap in its top row, Option (Alt) held anywhere over it, or the pane's own
empty background (the feed between cards, the Sessions band outside its lanes,
the Outline below its rows, the Files pane's empty state). The pointer is an
open hand over a surface you can grab and a closed hand while you hold one. A
press lifts only after a few pixels of travel, so a click, a text selection and
a scroll are never a drag, and Escape cancels. An outline in the accent color
shows where the pane will land: the left, right, top or bottom half of the pane
under the pointer. Dropping splits that pane, and every internal edge becomes a divider
you can drag.

A session tab is a payload too. Drag one into a pane's half and it becomes a
chat pane there; drop it on another chat pane's tab strip and it joins that
column. A whole pane dropped on a strip is refused, since a pane is not a tab,
and the refusal says so.

Every pane on screen takes part: the chat and each column of it, the Outline,
the Feed, the Files pane, the Sessions band and any other pane the kernel
carries, the Artifacts pane included. A pane switched off at the rail parks
with its content still mounted, so nothing reloads and no connection drops.
The arrangement is kept in this browser under a key of its own, written only
while docking is on; switching it off restores the shipped layout exactly, and
that switch is the only way back to it. Desktop only: the engine does not start
on the phone layout.

## Settings across machines

Settings sit in two stores. What a page looks like is kept in the browser you
are looking at: the theme, the transcript's density, which panes exist here,
the tab widgets, the backend a new session starts on. None of it travels.

One thing that used to sit there does travel now: the order you drag your tabs
into. Your kernel keeps it — one arrangement for the tab strip, the timeline's
lanes and the feed's groups at once — and serves it to every browser, phone and
editor panel looking at that kernel, so they all read the same. Dragging
anywhere moves it everywhere, as it happens; the last drag wins. A browser
opening for the first time, or after its storage was cleared, takes the
arrangement rather than replacing it, and a drag made while a page is
disconnected lands over whatever another device arranged meanwhile. Sessions
running on attached machines are arranged in with the rest, and the kernel keeps
the list without reading it: it can neither order nor prune sessions belonging to
a machine it has never heard of, which is why the arranging itself stays in the
browser, where every machine is visible at once.

The folded tag groups travel the same way, on the same kernel store and the same
push: which groups are folded, the ones that start folded you opened, and the
tabs set to show when folded, for the tab strip, the phone's session picker and
the Sessions pane at once. Whether the strip and the Sessions pane group by tag
at all stays with the browser. A browser that folded groups before its kernel
kept them hands its folds over the first time it connects to a kernel that has
none; a kernel that already has folds keeps them, and the browser's own are left
where they were, so going back to an older romp returns them.

What the kernel acts on is kept by the kernel, and most of those rows say
**Follows to every connected machine's kernel.** under them: one click there
is sent to every kernel you are connected to. Four rows go further and
converge, so two machines that were set differently while apart end up
agreeing: **Auto Nudge** and **Suggest /compact** (Automation, Nudges),
**Allow file editing** (General, Permissions) and **Task tracking**.

Converging never overwrites your machine quietly. A peer holding a newer pick
raises a proposal: a second line under that row reading, for example,
`web proposes off; this machine is on`, with **Apply** and **Keep mine**, and
an owner-less needs-you card on the feed's Notes run carrying **Apply**,
**Keep mine** and **Keep mine and pin this machine**. Acting either way
settles it, and the same proposal is not raised again.

The machine picker sits above the settings tabs and appears once a remote
kernel is up. It reads **All kernels** by default, with a count of the rows
the kernels disagree on beside it (`1 differs`, `2 differ`), and its rows are
this machine (named by its kernel, or **this machine** when it has no name)
and each connected one. Pick one or more machines and the four converging rows
apply there alone, which pins the value there; the pin glyph at a row's right
edge lights, and clicking it returns that row to the shared value.

A few kernel settings stay on the machine that holds them, because they
describe it: **Conserve memory**, **Thinking summaries**, **Whole chat
frames**, **Extra models from your API gateway** (the gateway is that
machine's), and the **Default directory** for new sessions, a path that means
nothing on another machine. **Updates install automatically** is sent to every
kernel when you click it but is not one of the converging four: each install
keeps its own boot policy.

Two marks say the machines differ. A quiet **mixed** on an ordinary kernel row
means picking here sets every machine the same way. A louder **differs** on
one of the four means the kernels disagree: under **All kernels** a click sets
them all, and picking a machine above sets that one alone.

The settings open from the gear at the bottom right, or the palette's **Open
settings**, in seven tabs: General, Chat, Feed, Sessions, Automation, Task
tracking and Debug. The tab you used last is remembered in this browser.

## Renaming, restarting and ending a session from the Outline

Right-click a session's name in the Outline pane, or press the Menu key (or
Shift and F10) on the focused row, for three items: **Rename**, **Restart
session** and **Delete**.

**Rename** turns the name into an input with the current name selected. Enter
commits, Escape cancels, and clicking away commits as Enter does. The name is
a label: mail, goals and history follow the session, not the word. Nothing
changes locally until the kernel confirms, so a name it refuses leaves the old
one standing; on a session from another machine the `host:` prefix stays fixed
beside the input and you edit the bare name.

**Restart session** is the tab menu's own row, described under [Restarting a
session in place](#restarting-a-session-in-place): the agent's process is
replaced by a fresh one on the same conversation, so the row stays where it is
and the session keeps everything but its program.

**Delete** raises the same confirmation the tab strip uses. It names the
session's open top-level goals and says that the session shuts down while its
history stays on disk, to revive any time from the picker or the timeline.
**End session** ends it, **Cancel** does nothing, and the row leaves on the
kernel's next push rather than ahead of it.

The pane itself is the rail's **Outline** button, off at the rail until you
press it. Whether it exists in this browser at all is General, Panes,
**Outline**, on by default, and with Task tracking off it is gone along with
the feed.

## Token usage: the two panels

**Sessions against judges.** Settings, Debug, Diagnostics, **Token usage
analytics** opens the **Token usage** panel: two bars, **Sessions** and
**Judges**, over the last `1h`, `6h`, `24h` (the default), `7d` or `30d`. The
judges' bar stacks **By judge** or by **Index vs triage**, the measure is
**Tokens** or **Cost ($)**, and a line under the legend reads the judges as a
percentage of the session tokens, with the combined figure. Judge cost is what
was logged; session cost is tokens priced from a table.

**What the API is costing.** The bottom bar's spend readout opens **API
spend**, which the palette reaches as **Token usage** too: **Totals**, **Spend
over time** and **By session**, over one day by hour, seven days by hour (the
default) or ninety days by day, measured in dollars or tokens, ordered by
spend or in your own order, and optionally merged by tag. The panel's choices
are kept in this browser.

## When a session's tab disappears

The chat pane never jumps to another session on its own. If the tab you are
on disappears (a kernel restart that hides a remote host's sessions until the
host reconnects, a relay down, a session that ended), the pane goes blank: no
tab is selected, the body names the session that vanished (and says it is
reconnecting when that is known), and the message box is disabled with no
session name in it. When that same session's tab returns, the pane goes back
to it. A kernel restart does not reload the page: the panes reconnect and the
board stays where it was. After a reload (one you take from the newer-build line,
or your browser's own) the page remembers the tab you were
on: until that session is listed again the pane stays blank and names it as not
listed yet, and it never settles on another session meanwhile; if it never
returns, the blank body stays until you pick a tab (a remembered tab that can
never return, a subagent's viewer or a session still being created, says so at
once). A tab view that stops showing your session keeps it on the strip as the
peek. From the blank pane an arrow key or Next Tab lands on the first visible
tab. Focus moves to a different session only when
you pick a tab, or when you close the active tab yourself (then the pane returns
to the tab you used before it).

## A postal card's head and its delivery mark

The head names both ends, the other session and this one, each in its session's
color, and carries the delivery mark at its right edge: sent, delivered, read,
parked while the recipient is unreachable, bounced, or recalled. A send that
failed has no mark at all, and the tool call's result says what happened. An
incoming message that waited while the session was offline wears the parked
mark. Hovering a mark gives the state and when it was reached.

## A comment thread's mail

A comment thread's mail is off, both directions, until you break it out: peers
cannot see or mail the thread, and its own mail is refused with a line saying
so. The comment box itself says nothing about it (the tab hover's Mail row and
the Sessions pane show the state), except a count when messages are actually
held for the thread; they land within seconds of a break-out. The moment you
break the thread out it is a session like any other, mail on unless you toggle
its mailbox off. Only peer mail is gated: what you type into the thread's
box yourself, and plain text the kernel's own send route carries, is yours and
still goes through; that is the human channel, by design, not a hole in the
gate.

## The tags a new session inherits

A session started from another one joins its tags. Forking a session, breaking
a comment thread out into its own session, and running `romp new` inside a
session's shell all put the new session in the parent's groups, so a session's
children land beside it in the tab strip. `romp new --no-inherit` starts one
outside them; `romp new --in <tag>` names the tags directly (repeatable). The
**+** picker shows the tags of the tab you are looking at pre-selected in its
**Tags** row, where you can unpick or add before creating. Opening a name that
already runs inherits nothing: `romp new --in` still applies to it, while from
the picker, a name that already runs is focused and the Tags row is not applied
(a message says so; the row is a prefill, and applying it would move the
running session). Comment threads have no tab and inherit nothing until they
are broken out; `romp new` run inside a thread inherits from the session the
thread belongs to.

## romp's mail tools, and Claude Code's own

Not the same tools: romp peers are discovered only through the postal service's `list_agents`. Claude Code also ships its own `ListAgents` and `SendMessage` tools, which list the account's Anthropic cloud sessions and this session's own subagents: a different system, and a cloud session in that list is easy to mistake for a romp peer. The recommended setting is `"permissions": { "deny": ["ListAgents"] }` in the Claude Code settings, so the only list of agents a session sees is romp's; `SendMessage` must stay allowed, because continuing a subagent uses it.

## The VS Code port forwarder and the browser dashboard

The dashboard's panes are long-lived sockets, and how a forwarder treats them
decides whether a closed pane is really closed. OpenSSH forwards each
browser socket one-to-one and propagates closes, so a pane that goes away is
gone on both ends.

!!! warning "The VS Code port forwarder is not a good path for the browser dashboard"

    VS Code's Remote and Tunnels port forwarder multiplexes every forwarded
    socket over one channel and does not close the far end when the browser
    side goes away. The dashboard's panes are long-lived WebSockets that stream
    view updates, and each pane reconnects when it hears nothing for thirty
    seconds, so through that forwarder every reconnect left a dead connection
    behind on the kernel's side, all of them still receiving full view payloads
    over the one shared channel, and the live panes starved. One such incident
    counted 84 connections from three real panes.

    The kernel now protects itself: it pings every pane on each heartbeat and
    drops one whose ping goes unanswered, a reconnecting pane retires its own
    previous socket at once, the timeline and feed cross the wire as
    deltas instead of whole payloads, and every frame over a kilobyte that
    compression makes smaller is compressed for a client that offers WebSocket
    compression (`permessage-deflate`, which every browser and Node's `ws` do;
    a whole feed frame that measured 2.5 MB plain crosses as about 0.4 MB).
    That keeps a forwarded dashboard usable,
    but the forwarder still carries every byte over a channel it shares with
    your editor, so prefer a path that gives each pane its own socket: plain ssh
    port forwarding, which the guide sets up under
    [From another machine](guide.md#from-another-machine), or
    [Tailscale](#reaching-romp-from-a-phone-the-full-tailscale-setup). The VS
    Code romp view is a different case: its sockets run on the kernel's own
    machine and close when a panel closes, so it never leaks connections, but
    under Remote or Tunnels the extension still relays each whole view payload
    to the local window as it changes. It does not yet take the deltas the
    browser panes do.
    A pane that falls 16 MB behind is dropped and reconnects on its own; the
    drop is logged in the kernel log and shows in the Log (the settings panel's "Open log" button carries the unread count on the desktop; the phone's bottom bar reddens its bell), so a
    link that cannot keep up reads as what it is rather than as a flaky network.

The panes' socket compression is on by default. `ROMP_WS_DEFLATE=0` (exactly
`0`) declines every pane's offer, so every pane runs plain: the lever if a proxy
on the way mishandles compressed frames. `ROMP_WS_DEFLATE_LEVEL` sets the level,
1 to 9 (default 3); a value outside that range is clamped, and one that is not a
number falls back to 3. The kernel reads both when it starts; they go in
`service.env` like the other service knobs (see
[Service environment and credentials](#service-environment-and-credentials)).

## Reaching romp from a phone: the full Tailscale setup

The user interface is a web page, so your phone can run it against a kernel on
another machine. The obstacle is reaching that machine: the kernel listens only
on `127.0.0.1`, which your phone is not on.

[Tailscale](https://tailscale.com) closes that gap, and is free for personal
use. It puts your own devices on a private encrypted network, so your phone can
reach your laptop directly whatever network either one is on. Install it on both
devices and sign in to the same account on each.

In the Tailscale admin console, enable **HTTPS Certificates**, and leave
**MagicDNS** on (it is on by default): the `ts.net` certificate names come from
MagicDNS, so turning it off makes certificate provisioning fail in confusing
ways.

Three settings in the Tailscale app on the kernel's machine decide whether your
phone can reach it at all:

- **Allow incoming connections** must be on. Without it the machine joins the
  network but serves nothing to it, which reads as Romp being broken rather than
  as a Tailscale setting.
- **Use Tailscale DNS settings** must be on. This is MagicDNS on the client, and
  it is what makes the `ts.net` name resolve.
- **Launch Tailscale at login** is worth turning on. The proxy below survives a
  reboot, but it can only serve while Tailscale is running, so without this the
  machine drops off the network until you next open the app.

Then, on that same machine, one command opens Romp to your other devices:

```bash
tailscale serve --bg 29855
```

The bare-port form needs Tailscale 1.56 or newer; on older clients write
`tailscale serve https / http://127.0.0.1:29855`. On macOS the `tailscale`
command is not on your `PATH` until you enable **CLI integration** in the app's
settings.

Two commands go with it, for later rather than now. `tailscale serve status`
prints where the proxy currently points, which is the first thing to check when
a device cannot reach Romp. `tailscale serve reset` undoes the setup and returns
the machine to local-only, so run it when you want remote access off, not as
part of turning it on.

!!! warning "If you change the kernel's port"

    `tailscale serve` remembers the port you gave it, not whatever Romp is
    running on now. Change `ROMP_KERNEL_PORT` and the proxy goes on pointing at
    the old one, so the phone gets a dead page while everything looks healthy on
    the machine itself. Re-run `tailscale serve --bg <new port>`: it replaces
    the existing mapping rather than adding to it.

On the phone, open `https://<machine>.<tailnet>.ts.net/`. Romp answers with a
page asking for your access token; paste in the one `romp` prints. A
year-long cookie remembers the phone afterwards. Prefer this to putting
`?token=<token>` in the address, which works but leaves the token in your
browser history and in anything you share the link through. The cookie is itself
a credential, so only do this on a phone you control.

Only devices signed in to your Tailscale account can reach Romp: Tailscale
checks each device's identity and encrypts the traffic between them, and nothing
is exposed to your local network or to the internet. The proxy survives restarts
of both Tailscale and the kernel.

Two settings are worth changing while you are in the admin console. Turn on
**device approval**, so a new device has to be approved before it can join, and
leave key expiry enabled on the phone. Do not use `tailscale funnel`, the
public-internet variant: it would leave the token as the only thing between the
internet and your agents, with no device check in front of it.

!!! warning "If other people are on your tailnet"

    `tailscale serve` exposes Romp to **every** device on the tailnet, not just
    yours. On a family or team tailnet, the access token becomes the only thing
    standing between other members and your agents. Either keep the tailnet to
    your own devices, or write an ACL restricting the kernel machine to them.

## Notifications on a phone or browser

Romp can buzz your phone when a session needs you or finishes a task, so you can
put the phone down while the sessions work. Every notification is titled with
the session's name: **Romp needs you: web** when that session is waiting on you,
and **Romp: web** for anything else (a task finished, a turn ended); the line
under it says what happened. On an iPhone, first add Romp to the
Home Screen (share sheet, then **Add to Home Screen**) and open it from there:
iOS only lets an installed app receive notifications, so in a plain Safari tab
the option stays off and says so. On Android and on a desktop browser the page
itself can receive them.

Then tap the bell. On a phone it sits in the bar along the bottom; on a desktop
it is in the bottom-right cluster. A small card opens with a main switch, two
switches indented under it, and a button:

- **Notifications** is the main switch. Off silences every device and the
  desktop of every machine you have attached; the bells on individual sessions
  and cards are mutes under it. While it is off, the two switches beneath it are
  dimmed but still work, so you can set a phone up first and switch everything
  on when you are ready.
- **This device**, under it, turns them on for the phone or browser you are
  holding. The first time, the browser asks for permission. If you refuse, the
  row goes grey and tells you where to allow it again (on an iPhone, Settings,
  then Notifications, then Romp; in a desktop browser, the site permission
  beside the address). Turning this off silences only this device. With the
  main switch off, the row says the device is set up but nothing arrives until
  the main switch is on. What the kernel needs for this, the Python
  `cryptography` package, the installer sets up; if the row says the package is
  missing, run `bin/romp-sdk-setup` on the machine running Romp and turn it on
  again.
- **Also when a turn finishes**, also under it, adds a notification every time
  any session finishes a turn you started, with the session's name and the
  first line of what it said. Turns a session starts on its own, such as
  reacting to one of its background agents finishing, or to a reminder, stay
  quiet: nothing there was waiting on you. With many sessions running this is
  still a lot of buzzing, so it is off unless you want it. A turn that ends by
  asking you something buzzes once, not twice.
- **Send a test notification** sends one notification to the device you are
  holding, whatever the switches say, and prints the push service's answer under
  the button, so you can see at once whether the phone is set up or why it is
  not. The test is addressed to the session you were looking at when you
  pressed the button, so you can switch to another session or another browser
  tab, tap the notification, and check that it brings you back. With the main
  switch off, the answer adds that real notifications will not arrive until it
  is on.

A restart of the kernel (an update deploys one) announces nothing by itself.
What Romp has told you about is written down beside its other state, so the
cards already waiting on you or already finished when it comes back stay
quiet. A card that stops needing you and then needs you again is announced
once, not at every turn, unless you acted on the card in between (answered
it, resolved it, crossed it off) or it finished in the meantime.

Tapping a notification brings Romp forward on the session it was about. On an
iPhone with the app already open in the background, the switch happens as the
app comes forward; a notification you swipe away instead is read the same way,
so the next time you open the app it may land on that session. With the app in
front, nothing moves until you next come back to it.

The bell itself shows the state of the device you are looking at: lit when the
main switch is on and this device is set up, and crossed out otherwise. Its
tooltip says which of the two is off.

## The token file: minting, permissions and refusals

The kernel and the bus mint the token file when it is missing, one mint between
them under a sibling lock file, `serve-token.lock`. An existing token is never
replaced: a file left looser than `0600` is tightened at the next start (its
value is kept, so every client stays valid), and a token that exists but cannot
be read, or a symlink at that path, refuses to start instead of minting a
replacement nobody else holds. Under the service that refusal repeats in
`manager.log` every 10 seconds until you repair the file; the kernel then comes
back on its own.

A few routes answer without the token: the liveness probes `/healthz`,
`/version` and `/busy` on the kernel and `/ping` on the bus, and the install
files `/manifest.webmanifest` and the three home-screen icons under `/media/`
(`romp-touch-180.png`, `romp-app-192.png`, `romp-app-512.png`, a fixed list of
names rather than a path prefix). They are fixed files that read no session
state, and a browser fetches the manifest and its icons with credentials
omitted, so a gate there would refuse them at the moment an install consults
them. `/busy`'s count is exempt as a probe; its drain hold is a write and takes
the token like any other.

Two routes answer outside that shape. `POST /push/ack`, which the push worker
uses to report that a notification was shown or tapped, is served ahead of the
token check and authenticated by the per-push id instead: 128 random bits the
kernel minted for one notification and handed only to the device that
notification went to. A report on a valid id stamps that row's shown or tapped
time, where the first stamp stands so a repeated report changes nothing, and
records the worker's build string, which every report rewrites. A shown report
also marks the older unsettled, untapped pushes for the same session on that
device superseded, since the show replaced their notifications. And the
request's origin, read from its `Origin` header, else its `Referer`, else the
forwarded headers or `Host`, is recorded on that device's subscription when
none is on file; a recorded origin stands, and a different one is logged as a
conflict. An unknown id is a 404, and the body is capped at 2 KB before a byte
of it is read.

A token-less `GET /` is not refused either: the gate runs and fails, and the
answer is the page that asks for the token rather than a 403, so a bare open of
the dashboard has somewhere to paste it.

## Switches

Effective immediately, no restart.

`touch` to **disable**, `rm` to re-enable:

- `~/.claude/romp-postal-off`: the postal service

