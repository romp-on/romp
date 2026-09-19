# Card boards: the feed as one instance of a generic card UI

Status: IN PROGRESS (2026-09-18), phased. Landing commits: phase one 58c3ce83 (PR 1834, the definition
extracted); phase two f2ecb7bb (PR 1837, the record fields); the rest TBD per phase. Tier per phase in section 6:
`feature` for phases one, two, four and five; phase three (the board definition store, its door and
its frame field) is the `major-feature` discussion point, called out there with the reasons, and the
phasing is built so nothing before it changes a persisted record or the feed protocol.

A design line for an ask on the user's list (the user, 2026-09-18, about 12:50 PM PT: they want
romp's cards made wholly generic, so that they can create a system of cards with categories other
than the feed's Blocked, Completed and Working, different sub-sorts, and different buttons than
Background and Summary, with today's feed becoming one instantiation of the card UI; they suspected
it might already be so and asked for another pass). The manager's ruling on the one question the
draft asked (2026-09-18): a board the user can make without a release is the destination, not a
later maybe; ONE definition schema, fixed here, with the feed's definition a code constant in that
schema and user- or producer-defined boards as data through a door that lands with `romp card`
naming boards; presentation beyond what the schema expresses stays a release. This document checks
the premise in code, fixes the schema, writes today's feed down as the first definition, and phases
the change so the feed renders byte-identically until a second board exists. No code here.

Line references were verified at `upstream/main` `24092ef4`. Cite by symbol first; a line is a
current pin, not a contract.

## 0. The premise, in code: it is not generic today

The feed is one bespoke card renderer over one bespoke kernel builder. Five facts, each a place the
generic model has to reach:

- **The category vocabulary is a literal on both sides.** The kernel's `build_feed`
  (`kernel/kernel.py:42169`) emits `column` as one of three strings. For a goal card the value is a
  projection of the judges' rolled-up status (`rollup_status`, `kernel/judge.py:7927`, writes
  `cleared`, `blocked`, `working`, `completed` per top at `:8163` to `:8182`) through one expression
  (`kernel.py:41976`): `needs_input` when the session is stopped on an API error, a judges' billing
  refusal, a live permission or picker prompt, a stall block, or a landed block not under a re-check;
  else `completed` when the rollup says so; else `working`. The awaiting state is a flavour riding
  Working, never a column (`col = "awaiting"` at `:41662`, folded into `had_awaiting`). The renderer's
  `AskItem.column` is typed to the same three strings (`ui/webview/feed.ts:102`), `askColumn()`
  (`:485`) maps them to the local keys `asks`, `needsInput`, `completed`, and `ensureCols` builds the
  columns from the literal table `["asks", "Working", "working"], ["needsInput", "Blocked", "blocked"],
  ["completed", "Completed", "completed"]` (`:4782`; a second copy for the focused-session section at
  `:5020`). The user docs say the same three (`docs/guide.md:319`).
- **Every kernel-made card family sets its column by hand.** The provisional placeholder
  (`kernel.py:39773`, `working`), the awaiting card (`:39809`, `working`), the blocked placeholder
  (`:39878`, `needs_input`), the parked handoff (`:42319`, `needs_input`), the quarantined peer mail
  (`:44940`, `needs_input`) and the notice card (`:24125`, `needs_input` when the producer said the user
  must act, else `completed`). `blocked.state` discriminates the families; there is no `kind` field.
- **The notification set is a kernel literal.** `_NOTIFY_COLUMNS = ("needs_input", "completed")`
  (`kernel.py:53688`) is the set whose entry rings the bell and the phone (`_feed_notifications`,
  `:53871`), and `_needs_you_count` (`:53948`) counts `needs_input` cards for the app badge.
