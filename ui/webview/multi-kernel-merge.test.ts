// Phase 2b — the pure core of the federated dashboard: prefix inbound session ids by host, route outbound
// messages to the owning kernel (stripping the prefix), and merge per-host tab orders group-by-host. This
// is the risky logic; the WebSocket/DOM wiring in federation.ts's manager is thin glue over these.

import { test } from "node:test";
import assert from "node:assert/strict";
import { prefixId, hostOf, bareId, prefixInbound, routeOutbound, mergeHostOrder, mergeHostFeeds,
         prefixTimelineData, mergeHostTimelines, mergeHostBars, stitchMessages,
         hostOffsets, rebaseHostTimes, rebaseExecs,
         FederationManager, pickWid } from "./federation";

const U = "11111111-2222-3333-4444-555555555555";
const V = "99999999-8888-7777-6666-555555555555";

test("prefixId / hostOf / bareId round-trip", () => {
  assert.equal(prefixId("gpu1", U), "gpu1:" + U);
  assert.equal(prefixId("", U), U, "local host adds no prefix");
  assert.equal(hostOf("gpu1:" + U), "gpu1");
  assert.equal(hostOf(U), "", "a bare UUID has no host");
  assert.equal(bareId("gpu1:" + U), U);
  assert.equal(bareId(U), U);
});

test("prefixInbound: local host is the identity transform", () => {
  const m = { type: "session", id: U };
  assert.deepEqual(prefixInbound("", m), m);
});

test("prefixInbound: scalar id (session/chatTail/focus/ledger/renamed/closed)", () => {
  assert.equal(prefixInbound("gpu1", { type: "session", id: U }).id, "gpu1:" + U);
  assert.equal(prefixInbound("gpu1", { type: "focus", id: U, anchor: "x" }).id, "gpu1:" + U);
});

test("prefixInbound: working.names[] array of ids", () => {
  const out = prefixInbound("gpu1", { type: "working", names: [U, V] });
  assert.deepEqual(out.names, ["gpu1:" + U, "gpu1:" + V]);
});

test("prefixInbound: tabOrder order[] and tabs[].id (+ display name)", () => {
  const out = prefixInbound("gpu1", { type: "tabOrder", order: [U, V], tabs: [{ id: U, name: "a" }] });
  assert.deepEqual(out.order, ["gpu1:" + U, "gpu1:" + V]);
  assert.equal(out.tabs[0].id, "gpu1:" + U);
  assert.equal(out.tabs[0].name, "gpu1:a", "the tab's display name is host-prefixed too (host:name)");
});

test("prefixInbound: asks[].origin attributes the delegating sender's host (the user 2026-07-26)", () => {
  // peerHost empty (or absent, an older kernel) → the sender is local to the card's own kernel:
  // attribute that host and prefix peerSid so the "↪ from" click routes there.
  const out = prefixInbound("gpu1", { type: "feed",
    asks: [{ sid: U, name: "web", origin: { peer: "api", peerSid: V, peerHost: "" } },
           { sid: U, name: "web", origin: { peer: "signal", peerSid: V, peerHost: "farhost" } }] });
  assert.deepEqual(out.asks[0].origin, { peer: "api", peerSid: "gpu1:" + V, peerHost: "gpu1" });
  // a recorded peerHost means the sender lives on some THIRD host that kernel named: keep it, and keep
  // peerSid bare — the viewer may be that very host, where the bare uuid opens directly.
  assert.deepEqual(out.asks[1].origin, { peer: "signal", peerSid: V, peerHost: "farhost" });
});

test("prefixInbound: display name is host-prefixed on session-bearing messages", () => {
  // a session's tab + chat header should read "gpu1:foo" so a remote session never collides visually with a
  // local same-named one. Only prefixed when a co-present id/sid marks it as a session name.
  assert.equal(prefixInbound("gpu1", { type: "session", id: U, name: "foo" }).name, "gpu1:foo");
  assert.equal(prefixInbound("gpu1", { type: "renamed", id: U, name: "bar" }).name, "gpu1:bar");
  // feed items carry sid+name → their card name is prefixed too
  const fed = prefixInbound("gpu1", { type: "feed", items: [{ sid: U, name: "baz" }] });
  assert.equal(fed.items[0].sid, "gpu1:" + U);
  assert.equal(fed.items[0].name, "gpu1:baz");
  // a bare `name` with NO id is left alone (not a session name)
  assert.deepEqual(prefixInbound("gpu1", { type: "toast", name: "hi" }), { type: "toast", name: "hi" });
});

test("prefixInbound: feed payload asks/items/ledgers[].sid and working[]", () => {
  const out = prefixInbound("gpu1", {
    type: "feed",
    asks: [{ sid: U, q: "?" }],
    items: [{ sid: V, t: 1 }],
    ledgers: [{ sid: U, ledger: {} }],
    working: [U, V],
  });
  assert.equal(out.asks[0].sid, "gpu1:" + U);
  assert.equal(out.asks[0].q, "?", "non-id fields untouched");
  assert.equal(out.items[0].sid, "gpu1:" + V);
  assert.equal(out.ledgers[0].sid, "gpu1:" + U);
  assert.deepEqual(out.working, ["gpu1:" + U, "gpu1:" + V]);
});

test("prefixInbound: messages without ids pass through (no spurious fields touched)", () => {
  const m = { type: "clipboardText", text: "hi" };
  assert.deepEqual(prefixInbound("gpu1", m), m);
  const orig = { type: "session", id: U };
  prefixInbound("gpu1", orig);
  assert.equal(orig.id, U, "input is not mutated (copy returned)");
});

test("routeOutbound: a local (bare) id routes to the local kernel unchanged", () => {
  const routes = routeOutbound({ type: "activeTab", id: U });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "");
  assert.equal(routes[0].msg.id, U);
});

test("routeOutbound: a remote id routes to its host with the prefix stripped", () => {
  const routes = routeOutbound({ type: "send", id: "gpu1:" + U, text: "hello" });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "gpu1");
  assert.equal(routes[0].msg.id, U, "id is stripped back to bare for the owning kernel");
  assert.equal(routes[0].msg.text, "hello");
});

