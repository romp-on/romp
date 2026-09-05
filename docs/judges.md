# The judges: a field guide

!!! note "Optional reading"
    You don't need any of this to use Romp. The Internals section is here for
    when you're curious how the task layer works under the hood.
    Describes the system as of **2026-07-20**; the behaviour it documents moves,
    so treat anything here as a snapshot rather than a contract.

> The picture first: [judge-pipeline.md](judge-pipeline.md) is the one-page
> diagram map (when each judge runs, card-first filing, the state machine,
> the postal flow). This page is the per-judge detail behind it, plus the
> failure contract. The state model itself (the diary, the fold, every chip)
> lives in [goal-state.md](goal-state.md).

romp keeps two live artifacts per session without you curating either: a
readable text record (chat captions, the session TOC, the timeline) and the
goal board (the cards on the feed). Judges maintain both. A judge is one
small LLM call with one narrow job, one system prompt, and one name. It
fires on an event in the session's life, writes its verdict to an
append-only record, and plain code does the rest. No judge owns state: a
wrong verdict is one bad event in a log, outvoted by later evidence, never a
corrupted fact.

The thirteen judges run in two tiers. The index tier (cheap, fast models) writes
text only and never touches goals. The triage tier (a stronger model) maintains the
board. Both run continuously from the kernel producer and are event-gated,
so idle sessions cost file stats, not model calls.

## The roster

| Judge | Tier | Prompt | Fires when |
|---|---|---|---|
| captioner | index | `CAPTION_SYS` | a segment or turn's work ends |
| gister | index | `GIST_SYS` | a user message lands |
| archiver | index | `ARCHIVE_SYS` | a session gains a turn |
| opener | triage | `OPENER_SYS` | a message lands, work still running |
| planner | triage | `PLAN_SYS` | a segment's work ends |
| placer | triage | `PLACE_SYS` | the planner filed under a card with open sub-goals |
| closer | triage | `CLOSER_SYS` | a turn ends |
| unblocker | triage | `UNBLOCK_SYS` | an open blocked goal has ended turns or done filings newer than its block (or its last check of each) |
| distiller | triage | `DISTILL_SYS` | a card completed and settled |
| briefer | triage | `BLOCK_BRIEF_SYS` | a card blocked |
| grouper | triage | `GROUP_SYS` | the set of open cards changed |
| consolidator | triage | `GROUP_SYS` (shared) | the set of completed cards changed |
| courier | triage | `COURIER_SYS` | a peer message arrives |

Twelve prompts back the thirteen names: the consolidator reuses the
grouper's prompt over a different column, under its own name. Usage and error logs
carry one name per prompt; the timeline band keeps five family rows and
folds the fine names onto them (`_JUDGE_FAMILY` in `kernel/kernel.py`).
Prompts are named by their constant in `kernel/judge.py`; grep the constant,
line numbers drift.

## A turn through the judges

A segment is one input and the work it causes; a turn holds one or more
segments (your message, then perhaps a peer's, each with its own work).

The moment your message lands, the gister captions what it is about and the
opener puts the ask on the board, before any work exists. When a segment's
work ends, the captioner writes its one finished line and the planner files
what the work actually did, calling the placer when a spot inside a card
needs picking. When the turn ends, the closer audits the goals the turn
touched for quiet completions and the archiver refreshes the session's
headline. From there the board keeps itself: a completed card settles and
gets the distiller's takeaway, a blocked card gets the briefer's decision
brief, a blocked goal with new conversation or new completions since its
block gets the unblocker's re-examination (was its question answered in
passing, or overtaken by finished work?), a
changed set of top cards may get nested by the grouper (open column) or
the consolidator (done column), and a peer message goes to the courier
instead of the planners. Every section below is one of those moments in
detail.

## The index tier: the text record

Three index-tier judges write the words you read in the chat, the timeline, and
search. They are write-only: nothing they emit changes a card's state.

**captioner.** The readable activity log. Per finished segment or turn, one
short past-tense phrase (4 to 7 words) that leads with the result and never
names a tool. An empty reply means "no finished work", skip. Appends to
`captions/<fsid>.jsonl`; feeds the chat, the feed cards, the timeline.

