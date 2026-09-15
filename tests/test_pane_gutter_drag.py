#!/usr/bin/env python3
"""A pane divider drag moves a landing line; the panes take their widths once, at release.

The dashboard's chat | Outline | feed panes are sized by flex-grow weights the gutter script (_LANDING_JS
in kernel/kernel.py) writes as --g-* variables on .row. Each write re-lays out the row, and with it every
same-origin pane document in that frame. Writing the pair's two grows on every mousemove made a drag cost
one full relayout of every pane per pointer step, which is a freeze once any pane holds a large document.
So a drag now moves only #gv-ghost, a fixed line over the row where the divider will land, and the release
writes the pair's grows once and persists them.

This EXECUTES the real _LANDING_JS in node against a DOM stub (the test_error_center.py pattern; a source
pin cannot show that a handler writes nothing) and drives two drags on the chat | feed gutter, a grab
whose right pane is gone, a drag on the Outline | feed gutter with the Outline pane shown, and three grabs
on the Files gutter (gv-c), whose left pane is the rightmost shown of feed, the Outline and chat:

  the grab normalises the shown panes' grows to their widths and shows the ghost at the divider, spanning
  the row; the move repositions the ghost and writes no grow; the release writes exactly the pair's two
  grows, hides the ghost, drops the drag classes, persists the grows and removes its window listeners; a
  far drag clamps both the ghost and the release at the pair's minimum, min(120 px, a quarter of the pair);
  a grab whose pane is missing arms nothing and leaves no drag cursor behind; a left pane that is not the
  leftmost places the ghost from its own client left, and a narrow pair clamps at a quarter of itself; the
  Files gutter pairs the Files pane with the feed, else the Outline, else the chat, by what is shown.

The chat side of every outer gutter is #chat-area (the chat rows, 2026-09-15), the wrapper the chat columns
live in, which wears the --g-chat weight #chat-pane wore; the first pane inside it has an inner weight of
its own (--g-chat1) that no outer grab touches. And the gutter between the two chat rows (#gv-rows) rides
the same drag code vertically: the pointer's Y against the rows' heights, #gv-ghost-h a horizontal line
across the chat area, no grow written at any point, and the release reporting the top row's share (0–1) to
the hook the split script hands __rompRowGutter — its own store keeps the share.

Synthetic only: no browser, no real DOM. The stub is told the layout the browser would produce from the
written grows (layout()), since a stub has no layout engine of its own.
"""
import json
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_gutter_drag", os.path.join(BIN, "romp-kernel"))