test("routeOutbound: a card op routes by its SID — an itemId alone can only go local", () => {
  // Why every card-addressed op has to carry `sid` (the user 2026-07-29): an itemId is "‹sid›:‹goalId›", so
  // it can never join SCALAR_ID — hostOf() splits on the FIRST colon and would read the session uuid as a
  // host name. The sid is the routing key; the itemId is already bare on both sides and rides through
  // untouched. A follow-up sent without the sid went to the LOCAL kernel, which owns no such session and
  // dropped it in silence: the reply was simply lost.
  const routes = routeOutbound({ type: "askFollowUp", itemId: U + ":g4", text: "and the fix?", sid: "gpu1:" + U });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "gpu1", "the sid picks the owning kernel");
  assert.equal(routes[0].msg.sid, U, "sid stripped back to bare");
  assert.equal(routes[0].msg.itemId, U + ":g4", "itemId was never prefixed, so it passes through as-is");
  // the regression: drop the sid and the SAME message lands on the wrong kernel
  const orphan = routeOutbound({ type: "askFollowUp", itemId: U + ":g4", text: "and the fix?" });
  assert.equal(orphan[0].host, "", "no sid → local, whoever the card belongs to");
});

test("routeOutbound: showAskPath routes by sid — a remote card's hover glow reaches its own kernel", () => {
  // The feed→timeline/chat highlight (the user 2026-08-03): hovering a remote session's card sent
  // showAskPath with only the (bare, unroutable) itemId, so it always landed on the LOCAL kernel,
  // which owns no such goal — nothing lit. The sid rides along purely as the routing key.
  const routes = routeOutbound({ type: "showAskPath", itemId: U + ":g4", sid: "gpu1:" + U, locate: false });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "gpu1", "the sid picks the owning kernel");
  assert.equal(routes[0].msg.sid, U, "sid stripped back to bare");
  assert.equal(routes[0].msg.itemId, U + ":g4", "itemId was never prefixed, so it passes through as-is");
  // a local card's hover (bare sid) keeps going local, exactly as before
  assert.equal(routeOutbound({ type: "showAskPath", itemId: U + ":g4", sid: U, off: true })[0].host, "");
});

test("routeOutbound: dropFile routes by its session id — attachment bytes reach the OWNING kernel", () => {
  // A composer attachment (📎 picker, drag-drop, paste) ships bytes as dropFile; the kernel that
  // takes them saves under ITS drops/ and the saved path rides the prompt, read by the agent on
  // that machine. So the bytes must land on the session's own kernel: saved anywhere else, the
  // agent is handed a path that does not exist on its filesystem (before the id was stamped,
  // every remote session's attachment was saved on the LOCAL kernel).
  const routes = routeOutbound({ type: "dropFile", name: "screenshot.png", b64: "aGVsbG8=", id: "gpu1:" + U });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "gpu1", "the session id picks the owning kernel");
  assert.equal(routes[0].msg.id, U, "id stripped back to bare for that kernel");
  assert.equal(routes[0].msg.name, "screenshot.png", "payload rides through untouched");
  assert.equal(routes[0].msg.b64, "aGVsbG8=");
  // a local session's attachment (bare id) stays local, the single-kernel path unchanged
  assert.equal(routeOutbound({ type: "dropFile", name: "a.png", b64: "eA==", id: U })[0].host, "");
  // ...and the droppedPath REPLY carries no session field, so prefixInbound passes it through
  // untouched — the pane attaches it to its own activeId, which is already host-prefixed.
  const reply = { type: "droppedPath", path: "~/.local/state/romp/drops/1-screenshot.png" };
  assert.deepEqual(prefixInbound("gpu1", reply), reply);
});

test("prefixInbound: glowTurns groups get sid-prefixed so the merged chat finds the remote pane", () => {
  // The return leg of the same highlight: the owning kernel answers with glowTurns keyed by its own
  // bare sids, but the merged chat page keys its views "host:sid" — unprefixed groups matched nothing.
  const m = prefixInbound("gpu1", { type: "glowTurns", groups: [{ sid: U, uuids: ["a1", "b2"] }], mids: [] });
  assert.equal(m.groups[0].sid, "gpu1:" + U);
  assert.deepEqual(m.groups[0].uuids, ["a1", "b2"], "atom uuids are globally unique and stay bare");
  const clear = prefixInbound("gpu1", { type: "glowTurns", groups: [], mids: [] });
  assert.deepEqual(clear.groups, [], "an empty clear passes through");
});

test("routeOutbound: a global message (no session id) goes local", () => {
  const routes = routeOutbound({ type: "setColormap", name: "viridis" });
  assert.deepEqual(routes, [{ host: "", msg: { type: "setColormap", name: "viridis" } }]);
});

test("routeOutbound: a cross-host reorder fans out one route per host with its own sids", () => {
  const order = [U, "gpu1:" + V, "gpu1:" + U, V]; // local U, gpu1 V, gpu1 U, local V
  const routes = routeOutbound({ type: "reorderTabs", order });
  const byHost = Object.fromEntries(routes.map((r) => [r.host, r.msg.order]));
  assert.deepEqual(byHost[""], [U, V], "local kernel gets its sids in their relative order, bare");
  assert.deepEqual(byHost["gpu1"], [V, U], "gpu1 gets its sids in their relative order, bare");
});

test("mergeHostOrder: each host verbatim, concatenated in hostSeq order, deduped", () => {
  const perHost = {
    "": [U, V],
    gpu1: ["gpu1:" + V, "gpu1:" + U],
  };
  const merged = mergeHostOrder(perHost, ["", "gpu1"]);
  assert.deepEqual(merged, [U, V, "gpu1:" + V, "gpu1:" + U]);
  // hostSeq controls grouping order; a missing host contributes nothing.
  assert.deepEqual(mergeHostOrder(perHost, ["gpu1", "", "absent"]), ["gpu1:" + V, "gpu1:" + U, U, V]);
});

