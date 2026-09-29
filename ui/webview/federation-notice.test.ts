// NOTICE CARDS over federation (T370, plans/notice-cards.md): a notice ask from another host rides the asks array the
// merge already carries; its sid, name AND item id take the host prefix (the id since round three of PR 1831: the reserved
// owner-less key sits in the sid slot on every host, so two hosts' cards under one key minted one id), the route strips
// the id on the way out, and a viewer's foreign clear never names the family (the kernel's _cleared_foreign skips it), so a
// dismissal is a routed gesture into the owning kernel's ledger.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mergeHostFeeds, prefixInbound, routeOutbound } from "./federation";

const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

const SID = "11111111-2222-3333-4444-555555555555";
const notice = (sid: string, rev: number) => ({
  itemId: `notice:${sid}:figure:${rev}`, sid, name: "web", color: { bg: "#1EA1EB", fg: "#ffffff" }, text: "A new figure", t: 1757000000,
  live: false, trgb: [1, 2, 3], turnId: `notice:${sid}:figure:${rev}`, column: "completed", tree: [],
  notice: { producer: "figure", key: "figure", rev, body: "the body", attachment: null, actions: [{ label: "Send again", route: "/send", body: { text: "x" } }], expiresAt: null, dismissOnAction: false },
});

test("a remote host's notice card merges with its prefixed sid and name kept and its item id untouched; a foreign clear naming a goal leaves it", () => {
  // inbound frames are host-prefixed field by field at receive time (sid, name); the merge concatenates them
  const A = "HOSTA:" + SID;
  const remote = { type: "feed", asks: [{ ...notice(SID, 2), sid: A, name: "HOSTA:web" }], sessions: [{ sid: A, name: "HOSTA:web" }], working: [], awaiting: [], ledgers: [] };
  const local = { type: "feed", asks: [], sessions: [], working: [], awaiting: [], ledgers: [], clearedForeign: [SID + ":g1"] };
  const merged: any = mergeHostFeeds({ "": local, HOSTA: remote }, ["", "HOSTA"]);
  const a = merged.asks.find((x: any) => x.notice);
  assert.ok(a, "the notice ask rides the merged asks");
  assert.equal(a.sid, A, "the sid wears the host prefix (gestures route by it)");
  assert.equal(a.name, "HOSTA:web", "the name too (the chip shows the host)");
  assert.equal(a.itemId, `notice:${SID}:figure:2`, "the MERGE touches no id: this fixture arrives already prefixed at the sid; the item id's host prefix is prefixInbound's (the test below), applied at the socket before the merge");
  assert.deepEqual(a.notice, remote.asks[0].notice, "the flavour object passes through untouched");
  assert.equal(merged.asks.length, 1, "a foreign clear of a goal id touches no notice");
});

test("a session frame's approval-box rows wear the host like the feed's notice cards, so a remote kernel's answer finds its row (the review of PR 1890, medium 2)", () => {
  const frame = { type: "session", id: SID, name: "web", status: { state: "idle", needsYou: true, notices: [
    { itemId: `notice:${SID}:m1:1`, key: "m1", rev: 1, title: "New message from api", body: "hello", producer: "postal", actions: [{ label: "Approve", kind: "quarantine", body: { mid: "m1", verdict: "approve" } }] }] } };
  const inb = prefixInbound("TESTHOST", frame);
  assert.equal(inb.id, `TESTHOST:${SID}`);
  assert.equal(inb.status.notices[0].itemId, `TESTHOST:notice:${SID}:m1:1`, "the row's id is prefixed like the card's");
  assert.deepEqual(inb.status.notices[0].actions, frame.status.notices[0].actions, "the action rides untouched");
  assert.equal(frame.status.notices[0].itemId, `notice:${SID}:m1:1`, "a COPY: the inbound frame is not mutated");
  const done = prefixInbound("TESTHOST", { type: "noticeActionDone", itemId: `notice:${SID}:m1:1`, ok: false, error: "x" });
  assert.equal(done.itemId, inb.status.notices[0].itemId, "the answer and the row agree");
  assert.deepEqual(prefixInbound("", frame).status.notices[0].itemId, `notice:${SID}:m1:1`, "the local host is the identity");
  const bare = prefixInbound("TESTHOST", { type: "session", id: SID, status: { state: "idle" } });
  assert.equal(bare.status.notices, undefined, "a frame without the slice gains nothing");
});

