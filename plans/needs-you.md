# Needs you: one category, one colour, a hard-stop mark, and a box at the bottom of the chat (2026-09-20)

**The ask (the user, 2026-09-20; paraphrased throughout, never quoted).** The feed's middle column, titled Blocked
today, becomes **Needs you** and holds everything the user can give feedback on: what a session cannot proceed
without, what it asked the user to clarify, and what it offered to do next. That is one category on purpose, since
an item moves between those states; a session fully stopped (a permission or approval prompt, or an API error only the
user can clear) is a **hard stop**, a mark on top of the category, never a
second column. The two dashed tab rings are renamed and one is recoloured: the red ring (today's Needs you row in the
settings' Tab widgets: a stopped session) becomes **Blocked**; the yellow ring (today's Waiting on you row: a card of
the session's in the column) becomes **Needs you** and turns **magenta**, because yellow already means working. One
colour for the Needs you category, used the same way across the software: the column's chip and header, the tab ring,
the card's accent, and the outline of the box at the bottom of the chat; red is reserved for the hard stop alone (the
Blocked ring, and a red mark on a card whose session is hard-stopped, a subset of the column). A box at the bottom of
the chat transcript lists the session's Needs you cards, one line per card with a way to act, outlined in the
category's colour, behind a settings row that is on by default. Completed must be safe to clear unread: nothing left
undone, offered as a next step or asked about may land there.

This note is phase one. Phase two is the renames, the chip on every surface and the colour; phase three is the box; the
judges' change that makes Completed safe to clear is the judges' owner's note, `plans/judge-prompt-experiments.md`, linked
below. The user signed the design off on 2026-09-20 with three amendments, paraphrased and folded in below: the box wears
the awaiting box's dress in the category's colour and holds the session's Needs you items that are not hard blocks; the
Blocked chip becomes the Needs you chip on every surface that shows it, the sessions pane's lanes and chips, the outline's
rows, the feed's column chip and header, the chat's status chip, the tag overview, the settings' demos and the guide; the
rest stands as written.

## The premises, checked in the code

Every claim the design leans on, read at `upstream/main` 55d8e8f0.

- **The category.** The feed's three categories are a code table in two places kept equal by a test: `_CODE_BOARDS["feed"]`
  in `kernel/kernel.py` (`{"id": "needs_input", "title": "Blocked", "chip": "blocked"}`, `needsYou: "needs_input"`,
  `notify: ["needs_input", "completed"]`) and `FEED_BOARD` in `ui/webview/board-def.ts` (the same triple;
  `feed-col-head-case.test.ts` and `board-def.test.ts` hold the header triples to it, `tests/test_card_boards.py` holds
  the kernel table to the pane's). The pane's column header chip is `.fcol-chip-blocked { background: #c0392b }`
  (`ui/webview/feed.css`), the modal's blocked node label the same red as a white-on-red chip (`.st-question .ftree-meta`),
  and the checkbox notation's blocked mark a red pause inside a red ring (`.fcheck.question .fcheck-mark`, `--err`: `#c0392b` in feed.css's dark root, `#B02A1C` in its light root,
  since that sheet has its own root). The category id `needs_input` is a wire
  value (every kernel and pane, federation included) and does not change; only the title, the chip class's colour and
  the copy do.
- **What files a card there.** The kernel's category expression in `_feed_session_entry` floors a card on a live
  permission or picker prompt (`_NEEDS_INPUT_STATES = ("permission", "picker")`, the card's `blocked.state`), on an API
  stop only the user can clear (the status flags `apiTooLong`, `apiSpendLimit`, `apiModelLimit`, `apiAuthErr`,
  `apiRefusal`, the same five `tabStateClass` reads for the red tab), on a judge's question verdict (a node with status
  `question`, the blocker's brief), on the idle hold of a blocking request, and on a held peer message (a notice card
  with `needsYou`), and on the judges' credential being refused (`_jauth_map`: the session's focus top floors with
  `blocked.state` `judgeAuth` and the `.fask-jauth` badge; the session itself runs, romp's analysis of it is down,
  and only the user can fix the key or the login). The hard stop is therefore already a distinguishable subset: the
  card carries `blocked.state` in the two prompt states, or its session's status carries one of the five API flags; a
  judge's question carries neither, and the credential refusal is a Needs you item without the mark by this note's
  own definition (the session is not stopped): its badge takes the category's token, and phase three's box row for
  it carries the credential fix as its action and no Clear, since a Clear would hide the fault while the refusals
  continue.