# Everything _LANDING_JS touches, and nothing else: .col (the timeline band's --tl var, never driven here),
# .row (records every --g-* write, in order), the gutters and panes by id with offsetWidth and a client rect
# and the parentElement the served markup gives them (the chat area and the other panes in .row, the first
# chat pane in #chat-row-1, the two chat rows in #chat-area) since a grab normalises the grabbed pane's
# siblings, #gv-ghost and #gv-ghost-h with style records, getComputedStyle(el).display for shown(),
# body.classList (po-* reads, drag/dragv/dragh writes), localStorage, and the window's mousemove/mouseup
# listeners a drag installs and removes.
# f-timeline is present (the served shell always carries the iframe) so the 'load' hookup runs; the event
# is never fired. The ghost's style record starts at display 'none', standing for the stylesheet's rule.
# The row's rect (top 24, height 760) differs from the panes' (top 0, height 800) on purpose: the ghost spans
# the row, and the test can tell which rect it read.
HARNESS = r"""
'use strict';
const STORE = {};
global.localStorage = {
  getItem: (k) => (k in STORE ? STORE[k] : null),
  setItem: (k, v) => { STORE[k] = String(v); },
  removeItem: (k) => { delete STORE[k]; },
};
const WRITES = [];   // every --g-* write on .row, in order: [name, value]
const ROW = {};      // the current --g-* values (the script's `grow` object is a closure)
const rowEl = {
  style: { setProperty: (k, v) => { ROW[k] = v; WRITES.push([k, v]); }, removeProperty: (k) => { delete ROW[k]; } },
  getBoundingClientRect: () => ({ left: 0, top: 24, width: 1007, height: 760 }),
};
const colEl = { style: { setProperty() {} }, getBoundingClientRect: () => ({ bottom: 800 }) };
// the containers inside the chat area (the served markup): the chat area's own rect spans the outer row's height
const areaEl = { id: 'chat-area-box', getBoundingClientRect: () => ({ left: 0, top: 24, width: 600, height: 760 }) };
const row1El = { id: 'chat-row-1-box', getBoundingClientRect: () => ({ left: 0, top: 24, width: 600, height: 400 }) };
function mkEl(id, w, left, display, parent, h, top) {
  return {
    id: id, offsetWidth: w, offsetHeight: h || 800, _left: left, _top: top || 0, _display: display, _ls: {}, parentElement: parent || rowEl, isConnected: true,
    style: {},   // a plain record: the script writes top / height / left / display on the ghost
    getBoundingClientRect() { return { left: this._left, top: this._top, width: this.offsetWidth, height: this.offsetHeight }; },
    addEventListener(k, f) { (this._ls[k] = this._ls[k] || []).push(f); },
    fire(k, ev) { (this._ls[k] || []).slice().forEach((f) => f(ev)); },
  };
}
const EL = {};
['gh', 'gv-a', 'gv-b', 'gv-c', 'gv-rows', 'f-timeline'].forEach((id) => { EL[id] = mkEl(id, 7, 0, 'flex'); });
// the chat area 600 px at the left edge (the first chat pane inside it, the same 600), the Outline pane hidden (so
// gv-b is the chat | feed gutter), feed 400 px after the 7 px gutter: the divider sits at x = 600; the Files pane
// hidden until the last drags
EL['chat-area'] = mkEl('chat-area', 600, 0, 'flex');
EL['chat-pane'] = mkEl('chat-pane', 600, 0, 'flex', row1El);
EL['fleet-pane'] = mkEl('fleet-pane', 300, 0, 'none');
EL['feed-pane'] = mkEl('feed-pane', 400, 607, 'flex');
EL['files-pane'] = mkEl('files-pane', 300, 0, 'none');
EL['gv-ghost'] = mkEl('gv-ghost', 7, 0, 'none');
EL['gv-ghost'].style.display = 'none';
EL['gv-ghost-h'] = mkEl('gv-ghost-h', 0, 0, 'none');
EL['gv-ghost-h'].style.display = 'none';
// the two chat rows inside the chat area: 400 px and 336 px tall behind the 7 px row gutter, the divider at y = 424
EL['chat-row-1'] = mkEl('chat-row-1', 600, 0, 'flex', areaEl, 400, 24);
EL['chat-row-2'] = mkEl('chat-row-2', 600, 0, 'flex', areaEl, 336, 431);
const APPLIED = [];   // the shares the row gutter reports at release
const WL = {};
global.window = {
  innerHeight: 900,
  addEventListener: (k, f) => { (WL[k] = WL[k] || []).push(f); },
  removeEventListener: (k, f) => { WL[k] = (WL[k] || []).filter((g) => g !== f); },
};
global.getComputedStyle = (el) => ({ display: el._display });
const BODY = new Set(['po-chat', 'po-feed', 'po-timeline']);
global.document = {
  querySelector: (sel) => (sel === '.col' ? colEl : sel === '.row' ? rowEl : null),
  getElementById: (id) => EL[id] || null,
  visibilityState: 'visible', addEventListener() {},   // a gesture's cancel source (visibilitychange), never fired here
  body: { classList: {
    contains: (c) => BODY.has(c),
    add: (...cs) => cs.forEach((c) => BODY.add(c)),
    remove: (...cs) => cs.forEach((c) => BODY.delete(c)),
  } },
};
function winFire(k, ev) { (WL[k] || []).slice().forEach((f) => f(ev)); }
function snap() {
  return {
    writes: WRITES.length,
    grows: Object.assign({}, ROW),
    ghost: Object.assign({}, EL['gv-ghost'].style),
    ghostH: Object.assign({}, EL['gv-ghost-h'].style),
    drag: BODY.has('drag'), dragv: BODY.has('dragv'), dragh: BODY.has('dragh'),
    listeners: { move: (WL['mousemove'] || []).length, up: (WL['mouseup'] || []).length },
    store: JSON.parse(STORE['romp-pane-grow'] || 'null'),
    applied: APPLIED.slice(),
  };
}
// the browser lays the panes out from the written grows; the stub has no layout engine, so it is told the
// shown panes' widths, left to right, and places each after the one before it plus the 7 px gutter (the
// first chat pane follows the chat area's width: alone in its row, it fills it)
function layout(widths) {
  let x = 0;
  [['chat', 'chat-area'], ['fleet', 'fleet-pane'], ['feed', 'feed-pane'], ['files', 'files-pane']].forEach(([k, id]) => {
    if (!(k in widths)) return;
    EL[id].offsetWidth = widths[k]; EL[id]._left = x; x += widths[k] + 7;
  });
  if ('chat' in widths) EL['chat-pane'].offsetWidth = widths.chat;
}
"""

