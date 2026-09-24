# The read side: the kernel, the UI, and the three panes

!!! note "Optional reading"
    You don't need any of this to use Romp.
    Describes the system as of **2026-07-24**; the behaviour it documents moves,
    so treat anything here as a snapshot rather than a contract.

Architecture deep dive. Layer 3 turns the records written by the event model
(`event-model.md`) and the summarizer layer (`docs/judges.md`) into the three
web-UI panes you look at: the **feed**, the **chat**, and the **timeline**.

## The governing principle

**Layer 3 derives no meaning. All meaning is computed below it (almost all in
Layer 2); the read side only selects and displays.** The judges write durable
meaning once; the panes are thin projections of it.

A direct consequence: the completion **rollup + "settled" gate** lives in
Layer 2. The producer publishes each goal's rolled-up status (working / blocked /
completed); the feed just paints columns. (Reflected in `docs/judges.md`.)

## The kernel and its clients

- **The kernel is the core, supervised by `romp-manager`.** One process: Layer 1
  (parse) + Layer 2 (the judges) **and** an HTTP server, single writer. Its
  *lifecycle* is owned by **`romp-manager`** — a durable, jupyter-lab-style
  supervisor, started by the login service that `install.sh` sets up, that spawns
  and respawns the kernel (via `romp-serve` → `romp-kernel`) and stays up across
  kernel restarts. Front ends
  (browser, phone, VS Code) ATTACH to the kernel; they never spawn it.
  `romp-serve` points at the Python kernel, so `romp up` supervises it on the
  manager's port (29855), and the front ends and tailscale serve attach unchanged.
- **The UI is served by the kernel.** The front-end (the three panes) is `ui/`. A
  browser hits the kernel's port and gets it.
- **The login service starts the supervisor**; `romp up` starts that service,
  or, on a machine without one, runs `romp-manager` in the foreground (like
  `jupyter lab`; `romp up --foreground` forces that for watching it work).
  `romp refresh` restarts the kernel(s), `romp down` stops them through the
  service and keeps them stopped until `romp up`, `romp status` reports them.
  The kernel binds loopback only; tailnet/phone reach is
  `tailscale serve` proxying to `127.0.0.1:29855` (there is no `0.0.0.0` opt-in
  door; the tailscale proxy carries the phone path). The UI itself is just a URL
  the kernel serves.
- **Session discovery keys on the rompUuid birth stamp, not a time window.** The
  kernel discovers only sessions carrying the rompUuid birth stamp the launcher
  writes — the same rompUuid registry `event-model.md` defers, which also powers
  session→files stitching. A 48h window is allowed only as a perf bound on how far
  back to parse, never as the eligibility test (a time window is a fragile proxy
  and a banned time-heuristic). The kernel does not read or migrate the legacy
  record stores (`summaries/`, `requests/`, `decision-log`, `corrections/`,
  `digest/`), so old data cannot pollute the new model. Pre-rebuild sessions
  simply never appear: no resume-picker entry, no scrollback over old history.
- **The VS Code extension is a thin client.** It speaks the kernel's WebSocket
  protocol (`vscode-extension/src/extension.ts`: "a THIN CLIENT of the romp web
  kernel", `ws://HOST:kernelPort()/ws?app=...`). Browser and extension render the
  **same served UI** over the **same protocol**, so the two front ends stay
  consistent by construction. Keeping the WS protocol stable is the compatibility
  contract.
- **Liveness is a keepalive, and staleness is event-keyed.** The kernel sends a
  `ka` frame to every socket every 10 s; the pane shim abandons a socket that has
  gone 30 s without any frame and redials at once, the silence measured from the
  later of the last frame and a Chromium `resume` (the thaw of a frozen tab) on a
  still-open socket. A socket kept on the strength of that stamp is provisional:
  if no frame confirms it within 15 s (1.5 keepalive periods) the watchdog puts
  it down there instead, a socket already 30 s overdue when the tab froze is not
  stamped and is redialed at the return, and a browser without `resume` behaves
  as before. After a reconnect the shim
  raises the "what you see may be stale" prompt only on the SECOND `ka` arriving
  before the resync frame — one full heartbeat period, bracketed by two kernel
  heartbeats on that socket with no resync between them (a single `ka` can be a
  beat that was already queued when the socket was accepted) — on the reconnected
  socket closing again before its resync, or on the shim abandoning it as quiet
  before its resync (a kernel that accepts the reconnect and never speaks on it
  sends nothing to count and nothing to close); the first non-keepalive frame
  retires the prompt, never a timer. A client that falls 16 MB behind is dropped
  by the kernel, loudly: one `ws: dropping` line in the kernel log, written where
  the drop is decided so every send path is covered, naming the pane, the
  dashboard, the backlog and the push slot whose frame tipped the budget when a
  push did; and a row in the dashboard's Log (at most five drop rows, none older
  than an hour, so they never crowd out a backend problem). Every close the
  browser reports for a socket that opened leaves a `wsclose` breadcrumb (code,
  reason, socket age, and the shim's ready state at the close: `bundleReady`,
  `readyAcked`, `readyQueued`) in `client-diag.jsonl` (rotated to `.1` at 8 MB).
  The kernel stamps every row a page posts through its socket (the `clientDiag`
  rows) with `reconnect`, whether the socket that carried the row declared the
  redial term (`?reconnect=1`), so the queued `wsclose` row, which rides the
  redial, names the redial's kind: `reconnect` true, a declared redial of a
  page the kernel had served whole; false with `bundleReady` false, the socket
  died before the bundle said ready (or the ready landed after the close) and
  the redial dialed as a fresh page; false with `readyQueued` true, the ready
  reached the shim while the socket was going down and rode the redial as the
  bundle's own; false with `bundleReady` true, `readyAcked` false and
  `readyQueued` false, the ready left on the socket and no caps frame answered
  it, so the redial dialed fresh and re-posted the ready. A socket
  the shim abandons leaves none — the watchdog's own `watchdog-close` row went
  down the quiet socket before the abandon (the foreground path's abandon sends
  none, but its `return` row queues for the redial), so an armed socket's raise,
  `reconnect-quiet` or `foreground-quiet`, queued for the redial, is the record
  that survives; the redials an outage refuses are counted and reported as one
  `wsconnfail` row on the next open, and at most 20 breadcrumbs wait in the
  shim's queue for it.
  Every return to the tab leaves its own rows: `return` with the decision
  (`keep`, `redial-closed` or `redial-stale`) and the hidden, frozen and quiet
  gaps, `return-fresh` with the wait for the first fresh frame, and `page-load`
  for a reload or a tab the browser discarded; a `resent: true` copy of the
  `return` row means the kept socket proved dead and the row was re-filed onto
  the redial.
  A redial declares itself (`reconnect=1` on the `/ws` URL) once the kernel's
  caps frame has answered the bundle's ready; before that, with the ready still
  queued, or after a socket that died before the caps frame came back, it dials
  as a fresh page. A page whose ready was never answered dials fresh for its
  life (the bundle posts ready once), so each of its redials is served whole.
  On a declared redial the kernel sends the active tab in full and lists every
  other session as a `skeleton` on the tab strip with one small `status` frame
  each, and the chat pane loads a skeleton on click or one at a time in idle,
  never while the tab is hidden; one `skeleton` client-diag row (count, active)
  records the regime.