- **The rings.** `ui/webview/tab-state.ts` names three ring ids in precedence order: `ring-needs-you` (the RED ring;
  its test is `tabStateClass(s) === "tab-awaiting" || "tab-blocked"`, a live prompt or an on-you API stop),
  `ring-waiting-on-you` (the YELLOW ring; its test is `status.needsYou === true` and the tab not closed, the kernel's
  per-session read of the column in `build_session`), and `ring-retrying` (amber). The strip composes one ring, the
  first switched on whose test holds (`composeTabRing`); the folded group header's pip follows the same rule
  (`sectionPip`: `blocked` for the red ring, `ask` for the yellow). The sheet keys on the ring classes:
  `.tab.ring-needs-you, .tab.ring-retrying { outline: 2px dashed var(--state) }` where `--state` is the state class's
  colour (`--st-awaiting-bg #c0392b` for a live prompt, `--st-blocked-bg #e5484d` for an API stop), and
  `.tab.ring-waiting-on-you { outline: 2px dashed var(--st-ask-bg) }`, the ask yellow (`#f5d33f` dark, `#504100`
  light); the red ring adds a translucent red fill on an API-stopped tab. The settings rows are `RING_ROWS` in
  `ui/webview/tab-widgets.ts` (labels **Needs you**, **Waiting on you**, **Retrying**, with a description and a demo
  each), rendered by the gear from `ringWidgets()` under Tab widgets; the gear's demo tab wears the same classes
  through `gear.css`. So the ring today named Needs you is the hard stop, and the one named Waiting on you is the
  category: the user's renames put each name on the thing it means.
- **The ask yellow's other surfaces.** The same token paints the folded group header's pip (`.tab-group-pip.ask`), the
  phone picker's current-session chip border and row bar (`#mcur.ask`, `.mrow.ask` in the mobile page's inline
  sheet, `kernel/kernel.py`), and the settings' ring demo. `theme-parity.test.ts` pins the ring hue as a line on the
  page at 3:1 in both themes and its text pair, and the light palette's comment records the pairwise distances of every
  ring and dot hue under a red-green deficiency.
- **The chat's chip.** `ui/webview/status-chip.ts` is the one vocabulary for a session's state in a pill: the state
  `needsInput` (a live permission or picker prompt) reads **Blocked**, and the tag overview's rows say **Blocked** for
  a feed-filed card too (T322b, "the feed's column word"); **API error** is the on-you API stop. The guide's session
  paragraph says the chip follows the feed.
- **The bottom of the chat.** Two boxes sit between the transcript and the composer already: `#notices`, the approval
  box (`renderNotices`, plans/notice-cards.md "The chat pane's approval box"): the active session's standing needs-you
  NOTICE cards that carry actions, one row each, reconciled in place and keyed by item id, its clicks on one delegate,
  its rows a per-session slice `status.notices` on the session frame; and `#bg-tasks`, the background box
  (`renderBgTasks`). Both ride the transcript's bottom-box rule (a resize observer keeps an at-bottom reader at the
  bottom; `render.ts` names the three boxes `notices`, `bg-tasks`, `footer`). Neither has a settings switch today.
- **The colour red, three tokens.** `--st-awaiting-bg #c0392b` (a live prompt: the tab state, the column chip, the
  modal's chip, `--err`), `--st-blocked-bg #e5484d` (an API stop: the tab state, the API-error badge on a card, the
  group pip's `blocked`), and the alarm fill on a blocked tab. Magenta existed once more, as `--st-5xx-bg` (`#c026d3`
  dark, `#A21CAF` light) and the two literal inks of `.ah-c-r5xx` (`#e879f9` dark, `#86198F` light): the 5xx marks of
  the API-health cell's hover tip and detail (and the legend, on every hover), which sit on the LANDING page beside
  every session's state (`/perf` and `/api-health` answer JSON). Phase two moved the 5xx hue to a purple apart from the Needs you magenta and routed the inks through a token
  (below).
