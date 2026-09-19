# Notice cards: kernel-made feed cards posted without the judges

Status: PROPOSED (2026-09-12), not shipped; awaiting the user's approval. Branch romp_cards-t370.

## The ask (2026-09-12)

romp needs one general mechanism for putting a card in front of the user without a judge call,
reachable from a romp interface so that any producer can post one. The user asked for this through
their feature-review session on 2026-09-12 (the user 2026-09-12, who asked for one general
mechanism rather than one-off paths, because several kinds of events want a card and none of them
wants to be tangled in the judges). Their example: a new version of a figure appears on the feed as
a rendered card.

They also asked whether the model-switch card already works that way. It half does, and the other
half is the reason for this note. `mint_fallback_card` in `kernel/judge.py` mints its card with no
judge call, then files that card as a node in the session's goal store, where about fourteen judge
selectors can pick it up. The card spends the judges' machinery to keep the judges off itself.

The first consumer is pull request 1496, from an outside contributor, which drops a typed message
that never landed when it is older than thirty minutes at a restart instead of replaying it. The
user accepts the thirty-minute line and wants the dropped message to reach them as a feed card
rather than as a notice injected into the session. That pull request is held until this design
exists.

The design below: a notice card carries a producer's payload, is posted through one kernel function
with three doors (an in-process helper, an HTTP route, a command line subcommand), lives in its own
per-session append-only file outside the goal store, and rides the existing feed pipeline under an
item identifier of its own.

## How kernel-made cards work today

### The model-switch card, the one judge-free mint

`mint_fallback_card(sid, from_model, to_model, ev_t=None)` and `mint_refusal_fallback_card(...)` in
`kernel/judge.py` write a completed top directly into the session's goal store. Each builds a
`GuardedNode` with the identifier `"<sid>:g<seq>"`, sets `why` to the constant
`CAPACITY_FALLBACK_WHY` ("kernel-observed API model fallback") as its dedupe key, sets `text` to
"Model changed automatically: <from> → <to>", leaves `log` empty, and then calls
`record_verdict(store, nd, "romp", "done", t, why=...)`, `rollup_status`, and `save_goals`.
**Dedupe is by existence**: while an uncleared node with the same `why` and `text` is on the board
and is absent from the cleared ledger (`_view_cleared`), another observation of the same swap mints
nothing. No time window is involved.

The kernel wires the mint as a class-level hook right after it constructs the SDK backend:
`type(_sdk_backend).on_model_fallback = staticmethod(lambda sid, frm, to:
(jd.mint_fallback_card(sid, frm, to), _push_soon()))` in `kernel/kernel.py`. The backend resolves
it with `getattr(type(self.backend), "on_model_fallback", None)` because `kernel/sdk_backend.py`
does not import the judge module. The card then behaves as any completed top: `build_feed` files it
by `col = status.get(nid, "working")`, the ordinary feed clear dismisses it, and the distiller
writes its takeaway.

Its only timeline footprint is indirect. The `done` verdict carries source `"romp"`, which the
planner and closer filters drop, so the card shows up on the timeline as a planner "mint" mark that
glosses the session's next planner span.

Every other kernel-made card (the parked handoff, the quarantined peer message, the provisional
card, the awaiting card, the blocked card) is built one-off inside `build_feed`. There is no shared
door.

### The goal store as a home, weighed and rejected

A node in `goals/<sid>.json` is selected by about fourteen judge menus and walks, all keyed on
generic flags: `open_menu`, the closer's turn menu and its riders, the unblocker's candidates, the
grouper and the consolidator, the nudge walk, the anchor latches, the heal, and `build_feed`'s own
root loop. No provenance field excludes a node from all of them. The only universal seal is
`cleared`, and the compaction sweep archives exactly that.

The store's write path is a compare-and-swap loop with rebase by node identifier, a replayed
override journal, and a boot migration. The judges take a pre-pass snapshot, so a kernel write
landing mid-pass surfaces only at the end of the pass.

The feed's per-session memo, in flight with the feed session, keys each session's derivation on
labelled inputs (transcript, states, names, store identity, registration, cleared slice, live row,
postal log, stalls, nudge, watches, subagents, usage, auth, downtime, peers). A separate
per-session notice file enters that key as one more labelled component, `notices`, at the cost of
one stat call, and invalidates one session. A board-wide notice file would re-derive every session
on each write. That memo is not in this checkout; its component list comes from the feed session's
account of it.

The feed session stated its preference on 2026-09-12: a separate per-session notice store,
append-only, read live the way the cleared ledger is read, with cards riding the existing pipeline
under an item identifier of their own, and a goal-store node only when a notice genuinely needs the
tree, follow-ups, or the judges.

### The feed's card model

`build_feed` emits exactly three raw columns, `working`, `needs_input`, and `completed`, and the
client files strictly by `column` (`AskItem.column` in `ui/webview/feed.ts`, mapped to the local
column by `askColumn()`). A `needs_input` card inflates the application badge through
`_needs_you_count` and fires bells through `_feed_notifications`, both in `kernel/kernel.py`. The
repository rule is that needs-you means a decision only the user can make.

Kernel-made cards already reuse the `AskItem` shape with a namespaced item identifier and an empty
tree: `provisional:<sid>`, `awaiting:<sid>`, `blocked:<sid>`, `parked:<msgId>` (column
`needs_input`, with `blocked.state` set to `"parkedHandoff"`), and `quarantine:<mid>`.
`blocked.state` is the de-facto flavour discriminator for kernel-made cards. There is no `kind`
field on a card.

