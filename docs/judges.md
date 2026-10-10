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
different thread; the pivot's goal is then its own card with `pivotFrom`
provenance; the structural tie retired with containers, T101). A segment opened by an
untargeted kernel notice (restart or resume) carries a housekeeping note:
pure verification sweeps file nothing. Since 2026-08-25 that is also a
mechanical floor, not just a request: a work-run whose segment was opened by
romp's own bookkeeping (a kernel notice, or the CLI's `[Request
interrupted…]` stop artifact) never mints a fresh top-level goal (its
menu-targeted ops still apply, so the work keeps advancing existing cards),
and no mint anywhere roots its promptUuid at a record that files nothing (a
coordinate/question mail, a bookkeeping record): the anchor substitutes the
segment's first assistant atom. The clear wrap-up is exempt: its one
blocked card is the designed needs-you escape.

A card appears only for work that traces to something the user asked for (the
user 2026-09-10, whose feed filled with cards titled after the workflows their
sessions ran on their own). The planner labels every mint `kind`: `ask` (a
deliverable the user's message asked for) or `process` (work the session started
for itself: a review round, an audit, a workflow or agent it launched), and the
rule at minting time trusts the labels: an ask keeps its card, on both the
unplaced and the already-placed path; a process mint nests as a step under the
goal the turn ran in (the seam's own top, the segment's placement, this reply's
ask, else the open top nearest in words), carrying `born` (`{kind: session, via:
workflow | agent | work, why, parentText}`), with the matching background launch
(a Workflow run, or an Agent or Task with `run_in_background` or an asynchronous
ack; a foreground subagent is no launch) supplying `via` and the why; with
nothing to nest under it files nothing and the ops chained onto it go with it. A
segment whose trigger is not a human ask (a seam tail a completion notification
woke, an autonomous stretch) treats every mint as process; a scheduled or
programmatic prompt is left untouched. A missing label is filled by the words
alone: without a launch a mint is an ask; with one, every unlabelled mint is
process except the one nearest the user's own words (its text against the
message, never its why or its position), and only among the mints no launch
fits better; when all read like a launch and a top exists, all nest. A harness
report (a background task's completion, a system reminder) or a teammate's line
never appears under USER ASKED in the text the judges read and never roots a
mint. `_demote_session_mints`, `_seg_launches`.

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
"Delegation reports" section; before that, a delegated ask's only visible
history was the dispatch, the closer correctly omitted, and the look-stamp
sealed a finished question open forever (the auto-nudge then re-asked it
seven times in 75 minutes). Two guardrails ride the same fix: the closer
never completes a "delegated to" tracking item itself (its ending event is
the recipient's completion; a dispatch-time done consumed the slot and
starved the report), and on a status-reporting turn (nudge / follow-up /
wrap-up) every open working top rides the audit (the cited-umbrella
descendants channel served containers and retired with them, T103: a
once-stranded leaf is its own top now and rides the plain channel).
It audits only the goals the turn actually touched;
verdict done, blocked, or omit, with "when in doubt, omit". Idempotent per
turn. Its diary events carry src `closer`, so planner and closer verdicts
stay distinguishable, and both defer to the user floor: a verdict computed
from evidence at or before your last reply loses.

A block addressed to a peer is a peer wait, not your needs-you (the user
2026-09-10, via the philosophy: waiting on a peer or another session is not
you being the bottleneck). Both judges file blocks through one writer that
reads the addressee from evidence, never from words alone: the session's own
open question to a live peer (the wait graph's source), failing that the peer
that delegated the work the block sits under (the courier-planted top's
origin when present; else the sender of the delegate mail the goal's anchor
names, the primary record, and only for a top the latch has read as a machine
record: a goal you typed keeps its blocks, a goal split out of one you typed
inherits that, a goal split out of a delegated one reads its own record (a
typed step stays yours, a system-record step is the manager's again), a
delegate mail with no message id sustains nothing, and a script mailer's
pseudo-sid is never a peer; a goal whose
anchor names no dispatch, or whose stamp the latch has not written yet, falls
back to the newest delegate the session received before its mint whose own
goal, latched or courier-planted, was still open at the mint, so a finished or
goal-less dispatch never claims your later decisions and a stray hand-off note
never displaces the manager's standing one), and words only to pick among
several open asks; a block on a
"delegated to <peer>" tracker waits on that peer, whose report ends the
delegate edge. A block in a
delegated goal whose text names you still goes to the delegating manager, who
relays; a worker's card reaches you only through the debt ladder's escalation
event. The write is the existing awaiting-a-peer stamp in place of the block
(the "Awaiting <peer>" chip in Working, the auto-nudge skipping it, the peer's
reply the lift); an already-blocked node is unblocked by romp first. When the
worker never mailed that peer, nothing could end the wait, so the kernel RELAYS
it: the block's why goes to the delegating peer as the worker's own question
(kind question, from the worker, "<worker> cannot move further: <why>", marked
relayed in the row and the header, on a far host too, the row naming the
marker so a send whose record was lost is adopted and never repeated; a why
that speaks romp is scrubbed to the question, and romp's own procedural whys
ride as the plain lead-in alone; under the question rides the conversation it
ends, quoted whole inside a fence: whole turns, the question's own always,
earlier ones newest first while they fit the bound, shown oldest first with a
line saying how many were left out, the user's prompts and the worker's
replies with tool calls collapsed to a count and code blocks never cut, a turn
with no paragraph break keeping its last lines, read newest first only as far
as the bound reaches; the bound is 24 KiB as the bus carries it (JSON-encoded UTF-8) by
default, a knob at ~/.config/romp/relay-context-bytes or
$ROMP_RELAY_CONTEXT_BYTES, read at call time and capped at 768 KiB, so you
raise it without a release), once per block off a marker the judge
leaves on the node (each marker has an identity, the block's evidence time
and the peer, that its queue entry and the record settling it name, and the
node remembers the markers it settled, so a block filed again after a lift is
a new marker nothing older can settle, an ended wait's unsent marker is never
reused for a new wait, a holder stale across two relays never re-mints the
first, and two holders filing one wait mint one marker) and an entry the
saver holding the store writes to a queue directory once its own publish
carried the marker (one file per entry, so the two writers never rewrite
each other's list; another holder's save of the same session flushes nothing
of it; an entry whose node carries a newer marker is rewritten for it; an
entry whose marker is gone with no record is spent once the store's
published revision passed the entry's; the boot pass re-queues a marker that
lost its entry; a pass that changed nothing is not repeated until the store,
the log or the entries move), so the reply lifts the stamp and the reminder
ladder covers it; a wait that ended before the tick is never relayed, a relay
the bus handed to a far host (parked, or in flight to a host that is up)
stays pending by its id up to the far host's delivered row or the peer's
answer (the pending stamp survives every holder's save), and a refusal the
bus cannot retry (no live recipient, a message that came back, with the far
host's reason) reverts the node to your block with the refusal in its why,
since nobody can be asked (the node is read again first, so a wait another
holder ended meanwhile stands down instead, and the block is filed at the
bounce's own time, so a follow-up of yours between the bounce and the tick
outranks it; the refusal is noted beside the block, never in its words); a
pending relay whose wait ended another way is withdrawn from the far host's
outbox, and one the judge retired while it was parked is recalled the same
way (the recall rides its own queue entry beside the marker's, the boot pass
re-queues a node that owes one, and a recall nobody answers is asked once per
hold for a while and then once per half hour, said both times; every queue
entry carries a token of its own that the spend's re-read compares, so a
fresh entry flushed over the path during a pass is never taken for the spent
one; a question the far host carried on before it could be withdrawn, or one
the host could not be reached to withdraw, leaves a note on the node that the
brief's owed why carries and the card and the modal show as their own line
under the brief, dropped when the node's wait next settles); a parked
question completes only on the far
host's delivered row, never on a later message; a refused relay's note reaches
the card's brief beside the question it could not carry; a dead worker's block is not relayed; each record lands
before its entry is spent; a send the bus answered late is never repeated
(the bus answers the send it holds, and the tick holds off after an unknown
outcome and reads the bus's row). A block filed again after the
peer's reply ended the wait relays again; a block re-asserted on a standing
wait never does. Your own follow-up on a delegated card, newer than every edge the block
could wait on (the delegation, a standing wait up the card, the worker's own
open question, a handoff), keeps its block yours. A top is attributed to the
delegate mail its anchor names (the delegate-kind marker of the delivery that
is a dispatch to this session, never one quoted from another session's, so a
batched inbox whose first mail is a peer's heads-up still belongs to the
manager whose dispatch follows it; a stamp naming no such dispatch leaves the
newest delegate whose own goal is open at the mint as the fallback, a row
with no message id never), so a worker two managers dispatched relays each
block to the manager that asked, and a dispatch handled without a goal, or
finished, claims none of the session's later goals; a goal split out of a
goal you typed inherits your anchor, one split out of a delegated goal reads
its own record, so a typed step stays yours and a system-record step is the
manager's again.
An open question to a peer the block never names does not capture a block in
the delegator's work: that block goes to the delegator, relayed. Rows filed before the rule convert once per boot. The debt ladder judges a debtor's
reminder only at an idle turn end (the nudge walk's own gates); for a manager
debtor the record also stands while delivered mail waits unread in its inbox,
so a manager with worker mail queued is not yet failing to answer. Any other
peer keeps the ladder as it was, and the dead-man backstop still applies. A block nothing
resolves to a peer stays yours, exactly as before.

**unblocker.** The stale-block backstop; it exists because answers arrive
in passing and work overtakes asks. A goal blocked on a question is only
ever unblocked by work filed on that exact node, but the answer usually
files wherever the planner judges the segment to serve, so a dormant
blocked goal never hears it and holds its card in Needs you (a card can
otherwise sit for hours on a buried sub whose question the very next
stretch of conversation already answered, or on an approval whose work
the session then visibly did anyway). Given each open blocked goal's
question (subs and tops both) plus two evidence sections (the
conversation since its block, and the goals the session has completed
since then with why each counts as done) it verdicts lift or hold, "when
unsure, hold"; a lift lands as a normal `unblock` diary event,
why-prefixed "answered in passing". The completed-since section is the
durable half: the conversation tail scrolls past its 9k-char window and a
hold is never re-examined against the same turns, so an ask superseded by
later completions used to rot in Needs you until cleared by hand (the
2026-08-08 study: 400 card-hours across 302 manual clears). Event-gated
per node on both streams: `blockCheckT` remembers the newest ended turn
examined (turn-time domain; the feed's re-judging latch reads it against
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

**Distiller notes.** Every judge that writes prose you read (distiller,
briefer, staller, captioner, gister, archiver) also carries your standing
style notes, when you keep any: `~/.config/romp/distiller-notes.md` is read
at call time (no restart needed) and appended to the prompt, notes winning
over prompt defaults on conflict. Plain language, e.g. "never cite PR or
commit numbers; say what the change does". Delete the file and the next
call runs bare. The placement judges and the courier never see it: those
emit verdicts and agent-directed copy, not prose for you. The path is a
plain read that follows symlinks, so the durable setup is the content in
your dotfiles with a symlink here; one edit then reaches every machine's
judges through your normal dotfiles sync (the user 2026-08-15), instead
of each kernel keeping its own hand-seeded copy.

**briefer.** When a top card blocks (and live for the focused picker or
permission goal): a decision brief that leads with exactly what you must
decide or provide, then options and tradeoffs. Same `SOURCE:` contract as
the distiller.

## Board shape: the grouper and the consolidator

Since 2026-08-26 (T101, the user's ruling) the board's unit is the
INDIVIDUAL ASK: every top-level goal is its own card, tops never nest under
other tops, and container ("umbrella") goals are retired: a store-level
container is unavoidably a tracked unit (it owns rollup and, measured in the
provenance audit, swallowed the chain evidence of every stranded ask), while
the visual-grouping job belongs to the feed's display-side group fold, which
has no store footprint. Both judges keep only the housekeeping that serves
the ask-unit rule, move whole subtrees, and append no diary events:
structure, never status.

**grouper.** Given the open top-level cards: merge true twins into one line,
split a drifted tangent out to its own card, retitle a card its thread
outgrew, and "doing nothing is a valid, common outcome". Called only when
the open-top set actually changed. Hard rules in `apply_group`: never touch
a view-cleared card, same-session only; the retired `mint`/`group` ops are
parsed away and ignored if hand-built. A to-do-mirror top that duplicates a
line already inside another card is explicitly the grouper's to merge.

**consolidator.** The same prompt over the completed column, now merge/
retitle housekeeping only. Legacy umbrellas from either judge DISSOLVE in
every writer's rollup (the pre-pass beside the handoff-children lift):
children re-parent to top level with their own provenance intact, the empty
container leaves the store, and placements that pointed at it retire:
idempotent, self-healing against save-rebase republishes.

## Peer mail: the courier

The courier owns peer-message segments; the planners skip them. The sender
declared each message delegate, coordinate, or question at send time
(schema-required); the courier takes that as a strong prior and reads the
body for whether work actually changed hands. Since 2026-08-25 minting is
CHAIN-ROOTED (the user's verdict, replacing a one-day view-side split):
delegating plants a real goal in the recipient's tree (origin-stamped) only
when the sender's linked goal traces to a human prompt (self-then-ancestors
in the sender's store, origin hops into a local grand-sender's chain, the
root record read against the sender's own session) AND no ask card already
exists: since 2026-08-26 (T101) a dispatch whose chain roots to an ask the
courier LINKED (the sender's ask node) never mints a recipient top; the
tracking node plants under that ask, fan-out lives inside the ask card
(several dispatches, one card; several asks to one worker, several cards),
and the recipient files quietly with the reply-sweep ending. Only a rooted
dispatch with no resolvable ask node still mints the recipient top: there
the recipient card IS the ask's card. An untraceable delegate
files quietly instead: no recipient top (its work lives in that session's
view and transcript, and a needs-you state still surfaces through the
goal-independent hard-block floor), while the sender's "delegated to"
tracking node plants either way, so the delegation stays one glance away on
the sender's board. At mint time uncertainty files quiet; the burden of
proof is on the mint, the inverse of a display filter's. Coordinating makes
no card, ever. A planted goal also stores the delegating mail's cleaned
first line as the additive node field `frame` (2026-08-25, part of the
goal-node consumer contract): the distiller and briefer prepend it, with
the sender's linked-ask title, to their prompts as a marked
`<delegating-request>` section. One hop down a team that framing is a
MANAGER's restatement in implementation nouns, so the trace's root record
(the human prompt the chain proved: text plus sid, returned in place of
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
recipient goal can ever back-link) completes on the recipient's reply, the
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
  never burn a retry cap, with three exceptions, each a call the same item
  would fail again identically. Two are the closer's, both adopting a turn
  loudly on their own event: a safeguards refusal of the turn's content,
  and a *killed* call (one the timer ended; never an API error or a process
  that ended any other way), each counted against the turn it happened on.
  The third is the planner's work run (2026-09-23): a content refusal of the
  segment (the safeguards flag or the classifier-stop envelope,
  `_REFUSAL_ENVELOPE_RE`) is struck against that segment's
  `PLAN_PARSE_RETRIES` counter and resolved at the cap as a parse reject is,
  a user message hard-placed and a non-user segment dropped, the `give-up`
  row naming the refusal. A transient failure (a rate limit, an auth blip,
  a dead CLI) still counts nothing. The kill streak is described below.
- **Every judge is capped.** Three genuine parse rejects on the same work
  item (`JUDGE_FAIL_CAP`) and the judge gives up loudly, one `give-up` row
  naming the re-arm event, instead of retrying every pass forever:

| Judge | On failure | Re-arms |
|---|---|---|
| opener, live re-plan | hard-places at card level immediately | (no retries needed) |
| planner (work run) | 3 parse rejects or content refusals, then hard-place a user message / drop a non-user segment | on its next segment |
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

A judge call bills **the account of the session it judges**: the same pick the
session's own Billing selector holds, read from the same registry, resolved by
the same rule the launch uses (the kernel wires the backend's resolver into the
judges): the session's own pick; else the machine's default set in the tab
menu's Billing flyout, when the machine can bill it; else the API key when
Claude Code's settings carry an `apiKeyHelper`, else the login. Every judge child
(`claude -p`) launches with the ambient credentials stripped from its environment.
A key-billed call gets the key the judge process holds (the user 2026-10-05): it
runs the user-settings `apiKeyHelper` once, keeps what it printed in memory, and
hands it to each key-billed child as `ANTHROPIC_API_KEY` with
`--settings '{"apiKeyHelper": ""}'`, so the child runs no helper of its own (one
helper run for every judge call, where each call used to be one secret-manager
read). When a child answers with a credential error, the process runs the helper
again, at most once a minute across every judge thread, and retries that child
once with the new value; a retry refused again makes the next key-billed calls
stand down (a skip, latched as below) until the minute is up. A helper that fails
files the same latch with the helper's own words, writes one line to the service
log, and spawns no child. A helper set in managed settings, which the per-call
layer cannot turn off, keeps the old road: each child resolves it itself. The
held value is never written to a file, a log line or the kernel's environment.
A login-billed call passes `--settings '{"apiKeyHelper": ""}'`,
which disables the helper for that one process, and gets back the login tokens
the kernel claimed out of its own environment at boot. A call billed to a
stored login passes the same suppression and gets that login's setup-token
instead, read by running the record's token command for that one child (the
environment road, 2026-09-14; a failing command fails the call in its own
words, never a fall onto another credential). The same selection
applies to standalone `romp-judge --once`. A failing credential cannot silently
use the login or a stale key; what the call files depends on how it fails: a
credential error the CLI reports within the call's 120 s alarm latches
judge-auth-down below, and a call the CLI never answers (a helper that hangs
inside a child that still runs one, or a rejected key the CLI keeps retrying) is
killed at the alarm and files as a timeout row. See [Service environment and
credentials](reference.md#service-environment-and-credentials) for the helper's
setup and the billing declaration.

A **credential-class** failure (not logged in, an invalid key, an expired OAuth
token) is one no retry can fix; only the user can. The first such error
envelope latches judge-auth-down for that session (`STATE/judge-auth.json`), and
the session's next successful call clears it; both edges are events, nothing is
re-derived per build. While latched, the feed floors the session's focus card to
needs-you wearing a filled-red "Can't analyze" chip that names the refused
credential, and the card face carries the story and the fix: the session may be
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
  folds it into the sender's ask card at render: fan-out inside the ask
  card, with needs-you breaking through (T137). A dispatch-less step
  threads the session's own prompt record instead. The grouper may still
  merge a duplicate mirror.
  It reads the live task store (`~/.claude/tasks/<fsid>/`, the same source
  the chat TO-DO card reads), never the transcript, whose record of a
  TaskUpdate can fall off the live chain when an api-error retry forks the
  graph. A missing store falls back to the transcript fold; an unreadable
  one logs a `task-store` row and skips the pass rather than silently
  degrading.
- **auto-nudge**: a kernel trigger, not an LLM. Detects a genuinely stalled
  session and injects one nudge prompt; the planner's nudge phase does the
  judging, and a failed nudge records the block.
- **awaiting**: layered. The LIVE sources are event-derived (subagents,
  the pending background-task set, the delegation graph) and the CLOSER files a
  durable awaiting verdict (the goal store's ⏳ stamp) carrying a KIND naming
  what the wait is on: agents, task, job (an external computation), peer, timer.
  The kind scopes the rules: a peer's answer supersedes only peer waits, and a
  job stamp survives its watcher dying. The wake's clock is a DEAD-MAN'S SWITCH
  for waits whose ending romp cannot observe: kind=job (external compute),
  cross-host peers, legacy kindless stamps, hung-forever agents/tasks, and
  prose-declared timer check-backs; every observable ending (a notification
  pairing, the restart epoch, a tool's declared deadline, a peer's answer or
  death) retires its wait as an event, with no clock at all. A task/job stamp
  whose launches the planner placed under another card lifts the same way once
  the session's live registry is empty and the last in-harness item ended after
  the stamp. The dead-man runs from the pusher whether or not auto-nudge is on;
  with it off, a due wait files its lift (no injected check-in) instead.

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
  Fast mode per judge tier (the gear's Fast mode box beside each tier's model
  picker): `STATE/judge-fast` (triage), `STATE/distill-fast`, `STATE/index-fast`
  (`on` | `off`, off by default; read per call for the call's tier, and the
  fast-mode opt-in rides only a call whose model is Opus: `_tier_fast`). A flag
  on for a tier whose model cannot run fast is kept and asks nothing. The CLI's
  refusal of a fast ask is recorded per tier in `STATE/fast-refused.json` (one
  `fast-refused` judge-errors row per change of reason) and cleared by the next
  fast call that engages; a key-billed fast call carries the sessions' org-check
  env (`_fast_org_env`, asked once per process). The one-time carry-over from
  the single flag is `_migrate_judge_fast_tiers` in the kernel's boot sweep.
  Pool width: `STATE/judge-concurrency` (the gear's Judge concurrency, 1..16,
  read fresh each pass; empty = `ROMP_JUDGE_CONCURRENCY` as read at load,
  else 6). Every pool reads it at call time (`_conc`, or `_judge_concurrency()`
  directly); `DEATH_DRAIN_PER_PASS` alone stays on the load-time value.
- Skips: the six triage tiers (planner, closer, unblocker, grouper,
  consolidator, distiller) run behind an evidence gate. Before a session is
  submitted, the runner takes the tier's signature: the identity (inode,
  mtime, size) of every file the tier's decision path reads, the store with
  its journal and archive among them, and for the planner, closer and
  unblocker the pass's pinned parse pair. A session whose signature equals
  the one the tier stamped after its last complete run is skipped, and its
  pass watermark is stamped as for a pass that found nothing to do. A run
  stamps only when it returned normally and set no completeness bit; a
  deferral without a write, an empty reply, a failed call, a raise, or a side
  file that exists and did not read leaves no stamp, and the session runs
  again next pass. The stamps are process state, so the first pass after a
  restart is a full walk. The planner has a second gate inside
  `_plan_session`: a session whose inputs have not moved since a pass that
  placed nothing, left the store's key where it was and ran to completion
  returns before the store read. The evidence gate keys on the same files by
  identity plus derived values the inner key does not read (the reg's
  `spawnedAt` and backend, the stall slice's value, the task-store
  fingerprint), so an idle session stops at the evidence gate; the inner gate's counters
  (`memos.plannerSkip` on `GET /perf`, see `docs/reference.md`) count only
  the sessions the evidence gate ran. Outside
  a pass frame (`romp-judge --plan`) the evidence gate stamps nothing, and
  the inner gate does the skipping.
- Logs: `STATE/judge-usage.jsonl` (per-call cost, one name per prompt, the
  CLI's `fast_mode_state` as `fast` and its `fast_mode_disabled_reason` as
  `fastReason`; an error envelope that carried the readback leaves a zero-cost
  row marked `err`, which the cost rollup skips),
  `STATE/judge-errors.jsonl` (the row contract above; kinds are parse,
  call, give-up, sweep-cut, cite-miss, rate-limited, task-store, history-unreadable,
  task-key-collision (a duplicated to-do mirror key, reconciled per node
  and surfaced loudly), store-unreadable: a goals file that cannot be read,
  filed once per fault episode and ended by the next successful read,
  store-unwritable: a goals file whose publish failed under a user gesture,
  store-quarantined: a goals file whose bytes did not parse, moved aside,
  frozen-store-write: a read-only site wrote to the shared store view, naming
  the site, after which the shared cache is off for the process,
  frozen-store-save: a shared store view was handed to `save_goals`, refused,
  clears-log: a write to the clears log refused (a clear's or an undo's rows, the
  mute's, the episode boundary's), one row per refusal, owed-note: the note of owed
  cards beside the log could not be written or read, one row per gesture and one
  per episode on the feed's own read,
  unroll-heal: a top left rolled up with settle rows and no done in its
  diary, given one reopen row so it can be judged again, gate-stamp: the
  evidence gate could not write a tier's stamp after a complete run, so the
  session stays due, and the eight `*-unreadable` kinds of the gate's side
  files, states-unreadable, cleared-unreadable, stall-unreadable,
  captions-unreadable, episodes-unreadable, marker-unreadable,
  archive-unreadable and reg-unreadable: a file the gate stat'd or read by
  value into a tier's signature exists and could not be read or parsed, so
  the gate runs the stage without a stamp (or the stage's own read marks the
  run incomplete), one row per failure episode; cleared-unreadable is filed by
  the kernel's own reader of the clears log as well, the Undo reading a present
  log that cannot be read or is not text as nothing to bring back and the feed
  build holding the last landed set meanwhile with one refused bell row per
  episode, one row per fault episode ended by a landed read, an absent log or a
  different fault).
  A file that does not parse is never deleted: it is moved beside its path as
  `<file>.corrupt-<utc stamp>` (a `-n` suffix when two land in the same second)
  before a fresh one is written, so the bytes survive for inspection, and the
  `*.json` globs that enumerate stores skip it; the same sidecar convention
  applies to any other state file romp moves aside as unparseable.
  `STATE/judge-auth.json` (the per-session judge-auth-down latch; see
  "Billing" above).
- Debugging: run the judge's own code against the live store
  (loaded by file path from `kernel/judge.py`) rather than inferring from logs.