test("mergeHostOrder: never re-sorts within a host (kernel order is authoritative)", () => {
  // even though V<U lexically, gpu1's given order is preserved verbatim
  const merged = mergeHostOrder({ gpu1: ["gpu1:" + U, "gpu1:" + V] }, ["gpu1"]);
  assert.deepEqual(merged, ["gpu1:" + U, "gpu1:" + V]);
});

test("mergeHostFeeds: remote syncNotices reach the local Log, host-prefixed and sig-scoped", () => {
  // the user 2026-08-15: a devbox updating itself all day left no trace on the laptop dashboard —
  // the merge kept only local chrome and dropped remote kernels' sync outcomes on the floor.
  const perHost = {
    "": { type: "feed", items: [], asks: [], working: [],
          syncNotices: [{ sig: "b1|3", t: 10, text: "updated to abc123", ok: true }] },
    TESTHOST: { type: "feed", items: [], asks: [], working: [],
             syncNotices: [{ sig: "b2|7", t: 11, text: "pulled and restarted", ok: true }] },
  };
  const m = mergeHostFeeds(perHost, ["", "TESTHOST"]);
  assert.deepEqual(m.syncNotices, [
    { sig: "b1|3", t: 10, text: "updated to abc123", ok: true },
    { sig: "TESTHOST|b2|7", t: 11, text: "TESTHOST: pulled and restarted", ok: true },
  ], "local rows verbatim; remote rows host-prefixed in text and sig-scoped per host");
  const none = mergeHostFeeds({ "": { type: "feed", items: [], asks: [], working: [] } }, [""]);
  assert.ok(!("syncNotices" in none), "no rows anywhere → the key stays absent, like the single-kernel path");
});

test("mergeHostFeeds: concatenates items/asks/working across hosts (local first), keeps local chrome", () => {
  // Regression: without a merge, the local + remote feed snapshots (each pushed ~2s) clobber each other and
  // the feed visibly flips back and forth. mergeHostFeeds combines them into one stable snapshot.
  const perHost = {
    "": { type: "feed", items: [{ sid: U }], asks: [{ sid: U, itemId: "a" }], working: [U],
          ledgers: [{ sid: U }], now: 1000, dismissedCount: 3, showDismissed: false, canUndoClear: true },
    TESTHOST: { type: "feed", items: [{ sid: "TESTHOST:" + V }], asks: [{ sid: "TESTHOST:" + V, itemId: "b" }],
             working: ["TESTHOST:" + V], ledgers: [{ sid: "TESTHOST:" + V }], now: 999, dismissedCount: 7 },
  };
  const m = mergeHostFeeds(perHost, ["", "TESTHOST"]);
  assert.equal(m.type, "feed");
  assert.deepEqual(m.items.map((i: any) => i.sid), [U, "TESTHOST:" + V], "items concatenated, local first");
  assert.deepEqual(m.asks.map((a: any) => a.sid), [U, "TESTHOST:" + V], "asks concatenated");
  assert.deepEqual(m.working, [U, "TESTHOST:" + V], "working concatenated");
  assert.deepEqual(m.ledgers.map((l: any) => l.sid), [U, "TESTHOST:" + V], "ledgers (fleet) concatenated");
  // local is authoritative for the dashboard's own chrome (clock, toggles)…
  assert.equal(m.now, 1000);
  assert.equal(m.showDismissed, false);
  // …but the dismissed/undo chrome spans hosts: counts SUM, undo lights when ANY kernel can undo.
  assert.equal(m.dismissedCount, 10, "3 local + 7 remote dismissed");
  assert.equal(m.canUndoClear, true);
});

test("mergeHostFeeds: per-host buildIds ride the merge — each kernel's counter, separately addressable", () => {
  // the user 2026-08-15: a reply to a remote card bounced Working → Completed → Working. The merged
  // payload's top-level buildId is the LOCAL kernel's counter (large after days of uptime); the remote
  // ack's buildId is the remote's (small after a restart). Comparing them cross-counter "outranked" the
  // ack instantly. The map is what lets the feed pane compare same-counter only.
  const perHost = {
    "": { type: "feed", items: [], asks: [], working: [], buildId: 4032 },
    TESTHOST: { type: "feed", items: [], asks: [], working: [], buildId: 7 },
  };
  const m = mergeHostFeeds(perHost, ["", "TESTHOST"]);
  assert.deepEqual(m.buildIds, { "": 4032, TESTHOST: 7 });
  assert.equal(m.buildId, 4032, "the local scalar stays for older panes — additive, never repurposed");
  // a host too old to send buildId simply has no entry — absent, never guessed
  const old = mergeHostFeeds({ "": { type: "feed", items: [], asks: [], working: [] } }, [""]);
  assert.deepEqual(old.buildIds, {});
});

test("prefixInbound: a remote cardMoveAck/cardPredict is host-stamped; its goal ids stay bare", () => {
  // goal ids are globally unique (uuid:gN) and match the pane's itemIds unprefixed; the HOST is what the
  // ack was missing — without it a buildId can't be placed on the counter it was minted by
  const ack = prefixInbound("TESTHOST", { type: "cardMoveAck", ids: [U + ":g6"], ok: true, buildId: 7 });
  assert.equal(ack.host, "TESTHOST");
  assert.deepEqual(ack.ids, [U + ":g6"], "goal ids pass through bare");
  assert.equal(prefixInbound("TESTHOST", { type: "cardPredict", ids: [U + ":g6"], flavor: "followup" }).host, "TESTHOST");
  const local = prefixInbound("", { type: "cardMoveAck", ids: [U + ":g6"], ok: true, buildId: 9 });
  assert.ok(!("host" in local), "local is the identity transform — no stamp");
});

test("mergeHostFeeds: a remote-only undo lights the Undo button (clear routed to that kernel)", () => {
  const m = mergeHostFeeds({
    "": { type: "feed", items: [], asks: [], working: [], canUndoClear: false, dismissedCount: 0 },
    TESTHOST: { type: "feed", items: [], asks: [], working: [], canUndoClear: true, dismissedCount: 2 },
  }, ["", "TESTHOST"]);
  assert.equal(m.canUndoClear, true);
  assert.equal(m.dismissedCount, 2);
});

