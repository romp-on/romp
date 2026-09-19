// The Task tracking switch across kernels (T404 rounds six to nine). mergeHostFeeds builds the merged frame as the
// local host's with the lists overridden, so before round six a remote host's off frame (the switch's stand-in: no cards
// built) merged under a local frame with the switch on left no trace, and the pane's writers that act on a card's
// ABSENCE (the badge mirror's prune, the view state's prune, a predicted move's gone verdict, an optimistic tick's
// retirement, a pending clear's confirmation) read that host's cards as gone. The merge names the off hosts beside their
// counters; the pane reads a frame's cards as unknown while any host is named (frameCardsUnknown, the ONE gate in
// applyFeedPayload) and every absence-driven writer stands down behind it; the bell's card marks name their host from
// the mint (round seven), so the mirror keeps exactly the off hosts' marks while the on hosts' prune by absence as
// ever, which is what bounds the store however long a host stays off. The frame's own `off` stays the LOCAL kernel's
// word, so the notice and the gear row (both read this dashboard's kernel) agree. The remote fixture is minted as
// prefixInbound hands a card to the merge: the sid and the name carry the host, the itemId is bare. Same harness as
// federation-cleared-overlay.test.ts; the helpers are read through the module namespace so a tree without them fails
// as a test and not as a build.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mergeHostFeeds } from "./federation";
import * as badgeMirror from "./badge-mirror";

const SID_L = "11111111-2222-3333-4444-555555555555";   // the local host's session
const SID_R = "66666666-7777-3333-4444-888888888888";   // the remote host's session
const REMOTE = "HOSTA";

type Half = { notices: { kind: string; itemId?: string }[]; active: Set<string> };
type Unknown = boolean | Set<string>;
const frameCardsUnknown = (badgeMirror as any).frameCardsUnknown as ((m: any) => Unknown) | undefined;
const badgeCardHalf = (badgeMirror as any).badgeCardHalf as ((items: any[], seen: Set<string>, unknown: Unknown) => Half) | undefined;
const sigHost = (badgeMirror as any).sigHost as ((sig: string) => string) | undefined;
const hasSigHost = (badgeMirror as any).hasSigHost as ((sig: string) => boolean) | undefined;
const keepCardSigs = (badgeMirror as any).keepCardSigs as ((seen: Iterable<string>, hosts: true | Set<string>) => string[]) | undefined;
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

// one card per host carrying a warn chip, so the mirror has a notice to mint and a mark to keep; shaped as prefixInbound
// hands it to the merge (federation.ts _prefixIdBearing): the sid and the name prefixed, the itemId bare
function card(host: string, sid: string, goal = "g1") {
  const pre = host ? host + ":" : "";
  return { itemId: sid + ":" + goal, sid: pre + sid, name: pre + "api", text: "ship the notes-api",
           warns: [{ kind: "distill", t: 100, msg: "the summarizer gave up" }] };
}
function onFrame(host: string, sid: string, goals: string[] = ["g1"]) {
  return { type: "feed", now: 100, buildId: 3, asks: goals.map((g) => card(host, sid, g)), items: [], working: [], awaiting: [], clearNotices: [], sdkNotices: [], syncNotices: [] };
}
function offFrame() {
  return { type: "feed", now: 100, buildId: 3, off: true, asks: [], items: [], working: [], awaiting: [], clearNotices: [], sdkNotices: [], syncNotices: [],
           dismissedCount: 0, showDismissed: false, canUndoClear: false };
}
// the mark the mirror stores for a host's card when that card is on screen (badgeNotices' w| sig, host segment and all)
function markFor(host: string, sid: string, seen: Set<string>, goal = "g1"): string {
  const minted = badgeMirror.badgeNotices([card(host, sid, goal)] as any, new Set());
  const w = Array.from(minted.active).find((s) => s.startsWith("w|"));
  assert.ok(w, "the minter names the card's warn");
  seen.add(w!);
  return w!;
}
const asSet = (u: Unknown): Set<string> => { assert.ok(u instanceof Set, "a host set, not a boolean: " + String(u)); return u as Set<string>; };

test("the helpers exist: the pane's reading of a frame, the mirror's card half, the mark's host", () => {
  assert.equal(typeof frameCardsUnknown, "function");
  assert.equal(typeof badgeCardHalf, "function");
  assert.equal(typeof sigHost, "function");
  assert.equal(typeof hasSigHost, "function");
});