- **The card's sections and actions are hard-coded in the card builder.** Row three carries the
  press toggles Background, Summary, Sub-goals, Stalled and Awaiting task (`feed.ts:1296` to `:1349`),
  wired by `applySections` (`:1709`) as one-open-at-a-time with the choice persisted per card
  (`FeedViewState.sec`, `ui/webview/feed-view-state.ts`). The actions are Clear (`clearButton`,
  `:799`, in row one's action corner), Retry, Log in, the cap switch, Revive, Approve, Deny and
  Continue (`:1205` to `:1237`, rows two and three), the bell (`cardNotify`), the right-click menu's
  Notify me and Browse files (`showCardMenu`, on the shared menu builder since v0.16.0), and the
  modal's Follow up, Check status, Clear and Continue (`:3591` to `:3602`). Each posts its own wire
  message (`askClear`, `askClearMany`, `askFollowUp`, `apiRetry`, `reviveSession`,
  `quarantineDecision`, `noticeAction`, `cardNotify`, `showAskPath`, `showOnTimeline`, `openSession`).
- **The sorts are inline.** Each bucket sorts by `t` in the direction of the `newestFirst` preference
  (`feed.ts:5629`), then grouped mode re-sorts by the kernel's session order with a header per run
  (`:5640` to `:5665`). The View menu (`buildViewMenu`, `:4147`) offers exactly four rows: the sort
  direction, the single-column layout, group by session, and the focused-session section. The
  preferences live in `romp:settings` (`FeedPrefs`, `:572`); the column order and the folded columns
  live in the view state (`FeedViewState.cols`, `.order`).

The one generic seam that exists is the notice card (`plans/notice-cards.md`, T370): a producer's
card the kernel builds without a judge, carrying `notice: {producer, key, rev, body, attachment,
actions: [{label, route, body}], expiresAt, dismissOnAction, acted}` (`feed.ts:131`; built by
`_notice_cards`, `kernel.py:24100`). Its stored `actions` are producer-defined buttons the kernel
executes against an allowlist, and its `key` and `rev` give a card an identity outside the goal
store. It rides the same `AskItem` shape and the same `column` string as every other card. Its door,
`post_notice` (`kernel.py:23947`, the `(row, error)` contract of `add_watch`, `:24614`), its route
`POST /notice` and its command `romp card` (`bin/romp:1126`) are the pattern every door in this
document follows.

Two more facts the design builds on. **The frame** (`kernel.py:42335`) is `{type: "feed", asks, now,
working, awaiting, stateUnknown, bgServices, dismissedCount, ...}` plus the views payload, sent to
each client only when its dedup signature differs (`_send_client`, `:49077`), so no card field may
derive from the clock. **The feed memo** derives each session's cards once per change of its inputs
(`_FEED_MEMO_LABELS`, `:40378`, one component per input the derivation reads; `_feed_key_with_deps`,
`:41013`), and the per-build fold stamps only the age tint (`_feed_fold_entry`, `:42141`). A card
field is either a function of that session's inputs, and lives inside the memoized entry, or a
per-build stamp; there is no third place.

Correction to a plausible reading of the ask: the feed's three columns are not three views of one
list that a client could re-bucket; the category is written by the kernel, from the judges'
verdicts and the live state, and the client files strictly by it (the 2026-06-29 ruling that
`it.column` is authoritative, `feed.ts:487`). The repository's law that cards move on new
information and never on inference flaps (`CLAUDE.md`, 2026-07-29) is what that arrangement
protects, and the board model keeps it: the kernel writes the category at an event, the board
renders it.

## 1. The board model: one schema for code and data

**Decision: a board is a definition in ONE schema, a card is a record that names its board and
category, and the renderer is a function of the two.** The feed's definition is a code constant in
the schema; every other board is a JSON document in the same schema, stored under the state root
and shipped in the frame. The schema is data-expressible end to end: everything a code-defined
board can say, a data-defined one can say too, and what the schema cannot express (a new section
body, a new action that posts a new wire message, a new chip colour) is a release for both.

The schema, with its validation and its defaults:

```
Board {
  id: string           // [a-z][a-z0-9_-]{0,31}; "feed" and every code-defined id are reserved (a define of one is refused)
  title: string        // <= 40 chars; default: the id with its first letter upper-cased. The palette's word for
                       //   the board (section 5); never a resting title bar (ui/CLAUDE.md, the docking plan)
  categories: Category[]   // 1 to 8, ids unique; in the board's default order (columns side by side, sections stacked)
  defaultCategory: string  // one of the category ids; default: the first. Where a card lands when its producer named
                           //   no category and no rule matched
  rules: Rule[]        // 0 to 16, evaluated IN ORDER by the kernel at the post (section 3), the first match wins;
                       //   default []. A rule assigns a category; it never re-sorts
  sort: SortKey        // default {key: "t", dir: "desc"}
  subSorts: SortKey[]  // 0 to 6, the orders the user may pick per board (section 5); default []
  groupBy: "session" | null    // the many-sessions rule (section 4); default null
  order: OrderRule[]   // 0 to 4, the board's own ordering rules run before `sort` (the owner rank, section 3); default []
  notify: string[]     // category ids whose ENTRY announces (bell, phone); default []
  needsYou: string | null      // the category the app badge counts; default null (this board never badges)
  kinds: string[]      // the card kinds this board renders, from the fixed set below; default ["notice"]
}
Category  { id: string /* the id grammar */; title: string /* <= 40, default: the id upper-cased */; chip: Chip /* default "neutral" */ }
Chip      = "working" | "blocked" | "completed" | "neutral"     // the state-chip classes styles.css has (.chip-working,
                                                               //   .chip-blocked, .chip-completed; neutral = the dim .chip);
                                                               //   a fifth colour is a release
SortKey   { key: "t" | "session" | "owner" | "title"; dir: "asc" | "desc" }   // the fixed set; a fifth key is a release
Rule      { when: Predicate; category: string /* one of the board's ids */ }
Predicate { needsYou?: boolean; producer?: string; keyPrefix?: string }       // every present member must hold (AND);
                                                               //   the fixed set; a fourth predicate is a release
OrderRule = "ownerRank"                                        // the fixed set; today's one entry (section 3)
Kind      = "goal" | "placeholder" | "parked" | "quarantine" | "notice"   // the code's families (section 2); a data-defined
                                                               //   board may name "notice" alone, since only a producer's
                                                               //   card can be posted to it
```

A definition is a JSON object of exactly these members (an unknown member is a refusal naming it,
so a typo never silently defaults). Validation runs in one function, `_board_check(defn) -> (defn,
error)`, for both the code constants (asserted at import, so a drifted constant fails the suite) and
the door. Every refusal is prose naming the member and the rule it broke; the door's shapes are
`/notice`'s (`400` for a malformed body, `200 {"ok": false, "error"}` for a definition the schema
refuses, `200 {"ok": true, "board": <defn>}` on success).

A **card record** is today's `AskItem` plus:

```
board: string      // the board it belongs to; every card the kernel builds today carries "feed"
category: string   // the board's category id; for the feed board today's raw column values
                   //   (needs_input, working, completed), carried beside `column` until the renderer
                   //   reads category alone (agreed with romp_cards 2026-09-18)
```

Three rules of the model, each chosen against an alternative:

- **The category is explicit on the card, written by the kernel at an event; a board never derives
  a category at render time.** A definition's `rules` are evaluated by the kernel when a card is
  posted or revised, once, and the matched category is written onto the record; the renderer reads
  the field. The alternative, a membership rule the renderer evaluates over the card's fields on
  every render, would move a card whenever the rule's inputs flapped between two renders over the
  same information, which is the flap the repository's law forbids. The feed board's rule is the
  code expression at `kernel.py:41976`, run once per session derivation and memoized, which is the
  same arrangement with the judges' verdict as the event. A rule-based selection that shows cards it
  does not own is allowed as a VIEW, the precedent being the focused-session section (T347,
  `feed.ts:4990`), which shows the chat's active session's cards above a divider without moving them.
- **A board owns its categories' order and notification set; the card owns nothing but its
  board and category.** The bell, the phone and the badge read the board's `notify` and `needsYou`
  instead of `_NOTIFY_COLUMNS` and a literal string; for the feed board the two lists are today's
  values, so nothing rings differently.
- **A card kind is chosen by the card's flavour, not by a new field.** `blocked.state`,
  `provisional` and `notice` already discriminate the families (section 0); the code's `match` test
  per kind names that test. A `kind` field on the record is not added, because every family the
  kernel builds today would have to write it and the pins on their payloads would move for no
  rendering change. The notice card's stored `actions` are the precedent for a kind whose buttons the
  producer defines: its buttons are read from the record, the other kinds' from code.

**What the schema expresses, and what stays a release.** Data expresses labelled categories with a
chip from the four-colour palette, a default category and post-time rules from the fixed predicate
set, sort and sub-sort keys from the fixed set, grouping by session, the owner rank, the
notification set and the badge category, and the notice kind, whose sections are its body and its
attachment and whose buttons are the record's stored actions (the kernel's route allowlist bounds
them, `plans/notice-cards.md`). Anything past that is code: a new section (a body computed from the
card the way Background or Sub-goals are), a new action (a button whose click posts a wire message
the kernel must implement), a new chip colour, a new sort or predicate, the goal kind and its
transcript roads. That line is the manager's ruling (2026-09-18) and this document's promise: a
producer can make a board and its categories today; a producer who needs a new button beyond the
stored actions files a change.

What is deliberately not in the model: a card's position inside a category set by hand (drag-to-sort
a card), a due date, a card created by hand in the UI, a category a card can be dragged into. Those
are a kanban product (section 7).

## 2. The feed as the built-in board

Today's feed, written as one definition in the schema, a code constant the existing renderer
consumes. Every value below is a literal the code carries now, cited; the proof of byte-identical
rendering is that no pin on those literals moves (section 6, phase one).

```
FEED_BOARD: Board = {
  id: "feed", title: "Feed",
  categories: [
    { id: "working",     title: "Working",   chip: "working"   },   // feed.ts:4782, local key "asks"
    { id: "needs_input", title: "Blocked",   chip: "blocked"   },   // local key "needsInput"
    { id: "completed",   title: "Completed", chip: "completed" },
  ],
  defaultCategory: "working",
  rules: [],                               // the feed's rule is code: the column expression in _feed_session_entry (kernel.py:41976)
  sort: { key: "t", dir: "asc" },          // oldest at the top (the user 2026-06-27); newestFirst flips it (:5629)
  subSorts: [],                            // none today; phase five adds them (section 5)
  groupBy: "session",                      // grouped mode, default ON (feedPrefs, :589; the user 2026-07-13)
  order: [],                               // the owner rank joins here in phase three (section 3)
  notify: ["needs_input", "completed"],    // _NOTIFY_COLUMNS, kernel.py:53688
  needsYou: "needs_input",                 // _needs_you_count, kernel.py:53948
  kinds: ["goal", "placeholder", "parked", "quarantine", "notice"],
}
```

The category ids are the kernel's raw column values on purpose: the card's `category` for the feed
board equals its `column`, so phase two writes the new field with the old value and the client's
`askColumn` becomes a table lookup on the definition (`working` to `asks`, `needs_input` to
`needsInput`, `completed` to `completed`) with the same three outputs. The local keys stay as they
are: forty-four test files pin `needs_input`, fifteen pin the titles, and the CSS classes
`col-asks`, `col-needsInput`, `col-completed` and `chip-needsInput` are the layout's. Renaming
them is a sweep of its own and not this change.

**Where the kernel writes a card's category.** For a goal card, `rollup_status` writes the per-top
status from the judges' verdicts (the verdict kinds recorded through `record_verdict`,
`judge.py:10508`: `done`, `block`, `unblock`, `settle`, `reopen`, `awaiting`, the planner's and the
closer's), and `build_feed`'s expression (`kernel.py:41976`) projects that status plus the live
floors onto the category. That expression is the feed board's category writer. It stays inline in
`_feed_session_entry` (correction at phase two: three tests pin the expression's text as the record of
the 2026-06-29 and 2026-07-07 rulings, and naming it out would move those pins for no change of
behaviour; a named function is phase four's if a second board ever needs to write a goal card's
category). For the kernel-made
families the literal each one sets today becomes the same literal under `category`. The law that
cards move only on new information is therefore unchanged in mechanism: a category changes when a
verdict lands, a live state changes, or the user acts, and never because a render re-evaluated a
rule.