Dismissal at the view level is the `askClear {itemId, sid}` socket operation, which appends
`{id, t, op: "clear"}` rows to `STATE/cleared.jsonl`; `_cleared_ids()` replays the file
newest-wins, and undo restores the newest batch. A card is dismissable when `build_feed` skips it
for `item_id in cleared` (the parked card's pattern) and does not set `provisional: True`. The
durable flag writer `_mark_nodes_cleared` takes `itemId.rsplit(":", 1)[0]` as a session identifier,
which is a harmless no-op for a namespaced family, and `_CLEARED_NO_SESSION` enumerates the
families that key no session so that `_cleared_foreign` does not ship their identifiers as foreign.

Every field on a card must be fixed across unchanged builds, with nothing derived from the current
clock, or the per-client dedup breaks and the card re-renders on every push.

The feed frame also carries top-level notice channels already: `clearNotices` from
`_boundary_clear_notices`, `sdkNotices` from `_sdk_problem_rows`, and `syncNotices` from
`_sync_notice_rows`. Those feed the bell and the log. None of them is a card.

### Routes, files and previews

Every route sits after `_authorize(q)`, which accepts a `?token=` query parameter, the `romp_token`
cookie with a same-origin check, or the `X-Romp-Token` header, compared against the serve-token in
the state directory. A POST reads its body only through `_read_post_body()`, which caps at one
megabyte and refuses a chunked, short or stalled body. The body is parsed with `_json_object_body`.
The answer shapes are `400 {"ok": false, "error"}` for a malformed body,
`200 {"ok": false, "error": <prose>}` for a domain refusal, and `200 {"ok": true, ...}` on success,
with the logic in a module-level function that returns `(row, error)`. `/watch` and `/watch-pr` are
the pattern, backed by `add_watch` and `add_pr_watch`; `_sid_of(who)` resolves a session identifier
or a live name.

Files are served by `GET /file?path=…&sid=…` (`_file_preview`), with the media type from
`_PREVIEW_MIME` (the image extensions of `_IMG_MIME`: png, jpg, jpeg, gif, webp, bmp, svg, plus
pdf) and an allowlist of text kinds. `/remote/<host>/file` is the federated twin. The plain view
route has no root confinement. The hover preview's slice route is confined by
`_slice_allowed(fp, sid)`, which judges the real path: a regular file, the secrets denylist on the
real name and its directories, the kind by name (`_slice_kind`, the link's name required to agree),
confinement to the session's folder or the home, and the size caps (two megabytes of text, fifty of
media). The kernel decides per link at message build time in `_path_preview_verdicts(links, sid)`,
shipping `pathPreview` and `pathPreviewWhy`.

The client has one content shape, `PreviewContent {kind: markdown|section|image|code|pdf|text|term,
title, subtitle?, body: {markdown?, html?, text?, url?, lang?}, note?, open?}` in
`ui/webview/file-preview.ts`, rendered by `renderFilePreview` in `ui/webview/render.ts`: markdown
through `previewMdClean`, which runs `sanitizeMd` (`ui/webview/md-sanitize.ts`) and then
`stripRemoteLoads` (`ui/webview/file-preview.ts`); an image as an `img` from `fileUrl(path, sid)`;
a pdf as an iframe. `canPreview()` in `ui/webview/preview.ts` gates the whole path on an http or
https origin, because the editor webview cannot reach the kernel's origin. `_pin_mention(fp)`
snapshots an image's bytes and serves them at `/file?pin=<digest>.<ext>`, so a file regenerated
under the same name keeps the picture the card was posted with.

### Federation and the timeline

Cards from another host reach a dashboard by push over one WebSocket per kernel and are merged in
the browser by `mergeHostFeeds` in `ui/webview/federation.ts`, which concatenates `asks`,
`sessions`, `working` and `awaiting`. Inbound frames are host-prefixed field by field, including
`sid` and `name` on each ask. Item identifiers are never prefixed, so a family must namespace
itself the way `parked:<key>` does. Outbound gestures route by the host prefix on `sid`, so every
gesture must carry `sid`; a dismissal is a routed gesture into the owning kernel's cleared ledger,
and `clearedForeign` (written by `_cleared_foreign`, applied by `applyViewerClears`) is the
viewer-side backstop. The kernel itself is host-blind apart from `selfHost`.

Everything on the timeline is keyed by a session lane. A goal-store notice leaks a planner "mint"
gloss onto that lane. A board artifact stored outside the goal store has no timeline presence and
needs none.

### The command line tool

`bin/romp` is one bash script. A built-in command is an `if [[ "${1:-}" == "<word>" ]]` block
placed above the final `case` fallthrough, with one `_romp_cmd` line in the help block. An HTTP
command reads `ROMP_KERNEL_PORT` (default 29855), passes the token through
`_romp_token_cfg | curl --config -` and never on the command line, builds its JSON with
`python3 -c 'import json…'`, tests the answer for `"ok": true`, prints one `romp <cmd>: …` line on
success, prints a `romp <cmd>: refused` line naming the error and exits 1 on a refusal, and exits 2
on a usage error. Inside a session the target defaults to `$ROMP_SID` under body key `id`;
`--session <name>` sends `name` instead. `romp watch` is the worked example.

The postal server is scoped to mail. There is no kernel-backed tool in it today.

### Retention

Byte bounds are fractions of machine memory. `_mem_total_bytes()` reads `MemTotal`, falls back to
`sysconf`, and floors at eight gigabytes when neither answers positively.
`_spend_tree_memo_bound()` takes a raw-bytes environment override when it names a positive integer
and otherwise returns a sixty-fourth of memory, read once into a module constant. `GET /perf`
reports each memo under `memos.<key>` as `{entries, bytes, bound}` (`_spend_tree_memo_report` is
the shape), and eviction sheds only the deficit, largest first.

Cleared goals are archived and never pruned. `_compact_goal_store(fsid)` moves each cleared top and
its subtree into `goals-archive/<fsid>.json`, and `_compact_goal_stores()` runs it on the producer
thread after the judge tiers, skipping stores whose modification time has not moved. Writers read
the archive through a fresh loader; readers read a stat-keyed memoized read-only twin. The cleared
ledger is never pruned, and readers bound their projection of the archive instead (the archive
reader in `kernel/kernel.py` caps its projection at twenty tops).

## A notice card: name and shape

A **notice card** is a kernel-made feed card posted by a producer, never by a judge. Both words are
already romp's, and nothing else in this design gets a name of its own.

A producer supplies these fields:

- `key`: the dedupe key, matching `[A-Za-z0-9_.-]{1,64}`, required.
- `title`: one line, required, up to 200 characters.
- `body`: markdown, optional, up to 64 kilobytes.
- `session`: required. Every notice belongs to a session, which is the source of the card's chip,
  its colour, its host routing in federation, and its memo key.
- `attachment`: optional. A file path, served through the existing file route, of an image, a pdf,
  or a text kind. The kernel judges it at post time and stores the verdict.
- `needsYou`: boolean, default false.
- `expiresAt`: optional epoch seconds.
- `actions`: optional, up to four `{label, route, body}` entries whose `route` is one of an
  allowlist of existing kernel POST routes. The first version allows `/send` only.
- `dismissOnAction`: boolean, default false. When true, a successful action dismisses the card.
- `producer`: a short label the card shows as its source, such as `dropped-sends`, `figure`, or
  `cli`.
- `t`: the event time. The kernel stamps the current time when it is absent.

The kernel assigns `rev`, the revision count for that `key` in that session, and the card's item
identifier is `notice:<sid>:<key>:<rev>`. The `notice:` namespace joins `_CLEARED_NO_SESSION`,
because a namespaced item identifier keys no session for `_mark_nodes_cleared`'s rsplit.

## The producer interface: one function behind three doors

One function validates, judges the attachment, appends the row, marks views dirty and wakes the
pusher. Three doors call it. **One function means one validation, one row shape, and one test of
the rules**; a route with its own validation and a command line subcommand with its own would
drift, and a producer inside the backend would have neither.

**Door one, in-process.** `post_notice(sid, key, title, body="", *, producer, attachment=None,
needs_you=False, expires_at=None, actions=None, t=None) -> (row, error)` in `kernel/kernel.py`,
placed beside `add_watch` and following its `(row, error)` contract. Subsystems inside the kernel
process call it directly. `kernel/sdk_backend.py` cannot import the kernel, so the kernel wires a
class-level hook beside the model-fallback one,
`type(_sdk_backend).on_notice = staticmethod(post_notice)`, and the backend resolves it with
`getattr(type(self), "on_notice", None)` plus a callable check. The defensive resolution is required
by the tests: the backend's own tests bind methods onto bare stand-in classes that carry no hooks,
and a hard attribute access breaks them.

**Door two, over HTTP.** `POST /notice` in `do_POST`, behind `_authorize`, with body
`{id | name, key, title, body?, attachment?, needsYou?, expiresAt?, actions?, producer?}` read
through `_read_post_body`, parsed by `_json_object_body`, and resolved by `_sid_of`. The answers
follow `/watch` exactly: 400 for a malformed body or a missing `key`, `title` or session;
`200 {"ok": false, "error"}` for an unknown session, a key that fails the regular expression, an
oversize body, a refused attachment carrying its `why`, or an action naming a route outside the
allowlist; `200 {"ok": true, "notice": <row>}` on success.

**Door three, from the command line.** `romp card` in `bin/romp`, as a block above the final
fallthrough with one `_romp_cmd` help line:

```
romp card --title <text> [--body <markdown> | --body-file <path>] [--session <name>]
          [--attach <path>] [--key <key>] [--needs-you] [--expires <duration>] [--producer <label>]
```

It follows `romp watch`'s mechanics exactly: the session defaults to `$ROMP_SID` under body key
`id`, `--session` sends `name`, neither available outside a session refuses with exit 2, the token
and the body go through `_romp_token_cfg` and `python3 -c 'import json…'`, and the answer prints
one `romp card: posted …` line or a `romp card: refused` line naming the error, exit 1.

The postal server gets nothing: its scope is mail, and a session has `romp card`.

## Where a notice card lives

A notice card lives in a separate per-session append-only file, `STATE/notices/<sid>.jsonl`, one
JSON object per line for each post, revision or expiry, and never as a goal-store node. The reasons
are the facts above: the fourteen judge selectors that no provenance field can exclude a node from;
the authority ladder and pre-pass snapshot, which delay and contest a kernel write; the feed memo's
per-session key, which a per-session file enters as one component and one stat where a board-wide
file would re-derive every session; and the boot migration, which never touches a file it does not
know. A write is an append under one in-process lock, with no compare-and-swap loop and no override
journal to replay.

The feed build reads the file live, memoized on the file's stat the way `_cleared_ids` is, and
projects the newest revision per `key`. It skips a revision whose item identifier is in the cleared
ledger, skips one whose `expiresAt` has passed, and hides every older revision of a key as
superseded.

A row carries the producer's fields, the kernel's `rev` and stamp, the attachment verdict, and an
`op`. A synthetic example, for session `11111111-2222-3333-4444-555555555555` in the notes-api demo
world:

```json
{"op": "post", "t": 1757000000, "key": "figure", "rev": 2,
 "sid": "11111111-2222-3333-4444-555555555555", "producer": "figure",
 "title": "A new version of the accuracy figure is ready",
 "body": "Regenerated after the sweep on tests finished. The earlier version is superseded.",
 "attachment": {"path": "/srv/notes-api/figures/accuracy.png", "kind": "image",
                "allowed": true, "why": "", "pin": "<digest>.png"},
 "actions": [], "needsYou": false, "expiresAt": null}
```

An expiry writes its own row (`{"op": "expire", "key": "figure", "rev": 2, "t": …}`) when a
producer retires a notice early, so the file is the whole history of what was posted and what
became of it.

## How a notice card moves

A notice card appears once, at its post, and then moves only on a named event. Its column is
`needs_input` when the producer says the user must act and `completed` otherwise, the model-switch
card's column, so an informational notice inflates no badge and rings no bell.

It is never re-derived. No planner, closer, unblocker, nudge, distiller, grouper or consolidator
reads the file, and the column is fixed on the row rather than computed per build.

Three events move a card, and nothing else does:

- **The user's dismissal.** `askClear` appends the item identifier to the cleared ledger, undo
  restores the newest batch, and in federation the gesture routes to the owning kernel by the host
  prefix on `sid`.
- **The producer's update under the same key.** A new revision takes a new item identifier, the
  older card is superseded and hidden, and the new card appears even when the older one was
  dismissed, because the producer holds new information. The user's own example is this case: a new
  version of the figure should show even after they crossed off the previous one.
- **The producer's declared expiry.** `expiresAt` is applied at build time as a skip. No field on
  the card changes, so the per-client dedup holds.

This satisfies the repository's rule that cards move on new information and never on inference
flaps. Each of the three is an exact event: a post, a dismissal, a revision, and the expiry is a
lifetime the producer declared for its own information rather than an age threshold romp inferred.
Nothing about a notice card's column depends on an open turn, a verdict, or a recomputation that
can differ between two builds over the same inputs.

## How a notice card renders

The card keeps the `AskItem` base and adds one flavour object. The base fields are the item
identifier, `sid`, `name`, `color`, `text` (the title), `t` (the post time), `live`, `trgb`, an
empty `turnId`, `column`, and an empty `tree`. The flavour object is
`notice: {producer, rev, body, attachment: {path, kind, allowed, why, pin}, actions, expiresAt}`,
discriminating the family the way `blocked.state` discriminates the kernel-made flavours today.

The client renders the title as the card's text and the producer label beside the session chip. The
body goes through the markdown path the hover preview already uses, `previewMdClean` with
`sanitizeMd` and `stripRemoteLoads`. The attachment goes through the one `PreviewContent` shape and
`renderFilePreview`: an image inline from `fileUrl(path, sid)`, a pdf as a click-to-view iframe, a
text kind as a code or markdown block. `canPreview()` gates all of it, and the kernel's stored
verdict gates it again.

That verdict is computed at post time under the slice route's confinement rule (the session's folder
or the home, the secrets denylists, the kind agreement, the size caps), which the plain view route
does not apply, so a notice card never shows a file the hover preview would refuse. An image is
pinned at post time through `_pin_mention`, so the card keeps the version that was posted even
after the file is regenerated under the same name, which is what the figure example needs.

