// Replies ready (the user 2026-09-08): a reply lands on a comment you scrolled away from and you forget to
// come back. The unread landed replies whose marks sit wholly above / below the viewport are counted per
// direction with the nearest of each; a mark on screen counts in neither. Two chips showed those counts until
// 2026-09-24, when they folded into the jump cluster's badge (jump-nav.ts, jump-cluster.test.ts). This file
// drives the pure half (reply-ready.ts) behaviorally. Synthetic text only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { isReplyReady, placeMark, placeWindowed, readyChips, firstLine, replyLine, replyWord,
         WINDOWED_OUT, type ReadyMark } from "./reply-ready";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const mark = (tid: string, dir: "above" | "below" | "in", dist: number, line = "q " + tid): ReadyMark =>
  ({ tid, uuid: "u-" + tid, line, dir, dist });

// ── the predicate ─────────────────────────────────────────────────────────────────────────────────
test("a reply is ready when the kernel's unread bit sits on an OPEN thread — the mark's own .unread rule", () => {
  assert.equal(isReplyReady({ unread: true, status: "open" }), true);
  assert.equal(isReplyReady({ unread: false, status: "open" }), false, "read");
  assert.equal(isReplyReady({ unread: true, status: "resolved" }), false, "a resolved thread's stale bit waits on nobody");
  assert.equal(isReplyReady({ unread: true, status: "merged" }), false);
  assert.equal(isReplyReady({ unread: true, status: "promoted" }), false);
  // the same expression the mark and the rail tick read, so the chips can never count a mark that shows no ring
  assert.match(RENDER, /m\.classList\.toggle\("unread", !!th\.unread && th\.status === "open"\);/);
  assert.match(RENDER, /\(th\.unread && th\.status === "open" \? " unread" : ""\)/);
});

// ── placement against the viewport ────────────────────────────────────────────────────────────────
test("a mark wholly above the top edge is above, wholly below the bottom edge is below, anything on screen is in", () => {
  const H = 600;
  assert.deepEqual(placeMark(-80, -60, H), { dir: "above", dist: 60 });
  assert.deepEqual(placeMark(-20, 0, H), { dir: "above", dist: 0 }, "its bottom exactly at the edge: not one pixel visible");
  assert.deepEqual(placeMark(650, 670, H), { dir: "below", dist: 50 });
  assert.deepEqual(placeMark(600, 620, H), { dir: "below", dist: 0 }, "its top exactly at the bottom edge");
  assert.deepEqual(placeMark(100, 120, H), { dir: "in", dist: 0 });
  assert.deepEqual(placeMark(-10, 10, H), { dir: "in", dist: 0 }, "partly visible at the top counts as seen");
  assert.deepEqual(placeMark(590, 610, H), { dir: "in", dist: 0 }, "partly visible at the bottom counts as seen");
  assert.deepEqual(placeMark(-100, 700, H), { dir: "in", dist: 0 }, "taller than the viewport, spanning it");
});

test("a windowed-out anchor takes its direction from event order and a distance tier past every pixel", () => {
  // the rendered window spans units [10, 30)
  assert.deepEqual(placeWindowed(4, 10, 30), { dir: "above", dist: WINDOWED_OUT + 6 });
  assert.deepEqual(placeWindowed(9, 10, 30), { dir: "above", dist: WINDOWED_OUT + 1 }, "just before the window");
  assert.deepEqual(placeWindowed(30, 10, 30), { dir: "below", dist: WINDOWED_OUT + 1 }, "the first unit after the window");
  assert.deepEqual(placeWindowed(45, 10, 30), { dir: "below", dist: WINDOWED_OUT + 16 });
  assert.deepEqual(placeWindowed(-1, 10, 30), { dir: "above", dist: WINDOWED_OUT * 2 }, "older than the resident head: above, farthest");
  assert.deepEqual(placeWindowed(15, 10, 30), { dir: "in", dist: 0 }, "inside the window with no turn of its own (a folded run): on screen in folded form");
  assert.ok(WINDOWED_OUT > 1e6, "no screen is a million pixels tall — every rendered mark is nearer than a windowed-out one");
});

