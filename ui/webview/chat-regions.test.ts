// The history regions' pure rules (T386 stage 2), each executed: runs and gaps from spans, a window inserted among them, a gap's
// estimated height, the page a gap asks for at either edge, the anchor's place inside a gap, the one notice's words, the cancel.
import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { DEFAULT_TURN_PX, OVERLAY_KINDS, gapAt, gapFraction, gapHeight, insertRun, landingCancel, landingNotice, pagesToAsk, regionsFromRuns, redoSpans, runHoldingKey, runsOf, splitHeldAgainstFrame, turnsBeforeTail, type Ev, type Region, type Run } from "./chat-regions";

const ev = (k: string) => ({ uuid: k });
const run = (lo: number, hi: number | null, keys: string[]): Run => ({ kind: "run", lo, hi, events: keys.map(ev) });
const shape = (rs: readonly Region[]) => rs.map((r) => r.kind === "gap" ? "gap[" + r.lo + "," + r.hi + ")" : "run[" + r.lo + "," + (r.hi ?? "tail") + "):" + r.events.map((e) => e.uuid).join(","));

test("the regions a tail run implies: a head gap before it, the tail open-ended; a tail from the head has no gap at all", () => {
  assert.deepEqual(shape(regionsFromRuns([run(200, null, ["t1", "t2"])])), ["gap[0,200)", "run[200,tail):t1,t2"]);
  assert.deepEqual(shape(regionsFromRuns([run(0, null, ["a"])])), ["run[0,tail):a"]);
});

test("a window inside the head gap splits it in two; a second window between them shrinks both; the order is by turn whatever the arrival order", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(96, 128, ["w1", "w2"]));
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,128):w1,w2", "gap[128,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(160, 176, ["x1"]));
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,128):w1,w2", "gap[128,160)", "run[160,176):x1", "gap[176,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(32, 48, ["v1"]));   // an earlier arrival lands earlier in the list
  assert.deepEqual(shape(rs).slice(0, 3), ["gap[0,32)", "run[32,48):v1", "gap[48,96)"]);
});

test("a window touching a held run merges into it (events in turn order), and one touching the tail run joins the tail", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(96, 128, ["w1"]));
  rs = insertRun(rs, run(128, 144, ["y1"]));   // touches the window's end: one run
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,144):w1,y1", "gap[144,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(80, 96, ["u1"]));     // touches the window's start: still one run, in order
  assert.deepEqual(shape(rs), ["gap[0,80)", "run[80,144):u1,w1,y1", "gap[144,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(176, 200, ["z1"]));   // touches the tail: the tail grows downward and stays open-ended
  assert.deepEqual(shape(rs), ["gap[0,80)", "run[80,144):u1,w1,y1", "gap[144,176)", "run[176,tail):z1,t1"]);
  assert.equal(turnsBeforeTail(rs), 176);
});

test("an overlapping window merges by the T323 order rule: the held part before, the window, the held part after, every key once", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(96, 128, ["a", "b", "c"]));
  rs = insertRun(rs, run(112, 144, ["b", "c", "d"]));   // overlaps b and c
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,144):a,b,c,d", "gap[144,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(80, 112, ["z", "a"]));         // overlaps a from above
  assert.deepEqual(shape(rs), ["gap[0,80)", "run[80,144):z,a,b,c,d", "gap[144,200)", "run[200,tail):t1"]);
  assert.equal(runsOf(rs).length, 2);
});

test("a window bridging two held runs joins all three into one", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(32, 48, ["a"]));
  rs = insertRun(rs, run(64, 80, ["c"]));
  rs = insertRun(rs, run(48, 64, ["b"]));
  assert.deepEqual(shape(rs), ["gap[0,32)", "run[32,80):a,b,c", "gap[80,200)", "run[200,tail):t1"]);
});

test("a gap's height is its turns times the measured row, the default row until measured, never under a row", () => {
  assert.equal(gapHeight({ lo: 0, hi: 96 }, null), 96 * DEFAULT_TURN_PX);
  assert.equal(gapHeight({ lo: 128, hi: 200 }, 42.5), Math.round(72 * 42.5));
  assert.equal(gapHeight({ lo: 10, hi: 10 }, 42.5), 43, "an empty span still shows a row's worth of space");
});