- **The Outline pane's ages run on the kernel's clock.** Its timestamps are the
  kernel's, so the pane never reads the browser's clock against them: it anchors
  on the frame's `now` paired with the moment that frame arrived from the wire
  (`feed-age.ts`; federation stamps the merged frame it re-emits with `nowAt`, so a
  re-emit on a view-order write or a remote host's frame never moves an age), and
  every "(Xm ago)", the current goal's elapsed time and the recency cutoff move on
  a 15 s refresh — skipped while the pane is hidden, with one catch-up render when
  it is shown again — rather than only when a frame lands.
- **Both judge tiers run continuously for any live session — no connection gate.**
  The kernel runs the index tier (captioner + archiver) AND the triage tier
  (planner → closer → courier → grouper → consolidator → distiller) in parallel,
  on a short event-driven backstop, whether or not a browser is attached — so the
  goal tree, feed, and timeline are already current the instant a client connects.
  A pass is cheap when nothing changed (cached parses; each judge makes an LLM call
  only on real new work), so always-on costs filesystem stats, not model calls,
  when idle. The tiers are a cost/value GROUPING (see `docs/judges.md`), not a
  runtime gate. Single process means single writer for free, no matter how many
  tabs are open.
- **Views are URL-hash tag selections.** `localhost:PORT/#work,personal` is one
  view; `#work` is another. Each browser tab is an independent view over the same
  kernel, ephemeral, zero-config, many at once. A saved default can live behind the
  settings gear.
- **Port is a per-machine config.** One fixed port the kernel binds at startup.
- **Liveness collapses to three states** (+ the user's `cleared`): **working**
  (open, nothing for you to do — including delegated work and waiting on a
  non-user trigger), **blocked** (needs *you*), **completed**. They map 1:1 onto
  the three feed columns.
- **A block addressed to a peer is a peer wait, in working** (2026-09-11): the
  judges read a block's addressee from the session's own open question to a live
  peer, else from the peer that delegated the work it sits under (the planted
  origin, or the delegate mail the goal's anchor names; a goal whose anchor
  names no dispatch falls back to the newest delegate received before its mint
  only while that dispatch's own goal was still open at the mint, an id-less
  mail never; never a goal you typed, nor one split out of it, while a goal
  split out of a delegated one reads its own record), and file the
  awaiting-a-peer stamp instead of the block, so the card shows the "Awaiting
  <peer>" chip in working and never a needs-you; when the worker never mailed
  that peer, the kernel relays the block's why to it as the worker's own question,
  once per block (each marker has an identity its queue entry and its record
  name), so the reply can end the wait; a relay handed to a far host stays
  pending by its id up to the far host's delivered row or the peer's answer; a
  refusal the bus cannot retry reverts the block to yours; your own follow-up on a
  delegated card, newer than the delegation, keeps its block yours. A block in a delegated goal that
  names you still goes to the delegating manager; a worker's card reaches you only
  when the debt ladder escalates, and for a manager debtor only at an idle turn end
  with no unread mail waiting for it. Your own sessions' blocks are untouched,
  whatever questions they have out.
- **`blocked` has a deterministic floor the judge cannot override.** A live
  permission / decision prompt is a fact, not a judgment. `blocked = hard OR soft`,
  hard wins: the planner's output can never clear a hard block. This is a merge
  rule, not a prompt instruction — the judge is told nothing special; its verdict
  simply never removes a hard block. Hard and soft rarely collide in time (the
  planner runs on *ended* segments; a live prompt sits on an *open* turn).
- **The read side has two inputs: durable judge records + a thin real-time
  live-state read.** Live state (the chip, the timeline stripes, the hard-block
  floor, "is a session mid-turn right now") comes from `states/<sid>.jsonl` + the
  event tree's open turn. It is deterministic and mechanical, not meaning-logic, so
  it does not violate the principle; it is the one thing that cannot be precomputed
  into a record because it is about *right now*.
- **Comms scope is directory-based, group-wide, alive-gated** (a design sketch —
  see below). A separate axis from view tags.
- **Tags are directory-derived, overridable.** A `directory → tag` map auto-tags a
  session at launch; a per-session manual override handles "get this out of work."
- **Hard data isolation is a separate `ROMP_STATE_DIR` root, manual, rare.** Default is
  one shared root (so views can overlap). Point a kernel at another root only when
  you genuinely need segregated data (a dedicated machine). It is free, because the
  root is already a parameter.

## The runtime picture

```
THE KERNEL  (one always-on process, single writer)
  Layer 1   parse transcripts → event tree
  Layer 2   index tier  (captioner + archiver)                      ALWAYS
            triage tier (planner/closer/courier/grouper/distiller)  ALWAYS (no connection gate)
  HTTP/WS   serve the UI + push pane payloads
  writes →  ~/.local/state/romp/   (the interface)

ROMP POSTAL SERVICE  (always-on infra, below Layer 2)  delivery never waits on a judge

CLIENTS  (0..N, pure readers, render only)
  browser tab(s)      ── each a view (URL-hash tags)
  VS Code extension   ── same WS protocol
```

The records dir is the only interface. The kernel is the only writer. Clients only
read and render. This is the producer/consumer split the whole system rests on,
collapsed into a single process for the common (one-machine) case while preserving
single-writer.

### How mail reaches a session

The bus stores mail per recipient (a Maildir) and the kernel owns the wake
(`POST /deliver`): it hands the banner to the session's backend, which enqueues
it on the session's input queue (a Claude Code session's SDK input queue).
First-class, nothing to scrape, and never a touch on the composer, so a
half-typed draft survives. The Stop-hook drain (`hooks/romp-postal-drain.sh`)
is the turn-boundary backstop for mail a wake could not land, and non-delivery
is caught by the maildir claim/retry and stuck-mail warnings either way.

## The two inputs

1. **Durable judge records** (`docs/judges.md` writes these):
   - **captions** — per segment and per turn, keyed by id. The activity log.
   - **the goal tree** — nodes + edges + per-node and rolled-up status. The inbox.
   - **courier records** — handoff (propagating / FYI) + which sender goal, keyed by
     message/segment id. The cross-session edges.
   - **archive** — per session, keyed by rompUuid: a sub-sentence **headline** + a
     2-3 sentence **abstract**, summarized from the session's captions (cheap
     input), continuously refreshed as the session gains turns. The index + the
     TOC header.
