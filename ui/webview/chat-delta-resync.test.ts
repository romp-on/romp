// A chatTail delta that starts PAST what the tab holds is a gap: the events in between never arrived.
// Applying it would fabricate a transcript that silently skips them, so the client used to just drop it
// and "wait for the next full" — which never came, because the kernel's per-client bookkeeping advances
// when it SENDS, not when the client APPLIES. The tab froze there: a stale "working" chip, no new
// messages, and every deep-link into the missing range honest-failing "couldn't locate this in the
// transcript", until the socket happened to drop (the user 2026-07-28 — locate-audit.jsonl recorded six
// pointer-not-rendered misses on one session, then pointer-exact on the SAME anchor the moment a kernel
// restart forced a reconnect). The fix: ask for the full session, and file the miss in the error center.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const FEED = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "bin", "romp-kernel"), "utf8");

test("a delta gap asks the kernel for a full session instead of freezing", () => {
  // measured against the KERNEL-owned length: the injected optimistic tail is not in the kernel's
  // coordinate space, and counting it masked a genuine 1-event gap (the user 2026-08-09)
  assert.match(RENDER, /if \(from > kernelLen\) \{/,
    "chatTail must treat a too-far-ahead delta as its own case, not fold it into the silent return");
  assert.match(RENDER, /const kernelLen = s\.events\.reduce\(\(n, e\) => n \+ \(isOptimistic\(e\) \|\| isHeldGroup\(e\) \? 0 : 1\), 0\);/,
    "…in kernel coordinates: our injections are counted out (since T252 a bubble sits at its send slot, mid-array) — and only counted here, the strip happens once the delta is applied");
  // since 2026-09-07 every ask names its WHY (a one-word diagnostic the kernel ignores; the return-to-tab
  // harness counts asks by it — skeleton-tabs-wiring.test.ts pins the vocabulary); the gap is "gap"
  assert.match(RENDER, /requestFullSession\(msg\.id, "gap"\);/, "…and request a re-base");
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "needFull", id, why \}\)/,
    "the resync request must actually reach the kernel");
});

test("the resync is asked ONCE per desync, and re-arms when the full session lands", () => {
  // the pusher runs every 0.5-3s; without the guard every rejected delta would re-ask
  assert.match(RENDER, /if \(awaitingFull\.has\(id\)\) \{[\s\S]*?\n\s*return;\n\s*\}\n\s*awaitingFull\.add\(id\);/,
    "one ask per desync: the latched branch returns before a second ask (its row is pinned in needfull-latch-hygiene.test.ts, 2026-09-19)");
  assert.match(RENDER, /awaitingFull\.add\(id\);/);
  assert.match(RENDER, /awaitingFull\.delete\(msg\.id\)/,
    "upsert() must clear the flag so a LATER gap can ask again");
});

test("a below-the-head delta is still ignored quietly (not a desync)", () => {
  // from < 0 means the change is in history this tab doesn't hold; its resident tail is still valid,
  // so it must NOT trigger a resync storm.
  assert.match(RENDER, /if \(from < 0\) return;/,
    "the below-the-loaded-head case keeps its quiet return");
});

test("the kernel handles needFull by forgetting what that client holds", () => {
  assert.ok(KERNEL.includes('msg.get("type") == "needFull"'), "the kernel must handle the frame");
  const i = KERNEL.indexOf('msg.get("type") == "needFull"');
  const body = KERNEL.slice(i, i + 1200);
  // the two pops live in _client_reset_chat_sid since 2026-09-04 (they run under the client's slot lock, so
  // the pusher's _send_chat lands whole before or after them) — pin the handler's call AND the helper's body
  assert.ok(body.includes("_client_reset_chat_sid(client, sid)"), "the handler forgets through the locked helper");
  const h = KERNEL.indexOf("def _client_reset_chat_sid(client, sid):");
  assert.ok(h >= 0, "the helper must exist");
  const helper = KERNEL.slice(h, h + 1500);
  assert.ok(helper.includes('"echat"'), "drop the client's tail base → next push sends a full session");
  assert.ok(helper.includes('("chat", sid)'), "drop the dedup slot → the full send isn't swallowed");
  assert.ok(body.includes("_push_one(client)"), "repair immediately, not on the next tick");
});