test("the page a gap asks for: scrolling up meets the bottom edge (the last aligned page, clipped to the gap); scrolling down the top edge", () => {
  assert.deepEqual(pagesToAsk({ lo: 0, hi: 96 }, "bottom", 16), { lo: 80, hi: 96 });
  assert.deepEqual(pagesToAsk({ lo: 0, hi: 96 }, "top", 16), { lo: 0, hi: 16 });
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 200 }, "bottom", 16), { lo: 192, hi: 200 }, "the last page is clipped to the gap's end");
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 200 }, "top", 16), { lo: 130, hi: 144 }, "the first page runs from the gap's start to the next boundary");
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 140 }, "top", 16), { lo: 130, hi: 140 }, "a gap inside one page asks for exactly itself");
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 140 }, "bottom", 16), { lo: 130, hi: 140 });
});

test("an anchor's place inside a gap is its time's proportion between the neighbours; one neighbour or none is the honest edge", () => {
  assert.equal(gapFraction(150, 100, 200), 0.5);
  assert.equal(gapFraction(50, 100, 200), 0, "before the run above's last: clamped to the gap's start");
  assert.equal(gapFraction(250, 100, 200), 1, "after the run below's first: clamped to the gap's end");
  assert.equal(gapFraction(150, 100, null), 1, "only the run above: the gap's end");
  assert.equal(gapFraction(150, null, 200), 0, "only the run below: the gap's start");
  assert.equal(gapFraction(150, null, null), 0);
});

test("the one notice's words, with the reader's clock and without a time", () => {
  assert.equal(landingNotice(1789000000, (t) => "7:41 AM (" + t + ")"), "Going to the message from 7:41 AM (1789000000), click to stay here");
  assert.equal(landingNotice(null, () => "never"), "Going to the earlier message, click to stay here");
});

test("the cancel drops the target and the notice and leaves the ask in flight: its reply still inserts the run", () => {
  assert.deepEqual(landingCancel({ target: "u-1", notice: true, askInFlight: true }), { target: null, notice: false, askInFlight: true });
  assert.deepEqual(landingCancel({ target: "u-1", notice: true, askInFlight: false }), { target: null, notice: false, askInFlight: false });
});

test("the gap a turn falls in", () => {
  const rs = insertRun(regionsFromRuns([run(200, null, ["t1"])]), run(96, 128, ["w1"]));
  assert.deepEqual(gapAt(rs, 10), { kind: "gap", lo: 0, hi: 96 });
  assert.deepEqual(gapAt(rs, 150), { kind: "gap", lo: 128, hi: 200 });
  assert.equal(gapAt(rs, 100), null, "a turn in a run is in no gap");
  assert.equal(gapAt(rs, 250), null, "the tail is in no gap");
});

// ── a full frame against a held run (the client merge guard, 2026-09-19) ─────────────────────────────────────────────────────
const H = (ks: string[]) => ks.map((k) => ({ uuid: k, kind: "user" })) as Ev[];
const keys = (es: readonly Ev[]) => es.map((e) => e.uuid);
const transcript = (e: Ev) => !OVERLAY_KINDS.has(String(e.kind)) && !String(e.uuid).startsWith("optimistic:") && !String(e.uuid).startsWith("held:");

test("the mirrored overlay set is the kernel's own (_OVERLAY_KINDS), member for member", () => {
  const kernel = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const m = /_OVERLAY_KINDS = frozenset\(\(([^)]*)\)\)/.exec(kernel);
  assert.ok(m, "the kernel names its overlay kinds in one frozenset");
  const kinds = Array.from(m![1].matchAll(/"([^"]+)"/g), (x) => x[1]).sort();
  assert.deepEqual(Array.from(OVERLAY_KINDS).sort(), kinds, "the client skips exactly the cards the kernel's anchor rule skips");
});

test("a held run against a frame's tail: the prefix before the first shared key stays above, what the frame lacks from that key on goes, and no shared key keeps the whole run", () => {
  let r = splitHeldAgainstFrame(H(["h1", "h2", "t1", "t2", "x", "t3"]), H(["t1", "t2", "t3", "t4"]), transcript);
  assert.deepEqual(keys(r.before), ["h1", "h2"], "history the frame did not carry");
  assert.deepEqual(keys(r.dropped), ["x"], "a resident row inside the frame's span the frame lacks: retracted");
  assert.equal(r.behind, false, "the frame carries t4 past the last shared key: it is newer than the page, not behind");
  r = splitHeldAgainstFrame(H(["t1", "t2"]), H(["live1", "live2"]), transcript);
  assert.deepEqual(keys(r.before), ["t1", "t2"], "no shared key: the whole held run is history above the frame (the floor-cut shape)");
  assert.deepEqual([keys(r.dropped), r.behind], [[], false]);
});