Actions render as buttons that POST the stored `{route, body}` to the kernel with the page's token.
A successful action dismisses the card when the producer asked for that with `dismissOnAction`. The
modal shows the same body at full size. The session chip and colour come from the `sid`, as for
every card.

Two surfaces are deliberately absent. The **timeline** gets nothing: a notice card is a board
artifact with no session-lane datum behind it. The **chat** gets nothing: there is no transcript
record, and a producer that wants the agent told sends the session a message itself, written as the
person it works for asking for something, per the injected-voice rule. The card face follows
`ui/CLAUDE.md`: glanceable by default, with the body, the attachment and the mechanics one click
away.

## Federation

A notice card rides the `asks` array, so the existing relay carries it with no new frame field. The
browser prefixes its `sid` and `name` in `mergeHostFeeds`, the host prefix renders on the chip, and
the item identifier stays unprefixed, which is why the family is namespaced as
`notice:<sid>:<key>:<rev>`. A dismissal routes to the owning kernel by the prefix on `sid` and
lands in that kernel's cleared ledger, with `clearedForeign` as the viewer-side backstop, so
`_cleared_foreign` must keep skipping the `notice:` family through its `_CLEARED_NO_SESSION`
membership. An attachment on a remote session's card is fetched through `/remote/<host>/file` by
`previewRoute(sid)`.

A notice posted on one host therefore shows on the other host's dashboard with no new mechanism.
Known limit, inherited rather than introduced: a dismissal taken while the owning host is
unreachable is dropped with the existing toast, as every gesture is.

## Retention

The live file holds current revisions. The compaction sweep that already runs after the judge tiers
(`_compact_goal_stores`, skipping stores whose modification time has not moved) gains a pass that
moves dismissed, expired and superseded rows into `STATE/notices-archive/<sid>.jsonl`. Nothing is
deleted. A session gone for good is forgotten the way `_notify_prev_forget_gone` forgets one. Undo moves a
card's rows back out of the archive (`_restore_notice_archive`, the pass's inverse for one card, run after the undo
row lands so the pass never meets a live row the ledger still holds), and `post_notice` counts a key's archived
revisions, so an id the cleared ledger holds is never minted again.

