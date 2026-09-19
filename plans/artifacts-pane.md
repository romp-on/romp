# The Artifacts pane (2026-09-19)

An optional pane, off by default, experimental: a session selector at its top and, under it, every file that was put
into that session's thread, as a file list, and for images a grid of BIG thumbnails, so that when a session produces a
run of plots the user can open the pane and cycle through them large. Nothing is injected into any session and no
instruction changes: this is an on-top read of what already happened, by deterministic rules only (no judge, no model
call). The user asked for it on 2026-09-19; the manager dispatched the design first.

This document records the decisions the code follows. Every premise below was checked in the code it names.

## 1. What "put into the thread" means: three rules, each with its source record

A file belongs to a session's artifacts when one of these rules names it. Each rule reads ONE record the kernel already
parses; nothing new is written anywhere.

1. **Written by the session.** An assistant turn's `tool_use` block whose `name` is one of the edit tools and whose
   `input` carries `file_path` (Write, Edit, MultiEdit) or `notebook_path` (NotebookEdit). The record is the transcript's
   assistant message content, the same read `_session_meta_step` makes for `lastEditPath` (`kernel/kernel.py`, the
   `_EDIT_TOOLS` set); the pane reads the same blocks over the whole thread instead of keeping the newest. A path is taken
   as the tool received it (absolute as Claude Code writes them); a relative one resolves against the session's cwd the
   way a click does (`_resolve_open_path`).
   *A road not taken:* Bash is NOT parsed. A `Bash` tool's `command` can create files a hundred ways (redirects, `tee`,
   `cp`, a script that writes), and guessing which words of a shell line are output paths is a heuristic that would be
   quietly wrong (CLAUDE.md: never a lossy reconstruction where the record is not authoritative). A plot a session makes
   through a script shows up under rule 2 when the session names it in its prose, which is what a session that wants the
   user to see a figure does.
2. **Rendered by the chat from the session's prose.** A bare path in assistant text that the chat renders as a figure or
   links as a file: the path-token walk of `ui/webview/path-links.ts` (`linkifyPathTokens`, the same matcher every surface
   imports) over the assistant turn's text, narrowed to what the kernel stat'd when the event carries a `pathLinks`
   verdict, and, for images, the figure rule of `render.ts` (a token whose `previewKind` is `img` renders full-size at
   its mention through the file route). The kernel side of this pane applies the same token rule to the assistant text
   of the parsed turns (`kernel.py`'s `_PREVIEW_IMG_RE`, built from `_IMG_MIME`, for image paths; the path-links grammar
   for other files), so a figure the chat showed is an artifact and one it did not is not. A `file://` URI names an
   absolute path and counts the same way.
3. **Dropped into the chat by the user.** A file dropped or pasted on the composer is saved by the kernel under the state
   directory's `drops/` (`_save_dropped_file`: `<millis>-<safe name>`) and its saved path enters the user's message: as
   an image block's source path or as a bare path in the text, both of which the user-turn image scan (`_user_images`)
   already reads. The pane reads the same user turn: a path under `STATE/drops/` in a user turn is an artifact the user
   put in, attributed to the turn that carries it.

Every rule yields `(path, t, via)`: the path, the time of the turn that mentioned it, and which rule (`write`, `edit`,
`multiedit`, `notebook`, `rendered`, `drop`). A path under `~/.claude/`, a secrets-shaped name, or a path outside the
session's folder and the user's home is listed but marked `refused` with the file route's own reason (`_slice_allowed`),
never fetched: the pane shows what the thread named, and the route decides what it will serve, as it does for the chat.

## 2. The list

- **Newest first, one entry per path.** Entries are de-duplicated by resolved path; the LATEST mention wins (its time,
  its rule), so a file written three times is one row dated by the last write, and a plot the session wrote and then
  named in its prose is one row whose rule is the later mention.
- **A missing file is shown as such, never hidden.** The kernel stats every path at request time (a regular file or
  not, its size, its mtime); a path that no longer exists (deleted since, or written on another machine) stays in the
  list with a "missing" mark and no thumbnail. Hiding it would silently drop a fact the thread states.