test("behind: the frame's last transcript key is resident and a transcript row the page holds sits after it", () => {
  let r = splitHeldAgainstFrame(H(["t1", "t2", "t3"]), H(["t1", "t2"]), transcript);
  assert.deepEqual([keys(r.before), keys(r.dropped), r.behind, r.afterLast], [[], ["t3"], true, 1], "a same-list stale full: the newest row is missing from it");
  r = splitHeldAgainstFrame(H(["t1", "t2", "t3"]), [...H(["t1", "t2"]), { uuid: "todo:9", kind: "todo" }], transcript);
  assert.equal(r.behind, true, "a frame ending in an overlay card whose key is not resident reads its last TRANSCRIPT key (the kernel's anchor rule), so the behind frame is still seen");
  r = splitHeldAgainstFrame([...H(["t1", "t2"]), { uuid: "apiError:1", kind: "apiError" }], H(["t1", "t2"]), transcript);
  assert.deepEqual([keys(r.dropped), r.behind], [["apiError:1"], false], "a dropped overlay card is no evidence: those come and go between builds");
  r = splitHeldAgainstFrame([...H(["t1", "t2"]), { uuid: "optimistic:5", kind: "queued" }], H(["t1", "t2"]), transcript);
  assert.deepEqual([keys(r.dropped), r.behind], [["optimistic:5"], false], "the client's own bubble is no evidence either");
});

test("the echo landing: the held echo after the last shared key is dropped, not filed above, and the frame carrying the record and the reply is not behind", () => {
  const r = splitHeldAgainstFrame(H(["t1", "t2", "t3", "echo:x"]), H(["t3", "rec", "reply"]), transcript);
  assert.deepEqual(keys(r.before), ["t1", "t2"]);
  assert.deepEqual(keys(r.dropped), ["echo:x"], "the echo the record replaced leaves with the frame");
  assert.equal(r.behind, false);
});

test("the history run holding a key, for a changedBelow tail: a held history run is found, the tail run and a gap never (2026-09-30)", () => {
  // the kernel names a change ABOVE the client's tail run by its key instead of sending a full frame; the page re-asks the
  // history run that holds it, and only that: the tail run is the delta's own business, a key in a gap is nothing it holds
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1", "t2"])]);
  rs = insertRun(rs, run(96, 128, ["w1", "w2"]));
  rs = insertRun(rs, run(32, 48, ["v1"]));
  assert.equal(runHoldingKey(rs, "w2")?.lo, 96, "the run above holding the key");
  assert.equal(runHoldingKey(rs, "v1")?.hi, 48);
  assert.equal(runHoldingKey(rs, "t1"), null, "the tail run holds it: not stale history");
  assert.equal(runHoldingKey(rs, "g9"), null, "a key in a gap: nothing held");
  assert.equal(runHoldingKey(rs, undefined), null);
  assert.equal(runHoldingKey(null, "w1"), null);
  // a key spelled as uuid#n (a record's second event) matches by the wire key, as every anchor does
  const keyed: Region[] = insertRun(regionsFromRuns([run(200, null, ["t1"])]), { kind: "run", lo: 0, hi: 16, events: [{ uuid: "r1", key: "r1#1" }] });
  assert.equal(runHoldingKey(keyed, "r1#1")?.lo, 0);
  assert.equal(runHoldingKey(keyed, "r1"), null, "the record's uuid is not the second event's key");
});

test("the deferred re-asks: a span named while its ask was on the wire is asked once more when free, per session, never while in flight (2026-09-30)", () => {
  const deferred = [{ sid: "s1", lo: 96, hi: 128 }, { sid: "s1", lo: 32, hi: 48 }, { sid: "s2", lo: 96, hi: 128 }];
  const busy = new Set(["96:128"]);
  assert.deepEqual(redoSpans(deferred, "s1", (lo, hi) => busy.has(lo + ":" + hi)), [{ sid: "s1", lo: 32, hi: 48 }], "the span still in flight waits; another session's entry is not this one's");
  busy.clear();
  assert.deepEqual(redoSpans(deferred, "s1", () => false).map((d) => d.lo), [96, 32], "every freed span, once");
  assert.deepEqual(redoSpans([], "s1", () => false), []);
});