**gister.** The captioner's sibling for a request still in progress: a
present-tense topic phrase ("a dark-mode toggle for settings"), not a
result. Feeds the "Analyzing:" placeholder card and the timeline dot the
moment a message lands.

**archiver.** The per-session headline and abstract, re-run when the
session gains a turn. Reads the session's turn captions oldest-first;
replies exactly two lines, `HEADLINE:` and `ABSTRACT:`. Written to
`archive/<fsid>.json`; feeds the chat TOC and the search index.

## Filing: the opener, the planner, the placer

Filing answers one question: which card does this belong to? Three judges
answer it at different moments. The opener places your ask the instant it
lands. The planner places the finished work and rules on it. The placer
picks the depth inside a card when that is still open.

All three file **card-first**. The open-goal menu renders as a
tree grouped under top-level cards, and filing names a card, never a nested
line; the test is "can this card be called done without this work?", judged
where you actually experience the board. Only the placer ever goes deeper
than the card.

**opener.** Fires the moment your message lands on a still-open segment, so
the board shows the ask before the work exists. It is the closer's mirror:
it may only open (mint a new card or file under an open one, card level,
never done or block), as the closer may only close. A reply that never
parses is logged and the ask is hard-placed anyway; a prompted goal never
stays unplaced.

**planner.** Fires when a segment's work ends, and holds the full op list:
`mint`, `sub`, `done`, `block`, `retitle`, `skip`. Done is eager (an answer
counts as done), but ending by asking you to approve is a block, and only
the human blocks: peer, CI, and build waits stay working. Its verdicts
append diary events. The same engine, mode-switched by note injections,
handles four more phases: a live re-plan after you clear a card mid-work,
nudge resolution (resolve the named goal, done or block, no plain step),
delegation follow-on (file the recipient's work under the courier's plant),
and tagged follow-ups (file under the cited goal unless the reply starts a
different thread — the pivot's goal is then its own card with `pivotFrom`
provenance; the structural tie retired with containers, T101). A segment opened by an
untargeted kernel notice (restart or resume) carries a housekeeping note:
pure verification sweeps file nothing. Since 2026-08-25 that is also a
mechanical floor, not just a request: a work-run whose segment was opened by
romp's own bookkeeping — a kernel notice, or the CLI's `[Request
interrupted…]` stop artifact — never mints a fresh top-level goal (its
menu-targeted ops still apply, so the work keeps advancing existing cards),
and no mint anywhere roots its promptUuid at a record that files nothing (a
coordinate/question mail, a bookkeeping record): the anchor substitutes the
segment's first assistant atom. The clear wrap-up is exempt — its one
blocked card is the designed needs-you escape.

**placer.** The second, scoped call, only when the chosen card already has
open sub-goals: it sees just that card's subtree and picks the spot, biased
to the highest level that makes sense. Most cards have no open sub-goals,
so most placements stay one call; the opener's and the live re-plan's
placements always stay card level.

None of the three reorganizes the board; that is the grouper's job, and the
prompts say so.

## Status and summaries: the closer, the unblocker, the distiller, the briefer

A card should say so when it is finished or stuck, without the agent having
to narrate it. The closer supplies the missing verdicts; the unblocker retires the stale
ones; the distiller and the briefer write what you read on the resolved card.

**closer.** The turn-end completion backstop; it exists because agents
rarely say "done". Since 2026-08-25 a delegated goal's report-back rides its
audit: when a "delegated to" tracking item completes, the recipient's own
resolution travels into the sender's tree (run_propagate) and the
steps-finished nomination shows it to the closer as a marked
"Delegation reports" section — before that, a delegated ask's only visible
history was the dispatch, the closer correctly omitted, and the look-stamp
sealed a finished question open forever (the auto-nudge then re-asked it
seven times in 75 minutes). Two guardrails ride the same fix: the closer
never completes a "delegated to" tracking item itself (its ending event is
the recipient's completion — a dispatch-time done consumed the slot and
starved the report), and on a status-reporting turn (nudge / follow-up /
wrap-up) every open working top rides the audit (the cited-umbrella
descendants channel served containers and retired with them, T103: a
once-stranded leaf is its own top now and rides the plain channel).
It audits only the goals the turn actually touched;
verdict done, blocked, or omit, with "when in doubt, omit". Idempotent per
turn. Its diary events carry src `closer`, so planner and closer verdicts
stay distinguishable, and both defer to the user floor: a verdict computed
from evidence at or before your last reply loses.