test("mergeHostFeeds: single (local) host is an equivalent passthrough", () => {
  const local = { type: "feed", items: [{ sid: U }], asks: [], working: [U], now: 5, dismissedCount: 0 };
  const m = mergeHostFeeds({ "": local }, [""]);
  assert.deepEqual(m.items, [{ sid: U }]);
  assert.deepEqual(m.working, [U]);
  assert.equal(m.now, 5);
});

test("mergeHostFeeds: a host with no feed yet contributes nothing (no crash)", () => {
  const m = mergeHostFeeds({ "": { type: "feed", items: [{ sid: U }], asks: [], working: [] } }, ["", "TESTHOST"]);
  assert.deepEqual(m.items.map((i: any) => i.sid), [U]);
});

test("mergeHostFeeds: ledgers omitted until some host builds them (fleet loader holds)", () => {
  // No host has a ledgers array yet → merged carries no `ledgers` key, so fleet.ts keeps its loader up
  // rather than dropping onto an empty pane.
  const m = mergeHostFeeds({ "": { type: "feed", items: [], asks: [], working: [] } }, [""]);
  assert.equal("ledgers" in m, false);
});

// ── timeline federation (the timeline now rides the same shim + merge as every other pane) ──────

test("prefixInbound: timeline {type:data} payload — sessions, turns keys + bar.tid, marks, activeChat", () => {
  const out = prefixInbound("TESTHOST", {
    type: "data",
    data: {
      sessions: [{ id: U, name: "sess1", state: "idle" }],
      turns: { [U]: [{ id: "ev-1", tid: U, start: 1, end: 2 }] },
      messages: [{ id: "m1", fromId: U, toId: V, sent: 1, exec: 2 }],
      judging: [{ judge: "planner", sid: U, t: 1 }],
      activeChat: { tid: U, name: "sess1" },
      now: 1000,
    },
  });
  const d = out.data;
  assert.equal(d.sessions[0].id, "TESTHOST:" + U);
  assert.equal(d.sessions[0].name, "TESTHOST:sess1", "lane label reads host:name");
  assert.deepEqual(Object.keys(d.turns), ["TESTHOST:" + U], "turns re-keyed by prefixed sid");
  assert.equal(d.turns["TESTHOST:" + U][0].tid, "TESTHOST:" + U, "a bar's tid (its sid) is prefixed");
  assert.equal(d.turns["TESTHOST:" + U][0].id, "ev-1", "event uuids stay bare (globally unique)");
  assert.equal(d.messages[0].fromId, "TESTHOST:" + U);
  assert.equal(d.messages[0].toId, "TESTHOST:" + V);
  assert.equal(d.judging[0].sid, "TESTHOST:" + U);
  assert.equal(d.activeChat.tid, "TESTHOST:" + U, "the active-chat cue lights the prefixed lane");
  assert.equal(d.now, 1000, "scalar fields untouched");
});

test("prefixInbound: timeline {type:bars} detail message (top-level turns/marks)", () => {
  const out = prefixInbound("TESTHOST", {
    type: "bars",
    turns: { [U]: [{ id: "ev-1", tid: U }] },
    judging: [{ judge: "closer", sid: U, t: 5 }],
    messages: [],
    now: 7,
  });
  assert.deepEqual(Object.keys(out.turns), ["TESTHOST:" + U]);
  assert.equal(out.turns["TESTHOST:" + U][0].tid, "TESTHOST:" + U);
  assert.equal(out.judging[0].sid, "TESTHOST:" + U);
  assert.equal(out.now, 7);
});

test("prefixTimelineData: local host is the identity transform", () => {
  const d = { sessions: [{ id: U, name: "a" }], turns: {} };
  assert.equal(prefixTimelineData("", d), d);
});

test("mergeHostTimelines: local lanes first, host stamped per session, turns/marks unioned, chrome local", () => {
  const perHost = {
    "": { sessions: [{ id: U, name: "loc" }], turns: {}, messages: [], judging: [],
          now: 1000, usage: { u: 1 }, focus: { nonce: 3 } },
    TESTHOST: { sessions: [{ id: "TESTHOST:" + V, name: "TESTHOST:rem" }], turns: { ["TESTHOST:" + V]: [{ id: "e" }] },
             messages: [{ fromId: "TESTHOST:" + V }], judging: [], now: 999 },
  };
  const m = mergeHostTimelines(perHost, ["", "TESTHOST"]);
  assert.deepEqual(m.sessions.map((s: any) => s.id), [U, "TESTHOST:" + V], "local group first, remote below");
  assert.deepEqual(m.sessions.map((s: any) => s.host), ["", "TESTHOST"], "owning host stamped (drives the lane-group gap)");
  assert.deepEqual(Object.keys(m.turns), ["TESTHOST:" + V]);
  assert.equal(m.messages.length, 1);
  assert.equal(m.now, 1000, "the LOCAL kernel is the clock authority");
  assert.deepEqual(m.usage, { u: 1 }, "usage (account rate-limit bars) stays local");
  assert.deepEqual(m.focus, { nonce: 3 }, "cross-pane focus stays local");
});

test("mergeHostBars: per-host bars union — one host's push can't clobber another's (applyBars replaces wholesale)", () => {
  const perHost = {
    "": { type: "bars", turns: { [U]: [{ id: "a" }] }, messages: [], judging: [{ judge: "planner", sid: U, t: 1 }], now: 50 },
    TESTHOST: { type: "bars", turns: { ["TESTHOST:" + V]: [{ id: "b" }] }, messages: [], judging: [], now: 49 },
  };
  const m = mergeHostBars(perHost, ["", "TESTHOST"]);
  assert.deepEqual(Object.keys(m.turns).sort(), [U, "TESTHOST:" + V].sort());
  assert.equal(m.judging.length, 1);
  assert.equal(m.now, 50, "local clock");
  // a host with no bars yet contributes nothing (no crash)
  const single = mergeHostBars({ "": perHost[""] }, ["", "TESTHOST"]);
  assert.deepEqual(Object.keys(single.turns), [U]);
});

