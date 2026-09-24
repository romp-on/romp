// The jump cluster's target selection (jump-nav.ts), driven by execution: previous / next of the user's own messages from
// any position (scrolled up, just landed, inside a long reply, at the bottom, after a landing the scroll could not
// top-align), previous / next comment (two threads on one passage included), the unread badge's next thread in each
// direction (and a whole walk that visits every unread thread without bouncing), barriers of unloaded history, and
// nothing to jump to. Synthetic numbers and ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { placeRendered, placeSpan, placeByOrder, placeUnplaced, pickNav, unreadNext, unreadCount, badgeTip, badgeAria, MOVE_TITLE, MOVES,
         HERE_PX, HEAD_DIST, type NavStop } from "./jump-nav";
import { placeMark, readyChips, WINDOWED_OUT, type ReadyMark } from "./reply-ready";

const VIEW = 600;
// a rendered stop by its top (px from the scroller's top edge), against the line the reader's position sets
const at = (uuid: string, top: number, atBottom = false): NavStop => ({ uuid, place: atBottom ? placeRendered(top, VIEW, 0) : placeRendered(top, 0) });
const pick = (stops: NavStop[], dir: "above" | "below") => pickNav(stops, dir)?.uuid ?? null;

// ── previous / next of the user's own messages ────────────────────────────────────────────────────
test("scrolled up: previous is the nearest message above the viewport top, next the nearest below it", () => {
  const s = [at("m1", -900), at("m2", -300), at("m3", 250), at("m4", 700)];
  assert.equal(pick(s, "above"), "m2");
  assert.equal(pick(s, "below"), "m3", "a message further down the screen is ahead of the reader");
});

test("just landed: the message on the line is HERE, neither previous nor next, so a second press moves on", () => {
  for (const top of [0, 2, -3, HERE_PX, -HERE_PX]) {
    const s = [at("m1", -700), at("m2", top), at("m3", 450)];
    assert.equal(pick(s, "above"), "m1", "top " + top);
    assert.equal(pick(s, "below"), "m3", "top " + top);
  }
  assert.deepEqual(placeRendered(HERE_PX + 1, 0), { dir: "below", dist: HERE_PX + 1 }, "past the band, it is ahead again");
});

test("inside a long reply: a stop whose top scrolled past the line is behind the reader", () => {
  const s = [at("m1", -2400), at("c1", -800), at("m2", 900)];   // c1: the top of the long reply the reader is inside
  assert.equal(pick(s, "above"), "c1");
  assert.equal(pick(s, "below"), "m2");
});

test("at the bottom: one previous lands on the last message even when it is in view; nothing is next", () => {
  const s = [at("m1", -1500, true), at("m2", -200, true), at("m3", 380, true)];
  assert.equal(pick(s, "above"), "m3", "the stated case: from the bottom, one previous is the user's last message");
  assert.equal(pick(s, "below"), null, "at the bottom nothing is below");
});

test("after a landing the scroll could not top-align, the remembered stop orders the next press", () => {
  // the view clamped at the bottom with m3 in view below the line: by pixels m3 would be previous again; by order the
  // reader is ON m3 (unit 8), so previous is m2 and next is nothing
  const cur = { unit: 8, rank: 0 };
  const s: NavStop[] = [{ uuid: "m1", place: placeByOrder(2, 0, cur) }, { uuid: "m2", place: placeByOrder(5, 0, cur) },
                        { uuid: "m3", place: placeByOrder(8, 0, cur) }];
  assert.equal(pick(s, "above"), "m2");
  assert.equal(pick(s, "below"), null);
  assert.deepEqual(placeByOrder(8, 0, cur), { dir: "here", dist: 0 });
});

test("windowed out: before the rendered window is above, after it below, always farther than anything rendered", () => {
  const win = { start: 40, end: 120 };
  const s: NavStop[] = [
    { uuid: "old", place: placeUnplaced(10, win.start, win.end) },       // in the top spacer
    { uuid: "older", place: placeUnplaced(3, win.start, win.end) },
    at("near", -400),
    { uuid: "later", place: placeUnplaced(130, win.start, win.end) },   // past the rendered window's end
  ];
  assert.equal(pick(s, "above"), "near", "a rendered stop is always nearer than a windowed-out one");
  assert.equal(pick(s.filter((x) => x.uuid !== "near"), "above"), "old", "windowed-out stops order by units from the window edge");
  assert.equal(pick(s, "below"), "later");
  assert.ok(placeUnplaced(10, 40, 120).dist >= WINDOWED_OUT);
  assert.deepEqual(placeUnplaced(60, 40, 120), { dir: "here", dist: 0 }, "inside the window with no turn of its own: a folded run on screen");
});

test("nothing to jump to: no stops, or only the one the reader is on", () => {
  assert.equal(pickNav([], "above"), null);
  assert.equal(pickNav([], "below"), null);
  const only = [at("m1", 1)];
  assert.equal(pick(only, "above"), null);
  assert.equal(pick(only, "below"), null);
});