The in-memory memo of parsed notice files is bounded by bytes as a fraction of machine memory,
through `_mem_total_bytes()`, following `_spend_tree_memo_bound`'s shape: `ROMP_NOTICE_MEMO_BYTES`
when it names a positive integer, validated, and otherwise a two-hundred-fifty-sixth of memory
(thirty-two megabytes on an eight gigabyte machine, half a gigabyte on a machine with one hundred
twenty-eight gigabytes). The bound is read once into a module constant, eviction sheds only the
deficit largest first, and `GET /perf` reports it under `memos.notices` as
`{entries, bytes, bound}` so a bound that binds cycle after cycle is visible.

The per-notice body cap of 64 kilobytes and the cap of four actions are input limits on a post, not
cache bounds; they refuse at the door rather than evicting later.

The archive pass takes the lesson recorded on `_USAGE_PRUNE_BYTES` in `kernel/judge.py`, where a
forty-eight megabyte trigger against a sixty-five megabyte retained month rewrote sixty-seven
megabytes per pass and could never shrink the file: a byte-triggered rewrite must sit comfortably
above what the file retains, or it lands back over its own trigger forever. This pass therefore
triggers on the presence of archivable rows and on a moved modification time, never on bytes.

## The archive bound (2026-09-16)

**What reads the archive today, and when.** `STATE/notices-archive/<sid>.jsonl` is written by the retention
pass (`_compact_notices`) and read whole at two doors, both under `_notice_lock`. `post_notice` reads it through
`_notice_archive_rev_unlocked` once per archive state (the file's stat) and keeps the result as `{key: rev}` in
`_NOTICE_ARCH_REVS`, so a revision never recycles an id the cleared ledger holds. `_undo_clear` reads it through
`_restore_notice_archive` once per Undo press, to move a restored card's rows back into the live file. Nothing
prunes the archive, and nothing bounds the `{key: rev}` memo. Three quantities therefore grow with a session's
history: the archive's bytes on disk (every dismissed, expired, superseded and over-cap row, each up to the
sixty-four kilobyte body cap); the time each whole read takes under the lock, paid by the next poster and by the
next Undo after every pass that archived; and the memo's entries, one per distinct key the session ever archived.
The sizes: a row is its post, a few hundred bytes of fields plus the body, so a producer posting a one kilobyte
card an hour archives about nine megabytes a year per session, and one posting at the body cap every minute about
thirty-three gigabytes. A whole read of nine megabytes is tens of milliseconds; the cost is that it sits on the
post path under the lock and repeats at every archive move, and that its ceiling is the file's size.

**The invariants any bound keeps.** A revision is minted once ever: `notice:<sid>:<key>:<rev>` may stand in the
cleared ledger forever, and the ledger is never pruned. Undo restores a card's rows from the archive for every batch
it can reach, one batch a press. The reference's words today are that the pass archives and deletes none.

**The revision index, a derived sidecar.** `STATE/notices-archive/<sid>.revs.json` holds `{key: highest archived
rev}`. The pass writes it under the lock whenever it archives post rows (a read-modify-write of a small file). It is
a high-water mark: it never decreases, and an Undo that moves rows back live does not touch it, since
`rev = 1 + max(live revs, index)` stays right either way. `post_notice` reads the index and never the archive. When
the index is absent and the archive exists, the first reader (the pass, or a post) rebuilds it from one whole read
of the archive and writes it: the index is a cache of the archive with a rebuild path, never a second source of
truth, so a lost or hand-deleted index costs one read and no invariant. Its growth is one entry per distinct key
ever archived, about fifty bytes each (ten thousand unique keys, half a megabyte), and it is read only at a post.
The refusal on a fault moves with the read: an index that cannot be read, or an absent index over an archive that
cannot be read, refuses the post with its reason, as `_notice_archive_rev_unlocked` refuses today; an absent index
over an absent archive is absence. `_NOTICE_ARCH_REVS` becomes the parsed index memoized on the index file's stat,
and it joins the rows memo under `NOTICE_MEMO_BYTES`: its bytes counted in the same total, shed by the same
largest-first rule, reported in `GET /perf memos.notices`, so it has the bound the rows have.

**Undo reads from the tail.** The pass appends a session's archived rows in one write, so the rows of one pass form
a contiguous block, and every row of one revision (its post, its acted mark, an expire row) is archived in the same
pass, because the expire row retires the post and the acted mark goes with its target. The pass stamps each row it
archives with the pass's time (`archivedAt`), and `_restore_notice_archive` strips the stamp when it moves a row back.
The restore then reads the file backwards in sixty-four kilobyte blocks: once every wanted `(key, rev)` has a row
found, it continues only to the start of the block those rows came from (the first row whose `archivedAt` differs),
and stops. The batch Undo restores is the newest cleared one, so its rows sit near the tail and the read is bounded
by the tail; an old batch reached by repeated presses walks further, as far as its pass and no further. Rows archived
before the stamp existed carry none and are read as one block.

**Disk growth: the decision.** With the index carrying the revision invariant on its own, pruning the archive would
cost only Undo reach for the oldest batches and the history, and would be safe for ids. Two roads: keep the words the
reference has (archives, deletes none), which is what the goals archive and the cleared ledger do, both never pruned,
their readers bounding their projection instead; or a per-session byte bound, `NOTICE_ARCHIVE_BYTES` with an
environment override, under which the pass drops the oldest rows first, whole pass blocks at a time, once the index
holds their revisions. That drop takes the `_USAGE_PRUNE_BYTES` lesson recorded above: the trigger sits well above what
one pass appends, and a drop removes a large share of the file (the oldest half), so the file never lands back over
its own trigger. The recommendation is the first road now: the index takes the archive off the post path and the tail
read takes it off the common Undo, and disk growth is the same accepted growth the cleared ledger and the goals archive
have. The second road changes a promise the reference makes and is the user's call; its mechanics are written here so
the call needs no second design.

**The test sketch.** The pass writes the index with the revisions it archives and never lowers it; a post reads rev 2
from the index with the archive unreadable; an absent index over an archive is rebuilt once and then read, over an
unreadable archive refuses the post; an unreadable index refuses the post; the index's bytes count under
`NOTICE_MEMO_BYTES` and appear in `memos.notices`. The restore finds the newest batch's rows in the tail block and
reads no further (a counter on the blocks read), walks to an old batch's pass on repeated presses, strips
`archivedAt` on the way back, and reads rows without a stamp whole. Red first at the head that lacks each piece.