- **Notifications.** The bell and the phone push fire on a card ENTERING `needs_input` or `completed`
  (`_feed_notifications`, `notify: ["needs_input", "completed"]`); their copy names the card, not the column.
- **The docs.** `docs/guide.md` says Blocked seven times (the ring paragraph, the chip paragraph, the sessions
  overview, two chip glyphs); `docs/reference.md` twice (the restart notice card, an internal row name that stays).

## The design

### One category, Needs you, with a hard-stop mark on top

The middle column is **Needs you**. Its members are the cards the user can act on: a session stopped on a prompt or
an approval, a session stopped on an API error only they can clear, a question a session asked (the judge's
`question` verdict with its decision brief), a blocking request under the idle hold, a held peer message awaiting
approval, and the offers a session made of what to do next (the judges' change; see Completed below). The category
id stays `needs_input`; the title, the chip class's colour and every user-facing word change.

The **hard stop** is a mark ON a card in that column, never a place: a card whose session is fully stopped (its
`blocked.state` a prompt state, or its session's status carrying one of the five on-you API flags; a `judgeAuth`
state is not a stop and wears no mark) wears a red mark
where today's ⏸ live-block badge and ⚠ API-error badge sit (the same two elements: they are the hard stop already,
and they keep their red). A card in the column without the mark is something the user can answer at their pace while
the session goes on. The judge's question mark in the tree (the red pause in a ring, the white-on-red node label) is
NOT a hard stop and turns to the category's colour.

Why one category and not two (the road not taken): a Stopped column beside Needs you would split one decision across
two places and move a card twice as a session stops, asks and resumes; the user's steer was explicit that an item moves
between the states, and the card-move rule (cards move on new information, never on inference) prefers a mark that
appears and disappears on the card over a card that changes column.

### The rings: Blocked and Needs you

`ring-needs-you` (red, the hard stop) is renamed **Blocked**; `ring-waiting-on-you` (yellow, the column) is renamed
**Needs you** and recoloured to the category's magenta. The precedence stays red over magenta over amber, the tests
stay what they are (`tabStateClass` red states; `status.needsYou`), and the class ids in the code stay as they are
(they are wire-adjacent: `settings.tabWidgets.on` stores them by id, so a rename of the id would drop every user's
switch state; the labels and descriptions in `RING_ROWS`, the guide and the demo change). The folded header's pip
follows: `blocked` stays red, `ask` turns magenta. The phone picker's current-session chip border and row bar take
the magenta with the same token.

Keeping yellow (the road not taken): the ask yellow was chosen to sit apart from the working gold and the retrying
amber, and it does; but the user reads yellow as working, and a colour the user has to learn against their own
reading is the wrong colour. Keeping red for the column (the other road not taken): red on the column and red on the
hard-stop ring said the same thing about two different states, and most of the column's members would have worn a
magenta ring under a red header; the user asked for the inconsistency resolved in favour of one colour per meaning.

### The colour rule

One token, `--st-needs-bg` with its text pair `--st-needs-fg`, in `styles.css` and mirrored in `feed.css` (that sheet
has its own root; every state token is mirrored there today); the mobile page's inline sheet reads it with the dark value as its fallback (`var(--st-needs-bg,#d946ef)`), the pair itself arriving through styles.css:

- dark `#d946ef` on `#1e1e1e`: 4.8:1 as a line on the page, 5.2:1 for a dark text pair (`#2a0a2a`), 3.5:1 for white;
- light `#a21caf` on `#F1EAE2`: 5.3:1 on the page, 6.3:1 for white text.