2. **A thin real-time live-state read**: `states/<sid>.jsonl` (working / permission /
   idle / closed transitions) + the event tree's open turn + the backend's own
   compacting bracket (set when romp delivers a `/compact` or the CLI's stream says a
   compaction started, automatic or manual; cleared by the stream's compaction result,
   the `compact_boundary` or the turn's result). Drives the chip, the timeline stripes,
   the hard-block floor, and the mid-turn pulse.

## The three panes (each a thin projection)

Chat is a zoom into Layer 1; the feed is a zoom into Layer 2; the timeline is the
bridge that shows Layer 1 spatially with Layer 2 labels.

### Chat = the event tree, rendered directly

Per-session tabs. Renders the event tree at the Atom / ContentBlock level (one
widget per block), with **no second transcript parser** — the event model already
produced the tree. Plus the live chip from the state read, and the TOC ledger below
the tabs.

**A send stays visible from the press until its record lands, and each layer
retires on an event, never a timer.** The kernel keeps an input echo (a synthetic
user atom in the backend's live store, mirrored to the registry so a restart cannot
lose it) from `send()` until the transcript carries the same text. For a message fed
into a running turn that record is the `queued_command` attachment the CLI writes
when it splices the message in at its next tool boundary. On the SDK route no floor
retires an echo: it retires when its text lands in a record stamped at or after the
send (a user record or that attachment), or it is flagged `dropped` (which the chat
shows as never delivered, with restore and dismiss) on one of two events: the CLI dies
holding it, or the transcript OVERTAKES it — a later genuine-human turn lands, in a
later second, while the send's text has landed nowhere and no queue still owes it
(the backend's own, or the CLI's queue ledger). The composer's messages travel one
channel in order, so a later one going through means the CLI skipped this one: lost,
not waiting (`settle_echoes`; before 2026-09-11 a
CLI that wedged, swallowed a send and carried on left a solid bubble nothing could
clear). At boot the same evidence turns the re-delivery of an unlanded human send
into the flag: a message the conversation has moved past is not re-sent behind the
newer ones. The chat's own
pending bubble, painted at the press, has no lifetime either: it ends on the same
events, read from the events after the send (a landing of the text, the kernel's
never-delivered verdict, or the user's ✕), and a record the CLI wrote from several
back-to-back sends retires one bubble per text block (`blocks` on the user event).
While the socket is down the bubble is labelled "not confirmed", until a kernel
copy of the send clears the label. A send that landed mid-turn (`absorbed` on the
user chat event) is placed where the model READ it: at its landing time, the
moment the CLI took it off its queue, below the steps that ran while it waited
(T252d, the user 2026-09-08). Their reasoning: the pane used to draw the message
at its send position, above those steps, while the model read it only after them,
so the order on screen contradicted the order the model saw; the read position is
the one that matches. So the chat's pending bubble sits at the TAIL while pending,
below every streaming step, and the landed atom appears in that same tail
position, so nothing moves on landing. Every other window of the session sees the
kernel's echo of that send in the same place: the live merge orders an in-flight
echo after everything the turn holds (a never-delivered one keeps its time), and
the pane dresses it as the sender's bubble is dressed, so one session in two split
columns agrees on what is pending (2026-09-11). The send time rides along as `sentAt` for
the bubble's hover ("sent at HH:MM", shown once landed when it differs from the
landing by more than a minute). No header, no cue. This supersedes the
in-place-at-send-position rule of T252/T252b; the kernel's per-copy identities
(T252c) stay and decide landing, cover and hiding. The identity exists from the
press: the client mints the copy's id (`qid`, in the kernel's echo form) and posts
it with the send, the kernel parks the copy under it (the parked op's fourth slot)
or queues it under it, and the ✕ names it, so the kernel cancels exactly the copy
the bubble stands for, never a same-text neighbour by index or body. That holds
wherever the copy carries the id: a parked send, and the SDK route's queue. A
copy whose ✕ names no id (an op the kernel parked itself, such as a nudge or a
re-delivery; a ✕ from an older client) is still cancelled by index and body.
Every copy the kernel queues itself (mail, a nudge, a re-delivery) is still
minted an id where it enters the backend's queue. The CLI extracts no image
paths on the stream-json route (its only image-path test belongs to the
interactive composer's paste handler), so an image path in an SDK send lands as
typed and the echo's text matches. The chat's own image previews
(`_user_images`) use a separate set, built from the served MIME table
(`_IMG_MIME`, svg and bmp included), so a preview is never proposed for a file the
image route cannot serve.

**A kernel restart does not re-run a mid-turn send.** The boot duplicate guard
(`_text_landed`) reads the `queued_command` attachment too, and it scans from the
transcript's byte size at the moment of the send (recorded on the echo as
`_echo_off` with the file id `_echo_fsid`, mirrored in the registry as `off` and
`fsid`) to the end of the file, so a landed send is neither re-queued nor flagged as
undelivered however much the session wrote afterwards. A mark from another file (a
/clear or a fork since), a mark past the end of the file, or an echo with no mark
reads the whole file. The found verdict is recorded on the echo (`_landed`), and
`prune_live` and the chat merge retire the echo on it without a text match, so a
found echo always has an exit and a later boot never re-scans it. Every by-text
comparison of an echo against a record (the guard's scan, `prune_live`'s retire, the
kernel's `_atom_user_texts` and its folds, and the fed-copy pairing
`qids_for_landing`) uses the two keys in `session_backend.py`:
`echo_text_key` (outer whitespace stripped, nothing else) and, for a slash send,
`command_text_key` (the tokens joined by single spaces). The second exists because the
CLI records a slash or skill command as its `<command-name>` wrapper, which parses to
`/name args` with one space whatever the sender typed between the name and the
arguments; both sides key a slash-shaped text both ways, so the scan and the prune
agree on the same records.

**The ledger is a table of contents** (pure projection of captions + archive):
- top: the archiver's one-sentence headline for the session,
- then **turn captions** as top-level bullets, the whole session (not just recent),
- a multi-segment turn expands to its **segment captions** indented beneath,
- click any line to jump to that point in the transcript.

The captioner emits both grains and the event model gives the turn→segment nesting,
so the TOC is free. (Caveat: a live permission prompt's *content* reaches the
kernel through the session's backend, not the transcript; a live
AskUserQuestion/ExitPlanMode is in the tree as an unanswered tool_use. The chip
state comes from `states/` regardless.)

### Feed = top-level-goal cards, nothing else

**The only cards are top-level goals.** One card per top-level goal, bucketed into
the three columns by the rolled-up status the producer already wrote (working /
blocked / completed). A sub-goal never gets its own card: a block anywhere in the
tree rolls UP, so the *top-level card* moves to Needs you and its modal shows which
leaf is blocking; likewise a completed step shows inside the modal, not as its own
Completed card. No read-time DAG rebuild, no status derivation, no handoff repair.

Work a session started on its own is never a card of its own (the user
2026-09-10). At mint time the planner nests it under the goal it ran in (see the
judges' origin rule); for stores written before that rule, `build_feed` heals
read-side: a top rooted in a machine record (the judge's latched `askAnchor`
verdict: a peer's line, the agent's own record, romp bookkeeping; never a top that
merely lacks an anchor, and never a scheduled prompt's top, which the latch marks
`scheduled` as the user's configured work), or anchored on the harness's own skill-load
record (the bare-named `<skill-format>` command wrapper with no arguments slot that the CLI
writes when it loads a skill for the model, never typed; the event model emits no atom for it since 2026-09-11,
and the judge's latch stamps the tops older stores minted from it `machine` off the record,
re-stamping an older `human` latch once and resolving the top with romp's done verdict
naming the skill, so nothing nudges or stalls it; a once-per-boot store-side pass reads the
wrapper records raw across the project directory for stores the chain or the discover
window no longer reaches, append-incrementally and under a byte budget, and an anchor once
checked (an atom of a parse, or absent from a complete directory index) is never re-read. Such a top never hosts, a block romp filed itself (a failed
nudge, an interrupt) does not except it, and with no host in the store it is hidden from
the feed rather than shown as a root: the session's own view keeps the work, and only a
live floor or the agent's own question to the user (a closer's or planner's block under
it, which the stamp never resolves away) keeps its card) is rendered inside the session's human-asked top that was current
when it was minted; word overlap with the transcript's recorded background
launches only picks which launch supplies the why and, among several open tops,
the parent. Hosts are the asks that trace to the user: human-anchored tops and
courier-planted delegated goals, never a handoff tracker; a completed host still
holds its rows and a cleared host hides them with it. A blocked top keeps its
card until the block lifts (needs-you breaks through) and still wears the face
that says what it is; the top a live prompt or error floor stands on keeps its
card too. The launch match reads the dispatch's own description, kept on the
task record from launch time, never the completion's summary or the brief. Deterministic on the same store and stream
(nothing moves between builds without a new record), never written back, and
said once per rise on stderr. A tree row born
of the session carries `born` with its why; a session-started root that still
shows (its parent gone, or no top to nest under) carries `sessionStarted` and
its face says in one line what it is and, when known, the request it served.
The awaiting panel's `local_workflow` / `local_agent` rows are the run's own
place in the parent card.

- A card's modal shows the goal's trail (its filed segments + sub-goal tree,
  interleaved).
- **No caption stream.** Turn/segment captions are NOT feed cards — they live in the
  card's trail, the ledger, and the timeline. The rule lives in `build_feed`.
- **Card detail**: a card shows its caption trail; a richer expand view is parked.
- **Clear-all + undo**: a button retires every currently-open top-level card at
  once (batch `cleared`); an **undo** restores that batch if invoked right after.
  For sweeping away a stale backlog you know you don't care about.

### Outline = the goal trees, with each goal's PR

The Outline pane rides the feed payload's `ledgers` slice — one per-session
`build_session` ledger, the same tree the ledger box draws. Alongside the tree, each
session carries its **PR slice**, so a goal row can show the PR that goal shipped and
that PR's live state:

| key | on | what |
|---|---|---|
| `prNums` | each ledger node | the PRs this goal opened, filtered to the session's own repo |
| `branch` | each session | the checkout's current branch (`""` when detached) |
| `prNum` | each session | that branch's PR — the header chip, "what is this shipping right now" |
| `prs` | each session | `{number: PR}` for the PRs this session references, each with a `live` flag |
| `prError` | each session | why the last `gh` read failed; **rendered** beside the last snapshot, never swallowed |

How the two sides split the work, and why:

- **The judge mines, the kernel filters.** `judge.goal_pr_refs` scans a goal's own
  recorded segments for full PR urls. It stamps at the **end of each turn** the closer
  judges (every pass, not only at distill), so a draft PR opened mid-turn shows once that
  turn ends, while its goal is still open. A goal none of whose segments resolve in the
  pass's parse keeps its refs: after a `/clear` the parse stops at the new root, and
  "not in this parse" is not "has no PR". It records refs
  **unfiltered**: that side has the atoms but not the checkout. `kernel._node_pr_nums`
  keeps only the session's own repo, since it is the side that knows the remote — and
  treats an **empty owner** as "this repo", which is how a bare number read out of a
  `gh pr …` command is stored (the command acted on the checkout it ran in).
- **A goal owns the PRs it ACTED ON, not the ones it mentioned.** A segment contributes
  refs only when it also holds the receipt: `gh pr create` / `edit` / `merge` / `ready` /
  `close` / `reopen` / `comment` / `review`, or a `git push`. Read-only subcommands
  (`view`, `list`, `checks`) are deliberately absent — looking at a PR is not acting on
  it — and the pattern is anchored to command position so `grep -rn 'git push' docs/`
  does not read as a push, while `cd x && git push` does. One matcher
  (`gitpr.PR_ACT_CMD_RE`) serves this gate and the kernel's push counter. Without this gate, live sessions attributed a stranger's PR
  to a goal that had merely re-authenticated a cloud CLI, and two PRs to one that had
  only read a design note.
- **That trade is deliberate, and the header chip is its counterweight** (the user
  2026-08-18). Strict receipts cost recall: a session whose PR was opened in an earlier
  episode, or by an outside agent, shows no chip on its goals. Rather than loosen
  attribution, the SESSION header carries its current branch's PR — so "what am I
  shipping here" is always answerable even when no individual goal can claim it.
- **Bare `#NNNN` is never mined.** It is ambiguous by construction — an internal
  ticket or audit id wears exactly that shape — and a silently-wrong PR link is worse
  than no link, the same call the path linkifier makes for shortened file mentions.
- **`live` comes from the ahead count**, an event (a commit, a completed push), never
  from an open-turn bit, which toggles at every turn boundary and would flap the chip
  with no new information. It marks only the branch's own PR. When a reused branch name
  carries several PRs, an open one wins (the largest number), else the largest of any
  state.
- **`kernel/gitpr.py` owns every git and `gh` call** for this surface, so the
  view-builder stays a pure assembler. The local half reads the checkout's pointer
  files: an unchanged checkout costs stats and no fork, and a moved HEAD, branch ref or
  upstream ref costs one `rev-list`. The repo name is memoized on the config file's
  mtime, so a remote added later is seen. One `gh` list call per **repo**, cached and
  invalidated by event: a moved ref, a rise in the transcript's push / `gh pr` count
  (a push moves *remote* state while HEAD and branch stay put), a PR number some
  session cites for the first time, or the error chip's click. A refresh assembles its
  result privately and publishes it whole; an invalidation that lands while it runs
  leaves the result stale, so it is re-read rather than lost. The branch's own PR gets
  its checks fetched first, ahead of the cited ones. The single interval in the module
  is a 30s poll that runs **only** while some check is non-terminal or the last read
  failed; CI completing and the network recovering are external and have no local event.
- **A failed `gh` read keeps the last snapshot and shows the reason beside it.** The
  pane draws the known chips plus a `⚠ PR status` chip whose title names the error;
  clicking it posts `prRetry`, which re-reads that session's repo now. A first read that
  fails has no snapshot, so only the reason shows.
- **`ROMP_PR_STATUS=off`** in the kernel's environment skips every git and `gh` read
  for this surface.

### Timeline = segments as bars, with connectors and overlays

- **Lanes**: one per session; each **segment** is a bar `[t, end]` (segments are
  exactly "what the timeline draws as a bar" in the event model), a dot at the
  trigger, idle atoms as the not-working gaps, caption on hover.
- **Stripes**: needs-input (a live permission/picker prompt) / compacting, from the state read.
- **Connectors**: postal messages between lanes, from courier records / the message
  log.
- **Overlays**: focus / hover from the feed and chat (UI ephemera, one WS channel).
- Reads **segments straight from the event model**.
- The timeline lives **in `ui/`** next to chat and feed, sharing one view-builder,
  one bundle, one set of types.

## One view-builder

A single read library (TS, in `ui/`) of pure functions `records → ChatView |
FeedView | TimelineView`. One implementation, because there is one front end.

## What stays in the read side

The read side does no meaning-work: no DAG rebuild, no status derivation, no
handoff classification or repair. Those live in Layer 1/2 — the planner un-blocks
via newest-wins, origin is `trigger.author`, the courier classifies handoffs at
write time, captions are keyed by id upstream. The completion rollup, column
derivation, and the settled gate live in Layer 2, leaving the feed to read status.
Recency fade is the one display-only heuristic the read side keeps.

## Comms scope (directory groups, alive-gated)

**Design sketch — not shipped.** What ships today is live-name addressing with
per-host trust tiers (see `SECURITY.md`); the directory-group gate below has not
been built.

At the postal/infra level, below Layer 2, keyed on the working directory. Separate
from view tags.

- Sessions in the **same directory talk freely** (one project). This fits the
  shared-worktree reality: sibling sessions in one checkout are one group.
- **Cross-directory is blocked by default.** The first attempt surfaces an approval
  to the user; approving opens a **group-wide** edge (every session in dir A ↔ every
  session in dir B), not just the two that triggered it.
- The edge is **alive-gated**: it lives while both directories have ≥1 live session
  and tears down when either empties, so it re-asks next time. Event-based, no
  timer. ("Allow personal and work to talk today; tomorrow they're separate again.")
- A **config allowlist** (directory-pairs or tag-pairs) permanently bypasses the
  gate for pairs you always want open.
- **Agent norm**: sessions should not attempt cross-directory messages unless the
  user directs it; an unsanctioned attempt surfaces the approval prompt rather than
  delivering silently or failing silently.

## Tags and views

A tag is a named, colored set of sessions. The kernel stores tags in
`timeline-views.json` (name, color, members, and `tagOrder`, the order the user
has dragged the tags into); a session may carry several, and a tag may span
attached kernels, joined by name. Tags are edited from a tab's context menu, the
timeline's tag table, the picker's **Tags** row, and `romp tag`. They do three
jobs:

- **Filtering.** Each surface (chat tabs, timeline lanes, outline) keeps its own
  lens: every session, the untagged ones, or any set of tags.
- **Grouping.** The chat tab strip sections by tag whenever a session carries
  one: a header per tag in `tagOrder`, each tab under every tag it carries (a
  session under two tags appears under both), the untagged on a line of their
  own. Sections fold per browser; dragging a header reorders `tagOrder` for every
  surface; **Move to <tag>** in a tab's menu adds the target tag and drops the
  tag of the group that copy sits in, in one click. Groups are tags: there is no
  second store and no per-session group field.
- **Inheritance.** A session spawned from another joins the parent's tags at the
  creation event: a fork, a promoted comment thread, and `romp new` run inside a
  session (it sends its `ROMP_SID` as `parent`; `--no-inherit` withholds it and
  `--in <tag>` adds tags; a thread's `ROMP_SID` resolves to the session the
  thread belongs to). Opening a running session inherits nothing: `/new`
  re-asserts an explicit `--in` on it, while the picker's createSession op
  warns and leaves the running session's tags alone, because its Tags row is a
  prefill from the active tab rather than an ask. A comment thread inherits
  nothing until it is promoted, since it has no tab. Only the local kernel's
  tags are inherited; a parent held only by a remote kernel's tag is a known gap.
  Every writer of the views blob, the WS `setTimelineViews` full-blob write
  included, runs under `_views_lock`.

The exact project directory still defines the **comms group** sketched above.
The directory-to-tag auto-tagging rule and the URL-hash view selection once
planned here did not ship; the per-surface lens took the hash's place.

Every dashboard write of the views blob is acknowledged on the socket that posted
it. A tag edit from the timeline's tag table, its lane gear menu, or a tab's Tags
flyout is one `tagEdit` WS op, `{writeId, edit: {op, tid, …}}`, where `op` is
create, rename, recolor, addMember, removeMember, delete or move. It is applied
through the same read-modify-write merge as `romp tag`, so the stale-writer guard
never refuses it. Every op but create names the tag by its stored id, never by
name; a create takes an optional name, and the kernel mints the id and, given no
name, the lowest free "tag N". A move (off one tag, onto another) is one write:
both halves land or neither does. The kernel answers `tagEditAck` with `ok`, the
post-write client blob, the tag's `tid` and `name`, or a plain refusal. A lens or
order change still posts the whole blob (`setTimelineViews`) together with
`edited`, the tag ids the write changed (none for a lens or order write).
`edited` bounds what the write may change. An empty list changes no tag: the
store's tags stand whatever tags the blob carries, and only the lens, order and
active fields land, with nothing judged and nothing logged (a lens write built
from a copy taken in the same second as a targeted edit used to revert that
edit, since the guard's stamps have one-second resolution). A list of ids may
change those tags only; a differing copy of any other tag, or its absence, is
kept from the store and listed in the ack. The guard may also keep the store's
copy of a stale edited tag; the kernel answers `viewsAck` listing each kept tag
with a reason, and `ok` is false only when a kept tag is one the client edited.
The kernel's own dashboard notice follows the same rule: a kept tag the client
edited is a lost edit and raises a dashboard notice; a kept tag it did not edit
is one stderr line and nothing on the dashboard. A write without `edited` (an
older client) is judged whole by the stamps, as before.
The lens, order and active fields are not judged under any value of `edited`:
the write's copies land whole, with no comparison of its `seq` or `at` to the
store's, so across dashboards the last writer wins for them. They are display
preferences a user sets by gesture, not tag data, and a surface's lens should
read as the last gesture made on it. The cost is stated here rather than
hidden: a dashboard whose adopted base predates another dashboard's lens
change carries the older lenses for the other surfaces along with its own
change and writes them back, so the other dashboard's filter reverts on its
next frame. Tightening this would mean naming the surfaces a write changed,
the way `edited` names tags. The one check made is on `active`: it is
validated against the tags that stand after the write, so a stale copy whose
active names a tag deleted since stores "all", not a dangling id.
`edited` also settles a case the guard could not judge before: a tag absent
from the store. Named in `edited`, it is a create (the no-capability path's
client-minted `g…` id) and lands; not named, it is a stale copy re-creating a
tag another dashboard deleted, and is kept out with a reason. A write without
`edited` keeps every unknown tag as new. The door also refuses a tag renamed to,
or created under, a name another tag in the resulting set holds, with a reason
naming the collision: a renamed tag stands as the store has it and keeps its claim
on that name, so a tag created under it in the same write is refused too; a new one
is kept out. Names address edits, so the store holds one tag per name, and a
name is clamped and stripped at both doors, so a padded spelling is the same
name. The store caps at 32 tags, and the cap never drops a kept store tag: when
the tags that stand after a write would exceed it, the write's own creates are
refused instead, last in array order first, each with a reason naming the cap
(`_edit_tag` refuses a 33rd create the same way). The door reads a posted blob
to 64 tags, so a 33rd reaches that pass, and a posted entry with a valid id
past that bound is always reported. The first 64 such entries get a row each:
one the store lacks is not created ("a write is read to 64 tags and it was past
that bound, so it was not created"), one the store holds keeps the store's copy
("a write is read to 64 tags and its copy was past that bound, so the store's
copy was kept"). Past those 64, the rest are counted in one summary row with no
`tid` or `name`, `{reason, more, moreEdited}`, whose reason reads "N more
entries past the 64-tag read bound were not read", with " (M of them this write
edited)" appended when `edited` names any of them; the reason stands on its own
because it can lead the ack's one-line `error`. The bound exists because the rows,
the loud notice and the ack's `error` all grew with the posted array: a
100k-entry post drew an ack of about 22 MB, which overran the socket's queue
and dropped the poster's own socket. Every unread store tag is kept, wherever
its copy sat past the read bound, and nothing past the read bound is stored. A
duplicate entry consumes a slot. `ok` is false when a refused tag is one
`edited` names or the summary row counts one (`moreEdited`), and on any refusal
when the write carries no `edited`. The ack's one-line `error` leads with the
rows for the tags `edited` names (or the summary row when it counts one), then
the rest, joined with "; " (a nameless row is its reason alone) up to 1000
characters, then "and N more"; without `edited` the rows appear in the judge's
order. The rows carry every reason in full and are never reordered; a client
composes the same shape from them, in their order, when the line is absent.
Until the 2026-09-05 review the line followed the judge's order,
which files rows in the posted array's order, quiet (kept copies of tags the
poster did not edit) and loud interleaved, with the cap pass last, so with
enough quiet rows ahead of it the bound cut the one row that made `ok` false
and the line named only tags the poster never edited. The loud notice gives the
count of refused tags and lists each with its cause. A remote tag's rendered name is on the
same stored basis (clamped, then stripped; "tag" when empty) as the lens and
order entries, so a padded name a remote kernel serves raw reads as the name a
lens stores: a lens can select it, and it joins the union of a local tag of that
name. A lens or order write (`edited: []`) touches no tag whatever the blob carries and
files none of these rows; it is built from the store's blob the client last
adopted plus the fields it sets. It never carries a targeted edit still in
flight, which is that edit's own claim; the copy the client shows keeps such
edits, and a refusal of one reverts it alone. Both acks carry the write's `writeId` and the blob's `seq`.

`seq` is the store's write sequence. `_set_timeline_views` stamps every accepted
write with a number greater than the last (seeded from the clock, so a store
recreated from nothing starts past what any connected client holds), and every
frame that embeds the blob (the timeline skeleton, the feed frame, the tabOrder
frames) carries it. A client adopts a blob only when its `seq` is at least the
one it holds, or when either side carries no seq, so a frame the pusher built
from its warmed cache before a write cannot replace the ack's newer blob,
whatever order the socket delivered them in. The gate keeps the last blob it
turned away and lets it go on its next adoption; the caps frame is the one
event that adopts a kept blob, and the one that announces a seq at which the
gate adopts a later blob even below the held one. The optimistic copy a gesture shows clears
on the ack; a refusal reverts
its own write at once and shows the reason (a notice in the dialog and the lane
gear menu, a toast in the chat pane), and a later write still in flight keeps
its change: the copy is re-derived from the store's blob plus the remaining
writes. Each surface allows one create in flight at a time: the dialog's
[+ New tag] reads "creating…" and the new-tag inputs are disabled until the
ack. The row the create draws meanwhile takes no gesture (no rename, delete,
recolor, drag, join, or move) until the ack names the tag's id. The join menu's
new-tag draft belongs to the open [+], survives repaints however the listed rows
change, and is dropped when the menu or the dialog closes. No count
of frames settles a write. The one frame-driven clear left is the exact-echo
match, kept together with the old three-frame yield for blobs without a `seq`:
a kernel from before the stamp acks nothing.

Reader-side rules keep the sequence consistent. A store from before the stamp
is stamped once on its first read, so the gate protects the first write after
an upgrade (a store that does not exist is left alone: its first write starts
past whatever a client holds). The same first read gives every tag the file
holds an mtime (the file's `at`, else the time of the stamp), and the
hidden-to-archived migration does the same for the tags it carries over, so
every stored tag carries the mark that tells it from a client's own create.
That stamp is the file's last write time, not each tag's own (a legacy store
recorded no per-tag time, so there is nothing truer to use), and it has a
one-time cost: a whole-blob writer whose copy predates the store's last
pre-upgrade write is refused on any legacy tag it edits, even one that write
never touched, because the guard compares the tag's mtime to the writer's
evidence. The refusal is loud, the client adopts the ack's blob, and its next
write carries the new `at`, so it happens once per stale copy; a copy taken
at that last write lands (the comparison is strict). Targeted edits read the
store first and are never affected. For the first-read stamp and the migration
the file is normalized under the disk cap of 32 before the judge sees it, as
every read normalizes what it serves, so a read never runs the door's
create-refusal pass and never files a refusal worded as a dashboard's. What the
cap leaves out is named once per file state, in a sync notice worded as a fact
about the file, whether or not the stamp's write lands. The count holds when
readers race: a reader that waited for the file lock re-checks the read cache
under it and serves the blob the first reader judged and cached, so when two
readers that start with the cache empty race on a full disk, the notice is
filed once. Until the 2026-09-05 review each judged the file, failed
the write and filed it. The notice is a
template that puts the cause and the count first and the dropped tags after.
It opens "the views file held N tags, over the store's 32-tag cap; no dashboard
wrote this." When the stamp was written it continues "K tags were dropped when
it was re-stamped on read (<why the file was stamped>): " and lists the dropped
tags, each with its member count. When the stamp's write failed (an unwritable
or full state dir) it continues "Its re-stamp could not be written — K tags are
not served and are lost at the next write unless the file is brought under the
cap first: " and the same list, because the served blob is what the next write
persists. The verbs agree with the count: one tag reads "1 tag was dropped" and
"1 tag is not served and is lost". Stderr carries the notice and every dropped
tag's members. The judge
carries a `writer` label apart from `foreign`, because `foreign` is also the
judging mode for unknown tags and would refuse every freshly stamped tag as a
re-creation. The loud notice is a template of the same shape: the writer, the
count and the remedy first, the refused tags after. It reads "<writer> was
partially refused: its changes to N tags were not applied and the store's state
was kept; <remedy>. Refused: " and then each refused tag with its cause. The
writer is "a stale dashboard write", "a stale write to the views file from
outside the kernel" or "the views file's re-stamp on read". The remedy is
offered only when there is something to reload: "reload that dashboard to
resync" for a dashboard's, "reload the panel that wrote it to resync" for a
foreign file's, and none for the re-stamp's, whose head ends at "kept." One tag
reads "its changes to 1 tag were not applied". Each label carries its cause:
"(stale copy)", "(deletion)", "(re-creation)", "(unread)", "(name collision)"
or "(over the cap)". The quiet stderr line for kept tags the client did not
edit carries the same kind of label on every tag, "(differing copy)" among
them; until the 2026-09-05 review a kept differing copy was the one
entry on it without a cause. Both notices bound their lists to fit the 240 characters
the dashboard's Log shows, under the 300 the kernel serves (`SYNC_NOTICE_FIT`,
`_notice_list`): as many entries as fit, then "and M more" for the rest, and
when not even the first fits, the head alone, which carries the count. Until
the 2026-09-05 review both notices put the cause clause and the
remedy last, after one entry per tag, and with two or more tags the Log cut
them away. A file written
outside the kernel (the timeline's Electron branch writes `timeline-views.json`
itself, with the seq it holds) can carry a seq behind the last one served. By its own seq the writer
held an older copy, so the reader judges the file against the last served blob
through the stale-writer guard, then re-stamps it past that seq: a tag deleted
since is not brought back (an unknown tag carrying an mtime existed in a store
once; one without is the writer's own create and lands), a member added since
is kept, and each refusal is reported through the sync notice. With nothing
served, the file is ordered as written: the highest seq the kernel has served
or written outlives the read cache's entry (a store read as missing forgets the
entry), so a file restored from an older copy is still re-stamped past it, and
every write orders past it. A kernel write also refreshes the read
cache with the blob it wrote, so the ack's blob is never a stale cache hit. The
hidden-to-archived migration works on a copy of the file, so the tag it fills
is stamped and a pre-migration copy cannot strip it. It finds an existing
archived tag on the stored name basis (a padded spelling is the same tag), so
the hidden entries land in it rather than in a second archived tag the
collision pass would refuse. When the re-stamp write
itself fails (an unwritable or full state dir), the read serves the judged
blob when the file was judged (the judgment is a step apart from the write,
so the refused foreign copy is neither served nor cached, and the next write
that lands persists the judged state) and the file as read otherwise, logs
once per distinct error, and stops retrying until the file changes or a
write succeeds.

The kernel announces what it can do in a `{type: "caps", caps, viewsSeq}` frame
in reply to every `ready` (a pane's bundle posts one per page life; the shell
page's own socket posts one at every open) and lists the caps on `/version`;
`tagEdit` covers the targeted op, the acks and the `seq`. A client uses the
targeted op only when the cap is present and posts the whole blob otherwise. A
message no handler takes is answered `{type: "unknownOp", op, writeId}`, which
the client treats as a refusal of that write and as withdrawing the cap. The
`ready` handler sends the caps frame after its own connect push, and the pane
shim latches on it as the kernel's word that the ready was processed and the
page served whole: a redial declares itself only once the frame has arrived.
It is not a reconnect signal for a pane: a pane's shim re-sends no `ready` on a
reconnect, so a reconnected pane socket gets a caps frame only when the
bundle's ready queued across the drop and flushed onto it; the shell's socket,
which posts `ready` at every open, learns the caps again each time. `viewsSeq`
is the write seq of the views blob that push put on the socket: the tabOrder
frame's, the timeline skeleton's or the feed frame's, the highest when the push
carried more than one. When the push carried no views frame (a chat page that
reconnects on a sentinel cycle gets no tabOrder), it is the store's current
seq at the time of the caps frame, the seq the next push serves. It is null
only when the store has no seq: no store at all, or a legacy store whose first
stamp could not be written. The kernel reads a
served seq from the frames the handler's own thread enqueued, so a write
landing between the push and the caps frame does not skew it, and a
pusher-thread frame is never what it names. A client applies one rule to the
frame. It adopts the blob its gate kept when that blob's seq equals `viewsSeq`
and discards the kept blob otherwise. When nothing kept matches, a numeric
`viewsSeq` is remembered as the seq the kernel announced for its current store:
one slot per store, overwritten by each caps frame and cleared by the client's
next adoption that changes the held blob. An adoption counts as changing it
when the blob arrives at a seq other than the held one, without a seq, or at
the announced seq itself; a re-arrival of the blob the client already holds,
at the same seq, is otherwise not new information and leaves the slot
standing. A later blob whose seq equals the announced seq is
adopted even below the held one, because the kernel said at connect which store
it holds and a blob carrying that seq is that store, not a stale frame. Null
and a missing field announce nothing; a frame without the field still adopts
the kept blob outright (a kernel from before the field). The kernel's seq floor
lives for its process, so a store restored from an older copy while the kernel
was down is served under its old seq after the restart. A page that stayed open
holds the higher seq. When its connect push carried the blob, the gate turns
the push away and keeps it, the caps frame names that blob, and the restored
store is adopted on the caps frame itself, with nothing to wait for. When the
push carried no blob, nothing is kept to match, and the announced seq covers
it: the pusher's next frame carries the restored store under that seq and is
adopted, and a frame at any other lower seq is still turned away. A pusher
frame built before a concurrent write carries a seq older than the connect
push's, so whether it lands before the caps frame (kept, then discarded) or
after it (turned away), it is never adopted: the gate is never open. Clearing
the slot when the held blob changes rules out a wrong adoption later: a write
that lands first stamps the store past the announced seq (a write's seq is
seeded from the clock), and a frame at the announced seq is then stale. Writes still
in flight when the caps frame arrives cannot be answered, so the client drops
them after that adoption, reverts the copy to the adopted base, and says so. A
remote kernel's caps frame describes that kernel and is dropped by the
federation router before it reaches a pane. The router's own two replayed
stores, the merged tabOrder's blob and the merged lanes payload's, keep, adopt
and remember by the same rule. A store that adopted on the caps frame re-emits
before it hands the frame on: a pane sees the local blob only through those
re-emits, so the restored blob must meet the pane's own gate, and be turned
away there, before the pane's caps door adopts it. A pane fills the same
announced slot from the same frame. The router re-emits on a remote host's
push or lanes payload, a `closed` frame, a view-order storage event and a host
drop; every merged re-emit between that frame and the pusher's next one hands
the pane the router's stored blob at the pane's own held seq, and that
re-arrival leaves the pane's slot standing, so when the router adopts the
pusher's frame at the announced seq and re-emits it, the pane adopts it by the
same rule. Until the
2026-09-05 review any adoption cleared the slot, so a re-emit in that window
cleared the pane's; the pane turned the restored store away while the router
held it, and the two diverged until the next write.

The adoption on the caps frame and the drop of writes still in flight, above,
wait on a caps frame, and the kernel sends one only in answer to a `ready` on
that socket: the shell page's socket, which posts `ready` at every open, gets
one on every reconnect; a pane's socket gets one at page load and, on a
reconnect, only when the bundle's ready queued across the drop and flushed onto
it. A reconnected pane socket that flushed no `ready` gets no caps frame.

The Outline pane's tag filter posts its lens the same way: the frame copy it
holds with only the outline lens changed, with a `writeId` and `edited: []`, so
the kernel applies the lens only. It ignores the ack and settles from the next
feed frame.

Until 2026-09-05 the dialog posted the whole blob for every edit from its own
un-echoed copy, so the guard refused a rename typed right after a create against
the dialog's own first write, the refusal reached only stderr and the sync
notices, and the dialog dropped its copy after three frames; renames and
assignments made in a burst were lost.

## The UI progress surface

When the kernel is catching up on a backlog (an old session opened that needs its
goals judged, or a burst of new activity), it is judging segments it hasn't judged
yet. The UI shows a **progress indicator** ("re-judging…", N pending) so the inbox
filling in is legible rather than mysterious. The kernel exposes the
pending-judgment count; the UI renders it.

## Remote kernels + postal federation

- **Each machine runs its own kernel** (its own records, its own indexing). Records
  stay local to the machine that produced them.
- **Postal federates over SSH.** Local and remote sessions share one bus address; a
  remote session tunnels the bus port to the laptop with `ssh -R
  PORT:127.0.0.1:PORT` and heartbeats for presence (`postal/postal_service.py`), so
  messages cross machines.
- **Viewing remote sessions**: shipped as read-federation — link a remote kernel
  and its sessions appear as `host:name` tabs and timeline lanes in the one local
  dashboard, sharing the feed (the guide's "Linking kernels on other machines"
  covers setup).
- **Comms across machines**: gated per host by trust tier (trusted / directed /
  isolated — see `SECURITY.md`), not by the directory-group sketch above.

## Serve-layer security (auth / CSRF hardening)

Binding `127.0.0.1` is not an auth boundary: any webpage the user opens can reach
localhost, and WebSockets are not covered by CORS, so without origin checks a
malicious page can open `ws://127.0.0.1:PORT/ws` and drive the kernel (inject
prompts, spawn/interrupt sessions). This is the ClawJacked class (CVE-2026-25253).
The Python kernel (`kernel/kernel.py`) closes it.

- **Always-on Origin/Host validation (token-independent).** Validate `Origin` and
  `Host` on every HTTP request AND the `/ws` upgrade; allow only the kernel's own
  origin plus known local client origins (the browser at the kernel's host, the
  `vscode-webview://` extension, the timeline), reject everything cross-site. This
  kills ClawJacked for free; legit local clients send the right Origin/Host.
- **Token REQUIRED on every gated route, loopback included** (Jupyter's model:
  loopback is one network stack shared by every local UID, so the `0600` token
  file — not the socket — is the same-user trust boundary; the gate keeps a
  same-host co-tenant out of `/send` and the bus). Accepted forms: `?token=`
  (browser bootstrap, seeds a `SameSite=Strict` cookie so it never re-prompts),
  the cookie, and `X-Romp-Token` (CLI/hooks/daemons, read from the file). The
  token is baked into how the kernel launches (env/autostart), never a manual
  per-launch flag; a bare browser open of `/` gets a paste-the-token login page
  (bare `romp` prints the link + opens a browser). Two kinds of route are exempt:
  the no-side-effect liveness probes (`/healthz`, `/version`, `/busy`; bus
  `/ping`) so liveness never breaks token-less monitors, and the install files
  (`/manifest.webmanifest`, plus three icon names under `/media/`, an allowlist
  rather than a prefix) because a browser fetches a manifest and its icons with
  credentials omitted, so a gated manifest 403s the moment "Add to Home Screen"
  consults it. The install files are static (a JSON literal, three PNG files)
  and read no session state. `tailscale serve` traffic needs the token
  once per device like any browser — and funnel (public internet through the
  same proxy) must still never be enabled for this port, since the token would
  then be the only gate with no device identity in front of it.
- Regression tests: a cross-site `/ws` upgrade with a foreign `Origin` must be
  rejected, and a token-less loopback request to any gated route must 403
  (tests/test_kernel_auth_hardening.py, tests/test_kernel_ws_auth.py,
  tests/test_postal_token.py).

## Naming

- **kernel** — the one always-on core (Layer 1 + Layer 2 + HTTP/WS serving).
- **ui/** — the front-end package (the three panes).
- **Romp Postal Service** — always-on messaging infra, below Layer 2.