test("a failed jump is filed in the error center, not just toasted", () => {
  // the toast is transient and locate-audit.jsonl is invisible from the UI, so a failed click left
  // nothing the user could point at afterwards (the user 2026-07-28)
  assert.match(RENDER, /notifyShell\("locate",/, "the chat files a locate entry on an honest failure");
  assert.match(RENDER, /romp: "notify", kind, text, sid/, "…over the shell's notify bridge");
  assert.match(FEED, /kind: "locate"/, "the feed's no-anchor summary click files one too");
  // and the kind must be registered in the shell, or the entry renders chip-less and unfilterable
  assert.ok(KERNEL.includes("'locate'"), "the error center must know the locate kind");
  assert.ok(KERNEL.includes("locate:'jump failed'"), "…with a label");
  assert.ok(/locate:"[^"]+"/.test(KERNEL), "…and a description for its filter tooltip");
});

test("the anchor re-query uses the same selectors as the first lookup", () => {
  // the recovery re-render found the event, then re-queried with only 2 of the 3 selectors — so an
  // unhydrated postal turn (ids only in data-mids) still honest-failed pointer-not-rendered
  const both = RENDER.split("data-mids~=").length - 1;
  assert.ok(both >= 2, "data-mids must be in BOTH the initial query and the post-re-render re-query");
});

test("a delta with NO base at all is a desync too — every delta path asks for the full frame", () => {
  // the lost-first-frame class (the user 2026-09-02): the full session frame landed before the bundle's
  // message listener existed, so the pane holds nothing while the kernel volunteers only deltas. Each
  // delta shape must ask for the base instead of silently returning (the old `if (!s) return;`).
  const chatTailFn = RENDER.slice(RENDER.indexOf("function chatTail(msg"), RENDER.indexOf("function statusOnly(msg"));
  assert.match(chatTailFn, /if \(!s\) \{[\s\S]{0,900}?requestFullSession\(msg\.id, "nobase"\);\s*\n\s*return;\s*\n\s*\}/,
    "chatTail: no base → ask, don't wait forever");
  const updateFn = RENDER.slice(RENDER.indexOf("function update(msg"), RENDER.indexOf("function chatTail(msg"));
  assert.match(updateFn, /if \(!s\) \{ requestFullSession\(msg\.id, "nobase"\); return; \}/, "update: same");
  // statusOnly is the exception (2026-09-11; it has OPENED with the skeleton-tab branch since 2026-09-07): the kernel
  // sends a status frame for a sid it holds as a skeleton and for no other, so a status for a session the page holds
  // nothing of is a skeleton's whose strip the shim's FIFO delivers BEHIND it (a newer strip takes the end of the
  // burst), not a lost first frame. It is held for the strip (skeleton-tabs.ts holdStatus); the ask that stood here
  // loaded the whole board into a chat column opened as a view of one session, one ask per withheld tab.
  // skeleton-tabs-wiring.test.ts pins the branch's shape.
  const statusFn = RENDER.split("function statusOnly(msg: any) {")[1].split("\n}")[0];
  assert.doesNotMatch(statusFn, /requestFullSession\(msg\.id, "nobase"\)/, "statusOnly: a status is never a desync");
  assert.match(statusFn, /if \(!s\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*holdStatus\(skeletonTabs, msg\.id, msg\.status\); return;/, "statusOnly: held for its strip");
});

test("a reconnect clears parked asks — a dead socket's pending needFull can never gag the new one", () => {
  assert.match(RENDER, /window\.addEventListener\("romp:wsup", \(\) => awaitingFull\.clear\(\)\);/);
});

test("the kernel's ready branch resets the client's WHOLE chat base before its push", () => {
  const i = KERNEL.indexOf('msg.get("type") == "ready"');
  assert.ok(i > 0);
  // the arm's window: it grew with the focused-session send (T347), the metrics team's connect-time reads and the
  // skeleton client's re-arm (the chat split) landing together on 2026-09-11, past the 1600 characters this read;
  // the connect push sits near 2400 now
  const body = KERNEL.slice(i, i + 3000);
  assert.ok(body.includes("_client_reset_chat_base(client)"), "ready = the renderer holds nothing");
  assert.ok(body.includes("_push_one(client)"), "the connect push is in the arm");
  assert.ok(body.indexOf("_client_reset_chat_base(client)") < body.indexOf("_push_one(client)"),
    "…reset first, so the push that follows is full frames");
});
