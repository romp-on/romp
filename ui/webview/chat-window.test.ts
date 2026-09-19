// The uuid-anchored chat wire's list operations (T323 stage 4b): executed for real over small lists.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { applyTailAfter, prependHead, mergeWindow, historyLabel, indexOfUuid, keyOf, olderRequestAllowed } from "./chat-window";

const ev = (u: string) => ({ uuid: u, kind: "user", md: u });
const run = (...u: string[]) => u.map(ev);
const uu = (l: { uuid?: string }[]) => l.map((e) => e.uuid);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("a chatTail by uuid truncates after its anchor and appends; an anchor not resident is a gap", () => {
  assert.deepEqual(uu(applyTailAfter(run("a", "b", "c"), "b", run("c2", "d"))!), ["a", "b", "c2", "d"]);
  assert.deepEqual(uu(applyTailAfter(run("a", "b", "c"), "c", run("d"))!), ["a", "b", "c", "d"]);
  assert.equal(applyTailAfter(run("a", "b"), "zz", run("d")), null, "the anchor is gone: ask for a full");
});

test("a chatHead by uuid prepends only when its beforeUuid is the resident oldest", () => {
  assert.deepEqual(uu(prependHead(run("d", "e"), "d", run("b", "c"))!), ["b", "c", "d", "e"]);
  assert.equal(prependHead(run("d", "e"), "e", run("b")), null, "stale: the oldest moved on");
  assert.deepEqual(uu(prependHead(run("d"), "d", [])!), ["d"], "an empty page (the head): the list stands");
});

test("a chatWindow replaces a run it does not overlap and merges one it does, in transcript order, once per uuid", () => {
  const far = mergeWindow(run("x", "y", "z"), run("a", "b", "c"));
  assert.equal(far.mode, "replace"); assert.deepEqual(uu(far.events), ["a", "b", "c"]);
  const before = mergeWindow(run("c", "d", "e"), run("a", "b", "c"));          // the window ends inside the run
  assert.equal(before.mode, "merge"); assert.deepEqual(uu(before.events), ["a", "b", "c", "d", "e"]);
  const inside = mergeWindow(run("a", "b", "c", "d"), run("b", "c"));          // the window is inside the run
  assert.equal(inside.mode, "merge"); assert.deepEqual(uu(inside.events), ["a", "b", "c", "d"]);
  const after = mergeWindow(run("a", "b", "c"), run("c", "d", "e"));           // the window starts inside the run
  assert.equal(after.mode, "merge"); assert.deepEqual(uu(after.events), ["a", "b", "c", "d", "e"]);
  assert.equal(indexOfUuid(after.events, "e"), 4);
});

test("a second event of one record keeps the record's uuid and is anchored by its key", () => {
  const text = { uuid: "a1", kind: "assistant", md: "running" }, tool = { uuid: "a1", key: "a1#2", kind: "tool", name: "Bash" };
  assert.equal(keyOf(text), "a1"); assert.equal(keyOf(tool), "a1#2");
  const list = [ev("u1"), text, tool];
  assert.equal(indexOfUuid(list, "a1#2"), 2, "the tool event, by its key");
  assert.deepEqual(uu(applyTailAfter(list, "a1#2", run("r1"))!), ["u1", "a1", "a1", "r1"], "a tail after the tool event keeps both");
  assert.deepEqual(uu(applyTailAfter(list, "a1", run("x"))!), ["u1", "a1", "x"], "a tail after the text event drops the tool event");
});

test("the history strip shows no number while the head is unknown", () => {
  assert.equal(historyLabel(false, 250, null), "older history");
  assert.equal(historyLabel(false, 250, 900), "older history", "a total handed with an unknown head is not shown either");
  assert.equal(historyLabel(true, 250, 900), "650 older");
  assert.equal(historyLabel(true, 900, 900), "");
});

test("an older-history ask needs an upward or unchanged move; a downward gesture never asks (T366)", () => {
  assert.equal(olderRequestAllowed(undefined, 120), true, "no previous top: a fresh or rebuilt view may ask");
  assert.equal(olderRequestAllowed(null, 0), true);
  assert.equal(olderRequestAllowed(500, 120), true, "moving up");
  assert.equal(olderRequestAllowed(120, 120), true, "unchanged (a resize, a relayout)");
  assert.equal(olderRequestAllowed(120, 121), false, "moving down, by any amount");
  assert.equal(olderRequestAllowed(0, 3000), false, "a flick down from the very top");
});

