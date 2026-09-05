# Subagent transcripts: open any agent's whole conversation from the dashboard

**Status: slice 1 IN FLIGHT** (branch `subagent-transcripts`, PR #935, 2026-09-05; lands with that PR's
merge commit). **Slice 2 BUILT** the same day on branch `awaiting-rows`, stacked on #935 — the Awaiting
box lists what a session waits on as rows grouped by kind, and the chip opens it; its section is at the
bottom.

Direction picked by the user (2026-09-05): a Claude Code subagent (the `Agent` tool, older
transcripts say `Task`) should be readable in full from the dashboard — live while it runs and
after it finishes — the way the desktop app lets you open one. Paraphrased: the two endpoints romp
shows today are not enough to tell what an agent is doing or why it took the turn it did.

## The problem (traced 2026-09-05, before the fix)

romp showed exactly two points of a subagent's life, both on the parent's Agent tool head
(`renderTool`, pinned by `ui/webview/render-agent.test.ts`): the kickoff **prompt** and the final
**report**, as collapsed folds. Everything in between — the agent's own reasoning, its tool calls,
the files it read, the commands it ran — was invisible. Three concrete defects fell out of that:

- **No progress while running.** A background agent's head showed an amber dot for minutes with
  nothing to say about what it was on. The bg-tasks box (`renderBgTasks` / kernel `_bg_tasks`)
  had a row, but its expansion showed only the clipped prompt and the last 8 KB of an output file
  that is, for an agent, a raw JSONL — the ugly launch record, not a transcript.
- **The report fold lied for background agents.** The tool_result of a `run_in_background` Agent
  is only the launch acknowledgement ("async agent launched…"), and that is what the fold showed
  forever. The real closing summary landed later as a `<task-notification>` user turn, rendered
  as its own notice card (`renderAgentNotif`) with no link back to the head.
- **The Awaiting word confusion.** The statusline's "Awaiting agents" chip collapses to a generic
  "task" wording as soon as one pending row is not an agent (`_session_awaiting`: kind is
  `agents` only if EVERY pending row is one), so a session waiting on two agents and one build
  reads as waiting on "tasks", and nothing on screen lists which. (Slice 2.)

## Verified data facts (read-only survey of this machine; nothing real is copied here)

- Claude Code writes ONE complete JSONL per subagent beside the parent transcript:
  `~/.claude/projects/<proj-slug>/<parent-sid>/subagents/agent-<agentId>.jsonl`, plus a sidecar
  `agent-<agentId>.meta.json` with keys `agentType`, `description`, `spawnDepth`, `toolUseId`
  (optional `name`, `isFork`, `stoppedByUser`, `parentAgentId`). `agentId` is `a` + 16 lowercase
  hex. **`meta.toolUseId` equals the parent's Agent `tool_use` block id** — the join key.
- The records are the same shape as a normal transcript (`user` / `assistant` / `attachment`,
  `message.content` blocks of text / thinking / tool_use / tool_result), tagged
  `isSidechain: true`, `agentId`, `sessionId` = the PARENT sid, `parentUuid` chaining within the
  file. So the FileAdapter parse that builds the chat already understands them.
- The background Agent tool's output file (`/tmp/claude-<uid>/<slug>/<sid>/tasks/<agentId>.output`)
  is a SYMLINK to that same JSONL — the projects dir is the single authoritative copy. Background
  BASH tasks (`b` + 9 alphanumerics) are plain text and are not subagents; their treatment is
  unchanged.