Both clear the ring and pair floors `theme-parity.test.ts` holds (3:1), and the dark hue sits apart from the working
gold, the retrying amber and both reds under a red-green deficiency (the test's pairwise pins extend to it). The 5xx
marks of the API-health cell's tip and detail on the landing page (`--st-5xx-bg`, the histogram's `.ah-seg-serverErrors`
band; `--st-5xx-ink`, the `.ah-c-r5xx` digits in the tip's line and the detail's rows; the landing's inline sheet declares
both in rules of their own, mirrors of styles.css) are chosen by the validator's two floors (OKLab x100 at least 15 to
full-colour readers, at least 8 under the Machado protan and deutan simulations) against every colour each mark shares a
surface with, and by the contrasts each needs on every ground it sits on. Dark: the fill `#cb94d1` (a lilac) 6.90:1 on the
tip's ground and 6.19:1 over the graph's wash, from the Needs you magenta 17.0 / 12.0, the accent band 15.9 / 10.0, the
429 band 20.2 / 17.6, the other band 29.9 / 25.7; the ink `#8a8aff` (a periwinkle) 5.68:1 on the ground and 4.76:1 on the
detail's row hover wash, from the accent ink on its line 19.2 / 18.9, the 429 ink 25.4 / 21.0, the words gray 17.0 / 16.4,
the other-count ink 38.0 / 36.8, and from the magenta 17.4 in full colour and 2.7 under a deficiency, the ONE pair conceded
on purpose (no violet or blue ink clears the accent ink, the other-count ink and the magenta at once under red-green CVD
while reading 4.5:1 on the hover wash; the two never share a line). Light: one deep violet `#4c1b7e` for fill and ink,
11.90:1 on the tip's white, where the bars and the rows sit (9.97:1 on the cream page behind it), and 10.63:1 on the
detail's row hover wash, from the magenta 19.1 / 11.6, the accent 31.6 / 25.6, the 429 band `#e5484d` 35.4 / 25.1, the 429
ink `#B02A1C` 27.1 / 21.0, the words 19.7 / 18.2, the other band's indigo 18.9 / 18.7. The purples of phase two's earlier rounds (`#7e22ce`,
2.39:1 on the tip's ground; `#c4b5fd`, 8.5 from the accent ink it shared a line with; `#8b7ec8`, 3.93:1 on the hover
wash) were replaced by these under the reviews; theme-parity.test.ts pins the floors and the conceded pair.

Where the token paints, and only there:

- the feed's column header chip (`.fcol-chip-blocked` becomes the `needs` chip class; the chip class in the board
  schema, `"blocked"`, is a wire value on data boards' definitions and stays, so the class keeps its name and takes
  the new colour, and the schema's chip name is documented as the Needs you dress);
- the card's accent in that column: the judge's question mark and node label in the card's tree and the modal
  (`.st-question`, `.fcheck.question`), today red;
- the tab ring `ring-waiting-on-you` and the group pip `ask`;
- the phone picker's chip border and row bar;
- the outline of the Needs you box below (phase three);
- the settings' ring demo;
- the sessions pane's lane chip for a session in the column (`ui/romp-timeline-view.js`, `BADGE.needs`, drawn on the
  canvas in the same pair);
- the status chip's Needs you state, `.chip-needsInput` and its legacy twin `.chip-awaiting`, wherever the shared chip is
  painted: the bar under the transcript, the tag overview rows and the comment popover's statusline.

Red stays on: the Blocked ring and its translucent fill, the hard-stop marks on a card (the ⏸ live-block badge, the
⚠ API-error badge), the chat chip's **API error**, the unread passage's dashed box (a different meaning, the same
family, left alone by this note). Two card badges LEFT the red family in phase two, since neither is a hard stop: the
⚠ retrying badge (`.fask-retrying`) wears the retrying amber its ring, chip and lane badge already wear, and the
⚠ credential badge (`.fask-jauth`) is filled in the Needs you pair, filled against outlined kept.

### The status chip: Needs you on every surface