test("the dismissal of a notice card is a routed gesture: the feed posts askClear with the card's sid", () => {
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "askClear", itemId: it\.itemId, sid: it\.sid \}\);/);
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "noticeAction", itemId: it\.itemId, sid: it\.sid, kind, body: act\.body, \.\.\.\(input \? \{ input \} : \{\}\) \}\);/, "an action carries the sid too (and its kind, 2026-09-19)");
});

test("the owner-less notice cards' owner key is one literal in the kernel and the pane, and the pane ranks it first by the feed board's rule", () => {
  // plans/notice-cards.md, "Owner-less cards and the terse command" (the user 2026-09-18): the reserved home is a word, so no
  // uuid sid can collide; the pane's sort rule reads the same key; the run's name is the kernel's
  assert.match(KERNEL, /^NOTICE_OWNERLESS_SID = "notes"/m, "the kernel's reserved key");
  assert.match(FEED, /const NOTICE_OWNERLESS_SID = "notes";/, "the pane's literal equals it");
  assert.match(KERNEL, /^NOTICE_OWNERLESS_NAME = "Notes"/m, "the run's name");
  // one helper strips a remote host's prefix the way federation adds it (round two of PR 1831), read by the rank, the chip and the header
  assert.match(FEED, /const isOwnerless = \(sid: string \| null \| undefined\): boolean => !!sid && bareId\(sid\) === NOTICE_OWNERLESS_SID;/, "the owner test, host-prefix aware");
  assert.match(FEED, /const ownerRank = \(sid: string\): number => isOwnerless\(sid\) \? 0 : 1;/, "the owner rule as a rank, not a timestamp");
  assert.match(FEED, /buckets\[k\]\.sort\(\(x, y\) => ownerRank\(entrySid\(x\)\) - ownerRank\(entrySid\(y\)\)\);/, "applied after the time sort in every mode, stable, on the shared entrySid");
  assert.doesNotMatch(FEED, /const entryOwner = /, "no second copy of entrySid");
  assert.match(FEED, /return isOwnerless\(s\) \? -1 : rank\.has\(s\)/, "and before the session order in grouped mode");
  assert.match(FEED, /const ownerless = isOwnerless\(it\.sid\);\s*\n\s*a\._name\.style\.display = ownerless \? "none" : "";/, "no session chip on the card");
  assert.match(FEED, /if \(isOwnerless\(e\.sid\) !== \(nm\.tagName === "SPAN"\)\) \{/, "the header's name node is a span for the owner-less run");
  assert.match(FEED, /nm\.classList\.remove\("dead"\); nm\.removeAttribute\("title"\); nm\.onclick = null;/, "plain text: no title, no dead class, no click");
  assert.match(KERNEL, /"board": "feed", "category": column,/, "the board model's two fields on every notice card (agreed with the board design's author)");
});

test("a remote notice card's item id is host-prefixed on the way in, in the rows AND in the kernel's replies, and stripped on the way out; a goal id is never touched", () => {
  // round three of PR 1831: two hosts' owner-less cards under one key minted one id (the reserved word sits in the sid slot on
  // every host) and the merged board kept one element; the prefix rides the id like the sid, the route strips it. Round four,
  // medium 1: the prefix covered a ROW's id and never a REPLY's, so a remote card's noticeActionDone arrived with the owning
  // kernel's bare id, missed the pane's map (keyed by the prefixed id) and the dismissing card stayed with its button latched.
  const a = { itemId: "notice:notes:k:1", sid: "notes", name: "Notes", column: "completed", t: 1 };
  const g = { itemId: SID + ":g1", sid: SID, name: "web", column: "working", t: 2 };
  const inb = prefixInbound("TESTHOST", { type: "feed", asks: [a, g], now: 1 });
  assert.equal(inb.asks[0].itemId, "TESTHOST:notice:notes:k:1", "a notice row's id wears the host");
  assert.equal(inb.asks[0].sid, "TESTHOST:notes");
  assert.equal(inb.asks[1].itemId, SID + ":g1", "a goal row's id stays bare");
  const done = prefixInbound("TESTHOST", { type: "noticeActionDone", itemId: "notice:notes:k:1", ok: true, error: "" });
  assert.equal(done.itemId, "TESTHOST:notice:notes:k:1", "the action's answer names the card the pane holds");
  const bell = prefixInbound("TESTHOST", { type: "settingRefused", gesture: "bell", sid: "notes", itemId: "notice:notes:k:1", flag: "", value: null, text: "x" });
  assert.deepEqual([bell.itemId, bell.sid], ["TESTHOST:notice:notes:k:1", "TESTHOST:notes"], "a refused bell names the card the pane latched");
  const err = prefixInbound("TESTHOST", { type: "err", op: "askFollowUp", itemId: "notice:notes:k:1", sid: "notes", title: "t", text: "x" });
  assert.equal(err.itemId, "TESTHOST:notice:notes:k:1", "an err naming its request too");
  const gerr = prefixInbound("TESTHOST", { type: "err", op: "askFollowUp", itemId: SID + ":g1", sid: SID, title: "t", text: "x" });
  assert.equal(gerr.itemId, SID + ":g1", "a goal id in a reply stays bare (T287)");
  const cit = prefixInbound("TESTHOST", { type: "dropCitation", itemId: SID + ":g1", itemIds: ["notice:notes:k:1", SID + ":g2"] });
  assert.deepEqual([cit.itemId, cit.itemIds], [SID + ":g1", ["TESTHOST:notice:notes:k:1", SID + ":g2"]], "a list of ids: the notice ones alone");
  const acct = prefixInbound("TESTHOST", { type: "err", op: "undoClear", sid: "notes", itemIds: ["notice:notes:k:1"], batches: [["notice:notes:k:1", SID + ":g2"], [SID + ":g3"]], owedBatch: ["notice:notes:k:1"], owedIds: ["notice:notes:k:1"], title: "t", text: "x" });
  assert.deepEqual([acct.batches, acct.owedBatch, acct.owedIds, acct.host], [[["TESTHOST:notice:notes:k:1", SID + ":g2"], [SID + ":g3"]], ["TESTHOST:notice:notes:k:1"], ["TESTHOST:notice:notes:k:1"], "TESTHOST"],
    "the kernel's stack on an account names notice ids the pane's way, the reorder's owed ids too, and the account wears its host (the eighth executed review of PR 1967; the second contributor's)");
  const bare = prefixInbound("TESTHOST", { type: "err", op: "undoClear", sid: "", title: "t", text: "x", itemIds: [] });
  const ack = prefixInbound("TESTHOST", { type: "undoAck", op: "undoClear", buildId: 7 });
  assert.deepEqual([ack.host, ack.buildId, ack.op], ["TESTHOST", 7, "undoClear"], "a landed undo's ack wears its kernel's host, its floor untouched (round fifteen of PR 1967): the floor lands on that kernel's checks alone");
  assert.ok(!("sid" in bare) || bare.sid === "", "an empty session id is not prefixed into the remote kernel's bare host (the second contributor's review): " + JSON.stringify(bare.sid));
  const r = routeOutbound({ type: "noticeAction", itemId: inb.asks[0].itemId, sid: inb.asks[0].sid, route: "/send", body: {} }, new Set(["TESTHOST"]));
  assert.deepEqual(r.map((x: any) => [x.host, x.msg.itemId, x.msg.sid]), [["TESTHOST", "notice:notes:k:1", "notes"]], "the action reaches the owning kernel with bare ids");
  const c = routeOutbound({ type: "askClear", itemId: inb.asks[0].itemId, sid: inb.asks[0].sid }, new Set(["TESTHOST"]));
  assert.deepEqual(c.map((x: any) => [x.host, x.msg.itemId]), [["TESTHOST", "notice:notes:k:1"]], "a clear too");
  // one helper for "a notice id wears its host", read by the row helper and by the top-level reply fields
  assert.match(FED, /^export function prefixNoticeId\(host: string, id: any\): any \{/m, "the helper");
  assert.equal((FED.match(/prefixNoticeId\(host, out\.itemId\)/g) || []).length, 2, "the row helper and the reply fields both read it");
  assert.match(FED, /if \(typeof out\.itemId === "string" && bareId\(out\.itemId\)\.startsWith\("notice:"\)\) out\.itemId = stripHost\(host, out\.itemId\);/, "the outbound strip on the scalar route");
});

test("the viewer's cleared overlay never clears a notice card: the kernel ships goal ids alone, so a remote notice stays whatever a stale ledger names", () => {
  // round four of PR 1831, low: the overlay's bare-notice compare of round three could never fire, because the kernel's
  // _cleared_foreign drops every prefixed family (notice: among them) before the ids ride the frame; a notice card's dismissal
  // is a routed gesture (askClear with the card's sid) recorded by the owning kernel's ledger under the bare id
  assert.match(KERNEL, /^_CLEARED_NO_SESSION = \(.*"notice:"\)/m, "the kernel's rule: no notice id is ever a foreign clear");
  assert.doesNotMatch(FED, /bareId\(id\)\.startsWith\("notice:"\) && foreign\.has/, "no unreachable arm in the overlay");
  const remoteNotice = prefixInbound("TESTHOST", { type: "feed", asks: [{ ...notice(SID, 1), column: "completed" }], sessions: [{ sid: SID, name: "web" }], working: [], awaiting: [], ledgers: [] });
  const local = { type: "feed", asks: [], sessions: [], working: [], awaiting: [], ledgers: [], clearedForeign: [`notice:${SID}:figure:1`, SID + ":g1"] };
  const merged: any = mergeHostFeeds({ "": local, TESTHOST: remoteNotice }, ["", "TESTHOST"]);
  assert.equal(merged.asks.length, 1, "the remote notice card stays: a viewer's ledger clears goals alone");
  assert.equal(merged.asks[0].itemId, `TESTHOST:notice:${SID}:figure:1`);
});

test("a remote session frame's rows carry their sender the way the feed's cards do: a local-sender origin takes the host as peerHost and a prefixed peerSid, the awaiting peers likewise, and the click routes to that host (a contributor's post-merge note on PR 2124)", () => {
  const PSID = "11111111-2222-3333-4444-000000000777", HOSTED = "11111111-2222-3333-4444-000000000778", HANDED = "11111111-2222-3333-4444-000000000779";
  const frame = { type: "session", id: SID, status: { state: "blocked", notices: [
    { itemId: `notice:${SID}:m1:1`, key: "m1", rev: 1, title: "New message from api", body: "hello", producer: "postal", actions: [],
      origin: { peer: "api", peerHost: "", peerSid: PSID, color: null, live: true },
      handoffTo: { peer: "tests", peerHost: "", peerSid: HANDED },
      awaiting: { peers: [{ sid: PSID, host: "", name: "api" }, { sid: HOSTED, host: "OTHER", name: "docs" }] } },
    { itemId: `notice:${SID}:m2:1`, key: "m2", rev: 1, title: "From another host", body: "x", producer: "postal", actions: [],
      origin: { peer: "docs", peerHost: "OTHER", peerSid: HOSTED, color: null, live: true } } ] } };
  const inb = prefixInbound("TESTHOST", frame);
  const [r1, r2] = inb.status.notices;
  assert.equal(r1.itemId, `TESTHOST:notice:${SID}:m1:1`, "the row's id wears the host, as before");
  assert.deepEqual([r1.origin.peerHost, r1.origin.peerSid], ["TESTHOST", "TESTHOST:" + PSID], "a sender the card's own kernel recorded as local is attributed to that host and its sid prefixed, so the badge's data-sid routes there");
  assert.deepEqual(r1.awaiting.peers.map((p: any) => [p.host, p.sid]), [["TESTHOST", "TESTHOST:" + PSID], ["OTHER", HOSTED]], "a peer the kernel resolved as its own takes the host; an already-hosted peer passes through untouched");
  assert.deepEqual([r2.origin.peerHost, r2.origin.peerSid], ["OTHER", HOSTED], "a sender on some OTHER host keeps its record and its bare sid");
  assert.deepEqual([r1.handoffTo.peerHost, r1.handoffTo.peerSid], ["TESTHOST", "TESTHOST:" + HANDED], "the delegated-to badge takes the origin's rule on a row (the verifier of PR 2141): its click routes to the recipient's kernel");
  const cardIn = prefixInbound("TESTHOST", { type: "feed", asks: [{ itemId: SID + ":g1", sid: SID, name: "web", column: "working", t: 1, handoffTo: { peer: "tests", peerHost: "", peerSid: HANDED }, waitingOn: "tests" }] });
  assert.deepEqual([cardIn.asks[0].handoffTo.peerHost, cardIn.asks[0].handoffTo.peerSid, cardIn.asks[0].waitingOn], ["TESTHOST", "TESTHOST:" + HANDED, "tests"], "…and on a feed card; waitingOn is a peer's name, display text, left alone");
  assert.deepEqual(routeOutbound({ type: "openSession", id: r1.origin.peerSid }), [{ host: "TESTHOST", msg: { type: "openSession", id: PSID } }], "the click on the row's badge reaches the sender's kernel, not the local one");
  assert.equal(frame.status.notices[0].origin.peerSid, PSID, "a COPY: the inbound frame is not mutated");
  assert.equal(prefixInbound("", frame).status.notices[0].origin.peerHost, "", "the local host is the identity");
});

test("the tracked delegation's recipients take the awaiting peers' rule, on a card and on a row: an entry the card's kernel recorded as its own takes the host and a prefixed sid, an already-hosted one passes untouched (a contributor's note on PR 2141)", () => {
  const HANDED = "11111111-2222-3333-4444-000000000779", HOSTED = "11111111-2222-3333-4444-000000000778";
  const tracked = [{ sid: HANDED, name: "tests", host: "" }, { sid: HOSTED, name: "docs", host: "OTHER" }];
  const cardIn = prefixInbound("TESTHOST", { type: "feed", asks: [{ itemId: SID + ":g1", sid: SID, name: "web", column: "working", t: 1, delegTracked: tracked }] });
  assert.deepEqual(cardIn.asks[0].delegTracked.map((p: any) => [p.host, p.sid]), [["TESTHOST", "TESTHOST:" + HANDED], ["OTHER", HOSTED]], "on a card: the badge's click target is the entry's sid, so it wears the host");
  const rowIn = prefixInbound("TESTHOST", { type: "session", id: SID, status: { notices: [{ itemId: `notice:${SID}:m1:1`, key: "m1", rev: 1, title: "t", body: "b", producer: "postal", actions: [], delegTracked: tracked }] } });
  assert.deepEqual(rowIn.status.notices[0].delegTracked.map((p: any) => [p.host, p.sid]), [["TESTHOST", "TESTHOST:" + HANDED], ["OTHER", HOSTED]], "on a row, which carries the field though it draws no tracked delegation: the same helper serves both shapes");
  assert.equal(tracked[0].sid, HANDED, "a COPY: the inbound entries are not mutated");
});

test("a remote host's error-center rows reach the merged feed with its host on the sig and the text; the local rows pass as they are", () => {
  // a file comment refused or rolled back on another host records its words in THAT kernel's error center, and the
  // viewer that sent it may be closed, so the bell is the only surface left
  const local = { type: "feed", sdkNotices: [{ sig: "boot|3", t: 1, text: "a local problem" }] };
  const remote = { type: "feed", sdkNotices: [{ sig: "boot|3", t: 2, text: "A comment on ~/a.md in web was not saved." }, { t: 3, text: "no sig" }] };
  const merged: any = mergeHostFeeds({ "": local, HOSTA: remote }, ["", "HOSTA"]);
  assert.deepEqual(merged.sdkNotices, [
    { sig: "boot|3", t: 1, text: "a local problem" },
    { sig: "HOSTA|boot|3", t: 2, text: "HOSTA: A comment on ~/a.md in web was not saved." },
  ], "two hosts' ring sequences never collide, and a row with no sig is not an entry");
  const none: any = mergeHostFeeds({ "": { type: "feed" }, HOSTA: { type: "feed" } }, ["", "HOSTA"]);
  assert.equal(none.sdkNotices, undefined);
});