test("mergeHostBars: warming is true if ANY host is still the cold partial (keeps the loader up)", () => {
  // the user 2026-07-03: on a cold restart the timeline flashed "no romp activity" instead of the romp
  // loader. The kernel's live-first build is PARTIAL (warming); merged warming must be sticky across hosts
  // so a warmed local + a still-cold remote keeps the loader until the remote settles.
  const warm = { type: "bars", turns: { [U]: [{ id: "a" }] }, messages: [], judging: [], now: 50, warming: false };
  const cold = { type: "bars", turns: {}, messages: [], judging: [], now: 50, warming: true };
  assert.equal(mergeHostBars({ "": warm, TESTHOST: cold }, ["", "TESTHOST"]).warming, true, "any warming host → keep warming");
  assert.equal(mergeHostBars({ "": warm }, ["", "TESTHOST"]).warming, false, "all settled → warming clears");
  assert.equal(mergeHostBars({ "": cold }, [""]).warming, true, "the cold local partial is warming");
});

test("stitchMessages: a cross-host connector lands on both lanes, recipient's copy wins the timing", () => {
  // laptop session U messaged TESTHOST session V. The LOCAL kernel emitted its one-sided copy (toId = the
  // bare foreign sid — it has no lane for it, no exec); TESTHOST's kernel emitted its own copy, whose
  // FOREIGN end (U) got blindly host-prefixed by inbound prefixing, and which KNOWS the delivery time.
  const sessions = [{ id: U, name: "local-sess" }, { id: "TESTHOST:" + V, name: "TESTHOST:remote-sess" }];
  const merged = stitchMessages([
    { id: "m1", fromId: U, toId: V, from: "local-sess", to: "", sent: 100, exec: 100, hasExec: false },
    { id: "m1", fromId: "TESTHOST:" + U, toId: "TESTHOST:" + V, from: "", to: "TESTHOST:remote-sess", sent: 100, exec: 130, hasExec: true },
  ], sessions);
  assert.equal(merged.length, 1, "the two kernels' copies of one message dedupe by id");
  const m = merged[0];
  assert.equal(m.fromId, U, "sender endpoint = the local lane");
  assert.equal(m.toId, "TESTHOST:" + V, "recipient endpoint stitched onto TESTHOST's (prefixed) lane");
  assert.equal(m.hasExec, true, "the recipient kernel's real delivery time wins");
  assert.equal(m.exec, 130);
});

test("stitchMessages: fills a missing display name from the matched lane; unmatched ends pass through", () => {
  const sessions = [{ id: U, name: "local-sess" }, { id: "TESTHOST:" + V, name: "TESTHOST:remote-sess" }];
  const [m] = stitchMessages([{ id: "m2", fromId: U, toId: V, from: "local-sess", to: "", sent: 1, exec: 1, hasExec: false }], sessions);
  assert.equal(m.to, "TESTHOST:remote-sess", "the sender kernel never knew the foreign name — the lane fills it");
  // an endpoint matching NO lane (a long-dead local sid, or a not-yet-attached host) is left alone —
  // the view's lane lookup drops the connector, same as ever.
  const ghost = "77777777-6666-5555-4444-333333333333";
  const [g] = stitchMessages([{ id: "m3", fromId: U, toId: ghost, sent: 1, exec: 1, hasExec: false }], sessions);
  assert.equal(g.toId, ghost);
});

test("mergeHostBars: stitches connectors against the lane list handed in (bars carry no lanes)", () => {
  const sessions = [{ id: U, name: "a" }, { id: "TESTHOST:" + V, name: "TESTHOST:b" }];
  const perHost = {
    "": { type: "bars", turns: {}, judging: [], nudges: [],
          messages: [{ id: "m1", fromId: U, toId: V, sent: 5, exec: 5, hasExec: false }] },
    TESTHOST: { type: "bars", turns: {}, judging: [], nudges: [],
             messages: [{ id: "m1", fromId: "TESTHOST:" + U, toId: "TESTHOST:" + V, sent: 5, exec: 9, hasExec: true }] },
  };
  const m = mergeHostBars(perHost, ["", "TESTHOST"], sessions);
  assert.equal(m.messages.length, 1);
  assert.deepEqual([m.messages[0].fromId, m.messages[0].toId, m.messages[0].exec], [U, "TESTHOST:" + V, 9]);
});

test("routeOutbound: an explicit host field routes there, stripped (createSession's + modal host pick)", () => {
  const remote = routeOutbound({ type: "createSession", name: "web", backend: "sdk", dir: "", host: "TESTHOST" });
  assert.deepEqual(remote, [{ host: "TESTHOST", msg: { type: "createSession", name: "web", backend: "sdk", dir: "" } }],
                   "the kernel handlers are host-blind — the field is stripped");
  const local = routeOutbound({ type: "createSession", name: "web", backend: "sdk", dir: "", host: "" });
  assert.deepEqual(local, [{ host: "", msg: { type: "createSession", name: "web", backend: "sdk", dir: "" } }]);
});

test("routeOutbound: name-addressed messages route to a KNOWN host only (compact / sendCommand / deepLink)", () => {
  const known = new Set(["TESTHOST"]);
  // a remote lane's display name is prefixed → routed + stripped
  assert.deepEqual(routeOutbound({ type: "compact", name: "TESTHOST:sess1" }, known),
                   [{ host: "TESTHOST", msg: { type: "compact", name: "sess1" } }]);
  assert.deepEqual(routeOutbound({ type: "deepLink", session: "TESTHOST:sess1" }, known),
                   [{ host: "TESTHOST", msg: { type: "deepLink", session: "sess1" } }]);
  // a local name that merely CONTAINS a colon must not misroute (unknown host prefix)
  assert.deepEqual(routeOutbound({ type: "compact", name: "odd:name" }, known),
                   [{ host: "", msg: { type: "compact", name: "odd:name" } }]);
  // and with no knownHosts at all, names never route
  assert.deepEqual(routeOutbound({ type: "compact", name: "TESTHOST:sess1" }),
                   [{ host: "", msg: { type: "compact", name: "TESTHOST:sess1" } }]);
  // renameSession routes by ID; its `name` (the user's new title) is never stripped
  const rn = routeOutbound({ type: "renameSession", id: "TESTHOST:" + U, name: "newtitle" }, known);
  assert.deepEqual(rn, [{ host: "TESTHOST", msg: { type: "renameSession", id: U, name: "newtitle" } }]);
});