test("a remote card's mark names its host as the last segment, a local card's carries the empty segment, a mark with none is one stored before, and the old shape reads as seen and is rewritten without a ring (rounds seven and eight)", () => {
  const remote = markFor(REMOTE, SID_R, new Set());
  assert.ok(remote.endsWith("|@" + REMOTE), remote);
  assert.equal(sigHost!(remote), REMOTE);
  const local = markFor("", SID_L, new Set());
  assert.equal(sigHost!(local), "");
  assert.ok(local.endsWith("|@") && hasSigHost!(local), "the local host's key is the empty string, and the segment is there: " + local);
  assert.ok(!hasSigHost!("w|" + SID_R + ":g1|100|distill"), "no segment at all is the old shape");
  // a mark in the old shape is kept while any host's cards are unknown, whoever its host was (round eight, low 1): the one-time
  // upgrade window loses nothing; a mark with a segment is kept only for its own host
  const oldRemote = "w|" + SID_R + ":g1|100|distill", oldLocal = "n|" + SID_L + ":g3";
  assert.deepEqual(keepCardSigs!(new Set([oldRemote, oldLocal, local, remote]), new Set([REMOTE])).sort(), [oldLocal, oldRemote, remote].sort(), "the old shapes and the named host's; the local host's new-shape mark prunes");
  assert.deepEqual(keepCardSigs!(new Set([oldRemote, local, remote]), new Set([""])).sort(), [local, oldRemote].sort());
  // the old shape: the same sig without the host segment, as every store holds it before this round
  const bare = remote.slice(0, remote.length - ("|@" + REMOTE).length);
  const seen = new Set([bare]);
  const minted = badgeMirror.badgeNotices([card(REMOTE, SID_R)] as any, seen);
  assert.deepEqual(minted.notices, [], "seen under the old shape: nothing rings on the upgrade");
  assert.ok(minted.active.has(remote) && !minted.active.has(bare), "the new shape is written, the old one leaves by absence");
});

test("local on, remote off: the merged frame names the remote host off and keeps its own signal on; the mirror keeps the remote host's marks alone, prunes the local host's absent mark, and still mints the local card's notice", () => {
  const merged = mergeHostFeeds({ "": onFrame("", SID_L), [REMOTE]: offFrame() }, ["", REMOTE]);
  assert.equal(merged.off, undefined, "the local kernel's word: the switch is on here");
  assert.deepEqual(merged.offHosts, [REMOTE]);
  assert.deepEqual(merged.asks.map((a: any) => a.itemId), [SID_L + ":g1"], "the local host's cards alone");
  const unknown = asSet(frameCardsUnknown!(merged));
  assert.deepEqual(Array.from(unknown), [REMOTE], "that host's cards are unknown; the local host's are in hand");
  const seen = new Set<string>();
  const remoteMark = markFor(REMOTE, SID_R, seen);
  const remoteGone = markFor(REMOTE, SID_R, seen, "g7");                 // a remote card seen once, absent now: unknown, kept
  const localGone = markFor("", SID_L, seen, "g9");                      // a local card that LEFT: the local host is on, pruned
  const half = badgeCardHalf!(merged.asks, seen, unknown);
  assert.ok(half.active.has(remoteMark) && half.active.has(remoteGone), "the off host's marks are kept, every one");
  assert.ok(!half.active.has(localGone), "the on host's absent mark prunes by absence as ever: the store's bound");
  assert.equal(half.notices.length, 1, "the local card's warn still rings");
  assert.equal(half.notices[0].itemId, SID_L + ":g1");
  assert.ok(Array.from(half.active).some((s) => s.startsWith("w|" + SID_L + ":g1|")), "the local card's own mark is written too");
});

test("local off, remote on: the merged frame says off (the local kernel's word, as the gear row does) and names the local host; the pane's off write keeps every card mark, since nothing is rendered and nothing can mint", () => {
  const merged = mergeHostFeeds({ "": offFrame(), [REMOTE]: onFrame(REMOTE, SID_R) }, ["", REMOTE]);
  assert.equal(merged.off, true);
  assert.deepEqual(merged.offHosts, [""]);
  assert.deepEqual(merged.asks.map((a: any) => a.itemId), [SID_R + ":g1"], "the remote host's cards ride the frame");
  assert.equal(frameCardsUnknown!(merged), true, "off is the whole frame's word: every card unknown to the pane");
  const seen = new Set<string>();
  const localMark = markFor("", SID_L, seen);
  const remoteMark = markFor(REMOTE, SID_R, seen);
  const remoteGone = markFor(REMOTE, SID_R, seen, "g7");
  // the pane's off branch mirrors no items with the cards unknown (feed.ts): every stored card mark survives the write
  const half = badgeCardHalf!([], seen, true);
  assert.ok(half.active.has(localMark) && half.active.has(remoteMark) && half.active.has(remoteGone));
  assert.deepEqual(half.notices, [], "nothing minted from a frame whose cards were not built");
});