**Landed (PR 1776):** the index, the stamp and the tail read as designed; the memo joins `NOTICE_MEMO_BYTES` under the
same keys in `memos.notices`, hits and misses included; the pass holds a session's rows when its index cannot be written.
Round three (PR 1776 as merged): the restore stats the archive before its rewrite and re-describes the index only when
the description it read matched that stat, so an index a rollback left behind stays behind and the next post pays its one
rebuild; an index over a vanished archive keeps its marks under a null description; a rebuild merges the standing marks,
so an archive that shrank or came back older never lowers one.
Round two: the index records the archive's size and mtime it describes and is rebuilt when the archive's stat differs (a
kernel that archived and wrote no index, a pass whose second write failed, a restore's rewrite), so it is the cache with a
rebuild path and never trusted behind the archive; a rebuild whose write fails still answers the post, uncached. Disk
growth stays as the reference says; the byte bound is the user's call. Known and queued: an Undo whose batch the pass has
not archived yet reads the whole archive (its rows are live, the tail read finds none and walks to the start), no worse
than before the bound.

## Owner-less cards and the terse command (2026-09-18)

**The ask.** The user found `romp card` overly restrictive: they want cards WITHOUT a session that appear at the TOP of the
feed, no key to manage, and the terse form `romp card -t "title" -m "some text"` to just make a card.

**The home.** An owner-less card lives in the reserved file `STATE/notices/notes.jsonl`, the key `notes`
(`NOTICE_OWNERLESS_SID`). It cannot collide with a session: the store is keyed by sid, a sid is a uuid (thirty-six
characters of hex and hyphens), and `notes` is a word. A session NAMED notes has a uuid sid, and the kernel resolves a
name to its sid before the store sees it, so a name no session answers to is still refused ("no session answers to"),
never guessed owner-less: only an ABSENT session (no `id`, no `name` on the route; no `--session` and no `ROMP_SID` on the
command line, or `--no-session` inside a session) takes the owner-less road. `post_notice` takes an EMPTY `sid` as that
road and nothing else: the reserved word arriving as a sid (an unresolved name handed back by `_sid_of`) is refused as a
name no session answers to, and the session check never answers for the key.

**Name and colour.** On the feed the owner-less run reads **Notes** (`NOTICE_OWNERLESS_NAME`) with no identity colour, since
no session stands behind it; the card carries no session chip. In a federated view the remote host's prefix distinguishes
one host's Notes from another's, as it does every remote card (federation prefixes a remote card's sid and name with its
host); the kernel bakes no hostname into the card, so the repo and the state carry no machine identifier.

**Item id and the ledger.** `notice:notes:<key>:<rev>`, the same shape as every notice card's; the cleared ledger already
treats `notice:` ids as sessionless (`_CLEARED_NO_SESSION`), and Undo, the restore and the retention pass split the id the
same way, so no reader changes.

**The archive bound.** The owner-less file is one more `<sid>.jsonl` to the retention pass: dismissed, expired,
superseded and over-cap rows move to `notices-archive/notes.jsonl`, the revision index `notes.revs.json` counts its
revisions, the restore's tail read serves its Undo. The fifty-live-keys cap applies to the file on its own, so owner-less
cards are capped as one session's would be.

**Order: the feed board's rule.** Every NOTICE card carries two fields the board model reads (plans/card-boards.md;
the names agreed with its author 2026-09-18): `board`, the board the card sits on, `"feed"` today; and `category`, the
card's category, which is today's raw column value (`needs_input`, `working`, `completed`), carried beside `column` until
the renderer reads `category` alone (`column` stays byte-identical meanwhile). The other card families write `column`
alone; every family carrying the two fields is the boards' phase two, as the card-boards plan says. The owner-less cards' place
is a SORT RULE of the feed board, not a card field and not a timestamp trick: within a category, runs order by the owner
key, the reserved no-session key first, then the session order, then time; needs-you still decides the category, so an
owner-less card that needs you heads the needs-you column and one that does not heads Completed. In grouped mode the Notes
run opens each column it has cards in; in ungrouped mode the owner-less cards sort first within the column by the same key
(a stable sort, so their own time order holds).

**Actions and attachments.** An owner-less card carries no actions: `/send` needs a session to send to, so `post_notice`
refuses actions on the owner-less road and the action runner refuses a hand-made owner-less id. An attachment is judged
against the home directory alone (no session folder to admit).

**The command.** `romp card -t <title> [-m <text>] [-k <key>] [-s <session> | --no-session] [--body-file <path>]
[--attach <path>] [--needs-you] [--expires <seconds>] [--producer <label>]`, with `--title`, `--message` (`--body` stays
as its alias), `--key` and `--session` the long forms, and the positional shorthand `romp card "title" "text"` (a bare
first word is the title, a bare second the text; anything more is a usage error). `-k` is OPTIONAL: when absent the command
mints a key, the first eight hex of a uuid4, never a slug of the title (an edited title must not make a second card, the
round-two lesson), and the success line prints it so a later revision can name it (`romp card -k <key> ...`). Outside a
session with no `-s` the card posts owner-less; inside a session `ROMP_SID` owns it by default and `--no-session` forces
owner-less. The usage string stays short; the reference documents the shorthand.

**The card's modal, and a reply's id (2026-09-18, the read of the owner-less cards' third round).** A notice card's modal shows
the notice: its title heads the overlay and its body is the card's own face (the producer line, the body through the sanitizer,
the attachment, the actions as the same latching buttons), in place of the goal tree a notice never has (the tree body read "No
work yet." and hid the title). No session gesture shows there: the kernel's follow-up road reads the session out of a GOAL id, so
Follow up, Check status and Continue on a notice id are refused as a session no kernel has, and they hide for every notice card;
Clear stays. An owner-less card's header name is plain text, never a session link, never struck through; the title locates
nothing and lights no chat turn. The kernel's answer to an action reaches the modal as it reaches the card: a success on a
dismissing card closes both, any other answer re-arms. In a federated view a NOTICE id wears its host on EVERY inbound face, a
row's and a reply's alike (noticeActionDone, a refused bell, an err naming its request), or the answer misses the card the
pane holds; the prefix comes off on the way out beside the sid's. The viewer's cleared overlay never touches a notice card: the
kernel ships goal ids alone as foreign clears (the prefixed families never ride), and a notice card's dismissal is a routed
gesture the owning kernel's ledger records under the bare id.

## Privacy

The store holds the producer's payload and nothing more. The kernel log names the session, the key
and the producer, never the title or the body, so a card's text exists only in the state directory
and on the user's own screen. The route refuses without the token like every route. Attachments
pass the slice route's confinement and its secrets denylists at post time, and the verdict is
stored with the row, so a card can never show a file the hover preview would refuse. The body
renders through the sanitizer that strips remote loads, so a posted body cannot phone out from the
dashboard. The command line tool never puts the token on the command line.

Tests and documentation for this mechanism use synthetic examples only: placeholder session
identifiers such as `11111111-2222-3333-4444-555555555555`, the hostname `TESTHOST`, and the
notes-api demo world with its `web`, `api` and `tests` sessions.

## The test sketch

**Kernel unit tests for `post_notice`** (`tests/test_notice_cards.py`, new): each required field;
the key regular expression, accepting and refusing; revision counting per key per session;
superseding, so the older revision hides; the expiry skip; the `needsYou` column mapping; the
attachment verdict, including a refusal carrying its `why`; the action route allowlist; and the
caps on body size and action count.

**The route**: a refusal with no token; 400 for a malformed body and for a missing required field;
`ok: false` with prose for an unknown session, a bad key, an oversize body, a refused attachment,
and a disallowed action route; `ok: true` with the row on success.

**The command line tool** in `tests/romp.bats`, through the existing curl stub: the posted key and
body; the session default from `$ROMP_SID` and the refusal outside a session; exit codes 0, 1 and
2; and the `romp card: refused` line naming the error.

**`build_feed`**: every field on the card fixed across two builds over the same inputs, with no
field derived from the current clock; the cleared-ledger skip; the superseded revision hidden; and
`notice:` present in `_CLEARED_NO_SESSION`.

**The webview**: a render test for the title, the producer label, the body, each attachment kind,
and the action buttons, plus an assertion that a dismissal carries `sid`. The feed's document
stand-in harness in `ui/webview/feed-render-incremental.test.ts` already renders a `parkedHandoff`
kernel-made card and can render this one.

**The federation merge**: the host prefix applied to `sid` and `name`, the item identifier left
unprefixed, and `clearedForeign` skipping the family.

**A served lab test**: a posted notice appears on the dashboard; a dismissal hides it and undo
restores it; an update re-shows it as a new revision even after a dismissal; an expired notice
leaves at the next build.

**Retention**: the sweep archives dismissed, expired and superseded rows and deletes none; the memo
bound's fraction default and its environment override, in the style of the spend-guard bound tests.

**The first consumer**: the drop path posts one notice per session per boot, carrying the count,
the age line, and each message's text and time; one Send-again action per message; `needsYou` true;
dedupe across a second call within one boot; and the injected session notice gone.

## Tier recommendation: major-feature

This should be filed `major-feature`, and the call belongs to the user. The reasons: it
adds a new interface contract in three forms (an HTTP route, a command line subcommand, and a
producer hook on the backend class), a new state store with its own retention and archive, and a
new card family with its own federation and privacy rules. It changes what romp offers to producers
rather than what one card looks like.

A new card kind on its own would be `feature`. The contract around it lifts this to tier 3, whose
requirement is a discussed issue: an issue linked from the pull request body with a comment by
someone other than the author, and the merge left to the maintainers rather than armed with
auto-merge.

## The first consumer: sends dropped at a restart

The dropped-sends producer is the first user of the mechanism, added after pull request 1496 lands,
or inside it with the contributor's permission, since the branch is theirs.

The drop happens in `SdkBackend._mark_dropped_echoes` in `kernel/sdk_backend.py`, which runs in the
kernel process from three callers: the backend's boot reseed (`_reseed_echoes`, called from
`SdkBackend.__init__` before the reconcile thread), a fresh CLI spawn from `SdkSession._run`, and a
resumable reconnect that passes `refeed=False` and never reaches the age line. The per-session host
is only a transport that the SDK client drives from the kernel, so the producer here is an
in-process call and never an HTTP one.

