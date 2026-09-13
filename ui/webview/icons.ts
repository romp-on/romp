// The stroke-family glyphs the bars share: a 24-unit viewBox, currentColor strokes, round caps (the composer
// buttons' family). ONE drawing per meaning, so a download reads the same in the lightbox's tray and in the file
// viewer's title bar (T367, the user 2026-09-12: the viewer's word buttons became icons, and the tray's glyph is
// reused, not redrawn). Inline SVG literals — no sanitize; aria-hidden, the button's title and aria-label carry
// the words (progressive disclosure: the meaning one hover away).
const svg = (paths: string): string =>
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"'
  + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths + '</svg>';
/** the tray: an arrow down onto a bar (the image lightbox's download control since 2026-08-19) */
export const ICON_DOWNLOAD = svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
  + '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>');
/** two offset sheets (the lightbox's copy-image control since 2026-08-31) */
export const ICON_COPY = svg('<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>'
  + '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>');
/** a pencil over a baseline */
export const ICON_EDIT = svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>');
/** a magnifier with a plus: the text-size control's one glyph */
export const ICON_ZOOM = svg('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'
  + '<line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>');
/** a fork: one line in from the left that branches into two, up-right and down-right, running on to the right
 *  edge; no arrowheads (the user 2026-09-12, describing the chat's fork control) */
export const ICON_FORK = svg('<polyline points="2 12 9 12 15 7 22 7"/><polyline points="9 12 15 17 22 17"/>');
/** the acknowledgements a glyph button swaps to: done, and failed */
export const ICON_CHECK = svg('<polyline points="20 6 9 17 4 12"/>');
export const ICON_CROSS = svg('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>');

/** THE PADLOCK (T395, the user 2026-09-12): the same drawing the Sessions pane's lock-to-now toggle draws at its bottom
 *  (ui/romp-timeline-view.js _drawLockToggle: a 15-unit box, the body a rounded rect, the shackle seated when locked and
 *  swung out when not), so a lock reads the same on the timeline and in the chat strip. That file is served raw and
 *  cannot import this module, so the numbers are stated there and here, pinned equal by tab-lock.test.ts. */
const LOCK_BODY = '<rect x="3" y="6.2" width="8" height="5.6" rx="1.2"/>';
export const LOCK_SHACKLE_SEATED = 'M4.8 6.2 V4.4 a2.2 2.2 0 0 1 4.4 0 V6.2';
export const LOCK_SHACKLE_OPEN = 'M9.4 6.2 V5.3 A2.4 2.4 0 0 1 13.6 3.7';
const lockSvg = (shackle: string): string =>
  '<svg viewBox="0 0 15 15" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"'   // drawn at the tag glyph's 14px, so the two boxes beside each other measure the same (T395 round two)
  + ' stroke-linecap="round" aria-hidden="true">' + LOCK_BODY + '<path d="' + shackle + '"/></svg>';
export const ICON_LOCK = lockSvg(LOCK_SHACKLE_SEATED);
export const ICON_LOCK_OPEN = lockSvg(LOCK_SHACKLE_OPEN);