// ── the chips: presence, counts, nearest ─────────────────────────────────────────────────────────
test("above only / below only / both / none — a chip exists only while its count is > 0", () => {
  assert.deepEqual(readyChips([]), { above: null, below: null });
  const a = readyChips([mark("a", "above", 100), mark("b", "above", 40)]);
  assert.equal(a.above?.count, 2); assert.equal(a.below, null);
  const b = readyChips([mark("c", "below", 300)]);
  assert.equal(b.above, null); assert.equal(b.below?.count, 1);
  const both = readyChips([mark("a", "above", 100), mark("c", "below", 300), mark("d", "below", 20)]);
  assert.equal(both.above?.count, 1); assert.equal(both.below?.count, 2);
});

test("a mark inside the viewport counts in NEITHER direction", () => {
  const r = readyChips([mark("a", "above", 100), mark("v", "in", 0), mark("w", "in", 0), mark("c", "below", 50)]);
  assert.equal(r.above?.count, 1);
  assert.equal(r.below?.count, 1);
  assert.deepEqual(readyChips([mark("v", "in", 0)]), { above: null, below: null }, "the one unread reply is on screen: no chips at all");
});

test("the nearest per direction is the least distance; a rendered mark beats a windowed-out one; ties keep the earlier", () => {
  const r = readyChips([mark("far", "above", 900), mark("near", "above", 12), mark("out", "above", WINDOWED_OUT + 1),
                        mark("b1", "below", 30), mark("b2", "below", 30)]);
  assert.equal(r.above?.nearest.tid, "near");
  assert.equal(r.above?.count, 3, "windowed-out marks still count");
  assert.equal(r.below?.nearest.tid, "b1", "the tie keeps the earlier (transcript order)");
  const onlyOut = readyChips([mark("o2", "above", WINDOWED_OUT + 9), mark("o1", "above", WINDOWED_OUT + 2)]);
  assert.equal(onlyOut.above?.nearest.tid, "o1", "windowed-out marks order among themselves by units from the window edge");
});

// ── copy ──────────────────────────────────────────────────────────────────────────────────────────
test("the phrase is the user's words, singular handled — 'reply', never 'comment' (the jump cluster's badge tip reads it)", () => {
  assert.equal(replyWord(1), "1 reply unread");
  assert.equal(replyWord(2), "2 replies unread");
  assert.doesNotMatch(replyWord(1) + replyWord(2), /comment|thread|card|board/, "no romp nouns, and the unread thing is the ANSWER to their comment");
});

test("firstLine takes the first non-empty line and clips long ones with an ellipsis", () => {
  assert.equal(firstLine("\n\n  keep the flag  \nsecond line"), "keep the flag");
  assert.equal(firstLine(""), "");
  const long = "a".repeat(80);
  const clipped = firstLine(long);
  assert.equal(clipped.length, 60);
  assert.ok(clipped.endsWith("…"));
  assert.equal(firstLine("exactly sixty characters long text ........................"), "exactly sixty characters long text ........................", "at the cap: untouched");
  assert.equal(firstLine("word word word word", 11), "word word…", "clips at the cap and trims the trailing space before the ellipsis");
});

test("replyLine names the thread by your LAST message in it (what the landed reply answers), else its name, else the passage", () => {
  const msgs = [{ who: "you" as const, text: "first ask\nmore", t: 1 }, { who: "agent" as const, text: "answer", t: 2 },
                { who: "you" as const, text: "follow-up ask", t: 3 }, { who: "agent" as const, text: "landed", t: 4 }];
  assert.equal(replyLine({ msgs, name: "web-comment-1", exact: "the passage" }), "follow-up ask");
  assert.equal(replyLine({ msgs: [], name: "web-comment-1", exact: "the passage" }), "web-comment-1");
  assert.equal(replyLine({ msgs: [], name: "", exact: "the passage" }), "the passage");
});

// The render.ts wiring moved with the chips into the jump cluster (2026-09-24): jump-cluster.test.ts pins the badge's
// measure (this module's placement and counts), its landing and the unread semantics.
