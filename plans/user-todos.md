# User todos — what a session needs from you, held until you or it says otherwise

**Status: built on the fork (2026-08-22); design settled with the fork's author 2026-08-20/21; upstream
acceptance of the default, vocabulary, badge semantics and escalation is open in romp-on/romp#993.** Landed in the commits of the
user-todos change: the store and the two postal tools, the split card with Reply and Dismiss, the
delivery-keyed answer, the tab flag and the feed marker, the idle-escalation floor and the badge,
the SessionStart hook, and the per-install switch. The decisions recorded here are the plan of
record, and each build slice at the bottom carries its as-built notes. File:line references
describe the design-time tree and are not current (see `plans/README.md`); the slices' own tests
are the current map. Companion records: the Vocabulary section at the end of this document and
the authority-tier decision in `docs/adr/0001-user-todos-authority-tier.md`.

## The problem

A session hits a point mid-turn where one strand of its work needs the user — and says so, once,
in passing: it can't do X without a decision, an input, or an action only they can provide, so it
notes that and continues with Y and Z. The session keeps looking busy — the transcript scrolls,
the card sits in Working with a gold pip — and X vanishes. The user learns about it days later,
re-reading a transcript, or never (the user 2026-08-20, describing the recurring shape). The lost
items come in two kinds, and both matter:

- **Partial blocks** — one branch is stuck on the user while the rest proceeds. In the demo
  world: the `api` session of `notes-api` needs a decision on the auth scheme before it can wire
  login, so it builds the unauthenticated routes meanwhile.
- **Non-blocking wants** — nothing is stuck *yet*, but the agent needs something eventually: a
  test credential, an opinion on a naming choice, a review of a draft it can keep polishing.

Today the second kind has no representation at all, and the first kind has one that erases
itself.

## Why today's machinery can't see it

Three stacked holes, traced end to end at design time:

1. **Mid-turn, the ask is invisible by construction.** Judges rule on ended segments only
   (`run_triage`, `kernel/judge.py`), and a segment splits only at genuine new *inputs*
   (`kernel/event_model.py`) — so an autonomous work turn is one segment, and the
   "I can't do X" sentence sits buried mid-segment behind the Y/Z work that follows it. While
   the turn runs, the card shows Working with open-turn narration.
2. **At turn end, the block usually never files.** The planner's block op requires the segment
   to *leave* a decision owed; a segment that ends on continuing Y/Z work reads as progress and
   files as `sub` ops. There is no verdict shape for "partially blocked" — a node is blocked or
   it isn't.
3. **When a block does file, the system erases it.** Every planner placement runs
   `_unblock_branch` — an unblock on the placed node and its whole ancestor chain, why = new
   work filed on this branch — so a block on the card Y/Z files under dies on the next judged
   segment. A block on a *sibling* sub survives that, but the unblocker re-examines every open
   block whenever a newer turn ends (`_unblock_session`) and its prompt licenses lifting when
   the work visibly moved past the question even though nobody typed an answer (`UNBLOCK_SYS`).
   The repo documents this erasure class about itself: the `WHY_UNBLOCK_UNSETTLED` design note
   in `kernel/judge.py` records a blocked-on-the-user goal repeatedly flipped back to working
   by "new work filed" / "answered in passing" rulings while the user's decision was still
   outstanding — and `_mark_nudge_failed`'s moot guard in `kernel/kernel.py` exists because of
   the same weekend. After a lift there is no residue: the decision brief is a blocked-card
   surface and clears with the block. The nudge ladder can resurrect the question much later,
   but only once the session goes *idle* with a still-working card (`_auto_nudge_tick`) — while
   the session stays busy on Y and Z, nothing fires.

None of this is a bug to patch in the judges. They infer from transcript text, and inference
over "I'll note X and continue" will keep failing in both directions — filing blocks that
aren't real, and lifting ones that are. What's missing is a channel where the agent *states*
the need explicitly, and where nothing that reasons by inference is allowed to clear it.

## The design

A **user todo**: a first-class object a session registers when it needs something from the
user — a decision, input, or action only they can provide — while it keeps working on
whatever else it can. It stays visible until one of exactly three events clears it, none of
them a judgment.

### Name and scope