DRIVER = r"""
const out = {};
out.boot = snap();
// 1) grab gv-b at the divider, move the pointer 100 px left, release
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 600 });
out.grab = snap();
winFire('mousemove', { clientX: 500 });
out.move = snap();
winFire('mouseup', {});
out.release = snap();
out.releaseWrites = WRITES.slice(out.move.writes);
layout({ chat: 500, feed: 500 });
// 2) a second grab at the new divider, dragged far past the right edge: the clamp holds the pair's minimum
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 500 });
out.grab2 = snap();
winFire('mousemove', { clientX: 1590 });
out.move2 = snap();
winFire('mouseup', {});
out.release2 = snap();
out.release2Writes = WRITES.slice(out.move2.writes);
layout({ chat: 880, feed: 120 });
// 3) a grab whose right pane is gone from the document
const feed = EL['feed-pane'];
delete EL['feed-pane'];
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 880 });
out.orphan = snap();
EL['feed-pane'] = feed;
// 4) the Outline pane shown, so gv-b is the Outline | feed gutter and its left pane sits at a nonzero client
//    left (307: chat 300 plus the gutter), and a narrow pair (200 | 200) whose minimum is a quarter of it, 100
BODY.add('po-fleet');
EL['fleet-pane']._display = 'flex';
layout({ chat: 300, fleet: 200, feed: 200 });   // the Outline | feed divider sits at x = 507
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 507 });
out.grab3 = snap();
winFire('mousemove', { clientX: 457 });
out.move3 = snap();
winFire('mousemove', { clientX: 350 });
out.move3far = snap();
winFire('mouseup', {});
out.release3 = snap();
out.release3Writes = WRITES.slice(out.move3far.writes);
// 5) the Files pane shown: gv-c's left pane is the rightmost SHOWN of feed, the Outline and chat. All four
//    shown first (chat 300 | Outline 100 | feed 300 | files 200, the divider at 714), then the feed hidden
//    (chat 300 | Outline 250 | files 250, the divider at 557), then the Outline hidden too (chat 500 | files 300,
//    the divider at 500); each drag moves the pointer 50 px left of the divider
BODY.add('po-files');
EL['files-pane']._display = 'flex';
layout({ chat: 300, fleet: 100, feed: 300, files: 200 });
EL['gv-c'].fire('mousedown', { preventDefault() {}, clientX: 714 });
out.grab5 = snap();
winFire('mousemove', { clientX: 664 });
winFire('mouseup', {});
out.release5 = snap();
out.release5Writes = WRITES.slice(out.grab5.writes);
BODY.delete('po-feed');
EL['feed-pane']._display = 'none';
layout({ chat: 300, fleet: 250, files: 250 });
EL['gv-c'].fire('mousedown', { preventDefault() {}, clientX: 557 });
out.grab6 = snap();
winFire('mousemove', { clientX: 507 });
winFire('mouseup', {});
out.release6Writes = WRITES.slice(out.grab6.writes);
BODY.delete('po-fleet');
EL['fleet-pane']._display = 'none';
layout({ chat: 500, files: 300 });
EL['gv-c'].fire('mousedown', { preventDefault() {}, clientX: 500 });
out.grab7 = snap();
winFire('mousemove', { clientX: 450 });
winFire('mouseup', {});
out.release7Writes = WRITES.slice(out.grab7.writes);
out.release7 = snap();
// 6) the ROW gutter (the chat rows, 2026-09-15), wired the way the split script wires it: grab at the divider (y 424),
//    move 50 px down, release; then a far drag past the bottom, clamped at the pair's minimum (min(120, 736 / 4) = 120)
window.__rompRowGutter('gv-rows', 'chat-row-1', 'chat-row-2', function (s) { APPLIED.push(s); });
EL['gv-rows'].fire('mousedown', { preventDefault() {}, clientY: 424 });
out.grabRows = snap();
winFire('mousemove', { clientY: 474 });
out.moveRows = snap();
winFire('mouseup', {});
out.releaseRows = snap();
EL['gv-rows'].fire('mousedown', { preventDefault() {}, clientY: 424 });
winFire('mousemove', { clientY: 1400 });
out.moveRowsFar = snap();
winFire('mouseup', {});
out.releaseRowsFar = snap();
console.log(JSON.stringify(out));
"""


class PaneGutterDragExecutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = HARNESS + km._LANDING_JS + DRIVER
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the gutter script threw: " + r.stderr[:800]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_boot_writes_the_five_default_grows_and_shows_no_ghost(self):
        b = self.out["boot"]
        self.assertEqual(b["writes"], 5, "the chat area's weight, the first pane's inner one, the outline, the feed, the files")
        self.assertEqual(b["grows"], {"--g-chat": 60, "--g-chat1": 60, "--g-fleet": 34, "--g-feed": 40, "--g-files": 40})
        self.assertEqual(b["ghost"]["display"], "none"); self.assertEqual(b["ghostH"]["display"], "none")
        self.assertFalse(b["drag"] or b["dragv"])
        self.assertEqual(b["listeners"], {"move": 0, "up": 0}, "no drag listeners before a grab")

    def test_the_grab_normalises_the_shown_panes_and_shows_the_ghost_at_the_divider(self):
        g = self.out["grab"]
        # the outer row's two shown panes — the chat AREA and the feed — are written as their px widths (the hidden
        # Outline pane keeps its grow; the first chat pane's inner weight is another container's and is not touched)
        self.assertEqual(g["writes"], self.out["boot"]["writes"] + 2)
        self.assertEqual(g["grows"], {"--g-chat": 600, "--g-chat1": 60, "--g-fleet": 34, "--g-feed": 400, "--g-files": 40})
        # the ghost shows at the divider, spanning the row (its rect, not the pane's)
        self.assertEqual(g["ghost"], {"display": "block", "left": "600px", "top": "24px", "height": "760px"})
        self.assertTrue(g["drag"] and g["dragv"], "body.drag and body.dragv during the drag")
        self.assertEqual(g["listeners"], {"move": 1, "up": 1})

    def test_the_move_positions_the_ghost_and_writes_no_grow(self):
        g, m = self.out["grab"], self.out["move"]
        self.assertEqual(m["writes"], g["writes"], "no grow is written while the pointer moves")
        self.assertEqual(m["grows"], g["grows"], "the panes hold their widths during the drag")
        self.assertEqual(m["ghost"]["display"], "block")
        self.assertEqual(m["ghost"].get("left"), "500px", "the ghost follows the pointer")
        self.assertTrue(m["drag"] and m["dragv"])

    def test_the_release_writes_the_pair_once_hides_the_ghost_and_persists(self):
        m, r = self.out["move"], self.out["release"]
        self.assertEqual(self.out["releaseWrites"], [["--g-chat", 500], ["--g-feed", 500]],
                         "exactly the pair's two grows, written at release")
        self.assertEqual(r["writes"], m["writes"] + 2)
        self.assertEqual(r["grows"], {"--g-chat": 500, "--g-chat1": 60, "--g-fleet": 34, "--g-feed": 500, "--g-files": 40})
        self.assertEqual(r["ghost"]["display"], "none", "the ghost hides at release")
        self.assertFalse(r["drag"] or r["dragv"], "body.drag and body.dragv are removed")
        self.assertEqual(r["store"], {"chat1": 60, "chat": 500, "fleet": 34, "feed": 500, "files": 40}, "the grows persist at release")
        self.assertEqual(r["listeners"], {"move": 0, "up": 0}, "the drag's window listeners are removed")

    def test_a_far_drag_clamps_the_ghost_and_the_release_at_the_pair_minimum(self):
        # chat 500 | feed 500: the pair's minimum is min(120, 1000 * 0.25) = 120, so the pointer at 1590
        # (asking for chat 1590) lands the divider at 880
        g2, m2, r2 = self.out["grab2"], self.out["move2"], self.out["release2"]
        self.assertEqual(g2["grows"], {"--g-chat": 500, "--g-chat1": 60, "--g-fleet": 34, "--g-feed": 500, "--g-files": 40})
        self.assertEqual(g2["ghost"].get("left"), "500px", "the ghost shows at the new divider")
        self.assertEqual(m2["writes"], g2["writes"], "still no grow written while the pointer moves")
        self.assertEqual(m2["ghost"].get("left"), "880px", "the ghost clamps at the pair's minimum")
        self.assertEqual(self.out["release2Writes"], [["--g-chat", 880], ["--g-feed", 120]])
        self.assertEqual(r2["grows"], {"--g-chat": 880, "--g-chat1": 60, "--g-fleet": 34, "--g-feed": 120, "--g-files": 40})
        self.assertEqual(r2["store"], {"chat1": 60, "chat": 880, "fleet": 34, "feed": 120, "files": 40})
        self.assertEqual(r2["ghost"]["display"], "none")

    def test_a_grab_whose_pane_is_gone_arms_nothing(self):
        # the pair is resolved before anything else happens: no drag classes to leave the col-resize cursor
        # stuck, no normalisation writes, no window listeners, no ghost
        r2, o = self.out["release2"], self.out["orphan"]
        self.assertEqual(o["writes"], r2["writes"], "a grab with a missing pane writes nothing")
        self.assertFalse(o["drag"] or o["dragv"], "no drag class is left on the body")
        self.assertEqual(o["listeners"], {"move": 0, "up": 0})
        self.assertEqual(o["ghost"]["display"], "none")
        self.assertEqual(o["grows"], r2["grows"])

    def test_with_the_outline_pane_shown_the_line_is_placed_from_its_client_left(self):
        # gv-b's left pane is now the Outline pane, at client left 307 (chat 300 and a 7 px gutter): the line's
        # left is that pane's client left plus the dragged width, not the width alone
        o, g3, m3 = self.out["orphan"], self.out["grab3"], self.out["move3"]
        self.assertEqual(g3["writes"], o["writes"] + 3, "all three shown panes are normalised")
        self.assertEqual(g3["grows"], {"--g-chat": 300, "--g-chat1": 60, "--g-fleet": 200, "--g-feed": 200, "--g-files": 40})
        self.assertEqual(g3["ghost"], {"display": "block", "left": "507px", "top": "24px", "height": "760px"},
                         "the line shows at the Outline | feed divider")
        self.assertEqual(m3["writes"], g3["writes"], "no grow written while the pointer moves")
        self.assertEqual(m3["ghost"]["left"], "457px", "307 + 150: the client left plus the dragged width")

    def test_a_narrow_pair_clamps_at_a_quarter_of_the_pair(self):
        # Outline 200 | feed 200: min(120, 400 * 0.25) = 100, so the pointer at 350 (asking for Outline 43)
        # lands the divider at 307 + 100, and the release writes that pair alone
        m3, r3 = self.out["move3far"], self.out["release3"]
        self.assertEqual(m3["ghost"]["left"], "407px", "the line clamps at a quarter of the pair")
        self.assertEqual(self.out["release3Writes"], [["--g-fleet", 100], ["--g-feed", 300]])
        self.assertEqual(r3["grows"], {"--g-chat": 300, "--g-chat1": 60, "--g-fleet": 100, "--g-feed": 300, "--g-files": 40}, "the chat grow is untouched")
        self.assertEqual(r3["store"], {"chat1": 60, "chat": 300, "fleet": 100, "feed": 300, "files": 40})
        self.assertEqual(r3["ghost"]["display"], "none")
        self.assertFalse(r3["drag"] or r3["dragv"])
        self.assertEqual(r3["listeners"], {"move": 0, "up": 0})

    def test_the_files_gutter_pairs_the_files_pane_with_the_rightmost_shown_column_to_its_left(self):
        # every shown pane is normalised at the grab (four, then three, then two), the ghost shows at the divider
        # placed from the left pane's own client left, and the release writes the PAIR the gutter resolved by
        # what is shown at grab time: feed | files, then Outline | files, then chat | files
        r3, g5, g6, g7, r7 = self.out["release3"], self.out["grab5"], self.out["grab6"], self.out["grab7"], self.out["release7"]
        self.assertEqual(g5["writes"], r3["writes"] + 4, "all four shown panes are normalised")
        self.assertEqual(g5["grows"], {"--g-chat": 300, "--g-chat1": 60, "--g-fleet": 100, "--g-feed": 300, "--g-files": 200})
        self.assertEqual(g5["ghost"], {"display": "block", "left": "714px", "top": "24px", "height": "760px"}, "the feed | files divider")
        self.assertEqual(self.out["release5Writes"], [["--g-feed", 250], ["--g-files", 250]], "the feed is the left pane while it is shown")
        self.assertEqual(g6["grows"], {"--g-chat": 300, "--g-chat1": 60, "--g-fleet": 250, "--g-feed": 250, "--g-files": 250}, "the hidden feed keeps its grow")
        self.assertEqual(g6["ghost"]["left"], "557px", "the Outline | files divider: the Outline's client left 307 plus its 250")
        self.assertEqual(self.out["release6Writes"], [["--g-fleet", 200], ["--g-files", 300]], "feed hidden: the Outline is the left pane")
        self.assertEqual(g7["ghost"]["left"], "500px")
        self.assertEqual(self.out["release7Writes"], [["--g-chat", 450], ["--g-files", 350]], "feed and Outline hidden: the chat is")
        self.assertEqual(r7["store"], {"chat1": 60, "chat": 450, "fleet": 200, "feed": 250, "files": 350}, "the grows persist at release")
        self.assertEqual(r7["ghost"]["display"], "none")
        self.assertFalse(r7["drag"] or r7["dragv"])
        self.assertEqual(r7["listeners"], {"move": 0, "up": 0})

    def test_the_row_gutter_moves_a_horizontal_line_writes_no_grow_and_reports_the_top_row_s_share_at_release(self):
        # the chat rows (2026-09-15): the grab writes nothing (a row pair is a share, not px), wears dragh (not dragv) and
        # shows #gv-ghost-h across the chat area at the divider; the move repositions it; the release reports 450 of 736
        # to the hook, hides the line, drops the classes, writes no grow and removes its listeners
        r7, g, m, r = self.out["release7"], self.out["grabRows"], self.out["moveRows"], self.out["releaseRows"]
        self.assertEqual(g["writes"], r7["writes"], "no normalisation at the grab: no grow is written")
        self.assertTrue(g["drag"] and g["dragh"]); self.assertFalse(g["dragv"])
        self.assertEqual(g["ghostH"], {"display": "block", "left": "0px", "width": "600px", "top": "424px"}, "the line spans the chat area, at the rows' divider")
        self.assertEqual(g["ghost"]["display"], "none", "the vertical line stays down")
        self.assertEqual(m["writes"], g["writes"]); self.assertEqual(m["ghostH"]["top"], "474px", "the line follows the pointer"); self.assertEqual(m["applied"], [])
        self.assertEqual(r["writes"], g["writes"], "the release writes no grow either")
        self.assertEqual(r["grows"], r7["grows"]); self.assertEqual(r["store"], r7["store"], "the pane store is untouched")
        self.assertEqual(r["applied"], [450 / 736], "the top row's share of the pair, reported once")
        self.assertEqual(r["ghostH"]["display"], "none"); self.assertFalse(r["drag"] or r["dragh"])
        self.assertEqual(r["listeners"], {"move": 0, "up": 0})
        f, rf = self.out["moveRowsFar"], self.out["releaseRowsFar"]
        self.assertEqual(f["ghostH"]["top"], "640px", "clamped: 24 + (736 - 120)")
        self.assertEqual(rf["applied"], [450 / 736, 616 / 736]); self.assertEqual(rf["writes"], g["writes"])


if __name__ == "__main__":
    unittest.main()