In hand at the drop: the session identifier; each stale echo atom with its full text (the atom's
`_echo_text` field); its send stamp `t` and `uuid`; the wall clock at the restart or spawn; the age
line `REDELIVER_MAX_AGE_S` (default 1800 seconds, overridden by `ROMP_REDELIVER_MAX_AGE_S`) that
pull request 1496 introduces; the oldest stamp among the stale echoes; and the session's name from
its registry row.

Today that pull request injects a one-line `[romp] N queued messages … were dropped as stale`
notice into the session, through a live session's queue or the dormant registry queue, and writes
one problem row. The dormant path also makes boot spawn every dormant session that held a stale
send. The user rejected the injected notice.

The producer replaces it. After the stale list is known, `_mark_dropped_echoes` resolves
`on_notice` on its own class defensively (`getattr(type(self), "on_notice", None)` with a callable
check, the model-fallback idiom, required because the pull request's tests bind the method onto a
bare stand-in class with no hooks) and posts one notice:

- `key` and `producer`: `dropped-sends`.
- `title`: "3 messages you typed to api before the restart were not re-sent".
- `body`: the restart time, the age line, and each dropped message's text truncated to a few lines
  with its send time.
- `actions`: one `{label: "Send again", route: "/send", body: {id: sid, text}}` per message, plus
  one for all of them when there are several. The re-feed paths the code already has are
  `_enqueue_with_id(s, text, uuid, t)` for a live session and the registry queue with its
  `queueMeta` for a dormant one; `/send` is the route `romp send` already uses to deliver a typed
  message by session identifier.
- `needsYou`: true. Resending a message the user typed is their decision.
- `dismissOnAction`: true.

The moment fires once per session per boot or spawn, so one boot produces one notice per affected
session. A later boot that drops further sends is a new revision, because it carries new
information.

The injected session notice goes away. The card is the user-facing surface, and the problem row
stays for the error centre.

The same hook path makes the model-switch card the natural second producer: `mint_fallback_card`'s
existence-keyed dedupe is this design's `key`, and its completed column is this design's default.
Moving it onto the notice store is a follow-up, not part of this note.

## Alternatives considered

- **A goal-store node of a new class, following the model-switch precedent.** Rejected: a node is
  selected by about fourteen judge menus and walks keyed on generic flags, no provenance field
  excludes it from all of them, kernel writes arrive at the end of the judges' pre-pass and must
  argue with an authority ladder built for verdicts, and the feed memo would pay across sessions
  for a per-session fact. It does buy the tree, follow-ups and the judges, none of which a notice
  needs; a producer that needs those should mint a goal, not a notice.
- **A board-wide notice file, one file with no session.** Rejected: it re-derives every session in
  the feed memo on each write, and a card with no session identifier cannot be routed or
  host-prefixed in federation, so it could never cross hosts or take a dismissal.
- **The frame's existing top-level notice channels (`clearNotices`, `sdkNotices`,
  `syncNotices`).** Rejected as the home: they feed the bell and the log, not a card the user can
  dismiss, supersede or act on, and the ask was for a card.
- **A separate "Notices" strip or pane.** Deferred, not rejected: the three columns plus the
  `needsYou` flag cover the ask, and a pane can be added later with no change to the store or the
  row shape.
- **A general plugin or webhook system for actions.** Rejected in favour of an allowlist of
  existing kernel routes, with `/send` the only entry in the first version. An arbitrary target
  would make every card a way to make the kernel issue a request on the user's behalf.
- **A kernel-backed tool in the postal server.** Rejected for the first version: that server's
  scope is mail, and a session that wants to post a card has `romp card`.

## Deferred, known gaps

- **Kernel-wide notices with no session.** Every notice belongs to a session here, so a message
  about the kernel itself has no home yet: it needs either a session-shaped stand-in or the
  board-wide file this note rejected, and neither is worth designing before a producer wants one.
- **The model-switch card's migration onto the notice store.** Mechanical once this lands, a
  separate change with its own tests.
- **A Notices pane.** Additive, and a question about the board's layout rather than about this
  store.
- **More action routes.** Superseded (2026-09-19): actions are of a KIND the kernel defines, and the
  allowlist admits kinds, never routes; the section "Action kinds and the held-mail card" below.
- **A chat presence for a notice.** The AGENT is still told nothing (a producer that wants the agent
  told sends the session a message itself); the USER, since 2026-09-19, sees the session's needs-you
  notices in a box at the bottom of the chat pane (the section below), the held-mail card first.
- **Posting to a session on another host from this host's command line tool.** `romp card` talks
  to the local kernel, so a notice for a remote session is posted on the host that owns it. The
  federated dashboard then shows it either way.
- **The archive bound.** The section above (2026-09-16) designs the revision index sidecar, Undo's tail read
  and the memo bound, and lays out the disk-growth decision; the code follows the design's read.

## Action kinds and the held-mail card (2026-09-19)