- Each entry: `{path, name, t, via, exists, size, mtime, kind, refused}`; `kind` is the file route's view kind
  (`image`, `pdf`, `markdown`, `code`, or `other`), the pane's grid rule below reads it. A drop's `name` is the name the
  user dropped (the saved file's millisecond prefix stripped), and a drop is the user's own file: allowed wherever the
  state directory lives, the secrets rule and the kind rule still applying.
- The list is capped at the newest 500 entries (a session that writes thousands of files is a build, not a thread of
  artifacts); the cap is stated on the page when it binds, like the notice store's live-keys cap.

## 3. The page

A single page served at `/artifacts`, built like the Files pane's (`_files_page`): the theme stylesheet, its own small
stylesheet, the pane shim (`_shim("artifacts", v, no_stale=True)`: this page receives no pushed view), `federation.js`
for host routing, and one bundle `ui/webview/artifacts.ts`.

- **The selector** at the top lists the sessions the picker lists (`requestSessions` / `sessionList`, the picker's payload:
  running first, then by recency, each with its name and identity colour), plus the session the shell's active chat
  names when it tells the pane (the `{romp:'panes'}` broadcast carries no session; a later pane-protocol field may). The
  selection is remembered per browser (`localStorage`, key `romp:artifacts:sid`).
- **The list and the grid.** Under the selector, the images of the selection (`kind === "image"`, existing, not
  refused) render as a grid of large thumbnails (a minimum of 220 px a side, three across at the pane's default width,
  `object-fit: contain`, newest first), each loaded through the file route with the session's sid, lazily
  (`loading="lazy"`). Below the grid, every entry as a file list row: the name in the link dress, the folder dimmed, the
  rule as a small word, the time as the chat's relative age, a missing file struck through with "missing" beside it, a
  refused one with the route's reason as its title. A row click on a file opens it in the Files pane when that pane is on
  screen, else in this document's viewer, the chat's own ladder (`ui/webview/file-route.ts fileLinkRoute`).
- **The large view.** A thumbnail click opens the image large IN PLACE (over the pane, the chat's lightbox dress), with
  left and right (the keyboard's arrows too) cycling through the grid's images in list order, the file's name and rule
  under it, Escape closing. One control sends the image to the Files pane's viewer: the shell's `viewFile` relay
  (`window.parent.postMessage({romp:'viewFile', path, sid, pane:'pane'})`, the same message a chat file link posts), which
  brings the Files pane forward and opens the file there; where no Files control exists (`avail.files` false in the
  pane-set broadcast) the control is hidden.
- **Thumbnails and the large view read the existing token-authed file route** (`/file?path=&sid=`, `fileUrl` in
  `ui/webview/preview.ts`, host-routed for a remote session), never a new file server. No pin: the pane shows the file
  as it stands now, and says "missing" when it does not.

## 4. The kernel's side: lazy, request and response, through the parse cache

- One WebSocket op, `listArtifacts {sid, reqId}`, answered on the asking socket with
  `artifactsListing {reqId, sid, items, capped, error}` by the kernel that owns the session (federation routes by the
  sid, as `listDir` is routed for the Files pane). No frame is pushed: an off pane costs nothing; a pane that is on but
  shows a session costs one request per selection and per explicit refresh (a Refresh control; the page also re-asks
  when the shell's pane broadcast turns it on screen).
- The kernel reads the session's parse through the existing store: `_parse_cached(path)` first (the cached parse under
  the live key, never a parse); when that misses (a cold kernel, a session no chat has opened), `jd.parsed_session(sid,
  [path], now)` once, into the same shared cache every other reader uses, so the next request and the chat's own build
  find it warm. The transcript path is the session's row (`_sessions`, the same enumeration the picker uses), forks
  included as the chat includes them.
- The walk is one pass over `turns[].atoms[]`: assistant blocks for rules 1 and 2, user blocks and text for rule 3; then
  the de-duplication, the stat, the route's verdict, the sort and the cap. No memo of its own: a request is rare (a
  selection), and the parse it reads is the memoized part.
- Nothing is written: no store, no index, no ledger. The pane's "state" is the browser's remembered selection.

## 5. The shell hooks: a new app key, hidden by default, one place each

The pane is an app key `artifacts` in the shell's pane set (`kernel.py _PANE_ORDER`, entry `("artifacts", "Artifacts")`),
so today's toggle control shows and hides it with no new mechanism:

- **The rail's toggle and the phone's tab** come from `_PANE_ORDER` (`_rail_buttons_html`, `_mtab_buttons_html`); the
  landing's `LBL` map gains its word, the `po-artifacts` body class is toggled in `apply()` beside the other five, the
  landing's CSS gains its column (`#artifacts-pane{flex:var(--g-artifacts,40) 1 0}`, hidden without `po-artifacts`, a
  gutter beside it), and the row gains its iframe `f-artifacts`, served with `data-src=/artifacts` like the optional
  panes (never loaded until shown).
- **Hidden by default like the Files pane's flag.** A fresh setting `showArtifactsControl` beside `showFilesControl`
  (`ui/webview/settings.ts`: only the literal `true` shows; the gear's Panes section gains the row), read by the landing
  the way `filesCtl()` is: off, the rail button and the tab are hidden, an open pane closes on the same apply, and
  `togglePane('artifacts')` refuses. The kernel's own `/version` says nothing about it; the flag is per browser.
- **The shipped naming pattern, exactly** (the timeline owner's word, 2026-09-19, for the registry that folds a pane in by
  key and the docking engine that keys panes by element id): the pane element `artifacts-pane`, the iframe `f-artifacts`
  with `data-src`, the body class `po-artifacts`, the grow variable `--g-artifacts`, the `_PANE_ORDER` entry with its
  `LBL` word; the pane-set broadcast carries the key from `_PANE_ORDER` for free. The docking engine
  (`ui/webview/pane-dock.ts`, on only under the gear's docking switch) lists the four dashboard panes by name today, so
  this pane shows, hides and orders through the shipped flex path and becomes a leaf of the docking tree only when the
  registry PR reads the pane set from `_PANE_ORDER`; that is the registry's change, not this one's.
- **Self-contained by protocol.** The page and the shell exchange only the pane protocol: inbound `{romp:'panes', on,
  avail}` (the shell's broadcast, so the pane knows whether the Files pane is on screen and whether its control
  exists) and outbound `{romp:'viewFile', ...}` (the existing relay). Its kernel traffic rides its own shim socket
  (`listArtifacts`, `requestSessions`). The timeline owner's coming panes-as-data registry (a pane defined like a board)
  will fold this pane in by its key and route; nothing here reaches into another pane's document.
- The viewer set that keeps the kernel's memory awake (`kernel.py`, the tuple of the five pane app keys beside the
  `viewer =` read in the conserve-memory check) gains `artifacts`, so a dashboard with only this pane open counts as a
  viewer.

## 6. Tests

- Kernel: `tests/test_artifacts_list.py`: the walk over a synthetic parsed session (two writes of one path keep the newest;
  a MultiEdit and a NotebookEdit path; a rendered image path in assistant prose; a Bash `command` naming a path yields
  nothing; a drop path in a user turn; a `file://` URI; a relative path resolved against the cwd), the stat and the
  missing mark, the refused mark for a secrets-shaped name and for a path outside the folder and home, the sort and the
  cap, the op's request and response shape and its sid routing, the pane key in `_PANE_ORDER`, the route serving the page.
- Pane: `ui/webview/artifacts.test.ts` on the pane's pure parts (the grid's kind rule, the cycle's order and wrap, the
  route ladder for a row click), and source pins on the shell hooks (the `po-artifacts` class, the iframe, the CSS, the
  setting).
- Served lab: `tests/test_artifacts_pane_served.py`: a hermetic kernel over one synthetic session in the notes-api world
  with a transcript carrying two written files (one since deleted), one rendered image path and one drop; the control
  turned on through the setting; the pane toggled on; the selector naming the session; the list's four rows newest
  first with the missing mark; the grid's thumbnails loaded through the file route; a click opening the large view and
  the arrows cycling; the `viewFile` relay posted to the shell; the pane toggled off; and, with the control off, no rail
  button and no request made (the off state costs nothing).

## 7. What stays out

- Any write: no export, no rename, no delete from the pane.
- Bash output paths (section 1), files a subagent wrote in its own transcript (a later rule, if asked), and files named
  in tool RESULTS (a `Read` of a file is not putting it into the thread).
- A pushed view or a per-session index; a pin of the file's bytes (the chat's mention pins stay the chat's).
- The panes-as-data registry itself (the timeline owner's design); this pane is written to fit it.

## 8. Privacy

Synthetic fixtures only (`TESTHOST`, the notes-api world, invented paths and placeholder uuids); the lab's files are
minted under its own temp root; no real transcript or path is copied anywhere.