test("routeOutbound: a feed card action routes by its sid, itemId untouched (askClear/expand/askFollowUp)", () => {
  // Regression (the user 2026-07-02): Clear on a REMOTE card sent {askClear, itemId} with NO sid → routed
  // to the LOCAL kernel (a no-op there), so the card resurrected on every reload. The card's sid now rides
  // along purely for routing; the itemId stays bare — it's already the owning kernel's own id.
  const r = routeOutbound({ type: "askClear", itemId: U + ":g3", sid: "TESTHOST:" + U });
  assert.deepEqual(r, [{ host: "TESTHOST", msg: { type: "askClear", itemId: U + ":g3", sid: U } }]);
  // a local card is unchanged: bare sid → local kernel
  const l = routeOutbound({ type: "askClear", itemId: U + ":g3", sid: U });
  assert.equal(l[0].host, "");
});

test("routeOutbound: a hover CLEAR broadcasts to every kernel (no sid to route by)", () => {
  const routes = routeOutbound({ type: "timelineHover", off: true }, new Set(["TESTHOST", "gpu1"]));
  assert.deepEqual(routes.map((r) => r.host).sort(), ["", "gpu1", "TESTHOST"].sort());
  // …but a hover ON routes to the lane's owner only
  const on = routeOutbound({ type: "timelineHover", sid: "TESTHOST:" + U, segIds: [] }, new Set(["TESTHOST"]));
  assert.deepEqual(on, [{ host: "TESTHOST", msg: { type: "timelineHover", sid: U, segIds: [] } }]);
});

test("routeOutbound: the gear's kernel-side settings reach EVERY attached kernel", () => {
  // The user 2026-08-14: Auto Nudge switched off in the dashboard, and the sessions on the other machine
  // went on being nudged for days. setAutoNudge carries no session id, so it fell through to LOCAL and
  // the remote kernel never heard it — while the gear, which fills the box from the LOCAL /version,
  // showed the change as applied everywhere. Silent in both directions.
  for (const msg of [{ type: "setAutoNudge", enabled: false },
                     { type: "setJudgeModel", model: "haiku" },
                     { type: "setIndexModel", model: "haiku" },
                     { type: "setJudgeEffort", effort: "high" },
                     { type: "setIndexEffort", effort: "" },
                     { type: "setUpdateMode", mode: "auto" },
                     { type: "setDistillModel", model: "haiku" },
                     { type: "setDistillEffort", effort: "triage" },
                     { type: "setFileEditing", enabled: true }]) {
    const routes = routeOutbound(msg, new Set(["TESTHOST", "gpu1"]));
    assert.deepEqual(routes.map((r) => r.host).sort(), ["", "TESTHOST", "gpu1"].sort(), msg.type);
    for (const r of routes) assert.deepEqual(r.msg, msg, "the kernels are host-blind: same message to each");
  }
  // with nothing attached it is the single-kernel path, byte for byte
  assert.deepEqual(routeOutbound({ type: "setAutoNudge", enabled: true }),
                   [{ host: "", msg: { type: "setAutoNudge", enabled: true } }]);
  // an explicit host still wins — the popover can ask ONE machine (that branch runs first)
  assert.deepEqual(routeOutbound({ type: "setAutoNudge", enabled: true, host: "gpu1" }, new Set(["gpu1"])),
                   [{ host: "gpu1", msg: { type: "setAutoNudge", enabled: true } }]);
  // NOT broadcast: a default directory is a path on one machine, and the colormap/palette are this
  // viewer's display prefs — both stay with the kernel serving the page.
  for (const msg of [{ type: "setDefaultDir", value: "~/code" }, { type: "setColormap", name: "aurora" },
                     { type: "setPalette", name: "default" }])
    assert.deepEqual(routeOutbound(msg, new Set(["gpu1"])), [{ host: "", msg }], msg.type);
});

test("routeOutbound: openFolder ALWAYS stays local, with a remote id's host prefix left INTACT", () => {
  // the user 2026-07-03: unlike every other id-bearing message, opening a folder means "open a window on
  // the machine the BROWSER runs on" — routing it to the remote kernel would open it on that headless
  // machine's own unwatched screen. The id must NOT be stripped either: the local kernel reads the host
  // prefix to know it should SSH out instead of treating the path as local.
  const remote = routeOutbound({ type: "openFolder", cwd: "/work/proj", id: "gpu1:" + U });
  assert.deepEqual(remote, [{ host: "", msg: { type: "openFolder", cwd: "/work/proj", id: "gpu1:" + U } }]);
  // a local session's click is unaffected — still local, id untouched (was already bare)
  const local = routeOutbound({ type: "openFolder", cwd: "/work/proj", id: U });
  assert.deepEqual(local, [{ host: "", msg: { type: "openFolder", cwd: "/work/proj", id: U } }]);
  // no id at all (an older client / a session with none) still just goes local
  const bare = routeOutbound({ type: "openFolder", cwd: "/work/proj" });
  assert.deepEqual(bare, [{ host: "", msg: { type: "openFolder", cwd: "/work/proj" } }]);
});

