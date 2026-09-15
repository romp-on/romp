// intentOp gates what survives a KernelPipe reconnect: user intent (typed text,
// explicit picks) delivers after the socket returns; view chatter drops, because
// the reconnect reloads the webview and resyncs it fresh (the user 2026-07-21).
// heldAcrossReload / ReloadHold are the inbound half: the kernel's refusal of a
// replayed op must outlive that same reload (review find, 2026-09-08).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { INTENT_OPS, intentOp, HELD_ACROSS_RELOAD, heldAcrossReload, ReloadHold } from "./pipe-intent";

test("typed-text ops are intent — losing them loses the user's words", () => {
  for (const t of ["sendMessage", "askFollowUp", "askText", "addCustomAsk", "sendCommand", "rewindSend"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("explicit state-changing picks are intent", () => {
  // setTimelineViews and tagEdit are the two views writes (a lens/order blob; a targeted tag edit,
  // 2026-09-05) — a tag renamed during a reconnect window must still land
  for (const t of ["setModel", "setEffort", "setMode", "setFast", "interrupt", "endSession",
    "nodeOverride", "askClear", "answerAsk", "submitAsk", "renameSession", "moveSession", "noticeAction",
    "setTimelineViews", "tagEdit", "editTag"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("a remote-tag edit is intent: dropped on reconnect, it was simply lost", () => {
  // editTag is the federation op for a tag whose home is another kernel (render.ts's union add/remove,
  // the timeline's __rompTimelineEditTag hook, 2026-08-24). The pane mirrors the edit locally beside
  // the post, but the mirror never outlives the reconnect: every panel's onReconnect reloads its
  // webview and the fresh "ready" resyncs from the kernel, where the edit never landed. So a post
  // dropped with the view chatter left the tag unchanged, with nothing saying the gesture was lost
  // (review find, 2026-09-08: the earlier wording had the mirror staying on screen, which it cannot)
  assert.ok(intentOp("editTag"), "a queued editTag must be kept across the extension's reconnect");
});

test("a refusal of a replayed intent is held across the webview's reload; state frames are not", () => {
  // the reconnect replays intent and reloads the webview in one tick; the kernel's refusal of a replayed
  // op (a remote-tag ADD right after a kernel restart: the home kernel's tunnel is not up yet, and an
  // add never queues) then lands on a page that is gone (review find, 2026-09-08). A refusal addressed
  // by name is one the fresh page can show; a state frame is resynced by its ready, and a per-page write
  // ack names a write the fresh page never made
  assert.ok(heldAcrossReload("tagEditFailed"), "the remote-tag refusal must reach the reloaded timeline");
  assert.ok(HELD_ACROSS_RELOAD.has("tagEditFailed"));
  for (const t of ["data", "bars", "caps", "ka", "pipeState", "models", "tagEditAck", "viewsAck"])
    assert.ok(!heldAcrossReload(t), `${t} is resynced by the fresh ready, or names a write the fresh page never made`);
  assert.ok(!heldAcrossReload(undefined));
  assert.ok(!heldAcrossReload(42));
});

test("the hold parks a refusal only while the webview is not ready, and releases once, in arrival order", () => {
  const h = new ReloadHold();
  const refused = { type: "tagEditFailed", host: "TESTHOST-B", name: "team",
    error: '"TESTHOST-B" is attached but not reachable right now', queued: false };
  assert.equal(h.offer({ type: "data", data: {} }, false), true, "a state frame goes into the reload; the ready resyncs it");
  assert.equal(h.offer(refused, false), false, "the refusal waits for the ready");
  assert.equal(h.offer({ type: "tagEditFailed", host: "TESTHOST-B", name: "pool", error: "refused" }, false), false);
  assert.deepEqual(h.release().map((m: any) => m.name), ["team", "pool"], "released in arrival order");
  assert.deepEqual(h.release(), [], "…and only once");
  assert.equal(h.offer(refused, true), true, "with the webview ready a refusal goes straight through");
  assert.deepEqual(h.release(), [], "…and is not parked on the way");
});

test("view chatter is not intent — the reconnect reload resyncs it", () => {
  for (const t of ["ready", "openSession", "showAskPath", "showOnTimeline", "dotHover",
    "hoverHighlight", "loadOlder", "requestSessions", "openByName", "dotOpen", "imgRequest"]) {
    assert.ok(!intentOp(t), `${t} is view state, not user intent`);
  }
});

test("non-strings never classify as intent", () => {
  assert.ok(!intentOp(undefined));
  assert.ok(!intentOp(null));
  assert.ok(!intentOp(42));
  assert.ok(!INTENT_OPS.has(""));
});