- Parent linkage: the assistant record's `tool_use` block `{id, name: "Agent"|"Task", input:
  {description, prompt, subagent_type, run_in_background?}}`; the matching user record's top-level
  `toolUseResult` carries `agentId` (the async variant also `isAsync`, `status`, `outputFile`; the
  sync variant the final `content`, `usage`, `totalDurationMs`…). A background agent's report
  lands later as a `<task-notification>…<result>` user turn (`origin.kind == "task-notification"`).
- Sizes: median agent file ~400 KB / ~116 records; the largest seen ~22 MB, dominated by a few
  giant tool_result blobs. Per-block truncation (the chat's existing caps) matters more than
  pagination; the shipped event list is a capped TAIL with a `truncated` flag.

## Decision: read the file, do not un-drop the stream

The SDK backend deliberately DROPS live stream messages tagged `parent_tool_use_id`
(`kernel/sdk_backend.py` `msg_to_atom`, pinned by `tests/test_sidechain_atoms.py`): a subagent's
kickoff prompt used to leak into the parent chat as a giant expanded box. That stays exactly as it
is. This feature reads the agent's ON-DISK file instead, which:

- keeps the parent stream clean (no sidechain atoms to filter per push);
- works for dormant and revived sessions and for tmux sessions, which have no SDK stream at all;
- reads the authoritative store (the file the CLI itself writes), never a reconstruction.

Liveness is event-based, never a clock: for an SDK session the backend's SubagentStart/Stop set
(`SdkSession._subagents`, now shipped WITH each agent's id) and its task-lifecycle set
(`bgTasks`, keyed by tool-use id) say what is in flight; for a tmux or dormant session, "running"
means the parent transcript has no final result for that tool-use id yet (a sync tool_result, or a
task-notification for the launch) and the launch postdates the current CLI epoch — the same ghost
gate `_bg_tasks` already applies, so the head's dot, the gist, the viewer and the box can never
disagree.

## What ships in slice 1

### Kernel

**Discovery + join.** `_subagent_meta_map(parent_path)` enumerates
`<proj>/<fsid>/subagents/agent-*.meta.json` into `toolUseId → {agentId, agentType, description,
spawnDepth}`, cached per directory on the directory's mtime (a new sidecar changes it — a stat, not
a timer). The parent's `toolUseResult.agentId` on the tool_result record is the second source of
the join (it also fills the bg-scan rows via `_bg_step`).

**The Agent tool event** (`build_session`, the `kind:"tool"` event) now carries:
- `toolUseId` — the tool_use BLOCK id. Verified: the event's `uuid` was the RECORD uuid, so the
  block id was not on the wire before. Every tool event carries it now (cheap; the join key for
  anything else that wants to pair a result to its call).
- `agentId` (or `null`) on every Agent/Task event.
- `agentAsync: true` when the tool_result was an async launch ack.
- while a BACKGROUND agent runs: `agentRunning: true`, `output: ""` (the launch ack is not a
  report — the client's existing "no output → amber dot" rule then reads it as running), and
  `agentGist: {recent: [{tool, desc, ts}, …up to 3, newest last], calls, since, last}` folded
  append-incrementally from the agent's file (`em.fold_records`, cached on the file's (mtime,
  size)). `desc` is the head vocabulary: `input.description`, else the file path, else the first
  line of the command, clipped.
- once the background agent's `<task-notification>` has landed in the parent transcript: `output`
  = the notification's `<result>` text (`_parse_task_notification` now captures it; the scan-all
  rows keep it, capped like the chat's own output cap) and `isError` from its status, so the head's
  report fold shows the closing summary instead of the launch ack. The task-notification notice
  card in the transcript stays as it was. A foreground agent's tool_result was always the report.
- `agentGist` is absent once the agent has finished — the report fold is the endpoint then.

**The chat fold** (issue 903's sealed-prefix cache) treats a running agent the way it treats an
undecided interrupt seam: the launch's turn is never sealed while the agent runs
(`_chat_agent_open_at` holds the boundary), so the gist and the eventual report are rebuilt live
with no per-push gate. Sealed Agent events are remembered in the entry (`agents`: tool-use id,
agent id, pending flag) and one cheap gate — `_chat_agents_moved` — demotes to a full build when a
sealed pending agent's row turns terminal or comes alive again, or a sealed null `agentId` gains
its sidecar.

**Open/close protocol.** Client → kernel `{type:"openSubagent", id:<sid>, agentId}` and
`{type:"closeSubagent", id, agentId}`. The kernel answers, and re-pushes while the viewer is open,
`{type:"subagent", id, agentId, meta:{agentType, description, spawnDepth, toolUseId}, running,
events:[…], truncated}` — `events` built through the SAME `build_session` path the chat uses
(`path_override` = the agent file, plus `sidechain=True`, which keeps the parent's side-store
notes — retry recoveries, orphan replies, effort/gesture chips — out of a transcript they do not
belong to). Capped at `SUBAGENT_EVENT_CAP` tail events with `truncated: true` when cut (the
`/clear` episode fold's precedent). A missing or unreadable file pushes
`{type:"subagent", …, error:"<plain sentence>"}` — loud, never blank. Open viewers live on the
client record (`client["subagents"]`); the pusher's existing per-cycle chat pass re-sends a frame
only when its change key — the agent file's (mtime, size) and the running flag — moved, and the
per-client dedup slot `("subagent", sid, agentId)` absorbs the rest; a close or a disconnect
ends the pushes. No new polling loop.

**Federation.** Nothing bespoke: the request carries the parent `id`, so `routeOutbound` sends it
to the owning host and strips the prefix, and `prefixInbound` re-prefixes the frame's `id` — the
same path a comment-thread request takes. The client's tab id is `<parentId>/agent/<agentId>`
(`ui/webview/subagent-view.ts`), NOT the `sub:<sid>:<agentId>` shape first sketched: host-prefix.ts
reads the FIRST colon of an id as the host marker, so a `sub:` prefix would have named a phantom
host everywhere `hostOf()` is consulted (offline marks, strip dimming, outbound routing). A
colon-free suffix keeps `hostOf(subId) === hostOf(parentId)` with no special case.

**bg-tasks rows** carry `agentId` when the launch is an agent (from the ack's `agentId`, the
sidecar map, or the output symlink's basename), so the box can offer the same arrow. Shell-task
rows are untouched.

### UI (`ui/webview/render.ts` + `ui/webview/subagent-view.ts`)

- **Arrow** (level 0): a house line-icon button on the Agent/Task head — and on agent bg-task
  rows — when `agentId` is present; tooltip "open transcript" (`setTip`), delegated through
  `actions.ts` (`data-act="openSubagent"`, click-safe across re-renders, `.romp-acted` pulse).
- **Live preview** (level 0, only while running): up to three dim rows under the head, one per
  recent tool call in the head vocabulary (`<tool> <desc>`), newest at the bottom, the last row
  trailing `· N tool calls · <elapsed>`. It wears the tool-fold-toggle's 0.86em (no new size) and
  lives INSIDE the tool turn, so a collapsed compact run hides it and an expanded run shows it.
  Gone the moment the agent finishes.
- **Peek tab viewer** (level 1): the arrow opens a peek tab (`peekId` / `.tab-peek` mechanics —
  `chatVisible()` says a subagent view is in the chat lens only when pinned) with id
  `sub:<sid>:<agentId>`, labelled by the sidecar's description (clipped) or agentType, wearing the
  parent's colour. Its header reads "subagent of <parent> · <agentType> · running|finished" with
  the parent name a link back to the tool head (setActive with the head's uuid as the anchor, the
  chat's own scroll-to-uuid), a pin control ("keep this tab") that converts it to a regular tab
  that stays until closed, and a "earlier part not shown" note when `truncated`. The transcript
  renders through the SAME `displayItems` / `renderEvent` path as the chat (Compact transcript
  applies), read-only: the composer is disabled, no ask controls. Live pushes replace the events in
  place with the chat's own scroll rule (follow the bottom only when already there). The romp loader
  holds the pane until the first frame; an `error` frame shows its sentence in the pane. Pinned
  subagent tabs do not survive a reload in this slice (a code comment says so).
- Feed and timeline: no changes.

## Sizes and caps

Per-block caps are the chat's own (`output` 16000 chars, `input` 4000, prompt/report markdown).
`SUBAGENT_EVENT_CAP` bounds the shipped tail; the gist keeps 3 recent calls and two timestamps. The
gist fold state is bounded by the JSONL cache's LRU (384 files) like every other reader.

## Slice 2 (PR stacked on #935): the Awaiting box lists what a session waits on, by kind

**Status: built 2026-09-05** (branch `awaiting-rows`). The user's call, paraphrased: the three things a
session can wait on — agents, background commands, kernel watches — are different things, so show them
as SEPARATE ROWS grouped by kind, and make the chip clickable.

### The defect it fixes (traced before the change)
`_session_awaiting` answered with ONE kind chosen by source precedence: live SDK subagents → "agents";
else the pending background launches → "agents" only if EVERY pending row was an agent, else the generic
"task" (so a background shell command plus a background agent read "Awaiting 2 tasks" with the agent
silently absorbed); else armed watches → "job", a word nothing else on screen explained. The same
situation read "agents" or "tasks" depending on which source spoke first, and the statusline chip was a
plain span — the one status word on the pane you could not click through. The feed's pill showed only
when the legacy `tasks` list was non-empty, so a wait on live subagents had no clickable affordance on
the card at all.

### Kernel (`kernel/kernel.py`, `kernel/judge.py`)
- `_session_awaiting` COMBINES its live sources instead of short-circuiting. Each contributes rows
  (`_awaiting_item`): the backend snapshot's live subagents (kind `agents`, carrying the hook's
  `agentId` so the slice-1 arrow works), the pending background launches (`agents` for Agent/Task/
  Workflow dispatches, `commands` for run_in_background Bash and Monitor), and the armed watches
  (`watches`; label = the `--note`, else the clipped predicate, else `PR #N (repo)`). A background agent
  seen by BOTH the hook set and the task stream is one row (matched on agentId), wearing the launch's id
  (Stop's handle), its description, and the earlier start. `_awaiting_from_items` derives the legacy
  `kind` / `count` / `why` / `since` / `tasks` from the union: one kind present → that kind's legacy key
  and the sentence it always wore; several → kind `"mixed"`, count = every row, and a why that names
  each group ("waiting on 2 background agents, 1 background command and 1 armed watch"). The all(...)
  collapse is gone. The overlay, owned-yield, judge-stamp and delegation arms ship `items: []` (they
  name no live rows) except the peer arms, which list their peers as `peer` rows.
- **The wire shape**, shipped wherever `awaitingKind`/`awaitingCount` ship — the chat status
  (`awaitingItems`), the timeline lane (`awaitingItems`), the goal card and the placeholder card
  (`awaiting.items`): `[{kind, id, label, since, agentId?, detail?, watchId?}]`, kind in
  `agents | commands | watches | peer | timer`. `since` is the row's OWN event time or null, never now.
  A generic watch row carries `watchId` (cancel_watch's handle) and its predicate as `detail`; a PR
  watch carries neither.
- `AWAIT_KINDS` gains `"mixed"`; the judge's parse sites (`_parse_close`, `_parse_plan`) validate
  against `AWAIT_KINDS_JUDGED` (the five specific kinds), so a closer never FILES mixed — an LLM
  emitting the word degrades to kindless like any other off-enum kind, and every lift/supersede rule
  keyed on a stamp's kind keeps seeing a specific one. The overlay reader accepts mixed as data.
- Vocabulary in the kernel's sentences: "waiting on a background command: X" / "waiting on 2 background
  commands — X, …" (was "task(s)"), "waiting on 2 armed watches — X, …" (unchanged), "N background
  agent(s) still working" (unchanged). The owned-yield arm and `_awaiting_card`'s fallback headline say
  "command" too; the legacy kind KEYS (`task`, `job`) stay for every consumer that reads them.
- A `cancelWatch` WS door: `{type: "cancelWatch", id: <sid>, watchId}` → the SAME `cancel_watch` that
  `romp watch --cancel <id>` and `POST /watch {"cancel"}` reach; loud `warn` on a miss. **A cancel path
  existed for generic watches only** — nothing retires a `romp watch-pr` early today, so PR-watch rows
  carry no `watchId` and the box offers no button for them (not added here: it would be a new retire
  path, out of this slice's scope).

### Words (`ui/webview/spin-caption.ts`, `ui/romp-timeline-view.js`)
`KIND_WORD` keeps the kernel's keys and changes the words: `task` → "command", `job` → "watch",
`mixed` → no word; `kindWord` pluralizes "watch" → "watches". New shared helpers: `groupRows` (display
order agents → commands → watches → peers → timers; an unknown group is kept, never dropped),
`awaitBreakdown` ("2 agents · 1 command · 1 watch"), and `awaitWord` — the ONE label rule for the chip,
the box gist and the feed pill: one row → its word ("agent" / "command" / "watch" / "timer"; a peer row →
the peer's own name, swapped in by the caller); several of one kind → count + word ("3 agents");
several kinds → the number alone ("4"); no rows (an older kernel, a stamp) → the legacy kind + count as
before. The timeline's standalone twin mirrors the table (`tlKindWord`), and its badge goes through
`tlAwaitSuffix` so a mixed wait reads "Awaiting 4" on the lane — the only timeline change.

### Chat pane (`ui/webview/render.ts`, `styles.css`)
- The statusline chip is a `<button class="chip chip-awaitingBg chip-btn" data-act="awaitingChip">`
  on a delegate installed once on the stable `#statusline` (click-safe across the per-push rebuild; the
  delegate's `.romp-acted` pulse acknowledges). Click → `bgFoldOpen.add(sid)` (the box's own fold
  state), `renderBgTasks()`, `scrollIntoView`. Tip via `setTip`: the breakdown, the kernel's why, and
  what the click does. Label per `awaitWord`; a single named peer keeps its coloured name.
- `renderBgTasks` hands the box to `renderAwaitWhy` whenever a wait exists (tracked tasks join its
  rows); the tasks-only path (no wait: a service the session keeps around) is unchanged. `renderAwaitWhy`
  header: "Awaiting <n> · <breakdown>" for mixed, the single-kind sentence as before. Expanded: the rows
  grouped by kind under `.bg-group-head` (shown only when 2+ groups, "Background tasks" counting as a
  group for the tracked leftovers), then the plain-words note. ONE row renderer (`bgRow`, fed by
  `awaitRowSpec` / `taskRowSpec`): agent rows → the slice-1 arrow + Stop while their launch is a live
  tracked task, the prompt as the fold, NO output tail (the output file is the raw transcript; the arrow
  is the way in — this also drops the raw JSONL fold from tracked agent rows in the tasks-only path);
  command rows → status, Stop, the command + output-tail fold; watch rows → await-green dot, "armed",
  "· 31m" since registration, Cancel when `watchId` rides, the predicate as the fold; peer rows → the
  name in identity colour; a row with nothing to unfold is not a toggle. Per-row "· 12m" clocks tick
  with the statusline timer from `data-since` (the box re-renders only on new fields; `awaitKey` now
  includes `awaitingItems`). A wait with no rows (stamp / overlay) still expands to its full sentence.
- No change to WHEN the chip flips or a card moves: `_session_chip`'s formula is untouched; only what
  the surfaces say and what is clickable changed.

### Feed (`ui/webview/feed.ts`, `feed.css`)
The pill shows for ANY wait with rows (`awaiting.items`, or an older kernel's `tasks` read as rows of
the legacy kind's group), reads by `awaitWord` (a single peer → its coloured name), opens by default
like the bg-task pill did, and its expansion lists the rows grouped under `.ftask-group` headers when
2+ groups — labels only (peer rows keep the identity colour + click-opens-session). `spinFor`'s
say-it-once rule now stands the caption down under rows of ANY kind (`items` or `tasks`); a wait the
kernel cannot enumerate keeps the boxed caption, the only place its why shows.

### Tests
Kernel: `tests/test_awaiting_rows.py` (all three sources at once → mixed; the shell + agent case is two
rows of two kinds, never "task"; the hook/launch merge; single-kind sentences; watch rows' handles; the
mixed kind's enum/parse rules; the cancel door), plus the existing pins updated with a note each
(`test_awaiting_count.py` also pins that every surface ships the rows). UI: `ui/webview/awaiting-rows.
test.ts` (the label rules executed; the three word maps agree on every kind × count; chip-as-button +
delegate; grouped rows and per-kind affordances; Cancel → cancel_watch; the feed pill for any wait; the
vocabulary), plus the updated pins in awaiting-state / awaiting-box-sync / awaiting-peer-name /
bg-tasks-layout / feed-awaiting-swirl / spin-caption / timeline-awaiting / src/bg-tasks.
Screenshot fixtures: `tools/ui-verify/fixtures/awaiting-rows-{chat,feed}.html`.

### Left for later
- Nested subagents' chain in the viewer header (parent → agent → agent) — slice-2 polish from the
  original scoping, not done here.
- An early-retire path for PR watches (then the watch row's Cancel appears for them too).
- The owned-yield arm words every owned dispatch as a "command" (it carries no type); a placed agent
  dispatch that outruns a block with no stamp is the one case that reads slightly off.