test("render.ts asks for older history only on an upward move, marks each window ask with whether a navigation made it, and files the ask (T366)", () => {
  const virt = RENDER.slice(RENDER.indexOf("function virtualizeToViewport()"), RENDER.indexOf("\n}\n", RENDER.indexOf("function virtualizeToViewport()")));
  assert.ok(virt.includes("const gesture = v.gestureScroll === true;\n  v.gestureScroll = undefined;\n  if (gesture) v.edgeUp = olderRequestAllowed(v.edgeTop, st);"), "the direction is read only on the reader's own gesture, from the view's last edge-check top, and the mark is consumed");
  assert.ok(virt.indexOf("if (gesture) v.edgeUp = olderRequestAllowed(v.edgeTop, st);") < virt.indexOf("v.edgeTop = st;"), "…before the top is remembered for the next check (the page's own writes move it too)");
  assert.ok(virt.includes("const upward = v.edgeUp !== false;"), "the last gesture's verdict holds across the page's compensating writes; no verdict yet allows the ask");
  assert.ok(virt.includes("st < topH + edgePx && upward) { requestOlder(activeId, v, content); return; }"), "the older ask is gated on the direction, not only the estimate's band");
  assert.match(RENDER, /const cls = classifyScroll\(c\.scrollTop, lastScrollWriteAfter\);\n\s*const gv = activeId \? views\.get\(activeId\) : null;\n\s*if \(gv\) gv\.gestureScroll = cls === "gesture";/, "the scroll listener marks a gesture (a write's echo is none) for the edge check that runs next");
  assert.ok(RENDER.includes("edgeTop?: number; edgeUp?: boolean; gestureScroll?: boolean;"), "the view remembers the top the last edge check saw, the last verdict and the gesture mark");
  assert.ok(RENDER.includes("v.unitTotal = undefined; v.edgeTop = undefined; v.edgeUp = undefined; v.stale = true; }"), "a window rebuild forgets both: the first check after a rebuild may ask");
  const around = RENDER.slice(RENDER.indexOf("function requestAround(sid: string, uuid: string): boolean {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function requestAround(sid: string, uuid: string): boolean {")));
  assert.ok(around.includes("const nav = !relandAsk;") && around.includes("const rec: WindowAsk = { anchor: uuid, nav,"), "a window ask records whether a navigation made it: every anchor landing but the re-land of the reader's own row across a rebuild");
  const keep = RENDER.slice(RENDER.indexOf("function keepPlaceAcrossWindow("), RENDER.indexOf("\n}\n", RENDER.indexOf("function keepPlaceAcrossWindow(")));
  assert.ok(keep.includes("relandAsk = true;\n  let landed = false;\n  try { landed = scrollToAnchor(keep.uuid); } finally { relandAsk = false; }"), "the re-land's own flag is set only around its landing (the reload restore shares the keep offset and must land)");
  assert.equal((RENDER.match(/relandAsk = true;/g) || []).length, 1, "nothing else raises the flag");
  assert.ok(around.includes('scrollDiagRow("regionask", { sid, why: "landing",'), "…and files a diagnostic row under the scroll rows' per-minute budget: the report's rows had the landing but not the ask (the T366 window-ask row, regionask since the regions)");
  assert.ok(RENDER.includes('| "unitchange" | "regionask" | "landmiss", data: any): void {'), "the budgeted row kinds include it, and the window-ask kind is gone");
  assert.ok(around.indexOf("const rec: WindowAsk = { anchor: uuid, nav,") < around.indexOf('type: "loadAround"'), "the mark is set before the ask goes out");
  // a second full ask while one is in flight is dropped before it can overwrite the pending reason (kept for the reconnect's diagnostics:
  // every full frame merges into the held runs since T386 stage 2, so no reason decides a merge or a replace any more)
  const full = RENDER.slice(RENDER.indexOf("function requestFullSession(id: string, why: NeedFullWhy): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function requestFullSession(id: string, why: NeedFullWhy): void {")));
  for (const line of ["if (!id) return;", "if (awaitingFull.has(id)) {", "pendingFullWhy.set(id, why);"]) assert.ok(full.includes(line), line + " is in requestFullSession (an absent line would make the order below vacuous)");
  assert.ok(full.indexOf("if (!id) return;") < full.indexOf("if (awaitingFull.has(id)) {") && full.indexOf("if (awaitingFull.has(id)) {") < full.indexOf("pendingFullWhy.set(id, why);"),
    "a second full ask while one is in flight is refused (the empty-id guard, then the latched branch, 2026-09-19) before it can overwrite the pending reason");
});