The chip that reads **Blocked** today becomes the **Needs you** chip, and the rename feeds out to every surface that shows
it (the user's second amendment): the chat's status chip under the transcript, the tag overview's rows, the sessions pane's
lanes and their chips, the outline pane's rows, the feed's column chip and header, the settings' demos and the guide. One
vocabulary (`status-chip.ts`, `CHIP_LABEL`) says the word once for the `needsInput` state and its legacy `awaiting`
spelling; the chip wears the category's colour (`.chip-needsInput` on the Needs you token), since the chip names the
category, and the hard stop is the ring and the card's red marks, never the chip's word. **API error** stays, red. Phase two
sweeps every surface for the word and the chip class, in the code and in the inline copies the kernel serves (the phone
page, the sessions pane), and the lab reads the word on each.

### The Needs you box at the bottom of the chat (phase three)

The box wears the awaiting box's dress in the category's colour (the user's first amendment): the same appearance as the
background-tasks box `#bg-tasks` today, a thin line around the edge, the same shape and the same placement between the
transcript and the composer, the line in the Needs you token instead of the await-green; and its rows are the shape the
closed requests pull request drew (#994, "Requests from sessions: what a session needs from you, held until you or it says otherwise": one line per request at the bottom of the transcript, Reply and Dismiss), one line per item with a way to act. It holds the session's Needs you items that are NOT
hard blocks: the permission and approval prompts the chat already shows inline stay out, and the box lists the judge's
questions (the card's title, its decision brief as the line), the offers a session made, the blocking requests under the
idle hold, and the held peer messages with their actions. The approval box (`#notices`, plans/notice-cards.md) already is a
row-per-notice box above `#bg-tasks` with a per-session slice on the session frame; phase three widens its slice to every
Needs you card of the session's that is not a hard block, with the kind on each row (a notice with its stored actions; a
goal card with **Reply**, which targets the composer at the card as Follow up does, **Continue** where the card offers it,
and **Clear**), keeps its in-place reconcile and its one delegate, and dresses the box as the awaiting box in the token,
titled **Needs you** with the count. It shows when the session has such an item and hides otherwise; an item leaves with the
frame that drops it (the answer, the judge's re-file, the clear). A settings row under Chat, **Needs you box**, on by default,
hides it; with the box off the tab (its badge, or the ring with the badge off) and the feed still say it.

The box's shape after its first weeks (the user 2026-09-23, who wanted the box collapsed by default like the awaiting box and opened in
steps, Reply and Clear only for now, and a switch they could find): collapsed to its header line until clicked, one click the items, a
second the full context, the level the page's state for the session and never a timer; the goal row offers Reply and Clear, the Continue
offer stored and its wire kept for a later return with no button; the switch heads its own settings section, **Boxes below the transcript**,
under Chat. Since then: while a judges' credential row shows, the box stands open at the items (the floor), so the fault is never hidden under
the header line; the header takes the keyboard (a button with a tab stop, Enter or Space advancing the level), its title naming the step the next click
takes (**Show the items**, **Show the full context**, **Hide the full context**, or **Collapse** where the next click folds the box to its header
line); and in the shell and VS Code the header's bar carries the box's own gear, which opens the settings at that section.

The row carries what the card carries (the user 2026-09-23, from a screenshot of a row at the items level that showed its title and
two buttons alone). At the items level a row shows the card at a glance: the title, the card's distill line under it (the decision brief or
the takeaway, clamped to four lines with More past it) whatever section is picked, since the pick governs the full-context level only
(the manager's ruling on a contributor's review, 2026-09-24), the state badges the card wears on its name row
(re-judging, done confirming, follow-up failed, interrupting or interrupted, the warning chip with its evidence, awaiting a peer, a delegation's origin or handoff), then Reply and Clear. At the full-context level
the row shows the card's whole disclosure: the same section toggles in the same order (Background, Summary, Stalled, the sub-goals,
Awaiting task, each present when the card has it), the same one-open rule with the same default, driving the same section bodies (the
background paragraph, the distill line with its paragraph stamps, the stall note, the sub-goal tree, the awaited-task list), and for a
notice row the notice's body and attachment. One builder draws both: the card's section toggles, bodies and distill line move into a
shared module that the feed and the chat page both import, as the notice face already is, and the row joins the card's twin set for
the item, so which section is open is one state and never a second copy. The session name and the age stay off the row: the box is the
session's own page. The kernel's row carries every field the card's sections read, from the same feed item. The one open section is one state across the shell's two documents: every write to the choice goes through the shared module's setter, which carries it over a channel from the feed page (the owner, which persists it) to the chat page (a follower, which asks for the map when it loads), so after a reload the row opens what the card opens. In VS Code the chat and feed webviews are separate origins and the channel crosses nothing: each page keeps its own choice there, and a fallback through the extension host is deferred to after the release (the freeze of 2026-09-23). Two changes to the card itself rode the shared builder (the reviews of
2026-09-24): the card's badges, its distill line and paragraphs, its awaited peers and its sub-goal triangles route their clicks through one
binding on the card (actions.ts delegate), so they wear the repository's short press pulse like every delegated control; and the provenance
anchor paints a separator between each of its facts, "from a · delegated to b · delegated to c", the handoff and a tracked delegation included. The feed
answers a row's pick with an acknowledgement, and the chat page holds its own pick only until that word arrives (a stale acknowledgement
spares a newer pick), so the maps that lack the pick afterwards, the Collapsed clear and the prune once the item leaves, govern both
documents (the 0.17.1 fix). A flip with no pick held leaves the map unchanged, so no map crosses: the feed's setter re-applies its
cards anyway (a caller that is not quiet gets the re-apply, since the default the cards resolve against moved), and the chat page
re-renders the box from its own listener on the settings key, so the default follows at once in both documents with no payload
between. The row's default section follows the feed's Collapsed flag everywhere, through the settings fan-out: the browser shell and
a standalone chat tab read the origin's shared storage, and a VS Code chat webview gets the gear's whole settings object relayed by the
extension host and writes its own copy, so its rows follow the flag as its cards do; only the picks stay per page in VS Code, since the
section channel does not cross its webviews. On the feed, a
sub-goal row's text and mark keep the modal's own click zones (wireNodeZones) rather than the delegated acts; on the chat page every
rebuilt click is delegated.