test("manager: merged timeline emits HOLD until the local lanes snapshot arrives (remote-first race)", () => {
  // The merges take `now` (the clock authority) from the LOCAL payload. When a remote host won the
  // connect race, the manager emitted a merged payload with now:undefined — the panel's fitWindow
  // latched a NaN window and every timeline x-coordinate stayed NaN for the page's lifetime (the
  // Chrome "stub lane lines, no bars" bug, 2026-07-15). The manager must buffer remote snapshots
  // and let the local arrival itself trigger the first emit (event-based, no timer).
  const emitted: any[] = [];
  const g: any = globalThis;
  const hadWindow = "window" in g;
  const prevWindow = g.window;
  g.window = { dispatchEvent: (ev: any) => emitted.push(ev.data) };
  try {
    const fm = new FederationManager();
    fm.inbound("TESTHOST", { type: "data", data: { sessions: [{ id: V, name: "rem" }], turns: {}, messages: [], judging: [], now: 500 } });
    fm.inbound("TESTHOST", { type: "bars", turns: { [V]: [{ id: "e", start: 400, end: 450 }] }, messages: [], judging: [], now: 500 });
    assert.equal(emitted.length, 0, "no emit before the local snapshot — it would carry now:undefined");
    fm.inbound("", { type: "data", data: { sessions: [{ id: U, name: "loc" }], turns: {}, messages: [], judging: [], now: 1000 } });
    assert.equal(emitted.length, 1, "the local arrival itself emits the merged lanes");
    assert.equal(emitted[0].type, "data");
    assert.equal(emitted[0].data.now, 1000, "the merged payload carries the local clock");
    assert.deepEqual(emitted[0].data.sessions.map((s: any) => s.id), [U, "TESTHOST:" + V],
      "the buffered remote lanes ride the first emit");
    fm.inbound("", { type: "bars", turns: {}, messages: [], judging: [], now: 1001 });
    assert.equal(emitted.length, 2, "bars flow once the local lanes exist");
    assert.equal(emitted[1].type, "bars");
    assert.ok(("TESTHOST:" + V) in emitted[1].turns, "the buffered remote bars merge in");
  } finally {
    if (hadWindow) g.window = prevWindow; else delete g.window;
  }
});

// ── pickWid: which DASHBOARD a federated socket belongs to ────────────────────────────────────────────
// The remote dial has to name the viewer the same way the pane's local socket does, or the remote kernel
// treats every federated window as one anonymous client and broadcasts what it means for one of them.
test("pickWid prefers the host-supplied ?wid=, then the shell's per-tab id", () => {
  assert.equal(pickWid("?app=chat&wid=from-host", "from-storage"), "from-host");
  assert.equal(pickWid("?app=chat", "from-storage"), "from-storage");
  assert.equal(pickWid("", ""), "", "neither → empty, and the kernel falls back to broadcasting");
});

test("pickWid survives a malformed query instead of throwing the connect away", () => {
  assert.equal(pickWid("%", "from-storage"), "from-storage");
});

// ── absorbHostReport: the merged strip keeps its promises about placement ─────────────────────────────
// End-to-end through the real manager: stub window (emissions) + localStorage (the stored arrangement),
// feed inbound tabOrder pushes, read the merged order the panes would render.
function withManager(fn: (fm: FederationManager, emitted: any[], store: Map<string, string>) => void): void {
  const emitted: any[] = [];
  const store = new Map<string, string>();
  const g: any = globalThis;
  const hadWindow = "window" in g, prevWindow = g.window;
  const hadLS = "localStorage" in g, prevLS = g.localStorage;
  g.window = { dispatchEvent: (ev: any) => { if (ev && ev.data) emitted.push(ev.data); } };
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null,
                     setItem: (k: string, v: string) => { store.set(k, v); } };
  try {
    fn(new FederationManager(), emitted, store);
  } finally {
    if (hadWindow) g.window = prevWindow; else delete g.window;
    if (hadLS) g.localStorage = prevLS; else delete g.localStorage;
  }
}
const lastOrder = (emitted: any[]) => emitted.filter((m) => m && m.type === "tabOrder").pop()!.order;

test("a session created after a remote host attached lands at the END of the merged strip", () => {
  // The 2026-08-10 report: the new session's provisional tab rendered last, then the merged push
  // re-slotted it in front of the remote host's block (host-blocked seed, local first).
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["a", "b"],
                     tabs: [{ id: "a", name: "web" }, { id: "b", name: "api" }] });
    fm.inbound("TESTHOST", { type: "tabOrder", order: [V], tabs: [{ id: V, name: "tests" }] });
    assert.deepEqual(lastOrder(emitted), ["a", "b", "TESTHOST:" + V]);
    // the new session appears mid-seed, at the end of the LOCAL kernel's block…
    fm.inbound("", { type: "tabOrder", order: ["a", "b", "n"],
                     tabs: [{ id: "a", name: "web" }, { id: "b", name: "api" }, { id: "n", name: "fresh" }] });
    // …but the merged strip shows it at the very end, where its provisional tab already rendered
    assert.deepEqual(lastOrder(emitted), ["a", "b", "TESTHOST:" + V, "n"]);
    // and the placement is WRITTEN, so it survives the next merge and a reload identically
    fm.inbound("TESTHOST", { type: "tabOrder", order: [V], tabs: [{ id: V, name: "tests" }] });
    assert.deepEqual(lastOrder(emitted), ["a", "b", "TESTHOST:" + V, "n"]);
  });
});

test("a relaunch that swaps the transcript fsid keeps the session's slot — it is not a new session", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["f1", "s"],
                     tabs: [{ id: "f1", name: "web" }, { id: "s", name: "api" }] });
    fm.inbound("TESTHOST", { type: "tabOrder", order: [V], tabs: [{ id: V, name: "tests" }] });
    // /clear: the kernel's own order already inherited the slot by name; the arrangement must follow
    fm.inbound("", { type: "tabOrder", order: ["f2", "s"],
                     tabs: [{ id: "f2", name: "web" }, { id: "s", name: "api" }] });
    assert.deepEqual(lastOrder(emitted), ["f2", "s", "TESTHOST:" + V],
      "f2 holds f1's slot instead of jumping to the end");
  });
});

test("a closed session leaves the arrangement; a detached host's sessions keep their slots", () => {
  withManager((fm, emitted, store) => {
    fm.inbound("", { type: "tabOrder", order: ["a", "b"],
                     tabs: [{ id: "a", name: "web" }, { id: "b", name: "api" }] });
    fm.inbound("TESTHOST", { type: "tabOrder", order: [V], tabs: [{ id: V, name: "tests" }] });
    fm.inbound("", { type: "tabOrder", order: ["b"], tabs: [{ id: "b", name: "api" }] });   // a closed
    assert.deepEqual(lastOrder(emitted), ["b", "TESTHOST:" + V]);
    assert.ok(!JSON.parse(store.get("romp:vieworder")!).includes("a"), "the closed id is pruned from storage");
    assert.ok(JSON.parse(store.get("romp:vieworder")!).includes("TESTHOST:" + V),
      "the remote id stays placed — its host simply wasn't the one reporting");
  });
});