test("a window ask carries the navigation's time and kind to its reply; the three direct landings (a notch, a reply chip, a comment tick) arm neither (T366)", () => {
  assert.ok(RENDER.includes("const rec: WindowAsk = { anchor: uuid, nav, named: nav && pendingAnchorKeepY == null, t: nav ? (pendingAnchorT ?? null) : null, kind: nav ? kind : null, origin: null, gap: null, cancelled: false };"), "the ask carries the navigation's time to the reply, and whether it was a click (any anchor landing without a keep offset; the reload restore of the reader's own place arms one)");
  assert.match(RENDER, /markjump: \(elx\) => \{\s*\n\s*const uuid = elx\.dataset\.uuid;\s*\n\s*if \(!uuid \|\| !activeId\) return;\s*\n\s*flashedAnchor = null;\s*\n\s*scrollToAnchor\(uuid\);/, "the notch lands directly");
  assert.match(RENDER, /replyjump: \(elx\) => \{[\s\S]*?flashedAnchor = null;[^\n]*\n\s*if \(scrollToAnchor\(uuid\)\) \{/, "the reply chip lands directly");
  assert.match(RENDER, /cmtjump: \(elx\) => \{[\s\S]*?flashedAnchor = null;\s*\n\s*scrollToAnchor\(uuid\);/, "the comment tick lands directly");
});

test("render.ts tracks the pending needFull reason and lands orphan notes by record uuid", () => {
  const upsert = RENDER.slice(RENDER.indexOf("function upsert(msg: any) {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function upsert(msg: any) {")));
  assert.ok(upsert.includes("pendingFullWhy.delete(msg.id)"), "the reason is consumed by the frame that answers it");
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "needFull", id, why \}\);\n  pendingFullWhy\.set\(id, why\);/, "requestFullSession records the reason with the ask");
  assert.match(RENDER, /window\.addEventListener\("romp:wsup", \(\) => pendingFullWhy\.clear\(\)\);/, "…and a new socket forgets the reasons with the asks");
  assert.ok(RENDER.includes('turn.dataset.orphanOf = String((ev as { orphanOf?: string }).orphanOf)'), "an orphan note's turn carries its record uuid");
  assert.equal((RENDER.match(/\.turn\[data-orphan-of="\$\{cssEscape\(uuid\)\}"\]/g) || []).length, 2, "…and both anchor lookups read it");
  assert.ok(RENDER.includes("(e as { orphanOf?: string }).orphanOf === uuid"), "…as does the events-list search behind them");
});

test("render.ts speaks proto 2 at ready and routes the proto-2 frames through this module and the regions' page frame through chat-regions.ts (T386 stage 2)", () => {
  assert.match(RENDER, /postMessage\(\{ type: "ready", proto: 2 \}\)/, "the ready frame names the protocol");
  for (const fn of ["indexOfUuid", "prependHead", "historyLabel", "keyOf"]) assert.ok(RENDER.includes(fn + "("), fn);   // chatTail truncates by indexOfUuid in place; mergeWindow is chat-regions.ts's now
  assert.ok(RENDER.includes('m.type === "chatWindow"') && RENDER.includes('m.type === "chatTurns"'), "the window frame and the page frame are dispatched");
  assert.ok(RENDER.includes('type: "loadAround"') && RENDER.includes('type: "loadTurns"'), "and the two requests are posted");
  for (const gone of ['m.type === "chatMore"', 'type: "loadNewer"', "updateLivePaused(", "reattachLive(", '"live-paused"', 'requestFullSession(sid, "reattach")', "s.detached"]) assert.ok(!RENDER.includes(gone), gone + " is retired: the tail run is always resident and live, no client is ever detached");
  for (const fn of ["insertRun(", "regionsFromRuns(", "gapHeight(", "pagesToAsk(", "gapFraction(", "landingNotice("]) assert.ok(RENDER.includes(fn), fn + " wired from chat-regions.ts");
  assert.ok(RENDER.includes('requestFullSession(msg.id, "gap"); return; }   // the anchor is gone'), "a missing chatHead is a gap");
});

test("federation tells every remote kernel the chat protocol on its socket's open", () => {
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.ok(FED.includes('if (this.pageProto !== null && !conn.dialedReconnect) { try { ws.send(JSON.stringify({ type: "ready", proto: this.pageProto })); }'), "the remote socket's open sends the ready with the protocol the page speaks, 1 included (the follow-up after PR 1584, low 2), EXCEPT on a redial whose reconnect=1 is its own handshake (2026-09-15)");
  assert.ok(FED.includes('if (c.ws && c.ws.readyState === 1 && !c.dialedReconnect) { try { c.ws.send(JSON.stringify({ type: "ready", proto: this.pageProto }))'), "…and the page's ready is told to every open remote socket the same way, a redial socket excepted for the same reason");
});