Every clear or undo account the kernel sends carries its Undo stack (`batches`: the ids an earlier undo left owed first, then the
clears log's batches by stamp, newest first; `owedBatch`: the owed ids alone; `batchesTotal`: the count of log batches before the
wire's bound, so a truncated stack reads as truncated), except any sent while the clears log cannot be read (a refused clear alone
ships none too), and the feed takes it as its own stack, so the
optimistic Undo restores what the kernel restores; the two are proven equal by enumeration over every press sequence of a
two-card world under every fault (tests/fixtures/undo-stack-transitions.json). The stack on the wire is bounded to the newest
20 log batches (`LEDGER_BATCHES_ON_WIRE`); the frame says how many there are. Past them, a page that did not make those clears (a
reload, another browser) takes the round trip, while the page that made them keeps their entries below the window and restores
them optimistically, which is what the kernel pops.
The optimistic Undo and that stack hold on a single-kernel pane, where the proof holds. A pane is federated when more than one
kernel is attached, read from federation's own account of the attachment and never from sessions or cards: a configured host that
is pending or down counts as attached, and so does every kernel whose build the merged payload carries, cards or none, so a card's
coming and going cannot flip the reading. On a federated pane Undo is the round trip. No entry is cached and none is popped, the
button shows the working cue, and federation routes the request to the kernels of the most recent clear and hands the panes the
kernels it went to. The send consumes that routing, so a second Undo goes to the local kernel alone; a kernel's account of that
undo, a refusal or the landed reorder's information frame, re-arms it for every kernel that answered, until the next send, unless
a clear was routed in between. The routing is read from its owner rather than shadowed on the page, since the send consumes it
and the board's Clear all fans out to every kernel, two things a page-side record could not track.