**unblocker.** The stale-block backstop; it exists because answers arrive
in passing and work overtakes asks. A goal blocked on a question is only
ever unblocked by work filed on that exact node — but the answer usually
files wherever the planner judges the segment to serve, so a dormant
blocked goal never hears it and holds its card in Needs you (a card can
otherwise sit for hours on a buried sub whose question the very next
stretch of conversation already answered — or on an approval whose work
the session then visibly did anyway). Given each open blocked goal's
question (subs and tops both) plus two evidence sections — the
conversation since its block, and the goals the session has completed
since then with why each counts as done — it verdicts lift or hold, "when
unsure, hold"; a lift lands as a normal `unblock` diary event,
why-prefixed "answered in passing". The completed-since section is the
durable half: the conversation tail scrolls past its 9k-char window and a
hold is never re-examined against the same turns, so an ask superseded by
later completions used to rot in Needs you until cleared by hand (the
2026-08-08 study: 400 card-hours across 302 manual clears). Event-gated
per node on both streams: `blockCheckT` remembers the newest ended turn
examined (turn-time domain — the feed's re-judging latch reads it against
reply times, so it never carries a filing time), and `blockCheckDoneT`
remembers the newest done-verdict filing, so a completion arms a
re-examination even when no new turn ever arrives (a late closer filing
on an idle session), a stable session costs zero calls, and a give-up
re-arms on the next new turn or filing. Its model call spans seconds, so
it holds no store copy across the call: verdicts apply to a fresh load,
and a node that moved on mid-call (you resolved or cleared it) is skipped
with a `drift-skip` error row rather than overwritten. `ROMP_UNBLOCKER=0`
disables.

**distiller.** When a top card completes and settles: `BACKGROUND:`
(re-orientation for a reader who lost the thread) plus `TAKEAWAY:` (the one
thing you would most want to know now that it is done), consuming the
closer's done-reason as ground truth. After a follow-up re-completes a
card, the prior summary is handed back and the work text is cut to the
stretch after your follow-up, so the takeaway is the update, never a recap.
May cite a `SOURCE: mN` line, parsed into the summary's deep link; a cite
that misses logs and chips the card instead of failing.

**Distiller notes.** Every judge that writes prose you read — distiller,
briefer, staller, captioner, gister, archiver — also carries your standing
style notes, when you keep any: `~/.config/romp/distiller-notes.md` is read
at call time (no restart needed) and appended to the prompt, notes winning
over prompt defaults on conflict. Plain language, e.g. "never cite PR or
commit numbers; say what the change does". Delete the file and the next
call runs bare. The placement judges and the courier never see it: those
emit verdicts and agent-directed copy, not prose for you. The path is a
plain read that follows symlinks, so the durable setup is the content in
your dotfiles with a symlink here — one edit then reaches every machine's
judges through your normal dotfiles sync (the user 2026-08-15), instead
of each kernel keeping its own hand-seeded copy.

**briefer.** When a top card blocks (and live for the focused picker or
permission goal): a decision brief that leads with exactly what you must
decide or provide, then options and tradeoffs. Same `SOURCE:` contract as
the distiller.

## Board shape: the grouper and the consolidator

