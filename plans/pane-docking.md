# The pane docking kit (grab empty space, dock panes and tabs, no chrome)

Status: PROPOSED, phased. Landing commits: TBD per phase. Tier: `feature` per phase (the
repository owner merges on green); a phase that rewrites a persisted store is called out below as a
`major-feature` discussion point, and the plan is built so none of them do.

A design line for a feature on the user's list (the user, 2026-09-18: wants to move the dashboard's
panes by grabbing their empty space rather than a title bar, so the resting appearance does not change,
and wants today's drag-a-tab-into-another-area folded into the same windowing kit; and gave the GO with
one constraint, that it ship DEFAULT OFF as an opt-in). This doc decides the layout model, the
geometry engine, the grab-and-drop protocol, the opt-in switch, the phasing, where the engine lives,
and the risks. No code here.

Line references were verified at `upstream/main` `f76aa0ba` by a parallel code-mapping pass that
grepped every cited symbol. Cite by symbol name first; the line is a current pin, not a contract.
Several premises in the dispatch are CORRECTED below against the code (they are flagged "correction:");
the corrections are the point of grounding a design in the source.

## 0. The premise, in code: three engines, one flex row, two stores

There is no single "pane engine". The dashboard shell (`_landing`) carries THREE inline JS constants in
`kernel/kernel.py`, each owning a different concern, plus the served pane pages in `ui/webview`:

- **`_LANDING_JS` (`kernel.py:57578`) owns GEOMETRY.** The horizontal widths (`setGrow(k,v)` writes a
  `--g-<key>` custom property on `.row`, `:57601`), the three column gutters `gv-a`/`gv-b`/`gv-c`
  (`gutter(gid,leftPick,rightId)`, `:57649`, exported `window.__rompGutter` `:57665`), the deferred
  resize line `#gv-ghost`, the timeline band's `--tl` height and its `#gh` row-resize gutter, and the
  split-column width helpers (`__rompGrowFair`, `__rompSplitGrow`, `__rompSplitShrink`,
  `__rompRegisterPane`, `__rompUnregisterPane`, `:57606`). Its store is **`romp-pane-grow`** (`GK`,
  `:57599`): a flat `{chat, fleet, feed, files, chat2, chat3, ...}` of flex-grow numbers (effectively
  px after a drag normalises every shown pane to its `offsetWidth`). Not ratios, not order.
- **`_LANDING_COLLAPSE_JS` (`kernel.py:60782`) owns VISIBILITY only.** (Correction: the dispatch
  attributes sizing/ratios here; sizing is `_LANDING_JS`.) It maintains `po`
  (`{chat, fleet, feed, timeline, files}` of booleans, default `chat/feed/timeline` on, `:60784`),
  toggles the `body.po-chat/po-fleet/po-feed/po-timeline/po-files` classes the row CSS keys on, wires
  the rail buttons through `togglePane(k,to)` (`:60878`, `window.__rompPaneToggle` `:60882`), and lazily
  mounts an optional pane by copying its iframe `data-src` to `src` ONCE in `reconcile(live)` (`:60844`).
  Its store is **`romp-panes`** (`PK`, `:60784`): a flat `{key: bool}` on/off set, no order, no sizes.
- **`_LANDING_SPLIT_JS` (`kernel.py:60913`) owns the CHAT COLUMN TREE.** The side split and the
  bottom (vertical) split of a chat pane: `moveTab(sid,to,dt)` (`:61036`, `window.__rompMoveTab`
  `:61104`), `canSplit()` (`:61024`, `cols.length+1<MAX`), the drop-zone factory `zone(p,cls,col,onDrop)`
  (`:61144`) and `mountZones()` (`:61150`), `make()`/`close()`, `sideCols()`/`isBelow()`/`belowOf()`,
  the vertical divider `gutterV` (writes the entry `ratio`). Its store is **`romp-chat-cols`** (`CK`,
  `{v:2, cols:[{n, ids, place?, parent?, ratio?}]}`), plus a per-column blob
  `romp-vscode-state-chat:<n>` (`BK`). This is the shipped side + vertical split (see
  `plans/chat-vertical-split.md`; correction: that plan is IMPLEMENTED, not pending).

Two more facts the kit must build on:

- **The layout is FLEXBOX, not grid, not absolute.** `.col{display:flex;flex-direction:column}`
  (`kernel.py:61620`) stacks `.row{display:flex}` (`:61624`) over the `#gh` gutter over the timeline
  band `#tl-pane{flex:0 0 var(--tl)}` (`:62171`, a full-width BOTTOM band, NOT a column in the row).
  The row is `chat-pane | gv-a | fleet-pane | gv-b | feed-pane | gv-c | files-pane` in FIXED DOM order
  (`:62543`), each `#<x>-pane{flex:var(--g-<x>,N) 1 0}` (`:62149`); the 7px `.gv` gutters are DOM
  siblings (`.row>.gv{flex:0 0 7px}` `:62162`). A hidden pane is `display:none` via
  `body:not(.po-<x>) #<x>-pane` (`:62150`); its iframe stays MOUNTED.
- **A pane's on/off is layered ABOVE availability.** The gear's `romp:settings.panes` (a
  per-browser `{timeline, fleet, feed}` in the localStorage settings store, `settings.ts:44`) decides
  MEMBERSHIP (whether a pane is offered at all); `romp-panes` decides on-screen. An unavailable pane has
  no rail button and no leaf.

The single most important prior art: **session state already moves between chat frames without
re-parenting.** A tab that changes column is handed over by the views-adoption path (`adoptViews`,
`adoptArrival`, `takeViews` in `render.ts`/`views-writes.ts`/`tab-order.ts`), and the destination
iframe stays where it is in the DOM. This is why chat columns can join a general tree (section 2).

## 1. The model: a split tree of registered panes

**Decision: one recursive layout tree per dashboard, whose nodes are SPLITS and whose leaves are
tab groups.** A node is either

- a `split`: `{ dir: "row" | "col", kids: Node[], ratios: number[] }` (ratios sum to 1, one per kid), or
- a `leaf`: `{ pane: PaneId, group?: PaneId[], active?: PaneId }`. Every leaf names a `pane`; a chat leaf
  (today's column) also carries `group`, the ids of the sessions it holds, and `active`, the shown one.
  The module never reads the group's contents, only whether it is non-empty (a chat leaf holding sessions
  is never closed, only emptied), so it types the ids as opaque strings; there is no group-only leaf.

`row` lays kids left to right, `col` top to bottom, to ANY depth. This SUPERSEDES three shipped
limits, each a hard-coded refusal the kit relaxes: the two-rows-deep cap (`moveTab('down')` refuses a
tab already `isBelow`, "A split pane cannot split again", `kernel.py:61047`), the one-bottom-pane cap
(`read()` sanitisation drops a second/nested bottom pane), and the fixed horizontal row order. It also
folds the timeline band and the four dashboard panes into the SAME tree, so "top and bottom" stops being
a special case of the outer `.col` and becomes an ordinary `col` split.

**Every surface is a registered pane.** A registry entry:

```
{ id, title, mount, minW, minH, defaultDock }
```

`id`/`title`/`mount` are read from the shipped panes: `f-chat` (Chat), `f-fleet` (Outline, served at
`/fleet`; correction: prose says Outline, the code keeps the legacy `fleet` identifier), `f-feed`
(Feed), `f-files` (Files), `f-timeline` (Sessions), plus each chat column `f-chat-<n>` and a later file
viewer (`kernel.py:57683` `PANE` map; iframe ids `:62554`). `title` is for the keyboard palette only,
never a resting title bar (section 3). `minW`/`minH` are the drag-time clamps that already exist
(section 5); `defaultDock` is the position the rail's "open" uses (section 5).

**Correction: there is no "minimum size from the many-sessions rule" in code.** `ui/CLAUDE.md` states
the design value (many concurrent sessions must stay legible), but the ENFORCED floors are drag-time
clamps inside the gutter handlers: `min(120px, 25%)` of the pair horizontally (`kernel.py:57658`),
`min(80px, 20%)` vertically (`:60986`), and the pane cap `MAX=4` (`:60915`). The kit lifts these bare
literals into the registry (`minW`/`minH` per pane) so the value is named once and can be derived from a
legibility target rather than copied.

## 2. Rendering by geometry, never re-parenting

**Decision: a flat layer of ABSOLUTELY positioned pane iframes, each assigned a rectangle computed from
the tree; the engine never moves an iframe in the DOM.** Chosen over the two alternatives:

- **Nested flex (today, extended).** Works to arbitrary depth in principle, but restructuring the tree
  (a move) requires RE-PARENTING an iframe into a different nested flex box, and a same-origin iframe
  reloads when it is re-parented (the exact reason `_LANDING_SPLIT_JS`'s `makeBelow()` keeps the parent
  iframe IN PLACE as the top sub and only nests a NEW iframe: `kernel.py:60997`, comment `:60980` "an
  iframe reparented in the DOM reloads, so the top pane is never moved"; `make()` `:61006` is the
  side-column path). Re-parenting on every move would drop each moved pane's socket and scroll. Rejected.
- **CSS grid areas.** A grid template must be re-declared on every structural change and every leaf
  mapped to a named area; arbitrary depth needs nested grids, which reintroduces the re-parenting
  problem. Rejected.

Absolute positioning keeps every pane iframe a FLAT child of one `#pane-layer`; the engine is a pure
function `layout(node, box: Rect, gutter) -> Array<{ pane, rect }>` (each leaf's rectangle, the gutter
thickness subtracted between siblings) and a writer that sets each iframe's
`transform`/`top`/`left`/`width`/`height` (so a container resize is one recompute). An
iframe never moves in the document, so a move is a pure geometry change: no reload, no lost socket. This
is the literal reading of the dispatch's "render by geometry, never by re-parenting" and it is why the
chat columns can join: they already hand session state between frames (section 0), so a chat leaf is
just a tab group whose rectangle the tree assigns.

**Gutters and the ghost line generalise PER AXIS, not uniformly.** (Correction: the dispatch says the
gutters' ghost-line pattern generalises to every split edge; the code refutes a single pattern.) An
internal edge of the tree is the boundary between two sibling rects; dragging it re-computes the two
ratios. But the two shipped resize mechanisms fork deliberately by cost, and the kit must keep the fork:

- **Horizontal column dividers DEFER via `#gv-ghost`** (`_LANDING_JS`): a flex-grow write on `.row`
  reflows every same-origin pane iframe (expensive with large chat documents), so the drag moves only a
  fixed ghost LINE (`#gv-ghost`, `position:fixed`, `z-index:40`, `pointer-events:none`) on `mousemove`
  and commits both sizes ONCE on `mouseup`.
- **The vertical split resize writes LIVE** (`gutterV`): a height trade inside one column is cheap, so
  it OMITS the ghost line and writes the ratio as the mouse moves.

In the tree, "expensive" becomes a property of the edge (does resizing it move iframes that carry heavy
documents), so the generic edge-drag reads that property and picks deferred-ghost or live. The reusable
overlay is the existing `zone()`/`#gv-ghost`/`#col-ghost` family (all `z-index:40`, `position:fixed`,
`pointer-events:none`, above the focus ring at `z6`); the drop-zone band (`#col-ghost`) is already the
more generic overlay and is kept.

## 3. No title bars, no resting chrome: grab surfaces and the drag arm

**Decision: the kit adds NO resting chrome. It exists only while a drag is in flight.** No title bars,
no handles at rest, no z-order, no floating windows, no minimise. The grab surfaces are places that
already exist, plus a modifier:

- **The empty run of a pane's existing top row, WHERE ONE EXISTS.** (Correction: the dispatch lists an
  empty top-row run for chat, feed, outline and files; the code gives one to only two.) Verified:
  - Chat `#tabbar`/`#tabs` HAS a real empty run between the `+` tab and the right-pushed
    `.tab-strip-end` (`margin-left:auto`). USABLE.
  - Files `.fileview-bar`/`.fb-bar` HAS a gap, but only in the open/browse states (`#files-empty` has
    no bar). USABLE when present.
  - Feed: chips live in the BOTTOM footer `#feed-foot`; `#feed-head` is `display:none`. NO top run.
  - Outline (`/fleet`): the top is a full-width `#fleet-search-bar` (an input), not an empty strip. NO
    safe top run.
  - Timeline: an SVG band with bottom-left controls. NO top row.

  So the kit does NOT invent a top strip for feed/outline/timeline (that would add the resting chrome
  the user is avoiding). Their grab surface is the SHELL, below.