The click releases no suppression. At federation's word the page writes a restore check on every card then suppressed on the
kernels the undo went to, each with the build of its kernel the page had seen at that moment (on a federated pane alone, read at
the send: a single-kernel pane released at the click and writes none, though the frame reaches it too), and a suppression ends on
the evidence that the card was restored: a payload from the card's own kernel that lists the card, built after that kernel
processed the undo. The kernel names that point on every undo account as a build floor, its feed build counter as the undo was
processed, on the refusal and reorder frames and on an ack for an undo that landed with nothing else to say; each undo the page
sends carries a sequence of its own, which the kernel echoes on that account, so the floor lands on the checks written for that
undo alone (two undos to one kernel with a clear between them keep their floors apart). A build past the floor
read the store after every clear that socket sent before the undo had applied and after the undo's batch was restored, so a card
it lists is restored; a build claimed before it, however it is stamped, is no evidence, and until the account lands the check
releases nothing (the card stays off, and the absence rule stands). A later undo's account from the same kernel releases that
kernel's older checks that have no floor, since the pressing socket delivers in order and their accounts will never come, while a
check with a floor keeps waiting for evidence. The account rides the pressing client's own socket, which a
redial can abandon, so a kernel's socket coming back drops that kernel's waiting checks and their suppressions, on the socket's
own reopen (the local shim's `wsup`; a remote relay socket's `romp:hostRelayUp`, the exact event a redial fires) and, as a second
trigger, on the tunnel poll's word that a remote kernel came back (`hostUp`): a listed card is never covered by the absence rule, and a lost account must not hold a
restored card off the board until a reload. An older kernel sends no account, which its payloads say
(`undoAck` on a kernel's frame, `ackHosts` on the merge), and its checks take the build seen at the send as their floor, as
before. A stale held frame cannot release a suppression; a kernel the undo never reached never lists a restored card past its
floor; a kernel that restored an older batch shows exactly what it restored; a card cleared after the send has no check, so no
payload releases it by evidence. A build below the one last seen from a kernel is its restart, since the counter is per process: the check and its floor re-base to
the new life and that payload judges the card. A kernel with no build on record can give no evidence, so its suppressions take
the click-time release instead, never a baseline of zero. A remote kernel's account re-shows the cards of a refused clear by
their ids and touches no stack. The working cue clears once every kernel the undo went to has built past the send, on an account
from one of them, or on the backstop; an account from one kernel of a fanned-out undo clears it while the other kernel's payload
is still in flight, an accepted residual. The reason for the round trip: stamps across kernels do not order, so a merged stack
cannot say which kernel's batch the next Undo reaches, and an optimistic pop would restore one kernel's card while another
restored its own.

Every writer of the clears log says an append it refuses, one stderr line and one judge-errors row (`clears-log`) per refusal, and
the kernel's own reader says a present log it cannot read or decode, once per fault episode (`cleared-unreadable`, the kind the
judges' readers of the same file use); an Undo over such a log restores nothing and its account says so, marked as the read's fault
(`readFault`), and the page takes back the restore the click made on that marked account alone, by the undo's sequence; an account
without the marker (an owed-note refusal beside a landed undo) settles nothing about the click. The episode boundary's settle is the one writer without a retry: its head is
recorded before its clear rows, so a refused append leaves the pre-clear open cards in the fresh conversation, and that settle is
lost; the row and the line name the session.

While the clears log cannot be read, the pane holds. The feed build, the off frame and the chat box's notice rows take the last
landed set the reader memoized, so the dismissed count, the Undo button, the foreign clears and the log-only seals stand as the last
landed read left them until a read lands (a press of that held Undo is refused with the dialog, the read-fault account, since an
undo answers for its own read), and the bell carries one refused row per fault episode naming the file: the display
readers' convention (the last-known value shown, one row, a clean read ends the episode). A cold memo has nothing to serve: the pane
shows nothing cleared and the row says so. The gestures never take the memo's set: an Undo answers for its own read, and an
account's stack for the read behind it. Every clear or undo account carries its stack except any sent while the clears log cannot be
read, a refused clear alone shipping none too, so a stack-less frame from the current kernel is that road beside an older kernel's
frame and a federated pane's. A served read carries its state's fault into the episode: a set served from the memo after a fault
that lifted with the file unmoved ends the episode, so the same fault before the next append is a new one and is said again. The
episodes end on three arms: a landed parse whose post-parse stat and pre-read flag both stand where the read found them, a served
landed hit, and an absent log. A set parsed from bytes that left the disk before a fault, or while a fault was filed, ends nothing
(the nudge walk parses beside the display builds) and is kept as the last landed set by an order: it wins when its pre-read stat's write
time is newer than the standing set's (a later write to the path: a create-and-rename, an unlink-and-create, an in-place truncating
rewrite, shapes the inode cannot tell apart on ext4), at an equal write time the parsed length decides (the log is append-only within one
file, so of two parses of one file the longer is the newer state whichever of two racing moved reads writes first; the settled arm
writes unconditionally), and an older write time loses whatever the length (the removed file's parse against the recreated file's landed
set). A path-only key on either side, the absent arm (which records length zero) or a failed pre-read stat, falls back to the length
order, and so do two writes within one mtime tick and a clock step backwards; a memoized fault returning through a memo hit after a
different fault files its judge row, so one row per episode holds across every ending.

