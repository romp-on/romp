# The loading placeholder tab keeps its final width (2026-09-19)

**The ask (the user, 2026-09-19).** A tab that is still loading (the placeholder the strip shows from the kernel's
tabOrder push until the session's first frame) is narrower than the tab it becomes, so the strip jumps as tabs settle.
The user wants the tab at the width it will have when rendered, from the first paint, and the spinning romp swirl shown
somewhere that costs no width: the status dot's slot, since the status is unknown until loaded.

**What differs today.** A placeholder is a 12 px swirl image and the label. A loaded tab is the before-the-name widgets
(the status dot's 7 px slot, present in every state when the widget is on), the label, the after-the-name widgets (the
context gauge once the session is half full, the hot-key keycap when one is assigned) and the close glyph (transparent
until hover, but a flex item with its glyph's width). So a tab grows by the close glyph less the swirl-to-dot difference
the moment it loads, and with a hot key by the keycap too.

**The design: the same structure, so the width is fixed by construction, never by measuring.** The placeholder is built
with the loaded tab's elements in the loaded tab's order:

- the before-the-name widgets composed for an unknown status (the dot widget renders its unknown slot, the same 7 px box
  a loaded tab reserves); the slot takes the class `loading` and the romp swirl rides INSIDE it, absolutely positioned
  and centred on the slot at its 12 px, so it paints over the slot and costs no width; the slot's grey ring hides under
  it. The swirl glyph is one asset in both themes (the same file the placeholder shows today), so nothing else changes
  per theme. With the dot widget switched off there is no slot on either tab: the placeholder shows no swirl, and its
  muted label alone says loading (the widths still match).
- the label, as today (the name and identity colour from the tabOrder push); a push that carries no name shows the
  ellipsis and settles when the name arrives, as before.
- the after-the-name widgets composed for the same unknown status: the context gauge draws nothing (it needs a fill),
  the hot-key keycap draws when the key is assigned, so a keycapped tab is as wide loading as loaded.
- an end spacer wearing the close glyph's metrics (the same glyph at the same size and padding) with its visibility
  hidden: it reserves the close glyph's width and is never a control (a loading tab still has no session to end and no
  drag; selection stays its only power).

What the placeholder cannot know stays out: the context gauge appears when the loaded session is half full, a later
state of its own, not the load.

**Pins.** A served lab over a hermetic kernel with several sessions records, at the moment each placeholder is inserted,
its width and its dot slot's rectangle, and after every tab has loaded the same tab's width and dot rectangle: equal
within a pixel for every tab; the swirl present and visible while loading, absent after; the dot slot's later state
painting where the swirl was. The placeholder's own pins (name and identity colour, clickable, no close control, no
drag, the swirl by the media route) stand, the swirl's append re-pointed into the slot.