test("the order of the hosts changes nothing, and both off names both", () => {
  const a = mergeHostFeeds({ [REMOTE]: offFrame(), "": onFrame("", SID_L) }, [REMOTE, ""]);
  assert.equal(a.off, undefined); assert.deepEqual(a.offHosts, [REMOTE]); assert.deepEqual(Array.from(asSet(frameCardsUnknown!(a))), [REMOTE]);
  const b = mergeHostFeeds({ [REMOTE]: onFrame(REMOTE, SID_R), "": offFrame() }, [REMOTE, ""]);
  assert.equal(b.off, true); assert.deepEqual(b.offHosts, [""]); assert.equal(frameCardsUnknown!(b), true);
  const both = mergeHostFeeds({ [REMOTE]: offFrame(), "": offFrame() }, [REMOTE, ""]);
  assert.equal(both.off, true); assert.deepEqual(both.offHosts, [REMOTE, ""]); assert.equal(frameCardsUnknown!(both), true);
});

test("both on: no host is named, the cards are known, and a mark whose card left the frame prunes by absence as before, on either host", () => {
  const merged = mergeHostFeeds({ "": onFrame("", SID_L), [REMOTE]: onFrame(REMOTE, SID_R) }, ["", REMOTE]);
  assert.equal(merged.off, undefined);
  assert.deepEqual(merged.offHosts, []);
  assert.equal(frameCardsUnknown!(merged), false);
  const seen = new Set<string>();
  const remoteGone = markFor(REMOTE, SID_R, seen, "gone"), localGone = markFor("", SID_L, seen, "gone");
  const live = markFor("", SID_L, seen); markFor(REMOTE, SID_R, seen);
  const half = badgeCardHalf!(merged.asks, seen, frameCardsUnknown!(merged));
  assert.equal(half.notices.length, 0, "both cards' warns were already seen");
  assert.ok(half.active.has(live), "the live card's mark stands");
  assert.ok(!half.active.has(remoteGone) && !half.active.has(localGone), "the absent cards' marks are pruned");
});

test("a host attached but yet to send a frame is PENDING: its cards are not in hand either, so it counts as unknown and its marks are kept while the local host's absent ones prune (round eight, the medium: a reload's first merged frame names every remote host pending)", () => {
  const pending = mergeHostFeeds({ "": onFrame("", SID_L) }, ["", REMOTE]);
  assert.deepEqual(pending.offHosts, []); assert.deepEqual(pending.pendingHosts, [REMOTE]);
  const unknown = asSet(frameCardsUnknown!(pending));
  assert.deepEqual(Array.from(unknown), [REMOTE], "the pending host is unknown, the local host in hand");
  const seen = new Set<string>(); const remoteMark = markFor(REMOTE, SID_R, seen); const localGone = markFor("", SID_L, seen, "gone");
  const half = badgeCardHalf!(pending.asks, seen, unknown);
  assert.ok(half.active.has(remoteMark), "a pending host's marks are kept: not in hand is not gone");
  assert.ok(!half.active.has(localGone), "the local host's absent mark prunes: its cards are in hand");
  // both reasons at once: one host off, another pending, a third on
  const mixed = mergeHostFeeds({ "": onFrame("", SID_L), [REMOTE]: offFrame() }, ["", REMOTE, "HOSTB"]);
  assert.deepEqual(Array.from(asSet(frameCardsUnknown!(mixed))).sort(), [REMOTE, "HOSTB"].sort());
});

test("the host list not read yet is an UNKNOWN state (round nine): a merged frame built before the first /tunnels answer says hostsUnread, the pane reads every card as unknown, and the manager re-emits when the list lands", () => {
  // a page load's first merged frame: the local kernel's push landed on its open socket before the first /tunnels answer,
  // so hostSeq is the local host alone, offHosts and pendingHosts are both empty, and before this round the frame read as
  // "every card in hand": every remote host's marks and disclosure state pruned, and every remote warn re-rung a moment later
  const early = mergeHostFeeds({ "": onFrame("", SID_L) }, [""], [], [], {}, false);
  assert.deepEqual(early.offHosts, []); assert.deepEqual(early.pendingHosts, []);
  assert.equal(early.hostsUnread, true, "the frame says its host list is not in hand");
  assert.equal(frameCardsUnknown!(early), true, "every card unknown until the list is read");
  const seen = new Set<string>(); const remoteMark = markFor(REMOTE, SID_R, seen); const localGone = markFor("", SID_L, seen, "gone");
  const half = badgeCardHalf!(early.asks, seen, frameCardsUnknown!(early));
  assert.ok(half.active.has(remoteMark) && half.active.has(localGone), "nothing prunes on the unread frame, the local host's absent mark included");
  assert.equal(half.notices.length, 1, "the local card's warn still rings");
  // the list in hand (the default): no mark, and the same host list reads as every card in hand
  const read = mergeHostFeeds({ "": onFrame("", SID_L) }, [""]);
  assert.ok(!("hostsUnread" in read), "read is the default and leaves no key behind");
  assert.equal(frameCardsUnknown!(read), false);
  // the manager: false until the first poll absorbs an answer, handed to the merge, and the feed re-emitted once on the first read
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /private hostsRead = false;/);
  assert.match(FED, /const firstRead = !this\.hostsRead;\s*\n\s*this\.hostsRead = true;/, "set once the /tunnels answer is absorbed, after a failed fetch has already returned");
  assert.match(FED, /if \(opened \|\| firstRead\) \{\s*\n\s*if \(LOCAL in this\.perHostFeed\) this\.emitMergedFeed\(\);/, "the first read re-emits the merged feed");
  assert.match(FED, /this\.emit\(mergeHostFeeds\(this\.perHostFeed, this\.hostSeq, this\.view\(\), dead, this\.perHostFeedAt, this\.hostsRead\)\);/);
  const poll = FED.slice(FED.indexOf("private async poll(): Promise<void> {"), FED.indexOf("private openRemote("));
  assert.ok(poll.indexOf("} catch (e) {\n      return;") < poll.indexOf("this.hostsRead = true;"), "a failed fetch returns before the list counts as read");
});