### Completed is safe to clear unread

Nothing left undone, offered as a next step, or asked about may land in Completed: those are Needs you's. That is the
judges' change, whose design, measurement and landing gate are the judges' owner's note,
`plans/judge-prompt-experiments.md`: the closer's optional-offer clause inverted as the first candidate, the planner's
done op and the unblocker's moot rule as the second and third surfaces. This note's part is the contract the column
relies on: a card in Completed asks nothing of the user, so a Clear all over Completed loses nothing.

## Phases

1. **This note** (docs).
2. **The renames, the chip on every surface and the colour**, one PR (feature): `RING_ROWS` labels and descriptions;
   the token in the three sheets; the column title in both tables; the chip class's colour; the tree and modal question
   marks; the group pip; the phone picker's two rules; the chip's word in `status-chip.ts` and its colour, and every
   surface that shows the chip swept for the word and the class (the sessions pane's lanes and chips, the outline's rows,
   the settings' demos, the kernel's inline copies); the guide's ring, chip and overview paragraphs and the two chip glyphs;
   the reference's restart card line; the settings' demo. A served lab reads the COMPUTED colours on every surface in both
   themes (the column chip and header, a card's question mark, the modal's node label, the tab ring and the group pip, the
   phone picker's chip border and row bar, the settings' demo tab) and the WORDS on every surface that shows the chip (the
   two ring rows, the column header, the chat chip, the overview row, the sessions pane's lane chip, the outline's row).
   Every pin on the old title and the old ring names moves in the same commit.
3. **The box** (feature): the widened slice, the rows by kind, the awaiting dress in the token, the switch, and a chat
   lab: a card entering the column shows a row; Reply and Clear each remove it (Continue's button left the row on
   2026-09-23; its offer and wire stay); a hard block shows no row; the switch hides the box and leaves the ring.

## Tests

- `theme-parity.test.ts`: the new token pair and the ring line in both themes; the pairwise distances extended.
- `tab-state.test.ts`, `tab-rings.test.ts`, `tab-widgets.test.ts`: the labels and the colour class; the precedence
  and the tests unchanged.
- `board-def.test.ts`, `feed-col-head-case.test.ts`, `tests/test_card_boards.py`: the header triple's new title,
  the two tables equal.
- `status-chip.test.ts`, `chip-label-case.test.ts`, `tab-snapshot.test.ts`: the chip's two words.
- The served labs: the colour lab above; `test_tab_widgets_browser.py`, `test_feed_focus_served.py`,
  `test_held_mail_chat_served.py`, `test_kernel_mobile.py` re-pointed to the words and the token.
- Phase three: a chat lab for the box (a row per item that is not a hard block; Reply and Clear each remove
  theirs; the switch hides the box and leaves the ring).

## Privacy

Synthetic fixtures only (the notes-api demo world, placeholder ids, `TESTHOST`); no session names or people in the
text or the tests.