**The ask (the user, 2026-09-18, through the manager; paraphrased).** A notice card's actions are of a
KIND the backend defines: a kind names its route and its body shape, and the allowlist admits kinds,
never bare routes, so a producer can route the user's click only where the kind allows. The held-mail
(quarantine) kind is the first: Approve and Deny only, no Edit (nobody edits mail); Approve delivers,
Deny drops, both through the kernel's existing quarantine act road. The held-mail card becomes a notice
card the kernel produces when a message is held, replacing today's hand-built quarantine family where the
model allows. The card's modal shows the MESSAGE TEXT, never a struck-through remote thread name (the
user's screenshot: a struck name where the text belongs). The chat pane gets an approval box ABOVE the
background-agents box at the bottom, listing the session's held messages with Approve and Deny, vanishing
on the decision; and a held message puts the session in the blocked state (the dashed yellow ring) until
decided, so a user with the feed's messages off still sees it. The settings plan
(`plans/settings-across-machines.md`) leans on the same API: its three answers against
`/setting-proposal` are one kind.

**Where the pieces are today** (verified at `upstream/main` `48c313a5`). The hand-built family:
`_quarantine_cards` (`kernel/kernel.py`) reads the bus's held files under `STATE/postal/quarantine`
(each `{mid, to, toId, frm, frmId, body, kind, origin, via, at, toWireId?, userAsk?, relayed?}`) and builds
a card under `quarantine:<mid>` with the flavour `blocked.state === "quarantine"` carrying `mid, frm, to,
origin, body, gist, what`; the pane's face (`feed.ts`: the `qbody` block, `quarWho`, the Approve and Deny
latches keyed `{kind: "quarantine", mid}`, the decision dialog `showQuarantineDialog` with its deny-note
step, the `showBlk` exclusion and the gate line) posts `quarantineDecision {mid, action, text?, sid,
feedback?}`; the kernel's op proxies it to the bus (`_bus_quarantine_act`, `POST /quarantine/act` with
the recipient `sid` since 2026-09-18, dialled by the bus's port record) and answers a refusal as
`quarantineRefused {mid}`; the bus's `quarantine_decide(mid, action, text, feedback, sid)` approves by
replaying `deliver()` (refusing when the held sid is no longer live, id-addressed mail never re-matched by
name) or denies by dropping the hold after parking the optional note to the sender. The notice model:
`post_notice` validates `actions` through `_notice_actions_check` (up to `NOTICE_ACTIONS_MAX` entries
`{label, route, body}`, the route in `NOTICE_ACTION_ROUTES`, today `/send` alone, with the two guards on a
`/send` body); `_notice_action` runs a stored action matched exactly by route and body under the in-flight
set, `dismissOnAction` appending the `acted` row and clearing the card; the pane's `fillNoticeFace` builds
the buttons and posts `noticeAction {itemId, sid, route, body}`, answered by `noticeActionDone`; the
modal of a notice card shows the notice (`renderNoticeModalBody`, PR 1831 round four). The kernel's WS op
chain handles `noticeAction` for any client, the chat's included. The ask ring: `_feed_needs_input` holds
the sids of every card filed under `needs_input` at the last feed build, `build_session` ships it as the
status's `needsYou`, and the tab strip composes the dashed yellow ring from it (`styles.css`, the ASK
ring, 2026-09-13). The chat's bottom box: `#bg-tasks` in the chat page's markup, rendered by
`renderBgTasks` from the session's `awaitingItems`, `awaitingWhy` and `bgTasks`, hidden when empty.

**Correction to today's card, from the code:** every notice card is stamped `live: False`
(`_notice_cards`), and the card modal's header strikes the owner's name through on `!it.live` (the
dead-session dress). That is the struck-through name in the user's screenshot: not a remote thread's
name, the recipient's, struck because the notice card claims its session dead. The held-mail card
inherits the fix below for every notice card.

### Action kinds

**Decision: a stored action is `{label, kind, body}`; the kernel's kind table names each kind's route,
its body shape and its click-time input; the post validates against the table and refuses an unknown
kind or a body outside its shape; the click is matched to the stored action exactly and may add only the
input the kind names.** The allowlist is the kind table. The stored `route` member is gone from new rows;
a row written before this (`{label, route: "/send", body}`) reads as kind `send`, so no store is
rewritten and no migration runs.

The table, `_NOTICE_ACTION_KINDS` in `kernel/kernel.py`, one entry per kind:

```
send        route: the plain-message door (_deliver_text, POST /send's), target: the card's OWN session;
            body: {text: non-empty, never beginning with a slash}; no click input.
            The two guards of the review of PR 1757 stand: no id or name in the body, no typed command.
quarantine  route: the bus's /quarantine/act through _bus_quarantine_act, with the card's OWNER as the
            recipient sid; body: {mid: a safe id, verdict: "approve" | "deny"}; click input: on a deny,
            an optional {note: string} (the note the bus parks for the sender, as today); on an approve,
            none. No Edit: no text rides an approve (the user).
setting-proposal  the settings plan's kind (its three answers, Apply / Keep mine / Keep mine and pin,
            against /setting-proposal); defined in that plan and added to the table by its phase one.
```

Each kind's runner is a function in the kernel (`_notice_run_send`, `_notice_run_quarantine`) taking
`(row, action, input)`; `_notice_action` looks the kind up, refuses the click when the stored action is
not on the card's revision (matched by kind and body), refuses an `input` the kind does not name or with
members outside its shape, and calls the runner. The owner-less home (`NOTICE_OWNERLESS_SID`) takes no
actions, as today. The one-delivery-per-click guard (the in-flight set), `dismissOnAction`'s `acted`
row and the rule that a card is never a way to issue an arbitrary request hold for every kind. The kind
`quarantine` refuses at the post when the card's owner is the owner-less home (a verdict needs a
recipient) and at the click when the held file's `toId` is not the card's owner (the bus checks the
recipient it serves too, since 2026-09-18, so a decision routed to the wrong kernel names itself).

**The wire.** The pane posts `noticeAction {itemId, sid, kind, body, input?}`; `route` is accepted from an
older pane and read as kind `send`. The answer stays `noticeActionDone {itemId, ok, error}`, to the
asking client, whichever pane it is. `quarantineDecision` and `quarantineRefused` retire with the
hand-built family.

**Alternatives weighed.** A general `route` kind whose body names an allowlisted route: rejected, it is
a bare route under another name, the shape the user ruled out. Validating the kind at the click rather
than the post: rejected, the producer learns of a refused button when the user presses it. A per-kind
plugin registry: rejected, the table is small and each kind's runner needs the kernel's own doors.

### The held-mail card

**Decision: a held message is a notice card the KERNEL posts, keyed by the message id, owned by the
recipient, needs-you, Approve and Deny of kind `quarantine`, `dismissOnAction`; the hand-built family
goes.** The census of today's card and where each field lands:

- the sender (`frm`, with `origin`): the title, `New message from <frm>` (the host prefix as the card's
  chip vocabulary renders it), and the body's first line names the route, `from <origin>:<frm> to
  <to>, held because peer <origin> is DIRECTED`;
- the recipient (`toId`, `to`): the card's owner, so its chip, colour, host routing and memo key;
- the reason held: the body's first line above, and the producer label `postal`;
- the message text (`body`): the body, through the sanitizer as every notice body (peer content, shown,
  never auto-run; the card shows its first lines, the modal the whole);
- the gist: no field of its own, the body's first ninety characters are on the card;
- the message id (`mid`): the key, and the action body's `mid`; the item id `notice:<toId>:<mid>:<rev>`.

**Who posts, and when.** The kernel, at every notice attach in `build_feed` (`_notice_cards`), before
the projection: each held file under `STATE/postal/quarantine` whose `mid` has no notice under the
recipient's key, in the live rows or the revision index, gets one `post_notice(toId, mid, ...)`; the
check is one directory listing memoized on the directory's stat (the held file's publish is an
`os.replace`, which moves it), so a boot, a first build and a hold landing while the kernel was down are
all covered and a decided or dismissed card is never re-posted. The bus changes nothing for this (a
later immediacy post through `POST /notice` stays idempotent by the same key check). A held file whose
recipient this kernel does not know (a renamed or ended session) posts to the owner-less home with the
recipient's name in the title and no actions, since a verdict needs a live recipient; the bus's approve
refusal for a gone recipient is the same fact.

**How the card moves.** On a decision the runner calls `_bus_quarantine_act({mid, action, sid: owner,
feedback?})`; success appends the `acted` row and clears the card (`dismissOnAction`), and appends an
`expire` row for the key so the projection retires it whatever the ledger later says; the bus deletes the
held file, so the backfill sees nothing to post. A refusal re-arms the buttons with the bus's reason
through `noticeActionDone`. Clear dismisses the card while the held file stays, as today, and the
backfill's key check keeps the dismissed key dismissed. A message re-held under the same id cannot
happen (the sender never resends a `peer_seen` id), so a key has one revision in practice; the revision
index keeps it so even if it did.

**The card's liveness (the struck name).** `_notice_cards` stamps `live` from the build's alive roster
for the owner (`sid in alive`), False for the owner-less home, instead of False for every card; the
modal's header then strikes a name only for a session that is dead, and a live recipient's held-mail
card shows the recipient's name plain over the message text. A federated frame carries the owning
kernel's word, prefixed like its sid.

**Removal, one commit.** `_quarantine_cards` and its attach; the `quarantineDecision` op and
`quarantineRefused`; the pane's quarantine kind (the `blocked` fields, the latch kind, `_qApprove`,
`_qDeny`, `_qBody`, the `isQuar` block of `updateAskCard`, `showQuarantineDialog`, `quarWho`, the
`showBlk` exclusion, the gate line and the render-counter exemption); the kind `quarantine` in
`ui/webview/board-def.ts` (`KindId`, `KIND_IDS`, `FEED_BOARD.kinds`, the descriptor, `kindOf`), in the
kernel's `_CODE_BOARDS["feed"]["kinds"]` and `_BOARD_KINDS` (asserted together at import), in
`board-def.test.ts` (`KIND_TABLE`, the `KIND_IDS` equality, the `kindOf` case), in
`tests/test_card_boards.py` (the families dict, the count of `"board": "feed"` sites, the kinds
literal) and in `plans/card-boards.md` section 2. The pins on the family, re-aimed or retired with it:
`tests/test_kernel_trust.py` (sixteen), `tests/test_kernel_goal_compaction.py` (three),
`feed-render-incremental.test.ts` (eight), `feed-quarantine.test.ts` (the file), `feed-card-gate.test.ts`
(four), `feed-sess-clear.test.ts` (three), `feed-judge-auth.test.ts` (its exclusion), `card-notify.test.ts`
(the attach order), `feed-render-incremental.test.ts`'s render-counter case. The notice descriptor's
actions entry stays `stored`.

### The chat pane's approval box

**Decision: the chat's session frame carries a per-session slice, `notices`, the session's standing
needs-you notice cards; the chat page renders them in a box ABOVE the background-agents box; the box is
the session's needs-you notice list, the held-mail card its first content, never a mail-only widget.**
The chat page hears no feed frame (the kernel sends the feed to feed clients alone), so the box rides
`build_session`'s frame beside `needsYou`: `notices: [{itemId, title, producer, body, attachment,
actions}]`, the projection's needs-you rows for the sid in post order (at most the live cap), empty for a
session with none, absent before the first feed build as `needsYou` is. The pane renders `#notices`, a
slot above `#bg-tasks` in the chat page's markup, shown and hidden by content as that box is
(`renderNotices` beside `renderBgTasks`, keyed on the slice's content): per card the title with the
producer, the body through the sanitizer, and the action buttons, the same stored actions posted as
`noticeAction {itemId, sid, kind, body, input?}` over the chat's own socket and re-armed on
`noticeActionDone` (the chat page gains that handler), one delivery per click; a deny's note prompt is
the same optional step the feed's card takes. The box vanishes once the decision lands: the `expire` row
drops the card from the next session frame the pane hears. The box follows the transcript's bottom-box
rule (the ResizeObserver over the boxes below the transcript keeps an at-bottom reader at the bottom),
and its click-safety is the delegate on the stable `#notices` container.

**The blocked state, pinned.** A held message puts the recipient in the needs-you state by construction:
the dashed yellow ask ring reads `_feed_needs_input`, the sids of every card filed under `needs_input`,
and the recipient's held-mail notice is such a card. The rewrite keeps that road and pins it with a
served lab: a hold lands, the recipient's tab wears the ring while the decision waits, the ring clears on
the decision; the chip and the state line keep their own states, since the ring is the needs-you mark in
every live state.

### Phasing and tiers

1. **This section** (docs; the pull request carrying it).
2. **The kind API, the held-mail card, the liveness fix and the removal** (feature, unarmed; additive
   contract to producers under the user's standing ruling). Tests: `_notice_actions_check` over the
   table (each kind's shape accepted and refused by name, an unknown kind refused, an older row's route
   read as `send`); `_notice_action` matching by kind and body, refusing an unnamed input, the quarantine
   runner's bus call with the owner sid (a stubbed bus), the `acted` and `expire` rows on success, the
   re-arm on refusal; the backfill (a held file posts once, a decided or dismissed key never re-posts, a
   file for an unknown recipient posts owner-less without actions); `live` from the alive roster; the
   removal's pins retired. A served lab over a hermetic bus and kernel: a held message appears as the
   card with the sender, the text and Approve and Deny; the modal shows the message text with the
   recipient's name plain; Approve delivers (the bus's held file gone, the card gone); a second held
   message denied with a note (the note parked). Red first per test at the base.
3. **The chat box and the ring's pin** (feature, unarmed). Tests: the session frame's `notices` slice
   (present, empty, absent before the first build); the box's render (content-keyed, hidden when empty,
   the buttons posting over the chat socket, the re-arm); a served lab: a hold lands with the feed pane
   off, the box shows above the background-agents box with Approve and Deny, the tab wears the dashed
   yellow ring, Deny clears both. Red first per test at the base.
4. Two and three may be one pull request if the size stays readable; the settings plan's kind lands in
   its own phase one against the same table.

### What stays out

No Edit action (the user). No tool-permission prompts: the ask is the mail approval cards. No general
route kind. The agent is still told nothing about a notice (the injected-voice rule); the box is the
user's. No change to the bus's hold or decide contracts beyond what 2026-09-18's recipient sid already
carries.

### Privacy

The card's title and body are the held message's sender name and text, which live in the bus's held
files and the notice store under the state root, never in this repository; the kernel log names the
key (the message id) and the producer, never the text. Tests and labs use synthetic message ids,
`TESTHOST` and the notes-api world.