- **The gutters and the pane's outer padding ring.** The column gutters `#gv-a/#gv-b/#gv-c` and the row
  gutter `#gh` are already `cursor:col-resize`/`row-resize` grab targets; a pane's outer padding
  (`.col{padding-right:3px}` `:61620`, and the pane's own padding) is dead space today. The kit treats
  the gutter and the padding ring as pane-move handles (a plain drag resizes an edge as now; a drag that
  crosses into another pane's zone docks). This gives feed/outline/timeline a handle without chrome.
- **Option-drag (Alt) anywhere, including over content.** Holding Option (mac) / Alt (elsewhere),
  `e.altKey` uniformly, arms a pane move from any point in a pane, over content included. This is
  COHERENT with an existing binding: Alt(Option)+Arrow ALREADY moves focus between panes
  (`onKey`, `kernel.py:57739`, `if(!e.altKey||e.shiftKey||e.ctrlKey||e.metaKey)return; ... moveFocus(dir)`,
  wired per-iframe in capture and at the shell). (Correction/new: no Alt-DRAG exists today; only the
  Alt+Arrow KEY binding. The pointer gesture is new surface, but it reuses the same modifier meaning
  "operate on whole panes".)

**The arm gate. Decision: a slop with input evidence, built fresh; do NOT reuse native HTML5 drag for
panes.** (Correction: the dispatch's "a drag begins after a slop with input evidence" describes no
single existing rule. The code has THREE unrelated drag models: native HTML5 drag for the tab strip
(`render.ts:6226/6366`, browser owns click-vs-drag, no slop), pointer-capture for the board chips, and
document-listeners for the settings grips. The slop `DRAG_SLOP_PX=4` in `feed.ts` is a focus/blur
decision, not a drag arm; the input-evidence machinery `gestureEvidence`/`scrollerGrab`
(`landing-settle.ts`), wired by `settleInput` (`render.ts:11667`), is the scroll-settle takeover,
unrelated.) The pane kit adopts the
DOCUMENT-LISTENER model (section 11 precedent), and arms a drag only after a pointer travels a few px
(a real slop, `DRAG_SLOP_PX`-style) FROM a valid grab surface with the primary button held. Until the
slop is crossed:

- **a press on selectable text or a control is never a drag** (mousedown on an input, a button, a link,
  or a text selection yields to that target; Option-drag suppresses selection only while Alt is held);
- **a scroll stays a scroll** (a wheel or a touch-scroll is never a drag);
- only after the slop does the pane LIFT slightly and the drop zones appear on the others.

**Touch: a long-press to lift.** (Correction/new: no long-press-to-drag timer exists; `render.ts:6098`
`releaseTabStrip` is the tab strip's click-safe press-hold, not a drag arm.) The kit adds a touch
long-press timer that arms a pane move; a scroll before the timer cancels it. Desktop needs no
long-press (the slop + Option cover it).

**The empty space INSIDE a pane is a grab surface (decided 2026-09-19, the user, who switched the kit on,
reached for the empty area of the feed below its cards and of the sessions band, and got no hand and no drag: the
hand showed only on the ring).** The shell can see only its own chrome (the ring, the dividers) and what it reads
into the same-origin pane documents; a pane's content is an iframe whose pointer events never reach the parent. So
each inner page owns the detection, and the shell owns the drag:

- While the kit is on, the shell marks every pane document's body `pane-docking` and injects the grab detector
  (`dist/pane-grab.js`, the same road as the cursor stylesheet it already injects); with the kit off the pages
  carry no class, no detector and no rule, so they stay byte-identical.
- The detector knows each page's EMPTY BACKGROUND by app: the feed's list, its columns and their card lists where
  no card, chip or control is under the pointer; the sessions band's SVG and its wrap outside every lane, bar and
  mark; the outline's list below its rows and its footer; the Files pane's empty state and its body. The chat is
  the deliberate exception: a press-drag over the transcript is a text selection (the rule above), so its grab
  surface stays the strip's empty run. The open hand shows over exactly those targets: the detector toggles a body
  class on pointer moves by the target under the pointer, so a card's text never wears a hand it cannot honour.
- On a primary-button press with no modifier on an empty target, the detector CAPTURES the pointer on the pressed
  element, so the moves keep flowing to that document once the pointer leaves the iframe, and posts
  `{romp:"paneGrab", clientX, clientY, pointerId}` to the shell. The shell resolves the pane from the message's
  source frame and arms exactly the press it arms on the ring: the slop, the closed hand, the live outline, Escape,
  the drop as a tree move. It hears the frame's pointermove and pointerup through the frame's window, as it already
  does for Option-drag; no transparent capture layer is laid over the iframes, because capture inside the child
  document is what keeps the pressed pointer's events addressable at all.

## 4. One drop protocol, two payloads, one zone set per pane

**Decision: one drag protocol carries either a WHOLE PANE or a SINGLE TAB, dropped against one uniform
zone set per pane.** The zone set per pane is: `left`, `right`, `top`, `bottom` halves (dock/split on
that edge) plus the TAB STRIP as a join zone. The payload rides the existing side channel: the tab drag
posts `{romp:"tabDrag", sid, ...}` via `postMessage` (the sid never rides `dataTransfer`,
`render.ts` `postTabDrag`); a pane drag posts the same shape with a pane id.

What is REUSED (shipped): the zone factory `zone(p,cls,col,onDrop)` (`kernel.py:61144`) and
`mountZones()` (`:61150`) already build hit-areas that read nothing from `dataTransfer` and drive one
mutation, `moveTab` (`:61036`); the busy gates and the deferred teardown of a busy column
(`closeBusy`, the `deferred[]` path) already handle a drop onto a working pane; the cap accounting is
uniform (`MAX=4` counts the first column and every entry, `canSplit()` `:61024`); the tag-section and
row-break geometry in the strip's virtual reorder (`dragslot.ts`) already handles grouped strips.

What is NEW surface (named so a phase can scope it):

- **Left, top, and insert-between-panes zones.** `mountZones()` builds a RIGHT-edge zone (open a new
  column, `moveTab 'new'`), a per-pane BOTTOM zone (split down, `moveTab 'down'`), and a whole-pane
  column/join zone (`zone(p,'',c.n,...)`, `:61153`); it builds NO left, top, or between-siblings edge
  zone. Those are new zone geometry plus new `moveTab` targets (a tree insertion at an index).
- **A chosen-index dock into another strip.** Joining another chat leaf's strip TODAY appends with no
  slot choice (the shell overlay mounts the zones, the target iframe never runs its own `dragover`, so
  there is no cross-strip slot selection). Reorder-within-a-strip keeps its live virtual hit-test
  (`dragslot.ts`, computed against a virtual wrap layout, not the live DOM, the fix for the 2026-08-28
  slot oscillation). A chosen-index dock across strips would need a cross-iframe drag protocol; the kit
  MAY defer it (append-on-join is acceptable) and says so.

The mapping of a drop to an operation: a tab on a pane EDGE opens a new chat leaf there (a new `col`/`row`
split with a chat group); a tab on another chat leaf's STRIP joins that group (today's move); a whole
pane on an edge docks/splits; a whole pane on a strip is refused (a pane is not a tab). Reorder within a
strip is unchanged.

## 5. Five operations, presets, and the rail

**The five operations**, each a pure tree edit plus the geometry recompute:

1. **dock / split** (insert a leaf at an edge, subdividing a node);
2. **join a group** (append a session id to a leaf's `group`);
3. **move** (detach a leaf/subtree and re-insert it elsewhere; a drag is move + the drop target);
4. **resize** (re-weight a split's `ratios`; the per-axis divider of section 2);
5. **close / open** (PARK a leaf: remove it from the tree and collapse its parent split, but keep its
   iframe mounted and hidden with its socket intact; or re-insert a parked or new pane at its
   `defaultDock`). See the rail note below: a close never unmounts.

No floating windows, no z-order, no minimise (the dispatch's exclusion, and coherent with the flat
positioned layer: there is nothing to stack).

**The rail toggle becomes "open at default dock"; a close PARKS, it never unmounts.** (Correction:
today `togglePane` HIDES a pane in place, `body.po-<x>` -> `display:none`, and the iframe stays mounted
with its socket; `:60878`.) Under the kit, opening a pane inserts a leaf at its `defaultDock`; closing
removes the leaf FROM THE TREE (so the geometry recompute stops allotting it a rectangle) but keeps its
iframe MOUNTED in the flat pane layer, hidden, socket and state intact, exactly today's `togglePane`
feel. Decision (recorded 2026-09-18): a close PARKS, it does not unmount. Reasons: the rail toggle is a
glance gesture the user does often (hide the feed, bring it back), so a reload with lost scroll and a
dropped socket on every toggle would be a felt regression the kit itself introduced; parity with the
OFF path (byte-identical toggling) is the safest promise for an opt-in switch. The parked set is
bookkeeping in the layout store (a `parked` list beside the tree), not chrome. The lazy FIRST mount
stays (`reconcile()` copying `data-src`->`src` once); re-opening re-inserts the parked leaf at its
default dock with no reload.

**A chat leaf holding sessions is never closed from the rail** (today's rule, kept). A chat leaf can
only be EMPTIED, by moving its tabs out to another leaf or a new one; an empty chat leaf then collapses.
The busy gates (`closeBusy`, `kernel.py:60974`) and the session hand-off (section 0) exist precisely so
a leaf holding sessions is never torn down by a close, and parking (above) preserves that guarantee for
the dashboard panes too.

**Presets and reset.** (Correction/new: no layout-preset or layout-reset precedent exists; the nearest
shapes are the `ChatScheme` enum `settings.ts:38` for a small named set and the shortcuts modal's Reset
button `shortcuts-modal.ts:285`.) The kit ships two or three named presets (for example
"columns" = today's row, "reading" = one wide chat + a narrow outline, "triage" = feed and outline
flanking chat) as stored layout trees selectable from the palette, plus a "reset to default" that
restores the columns preset. Presets are the tree analogue of the scheme enum: a small closed set the
palette lists.

**Keyboard.** Moves go through the palette (the dispatch): the palette lists the five operations and the
presets; Alt(Option)+Arrow keeps its shipped meaning (move focus between panes, `onKey` `:57739`), and a
palette command performs a MOVE of the focused pane. No new global chord is minted.

## 6. Default OFF, opt in: the switch, the store, migration

**Decision: a fresh per-browser boolean `paneDocking` in the gear's localStorage settings store,
defaulting off, copying `denseChrome` end to end.** `denseChrome` (`settings.ts:30`, default `false` in
`DEFAULT_SETTINGS` `:73`, applied as a body class by `dense-chrome.ts`) is the exact precedent: a
per-browser view concern that needs no kernel round-trip and fans out the gear's way (the same-document
signal plus the VS Code host relay). The recipe: add `paneDocking: boolean` to `RompSettings`
(`settings.ts`), `paneDocking: false` to `DEFAULT_SETTINGS`, the normalisation line, a gear checkbox in
the Chat/Layout section, and a reader in the shell. The fresh-key idiom holds even for a brand-new key
(the memory rule `flip-a-default-under-a-fresh-key`): `gear.js save()` writes the whole object, so
`paneDocking` must be its own key, never a reuse of `panes`.

**With the switch OFF: byte-identical, no kit code runs.** (This is the unknown the mapping pass could
not settle from code; the plan RESOLVES it by construction.) The kit is a separate bundle
(section 10) whose `<script>` tag is emitted only when `paneDocking` is on, or whose IIFE early-returns
when off, so no kit code executes. The three shipped stores (`romp-panes`, `romp-pane-grow`,
`romp-chat-cols`) and their engines drive the layout exactly as today, and the OFF path must stay
STRICTLY ADDITIVE because source pins assert exact emitted substrings: the `po` default and `GK` in
`tests/test_kernel_pane_rail.py`, the inline `<script>` count `== 21` in `tests/test_kernel_mobile.py:193`,
the gutter drag in `tests/test_pane_gutters.py`/`test_pane_gutter_drag.py`, and the split source-pins in
`ui/webview/chat-split.test.ts`. A phase that changes any of those literals is not additive and is
called out.

**The versioned layout store.** A NEW key `romp-layout` (`{v:1, tree, parked}`, where `parked` is the ids
of panes closed from the rail but kept mounted, section 5), written only when the kit is
on. Turning the kit ON migrates ONCE by READING the three old keys to SEED the tree (romp-panes ->
which leaves exist, romp-pane-grow -> the row split's ratios, romp-chat-cols -> the chat leaves and
their bottom splits), and does NOT rewrite them. Turning the kit OFF keeps `romp-layout` on disk but
stops applying it; the old keys, untouched, drive today's layout again. Because the old stores are never
rewritten, turning the kit off is lossless and each phase stays a `feature`, not a persisted-contract
break. The migration must read all THREE keys plus the `romp:settings.panes` availability layer (a
migration reading only romp-panes/romp-pane-grow would lose every optional-pane availability and every
split column).

## 7. Discoverability without chrome

Three cues, none of them resting chrome:

- **A one-time first-hover hint** on the first hover of a grab surface (the empty tab-strip run, a
  gutter, the padding ring), dismissed forever after once shown. (Correction/new: no coachmark or
  once-shown precedent exists; the "seen" hits in `ui/webview` are card-signature sets, unrelated.) A
  fresh localStorage flag (for example `romp-panedock-hinted`) gates it; it is a tooltip, not a modal.
- **The Option-drag cursor.** While Alt(Option) is held over a pane, the cursor becomes a move cursor,
  advertising that a drag will move the pane.
- **The palette** lists the five operations and the presets, so every move is reachable and named
  without any on-screen affordance.

## 8. The phone fallback

**Decision: the tree collapses to tabs on the phone, exactly as today; no docking on touch in the first
release.** The shipped fallback is fully in place: `_MOBILE_MQ` (`kernel.py:60155`) turns `.row` into
`display:block`, `.gv/.gh/.pane-rail` into `display:none`, `.pane` into `display:contents`, and shows a
bottom `#mtabs` tab bar of one pane at a time; splits are hard-refused on the phone (`mobile()` gates
`canSplit()` and `mountZones()` early-returns, "one pane at a time"). A second breakpoint
`_CHAT_MOBILE_CSS` (`:56976`) collapses the chat iframe's own session strip to a compact picker. The
kit renders the tree as the ordered set of its leaves for the `#mtabs` picker (a depth-first flatten),
and the touch long-press (section 3) is the only docking gesture the phone could later gain; the first
release keeps the phone at parity with today.

## 9. Phasing as pull requests

Each phase is a `feature` (section 10), ships DEFAULT OFF, keeps every existing served lab and node test
green, and adds served labs at `deviceScaleFactor` 1 and 2 (per-test `newContext({deviceScaleFactor:N})`,
the pattern of `tests/test_chat_line_raster_served.py`, the shipped 1-and-2 case).

1. **The pure layout-tree module.** `ui/webview/pane-tree.ts`: `splitAt`, `move`, `closePane` (park),
   `openPane`, `resize`, `serialise`/`parse`, `layout(node, box, gutter)->[{pane,rect}]`, all pure, no DOM. Node tests
   (`ui/webview/pane-tree.test.ts`) that EXECUTE it (the `chat-columns.ts` + `chat-columns.test.ts`
   precedent: a documented pure module run under `node --test out-tests/**`, `esbuild.js testBuild`).
   Behind the switch this phase renders today's row unchanged (the tree seeded from the old keys
   produces the same rects). No shell behavior change; this is the safest first PR.
2. **Grab-and-dock plus gutters for the four dashboard panes.** The `panedock-main.ts` engine
   (section 10) positions the four panes from the tree, mounts the generalised zones and the per-axis
   divider, and wires the drag arm (section 3). Served labs at 1x/2x for dock/move/resize of the four
   panes.
3. **The chat columns folded into the tree.** A chat leaf becomes a tree node; `moveTab` becomes a tree
   edit; `romp-chat-cols` is read to seed and then the tree is authoritative while the kit is on. The
   take/adopt handoff (section 0) is unchanged. Served labs: a long session in every pane (the wall
   lesson from `plans/chat-vertical-split.md` section 6), reorder within a strip still green.
4. **Presets, reset, and the palette moves** (section 5).
5. **The phone story** (section 8): the tree flattened into `#mtabs`, and a decision on touch docking.

## 10. Where the engine lives, and the tier

**Decision: a new compiled module `ui/webview/panedock-main.ts`, served as `dist/panedock-main.js` and
loaded by a `<script src>` tag, plus the pure `ui/webview/pane-tree.ts`. No new inline JS.** The shell
carries the pane logic as inline `_LANDING_*` strings today; the dispatch forbids adding more. The
verified pattern: `palette-main.ts` is a self-booting IIFE, entered in `vscode-extension/esbuild.js`
`webview.entryPoints`, output to `dist/palette-main.js`, and loaded by a `<script src>` tag in the shell
head with the `?v=` cache-bust from `_dist_ver()`. A `<script src>` does NOT increment
`html.count('<script>')`, so the inline count stays `21` and `tests/test_kernel_mobile.py:193` stays
green without touching inline code. `panedock-main.ts` boots only when `paneDocking` is on; it reads and
writes `romp-layout`, positions the flat pane layer, and exposes the same globals the inline logic
exposes where it must interoperate during the phased transition (`window.__rompPaneToggle`,
`__rompMoveTab`, `__rompGutter`), delegating to the shipped inline paths while the kit is off.

**Tier per phase: `feature`** (the repository owner merges on green; sections 0 and 6 keep every phase
additive and every old store intact). The ONE contract to watch is the layout store: introducing
`romp-layout` is additive (a new key, read-only against the old ones), so it is a `feature`; if a later
phase ever RETIRED an inline `_LANDING_*` block or REWROTE `romp-chat-cols`/`romp-pane-grow` into the
tree (dropping the old keys), that is a persisted-contract change and a `major-feature` discussion in a
linked issue. The plan avoids it by keeping the old keys as the OFF-path source of truth.

## 11. Risks and open questions

- **The settings drag-handle precedent is the model, not native drag.** The settings grips use DOCUMENT
  listeners, not pointer capture, and a live drag answers "no" to the close probe (`__rompSettingsClose`
  reads a module-scope `widgetDrag` flag set at drag start, cleared at every drag end). The kit copies
  this: a module-scope `paneDrag` flag, document `pointermove`/`pointerup` listeners, primary-button
  only, a capture-phase Escape that cancels the drag and restores the tree. Reusing this precedent
  (rather than native HTML5 drag) is what lets the kit arm on a slop and drag over content.
- **A half-built kit never shows** (the memory rule, and the `ui/CLAUDE.md:93` loader precedent, the
  `distiller-line.ts:11` "produced content shows only once non-empty" precedent). The drop zones, the
  lift, and the ghost appear only AFTER the slop is crossed; a cancelled or too-short press leaves the
  layout untouched, with no flash of zones.
- **The re-parenting reload trap.** Section 2's flat positioned layer is load-bearing: any code path
  that moves an iframe in the DOM reloads it and drops its socket. Every tree edit must be a geometry
  recompute, never a DOM move of an iframe.
- **The two sizing mechanisms.** Horizontal (deferred `#gv-ghost`, flex-grow px) and vertical (live
  ratio) resize differ by cost, not by whim; the general edge-drag must keep the per-axis fork
  (section 2). Open question: whether the tree should store all edges as ratios (uniform) and translate
  to the flex-grow store only at the row level for the OFF path, or keep two stores. Recommendation:
  the tree stores ratios uniformly; the OFF path keeps `romp-pane-grow`.
- **The timeline band keeps a FIXED height via a fixed-px kid (decided, phase two).** Today `#tl-pane`
  is `flex:0 0 var(--tl)`, a fixed ~200 px band, but a ratio kid grows with the window (200 px at 900
  high becomes 312 px at 1400). Decision: a split kid may carry a FIXED px size instead of a ratio share;
  `layout` allots the fixed kids their px first, then divides the remainder among the ratio kids. This
  matches the band's `flex:0 0` exactly and is cleaner than the alternative (the shell re-seeding the
  band's ratio from the viewport on every resize, which drifts and needs a resize listener). The module
  gains the fixed-px kid in PHASE TWO, when the band is first wired; until then `seedRowOverBand`'s ratio
  band is a phase-one placeholder with no consumer, so no module change is needed this round.
- **Feed/outline/timeline have no top grab run** (section 3). If, later, the user wants a consistent top
  grab strip across panes, feed and outline would need to surface a thin top row (both currently dock
  controls in a bottom footer); that is a per-pane change outside this kit and is left open.
- **The 4-pane cap and the availability layer.** The kit relaxes `MAX=4` and the two-deep limit; it must
  choose a new sane cap (a legibility floor from `minW`/`minH` rather than a fixed count) and keep the
  `romp:settings.panes` availability layer above the tree (an unavailable pane has no leaf and no dock).
- **Cross-machine convergence is explicitly OUT.** The layout is per-browser (localStorage), like
  `denseChrome` and the shipped `po`/`romp-pane-grow`. Converging a layout across attached machines
  (a kernel STATE file plus a `/version` mesh, the task-tracking model) is a separate product decision,
  not this kit.

**Resolved (2026-09-18): a close PARKS the iframe, it does not unmount** (section 5). The open question
this doc first carried, unmount a closed pane versus keep it mounted-but-hidden, is decided for parking:
a close removes the leaf from the tree but keeps the iframe mounted and hidden with its socket and state,
matching today's `togglePane` feel. The reasons: the rail toggle is a frequent glance gesture, so a
reload with lost scroll and a dropped socket on every toggle would be a felt regression the kit itself
introduced; a chat leaf holding sessions must never be torn down by a close (the busy gates and the
state hand-off exist to prevent exactly that, and a chat leaf is emptied, never rail-closed); and
byte-identical toggling is the safest parity promise for an opt-in switch. The parked set is bookkeeping
in the layout store, not chrome. Everything else in this plan stands as written.
