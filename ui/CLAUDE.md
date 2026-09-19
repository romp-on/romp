# romp UI design rules

Repo-wide rules live in the root `CLAUDE.md`; these apply to any UI work.

### Progressive disclosure is the UI's organizing principle (user rule, 2026-07-17)
Every surface defaults to its most COMPACT legible form, and you can always click
to go one level deeper — gist → summary → full mechanics, each level a click. When
adding or changing any UI element, ask "what is the one-line version?" and render
that by default, with the rest behind a keyed expand (state survives re-renders —
`openFolds` / `expandedGroups`). Never dead-end a compact view: if there is more
underneath, it must be clickable. Existing examples: tool heads with inline folds,
collapsed tool-group runs, notice cards, postal/teammate cards, nudge gists,
Task/Agent prompt+report. This is the "Glanceable by default; mechanics one click
away" bullet of the Philosophy, stated as the standing rule for every new surface.

### Panels open as centered modals over a dimmed, UNCHANGED dashboard (user rule, 2026-08-08)
Every panel that opens over the dashboard — settings, the new-session picker, the
Log, remote kernels, the command palette, and every future one — wears ONE
treatment: a centered card over a translucent `rgba(0,0,0,0.55)` backdrop, with
everything behind it left exactly as it was (dimmed but visible — never hidden,
never solid black, never a layout change). Shell-native panels (`#rnet-back`,
`#rerr-back`, `#rpal-back`) get this for free: their backdrop composites over the
real panes. A panel living INSIDE a pane iframe that must cover the whole window
(settings, picker) is lifted by the shell (`body.settings-open` /
`body.picker-open`) and must then keep every pixel behind its backdrop looking
untouched: the page's `html` goes transparent AND the shell's lift rule sets the
iframe ELEMENT's own `background:transparent` (the default
`iframe{background:#1e1e1e}` otherwise turns the dim into a full-window black-out
— the 2026-08-08 bug, twice); and the page's BODY is pinned to the pane's old
screen rect and KEEPS PAINTING (`--pane-*` vars measured from the shell's pane
div: `placeLifted()` in render.ts / gear.js), because hiding the content instead
leaves a black hole where that pane was — the same bug's third form. The pinned
body's own `background` must stay TRANSPARENT, with the pane-rect backing on a
`::before` child (absolute inset 0, `--bg`, z-index -1): with the root
transparent, CSS promotes the BODY's background to the CANVAS — the whole
viewport — so an opaque body background painted a full-window sheet under the dim
and blacked out every pane outside the pinned rect. That is the bug's FOURTH form
(2026-08-09, found by headless pixel comparison after the third fix; a child's
background never propagates). Only an unmeasurable pane (hidden, or a
cross-origin parent like VS Code) falls back to hiding its content (`.pane-gone`
/ `.rs-pane-gone`), which also hides the backing pseudo — its var-less box spans
the viewport. Small pane-local dialogs
(confirm boxes, the feed's card modal) stay pane-local by design; this rule is
for panels that present over the dashboard as a whole.

### Font sizes: few, and consistent by information type (user rule, 2026-07-02)
Do not multiply font sizes. Similar kinds of information wear the SAME size — labels
match labels, times match the lines they annotate, section bodies match each other.
Before adding a new `font-size`, reuse one already on the surface; nesting relative
`em` sizes compounds (a 0.74em button inside 0.86em text renders smaller than its
siblings), so prefer flat contexts or compensate explicitly. Triggered by the
follow-up header rendering as a soup of 0.74/0.78/0.9em fragments.

### Menus and dropdowns wear ONE vocabulary (user rule, 2026-08-09)
Every dropdown on every romp surface — the chat tab context menu, the statusline
meta menus, the timeline's lane gear + model/effort pickers, and any future one —
wears the same skin, expressed through the menu TOKENS each theme defines
(`ui/webview/styles.css` `:root` + `body.theme-light`, mirrored in `feed.css`):
`--menu-bg` card, `--menu-fg` text, `--menu-border` hairline, `--menu-hover` row
wash, `--radius-menu`, `--shadow-menu`, and the `--check-bg` ✓-in-circle current
mark — plus 12px romp sans and sub-lines `0.82em` at 0.6 opacity (geometry, not
theme). The DARK themes resolve those to the values the rule always named, byte
for byte: `#252526` card, `rgba(255,255,255,0.12)` hairline, 6px radius,
`0 4px 12px rgba(0,0,0,0.35)` shadow, `#cccccc` text, `#1EA1EB` ✓; the light
theme resolves them in its own palette (cream card, clay ✓). Never write those
hex values into a menu rule or inline menu string except as a `var()` FALLBACK
(a file:// harness or a foreign host loads no sheet) — the 2026-09-02 light-mode
bug was exactly that: pickers hardcoding the dark card stayed dark, and the theme
select's own options were unreadable (`menu-theme-tokens.test.ts` bans it). The
chat pane's `.ctx-menu`/`.meta-menu` is the reference spec; the timeline inlines
the RESOLVED twin as `MENU_STYLE`/`MENU_CHECK_STYLE` from its `PAL_DARK`/`PAL_LIGHT`
palettes in `ui/romp-timeline-view.js`, because a surface that cannot load
styles.css (the timeline also runs inside Obsidian) has no vars to read — and it
MUST declare `font-family` explicitly there: an adopted element inherits the host
app's font otherwise, which is exactly how the timeline gear menu drifted
off-brand (triggered 2026-08-09: bluish `#1c2430` card, host font, its own radii
and sub-sizes).

### The accent color is light blue `#9cd2ff` — use `var(--accent)`
The romp accent is light blue `#9cd2ff` (`--accent` in `ui/webview/styles.css`, with
`--accent-fg: #0c1a2e` for text on it). Use it for accent/highlight chrome — selected
toggles, in-progress loading dots, the Fleet pill, focus cues — anywhere you want "the
romp blue." Do NOT use it for STATUS colors, which keep their own meaning: working =
`--st-working-bg` (yellow), blocked/API-error = red, ready = `--st-ready-bg`, compacting =
teal. New accent chrome should reference `var(--accent)`, never re-hardcode the hex.
ONE exception, the user's choice of 2026-09-12 (T394): the background box's COMMAND rows wear
the accent blue as their KIND hue (`--kind-command`, the dot and the caption word), beside
the agents' working gold and the watches' awaiting green. That is a kind, not a status: the
status still overrides it where it means something (a failed row's dot is the blocked red,
a completed row's the dim ink). The light theme's accent is an orange, so `--kind-command`
carries its own blue there (`#356890`).
Section labels in the settings modal and its mirrors (`.rs-sec`, `.rs-divider`, `.rs-preview-title`, the network panel's `.rnet-khead` and the strip's `.sn-khead`) are sentence case in the accent colour at 11px/600, never uppercase and never letter-spaced: hierarchy by size and colour, not by shouting (the user 2026-09-18).

### Loading/waiting states: show the romp loader FIRST
Anytime something is loading, parsing, or otherwise making the user wait, the FIRST
thing to put up is the romp loader animation — the spinning swirl glyph
(`/media/romp-swirl-glyph.svg`, reverse spin) + the "romp" wordmark + three pulsing
accent-blue (`#9cd2ff`) dots — centered over the waiting surface, fading the instant
real content arrives (event-based; a backstop timeout so it can never trap the user).
It's the boot splash (`_landing` `#romp-boot`) and every pane's loader (`_pane_spin`).
Reuse that treatment for any new wait state rather than a blank, a bare spinner, or
text — a consistent "something's happening, it's romp" beats a frozen-looking screen.
A determinate progress bar is even better *when real progress is knowable*; default to
the loader animation otherwise.

### Buttons must stay click-safe across re-renders, and always acknowledge
The dashboard re-renders on every kernel push (a 0.5–3s backstop, plus an
immediate push per SDK stream event and per hook `/tick`). A control whose action
is hung on a DOM node that a re-render rebuilds gets destroyed mid-click — a
native `click` needs mousedown AND mouseup on the same element, so a rebuild
between them silently drops the click. That is the "had to click it several
times" bug. Every interactive control MUST therefore:

1. **Be click-safe across re-renders.** Never attach the action to a node you
   rebuild. Either:
   - **Delegate** to a STABLE ancestor — the container fetched by id survives
     `replaceChildren()`; only its children are swapped — and key the action off a
     `data-act` attribute. Use the shared helper `ui/webview/actions.ts`
     (`delegate(root, handlers)`), installed ONCE per root, never in a render
     loop. This is the default for HTML lists (chat tab bar `#tabs`, Fleet
     `#fleet-list`). A click whose original target was swapped mid-press still
     bubbles to the stable ancestor, so it always lands.
   - For full-canvas redraw surfaces (the SVG timeline) where threading every
     action param through data-attrs is impractical, **defer the rebuild while a
     pointer is pressed** over the surface and flush on `pointerup`/`pointercancel`
     (event-based, not a time heuristic), so the pressed element survives the
     click. See `ui/romp-timeline-view.js` `draw()`'s `_pointerHeld` guard.
2. **Always acknowledge the click immediately**, before any kernel round-trip —
   so the user never re-clicks because "nothing happened." `actions.ts`'s
   `flash()` adds a layout-safe `.romp-acted` press pulse on every delegated
   activation; a button that posts-and-waits (e.g. feed Nudge) must also disable +
   change its own label on click and self-restore. The error / dialog / result
   follows the acknowledgement; it does not replace it.

Reuse `ui/webview/actions.ts` for any new dashboard control. (`.romp-acted` is
defined in both `styles.css` and `feed.css` since the feed page loads only the
latter.)

### Designs must accommodate many tags and many sessions (user rule, 2026-09-09)
Assume dozens of tags and dozens of sessions on every surface that lists them, and lay
the surface out for that case, not for three. Per-item controls take ONE line: a tag row
is its pill, its actions and one colour dot; a session row is its name, its [+] and its
chips. Palettes, pane-by-tag matrices and any other per-item detail open on demand (a
popover, a fold), never inline for every item at once. The working area keeps the space:
the sections above it fold or cap their height and scroll within themselves, so the part
the user came to act on (the sessions table in Sessions & tags) is never pushed under
the fold. Triggered by that dialog with ten tags, where twelve inline swatches per row
and a five-pane matrix of every tag filled the page and left two session rows showing.
A change to a tag or session surface is checked against a fixture of thirty tags (the
dialog's is `ui/timeline-tags-scale.test.ts`, with the measured layout in
`ui/timeline-tags-scale-browser.test.ts`).

**Tags render as ONE chip everywhere** (the user 2026-09-10): `tagChip` in
`ui/webview/tag-menu.ts` builds every tag the UI shows (the strip's group rows and filter
chips, the feed's and outline's filter chips, the tag-lens menu, the tab menu's Tags flyout,
the picker's Tags row), a thin border and the text in the tag's colour, weight 400, the
context's size, faded when off; never bold, which is the session names' weight
(`ui/webview/tag-chip-everywhere.test.ts` pins the sites and the sheets; the two documents
that load no module, the landing page's spend panel and the Obsidian timeline view, inline
the same bytes under drift pins in `tests/test_spend_detail.py` and
`ui/timeline-tag-chips.test.ts`).