// ── cross-host clock re-basing (the user 2026-08-15) ──────────────────────────────────────────────
// A postal connector touched a sender's lane AFTER that lane's last bar — a send apparently fired by
// a stopped session. Every kernel stamps times with its own clock; these tests pin the re-basing that
// puts every host's bars, marks and connectors on the LOCAL clock at merge time.

test("a skewed host's bars, lanes, marks and sent-times re-base onto the local clock", () => {
  const perHost: Record<string, any> = {
    "": { now: 1000, sessions: [{ id: "L", since: 990 }], turns: { L: [{ start: 900, end: 950 }] },
          messages: [], judging: [] },
    gpu1: { now: 940, sessions: [{ id: "gpu1:R", since: 930 }],   // gpu1's clock runs 60s behind
            turns: { "gpu1:R": [{ start: 840, end: 890 }] },
            messages: [{ id: "m1", fromId: "gpu1:R", toId: "L", sent: 900, exec: 900, hasExec: false }],
            judging: [{ judge: "closer", sid: "gpu1:R", t: 880 }] },
  };
  const m = mergeHostTimelines(perHost, ["", "gpu1"]);
  const r = m.sessions.find((s: any) => s.id === "gpu1:R");
  assert.equal(r.since, 990, "lane since re-based (+60)");
  assert.deepEqual(m.turns["gpu1:R"][0], { start: 900, end: 950 }, "bars re-based (+60)");
  assert.equal(m.judging.find((j: any) => j.sid === "gpu1:R").t, 940, "marks re-based (+60)");
  const msg = m.messages.find((x: any) => x.id === "m1");
  assert.equal(msg.sent, 960, "sent re-based by the EMITTING host (+60)");
  assert.equal(msg.exec, 960, "a pending exec is the emitter's copy of sent — it moves with it");
  // the local payload is the authority: untouched
  assert.equal(m.sessions.find((s: any) => s.id === "L").since, 990);
  assert.deepEqual(m.turns.L[0], { start: 900, end: 950 });
});

test("a DELIVERED exec re-bases by the RECIPIENT lane's host — the receipt carried the reader's clock", () => {
  // sender gpu1 (-60 vs local), recipient gpu2 (+30 vs local): sent moves +60 with its emitter, exec
  // moves -30 with the machine whose clock actually stamped the read
  const perHost: Record<string, any> = {
    "": { now: 1000, sessions: [{ id: "L" }], turns: {}, messages: [], judging: [] },
    gpu1: { now: 940, sessions: [{ id: "gpu1:A" }], turns: {},
            messages: [{ id: "m2", fromId: "gpu1:A", toId: "b-b-b-b-b", sent: 900, exec: 1010, hasExec: true }],
            judging: [] },
    gpu2: { now: 1030, sessions: [{ id: "gpu2:b-b-b-b-b" }], turns: {}, messages: [], judging: [] },
  };
  const m = mergeHostTimelines(perHost, ["", "gpu1", "gpu2"]);
  const msg = m.messages.find((x: any) => x.id === "m2");
  assert.equal(msg.toId, "gpu2:b-b-b-b-b", "the stitch resolved the foreign endpoint first");
  assert.equal(msg.sent, 960, "sent: emitter's offset (+60)");
  assert.equal(msg.exec, 980, "exec: recipient host's offset (-30), applied post-stitch");
});

test("sub-second deltas and hosts reporting no clock are never re-based", () => {
  assert.deepEqual(hostOffsets({ "": { now: 1000 }, a: { now: 999.6 }, b: {} }), {},
    "jitter is not skew, and an unreported clock is unknown — never guessed");
  const d = { sessions: [{ id: "a:X", since: 10 }], turns: {}, messages: [], judging: [] };
  assert.equal(rebaseHostTimes(d, 0), d, "zero offset returns the payload untouched");
});

test("the bars merge re-bases the same way (turns + marks + exec pass)", () => {
  const sessions = [{ id: "L" }, { id: "gpu1:R" }];
  const perHost: Record<string, any> = {
    "": { now: 500, turns: { L: [{ start: 400, end: 450 }] }, messages: [], judging: [] },
    gpu1: { now: 440, turns: { "gpu1:R": [{ start: 340, end: 390 }] },
            messages: [{ id: "m3", fromId: "gpu1:R", toId: "L", sent: 400, exec: 430, hasExec: true }],
            judging: [] },
  };
  const b = mergeHostBars(perHost, ["", "gpu1"], sessions);
  assert.deepEqual(b.turns["gpu1:R"][0], { start: 400, end: 450 });
  const msg = b.messages.find((x: any) => x.id === "m3");
  assert.equal(msg.sent, 460, "sent +60 with the emitter");
  assert.equal(msg.exec, 430, "exec stamped by the LOCAL recipient — the local clock never shifts");
});

test("re-based times cannot strand a connector after its sender's last bar (the reported artifact)", () => {
  // gpu1's clock runs 90s AHEAD of local: unre-based, its lane bars sat 90s right of local truth,
  // and its send mark (stamped 90s ahead) rendered after the lane's real work ended
  const perHost: Record<string, any> = {
    "": { now: 1000, sessions: [{ id: "L", since: 995 }], turns: { L: [{ start: 900, end: 995 }] },
          messages: [], judging: [] },
    gpu1: { now: 1090, sessions: [{ id: "gpu1:S", since: 1085 }],
            turns: { "gpu1:S": [{ start: 1000, end: 1085 }] },
            messages: [{ id: "m4", fromId: "gpu1:S", toId: "L", sent: 1080, exec: 1080, hasExec: false }],
            judging: [] },
  };
  const m = mergeHostTimelines(perHost, ["", "gpu1"]);
  const lane = m.turns["gpu1:S"][0];
  const msg = m.messages.find((x: any) => x.id === "m4");
  assert.ok(msg.sent <= lane.end, "the send mark sits within the sender's re-based work, not after it");
  assert.equal(msg.sent, 990);
  assert.deepEqual(lane, { start: 910, end: 995 });
});