test("the ONE gate in applyFeedPayload: the frame's reading is taken first, and every absence-driven writer stands behind it (source pins on feed.ts)", () => {
  const start = FEED.indexOf("function applyFeedPayload(m: any): void {");
  assert.ok(start > 0);
  const body = FEED.slice(start, FEED.indexOf("\n}\n", start));
  const gateAt = body.indexOf("const cardsUnknown = frameCardsUnknown(m);");
  assert.ok(gateAt > 0, "the gate is read from the frame inside applyFeedPayload");
  // each writer that acts on a card's absence, after the gate and under it
  const writers: [RegExp, string][] = [
    [/if \(!cardsUnknown\) for \(const id of Array\.from\(pendingCleared\)\) if \(!incomingAsks\.some\(\(a\) => a\.itemId === id\)\) pendingCleared\.delete\(id\);/, "a pending clear's confirmation by absence"],
    [/if \(!cardsUnknown\) pruneViewStateTo\(new Set\(incomingAsks\.map\(\(a\) => a\.itemId\)\)\);/, "the view state's prune"],
    [/reconcileFollowMove\(incomingAsks, lastPayloadBuildId, perHostBuildIds, !!cardsUnknown\);/, "the predicted move's gone verdict"],
    [/reconcilePendingDone\(incomingAsks, !!cardsUnknown\);/, "the optimistic tick's retirement"],
    [/mirrorBadges\(incomingAsks,[^;]*\{ cardsUnknown \}\);/, "the badge mirror's card half"],
  ];
  for (const [re, what] of writers) {
    const m = re.exec(body);
    assert.ok(m, what + " is under the gate");
    assert.ok(m!.index > gateAt, what + " comes after the gate is read");
  }
  // no ungated prune or confirmation-by-absence remains in the function
  assert.equal((body.match(/pruneViewStateTo\(/g) || []).length, 1, "one prune call, the gated one");
  assert.equal((body.match(/pendingCleared\.delete\(/g) || []).length, 1, "one confirmation by absence, the gated one");
  // the two reconcilers honour the flag they are handed
  const fm = FEED.slice(FEED.indexOf("function reconcileFollowMove("), FEED.indexOf("\n}\n", FEED.indexOf("function reconcileFollowMove(")));
  assert.match(fm, /if \(!a && cardsUnknown\) continue;\s*\n\s*if \(!a \|\| askColumn\(a\) === "asks" \|\| pendingMoveKind\.get\(id\) === "answer"\) \{/, "absent: gone only when the frame can vouch for every host's cards; the three verdicts' one condition stands");
  assert.match(fm, /clearFollowMove\(id, !a \? "gone" : askColumn\(a\) === "asks" \? "confirmed" : "answer-yield"\);/, "presence-driven verdicts go on (Working read through askColumn since the boards' phase two)");
  const pd = FEED.slice(FEED.indexOf("function reconcilePendingDone("), FEED.indexOf("\n}\n", FEED.indexOf("function reconcilePendingDone(")));
  assert.match(pd, /if \(st === "done" \|\| \(st === undefined && !cardsUnknown\)\) pendingDone\.delete\(id\);/);
  // the mirror's card half takes the frame's reading through, a set or a boolean
  assert.match(FEED, /const badges = badgeCardHalf\(items, seenSet, opts\?\.cardsUnknown \?\? false\);/, "one card half for both branches");
  assert.match(FEED, /import \{[^}]*\bbadgeCardHalf\b[^}]*\} from "\.\/badge-mirror"/);
  assert.match(FEED, /import \{[^}]*\bframeCardsUnknown\b[^}]*\} from "\.\/badge-mirror"/);
  assert.match(FEED, /import \{[^}]*\btype CardsUnknown\b[^}]*\} from "\.\/badge-mirror"/);
});