// ── barriers: unloaded history between the reader and the next stop ─────────────────────────────────
test("older history on the server: previous with no loaded message above asks for it; one loaded above wins", () => {
  const head: NavStop<string> = { uuid: "", place: { dir: "above", dist: HEAD_DIST }, load: "head" };
  assert.equal(pickNav([head, at("m9", 300) as NavStop<string>], "above")?.load, "head", "nothing loaded above: the move loads older history");
  assert.equal(pickNav([head, at("m8", -300) as NavStop<string>], "above")?.uuid, "m8", "a loaded message above is nearer than older history");
  assert.equal(pickNav([head], "below"), null, "older history is never below");
});

test("a gap of unloaded history nearer than the next loaded message stands in front of it", () => {
  const gap: NavStop<string> = { uuid: "", place: placeSpan(-3000, -500, 0), load: "gap" };
  const beyond = at("m1", -3400) as NavStop<string>;
  assert.equal(pickNav([gap, beyond], "above")?.load, "gap", "the gap may hold nearer messages: load it first");
  assert.equal(pickNav([gap, beyond, at("m2", -200) as NavStop<string>], "above")?.uuid, "m2", "a message between the reader and the gap is nearer");
  assert.deepEqual(placeSpan(-100, 300, 0), { dir: "here", dist: 0 }, "a gap spanning the line is being filled already");
  assert.deepEqual(placeSpan(900, 2000, 0), { dir: "below", dist: 900 }, "a gap below: a next move meets its top");
});

// ── comments ──────────────────────────────────────────────────────────────────────────────────────
test("previous / next comment, and two threads on one passage both reachable", () => {
  const s = [at("c1", -1200), at("c2", -40), at("c3", 520)];
  assert.equal(pick(s, "above"), "c2");
  assert.equal(pick(s, "below"), "c3");
  // two threads anchored to one reply (unit 12, ranks 0 and 1): landed on the first, next is its sibling
  const cur = { unit: 12, rank: 0 };
  const t: NavStop[] = [{ uuid: "a", place: placeByOrder(12, 0, cur) }, { uuid: "b", place: placeByOrder(12, 1, cur) },
                        { uuid: "c", place: placeByOrder(20, 0, cur) }, { uuid: "z", place: placeByOrder(-1, 0, cur) }];
  assert.equal(pick(t, "below"), "b");
  assert.equal(pick(t, "above"), "z", "a thread anchored in history the page has not loaded is above everything");
});

// ── the unread badge ──────────────────────────────────────────────────────────────────────────────
const chip = (count: number, tid: string) => ({ count, nearest: { tid, uuid: "u-" + tid, line: "q " + tid, dir: "above" as const, dist: 1 } });

test("the badge goes below first, then above; a last press's direction continues while unread waits that way", () => {
  const both = { above: chip(2, "a1"), below: chip(1, "b1") };
  assert.equal(unreadNext(both, null)?.dir, "below", "no last press: read on from here");
  assert.equal(unreadNext(both, "above")?.dir, "above", "keeps walking up");
  assert.equal(unreadNext(both, "below")?.dir, "below");
  assert.equal(unreadNext({ above: chip(1, "a1"), below: null }, "below")?.dir, "above", "nothing left below: turn round");
  assert.equal(unreadNext({ above: null, below: chip(3, "b2") }, "above")?.chip.nearest.tid, "b2");
  assert.equal(unreadNext({ above: null, below: null }, null), null, "none off screen: no target (a thread in view shows its own ring)");
  assert.equal(unreadCount(both), 3);
  assert.equal(unreadCount({ above: null, below: null }), 0);
});

test("a walk from the bottom visits every unread thread before any repeats (no bouncing between two)", () => {
  // a long document with five unread threads; the viewport is VIEW tall; a press lands the target at the viewport top
  const ys: Record<string, number> = { t1: 400, t2: 2600, t3: 5200, t4: 7900, t5: 9100 };
  const DOC = 12000;
  let scroll = DOC - VIEW;                                   // at the bottom
  let last: "above" | "below" | null = null;
  const seen: string[] = [];
  for (let press = 0; press < 5; press++) {
    const marks: ReadyMark[] = Object.entries(ys).map(([tid, y]) => ({ tid, uuid: "u-" + tid, line: "", ...placeMark(y - scroll, y - scroll + 20, VIEW) }));
    const next = unreadNext(readyChips(marks), last);
    assert.ok(next, "press " + press + " has a target");
    seen.push(next!.chip.nearest.tid);
    last = next!.dir;
    scroll = Math.min(DOC - VIEW, ys[next!.chip.nearest.tid]);   // landed: the thread's mark at the top
  }
  assert.deepEqual(seen.slice().sort(), ["t1", "t2", "t3", "t4", "t5"], "every unread thread, once each: " + seen.join(","));
});

// ── copy ──────────────────────────────────────────────────────────────────────────────────────────
test("the controls speak the user's words", () => {
  assert.deepEqual(MOVES, ["prevMine", "nextMine", "prevComment", "nextComment", "nextUnread"]);
  assert.equal(MOVE_TITLE.prevMine, "Your previous message");
  assert.equal(MOVE_TITLE.nextComment, "Next comment");
  assert.equal(badgeTip(2, "above", "why the cap?"), "2 replies unread · next one above: why the cap?");
  assert.equal(badgeTip(1, "below", ""), "1 reply unread · next one below");
  assert.equal(badgeAria(3, "below"), "3 replies unread, go to the next one below");
});