**The kinds, as the code has them.** The kind's sections and actions are code (section 1); the
definition names the kinds a board renders.

- `goal`: the default kind. Sections Background (`it.background`), Summary (`it.summary` or
  `it.blockSummary` by `distillState`), Sub-goals (the card's `tree`), Stalled (`it.stalled`),
  Awaiting task (`it.awaiting.items`); actions Clear, Follow up, Check status, Continue (the modal's
  four), Retry, Log in, the cap switch, the bell; menu Notify me, Browse files. The group card
  (`buildGroup`, `feed.ts:2845`, siblings of one typed turn) is a presentation of this kind, not a kind.
- `placeholder`: `provisional`, `awaiting:<sid>` and `blocked:<sid>` cards. No sections; actions
  Clear (where the family allows it) and the bell.
- `parked`: `blocked.state === "parkedHandoff"`; action Revive.
- `quarantine`: `blocked.state === "quarantine"`; the held body shown in full; actions Approve and
  Deny (the edit happens in the modal, not as a card action; the 1834 read, 2026-09-18).
- `notice`: `it.notice`; the producer label, the body through the sanitizer, the attachment; actions
  the record's own `actions` list (`noticeAction`), Clear and the bell. The one kind a data-defined
  board renders.

Writing the feed down this way changes no pixel: it is the same five families, the same rows, the
same buttons, keyed by the same tests. The renderer's card builder (`makeAskCard`, `feed.ts:1118`)
keeps building every element and hiding the ones the kind does not show, exactly as `updateAskCard`
and `applySections` hide them today; phase one replaces the literals it reads with reads of the
definition and nothing else (section 4 says which reads).

## 3. The producer side

**What a card must carry to belong to a board and a category.** The two fields of section 1,
`board` and `category`, plus what it carries today: `itemId` (namespaced for a non-goal family, the
`parked:`, `quarantine:`, `notice:<sid>:<key>:<rev>` pattern, so federation's unprefixed ids never
collide), `sid` and `name` (the session that routes the card's gestures and colours its chip; the
owner-less case below), `text`, `t` (fixed at the post, never the clock), `live`, `turnId`, `tree`
(empty for a non-goal), `column` (until phase two's transition ends), and the flavour object that
picks its kind. A notice card's own record adds `key`, `rev`, `producer`, `actions`, `expiresAt`,
`dismissOnAction` (`plans/notice-cards.md`, "A notice card: name and shape").

**The board definition store and its door.** One function, `define_board(defn, *, now=None) ->
(defn, error)` in `kernel/kernel.py` beside `post_notice`, validates through `_board_check`, refuses
a reserved id, and writes `STATE/boards/<id>.json` whole (an atomic replace, the state-file write
the kernel uses elsewhere). Three doors call it, the notice card's pattern exactly:

- in process, for a producer inside the kernel;
- `POST /board` behind `_authorize`, the body the definition itself, read through `_read_post_body`
  and `_json_object_body`, the answers `/notice`'s; `GET /boards` answers the current set (for the
  command's `list` and for a producer checking before a post);
- `romp board define <id> --from <path> | --json <text>`, `romp board list`, `romp board show <id>`
  in `bin/romp`, on `romp watch`'s mechanics (the token through `_romp_token_cfg`, one `romp board:
  ...` line, `refused` naming the error with exit 1, usage errors exit 2).

A define REPLACES the board's definition. A define that drops a category still holding a standing
card (a live notice row filed under it) is refused naming the category and the count: the user
dismisses or re-posts first, so no card is ever left under a category no definition has (the
fail-loudly rule over a silent fallback). The store is read by `_boards()`, memoized on the
directory's stat (one stat per build, the cleared ledger's pattern), merged with the code constants
(code wins on an id collision, which the door prevents), and shipped in the frame as
`boards: {<id>: <defn>}` for the data-defined set alone: the code constants are the renderer's own
and never cross the wire. Deleting a board: `romp board remove <id>`, refused while any standing
card names it; a removed file leaves the frame on the next build.

**How `romp card` names a board and a category.** Two flags join the command (`bin/romp:1126`), with
short forms in romp_cards' short-flag convention:

```
romp card --key <key> --title <text> [-b|--board <id>] [-c|--category <id>] [...today's flags]
```

Both ride the `/notice` body as `board` and `category`, resolved by `post_notice` against
`_boards()` merged with the code table. The resolution, in order:

1. no `board`: the feed board, and `category` is what `--needs-you` decides today (`needs_input` or
   `completed`), so every existing caller posts exactly what it posts now; a `--category` naming one
   of the feed's three ids is accepted, any other is refused;
2. a known board and a known category: filed as named;
3. a known board and no category: the board's `rules` are evaluated over the post (`needsYou`, the
   producer, the key), the first match names the category, else `defaultCategory`;
4. an unknown board: **created on first use** with the defaults of section 1 (title from the id,
   one category named by `-c`, or `notes` when none was given, chip neutral, sort newest first, no
   grouping, no notifications), written through `define_board`, so the user has a board with one
   command and refines it with `romp board define` when they want titles, chips or a bell;
5. a known data-defined board and an unknown category: the category is **appended** to that board's
   definition with default dress, through the same function (the board is the user's, and refusing
   would force a define before every new category); on a code-defined board it is refused.

`--needs-you` keeps its meaning on the feed board; on another board it means "the board's `needsYou`
category", and a board with none refuses it naming the flag. The notice row stores the two fields it
was filed under (an additive persisted field: the phase-three call-out in section 6), and
`_notice_cards` copies them onto the card; a row from before the fields reads as `feed` and its
`needsYou` mapping, so no migration touches a file.

The producers inside the kernel that build cards by hand (the placeholders, the parked handoff, the
quarantine) write `board: "feed"` and their category literal directly; they have no reason to name
another board.

**Owner-less cards.** romp_cards' current design (a notice with no session, mail of 2026-09-18) puts
such a card at the top of the feed by a sort rule of the feed board over the owner key: a card whose
owner is the reserved no-session key ranks before every session run, then the session order, then
time, with needs-you still deciding the category. Under this model that is the `OrderRule`
`ownerRank` in the feed board's `order`, and nothing else: not a category (the card's category is
still what needs-you decides, so the badge and the bell keep their meaning), not a card field (a
card-carried rank would be a producer-set order no board reads; agreed with romp_cards 2026-09-18),
and not a timestamp trick (a `t` set into the future would lie to the age tint and to every reader
of `t`). The rule reads two fields the card already has, `sid` and `t`, plus the kernel's session
order the frame already carries (`order`). In grouped mode the owner-less run is one more run,
first, with a header the board titles (romp_cards' call; "Notes" is the natural word); ungrouped,
the rule sorts them first inside each category. The notes board of phase four is then a
data-defined board with no `groupBy` and an empty `order`: the same cards, filed under that board's
id when the producer names it, rendered by the same code.

## 4. The UI side

**What becomes a renderer parameterised by a board definition.** Each item is a read of a literal
today and a read of the definition after phase one; none changes what the feed shows.

- `ensureCols` (`feed.ts:4768`) and its focused-section twin (`:5020`): the column list, titles and
  chip classes come from `board.categories`; the local key stays a table `{working: "asks",
  needs_input: "needsInput", completed: "completed"}` inside the feed definition until the sweep
  that renames the CSS keys, which is not this change. A data-defined board's categories take the
  generic column class (`col-<id>`) and the chip class of their `chip`.
- `askColumn` (`:485`): a lookup of the card's `category` (phase two on; `column` until then) in
  the board's table.
- The bucket sort (`:5629`) reads `board.sort` and the user's picked sub-sort (section 5) instead of
  `newestFirst` alone; grouped mode (`:5640`) runs when `board.groupBy === "session"` and the
  preference is on; `board.order` runs before the primary sort, which is where the owner rank lands.
- The header chips, the fold carets and the column drag (`:4788` to `:4808`) iterate the
  definition's categories; `STACK_DEFAULT` and `ROW_DEFAULT` (`:4468`) become the definition's order
  and its stacked reverse.
- `makeAskCard` and `updateAskCard`: the section toggles and the action buttons are built from the
  card's kind (the code's `match` tests, gated by `board.kinds`); the elements are the same elements,
  minted in the same order, so the layout pins (`feed-card-layout.test.ts`, `button-vocab.test.ts`)
  hold. `applySections` reads the kind's sections for the one-open-at-a-time set and the persisted
  choice.
- The card menu (`showCardMenu`): the kind's menu.
- The View menu (`buildViewMenu`, `:4147`): the rows the board offers (section 5); today's four for
  the feed.
- The kernel's bell, phone and badge (`_feed_notifications`, `_needs_you_count`): the board table's
  `notify` and `needsYou` for the card's `board` (phase two for the feed; phase three for the data
  set).
- The definitions the renderer holds: the code constants plus the frame's `boards` field (phase
  three), merged by id with code winning; a card whose `board` names no known definition renders
  under the feed's rules with its category unknown, which files it under the feed's default category
  and marks the card's chip "unknown board" (the same loud fallback an unknown flavour would get;
  never a silent drop).

**What stays feed-specific, and why.**

- The **focused-session section** (T347): a view over the chat pane's active session, keyed on the
  `activeChat` relay from the kernel; it presumes cards that belong to sessions and a chat pane. A
  board with `groupBy: "session"` may opt in; the notes board does not.
- The **goal card's transcript roads**: the jump on the title (`showAskPath`, the lit timeline
  journey), the summary's landing on the completion turn (`summaryAnchorUuid`), the hover highlight,
  the modal's tree with per-node anchors, the follow-up composer. These are the goal kind's, not
  the board's; a board without the goal kind never shows them.
- The **turn groups** (`turnGroups`, `buildGroup`): siblings of one typed turn fold into one card.
  A goal-kind rule.
- The **serving folds** (T137, worker mirror cards) and the **tracked delegation** satellites: goal
  kind, kernel-derived.

Shared by every board because they read nothing feed-specific: the session chips and grouped
headers (any board with `groupBy: "session"`), the session filter combobox and the search
(`viewFiltered`, `:5492`; they read `sid`, `name` and `text`), the folds (`collapsedCols` keyed by
category id, `collapsedThreads` keyed by `(sid, category)`, `feed-view-state.ts`), the stacked and
side-by-side layouts, the hover freeze (`feed-freeze.ts`), the keyboard navigation (`kbCardEls`,
`:5870`), the Clear all and Undo footer over the cleared ledger, the per-card update gate
(`feed-card-gate.ts`), the age tint, and the dismissal through `askClear` and `cleared.jsonl`.

**How a second board mounts.** Two roads exist in the code today; the design takes the first now
and names the second for the docking kit.

1. **A view switch inside the feed pane.** The feed pane's page (`_feed_page`, `kernel.py:57498`)
   loads one bundle (`feed.js`) over one socket app (`_shim("feed", v)`); the frame carries every
   card with its `board` field and the data-defined definitions. The renderer files each card under
   its board and shows one board at a time: the View menu gains a "Board" row per definition
   (`menuitemradio`, the one vocabulary), the pick persisted in the view state (`FeedViewState.board`,
   prune-exempt like `cols` and `order`). A board created by `romp card -b` appears in the menu on the
   next frame with no reload. No shell change, no inline JS, no new route, no new socket: the
   whole-suite trigger list is not touched. This is phase four's road.
2. **A pane per board.** The docking kit (`plans/pane-docking.md`, PR 1828, PROPOSED) makes every
   surface a registry entry `{id, title, mount, minW, minH, defaultDock}` and opens a pane at its
   default dock from the rail or the palette. A board is one entry whose `mount` is the feed page
   with a board id in its query (`/feed?board=notes`; `_feed_page` passes it to the bundle, the
   renderer selects that definition and hides the Board row). Until the kit lands, the same needs an
   iframe in the landing's fixed row, a rail button and a `PANE` map entry (`kernel.py:57829`,
   `:62709`), which is inline JS and the shell's fixed order: a whole-suite change for one board.
   Deferred to the kit; the query parameter is designed now so the page needs no second change.

**The many-sessions rule** (`ui/CLAUDE.md`, 2026-09-09: dozens of sessions on every surface that
lists them). A board with `groupBy: "session"` inherits the feed's answer: one run per session with a
one-line header, folds per `(session, category)`, the session combobox to filter, the search. A board
whose cards carry no session (the notes board) has no runs and no combobox; its categories lay out
as the feed's do and its cards sort by the board's key. The same rule bounds the boards themselves:
the Board row is a radio group inside the View menu, never a strip of tabs across the pane, so a
user with twenty boards scrolls one menu. A board's title never sits as resting chrome above its
columns (the docking plan's rule and `ui/CLAUDE.md`'s progressive disclosure): the pane's header row
(`#feed-head`) stays empty, and the board's name lives in the View menu's radio row and in the
palette.

## 5. Sub-sorts and per-board choices: where the user sets them

**Decision: the board's own View menu, persisted per board; the gear for cross-board defaults; the
palette for reach.** The three places the dispatch named, weighed:

- **The View menu** (the footer's "View" word-button, `ensureViewMenuBtn`, `feed.ts:4226`) is where
  the feed already keeps its sort direction, its layout and its grouping, as `menuitemcheckbox` rows
  in the one menu vocabulary. Sub-sorts join it as a radio group under a divider (one row per
  `SortKey` in `board.subSorts`, the primary marked), and the Board row of section 4 sits above.
  Per-board persistence: `FeedPrefs` stays the feed's (`romp:settings`, shared with the gear), and
  a `boards: {<id>: {sort, groupBy, cols, order}}` map joins the view state
  (`feed-view-state.ts`, prune-exempt, bounded by the known board ids and pruned when a board leaves
  the frame), so the notes board's sort never changes the feed's. A sub-sort is a view choice, never
  a card move: the kernel is not told.
- **A board header without resting chrome** is rejected as the home of the controls: there is no
  header to put them in (the rule above), and a control that appears only on hover fails the
  click-safe rule (`ui/CLAUDE.md`, buttons acknowledge and stay put). The View menu is one click
  from the same footer and already exists.
- **The gear** (`ui/webview/settings.ts`, `RompSettings`) keeps what is a default across boards and
  across panes: the recency colormap, the sub-goals default, the panes on offer. A "Boards" block
  there is not added in these phases; a per-board default that a user wants everywhere is a later
  ask.
- **The palette** (`registerCommand({id, title, run})`, `ui/webview/palette-main.ts:122`) gets one
  command per board ("Open the <title> board", the view switch of section 4) and one per View menu
  row it does not already have ("Sort the board by ..."), so a keyboard user reaches every choice.

**Per-board buttons.** A board's buttons are its kinds' actions and menu (section 1), code by
kind; the notice kind's are the record's own stored actions. The user therefore sets no button in
the UI: a producer chooses a card's buttons by its kind or by the stored `actions` list, which is
what the ask's "different buttons than Background and Summary" reaches within the schema. A
user-configurable button set (hide the Summary toggle on every card of a board) is the same `boards`
map in the view state carrying a `hiddenSections: string[]` per board, one checkbox row per section
in the View menu; it is cheap and is written down here so phase five can take it if the user wants
it, but it is not promised. A button that does something new is a release (section 1).

## 6. Phasing and tiers

Each phase is one pull request, red-first per test executed at the base, the whole extension suite
and the feed's served labs on any head that touches the renderer, the whole Python suite on any
head that touches the kernel; the pins that hold today's literals are the proof that phase one moved
nothing.

1. **The definition extracted, byte-identical.** `ui/webview/board-def.ts` (a new small module):
   the schema's types, `FEED_BOARD` as in section 2, the kinds' `match` tests and their section and
   action lists pointing at the functions `feed.ts` already has, and the client half of
   `_board_check` for the definitions the frame will carry. `feed.ts` reads the definition at the
   sites listed in section 4 and nowhere else; no element is minted differently, no class or label
   changes. Proof: every existing pin passes unchanged (the forty-four `needs_input` files, the
   fifteen title files, the sixteen button files, `feed-col-head-case.test.ts`'s literal table
   re-aimed at the definition and asserted equal to today's triples), `feed-render-incremental.test.ts`
   boots the same frames and rebuilds the same elements, and a new `board-def.test.ts` asserts the
   definition's values against the literals this document cites and runs the schema check over it.
   Tier `feature`.
2. **The kernel's card records carry `board` and `category`, the feed as default.** A kernel table
   `_CODE_BOARDS = {"feed": <the schema's dict>}` beside `_NOTIFY_COLUMNS`, which becomes a read of
   it, checked by `_board_check` at import; the column expression stays inline (section 2's
   correction); every family's dict gains `"board": "feed"` and `"category": <its column>`;
   `_feed_notifications` and `_needs_you_count` read the table for the card's board. `column` stays
   byte-identical beside `category`. The frame gains two additive fields per card, both fixed across
   unchanged builds (the dedup holds), both per-session derived (inside the memoized entry, no new
   memo component). Pins: the payload census tests (`tests/test_kernel.py`, `test_notice_card_store.py`,
   the served feed labs) gain the two fields; `test_feed_memo_inputs.py`'s census reads no new input;
   a pin reads `board-def.ts`'s feed ids and asserts they equal the kernel table's, so the two
   copies of the one constant cannot drift. **Call-out:** additive fields on the frame with a
   default have landed as `feature` before (`notify`, `notice`, `delegTracked`); removing `column`
   later is the protocol change and would be a `major-feature` discussion, so `column` is not removed
   in any phase here. Tier `feature`.
3. **The board store, its door, and producers naming boards.** `define_board`, `_board_check`
   (the one validator, shared with phase two's import check), `STATE/boards/<id>.json`, `POST /board`,
   `GET /boards`, `romp board define | list | show | remove`, `_boards()` memoized on the directory's
   stat, the frame's `boards` field, `romp card -b -c` with the five-step resolution of section 3
   (create on first use, append a category on first use), `post_notice` storing `board` and
   `category` on the notice row, the owner-less rule as `FEED_BOARD.order = ["ownerRank"]` on the
   renderer side and romp_cards' reserved owner key on the kernel side. romp_cards owns the notice
   producer and `romp card`; the field names are agreed (2026-09-18) and the door's shape is this
   document's, so the phase is co-authored, in two pull requests on one seam agreed by mail
   (2026-09-18): first the store, `define_board`, `_boards()`, `remove_board`, `POST /board`,
   `GET /boards`, `romp board`, the frame's `boards` field and its reads in the renderer and the
   federation merge, `_notice_standing_count` (the notice store's reader the door's refusals call,
   handed over to the notice producer's owner afterwards) and a PURE `_board_resolve_post(board,
   category, *, needs_you, producer, key) -> (board_id, category_id, error, pending)` that runs the
   five-step resolution and returns `pending`, the definition to write (a new board with the
   defaults, or a data board with the unknown category appended in neutral dress); then, on top,
   `romp card -b -c` on the `/notice` body, `post_notice` calling the resolver and writing `pending`
   through `define_board` only after every other check of the post has passed, right before the row
   appends (so a refused post leaves no board behind, and a crash between the two leaves a board with
   no card rather than a card on no board), the row storing `board` and `category`, and
   `_notice_cards` copying them (a row without the fields keeps the phase-two mapping). Until the
   second lands, a card can name no board but the feed. **The tier, the user's ruling (2026-09-18):
   a REGULAR `feature`**, merged on green under their account by the manager; no linked issue, no
   maintainers' hold. (The recommendation this document made before the ruling, `major-feature` on
   the notice plan's reasoning of a contract to producers, is superseded by it.)
4. **A second board, the proof of the door.** The owner-less notes board, DATA-DEFINED: the lab
   runs `romp card -b notes -c new --title ...` against a hermetic kernel with no such board, and the
   board exists on the next frame with the defaults (one category `new`, chip neutral, newest first);
   `romp board define notes --json ...` then gives it the shape romp_cards' design names (a first
   cut is `new`, `kept` and `done`, `needsYou: "new"`, `notify: ["new"]`, a rule filing a
   `needsYou` post under `new`), and the lab reads the View menu's Board row, the switch, the per-board
   view state across a reload, the bell for `new` and the badge counting it, the palette command,
   and the `?board=` query for the docking kit. The feed renders byte-identically with the switch on
   the feed (phase one's frames, again), and a define dropping `new` while a card stands there is
   refused naming it. Tier `feature` (it adds no contract phase three did not; the shape of the notes
   board is romp_cards' to settle before the lab is written).
5. **Sub-sorts and per-board choices in the UI.** `subSorts` on the feed's constant (`t` both ways,
   `session`, `title`) and on data boards through the schema, the View menu's radio group, the
   `boards` map in the view state, the palette commands, and the optional `hiddenSections` if the
   user asks. Tier `feature`.

## 7. What stays out, and the risks

**Out.**

- **A general kanban product.** No dragging a card between categories (a category is the kernel's
  statement at an event, and a hand move would contradict the next push), no hand-made cards in
  the UI (`romp card` is the door), no due dates or assignees, no per-card manual order.
- **Cross-machine boards.** Federation merges per-host frames by concatenation (`mergeHostFeeds`,
  `ui/webview/federation.ts:517`), item ids unprefixed and `sid` prefixed. A remote card carries its
  `board` and `category`; the merge concatenates the hosts' `boards` fields with the local host's
  definition winning on an id, so a board defined on both kernels shows both hosts' cards under the
  viewer's definition, and a board defined only on the remote shows under the remote's shipped
  definition. No board store crosses kernels; `romp board define` talks to the local kernel, as
  `romp card` does.
- **A chat or timeline presence for a board.** As for notice cards: none.
- **Renaming the CSS column keys and the `Column` type** (`asks`, `needsInput`): a sweep with its own
  pins, after the boards exist.
- **Presentation beyond the schema** (section 1): a new section body, a new action, a fifth chip
  colour, a new sort key or predicate. Each is a release with its own pins, never a definition.

**Risks, and the design's answer.**

- **`feed.ts` is 6854 lines.** Phase one is a move of reads onto a definition, never a rewrite of the
  card builder; the incremental render harness (`feed-render-incremental.test.ts`) and the layout
  pins are the tripwire, and the phase is red-first at the level of "the definition equals the
  literals". The extraction of the card builder into a module of its own is not attempted here.
- **The judges' fourteen selectors.** A goal card's category stays a projection of `rollup_status`,
  and no board field is written into the goal store, so no selector (`open_menu`, `judge.py:6784`;
  `_turn_menu`, `:14321`; `_consolidate_tops`, `:13102`; the unblocker, the nudge walk
  `_nudge_fire_list`, `kernel.py:15453`; the anchor latches; the heal; `build_feed`'s root loop) sees
  a new node field. Notice cards stay outside the store (the notice plan's decision), and a
  data-defined board's cards are notice cards.
- **The feed memo's per-session key.** `board` and `category` are functions of the session's inputs
  the entry already reads, so they live inside the memoized entry and add no component to
  `_FEED_MEMO_LABELS`. The board table's code half is code, not an input; its data half is read
  once per build by stat and attached to the FRAME (`boards`), never folded into a session's
  key, so a define re-derives no session. That is the notice plan's lesson about a board-wide file
  applied in the other direction: a board-wide fact rides the frame, a per-session fact rides the
  entry. Post-time rule evaluation reads the table at the event, not at the build.
- **The dedup and the tint.** No new field derives from the clock; the owner rank reads `t` and the
  session order and sorts on the client; a definition carries no timestamp. The age tint is stamped
  per build as it is.
- **The pins as the proof.** The byte-identical claim rests on pins that hold text; a phase-one
  change that needs a pin re-aimed for anything but the definition's own literal is a change to
  what renders and is refused at review.
- **Two writers of one vocabulary.** The kernel table and the renderer constant name the same
  category ids; the pin of phase two reads both and asserts equality, and `_board_check` runs over
  both at import and at the door (one validator, the shared-bodies lesson).
- **A first-use board with a typo.** `romp card -b notse` creates a board named `notse`; the answer
  is the loud one: the `posted` line names the board it created ("posted to a NEW board notse"),
  `romp board list` shows it, and `romp board remove` takes it away once its card is dismissed. A
  confirmation prompt would break every scripted producer.

## Alternatives considered

- **A client-side rule per category** (the renderer buckets by a predicate over the card). Rejected
  in section 1: a render-time rule moves cards on inference flaps; the schema's rules run in the
  kernel at the post.
- **Code-defined boards only, the data door later.** Rejected by the manager's ruling (2026-09-18):
  a board per release lands the user back at a pull request per category, which is where `romp card`
  stood before this design; the door lands in phase three.
- **Two schemas, one for code and one for data.** Rejected: a data board must be able to say
  everything the feed's constant says (or the feed is not "one instantiation"), and one validator
  over both is what keeps them from drifting.
- **A `kind` field on every card.** Rejected in section 1: every family writes it for no rendering
  change, and the flavour objects already discriminate.
- **Shipping the code constants in the frame.** Rejected: a constant on the wire changes the
  protocol for nothing; only data crosses.
- **A pane per board now.** Deferred to the docking kit (section 4): one board would cost a shell
  change the kit makes a registry line.
- **Sub-sorts in the gear.** Rejected in section 5: the gear holds cross-pane defaults; the View
  menu is where the feed's sorts already live.
- **Refusing an unknown board or category at `romp card`.** Rejected for data-defined boards
  (create and append on first use are the ruling's "created on first use"); kept for the code-defined
  feed, whose categories the judges write.

## Privacy

Every example in this document, its tests and its labs uses the synthetic notes-api world (`web`,
`api`, `tests`, `TESTHOST`, placeholder session identifiers). A board's title and category titles
are user-facing strings and get no personal or other-project names; a definition file holds the
schema's members and nothing else, a producer's card text lives in the notice store under the state
root, as the notice plan says, and neither lives in this repository. The kernel log names a board
by its id at a define or a removal, never a card's text.