Since 2026-08-26 (T101, the user's ruling) the board's unit is the
INDIVIDUAL ASK: every top-level goal is its own card, tops never nest under
other tops, and container ("umbrella") goals are retired — a store-level
container is unavoidably a tracked unit (it owns rollup and, measured in the
provenance audit, swallowed the chain evidence of every stranded ask), while
the visual-grouping job belongs to the feed's display-side group fold, which
has no store footprint. Both judges keep only the housekeeping that serves
the ask-unit rule, move whole subtrees, and append no diary events:
structure, never status.

**grouper.** Given the open top-level cards: merge true twins into one line,
split a drifted tangent out to its own card, retitle a card its thread
outgrew — and "doing nothing is a valid, common outcome". Called only when
the open-top set actually changed. Hard rules in `apply_group`: never touch
a view-cleared card, same-session only; the retired `mint`/`group` ops are
parsed away and ignored if hand-built. A to-do-mirror top that duplicates a
line already inside another card is explicitly the grouper's to merge.

**consolidator.** The same prompt over the completed column, now merge/
retitle housekeeping only. Legacy umbrellas from either judge DISSOLVE in
every writer's rollup (the pre-pass beside the handoff-children lift):
children re-parent to top level with their own provenance intact, the empty
container leaves the store, and placements that pointed at it retire —
idempotent, self-healing against save-rebase republishes.

## Peer mail: the courier

The courier owns peer-message segments; the planners skip them. The sender
declared each message delegate, coordinate, or question at send time
(schema-required); the courier takes that as a strong prior and reads the
body for whether work actually changed hands. Since 2026-08-25 minting is
CHAIN-ROOTED (the user's verdict, replacing a one-day view-side split):
delegating plants a real goal in the recipient's tree (origin-stamped) only
when the sender's linked goal traces to a human prompt — self-then-ancestors
in the sender's store, origin hops into a local grand-sender's chain, the
root record read against the sender's own session — AND no ask card already
exists: since 2026-08-26 (T101) a dispatch whose chain roots to an ask the
courier LINKED (the sender's ask node) never mints a recipient top — the
tracking node plants under that ask, fan-out lives inside the ask card
(several dispatches, one card; several asks to one worker, several cards),
and the recipient files quietly with the reply-sweep ending. Only a rooted
dispatch with no resolvable ask node still mints the recipient top: there
the recipient card IS the ask's card. An untraceable delegate
files quietly instead: no recipient top (its work lives in that session's
view and transcript, and a needs-you state still surfaces through the
goal-independent hard-block floor), while the sender's "delegated to"
tracking node plants either way — so the delegation stays one glance away on
the sender's board. At mint time uncertainty files quiet; the burden of
proof is on the mint, the inverse of a display filter's. Coordinating makes
no card, ever. A planted goal also stores the delegating mail's cleaned
first line as the additive node field `frame` (2026-08-25, part of the
goal-node consumer contract): the distiller and briefer prepend it — with
the sender's linked-ask title — to their prompts as a marked
`<delegating-request>` section. One hop down a team that framing is a
MANAGER's restatement in implementation nouns, so the trace's root record
(the human prompt the chain proved — text plus sid, returned in place of
the old boolean) is stored too, shaped, as the additive field `userAsk`
(2026-08-26): the writers render it as a marked `<user-ask>` section beside
the frame and open the card's prose in the asker's own terms; a board's own
prompt-minted top threads its verbatim `quote` through the same section
when its promptUuid still resolves to a human record. The writers' prompts
also carry a standing jargon gate (no coined or internal name in an opening
sentence unless the `<user-ask>` itself uses it) and a source preference:
a report the session already wrote TO the user outranks the root ask, the
frame, and the raw work, in that order. A goal without a frame or root
record (a session's own work, or a node minted before the fields existed)
distills byte-identically to before.
The companion `run_propagate` is deterministic: when the
recipient completes the plant, the sender's tracker checks itself off
through the origin pointer; a quiet-filed delegation's tracker (which no
recipient goal can ever back-link) completes on the recipient's reply — the
report-back event, the same rule cross-host handoffs have always used.

## When a judge fails

Every failure logs one row to `judge-errors.jsonl` that answers who, where,
what, and why on its own: `judge` (the one-per-prompt name),
`fsid`, `err` (the kind), and `note` (the evidence: a reply tail, the API's
own error message, an exception name, or a give-up's scope and re-arm
event).

- **A failed call is not a parse failure.** Every call goes through one
  entrypoint, `_judge_run`: an isolated `claude -p` subprocess with a hard
  timeout. That entrypoint owns all call-level logging: a dead subprocess,
  a timeout, or an API **error envelope** (the CLI answering with an error
  string instead of a model reply) logs one `call` row and hands the caller
  an empty reply. Error text never reaches a parser. One API incident once
  became 2,352 phantom "parse" errors in a single hour because every parser
  rejected the same error message and every caller retried; this rule is
  why that cannot recur.
- **An empty reply never counts.** `parse` means the model's own text was
  rejected, and the row carries the reply tail so the log says why. Empty
  replies (the rate gate, a failed call) log nothing at the caller and
  never burn a retry cap — with two exceptions, both the closer's, both
  adopting a turn loudly on their own event: a safeguards refusal of the
  turn's content, and a *killed* call (one the timer ended; never an API
  error or a process that ended any other way), each counted against the
  turn it happened on. The kill streak is described below.
- **Every judge is capped.** Three genuine parse rejects on the same work
  item (`JUDGE_FAIL_CAP`) and the judge gives up loudly, one `give-up` row
  naming the re-arm event, instead of retrying every pass forever:

| Judge | On failure | Re-arms |
|---|---|---|
| opener, live re-plan | hard-places at card level immediately | (no retries needed) |
| planner (work run) | 3 tries, then hard-place a user message / drop a non-user segment | on its next segment |
| placer | files at the card immediately | (no retries needed) |
| closer | 3 parse rejects, or 3 killed calls, then skips the turn | when the turn gains atoms |
| archiver | 3 tries, then keeps serving the old headline | when the session gains a turn |
| grouper, consolidator | 3 tries, then leaves the board shape as is | when the top set changes |
| courier | 3 tries, then resolves from the sender's declared kind | (terminal; never orphans a message) |
| distiller, briefer | 3 tries, then a blank summary plus a card warn | on recovery, or a fresh completion/block |

While the account usage window is exhausted, the rate gate skips every call
across every session and logs one `rate-limited` row per window; gate skips count
toward nothing.

- **The closer bounds its own call and its own sweep.** Behind a turn's own
  goals the closer carries *riders*: open work nominated from elsewhere (a goal
  whose recorded steps are all finished, a starved leaf, a lifted card, and on
  a status-report turn every open top). One session's closer calls were once
  killed by the call timer 192 times in a row inside a single per-session
  sweep, silencing every judge for six hours: the menu carried 24 riders, the
  reply did not fit under the timer, and a killed call stamps nothing, so the
  identical menu rode the next turn. Two bounds, neither a fairness cap.
  `CLOSE_RIDER_CAP` limits the re-nominating riders per call (never the turn's
  own goals, never the status riders, which are one-shot per status turn),
  never-looked riders first; a cut rider rides a later landed call, so the
  backlog drains one landed call at a time. And a FAILED call (a kill, a
  subprocess error, an API error envelope, on either engine) ends that
  session's sweep for the pass with one `sweep-cut` row naming the turns left
  behind and the shape of the menu that died; parse rejects and pause-skips
  still walk on. Three *killed* calls on the same turn at the same size (the
  timer ending the call; an API error, or a process that ended any way other
  than the timer, cuts the walk too but leaves no strike) give that turn up
  loudly (one `give-up` row; the turn growing re-arms it) and the walk moves
  on, so a session whose one turn always dies still gets its later turns
  swept. A dead session cut this way keeps its marker pending and waits at
  the back of the death drain, so it cannot starve the others.

## Billing, and when the credential itself is broken

A judge call bills **the account of the session it judges** — the same pick the
session's own Billing selector holds, read from the same registry, with the same
selection (an explicit login pick → the login; otherwise the configured API
key source when one exists, else the login). With `ROMP_API_KEY_REF` configured,
each key-billed judge call resolves the reference through `op read --no-newline`; a
retrieval that fails is not retried by later calls in the same judging pass, and the first
call of a pass to reach the key gates the others until its retrieval returns. The next pass,
or a changed source, retries.
The resolved key is used for that call without a provider cache or a plaintext
file. The same source selection applies to standalone `romp-judge --once`.
Every judge child environment strips ambient Anthropic credentials and injects
the selected key only for a key-mode call. A provider failure fails that call
with a credential error; it cannot silently use the machine login or a stale
key. An explicitly login-billed call does not run `op`.

Legacy `ANTHROPIC_API_KEY` and Claude login remain supported when no runtime
provider is selected. See [Service environment and credentials](reference.md#service-environment-and-credentials)
for setup, service PATH and authentication requirements, and migration.

A **credential-class** failure (not logged in, an invalid key, an expired OAuth
token) is one no retry can fix — only the user can. The first such error
envelope latches judge-auth-down for that session (`STATE/judge-auth.json`), and
the session's next successful call clears it; both edges are events, nothing is
re-derived per build. While latched, the feed floors the session's focus card to
needs-you wearing a filled-red "Can't analyze" chip that names the refused
credential, and the card face carries the story and the fix — the session may be
fine; it is romp's analysis of it that is down, and every card of that session is
frozen until the credential works again. The floor yields to the live
permission/API-error floors: one interrupt at a time, the present event first.

## Other machinery that reads the same data

- **rollup_status**: pure code. Folds each node's diary into its state and
  each card's subtree into a column (see goal-state.md). Self-healing, and
  holds the authoritative tier: an open item on the agent's own to-do list
  pins the card in Working over any judge verdict.
- **plan-sync**: pure code. Mirrors the agent's own to-do list as flat top
  cards ("declared in the agent's own to-do list"). A step declared while
  the session serves a linked dispatch is stamped `serving` ({peer, msgId,
  goalId}, latched at mint on the newest delegate-kind segment at or before
  the declaration) plus the dispatch's frame and root-ask, and the feed
  folds it into the sender's ask card at render — fan-out inside the ask
  card, with needs-you breaking through (T137). A dispatch-less step
  threads the session's own prompt record instead. The grouper may still
  merge a duplicate mirror.
  It reads the live task store (`~/.claude/tasks/<fsid>/`, the same source
  the chat TO-DO card reads) — never the transcript, whose record of a
  TaskUpdate can fall off the live chain when an api-error retry forks the
  graph. A missing store falls back to the transcript fold; an unreadable
  one logs a `task-store` row and skips the pass rather than silently
  degrading.
- **auto-nudge**: a kernel trigger, not an LLM. Detects a genuinely stalled
  session and injects one nudge prompt; the planner's nudge phase does the
  judging, and a failed nudge records the block.
- **awaiting**: layered. The LIVE sources are event-derived — subagents,
  the pending background-task set, the delegation graph — and the CLOSER files a
  durable awaiting verdict (the goal store's ⏳ stamp) carrying a KIND naming
  what the wait is on: agents, task, job (an external computation), peer, timer.
  The kind scopes the rules: a peer's answer supersedes only peer waits, and a
  job stamp survives its watcher dying. The wake's clock is a DEAD-MAN'S SWITCH
  for waits whose ending romp cannot observe — kind=job (external compute),
  cross-host peers, legacy kindless stamps, hung-forever agents/tasks, and
  prose-declared timer check-backs; every observable ending (a notification
  pairing, the restart epoch, a tool's declared deadline, a peer's answer or
  death) retires its wait as an event, with no clock at all.

## Where responsibilities overlap

- **planner vs closer** on done/block: by design. The planner is eager per
  segment (precision), the closer is the turn-end backstop (recall); diary
  src tells them apart, and both yield to the user floor.
- **opener vs planner**: the same segment, two moments. The opener's
  placement is the instant guess; the planner's work run may correct it
  (retitle, refine) but never duplicates it.
- **grouper vs consolidator**: same prompt, disjoint columns, separate
  names in the logs.
- **distiller vs closer**: consumer relationship; the distiller treats the
  closer's done-reason as ground truth.
- **courier vs planner**: mutually exclusive by segment author; the courier
  plants, the planner's delegation phase files under the plant.

## Ops and knobs

- Toggles: `CLOSER_ON`, `GROUPER_ON`, `DISTILLER_ON`, `CONSOLIDATE_ON`.
  Models: `STATE/judge-model` (triage), `STATE/index-model`.
- Logs: `STATE/judge-usage.jsonl` (per-call cost, one name per prompt),
  `STATE/judge-errors.jsonl` (the row contract above; kinds are parse,
  call, give-up, sweep-cut, cite-miss, rate-limited, task-store, history-unreadable,
  task-key-collision — a duplicated to-do mirror key, reconciled per node
  and surfaced loudly),
  `STATE/judge-auth.json` (the per-session judge-auth-down latch — see
  "Billing" above).
- Debugging: run the judge's own code against the live store
  (`SourceFileLoader` on `kernel/judge.py`) rather than inferring from logs.