"User todo" is the user-facing and glossary term (the author's pick over "ask", 2026-08-20).
Internal identifiers use `userTodo`/`user_todos` and never `ask` — the feed payload's existing
`asks` field is the card list itself, and a second meaning of the word in the same payloads
would be a collision. Scope is gatekept by the tool description, not by any classifier: partial
blocks and non-blocking wants both qualify; status updates and FYIs never do — those the user
already sees.

### The channel: two postal tools

Registration rides the postal MCP server (`postal/postal_service.py`) — the one tool surface
every romp session already holds, SDK and tmux alike, wired at the session level with nothing
romp-side restricting subagent inheritance. Two new tools:

- **`add_user_todo`** — required `text` (one short line: what's needed and why), optional
  `detail` (longer context for when the short line can't carry it). Returns a stable id
  (`ut-` + 8 hex, minted kernel-side).
- **`withdraw_user_todo`** — takes the id; stamps the todo withdrawn. Withdrawing an id that
  is not open returns a loud, plain answer that says what happened, never a silent success, and
  an error only when the id is not yours or unknown: a todo the person already answered or
  dismissed, or one the session already withdrew, is the need met, not a failure (2026-09-07;
  the route's `state` / `at` / `owner` fields carry the account the tool words).

The descriptions follow the veil (sessions don't know romp exists): they speak of "the person
you work for" and name no romp machinery. The shipped wording, pinned by
`tests/test_injected_voice.py`:

> **add_user_todo** — Flag something you need from the person you work for — a decision, an
> input, or an action only they can provide — while you keep working on what you can. Give one
> short line saying what you need and why; add detail only if the line can't carry it. Returns
> an id: withdraw it (withdraw_user_todo) the moment the need is met or moot. Not for status
> updates or FYIs — only things you are waiting on them for.
>
> **withdraw_user_todo** — Take back a need you flagged (by id) once it's met, answered some
> other way, or no longer applies — so the person you work for doesn't act on a stale request.

Construction is `set_working`'s exact shape: one `MCP_TOOLS` schema entry + one `_mcp_call`
branch each, backed by kernel routes (`POST /usertodo`, `POST /usertodo/withdraw`) the way
`_publish_working` posts to the kernel's `/working`.

**The subagent caveat, documented rather than fixed:** postal identifies its caller from the
CLI-process environment (`CLAUDE_CODE_SESSION_ID`), so a subagent calling the tool registers the
todo *as its parent session*. That is the right behavior — the need belongs to the session the
user talks to — it just means "who filed this" is always the session, never an individual
subagent, and the docs say so.

### The store

A sid-keyed JSON blob under STATE, following the `notify-cards.json` / `session-flags.json`
idiom (mtime-cached read, `_atomic_write(..., sort_keys=True)` publish):

```
~/.local/state/romp/user-todos.json
{ "<sid>": [ { "id": "ut-9f2c1a34",
               "text": "Need the auth-scheme decision to wire login — building the open routes meanwhile",
               "detail": "…optional longer context…",
               "createdT": 1755741000,
               "resolved": { "kind": "answered", "t": 1755749200 } } ] }
```

Open = no `resolved` key. Resolution *stamps* rather than deletes — `kind` is one of
`answered` / `dismissed` / `withdrawn`, so the record carries its own history (created,
cleared, by which event). Kernel-side, so it survives session death and kernel restarts alike.
Rows leave the file by exactly two paths, both touching RESOLVED rows only (as built): the
per-sid resolved-history cap at stamp time (`_USER_TODO_RESOLVED_KEEP`, the newest 64 stay —
enforced at the one choke point where every resolved row is born), and the prune that drops a
dead session's resolved rows once its death is corroborated (`_prune_user_todos` — a durable
death record, never a display-set miss). An OPEN row never leaves the file at all, whatever
the session's state: only the three clearing events below can resolve it, and only a resolved
row is ever capped or pruned. Live data only under STATE, never in the repo, as ever.

### Lifecycle: an authority tier

Exactly three events clear a user todo:

1. **The user answers** — the reply affordance on the todo (the split card, below). The reply
   is injected into the session as a message from the person the agent works for —
   injected-voice rules apply, no romp nouns — anchored to the need it answers so a terse
   reply lands unambiguously: the todo's own short text as a prefix (`Re: <text> — <reply>`),
   pinned by `tests/test_injected_voice.py`. The stamp is **delivery-keyed, not gesture-keyed**
   (as built — the interview's "stamps at the send" undercounted the ways a send comes undone):
   an answer a live backend accepts stamps on the truthy send; an answer parked for a dormant
   session stamps when the parked op drains; and an answer whose delivery comes undone
   **reopens** the todo. That reopen class (`_reopen_user_todo`, the one un-stamp) covers
   exactly two events: the recall of a still-queued answer (the user pulled it back), and the
   corroborated loss of its holder (`_user_todo_answer_lost`; `_user_todo_loss_boot_pass`
   re-offers marks whose reopen a kernel death cut short), which reopens unless the transcript
   proves the text landed. Every leg keys on a delivery event — never a judgment.
   On tmux the truthy send is not the delivery: `TmuxBackend.send` starts the paste on a thread
   whose clear-guard can still refuse it, so an answer's send persists a pending-paste mark
   before returning, the paste thread's verdict reopens (refused) or clears the mark
   (delivered), a refusal that outruns the stamp flips the mark and the stamp stands down at
   its own write moment — matched by the send's nonce, so a stale flag never stands down a
   later delivered answer — and `_tmux_paste_loss_boot_pass` re-offers marks a dead kernel left.
   The Codex backend advertises neither capability (`queue_carries_todos`, `send_reports_refusal`),
   so an answer to a Codex session takes the plain two-argument send and stamps at the truthy
   return, with no recall or loss seam behind it.
2. **The user dismisses** — clears it without a message; nothing is injected. For moot and
   stale items.
3. **The agent withdraws** — via the tool, when it got what it needed some other way or the
   need evaporated.

**Judges and the unblocker get no vote.** Nothing in `judge.py` may write this store — a
grep-provable invariant, and the whole point: every eraser in the three holes above acts by
inference, and this object exists to survive inference. The precedent is the agentTask tier:
`open_task` nodes mirrored from the live task store trump a judge/rollup done because we trust
the agent's declaration over inference. User todos are the same move for the other direction of
obligation.

The deliberate, stated cost: an agent that forgets to withdraw leaves a moot todo sitting
visibly until the user dismisses it. Accepted — a stale visible todo costs one glance and one
click; a silently vanished ask costs whatever X was. `docs/adr/0001` records this trade-off.

### Withdrawal support: how the agent remembers

One mechanism is rejected outright, not deferred: **idle check-in turns**. A scheduled "do you
still need all of these?" turn was weighed and refused (the user 2026-08-20) — at idle the
common truth is that everything registered is still waiting, so check-ins would burn a turn
per session per idle to say nothing. Three passive mechanisms carry the load instead:

1. **The tool description instructs withdrawal at registration time** — the agent learns the
   contract in the same breath it files the need.
2. **Open todos ride into the contexts the agent naturally receives.** On SessionStart after a
   restart or revival, and after compaction (the SessionStart hook family covers both sources;
   built as `hooks/romp-usertodo-context.sh` — a passive context block, no forced turn),
   the session sees its open todos phrased as its *own* outstanding notes to the person it
   works for, with ids and an invitation to withdraw any that are met or moot. The wording,
   veil-compliant, pinned by the voice test:

   > Notes you still have open with the person you work for — things you said you needed from
   > them:
   > - Need the auth-scheme decision to wire login (ut-9f2c1a34, opened 2026-08-20)
   >
   > If one is met or moot now, withdraw it (withdraw_user_todo); otherwise leave it standing.

3. **The user's dismiss covers the rest.**

### Escalation: the idle endgame

While the session still works, an open todo changes no card's column — the session is not
waiting on the user, it told them so. The one earned move: **when the session goes idle with
open user todos and nothing else dispatched — no open turn, no background work awaited — the
todo IS the session's frontier**, and its card escalates to Blocked (needs-input).

Mechanically this is a read-side floor in the `perm_top` family, NOT a judge verdict: a
verdict would land in the diary the unblocker examines and could be lifted like any other
block. A session with no open card gets a needs-input placeholder, the same way a goal-less
permission prompt does. Both directions are event-keyed, per the card-move rule: the floor arms
on the turn-end/await-drain that empties the frontier while a todo stands, and stands down when
a todo clears or the session starts new work (a message arrived, the user acted) — each a real
event, never a per-build re-derivation from a flapping proxy.

**The status nudge stands down for a session whose idle is already explained by open todos**
— the same reasoning as the no-check-ins call: the todo already says what a nudge would fish
for, and the escalated card, not a manufactured turn, is the surface. Scoped to the
status-nudge branch ALONE (as built — the first cut stood down at session level and silenced
two unrelated ladders): the awaiting WAKE flows past an open todo, because it is the 6h
lost-wakeup backstop for dispatched background work, not a status ask; and the DEBT machinery
flows past too, because it is the one mechanism that unparks a peer silently waiting on this
session's answer — a todo names what this session needs from the user, and says nothing about
what a peer needs from it. The stand-down lifts the moment the last todo clears: answer,
dismiss, or withdraw, each a real event the gate's store read sees live.

**The peer-wait stand-down is local-host only — a known limitation, documented not fixed
(2026-08-22).** The floor reads its peer-wait input from `_wait_for_graph`, which keeps an edge
only when the awaited peer is in THIS kernel's alive set: an unanswered ask to a FEDERATED peer
(a relay-addressed `peer:<host>` row) makes no edge, so a session idle on a cross-host reply
still floors as needs-you. The waitingOn chip and the auto-nudge tick's skip inherit the exact
same scope — all three read the same graph, deliberately. Building cross-host wait edges is
`_wait_for_graph`'s job when it happens: widening it there lifts every surface at once, while a
floor-only special case would fork the wait derivation. Pinned by
`test_user_todos.py::PeerWaitScopeIsLocalOnly`, so any future widening flips the floor's
expectation consciously alongside the chip's.

### Surfaces

**(a) The split card by the composer.** The existing transcript-bottom to-do checklist card
(`kind:"todo"`; renderer `renderTodo` in `ui/webview/render.ts`) becomes a shared card with
two sections — **the agent's plan** (the existing checklist, exactly as today) and **waiting
on you** (open user todos, newest last), each auto-hiding when empty, so today's behavior is
unchanged when no todos exist. Each todo row carries its short text (detail behind the
existing disclosure idiom — and when there IS detail, the row wears a small "▸ details" hint
after the text, so a bare one-line ask and one with more behind it read differently at a glance;
a bare ask renders nothing extra), a **Reply** affordance (answer path above) and a **Dismiss**.
Two different things share this card on purpose: the agent's plan for itself, and the agent's
asks of you — the vocabulary below keeps the terms apart.

**(b) The tab glyph.** A session tab with open todos carries a small, non-numeric glyph.
Precedent: the per-tab ctx gauge and compacting mini-bar — tabs deliberately carry no counts
today and this stays that way; the glyph says "something here waits on you", the card says
what. Exact character/placement is a build-time UI call.

**(c) A quiet feed-card marker.** Each of the owning session's feed cards carries a quiet
marker (todos are session-scoped, not card-scoped). **No feed strip in v1** — the feed's
banner slot stays single-purpose.

**(d) The app badge.** `_needs_you_count` widens to one number for "things only the user can
move": **open user todos (of non-ended sessions) plus hard-stopped needs-input sessions** —
the permission-prompt class stays in (the author confirmed, 2026-08-20). Dedup rule: the
escalation floor is a *presentation* of todos the count already includes, so an idle session
escalated by its todos adds nothing extra; a session hard-stopped for a non-todo reason
(permission prompt, on-you API error) counts once as itself. Per-item decision cards count per
CARD, not per session (2026-08-22): a parked handoff (deliver-or-dismiss per
send) and a quarantined peer mail (approve/deny/edit per message) are each an independent user
decision, not a state of their session — `_NEEDS_YOU_PER_ITEM` enumerates them from
build_feed's own needs-input constructors, and the per-session dedup was absorbing real
decisions (a permission stop plus two held mails read badge 1). Rides the existing push
(`_badge_push` → the shell WS `{type:'badge'}`, and the service-worker copy).

**Muted sessions — a deliberate asymmetry (2026-08-22).** A `hideFromFeed` mute
quiets the feed and every aggregate built from it — the card marker (c), the idle-escalation
floor, and the badge (d) — because mute means "stop interrupting me about this session". The
tab glyph (b) stays: it reads the chat payload's `userTodos`, which mute does not touch, so the
tab remains truthful about what its session holds. Do not "fix" the glyph to match the feed
surfaces; the split is the point (quiet the interrupts, never lie on the session's own tab).

### Dead and dormant sessions

Two different "not running" states, two different answers:

- **Dormant** (registry alive, no live thread — e.g. after a kernel restart): the session is
  still addressable, its todos show everywhere, and answering one just works — the send path
  auto-revives a dormant session with its history intact (`_ensure` → resume in
  `kernel/sdk_backend.py`).
- **Ended** (registry `alive: false`): the todos persist in the store but **hide** from every
  surface and every aggregate — the split card, the glyph, the marker, the badge. They are
  hidden, not cleared: revive the session (the dashboard's Revive) and they return with it. A
  dead session's asks should neither nag from beyond the grave nor be silently lost.

## Data seams

Three seams, all existing patterns:

- **Chat page**: `build_session` grows a `userTodos` field — open todos only, sorted by
  `createdT`, no per-build values. The client merges it through the upsert's prev-fallback
  pattern in `render.ts`. **The stability caveat, by name**: `_send_client` dedups by comparing
  the serialized payload (the `firstSeen` lesson), so this field must serialize identically
  across builds when nothing changed — or every connected client re-receives its full
  transcript about once a second. The tab glyph derives client-side from the same field.
- **Feed page**: `build_feed`'s return grows a top-level sid-keyed open-count map for the card
  marker, and the escalation floor + placeholder live in the same column mapping the perm floor
  uses.
- **Shell**: no new seam — the widened count rides the existing badge WS.

## Build slices

Each slice ships independently, tests included, per the standing rule.

**Slice 1 — the object and its loop.** Store helpers (mtime-cached read, atomic publish,
sweep on session delete) + kernel routes + the two postal tools + the `build_session` field +
the split card with Reply and Dismiss + the answer injection. Shippable alone: register, see,
answer, dismiss, withdraw all work end to end before any ambient surface exists.
*Tests*: `tests/test_user_todos.py` (store round-trip and stamps, sid-keying, id stability,
sweep, loud unknown-id withdraw, route auth); postal tool-dispatch tests (register returns the
id; caller resolves to the session); `test_injected_voice.py` extended to the answer prefix
and both tool descriptions; UI source pins for the split card (sections auto-hide, delegate
root, payload-stability pin). Synthetic data only (the `notes-api` world).
*As built (2026-08-22):* four deviations from the letter above, none from its spirit. The
answer stamp became DELIVERY-keyed with a reopen class — the lifecycle section carries the
full arc; the todo id travels with the queue entry itself, so recall and loss act on the
entry they hold with no kernel-side table to restart away or evict. The open rows ride ON the
split card's `todo` event, not only build_session's top-level `userTodos` field: the chat
wire's steady state is chatTail deltas, which re-send changed EVENTS only, so a field that
changed with no event change would never reach a caught-up client. Reply is a small MODAL on
the confirm chrome (the need quoted, a box, Enter to send), not an inline input — the card
rebuilds on every push, which would clobber a half-typed box — and ONE kernel op
(`userTodoAnswer`) both injects and stamps, so the two can't diverge. And the ended gate is
build_session's exact corroborated read per backend — the SDK registry's `alive: false`, or a
reg-less tmux sid's durable death record (un-ended by any newer states row), never a raw
listing miss — with the answer op refusing a dead session loudly instead of firing into the
void.
*As built (2026-09-07):* the card's per-row UI state is KEYED by todo id, not held on the DOM
node: Dismiss's armed "Really dismiss?" and the optimistic row removal after Reply or Dismiss
live in Sets, so both survive the card's rebuild while the session streams (before, a push
between the two Dismiss clicks reset the button); the removal also rewrites the "Waiting on you ·
N" heading and drops it with the last row, and the kernel-warn re-sync of the chat view runs only
while this client has an unconfirmed Reply or Dismiss outstanding, not on every warn. Enter in
the Reply box sends on a fine pointer only; on a coarse pointer it is a newline and the Send
button sends, the composer's own rule. The writer bounds a note (`_USER_TODO_TEXT_CAP` 500,
`_USER_TODO_DETAIL_CAP` 4000 characters): over the cap is REFUSED, never trimmed, and the route's
400 and the tool's refusal carry the one-line advice. An unreadable store (a `user-todos.json`
that is not sid → records) is said on every surface instead of read as empty: the card carries an
error line naming the file in place of the requests, Reply and Dismiss warn that nothing was sent
and nothing changed, `POST /usertodo` answers 503, a withdrawal is told the store could not be
read (`owner: null`, never "no such request"), and the kernel log names the file and what it
found.

**Slice 2 — ambient visibility and the endgame.** The feed seam + card marker, the tab glyph,
the widened badge, the idle escalation floor + placeholder, and the auto-nudge stand-down.
*Tests*: floor semantics (idle + open todo → needs_input; working session → no column change;
a turn the user did not open → holds; clear → stands down; ended session → excluded; placeholder
when no card; the real predicate through build_feed, `EscalationFloorLive`), badge arithmetic
(including the no-double-count rule), nudge stand-down, UI pins for glyph and marker.
*As built (2026-08-22):* the stand-down is SCOPED to the status-nudge branch — the awaiting
wake and the debt machinery flow past (the escalation section carries the reasoning). The
floor's predicate gained a PEER-WAIT gate: `_wait_for_graph`'s edge (the same one the
waitingOn chip reads) stands the floor down while a live peer owes this session a reply,
because waiting on a peer is not needs-you — local-host scope only, the documented limitation
above. The floor's OS push gained a LATCH (`_NOTIFY_UT_FIRED`): the card dips to Working when
the user speaks to the session and re-enters the column at the next settle without new
information, so the push dedups on the floored todo SET, not the column transition — a new id is news, an identical set re-entering is not; a LOST
answer's reopen un-latches its id (the re-floor is the one signal the answer never arrived)
while the user's own ✕ recall stays silent, and a restart's baseline seeds the latch from the
already-floored world. Two focus refinements: a done-CONFIRMING top is never floored (its
imminent completion is the settle's to deliver, and flooring it flapped the card with no new
information), and when the focus chain dead-ends in a completed top the floor falls back to
the first plain-working top in store order — without that, the escalation was invisible
exactly when a card existed to carry it. The badge grew the per-item decision classes,
recorded in (d).
*As built (2026-09-07):* the stand-down is AUTHOR-AWARE. `_user_todo_idle` keeps a per-sid arm
record (`_UT_FLOOR_ARM`: the open todo ids + the settled turn's end it armed at); a turn a peer or
romp opens holds the floor, and so does a harness notification (which never opens a turn of its
own: the event model absorbs it into the running turn); the record is spent by the human speaking
to the session (`_last_human_msg_t`: every author-`human` atom since the arm — a plain prompt, a
card reply, or a message absorbed into a turn someone else opened; a trigger-only read missed the
absorbed shape and a card reply that ended before a peer's turn opened), a user interrupt, queued
intent, the open set changing (build_feed disarms when it empties), or a peer-wait edge; awaiting /
API error / live prompt / compaction still read not-idle for their duration without touching the
record, and the ANSWER to a permission or judge-auth prompt is not a stand-down event (it leaves no
human atom in the transcript, so after an approval on a held turn the card wears the floor while
the approved action runs, until the next settle — whether a prompt answer should spend the record
is an open call); the record is in-memory, so after a kernel restart a session mid non-human turn
reads Working once until its next settle. Dead sessions: `_prune_user_todos` drops the record of a
sid whose death is corroborated, so a session that dies with open rows does not keep one until
restart.

**Slice 3 — memory across context loss.** The SessionStart hook (sources: resume and compact)
that emits open todos as a passive context block, in the agent's-own-notes voice.
*Tests*: hook output shape (no todos → no block at all), the voice test on the rendered text,
dormant/ended gating.
*As built (2026-08-22):* `hooks/romp-usertodo-context.sh`, registered at SessionStart by
install.sh — deliberately NOT tmux-gated the way `romp-postal-context.sh` is, because SDK
sessions need the block too. No fsid→sid join either: both backends already put the stable sid
in the CLI env as `ROMP_SID` (bin/romp's launch line, sdk_backend `_options`), and the hook
payload's `session_id` is the current transcript fsid, the wrong key after a fork. The KERNEL
renders the words (`_user_todo_context_block` → read-only `POST /usertodo/context`), so
`test_injected_voice.py` scans exactly what a session receives; the wording is the draft above,
newest first, capped at 12 (`_USER_TODO_CONTEXT_CAP`, the `_open_leaf_bullets` cap idiom) with
an "…and N more from earlier" tail, todo text marker-neutralized. The compact seam was
VERIFIED at build time, not assumed: the CLI then shipped (2.1.224, SDK-bundled) fires
SessionStart with source ∈ startup/resume/clear/compact/fork and delivers every source's
additionalContext (only session_title is source-filtered), and SDK sessions load user-settings
hooks (the SDK's `setting_sources=None` default = CLI defaults). Re-verified 2026-09-07 against
CLI 2.1.263 in a hermetic config dir with a logging SessionStart hook: the hook fired with
source startup, resume and compact; its additionalContext reached the model's reply on startup
and resume, and the compact-source context landed in the transcript as a SessionStart
hook_additional_context attachment. startup stays silent (a fresh sid has an empty store) and
so do clear/fork (the user's own reset or rewind of the conversation — the plan's chosen sources
are resume and compact, and widening to clear is a separate call). One refinement of this slice's "dormant/ended gating" test line: the read leg
has NO liveness re-check, deliberately — an ended session fires no SessionStart, and
re-checking the death marker at the route would race the revival's own states row (written
from the same SessionStart) and eat the exact block the revival came for. `ContextBlock` pins
that with the reasoning; ended sessions still hide from every USER surface exactly as designed
above.

**The per-install switch (2026-09-03), off by default.** Everything above is switchable per
machine. `STATE/user-todos-enabled.json` (`{"enabled", "gt"}`, the thinking-summaries idiom:
gesture-clock stand-down, settingStale reply, atomic write, loud write failure) holds the answer,
the gear's **User todos** checkbox flips it, and `/version` reports it top-level as `userTodos`.
It is deliberately not a federation `KERNEL_SETTING`: each kernel keeps its own copy, and the
choice never propagates. While off, every surface refuses and says so: `POST /usertodo` and
`/usertodo/withdraw` answer 409 with one plain line; `userTodoAnswer` and `userTodoDismiss` answer
with a warning toast; `/usertodo/context` answers `enabled: false` with an empty block, so the
SessionStart hook injects nothing; the postal bus leaves the two tools out of `tools/list` and
refuses a call anyway before any post; and `_open_user_todos`, the one gated store read, returns
`[]`, so the card, the tab glyph, the feed marker, the escalation floor, the nudge stand-down and
the push latch all show nothing, with no client logic. The badge's arithmetic is part of the
feature too: `_needs_you_count` reads the switch and, while off, is the pre-feature count (every
real needs-input card, per card), so an install that never turned the feature on sees no change
in the number its icon wears. The store is untouched by the switch: rows registered while it was
on stay on disk and reappear when it is turned back on, and a boot notice counts the open rows
stored behind an off switch. The switch is its own commit, so dropping that commit ships the
feature on by default. The stdio server also declares `tools.listChanged` and polls the switch
file (one stat per 2 s tick on a poll thread of its own, `ROMP_POSTAL_SWITCH_POLL`), so a connected
session is told to re-list on a flip and gains or loses the two tools without a restart
(2026-09-07).

The store also guards its own shape, because the switch file is easy to mistake for it. A
`user-todos.json` that is not sid → list (a settings blob, a JSON list, unparsable text) is
reported once per file version in the kernel log, with what was found, and every writer refuses
to overwrite that version until the file is fixed or removed; without the guard, the next register
would replace the whole store with a one-row one. Since 2026-09-07 the flagged version is not read
as empty either: every user-facing surface, the register route (503) and the withdraw tool say the
store is unreadable; the add tool hears a refusal without the cause, because the bus reads every
non-2xx answer as None (the slice 1 as-built notes list them). The switch file has the mirror guard: a `user-todos-enabled.json` that is not an
`{"enabled": …}` object reads as OFF and is said once per file version in the kernel log and the
bus's log; an absent file is silent.

**The route contract (as built, 2026-09-07).** `POST /usertodo` answers 400 over the caps (the
error carries the one-line advice), 409 while the switch is off, and 503 `{"ok": false, "error":
"the request store is unreadable (see the kernel log)"}` on a flagged store; for a sid on an
attached machine it relays that kernel's 409 (the error names the host) or answers 502 with the
cause (the tunnel not answering, a kernel that predates `/usertodo`, an HTTP status, an answer
without a todo id) instead of a 200 `{"ok": false}` the bus could only word as "try again". `POST
/usertodo/withdraw` answers the same 409 and, on a 200, an account (`state`, `at`, `owner`); when
the local store is flagged the account is `owner: null` with the unreadable-store error, so the
tool says the store could not be read rather than "no such request".

## Judges: no vote now, a suggestion later

Explicitly deferred, not in v1: **judge-suggested mootness**. A judge that notices a todo
looks answered or overtaken could *suggest* clearing it — rendered as a one-click confirm for
the user on the todo itself, never an auto-clear. Deferred because v1's worth is measured by
how much the user trusts the invariant that a visible todo is still real until they or the
agent clear it; a suggestion channel is only safe to add once that trust exists, and it
changes no store semantics when it comes.

## Deliberately not in v1

A feed strip or column for todos (the marker + escalation carry it); a cross-session digest
of every open todo (the tab glyph, the feed marker and the badge carry it); idle check-in turns
(rejected outright, not deferred); numeric counts on tabs; editing a todo's text (withdraw and
re-add); priorities, deadlines, or ordering beyond creation time; per-todo Web Push (the badge
and the existing needs-input push cover the phone); the judge mootness suggestion (deferred
above).

## Open questions

The interview settled the design; the two build-time calls it left open were made in the build
(2026-08-22), pinned in the UI suites (`tab-usertodo.test.ts`, `user-todos-card.test.ts`):

1. The tab glyph is **⚑**, right after the session name and before the ctx gauge — non-numeric
   as decided, and its own element, never a `.tab-dot` (pips encode turn state, and the mobile
   scrape keys on the pip classes).
2. The split card is the transcript's to-do notice: its head reads the agent's plan as **"to-do ·
   n of m done"** (the head every chat notice shares since 2026-09-08, "unavailable" when a store
   could not be read, and **"waiting on you · N"** when there is no checklist to name), and the
   open todos sit under their own heading, **"Waiting on you · N"**, inside the notice body.

## Vocabulary

The terms this feature adds, with the words to avoid because they already mean something else
in this repo.

**User todo**: a need an agent registers with the person it works for — a decision, input, or
action only they can provide — held open while the agent keeps working on whatever else it
can. Cleared only by answer, dismiss, or withdraw; never by inference.
*Avoid*: ask (the feed payload's `asks` field already means the card list), request, user task.

**Answer**: the user clears a user todo by replying to it; the reply is injected into the
session as a message from the person the agent works for. One of the three clearing events.
*Avoid*: resolve, respond.

**Dismiss**: the user clears a user todo without a reply — for moot or stale items. Nothing
reaches the session. One of the three clearing events.
*Avoid*: clear (already means removing a card from the feed), delete.

**Withdraw**: the agent takes back its own user todo, by id, when the need was met some other
way or went moot. The only agent-side clearing event.
*Avoid*: cancel, retract, recall (already means unsending postal mail).

**Escalation**: the single card move a user todo can earn: when its session goes idle with
open user todos and nothing else dispatched, the todo is the session's frontier and the card
enters the Blocked column. While the session works, todos never move a card.

**Authority tier**: a class of state the judges and the unblocker cannot clear — only
designated actors can. agentTask nodes were the first (an agent's open task vetoes an inferred
done); user todos are the second (cleared only by answer, dismiss, or withdraw).

A related pre-existing term, kept distinct on purpose — **the agent to-do checklist**: the
agent's own plan for itself, the mirror of Claude Code's live task store, rendered as the
transcript-bottom checklist card and as authority discs in a card's tree. A different thing
from a user todo: the checklist is what the agent owes the work; a user todo is what the user
owes the agent. *Avoid*: todo card (ambiguous with user todo), plan card.
