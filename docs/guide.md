# Guide

This guide covers how to use Romp and how its back end works.

## The Romp user interface

Romp gathers all your Claude Code sessions into one interface, with five
complementary views of what the agents are doing:

- **[The chat](#the-chat)** is the regular interface for talking to a coding
  agent, with features that make a long session easier to scan.
- **[The feed](#the-feed)** is Romp's task-management layer: what is in
  progress, what needs your input, and what is done.
- **[The timeline](#the-timeline)** is the history of what each session worked
  on and how they coordinated; click any part to jump to that moment in the
  chat.
- **[The outline](#the-outline)** lists every session with its tasks, for
  reviewing what a session has done and searching across all of them.
- **[Files](#files)** holds the file viewer in a column of its own, so a file
  stays open beside the chat and the feed. Off by default.

### The chat

![Tool calls fold into runs; each expands to one line per call](assets/guide/chat-detail.png){ width="100%" }

**Reviewing a document.** Select any passage in the file viewer and it lands in the
composer as a quote chip, labeled with the file and the line the passage lives on. Type
what should change and press **⌘⏎** to set the note aside; keep reading, select the next
passage, and repeat — each staged note remembers its quote and its place. The list above
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
says why (the file is not in a git repository or not committed — untracked, staged but in no
commit, or on a branch with no commits yet — the repository has no origin remote, its origin
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

**Naming another session.** Type `@` and the first letters of a session's name in the
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
session is doing; the message itself still carries the `@name` you typed.

**A message that has not gone yet.** Send to a busy session and your message waits as a
dashed bubble under an hourglass until the session takes it — while it compacts, while a
turn runs, or in the beat before the kernel confirms the send. Until then it is still
yours: the **✕** in its corner pulls it back into the composer, and the **✎** beside it turns
the bubble's text into a field where it sits, so you can change your mind without losing
your place in the queue. While the field is open the message holds: it does not go until you
are done. Enter (or Save) replaces the message where it was, a follow-up keeps its context,
and Esc (or Cancel) leaves it as it was; the composer is not involved. If the session took
the message before you could hold it, the bubble says so, and if an edit cannot be applied
romp gives your words back in a notice rather than sending them twice.

**Opening a markdown document.** A markdown link in the chat opens in the file viewer,
rendered, with **Raw** one click away — a path on the session's machine, or a link to a
file served from the dashboard's own address (a published report, an evidence doc). Figures
and links inside the document resolve relative to the document, so a `![fig](fig.png)`
beside it shows, and a link to a sibling document opens in the same viewer. Links to files
on other sites open in a new tab, as before — and a ctrl- or ⌘-click still opens the file in
a tab. The document is set for reading: a sans face at a slightly larger size, headings in
proportion, a centred column about 80 characters wide, and task lists, keyboard keys and
aligned table columns as GitHub shows them. Every code block is numbered by line and carries a
**Copy** button that copies the block as the file holds it, tabs included; fences labelled
`rust`, `go`, `c`, `java`, `sql` or `toml` are highlighted, in addition to the languages the
chat already knows. Printing the page while a rendered file is open prints the file alone,
black on white, across as many pages as it needs.

**A file's own HTML.** The Rendered view keeps the HTML a markdown file carries, under rules
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

**Text size and width.** The **A−** and **A+** buttons in the viewer's title bar make the
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

**Opening a PDF.** A PDF the session mentions, or one you click in the file browser, opens
inside the dashboard like an image: the chat's PDF card opens it full-view, a path or a
file-browser row opens it in the file viewer. Cmd-click it instead (Ctrl on Windows and
Linux), or middle-click, and it opens in a new browser tab in the browser's own viewer, the
way a paper opens from OpenReview: full size, and it stays open beside the dashboard while
you keep working. If the browser blocks that new tab, the PDF opens inside the dashboard
instead; a PDF too large to show offers a download in its place.

**Links in a file.** Wherever the viewer shows a file's text, the links in that text work. A
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
a chat message gets: a path links only when it has a slash and a file extension, starts on its
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

**Tags and groups.** A tag is a named, colored set of sessions; a session can be in
several. Right-click a tab and open **Tags** to add or remove them. Tags filter every
surface (the tag button in the strip narrows the tabs to the tags you pick), and they group
the tabs: as soon as any session carries a tag, the strip shows one section per tag, in your
tag order, each with a header in the tag's color, and the untagged sessions on a row of their
own at the end. A session with several tags appears under each of them; every copy is the same
session (click either to open it, and closing either ends it). Each header shows the tag's color and name, then a chevron and a
member count. Click a header, or press Enter on it, to fold its section down to the header
alone; the count then says how many tabs are folded away, and a small dot after it shows when
one of them is busy or needs you: red when one is blocked or waiting on you, otherwise gold
when one is working, otherwise amber when one hit an API error and is retrying on its own
(hover it for their names). To keep one tab visible while its section is folded, right-click
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
**+** adds the tag without moving the tab. **Group tabs by tag**, at the foot of the tag
button's menu, turns the sections off for this browser. Every group starts on its own row; turning off
the gear's **One tag group per row in the tab strip** lets the groups follow one another across the
strip and wrap as they need, with the untagged sessions behind a thin divider, so a strip with many
tags stays short.

**A section at a glance.** Clicking a header also shows the section in the transcript's place: one
row per session, with its color, a dot for its state (yellow working, red stopped on a prompt or an
API error only you can clear, amber retrying an API error on its own, teal compacting, green waiting
on background work, none while it is idle), a **needs you** or **waiting** word, what it is doing
now in a few words, and how long ago it last did anything. **Needs you** appears when the feed shows
one of the session's cards under Blocked, or when the session is stopped on a prompt or an API error
only you can clear; **waiting**, when it is waiting on background work. A session that asked a
question and went quiet shows the word with no dot: the dot follows the session's own state, the
word follows the feed. What it is doing now comes from its current task, else from the headline of
its work so far, else from the last task it had; a session that has published a note of what it is
working on shows the note as a quieter second line. Hover a row for its last message, shown without
its formatting; click one to open that session, which also opens its section if the section is
folded (with several tags, the first folded group of them). The rows update as the sessions work and
change only when something about a session changes; the **needs you** word follows the feed, one
refresh behind it at most. The transcript comes back when you pick a session, press Escape, or click
that header again while its section is open and holds the tab you are reading.

**Coming back after a dropped connection.** When the dashboard's link to the kernel
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

**On a small screen.** To keep more of the transcript in view, turn on the gear's
**Compact tabs and agents** setting. It tightens the rows in the background-work panel above the
composer (the one headed **Awaiting** or **In the background**) and shows about four of its rows,
scrolling for the rest; the cap lifts while a row's details are open. Where the tab strip is showing,
it also shrinks the tabs and group headers; on a phone the session picker stands in for the strip, so
there the setting tightens the panel alone. Like the other chat settings, it is per browser.

### The feed

The feed is Romp's task-management layer: a card for each task. Romp's
[judges](judges.md) watch each session's work, split it into those tasks, and
keep every card current.

Cards sit in three columns:

- <span class="romp-chip romp-chip-working">Working</span> — the session is
  actively working on the task.
- <span class="romp-chip romp-chip-blocked">Blocked</span> — it needs your
  input to move on.
- <span class="romp-chip romp-chip-completed">Completed</span> — done, ready
  for you to review and clear.

![The feed's three columns, with the cues on a card](assets/guide/feed-annotated.png){ width="100%" }

<span class="romp-btn">Background</span> is why the agent is taking the action,
and <span class="romp-btn">Summary</span> is what it did. When a task divides
naturally into parts, the card's <span class="romp-btn">Sub-goals</span> button
opens them.

Cards follow the work rather than the session: one session can hold several
tasks, and a task can be handed from one session to another.

Press <span class="romp-btn">Clear</span> on a card when you are done with it. A
cleared card is archived, and no more work is added to it.

### The timeline

Each row is one session. A bar is a stretch where the session was working, and a
circle is a message you sent. A striped stretch means the session is blocked,
waiting on your input.

![A timeline lane per session, with status and context at the left](assets/guide/timeline-annotated.png){ width="100%" }

Click a bar or a message marker to jump straight to where it happened in the
chat.

Session statuses:

![Each session state and what its color means](assets/guide/status-legend.png){ width="70%" }

### The outline

Every session with its task tree: open work stays up top, finished work folds
beneath. Open the outline to review what a session has worked through, or to
find past work: the search box reaches every session, live or closed.

![The outline: each session's tasks as a tree](assets/guide/outline.png){ width="100%" }

### Files

The Files pane holds the file viewer in a column of its own, beside the chat
and the feed, so an open file covers neither. While the pane is open, a file
link clicked in the chat opens in it. When it is closed, the gear's **File
links open in** setting decides where a link opens: over the pane you clicked
(the default), or in the Files pane, which then opens and stays open; on a
phone, closing the file takes you back to the tab you came from. The folder
shown under the chat (the session's working directory), the **Directory** row
of the **System context** card and **Browse files** on a tab's right-click menu
open a listing of that folder by the same rule: in this pane while it is open
or when the setting names it, otherwise over the chat. Pick a file in the
listing and it opens where the listing is. Selecting a passage in the viewer
puts the quote in the chat's composer, as it does from the viewer over the
chat. When no file is open, the pane lists the files most recently opened in
it; click one to open it again. The pane is off by default; the bottom bar
turns it on, and on a phone it is a tab like the others.

## Automatic nudges

Agents stall: they hit an API error, they get interrupted, or they end a turn
leaving it ambiguous whether a task is done. Romp nudges a stalled session with
an injected message, so every task ends up either explicitly done or explicitly
needing your input.

Romp asks the agent, item by item, where each open piece stands: continue what
it can, and say what blocks the rest.

- If the agent can keep going, it does, and you were never interrupted.
- If something needs you, the card flips to <span class="romp-chip romp-chip-blocked">Blocked</span> and names exactly what
  it needs.

Nudging engages only when you are not actively messaging the session, so it
never talks over you and never loops on its own messages.

A session can also be waiting on something unrelated to you: it dispatches work
into the background, then pauses for the result. In that case it shows an
<span class="romp-chip romp-chip-await">Awaiting</span> chip. The chip clears on the
session's next turn, when the task finishes or blocks, when you clear the card,
or as soon as you reply.

## User todos

A user todo is a request a session files with you when one part of its work
needs something only you can give: a decision, a credential, a review of a
draft. The session keeps working on everything else, and the request stays in
view until you answer it, you dismiss it, or the session withdraws it. The
feature is off by default; [Turning it on](#turning-it-on) below says where the
switch is.

Without it, the request disappears. The `api` session of `notes-api` reaches the
login routes, finds it cannot pick the auth scheme on its own, says so in one
sentence, and moves on to the routes that need no login. Its card stays in
Working, the transcript keeps scrolling, and the sentence sits in the middle of
a long turn. You learn about it days later when you reread the transcript, or
never. Romp's [judges](judges.md) cannot catch this reliably: they read the
transcript and infer, and a turn that ends on progress reads as progress. A user
todo is the session stating the need outright, in a place that inference cannot
erase.

### What you see

**The card by the composer.** The card at the bottom of a session's
transcript lists the agent's own checklist under a **to-do · 0 of 3 done** head
(the picture shows an older spelling of that head).
When the session has open requests, the card gains a second section, **Waiting
on you · N**, with one row per request, oldest first. Each row is the session's
one-line request. A small "▸ details" hint after the line means there is more
behind it; click the line to open it, and a file path in the details opens the
file. Each row has two buttons:

- **Reply** opens a small dialog that quotes the request. Type your answer and
  press Enter. On a phone, Enter starts a new line and **Send** sends. The
  answer reaches the session as a message from you, prefixed with `Re:` and the
  request's own line, so a short answer such as "session cookies" lands without
  ambiguity. The row leaves the card.
- **Dismiss** clears the request without sending anything. Press it twice; the
  first press asks you to confirm. Use it for a request that is moot or stale.

Each section appears only when it has rows, so a session with no open requests
shows the card it always did. If the kernel cannot read its request store, the
card says so in place of the requests and names the file; Reply and Dismiss
change nothing and say so, and the kernel log has the cause. Fix or remove the
file and the card reads again.

![The card by the composer: the agent's checklist above, the requests waiting on you below](assets/guide/user-todos-card.png){ width="100%" }

![The first request opened: its details, with file paths as links](assets/guide/user-todos-detail-open.png){ width="100%" }

![The Reply dialog quotes the request and its details above the box for your answer](assets/guide/user-todos-reply-modal.png){ width="100%" }

Reply quotes the request; the answer lands in the chat as a message from you,
and the row leaves the card:

<video src="../assets/guide/user-todos-reply.mp4" controls loop muted playsinline preload="none" data-romp-autoplay width="100%"></video>

**The tab.** A session with open requests carries a ⚑ after its name in the tab
strip. The flag says that something in that session waits on you; the card says
what. Tabs carry no counts.

![A ⚑ on the tab of a session that has asked you for something](assets/guide/user-todos-tab.png){ width="232" }

**The feed.** Every card of that session wears a small "⚑ waiting on you"
marker, with the number of open requests when there is more than one; click it
and the session's chat opens. While the session is still working, its cards
stay where they are: it told you what it needs, and it is not waiting on you
for the rest.

![The feed while the session still works: its card wears the marker with the count of open requests and stays in Working](assets/guide/user-todos-feed-working.png){ width="100%" }

When the session goes idle with a request still open and nothing else in
progress (no turn running, no background work awaited, no reply owed to it by
another session on the same machine), the request is all that is left of its
work, and the card for its current work moves to
<span class="romp-chip romp-chip-blocked">Blocked</span> with a red
"⚑ waiting on you" badge in place of the marker; the session's other cards keep
theirs. Hover the badge and it says that the session has run out of work it can
do alone and is waiting on what it asked you for. A session with no card at
that point gets a placeholder card in Blocked, titled with its oldest request.
The card returns to Working when you answer or dismiss the request, or when you
next speak to the session; if other requests are still open when the session
settles again, it returns to Blocked for them. Work set off by another
session's message, a reminder, or a notification does not by itself move it:
the card stays in Blocked until you act or the session withdraws the request.
What that work runs into still shows while it lasts: if the session sends out
agents, compacts, files another request, or hits a passing API error, the card
reads Working until the session settles again; if it stops on a permission
prompt or an error only you can fix, the card keeps its place and wears that
badge instead. Either way it comes back for the request at the next settle.

![The api session idle on two requests: its task cards keep the marker, and the card for its current work sits in Blocked](assets/guide/user-todos-feed.png){ width="100%" }

The session goes idle, its card leaves Working for Blocked and takes the badge,
and the three task cards the judges file next wear the marker:

<video src="../assets/guide/user-todos-escalates.mp4" controls loop muted playsinline preload="none" data-romp-autoplay width="100%"></video>

**The badge and the bell.** If you run the dashboard as an installed app on your
phone, the count on its icon is the number of things only you can move: open
requests, plus sessions stopped on you for another reason, such as a permission
prompt. For a session moved to Blocked by its own requests, the requests are
counted and the card itself adds nothing. Romp announces the move to Blocked the
way it announces any other block, once per request: a card that returns to
Working and comes back for the same request does not announce again, and the
placeholder card is silent.

**Sessions that are hidden, ended, or asleep.** Hiding a session from the feed
(right-click its tab, **Hide from feed**) hides its markers, its move to
Blocked, and its share of the badge; its tab keeps the ⚑, because the tab
describes the session itself. A session that has ended keeps its requests out of
every surface until you revive it; they are hidden, not cleared, and come back
with the session. A session that is only asleep after a kernel restart is still
listed, and Reply wakes it with its history intact.

### Turning it on

The feature is off by default. The gear's **User todos** checkbox (under
*Sessions*) turns it on for the machine whose kernel you are looking at; each
attached machine keeps its own setting. While it is off, sessions on that
machine are not offered the tools that file or withdraw a request, nothing is
listed, nothing is handed back to a session on resume, and the count on the app
icon is what it was before the feature existed. Flipping the switch reaches
sessions that are already running: within a few seconds the tools appear or
disappear for them, no restart needed. Requests filed earlier stay stored and
reappear when you turn it back on; at startup, the kernel's log says how many
are waiting.

![The gear's User todos checkbox](assets/guide/user-todos-gear.png){ width="518" }

### What the session sees

While it works, nothing changes for the session: it filed the request and moved
on. Your reply arrives as an ordinary message from you, quoting the request it
answers. A dismissal sends nothing.

After a restart, a revival, or a compaction, the session has lost its working
memory, and with it the memory of what it asked you for. Romp hands it a short
list of its open requests as context, phrased as its own notes: what it still
has open with you, each with its id and the date it was filed, newest first, and
a line inviting it to withdraw any that are met or moot. The list costs no turn;
the session reads it when it next works. A session with no open requests
receives nothing.

A session you clear with `/clear` is not handed its open requests back: it
starts a fresh conversation, and what it asked for stays in view on its card,
its tab, and the badge until you answer or dismiss it.

After a kernel restart, Reply on the card wakes the sleeping session with its
history intact; it answers, withdraws the request the answer made moot, and the
section empties:

<video src="../assets/guide/user-todos-resume.mp4" controls loop muted playsinline preload="none" data-romp-autoplay width="100%"></video>

### How a session files one

A session gets two tools alongside its mail tools (see
[Inter-agent communication](#inter-agent-communication-the-romp-postal-service)):

- `add_user_todo` takes one short line, what it needs and why, and an optional
  `detail` for context the line cannot carry. It returns an id. The line holds
  up to 500 characters and the detail up to 4000; a longer one is refused rather
  than cut short, and the session is told to keep the note to one line and put
  the rest in its reply. The tool's description tells the session what
  qualifies: something it is waiting on you for, never a status update or an
  FYI. It also tells the session to withdraw the request the moment the need is
  met.
- `withdraw_user_todo` takes the id and takes the request back. Withdrawing a
  request you already answered or dismissed, or one the session already
  withdrew, gets a plain answer saying so, with the time, and no error: the
  need is met, which is what the session wanted. An id that is unknown or
  another session's is refused. Neither is a silent success.

The session files a request in the middle of its turn; the section appears
under the transcript and the tab gains its flag:

<video src="../assets/guide/user-todos-filed.mp4" controls loop muted playsinline preload="none" data-romp-autoplay width="100%"></video>

A request filed by a subagent belongs to the session the subagent works inside,
because you talk to the session, not to the subagent.

Nothing prompts the session to file a request beyond the tool's own description,
and nothing forces it to withdraw one. A session that forgets to withdraw leaves
a moot request in view until you dismiss it. That is the accepted cost: a stale
visible request costs a glance and a click, and a vanished one costs whatever
was asked.

### What it deliberately does not do

- Romp never clears a request on its own. The judges that keep the feed current
  cannot answer, dismiss, or withdraw one, because they infer from transcript
  text, and inference has erased this kind of request before. Only you or the
  session can clear it.
- Romp never asks a session whether it still needs its open requests. An idle
  session almost always does, and asking would spend a turn per session to learn
  nothing.
- Tabs carry no counts, the feed has no column or strip for requests, and a
  request has no priority, deadline, or edit. To change one, the session
  withdraws it and files another.
- Waiting on another session is not waiting on you. A session whose only open
  question is to a live session on the same machine does not move to Blocked
  while that session owes it a reply.
- A request gets no notification of its own; the move to Blocked is what
  announces it.

## Inter-agent communication (the Romp Postal Service)

Sessions message each other through a mailbox Romp gives them, and every
exchange is visible to you. Each session gets mail tools: send a message to a
session by name, check the inbox, and see who is live. Each session also
publishes a working note saying what it currently holds, so agents can see who
to talk to instead of messaging each other to find out.

Not the same tools: romp peers are discovered only through the postal service's `list_agents`. Claude Code also ships its own `ListAgents` and `SendMessage` tools, which list the account's Anthropic cloud sessions and this session's own subagents: a different system, and a cloud session in that list is easy to mistake for a romp peer (the user 2026-09-08, who found one there that read like a session of theirs). The recommended setting is `"permissions": { "deny": ["ListAgents"] }` in the Claude Code settings, so the only list of agents a session sees is romp's; `SendMessage` must stay allowed, because continuing a subagent uses it.

The timeline draws an arc for each message. Hover one for its gist:

<video src="../assets/guide/coordination.mp4" controls loop muted playsinline preload="none" data-romp-autoplay width="100%"></video>

Underneath, a local message bus writes the message into a mailbox on disk that
belongs to the recipient, then delivers it: straight away if that session is
idle, otherwise when its current turn ends. The recipient reads it as a message
in its chat, and it appears in the user interface as a card naming the sender
and the kind:

![A message from another session, as the recipient's chat shows it](assets/guide/postal-chat.png){ width="100%" }

Every message declares its kind, which the card wears as a chip:

- <span class="romp-chip-kind romp-chip-delegate">delegation</span> — the recipient owns the work now.
- <span class="romp-chip-kind romp-chip-coordinate">coordination</span> — a heads-up; a reply is optional.
- <span class="romp-chip-kind romp-chip-question">question</span> — an answer is required.

The same mailbox is on the command line, for you and for scripts:

```bash
romp mail send --kind question api "Which auth approach did we settle on?"   # send, to the session named "api"
romp mail inbox                                                              # read this session's messages, and clear them
```

The full mail surface, shell and in-session, is in the
[Reference](reference.md#mail-from-the-terminal). Names resolve against the
currently live sessions; sending to a dead session's name errors instead of
silently parking mail.

## Sessions, revival, and search

A session outlives the conversations inside it. `/clear` starts the agent on a
blank slate, a relaunch starts it over, the kernel restarts: each of those is a
new conversation underneath, and `api` is still the same `api` on your board,
with its history and its cards.

Closed sessions come back. Click **+** and the closed ones are listed under
**Recent**; pick one and Romp offers to revive it, with its history intact, or
to open it read-only. Revival works by picking the session, not by its name, so
a new session that reuses an old name is a new session rather than the old one
resumed.

A session can also move to another folder. When the code it works on moves, say
a subproject that became its own repository, right-click its tab and choose
**Move to folder…** (or run `romp move <session> <dir>`): the conversation,
name, mail and history stay with the session, and from the next turn on the
agent works in the new folder and reads its `CLAUDE.md`.

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

Search reaches inside sessions, not just across their names. As sessions run, a
lightweight index judge writes each one a headline and an abstract of what it
did, so searching for the work finds the session that did it, months later.

### Session backends

Sessions run on one of these backends, chosen per session:

- **Claude Code (the default, strongly recommended).** The kernel runs the
  Claude Code session itself, through the Claude Agent SDK.
- **Claude Code (tmux).** A Claude Code session running in a terminal inside
  tmux. Run `romp new -t <name>` and that terminal session joins the interface
  like any other, so you can work in the terminal directly and still see it in
  Romp. The cost is that Romp has no direct connection to it: it reads what
  appears in the terminal and on disk, and sends messages and nudges by
  injecting keystrokes. That makes it less reliable and less responsive than
  Claude Code itself, since scraping a terminal has edge cases a real API does
  not, and updates wait on the transcript reaching disk. The new-session picker
  and the gear's Default backend list offer it only while **Enable Claude Code
  tmux backend** is on in the gear's Updates & debug section (off by default);
  sessions already running on it keep working either way.
- **Codex.** An OpenAI Codex agent; see [docs/codex.md](codex.md).

The backends interleave freely, so terminal sessions and Claude Code sessions
sit side by side in the interface and message each other like any other pair.

## The Romp kernel (the back end)

The kernel is the program that runs your agents, watches their work, and serves
the user interface at `127.0.0.1:29855`. You run it on your own machine, with no
hosted service in between. Everything Romp stores stays local; the only traffic
that leaves your machine is `claude` itself, both the agents' own model calls and
the LLM calls in Romp's judge pipeline.

The kernel runs as a login service, so it is up whenever you are logged in. To
stop it on purpose, run `romp down`: it gives the agents a few seconds to
finish the turn they are on, then stops the kernel and keeps it stopped, and
`romp status` says so. `romp up` starts it again, and every session comes back
with its history; a session that was cut mid-turn is told so, and when, and
picks its work back up. `romp down --now` skips the wait; `romp down --wait 60`
lengthens it.

### Linking kernels on other machines

Romp kernels can connect and communicate across multiple machines, e.g. a laptop
and a server. This lets you control them all from one user interface, and lets
their agents communicate across the machines. A linked machine's sessions appear
as
<span class="romp-sid"><span class="host">server:</span>api</span> tabs and
timeline lanes beside your local ones, its cards share the feed, and its
sessions message yours, so an agent on your desktop can hand work to one on the
server.

You link machines from the network popover, which opens from the button at the
bottom right beside the settings gear:

![The network button](assets/guide/network-icon.png){ width="72" }

Every machine gets a row there with two controls. **Attach** brings that machine
into your interface: its sessions, its cards, and mail both ways over the one
connection. **Share my sessions there** puts your sessions in *its* interface,
for when you also work from that machine.

#### Attach a Romp kernel on another machine

Attach any machine you can `ssh` to, such as a server or a desktop that stays
on. Your kernel opens the connection.

1. **Install Romp on that machine**, the same way you installed it on your own
   (see [Install](install.md)).
2. **Check that `ssh <host>` connects without prompting you for anything.** Romp
   opens the connection in the background, so a password or passphrase prompt
   stops it; set up key-based login if you need to. Any target you could type
   after `ssh` works, including a `~/.ssh/config` alias.
3. **Attach it from your interface.** Open the network popover, click **+ Add a
   host**, type the ssh target, and click **Attach**.

The machine appears as a row with a live status, and Romp reads its kernel's
access token over ssh so your browser can authorize against it. A row reading
**kernel not answering** means no Romp kernel is running there: click **Start**,
which brings that machine's Romp up to date with this one's and boots it. Romp
never starts a remote kernel by itself, since a stopped one may be stopped on
purpose.
Detaching keeps the machine under **Previously attached**, so re-linking later
is one click and it returns with the trust level you last gave it.

#### Mail across linked machines

Each linked machine carries a trust level, set on its row, that decides what
happens to mail arriving from it:

- **trusted** — delivered straight to your sessions.
- **directed**, the default — held for your approval as a needs-you card.
- **isolated** — no mail either way; its sessions still appear in your
  interface.

Which to choose, and what each one guards against, is in
[Security and trust](#security-and-trust).

Machines that a linked machine can reach appear to you too, under **Reachable
via relay**: no tunnel of your own, their mail arriving one hop through the
machine in between. They carry the same trust selector as any host, because a
message is judged by where it came from rather than by the route it took.

When another machine is holding mail for approval, **Held for approval
elsewhere** shows you that it is, and how much, with the gist on hover. Acting
on a held message stays on the machine holding it, so this tells you where to
go rather than deciding for you.

Mail for a machine that is offline waits in an outbox and delivers when it comes
back. If it returns and the session you addressed is gone, that refusal comes
back to you. Every machine runs its own postal bus, so a laptop with no
connection at all still has full local messaging; linking only adds reach.

#### Also drive the fleet from the far machine

Attaching leaves the far machine's interface unaware of your sessions. Do this
only if you work from both computers, since mail already crosses both ways
without it.

If that machine can `ssh` to yours, it can just attach you and you are done. A
laptop usually cannot be reached that way: it moves between networks and sits
behind a router that accepts no incoming connections. Work from the laptop
instead:

1. **Attach the always-on machine** you want your sessions to appear on,
   following the steps above.
2. **Tick "Share my sessions there"** on that machine's row.

Your sessions now show up in its interface from whatever network you are on.
Because the laptop is the end that connects, the always-on machine never holds a
way in to it; untick the box and it forgets you. Romp calls this checking in,
and the always-on machine the hub, which is where `romp checkin` and
`romp checkout` get their names. Restarting Romp from the hub's interface
restarts the machines linked to it as well, and a checked-in machine is asked to
restart itself only: anything attached to that machine alone is restarted from
its own interface.

#### Hand the connection to a different machine

The add-host box has a **from** picker. Leave it on *this machine* and the
tunnel lives here, dropping when this kernel stops. Choose an attached host
instead and the attach is forwarded to that kernel, which dials out itself, so
the connection outlives your laptop. That machine needs its own ssh access to
the target.

The mechanics, including how the tunnels and the check-in handshake work, are in
[How Romp works](architecture.md).

## Remote access

You reach Romp in a browser tab, in the VS Code / Cursor extension, or from your
phone.

### From another machine

The kernel listens only on `127.0.0.1`, so a browser on another machine needs a
path to that port. Two paths work well; one common one does not.

**Plain ssh port forwarding.** From the machine with the browser:

```bash
ssh -N -L 29855:127.0.0.1:29855 <the machine running romp>
```

Then open `http://127.0.0.1:29855` as usual. OpenSSH forwards each browser
socket one-to-one and propagates closes, so a pane that goes away is gone on
both ends.

**Tailscale.** The same setup as for your phone below gives every device you
own a direct path; the dashboard is then a plain URL on your tailnet.

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
    previous socket at once, and the timeline and feed cross the wire as
    deltas instead of whole payloads. That keeps a forwarded dashboard usable,
    but the forwarder still carries every byte over a channel it shares with
    your editor, so prefer one of the two paths above. The VS Code romp view
    is a different case: its sockets run on the kernel's own machine and close
    when a panel closes, so it never leaks connections, but under Remote or
    Tunnels the extension still relays each whole view payload to the local
    window as it changes. It does not yet take the deltas the browser panes do.
    A pane that falls 16 MB behind is dropped and reconnects on its own; the
    drop is logged in the kernel log and shows in the Log (the settings panel's "Open log" button carries the unread count on the desktop; the phone's bottom bar reddens its bell), so a
    link that cannot keep up reads as what it is rather than as a flaky network.

### From your phone

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
    the machine itself. Re-run `tailscale serve --bg <new port>` — it replaces
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

#### Notifications on your phone

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
  the main switch is on.
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

## Security and trust

Romp drives agents that run tools and shell commands as you, so reaching its API
is equivalent to running code as you. Everything below follows from that.

**One token, required on every request.** The kernel and the postal bus both
demand a token on every request, local ones included. Loopback is not a
security boundary: on a multi-user machine every local account can reach your
ports, so without this any other user could inject prompts into your live
sessions. The token is 144-bit random and lives at
`~/.local/state/romp/serve-token` with mode `0600` (readable only by your own
user account). Local tools (the CLI, hooks, the bus, the editor extension) read
that file and send it automatically, so you never type it. Only two kinds of
request skip the token: the liveness probes (`/healthz`, `/version`, `/busy`,
and the bus's `/ping`), and the files a browser fetches without credentials
when you add Romp to the Home Screen (`/manifest.webmanifest` and three icons
under `/media/`). Those files are fixed (the app's name, colors and icon art)
and read no session state.

The kernel and the bus mint the token file when it is missing, one mint between
them under a sibling lock file, `serve-token.lock`. An existing token is never
replaced: a file left looser than `0600` is tightened at the next start (its
value is kept, so every client stays valid), and a token that exists but cannot
be read, or a symlink at that path, refuses to start instead of minting a
replacement nobody else holds. Under the service that refusal repeats in
`manager.log` every 10 seconds until you repair the file; the kernel then comes
back on its own.

A browser cannot read that file, which is why the link `romp` prints carries the
token in it. The first visit trades it for a year-long cookie, so the bare
`http://127.0.0.1:29855/` works from then on; `romp url` prints the link again
for a new browser or after clearing cookies. The cookie is a credential in its
own right, so treat a machine holding one as signed in.

**Remote machines.** Every machine mints its own token. When you attach a host,
your machine reads that host's token over ssh and stores it locally (in
`~/.local/state/romp/remotes.json`, also `0600`: it is a credential store).
Dashboard traffic to a remote never crosses the network in the open; it rides
the ssh tunnel, which supplies encryption and machine identity, while the token
authorizes at the far end. Checking a laptop in to a hub reverses which end opens
the connection, so credentials still flow outward from the machine that
initiates, and a hub never holds a way in.

**Trust levels for attached hosts.** Attaching two kernels lets their sessions
message each other, which means a session on the remote machine can put text
into a local session's context, and text in an agent's context can steer it. So
each host carries a trust level, set on its row in the network popover and
remembered per host. The level goes by where a message originated, not by the
route it travelled, so a machine whose mail reaches you relayed through a hub is
judged by the level you gave that machine:

- **trusted** — sessions on both machines message each other freely, as if they
  were on the same machine. For a machine you fully control.
- **directed** (the default for a newly attached host) — you can send work to
  its sessions, but its mail back to you is held for approval: each held message
  becomes a needs-you card with **Approve**, **Edit**, and **Deny**, so a person
  decides before that host's content reaches one of your agents. For rented or
  shared compute.
- **isolated** — no messaging in either direction. Its sessions still appear in
  your interface, but the two mail systems never connect. For gathering kernels
  that have nothing to do with each other into one place to work from.

**What this does not protect against.** Anyone with administrator access to a
machine can read any file on it, the token included, so don't keep long-lived
credentials on a machine you don't trust that far. Any program already running
under your own user account can read the token too, so two sessions on the same
machine are separated by policy rather than by this boundary. The lines Romp can
actually enforce are per-user (the token file) and per-machine (the trust
level).

Full details, including how to report a vulnerability, are in
[SECURITY.md](https://github.com/romp-on/romp/blob/main/SECURITY.md).

## How many tokens does Romp use?

Romp spends tokens on top of what you spend yourself. If you are running models
like Opus or Fable at high effort, the judging costs much less than the sessions
themselves. The analytics modal in settings shows what you actually spent,
separating your sessions from the judge pipeline. You can also reconfigure the
judges from the gear: the high-volume indexing tier defaults to Haiku, and the
judgment tier defaults to Sonnet.

The bottom bar's API readout carries one small dot, right after its **API**
label, that shows how the API is treating your sessions on
every connected kernel. The accent colour (blue in the dark theme, clay in the
light one) means everything is fine. Red means errors are being met somewhere:
a 429 rate-limit storm, 5xx failures, a machine that cannot reach the API, or
auto-retry paused (a usage limit, the monthly spend cap, or you stopped it), and
it stays red while a failed attempt sits in the last 15 minutes anywhere. Gray
means the API is not being used right now: no traffic in the last 15 minutes on
any machine. A machine whose link is down is named in the popup with what it
last said and does not colour the dot. Hover for the counts: one line per
machine, each named by its own name, with its successful requests over the last
24 hours in the accent and any failures counted in their colours (429s in red,
5xx in magenta, no connection in gray), the waiting sessions listed, and
the history under it: a stacked histogram of attempts per quarter hour over
the last 24 hours in those same colours, a legend for the codes, when the
reading was taken in words, and the most recent state changes with how long
each held. A kernel restart shows as its own line there, because the counts
start over with the kernel. Click the dot, or press Enter on it, for the
detail: the same lines with a larger histogram per machine and a choice of
range (1 hour, 24 hours, 7 days),
each waiting session (click one to open that session), a button that stops
auto-retry for every session while sessions are waiting and resumes it while
paused, and links to the usage figures and the Log.
